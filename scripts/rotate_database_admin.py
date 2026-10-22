"""Explicit operator maintenance: rotate an existing DB login without restarting apps.

Run only after inventorying every consumer. Passwords stay in protected settings
and process stdin; neither command arguments nor diagnostics contain credentials.
"""
import argparse
import base64
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def run(args, data=None, timeout=60):
    p = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"{args[0]} failed; output withheld because it may contain credentials")
    return p.stdout


def parse_env(raw):
    result = {}
    for line in raw.decode().splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        if key in result:
            raise ValueError("Duplicate environment key")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def replace_password(raw, password, expected_user):
    values = parse_env(raw)
    parsed = urlsplit(values["DATABASE_URL"])
    if (parsed.scheme not in ("postgres", "postgresql") or
            unquote(parsed.username or "") != expected_user or not parsed.password or
            values["POSTGRES_USER"] != expected_user or
            unquote(parsed.password) != values["POSTGRES_PASSWORD"]):
        raise ValueError("Administrator configuration is inconsistent")
    authority = parsed.netloc.rsplit("@", 1)[1]
    url = urlunsplit(parsed._replace(netloc=quote(expected_user, safe="") + ":" + quote(password, safe="") + "@" + authority))
    replacements = {"DATABASE_URL": url, "POSTGRES_PASSWORD": password}
    text = raw.decode()
    for key, value in replacements.items():
        text, count = re.subn(r"(?m)^(\s*(?:export\s+)?" + key + r"\s*=)[^\r\n]*", lambda m: m[1] + value, text)
        if count != 1:
            raise ValueError("Expected exactly one setting")
    return text.encode()


def scram_verifier(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, 4096)
    stored = hashlib.sha256(hmac.digest(salted, b"Client Key", "sha256")).digest()
    server = hmac.digest(salted, b"Server Key", "sha256")
    enc = lambda value: base64.b64encode(value).decode("ascii")
    return "SCRAM-SHA-256$4096:" + enc(salt) + "$" + enc(stored) + ":" + enc(server)


