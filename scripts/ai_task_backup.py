"""Versioned, read-only backup validation and isolated filesystem rehearsal.

No production backup writer, exclusion switch, migration or cleanup executor.
The deployed six-file format stays unchanged. Split v2 is a restore contract,
not permission to exclude a live subtree or retire a recovery archive.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile

RELEASES_ROOT = Path('/home/ubuntu/ichiyon-releases')
RECOVERY_ROOT = Path('/home/ubuntu/ichiyon-recovery-archives')
BACKUPS_ROOT = Path('/home/ubuntu/ichiyon-backups')
SHARED_ROOT = Path('/home/ubuntu/ichiyon-shared')
SCOPE = ['data', 'assets/images', 'secrets', '.env']
V1_FILES = {'READY', 'previous', 'infra.json', 'production.dump', 'persistence.tar', 'checksums.sha256'}
RELEASE_FILES = {'REVISION', 'src', 'compose.immutable.yml', 'immutable-image.txt',
                 'persistence.txt', 'rollback-images.txt', 'validate-immutable-compose.py'}
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}')


class BackupError(ValueError):
    """Only stable codes, never archive contents or secret values."""


def need(ok, code):
    if not ok:
        raise BackupError(code)


def signature(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def opened_signature(value):
    # Windows Python exposes creation time through lstat but change time through
    # fstat. Keep the full path signature check, compare portable fd fields.
    values = signature(value)
    return values[:5] + values[6:] if os.name == 'nt' else values


def directory(path):
    path = Path(path).absolute()
    for part in [path] + list(path.parents):
        need(stat.S_ISDIR(part.lstat().st_mode) and not part.is_symlink(), 'directory_not_normal')
    need(path.resolve(strict=True) == path, 'directory_alias')
    return path


@contextmanager
def regular(path):
    directory(path.parent)
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'file_not_exclusive_regular')
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        need(opened_signature(os.fstat(stream.fileno())) == opened_signature(before), 'file_changed')
        yield stream
        need(opened_signature(os.fstat(stream.fileno())) == opened_signature(before)
             and signature(path.lstat()) == signature(before), 'file_changed')


def content(path, limit=2 * 1024 * 1024):
    with regular(path) as stream:
        data = stream.read(limit + 1)
    need(len(data) <= limit, 'metadata_too_large')
    return data


def digest(path):
    value = hashlib.sha256()
    with regular(path) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(BackupError('invalid_json_number')))


def checksums(root, names):
    lines = content(root / 'checksums.sha256', 4096).decode('ascii').splitlines()
    result = {}
    for line in lines:
        match = re.fullmatch(r'([0-9a-f]{64})  ([a-z.]+)', line)
        need(match is not None, 'checksum_manifest_invalid')
        expected, name = match.groups()
        need(name in names and name not in result, 'checksum_manifest_invalid')
        need(digest(root / name) == expected, 'checksum_mismatch')
        result[name] = expected
    need(set(result) == set(names), 'checksum_manifest_incomplete')
    return result


def canonical(name):
    need(isinstance(name, str) and name and not any(ord(c) < 32 for c in name)
         and '\\' not in name and ':' not in name, 'archive_path_invalid')
    path = PurePosixPath(name)
    need(not path.is_absolute() and '..' not in path.parts and str(path) == name,
         'archive_path_invalid')
    need(all(not re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', x)
             and not x.endswith((' ', '.')) for x in path.parts), 'archive_path_invalid')
    return path


def manifest_inventory(items):
    need(type(items) is list, 'manifest_inventory_invalid')
    for entry in items:
        need(type(entry) is dict and set(entry) == {'path', 'type', 'size', 'sha256'},
             'manifest_inventory_invalid')
        canonical(entry['path'])
        need(entry['type'] in ('file', 'directory') and type(entry['size']) is int
             and entry['size'] >= 0, 'manifest_inventory_invalid')
        need((entry['type'] == 'directory' and entry['size'] == 0 and entry['sha256'] is None)
             or (entry['type'] == 'file' and isinstance(entry['sha256'], str)
                 and DIGEST.fullmatch(entry['sha256'])), 'manifest_inventory_invalid')
    return items


def private_parents(root, parts):
    for part in parts:
        root = root / part
        root.mkdir(mode=0o700, exist_ok=True)
        directory(root)
    return root


def private_output(path):
    return os.fdopen(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                            getattr(os, 'O_NOFOLLOW', 0), 0o600), 'wb')


def archive_inventory(path, *, recovery=False, split=False, write_to=None):
    entries, end, kinds, ancestors = [], 0, {}, set()
    with regular(path) as stream:
        with tarfile.open(fileobj=stream, mode='r:') as archive:
            for member in archive:
                name = member.name.rstrip('/')
                parsed = canonical(name)
                need(name not in kinds, 'archive_duplicate')
                need((member.isfile() or member.isdir()) and not member.sparse, 'archive_type_invalid')
                need(not member.isdir() or member.size == 0, 'archive_directory_payload')
                allowed = (name == 'data/backups' or name.startswith('data/backups/')) if recovery else (
                    name in SCOPE or name == 'assets' or name.startswith(('data/', 'assets/images/', 'secrets/')))
                need(allowed, 'archive_scope_invalid')
                need(not split or not (name == 'data/backups' or name.startswith('data/backups/')),
                     'recursive_scope_not_separated')
                if name in ('data', 'data/backups', 'assets', 'assets/images', 'secrets'):
                    need(member.isdir(), 'archive_root_not_directory')
                if name == '.env':
                    need(member.isfile(), 'env_not_regular')
                need(all(kinds.get(str(a)) != 'file' for a in parsed.parents)
                     and not (member.isfile() and name in ancestors), 'archive_file_parent')
                ancestors.update(str(a) for a in parsed.parents)
                kinds[name] = 'file' if member.isfile() else 'directory'
                count, value = 0, hashlib.sha256()
                destination = None
                if write_to is not None:
                    destination = write_to.joinpath(*parsed.parts)
                    private_parents(write_to, parsed.parts[:-1])
                    if member.isdir():
                        destination.mkdir(mode=0o700, exist_ok=True)
                        directory(destination)
                if member.isfile():
                    output = None
                    try:
                        if destination is not None:
                            output = private_output(destination)
                        with archive.extractfile(member) as source:
                            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                                count += len(chunk)
                                value.update(chunk)
                                if output is not None:
                                    output.write(chunk)
                        need(count == member.size, 'archive_truncated')
                    finally:
                        if output is not None:
                            output.close()
                entries.append(dict(path=name, type=kinds[name], size=count,
                                    sha256=value.hexdigest() if member.isfile() else None))
                end = max(end, member.offset_data + ((member.size + 511) // 512) * 512)
        required = {'data/backups'} if recovery else set(SCOPE)
        need(required <= set(kinds), 'archive_scope_incomplete')
        stream.seek(end)
        padding = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            need(not any(chunk), 'archive_trailing_content')
            padding += len(chunk)
        need(padding >= 1024 and padding % 512 == 0, 'archive_eof_invalid')
    return sorted(entries, key=lambda e: e['path'])


def release_contract(root, sha):
    need(isinstance(sha, str) and SHA.fullmatch(sha), 'release_sha_invalid')
    path = directory(directory(root) / sha)
    need({p.name for p in path.iterdir()} == RELEASE_FILES, 'release_layout_invalid')
    need(content(path / 'REVISION') == (sha + '\n').encode(), 'release_revision_invalid')
    directory(path / 'src')
    if (path / 'src/REVISION').exists():
        need(content(path / 'src/REVISION') == (sha + '\n').encode(), 'release_revision_invalid')
    metadata = content(path / 'immutable-image.txt').decode('utf8')
    tag = 'ichiyon-robot-app:' + sha
    if metadata.strip() != tag:
        need(not (path / 'src/REVISION').exists(), 'legacy_release_invalid')
        fields = dict(line.split('=', 1) for line in metadata.splitlines())
        need(fields.get('release_sha') == sha and fields.get('image_tag') == tag
             and IMAGE.fullmatch(fields.get('image_id', '')), 'legacy_release_invalid')
    pins = content(path / 'rollback-images.txt').decode('ascii').splitlines()
    need(len(pins) == 3 and all(IMAGE.fullmatch(x) for x in pins), 'rollback_images_invalid')
    metadata_hashes = {name: digest(path / name) for name in sorted(RELEASE_FILES - {'src'})}
    return dict(sha=sha, image_tag=tag, rollback_images=sorted(set(pins)), metadata_sha256=metadata_hashes)


def list_dump(path):
    """Parse a custom dump only. No database connection or restore command."""
    with regular(path) as stream:
        result = subprocess.run(['pg_restore', '--list'], stdin=stream, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=120, check=False)
    return result.returncode == 0 and bool(result.stdout)


def validate_backup(path, *, releases_root=RELEASES_ROOT, recovery_root=RECOVERY_ROOT,
                    dump_validator=list_dump):
    try:
        return _validate_backup(Path(path), Path(releases_root), Path(recovery_root), dump_validator)
    except BackupError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, tarfile.TarError,
            subprocess.SubprocessError):
        raise BackupError('backup_validation_unavailable_or_invalid') from None


def _validate_backup(path, releases_root, recovery_root, dump_validator):
    path = directory(path)
    root_before = signature(path.lstat())
    need(SHA.fullmatch(path.name), 'backup_target_invalid')
    names = {p.name for p in path.iterdir()}
    v2 = 'manifest.json' in names
    need(names == V1_FILES | ({'manifest.json'} if v2 else set()), 'backup_members_invalid')
    before = {p.name: signature(p.lstat()) for p in path.iterdir()}
    need(content(path / 'READY', 0) == b'', 'ready_invalid')
    previous = content(path / 'previous', 1024).decode('utf8')
    previous_sha = previous.rstrip('\n').rsplit('/', 1)[-1]
    need(SHA.fullmatch(previous_sha) and previous == (releases_root / previous_sha).as_posix() + '\n',
         'previous_reference_invalid')
    releases = [release_contract(releases_root, s) for s in (path.name, previous_sha)]
    infra = strict_json(content(path / 'infra.json'))
    need(isinstance(infra, list) and len(infra) == 2 and all(
        isinstance(row, list) and len(row) == 3 and isinstance(row[0], str) and DIGEST.fullmatch(row[0])
        and type(row[1]) is int and row[1] >= 0 and isinstance(row[2], str)
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', row[2]) for row in infra)
        and infra[0][0] != infra[1][0], 'infra_invalid')
    sums = checksums(path, {'production.dump', 'persistence.tar'} |
                     ({'manifest.json', 'previous', 'infra.json'} if v2 else set()))
    with regular(path / 'production.dump') as stream:
        need(stream.read(5) == b'PGDMP', 'dump_header_invalid')
    need(callable(dump_validator) and dump_validator(path / 'production.dump') is True, 'dump_not_readable')
    manifest, recovery_path, recovery_before, split = None, None, None, False
    if v2:
        manifest = strict_json(content(path / 'manifest.json', 32 * 1024 * 1024))
        need(isinstance(manifest, dict) and set(manifest) == {'format', 'version', 'target_release',
             'previous_release', 'persistence', 'recovery', 'inventory'}, 'manifest_shape_invalid')
        need(manifest['format'] == 'ichiyon-production-backup' and type(manifest['version']) is int
             and manifest['version'] == 2 and manifest['target_release'] == path.name
             and manifest['previous_release'] == previous_sha, 'manifest_identity_invalid')
        scope = manifest['persistence']
        need(isinstance(scope, dict) and set(scope) == {'include', 'exclude'} and scope['include'] == SCOPE
             and scope['exclude'] in ([], ['data/backups']), 'manifest_scope_invalid')
        split = bool(scope['exclude'])
        if split:
            recovery = manifest['recovery']
            need(isinstance(recovery, dict) and set(recovery) == {'archive_id'}
                 and isinstance(recovery['archive_id'], str) and DIGEST.fullmatch(recovery['archive_id']),
                 'recovery_reference_invalid')
            root = directory(directory(recovery_root) / recovery['archive_id'])
            need({p.name for p in root.iterdir()} == {'READY', 'checksums.sha256', 'recovery.tar'},
                 'recovery_members_invalid')
            recovery_before = (signature(root.lstat()), {p.name:signature(p.lstat()) for p in root.iterdir()})
            need(content(root / 'READY', 0) == b'', 'recovery_ready_invalid')
            recovery_sums = checksums(root, {'recovery.tar'})
            need(recovery_sums['recovery.tar'] == recovery['archive_id'], 'recovery_identity_invalid')
            recovery_path = root / 'recovery.tar'
        else:
            need(manifest['recovery'] is None, 'unexpected_recovery_reference')
    inventory = archive_inventory(path / 'persistence.tar', split=split)
    if recovery_path is not None:
        inventory += archive_inventory(recovery_path, recovery=True)
        inventory.sort(key=lambda e: e['path'])
        root = recovery_path.parent
        need(recovery_before == (signature(root.lstat()), {p.name:signature(p.lstat()) for p in root.iterdir()}),
             'recovery_archive_changed')
    need(len({e['path'] for e in inventory}) == len(inventory), 'restore_inventory_overlap')
    if v2:
        need(manifest_inventory(manifest['inventory']) == inventory,
             'restore_inventory_mismatch')
    metadata_hashes = {name: digest(path / name) for name in sorted(names - {'persistence.tar'})}
    need(releases == [release_contract(releases_root, s) for s in (path.name, previous_sha)],
         'release_contract_changed')
    need(signature(directory(path).lstat()) == root_before, 'backup_directory_changed')
    need(before == {p.name: signature(p.lstat()) for p in path.iterdir()}, 'backup_changed')
    return dict(format_version=2 if v2 else 1, target_release=path.name, previous_release=previous_sha,
                checksum_verified=True, pg_restore_list=True, persistence_tar_verified=True,
                scope=manifest['persistence'] if v2 else {'include':SCOPE[:], 'exclude':[]},
                recovery_archive_id=manifest['recovery']['archive_id'] if split else None,
                inventory=inventory, checksums=sums, releases=releases, metadata_sha256=metadata_hashes,
                restore_validation='checksums_dump_list_full_tar_inventory_not_database_restore')


def restore_files(path, destination, *, releases_root=RELEASES_ROOT, recovery_root=RECOVERY_ROOT,
                  dump_validator=list_dump):
    """Offline rehearsal only: empty temp directory, never any production root.

    This restores bytes into a sandbox, not a database, app or production path.
    Tests restore production.dump separately into their disposable PostgreSQL.
    """
    destination = directory(destination)
    temp = Path(tempfile.gettempdir()).resolve()
    need(temp in destination.parents and not list(destination.iterdir()), 'rehearsal_requires_empty_temp_directory')
    need(not any(root == destination or root in destination.parents for root in
                 (Path('/home/ubuntu'), RELEASES_ROOT, RECOVERY_ROOT, BACKUPS_ROOT, SHARED_ROOT)),
         'production_restore_forbidden')
    proof = validate_backup(path, releases_root=releases_root, recovery_root=recovery_root,
                            dump_validator=dump_validator)
    os.chmod(destination, 0o700)
    path = Path(path)
    persistence, metadata = destination / 'persistence', destination / 'metadata'
    persistence.mkdir(mode=0o700); metadata.mkdir(mode=0o700)
    extracted = archive_inventory(path / 'persistence.tar', split=bool(proof['recovery_archive_id']), write_to=persistence)
    if proof['recovery_archive_id']:
        extracted += archive_inventory(Path(recovery_root) / proof['recovery_archive_id'] / 'recovery.tar',
                                       recovery=True, write_to=persistence)
    need(sorted(extracted, key=lambda e: e['path']) == proof['inventory'], 'rehearsal_inventory_changed')
    # Copy only fixed metadata names. Do not follow an arbitrary manifest path.
    for name in sorted(V1_FILES | ({'manifest.json'} if proof['format_version'] == 2 else set())):
        if name == 'persistence.tar':
            continue
        with regular(path / name) as source, private_output(metadata / name) as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                output.write(chunk)
        need(digest(metadata / name) == proof['metadata_sha256'][name], 'rehearsal_metadata_changed')
    # Detect mutation between preflight, extraction and metadata copy.
    after = validate_backup(path, releases_root=releases_root, recovery_root=recovery_root,
                            dump_validator=dump_validator)
    need(after == proof, 'backup_changed_during_rehearsal')
    return dict(persistence=str(persistence), metadata=str(metadata), validation=proof)
