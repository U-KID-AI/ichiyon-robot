#!/usr/bin/env bash
# Reviewed fixed protocol. Never invoke this script from a generic task.
set -euo pipefail
umask 077
exec 3>&1
exec 4>&2
diagnostics=
report_diagnostics() {
    # Keep stdout reserved for the proof; the local adapter redacts stderr.
    if [[ -f $diagnostics ]]; then
        cat -- "$diagnostics" >&4
        rm -f -- "$diagnostics"
    fi
}
trap report_diagnostics EXIT
trap 'printf "Deployment command failed at protocol line %s\n" "$LINENO" >&2' ERR
export PATH=/usr/bin:/bin
export LC_ALL=C
unset CDPATH ENV BASH_ENV
repo_url=https://github.com/U-KID-AI/ichiyon-robot.git
project=ichiyon-robot
releases_root=/home/ubuntu/ichiyon-releases
current_link=/home/ubuntu/ichiyon-current
shared_root=/home/ubuntu/ichiyon-shared
backups_root=/home/ubuntu/ichiyon-backups
lock_path=/home/ubuntu/ichiyon-deploy.lock
db_volume=ichiyon-robot_postgres_data
network=ichiyon-robot_default
quiesced=0
previous=
helper=
infra_file=
stage=
prepared=
pointer=
storage_failed=0
operation_id=
cancelled=0
rollback_result=not_needed
backup_stage=

# Replaced only by the installed adapter with its sibling reviewed module.
# Running an unrendered template fails closed before staging or app mutation.
storage_check() {
    local phase=$1
    if python3 -I - "$phase" "$sha" >&3 <<'STORAGE_PY'
# __ICHIYON_STORAGE_MODULE__
raise SystemExit(1)  # A missing trusted module must never permit deployment.
STORAGE_PY
    then
        return 0
    else
        storage_failed=1
        printf 'STORAGE_CHECK_PHASE=%s\n' "$phase" >&3
        printf '%s\n' 'DEPLOY_ERROR=INSUFFICIENT_STORAGE' >&3
        exit 1
    fi
}

