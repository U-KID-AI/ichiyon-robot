"""Trusted remote BDS deployment transaction.

This file is a template. The local trusted adapter replaces exactly one
archive placeholder with base64 for an exact reviewed Git commit, then sends
the resulting Python program over SSH stdin.

Only trusted configuration is accepted as argv. Task text never reaches here.
"""

import base64
import binascii
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from uuid import UUID


MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILES = 10000
KINDS = ("behavior_packs", "resource_packs")
WORLD_FILES = {
    "behavior_packs": "world_behavior_packs.json",
    "resource_packs": "world_resource_packs.json",
}
ARCHIVE_B64 = """__ICHYON_ARCHIVE_BASE64__"""


def fail():
    raise RuntimeError("deployment rejected")


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def safe_atom(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value) is not None
        and value not in (".", "..")
    )


def safe_tar_part(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", value) is not None
        and not value.endswith(".")
    )


def strict_json_bytes(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail()
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: fail(),
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        fail()


def validate_uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value.lower():
            fail()
    except ValueError:
        fail()
    return value.lower()


def validate_version(value):
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(part) is not int or not 0 <= part <= 2147483647 for part in value)
    ):
        fail()
    return list(value)


def manifest_identity(raw):
    obj = strict_json_bytes(raw)
    if not isinstance(obj, dict):
        fail()
    header = obj.get("header")
    if not isinstance(header, dict):
        fail()
    return (
        validate_uuid(header.get("uuid")),
        validate_version(header.get("version")),
    )


def validate_world_entries(raw):
    entries = strict_json_bytes(raw)
    if not isinstance(entries, list):
        fail()

    seen = set()
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail()
        uuid = validate_uuid(entry.get("pack_id"))
        validate_version(entry.get("version"))
        if uuid in seen:
            fail()
        seen.add(uuid)
        result.append(dict(entry))
    return result


def sync_world(entries, manifests, removed_uuids):
    desired = {}
    for raw in manifests:
        uuid, version = manifest_identity(raw)
        if uuid in desired:
            fail()
        desired[uuid] = version

    output = []
    seen = set()

    for entry in entries:
        uuid = validate_uuid(entry.get("pack_id"))
        if uuid in removed_uuids and uuid not in desired:
            continue
        if uuid in desired:
            entry = {**entry, "version": desired[uuid]}
            seen.add(uuid)
        output.append(entry)

    for uuid, version in desired.items():
        if uuid not in seen:
            output.append({"pack_id": uuid, "version": version})

    return output


def canonical_json(value):
    return (
        json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        + b"\n"
    )


def validate_normal_dir(path):
    try:
        if not path.is_absolute() or not path.is_dir() or path.is_symlink():
            fail()
        if path.resolve(strict=True) != path:
            fail()
        for parent in (path, *path.parents):
            if parent.is_symlink():
                fail()
    except OSError:
        fail()


