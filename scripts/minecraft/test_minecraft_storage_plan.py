"""Small offline retention fixtures; never touch the configured production path."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

import minecraft_storage_plan as planner


class RetentionPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.family = self.root / 'cosmetics-applications'
        self.family.mkdir()
        self.usage = {'complete': True, 'paths': [], 'issues': []}

    def operation(self, number, status='succeeded'):
        name = str(uuid.UUID(int=number))
        folder = self.family / name
        (folder / 'stage').mkdir(parents=True)
        (folder / 'original').mkdir()
        (folder / 'stage/payload').write_bytes(b'fixture')
        (folder / 'original/payload').write_bytes(b'prior')
        self.write(folder / 'job.json', {'operation_id': name, 'status': status})
        return folder

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding='utf8')

    def plan(self):
        return planner.build_plan(self.root, usage=self.usage, minimum_age_days=0)

    def decisions(self, result, folder):
        return {row['decision'] for row in result['targets'] if row['operation'].endswith(folder.name)}

    def test_current_previous_lineage_and_pin_preserved_without_endless_previous_chain(self):
        old, previous, current, lineage, pinned = [self.operation(n) for n in range(1, 6)]
        self.write(self.family / 'active.json', {'operation_id': current.name})
        self.write(current / 'original/active.json', {'operation_id': previous.name})
        self.write(previous / 'original/active.json', {'operation_id': old.name})
        self.write(current / 'preflight.json', {'world_operation': lineage.name})
        self.write(self.root / 'minecraft-retention-pins.json', {'operations': [pinned.name]})
        result = self.plan()
        self.assertEqual(self.decisions(result, old), {'REVIEW_CANDIDATE'})
        for kept in (previous, current, lineage, pinned):
            self.assertEqual(self.decisions(result, kept), {'KEEP'})
        self.assertFalse(result['deletion_supported'])
        self.assertEqual(result['guaranteed_reclaimable_bytes'], 0)

    def test_current_usage_failure_and_incomplete_inventory_fail_closed(self):
        active = self.operation(1)
        failed = self.operation(2, 'recovery_failed')
        self.usage['paths'] = [str(active / 'stage/payload')]
        result = self.plan()
        self.assertEqual(self.decisions(result, active), {'KEEP'})
        self.assertEqual(self.decisions(result, failed), {'KEEP'})
        other = self.operation(3)
        self.usage['complete'] = False
        self.assertEqual(self.decisions(self.plan(), other), {'NEEDS_REVIEW'})

    def test_missing_previous_or_invalid_metadata_blocks_candidates(self):
        current, old = self.operation(1), self.operation(2)
        self.write(self.family / 'active.json', {'operation_id': current.name})
        self.write(current / 'original/state.json', {'active_exists': True})
        self.assertEqual(self.decisions(self.plan(), old), {'NEEDS_REVIEW'})
        (current / 'original/state.json').unlink()
        (self.family / 'latest.json').write_text('{bad', encoding='utf8')
        self.assertEqual(self.decisions(self.plan(), old), {'NEEDS_REVIEW'})

    def test_no_files_changed_and_backups_never_candidates(self):
        self.operation(1)
        (self.root / 'backups').mkdir()
        (self.root / 'backups/retain').write_bytes(b'private fixture')
        def state():
            return {str(p.relative_to(self.root)): (p.stat().st_mtime_ns, p.read_bytes())
                    for p in self.root.rglob('*') if p.is_file()}
        before = state()
        result = self.plan()
        self.assertEqual(before, state())
        self.assertFalse(any('/backups/' in row['path'] for row in result['targets']))
        self.assertTrue(all(not row['deletion_authorized'] for row in result['targets']))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            planner.main(['--project', str(self.root), '--apply'])

    def test_unresolved_explicit_pin_does_not_grant_candidate_status(self):
        old = self.operation(1)
        self.write(self.root / 'minecraft-retention-pins.json', {'operations': [str(uuid.UUID(int=99))]})
        self.assertEqual(self.decisions(self.plan(), old), {'NEEDS_REVIEW'})

    @unittest.skipUnless(hasattr(os, 'link'), 'hardlinks unavailable')
    def test_shared_inode_never_counted_as_reclaimable(self):
        operation = self.operation(1)
        try:
            os.link(operation / 'stage/payload', self.root / 'kept-link')
        except OSError:
            self.skipTest('hardlinks unsupported on this filesystem')
        row = next(r for r in self.plan()['targets'] if r['path'] == str(operation / 'stage'))
        self.assertEqual(row['decision'], 'NEEDS_REVIEW')
        self.assertEqual(row['estimated_reclaimable_bytes'], 0)


if __name__ == '__main__':
    unittest.main()