fail() { printf '%s\n' 'DEPLOY_ERROR=FAILED' >&3; printf '%s\n' 'Deployment safety check failed' >&2; exit 1; }
preflight_failed() { printf '%s\n' 'DEPLOY_ERROR=PREFLIGHT_FAILED' >&3; printf '%s\n' 'Deployment preflight failed' >&2; exit 1; }
lock_busy() { printf '%s\n' 'DEPLOY_ERROR=LOCK_BUSY' >&3; printf '%s\n' 'An existing deployment is in progress; lock retained. Wait for it to finish before retrying.' >&2; exit 1; }
[[ $# == 1 && $1 =~ ^[0-9a-f]{40}$ ]] || preflight_failed
sha=$1
release=$releases_root/$sha
image=ichiyon-robot-app:$sha

# All children inherit the lock. Losing SSH must not allow a concurrent writer.
probe() {
    [[ $(id -un) == ubuntu ]] || return 1
    for path in /home /home/ubuntu "$releases_root" "$shared_root" "$backups_root"; do
        [[ -d $path ]] || return 1
    done
    [[ $(stat -Lc %a "$shared_root") == 700 ]] || return 1
    [[ -f $shared_root/.env ]] || return 1
    [[ $(stat -Lc %a "$shared_root/.env") == 600 ]] || return 1
    for path in "$shared_root/data" "$shared_root/assets" "$shared_root/assets/images" "$shared_root/secrets"; do
        [[ -d $path ]] || return 1
    done
    [[ -L $current_link ]] || return 1
    [[ ! -L $lock_path ]] || return 1
    if [[ -e $lock_path ]]; then
        [[ -f $lock_path && $(stat -c %U "$lock_path") == ubuntu ]] || return 1
    fi
    command -v flock git docker python3 curl tar sha256sum >/dev/null
}
probe || preflight_failed
releases_root=$(realpath -e "$releases_root") || preflight_failed
release=$releases_root/$sha
exec 9>>"$lock_path" || preflight_failed
# Reserve a distinct contention exit code; other flock errors are preflight failures.
if flock -x -w 30 -E 75 9; then
    :
else
    lock_status=$?
    if [[ $lock_status == 75 ]]; then lock_busy; fi
    preflight_failed
fi
probe || preflight_failed

# This helper comes from SSH stdin, never from target/previous release code.
storage_check deploy-start
diagnostics=$(mktemp)
exec >"$diagnostics" 2>&1
helper=$(mktemp /home/ubuntu/.ichiyon-deploy-helper.XXXXXXXX.py)
cat >"$helper" <<'PY'
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time
import traceback

# __ICHIYON_DEPLOY_MODULES__

ROOT = Path('/home/ubuntu/ichiyon-releases')
SHARED = Path('/home/ubuntu/ichiyon-shared')
APPS = ('admin', 'bot', 'bot-irsia')
INFRA = ('db', 'youtube-vpn-proxy')
PROJECT = 'ichiyon-robot'
NETWORK = 'ichiyon-robot_default'
VOLUME = 'ichiyon-robot_postgres_data'

def run(args, *, data=None, stdin=None, timeout=120):
    result = subprocess.run(args, input=data, stdin=stdin, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f'{args[0]} failed (exit={result.returncode}): ' +
                           result.stderr.decode('utf-8', errors='replace') + '\nstdout:\n' +
                           result.stdout.decode('utf-8', errors='replace'))
    return result.stdout

def normal(path, directory=False):
    assert path.is_absolute()
    assert path.is_dir() if directory else path.is_file()

def tree(path):
    normal(path, True)
    result = {}
    for p in path.rglob('*'):
        assert p.is_file() or p.is_dir() or p.is_symlink()
        if p.is_symlink():
            result[str(p.relative_to(path))] = ('symlink', os.readlink(p))
        elif p.is_file():
            result[str(p.relative_to(path))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result

def immutable_image_metadata(path):
    metadata = path / 'immutable-image.txt'
    normal(metadata)

    text = metadata.read_text()
    tag = 'ichiyon-robot-app:' + path.name

    # Current Phase 3C+ format.
    if text in (tag, tag + '\n'):
        return {
            'format': 'current',
            'image_tag': tag,
            'image_id': None,
        }

    # Phase 3B used an explicit key=value manifest.
    # Only a release that also predates src/REVISION may use it.
    assert not (path / 'src/REVISION').exists()

    fields = {}

    for line in text.splitlines():
        assert line and '=' in line
        key, value = line.split('=', 1)
        assert key and value and key not in fields
        fields[key] = value

    required = {
        'release_sha',
        'image_tag',
        'image_id',
        'source',
        'compose_base',
        'compose_overlay',
        'shared_root',
        'database_volume',
        'network',
    }

    assert set(fields) == required

    assert fields['release_sha'] == path.name
    assert fields['image_tag'] == tag
    assert re.fullmatch(
        'sha256:[0-9a-f]{64}',
        fields['image_id'],
    )

    assert fields['source'] == str(path / 'src')
    assert fields['compose_base'] == str(
        path / 'src/docker-compose.yml'
    )
    assert fields['compose_overlay'] == str(
        path / 'compose.immutable.yml'
    )

    assert fields['shared_root'] == str(SHARED)
    assert fields['database_volume'] == VOLUME
    assert fields['network'] == NETWORK

    return {
        'format': 'phase3b',
        'image_tag': fields['image_tag'],
        'image_id': fields['image_id'],
    }


def release(path):
    assert path.parent.resolve() == ROOT.resolve() and re.fullmatch('[0-9a-f]{40}', path.name)
    normal(path, True)
    normal(path / 'REVISION')
    assert (path / 'REVISION').read_text() == path.name + '\n'
    tree(path / 'src')
    # Phase 3B immutable releases predate src/REVISION.
    # New releases always contain it, but a reviewed legacy previous
    # release remains valid without this later marker.
    src_revision = path / 'src/REVISION'
    if src_revision.exists():
        normal(src_revision)
        assert src_revision.read_text() == path.name + '\n'
    for name in ('compose.immutable.yml', 'immutable-image.txt', 'persistence.txt',
                 'rollback-images.txt', 'validate-immutable-compose.py'):
        normal(path / name)
    immutable_image_metadata(path)
    return path.name

def compose(path, *args):
    return run(['docker', 'compose', '--project-name', PROJECT,
                '--env-file', str(SHARED / '.env'), '--profile', 'bot', '--profile', 'irsia',
                '-f', str(path / 'src/docker-compose.yml'),
                '-f', str(path / 'compose.immutable.yml'), *args])

def contract(path, *, previous=False):
    sha = release(path)
    c = json.loads(compose(path, 'config', '--format', 'json'))

    legacy_previous = False

    if previous:
        metadata = immutable_image_metadata(path)
        legacy_previous = metadata['format'] == 'phase3b'

        if legacy_previous:
            assert not (path / 'src/REVISION').exists()

    assert c['volumes']['postgres_data']['name'] == VOLUME
    assert c['networks']['default']['name'] == NETWORK

    if legacy_previous:
        # Phase 3B predates the external-resource declaration.
        # Exact names and independently inspected live infra remain mandatory.
        assert c['volumes']['postgres_data'].get('external') in (None, False)
        assert c['networks']['default'].get('external') in (None, False)
    else:
        assert c['volumes']['postgres_data']['external'] is True
        assert c['networks']['default']['external'] is True
    for service in APPS:
        s = c['services'][service]
        assert s['image'] == 'ichiyon-robot-app:' + sha
        assert s['pull_policy'] == 'never' and 'build' not in s
        mounts = {(str(Path(v['source']).resolve()), v['target'], bool(v.get('read_only', False)), v['type'])
                  for v in s['volumes']}
        assert len(s['volumes']) == 3 and mounts == expected_mounts()
        assert all(v['target'] != '/app' for v in s['volumes'])
        assert set(s['networks']) == {'default'}
        assert not s.get('network_mode')
    dbmounts = c['services']['db']['volumes']
    assert any(v['source'] == 'postgres_data' and v['target'] == '/var/lib/postgresql/data'
               for v in dbmounts)

def expected_mounts():
    return {(str((SHARED / 'data').resolve()), '/app/data', False, 'bind'),
            (str((SHARED / 'assets/images').resolve()), '/app/assets/images', False, 'bind'),
            (str((SHARED / 'secrets').resolve()), '/app/secrets', True, 'bind')}

def container(service):
    ids = run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT,
               '--filter', 'label=com.docker.compose.service=' + service]).decode().split()
    assert len(ids) == 1
    return json.loads(run(['docker', 'inspect', ids[0]]))[0]

def infra():
    result = []
    for name in INFRA:
        c = container(name)
        assert c['Name'] == '/ichiyon-robot-' + name
        assert c['State']['Running']
        assert NETWORK in c['NetworkSettings']['Networks']
        if name == 'db':
            assert any(m.get('Name') == VOLUME and m['Destination'] == '/var/lib/postgresql/data'
                       for m in c['Mounts'])
        result.append([c['Id'], c['RestartCount'], c['State']['StartedAt']])
    return result

def image_check(sha, require_revision=True):
    tag = 'ichiyon-robot-app:' + sha
    image = json.loads(run(['docker', 'image', 'inspect', tag]))[0]
    assert image['Config']['Labels']['org.opencontainers.image.revision'] == sha
    assert not image['Config'].get('Volumes')
    # File extensions do not establish whether reviewed source contains secrets.
    # Build-context exclusions remain in .dockerignore; verify image identity here.
    code = "from pathlib import Path; import sys; marker=Path('/app/REVISION'); required=sys.argv[2]=='1'; assert (not required or marker.is_file()); assert (not marker.exists() or marker.is_file()); assert (not marker.exists() or marker.read_text()==sys.argv[1]+'\\n'); print('IMAGE_REVISION_VERIFIED')"
    out = run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges', '--entrypoint', 'python', tag, '-I', '-c', code, sha,
               '1' if require_revision else '0'])
    assert out == b'IMAGE_REVISION_VERIFIED\n'
    return image['Id']

