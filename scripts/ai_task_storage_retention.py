"""Read-only production inventory for the retention proposal; stdout JSON only.

No apply mode, filesystem writer, Docker mutation, shell, deployment import or
lock acquisition exists here. Run reviewed source over SSH stdin, with the pure
graph module loaded in memory. Metadata and checksums are read; archives are
never extracted and env/secret/log bodies are never reported.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import time

from ai_task_storage_retention_graph import build_plan

SHA = re.compile(r'[0-9a-f]{40}')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}')
NAME = re.compile(r'[A-Za-z0-9_.-]{1,180}')
HOME = Path('/home/ubuntu')
DEFAULT_PATHS = dict(home=HOME, releases=HOME / 'ichiyon-releases',
                     backups=HOME / 'ichiyon-backups', current=HOME / 'ichiyon-current',
                     shared=HOME / 'ichiyon-shared')
RELEASE_FILES = {'REVISION', 'src', 'compose.immutable.yml', 'immutable-image.txt',
                 'persistence.txt', 'rollback-images.txt', 'validate-immutable-compose.py'}
BACKUP_FILES = {'READY', 'previous', 'infra.json', 'production.dump',
                'persistence.tar', 'checksums.sha256'}
STAGE_PREFIXES = ('.prepare-', '.release-', '.backup-', '.ichiyon-deploy-helper.',
                  '.ichiyon-deploy-infra.', '.ichiyon-pointer.')
RELATED_BACKUPS = ('backups', 'ichiyon-deploy-backups', 'ichiyon-prod-backup',
                   'ichiyon-data-backup', 'ichiyon-db-backups', 'prod-backups',
                   'ichiyon-robot/backups')


class DockerReader:
    """Inventory subprocess capability: fixed Docker read operations only."""

    @staticmethod
    def _run(argv):
        fixed = (['docker', 'info', '--format',
                  '{"DockerRootDir":{{json .DockerRootDir}},"Driver":{{json .Driver}}}'],
                 ['docker', 'image', 'ls', '--all', '--quiet', '--no-trunc'],
                 ['docker', 'ps', '--all', '--quiet', '--no-trunc'])
        inspect = (argv[:3] in (['docker', 'image', 'inspect'], ['docker', 'container', 'inspect'])
                   and 0 < len(argv[3:]) <= 32 and all(
                       (IMAGE if argv[1] == 'image' else re.compile(r'[0-9a-f]{64}')).fullmatch(value)
                       for value in argv[3:]))
        if argv not in fixed and not inspect:
            raise ValueError('docker_operation_rejected')
        result = subprocess.run(argv, shell=False, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=60, check=False)
        if result.returncode or len(result.stdout) > 32 * 1024 * 1024:
            raise ValueError('docker_inventory_unavailable')
        return result.stdout.decode('utf-8')

    def __call__(self, kind, identifiers=()):
        if identifiers:
            raise ValueError('external_docker_arguments_rejected')
        if kind == 'info':
            return json.loads(self._run(['docker', 'info', '--format',
                '{"DockerRootDir":{{json .DockerRootDir}},"Driver":{{json .Driver}}}']))
        if kind not in ('images', 'containers'):
            raise ValueError('docker_operation_rejected')
        command = ['docker', 'image', 'ls', '--all', '--quiet', '--no-trunc'] if kind == 'images' else [
            'docker', 'ps', '--all', '--quiet', '--no-trunc']
        ids = sorted(set(self._run(command).split()))
        pattern = IMAGE if kind == 'images' else re.compile(r'[0-9a-f]{64}')
        if any(not pattern.fullmatch(item) for item in ids):
            raise ValueError('docker_identity_invalid')
        output = []
        for offset in range(0, len(ids), 32):
            argv = ['docker', 'image', 'inspect'] if kind == 'images' else ['docker', 'container', 'inspect']
            output.extend(json.loads(self._run(argv + ids[offset:offset + 32])))
        return output


def identity(value):
    # Windows lstat/fstat expose different ctime semantics on some Python
    # versions. Production Linux retains the inode-change timestamp check.
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns if os.name != 'nt' else None)


def regular_stream(path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('file_not_regular')
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    stream = os.fdopen(descriptor, 'rb')
    if identity(os.fstat(stream.fileno())) != identity(before):
        stream.close()
        raise ValueError('file_changed')
    return stream


def production_dump_list(path):
    """Use the production DB container's parser without connecting to a DB.

    Only the dump bytes go to stdin. The fixed command lists its TOC, with no
    shell, database, output-file or restore options; neither TOC nor stderr is
    exposed by the collector. A missing tool/container fails validation closed.
    """
    with regular_stream(path) as stream:
        before = identity(os.fstat(stream.fileno()))
        result = subprocess.run(
            ['docker', 'exec', '-i', 'ichiyon-robot-db', 'pg_restore', '--list'],
            stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=120, check=False, shell=False)
        if identity(os.fstat(stream.fileno())) != before or identity(path.lstat()) != before:
            raise ValueError('dump_changed_during_validation')
    return result.returncode == 0 and bool(result.stdout)


def read_metadata(path, limit=65536):
    """Small, regular, non-symlink metadata; no raw text crosses the collector."""
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
        raise ValueError('metadata_not_small_regular_file')
    # O_NOFOLLOW also closes the final-component symlink race on Linux.
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError('metadata_changed')
        data = stream.read(limit + 1)
        if identity(os.fstat(stream.fileno())) != identity(metadata):
            raise ValueError('metadata_changed')
    if len(data) > limit:
        raise ValueError('metadata_too_large')
    return data.decode('utf-8')


def safe_name(path):
    return path.name if NAME.fullmatch(path.name) else 'unrecognized-entry'


def under(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def release_reference(value, root):
    path = Path(value.strip())
    if path.parent == root and SHA.fullmatch(path.name):
        return path.name
    raise ValueError('release_reference_unresolved')


def scan_allocation(path, owner, allocations):
    """lstat-only traversal, no mounted filesystem or symlink traversal."""
    total, complete, count = 0, True, 0
    seen = set()
    initial_device = path.lstat().st_dev
    stack = [path]
    while stack:
        item = stack.pop()
        try:
            value = item.lstat()
            if value.st_dev != initial_device:
                complete = False
                continue
            if not (stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode)
                    or stat.S_ISLNK(value.st_mode)):
                complete = False
                continue
            key = '{}:{}'.format(value.st_dev, value.st_ino)
            blocks = getattr(value, 'st_blocks', None)
            size = blocks * 512 if blocks is not None else 0
            if blocks is None:
                complete = False
            if key not in seen:
                total += size
                count += 1
                seen.add(key)
            entry = allocations.setdefault(key, dict(id=key, allocated_bytes=size,
                owners=set(), paths=set(), links=value.st_nlink,
                regular=stat.S_ISREG(value.st_mode), open=False))
            entry['owners'].add(owner)
            entry['paths'].add(str(item))
            entry.setdefault('observations', {})[str(item)] = identity(value)
            if stat.S_ISDIR(value.st_mode):
                stack.extend(item.iterdir())
        except (OSError, ValueError):
            complete = False
    return total, complete, count


def base_node(path, category, allocations, identifier=None):
    value = path.lstat()
    identifier = identifier or safe_name(path)
    size, complete, entries = scan_allocation(path, category + ':' + identifier, allocations)
    return dict(id=identifier, path=str(path), allocated_bytes=size, size_complete=complete,
        entries=entries, created_at=value.st_mtime,
        timestamp_source='directory_mtime_not_creation_proof', ctime=value.st_ctime,
        validation='invalid', managed=True, references_complete=True,
        metadata_errors=[], unknown_reference_kinds=[])


def extract_references(text, paths, node):
    """Narrow reference syntax only; never emit raw metadata or arbitrary values."""
    node.setdefault('release_refs', [])
    node.setdefault('image_refs', [])
    node.setdefault('backup_refs', [])
    for match in re.finditer(re.escape(str(paths['releases'])) + r'/([0-9a-f]{40})(?![0-9a-f])', text):
        node['release_refs'].append(match.group(1))
    node['image_refs'].extend(IMAGE.findall(text))
    node['image_refs'].extend(re.findall(r'ichiyon-robot-app:[0-9a-f]{40}(?![0-9a-f])', text))
    for match in re.finditer(re.escape(str(paths['backups'])) + r'/([A-Za-z0-9_.-]+)', text):
        node['backup_refs'].append(match.group(1))


def read_release(path, paths, tags, allocations):
    node = base_node(path, 'releases', allocations)
    node.update(image_id=None, rollback_images=[])
    try:
        if path.is_symlink() or not path.is_dir() or set(p.name for p in path.iterdir()) != RELEASE_FILES:
            raise ValueError('release_layout_unknown')
        if read_metadata(path / 'REVISION') != path.name + '\n':
            raise ValueError('release_revision_invalid')
        if (path / 'src').is_symlink() or not (path / 'src').is_dir():
            raise ValueError('source_layout_unknown')
        src_revision = path / 'src/REVISION'
        if src_revision.exists() and read_metadata(src_revision) != path.name + '\n':
            raise ValueError('source_revision_invalid')
        metadata = read_metadata(path / 'immutable-image.txt')
        tag = 'ichiyon-robot-app:' + path.name
        node['image_id'] = tags.get(tag)
        if metadata.strip() == tag:
            node['validation'] = 'verified'
        else:
            extract_references(metadata, paths, node)
            fields = dict(line.split('=', 1) for line in metadata.splitlines() if '=' in line)
            if fields.get('release_sha') != path.name or fields.get('image_tag') != tag:
                raise ValueError('legacy_release_identity_invalid')
            recorded = fields.get('image_id', '')
            if not IMAGE.fullmatch(recorded) or node['image_id'] != recorded:
                raise ValueError('legacy_image_identity_invalid')
            node['validation'] = 'legacy'
            node['metadata_errors'].append('legacy_manifest_requires_restore_review')
        rollback = read_metadata(path / 'rollback-images.txt')
        if all(IMAGE.fullmatch(line) for line in rollback.splitlines()) and len(rollback.splitlines()) == 3:
            node['rollback_images'] = sorted(set(rollback.splitlines()))
        elif node['validation'] == 'legacy':
            extract_references(rollback, paths, node)
            node['rollback_images'] = sorted(set(IMAGE.findall(rollback)))
            if not node['rollback_images']:
                raise ValueError('rollback_metadata_unresolved')
        else:
            raise ValueError('rollback_metadata_invalid')
        for name in ('compose.immutable.yml', 'persistence.txt'):
            extract_references(read_metadata(path / name), paths, node)
        # The validator is inspected as a regular file, never imported/executed.
        validator = (path / 'validate-immutable-compose.py').lstat()
        if not stat.S_ISREG(validator.st_mode) or not node['image_id']:
            raise ValueError('release_recovery_dependency_unavailable')
    except (OSError, ValueError, UnicodeError):
        node['validation'] = 'invalid'
        node['metadata_errors'].append('release_metadata_unavailable_or_inconsistent')
        node['unknown_reference_kinds'] = ['releases', 'images', 'backups']
    return node


def digest_file(path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('checksum_file_not_regular')
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    digest = hashlib.sha256()
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('checksum_file_changed')
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if identity(before) != identity(after):
        raise ValueError('checksum_file_changed')
    return digest.hexdigest()


def validate_archive(path):
    required = {'data', 'assets/images', 'secrets', '.env'}
    with regular_stream(path) as stream, tarfile.open(fileobj=stream, mode='r:') as archive:
        for member in archive:
            name = member.name.rstrip('/')
            if name.startswith('/') or '..' in Path(name).parts:
                raise ValueError('archive_path_invalid')
            if not (member.isfile() or member.isdir()):
                raise ValueError('archive_type_invalid')
            required.discard(name)
    if required:
        raise ValueError('archive_scope_incomplete')


def read_backup(path, paths, allocations, verify_checksums=True, related=False,
                validated_tar_hashes=None):
    identifier = 'related:' + path.relative_to(paths['home']).as_posix() if related else safe_name(path)
    node = base_node(path, 'backups', allocations, identifier)
    node.update(target_release=path.name if SHA.fullmatch(path.name) else None,
                previous_release=None, checksum_verified=False, ready=False)
    try:
        if path.is_symlink() or not path.is_dir():
            raise ValueError('backup_root_invalid')
        names = {p.name for p in path.iterdir()}
        node['metadata_files'] = sorted(name for name in names if NAME.fullmatch(name))
        if 'previous' in names:
            node['previous_release'] = release_reference(read_metadata(path / 'previous'), paths['releases'])
        else:
            node['validation'] = 'legacy' if related or not SHA.fullmatch(path.name) else 'incomplete'
            for name in sorted(names & {'manifest.txt', 'manifest.before.txt', 'manifest.after.txt',
                                        'compose.rollback.yml', 'compose.immutable.yml'}):
                extract_references(read_metadata(path / name), paths, node)
            node['metadata_errors'].append('legacy_restore_contract_requires_review')
            return node
        if 'manifest.json' in names:
            # Both full and split v2 backups require the complete restore
            # contract. Production uses the DB container's pg_restore parser;
            # alternate fixture roots retain the injected/local parser path.
            import ai_task_backup
            if not verify_checksums:
                node['validation'] = 'incomplete'
                node['metadata_errors'].append('checksums_not_verified')
                return node
            dump_options = {'dump_validator': production_dump_list} if paths == DEFAULT_PATHS else {}
            proof = ai_task_backup.validate_backup(path, releases_root=paths['releases'],
                recovery_root=paths['home'] / 'ichiyon-recovery-archives', **dump_options)
            node.update(validation='verified', checksum_verified=True, ready=True,
                backup_format_version=2, checksums=proof['checksums'],
                restore_validation=proof['restore_validation'],
                recovery_archive_ids=[proof['recovery_archive_id']] if proof['recovery_archive_id'] else [],
                created_at=(path / 'READY').stat().st_mtime,
                timestamp_source='ready_mtime_not_creation_proof')
            return node
        if names != BACKUP_FILES:
            node['validation'] = 'incomplete'
            node['metadata_errors'].append('ready_or_backup_members_missing')
            return node
        node['ready'] = stat.S_ISREG((path / 'READY').lstat().st_mode)
        if not node['ready']:
            raise ValueError('ready_not_regular')
        node['created_at'] = (path / 'READY').stat().st_mtime
        node['timestamp_source'] = 'ready_mtime_not_creation_proof'
        infra = json.loads(read_metadata(path / 'infra.json'))
        if (not isinstance(infra, list) or len(infra) != 2 or any(
                not isinstance(row, list) or len(row) != 3 or
                not isinstance(row[0], str) or not re.fullmatch(r'[0-9a-f]{64}', row[0]) or
                type(row[1]) is not int or row[1] < 0 or not isinstance(row[2], str) or
                not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', row[2])
                for row in infra) or infra[0][0] == infra[1][0]):
            raise ValueError('infra_metadata_invalid')
        manifest = read_metadata(path / 'checksums.sha256')
        matches = re.findall(r'^([0-9a-f]{64})  (production.dump|persistence.tar)$', manifest, re.M)
        if len(matches) != 2 or len(manifest.splitlines()) != 2 or len({name for _, name in matches}) != 2:
            raise ValueError('checksum_manifest_invalid')
        node['checksums'] = {name: digest for digest, name in matches}
        if not verify_checksums:
            node['validation'] = 'incomplete'
            node['metadata_errors'].append('checksums_not_verified')
            return node
        for name, expected in node['checksums'].items():
            if digest_file(path / name) != expected:
                raise ValueError('checksum_mismatch')
        with regular_stream(path / 'production.dump') as stream:
            if stream.read(5) != b'PGDMP':
                raise ValueError('dump_header_invalid')
        tar_hash = node['checksums']['persistence.tar']
        # Every physical copy is still hashed above. Byte-identical verified
        # archives share only the expensive member/scope validation in this
        # single observation; no cache is persisted or trusted across runs.
        if validated_tar_hashes is None or tar_hash not in validated_tar_hashes:
            validate_archive(path / 'persistence.tar')
            if validated_tar_hashes is not None:
                validated_tar_hashes.add(tar_hash)
        node['validation'] = 'verified'
        node['checksum_verified'] = True
        node['restore_validation'] = 'metadata_checksums_tar_scope_dump_header_not_full_restore'
    except (OSError, ValueError, UnicodeError, tarfile.TarError):
        node['validation'] = 'invalid'
        node['metadata_errors'].append('backup_metadata_checksum_or_archive_invalid')
        if not node['previous_release']:
            node['unknown_reference_kinds'] = ['releases', 'images']
    return node


def process_observation(proc, lock_path):
    result = dict(lock_held=None, owner_pids=[], operation_shas=[], open_paths=set(), complete=True)
    try:
        value = lock_path.stat()
        lock_id = (os.major(value.st_dev), os.minor(value.st_dev), value.st_ino)
        result['lock_held'] = False
        for line in (proc / 'locks').read_text().splitlines():
            fields = line.split()
            for pos, field in enumerate(fields):
                if re.fullmatch(r'[0-9a-f]+:[0-9a-f]+:[0-9]+', field):
                    major, minor, inode = field.split(':')
                    if (int(major, 16), int(minor, 16), int(inode)) == lock_id:
                        result['lock_held'] = True
                        pid = fields[pos - 1]
                        if pid.isdigit():
                            result['owner_pids'].append(int(pid))
        for directory in proc.iterdir():
            if not directory.name.isdigit():
                continue
            try:
                args = (directory / 'cmdline').read_bytes().split(b'\0')
                # Only exact arguments from fixed protocol processes are used.
                if any(b'ichiyon' in arg for arg in args) or b'bash' in args:
                    result['operation_shas'].extend(arg.decode('ascii') for arg in args
                        if re.fullmatch(b'[0-9a-f]{40}', arg))
                for entry in [directory / 'cwd'] + list((directory / 'fd').iterdir()):
                    try:
                        target = os.readlink(str(entry))
                        if target.startswith('/'):
                            result['open_paths'].add(target.removesuffix(' (deleted)') if hasattr(str, 'removesuffix') else target.replace(' (deleted)', ''))
                    except FileNotFoundError:
                        pass
                # mmap can retain an inode after the last ordinary fd closes.
                for line in (directory / 'maps').read_text(errors='replace').splitlines():
                    fields = line.split(None, 5)
                    if len(fields) == 6 and fields[5].startswith('/'):
                        result['open_paths'].add(fields[5].split(' (deleted)', 1)[0])
            except (FileNotFoundError, ProcessLookupError):
                pass
            except PermissionError:
                result['complete'] = False
    except (OSError, ValueError):
        result['complete'] = False
    result['operation_shas'] = sorted(set(result['operation_shas']))
    return result


def docker_fingerprint(images, containers):
    return (sorted((item.get('Id'), tuple(sorted(item.get('RepoTags') or []))) for item in images),
            sorted((item.get('Id'), item.get('Image'), item.get('State', {}).get('Running')) for item in containers))


def collect_snapshot(paths=None, docker=None, proc_root=None, verify_checksums=True):
    paths = {key: Path(value) for key, value in (paths or DEFAULT_PATHS).items()}
    docker = docker or DockerReader()
    proc = Path(proc_root or '/proc')
    snapshot = dict(schema_version=1, captured_at=time.time(), current_release=None,
        previous_known_good=None, runtime_releases=[], releases=[], backups=[], images=[],
        containers=[], staging=[], explicit_pins=dict(releases=[], backups=[], images=[]),
        references_complete=True, collection_errors=[], filesystem_allocations_complete=False)
    allocations = {}
    validated_tar_hashes = set()
    def problem(code):
        snapshot['references_complete'] = False
        if code not in snapshot['collection_errors']:
            snapshot['collection_errors'].append(code)
    try:
        before_current = paths['current'].resolve(strict=True)
        snapshot['current_release'] = release_reference(str(before_current), paths['releases'].resolve())
        before_names = {kind: sorted(p.name for p in paths[kind].iterdir() if SHA.fullmatch(p.name))
                        for kind in ('releases', 'backups')}
        info, raw_images, raw_containers = docker('info'), docker('images'), docker('containers')
    except Exception:
        problem('core_inventory_unavailable')
        return snapshot
    tags = {tag: item['Id'] for item in raw_images for tag in item.get('RepoTags') or []}
    container_images = {item.get('Image') for item in raw_containers}
    for item in raw_images:
        identifier = item.get('Id', '')
        if not IMAGE.fullmatch(identifier):
            problem('image_identity_unavailable')
            continue
        image_tags = [tag for tag in item.get('RepoTags') or [] if re.fullmatch(r'[A-Za-z0-9_./:@-]+', tag)]
        managed = identifier in container_images or any(
            re.fullmatch(r'ichiyon-robot-app:[0-9a-f]{40}', tag) for tag in image_tags)
        node = dict(id=identifier, tags=image_tags, size_bytes=item.get('Size'), allocated_bytes=0,
                    size_complete=isinstance(item.get('Size'), int), validation='verified', managed=managed,
                    created_at=item.get('Created'), layers=[], layers_complete=True,
                    references_complete=True)
        chain = None
        for diff in item.get('RootFS', {}).get('Layers', []):
            if not IMAGE.fullmatch(diff):
                node['layers_complete'] = False
                continue
            chain = diff if chain is None else 'sha256:' + hashlib.sha256((chain + ' ' + diff).encode()).hexdigest()
            size = None
            try:
                if info.get('Driver') != 'overlay2':
                    raise ValueError('unsupported_layer_store')
                path = Path(info['DockerRootDir']) / 'image/overlay2/layerdb/sha256' / chain[7:] / 'size'
                value = read_metadata(path, 128).strip()
                if not value.isdigit():
                    raise ValueError('invalid_layer_size')
                size = int(value)
            except (OSError, ValueError, KeyError):
                node['layers_complete'] = False
            node['layers'].append(dict(id=chain, size_bytes=size))
        if not node['layers']:
            node['layers_complete'] = False
        snapshot['images'].append(node)
        if any('rollback' in tag or ':pin-' in tag for tag in image_tags):
            snapshot['explicit_pins']['images'].append(identifier)
    for item in raw_containers:
        labels = item.get('Config', {}).get('Labels') or {}
        configured = item.get('Config', {}).get('Image', '')
        match = re.fullmatch(r'ichiyon-robot-app:([0-9a-f]{40})', configured)
        name = item.get('Name', '').lstrip('/')
        sha = match.group(1) if match else None
        migration_prefix = 'ichiyon-robot-migrate-'
        if not sha and name.startswith(migration_prefix) and SHA.fullmatch(name[len(migration_prefix):]):
            sha = name[len(migration_prefix):]
        node = dict(id=item.get('Id'), image_id=item.get('Image'), release_sha=sha,
                    running=bool(item.get('State', {}).get('Running')),
                    name=name if NAME.fullmatch(name) else 'unrecognized-container',
                    deployment_owned=labels.get('com.docker.compose.project') == 'ichiyon-robot' or
                        labels.get('ichiyon.fixed-deploy.migration') == 'true')
        snapshot['containers'].append(node)
        if sha and node['running'] and labels.get('com.docker.compose.service') in ('admin', 'bot', 'bot-irsia'):
            snapshot['runtime_releases'].append(sha)
    observed_before = process_observation(proc, paths['home'] / 'ichiyon-deploy.lock')
    for kind, reader in (('releases', read_release), ('backups', read_backup)):
        try:
            for path in sorted(paths[kind].iterdir()):
                if path.name.startswith(STAGE_PREFIXES):
                    continue
                if kind == 'releases' and not SHA.fullmatch(path.name):
                    problem('unrecognized_release_entry')
                    continue
                try:
                    node = reader(path, paths, tags, allocations) if kind == 'releases' else reader(
                        path, paths, allocations, verify_checksums=verify_checksums,
                        validated_tar_hashes=validated_tar_hashes)
                    snapshot[kind].append(node)
                except (OSError, ValueError):
                    problem(kind + '_entry_unavailable')
        except OSError:
            problem(kind + '_inventory_unavailable')
    for relative in RELATED_BACKUPS:
        path = paths['home'] / relative
        if path.exists():
            try:
                snapshot['backups'].append(read_backup(path, paths, allocations, related=True))
            except (OSError, ValueError):
                problem('related_backup_inventory_unavailable')
    for node in snapshot['releases']:
        snapshot['explicit_pins']['images'].extend(node['rollback_images'])
        snapshot['explicit_pins']['backups'].extend(node.get('backup_refs', []))
    for node in snapshot['backups']:
        if node['id'] == snapshot['current_release'] and node['validation'] == 'verified':
            snapshot['previous_known_good'] = node['previous_release']
    if not snapshot['previous_known_good']:
        problem('previous_known_good_unresolved')
    # Optional operator pins are data, never Python/shell configuration.
    pins_path = paths['home'] / 'ichiyon-retention-pins.json'
    if pins_path.exists() or pins_path.is_symlink():
        try:
            pins = json.loads(read_metadata(pins_path))
            if not isinstance(pins, dict) or set(pins) - {'releases', 'images', 'backups'}:
                raise ValueError('pins_invalid')
            for kind, values in pins.items():
                if not isinstance(values, list) or any(not isinstance(value, str) or not (
                        IMAGE.fullmatch(value) if kind == 'images' else NAME.fullmatch(value)) for value in values):
                    raise ValueError('pins_invalid')
                snapshot['explicit_pins'][kind].extend(values)
        except (OSError, ValueError):
            problem('operator_pins_unresolved')
    stages = []
    for root in (paths['releases'], paths['backups'], paths['home']):
        try:
            stages.extend(p for p in root.iterdir() if p.name.startswith(STAGE_PREFIXES))
        except OSError:
            problem('staging_inventory_unavailable')
    for path in stages:
        try:
            node = base_node(path, 'staging', allocations, str(path))
            match = re.search(r'\.(?:prepare|release|backup)-([0-9a-f]{40})\.', path.name)
            node['operation_sha'] = match.group(1) if match else None
            node['validation'] = 'incomplete'
            node['ownership'] = 'unknown'
            node['ready'] = (path / 'READY').is_file() if path.is_dir() else False
            node['checksum_manifest_present'] = (path / 'checksums.sha256').is_file() if path.is_dir() else False
            node['metadata_errors'].append('no_durable_operation_owner_or_terminal_receipt')
            if path.is_dir() and not path.is_symlink():
                for name in ('previous', 'rollback-images.txt', 'immutable-image.txt'):
                    if (path / name).is_file():
                        try:
                            extract_references(read_metadata(path / name), paths, node)
                        except (OSError, ValueError, UnicodeError):
                            node['metadata_errors'].append('staging_metadata_unreadable')
                            node['unknown_reference_kinds'] = ['releases', 'images', 'backups']
                pointer = path / 'current'
                if pointer.is_symlink():
                    extract_references(os.readlink(str(pointer)), paths, node)
            snapshot['staging'].append(node)
        except (OSError, ValueError):
            snapshot['collection_errors'].append('staging_changed_during_observation')
    observed_after = process_observation(proc, paths['home'] / 'ichiyon-deploy.lock')
    open_paths = observed_before['open_paths'] | observed_after['open_paths']
    active_shas = set(observed_before['operation_shas'] + observed_after['operation_shas'])
    lock_held = observed_before['lock_held'] or observed_after['lock_held']
    for node in snapshot['staging']:
        active_path = any(value == node['path'] or value.startswith(node['path'] + '/') for value in open_paths)
        if active_path or node['operation_sha'] in active_shas or (
                lock_held and node['operation_sha'] == snapshot['current_release']) or (
                lock_held and node['operation_sha'] is None):
            node['ownership'] = 'possible_active'
        node['lock_held_during_observation'] = lock_held
    for node in snapshot['releases'] + snapshot['backups']:
        if node.get('id') in active_shas or any(
                value == node['path'] or value.startswith(node['path'] + '/') for value in open_paths):
            node['active'] = True
    snapshot['operation_observation'] = dict(lock_held=lock_held, operation_shas=sorted(active_shas),
        process_scan_complete=observed_before['complete'] and observed_after['complete'])
    if not snapshot['operation_observation']['process_scan_complete']:
        problem('process_reference_inventory_unavailable')
    try:
        after_names = {kind: sorted(p.name for p in paths[kind].iterdir() if SHA.fullmatch(p.name))
                       for kind in ('releases', 'backups')}
        if paths['current'].resolve(strict=True) != before_current or before_names != after_names or (
                docker_fingerprint(raw_images, raw_containers) != docker_fingerprint(docker('images'), docker('containers'))):
            problem('runtime_or_generation_inventory_changed')
    except Exception:
        problem('final_inventory_unavailable')
    for entry in allocations.values():
        # Detect replacement/in-place changes, including checksum inputs, after
        # the complete observation. Mutable staging churn is recorded separately.
        for path, before in entry['observations'].items():
            try:
                unchanged = identity(Path(path).lstat()) == before
            except OSError:
                unchanged = False
            if not unchanged and any(not owner.startswith('staging:') for owner in entry['owners']):
                problem('generation_content_changed_during_observation')
                break
        entry['open'] = bool(entry['paths'] & open_paths)
        entry['external_links'] = entry['regular'] and entry['links'] > len(entry['paths'])
        entry['owners'] = sorted(entry['owners'])
        for name in ('paths', 'links', 'regular', 'observations'):
            entry.pop(name)
    snapshot['inode_allocations'] = list(allocations.values())
    snapshot['filesystem_allocations_complete'] = snapshot['operation_observation']['process_scan_complete'] and all(
        node['size_complete'] for kind in ('releases', 'backups') for node in snapshot[kind])
    snapshot['captured_end_at'] = time.time()
    snapshot['layer_size_basis'] = 'overlay2_chain_layer_uncompressed_metadata_not_df_reclaim'
    snapshot['docker_layers_complete'] = all(node['layers_complete'] for node in snapshot['images'])
    snapshot['build_cache_layer_pins_known'] = False
    snapshot['runtime_releases'] = sorted(set(snapshot['runtime_releases']))
    if paths == DEFAULT_PATHS:
        # The receipt loader has a fixed trusted root and independently checks
        # current process/inode identities. Fixtures with alternate inventories
        # never consult the actual host's evidence namespace.
        from ai_task_storage_evidence import enrich_snapshot
        enrich_snapshot(snapshot, open_paths)
    return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    result = build_plan(collect_snapshot())
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
