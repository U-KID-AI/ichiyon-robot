"""Native LevelDB placement safety and encoding checks; no gameplay claims."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

import leveldb
import nbtlib as n

from junket_catalog_world import encode_record, parse_records
from junket_native_world import (AIR, NativePlacement, apply_layouts, database_hashes,
                                  decode_section, encode_section, prefix, section_key)
from junket_structure import ConvertedWorld


def block(name):
    return n.Compound({'name': n.String('minecraft:' + name), 'states': n.Compound(),
                       'version': n.Int(18186752)})


class NativeWorldChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.world_path = self.root / 'world'
        self.world_path.mkdir()
        db = leveldb.LevelDB(str(self.world_path / 'db'), create_if_missing=True)
        self.p = prefix(1250, 1250)
        section = decode_section(None, 4)
        section['layers'][0]['palette'].extend([block('stone'), block('diamond_block')])
        for x in range(4):
            for z in range(4):
                section['layers'][0]['indices'][x * 256 + z * 16 + 1] = 1
        section['layers'][0]['indices'][15 * 256 + 15 * 16 + 15] = 2
        db[section_key(1250, 4, 1250)] = encode_section(section, 4)
        self.metadata = {self.p + b',': b'\x2a', self.p + b'6': b'\x02\x00\x00\x00',
                         b'actorprefix-original': b'Original avatar/player/inventory payload'}
        for key, value in self.metadata.items():
            db[key] = value
        db[self.p + b'+'] = b'\0' * 512 + b'native-biome-tail-must-remain'
        protected = n.File({'id': n.String('Chest'), 'x': n.Int(20015), 'y': n.Int(66),
                            'z': n.Int(20015), 'CustomName': n.String('Unrelated chest')})
        db[self.p + b'1'] = encode_record(protected)
        db.close()
        self.world = ConvertedWorld(self.world_path)
        size = [2, 3, 2]
        palette = [AIR, block('gold_block'), block('oak_sign')]
        indices = [1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1]
        self.structure = n.File({'format_version': n.Int(1), 'size': n.List[n.Int](size),
            'structure_world_origin': n.List[n.Int]([0, 0, 0]),
            'structure': n.Compound({'block_indices': n.List[n.List[n.Int]]([
                n.List[n.Int](indices), n.List[n.Int]([-1] * 12)]),
                'entities': n.List[n.Compound](), 'palette': n.Compound({'default': n.Compound({
                    'block_palette': n.List[n.Compound](palette), 'block_position_data': n.Compound({
                        '8': n.Compound({'block_entity_data': n.Compound({'id': n.String('Sign'),
                            'x': n.Int(1), 'y': n.Int(1), 'z': n.Int(0),
                            'FrontText': n.Compound({'Text': n.String('Original venue sign')})})})})})})})})
        path = self.root / 'original.mcstructure'
        self.structure.save(path, byteorder='little')
        self.game = {'id': 'sparrow', 'production_origin': [20000, 65, 20000], 'size': size,
                     'structures': [{'path': str(path), 'origin': [20000, 65, 20000], 'size': size}]}
        self.legacy = {'origin': [20000, 65, 20000], 'size': [4, 4, 4]}

    def tearDown(self):
        self.world.db.close()
        self.tmp.cleanup()

    def test_subchunk_round_trip_both_versions_and_two_layers(self):
        for version in (8, 9):
            section = decode_section(None, 4)
            section['version'] = version
            section['layers'][0]['palette'] += [block('stone'), block('water')]
            section['layers'][0]['indices'][37] = 2
            section['layers'].append({'palette': [AIR, block('water')], 'indices': [0] * 4096})
            section['layers'][1]['indices'][37] = 1
            raw = encode_section(section, 4)
            self.assertEqual(encode_section(decode_section(raw, 4), 4), raw)

    def test_empty_saved_subchunk_is_preserved_until_a_block_is_added(self):
        for version, raw in ((8, b'\x08\x00'), (9, b'\x09\x00\x13')):
            section = decode_section(raw, 19)
            self.assertEqual(section['version'], version)
            self.assertEqual(encode_section(section, 19), raw)
            section['layers'][0]['palette'].append(block('stone'))
            section['layers'][0]['indices'][4095] = 1
            rewritten = encode_section(section, 19)
            self.assertNotEqual(rewritten, raw)
            self.assertEqual(decode_section(rewritten, 19)['layers'][0]['indices'][4095], 1)

    def test_dirty_restore_empty_top_subchunk_does_not_break_height_update(self):
        key = section_key(1250, 19, 1250)
        self.world.db[key] = b'\x09\x00\x13'
        placement = NativePlacement(self.world)
        placement.stage_game(self.game, self.legacy)
        values = placement.values()
        self.assertNotIn(key, values)
        self.assertEqual(self.world.get(key), b'\x09\x00\x13')

    def test_palette_packing_round_trips_non_divisors_of_32(self):
        for size in (5, 17, 33, 65, 257):
            section = decode_section(None, 4)
            section['layers'][0]['palette'] = [block('synthetic_' + str(i)) for i in range(size)]
            expected = [i % size for i in range(4096)]
            section['layers'][0]['indices'] = expected
            raw = encode_section(section, 4)
            self.assertEqual(decode_section(raw, 4)['layers'][0]['indices'], expected)

    def test_native_original_replaces_old_hall_only_and_preserves_metadata(self):
        before = database_hashes(self.world)
        previous_tiles = self.world.get(self.p + b'1')
        placement = NativePlacement(self.world)
        placement.stage_game(self.game, self.legacy)
        values = placement.values()
        self.world.db.putBatch(values)
        self.world.section.cache_clear()
        after = database_hashes(self.world)
        self.assertEqual({key for key in set(before) | set(after) if before.get(key) != after.get(key)}, set(values))
        self.assertEqual(str(self.world.blocks(20000, 65, 20000)[0]['name']), 'minecraft:gold_block')
        self.assertEqual(str(self.world.blocks(20003, 65, 20003)[0]['name']), 'minecraft:air')
        self.assertEqual(str(self.world.blocks(20015, 79, 20015)[0]['name']), 'minecraft:diamond_block')
        self.assertEqual(self.world.get(self.p + b'+')[512:], b'native-biome-tail-must-remain')
        for key, value in self.metadata.items():
            self.assertEqual(self.world.get(key), value)
        records = parse_records(self.world.get(self.p + b'1'))
        self.assertEqual(records[0][2], previous_tiles)
        self.assertEqual(records[1][0], (20001, 66, 20000))
        self.assertEqual(str(records[1][1]['FrontText']['Text']), 'Original venue sign')
        self.world.db.close()
        self.world = ConvertedWorld(self.world_path)
        self.assertEqual(str(self.world.blocks(20000, 65, 20000)[0]['name']), 'minecraft:gold_block')

    def test_extension_collision_refused_without_database_writes(self):
        self.legacy['size'] = [1, 1, 1]
        before = database_hashes(self.world)
        with self.assertRaisesRegex(ValueError, 'existing building outside old'):
            NativePlacement(self.world).stage_game(self.game, self.legacy)
        self.assertEqual(database_hashes(self.world), before)

    def test_missing_chunk_is_not_synthesized(self):
        self.game['size'][0] = 17
        with self.assertRaisesRegex(ValueError, 'generate/save target chunks'):
            NativePlacement(self.world).stage_game(self.game, self.legacy)

    def test_inventory_inside_replacement_bounds_is_protected(self):
        root = n.File({'id': n.String('Chest'), 'x': n.Int(20001), 'y': n.Int(66), 'z': n.Int(20001)})
        self.world.db[self.p + b'1'] = encode_record(root)
        placement = NativePlacement(self.world)
        placement.stage_game(self.game, self.legacy)
        with self.assertRaisesRegex(ValueError, 'unrelated/inventory'):
            placement.values()

    def test_managed_entry_requires_stopped_world_and_real_backup(self):
        with self.assertRaisesRegex(ValueError, 'backup required'):
            apply_layouts(self.world_path, {}, self.root / 'rollback.json',
                          full_backup=self.root / 'missing', world_is_stopped=True,
                          legacy_pack=self.root)

    def test_gameplay_overlay_replaces_source_block_and_removes_its_tile(self):
        self.game['block_overlays'] = [{'location': {'x': 20001, 'y': 66, 'z': 20000},
                                        'block': 'minecraft:barrier'}]
        placement = NativePlacement(self.world)
        placement.stage_game(self.game, self.legacy)
        self.assertEqual(str(placement.get((20001, 66, 20000))['name']), 'minecraft:barrier')
        self.assertNotIn((20001, 66, 20000), placement.tiles)
        self.assertEqual(placement.game_reports[0]['gameplay_block_overlays'], 1)

    def test_gameplay_overlay_cannot_extend_the_authorized_hall(self):
        self.game['block_overlays'] = [{'location': {'x': 20005, 'y': 66, 'z': 20000},
                                        'block': 'minecraft:barrier'}]
        before = database_hashes(self.world)
        with self.assertRaisesRegex(ValueError, 'overlay outside'):
            NativePlacement(self.world).stage_game(self.game, self.legacy)
        self.assertEqual(database_hashes(self.world), before)


if __name__ == '__main__':
    unittest.main()