def previous_image_check(path):
    sha = release(path)
    metadata = immutable_image_metadata(path)

    # Phase 3B images predate /app/REVISION exactly when their immutable
    # release source predates src/REVISION. New-format previous releases
    # therefore remain strict.
    require_revision = (path / 'src/REVISION').exists()

    image_id = image_check(
        sha,
        require_revision=require_revision,
    )

    # Bind the old Phase 3B manifest's recorded immutable image identity
    # to the actual locally installed Docker image.
    if metadata['format'] == 'phase3b':
        assert metadata['image_id'] == image_id

    return image_id


def health(path, expected_infra, migrations=True, previous=False):
    sha = release(path)
    contract(path, previous=previous)

    if previous:
        image_id = previous_image_check(path)
    else:
        image_id = image_check(sha)

    initial = None
    # Six samples over 30 seconds; startup gets a bounded separate retry window.
    for sample in range(7):
        ids = []
        for name in APPS:
            c = container(name)
            ids.append(c['Id'])
            assert c['State']['Running'] and c['RestartCount'] == 0
            assert c['Image'] == image_id and c['Config']['Image'] == 'ichiyon-robot-app:' + sha
            assert len(c['Mounts']) == 3
            assert {(str(Path(m['Source']).resolve()), m['Destination'], not m['RW'], m['Type']) for m in c['Mounts']} == expected_mounts()
            assert not any(m['Destination'] == '/app' for m in c['Mounts'])
            assert set(c['NetworkSettings']['Networks']) == {NETWORK}
            if name != 'admin':
                logs = run(['docker', 'logs', '--since', c['State']['StartedAt'], c['Id']])
                # Docker separates stdout/stderr logs. Include both if login failed.
                log_result = subprocess.run(['docker', 'logs', '--since', c['State']['StartedAt'], c['Id']],
                                            capture_output=True, check=True, timeout=30)
                assert b'Logged in as ' in logs + log_result.stderr, (logs + log_result.stderr).decode('utf-8', errors='replace')
        if initial is None:
            initial = ids
        assert ids == initial
        status = run(['curl', '--silent', '--output', '/dev/null', '--write-out', '%{http_code}',
                      '--max-time', '5', 'http://127.0.0.1:8000/openapi.json'])
        assert status == b'200'
        assert infra() == expected_infra
        if sample != 6:
            time.sleep(5)
    if migrations:
        expected = sorted(p.stem for p in (path / 'src/migrations').glob('*.sql'))
        assert expected
        # The image code uses the existing migration schema and connection helper.
        code = "import json,sys; sys.path.insert(0,'/app'); from scripts.migrate import get_connection,get_applied_versions\nwith get_connection() as c:\n print(json.dumps(sorted(get_applied_versions(c))))"
        actual = json.loads(run(['docker', 'exec', container('admin')['Id'], 'python', '-I', '-c', code]))
        assert actual == expected  # Exact set also proves head and count, rejects extra versions.

