"""Validate the shared 17-game catalog and verified production warp slots."""
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts/minecraft'))
from build_junket_warp import EXPECTED_IDS, STRUCTURES, build_plan, function_source, load
from build_junket_warp import runtime_source


class WarpCatalogChecks(unittest.TestCase):
    def setUp(self):
        self.catalog = load()
        self.plan = build_plan(self.catalog)

    def test_all_17_source_games_have_unique_titles_and_controls(self):
        games = self.catalog['games']
        placements = self.plan['placements']
        self.assertEqual([game['id'] for game in games], EXPECTED_IDS)
        self.assertEqual(len({game['title'] for game in games}), 17)
        self.assertEqual(len({tuple(item['button']) for item in placements}), 17)
        self.assertEqual(len({tuple(item['command_block']) for item in placements}), 17)
        self.assertEqual(len({tuple(item['sign']) for item in placements}), 17)
        self.assertEqual(
            [item['command_block'] for item in placements],
            [[10048, 65, z] for z in range(10102, 10068, -2)],
        )
        self.assertTrue(all(item['command_block'][0] == 10048 for item in placements))
        self.assertTrue(all(item['sign'][0] == 10049 for item in placements))
        self.assertTrue(all(item['button'][1] == 66 for item in placements))

    def test_existing_return_controls_are_protected(self):
        anchor = self.plan['protected_anchor']
        protected = {
            tuple(anchor['command_block']),
            tuple(anchor['sign']),
            tuple(anchor['button']),
        }
        targets = {
            tuple(location)
            for item in self.plan['placements']
            for location in (item['command_block'], item['sign'], item['button'])
        }
        self.assertFalse(targets & protected)
        self.assertEqual(anchor['label'], '拠点行き')
        self.assertEqual(anchor['command'], 'tp @p -44 75 210')
        self.assertTrue(anchor['require_nonblank_sign'])
        runtime = runtime_source(self.plan)
        self.assertIn('"PROTECTED_ANCHOR"', runtime)
        self.assertIn('"command_block": [\n      10051,', runtime)
        self.assertIn('"sign": [\n      10051,', runtime)
        self.assertIn('"button": [\n      10051,', runtime)

    def test_explicit_enabled_flag_controls_test_access(self):
        for item, game in zip(self.plan['placements'], self.catalog['games']):
            self.assertEqual(item['enabled'], game['enabled'])
            self.assertTrue(item['enabled'])
            self.assertEqual(item['readiness'], 'in-progress')

    def test_fixed_player_counts_match_source_start_functions(self):
        counts = {game['id']: game['max_players'] for game in self.catalog['games']}
        self.assertEqual(counts['sparrow'], 2)
        self.assertEqual(counts['temperance'], 2)
        self.assertEqual(counts['ace'], 4)
        self.assertEqual(counts['auction'], 4)
        self.assertEqual(counts['pig'], 4)
        hope = next(game for game in self.catalog['games'] if game['id'] == 'hope')
        self.assertEqual(hope['player_mode'], 'registration')
        self.assertEqual(hope['max_players'], 16)

    def test_each_slot_updates_only_existing_command_and_sign(self):
        self.assertFalse((STRUCTURES / 'warp_row.mcstructure').exists())
        self.assertFalse(list(STRUCTURES.glob('warp_slot_*.mcstructure')))
        for item, game in zip(self.plan['placements'], self.catalog['games']):
            self.assertNotIn('structure', item)
            self.assertNotIn('structure_origin', item)
            x, y, z = item['button']
            self.assertEqual(
                item['command'],
                f"execute as @a[tag=junket:warp_{game['id']},"
                f"x={x},y={y},z={z},r=4] run scriptevent junket:warp {game['id']}",
            )

    def test_button_is_never_part_of_a_structure_patch(self):
        function = function_source(self.plan)
        self.assertNotIn('setblock', function)
        self.assertNotIn('minecraft:acacia_button', function)
        self.assertNotIn('minecraft:wooden_button', function)
        self.assertEqual(function.count('blockdata '), 34)

    def test_misplaced_row_cleanup_is_exact_and_guarded(self):
        cleanup = self.plan['misplaced_row']
        cells = cleanup['cells']
        self.assertEqual(len(cells), 53)
        self.assertEqual(
            {cell['current_type'] for cell in cells},
            {
                'minecraft:oak_sign', 'minecraft:command_block',
                'minecraft:acacia_button', 'minecraft:dirt',
            },
        )
        self.assertEqual(
            {cell['restore_type'] for cell in cells},
            {'minecraft:air', 'minecraft:grass_block'},
        )
        protected = {
            tuple(self.plan['protected_anchor']['command_block']),
            tuple(self.plan['protected_anchor']['sign']),
            tuple(self.plan['protected_anchor']['button']),
        }
        controls = {
            tuple(location)
            for item in self.plan['placements']
            for location in (item['command_block'], item['sign'], item['button'])
        }
        locations = {tuple(cell['location']) for cell in cells}
        self.assertFalse(locations & protected)
        self.assertFalse(locations & controls)
        self.assertEqual(
            cleanup['entity_tags'], ['junket:misplaced_warp', 'junket:warp_label'],
        )


if __name__ == '__main__':
    unittest.main()
