"""Offline durable-index, crash recovery and 20,000-operation stress checks."""
from contextlib import closing, contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest.mock import patch

import ai_task_storage_evidence_store as store

SHA = 'a' * 40
OTHER = 'b' * 40
PROOF = dict(persistent_absence_verified=True, health_verified=True, cleanup_verified=True)


def active(opid, target=SHA, previous=SHA):
    return dict(operation_id=opid, owner=dict(pid=1, start_ticks=2), target_sha=target,
                previous_sha=previous, state='running', created_at=1.0, completed_at=None,
                sequence=1, active_payload_sha256=None, lock=dict(inode=42), initial_names={},
                initial_runtime={}, staging=[], rollback_result=None)


def terminal(payload, state='succeeded', rollback='not_needed'):
    result = copy.deepcopy(payload)
    result.update(state=state, rollback_result=rollback, completed_at=2.0,
                  sequence=payload['sequence'] + 1, active_payload_sha256=store.digest(payload))
    return result


class StoreChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'evidence'
        self.patch_root = patch.object(store, 'ROOT', self.root)
        self.patch_root.start()
        self.patch_uid = patch.object(store, '_uid', lambda: os.getuid() if hasattr(os, 'getuid') else 0)
        self.patch_uid.start()
        with store.Store(create=True):
            pass

    def tearDown(self):
        self.patch_uid.stop()
        self.patch_root.stop()
        self.temp.cleanup()

    def finish(self, writer, classification='C', keys=(), state='succeeded', rollback='not_needed'):
        opid = writer.begin(active)
        payload = terminal(writer.get_active(opid), state, rollback)
        writer.finish(payload, classification, keys, PROOF if classification == 'C' else None)
        return opid

    def mutate(self, sql, params=()):
        with closing(sqlite3.connect(self.root / 'v2/index.sqlite3')) as connection:
            with connection:
                connection.execute(sql, params)

    def test_full_synchronous_and_private_fixed_namespace(self):
        with store.Store() as writer:
            self.assertEqual(writer.connection.execute('PRAGMA synchronous').fetchone()[0], 2)
            self.assertEqual(writer.connection.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
            self.finish(writer)
        if os.name != 'nt':
            self.assertEqual((self.root / 'v2').stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.root / 'v2/index.sqlite3').stat().st_mode & 0o777, 0o600)
        with store.read_session() as reader:
            self.assertEqual(reader.connection.execute('PRAGMA query_only').fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                reader.connection.execute('DELETE FROM active')

    def test_active_bound_and_no_automatic_reclamation(self):
        with store.Store() as writer, patch.object(store, 'MAX_ACTIVE', 3):
            ids = [writer.begin(active) for _ in range(3)]
            with self.assertRaisesRegex(store.StoreError, 'admission_limit'):
                writer.begin(active)
            self.assertEqual([x['payload']['operation_id'] for x in writer.active()], ids)
            self.assertEqual(writer.summary()['counters']['active'], 3)

    def test_update_identity_and_duplicate_finish_fail_closed(self):
        with store.Store() as writer:
            opid = writer.begin(active)
            payload = writer.get_active(opid)
            payload['sequence'] += 1
            writer.update(payload)
            with self.assertRaisesRegex(store.StoreError, 'sequence_invalid'):
                writer.update(payload)
            payload = terminal(payload)
            writer.finish(payload, 'C', proof=PROOF)
            with self.assertRaisesRegex(store.StoreError, 'active_missing_or_terminal'):
                writer.finish(payload, 'C', proof=PROOF)
            with self.assertRaises(store.StoreError):
                writer.begin(lambda _: active(opid))

    def test_object_and_incident_receipts_remain_lossless(self):
        with store.Store() as writer, patch.object(store, 'RECENT_LIMIT', 2):
            referenced = self.finish(writer, 'A', [('releases', SHA), ('backups', OTHER)])
            incidents = [self.finish(writer, 'B', state=state, rollback=rollback)
                         for state, rollback in [('failed', 'not_attempted'), ('cancelled', 'succeeded'),
                                                 ('failed', 'failed'), ('succeeded', 'succeeded')]]
            retired = self.finish(writer)
            for _ in range(4):
                self.finish(writer)
            with self.assertRaisesRegex(store.StoreError, 'compacted'):
                writer.get(retired)
            self.assertEqual(writer.get(referenced)['classification'], 'A')
            self.assertEqual([x['payload']['operation_id'] for x in writer.resolve_objects([('releases', SHA)])], [referenced])
            self.assertEqual([x['payload']['operation_id'] for x in writer.incidents()], incidents)
            self.assertEqual(writer.summary()['counters'], dict(generation=10, active=0, A=1, B=4,
                                                               C_total=5, recent=2, rollups=1, object_bindings=2))

    def test_same_sha_alone_cannot_enter_rollup(self):
        with store.Store() as writer:
            opid = writer.begin(active)
            payload = terminal(writer.get_active(opid))
            for proof in (None, {}, dict(PROOF, health_verified=False)):
                with self.assertRaisesRegex(store.StoreError, 'rollup_not_proven'):
                    writer.finish(payload, 'C', proof=proof)
            with self.assertRaisesRegex(store.StoreError, 'rollup_not_proven'):
                writer.finish(payload, 'C', [('releases', SHA)], PROOF)
            self.assertEqual(writer.summary()['counters']['active'], 1)
            self.assertEqual(writer.summary()['counters']['C_total'], 0)

    def test_corrupt_receipt_is_not_object_evidence(self):
        with store.Store() as writer:
            opid = self.finish(writer, 'A', [('releases', SHA)])
        self.mutate("UPDATE receipts SET body='{}' WHERE operation_id=?", (opid,))
        with store.read_session() as reader:
            with self.assertRaisesRegex(store.StoreError, 'checksum_invalid'):
                reader.resolve_objects([('releases', SHA)])

    def test_corrupt_or_missing_index_fails_summary(self):
        with store.Store() as writer:
            self.finish(writer, 'A', [('releases', SHA)])
        self.mutate('UPDATE object_index SET object_key=?', (OTHER,))
        with store.read_session() as reader:
            with self.assertRaisesRegex(store.StoreError, 'index_invalid'):
                reader.summary()
        self.mutate('DELETE FROM object_index')
        with store.read_session() as reader:
            with self.assertRaisesRegex(store.StoreError, 'count_mismatch'):
                reader.summary()

    def test_corrupt_metadata_and_rollup_fail_closed(self):
        with store.Store() as writer:
            self.finish(writer)
        self.mutate("UPDATE rollups SET checksum=?", ('f' * 64,))
        with store.read_session() as reader:
            with self.assertRaisesRegex(store.StoreError, 'checksum_invalid'):
                reader.summary()
        self.mutate("UPDATE meta SET checksum=?", ('f' * 64,))
        with self.assertRaisesRegex(store.StoreError, 'open_failed'):
            store.read_session()

    def test_crash_during_compaction_transaction_preserves_active(self):
        with store.Store() as writer, patch.object(store, 'RECENT_LIMIT', 2):
            self.finish(writer)
            self.finish(writer)
            opid = writer.begin(active)
            payload = terminal(writer.get_active(opid))
            before = writer.summary()['counters']
            with patch.object(writer, '_write_meta', side_effect=RuntimeError('injected crash')):
                with self.assertRaises(RuntimeError):
                    writer.finish(payload, 'C', proof=PROOF)
            self.assertEqual(writer.summary()['counters'], before)
            self.assertEqual(writer.get_active(opid)['state'], 'running')
            self.assertEqual(len(writer.recent()), 2)
            writer.finish(payload, 'C', proof=PROOF)
            self.assertEqual(writer.summary()['counters']['C_total'], 3)

    def test_process_crash_hot_journal_recovery_is_atomic(self):
        with store.Store() as writer:
            self.finish(writer)
            opid = writer.begin(active)
        code = '''import os,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ai_task_storage_evidence_store as s
s.ROOT=Path(sys.argv[2]);s._uid=lambda:os.getuid() if hasattr(os,'getuid') else 0
w=s.Store();p=w.get_active(sys.argv[3]);p.update(state='succeeded',rollback_result='not_needed',completed_at=2.0,sequence=p['sequence']+1,active_payload_sha256=s.digest(p))
w._write_meta=lambda *_args,**_kwargs:os._exit(71)
w.finish(p,'C',proof=dict(persistent_absence_verified=True,health_verified=True,cleanup_verified=True))
'''
        result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve().parent), str(self.root), opid],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 71, result.stderr.decode('utf-8', 'replace'))
        with store.Store() as writer:
            self.assertEqual(writer.summary()['counters']['C_total'], 1)
            self.assertEqual(writer.get_active(opid)['state'], 'running')

    def test_spilled_hot_journal_readonly_does_not_recover_or_modify(self):
        with store.Store() as writer:
            opid = writer.begin(lambda identifier: dict(active(identifier), fixture_blob='x' * 100000))
        code = '''import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import ai_task_storage_evidence_store as s
s.ROOT=Path(sys.argv[2]);s._uid=lambda:os.getuid() if hasattr(os,'getuid') else 0
w=s.Store();w.connection.execute('PRAGMA cache_size=1')
p=w.get_active(sys.argv[3]);p.update(state='succeeded',rollback_result='not_needed',completed_at=2.0,sequence=p['sequence']+1,active_payload_sha256=s.digest(p))
w._write_meta=lambda *_args,**_kwargs:os._exit(72)
w.finish(p,'C',proof=dict(persistent_absence_verified=True,health_verified=True,cleanup_verified=True))
'''
        result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve().parent), str(self.root), opid],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 72, result.stderr.decode('utf-8', 'replace'))
        journal = self.root / 'v2/index.sqlite3-journal'
        self.assertTrue(journal.is_file())
        self.assertGreater(journal.stat().st_size, 512)
        self.assertEqual(journal.read_bytes()[:8], bytes.fromhex('d9d505f920a163d7'))
        def inventory():
            return {path.name: (path.stat().st_size, path.stat().st_mtime_ns,
                               hashlib.sha256(path.read_bytes()).hexdigest())
                    for path in (self.root / 'v2').iterdir()}
        before = inventory()
        with self.assertRaisesRegex(store.StoreError, 'open_failed'):
            store.read_session()
        self.assertEqual(inventory(), before)
        with store.Store() as writer:
            self.assertEqual(writer.summary()['counters']['C_total'], 0)
            self.assertEqual(writer.get_active(opid)['fixture_blob'], 'x' * 100000)

    def test_object_resolution_pages_have_no_total_matching_limit(self):
        with store.Store() as writer:
            ids = [self.finish(writer, 'B', [('releases', SHA)], state='failed', rollback='failed')
                   for _ in range(store.PAGE_LIMIT + 3)]
        with store.read_session() as reader:
            first = reader.resolve_objects_page('releases', SHA)
            second = reader.resolve_objects_page('releases', SHA, first[-1]['payload']['operation_id'])
            self.assertEqual(len(first), store.PAGE_LIMIT)
            self.assertEqual(len(second), 3)
            self.assertEqual([item['payload']['operation_id'] for item in first + second], ids)
            self.assertEqual(len(reader.resolve_objects([('releases', SHA)])), len(ids))

    def test_reader_snapshot_stays_stable(self):
        with store.Store() as writer:
            opid = self.finish(writer)
        with store.read_session() as reader:
            original = reader.get(opid)
            self.assertEqual(reader.get(opid), original)
            self.assertTrue(reader.connection.in_transaction)

    def test_deleted_database_is_not_silently_recreated(self):
        (self.root / 'v2/index.sqlite3').unlink()
        with self.assertRaisesRegex(store.StoreError, 'missing_existing_database'):
            store.Store(create=True)
        self.assertFalse((self.root / 'v2/index.sqlite3').exists())

    def test_hardlink_database_is_rejected(self):
        alias = self.root / 'alias'
        os.link(self.root / 'v2/index.sqlite3', alias)
        with self.assertRaisesRegex(store.StoreError, 'exclusive_regular'):
            store.Store()

    @unittest.skipIf(os.name == 'nt', 'POSIX owner/mode and symlink boundary')
    def test_symlink_foreign_owner_and_unsafe_modes_rejected(self):
        database = self.root / 'v2/index.sqlite3'
        database.chmod(0o644)
        with self.assertRaisesRegex(store.StoreError, 'owner_or_mode'):
            store.Store()
        database.chmod(0o600)
        with patch.object(store, '_uid', return_value=os.getuid() + 1):
            with self.assertRaisesRegex(store.StoreError, 'owner_or_mode'):
                store.Store()
        path = self.root / 'v2/index.sqlite3-journal'
        path.symlink_to(database)
        with self.assertRaisesRegex(store.StoreError, 'exclusive_regular'):
            store.Store()

    def test_arbitrary_object_keys_and_policy_inputs_rejected(self):
        with store.Store() as writer:
            opid = writer.begin(active)
            for key in [('releases', '../escape'), ('staging', '/tmp/object'), ('anything', SHA)]:
                with self.assertRaisesRegex(store.StoreError, 'object_key_invalid'):
                    writer.resolve_objects([key])
            self.assertEqual(writer.get_active(opid)['operation_id'], opid)
            for length in (8, 16, 32):
                self.assertEqual(writer.resolve_objects([('staging', '.prepare-' + SHA + '.' + 'x' * length)]), [])
            with self.assertRaisesRegex(store.StoreError, 'object_key_invalid'):
                writer.resolve_objects([('staging', '.prepare-' + SHA + '.' + 'x' * 33)])

    def test_twenty_thousand_successes_are_bounded_and_resolvable(self):
        start = time.monotonic()
        with store.Store() as writer:
            reference = self.finish(writer, 'A', [('releases', SHA), ('images', 'sha256:' + 'c' * 64)])
            incident = self.finish(writer, 'B', state='failed', rollback='failed')
            running = writer.begin(active)
            # Exercise the exact 40,000 begin/finish state transitions. Fixture
            # transactions use savepoints within batches of 500 operations to
            # avoid 40,000 fsyncs. Production FULL commit and actual process
            # crashes have independent tests above; no production batching API.
            @contextmanager
            def nested():
                writer.connection.execute('SAVEPOINT fixture')
                try:
                    yield
                    writer.connection.execute('RELEASE fixture')
                except BaseException:
                    writer.connection.execute('ROLLBACK TO fixture')
                    writer.connection.execute('RELEASE fixture')
                    raise
            for _ in range(40):
                writer.connection.execute('BEGIN IMMEDIATE')
                try:
                    with patch.object(writer, 'transaction', nested):
                        for _ in range(500):
                            self.finish(writer)
                    writer.connection.execute('COMMIT')
                except BaseException:
                    writer.connection.execute('ROLLBACK')
                    raise
        build_seconds = time.monotonic() - start
        tracemalloc.start()
        read_start = time.monotonic()
        with store.read_session() as reader:
            summary = reader.summary()
            recent = reader.recent()
            incidents = reader.incidents()
            resolved = reader.resolve_objects([('releases', SHA)])
            active_rows = reader.active()
            rollups = reader.rollups()
        read_seconds = time.monotonic() - read_start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(summary['counters']['C_total'], 20000)
        self.assertEqual(summary['counters']['recent'], store.RECENT_LIMIT)
        self.assertEqual(summary['counters']['A'], 1)
        self.assertEqual(summary['counters']['B'], 1)
        self.assertEqual(summary['counters']['active'], 1)
        self.assertEqual(summary['counters']['rollups'], 1)
        self.assertEqual(rollups[0]['count'], 20000)
        self.assertEqual(len(recent), store.RECENT_LIMIT)
        self.assertEqual(resolved[0]['payload']['operation_id'], reference)
        self.assertEqual(incidents[0]['payload']['operation_id'], incident)
        self.assertEqual(active_rows[0]['payload']['operation_id'], running)
        self.assertLess(peak, 8 * 1024 * 1024)
        self.assertLess(read_seconds, 10)
        self.assertLess(summary['database_bytes'], 2 * 1024 * 1024)
        print('STORE_STRESS ' + json.dumps(dict(operations=20000, build_seconds=round(build_seconds, 3),
            reader_seconds=round(read_seconds, 4), reader_peak_bytes=peak, database_bytes=summary['database_bytes'],
            recent_raw=len(recent), active=len(active_rows), persistent=1, incidents=1, rollups=len(rollups)), sort_keys=True))


if __name__ == '__main__':
    unittest.main()
