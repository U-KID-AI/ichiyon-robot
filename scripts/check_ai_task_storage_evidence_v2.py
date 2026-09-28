"""Offline frontend/store integration with isolated fixture directories only."""
from contextlib import closing, contextmanager
import copy
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
import tracemalloc
import types
import unittest
from unittest.mock import patch

import ai_task_storage_evidence as evidence
import ai_task_storage_evidence_classification as classification
import ai_task_storage_evidence_store as store
import ai_task_storage_retention_graph as graph
from check_ai_task_storage_evidence import Host, TARGET, PREVIOUS
from check_ai_task_storage_cleanup import fixture as cleanup_fixture
from check_ai_task_storage_retention import filesystem_state, forbid_mutations


IMAGE = 'sha256:' + '1' * 64
MIGRATION = '2' * 64
REAL_CLASSIFY = classification.classify


def classify_fixture_paths(payload, observation):
    """Translate only temporary fixture roots at the pure classifier boundary.

    The real frontend has already verified and observed these local paths. Its
    production path policy is independently tested by the classifier suite. This
    adapter lets the same native filesystem integration run on NT and POSIX;
    it never changes classification, identities, lifecycle or absence flags.
    """
    payload, observation = copy.deepcopy((payload, observation))
    for rows in (payload['staging'], observation['stages']):
        for row in rows:
            row['path'] = classification.ROOTS[row['kind']] + '/' + Path(row['path']).name
    return REAL_CLASSIFY(payload, observation)


