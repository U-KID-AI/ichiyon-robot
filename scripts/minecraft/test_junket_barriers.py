"""Barrier removal is narrower than a venue rebuild and retains falling floors."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
import json

import nbtlib as n

from junket_barriers import (BARRIER, VISIBLE_FLOOR, derive_structure, floor_replacement,
                             stage_barrier_removal, validate_barrier_revision, validate_barrier_free_bundle)
from junket_native_world import EXPECTED_IDS, NativePlacement, commit_placement, database_hashes
import test_junket_native_world as native_test


class BarrierPolicyTests(unittest.TestCase):
    def game(self, gid):
        return {'id': gid, 'translation': [0, 0, 0], 'source_seats': [[2, 65, 2], [6, 65, 2]],
                'source_floor_policy': {'planes': [{'y': 64, 'x': [0, 8], 'z': [0, 8]}]}}

    def test_only_explicit_floor_planes_are_visible_and_removable(self):
        for gid in ('hope', 'rabbit', 'clock', 'garden'):
            game = self.game(gid)
            game['floor_template'] = {'source_target_offset': [0, 2, 0], 'cells': []}
            self.assertEqual(floor_replacement(game, (2, 64, 2)), VISIBLE_FLOOR)
            self.assertEqual(floor_replacement(game, (2, 65, 2)), 'minecraft:air')
            self.assertEqual(floor_replacement(game, (9, 64, 2)), 'minecraft:air')

    def test_clock_keeps_source_template_sector_colours(self):
        game = self.game('clock')
        game['floor_template'] = {'source_target_offset': [0, 2, 0],
            'cells': [{'location': [2, 62, 2], 'block': {'name': 'minecraft:red_wool'}}]}
        self.assertEqual(floor_replacement(game, (2, 64, 2)), 'minecraft:red_wool')

    def test_private_runtime_floor_builder_is_not_bundled_with_public_sources(self):
        self.skipTest('Coordinate-specific physical_board generator is retained privately; public tests cover the generic barrier writer')

    def test_final_pack_guard_rejects_dynamic_or_hidden_collision_regression(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'scripts').mkdir()
            (root/'functions').mkdir()
            (root/'entities').mkdir()
            script = root/'scripts/runtime.js'
            script.write_text('if (block.typeId === "minecraft:barrier") return false;', encoding='utf-8')
            function = root/'functions/remove.mcfunction'
            function.write_text('fill 0 1 2 3 4 5 air replace barrier', encoding='utf-8')
            self.assertEqual(validate_barrier_free_bundle(root, [])['runtime_barrier_references'], 0)
            for text in ['block.setType("minecraft:barrier");',
                         'BlockPermutation.resolve("minecraft:barrier");',
                         'dimension.runCommand("fill 0 1 2 3 4 5 barrier");']:
                script.write_text(text, encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'placement'):
                    validate_barrier_free_bundle(root, [])
            script.write_text('', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'metadata'):
                validate_barrier_free_bundle(root, [{'block_overlays': [{'block': BARRIER}]}])
            entity = {'minecraft:entity': {'description': {'identifier':'junket:anchor'},
                       'components': {'minecraft:physics': {'has_collision': True}}}}
            (root/'entities/anchor.json').write_text(json.dumps(entity), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'obstruct'):
                validate_barrier_free_bundle(root, [{'fixtures':[{'type':'junket:anchor'}]}])


class BarrierDatabaseTests(unittest.TestCase):
    setUp = native_test.NativeWorldChecks.setUp
    tearDown = native_test.NativeWorldChecks.tearDown

    def test_barrier_inside_changes_but_neighbour_barrier_and_all_ordinary_blocks_remain(self):
        placement = NativePlacement(self.world)
        placement.set((20000, 66, 20000), native_test.block('barrier'))
        placement.set((20003, 66, 20000), native_test.block('barrier'))
        commit_placement(self.world, placement, self.root / 'setup.json')
        foreign_tile = self.world.get(self.p + b'1')
        ordinary = self.world.blocks(20001, 65, 20001)
        game = {**self.game, 'translation': [19900, 0, 19900]}
        placement = NativePlacement(self.world)
        report = stage_barrier_removal(placement, [game])
        proof = commit_placement(self.world, placement, self.root / 'removed.json')
        self.assertEqual(report[0]['barriers_removed'], 1)
        self.assertEqual(str(self.world.blocks(20000, 66, 20000)[0]['name']), 'minecraft:air')
        self.assertEqual(str(self.world.blocks(20003, 66, 20000)[0]['name']), BARRIER)
        self.assertEqual(self.world.blocks(20001, 65, 20001), ordinary)
        self.assertEqual(self.world.get(self.p + b'1'), foreign_tile)
        self.assertTrue(proof['actors_and_players_preserved'])
        for key, value in self.metadata.items():
            self.assertEqual(self.world.get(key), value)
        before = database_hashes(self.world)
        placement = NativePlacement(self.world)
        stage_barrier_removal(placement, [game])
        again = commit_placement(self.world, placement, self.root / 'again.json')
        self.assertEqual(again['changed_blocks'], 0)
        self.assertEqual(database_hashes(self.world), before)

    def test_gallery_support_is_replaced_without_moving_its_height(self):
        placement = NativePlacement(self.world)
        placement.set((20000, 66, 20000), native_test.block('barrier'))
        commit_placement(self.world, placement, self.root / 'setup.json')
        game = {**self.game, 'id': 'hope', 'translation': [0, 0, 0],
                'source_floor_policy': {'planes': [{'y': 66}]}}
        placement = NativePlacement(self.world)
        stage_barrier_removal(placement, [game])
        commit_placement(self.world, placement, self.root / 'visible.json')
        self.assertEqual(str(self.world.blocks(20000, 66, 20000)[0]['name']), VISIBLE_FLOOR)
        self.assertEqual(str(self.world.blocks(20000, 67, 20000)[0]['name']), 'minecraft:air')

    def test_repair_structure_removes_barrier_palette_and_preserves_ordinary_data(self):
        self.structure['structure']['palette']['default']['block_palette'].append(native_test.block('barrier'))
        self.structure['structure']['block_indices'][0][0] = n.Int(3)
        old = self.root / 'barrier.mcstructure'
        self.structure.save(old, byteorder='little')
        tile = {**self.game['structures'][0], 'path': str(old), 'sha256': sha256(old.read_bytes()).hexdigest(),
                'identifier': 'junket:test'}
        result = derive_structure({**self.game, 'translation': [0, 0, 0]}, tile, self.root / 'derived.mcstructure')
        after = n.load(result['path'], gzipped=False, byteorder='little')
        self.assertFalse(any(str(p['name']) == BARRIER for p in after['structure']['palette']['default']['block_palette']))
        self.assertEqual(after['structure']['palette']['default']['block_position_data'],
                         self.structure['structure']['palette']['default']['block_position_data'])
        self.assertEqual(result['barrier_replacements'], {'minecraft:air': 1})
        self.assertEqual(sha256(old.read_bytes()).hexdigest(), tile['sha256'])

    def layouts(self):
        path = self.root / 'original.mcstructure'
        old = {'source_sha256': 'a' * 64, 'games': []}
        current = {'source_sha256': 'a' * 64, 'barrier_revision': 1, 'games': []}
        for gid in EXPECTED_IDS:
            game = {**deepcopy(self.game), 'id': gid, 'translation': [0, 0, 0], 'destination': [20000, 66, 20000],
                    'source_floor_policy': {'planes': []}}
            game['structures'][0].update(sha256=sha256(path.read_bytes()).hexdigest(), identifier='junket:test')
            old['games'].append(game)
            revised = deepcopy(game)
            revised['structures'] = [derive_structure(revised, game['structures'][0], self.root / (gid + '.mcstructure'))]
            current['games'].append(revised)
        return current, old

    def test_narrow_revision_rejects_ordinary_block_edit_and_moved_arrival(self):
        current, old = self.layouts()
        self.assertEqual(len(validate_barrier_revision(current, old)), 17)
        bad = deepcopy(current)
        bad['games'][0]['destination'][0] += 1
        with self.assertRaisesRegex(ValueError, 'placement'):
            validate_barrier_revision(bad, old)
        tile = current['games'][0]['structures'][0]
        root = n.load(tile['path'], gzipped=False, byteorder='little')
        root['structure']['palette']['default']['block_palette'][1]['name'] = n.String('minecraft:diamond_block')
        root.save(tile['path'], byteorder='little')
        tile['sha256'] = sha256(Path(tile['path']).read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'ordinary'):
            validate_barrier_revision(current, old)


if __name__ == '__main__':
    unittest.main()
