"""Source-anchor transform reuse and fixed-entity safety regressions."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from build_junket_all17_bundle import (explicit_fixed_fixture_health, configure_interactive_source_entities,
                                      normalize_legacy_damage_sensors, legacy_damage_sensor_fields)
from junket_original_runtime_support import apply_source_models, include_return_tag
from inspect_junket_original_quality import source_local_normal


class SourceQualityTests(unittest.TestCase):
    def test_return_tags_have_one_canonical_order_without_changing_other_fixture_data(self):
        from itertools import permutations
        tags = ['junket:return', 'junket:venue_fixture', 'junket:sparrow:fixture:return']
        results = []
        for order in permutations(tags):
            fixture = {'tags': list(order) + [order[0]], 'role': 'return', 'location': [1, 2, 3]}
            include_return_tag(fixture)
            self.assertEqual(fixture, {'tags': sorted(tags), 'role': 'return', 'location': [1, 2, 3]})
            results.append(json.dumps(fixture, separators=(',', ':')))
            include_return_tag(fixture)
            self.assertEqual(json.dumps(fixture, separators=(',', ':')), results[-1])
        self.assertEqual(len(set(results)), 1)

    def test_exact_anchor_reuses_converted_display_without_moving_fixture(self):
        fixture = {'role': 'box0', 'type': 'junket:lucy_box', 'source_tag': 'lucy-box-1',
                   'location': [20201, 68.22, 20009.2], 'tags': ['owned'], 'properties': {'seat': 0}}
        venue = {'id': 'lucy', 'origin': [20200, 65, 20000], 'size': [30, 30, 30], 'fixtures': [fixture]}
        display = {'source_model': 'game/lucy/box', 'source_tags': ['lucy-box-1'], 'source_line': 5,
                   'type': 'junket_original:lucy_display_0', 'rotation': {'y': 180},
                   'location': {'x': 20201, 'y': 68.22, 'z': 20009.2}}
        models = {'models': {'lucy': {'box': {'entity': 'junket_original:lucy_box'}}}, 'displays': {'lucy': [display]}}
        layout = {'summons': [{'line': 5, 'rotation': [180, 0], 'transformation': {'scale': [.3, .3, .3]}}]}
        original = deepcopy(fixture)
        apply_source_models(venue, models, layout)
        self.assertEqual(fixture['type'], display['type'])
        self.assertEqual(fixture['source_transformation']['scale'], [.3, .3, .3])
        self.assertEqual(fixture['rotation'], 180)
        for key in ('role', 'location', 'tags', 'properties'):
            self.assertEqual(fixture[key], original[key])

    def test_unmatched_anchor_does_not_guess_another_props_transform(self):
        fixture = {'role': 'tv', 'type': 'junket:lucy_tv', 'source_tag': 'different-tv',
                   'location': [0, 0, 0], 'rotation': 7}
        venue = {'id': 'lucy', 'origin': [0, 0, 0], 'size': [1, 1, 1], 'fixtures': [fixture]}
        models = {'models': {'lucy': {'tv': {'entity': 'junket_original:lucy_tv'}}}, 'displays': {}}
        apply_source_models(venue, models, {'summons': []})
        self.assertEqual(fixture['type'], 'junket_original:lucy_tv')
        self.assertEqual(fixture['rotation'], 7)
        self.assertNotIn('source_display_transform_applied', fixture)

    def test_item_normal_accounts_for_quaternion_and_keeps_entity_yaw_separate(self):
        self.assertEqual(source_local_normal([0, 0, -1], {'scale': [2.4, 2, 1]}), [0, 0, -1])
        self.assertEqual(source_local_normal([0, 0, -1], {'right_rotation': [1, 0, 0, 1]}), [0, 1, 0])

    def test_generated_status_copies_leave_one_original_source_display(self):
        display = {'role': 'original_tv', 'source_model': 'game/ace/tv', 'source_tags': ['ace-tv'],
                   'source_line': 8, 'type': 'junket_original:ace_display_1',
                   'rotation': {'y': 90}, 'location': {'x': 2, 'y': 2, 'z': 2}}
        fixtures = [{'role': 'display_' + str(index), 'type': 'junket:ace_tv',
                     'source_model': 'game/ace/tv', 'source_display_line': 8,
                     'location': [2, 2 + index * .35, 2]} for index in range(3)]
        venue = {'id': 'ace', 'origin': [0, 0, 0], 'size': [10, 10, 10], 'fixtures': fixtures}
        models = {'models': {'ace': {'tv': {'entity': 'junket_original:ace_tv'}}}, 'displays': {'ace': [display]}}
        apply_source_models(venue, models, {'summons': [{'line': 8, 'rotation': [90, 0]}]})
        self.assertEqual(len(venue['fixtures']), 1)
        self.assertEqual(venue['fixtures'][0]['role'], 'original_tv')
        self.assertEqual(venue['fixtures'][0]['location'], [2, 2, 2])

    def test_hope_interactive_apparatus_is_separate_from_immutable_displays(self):
        with TemporaryDirectory() as temp:
            bp = Path(temp)
            (bp / 'entities').mkdir()
            (bp / 'scripts').mkdir()
            path = bp / 'entities/code.json'
            path.write_text(json.dumps({'minecraft:entity': {
                'description': {'identifier': 'junket_original:hope_code'},
                'components': {'minecraft:persistent': {}, 'minecraft:physics': {'has_gravity': False, 'has_collision': False},
                               'minecraft:damage_sensor': {'triggers': [{'cause': 'all', 'deals_damage': 'no'}]}}}}), encoding='utf-8')
            (bp / 'scripts/all17_original_generated.json').write_text(json.dumps({'venues': [{'id': 'hope', 'fixtures': [
                {'role': 'hope_code', 'type': 'junket_original:hope_code'}]}]}), encoding='utf-8')
            self.assertEqual(configure_interactive_source_entities(bp), ['junket_original:hope_code'])
            self.assertEqual(explicit_fixed_fixture_health(bp), [])
            components = json.loads(path.read_text(encoding='utf-8'))['minecraft:entity']['components']
            self.assertEqual(components['minecraft:health'], {'value': 3, 'max': 3})
            self.assertEqual(components['minecraft:damage_sensor']['triggers'][0], {'cause': 'entity_attack', 'deals_damage': 'yes'})
            self.assertEqual(components['minecraft:damage_sensor']['triggers'][1]['on_damage']['filters']['value'], 'attack')

    def test_health_only_added_to_undamageable_fixed_manifest_props(self):
        with TemporaryDirectory() as temp:
            bp = Path(temp)
            (bp / 'entities').mkdir()
            (bp / 'scripts').mkdir()
            fixed = {'minecraft:physics': {'has_gravity': False, 'has_collision': False},
                     'minecraft:persistent': {},
                     'minecraft:damage_sensor': {'triggers': [{'cause': 'all', 'deals_damage': 'no'}]}}
            variants = {'fixed': deepcopy(fixed), 'healthy': deepcopy(fixed),
                        'creature': {'minecraft:physics': {'has_gravity': True}},
                        'not_in_manifest': deepcopy(fixed)}
            variants['healthy']['minecraft:health'] = {'value': 7, 'max': 9}
            for name, components in variants.items():
                (bp / ('entities/' + name + '.json')).write_text(json.dumps({'minecraft:entity': {
                    'description': {'identifier': 'junket:' + name}, 'components': components}}), encoding='utf-8')
            (bp / 'scripts/all17_original_generated.json').write_text(json.dumps({'venues': [{'fixtures': [
                {'type': 'junket:' + name} for name in ('fixed', 'healthy', 'creature')]}]}), encoding='utf-8')
            self.assertEqual(explicit_fixed_fixture_health(bp), ['junket:fixed'])
            for name in ('healthy', 'creature', 'not_in_manifest'):
                actual = json.loads((bp / ('entities/' + name + '.json')).read_text(encoding='utf-8'))
                self.assertEqual(actual['minecraft:entity']['components'], variants[name])
            self.assertEqual(explicit_fixed_fixture_health(bp), [])

    def test_legacy_damage_sensor_uses_booleans_and_keeps_hope_attack_enabled(self):
        with TemporaryDirectory() as temp:
            bp = Path(temp)
            (bp / 'entities').mkdir()
            value = {'format_version': '1.21.0', 'minecraft:entity': {
                'description': {'identifier': 'junket_original:hope_code'},
                'components': {'minecraft:damage_sensor': {'triggers': [
                    {'cause': 'entity_attack', 'deals_damage': 'yes'},
                    {'cause': 'all', 'deals_damage': 'no', 'on_damage': {'filters': {'test': 'has_damage', 'value': 'attack', 'operator': 'not'}}}]}},
                'component_groups': {'fixed': {'minecraft:damage_sensor': {'triggers': {'cause': 'all', 'deals_damage': 'no'}}}}}}
            path = bp / 'entities/code.json'
            path.write_text(json.dumps(value), encoding='utf-8')
            self.assertEqual(normalize_legacy_damage_sensors(bp)[0]['normalized_fields'], 3)
            actual = json.loads(path.read_text(encoding='utf-8'))
            triggers = actual['minecraft:entity']['components']['minecraft:damage_sensor']['triggers']
            self.assertIs(triggers[0]['deals_damage'], True)
            self.assertIs(triggers[1]['deals_damage'], False)
            self.assertEqual(triggers[1]['on_damage'], value['minecraft:entity']['components']['minecraft:damage_sensor']['triggers'][1]['on_damage'])
            self.assertEqual(list(legacy_damage_sensor_fields(actual)), [])
            self.assertEqual(normalize_legacy_damage_sensors(bp), [])

    def test_modern_sensor_enum_is_preserved(self):
        value = {'format_version': '1.21.40', 'minecraft:entity': {'components': {
            'minecraft:damage_sensor': {'triggers': {'deals_damage': 'no_but_side_effects_apply'}}}}}
        self.assertEqual(list(legacy_damage_sensor_fields(value)), [])


if __name__ == '__main__':
    unittest.main()