class V2FrontendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.host = Host(Path(self.directory.name).resolve())

    def tearDown(self):
        self.directory.cleanup()

    @contextmanager
    def frontend(self):
        with self.host.patched(legacy=False), patch.object(classification, 'classify', side_effect=classify_fixture_paths):
            yield

    def configure_same_sha(self):
        self.host.runtime = dict(target_release=evidence._directory_identity(self.host.releases / PREVIOUS),
            target_image=dict(id=IMAGE, revision=PREVIOUS), image_ids=[IMAGE],
            image_tags={'ichiyon-robot-app:' + PREVIOUS: IMAGE}, migration_container=None,
            observation_complete=True)

    def stage(self, opid, target=PREVIOUS, kind='prepare', suffix='abcdefgh'):
        root = self.host.backups if kind == 'backup' else self.host.releases
        path = root / ('.' + kind + '-' + target + '.' + suffix)
        path.mkdir(mode=0o700)
        evidence.record_stage(opid, 123, kind, str(path))
        return path

    def finish_noop(self, health_retry=False, keep_stage=False, ancillary=True):
        self.configure_same_sha()
        opid = evidence.begin(PREVIOUS, 123)
        stage = self.stage(opid)
        evidence.observe(opid, 123, 'reconcile')
        if health_retry:
            evidence.observe(opid, 123, 'health_retry')
        evidence.observe(opid, 123, 'health')
        if not keep_stage:
            stage.rmdir()  # This isolated empty fixture stage belongs to this test.
        evidence.observe(opid, 123, 'cleanup')
        evidence.observe(opid, 123, 'ancillary_cleanup')
        with patch.object(evidence, '_ancillary_absent', return_value=ancillary):
            result = evidence.finish(opid, 123, 'succeeded', 'not_needed')
        return opid, stage, result

    def snapshot(self, stage):
        result = cleanup_fixture()
        result['staging'][0].update(id=str(stage), path=str(stage), operation_sha=TARGET)
        return result

    def collect(self, stage):
        snapshot = self.snapshot(stage)
        with patch.object(evidence, '_owner_state', side_effect=lambda _, terminal:
                          'terminated_identity_verified' if terminal else 'alive'):
            evidence.enrich_snapshot(snapshot)
        return snapshot, graph.build_plan(snapshot)

    def test_real_begin_observe_finish_classifies_and_rolls_up_c(self):
        with self.frontend():
            opid, stage, result = self.finish_noop()
            self.assertEqual(result['classification'], 'C')
            self.assertFalse(stage.exists())
            with store.read_session() as reader:
                receipt = reader.get(opid)
                self.assertEqual(receipt['classification'], 'C')
                self.assertEqual(receipt['object_keys'], [])
                self.assertEqual(reader.active(), [])
                self.assertEqual(reader.rollups()[0]['count'], 1)
                self.assertEqual(reader.summary()['counters']['C_total'], 1)
                evidence._indexed_payload(receipt)
            self.assertFalse((self.host.evidence / 'operations').exists())
            self.assertFalse((self.host.evidence / 'active').exists())

    def test_health_retry_stays_lossless_b_and_not_success_rollup(self):
        with self.frontend():
            opid, _, result = self.finish_noop(health_retry=True)
            self.assertEqual(result['classification'], 'B')
            with store.read_session() as reader:
                self.assertEqual(reader.rollups(), [])
                self.assertEqual(reader.incidents()[0]['payload']['operation_id'], opid)
                self.assertIn('health_retry', [item['phase'] for item in reader.get(opid)['payload']['events']])

    def test_ancillary_failure_retains_b_without_discarding_active_history(self):
        with self.frontend():
            opid, _, result = self.finish_noop(ancillary=False)
            self.assertEqual(result['classification'], 'B')
            with store.read_session() as reader:
                receipt = reader.get(opid)
                self.assertTrue(receipt['active_payload']['staging'])
                self.assertIsNone(receipt['proof'])
                self.assertEqual(reader.summary()['counters']['B'], 1)

    def test_remaining_stage_keeps_a_and_index_resolves_exact_receipt(self):
        with self.frontend():
            opid, stage, result = self.finish_noop(keep_stage=True)
            self.assertEqual(result['classification'], 'A')
            with store.read_session() as reader:
                resolved = reader.resolve_objects([('staging', stage.name)])
                self.assertEqual(resolved[0]['payload']['operation_id'], opid)
                self.assertEqual(reader.rollups(), [])

    def test_failed_new_deployment_indexes_long_suffix_stage(self):
        with self.frontend():
            self.host.runtime['image_tags'] = {}
            opid = evidence.begin(TARGET, 123)
            stage = self.stage(opid, TARGET, kind='backup', suffix='abcdefghijklmnop')
            result = evidence.finish(opid, 123, 'failed', 'not_attempted')
            self.assertEqual(result['classification'], 'B')
            with store.read_session() as reader:
                self.assertEqual(reader.resolve_objects([('staging', stage.name)])[0]['payload']['operation_id'], opid)
                self.assertEqual(reader.resolve_objects([('backups', TARGET)])[0]['payload']['operation_id'], opid)

    def test_unavailable_runtime_preserves_terminal_failure(self):
        with self.frontend():
            self.host.runtime.update(image_tags={}, observation_complete=False)
            opid = evidence.begin(TARGET, 123)
            result = evidence.finish(opid, 123, 'failed', 'unknown')
            self.assertEqual(result['classification'], 'B')
            with store.read_session() as reader:
                self.assertFalse(reader.get(opid)['payload']['runtime']['observation_complete'])

    def test_final_raw_migration_absence_does_not_erase_historical_id(self):
        with self.frontend():
            self.configure_same_sha()
            opid = evidence.begin(PREVIOUS, 123)
            stage = self.stage(opid)
            evidence.observe(opid, 123, 'reconcile')
            self.host.runtime['migration_container'] = dict(id=MIGRATION, image_id=IMAGE, status='exited',
                                                            name='ichiyon-robot-migrate-' + PREVIOUS)
            evidence.observe(opid, 123, 'health')
            self.host.runtime['migration_container'] = None
            stage.rmdir()
            evidence.observe(opid, 123, 'cleanup')
            evidence.observe(opid, 123, 'ancillary_cleanup')
            with patch.object(evidence, '_ancillary_absent', return_value=True):
                result = evidence.finish(opid, 123, 'succeeded', 'not_needed')
            self.assertEqual(result['classification'], 'C')
            with store.read_session() as reader:
                receipt = reader.get(opid)
                self.assertEqual(receipt['payload']['runtime']['migration_container']['id'], MIGRATION)
                self.assertEqual(receipt['object_keys'], [])

    def test_owner_identity_changed_before_finish_cannot_publish(self):
        with self.frontend():
            self.host.runtime['image_tags'] = {}
            opid = evidence.begin(TARGET, 123)
            changed = dict(self.host.owner, start_ticks=self.host.owner['start_ticks'] + 1)
            with patch.object(evidence, '_owner', return_value=(changed, self.host.lock_identity)):
                with self.assertRaisesRegex(evidence.EvidenceError, 'owner_or_lock_changed'):
                    evidence.finish(opid, 123, 'failed', 'unknown')
            with store.read_session() as reader:
                self.assertEqual(reader.active()[0]['payload']['operation_id'], opid)
                self.assertEqual(reader.summary()['counters']['B'], 0)

    def test_final_stage_absence_requires_present_parent(self):
        with self.frontend():
            self.host.runtime['image_tags'] = {}
            opid = evidence.begin(TARGET, 123)
            stage = self.stage(opid, TARGET, kind='backup')
            with store.read_session() as reader:
                payload = reader.get_active(opid)
            stage.rmdir()
            self.host.backups.rmdir()
            with patch.object(evidence, '_ancillary_absent', return_value=False):
                observed = evidence._final_observation(payload, copy.deepcopy(self.host.runtime), 123)
            self.assertEqual(observed['stages'][0]['status'], 'unknown')

    def test_lstat_or_identity_failure_is_unknown_not_absent(self):
        with self.frontend():
            self.host.runtime['image_tags'] = {}
            opid = evidence.begin(TARGET, 123)
            self.stage(opid, TARGET)
            with store.read_session() as reader:
                payload = reader.get_active(opid)
            with patch.object(evidence, '_directory_identity', side_effect=evidence.EvidenceError('directory_alias')):
                observed = evidence._final_observation(payload, copy.deepcopy(self.host.runtime), 123)
            self.assertEqual(observed['stages'][0]['status'], 'unknown')
            self.assertIsNone(observed['stages'][0]['identity'])

    @unittest.skipIf(os.name == 'nt', 'POSIX no-follow symlink and mode contract is tested in Linux CI')
    def test_replaced_stage_symlink_is_unknown_and_target_is_not_followed(self):
        with self.frontend():
            self.host.runtime['image_tags'] = {}
            opid = evidence.begin(TARGET, 123)
            stage = self.stage(opid, TARGET)
            with store.read_session() as reader:
                payload = reader.get_active(opid)
            stage.rmdir()
            stage.symlink_to(self.host.releases / PREVIOUS, target_is_directory=True)
            observed = evidence._final_observation(payload, copy.deepcopy(self.host.runtime), 123)
            self.assertEqual(observed['stages'][0]['status'], 'unknown')
            self.assertTrue((self.host.releases / PREVIOUS / 'REVISION').is_file())

    def test_ancillary_proof_requires_deleted_owned_regular_helper_inode(self):
        with self.frontend():
            name = '/home/ubuntu/.ichiyon-deploy-helper.abcdefgh.py (deleted)'
            good = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=0, st_uid=self.host.owner['uid'])
            with patch.object(evidence.os, 'readlink', return_value=name), patch.object(Path, 'stat', return_value=good):
                self.assertTrue(evidence._ancillary_absent(123))
            for changed in (dict(st_nlink=1), dict(st_uid=self.host.owner['uid'] + 1), dict(st_mode=stat.S_IFDIR | 0o700)):
                fields = dict(st_mode=good.st_mode, st_nlink=good.st_nlink, st_uid=good.st_uid)
                fields.update(changed)
                with patch.object(evidence.os, 'readlink', return_value=name), patch.object(Path, 'stat', return_value=types.SimpleNamespace(**fields)):
                    self.assertFalse(evidence._ancillary_absent(123))
            with patch.object(evidence.os, 'readlink', return_value=name.replace(' (deleted)', '')):
                self.assertFalse(evidence._ancillary_absent(123))

    def test_legacy_receipt_bytes_preserved_across_new_index_publication(self):
        with self.host.patched():
            legacy = evidence.begin(TARGET, 123)
            evidence.finish(legacy, 123, 'failed', 'unknown')
            before = {path: path.read_bytes() for path in self.host.evidence.rglob('*.json')}
        with self.frontend():
            _, _, result = self.finish_noop()
            self.assertEqual(result['classification'], 'C')
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(self.host.terminal(legacy)['state'], 'failed')

    def test_recent_success_bound_does_not_retain_flat_json_generations(self):
        with self.frontend(), patch.object(store, 'RECENT_LIMIT', 2):
            ids = [self.finish_noop()[0] for _ in range(5)]
            with store.read_session() as reader:
                self.assertEqual(len(reader.recent()), 2)
                self.assertEqual(reader.rollups()[0]['count'], 5)
                self.assertEqual(reader.summary()['counters']['C_total'], 5)
                with self.assertRaisesRegex(store.StoreError, 'missing_or_compacted'):
                    reader.get(ids[0])
            self.assertEqual(list(self.host.evidence.rglob('*.json')), [])

    def test_indexed_object_collector_is_readonly_and_cannot_upgrade_cleanup(self):
        with self.frontend():
            opid, stage, result = self.finish_noop(keep_stage=True)
            self.assertEqual(result['classification'], 'A')
            before = filesystem_state(self.host.home)
            with forbid_mutations(), patch('subprocess.run', side_effect=AssertionError('process')):
                snapshot, planned = self.collect(stage)
            self.assertEqual(filesystem_state(self.host.home), before)
            self.assertEqual(snapshot['durable_operations']['errors'], [])
            self.assertEqual(snapshot['staging'][0]['operation_id'], opid)
            self.assertTrue(snapshot['staging'][0]['ownership_verified'])
            self.assertEqual(snapshot['cleanup_evidence']['records'][0]['source_format'], 'indexed-v2')
            self.assertEqual(planned['nodes']['staging'][0]['operation_id'], opid)
            self.assertFalse(any(node['classification'] == 'SAFE_TO_CLEAN'
                                 for rows in planned['cleanup']['nodes'].values() for node in rows))

    def test_corrupt_index_prevents_object_binding_and_cleanup_upgrade(self):
        with self.frontend():
            _, stage, _ = self.finish_noop(keep_stage=True)
            with closing(sqlite3.connect(self.host.evidence / 'v2/index.sqlite3')) as database:
                with database:
                    database.execute("UPDATE object_index SET checksum=?", ('0' * 64,))
            before = filesystem_state(self.host.home)
            with forbid_mutations(), patch('subprocess.run', side_effect=AssertionError('process')):
                snapshot, planned = self.collect(stage)
            self.assertEqual(filesystem_state(self.host.home), before)
            self.assertIn('INDEXED_EVIDENCE_INVALID_OR_UNAVAILABLE', snapshot['durable_operations']['errors'])
            self.assertFalse(snapshot['cleanup_evidence']['source_verified'])
            self.assertNotIn('operation_id', snapshot['staging'][0])
            self.assertFalse(any(node['classification'] == 'SAFE_TO_CLEAN'
                                 for rows in planned['cleanup']['nodes'].values() for node in rows))

    def test_index_replacement_after_read_before_binding_loses_ownership_proof(self):
        with self.frontend():
            _, stage, _ = self.finish_noop(keep_stage=True)
            database = self.host.evidence / 'v2/index.sqlite3'
            replacement = self.host.home / 'fixture-replacement.sqlite3'
            replacement.write_bytes(database.read_bytes())
            replacement.chmod(0o600)
            bound = evidence._bound
            replaced = []

            def swap(payload, category, node):
                result = bound(payload, category, node)
                if result and not replaced:
                    os.replace(str(replacement), str(database))
                    replaced.append(True)
                return result

            with patch.object(evidence, '_bound', side_effect=swap):
                snapshot, planned = self.collect(stage)
            self.assertEqual(replaced, [True])
            self.assertIn('INDEXED_EVIDENCE_CHANGED', snapshot['durable_operations']['errors'])
            self.assertFalse(snapshot['cleanup_evidence']['source_verified'])
            self.assertFalse(snapshot['staging'][0]['ownership_verified'])
            self.assertEqual(snapshot['staging'][0]['ownership'], 'unverified_evidence_race')
            record = snapshot['cleanup_evidence']['records'][0]
            self.assertFalse(record['source_verified'])
            self.assertFalse(record['source_stable'])
            self.assertEqual(planned['cleanup']['nodes']['staging'][0]['classification'], 'NEEDS_REVIEW')

    def test_recent_c_and_compacted_rollup_cannot_bind_recreated_stage(self):
        with self.frontend(), patch.object(store, 'RECENT_LIMIT', 1):
            _, stage, _ = self.finish_noop()
            self.finish_noop()
            stage.mkdir(mode=0o700)  # A new object at the old path is not owned by either C.
            snapshot, planned = self.collect(stage)
            self.assertEqual(snapshot['durable_operations']['indexed']['counters']['C_total'], 2)
            self.assertEqual(snapshot['durable_operations']['operations'], [])
            self.assertEqual(snapshot['cleanup_evidence']['records'], [])
            self.assertNotIn('operation_id', snapshot['staging'][0])
            self.assertFalse(any(node['classification'] == 'SAFE_TO_CLEAN'
                                 for rows in planned['cleanup']['nodes'].values() for node in rows))

    def test_twenty_thousand_full_receipts_keep_frontend_reader_and_planner_bounded(self):
        with self.frontend():
            self.host.runtime['image_tags'] = {}
            object_opid = evidence.begin(TARGET, 123)
            object_stage = self.stage(object_opid, TARGET, suffix='objectaa')
            self.assertEqual(evidence.finish(object_opid, 123, 'succeeded', 'not_needed')['classification'], 'A')
            failed_opid = evidence.begin(TARGET, 123)
            self.stage(failed_opid, TARGET, kind='backup', suffix='incident')
            self.assertEqual(evidence.finish(failed_opid, 123, 'failed', 'failed')['classification'], 'B')
            active_opid = evidence.begin(TARGET, 123)
            self.stage(active_opid, TARGET, suffix='activeaa')
            noop_opid, _, _ = self.finish_noop()
            with store.read_session() as reader:
                exemplar = reader.get(noop_opid)

            def factory(opid):
                active = copy.deepcopy(exemplar['active_payload'])
                active.update(operation_id=opid, sequence=1)
                return evidence._validate(active, opid, False)

            def populate(count):
                # Exercise real begin/finish serialization, checksum, index and
                # compaction transitions with fully frontend-valid receipts.
                # Offline-only savepoint batching avoids 40,000 host fsyncs;
                # production uses FULL commits, covered by store crash tests.
                with store.Store() as writer:
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
                    for offset in range(0, count, 500):
                        writer.connection.execute('BEGIN IMMEDIATE')
                        try:
                            with patch.object(writer, 'transaction', nested):
                                for _ in range(min(500, count - offset)):
                                    opid = writer.begin(factory)
                                    active = writer.get_active(opid)
                                    terminal = copy.deepcopy(exemplar['payload'])
                                    terminal.update(operation_id=opid, sequence=2,
                                                    active_payload_sha256=evidence.digest(active))
                                    evidence._validate(terminal, opid, True)
                                    writer.finish(terminal, 'C', proof=exemplar['proof'])
                            writer.connection.execute('COMMIT')
                        except BaseException:
                            writer.connection.execute('ROLLBACK')
                            raise

            def measure():
                before = filesystem_state(self.host.home)
                tracemalloc.start()
                start = time.monotonic()
                try:
                    with forbid_mutations(), patch('subprocess.run', side_effect=AssertionError('process')):
                        snapshot, planned = self.collect(object_stage)
                    elapsed = time.monotonic() - start
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertEqual(filesystem_state(self.host.home), before)
                self.assertEqual(snapshot['durable_operations']['errors'], [])
                self.assertEqual(snapshot['staging'][0]['operation_id'], object_opid)
                self.assertTrue(snapshot['staging'][0]['ownership_verified'])
                observed = {row['operation_id'] for row in snapshot['durable_operations']['operations']}
                self.assertTrue({object_opid, failed_opid, active_opid} <= observed)
                classification_rows = {category: [(node['id'], node['classification']) for node in rows]
                                       for category, rows in planned['cleanup']['nodes'].items()}
                self.assertFalse(any(state == 'SAFE_TO_CLEAN' for rows in classification_rows.values()
                                     for _, state in rows))
                return snapshot, classification_rows, elapsed, peak

            start = time.monotonic()
            populate(63)
            small, small_classes, small_seconds, small_peak = measure()
            populate(20000 - 64)
            build_seconds = time.monotonic() - start
            large, large_classes, large_seconds, large_peak = measure()
            counters = large['durable_operations']['indexed']['counters']
            self.assertEqual(counters['C_total'], 20000)
            self.assertEqual(counters['recent'], store.RECENT_LIMIT)
            self.assertEqual((counters['A'], counters['B'], counters['active'], counters['rollups']), (1, 1, 1, 1))
            self.assertEqual(small_classes, large_classes)
            self.assertLess(large_peak, small_peak * 3 + 1024 * 1024)
            self.assertLess(large_peak, 64 * 1024 * 1024)
            self.assertLess(large_seconds, max(20, small_seconds * 10))
            self.assertEqual(list(self.host.evidence.rglob('*.json')), [])
            print(json.dumps(dict(test='full_frontend_20000', build_seconds=round(build_seconds, 3),
                reader_64_seconds=round(small_seconds, 3), reader_20000_seconds=round(large_seconds, 3),
                reader_64_peak_bytes=small_peak, reader_20000_peak_bytes=large_peak,
                recent=counters['recent'], rollups=counters['rollups'], incidents=counters['B'],
                persistent=counters['A'], active=counters['active']), sort_keys=True))


if __name__ == '__main__':
    unittest.main()
