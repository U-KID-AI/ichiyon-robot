"""Console queue completion checks; these do not claim a live world migration."""
from pathlib import Path
from copy import deepcopy
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

from junket_native_release import (confirmed_blocks, wait_console,
                                   correction_operations, require_same_physical_layout,
                                   recover_native_lineage, recovered_native_active, WORLD, JUNKET)


class ConsoleTests(unittest.TestCase):
    def test_waits_for_every_coordinate_not_duplicate_count(self):
        a = 'Successfully found the block at 20096,319,20064.\n'
        b = 'Successfully found the block at 20112,319,20064.\n'
        responses = iter([a + a, a + b])
        calls = []
        def run(args):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=next(responses), stderr='')
        api = SimpleNamespace(CONTAINER_NAME='fixed-container', run_fixed=run)
        expected = {(20096, 319, 20064), (20112, 319, 20064)}
        with TemporaryDirectory() as directory, patch('junket_native_release.time.sleep') as sleep:
            log = Path(directory) / 'console.log'
            wait_console(api, 'precise-time', log, lambda out: expected <= confirmed_blocks(out))
            self.assertEqual(confirmed_blocks(log.read_text()), expected)
            self.assertEqual(len(calls), 2)
            sleep.assert_called_once_with(2)
            self.assertEqual(calls[0], ['docker', 'logs', '--since', 'precise-time', 'fixed-container'])

    def test_partial_or_failed_logs_never_confirm(self):
        api = SimpleNamespace(CONTAINER_NAME='fixed-container', run_fixed=lambda args:
                              SimpleNamespace(returncode=1, stdout='complete', stderr='failure'))
        with TemporaryDirectory() as directory:
            log = Path(directory) / 'failed.log'
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                wait_console(api, 'time', log, lambda out: True, timeout=0)
            self.assertEqual(log.read_text(), 'completefailure')

    def test_only_success_response_coordinates_are_accepted(self):
        text = ('testforblock 20096 319 20064 air\n'
                'Successfully found the block at -1, 319, 42.\n'
                'The block at 20096,319,20064 did not match the expected block type.\n')
        self.assertEqual(confirmed_blocks(text), {(-1, 319, 42)})


class CorrectionTests(unittest.TestCase):
    def test_pack_only_keeps_latest_warp_proof_not_original_direct_tp(self):
        building = '10000000-0000-4000-8000-000000000002'
        warp = '10000000-0000-4000-8000-000000000003'
        next_operation = '10000000-0000-4000-8000-000000000004'
        active = {'native_world_operation': {'world_operation_id': building, 'warp_operation_id': warp}}
        self.assertEqual(correction_operations(active, next_operation, False), (building, warp))
        self.assertEqual(correction_operations(active, next_operation, True), (building, next_operation))
        del active['native_world_operation']['warp_operation_id']
        self.assertEqual(correction_operations(active, next_operation, False), (building, building))

    def test_correction_rejects_building_floor_and_arrival_changes(self):
        original = {'games': [{'id': 'sparrow', 'production_origin': [20000, 65, 20000],
            'size': [32, 32, 32], 'destination': [20022, 67, 20021], 'block_overlays': [],
            'structures': [{'path': '/old/place', 'sha256': 'source-hash', 'offset': [0, 0, 0]}]}]}
        relocated_file = deepcopy(original)
        relocated_file['games'][0]['structures'][0]['path'] = '/new/bundle/place'
        require_same_physical_layout(relocated_file, original)
        for key, value in [('production_origin', [1, 65, 1]), ('destination', [20022, 68, 20021]),
                           ('block_overlays', [{'location': [0, 0, 0], 'block': 'air'}])]:
            changed = deepcopy(original)
            changed['games'][0][key] = value
            with self.assertRaises(ValueError):
                require_same_physical_layout(changed, original)
        changed = deepcopy(original)
        changed['games'][0]['structures'][0]['sha256'] = 'different-building'
        with self.assertRaises(ValueError):
            require_same_physical_layout(changed, original)


class NativeLineageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.operation = '10000000-0000-4000-8000-000000000005'
        self.hashes = dict(zip(JUNKET, ('a' * 64, 'b' * 64)))
        self.active = {'operation_id': '10000000-0000-4000-8000-000000000006',
                       'catalog_digest': 'c' * 64,
                       'packs': [{'path': key, 'content_hash': value} for key, value in self.hashes.items()]
                                + [{'path': 'resource_packs/other_' + str(i), 'content_hash': 'd' * 64}
                                   for i in range(11)]}
        self.marker = {'schema': 1, 'operation_id': self.operation,
                       'world_operation_id': self.operation, 'warp_operation_id': self.operation,
                       'native_source_bundle_sha256': 'e' * 64, 'packs': self.hashes}
        self.marker_path = self.project / 'junket-native-overlay.json'
        self.latest_path = self.project / 'junket-world-applications/latest.json'
        self.proof_path = self.project / 'junket-world-applications' / self.operation / 'native-release-proof.json'
        self.preflight_path = self.proof_path.with_name('preflight.json')
        self.write(self.marker_path, self.marker)
        self.write(self.latest_path, {'operation_id': self.operation, 'status': 'succeeded'})
        self.write(self.proof_path, {'operation_id': self.operation, 'status': 'succeeded',
                   'world': WORLD, 'installed': True, 'world_placement_operation': self.operation,
                   'warp_operation': self.operation})
        self.write(self.preflight_path, {'world': WORLD, 'bundle_report_sha256': 'e' * 64,
                   'world_placement_operation': self.operation, 'warp_operation': self.operation})
        self.validator = Mock(return_value={'schema': 1, 'operation_id': self.operation, 'packs': self.hashes})

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def recover(self, installed=None):
        return recover_native_lineage(self.active, self.project,
                                      self.hashes if installed is None else installed, self.validator)

    def test_verified_overlay_recovers_only_metadata_without_mutating_any_pack_or_active(self):
        before_active = deepcopy(self.active)
        before_files = {str(p): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        recovery = self.recover()
        adopted = recovered_native_active(self.active, recovery)
        self.validator.assert_called_once_with(self.project)
        self.assertEqual(adopted['native_world_operation'], self.marker)
        self.assertEqual(adopted['packs'], before_active['packs'])
        self.assertEqual(len(adopted['packs']), 13)
        self.assertEqual(adopted['operation_id'], before_active['operation_id'])
        self.assertEqual(self.active, before_active)
        self.assertEqual({str(p): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}, before_files)
        self.assertEqual(len(recovery['verified_files']), 4)
        self.assertEqual(recovered_native_active(before_active, json.loads(json.dumps(recovery))), adopted)

    def test_rejects_installed_pack_or_managed_baseline_drift(self):
        with self.assertRaisesRegex(ValueError, 'installed managed pack'):
            self.recover({JUNKET[0]: 'f' * 64, JUNKET[1]: self.hashes[JUNKET[1]]})
        self.active['packs'][0]['content_hash'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'installed managed pack'):
            self.recover()

    def test_existing_stage_guard_failure_is_not_bypassed(self):
        self.validator.side_effect = ValueError('Pinned JUNKET source fingerprint mismatch')
        with self.assertRaisesRegex(ValueError, 'source fingerprint mismatch'):
            self.recover()

    def test_rejects_unowned_pack_and_invalid_or_different_marker(self):
        for change in ({'packs': {**self.hashes, 'resource_packs/other': 'a' * 64}},
                       {'operation_id': '../../other'}, {'native_source_bundle_sha256': ''},
                       {'schema': 2}):
            with self.subTest(change=change):
                self.write(self.marker_path, {**self.marker, **change})
                with self.assertRaises((ValueError, KeyError)):
                    self.recover()

    def test_rejects_incomplete_wrong_world_or_mismatching_lineage_evidence(self):
        variants = [(self.latest_path, 'status', 'running'),
                    (self.latest_path, 'operation_id', self.active['operation_id']),
                    (self.proof_path, 'status', 'failed'), (self.proof_path, 'installed', False),
                    (self.proof_path, 'world', 'unrelated-world'),
                    (self.proof_path, 'world_placement_operation', self.active['operation_id']),
                    (self.proof_path, 'warp_operation', self.active['operation_id']),
                    (self.preflight_path, 'bundle_report_sha256', 'f' * 64),
                    (self.preflight_path, 'warp_operation', self.active['operation_id'])]
        for path, key, value in variants:
            with self.subTest(path=path.name, key=key):
                original = path.read_bytes()
                record = json.loads(original)
                record[key] = value
                self.write(path, record)
                try:
                    with self.assertRaises(ValueError):
                        self.recover()
                finally:
                    path.write_bytes(original)

    def test_resume_rejects_recovery_from_another_managed_baseline(self):
        recovery = self.recover()
        changed = deepcopy(self.active)
        changed['operation_id'] = self.operation
        with self.assertRaisesRegex(ValueError, 'another managed operation'):
            recovered_native_active(changed, recovery)
        changed = deepcopy(self.active)
        changed['packs'][0]['content_hash'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'saved managed baseline'):
            recovered_native_active(changed, recovery)

    def test_lineage_carries_verified_static_overlay_and_rejects_changed_pin(self):
        pin = {'manifest_sha256': '1' * 64}
        self.marker['schema'] = 2
        self.marker['static_overlay'] = pin
        self.write(self.marker_path, self.marker)
        self.validator.return_value['schema'] = 2
        self.validator.return_value['static_overlay'] = pin
        preflight = json.loads(self.preflight_path.read_text())
        preflight['static_overlay'] = pin
        self.write(self.preflight_path, preflight)
        recovered = self.recover()['native_world_operation']
        self.assertEqual(recovered['schema'], 2)
        self.assertEqual(recovered['static_overlay'], pin)
        preflight['static_overlay'] = {'manifest_sha256': '2' * 64}
        self.write(self.preflight_path, preflight)
        with self.assertRaisesRegex(ValueError, 'static overlay differs'):
            self.recover()

    def test_static_lineage_rejects_schema_downgrade_before_validation(self):
        self.marker['static_overlay'] = {'manifest_sha256': '1' * 64}
        self.write(self.marker_path, self.marker)
        with self.assertRaisesRegex(ValueError, 'requires pinned JUNKET overlay schema 2'):
            self.recover()
        self.validator.assert_not_called()

    def test_schema_two_lineage_requires_static_pin_before_validation(self):
        self.marker['schema'] = 2
        self.write(self.marker_path, self.marker)
        with self.assertRaisesRegex(ValueError, 'schema 2 requires static_overlay'):
            self.recover()
        self.validator.assert_not_called()


if __name__ == '__main__':
    unittest.main()