def prepare(stage, sha):
    archive = run(['git', '-C', str(stage / 'git'), 'archive', '--format=tar', sha])
    src = stage / 'src'
    src.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = tar.getmembers()
        for m in members:
            p = PurePosixPath(m.name)
            assert not p.is_absolute() and '..' not in p.parts
            assert m.isfile() or m.isdir() or m.issym() or m.islnk()
        # Check each destination after preceding links have been created. This
        # also supports production Python versions predating tar's data filter.
        root = src.resolve()
        for m in members:
            destination = (src / m.name).resolve()
            assert destination == root or root in destination.parents
            if m.issym() or m.islnk():
                target = ((destination.parent if m.issym() else src) / m.linkname).resolve()
                assert not PurePosixPath(m.linkname).is_absolute()
                assert target == root or root in target.parents
            if hasattr(tarfile, 'data_filter'):
                tar.extract(m, src, filter='data')
            else:
                tar.extract(m, src)
    (src / 'REVISION').write_text(sha + '\n')
    (stage / 'REVISION').write_text(sha + '\n')
    (stage / 'immutable-image.txt').write_text('ichiyon-robot-app:' + sha + '\n')
    (stage / 'persistence.txt').write_text('data RW /app/data\nassets/images RW /app/assets/images\nsecrets RO /app/secrets\n')
    (stage / 'rollback-images.txt').write_text('\n'.join(container(s)['Image'] for s in APPS) + '\n')
    overlay = 'services:\n'
    for name in APPS:
        overlay += ('  ' + name + ':\n    image: ichiyon-robot-app:' + sha +
                    '\n    pull_policy: never\n    build: !reset null\n    volumes: !override\n'
                    '      - /home/ubuntu/ichiyon-shared/data:/app/data:rw\n'
                    '      - /home/ubuntu/ichiyon-shared/assets/images:/app/assets/images:rw\n'
                    '      - /home/ubuntu/ichiyon-shared/secrets:/app/secrets:ro\n')
    overlay += ('volumes:\n  postgres_data:\n    external: true\n    name: ichiyon-robot_postgres_data\n'
                'networks:\n  default:\n    external: true\n    name: ichiyon-robot_default\n')
    (stage / 'compose.immutable.yml').write_text(overlay)
    (stage / 'validate-immutable-compose.py').write_bytes(Path(__file__).read_bytes())

