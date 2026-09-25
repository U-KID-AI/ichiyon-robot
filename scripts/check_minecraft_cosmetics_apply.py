"""Offline filesystem transactions: healthy apply, rollback, restart recovery and hostile ZIPs."""
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile
import json
import asyncio
from copy import deepcopy
import shutil
import sys
import tempfile
import tarfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts/minecraft')]
import minecraft_cosmetics_apply as deploy
from bot.services.minecraft_cosmetics_pack import builtin_assets, pack_zip


class NoThread:
    def __init__(self, **kwargs): pass
    def start(self): pass
    def is_alive(self): return False


class API:
    WORLD_NAME = 'test-world'
    COMPOSE_SERVICE = 'test-service'
    RESTART_WAIT_SECONDS = 1
    def __init__(self, root):
        self.PROJECT_DIR = root
        self.DATA_DIR = root / 'data'
        self.commands = []
        self.health_failures = 0
    def run_fixed(self, args, timeout):
        self.commands.append(args)
        return SimpleNamespace(returncode=0)
    def wait_for_container_stopped(self, timeout): return True
    def status_payload(self): return {'container': {'state':'running', 'health':'healthy'}, 'bridge': {'responding':True}}
    def wait_for_ready(self, timeout, *, require_healthy=False):
        if self.health_failures:
            self.health_failures -= 1
            return {'container': {'state':'running', 'health':'unhealthy'}}
        return self.status_payload()
    def update_world_pack_reference(self, path, pack_id, version):
        refs = json.loads(path.read_text(encoding='utf-8'))
        refs = [r for r in refs if r['pack_id'] != pack_id]
        refs.append({'pack_id':pack_id, 'version':version})
        deploy.atomic_json(path, refs)


class ApplyChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = pack_zip(ROOT / 'minecraft', builtin_assets(ROOT / 'minecraft'), 1)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.api = API(Path(temporary.name))
        for pack in deploy.PACKS:
            shutil.copytree(ROOT / 'minecraft' / pack, self.api.DATA_DIR / pack)
        for kind in ('behavior', 'resource'):
            deploy.atomic_json(self.api.DATA_DIR / 'worlds/test-world' / f'world_{kind}_packs.json', [{'pack_id':'keep-other-pack','version':[2,0,0]}])
        deploy.atomic_json(self.api.DATA_DIR / deploy.PERMISSIONS, {'allowed_modules':['@minecraft/server'], 'module_permissions': {'preserve':True}})
        self.manager = deploy.PackApplications(self.api)
        self.identifier = str(uuid.uuid4())
        for p in (patch.object(deploy, 'deployment_lock', nullcontext), patch.object(deploy.threading, 'Thread', NoThread)):
            p.start(); self.addCleanup(p.stop)

    def submit(self): return self.manager.submit(self.identifier, self.archive)

    def test_apply_preserves_world_other_packs_permissions_and_records_success(self):
        job = self.submit()
        self.manager._run(job)
        status = self.manager.status()
        self.assertEqual(status['status'], 'succeeded')
        self.assertTrue(status['installed']); self.assertTrue(self.manager.managed())
        self.assertEqual(len(self.api.commands), 2)
        refs = json.loads((self.api.DATA_DIR / 'worlds/test-world/world_behavior_packs.json').read_text(encoding='utf-8'))
        self.assertEqual(refs[0]['pack_id'], 'keep-other-pack'); self.assertEqual(len(refs), 3)
        resources = json.loads((self.api.DATA_DIR / 'worlds/test-world/world_resource_packs.json').read_text(encoding='utf-8'))
        self.assertIn({'pack_id': 'c2de9f3f-7956-4c7a-b6a1-63b264a9a059', 'version': [1, 0, 0]}, resources)
        active = json.loads((self.manager.root / 'active.json').read_text(encoding='utf-8'))
        self.assertIn('resource_packs/ichiyon_video_akki_rp', [p['path'] for p in active['packs']])
        permission = json.loads((self.api.DATA_DIR / deploy.PERMISSIONS).read_text(encoding='utf-8'))
        self.assertTrue(permission['module_permissions']['preserve']); self.assertIn('@minecraft/server-ui', permission['allowed_modules'])

    def test_duplicate_submit_does_not_start_second_restart_and_other_id_is_busy(self):
        self.submit()
        self.assertEqual(self.manager.submit(self.identifier, b'ignored retry')['operation_id'], self.identifier)
        with self.assertRaises(RuntimeError): self.manager.submit(str(uuid.uuid4()), self.archive)
        self.assertFalse(self.api.commands)

    def test_health_failure_restores_every_pack_and_reference(self):
        original = {str(p.relative_to(self.api.DATA_DIR)):p.read_bytes() for p in self.api.DATA_DIR.rglob('*') if p.is_file()}
        job = self.submit(); self.api.health_failures = 1
        self.manager._run(job)
        self.assertEqual(self.manager.status()['status'], 'failed')
        restored = {str(p.relative_to(self.api.DATA_DIR)):p.read_bytes() for p in self.api.DATA_DIR.rglob('*') if p.is_file()}
        self.assertEqual(original, restored)
        self.assertFalse(self.manager.managed())

    def test_interrupted_install_recovers_from_durable_backup(self):
        job = self.submit()
        directory = self.manager.root / self.identifier
        old = (self.api.DATA_DIR / deploy.PACKS[1] / 'manifest.json').read_bytes()
        self.manager._backup(directory)
        self.manager._save(job, status='installing')
        self.manager._install(directory)
        recovered = deploy.PackApplications(self.api)
        recovered._run(recovered.status(), recovering=True)
        self.assertEqual(recovered.status()['status'], 'failed')
        self.assertEqual((self.api.DATA_DIR / deploy.PACKS[1] / 'manifest.json').read_bytes(), old)

    def test_bad_archive_never_stops_server(self):
        for name in ('../outside', 'behavior_packs/ichiyon_avatar_bp/../../outside', 'behavior_packs/ichiyon_avatar_bp/a/../x', 'worlds/test-world/level.dat'):
            stream = BytesIO()
            with ZipFile(stream, 'w') as archive: archive.writestr(name, b'x')
            with self.assertRaises(ValueError): self.manager.submit(str(uuid.uuid4()), stream.getvalue())
        self.assertFalse(self.api.commands)

    def test_pack_uuid_substitution_is_rejected(self):
        stream = BytesIO()
        with ZipFile(BytesIO(self.archive)) as old, ZipFile(stream, 'w') as new:
            for name in old.namelist():
                data = old.read(name)
                if name == deploy.PACKS[1] + '/manifest.json':
                    value = json.loads(data); value['header']['uuid'] = str(uuid.uuid4()); data = json.dumps(value).encode()
                new.writestr(name, data)
        with self.assertRaises(ValueError): self.manager.submit(self.identifier, stream.getvalue())
        self.assertFalse(self.api.commands)

    def test_identical_export_keeps_runtime_version(self):
        job = self.submit(); self.manager._run(job)
        first = json.loads((self.api.DATA_DIR / deploy.PACKS[1] / 'manifest.json').read_text(encoding='utf-8'))['header']['version']
        job = self.manager.submit(str(uuid.uuid4()), self.archive); self.manager._run(job)
        second = json.loads((self.api.DATA_DIR / deploy.PACKS[1] / 'manifest.json').read_text(encoding='utf-8'))['header']['version']
        self.assertEqual(second, first)


SPLIT_RPS = tuple('resource_packs/ichiyon_' + name + '_rp' for name in
                  ('core', 'mannequin_skins', 'accessories', 'posters', 'video', 'records'))


