"""Offline checks for pinned private JUNKET preservation in managed releases."""
from contextlib import nullcontext
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts/minecraft'))
import minecraft_cosmetics_apply as deploy
from minecraft_junket_overlay import JUNKET_PACKS, preserve_overlay


class OverlayChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.stage = self.project / 'managed-stage'
        self.live = self.project / 'data'
        self.operation = str(uuid.uuid4())
        self.source = self.project / 'junket-world-applications' / self.operation / 'stage'
        self.digest = 'a' * 64
        self.normal = [*deploy.BEHAVIOR_PACKS, 'resource_packs/ichiyon_test_rp']
        for pack in self.normal:
            self.make_pack(self.stage, pack, 'DB CATALOG CONTENT')
        (self.stage / deploy.CATALOG).parent.mkdir(parents=True, exist_ok=True)
        (self.stage / deploy.CATALOG).write_text(self.digest, encoding='utf-8')
        deploy.atomic_json(self.stage / 'cosmetics-build.json', {'catalog_digest': self.digest, 'packs': self.normal})
        deploy.atomic_json(self.stage / 'cosmetics/catalog.lock.json', {'digest': self.digest})
        for pack in JUNKET_PACKS:
            self.make_pack(self.source, pack, 'PRIVATE ORIGINAL CONTENT')
        self.marker = {'schema': 1, 'operation_id': self.operation,
                       'packs': {pack: deploy.content_hash(self.source / pack) for pack in JUNKET_PACKS}}
        deploy.atomic_json(self.project / 'junket-native-overlay.json', self.marker)
        deploy.atomic_json(self.project / 'junket-world-applications/latest.json', {'operation_id': self.operation, 'status': 'succeeded'})

    def make_pack(self, root, pack, contents):
        manifest = {'format_version': 2, 'header': {'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, pack)), 'version': [1, 0, 0]},
                    'modules': [{'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, pack + '/module')), 'version': [1, 0, 0],
                                 'type': 'data' if pack.startswith('behavior') else 'resources'}]}
        deploy.atomic_json(root / pack / 'manifest.json', manifest)
        (root / pack / 'content.txt').write_text(contents, encoding='utf-8')

    def archive(self):
        stream = BytesIO()
        with ZipFile(stream, 'w') as archive:
            for path in self.stage.rglob('*'):
                if path.is_file(): archive.write(path, path.relative_to(self.stage).as_posix())
        return stream.getvalue()

    def test_preserves_db_packs_and_adds_both_pinned_packs(self):
        before = {pack: deploy.content_hash(self.stage / pack) for pack in self.normal}
        result = preserve_overlay(self.project, self.stage)
        self.assertEqual(result, self.marker)
        for pack, expected in before.items(): self.assertEqual(deploy.content_hash(self.stage / pack), expected)
        for pack, expected in self.marker['packs'].items(): self.assertEqual(deploy.content_hash(self.stage / pack), expected)
        proof, packs, _ = deploy.pack_layout(self.stage)
        self.assertEqual(proof['catalog_digest'], self.digest)
        self.assertEqual(set(packs), set(self.normal + list(JUNKET_PACKS)))

    def test_package_import_uses_helpers_from_the_same_checkout(self):
        from scripts.minecraft import minecraft_cosmetics_apply as package_deploy
        from scripts.minecraft import junket_static_overlay as package_static
        with patch.dict(sys.modules, {'minecraft_cosmetics_apply': None,
                                      'minecraft_junket_overlay': None,
                                      'junket_static_overlay': None}):
            result = package_deploy.pinned_native_overlay(self.project, self.stage)
            self.assertEqual(result, self.marker)
            self.assertEqual(package_static._path(self.stage, self.normal[0] + '/content.txt'),
                             self.stage / self.normal[0] / 'content.txt')

    def test_replaces_generated_junket_without_changing_other_pack_metadata(self):
        proof = deploy.read_json(self.stage / 'cosmetics-build.json')
        proof['packs'] = [{'path': p, 'uuid': deploy.read_json(self.stage / p / 'manifest.json')['header']['uuid'], 'kept': p} for p in self.normal]
        for pack in JUNKET_PACKS:
            self.make_pack(self.stage, pack, 'GENERATED SUBSTITUTE')
            proof['packs'].append({'path': pack, 'uuid': deploy.read_json(self.stage / pack / 'manifest.json')['header']['uuid']})
        deploy.atomic_json(self.stage / 'cosmetics-build.json', proof)
        preserve_overlay(self.project, self.stage)
        self.assertEqual(deploy.read_json(self.stage / 'cosmetics-build.json')['packs'][:3], proof['packs'][:3])
        self.assertEqual((self.stage / JUNKET_PACKS[0] / 'content.txt').read_text(), 'PRIVATE ORIGINAL CONTENT')

    def test_bad_pin_hash_rejected_before_any_stage_mutation(self):
        before = {str(p.relative_to(self.stage)): p.read_bytes() for p in self.stage.rglob('*') if p.is_file()}
        (self.source / JUNKET_PACKS[1] / 'content.txt').write_text('CORRUPTED')
        with self.assertRaisesRegex(ValueError, 'fingerprint mismatch'): preserve_overlay(self.project, self.stage)
        self.assertEqual(before, {str(p.relative_to(self.stage)): p.read_bytes() for p in self.stage.rglob('*') if p.is_file()})

    def test_pending_migration_rejects_submit_before_stage_creation(self):
        deploy.atomic_json(self.project / 'junket-world-applications/latest.json', {'status': 'installing'})
        manager = deploy.PackApplications(SimpleNamespace(PROJECT_DIR=self.project, DATA_DIR=self.live))
        with self.assertRaisesRegex(RuntimeError, 'migration is active'): manager.submit(str(uuid.uuid4()), self.archive())
        self.assertFalse((self.project / 'cosmetics-applications').exists())

    def test_unpack_applies_pin_before_preparing_versions(self):
        destination = self.project / 'unpacked'
        result = deploy.unpack(self.archive(), destination, self.live, self.project)
        self.assertEqual(result, self.digest)
        self.assertEqual(deploy.read_json(destination / 'cosmetics-build.json')['native_junket_overlay'], self.marker)
        for pack, expected in self.marker['packs'].items(): self.assertEqual(deploy.content_hash(destination / pack), expected)

    def test_missing_optional_module_fails_closed_when_marker_exists(self):
        with patch.dict(sys.modules, {'minecraft_junket_overlay': None}):
            with self.assertRaisesRegex(RuntimeError, 'helper is unavailable'): deploy.pinned_native_overlay(self.project)

    def test_without_pin_or_migration_record_remains_noop_even_without_module(self):
        (self.project / 'junket-native-overlay.json').unlink()
        (self.project / 'junket-world-applications/latest.json').unlink()
        with patch.dict(sys.modules, {'minecraft_junket_overlay': None}):
            self.assertIsNone(deploy.pinned_native_overlay(self.project, self.stage))

    def test_marker_cannot_select_unrelated_pack_or_arbitrary_path(self):
        marker = dict(self.marker, packs={**self.marker['packs'], 'behavior_packs/import_structures': 'b' * 64})
        deploy.atomic_json(self.project / 'junket-native-overlay.json', marker)
        with self.assertRaisesRegex(ValueError, 'only the two'): preserve_overlay(self.project, self.stage)
        marker = dict(self.marker, operation_id='../../outside')
        deploy.atomic_json(self.project / 'junket-native-overlay.json', marker)
        with self.assertRaises(ValueError): preserve_overlay(self.project, self.stage)

    def test_pending_migration_started_after_submit_is_rejected_under_lock(self):
        manager = deploy.PackApplications(SimpleNamespace(PROJECT_DIR=self.project, DATA_DIR=self.live))
        operation = str(uuid.uuid4())
        directory = manager.root / operation
        directory.mkdir(parents=True)
        shutil.copytree(self.stage, directory / 'stage')
        job = {'operation_id': operation, 'catalog_digest': self.digest, 'status': 'queued'}
        manager._save(job)
        deploy.atomic_json(self.project / 'junket-world-applications/latest.json', {'status': 'starting'})
        # The API deliberately has no stop/start/status methods: none should run.
        with patch.object(deploy, 'deployment_lock', nullcontext): manager._run(job)
        self.assertEqual(manager.status()['status'], 'failed')
        self.assertFalse(self.live.exists())

    def test_separate_native_runner_does_not_reject_its_own_pending_operation(self):
        class Native(deploy.PackApplications):
            def _stop(self): pass
            def _start(self): pass
            def _backup(self, directory): pass
            def _install(self, directory):
                for pack in deploy.pack_layout(directory / 'stage')[1]:
                    shutil.copytree(directory / 'stage' / pack, self.api.DATA_DIR / pack)
        api = SimpleNamespace(PROJECT_DIR=self.project, DATA_DIR=self.live,
                              status_payload=lambda: {'container': {'state': 'running', 'health': 'healthy'}})
        manager = Native(api)
        manager.root = self.project / 'junket-world-applications'
        operation = str(uuid.uuid4())
        directory = manager.root / operation
        directory.mkdir()
        shutil.copytree(self.stage, directory / 'stage')
        job = {'operation_id': operation, 'catalog_digest': self.digest, 'status': 'queued'}
        manager._save(job)
        with patch.object(deploy, 'deployment_lock', nullcontext), patch.object(deploy, 'pinned_native_overlay', side_effect=AssertionError('native runner called its own guard')):
            manager._run(job)
        self.assertEqual(manager.status()['status'], 'succeeded')


if __name__ == '__main__': unittest.main()