def migration_state(sha):
    assert re.fullmatch('[0-9a-f]{40}', sha)
    name = 'ichiyon-robot-migrate-' + sha
    ids = run(['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$']).decode().split()
    owned = run(['docker', 'ps', '-aq', '--filter', 'label=ichiyon.fixed-deploy.migration=true']).decode().split()
    assert set(owned) <= set(ids)  # Other targets require human reconciliation.
    if not ids:
        return None
    assert len(ids) == 1
    c = json.loads(run(['docker', 'inspect', name]))[0]
    assert c['Name'] == '/' + name
    assert c['Config']['Labels']['ichiyon.fixed-deploy.migration'] == 'true'
    assert c['Config']['Image'] == 'ichiyon-robot-app:' + sha
    image = json.loads(run(['docker', 'image', 'inspect', 'ichiyon-robot-app:' + sha]))[0]
    assert c['Image'] == image['Id']
    assert c['Config']['Entrypoint'] == ['python']
    assert c['Config']['Cmd'] == ['-I', 'scripts/migrate.py']
    assert c['Config']['WorkingDir'] == '/app'
    assert c['Path'] == 'python' and c['Args'] == ['-I', 'scripts/migrate.py']
    assert c['HostConfig']['NetworkMode'] == NETWORK
    assert set(c['NetworkSettings']['Networks']) == {NETWORK}
    assert not c['Mounts'] and not c['HostConfig']['Privileged']
    assert not c['HostConfig'].get('Binds') and not c['HostConfig'].get('Devices')
    assert c['HostConfig']['RestartPolicy']['Name'] == 'no'
    assert not c['State']['Running'] and not c['State'].get('Paused')
    assert c['State']['Status'] == 'exited'  # Running/created/dead are inspectable, never killed.
    return c['State']['ExitCode']


def migration_database(path):
    expected = sorted(p.stem for p in (path / 'src/migrations').glob('*.sql'))
    assert expected
    sql = "SELECT COALESCE(json_agg(version ORDER BY version), '[]'::json) FROM schema_migrations"
    actual = json.loads(run(['docker', 'exec', 'ichiyon-robot-db', 'sh', '-c',
                            'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc ' +
                            "\"" + sql + "\""]))
    assert actual == expected


def migrate(path):
    sha = release(path)
    name = 'ichiyon-robot-migrate-' + sha
    state = migration_state(sha)
    if state == 0:
        migration_database(path)
        return
    if state is not None:
        # Identity and stopped state proved above; never force-remove.
        run(['docker', 'rm', name])
    run(['docker', 'run', '--name', name, '--label', 'ichiyon.fixed-deploy.migration=true',
         '--network', NETWORK, '--env-file', str(SHARED / '.env'), '--entrypoint', 'python',
         'ichiyon-robot-app:' + sha, '-I', 'scripts/migrate.py'], timeout=1800)
    assert migration_state(sha) == 0
    migration_database(path)


def backup_validate(path, previous, snapshot):
    normal(path, True)
    if (path / 'manifest.json').exists():
        # The new reader accepts both formats. No split writer is activated.
        assert (path / 'previous').read_text() == str(previous) + '\n'
        assert (path / 'infra.json').read_bytes() == snapshot.read_bytes()
        target = re.fullmatch(r'\.backup-([0-9a-f]{40})\.[A-Za-z0-9]{8}', path.name)
        if target:
            ai_task_backup.validate_staged_backup(path, target[1], dump_validator=backup_dump_readable)
        else:
            ai_task_backup.validate_backup(path, dump_validator=backup_dump_readable)
        return
    names = {'previous', 'infra.json', 'production.dump', 'persistence.tar', 'checksums.sha256', 'READY'}
    assert {p.name for p in path.iterdir()} == names
    for name in names:
        normal(path / name)
    assert (path / 'previous').read_text() == str(previous) + '\n'
    assert (path / 'infra.json').read_bytes() == snapshot.read_bytes()
    # Fixed manifest grammar prevents arbitrary paths/options in sha256sum --check.
    expected = ''
    for name in ('production.dump', 'persistence.tar'):
        digest = hashlib.sha256()
        with (path / name).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        expected += digest.hexdigest() + '  ' + name + '\n'
    assert (path / 'checksums.sha256').read_text() == expected
    assert (path / 'production.dump').stat().st_size > 0
    with (path / 'production.dump').open('rb') as stream:
        run(['docker', 'exec', '-i', 'ichiyon-robot-db', 'pg_restore', '--list'], stdin=stream)
    with tarfile.open(path / 'persistence.tar') as archive:
        members = archive.getmembers()
        roots = set()
        for m in members:
            p = PurePosixPath(m.name)
            assert not p.is_absolute() and '..' not in p.parts
            assert m.isfile() or m.isdir()
            assert p.parts and (p.parts[0] in ('data', 'secrets') or
                                m.name == '.env' or p.parts[:2] == ('assets', 'images'))
            roots.add(m.name.rstrip('/'))
            if m.isfile():
                with archive.extractfile(m) as stream:
                    size = sum(len(chunk) for chunk in iter(lambda: stream.read(1024 * 1024), b''))
                assert size == m.size
        assert {'data', 'assets/images', 'secrets', '.env'} <= roots