class SplitApplyChecks(unittest.TestCase):
    """Small independent fixtures keep transaction checks separate from the compiler."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.api = API(Path(temporary.name))
        self.manager = deploy.PackApplications(self.api)
        self.files = {}
        self.digest = 'a' * 64
        self.packs = [*deploy.BEHAVIOR_PACKS, *SPLIT_RPS]
        for pack in self.packs:
            manifest = self.manifest(pack)
            self.files[pack + '/manifest.json'] = manifest
            self.files[pack + '/content.txt'] = pack.encode()
        self.files[deploy.CATALOG] = ('export const digest = "' + self.digest + '";').encode()
        self.files['cosmetics/catalog.lock.json'] = {'digest': self.digest}
        self.files['cosmetics-build.json'] = {
            'revision': 1, 'catalog_digest': self.digest, 'packs': self.packs,
            'retired_packs': [{'path': deploy.PACKS[2], 'uuid': deploy.RETIRED_PACKS[deploy.PACKS[2]]}]}
        dependent = self.files[deploy.BEHAVIOR_PACKS[1] + '/manifest.json']
        dependent['dependencies'] = [{'uuid': self.pack_id(SPLIT_RPS[1]), 'version': [1, 0, 1]},
                                     {'module_name': '@minecraft/server', 'version': '2.0.0'}]
        for pack in deploy.PACKS:
            deploy.atomic_json(self.api.DATA_DIR / pack / 'manifest.json', self.manifest(pack))
        for kind in ('behavior', 'resource'):
            refs = [{'pack_id': 'keep-other-pack', 'version': [2, 0, 0], 'extra': 'preserve'}]
            if kind == 'resource':
                refs.append({'pack_id': self.pack_id(deploy.PACKS[2]), 'version': [1, 0, 1]})
            deploy.atomic_json(self.api.DATA_DIR / 'worlds/test-world' / f'world_{kind}_packs.json', refs)
        deploy.atomic_json(self.api.DATA_DIR / deploy.PERMISSIONS, {'allowed_modules': ['@minecraft/server']})
        deploy.atomic_json(self.manager.root / 'active.json', {'catalog_digest': 'b' * 64, 'operation_id': str(uuid.uuid4())})
        self.old_active = (self.manager.root / 'active.json').read_bytes()
        self.original = self.snapshot()
        for p in (patch.object(deploy, 'deployment_lock', nullcontext), patch.object(deploy.threading, 'Thread', NoThread)):
            p.start()
            self.addCleanup(p.stop)

    def pack_id(self, pack):
        return deploy.RETIRED_PACKS.get(pack, str(uuid.uuid5(uuid.NAMESPACE_URL, pack)))

    def manifest(self, pack):
        return {'format_version': 2,
                'header': {'name': pack, 'uuid': self.pack_id(pack), 'version': [1, 0, 1], 'min_engine_version': [1, 26, 0]},
                'modules': [{'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, pack + '/module')), 'version': [1, 0, 1],
                             'type': 'resources' if pack.startswith('resource') else 'data'}]}

    def archive(self, files=None):
        output = BytesIO()
        with ZipFile(output, 'w') as archive:
            for name, data in (self.files if files is None else files).items():
                archive.writestr(name, json.dumps(data).encode() if isinstance(data, dict) else data)
        return output.getvalue()

    def submit(self, files=None):
        return self.manager.submit(str(uuid.uuid4()), self.archive(files))

    def apply(self, files=None):
        job = self.submit(files)
        self.manager._run(job)
        self.assertEqual(self.manager.status()['status'], 'succeeded')
        return job

    def versions(self):
        return {pack: deploy.read_json(self.api.DATA_DIR / pack / 'manifest.json')['header']['version'] for pack in self.packs}

    def snapshot(self):
        return {p.relative_to(self.api.DATA_DIR).as_posix(): p.read_bytes() for p in self.api.DATA_DIR.rglob('*') if p.is_file()}

    def refs(self):
        return deploy.read_json(self.api.DATA_DIR / 'worlds/test-world/world_resource_packs.json')

    def assert_restored(self):
        self.assertEqual(self.manager.status()['status'], 'failed')
        self.assertEqual(self.snapshot(), self.original)
        self.assertEqual((self.manager.root / 'active.json').read_bytes(), self.old_active)
        self.assertTrue(all(not (self.api.DATA_DIR / pack).exists() for pack in SPLIT_RPS))

    def test_migration_preserves_foreign_refs_and_orders_six_verified_resources(self):
        self.assertEqual(self.manager.status()['active_digest'], 'b' * 64)
        job = self.apply()
        self.assertEqual([ref['pack_id'] for ref in self.refs()], ['keep-other-pack', *(self.pack_id(p) for p in SPLIT_RPS)])
        self.assertEqual(self.refs()[0]['extra'], 'preserve')
        self.assertFalse((self.api.DATA_DIR / deploy.PACKS[2]).exists())
        backup = self.manager.root / job['operation_id'] / 'original'
        self.assertEqual((backup / deploy.PACKS[2] / 'manifest.json').read_bytes(), self.original[deploy.PACKS[2] + '/manifest.json'])
        self.assertEqual((backup / 'active.json').read_bytes(), self.old_active)
        self.assertEqual((backup / 'worlds/test-world/world_resource_packs.json').read_bytes(),
                         self.original['worlds/test-world/world_resource_packs.json'])
        self.assertEqual(len(deploy.read_json(self.manager.root / 'active.json')['packs']), 8)
        for pack in deploy.BEHAVIOR_PACKS:
            self.assertEqual(deploy.read_json(self.api.DATA_DIR / pack / 'manifest.json')['header']['uuid'], self.pack_id(pack))

    def test_export_revision_and_pack_dependency_versions_do_not_bump_unchanged_packs(self):
        self.apply()
        versions = self.versions()
        original = self.snapshot()
        self.files['cosmetics-build.json']['revision'] = 999
        for pack in self.packs:
            manifest = self.files[pack + '/manifest.json']
            manifest['header']['version'] = [9, 9, 9]
            for module in manifest['modules']:
                module['version'] = [9, 9, 9]
            for dep in manifest.get('dependencies', []):
                if 'uuid' in dep:
                    dep['version'] = [9, 9, 9]
        self.apply()
        self.assertEqual(self.versions(), versions)
        self.assertEqual(self.snapshot(), original)

    def test_cosmetic_content_bumps_only_one_rp_and_keeps_video_records_bytes(self):
        self.apply()
        first = self.versions()
        original = self.snapshot()
        self.files[SPLIT_RPS[1] + '/content.txt'] = b'new skin texture'
        self.apply()
        second = self.versions()
        self.assertEqual([p for p in self.packs if first[p] != second[p]], [SPLIT_RPS[1]])
        self.assertGreater(second[SPLIT_RPS[1]], first[SPLIT_RPS[1]])
        dependent = deploy.read_json(self.api.DATA_DIR / deploy.BEHAVIOR_PACKS[1] / 'manifest.json')
        self.assertEqual(dependent['dependencies'][0]['version'], second[SPLIT_RPS[1]])
        self.assertEqual(dependent['dependencies'][1]['version'], '2.0.0')
        for name, data in self.snapshot().items():
            if name.startswith((SPLIT_RPS[4] + '/', SPLIT_RPS[5] + '/')):
                self.assertEqual(data, original[name])
        self.apply()
        self.assertEqual(self.versions(), second)

    def test_module_dependency_and_engine_versions_are_real_content(self):
        self.apply()
        first = self.versions()
        self.files[deploy.BEHAVIOR_PACKS[1] + '/manifest.json']['dependencies'][1]['version'] = '2.1.0'
        self.files[SPLIT_RPS[0] + '/manifest.json']['header']['min_engine_version'] = [1, 26, 1]
        self.apply()
        second = self.versions()
        self.assertEqual([p for p in self.packs if first[p] != second[p]], [deploy.BEHAVIOR_PACKS[1], SPLIT_RPS[0]])

    def test_catalog_update_keeps_video_and_records_versions(self):
        self.apply()
        first = self.versions()
        self.files['cosmetics-build.json']['catalog_digest'] = 'c' * 64
        self.files['cosmetics/catalog.lock.json']['digest'] = 'c' * 64
        self.files[deploy.CATALOG] = ('export const digest = "' + 'c' * 64 + '";').encode()
        self.files[SPLIT_RPS[3] + '/content.txt'] = b'new poster'
        self.apply()
        second = self.versions()
        self.assertEqual([p for p in self.packs if first[p] != second[p]], [deploy.BEHAVIOR_PACKS[0], SPLIT_RPS[3]])
        self.assertEqual(self.manager.status()['active_digest'], 'c' * 64)

    def test_failed_health_restores_legacy_refs_active_and_absent_new_directories(self):
        job = self.submit()
        self.api.health_failures = 1
        self.manager._run(job)
        self.assert_restored()

    def test_retired_directory_is_removed_only_after_verification_and_both_reference_writes(self):
        remove = shutil.rmtree
        verify = self.manager._verify
        verified = []
        def record_verification(stage, packs):
            self.assertTrue((self.api.DATA_DIR / deploy.PACKS[2]).exists())
            verify(stage, packs)
            verified.append(True)
        def check_retirement(target, *args, **kwargs):
            if target == self.api.DATA_DIR / deploy.PACKS[2]:
                self.assertTrue(verified)
                for kind in ('behavior', 'resource'):
                    refs = deploy.read_json(self.api.DATA_DIR / 'worlds/test-world' / f'world_{kind}_packs.json')
                    wanted = [self.pack_id(p) for p in self.packs if p.startswith(kind)]
                    self.assertEqual([ref['pack_id'] for ref in refs], ['keep-other-pack', *wanted])
            return remove(target, *args, **kwargs)
        with patch.object(self.manager, '_verify', side_effect=record_verification), patch.object(shutil, 'rmtree', side_effect=check_retirement):
            self.apply()
        self.assertFalse((self.api.DATA_DIR / deploy.PACKS[2]).exists())

    def test_retirement_failure_rolls_back_references_and_new_directories(self):
        remove = shutil.rmtree
        failed = []
        def fail_retirement(target, *args, **kwargs):
            if target == self.api.DATA_DIR / deploy.PACKS[2] and not failed:
                failed.append(True)
                raise OSError('simulated retirement failure')
            return remove(target, *args, **kwargs)
        job = self.submit()
        with patch.object(shutil, 'rmtree', side_effect=fail_retirement):
            self.manager._run(job)
        self.assertTrue(failed)
        self.assert_restored()

    def test_verification_failure_leaves_legacy_reference_before_rollback(self):
        job = self.submit()
        original_refs = self.refs()
        verify = self.manager._verify
        def corrupt(stage, packs):
            self.assertEqual(self.refs(), original_refs)
            (self.api.DATA_DIR / SPLIT_RPS[-1] / 'content.txt').write_bytes(b'corrupt')
            verify(stage, packs)
        with patch.object(self.manager, '_verify', side_effect=corrupt):
            self.manager._run(job)
        self.assert_restored()

    def test_restart_recovery_restores_original_active_even_after_active_was_written(self):
        job = self.submit()
        directory = self.manager.root / job['operation_id']
        self.manager._backup(directory)
        self.manager._save(job, status='starting')
        self.manager._install(directory)
        deploy.atomic_json(self.manager.root / 'active.json', {'catalog_digest': self.digest, 'operation_id': job['operation_id']})
        shutil.rmtree(directory / 'stage')
        recovered = deploy.PackApplications(self.api)
        recovered._run(recovered.status(), recovering=True)
        self.assert_restored()

    def test_failure_after_active_write_restores_original_active(self):
        save = self.manager._save
        def fail_success(job, **changes):
            if changes.get('status') == 'succeeded':
                raise OSError('simulated final journal failure')
            save(job, **changes)
        job = self.submit()
        with patch.object(self.manager, '_save', side_effect=fail_success):
            self.manager._run(job)
        self.assert_restored()

    def test_recovery_removes_active_when_originally_absent(self):
        (self.manager.root / 'active.json').unlink()
        job = self.submit()
        directory = self.manager.root / job['operation_id']
        self.manager._backup(directory)
        self.manager._install(directory)
        self.manager._save(job, status='starting')
        deploy.atomic_json(self.manager.root / 'active.json', {'catalog_digest': self.digest, 'operation_id': job['operation_id']})
        self.manager._run(job, recovering=True)
        self.assertFalse(self.manager.managed())
        self.assertEqual(self.snapshot(), self.original)

    def test_old_three_pack_archive_without_metadata_remains_supported(self):
        files = {name: deepcopy(data) for name, data in self.files.items()
                 if not name.startswith('resource_packs/')}
        files['cosmetics-build.json'].pop('packs')
        files['cosmetics-build.json'].pop('retired_packs')
        files[deploy.PACKS[2] + '/manifest.json'] = self.manifest(deploy.PACKS[2])
        files[deploy.PACKS[2] + '/content.txt'] = b'legacy'
        files[deploy.BEHAVIOR_PACKS[1] + '/manifest.json']['dependencies'][0]['uuid'] = self.pack_id(deploy.PACKS[2])
        self.apply(files)
        self.assertEqual([ref['pack_id'] for ref in self.refs()], ['keep-other-pack', self.pack_id(deploy.PACKS[2])])

    def test_additional_rp_is_discovered_without_fixed_count(self):
        pack = 'resource_packs/ichiyon_future_rp'
        self.packs.append(pack)
        self.files[pack + '/manifest.json'] = self.manifest(pack)
        self.files[pack + '/content.txt'] = b'future pack'
        self.apply()
        self.assertEqual(self.refs()[-1]['pack_id'], self.pack_id(pack))

    def test_object_pack_metadata_also_supported(self):
        self.files['cosmetics-build.json']['packs'] = [{'path': p, 'uuid': self.pack_id(p)} for p in self.packs]
        self.apply()

    def test_invalid_lists_retirement_and_duplicate_identities_never_stop_server(self):
        variants = []
        for key, value in (
            ('packs', self.packs[:-1]), ('packs', self.packs + [self.packs[0]]),
            ('packs', ['worlds/test-world'] + self.packs[1:]),
            ('retired_packs', [{'path': deploy.PACKS[2], 'uuid': str(uuid.uuid4())}]),
            ('retired_packs', [{'path': 'resource_packs/foreign_pack', 'uuid': self.pack_id(deploy.PACKS[2])}]),
        ):
            files = deepcopy(self.files)
            files['cosmetics-build.json'][key] = value
            variants.append(files)
        files = deepcopy(self.files)
        files[SPLIT_RPS[0] + '/manifest.json']['header']['uuid'] = self.pack_id(SPLIT_RPS[1])
        variants.append(files)
        for identity in (self.pack_id(SPLIT_RPS[0]), self.pack_id(SPLIT_RPS[1]),
                         self.files[deploy.BEHAVIOR_PACKS[0] + '/manifest.json']['modules'][0]['uuid']):
            files = deepcopy(self.files)
            files[SPLIT_RPS[0] + '/manifest.json']['modules'][0]['uuid'] = identity
            variants.append(files)
        files = deepcopy(self.files)
        files[deploy.BEHAVIOR_PACKS[1] + '/manifest.json']['dependencies'][0]['uuid'] = self.pack_id(deploy.PACKS[2])
        variants.append(files)
        for files in variants:
            with self.subTest(proof=files['cosmetics-build.json']), self.assertRaises(ValueError):
                self.submit(files)
        self.assertFalse(self.api.commands)

    def test_hostile_paths_cannot_write_outside_managed_roots(self):
        for name in ('worlds/test-world/level.dat', 'resource_packs/foreign_pack/manifest.json',
                     'behavior_packs/foreign_pack/manifest.json', SPLIT_RPS[0] + '/../outside',
                     SPLIT_RPS[0] + '/x/../../outside', SPLIT_RPS[0] + '/CON', SPLIT_RPS[0] + '/file:stream'):
            files = deepcopy(self.files)
            files[name] = b'invalid'
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.submit(files)
        self.assertFalse(self.api.commands)
        self.assertEqual(self.snapshot(), self.original)

    def test_byte_and_seekable_archives_enforce_all_inclusive_budgets(self):
        data = self.archive()
        with ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
        limits = {'MAX_ARCHIVE': len(data), 'MAX_EXPANDED': sum(i.file_size for i in infos),
                  'MAX_FILE': max(i.file_size for i in infos), 'MAX_FILES': len(infos)}
        for use_file in (False, True):
            for limited in (None, *limits):
                values = dict(limits)
                if limited:
                    values[limited] -= 1
                with self.subTest(file=use_file, limit=limited), tempfile.TemporaryFile() as stream:
                    stream.write(data)
                    source = stream if use_file else data
                    stage = self.api.PROJECT_DIR / str(uuid.uuid4())
                    with patch.multiple(deploy, **values):
                        if limited:
                            with self.assertRaises(ValueError):
                                deploy.unpack(source, stage, self.api.DATA_DIR)
                        else:
                            self.assertEqual(deploy.unpack(source, stage, self.api.DATA_DIR), self.digest)
                    self.assertFalse(stream.closed)
        self.assertFalse(self.api.commands)


class ControlBackupChecks(unittest.TestCase):
    def test_raw_restart_backup_uses_live_manifests_and_preserves_world_and_active(self):
        import minecraft_control_api as api
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            data = project / 'data'
            (project / 'docker-compose.yml').write_text('services: {}', encoding='utf-8')
            world = data / 'worlds/test-world'
            deploy.atomic_json(world / 'world_resource_packs.json', [{'pack_id': 'foreign', 'version': [1, 0, 1]}])
            deploy.atomic_json(world / 'world_behavior_packs.json', [])
            (world / 'level.dat').write_bytes(b'world-data')
            packs = [*deploy.BEHAVIOR_PACKS, *SPLIT_RPS, 'resource_packs/foreign_pack']
            for pack in packs:
                deploy.atomic_json(data / pack / 'manifest.json', {'header': {'uuid': str(uuid.uuid4())}})
                (data / pack / 'asset.bin').write_bytes(pack.encode())
            (data / deploy.PACKS[2]).mkdir()
            source = project / 'source' / SPLIT_RPS[0]
            source.mkdir(parents=True)
            (source / 'authoring-only.txt').write_bytes(b'not deployed')
            active = project / 'cosmetics-applications/active.json'
            deploy.atomic_json(active, {'catalog_digest': 'a' * 64, 'operation_id': str(uuid.uuid4())})
            before = {p.relative_to(data).as_posix(): p.read_bytes() for p in data.rglob('*') if p.is_file()}
            with patch.multiple(api, PROJECT_DIR=project, DATA_DIR=data, BACKUP_DIR=project / 'backups',
                                WORLD_NAME='test-world', PACK_SOURCE_DIR=project / 'source'):
                backup = api.create_backup()
            with tarfile.open(backup) as archive:
                names = archive.getnames()
                for pack in packs:
                    self.assertIn('data/' + pack + '/manifest.json', names)
                    self.assertEqual(archive.extractfile('data/' + pack + '/asset.bin').read(), pack.encode())
                self.assertNotIn('data/' + deploy.PACKS[2], names)
                self.assertFalse(any('authoring-only' in name for name in names))
                self.assertEqual(archive.extractfile('data/worlds/test-world/level.dat').read(), b'world-data')
                self.assertEqual(archive.extractfile('data/worlds/test-world/world_resource_packs.json').read(),
                                 before['worlds/test-world/world_resource_packs.json'])
                self.assertEqual(archive.extractfile('cosmetics-applications/active.json').read(), active.read_bytes())
            self.assertEqual({p.relative_to(data).as_posix(): p.read_bytes() for p in data.rglob('*') if p.is_file()}, before)

    def test_raw_sync_does_not_touch_managed_pack_references(self):
        import minecraft_control_api as api
        with patch.object(api, 'cosmetic_applications') as manager, patch.object(api, 'update_world_pack_reference') as update:
            manager.managed.return_value = True
            self.assertEqual(api.sync_packs(), {'status': 'managed_by_cosmetics', 'changed_packs': []})
            update.assert_not_called()


class ReadinessChecks(unittest.TestCase):
    def setUp(self):
        import minecraft_control_api as api
        self.api = api
        self.elapsed = 0
        def sleep(seconds): self.elapsed += seconds
        for p in (patch.object(api.time, 'monotonic', side_effect=lambda: self.elapsed),
                  patch.object(api.time, 'sleep', side_effect=sleep),
                  patch.object(api, 'run_fixed', return_value=SimpleNamespace(returncode=0))):
            p.start(); self.addCleanup(p.stop)

    def state(self, health='healthy', responding=True):
        return {'container': {'state':'running', 'health':health}, 'bridge': {'responding':responding}}

    def test_apply_and_rollback_start_wait_for_docker_health_after_udp_responds(self):
        # Exercise the real _start + wait_for_ready pair used in both transactions.
        with patch.object(self.api, 'status_payload', side_effect=[self.state('starting'), self.state('starting'), self.state()]) as status:
            deploy.PackApplications(self.api)._start()
        self.assertEqual(status.call_count, 3)
        self.assertEqual(self.elapsed, 4)

    def test_docker_health_alone_is_not_ready_without_bedrock_response(self):
        with patch.object(self.api, 'status_payload', side_effect=[self.state(responding=False), self.state()]) as status:
            deploy.PackApplications(self.api)._start()
        self.assertEqual(status.call_count, 2)

    def test_timeout_still_fails_closed_after_waiting(self):
        for state in (self.state('starting'), self.state('unhealthy'), self.state(None), self.state(responding=False)):
            with self.subTest(state=state), patch.object(self.api, 'RESTART_WAIT_SECONDS', 5), patch.object(self.api, 'status_payload', return_value=state):
                before = self.elapsed
                with self.assertRaisesRegex(RuntimeError, 'health failed'):
                    deploy.PackApplications(self.api)._start()
                self.assertEqual(self.elapsed - before, 5)

    def test_existing_restart_keeps_udp_readiness(self):
        with patch.object(self.api, 'status_payload', return_value=self.state('starting')):
            result = self.api.wait_for_ready(5)
        self.assertEqual(result['container']['health'], 'starting')
        self.assertEqual(self.elapsed, 0)


class ControlHTTPChecks(unittest.TestCase):
    def test_control_auth_body_validation_and_fixed_operation(self):
        from fastapi.testclient import TestClient
        import minecraft_control_api as api
        with patch.object(api, 'CONTROL_SECRET', 'offline-control-test'), patch.object(api, 'cosmetic_applications') as manager:
            client = TestClient(api.app)
            identifier = str(uuid.uuid4())
            self.assertEqual(client.get('/cosmetics').status_code, 401)
            self.assertEqual(client.post('/cosmetics/' + identifier, content=b'x').status_code, 401)
            manager.submit.assert_not_called()
            headers = {'X-Minecraft-Control-Secret':'offline-control-test'}
            manager.submit.side_effect = ValueError('never disclose details')
            response = client.post('/cosmetics/' + identifier, content=b'x', headers=headers)
            self.assertEqual(response.status_code, 400); self.assertNotIn('disclose', response.text)
            manager.submit.side_effect = RuntimeError('busy')
            self.assertEqual(client.post('/cosmetics/' + identifier, content=b'x', headers=headers).status_code, 409)
            received = []
            def submit(operation_id, stream):
                self.assertEqual(operation_id, identifier)
                self.assertEqual(stream.read(), b'pack')
                self.assertFalse(stream.closed)
                received.append(stream)
                return {'status': 'queued'}
            manager.submit.side_effect = submit
            self.assertEqual(client.post('/cosmetics/' + identifier, content=b'pack', headers=headers).status_code, 202)
            self.assertTrue(received[0].closed)
            client.close()

    def test_auth_precedes_body_and_declared_and_chunked_limits_are_strict(self):
        from fastapi import HTTPException
        from starlette.requests import Request
        import minecraft_control_api as api
        identifier = str(uuid.uuid4())
        with patch.object(api, 'CONTROL_SECRET', 'offline-control-test'), patch.object(api, 'MAX_ARCHIVE', 4), \
                patch.object(api, 'cosmetic_applications') as manager:
            async def attempt(chunks, length=None, secret='offline-control-test'):
                messages = iter(chunks)
                async def receive():
                    body, more = next(messages)
                    return {'type': 'http.request', 'body': body, 'more_body': more}
                headers = [] if length is None else [(b'content-length', length.encode())]
                request = Request({'type': 'http', 'headers': headers}, receive)
                return await api.apply_cosmetics(identifier, request, secret)
            for length, secret, status in ((None, None, 401), ('5', 'offline-control-test', 413),
                                            ('-1', 'offline-control-test', 400), ('invalid', 'offline-control-test', 400)):
                with self.subTest(length=length, secret=secret), self.assertRaises(HTTPException) as error:
                    asyncio.run(attempt([], length, secret))
                self.assertEqual(error.exception.status_code, status)
            for length in (None, '2'):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(attempt([(b'123', True), (b'45', False)], length))
                self.assertEqual(error.exception.status_code, 413)
            manager.submit.assert_not_called()
            streams = []
            def submit(operation_id, stream):
                self.assertEqual(stream.read(), b'1234')
                streams.append(stream)
                return {'status': 'queued'}
            manager.submit.side_effect = submit
            self.assertEqual(asyncio.run(attempt([(b'12', True), (b'34', False)], '4')), {'status': 'queued'})
            self.assertTrue(streams[0].closed)

    def test_upload_temporary_file_closes_on_invalid_archive(self):
        from fastapi.testclient import TestClient
        import minecraft_control_api as api
        received = []
        def reject(operation_id, stream):
            received.append(stream)
            raise ValueError('invalid')
        with patch.object(api, 'CONTROL_SECRET', 'offline-control-test'), patch.object(api, 'cosmetic_applications') as manager:
            manager.submit.side_effect = reject
            with TestClient(api.app) as client:
                response = client.post('/cosmetics/' + str(uuid.uuid4()), content=b'invalid',
                                       headers={'X-Minecraft-Control-Secret': 'offline-control-test'})
            self.assertEqual(response.status_code, 400)
            self.assertTrue(received[0].closed)


class AdminControlClientChecks(unittest.IsolatedAsyncioTestCase):
    async def test_managed_archive_cap_is_checked_before_network_and_has_upload_timeout(self):
        from unittest.mock import AsyncMock
        from bot.services import minecraft_control as client
        identifier = str(uuid.uuid4())
        response = SimpleNamespace(status_code=202, raise_for_status=lambda: None, json=lambda: {'status': 'queued'})
        with patch.object(client, 'control_api_configured', return_value=True), patch.object(client, '_headers', return_value={}), \
                patch.object(client, '_base_url', return_value='http://control.invalid'), patch.object(client, 'MAX_ARCHIVE', 4), \
                patch.object(client.httpx, 'AsyncClient') as factory:
            for payload in (None, 'pack', b'12345'):
                with self.assertRaises(client.MinecraftControlError):
                    await client.cosmetics_control(identifier, payload)
            factory.assert_not_called()
            session = factory.return_value.__aenter__.return_value
            session.post = AsyncMock(return_value=response)
            self.assertEqual(await client.cosmetics_control(identifier, b'1234'), {'status': 'queued'})
            self.assertEqual(session.post.call_args.kwargs['content'], b'1234')
            self.assertFalse(factory.call_args.kwargs['trust_env'])
            timeout = factory.call_args.kwargs['timeout']
            self.assertEqual((timeout.connect, timeout.read, timeout.write), (10, 180, 180))
            session.get = AsyncMock(return_value=response)
            await client.cosmetics_control()
            self.assertEqual(factory.call_args.kwargs['timeout'], 60)


if __name__ == '__main__': unittest.main()
