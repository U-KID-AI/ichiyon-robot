"""Offline P1c-2B fixtures; all deletion is inside TemporaryDirectory."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ai_task_storage_maintenance as maintenance
from check_ai_task_storage_cleanup import fixture, attest


class MemoryAudit:
    def __init__(self):
        self.events = []
        self.closed = False
    def write(self, event):
        self.events.append(copy.deepcopy(event))
    def close(self):
        self.closed = True


@contextmanager
def fixture_lock():
    yield [1, 2]


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixture()
        self.now = self.snapshot['captured_end_at']
        self.audit = MemoryAudit()
        self.tree = [dict(path='', signature=[1, 2, 16832, 4096, 1, 1], sha256=None)]
        self.tree_patch = patch.object(maintenance, 'target_tree', return_value=self.tree)
        self.tree_patch.start()
        self.addCleanup(self.tree_patch.stop)
        self.plan = maintenance.maintenance_plan(self.snapshot)
        self.plan['body']['targets'] = [t for t in self.plan['body']['targets'] if t['category'] == 'staging']
        self.plan['sha256'] = maintenance.digest(self.plan['body'])
        self.window = dict(start=self.now - 1, end=self.now + 60)

    def execute(self, **kwargs):
        defaults = dict(collect=lambda identity: self.snapshot, clock=lambda: self.now,
                        lock=fixture_lock, audit_factory=lambda: self.audit, remover=lambda target, audit: None)
        defaults.update(kwargs)
        return maintenance.execute_plan(self.plan, self.window, **defaults)

    def test_fresh_complete_evidence_and_durable_order(self):
        calls = []
        result = self.execute(remover=lambda target, audit: calls.append(audit.events[-1]['state']))
        self.assertEqual(calls, ['target_started'])
        self.assertEqual(result[0]['state'], 'removed')
        self.assertEqual(self.audit.events[-1]['state'], 'completed')
        self.assertTrue(self.audit.closed)

    def test_changed_references_block_existing_approved_plan(self):
        changed = copy.deepcopy(self.snapshot)
        changed['staging'][0]['active'] = True
        attest(changed)
        result = self.execute(collect=lambda identity: changed,
                              remover=lambda *args: self.fail('must not delete'))
        self.assertEqual(result[0]['state'], 'failed_or_partial')
        self.assertEqual(self.audit.events[-1]['state'], 'partial_failure')

    def test_changed_content_or_identity_blocks(self):
        with patch.object(maintenance, 'target_tree', return_value=[dict(path='', signature=[2], sha256=None)]):
            result = self.execute(remover=lambda *args: self.fail('must not delete'))
        self.assertEqual(result[0]['state'], 'failed_or_partial')

    def test_partial_failure_is_audited_and_not_retried(self):
        def partial(target, audit):
            audit.write(dict(state='member_removed', member='/first'))
            raise OSError('private detail')
        result = self.execute(remover=partial)
        self.assertEqual(result[0]['state'], 'failed_or_partial')
        self.assertNotIn('private detail', json.dumps(self.audit.events))
        self.assertIn('member_removed', [event['state'] for event in self.audit.events])

    def test_window_expiry_before_target_stops_mutation(self):
        calls = iter([self.now, self.now, self.now + 61])
        result = self.execute(clock=lambda: next(calls), remover=lambda *args: self.fail('must not delete'))
        self.assertEqual(result[0]['state'], 'failed_or_partial')

    def test_collection_crossing_window_never_starts_deletion(self):
        current = [self.now]
        def slow_collect(identity):
            self.assertEqual(identity, [1, 2])
            current[0] = self.window['end']
            return self.snapshot
        result = self.execute(collect=slow_collect, clock=lambda: current[0],
                              remover=lambda *args: self.fail('expired collection must not delete'))
        self.assertEqual(result[0]['state'], 'failed_or_partial')
        states = [event['state'] for event in self.audit.events]
        self.assertNotIn('target_started', states)
        self.assertEqual(states[-1], 'partial_failure')
        self.assertTrue(self.audit.closed)

    def test_first_partial_target_stops_second_approved_target(self):
        extra = copy.deepcopy(self.snapshot['staging'][0])
        extra['operation_sha'] = 'f' * 40
        extra['id'] = extra['path'] = '/home/ubuntu/ichiyon-releases/.prepare-' + 'f' * 40 + '.ijklmnop'
        self.snapshot['staging'].append(extra)
        attest(self.snapshot)
        self.plan = maintenance.maintenance_plan(self.snapshot)
        self.plan['body']['targets'] = [t for t in self.plan['body']['targets'] if t['category'] == 'staging']
        self.plan['sha256'] = maintenance.digest(self.plan['body'])
        self.assertEqual(len(self.plan['body']['targets']), 2)
        calls = []
        def partial(target, audit):
            calls.append(target['id'])
            audit.write(dict(state='member_removed', member='/first'))
            raise OSError('fixture partial failure')
        result = self.execute(remover=partial)
        self.assertEqual(calls, [self.plan['body']['targets'][0]['id']])
        self.assertEqual([row['state'] for row in result], ['failed_or_partial', 'not_attempted'])
        self.assertEqual(result[1]['reason'], 'EARLIER_FAILURE')
        self.assertEqual(self.audit.events[-1]['state'], 'partial_failure')
        self.assertEqual(self.audit.events[-1]['results'], result)

    def test_durable_audit_failure_prevents_deletion(self):
        for fail_at in ('started', 'target_started'):
            with self.subTest(fail_at=fail_at):
                self.audit = MemoryAudit()
                write = self.audit.write
                def unavailable(event):
                    if event['state'] == fail_at:
                        raise OSError('fixture audit unavailable')
                    write(event)
                with patch.object(self.audit, 'write', side_effect=unavailable):
                    if fail_at == 'started':
                        with self.assertRaises(OSError):
                            self.execute(remover=lambda *args: self.fail('audit failure must not delete'))
                    else:
                        result = self.execute(remover=lambda *args: self.fail('audit failure must not delete'))
                        self.assertEqual(result[0]['state'], 'failed_or_partial')
                        self.assertEqual(self.audit.events[-1]['state'], 'partial_failure')
                self.assertTrue(self.audit.closed)
                self.assertNotIn('removed', [event['state'] for event in self.audit.events])

    def test_forged_or_expired_plan_rejected_before_lock(self):
        self.plan['body']['targets'][0]['id'] = 'forged'
        with self.assertRaises(maintenance.MaintenanceError):
            self.execute()
        self.assertEqual(self.audit.events, [])
        self.plan = maintenance.maintenance_plan(self.snapshot)
        with self.assertRaises(maintenance.MaintenanceError):
            self.execute(clock=lambda: self.now + 301)

    def test_active_and_stale_operations_never_gain_terminal_authority(self):
        for state, owner in [('running', 'alive'), ('running', 'terminated_identity_verified'), ('failed', 'unknown')]:
            changed = copy.deepcopy(self.snapshot)
            for record in changed['cleanup_evidence']['records']:
                record['operation'].update(state=state, owner_process_state=owner)
            proposal = maintenance.maintenance_plan(changed)
            self.assertEqual(proposal['body']['targets'], [])

    def test_only_independently_observed_exclusive_holder_is_accepted(self):
        changed = copy.deepcopy(self.snapshot)
        changed['operation_observation'].update(lock_held=True, maintenance_holder_pid=123)
        changed['cleanup_evidence']['lock'].update(held=True, holder_pids=[123], maintenance_holder_pid=123)
        changed['cleanup_evidence']['inventory_sha256'] = maintenance.inventory_digest(changed)
        self.assertTrue(maintenance.maintenance_plan(changed)['body']['targets'])
        changed['cleanup_evidence']['lock']['holder_pids'].append(456)
        self.assertEqual(maintenance.maintenance_plan(changed)['body']['targets'], [])


class ReceiptGraphTests(unittest.TestCase):
    def row(self, key, **kwargs):
        return dict(key=key, references=[], verified=True, active=False,
                    incident_review_complete=True, completed_at=0, off_host_restore_verified=True, **kwargs)

    def test_backup_and_lossless_receipt_pin_archive(self):
        obj = self.row('backup:a')
        obj['references'] = ['receipt:a', 'archive:a']
        receipt = self.row('receipt:a')
        receipt['references'] = ['archive:a']
        result = maintenance.receipt_archive_graph([obj], [receipt], [self.row('archive:a')], 40 * 86400)
        self.assertTrue(result['complete'])
        self.assertTrue(all(row['classification'] == 'KEEP' for row in result['nodes']))
        self.assertIn('LOSSLESS_RECEIPT_REQUIRED', result['nodes'][0]['reasons'])

    def test_unreferenced_archive_needs_independent_restore_and_review(self):
        archive = self.row('archive:a')
        result = maintenance.receipt_archive_graph([], [], [archive], 40 * 86400)
        self.assertEqual(result['nodes'][0]['classification'], 'RETIREMENT_PROPOSAL')
        for field in ('verified', 'off_host_restore_verified', 'incident_review_complete'):
            modified = dict(archive, **{field: False})
            self.assertEqual(maintenance.receipt_archive_graph([], [], [modified], 40 * 86400)['nodes'][0]['classification'], 'KEEP')

    def test_unknown_edges_incomplete_inventory_and_stale_owner_keep_all(self):
        receipt = self.row('receipt:a', owner_state='unknown')
        receipt['references'] = ['archive:missing']
        result = maintenance.receipt_archive_graph([], [receipt], [self.row('archive:a')], 40 * 86400)
        self.assertFalse(result['complete'])
        self.assertTrue(all(row['classification'] == 'KEEP' for row in result['nodes']))
        self.assertFalse(maintenance.receipt_archive_graph([], [], [], 40 * 86400, complete=False)['complete'])


@unittest.skipUnless(os.name == 'posix', 'descriptor deletion and flock require Linux')
class ExactExecutorTests(unittest.TestCase):
    def setUp(self):
        owner = patch('ai_task_storage_evidence._uid', return_value=os.getuid())
        owner.start()
        self.addCleanup(owner.stop)

    def test_real_exact_tree_delete_preserves_neighbor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'target'
            (target / 'nested').mkdir(parents=True)
            (target / 'nested/file').write_bytes(b'fixture')
            neighbor = root / 'neighbor'
            neighbor.write_bytes(b'keep')
            tree = maintenance.target_tree(target)
            audit = MemoryAudit()
            with patch.object(maintenance, '_target_valid', side_effect=lambda category, node: node['path'] == str(target)):
                maintenance.remove_exact(dict(category='releases', id='a' * 40, path=str(target), tree=tree), audit)
            self.assertFalse(target.exists())
            self.assertEqual(neighbor.read_bytes(), b'keep')
            self.assertEqual(sum(row['state'] == 'member_removed' for row in audit.events), 2)

    def test_same_device_bind_mount_at_target_or_descendant_blocks_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'target'
            nested = target / 'nested'
            nested.mkdir(parents=True)
            member = nested / 'file'
            member.write_bytes(b'keep')
            tree = maintenance.target_tree(target)
            # A bind mount retains st_dev; filesystem identity checks alone do
            # not detect it. Supply the independently read kernel mount paths.
            self.assertEqual(target.stat().st_dev, nested.stat().st_dev)
            for mount in (target, nested):
                with self.subTest(mount=mount), \
                     patch.object(maintenance, 'mount_points', return_value=[Path('/'), mount]), \
                     patch.object(maintenance, '_target_valid', return_value=True):
                    with self.assertRaisesRegex(maintenance.MaintenanceError, 'TARGET_CONTAINS_MOUNT'):
                        maintenance.target_tree(target)
                    audit = MemoryAudit()
                    with self.assertRaisesRegex(maintenance.MaintenanceError, 'TARGET_CONTAINS_MOUNT'):
                        maintenance.remove_exact(dict(category='releases', id='a' * 40,
                            path=str(target), tree=tree), audit)
                    self.assertEqual(member.read_bytes(), b'keep')
                    self.assertEqual(audit.events, [])
            with patch.object(maintenance, 'mount_points', return_value=[Path('/'), root / 'neighbor']):
                self.assertEqual(maintenance.target_tree(target), tree)

    def test_expired_guard_after_first_member_is_audited_and_preserves_rest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'target'
            target.mkdir()
            first, later, neighbor = target / 'a-first', target / 'b-later', root / 'neighbor'
            for member in (first, later, neighbor):
                member.write_bytes(b'keep unless first')
            snapshot = fixture()
            now = snapshot['captured_end_at']
            current = [now]
            window = dict(start=now - 1, end=now + 60)
            exact = dict(category='releases', id='a' * 40, path=str(target),
                         tree=maintenance.target_tree(target), object_sha256='b' * 64)
            body = dict(version=1, plan_only=True, captured_at=now, targets=[exact])
            plan = dict(body=body, sha256=maintenance.digest(body))
            audit = MemoryAudit()
            write = audit.write
            def expire_after_first(event):
                write(event)
                if event['state'] == 'member_removed':
                    current[0] = window['end']
            # Eligibility is synthetic in this fixture; deletion itself uses
            # real descriptor walking, hashing, fsync and per-member guards.
            with patch.object(maintenance, 'maintenance_plan', return_value=plan), \
                 patch.object(maintenance, '_target_valid', return_value=True), \
                 patch.object(audit, 'write', side_effect=expire_after_first):
                result = maintenance.execute_plan(plan, window, collect=lambda identity: snapshot,
                    clock=lambda: current[0], lock=fixture_lock, audit_factory=lambda: audit)
            self.assertEqual(result[0]['state'], 'failed_or_partial')
            self.assertFalse(first.exists())
            self.assertEqual(later.read_bytes(), b'keep unless first')
            self.assertEqual(neighbor.read_bytes(), b'keep unless first')
            self.assertTrue(target.is_dir())
            removed = [event['member'] for event in audit.events if event['state'] == 'member_removed']
            self.assertEqual(removed, ['/a-first'])
            self.assertEqual(audit.events[-1]['state'], 'partial_failure')
            self.assertTrue(audit.closed)

    def test_symlink_hardlink_fifo_and_replaced_tree_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'target'
            target.mkdir()
            outside = root / 'outside'
            outside.write_bytes(b'keep')
            (target / 'link').symlink_to(outside)
            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.target_tree(target)
            (target / 'link').unlink()
            os.link(outside, target / 'hard')
            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.target_tree(target)
            (target / 'hard').unlink()
            os.mkfifo(target / 'fifo')
            with self.assertRaises(maintenance.MaintenanceError):
                maintenance.target_tree(target)
            (target / 'fifo').unlink()
            tree = maintenance.target_tree(target)
            (target / 'new').write_bytes(b'new')
            with patch.object(maintenance, '_target_valid', return_value=True), self.assertRaises(maintenance.MaintenanceError):
                maintenance.remove_exact(dict(category='releases', id='a' * 40, path=str(target), tree=tree), MemoryAudit())
            self.assertEqual(outside.read_bytes(), b'keep')
            self.assertTrue((target / 'new').exists())

    def test_audit_hash_chain_survives_partial_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            with patch.object(maintenance, 'AUDIT_ROOT', root):
                audit = maintenance.Audit()
                audit.write(dict(state='started'))
                audit.write(dict(state='target_started'))
                name = audit.name
                audit.close()
            rows = [json.loads(line) for line in (root / name).read_text().splitlines()]
            self.assertEqual(rows[0]['sha256'], maintenance.digest(rows[0]['body']))
            self.assertEqual(rows[1]['body']['previous_sha256'], rows[0]['sha256'])
            self.assertEqual(rows[-1]['body']['event']['state'], 'target_started')

    def test_flock_excludes_second_owner_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'deploy.lock'
            path.write_bytes(b'')
            path.chmod(0o600)
            with patch.object(maintenance, 'DEPLOY_LOCK', str(path)):
                with maintenance.deployment_lock():
                    with self.assertRaises(BlockingIOError):
                        with maintenance.deployment_lock():
                            self.fail('second holder admitted')
            link = Path(temporary) / 'link'
            link.symlink_to(path)
            with patch.object(maintenance, 'DEPLOY_LOCK', str(link)), self.assertRaises(OSError):
                with maintenance.deployment_lock():
                    self.fail('symlink admitted')

    def test_docker_exact_id_without_force_or_prune(self):
        ident = 'sha256:' + 'a' * 64
        with patch.object(maintenance.subprocess, 'run') as run:
            run.return_value.returncode = 0
            maintenance.remove_exact(dict(category='images', id=ident, path=None), MemoryAudit())
        self.assertEqual(run.call_args.args[0], ['docker', 'image', 'rm', ident])


if __name__ == '__main__':
    unittest.main(verbosity=2)