def backup_dump_readable(path):
    with ai_task_backup.regular(path) as stream:
        return bool(run(['docker', 'exec', '-i', 'ichiyon-robot-db', 'pg_restore', '--list'], stdin=stream))


def evidence_command(args):
    # Fixed API verbs and validated identities, never a task-supplied path root.
    verb = args[0]
    if verb == 'begin':
        assert len(args) == 3
        print(ai_task_storage_evidence.begin(args[1], int(args[2])))
    elif verb == 'stage':
        assert len(args) == 5
        ai_task_storage_evidence.record_stage(args[1], int(args[2]), args[3], args[4])
    elif verb == 'observe':
        assert len(args) == 4
        ai_task_storage_evidence.observe(args[1], int(args[2]), args[3])
    elif verb == 'finish':
        assert len(args) == 5
        ai_task_storage_evidence.finish(args[1], int(args[2]), args[3], args[4])
    else:
        raise ValueError('evidence_verb_invalid')


def cleanup(path, sha):
    assert re.fullmatch('[0-9a-f]{40}', sha)
    allowed = ((ROOT, '.prepare-' + sha + '.'), (ROOT, '.release-' + sha + '.'),
               (Path('/home/ubuntu'), '.ichiyon-pointer.'))
    assert any(path.parent.resolve() == parent.resolve() and re.fullmatch(re.escape(prefix) + '[A-Za-z0-9]{8}', path.name)
               for parent, prefix in allowed)
    normal(path, True)
    assert not path.is_symlink()  # This must be our actual mktemp directory.
    # rmtree unlinks archived symlinks without following their targets.
    shutil.rmtree(path)


def main():
    mode = sys.argv[1]
    if mode == 'evidence':
        evidence_command(sys.argv[2:])
        return
    path = Path(sys.argv[2])
    if mode == 'migration-idle':
        migration_state(path.name)
    elif mode == 'migrate':
        migrate(path)
    elif mode == 'migration-cleanup':
        if migration_state(path.name) == 0:
            migration_database(path)
            run(['docker', 'rm', 'ichiyon-robot-migrate-' + path.name])
    elif mode == 'backup':
        backup_validate(path, Path(sys.argv[3]), Path(sys.argv[4]))
    elif mode == 'backup-full':
        ai_task_backup.write_full_backup_metadata(path, sys.argv[3], dump_validator=backup_dump_readable)
    elif mode == 'cleanup':
        cleanup(path, sys.argv[3])
    elif mode == 'release':
        release(path)
    elif mode == 'contract':
        contract(path)
    elif mode == 'previous-contract':
        contract(path, previous=True)
    elif mode == 'prepare':
        prepare(path, sys.argv[3])
    elif mode == 'compare':
        other = Path(sys.argv[3])
        assert tree(path / 'src') == tree(other / 'src')
        for name in ('REVISION', 'compose.immutable.yml', 'immutable-image.txt', 'persistence.txt', 'validate-immutable-compose.py'):
            normal(path / name)
            assert (path / name).read_bytes() == (other / name).read_bytes()
    elif mode == 'compare-source':
        assert tree(path / 'src') == tree(Path(sys.argv[3]) / 'src')
    elif mode == 'infra':
        print(json.dumps(infra()))
    elif mode == 'image':
        image_check(path.name)
    elif mode == 'previous-image':
        previous_image_check(path)
    elif mode == 'health':
        rollback = len(sys.argv) == 5

        if rollback:
            assert sys.argv[4] == 'rollback'
        else:
            assert len(sys.argv) == 4

        health(
            path,
            json.loads(Path(sys.argv[3]).read_text()),
            migrations=not rollback,
            previous=rollback,
        )
    else:
        raise ValueError

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        detail = (f'process timed out after {exc.timeout}s: {exc.stderr!r}'
                  if isinstance(exc, subprocess.TimeoutExpired) else str(exc))
        if isinstance(exc, subprocess.CalledProcessError):
            detail += '\n' + (exc.stderr or b'').decode('utf-8', errors='replace')
            detail += '\nstdout:\n' + (exc.stdout or b'').decode('utf-8', errors='replace')
        print(f'Deployment {sys.argv[1]} failed in {frame.name}:{frame.lineno}: '
              f'{type(exc).__name__}: {detail}', file=sys.stderr)
        sys.exit(1)
PY

migration_idle() { python3 -I "$helper" migration-idle "$release"; }
evidence() { python3 -I "$helper" evidence "$@"; }
evidence_phase() { evidence observe "$operation_id" "$$" "$1"; }

