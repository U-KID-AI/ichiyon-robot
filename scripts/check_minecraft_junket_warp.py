"""Validate the shared 17-game catalog and protected production controls."""
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts/minecraft'))
from build_junket_warp import EXPECTED_IDS, build_plan, load
from build_junket_warp import STRUCTURE, runtime_source
import nbtlib as n


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

    def test_existing_return_command_block_is_not_a_generated_target(self):
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
        runtime = runtime_source(self.plan)
        self.assertIn('"PROTECTED_ANCHOR"', runtime)
        self.assertIn('"command_block": [\n      10051,', runtime)
        self.assertIn('"sign": [\n      10051,', runtime)
        self.assertIn('"button": [\n      10051,', runtime)

    def test_implemented_games_are_open_and_unfinished_games_are_denied(self):
        for item, game in zip(self.plan['placements'], self.catalog['games']):
            self.assertEqual(item['enabled'], game['readiness'] in ('verified', 'implemented'))
            if game['readiness'] not in ('verified', 'implemented'):
                self.assertFalse(item['enabled'])

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

    def test_control_structure_contains_all_signed_commands(self):
        with STRUCTURE.open('rb') as stream:
            root = n.File.parse(stream, byteorder='little')
        self.assertEqual([int(value) for value in root['size']], [34, 1, 3])
        positions = root['structure']['palette']['default']['block_position_data']
        self.assertEqual(len(positions), 34)
        commands = sorted(
            str(value['block_entity_data']['Command'])
            for value in positions.values()
            if 'Command' in value['block_entity_data']
        )
        self.assertEqual(len(commands), 17)
        self.assertEqual(commands, sorted(
            f"scriptevent junket:warp {game['id']}" for game in self.catalog['games']))
        sign_entities = [
            value['block_entity_data'] for value in positions.values()
            if str(value['block_entity_data'].get('id', '')) == 'Sign'
        ]
        self.assertEqual(len(sign_entities), 17)
        self.assertTrue(all(str(value['FrontText'].get('TextOwner', '')) == 'JUNKET'
                            for value in sign_entities))


if __name__ == '__main__':
    unittest.main()