def docker(args):
    result = subprocess.run(
        ["docker", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        fail()
    return result.stdout


def inspect_container(container):
    raw = docker(["inspect", container])
    try:
        info = json.loads(raw)
    except ValueError:
        fail()
    if not isinstance(info, list) or len(info) != 1 or not isinstance(info[0], dict):
        fail()
    return info[0]


def runtime_ready(container):
    info = inspect_container(container)
    state = info.get("State")
    if not isinstance(state, dict) or state.get("Running") is not True:
        return False
    health = state.get("Health")
    if health is not None:
        if not isinstance(health, dict) or health.get("Status") != "healthy":
            return False
    return True


def wait_stable(container, timeout=180):
    deadline = time.monotonic() + timeout
    consecutive = 0
    while time.monotonic() < deadline:
        try:
            ready = runtime_ready(container)
        except Exception:
            ready = False
        if ready:
            consecutive += 1
            if consecutive >= 3:
                return
        else:
            consecutive = 0
        time.sleep(5)
    fail()


def parse_archive(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_ARCHIVE_BYTES:
        fail()

    files = {}
    names = set()
    total = 0

    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                name = member.name.rstrip("/") if member.isdir() else member.name
                parts = name.split("/")

                if (
                    not name
                    or name in names
                    or len(names) >= MAX_FILES
                    or any(not safe_tar_part(part) for part in parts)
                    or parts[0] != "minecraft"
                    or (len(parts) > 1 and parts[1] not in KINDS)
                    or not (member.isfile() or member.isdir())
                    or (member.isfile() and len(parts) < 4)
                ):
                    fail()

                names.add(name)

                if member.isfile():
                    if member.size < 0:
                        fail()
                    total += member.size
                    if total > MAX_ARCHIVE_BYTES:
                        fail()
                    stream = archive.extractfile(member)
                    if stream is None:
                        fail()
                    content = stream.read(member.size + 1)
                    if len(content) != member.size:
                        fail()
                    files[name] = content
    except (tarfile.TarError, OSError, KeyError, ValueError):
        fail()

    if not files:
        fail()

    for name in files:
        parts = name.split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in files:
                fail()

    packs = {kind: {} for kind in KINDS}

    for name, content in files.items():
        parts = name.split("/")
        kind = parts[1]
        pack = parts[2]
        packs[kind].setdefault(pack, {})["/".join(parts[3:])] = content

    for kind in KINDS:
        for pack, pack_files in packs[kind].items():
            if "manifest.json" not in pack_files:
                fail()
            manifest_identity(pack_files["manifest.json"])

    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode("ascii") + b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)

    return files, packs, digest.hexdigest()


def read_state(path):
    if path.is_symlink():
        fail()

    if not path.exists():
        return {"version": 1, "packs": {kind: {} for kind in KINDS}}

    if not path.is_file():
        fail()

    value = strict_json_bytes(path.read_bytes())
    if not isinstance(value, dict) or value.get("version") != 1:
        fail()

    packs = value.get("packs")
    if not isinstance(packs, dict) or set(packs) != set(KINDS):
        fail()

    clean = {"version": 1, "packs": {}}
    for kind in KINDS:
        mapping = packs[kind]
        if not isinstance(mapping, dict):
            fail()
        clean["packs"][kind] = {}
        for name, uuid in mapping.items():
            if not safe_atom(name):
                fail()
            clean["packs"][kind][name] = validate_uuid(uuid)

    tree_hash = value.get("tree_hash")
    if tree_hash is not None:
        if not isinstance(tree_hash, str) or re.fullmatch(r"[0-9a-f]{64}", tree_hash) is None:
            fail()
        clean["tree_hash"] = tree_hash

    return clean


def desired_state(packs, tree_hash):
    mapping = {}
    for kind in KINDS:
        mapping[kind] = {}
        for pack, pack_files in sorted(packs[kind].items()):
            uuid, _ = manifest_identity(pack_files["manifest.json"])
            mapping[kind][pack] = uuid
    return {
        "version": 1,
        "packs": mapping,
        "tree_hash": tree_hash,
    }


def current_tree_matches(root, expected):
    if not root.exists() or not root.is_dir() or root.is_symlink():
        return False

    actual = {}
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                return False
            if path.is_file():
                actual[path.relative_to(root).as_posix()] = path.read_bytes()
            elif not path.is_dir():
                return False
    except OSError:
        return False

    return actual == expected


def write_atomic(path, raw):
    tmp = path.with_name(path.name + ".ichiyon-tmp")

    if os.path.lexists(tmp):
        if tmp.is_symlink() or not tmp.is_file():
            fail()
        tmp.unlink()

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            if (
                os.path.lexists(tmp)
                and not tmp.is_symlink()
                and tmp.is_file()
            ):
                tmp.unlink()
        except OSError:
            pass
        fail()


def restore_file(path, existed, raw):
    if existed:
        write_atomic(path, raw)
    elif os.path.lexists(path):
        if path.is_dir() or path.is_symlink():
            fail()
        path.unlink()


def require_git_pack_ownership(data_root):
    # The Web compiler includes DB-only assets absent from a Git archive. Once
    # it owns the packs, exact-Git catch-up would erase those assets on every
    # idle runner tick. Reject before any pack/state/health/restart operations;
    # never issue an exact-Git deployment proof for Web-generated content.
    managed = data_root.parent / "cosmetics-applications"
    if managed.is_symlink() or os.path.lexists(managed / "active.json"):
        fail()


def main():
    if len(sys.argv) != 5:
        fail()

    merge_sha, data_arg, world_name, container = sys.argv[1:]

    if not valid_sha(merge_sha) or not safe_atom(world_name) or not safe_atom(container):
        fail()

    data_root = Path(data_arg)
    if (
        not data_root.is_absolute()
        or "\x00" in data_arg
        or any(ord(char) < 32 or ord(char) == 127 for char in data_arg)
    ):
        fail()

    validate_normal_dir(data_root)

    behavior_root = data_root / "behavior_packs"
    resource_root = data_root / "resource_packs"
    world_root = data_root / "worlds" / world_name

    for path in (behavior_root, resource_root, world_root):
        validate_normal_dir(path)

    state_path = data_root / ".ichiyon-ai-managed-packs.json"
    lock_path = Path.home() / ".ichiyon-ai-bds-deploy.lock"

    try:
        compact = "".join(ARCHIVE_B64.splitlines())
        raw_archive = base64.b64decode(compact, validate=True)
    except (ValueError, binascii.Error):
        fail()

    files, packs, tree_hash = parse_archive(raw_archive)

    lock_path.touch(mode=0o600, exist_ok=True)
    with open(lock_path, "r+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        require_git_pack_ownership(data_root)

        previous_state = read_state(state_path)
        next_state = desired_state(packs, tree_hash)

        removed = {}
        for kind in KINDS:
            previous = previous_state["packs"][kind]
            desired = next_state["packs"][kind]
            removed[kind] = {
                uuid for name, uuid in previous.items()
                if name not in desired
            }

        world_paths = {
            kind: world_root / WORLD_FILES[kind]
            for kind in KINDS
        }

        world_before = {}
        world_desired = {}

        for kind in KINDS:
            path = world_paths[kind]
            if not path.is_file() or path.is_symlink():
                fail()
            raw = path.read_bytes()
            entries = validate_world_entries(raw)
            manifests = [
                pack_files["manifest.json"]
                for _, pack_files in sorted(packs[kind].items())
            ]
            desired_entries = sync_world(entries, manifests, removed[kind])
            world_before[kind] = raw
            world_desired[kind] = desired_entries

        changed = False

        roots = {
            "behavior_packs": behavior_root,
            "resource_packs": resource_root,
        }

        for kind in KINDS:
            previous_names = set(previous_state["packs"][kind])
            desired_names = set(packs[kind])

            for name in desired_names:
                if not current_tree_matches(roots[kind] / name, packs[kind][name]):
                    changed = True

            for name in previous_names - desired_names:
                if (roots[kind] / name).exists():
                    changed = True

            current_entries = validate_world_entries(world_before[kind])
            if current_entries != world_desired[kind]:
                changed = True

        if not changed:
            wait_stable(container)
            write_atomic(state_path, canonical_json(next_state))
            print("MINECRAFT_DEPLOY_RESULT=SUCCESS")
            print("DEPLOYED_COMMIT_SHA=" + merge_sha)
            print("MINECRAFT_TREE_SHA256=" + tree_hash)
            print("MINECRAFT_CHANGED=0")
            return

        # Never mutate a production BDS that is already unhealthy.
        wait_stable(container)

        stage = Path(tempfile.mkdtemp(prefix=".ichiyon-ai-stage-", dir=data_root))
        backup = Path(tempfile.mkdtemp(prefix=".ichiyon-ai-backup-", dir=data_root))
        moved = []
        state_existed = os.path.lexists(state_path)
        if state_existed:
            if state_path.is_symlink() or not state_path.is_file():
                fail()
            state_before = state_path.read_bytes()
        else:
            state_before = b""
        rollback_failed = False

        try:
            for name, content in files.items():
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "wb") as stream:
                    stream.write(content)

            was_running = bool(inspect_container(container).get("State", {}).get("Running"))
            if was_running:
                docker(["stop", "--time", "60", container])

            for kind in KINDS:
                previous_names = set(previous_state["packs"][kind])
                desired_names = set(packs[kind])

                for name in sorted(previous_names | desired_names):
                    if not safe_atom(name):
                        fail()

                    destination = roots[kind] / name
                    old = backup / kind / name
                    old.parent.mkdir(parents=True, exist_ok=True)

                    if os.path.lexists(destination):
                        if not destination.is_dir() or destination.is_symlink():
                            fail()
                        os.replace(destination, old)
                        moved.append((destination, old, True))
                    else:
                        moved.append((destination, old, False))

                    if name in desired_names:
                        staged = stage / "minecraft" / kind / name
                        if not staged.is_dir() or staged.is_symlink():
                            fail()
                        os.replace(staged, destination)

            for kind in KINDS:
                write_atomic(
                    world_paths[kind],
                    canonical_json(world_desired[kind]),
                )

            write_atomic(state_path, canonical_json(next_state))

            docker(["start", container])
            wait_stable(container)

            for kind in KINDS:
                for name, expected in packs[kind].items():
                    if not current_tree_matches(roots[kind] / name, expected):
                        fail()

                for name in set(previous_state["packs"][kind]) - set(packs[kind]):
                    if (roots[kind] / name).exists():
                        fail()

                deployed_entries = validate_world_entries(world_paths[kind].read_bytes())
                if deployed_entries != world_desired[kind]:
                    fail()

        except Exception:
            try:
                info = inspect_container(container)
                if info.get("State", {}).get("Running"):
                    docker(["stop", "--time", "60", container])

                for destination, old, existed in reversed(moved):
                    if os.path.lexists(destination):
                        if destination.is_symlink() or not destination.is_dir():
                            fail()
                        shutil.rmtree(destination)

                    if existed:
                        if not old.is_dir() or old.is_symlink():
                            fail()
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(old, destination)

                for kind in KINDS:
                    write_atomic(world_paths[kind], world_before[kind])

                restore_file(state_path, state_existed, state_before)

                docker(["start", container])
                wait_stable(container)
            except Exception:
                rollback_failed = True
            fail()
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            if not rollback_failed:
                shutil.rmtree(backup, ignore_errors=True)

        print("MINECRAFT_DEPLOY_RESULT=SUCCESS")
        print("DEPLOYED_COMMIT_SHA=" + merge_sha)
        print("MINECRAFT_TREE_SHA256=" + tree_hash)
        print("MINECRAFT_CHANGED=1")


try:
    main()
except Exception:
    sys.exit(1)