compose() {
    local base=$1
    shift
    docker compose --project-name "$project" --env-file "$shared_root/.env" \
        --profile bot --profile irsia -f "$base/src/docker-compose.yml" \
        -f "$base/compose.immutable.yml" "$@"
}
infra_same() {
    local actual expected
    actual=$(python3 -I "$helper" infra "$release") || return 1
    expected=$(cat "$infra_file") || return 1
    [[ -n $expected && $actual == "$expected" ]]
}
health() {
    local base=$1
    local attempt
    for attempt in 1 2 3 4 5 6; do
        if python3 -I "$helper" health "$base" "$infra_file"; then return 0; fi
        evidence_phase health_retry || return 1
        sleep 5
    done
    return 1
}
cleanup_temporaries() {
    local temporary
    for temporary in "$stage" "$prepared" "$pointer"; do
        if [[ -n $temporary && -d $temporary ]]; then
            python3 -I "$helper" cleanup "$temporary" "$sha" || return 1
        fi
    done
    if [[ $infra_file == /home/ubuntu/.ichiyon-deploy-infra.* && -f $infra_file && ! -L $infra_file ]]; then
        rm -f -- "$infra_file" || return 1
    fi
}
on_exit() {
    local code=$?
    trap - EXIT HUP INT TERM
    if (( code != 0 )); then
        if (( quiesced == 1 )) && [[ -n $previous ]]; then
            # Explicit checks: never rely on errexit inside a conditional/trap.
            if migration_idle && python3 -I "$helper" previous-contract "$previous" && infra_same &&
                compose "$previous" up -d --no-deps --no-build --pull never --force-recreate admin bot bot-irsia &&
                infra_same && python3 -I "$helper" health "$previous" "$infra_file" rollback; then
                rollback_result=succeeded
                printf '%s\n' 'DEPLOY_ERROR=ROLLED_BACK' >&3
            else
                rollback_result=failed
                printf '%s\n' 'DEPLOY_ERROR=ROLLBACK_FAILED' >&3
            fi
        elif (( storage_failed == 0 )); then
            printf '%s\n' 'DEPLOY_ERROR=FAILED' >&3
        fi
    fi
    if (( code == 0 )) && [[ -n $operation_id ]]; then
        # The final interpreter reads our already-open helper inode. Unlink
        # successful diagnostics and the helper before a C receipt can commit;
        # a crash after commit therefore leaves neither persistent artifact.
        exec 1>&3 2>&4
        if [[ -f $helper && ! -L $helper && $helper == /home/ubuntu/.ichiyon-deploy-helper.*.py ]] &&
            exec 8<"$helper" && rm -f -- "$diagnostics" "$helper" &&
            [[ ! -e $diagnostics && ! -L $diagnostics && ! -e $helper && ! -L $helper ]]; then
            helper=/proc/self/fd/8
            if ! evidence_phase ancillary_cleanup; then code=1; fi
        else
            code=1
        fi
    fi
    if [[ -n $operation_id ]]; then
        local terminal=failed
        if (( code == 0 )); then terminal=succeeded; fi
        if (( cancelled == 1 )); then terminal=cancelled; fi
        # An evidence failure must not interrupt rollback or turn failure into
        # success. No partial receipt is promoted by this best-effort final step.
        if ! evidence finish "$operation_id" "$$" "$terminal" "$rollback_result"; then
            printf '%s\n' 'DEPLOY_EVIDENCE=INCOMPLETE' >&4
            if (( code == 0 )); then code=1; fi
        fi
    fi
    # Only our mktemp helper is unlinked; interrupted staging is left inspectable.
    if [[ $helper == /home/ubuntu/.ichiyon-deploy-helper.*.py ]]; then rm -f -- "$helper"; fi
    if (( code != 0 )); then
        report_diagnostics
    else
        rm -f -- "$diagnostics"
    fi
    exit "$code"
}
trap on_exit EXIT
trap 'cancelled=1; exit 1' HUP INT TERM

operation_id=$(evidence begin "$sha" "$$")
[[ $operation_id =~ ^[0-9a-f]{32}$ ]] || fail
migration_idle || fail

previous=$(readlink -e "$current_link")
python3 -I "$helper" release "$previous"
infra_file=$(mktemp /home/ubuntu/.ichiyon-deploy-infra.XXXXXXXX)
python3 -I "$helper" infra "$release" >"$infra_file"

