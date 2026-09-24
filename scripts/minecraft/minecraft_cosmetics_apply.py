"""Durable, fixed-target pack application for the authenticated control API.

Only the admin application's compiler sends archives here. No client selects a
host, command, destination, world or service. Files are staged before shutdown.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import uuid
from zipfile import ZipFile, BadZipFile

# Authenticated managed archives only; mirrored by minecraft_resource_packs.py.
MAX_ARCHIVE = 192 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_FILES = 16384
PACKS = ('behavior_packs/import_structures', 'behavior_packs/ichiyon_avatar_bp', 'resource_packs/ichiyon_avatar_rp')
BEHAVIOR_PACKS = PACKS[:2]
RETIRED_PACKS = {PACKS[2]: '3e1bcf76-b5e3-465a-a184-d2d90cfa0d74'}
CATALOG = BEHAVIOR_PACKS[0] + '/scripts/cosmetics_catalog.js'
PERMISSIONS = 'config/2fbc1c02-0c4d-4e98-a851-c1e41337c7a8/permissions.json'
TERMINAL = ('succeeded', 'failed', 'recovery_failed')


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def deployment_lock():
    import fcntl
    path = Path.home() / '.ichiyon-ai-bds-deploy.lock'
    if path.is_symlink():
        raise ValueError('unsafe lock')
    with path.open('a+b') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def pack_path(value):
    if not isinstance(value, str):
        raise ValueError('invalid pack path')
    value = value.rstrip('/')
    if value not in BEHAVIOR_PACKS and not re.fullmatch(r'resource_packs/ichiyon_[a-z0-9_]+_rp', value):
        raise ValueError('untrusted pack root')
    return value


def safe_path(root, relative):
    """Reject linked ancestors as well as links inside a copied/deleted tree."""
    path = PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or '\\' in relative or ':' in relative or str(path) != relative:
        raise ValueError('unsafe destination')
    target = root / relative
    for entry in (root, *(root / Path(*path.parts[:i]) for i in range(1, len(path.parts) + 1))):
        if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
            raise ValueError('linked pack destination')
    return target


def check_tree(path):
    for entry in path.rglob('*'):
        if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
            raise ValueError('linked pack destination')
        if not entry.is_dir() and not entry.is_file():
            raise ValueError('invalid pack file')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def valid_uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('invalid pack UUID')
    return value


def valid_version(value):
    if not isinstance(value, list) or len(value) != 3 or any(type(v) is not int or not 0 <= v <= 2147483647 for v in value):
        raise ValueError('invalid version')
    return value


def next_version(version):
    result = list(valid_version(version))
    for index in (2, 1, 0):
        if result[index] < 2147483647:
            result[index] += 1
            return result
        result[index] = 0
    raise ValueError('version exhausted')


def pack_layout(stage):
    """Manifest discovery also supports archives predating the packs field."""
    proof = read_json(stage / 'cosmetics-build.json')
    discovered = {pack_path(p.parent.relative_to(stage).as_posix()) for p in stage.glob('*/*/manifest.json')}
    entries = proof.get('packs', sorted(discovered))
    if not isinstance(entries, list):
        raise ValueError('invalid packs')
    packs = [pack_path(entry['path'] if isinstance(entry, dict) else entry) for entry in entries]
    if len(set(packs)) != len(packs) or set(packs) != discovered or not set(BEHAVIOR_PACKS) <= discovered or not discovered - set(BEHAVIOR_PACKS):
        raise ValueError('pack list mismatch')
    for entry, pack in zip(entries, packs):
        if isinstance(entry, dict) and 'uuid' in entry and entry['uuid'] != read_json(stage / pack / 'manifest.json')['header']['uuid']:
            raise ValueError('pack identity mismatch')
    retired = proof.get('retired_packs', [])
    if not isinstance(retired, list):
        raise ValueError('invalid retired packs')
    seen = set()
    for entry in retired:
        path = pack_path(entry['path'])
        if path in packs or path in seen or RETIRED_PACKS.get(path) != entry['uuid']:
            raise ValueError('invalid retired identity')
        seen.add(path)
    return proof, packs, retired


def content_hash(directory):
    """Pack versions and UUID dependency versions do not change pack content."""
    check_tree(directory)
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob('*') if p.is_file()):
        relative = path.relative_to(directory).as_posix()
        data = path.read_bytes()
        if relative == 'manifest.json':
            manifest = json.loads(data.decode('utf-8-sig'))
            manifest['header'].pop('version', None)
            for module in manifest['modules']:
                module.pop('version', None)
            for dependency in manifest.get('dependencies', []):
                if 'uuid' in dependency and 'module_name' not in dependency:
                    dependency.pop('version', None)
            data = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        digest.update(relative.encode('utf-8') + b'\0' + hashlib.sha256(data).digest())
    return digest.hexdigest()


def prepare_manifests(stage, live):
    proof, packs, retired = pack_layout(stage)
    manifests, identities = {}, set()
    retired_ids = {entry['uuid'] for entry in retired}
    for pack in packs:
        manifest = read_json(stage / pack / 'manifest.json')
        identity = valid_uuid(manifest['header']['uuid'])
        if identity in identities or identity in retired_ids:
            raise ValueError('duplicate pack identity')
        identities.add(identity)
        version = valid_version(manifest['header']['version'])
        if not isinstance(manifest['modules'], list) or not manifest['modules']:
            raise ValueError('invalid modules')
        for module in manifest['modules']:
            module_id = valid_uuid(module['uuid'])
            if module_id in identities or module_id in retired_ids:
                raise ValueError('duplicate module identity')
            identities.add(module_id)
            valid_version(module['version'])
            if pack.startswith('resource_packs/') and module['type'] != 'resources':
                raise ValueError('invalid resource module')
        target = safe_path(live, pack)
        if target.exists():
            check_tree(target)
            previous = read_json(target / 'manifest.json')
            if identity != previous['header']['uuid']:
                raise ValueError('pack identity mismatch')
            current = valid_version(previous['header']['version'])
            version = current if content_hash(stage / pack) == content_hash(target) else max(version, next_version(current))
        manifest['header']['version'] = version
        for module in manifest['modules']:
            module['version'] = version
        manifests[pack] = manifest
    for entry in retired:
        target = safe_path(live, pack_path(entry['path']))
        if target.exists():
            check_tree(target)
            if read_json(target / 'manifest.json')['header']['uuid'] != entry['uuid']:
                raise ValueError('retired pack identity mismatch')
    versions = {m['header']['uuid']: m['header']['version'] for m in manifests.values()}
    for pack, manifest in manifests.items():
        for dependency in manifest.get('dependencies', []):
            if dependency.get('uuid') in retired_ids:
                raise ValueError('dependency on retired pack')
            if 'module_name' not in dependency and dependency.get('uuid') in versions:
                dependency['version'] = versions[dependency['uuid']]
        atomic_json(stage / pack / 'manifest.json', manifest)
    return proof, packs


def unpack(data, destination, live):
    """Accept bytes or a seekable upload; reject links, bombs and foreign packs."""
    if isinstance(data, bytes):
        if len(data) > MAX_ARCHIVE:
            raise ValueError('archive too large')
        data = BytesIO(data)
    seen, total = set(), 0
    try:
        data.seek(0, os.SEEK_END)
        if data.tell() > MAX_ARCHIVE:
            raise ValueError('archive too large')
        data.seek(0)
        with ZipFile(data) as archive:
            if len(archive.infolist()) > MAX_FILES:
                raise ValueError('too many files')
            for info in archive.infolist():
                name = info.filename
                path = PurePosixPath(name)
                allowed = name in ('cosmetics-build.json', 'cosmetics/catalog.lock.json')
                if not allowed and len(path.parts) >= 3:
                    pack_path('/'.join(path.parts[:2]))
                    allowed = True
                mode = info.external_attr >> 16
                if (not allowed or name.casefold() in seen or path.is_absolute() or '..' in path.parts
                        or '\\' in name or ':' in name or str(path) != name or info.is_dir()
                        or any(part.endswith(('.', ' ')) or re.search(r'[<>"|?*\x00-\x1f]', part)
                               or re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part) for part in path.parts)
                        or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))):
                    raise ValueError('invalid archive path')
                total += info.file_size
                if total > MAX_EXPANDED or info.file_size > MAX_FILE:
                    raise ValueError('expanded archive too large')
                seen.add(name.casefold())
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        proof, packs, _ = pack_layout(destination)
        if any(name not in ('cosmetics-build.json', 'cosmetics/catalog.lock.json') and not any(name.startswith(pack + '/') for pack in packs) for name in seen):
            raise ValueError('unlisted pack data')
        digest = proof['catalog_digest']
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid catalog')
        registry = json.loads((destination / 'cosmetics/catalog.lock.json').read_text(encoding='utf-8'))
        if registry['digest'] != digest or digest not in (destination / CATALOG).read_text(encoding='utf-8'):
            raise ValueError('catalog mismatch')
        prepare_manifests(destination, live)
        return digest
    except (BadZipFile, KeyError, TypeError, AttributeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid pack archive') from exc


class PackApplications:
    def __init__(self, api):
        self.api = api
        self.root = api.PROJECT_DIR / 'cosmetics-applications'
        self.guard = threading.Lock()
        self.thread = None

    def _read(self, name, default=None):
        path = self.root / name
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

    def status(self):
        job = self._read('latest.json', {'status': 'idle'})
        active = self._read('active.json')
        if active:
            path = self.api.DATA_DIR / CATALOG
            loaded = path.exists() and active['catalog_digest'] in path.read_text(encoding='utf-8')
            job = dict(job, active_digest=active['catalog_digest'], installed=loaded)
        return job

    def managed(self):
        return (self.root / 'active.json').exists()

    def submit(self, operation_id, data):
        if str(uuid.UUID(operation_id)) != operation_id:
            raise ValueError('invalid operation ID')
        with self.guard:
            previous = self._read('latest.json', {})
            if previous.get('operation_id') == operation_id:
                return self.status()
            if previous.get('status') not in (*TERMINAL, None) or (self.thread and self.thread.is_alive()):
                raise RuntimeError('application already active')
            directory = safe_path(self.root, operation_id)
            if directory.exists():
                raise ValueError('operation ID already used')
            directory.mkdir(parents=True, mode=0o700)
            stage = directory / 'stage'
            stage.mkdir()
            try:
                digest = unpack(data, stage, self.api.DATA_DIR)
                for relative in self._paths(directory):
                    check_tree(safe_path(self.api.DATA_DIR, relative))
                safe_path(self.root, 'active.json')
            except Exception:
                shutil.rmtree(directory)
                raise
            job = {'operation_id': operation_id, 'catalog_digest': digest, 'status': 'queued', 'started_at': now(), 'message': '反映の準備中です。'}
            self._save(job)
            self.thread = threading.Thread(target=self._run, args=(job,), daemon=True)
            self.thread.start()
            return job

    def _save(self, job, **changes):
        job.update(changes)
        atomic_json(self.root / job['operation_id'] / 'job.json', job)
        atomic_json(self.root / 'latest.json', job)

    def recover(self):
        job = self._read('latest.json', {})
        if job and job.get('status') not in TERMINAL:
            self.thread = threading.Thread(target=self._run, args=(job, True), daemon=True)
            self.thread.start()

    def _stop(self):
        result = self.api.run_fixed(['docker', 'compose', 'stop', '-t', '60', self.api.COMPOSE_SERVICE], timeout=90)
        if result.returncode or not self.api.wait_for_container_stopped(30):
            raise RuntimeError('stop failed')

    def _start(self):
        result = self.api.run_fixed(['docker', 'compose', 'up', '-d', self.api.COMPOSE_SERVICE], timeout=120)
        if result.returncode:
            raise RuntimeError('start failed')
        result = self.api.wait_for_ready(self.api.RESTART_WAIT_SECONDS, require_healthy=True)
        state = result.get('container', {})
        if state.get('state') != 'running' or state.get('health') != 'healthy' or not result.get('bridge', {}).get('responding'):
            raise RuntimeError('health failed')

    def _reference_paths(self):
        if not self.api.WORLD_NAME or PurePosixPath(self.api.WORLD_NAME).name != self.api.WORLD_NAME:
            raise ValueError('invalid configured world')
        return (f'worlds/{self.api.WORLD_NAME}/world_behavior_packs.json',
                f'worlds/{self.api.WORLD_NAME}/world_resource_packs.json', PERMISSIONS)

    def _paths(self, directory):
        _, packs, retired = pack_layout(directory / 'stage')
        return (*packs, *(pack_path(entry['path']) for entry in retired), *self._reference_paths())

    def _backup(self, directory):
        backup = directory / 'original'
        backup.mkdir()
        missing = []
        paths = self._paths(directory)
        for relative in paths:
            source = safe_path(self.api.DATA_DIR, relative)
            if not source.exists():
                missing.append(relative)
                continue
            check_tree(source)
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        active = safe_path(self.root, 'active.json')
        if active.exists():
            shutil.copy2(active, backup / 'active.json')
        atomic_json(backup / 'state.json', {'paths': paths, 'active_exists': active.exists()})
        # This marker is last: an interrupted backup must never be restored.
        atomic_json(backup / 'missing.json', missing)

    def _restore(self, directory):
        backup = directory / 'original'
        missing = json.loads((backup / 'missing.json').read_text(encoding='utf-8'))
        state = read_json(backup / 'state.json') if (backup / 'state.json').exists() else None
        # Recovery uses the committed backup inventory, even if staging is lost.
        paths = state['paths'] if state else self._paths(directory)
        fixed = self._reference_paths()
        if not isinstance(paths, (list, tuple)) or len(set(paths)) != len(paths) or not set(missing) <= set(paths):
            raise ValueError('invalid backup inventory')
        for relative in paths:
            if relative not in fixed and pack_path(relative) != relative:
                raise ValueError('invalid backup path')
            safe_path(self.api.DATA_DIR, relative)
            source = safe_path(backup, relative)
            check_tree(source)
            if relative not in missing and not source.exists():
                raise ValueError('incomplete backup')
        for relative in paths:
            target = safe_path(self.api.DATA_DIR, relative)
            check_tree(target)
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            if relative not in missing:
                target.parent.mkdir(parents=True, exist_ok=True)
                source = backup / relative
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
        if state is not None:
            active = safe_path(self.root, 'active.json')
            if state['active_exists']:
                shutil.copy2(backup / 'active.json', active)
            elif active.exists():
                active.unlink()

    def _install(self, directory):
        stage = directory / 'stage'
        _, packs, retired = pack_layout(stage)
        for pack in packs:
            target = safe_path(self.api.DATA_DIR, pack)
            check_tree(target)
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(stage / pack, target)
        # Reference changes (especially retirement) follow full byte verification.
        self._verify(stage, packs)
        for kind in ('behavior', 'resource'):
            reference_path = safe_path(self.api.DATA_DIR, f'worlds/{self.api.WORLD_NAME}/world_{kind}_packs.json')
            refs = read_json(reference_path) if reference_path.exists() else []
            manifests = [read_json(stage / pack / 'manifest.json') for pack in packs if pack.startswith(kind + '_packs/')]
            owned = {manifest['header']['uuid'] for manifest in manifests}
            if kind == 'resource':
                owned.update(entry['uuid'] for entry in retired)
            refs = [ref for ref in refs if ref['pack_id'] not in owned]
            refs.extend({'pack_id': manifest['header']['uuid'], 'version': manifest['header']['version']} for manifest in manifests)
            atomic_json(reference_path, refs)
        for entry in retired:
            target = safe_path(self.api.DATA_DIR, pack_path(entry['path']))
            check_tree(target)
            if target.exists():
                shutil.rmtree(target)
        permission_path = safe_path(self.api.DATA_DIR, PERMISSIONS)
        permission = json.loads(permission_path.read_text(encoding='utf-8')) if permission_path.exists() else {'allowed_modules': ['@minecraft/server', '@minecraft/server-net', '@minecraft/server-admin']}
        if '@minecraft/server-ui' not in permission.setdefault('allowed_modules', []):
            permission['allowed_modules'].append('@minecraft/server-ui')
        atomic_json(permission_path, permission)

    def _verify(self, stage, packs):
        for pack in packs:
            target = safe_path(self.api.DATA_DIR, pack)
            check_tree(target)
            expected = {p.relative_to(stage / pack) for p in (stage / pack).rglob('*') if p.is_file()}
            actual = {p.relative_to(target) for p in target.rglob('*') if p.is_file()}
            if expected != actual:
                raise RuntimeError('file verification failed')
            for path in (stage / pack).rglob('*'):
                if path.is_file() and path.read_bytes() != (self.api.DATA_DIR / path.relative_to(stage)).read_bytes():
                    raise RuntimeError('file verification failed')

    def _run(self, job, recovering=False):
        directory = self.root / job['operation_id']
        with deployment_lock():
            try:
                if recovering:
                    raise RuntimeError('interrupted application')
                state = self.api.status_payload()['container']
                if state.get('state') != 'running' or state.get('health') != 'healthy':
                    raise ValueError('server not healthy before apply')
                self._save(job, status='stopping', message='保存してサーバーを停止しています。')
                self._stop()
                self._backup(directory)
                self._save(job, status='installing', message='素材を反映しています。')
                self._install(directory)
                self._save(job, status='starting', message='サーバーを起動して確認しています。')
                self._start()
                _, packs, _ = pack_layout(directory / 'stage')
                installed = []
                for pack in packs:
                    manifest = read_json(self.api.DATA_DIR / pack / 'manifest.json')
                    installed.append({'path': pack, 'uuid': manifest['header']['uuid'],
                                      'version': manifest['header']['version'], 'content_hash': content_hash(self.api.DATA_DIR / pack)})
                atomic_json(self.root / 'active.json', {'catalog_digest': job['catalog_digest'], 'operation_id': job['operation_id'], 'packs': installed})
                self._save(job, status='succeeded', completed_at=now(), message='反映が完了しました。Minecraftに入り直してください。')
            except Exception:
                try:
                    if job['status'] != 'queued':
                        self._stop()
                        if (directory / 'original/missing.json').exists():
                            self._restore(directory)
                        self._start()
                    self._save(job, status='failed', completed_at=now(), message='反映できなかったため元の状態を維持・復元しました。もう一度反映してください。')
                except Exception:
                    self._save(job, status='recovery_failed', completed_at=now(), message='サーバーの復旧確認に失敗しました。バックアップは保持しています。')
