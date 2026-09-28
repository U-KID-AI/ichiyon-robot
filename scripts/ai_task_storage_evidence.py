"""Durable evidence for the fixed deployment protocol, never a cleanup executor.

Writers require the live protocol owner's boot/PID/start identity and its exact
FD 9 flock. Receipts contain metadata and hashes only. A terminal receipt is a
single atomically published, checksummed document; active or temporary files
never substitute for it. Existing generation/staging names are not enrolled.

The checksum detects damage, not a malicious actor with the deployment user's
privileges. Trusted code, fixed paths, owner/mode checks and fresh observations
are the authority boundary. No command accepts an arbitrary evidence root.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time
import uuid


HOME = Path('/home/ubuntu')
ROOT = HOME / 'ichiyon-storage-evidence'
RELEASES = HOME / 'ichiyon-releases'
BACKUPS = HOME / 'ichiyon-backups'
CURRENT = HOME / 'ichiyon-current'
LOCK = HOME / 'ichiyon-deploy.lock'
PROC = Path('/proc')
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_EVENTS = 32
MAX_NAMES = 8192
SCHEMA = 'ichiyon-deployment-evidence'
PHASES = {'preflight', 'prepare', 'release', 'image', 'backup', 'migrate',
          'health', 'health_retry', 'reconcile', 'cleanup', 'ancillary_cleanup', 'rollback'}
TERMINAL = {'succeeded', 'failed', 'cancelled'}
ROLLBACK = {'not_needed', 'not_attempted', 'succeeded', 'failed', 'unknown'}
SHA = re.compile(r'[0-9a-f]{40}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
OPID = re.compile(r'[0-9a-f]{32}\Z')
BOOT = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
NAME = re.compile(r'[A-Za-z0-9_.-]{1,180}\Z')
IMAGE_FORMAT = '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
MIGRATION_FORMAT = '{{.Id}} {{.Image}} {{.State.Status}} {{.Name}}'


class EvidenceError(ValueError):
    """Stable diagnostic code only; never include file or process contents."""


def need(value, code):
    if not value:
        raise EvidenceError(code)


def _match(expression, value):
    return isinstance(value, str) and expression.fullmatch(value) is not None


def _positive(value):
    return type(value) is int and value > 0


def _timestamp(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError('invalid_json_number')))


def _uid():
    import pwd
    return pwd.getpwnam('ubuntu').pw_uid


def _signature(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink]


def _owner_mode(info, mode):
    need(info.st_uid == _uid() and stat.S_IMODE(info.st_mode) == mode,
         'evidence_owner_or_mode_invalid')


def _normal_directory(path):
    path = Path(path)
    for part in [path] + list(path.parents):
        info = part.lstat()
        need(stat.S_ISDIR(info.st_mode) and not part.is_symlink(), 'directory_symlink_or_alias')
    need(path.is_absolute() and path.resolve(strict=True) == path, 'directory_symlink_or_alias')
    return path


def _secure_root(create=False):
    _normal_directory(HOME)
    for path in (ROOT, ROOT / 'active', ROOT / 'operations', ROOT / '.pending'):
        created = False
        if create:
            try:
                path.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                pass
        _normal_directory(path)
        _owner_mode(path.lstat(), 0o700)
        need(path.stat().st_dev == HOME.stat().st_dev, 'evidence_mount_crossing')
        if created:
            _fsync_directory(path)
            _fsync_directory(path.parent)
    return ROOT


def _read(path, limit=MAX_DOCUMENT_BYTES, secure=False):
    _normal_directory(path.parent)
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
         'evidence_file_not_exclusive_regular')
    if secure:
        _owner_mode(before, 0o600)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino), 'evidence_file_changed')
        data = stream.read(limit + 1)
        need(len(data) <= limit and _signature(path.lstat()) == _signature(before), 'evidence_file_changed')
    return data


def _fsync_directory(path):
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(path, payload, terminal=False):
    _secure_root()
    need(path.parent in (ROOT / 'active', ROOT / 'operations') and path.name == payload['operation_id'] + '.json',
         'evidence_publish_path_invalid')
    if path.exists() or path.is_symlink():
        need(not terminal, 'terminal_receipt_already_exists')
        _read(path, secure=True)
    envelope = dict(schema=SCHEMA, version=1, payload=payload, sha256=digest(payload))
    data = encoded(envelope) + b'\n'
    need(len(data) <= MAX_DOCUMENT_BYTES, 'evidence_document_limit')
    temporary = ROOT / '.pending' / (payload['operation_id'] + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    # The owner's deployment lock serializes publishers. UUID and final-name
    # checks prevent replacing another operation, including on a retry.
    need(not terminal or not path.exists(), 'terminal_receipt_already_exists')
    os.replace(str(temporary), str(path))
    _fsync_directory(path.parent)
    return envelope['sha256']


def _process(pid):
    need(_positive(pid), 'owner_pid_invalid')
    path = PROC / str(pid)
    raw = (path / 'stat').read_text(encoding='ascii')
    end = raw.rfind(')')
    need(end >= 0, 'owner_stat_invalid')
    fields = raw[end + 1:].split()
    need(len(fields) >= 20, 'owner_stat_invalid')
    return dict(pid=pid, ppid=int(fields[1]), start_ticks=int(fields[19]), uid=path.stat().st_uid)


def _boot():
    value = (PROC / 'sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    need(_match(BOOT, value), 'owner_boot_invalid')
    return value


def _lock_for_owner(pid):
    _normal_directory(LOCK.parent)
    info = LOCK.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'deployment_lock_not_regular')
    _owner_mode(info, 0o600)
    descriptor = PROC / str(pid) / 'fd/9'
    need(os.readlink(str(descriptor)) == str(LOCK), 'owner_lock_descriptor_invalid')
    opened = descriptor.stat()
    need((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino), 'owner_lock_identity_changed')
    held = False
    for line in (PROC / str(pid) / 'fdinfo/9').read_text(encoding='ascii').splitlines():
        fields = line.split()
        if 'FLOCK' not in fields or 'WRITE' not in fields:
            continue
        for field in fields:
            if re.fullmatch(r'[0-9a-f]+:[0-9a-f]+:[0-9]+', field):
                major, minor, inode = field.split(':')
                held = held or (int(major, 16), int(minor, 16), int(inode)) == (
                    os.major(info.st_dev), os.minor(info.st_dev), info.st_ino)
    need(held and _signature(LOCK.lstat()) == _signature(info), 'owner_deployment_flock_unverified')
    return dict(path=str(LOCK), device=info.st_dev, inode=info.st_ino,
                owner_uid=info.st_uid, mode=0o600, descriptor=9)


def _owner(pid, target):
    need(_match(SHA, target), 'target_sha_invalid')
    observed = _process(pid)
    need(observed['uid'] == _uid(), 'owner_user_invalid')
    args = (PROC / str(pid) / 'cmdline').read_bytes().split(b'\0')
    need(args and args[0].rsplit(b'/', 1)[-1] == b'bash' and target.encode('ascii') in args,
         'owner_protocol_identity_invalid')
    parent = os.getppid()
    for _ in range(8):
        if parent == pid:
            break
        parent = _process(parent)['ppid']
    need(parent == pid, 'owner_not_ancestor')
    identity = dict(boot_id=_boot(), pid=pid, start_ticks=observed['start_ticks'], uid=observed['uid'])
    return identity, _lock_for_owner(pid)


def _names(root):
    _normal_directory(root)
    names = sorted(path.name for path in root.iterdir())
    need(len(names) <= MAX_NAMES and all(_match(NAME, name) for name in names), 'reference_names_invalid')
    return names


def _current_sha():
    need(CURRENT.is_symlink(), 'current_pointer_invalid')
    target = os.readlink(str(CURRENT))
    path = Path(target)
    need(path.parent == RELEASES and _match(SHA, path.name), 'current_pointer_invalid')
    _normal_directory(path)
    return path.name


def _directory_identity(path):
    _normal_directory(path)
    info = path.lstat()
    need(info.st_uid == _uid() and info.st_dev == HOME.stat().st_dev, 'object_owner_or_mount_invalid')
    return dict(device=info.st_dev, inode=info.st_ino, signature=_signature(info))


def _docker_run(args):
    allowed = (args == ['docker', 'image', 'ls', '--all', '--no-trunc', '--format', '{{.ID}} {{.Repository}}:{{.Tag}}'] or
        (isinstance(args, list) and len(args) == 6 and args[:5] == ['docker', 'image', 'inspect', '--format', IMAGE_FORMAT]
         and _match(IMAGE, args[5])) or
        (isinstance(args, list) and len(args) == 6 and args[:5] == ['docker', 'container', 'inspect', '--format', MIGRATION_FORMAT]
         and _match(HEX, args[5])) or
        (isinstance(args, list) and len(args) == 8 and args[:7] == ['docker', 'container', 'ls', '--all', '--quiet', '--no-trunc', '--filter']
         and isinstance(args[7], str) and re.fullmatch(r'name=\^/ichiyon-robot-migrate-[0-9a-f]{40}\$', args[7])))
    need(allowed, 'docker_command_not_read_only_protocol')
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                            check=False, shell=False)
    need(result.returncode == 0 and len(result.stdout) <= 4 * 1024 * 1024, 'docker_observation_unavailable')
    return result.stdout.decode('ascii')


def _image_inventory():
    rows = _docker_run(['docker', 'image', 'ls', '--all', '--no-trunc', '--format', '{{.ID}} {{.Repository}}:{{.Tag}}'])
    identifiers, tags = set(), {}
    for row in rows.splitlines():
        fields = row.split()
        need(len(fields) == 2 and _match(IMAGE, fields[0]), 'image_inventory_invalid')
        identifiers.add(fields[0])
        tag = fields[1]
        if tag == '<none>:<none>':
            continue
        need(tag not in tags or tags[tag] == fields[0], 'image_tag_ambiguous')
        tags[tag] = fields[0]
    need(len(identifiers) <= MAX_NAMES, 'image_inventory_limit')
    return sorted(identifiers), tags


def _docker(kind, target, image_inventory=None):
    need(kind in ('image', 'migration') and _match(SHA, target), 'docker_observation_invalid')
    name = 'ichiyon-robot-app:' + target if kind == 'image' else 'ichiyon-robot-migrate-' + target
    if kind == 'image':
        _, tags = image_inventory if image_inventory is not None else _image_inventory()
        identifier = tags.get(name)
        if identifier is None:
            return None
        fields = _docker_run(['docker', 'image', 'inspect', '--format',
            IMAGE_FORMAT, identifier]).split()
        need(len(fields) == 2 and _match(IMAGE, fields[0]) and fields[1] == target, 'target_image_identity_invalid')
        need(fields[0] == identifier, 'target_image_identity_changed')
        return dict(id=fields[0], revision=fields[1])
    identifiers = _docker_run(['docker', 'container', 'ls', '--all', '--quiet', '--no-trunc',
                              '--filter', 'name=^/' + name + '$']).split()
    need(len(identifiers) <= 1 and all(_match(HEX, item) for item in identifiers), 'migration_inventory_invalid')
    if not identifiers:
        return None
    fields = _docker_run(['docker', 'container', 'inspect', '--format',
        MIGRATION_FORMAT, identifiers[0]]).split()
    need(len(fields) == 4 and fields[0] == identifiers[0] and _match(IMAGE, fields[1]) and fields[3] == '/' + name and
         fields[2] in ('created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead'), 'migration_identity_invalid')
    return dict(id=fields[0], image_id=fields[1], status=fields[2], name=name)


def _references(target, previous):
    """Small fixed-protocol metadata only; no env, SQL, task or source content."""
    result = dict(current_sha=None, releases=[], backups=[], release_contracts=[],
                  backup_previous=[], explicit_pins_sha256=None, complete=True)
    try:
        result['current_sha'] = _current_sha()
        result['releases'] = _names(RELEASES)
        result['backups'] = _names(BACKUPS)
        for sha in sorted({target, previous, result['current_sha']}):
            path = RELEASES / sha
            if not path.exists():
                continue
            fields = {}
            for name in ('REVISION', 'immutable-image.txt', 'rollback-images.txt', 'persistence.txt', 'compose.immutable.yml'):
                fields[name] = hashlib.sha256(_read(path / name, limit=65536)).hexdigest()
            result['release_contracts'].append(dict(sha=sha, metadata_sha256=fields))
        for name in result['backups']:
            if not _match(SHA, name):
                continue
            raw = _read(BACKUPS / name / 'previous', 1024).decode('ascii')
            sha = raw.strip().rsplit('/', 1)[-1]
            need(_match(SHA, sha) and raw == str(RELEASES / sha) + '\n', 'backup_previous_invalid')
            result['backup_previous'].append(dict(backup=name, previous_sha=sha))
        pins = HOME / 'ichiyon-retention-pins.json'
        if pins.exists() or pins.is_symlink():
            result['explicit_pins_sha256'] = hashlib.sha256(_read(pins, 65536)).hexdigest()
        need(result['current_sha'] == _current_sha() and result['releases'] == _names(RELEASES)
             and result['backups'] == _names(BACKUPS), 'reference_inventory_changed')
    except (OSError, ValueError, UnicodeError):
        result['complete'] = False
    return result


def _runtime(target):
    result = dict(target_release=None, target_image=None, migration_container=None, image_ids=[], image_tags={}, observation_complete=True)
    inventory = None
    try:
        inventory = _image_inventory()
        result['image_ids'] = inventory[0]
        result['image_tags'] = inventory[1]
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        result['observation_complete'] = False
    for key, reader in (('target_release', lambda: _directory_identity(RELEASES / target)),
                        ('target_image', lambda: _docker('image', target, inventory)),
                        ('migration_container', lambda: _docker('migration', target))):
        if key == 'target_image' and inventory is None:
            continue
        try:
            result[key] = reader()
        except FileNotFoundError:
            # Only an absent release directory is an absence observation. A
            # missing Docker executable during a later call is unavailable,
            # even when the earlier image inventory succeeded.
            if key != 'target_release':
                result['observation_complete'] = False
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            result['observation_complete'] = False
    return result


def _stage_path(kind, value, target):
    need(kind in ('prepare', 'release', 'backup') and isinstance(value, str), 'stage_kind_invalid')
    path = Path(value)
    root = BACKUPS if kind == 'backup' else RELEASES
    need(path.parent == root and re.fullmatch(r'\.' + kind + '-' + target + r'\.[A-Za-z0-9]{8,32}', path.name),
         'stage_path_outside_protocol')
    return path


def _validate(payload, opid, terminal):
    need(isinstance(payload, dict) and set(payload) == {
        'operation_id', 'owner', 'target_sha', 'previous_sha', 'created_at', 'completed_at',
        'state', 'phase', 'rollback_result', 'lock', 'initial_names', 'initial_runtime',
        'staging', 'runtime', 'reference_snapshot', 'reference_snapshot_sha256',
        'events', 'omitted_event_count', 'sequence', 'active_payload_sha256'}, 'evidence_schema_invalid')
    need(payload['operation_id'] == opid and _match(OPID, opid) and
         _match(SHA, payload['target_sha']) and _match(SHA, payload['previous_sha']), 'operation_identity_invalid')
    owner = payload['owner']
    need(isinstance(owner, dict) and set(owner) == {'boot_id', 'pid', 'start_ticks', 'uid'} and
         _match(BOOT, owner['boot_id']) and _positive(owner['pid']) and _positive(owner['start_ticks']) and
         type(owner['uid']) is int and owner['uid'] == _uid(), 'owner_identity_invalid')
    need(_timestamp(payload['created_at']) and payload['phase'] in PHASES and
         _positive(payload['sequence']) and type(payload['omitted_event_count']) is int and
         payload['omitted_event_count'] >= 0, 'evidence_lifecycle_invalid')
    need(payload['state'] in TERMINAL if terminal else payload['state'] == 'running', 'terminal_state_invalid')
    if terminal:
        need(_timestamp(payload['completed_at']) and payload['created_at'] <= payload['completed_at'] and
             payload['rollback_result'] in ROLLBACK and _match(HEX, payload['active_payload_sha256']), 'terminal_receipt_invalid')
    else:
        need(payload['completed_at'] is None and payload['rollback_result'] is None and
             payload['active_payload_sha256'] is None, 'active_receipt_claims_terminal')
    refs = payload['reference_snapshot']
    need(isinstance(refs, dict) and set(refs) == {'current_sha', 'releases', 'backups',
         'release_contracts', 'backup_previous', 'explicit_pins_sha256', 'complete'} and
         type(refs['complete']) is bool and (refs['current_sha'] is None or _match(SHA, refs['current_sha'])) and
         (refs['explicit_pins_sha256'] is None or _match(HEX, refs['explicit_pins_sha256'])) and
         payload['reference_snapshot_sha256'] == digest(payload['reference_snapshot']), 'reference_digest_invalid')
    for names in (refs['releases'], refs['backups']):
        need(isinstance(names, list) and len(names) <= MAX_NAMES and all(_match(NAME, name) for name in names), 'reference_inventory_invalid')
    need(isinstance(refs['release_contracts'], list) and len(refs['release_contracts']) <= 3 and
         isinstance(refs['backup_previous'], list) and len(refs['backup_previous']) <= MAX_NAMES, 'reference_inventory_invalid')
    for item in refs['release_contracts']:
        need(isinstance(item, dict) and set(item) == {'sha', 'metadata_sha256'} and _match(SHA, item['sha']) and
             isinstance(item['metadata_sha256'], dict) and set(item['metadata_sha256']) == {
                 'REVISION', 'immutable-image.txt', 'rollback-images.txt', 'persistence.txt', 'compose.immutable.yml'} and
             all(_match(HEX, value) for value in item['metadata_sha256'].values()), 'reference_inventory_invalid')
    for item in refs['backup_previous']:
        need(isinstance(item, dict) and set(item) == {'backup', 'previous_sha'} and
             _match(SHA, item['backup']) and _match(SHA, item['previous_sha']), 'reference_inventory_invalid')
    lock = payload['lock']
    need(isinstance(lock, dict) and set(lock) == {'path', 'device', 'inode', 'owner_uid', 'mode', 'descriptor'} and
         lock['path'] == str(LOCK) and _positive(lock['device']) and _positive(lock['inode']) and
         lock['owner_uid'] == _uid() and lock['mode'] == 0o600 and lock['descriptor'] == 9, 'lock_receipt_invalid')
    need(isinstance(payload['initial_names'], dict) and set(payload['initial_names']) == {'releases', 'backups'}, 'initial_inventory_invalid')
    for names in payload['initial_names'].values():
        need(isinstance(names, list) and len(names) <= MAX_NAMES and names == sorted(set(names)) and
             all(_match(NAME, name) for name in names), 'initial_inventory_invalid')
    need(isinstance(payload['staging'], list) and len(payload['staging']) <= 3, 'stage_bindings_invalid')
    seen = set()
    for item in payload['staging']:
        need(isinstance(item, dict) and set(item) == {'kind', 'path', 'created_identity', 'created_at'}, 'stage_bindings_invalid')
        path = _stage_path(item['kind'], item['path'], payload['target_sha'])
        need(item['kind'] not in seen and path.name not in payload['initial_names']['backups' if item['kind'] == 'backup' else 'releases'],
             'legacy_or_duplicate_stage_binding')
        seen.add(item['kind'])
        ident = item['created_identity']
        need(isinstance(ident, dict) and set(ident) == {'device', 'inode', 'signature'} and
             _positive(ident['device']) and _positive(ident['inode']) and isinstance(ident['signature'], list) and
             len(ident['signature']) == 7 and ident['signature'][:2] == [ident['device'], ident['inode']] and
             all(type(value) is int and value >= 0 for value in ident['signature']) and
             stat.S_ISDIR(ident['signature'][2]) and
             _timestamp(item['created_at']) and item['created_at'] >= payload['created_at'], 'stage_identity_invalid')
    need(isinstance(payload['events'], list) and len(payload['events']) <= MAX_EVENTS, 'event_history_invalid')
    for event in payload['events']:
        need(isinstance(event, dict) and set(event) == {'phase', 'at'} and event['phase'] in PHASES and
             _timestamp(event['at']) and event['at'] >= payload['created_at'], 'event_history_invalid')
    for runtime in (payload['initial_runtime'], payload['runtime']):
        need(isinstance(runtime, dict) and set(runtime) in ({'target_release', 'target_image', 'migration_container', 'image_ids', 'observation_complete'},
                 {'target_release', 'target_image', 'migration_container', 'image_ids', 'image_tags', 'observation_complete'}) and
             type(runtime['observation_complete']) is bool, 'runtime_identity_invalid')
        need(isinstance(runtime['image_ids'], list) and len(runtime['image_ids']) <= MAX_NAMES and
             all(_match(IMAGE, value) for value in runtime['image_ids']) and
             runtime['image_ids'] == sorted(set(runtime['image_ids'])), 'runtime_image_inventory_invalid')
        if 'image_tags' in runtime:
            need(isinstance(runtime['image_tags'], dict) and len(runtime['image_tags']) <= MAX_NAMES and
                 all(isinstance(tag, str) and re.fullmatch(r'[A-Za-z0-9_.:/@+-]{1,512}', tag) and
                     _match(IMAGE, ident) and ident in runtime['image_ids']
                     for tag, ident in runtime['image_tags'].items()), 'runtime_image_tags_invalid')
        release = runtime['target_release']
        if release is not None:
            need(isinstance(release, dict) and set(release) == {'device', 'inode', 'signature'} and
                 _positive(release['device']) and _positive(release['inode']) and
                 isinstance(release['signature'], list) and len(release['signature']) == 7 and
                 all(type(value) is int and value >= 0 for value in release['signature']) and
                 release['signature'][:2] == [release['device'], release['inode']] and
                 stat.S_ISDIR(release['signature'][2]), 'runtime_release_identity_invalid')
        image = runtime['target_image']
        if image is not None:
            need(isinstance(image, dict) and set(image) == {'id', 'revision'} and
                 _match(IMAGE, image['id']) and image['revision'] == payload['target_sha'] and
                 image['id'] in runtime['image_ids'], 'runtime_image_invalid')
        migration = runtime['migration_container']
        if migration is not None:
            need(isinstance(migration, dict) and set(migration) == {'id', 'image_id', 'status', 'name'} and
                 _match(HEX, migration['id']) and _match(IMAGE, migration['image_id']) and
                 migration['name'] == 'ichiyon-robot-migrate-' + payload['target_sha'] and
                 migration['status'] in ('created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead'), 'runtime_migration_invalid')
    return payload


def read_receipt(path, terminal=True):
    """Read and validate fixed-root evidence. This function performs no writes."""
    _secure_root()
    path = Path(path)
    opid = path.stem
    need(_match(OPID, opid) and path.name == opid + '.json' and
         path.parent == ROOT / ('operations' if terminal else 'active'), 'receipt_path_invalid')
    envelope = _strict_json(_read(path, secure=True))
    need(isinstance(envelope, dict) and set(envelope) == {'schema', 'version', 'payload', 'sha256'} and
         envelope['schema'] == SCHEMA and type(envelope['version']) is int and envelope['version'] == 1 and
         _match(HEX, envelope['sha256']) and envelope['sha256'] == digest(envelope['payload']), 'evidence_checksum_invalid')
    payload = _validate(envelope['payload'], opid, terminal)
    if terminal:
        active = read_receipt(ROOT / 'active' / path.name, terminal=False)
        need(payload['active_payload_sha256'] == digest(active) and payload['owner'] == active['owner'] and
             payload['created_at'] == active['created_at'] and payload['sequence'] == active['sequence'] + 1 and
             payload['staging'] == active['staging'] and payload['target_sha'] == active['target_sha'] and
             payload['previous_sha'] == active['previous_sha'] and payload['lock'] == active['lock'] and
             payload['initial_names'] == active['initial_names'] and payload['initial_runtime'] == active['initial_runtime'],
             'terminal_active_binding_invalid')
    return payload


def _v1_load_active(opid, owner_pid):
    need(_match(OPID, opid), 'operation_id_invalid')
    need(not (ROOT / 'operations' / (opid + '.json')).exists(), 'operation_already_terminal')
    payload = read_receipt(ROOT / 'active' / (opid + '.json'), terminal=False)
    owner, lock = _owner(owner_pid, payload['target_sha'])
    need(owner == payload['owner'] and lock == payload['lock'], 'operation_owner_or_lock_changed')
    return payload


def _event(payload, phase):
    need(phase in PHASES, 'phase_invalid')
    payload['phase'] = phase
    payload['events'].append(dict(phase=phase, at=time.time()))
    if len(payload['events']) > MAX_EVENTS:
        payload['events'].pop(0)
        payload['omitted_event_count'] += 1
    payload['sequence'] += 1


def _v1_begin(target_sha, owner_pid):
    owner, lock = _owner(owner_pid, target_sha)
    previous = _current_sha()
    names = dict(releases=_names(RELEASES), backups=_names(BACKUPS))
    runtime = _runtime(target_sha)
    now = time.time()
    refs = _references(target_sha, previous)
    payload = dict(operation_id=uuid.uuid4().hex, owner=owner, target_sha=target_sha,
        previous_sha=previous, created_at=now, completed_at=None, state='running', phase='preflight',
        rollback_result=None, lock=lock, initial_names=names, initial_runtime=runtime, staging=[],
        runtime=runtime, reference_snapshot=refs, reference_snapshot_sha256=digest(refs),
        events=[dict(phase='preflight', at=now)], omitted_event_count=0, sequence=1,
        active_payload_sha256=None)
    _validate(payload, payload['operation_id'], False)
    _secure_root(create=True)
    _publish(ROOT / 'active' / (payload['operation_id'] + '.json'), payload)
    return payload['operation_id']


def _v1_record_stage(opid, owner_pid, kind, exactpath):
    payload = _v1_load_active(opid, owner_pid)
    path = _stage_path(kind, exactpath, payload['target_sha'])
    need(path.name not in payload['initial_names']['backups' if kind == 'backup' else 'releases'], 'legacy_stage_cannot_be_enrolled')
    need(not any(item['kind'] == kind for item in payload['staging']), 'stage_already_bound')
    identity = _directory_identity(path)
    need(path.lstat().st_ctime_ns >= int(payload['created_at'] * 1e9), 'stage_predates_operation')
    payload['staging'].append(dict(kind=kind, path=str(path), created_identity=identity, created_at=time.time()))
    _event(payload, kind if kind in PHASES else 'prepare')
    _validate(payload, opid, False)
    _publish(ROOT / 'active' / (opid + '.json'), payload)


def _v1_observe(opid, owner_pid, phase):
    payload = _v1_load_active(opid, owner_pid)
    _event(payload, phase)
    runtime = _runtime(payload['target_sha'])
    # A completed migration container can be removed by the normal protocol;
    # retain its last observed exact identity rather than replacing it with null.
    if runtime['migration_container'] is None and payload['runtime']['migration_container'] is not None:
        runtime['migration_container'] = payload['runtime']['migration_container']
    payload['runtime'] = runtime
    payload['reference_snapshot'] = _references(payload['target_sha'], payload['previous_sha'])
    payload['reference_snapshot_sha256'] = digest(payload['reference_snapshot'])
    _validate(payload, opid, False)
    _publish(ROOT / 'active' / (opid + '.json'), payload)


def _v1_finish(opid, owner_pid, state, rollback):
    need(state in TERMINAL and rollback in ROLLBACK, 'terminal_state_invalid')
    payload = _v1_load_active(opid, owner_pid)
    active_digest = digest(payload)
    payload['state'], payload['rollback_result'] = state, rollback
    payload['completed_at'] = time.time()
    payload['active_payload_sha256'] = active_digest
    payload['sequence'] += 1
    runtime = _runtime(payload['target_sha'])
    if runtime['migration_container'] is None:
        runtime['migration_container'] = payload['runtime']['migration_container']
    payload['runtime'] = runtime
    payload['reference_snapshot'] = _references(payload['target_sha'], payload['previous_sha'])
    payload['reference_snapshot_sha256'] = digest(payload['reference_snapshot'])
    _validate(payload, opid, True)
    _publish(ROOT / 'operations' / (opid + '.json'), payload, terminal=True)
    return dict(operation_id=opid, terminal_state=state, receipt=str(ROOT / 'operations' / (opid + '.json')))


def _load_indexed_active(store, opid, owner_pid):
    need(_match(OPID, opid), 'operation_id_invalid')
    payload = _validate(store.get_active(opid), opid, False)
    owner, lock = _owner(owner_pid, payload['target_sha'])
    need(owner == payload['owner'] and lock == payload['lock'], 'operation_owner_or_lock_changed')
    return payload


def begin(target_sha, owner_pid):
    from ai_task_storage_evidence_store import Store
    owner, lock = _owner(owner_pid, target_sha)
    previous = _current_sha()
    names = dict(releases=_names(RELEASES), backups=_names(BACKUPS))
    runtime = _runtime(target_sha)
    now = time.time()
    refs = _references(target_sha, previous)

    def factory(opid):
        payload = dict(operation_id=opid, owner=owner, target_sha=target_sha,
            previous_sha=previous, created_at=now, completed_at=None, state='running', phase='preflight',
            rollback_result=None, lock=lock, initial_names=names, initial_runtime=runtime, staging=[],
            runtime=runtime, reference_snapshot=refs, reference_snapshot_sha256=digest(refs),
            events=[dict(phase='preflight', at=now)], omitted_event_count=0, sequence=1,
            active_payload_sha256=None)
        return _validate(payload, opid, False)

    with Store(create=True) as store:
        return store.begin(factory)


def record_stage(opid, owner_pid, kind, exactpath):
    from ai_task_storage_evidence_store import Store
    with Store() as store:
        payload = _load_indexed_active(store, opid, owner_pid)
        path = _stage_path(kind, exactpath, payload['target_sha'])
        need(path.name not in payload['initial_names']['backups' if kind == 'backup' else 'releases'],
             'legacy_stage_cannot_be_enrolled')
        need(not any(item['kind'] == kind for item in payload['staging']), 'stage_already_bound')
        identity = _directory_identity(path)
        need(path.lstat().st_ctime_ns >= int(payload['created_at'] * 1e9), 'stage_predates_operation')
        payload['staging'].append(dict(kind=kind, path=str(path), created_identity=identity, created_at=time.time()))
        _event(payload, kind)
        store.update(_validate(payload, opid, False))


def _refresh(payload, runtime=None):
    runtime = _runtime(payload['target_sha']) if runtime is None else dict(runtime)
    # Preserve migration history in the lossless receipt. Classification uses
    # the separate fresh raw observation, never this remembered container.
    if runtime['migration_container'] is None:
        runtime['migration_container'] = payload['runtime']['migration_container']
    payload['runtime'] = runtime
    payload['reference_snapshot'] = _references(payload['target_sha'], payload['previous_sha'])
    payload['reference_snapshot_sha256'] = digest(payload['reference_snapshot'])


def observe(opid, owner_pid, phase):
    from ai_task_storage_evidence_store import Store
    with Store() as store:
        payload = _load_indexed_active(store, opid, owner_pid)
        _event(payload, phase)
        _refresh(payload)
        store.update(_validate(payload, opid, False))


def _ancillary_absent(owner_pid):
    """The normal shell unlinks its helper before finalization through FD 8.

    The checked deleted inode remains open only for this final interpreter.
    Diagnostics have already been unlinked and output restored by the shell.
    No task text, arbitrary file path or caller-selected retention enters here.
    """
    try:
        descriptor = PROC / str(owner_pid) / 'fd/8'
        name = os.readlink(str(descriptor))
        need(re.fullmatch(r'/home/ubuntu/\.ichiyon-deploy-helper\.[A-Za-z0-9]{8}\.py \(deleted\)', name),
             'helper_cleanup_unverified')
        info = descriptor.stat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 0 and info.st_uid == _uid(),
             'helper_cleanup_unverified')
        return True
    except (OSError, ValueError):
        return False


def _final_observation(payload, runtime, owner_pid):
    stages = []
    for item in payload['staging']:
        row = dict(kind=item['kind'], path=item['path'], status='unknown', identity=None)
        try:
            path = _stage_path(item['kind'], item['path'], payload['target_sha'])
            _normal_directory(path.parent)
            try:
                path.lstat()
            except FileNotFoundError:
                row['status'] = 'absent'
            else:
                row.update(status='present', identity=_directory_identity(path))
        except (OSError, ValueError):
            pass
        stages.append(row)
    refs = payload['reference_snapshot']
    phases = [event['phase'] for event in payload['events']]
    return dict(version=1, operation_id=payload['operation_id'], observed_at=time.time(),
        current_sha=refs['current_sha'], references_complete=refs['complete'],
        names=dict(releases=refs['releases'], backups=refs['backups']), runtime=runtime, stages=stages,
        health_verified='health' in phases, reconciliation_verified='reconcile' in phases,
        cleanup_completed='cleanup' in phases,
        ancillary_cleanup_verified='ancillary_cleanup' in phases and _ancillary_absent(owner_pid))


def finish(opid, owner_pid, state, rollback):
    from ai_task_storage_evidence_store import Store
    from ai_task_storage_evidence_classification import classify
    need(state in TERMINAL and rollback in ROLLBACK, 'terminal_state_invalid')
    with Store() as store:
        payload = _load_indexed_active(store, opid, owner_pid)
        active_digest = digest(payload)
        payload['state'], payload['rollback_result'] = state, rollback
        payload['completed_at'] = time.time()
        payload['active_payload_sha256'] = active_digest
        payload['sequence'] += 1
        runtime = _runtime(payload['target_sha'])
        _refresh(payload, runtime)
        _validate(payload, opid, True)
        observation = _final_observation(payload, runtime, owner_pid)
        classified = classify(payload, observation)
        store.finish(payload, classified['classification'], classified['object_keys'], classified['noop_proof'])
    return dict(operation_id=opid, terminal_state=state, classification=classified['classification'],
                receipt=str(ROOT / 'v2/index.sqlite3'))


def _indexed_payload(envelope, terminal=True):
    """Validate both lossless payloads, in addition to store checksums/index."""
    payload = envelope['payload']
    opid = payload['operation_id']
    _validate(payload, opid, terminal)
    if terminal:
        active = _validate(envelope['active_payload'], opid, False)
        need(payload['active_payload_sha256'] == digest(active) and
             payload['sequence'] == active['sequence'] + 1 and
             all(payload[key] == active[key] for key in ('owner', 'created_at', 'staging', 'target_sha',
                 'previous_sha', 'lock', 'initial_names', 'initial_runtime')), 'terminal_active_binding_invalid')
    return payload


def _owner_state(payload, terminal):
    """A PID alone is never historical termination evidence."""
    owner = payload['owner']
    try:
        boot = _boot()
    except (OSError, ValueError, KeyError, IndexError):
        return 'unknown'
    if boot != owner['boot_id']:
        return 'terminated_identity_verified' if terminal else 'unknown_without_terminal_receipt'
    try:
        observed = _process(owner['pid'])
        if observed['start_ticks'] == owner['start_ticks'] and observed['uid'] == owner['uid']:
            return 'alive'
        return 'terminated_identity_verified' if terminal else 'unknown_without_terminal_receipt'
    except (FileNotFoundError, ProcessLookupError):
        return 'terminated_identity_verified' if terminal else 'unknown_without_terminal_receipt'
    except (OSError, ValueError, KeyError, IndexError):
        return 'unknown'


def _read_lock():
    result = dict(path=str(LOCK), regular_file=False, no_symlink=False,
                  identity_stable=False, kernel_scan_complete=False,
                  held=None, holder_pids=[], device=None, inode=None, identity=None)
    try:
        _normal_directory(LOCK.parent)
        info = LOCK.lstat()
        _owner_mode(info, 0o600)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'lock_invalid')
        result.update(regular_file=True, no_symlink=True, device=info.st_dev,
                      inode=info.st_ino, identity=[info.st_dev, info.st_ino], held=False)
        for line in (PROC / 'locks').read_text(encoding='ascii').splitlines():
            fields = line.split()
            if 'FLOCK' not in fields:
                continue
            for index, field in enumerate(fields):
                if not re.fullmatch(r'[0-9a-f]+:[0-9a-f]+:[0-9]+', field):
                    continue
                major, minor, inode = field.split(':')
                if (int(major, 16), int(minor, 16), int(inode)) == (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino):
                    result['held'] = True
                    if index and fields[index - 1].isdigit():
                        result['holder_pids'].append(int(fields[index - 1]))
        result['identity_stable'] = _signature(LOCK.lstat()) == _signature(info)
        result['kernel_scan_complete'] = True
    except (OSError, ValueError, AttributeError):
        pass
    return result


def _bound(payload, category, node):
    """Only newly recorded objects, never preexisting generations by SHA/name."""
    target = payload['target_sha']
    if category == 'images':
        current, initial = payload['runtime']['target_image'], payload['initial_runtime']['target_image']
        return (payload['runtime']['observation_complete'] and payload['initial_runtime']['observation_complete'] and
                current is not None and initial is None and current['id'] == node.get('id') and
                current['id'] not in payload['initial_runtime']['image_ids'])
    path = node.get('path')
    if not isinstance(path, str):
        return False
    allowed = []
    for item in payload['staging']:
        if category == 'staging' and path == item['path']:
            allowed.append(item)
        if category == 'releases' and item['kind'] == 'release' and path == str(RELEASES / target) and target not in payload['initial_names']['releases']:
            allowed.append(item)
        if category == 'backups' and item['kind'] == 'backup' and path == str(BACKUPS / target) and target not in payload['initial_names']['backups']:
            allowed.append(item)
    if len(allowed) != 1:
        return False
    try:
        now = _directory_identity(Path(path))
        before = allowed[0]['created_identity']
        return (now['device'], now['inode']) == (before['device'], before['inode'])
    except (OSError, ValueError):
        return False


def enrich_snapshot(snapshot, open_paths=()):
    """Independently observe fixed receipts and current process/object identities.

    Called last by the read-only retention collector. No root creation, receipt
    publication, process execution or cleanup occurs here. New receipt bindings
    add ownership evidence; recovery disposition is intentionally still unknown.
    """
    from ai_task_storage_cleanup import inventory_digest, object_digest
    summaries, verified, errors, pending_count = [], [], [], 0
    observations = {}
    try:
        _normal_directory(ROOT)
        _owner_mode(ROOT.lstat(), 0o700)
        legacy_paths = [ROOT / name for name in ('operations', 'active', '.pending')]
        legacy_present = [path.exists() or path.is_symlink() for path in legacy_paths]
        need(not any(legacy_present) or all(legacy_present), 'legacy_namespace_incomplete')
        if all(legacy_present):
            _secure_root()
        terminal_names = _names(ROOT / 'operations') if all(legacy_present) else []
        active_names = _names(ROOT / 'active') if all(legacy_present) else []
        pending_count = len(_names(ROOT / '.pending')) if all(legacy_present) else 0
        if pending_count:
            errors.append('INCOMPLETE_EVIDENCE_PUBLISH')
        for name in sorted(set(terminal_names + active_names)):
            terminal = name in terminal_names
            path = ROOT / ('operations' if terminal else 'active') / name
            try:
                watched = [path] + ([ROOT / 'active' / name] if terminal else [])
                before = {item: _signature(item.lstat()) for item in watched}
                payload = read_receipt(path, terminal=terminal)
                need(before == {item: _signature(item.lstat()) for item in watched}, 'receipt_changed_during_observation')
                observations.update(before)
                state = _owner_state(payload, terminal)
                summaries.append(dict(operation_id=payload['operation_id'], target_sha=payload['target_sha'],
                    previous_sha=payload['previous_sha'], state=payload['state'], phase=payload['phase'],
                    owner_state=state, rollback_result=payload['rollback_result'],
                    staging_count=len(payload['staging']), terminal=terminal,
                    receipt_sha256=digest(payload)))
                verified.append((path, payload, terminal, state))
            except (OSError, ValueError, TypeError, KeyError, IndexError):
                errors.append('RECEIPT_INVALID_OR_CHANGED')
        if all(legacy_present) and (terminal_names != _names(ROOT / 'operations') or active_names != _names(ROOT / 'active')):
            errors.append('RECEIPT_INVENTORY_CHANGED')
    except FileNotFoundError:
        errors.append('EVIDENCE_ROOT_UNAVAILABLE')
    except (OSError, ValueError, TypeError):
        errors.append('EVIDENCE_ROOT_INVALID')
    legacy_terminal_count = sum(row['terminal'] for row in summaries)
    legacy_active_count = sum(not row['terminal'] for row in summaries)
    indexed_summary, indexed_sources, indexed_identity = None, {}, None
    index_path = ROOT / 'v2/index.sqlite3'
    try:
        if (ROOT / 'v2').exists() or (ROOT / 'v2').is_symlink():
            from ai_task_storage_evidence_store import read_session, PAGE_LIMIT
            with read_session() as store:
                indexed_summary = store.summary()
                indexed_identity = store.identity
                # Bounded samples are for reporting only. Actual current object
                # lookup uses every matching indexed page, never these samples.
                envelopes = {}
                persistent_sample, incident_sample = store.persistent(), store.incidents()
                for entry in persistent_sample + incident_sample:
                    envelopes[entry['payload']['operation_id']] = entry
                indexed_summary['rollup_sample'] = store.rollups()
                indexed_summary['sample_policy'] = dict(
                    operations='all_current_object_matches_plus_bounded_A_B_samples_and_active',
                    terminal_count_basis='legacy_raw_plus_A_B_plus_recent_C',
                    A_truncated=indexed_summary['counters']['A'] > len(persistent_sample),
                    B_truncated=indexed_summary['counters']['B'] > len(incident_sample),
                    rollups_truncated=indexed_summary['counters']['rollups'] > len(indexed_summary['rollup_sample']),
                    A_after_id=persistent_sample[-1]['payload']['operation_id'] if persistent_sample else '',
                    B_after_id=incident_sample[-1]['payload']['operation_id'] if incident_sample else '',
                    rollups_after_sha=indexed_summary['rollup_sample'][-1]['target_sha'] if indexed_summary['rollup_sample'] else '')
                indexed_summary['recent_sample'] = [dict(operation_id=item['payload']['operation_id'],
                    completed_at=item['payload']['completed_at'], receipt_sha256=item['sha256'])
                    for item in store.recent()]
                for category in ('releases', 'backups', 'images', 'staging'):
                    for node in snapshot.get(category, []):
                        key = Path(node.get('path', '')).name if category == 'staging' else node.get('id')
                        valid = (_match(IMAGE, key) if category == 'images' else
                                 bool(re.fullmatch(r'\.(prepare|release|backup)-[0-9a-f]{40}\.[A-Za-z0-9]{8,32}', key or ''))
                                 if category == 'staging' else _match(SHA, key))
                        if not valid:
                            continue
                        after = ''
                        while True:
                            page = store.resolve_objects_page(category, key, after)
                            for entry in page:
                                envelopes[entry['payload']['operation_id']] = entry
                            if len(page) < PAGE_LIMIT:
                                break
                            after = page[-1]['payload']['operation_id']
                selected = [(entry, True) for entry in envelopes.values()] + [(entry, False) for entry in store.active()]
                indexed_verified, indexed_summaries = [], []
                for entry, terminal in selected:
                    payload = _indexed_payload(entry, terminal)
                    state = _owner_state(payload, terminal)
                    classification = entry.get('classification')
                    # C never enters selected or object ownership bindings.
                    need(not terminal or classification in ('A', 'B'), 'summary_cannot_bind_object')
                    indexed_sources[payload['operation_id']] = classification
                    indexed_verified.append((index_path, payload, terminal, state))
                    indexed_summaries.append(dict(operation_id=payload['operation_id'], target_sha=payload['target_sha'],
                        previous_sha=payload['previous_sha'], state=payload['state'], phase=payload['phase'],
                        owner_state=state, rollback_result=payload['rollback_result'], staging_count=len(payload['staging']),
                        terminal=terminal, receipt_sha256=entry['sha256'], classification=classification,
                        source_format='indexed-v2'))
                # SQLite pins a consistent readonly snapshot throughout these
                # reads; writers cannot commit through this read transaction.
                store._identity()
            verified.extend(indexed_verified)
            summaries.extend(indexed_summaries)
    except (OSError, ValueError, TypeError, KeyError, IndexError, sqlite3.Error):
        errors.append('INDEXED_EVIDENCE_INVALID_OR_UNAVAILABLE')
        indexed_sources = {}
    counts = indexed_summary['counters'] if indexed_summary else {}
    snapshot['durable_operations'] = dict(schema_version=2, source_root=str(ROOT),
        operations=summaries, legacy_terminal_count=legacy_terminal_count,
        legacy_active_or_incomplete_count=legacy_active_count, indexed=indexed_summary,
        terminal_count=legacy_terminal_count + sum(counts.get(key, 0) for key in ('A', 'B', 'recent')),
        active_or_incomplete_count=legacy_active_count + counts.get('active', 0),
        interrupted_publish_count=pending_count, errors=sorted(set(errors)))
    bindings = []
    for category in ('releases', 'backups', 'images', 'staging'):
        for node in snapshot.get(category, []):
            matches = [(path, payload, terminal, state) for path, payload, terminal, state in verified
                       if _bound(payload, category, node)]
            if len(matches) > 1:
                errors.append('CONFLICTING_OBJECT_OWNERSHIP')
                continue
            if not matches:
                continue
            path, payload, terminal, state = matches[0]
            node.update(operation_id=payload['operation_id'], ownership_verified=True, owner_state=state)
            if state == 'alive':
                node.update(active=True, ownership='possible_active')
            else:
                node['ownership'] = 'verified_terminal' if terminal else 'incomplete_operation'
            bindings.append((category, node, path, payload, terminal, state))
    unstable = set()
    if indexed_identity is not None:
        try:
            from ai_task_storage_evidence_store import _file
            _normal_directory(index_path.parent)
            _owner_mode(index_path.parent.lstat(), 0o700)
            info = _file(index_path)
            need((info.st_dev, info.st_ino) == indexed_identity, 'indexed_file_replaced')
        except (OSError, ValueError):
            errors.append('INDEXED_EVIDENCE_CHANGED')
            unstable.update(indexed_sources)
    for path, identity in observations.items():
        try:
            changed = _signature(path.lstat()) != identity
        except OSError:
            changed = True
        if changed:
            unstable.add(path.stem)
    if unstable:
        errors.append('RECEIPT_CHANGED_DURING_OBSERVATION')
        for _, node, _, payload, _, _ in bindings:
            if payload['operation_id'] in unstable:
                node.update(ownership_verified=False, ownership='unverified_evidence_race', owner_state='unknown')
    records = []
    for category, node, path, payload, terminal, state in bindings:
        target = node.get('path')
        opened = sorted(value for value in open_paths if isinstance(target, str) and
                        (value == target or value.startswith(target + '/')))
        stable = payload['operation_id'] not in unstable
        record = dict(category=category, id=node['id'], object_sha256=object_digest(node),
            source_path=str(path), source_verified=stable, source_no_symlinks=True,
            source_stable=stable, source_checksum_verified=True,
            operation=dict(id=payload['operation_id'], ownership_verified=stable,
                boot_id=payload['owner']['boot_id'], owner_pid=payload['owner']['pid'],
                owner_start_ticks=payload['owner']['start_ticks'], target_sha=payload['target_sha'],
                created_at=payload['created_at'], completed_at=payload['completed_at'],
                state=payload['state'], terminal_receipt_verified=terminal,
                owner_process_state=state, lease_state='active' if state == 'alive' else
                    'terminal_verified' if terminal and state == 'terminated_identity_verified' else 'unresolved'),
            object_identity_stable=True, no_symlink_escape=True, no_mount_crossing=True,
            content_inventory_verified=False, content_sha256=None, open_paths=opened,
            active_processes=[payload['owner']['pid']] if state == 'alive' else [],
            format_validation=None, image_identity_verified=category == 'images',
            recovery=dict(unique_data=None, evidence_preserved=False, incident_review_complete=False,
                          disposition_validation=None, proof_sha256=None),
            backup_validation={})
        if path == index_path:
            record.update(source_format='indexed-v2', source_operation_id=payload['operation_id'],
                          source_index_verified=True, source_classification=indexed_sources[payload['operation_id']])
        records.append(record)
    observation = snapshot.get('operation_observation', {})
    lock = _read_lock()
    observation['lock_identity'] = lock['identity']
    snapshot['durable_operations']['errors'] = sorted(set(errors))
    snapshot['captured_end_at'] = time.time()
    snapshot['cleanup_evidence'] = dict(schema_version=1, source_root=str(ROOT), source_verified=not errors,
        observed_at=snapshot['captured_end_at'], process_scan_complete=observation.get('process_scan_complete') is True,
        open_file_scan_complete=observation.get('process_scan_complete') is True,
        lease_scan_complete=all(state in ('alive', 'terminated_identity_verified') for _, _, _, state in verified),
        migration_reference_scan_complete=snapshot.get('references_complete') is True,
        reference_scan_complete=snapshot.get('references_complete') is True,
        lock=lock, records=records)
    snapshot['cleanup_evidence']['inventory_sha256'] = inventory_digest(snapshot)
    return snapshot