# Even an idempotent request must still name exact GitHub main.
stage=$(mktemp -d "$releases_root/.prepare-$sha.XXXXXXXX")
evidence stage "$operation_id" "$$" prepare "$stage"
evidence_phase prepare
git -c core.hooksPath=/dev/null init --bare "$stage/git"
git -c core.hooksPath=/dev/null -C "$stage/git" fetch --depth=1 "$repo_url" refs/heads/main
[[ $(git -C "$stage/git" rev-parse FETCH_HEAD) == "$sha" ]]
python3 -I "$helper" prepare "$stage" "$sha"

# Healthy current reconciliation never rebuilds images or restarts app services.
if [[ $previous == "$release" ]]; then
    evidence_phase reconcile
    python3 -I "$helper" compare-source "$release" "$stage"
    health "$release"
    evidence_phase health
    python3 -I "$helper" migration-cleanup "$release"
else
    if [[ -e $release || -L $release ]]; then
        python3 -I "$helper" release "$release"
        python3 -I "$helper" compare "$release" "$stage"
    else
        # Publish only the source/artifacts, never the dedicated git metadata.
        prepared=$(mktemp -d "$releases_root/.release-$sha.XXXXXXXX")
        evidence stage "$operation_id" "$$" release "$prepared"
        for name in REVISION compose.immutable.yml immutable-image.txt persistence.txt rollback-images.txt validate-immutable-compose.py src; do
            mv -- "$stage/$name" "$prepared/$name"
        done
        mv -T -- "$prepared" "$release"
    fi
    evidence_phase release
    # Refine the estimate with the actual fetched source before a new build.
    storage_check pre-build
    if ! docker image inspect "$image"; then
        docker build --label "org.opencontainers.image.revision=$sha" --tag "$image" "$release/src"
    fi
    python3 -I "$helper" image "$release"
    evidence_phase image
    python3 -I "$helper" contract "$release"
    python3 -I "$helper" previous-contract "$previous"
    python3 -I "$helper" previous-image "$previous"
    infra_same
    [[ $(readlink -e "$current_link") == "$previous" ]]
    backup=$backups_root/$sha
    [[ ! -L $backup || -d $backup ]]
    if [[ -e $backup ]]; then
        python3 -I "$helper" backup "$backup" "$previous" "$infra_file"
    fi
    # Re-measure after build, immediately before accepting rollback obligation.
    # Failure exits with quiesced=0: no app stop and no rollback recreation.
    storage_check pre-stop
    # Set rollback obligation before the first operation that can stop an app.
    quiesced=1
    compose "$previous" stop admin bot bot-irsia
    infra_same
    if [[ ! -e $backup ]]; then
        # Old .backup-<sha>.* staging is ignored and retained for inspection.
        backup_stage=$(mktemp -d "$backups_root/.backup-$sha.XXXXXXXX")
        evidence stage "$operation_id" "$$" backup "$backup_stage"
        evidence_phase backup
        printf '%s\n' "$previous" >"$backup_stage/previous"
        cp -- "$infra_file" "$backup_stage/infra.json"
        docker exec ichiyon-robot-db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$backup_stage/production.dump"
        tar --dereference -C "$shared_root" -cf "$backup_stage/persistence.tar" data assets/images secrets .env
        python3 -I "$helper" backup-full "$backup_stage" "$sha"
        python3 -I "$helper" backup "$backup_stage" "$previous" "$infra_file"
        sync -f "$backup_stage"
        # No replacement, including empty directories. The deployment lock serializes publishers.
        mv -Tn -- "$backup_stage" "$backup"
        [[ ! -e $backup_stage ]]
        sync -f "$backups_root"
    fi
    python3 -I "$helper" backup "$backup" "$previous" "$infra_file"
    infra_same
    python3 -I "$helper" migrate "$release"
    evidence_phase migrate
    compose "$release" up -d --no-deps --no-build --pull never --force-recreate admin bot bot-irsia
    health "$release"
    evidence_phase health
    infra_same
    [[ $(readlink -e "$current_link") == "$previous" ]]
    # Atomic pointer publication is strictly after full health validation.
    pointer=$(mktemp -d /home/ubuntu/.ichiyon-pointer.XXXXXXXX)
    ln -s -- "$release" "$pointer/current"
    mv -Tf -- "$pointer/current" "$current_link"
    quiesced=0
    python3 -I "$helper" migration-cleanup "$release"
    sync -f /home/ubuntu
fi
[[ $(readlink -e "$current_link") == "$release" ]]
cleanup_temporaries
evidence_phase cleanup
printf 'DEPLOY_RESULT=SUCCESS\nDEPLOYED_COMMIT_SHA=%s\nDEPLOY_SUMMARY=Immutable app deployment verified.\n' "$sha" >&3
