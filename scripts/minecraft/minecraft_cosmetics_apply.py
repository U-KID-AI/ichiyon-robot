"""Durable, fixed-target pack application for the authenticated control API.

Only the admin application's compiler sends archives here. No client selects a
host, command, destination, world or service. Files are staged before shutdown.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
import json
import os
import re
import shutil
import stat
import threading
import uuid
from zipfile import ZipFile, BadZipFile

MAX_ARCHIVE = 32 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
PACKS = ('behavior_packs/import_structures', 'behavior_packs/ichiyon_avatar_bp', 'resource_packs/ichiyon_avatar_rp')
PERMISSIONS = 'config/2fbc1c02-0c4d-4e98-a851-c1e41337c7a8/permissions.json'
TERMINAL = ('succeeded', 'failed', 'recovery_failed')


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
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


def unpack(data, destination, live):
    """No extractall: reject links, duplicate paths, zip bombs and foreign packs."""
    if len(data) > MAX_ARCHIVE:
        raise ValueError('archive too large')
    seen, total = set(), 0
    try:
        with ZipFile(BytesIO(data)) as archive:
            if len(archive.infolist()) > 8192:
                raise ValueError('too many files')
            for info in archive.infolist():
                name = info.filename
                path = PurePosixPath(name)
                allowed = name in ('cosmetics-build.json', 'cosmetics/catalog.lock.json') or any(name.startswith(pack + '/') for pack in PACKS)
                mode = info.external_attr >> 16
                if (not allowed or name in seen or path.is_absolute() or '..' in path.parts
                        or '\\' in name or ':' in name or str(path) != name or info.is_dir()
                        or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))):
                    raise ValueError('invalid archive path')
                total += info.file_size
                if total > MAX_EXPANDED or info.file_size > 8 * 1024 * 1024:
                    raise ValueError('expanded archive too large')
                seen.add(name)
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        proof = json.loads((destination / 'cosmetics-build.json').read_text(encoding='utf-8'))
        digest = proof['catalog_digest']
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid catalog')
        registry = json.loads((destination / 'cosmetics/catalog.lock.json').read_text(encoding='utf-8'))
        if registry['digest'] != digest or digest not in (destination / PACKS[0] / 'scripts/cosmetics_catalog.js').read_text(encoding='utf-8'):
            raise ValueError('catalog mismatch')
        manifests = {}
        for pack in PACKS:
            manifest = json.loads((destination / pack / 'manifest.json').read_text(encoding='utf-8'))
            previous = json.loads((live / pack / 'manifest.json').read_text(encoding='utf-8'))
            if manifest['header']['uuid'] != previous['header']['uuid']:
                raise ValueError('pack identity mismatch')
            version = manifest['header']['version']
            if len(version) != 3 or any(type(v) is not int or not 0 <= v <= 2147483647 for v in version):
                raise ValueError('invalid version')
            # Use a strictly increasing runtime version, including after DB restore.
            current = previous['header']['version']
            version = max(version, [current[0], current[1], current[2] + 1])
            manifest['header']['version'] = version
            for module in manifest['modules']:
                module['version'] = version
            manifests[pack] = manifest
        versions = {m['header']['uuid']: m['header']['version'] for m in manifests.values()}
        for pack, manifest in manifests.items():
            for dependency in manifest.get('dependencies', []):
                if dependency.get('uuid') in versions:
                    dependency['version'] = versions[dependency['uuid']]
            atomic_json(destination / pack / 'manifest.json', manifest)
        return digest
    except (BadZipFile, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
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
            path = self.api.DATA_DIR / PACKS[0] / 'scripts/cosmetics_catalog.js'
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
            directory = self.root / operation_id
            if directory.exists():
                raise ValueError('operation ID already used')
            directory.mkdir(parents=True, mode=0o700)
            stage = directory / 'stage'
            stage.mkdir()
            try:
                digest = unpack(data, stage, self.api.DATA_DIR)
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

    def _paths(self):
        return (*PACKS, f'worlds/{self.api.WORLD_NAME}/world_behavior_packs.json',
                f'worlds/{self.api.WORLD_NAME}/world_resource_packs.json', PERMISSIONS)

    def _backup(self, directory):
        backup = directory / 'original'
        backup.mkdir()
        missing = []
        for relative in self._paths():
            source = self.api.DATA_DIR / relative
            if not source.exists():
                missing.append(relative)
                continue
            if source.is_symlink() or any(p.is_symlink() for p in source.rglob('*')):
                raise ValueError('linked pack destination')
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        atomic_json(backup / 'missing.json', missing)

    def _restore(self, directory):
        backup = directory / 'original'
        missing = json.loads((backup / 'missing.json').read_text(encoding='utf-8'))
        for relative in self._paths():
            target = self.api.DATA_DIR / relative
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

    def _install(self, directory):
        stage = directory / 'stage'
        for pack in PACKS:
            target = self.api.DATA_DIR / pack
            shutil.rmtree(target)
            shutil.copytree(stage / pack, target)
            manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
            kind = 'world_resource_packs.json' if pack.startswith('resource') else 'world_behavior_packs.json'
            self.api.update_world_pack_reference(self.api.DATA_DIR / 'worlds' / self.api.WORLD_NAME / kind,
                                                 manifest['header']['uuid'], manifest['header']['version'])
        permission_path = self.api.DATA_DIR / PERMISSIONS
        permission = json.loads(permission_path.read_text(encoding='utf-8')) if permission_path.exists() else {'allowed_modules': ['@minecraft/server', '@minecraft/server-net', '@minecraft/server-admin']}
        if '@minecraft/server-ui' not in permission.setdefault('allowed_modules', []):
            permission['allowed_modules'].append('@minecraft/server-ui')
        atomic_json(permission_path, permission)
        for pack in PACKS:
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
                atomic_json(self.root / 'active.json', {'catalog_digest': job['catalog_digest'], 'operation_id': job['operation_id']})
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
