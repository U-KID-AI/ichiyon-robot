"""BDS evidence parsing: partial reports must remain partial."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from check_junket_native_runtime import (
    EXPECTED_IDS, WARP_MARKER, check_warp_destinations, parse_runtime_reports,
    parse_tickingarea_listing, parse_warp_reports, runtime_errors, validate_warp_report,
    QUALITY_STAGES, validate_quality_report,
    HEALTH_RECONCILE_MARKER, parse_fixture_health_reconciliations,
    validate_resilience_report, resilience_path_counts,
    panel_initialization_evidence, PANEL_MARKER,
)


class ResilienceEvidenceTests(unittest.TestCase):
    def report(self):
        checks = [{'id': gid, 'ok': True, 'fixture_count': 10,
                   'access': {'server_actor_collision_checks': True, 'player_walk_test': False, 'checked_path_cells': 12},
                   'controls': [{'action': action, 'stand_clear': True, 'sign_text': action,
                       'handler_bound': True, 'legacy_controls': [{'action': old, 'status': 'restored'}
                            for old in ('reception', 'return', 'reset', 'repair')]}
                                for action in ('menu',)],
                   'attack': {'tested': 10, 'health_identity_position_preserved': True},
                   'physics': {'tested': 10, 'immediate_velocity_max': .3,
                               'anchor_position_restored': True, 'final_velocity_zero': True},
                   'recovery': {field: True for field in ('deleted_restored', 'displacement_restored',
                       'duplicates_removed', 'original_block_restored', 'controls_restored', 'foreign_actor_preserved')},
                   'idempotent': True} for gid in EXPECTED_IDS]
        return {'schema': 1, 'request_id': 'test', 'phase': 'destructive', 'ok': True,
                'failures': [], 'player_test': False, 'mobile_test': False, 'checks': checks}

    def validate(self, report, phase='destructive', path_counts=None):
        return validate_resilience_report(report, 'test', dict.fromkeys(EXPECTED_IDS, 10), phase,
                                          expected_path_counts=path_counts if path_counts is not None else dict.fromkeys(EXPECTED_IDS, 12))

    def test_complete_evidence(self):
        self.assertEqual(self.validate(self.report()), [])

    def test_manifest_short_paths_require_every_declared_collision_check(self):
        games = [{'id': gid, 'service_controls': [{'action': 'menu', 'path': [[1, 72, z] for z in range(
            3 if gid in ('walker', 'rabbit') else 6)]}]} for gid in EXPECTED_IDS]
        counts = resilience_path_counts(games)
        report = self.report()
        for check in report['checks']:
            check['access']['checked_path_cells'] = counts[check['id']]
        self.assertEqual(self.validate(report, path_counts=counts), [])
        for actual in (0, 1, 2, 4, 3.0, True, None):
            broken = deepcopy(report)
            next(check for check in broken['checks'] if check['id'] == 'walker')['access']['checked_path_cells'] = actual
            self.assertIn('walker: actual server collision checks incomplete', self.validate(broken, path_counts=counts))
        broken = deepcopy(report)
        next(check for check in broken['checks'] if check['id'] == 'rabbit')['access']['server_actor_collision_checks'] = False
        self.assertIn('rabbit: actual server collision checks incomplete', self.validate(broken, path_counts=counts))

    def test_missing_empty_and_unknown_manifest_paths_are_rejected(self):
        games = [{'id': gid, 'service_controls': [{'action': 'menu', 'path': [[1, 72, 1]]}]} for gid in EXPECTED_IDS]
        self.assertEqual(resilience_path_counts(games), dict.fromkeys(EXPECTED_IDS, 1))
        for case in ('empty', 'missing', 'unknown', 'duplicate', 'invalid-cell'):
            broken = deepcopy(games)
            if case == 'empty': broken[0]['service_controls'][0]['path'] = []
            if case == 'missing': broken.pop()
            if case == 'unknown': broken[0]['id'] = 'unknown'
            if case == 'duplicate': broken[0]['id'] = broken[1]['id']
            if case == 'invalid-cell': broken[0]['service_controls'][0]['path'] = [[1, 72]]
            with self.subTest(case=case), self.assertRaises(ValueError):
                resilience_path_counts(broken)
        for counts in ({}, dict.fromkeys(EXPECTED_IDS, 0), {**dict.fromkeys(EXPECTED_IDS, 12), 'unknown': 12}):
            self.assertTrue(self.validate(self.report(), path_counts=counts))

    def test_partial_existence_only_is_rejected(self):
        report = self.report()
        del report['checks'][0]['physics']
        self.assertTrue(self.validate(report))
        self.assertTrue(self.validate(None))

    def test_each_required_observation_is_enforced(self):
        for group, field in [('attack', 'health_identity_position_preserved'),
                             ('physics', 'final_velocity_zero'), ('recovery', 'original_block_restored'),
                             ('recovery', 'foreign_actor_preserved')]:
            report = self.report()
            report['checks'][0][group][field] = False
            self.assertTrue(self.validate(report), (group, field))

    def test_cannot_claim_mobile_or_skip_restart(self):
        report = self.report()
        report['mobile_test'] = True
        self.assertTrue(self.validate(report))
        report = self.report()
        report['phase'] = 'restart'
        self.assertTrue(self.validate(report, 'restart'))
        for check in report['checks']:
            check['restart_persistence'] = True
        self.assertEqual(self.validate(report, 'restart'), [])


class RuntimeEvidenceTests(unittest.TestCase):
    def test_panel_proof_requires_all17_bound_and_all68_retired(self):
        rows = [{'id': gid, 'controls': 1, 'handler_bound': True, 'originalSupportsPreserved': True,
                 'legacy': [{'action': action, 'status': 'restored'}
                            for action in ('reception', 'return', 'reset', 'repair')]} for gid in EXPECTED_IDS]
        logs = lambda: '\n'.join(PANEL_MARKER + json.dumps(row) for row in rows)
        self.assertTrue(panel_initialization_evidence(logs())['verified'])
        rows[0]['legacy'][0]['status'] = 'retained'
        self.assertFalse(panel_initialization_evidence(logs())['verified'])
        rows[0]['legacy'][0]['status'] = 'restored'
        rows[0]['handler_bound'] = False
        self.assertFalse(panel_initialization_evidence(logs())['verified'])
        rows.pop(0)
        self.assertFalse(panel_initialization_evidence(logs())['verified'])

    def test_preserves_actual_health_migration_evidence(self):
        record = {'id': 'lucy', 'role': 'doll', 'before': 0, 'after': 20,
                  'old_id': 'old', 'new_id': 'new', 'action': 'replace_dead'}
        output = '[INFO] ' + HEALTH_RECONCILE_MARKER + json.dumps(record)
        self.assertEqual(parse_fixture_health_reconciliations(output), [record])

    def test_partial_progress_is_not_a_final_report(self):
        report, progress = parse_runtime_reports(
            '[INFO] [JUNKET ORIGINAL] runtime fixture progress: '
            '{"id":"clock","status":"completed","result":{"entities":26}}')
        self.assertIsNone(report)
        self.assertEqual(progress[0]['id'], 'clock')

    def test_latest_attempt_replaces_previous_progress(self):
        prefix = '[JUNKET ORIGINAL] runtime fixture progress: '
        report, progress = parse_runtime_reports('\n'.join([
            prefix + '{"id":"clock","status":"failed","message":"unloaded"}',
            prefix + '{"id":"clock","status":"completed","result":{"entities":26}}',
        ]))
        self.assertIsNone(report)
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0]['status'], 'completed')

    def test_final_failure_is_preserved(self):
        report, _ = parse_runtime_reports('[JUNKET ORIGINAL] runtime fixtures initialized: '
                                          '{"ok":false,"completed":[],"failures":[{"id":"clock"}]}')
        self.assertFalse(report['ok'])
        self.assertEqual(report['failures'][0]['id'], 'clock')


class QualityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.request = 'bbc099ce-3541-4d6d-a773-88c82cae9211'
        self.counts = {gid: 6 + index for index, gid in enumerate(EXPECTED_IDS)}
        self.report = {'schema': 1, 'request_id': self.request, 'ok': True, 'failures': [], 'player_test': False,
                       'checks': [{'id': gid, 'fixture_count': count, 'identities_preserved': True,
                                   'health_preserved': True, 'labels_valid': True, 'sentinel_preserved': True, 'cleaned': True,
                                   'health_recoveries_during_test': 0,
                                   'stages': [{'name': name, 'ok': True} for name in QUALITY_STAGES]}
                                  for gid, count in self.counts.items()]}

    def test_exact_complete_lifecycle_report(self):
        self.assertEqual(validate_quality_report(self.report, self.request, self.counts), [])

    def test_rule_schema_requires_every_game_to_finish_without_client_claim(self):
        report = deepcopy(self.report)
        report['rules_schema'] = 1
        self.assertTrue(validate_quality_report(report, self.request, self.counts))
        for item in report['checks']:
            item['rule_progression'] = {'game': item['id'], 'phase': 'finished',
                                        'actions': 3, 'client_play': False}
        self.assertEqual(validate_quality_report(report, self.request, self.counts), [])
        report['checks'][0]['rule_progression']['phase'] = 'choose'
        self.assertTrue(validate_quality_report(report, self.request, self.counts))
        report['checks'][0]['rule_progression']['phase'] = 'finished'
        report['checks'][0]['rule_progression']['client_play'] = True
        self.assertTrue(validate_quality_report(report, self.request, self.counts))

    def test_missing_stage_or_replaced_fixture_is_not_pass(self):
        for field in ('identities_preserved', 'health_preserved', 'labels_valid', 'sentinel_preserved', 'cleaned'):
            report = deepcopy(self.report)
            report['checks'][0][field] = False
            self.assertTrue(validate_quality_report(report, self.request, self.counts))
        report = deepcopy(self.report)
        report['checks'][0]['stages'].pop()
        self.assertTrue(validate_quality_report(report, self.request, self.counts))

    def test_bundle_count_and_request_are_required(self):
        self.assertTrue(validate_quality_report(self.report, 'different', self.counts))
        report = deepcopy(self.report)
        report['checks'][0]['fixture_count'] += 1
        self.assertTrue(validate_quality_report(report, self.request, self.counts))

    def test_recovery_must_not_hide_lifecycle_damage(self):
        for count in (None, 1, True):
            report = deepcopy(self.report)
            report['checks'][0]['health_recoveries_during_test'] = count
            self.assertTrue(validate_quality_report(report, self.request, self.counts))

    def test_duplicate_games_and_player_claim_rejected(self):
        report = deepcopy(self.report)
        report['checks'] = [report['checks'][0]] * 17
        self.assertTrue(validate_quality_report(report, self.request, self.counts))
        report = self.report | {'player_test': True}
        self.assertTrue(validate_quality_report(report, self.request, self.counts))


class WarpEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.request = '4d8c0efe-d52b-433d-acbf-a1797857542e'
        self.destinations = {gid: {'x': 20000 + index * 96, 'y': 67, 'z': 20005}
                             for index, gid in enumerate(EXPECTED_IDS)}
        self.report = {'schema': 1, 'request_id': self.request, 'ok': True,
                       'player_test': False, 'test_entity': 'minecraft:armor_stand', 'failures': [],
                       'checks': [{'id': gid, 'loaded_before': False, 'loaded_after': True,
                                   'safe': True, 'teleported': True, 'cleaned': True, 'location': point}
                                  for gid, point in self.destinations.items()]}

    def test_completed_exact_request_and_actual_positions_pass(self):
        self.assertEqual(validate_warp_report(self.report, self.request, self.destinations), [])

    def test_duplicate_or_partial_game_lists_are_rejected(self):
        for checks in (self.report['checks'][:-1], [self.report['checks'][0]] * 17):
            with self.subTest(length=len(checks)):
                report = self.report | {'checks': checks}
                self.assertTrue(validate_warp_report(report, self.request, self.destinations))

    def test_unsafe_unloaded_or_uncleaned_destination_is_rejected(self):
        for field in ('loaded_after', 'safe', 'teleported', 'cleaned'):
            with self.subTest(field=field):
                report = deepcopy(self.report)
                report['checks'][0][field] = False
                self.assertTrue(validate_warp_report(report, self.request, self.destinations))

    def test_wrong_or_nonfinite_teleport_coordinates_are_rejected(self):
        for coordinate in (0, float('nan')):
            report = deepcopy(self.report)
            report['checks'][0]['location']['x'] = coordinate
            self.assertTrue(validate_warp_report(report, self.request, self.destinations))

    def test_old_report_never_completes_a_new_request(self):
        text = WARP_MARKER + json.dumps(self.report)
        report, progress = parse_warp_reports(text, 'another-request')
        self.assertIsNone(report)
        self.assertEqual(progress, [])
        self.assertEqual(parse_warp_reports(text, self.request)[0], self.report)

    def test_console_list_preserves_unknown_names_and_dimensions(self):
        text = ('[2026-10-19 10:00:00:001 INFO] Ticking Areas in the Overworld:\n'
                '[2026-10-19 10:00:00:001 INFO] future_user_area: from (1, 0, 2) to (16, 0, 17) (preload)\n'
                '[2026-10-19 10:00:00:001 INFO] Ticking Areas in the Nether:\n'
                '[2026-10-19 10:00:00:001 INFO] another_area: center (-488, 0, -440), radius 4\n'
                '[2026-10-19 10:00:00:001 INFO] There are 2/10 ticking areas in use.\n')
        parsed = parse_tickingarea_listing(text)
        self.assertEqual(len(parsed['entries']), 2)
        self.assertIn('future_user_area', '\n'.join(parsed['entries']))
        self.assertNotEqual(parsed, parse_tickingarea_listing(text.replace('radius 4', 'radius 3')))
        self.assertIsNone(parse_tickingarea_listing('Script event sent.'))
        self.assertIsNone(parse_tickingarea_listing('Ticking Areas in the Overworld:'))

    def test_actor_and_script_errors_are_kept(self):
        output = ('[2026-10-19 10:00:00 ERROR] [Actor] broken properties\n'
                  '[Scripting][error]-invalid successCount\n')
        self.assertEqual(len(runtime_errors(output)), 2)

    def test_actual_bds_multiline_list_requires_complete_count_and_keeps_user_area(self):
        # Actual release-20261019-10 console format, including the user's fifth area.
        text = ('[2026-10-19 11:43:52:864 INFO] \u00a7aList of all ticking areas in all dimensions\n'
                'Overworld: \n'
                '- narita_probe (Circle): -488 0 -440 Radius: 4 chunks\n'
                '- oreka_fix: 10304 0 9920 to 10431 0 10047 Preload\n'
                '- koumou_fix: 10560 0 9920 to 10687 0 10047 Preload\n'
                '- narita_fix: 22208 0 22208 to 22335 0 22335 Preload\n'
                '- sparrow_preview (Circle): 20024 0 20024 Radius: 2 chunks\n'
                '5/10 ticking areas in use.\n')
        parsed = parse_tickingarea_listing(text)
        self.assertEqual(parsed['reported_count'], 5)
        self.assertEqual(len(parsed['entries']), 5)
        self.assertEqual(parsed['headers'], ['Overworld:'])
        self.assertTrue(any('sparrow_preview (Circle): 20024 0 20024 Radius: 2 chunks' in item
                            for item in parsed['entries']))
        self.assertIsNone(parse_tickingarea_listing(text.replace('5/10 ticking areas in use.\n', '')))
        self.assertIsNone(parse_tickingarea_listing(text.replace(
            '- sparrow_preview (Circle): 20024 0 20024 Radius: 2 chunks\n', '')))
        self.assertNotEqual(parsed, parse_tickingarea_listing(text.replace('Radius: 2 chunks', 'Radius: 3 chunks')))
        multi = text.replace('5/10 ticking areas in use.',
                             'Nether:\n- nether_keep: 0 0 0 to 15 0 15\nEnd:\n- end_keep (Circle): 16 0 16 Radius: 1 chunks\n7/10 ticking areas in use.')
        self.assertEqual(parse_tickingarea_listing(multi)['reported_count'], 7)

    def test_transport_nul_prefix_does_not_hide_the_listing_header(self):
        text = ('[2026-10-19 11:47:20:413 INFO] \u00a7aList of all ticking areas in all dimensions\n'
                'Overworld:\n- user_area (Circle): 20024 0 20024 Radius: 2 chunks\n'
                '1/10 ticking areas in use.\n')
        parsed = parse_tickingarea_listing(text)
        self.assertEqual(parse_tickingarea_listing('\x00' + text), parsed)
        self.assertEqual(parse_tickingarea_listing('\x00\x00' + text), parsed)
        self.assertNotEqual(parse_tickingarea_listing(text.replace('user_area', 'user\x00_area')), parsed)

    def test_selftest_proof_checks_late_errors_and_area_preservation(self):
        for late_errors, changed, expected in [('', False, True),
                                               ('[Scripting][error]-successCount failed', False, False),
                                               ('', True, False)]:
            with self.subTest(late_errors=late_errors, changed=changed), TemporaryDirectory() as temp:
                directory = Path(temp)
                staged = directory / 'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json'
                staged.parent.mkdir(parents=True)
                staged.write_text(json.dumps({'venues': [{'id': gid, 'destination': point}
                                                        for gid, point in self.destinations.items()]}), encoding='utf-8')
                before = {'entries': ['Overworld | existing: from (1, 0, 2) to (3, 0, 4)']}
                after = {'entries': ['Overworld | unexpected: from (1, 0, 2) to (3, 0, 4)']} if changed else before
                output = WARP_MARKER + json.dumps(self.report)
                with patch('check_junket_native_runtime.uuid.uuid4', return_value=self.request), \
                        patch('check_junket_native_runtime.collect_tickingareas', side_effect=[before, after]), \
                        patch('check_junket_native_runtime.send_console') as send, \
                        patch('check_junket_native_runtime.read_logs', side_effect=[output, output + '\n' + late_errors]):
                    proof = check_warp_destinations(directory, 'operation')
                self.assertEqual(proof['server_warp_destinations_verified'], expected)
                self.assertTrue(proof['test_entities_removed'])
                self.assertFalse(proof['player_test'])
                self.assertEqual(len(proof['initially_unloaded_game_ids']), 17)
                send.assert_called_once_with('scriptevent junket:warpcheck all ' + self.request)
                self.assertTrue((directory / 'warp-runtime-proof.json').is_file())

    def test_incomplete_execution_keeps_failure_and_checks_area_cleanup(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            staged = directory / 'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json'
            staged.parent.mkdir(parents=True)
            staged.write_text('{"venues": []}', encoding='utf-8')
            listing = {'entries': ['existing: (1, 0, 1)']}
            with patch('check_junket_native_runtime.collect_tickingareas', return_value=listing) as collect, \
                    patch('check_junket_native_runtime.send_console'), \
                    patch('check_junket_native_runtime.read_logs', return_value='Script event sent.'):
                proof = check_warp_destinations(directory, 'operation', timeout=0)
            self.assertFalse(proof['server_warp_destinations_verified'])
            self.assertFalse(proof['test_entities_removed'])
            self.assertEqual(collect.call_count, 2)
            self.assertIsNone(proof['report'])

    def test_warm_destination_keeps_teleport_success_separate_from_all17_cold_proof(self):
        for warm_count in (0, 1):
            with self.subTest(warm_count=warm_count), TemporaryDirectory() as temp:
                directory = Path(temp)
                staged = directory / 'stage/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json'
                staged.parent.mkdir(parents=True)
                staged.write_text(json.dumps({'venues': [{'id': gid, 'destination': point}
                                                        for gid, point in self.destinations.items()]}), encoding='utf-8')
                report = deepcopy(self.report)
                for check in report['checks'][:warm_count]:
                    check['loaded_before'] = True
                output = WARP_MARKER + json.dumps(report)
                with patch('check_junket_native_runtime.uuid.uuid4', return_value=self.request), \
                        patch('check_junket_native_runtime.collect_tickingareas', return_value={'entries': ['existing']}), \
                        patch('check_junket_native_runtime.send_console'), \
                        patch('check_junket_native_runtime.read_logs', return_value=output):
                    proof = check_warp_destinations(directory, 'operation')
                self.assertTrue(proof['server_warp_destinations_verified'])
                self.assertTrue(proof['cold_destination_load_observed'])
                self.assertEqual(proof['initially_unloaded_game_count'], 17 - warm_count)
                self.assertEqual(proof['all17_initially_unloaded_verified'], warm_count == 0)


if __name__ == '__main__':
    unittest.main()
