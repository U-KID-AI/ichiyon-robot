"""Offline split producer tests: exact scope, deduplication, delta, fail closed."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import ai_task_backup as backup
import ai_task_backup_split as split
from check_ai_task_backup_restore import BackupFixture, FILES, TARGET, file_map


class SplitWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ichiyon-split-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared = self.root / 'shared'
        for name, value in FILES.items():
            path = self.shared / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        self.recovery = self.root / 'recovery'
        self.recovery.mkdir()

    def stage(self, name):
        fixture = BackupFixture(self.root / name, version=1).make_writer_stage()
        # This is a disposable fixture only; production stages start with three inputs.
        (fixture.path / 'persistence.tar').unlink()
        return fixture

    def write(self, fixture, validator=None):
        return split.write_split_backup(fixture.path, TARGET, shared_root=self.shared,
            backups_root=fixture.path.parent, releases_root=fixture.releases, recovery_root=self.recovery,
            dump_validator=validator or (lambda _: True))

    def test_full_and_split_restore_exact_files_previous_dump(self):
        full = BackupFixture(self.root / 'full', version=2)
        fixture = self.stage('split')
        proof = self.write(fixture)
        # Existing v2 validator verifies the union; existing restore implementation is reused.
        self.assertEqual(proof['inventory'], full.validate()['inventory'])
        self.assertEqual(proof['previous_release'], full.validate()['previous_release'])
        self.assertTrue(all(not split.historical(row['path']) for row in
                           backup.archive_inventory(fixture.path / 'persistence.tar', split=True)))
        fixture.path.rename(fixture.path.parent / TARGET)
        fixture.path = fixture.path.parent / TARGET
        destination = self.root / 'restore'
        destination.mkdir()
        restored = backup.restore_files(fixture.path, destination, releases_root=fixture.releases,
                                        recovery_root=self.recovery, dump_validator=lambda _: True)
        self.assertEqual(file_map(Path(restored['persistence'])), FILES)
        self.assertEqual((Path(restored['metadata']) / 'production.dump').read_bytes(),
                         (full.path / 'production.dump').read_bytes())

    def test_unchanged_history_reuses_archive_even_when_mtime_changes(self):
        first = self.write(self.stage('first'))
        for name in FILES:
            if split.historical(name):
                os.utime(self.shared / name, (1, 1))
        second = self.write(self.stage('second'))
        self.assertEqual(first['recovery_archive_id'], second['recovery_archive_id'])
        self.assertEqual(len(list(self.recovery.iterdir())), 1)

    def test_new_historical_delta_is_captured_and_old_archive_retained(self):
        first = self.write(self.stage('first'))
        (self.shared / 'data/backups/new.json').write_bytes(b'new snapshot')
        second = self.write(self.stage('second'))
        self.assertNotEqual(first['recovery_archive_id'], second['recovery_archive_id'])
        self.assertEqual(len(list(self.recovery.iterdir())), 2)
        self.assertIn('data/backups/new.json', {row['path'] for row in second['inventory']})

    def test_bad_existing_archive_is_never_overwritten(self):
        proof = self.write(self.stage('first'))
        archive = self.recovery / proof['recovery_archive_id'] / 'recovery.tar'
        archive.write_bytes(b'corrupt')
        stage = self.stage('second')
        with self.assertRaises(backup.BackupError):
            self.write(stage)
        self.assertEqual(archive.read_bytes(), b'corrupt')
        self.assertFalse((stage.path / 'READY').exists())

    def test_source_change_before_ready_retains_unpublished_stage(self):
        stage = self.stage('changing')
        def validator(_):
            (self.shared / 'data/backups/new.json').write_bytes(b'delta during backup')
            return True
        with self.assertRaisesRegex(backup.BackupError, 'source_changed'):
            self.write(stage, validator)
        self.assertFalse((stage.path / 'READY').exists())
        self.assertTrue((stage.path / 'production.dump').exists())
        self.assertTrue((self.shared / 'data/backups/new.json').exists())

    def test_missing_history_or_unreadable_dump_rejected(self):
        stage = self.stage('bad-dump')
        with self.assertRaises(backup.BackupError):
            self.write(stage, lambda _: False)
        self.assertFalse((stage.path / 'READY').exists())
        (self.shared / 'data/backups').rename(self.shared / 'data/history-moved')
        with self.assertRaisesRegex(backup.BackupError, 'historical_root_required'):
            self.write(self.stage('missing-history'))

    def test_links_and_hardlinks_rejected(self):
        source = self.shared / 'data/runtime.json'
        alias = self.shared / 'data/alias.json'
        try:
            os.link(source, alias)
        except OSError:
            self.skipTest('hardlink unavailable')
        with self.assertRaises(backup.BackupError):
            self.write(self.stage('hardlink'))
        alias.unlink()
        try:
            alias.symlink_to(source)
        except OSError:
            return
        with self.assertRaises(backup.BackupError):
            self.write(self.stage('symlink'))

    def test_published_generation_cannot_be_rewritten(self):
        stage = self.stage('same')
        self.write(stage)
        original = file_map(stage.path)
        with self.assertRaises(backup.BackupError):
            self.write(stage)
        self.assertEqual(file_map(stage.path), original)

    def test_archive_publication_failure_has_no_backup_ready(self):
        stage = self.stage('failure')
        with patch.object(Path, 'rename', side_effect=OSError('fixture publication failure')):
            with self.assertRaises(OSError):
                self.write(stage)
        self.assertFalse((stage.path / 'READY').exists())
        self.assertTrue(any(path.name.startswith('.pending-') for path in self.recovery.iterdir()))


if __name__ == '__main__':
    unittest.main()
