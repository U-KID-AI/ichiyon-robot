from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from junket_source_access import service_controls, reachable_cells, repair_structures, floor
from build_junket_all17_bundle import create_protected_guide, protect_fixture_motion
from junket_original_runtime_support import protect_fixture_npcs


class SourceAccessTests(unittest.TestCase):
    def room(self):
        layout = {'id': 'test', 'production_origin': [0, 0, 0], 'size': [9, 4, 9], 'destination': [4.5, 1, 4.5]}
        blocks = {(x, 0, z): {'name': 'minecraft:stone', 'states': {}} for x in range(9) for z in range(9)}
        return layout, blocks

    def test_controls_use_reachable_air_keep_floor_and_barriers(self):
        layout, blocks = self.room()
        blocks[(0, 1, 0)] = {'name': 'minecraft:barrier', 'states': {}}
        before = deepcopy(blocks)
        controls = service_controls(layout, {'fixtures': [], 'block_overlays': []}, blocks)
        self.assertEqual({c['action'] for c in controls}, {'reception', 'return', 'reset', 'repair'})
        self.assertEqual(len({tuple(c[key]) for c in controls for key in ('button', 'sign')}), 8)
        hardware = {tuple(c[key]) for c in controls for key in ('button', 'sign')}
        for control in controls:
            self.assertEqual(control['path'][0], [4, 1, 4])
            self.assertEqual(control['path'][-1], [int(value) for value in control['stand']])
            self.assertFalse(hardware.intersection(map(tuple, control['path'])))
            self.assertEqual(sum(abs(a - b) for a, b in zip(control['path'][-1], control['button'])), 1)
            self.assertEqual(control['support_block']['name'], 'minecraft:stone')
            self.assertNotEqual(control['button'], [0, 1, 0])
        self.assertEqual(blocks, before)

    def test_strict_paths_reroute_around_carpet_and_installed_controls(self):
        layout, blocks = self.room()
        blocks[(3, 1, 4)] = {'name': 'minecraft:light_gray_carpet', 'states': {}}
        relaxed, _ = reachable_cells(layout, blocks)
        self.assertIn((3, 1, 4), relaxed)
        strict, _ = reachable_cells(layout, blocks, strict_clearance=True, blocked={(4, 1, 5)})
        self.assertNotIn((3, 1, 4), strict)
        self.assertNotIn((4, 1, 5), strict)
        self.assertIn((2, 1, 4), strict)
        controls = service_controls(layout, {'fixtures': []}, blocks)
        self.assertTrue(all([3, 1, 4] not in control['path'] for control in controls))

    def test_all_fixed_npc_roles_are_protected_without_moving_or_changing_actions(self):
        fixtures = [{'type': 'minecraft:npc', 'role': role, 'location': [1, 2, 3],
                     'action': role, 'tags': ['junket:venue_id=silence'], 'properties': {'skin_id': 2}}
                    for role in ('reception', 'return', 'guard')]
        fixtures.append({'type': 'junket:hope_judge', 'role': 'judge', 'location': [4, 5, 6]})
        before = deepcopy(fixtures)
        protect_fixture_npcs({'fixtures': fixtures})
        for fixture, old in zip(fixtures[:3], before[:3]):
            self.assertEqual(fixture['type'], 'junket:guide')
            self.assertEqual(fixture['frame_property'], 'none')
            self.assertEqual(fixture['properties'], {})
            for field in ('location', 'action', 'tags', 'role'):
                self.assertEqual(fixture[field], old[field])
        self.assertEqual(fixtures[-1], before[-1])

    def test_barrier_wall_splits_reachability_without_being_removed(self):
        layout, blocks = self.room()
        for z in range(9):
            for y in (1, 2):
                blocks[(2, y, z)] = {'name': 'minecraft:barrier', 'states': {}}
        paths, _ = reachable_cells(layout, blocks)
        self.assertIn((3, 1, 4), paths)
        self.assertNotIn((1, 1, 4), paths)

    def test_double_slab_is_full_support_but_half_slab_is_conservative(self):
        self.assertTrue(floor('minecraft:smooth_quartz_double_slab'))
        self.assertFalse(floor('minecraft:smooth_quartz_slab'))

    def test_repair_tiles_cannot_escape_venue_bounds(self):
        layout, _ = self.room()
        layout['structures'] = [{'identifier': 'junket:test', 'origin': [0, 0, 0], 'size': [9, 4, 9], 'sha256': 'abc'}]
        self.assertEqual(repair_structures(layout)[0]['offset'], [0, 0, 0])
        layout['structures'][0]['origin'][0] = 1
        with self.assertRaisesRegex(ValueError, 'escapes'):
            repair_structures(layout)

    def test_custom_guide_and_motion_protection_preserve_interactive_damage(self):
        with TemporaryDirectory() as temp:
            bp, rp = Path(temp) / 'bp', Path(temp) / 'rp'
            create_protected_guide(bp, rp)
            guide = json.loads((bp / 'entities/guide.json').read_text(encoding='utf-8'))
            self.assertNotIn('minecraft:npc', guide['minecraft:entity']['components'])
            self.assertIs(guide['minecraft:entity']['components']['minecraft:damage_sensor']['triggers'][0]['deals_damage'], False)
            creature = {'minecraft:entity': {'description': {'identifier': 'junket:creature'}, 'components': {'minecraft:physics': {'has_gravity': True}}}}
            creature_path = bp / 'entities/creature.json'
            creature_path.write_text(json.dumps(creature), encoding='utf-8')
            code = {'minecraft:entity': {'description': {'identifier': 'junket_original:hope_code'}, 'components': {
                'minecraft:damage_sensor': {'triggers': [{'cause': 'entity_attack', 'deals_damage': True}]},
                'minecraft:health': {'value': 3, 'max': 3}}}}
            code_path = bp / 'entities/code.json'
            code_path.write_text(json.dumps(code), encoding='utf-8')
            (bp / 'scripts').mkdir()
            (bp / 'scripts/all17_original_generated.json').write_text(json.dumps({'venues': [{'fixtures': [
                {'type': 'junket:guide'}, {'type': 'junket_original:hope_code'}]}]}), encoding='utf-8')
            self.assertEqual(len(protect_fixture_motion(bp)), 2)
            protected = json.loads(code_path.read_text(encoding='utf-8'))['minecraft:entity']['components']
            self.assertIs(protected['minecraft:damage_sensor']['triggers'][0]['deals_damage'], True)
            self.assertEqual(protected['minecraft:health'], {'value': 3, 'max': 3})
            self.assertEqual(protected['minecraft:knockback_resistance'], {'value': 1.0})
            self.assertEqual(json.loads(creature_path.read_text(encoding='utf-8')), creature)


if __name__ == '__main__':
    unittest.main()
