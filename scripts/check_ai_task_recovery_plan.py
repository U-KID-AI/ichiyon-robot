"""Offline fixed-path, race and read-only recovery separation planner checks."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch

import ai_task_recovery_plan as planner
from ai_task_backup import BackupError
from check_ai_task_storage_retention import filesystem_state, forbid_mutations


class RecoveryFixture:
    def __init__(self, root):
        self.root = root
        self.shared = root / 'shared'
        self.recovery = root / 'recovery'
        self.source = self.shared / 'data/backups'
        self.source.mkdir(parents=True)
        self.pack = self.source / 'pack-operation'
        self.pack.mkdir()
        (self.pack / 'baseline.zip').write_bytes(b'fixture baseline bytes')
        (self.pack / 'proof.json').write_bytes(b'{"fixture": true}')
        (self.source / 'old-json.json').write_bytes(b'{"secret_fixture": "DO_NOT_PRINT_FIXTURE"}')
        (self.shared / 'data/runtime.json').write_bytes(b'not an archive')
        (self.shared / 'secrets').mkdir()
        (self.shared / 'secrets/token').write_bytes(b'OUTSIDE_SCOPE_SECRET')

    def plan(self):
        with patch.object(planner, 'SHARED_ROOT', self.shared), \
                patch.object(planner, 'RECOVERY_ROOT', self.recovery):
            return planner.plan_recovery()


class RecoveryPlanTests(unittest.TestCase):
    def test_production_roots_are_fixed_constants(self):
        self.assertEqual(planner.SHARED_ROOT.as_posix(), '/home/ubuntu/ichiyon-shared')
        self.assertEqual(planner.RECOVERY_ROOT.as_posix(), '/home/ubuntu/ichiyon-recovery-archives')

    def test_plan_preserves_unique_archives_and_requires_production_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            result = host.plan()
            self.assertEqual(result['mode'], 'plan-only')
            self.assertEqual(result['classification'], 'NEEDS_REVIEW')
            self.assertFalse(result['migration_allowed'])
            self.assertFalse(result['production_scope_change_allowed'])
            self.assertEqual(result['actions'], [])
            self.assertEqual(result['subtrees'], ['old-json.json', 'pack-operation'])
            self.assertEqual(result['files'], 3)
            self.assertEqual(result['entries'], 5)
            expected = sum(path.stat().st_size for path in host.source.rglob('*') if path.is_file())
            self.assertEqual(result['payload_bytes'], expected)
            self.assertEqual(len(result['source_inventory_sha256']), 64)
            self.assertIn('LIVE_JSON_SNAPSHOT_WRITERS_REQUIRE_REDIRECTION_OR_EVERY_BACKUP_DELTA', result['blockers'])
            self.assertIn('PRODUCTION_SOURCE_AND_RECOVERY_INVENTORY_EQUALITY_NOT_PROVEN', result['blockers'])

    def test_inventory_hash_is_deterministic_and_contains_file_hash_not_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            before = host.plan()
            inventory = planner.source_inventory(host.source)
            expected = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(before['source_inventory_sha256'], expected)
            self.assertEqual(before, host.plan())
            self.assertTrue(all(item['path'].startswith('data/backups') for item in inventory))
            output = json.dumps([before, inventory])
            for secret in ('DO_NOT_PRINT_FIXTURE', 'OUTSIDE_SCOPE_SECRET', 'fixture baseline bytes'):
                self.assertNotIn(secret, output)
            (host.pack / 'baseline.zip').write_bytes(b'changed fixture baseline')
            self.assertNotEqual(before['source_inventory_sha256'], host.plan()['source_inventory_sha256'])

    def test_no_write_no_process_and_no_recovery_directory_created(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            before = filesystem_state(host.root)
            tripwire = forbid_mutations()
            with tripwire, patch('subprocess.run', side_effect=AssertionError('process execution')):
                result = host.plan()
            self.assertEqual(tripwire.attempts, [])
            self.assertIn('source_inventory_sha256', result)
            self.assertFalse(host.recovery.exists())
            self.assertEqual(filesystem_state(host.root), before)

    def test_cli_rejects_apply_cleanup_and_arbitrary_roots_before_inventory(self):
        for args in (['--apply'], ['--delete'], ['--prune'], ['--source', '/tmp/source'],
                     ['--recovery-root', '/tmp/recovery'], ['/tmp/source'], ['--execute']):
            with self.subTest(args=args), patch.object(planner, 'plan_recovery') as plan:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    planner.main(args)
                self.assertNotEqual(error.exception.code, 0)
                plan.assert_not_called()

    def test_cli_only_emits_json_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            output = io.StringIO()
            with patch.object(planner, 'SHARED_ROOT', host.shared), \
                    patch.object(planner, 'RECOVERY_ROOT', host.recovery), redirect_stdout(output):
                self.assertEqual(planner.main([]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result, host.plan())
            self.assertNotIn('DO_NOT_PRINT_FIXTURE', output.getvalue())

    def test_missing_source_fails_closed_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory) / 'missing'
            with patch.object(planner, 'SHARED_ROOT', shared):
                result = planner.plan_recovery()
            self.assertIn('SOURCE_INVENTORY_UNAVAILABLE_OR_CHANGED', result['blockers'])
            self.assertNotIn('source_inventory_sha256', result)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_lexical_parent_escape_rejected_by_directory_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            alias = host.source / '..' / 'backups'
            with self.assertRaises(BackupError):
                planner.source_inventory(alias)

    def test_symlink_file_is_not_followed_or_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            link = host.source / 'escape'
            try:
                link.symlink_to(host.shared / 'secrets/token')
            except OSError:
                self.skipTest('OS does not permit symlink creation')
            with patch.object(planner, 'digest', wraps=planner.digest) as digest:
                result = host.plan()
            self.assertIn('SOURCE_INVENTORY_UNAVAILABLE_OR_CHANGED', result['blockers'])
            self.assertNotIn('source_inventory_sha256', result)
            self.assertFalse(any(call.args[0] == link for call in digest.call_args_list))

    def test_symlink_root_is_rejected_before_any_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            alias = host.root / 'alias'
            try:
                alias.symlink_to(host.shared, target_is_directory=True)
            except OSError:
                self.skipTest('OS does not permit directory symlink creation')
            with patch.object(planner, 'SHARED_ROOT', alias), patch.object(planner, 'digest') as digest:
                result = planner.plan_recovery()
            self.assertIn('SOURCE_INVENTORY_UNAVAILABLE_OR_CHANGED', result['blockers'])
            digest.assert_not_called()

    def test_special_file_metadata_is_rejected_before_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            target = host.source / 'old-json.json'
            original = Path.lstat
            def special(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                if path == target:
                    fields = {name: getattr(value, name) for name in (
                        'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')}
                    return types.SimpleNamespace(st_mode=stat.S_IFIFO | 0o600, **fields)
                return value
            with patch.object(Path, 'lstat', special), patch.object(planner, 'digest', wraps=planner.digest) as digest:
                result = host.plan()
            self.assertIn('SOURCE_INVENTORY_UNAVAILABLE_OR_CHANGED', result['blockers'])
            self.assertFalse(any(call.args[0] == target for call in digest.call_args_list))

    def test_changed_file_during_its_digest_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            original = planner.digest
            def changed(path):
                result = original(path)
                path.write_bytes(b'concurrent writer fixture')
                return result
            with patch.object(planner, 'digest', changed):
                result = host.plan()
            self.assertIn('SOURCE_INVENTORY_UNAVAILABLE_OR_CHANGED', result['blockers'])
            self.assertNotIn('source_inventory_sha256', result)

    def test_cross_filesystem_subtree_is_rejected_before_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            target = host.source / 'old-json.json'
            original = Path.lstat
            def mounted(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                if path == target:
                    fields = {name: getattr(value, name) for name in (
                        'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')}
                    return types.SimpleNamespace(st_dev=value.st_dev + 1, **fields)
                return value
            with patch.object(Path, 'lstat', mounted), patch.object(planner, 'digest', wraps=planner.digest) as digest:
                result = host.plan()
            self.assertIn('SOURCE_INVENTORY_UNAVAILABLE_OR_CHANGED', result['blockers'])
            self.assertFalse(any(call.args[0] == target for call in digest.call_args_list))

    def test_nonexclusive_file_cannot_prove_independent_recovery_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            target = host.source / 'old-json.json'
            try:
                os.link(str(target), str(host.root / 'external-hardlink'))
            except OSError:
                self.skipTest('OS does not permit hardlink creation')
            result = host.plan()
            self.assertIn('SOURCE_INVENTORY_UNAVAILABLE_OR_CHANGED', result['blockers'])
            self.assertNotIn('source_inventory_sha256', result)

    def test_changed_earlier_file_while_later_file_is_hashed_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            first = host.source / 'old-json.json'
            later = host.pack / 'baseline.zip'
            original = planner.digest
            def changed(path):
                result = original(path)
                if path == later:
                    first.write_bytes(b'changed after first file was already verified')
                return result
            with patch.object(planner, 'digest', changed):
                result = host.plan()
            self.assertIn('SOURCE_INVENTORY_UNAVAILABLE_OR_CHANGED', result['blockers'])
            self.assertNotIn('source_inventory_sha256', result)

    def test_added_subtree_during_inventory_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            host = RecoveryFixture(Path(directory))
            original = planner.digest
            added = False
            def changed(path):
                nonlocal added
                result = original(path)
                if not added:
                    (host.source / 'new-operation').mkdir()
                    added = True
                return result
            with patch.object(planner, 'digest', changed):
                result = host.plan()
            self.assertIn('SOURCE_INVENTORY_UNAVAILABLE_OR_CHANGED', result['blockers'])
            self.assertNotIn('source_inventory_sha256', result)


if __name__ == '__main__':
    unittest.main()
