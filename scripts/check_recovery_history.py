"""Offline fixed-path producer compatibility; never touches production data."""
import ast
import hashlib
import json
import os
import stat
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot import recovery_history as history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()
        self.patch = patch.object(history, 'DATA_ROOT', self.data)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.source = self.data / 'quotes.json'
        self.payload = b'{"unique_old_value":"synthetic"}\n'
        self.source.write_bytes(self.payload)

    def test_full_legacy_scope_and_exact_bytes_remain(self):
        result = history.write_legacy_snapshot(self.source)
        self.assertEqual((self.data / 'backups' / result['name']).read_bytes(), self.payload)
        self.assertEqual(result['sha256'], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(result['destination'], 'legacy-full-scope')
        self.assertEqual(self.source.read_bytes(), self.payload)
        self.assertNotIn('synthetic', json.dumps(result))
        self.assertEqual(len(list((self.data / 'backups').iterdir())), 1)
        self.assertEqual((self.data / 'backups' / result['name']).stat().st_nlink, 1)
        if os.name != 'nt':
            # Container root creates these, but host ubuntu must tar them.
            self.assertEqual(stat.S_IMODE((self.data / 'backups').stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((self.data / 'backups' / result['name']).stat().st_mode), 0o644)

    def test_same_second_snapshots_do_not_overwrite_history(self):
        one = history.write_legacy_snapshot(self.source)
        self.source.write_bytes(b'{"new":true}')
        two = history.write_legacy_snapshot(self.source)
        self.assertNotEqual(one['name'], two['name'])
        self.assertEqual((self.data / 'backups' / one['name']).read_bytes(), self.payload)

    def test_missing_source_is_normal_noop(self):
        self.source.unlink()
        self.assertIsNone(history.write_legacy_snapshot(self.source))
        self.assertFalse((self.data / 'backups').exists())

    def test_no_arbitrary_source_or_destination(self):
        for source in (self.data / '../quotes.json', self.data / 'token.json',
                       self.data / 'secrets/quotes.json', self.data / 'backups/quotes.json'):
            with self.subTest(path=source), self.assertRaises(ValueError):
                history.write_legacy_snapshot(source)
        with self.assertRaises(TypeError):
            history.write_legacy_snapshot(self.source, destination='/tmp/elsewhere')

    def test_symlink_file_and_directory_refused(self):
        original = self.data / 'original'
        self.source.rename(original)
        try:
            self.source.symlink_to(original)
        except OSError:
            self.skipTest('Windows symlink privilege unavailable')
        with self.assertRaises(ValueError):
            history.write_legacy_snapshot(self.source)
        self.source.unlink()
        original.rename(self.source)
        elsewhere = Path(self.temp.name) / 'elsewhere'
        elsewhere.mkdir()
        (self.data / 'backups').symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(ValueError):
            history.write_legacy_snapshot(self.source)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_hardlink_and_oversized_payload_refused(self):
        linked = self.data / 'hardlink'
        os.link(self.source, linked)
        with self.assertRaises(ValueError):
            history.write_legacy_snapshot(self.source)
        linked.unlink()
        with patch.object(history, 'MAX_SNAPSHOT_BYTES', 2), self.assertRaises(ValueError):
            history.write_legacy_snapshot(self.source)

    def test_publication_failure_preserves_source_and_pending_evidence(self):
        with patch.object(history.os, 'rename', side_effect=OSError('fixture')), self.assertRaises(OSError):
            history.write_legacy_snapshot(self.source)
        self.assertEqual(self.source.read_bytes(), self.payload)
        pending = list((self.data / 'backups').iterdir())
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0].name.startswith('.snapshot-'))
        self.assertEqual(pending[0].stat().st_nlink, 1)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(pending[0].stat().st_mode), 0o644)

    def test_destination_collision_preserves_existing_snapshot(self):
        with patch.object(history.uuid, 'uuid4') as operation, patch.object(history, 'datetime') as clock:
            operation.return_value.hex = '0' * 32
            clock.now.return_value.strftime.return_value = '20260928_000000'
            one = history.write_legacy_snapshot(self.source)
            self.source.write_bytes(b'{"new":true}')
            with self.assertRaisesRegex(ValueError, 'history_destination_exists'):
                history.write_legacy_snapshot(self.source)
        root = self.data / 'backups'
        self.assertEqual((root / one['name']).read_bytes(), self.payload)
        self.assertEqual(len(list(root.iterdir())), 2)
        self.assertTrue(all(path.stat().st_nlink == 1 for path in root.iterdir()))

    def test_post_publication_failure_leaves_one_regular_archive_member(self):
        with patch.object(history, '_sync_directory', side_effect=OSError('fixture')), self.assertRaises(OSError):
            history.write_legacy_snapshot(self.source)
        root = self.data / 'backups'
        files = list(root.iterdir())
        self.assertEqual(len(files), 1)
        self.assertFalse(files[0].name.startswith('.snapshot-'))
        self.assertEqual(files[0].read_bytes(), self.payload)
        self.assertEqual(files[0].stat().st_nlink, 1)
        archive = Path(self.temp.name) / 'persistence.tar'
        with tarfile.open(str(archive), 'w') as writer:
            writer.add(str(root), arcname='data/backups')
        with tarfile.open(str(archive), 'r') as reader:
            members = reader.getmembers()
        self.assertTrue(all(member.isdir() or member.isreg() for member in members))
        self.assertEqual(sum(member.isreg() for member in members), 1)

    def test_migration_is_plan_only_and_ignores_external_environment(self):
        before = list(Path(self.temp.name).rglob('*'))
        with patch.dict(os.environ, {'RECOVERY_ROOT': '/tmp/attacker', 'BACKUP_DIR': '/tmp/attacker'}):
            plan = history.migration_plan()
        self.assertEqual(plan['actions'], [])
        self.assertFalse(plan['migration_allowed'])
        self.assertEqual(plan['recovery_root'], '/home/ubuntu/ichiyon-recovery-archives')
        self.assertEqual(before, list(Path(self.temp.name).rglob('*')))

    def test_admin_and_bot_wrappers_use_same_writer_and_isolate_failure(self):
        # Execute only wrapper ASTs, without importing runtime startup or DB code.
        for name in ('admin/main.py', 'bot/data_store.py'):
            tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
            function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                            and node.name == 'backup_json_file')
            code = compile(ast.Module(body=[function], type_ignores=[]), name, 'exec')
            context = {'Path': Path, 'write_legacy_snapshot': history.write_legacy_snapshot}
            exec(code, context)
            context['backup_json_file'](self.source)
            with patch.dict(context, {'write_legacy_snapshot': lambda _: (_ for _ in ()).throw(ValueError('secret'))}), \
                    patch('builtins.print') as printed:
                context['backup_json_file'](self.source)
            self.assertNotIn('secret', str(printed.call_args))


if __name__ == '__main__':
    unittest.main()