def atomic_private(path, content, uid, gid):
    fd, name = tempfile.mkstemp(prefix=".credential-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(name, uid, gid)
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


PROBE = """import json,sys,psycopg
p=json.load(sys.stdin)
try:
 with psycopg.connect(p['url'],connect_timeout=8) as c:
  c.execute('BEGIN READ ONLY')
  role=c.execute('SELECT current_user').fetchone()[0]
  c.execute('SELECT 1').fetchone()
 print(json.dumps({'connected':True,'role':role}))
except psycopg.Error as e:
 # libpq may omit SQLSTATE for startup authentication failures. Do not treat
 # a timeout/refused connection as proof that a password has been revoked.
 failed=e.sqlstate=='28P01' or 'password authentication failed for user' in str(e)
 print(json.dumps({'connected':False,'authentication_failed':failed}))
"""


def rotate(args):
    import fcntl
    if os.geteuid() != 0:
        raise RuntimeError("Run through the authorized root maintenance channel")
    os.umask(0o077)
    lock_path = Path(args.lock)
    if lock_path.is_symlink():
        raise ValueError("Maintenance lock cannot be a symlink")
    lock = lock_path.open("ab")
    def lock_timeout(signum, frame):
        raise RuntimeError("Existing deployment holds the lock; no settings or credentials changed")
    previous_handler = signal.signal(signal.SIGALRM, lock_timeout)
    signal.alarm(60)
    try:
        # Queue a bounded waiter instead of repeatedly racing an idle release.
        fcntl.flock(lock, fcntl.LOCK_EX)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
    objects = json.loads(run(["docker", "inspect", args.db_container, *args.app_container]))
    baseline = [(o["Id"], o["State"]["StartedAt"], o["RestartCount"]) for o in objects]
    assert all(o["State"]["Running"] for o in objects)
    db_env = dict(v.split("=", 1) for v in objects[0]["Config"]["Env"] if "=" in v)
    user, database = db_env["POSTGRES_USER"], db_env["POSTGRES_DB"]
    assert not db_env.get("PGPASSWORD")
    def sql(statement):
        return run(["docker", "exec", "-i", "-u", "postgres", args.db_container,
                    "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-U", user, "-d", database], statement.encode())
    def probe(url):
        return json.loads(run(["docker", "exec", "-i", args.app_container[0], "python", "-c", PROBE], json.dumps({"url": url}).encode()))
    quoted_user = '"' + user.replace('"', '""') + '"'
    def alter(password):
        verifier = scram_verifier(password)
        sql("BEGIN; SET LOCAL log_statement='none'; SET LOCAL log_min_duration_statement=-1; "
            "SET LOCAL log_min_error_statement='panic'; ALTER ROLE " + quoted_user +
            " PASSWORD '" + verifier + "'; COMMIT;")
    files = []
    for filename in args.env_file:
        path = Path(filename)
        assert path.is_absolute() and path.is_file() and not path.is_symlink()
        raw, stat = path.read_bytes(), path.stat()
        values = parse_env(raw)
        assert values["POSTGRES_DB"] == database
        replace_password(raw, "preflight-placeholder", user)
        files.append((path, raw, stat, values))
    old_url = files[0][3]["DATABASE_URL"]
    old_password = files[0][3]["POSTGRES_PASSWORD"]
    old_password.encode("ascii")  # Guarantee the same SCRAM encoder can restore it.
    assert all(f[3]["POSTGRES_PASSWORD"] == old_password for f in files)
    assert probe(old_url) == {"connected": True, "role": user}
    runtime_urls = []
    for obj in objects[1:]:
        env = dict(v.split("=", 1) for v in obj["Config"]["Env"] if "=" in v)
        url = env["DATABASE_URL"]
        assert unquote(urlsplit(url).username or "") != user
        assert probe(url)["connected"]
        runtime_urls.append(url)
    # A one-shot migration/backup using TCP must finish before rotation.
    active = int(sql("SELECT count(*) FROM pg_stat_activity WHERE usename=current_user AND client_addr IS NOT NULL;").strip())
    assert active == 0, "Administrator TCP consumers are active"
    if not args.execute:
        return {"status": "ready", "settings_files": len(files), "runtime_connections": len(runtime_urls), "restart_required": False}
    size = int(sql("SELECT pg_database_size(current_database());").strip())
    parent = Path(args.backup_root)
    assert parent.is_dir() and not parent.is_symlink()
    assert shutil.disk_usage(parent).free > size * 2 + 1024**3
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = parent / ("database-credential-rotation-" + stamp)
    backup.mkdir(mode=0o700)
    def journal(state):
        (backup / "state.json").write_text(json.dumps({"state": state, "files": [str(f[0]) for f in files]}, indent=2))
    for index, (_, raw, stat, _) in enumerate(files):
        (backup / (str(index) + ".env.before")).write_bytes(raw)
    dump = backup / "database.dump"
    with dump.open("wb") as stream:
        p = subprocess.run(["docker", "exec", "-u", "postgres", args.db_container, "pg_dump", "-U", user, "-d", database, "-Fc"], stdout=stream, stderr=subprocess.PIPE, timeout=600)
    assert p.returncode == 0 and dump.stat().st_size > 0, "Backup failed; no credential changed"
    with dump.open("rb") as stream:
        p = subprocess.run(["docker", "exec", "-i", args.db_container, "pg_restore", "--list"], stdin=stream, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
    assert p.returncode == 0, "Backup validation failed; no credential changed"
    password = secrets.token_urlsafe(48)
    updates = [replace_password(f[1], password, user) for f in files]
    for i, raw in enumerate(updates):
        (backup / (str(i) + ".env.pending")).write_bytes(raw)
    journal("prepared")
    changed = False
    written = []
    try:
        assert all(path.read_bytes() == old for path, old, _, _ in files), "Settings changed concurrently"
        for (path, old, stat, _), new in zip(files, updates):
            assert path.read_bytes() == old, "Settings changed concurrently"
            atomic_private(path, new, stat.st_uid, stat.st_gid)
            written.append((path, old, stat, new))
        journal("settings_written")
        changed = True
        alter(password)
        new_url = parse_env(updates[0])["DATABASE_URL"]
        assert probe(new_url) == {"connected": True, "role": user}
        assert probe(old_url) == {"connected": False, "authentication_failed": True}
        assert all(probe(url)["connected"] for url in runtime_urls)
        # The formal backup entry point continues using its unchanged local socket.
        assert sql("SELECT 1;").strip() == b"1"
        after = json.loads(run(["docker", "inspect", args.db_container, *args.app_container]))
        assert [(o["Id"], o["State"]["StartedAt"], o["RestartCount"]) for o in after] == baseline
        journal("verified")
        result = {"status": "success", "settings_files": len(files), "new_admin_connection": "verified", "old_password": "authentication rejection verified", "runtime_connections": "unchanged and reconnect successfully", "backup": "custom archive validated", "backup_directory": str(backup), "containers": "same IDs, start times and restart counts", "initialization_environment": "old container initialization value is revoked; next planned recreation reads updated settings"}
        (backup / "result.json").write_text(json.dumps(result, indent=2))
        return result
    except Exception:
        # Restore both sides using the independent local administrative socket.
        if changed:
            alter(old_password)
        for path, raw, stat, expected in written:
            if path.read_bytes() != expected:
                journal("concurrent_settings_change_requires_reconciliation")
                raise RuntimeError("Concurrent settings retained; reconcile with protected maintenance state") from None
            atomic_private(path, raw, stat.st_uid, stat.st_gid)
        assert probe(old_url)["connected"]
        journal("rolled_back")
        raise RuntimeError("Rotation failed and original credentials were restored; inspect protected maintenance state") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", action="append", required=True)
    parser.add_argument("--db-container", default="ichiyon-robot-db")
    parser.add_argument("--app-container", action="append", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--backup-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(rotate(args)))
    except Exception as error:
        # RuntimeError messages above are static/redacted; never render driver,
        # subprocess or parser exceptions that might contain a credential.
        reason = str(error) if type(error) is RuntimeError else "sensitive diagnostics withheld"
        print("Credential maintenance stopped: " + type(error).__name__ + "; " + reason)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
