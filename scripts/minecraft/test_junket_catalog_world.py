"""Offline catalog NBT preservation/regression tests, not Minecraft playtests."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import leveldb
import nbtlib as n

from build_junket_warp import build_plan, load, runtime_source, script_load_command
from junket_catalog_world import (
    apply_patch, chunk_key, encode_record, native_plan, parse_records,
    prepare_patch, previous_native_tp_command, verify_patch,
)


class FixtureWorld:
    def __init__(self, path, plan):
        self.db = leveldb.LevelDB(str(path), create_if_missing=True)
        self.palette = {}
        groups = {}

        def add(at, name, states, root=None):
            self.palette[tuple(at)] = n.Compound({'name': n.String(name), 'states': n.Compound({
                key: (n.Byte(value) if isinstance(value, bool) else n.Int(value))
                for key, value in states.items()})})
            if root:
                root.update({key: n.Int(value) for key, value in zip(('x', 'y', 'z'), at)})
                groups.setdefault(chunk_key(at), []).append(encode_record(n.File(root)))

        for item in plan['placements']:
            add(item['command_block'], item['command_block_type'], item['command_block_states'],
                {'id': n.String('CommandBlock'), 'Command': n.String('')})
            add(item['sign'], item['sign_type'], item['sign_states'],
                {'id': n.String('Sign'), 'FrontText': n.Compound({'Text': n.String('')})})
            add(item['button'], item['button_type'], item['button_states'])
        anchor = plan['protected_anchor']
        add(anchor['command_block'], anchor['command_block_type'], anchor['command_block_states'],
            {'id': n.String('CommandBlock'), 'Command': n.String(anchor['command']),
             'LastOutput': n.String('Existing output must remain'), 'unknown': n.IntArray([9, 4, 1])})
        add(anchor['sign'], anchor['sign_type'], anchor['sign_states'],
            {'id': n.String('Sign'), 'FrontText': n.Compound({'Text': n.String('拠点行き')})})
        add(anchor['button'], anchor['button_type'], anchor['button_states'])
        add([10050, 65, 10070], 'minecraft:chest', {},
            {'id': n.String('Chest'), 'CustomName': n.String('UNRELATED CHEST'),
             'Items': n.List[n.Compound]([n.Compound({'Name': n.String('minecraft:diamond'),
                                                  'Count': n.Byte(64)})])})
        self.db.putBatch({key: b''.join(records) for key, records in groups.items()})
        self.db[b'unrelated-actor-record'] = b'unchanged-actor-and-player-data'

    def get(self, key):
        return self.db.get(key) if key in self.db else None

    def blocks(self, x, y, z):
        value = self.palette.get((x, y, z))
        return [value] if value is not None else []


class CatalogWorldChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.layouts = {'games': [
            {'id': game['id'], 'production_origin': [20000 + 96 * index, 65, 20000],
             'destination': [20012.5 + 96 * index, 67, 20010.5]}
            for index, game in enumerate(load()['games'])]}
        self.plan = native_plan(self.layouts)
        self.world = FixtureWorld(self.root / 'db', self.plan)

    def tearDown(self):
        self.world.db.close()
        self.temp.cleanup()

    def test_native_commands_match_fixed_row_and_recorded_origins(self):
        for index, slot in enumerate(self.plan['placements']):
            self.assertEqual(slot['command_block'], [10048, 65, 10102 - index * 2])
            self.assertEqual(slot['destination'], self.layouts['games'][index]['destination'])
            self.assertEqual(slot['command_mode'], 'script_load')
            self.assertEqual(slot['command'], f"scriptevent junket:warp {slot['id']}")
            self.assertNotIn('@', slot['command'])
        self.assertIn('"WARP_OFFLINE_INSTALLED": true', runtime_source(self.plan))
        self.assertIn('"WARP_PRESERVE_OBSERVED_ANCHOR": true', runtime_source(self.plan))

    def test_legacy_generator_remains_unchanged(self):
        plan = build_plan(load())
        for slot in plan['placements']:
            x, y, z = slot['button']
            self.assertEqual(slot['command'],
                             f"execute as @a[tag=junket:warp_{slot['id']},x={x},y={y},z={z},r=4] "
                             f"run scriptevent junket:warp {slot['id']}")
        self.assertNotIn('WARP_OFFLINE_INSTALLED', runtime_source(plan))
        with self.assertRaisesRegex(ValueError, 'unknown JUNKET'):
            script_load_command('sparrow @p')

    def replace_commands(self, commands):
        for key in {chunk_key(at) for at in commands}:
            encoded = []
            for at, root, raw in parse_records(self.world.db[key]):
                if at in commands:
                    root['Command'] = n.String(commands[at])
                    raw = encode_record(root)
                encoded.append(raw)
            self.world.db[key] = b''.join(encoded)

    def test_all_17_previous_native_commands_migrate_without_touching_signs(self):
        apply_patch(self.world, self.plan, self.root / 'initial.json')
        self.replace_commands({tuple(slot['command_block']): previous_native_tp_command(slot)
                               for slot in self.plan['placements']})
        before, _, untouched = prepare_patch(self.world, self.plan, command_only=True)
        signs = {tuple(slot['sign']): before['records'][tuple(slot['sign'])][1]
                 for slot in self.plan['placements']}
        report = apply_patch(self.world, self.plan, self.root / 'migration.json', command_only=True)
        self.assertEqual(report['changed_block_entities'], 17)
        self.assertEqual(len(report['changed_leveldb_keys']), 3)
        self.assertEqual(report['unrelated_block_entities_unchanged'], 20)
        self.assertFalse(report['sign_block_entities_written'])
        self.assertTrue(report['protected_unchanged'])
        self.assertTrue(report['all_leveldb_keys_accounted_for'])
        self.world.db.close()
        self.world.db = leveldb.LevelDB(str(self.root / 'db'))
        self.assertTrue(verify_patch(self.world, self.plan, before, untouched)['verified'])
        after, _, _ = prepare_patch(self.world, self.plan, command_only=True)
        self.assertEqual({at: after['records'][at][1] for at in signs}, signs)
        repeated = apply_patch(self.world, self.plan, self.root / 'repeat.json', command_only=True)
        self.assertEqual(repeated['changed_block_entities'], 0)
        self.assertEqual(repeated['changed_leveldb_keys'], [])

    def test_changed_native_destination_and_wrong_game_refused_before_write(self):
        slot = self.plan['placements'][0]
        at = tuple(slot['command_block'])
        commands = [previous_native_tp_command(slot).replace('20012.5', '20013.5'),
                    'scriptevent junket:warp rabbit']
        for command in commands:
            with self.subTest(command=command):
                self.replace_commands({at: command})
                original = dict(self.world.db.items())
                with self.assertRaisesRegex(ValueError, 'another use'):
                    apply_patch(self.world, self.plan, self.root / 'rejected.json')
                self.assertEqual(dict(self.world.db.items()), original)
                self.assertFalse((self.root / 'rejected.json').exists())

    def test_command_only_requires_already_formal_signs(self):
        original = dict(self.world.db.items())
        with self.assertRaisesRegex(ValueError, 'requires existing formal sign'):
            apply_patch(self.world, self.plan, self.root / 'rejected.json', command_only=True)
        self.assertEqual(dict(self.world.db.items()), original)
        self.assertFalse((self.root / 'rejected.json').exists())

    def test_moved_or_missing_game_refused(self):
        self.layouts['games'][3]['production_origin'][0] += 1
        with self.assertRaisesRegex(ValueError, 'origin mismatch'):
            native_plan(self.layouts)
        self.layouts['games'].pop()
        with self.assertRaisesRegex(ValueError, 'exactly 17'):
            native_plan(self.layouts)

    def test_atomic_patch_preserves_return_and_all_unrelated_records(self):
        original = dict(self.world.db.items())
        before, expected_values, untouched = prepare_patch(self.world, self.plan)
        report = apply_patch(self.world, self.plan, self.root / 'rollback.json')
        self.assertTrue(report['verified'])
        self.assertEqual(len(report['slots']), 17)
        self.assertTrue(report['protected_unchanged'])
        self.assertEqual(report['unrelated_block_entities_unchanged'], 3)
        self.assertFalse(report['button_palette_written'])
        self.assertTrue(report['all_leveldb_keys_accounted_for'])
        self.assertEqual(report['unrelated_leveldb_keys_unchanged'], len(original) - len(expected_values))
        self.assertEqual(self.world.db[b'unrelated-actor-record'], original[b'unrelated-actor-record'])
        changed = {key for key, old in original.items() if self.world.db[key] != old}
        self.assertEqual(changed, set(expected_values))
        rollback = json.loads((self.root / 'rollback.json').read_text(encoding='utf-8'))
        self.assertEqual(len(rollback['records']), 3)
        self.assertTrue(verify_patch(self.world, self.plan, before, untouched)['verified'])
        # Reopen the database: this verifies disk persistence, not just a cache.
        self.world.db.close()
        self.world.db = leveldb.LevelDB(str(self.root / 'db'))
        self.assertTrue(verify_patch(self.world, self.plan)['verified'])

    def test_wrong_button_orientation_refused_before_any_write(self):
        self.world.palette[(10048, 66, 10102)]['states']['facing_direction'] = n.Int(2)
        original = dict(self.world.db.items())
        with self.assertRaisesRegex(ValueError, 'catalog block differs'):
            apply_patch(self.world, self.plan, self.root / 'rollback.json')
        self.assertEqual(dict(self.world.db.items()), original)
        self.assertFalse((self.root / 'rollback.json').exists())

    def test_pressed_button_accepted_without_palette_or_state_changes(self):
        apply_patch(self.world, self.plan, self.root / 'initial.json')
        self.replace_commands({tuple(slot['command_block']): previous_native_tp_command(slot)
                               for slot in self.plan['placements']})
        at = tuple(self.plan['placements'][0]['button'])
        self.world.palette[at]['states']['button_pressed_bit'] = n.Byte(1)
        palette_before = self.world.palette[at].snbt()
        subchunk_key = struct.pack('<ii', at[0] // 16, at[2] // 16) + b'/' + bytes([at[1] // 16])
        self.world.db[subchunk_key] = b'unchanged-native-button-palette-with-pressed-bit-1'
        raw_before = self.world.db[subchunk_key]
        report = apply_patch(self.world, self.plan, self.root / 'pressed.json', command_only=True)
        self.assertEqual(report['changed_block_entities'], 17)
        self.assertFalse(report['button_palette_written'])
        self.assertTrue(report['all_leveldb_keys_accounted_for'])
        self.assertEqual(self.world.palette[at].snbt(), palette_before)
        self.assertEqual(self.world.db[subchunk_key], raw_before)

    def test_invalid_button_press_bit_is_refused(self):
        self.world.palette[(10048, 66, 10102)]['states']['button_pressed_bit'] = n.Int(2)
        original = dict(self.world.db.items())
        with self.assertRaisesRegex(ValueError, 'button press state invalid'):
            apply_patch(self.world, self.plan, self.root / 'invalid-pressed.json')
        self.assertEqual(dict(self.world.db.items()), original)

    def test_unrelated_sign_use_refused_before_any_write(self):
        key = chunk_key([10049, 65, 10102])
        records = parse_records(self.world.db[key])
        replacements = []
        for at, root, raw in records:
            if at == (10049, 65, 10102):
                root['FrontText']['Text'] = n.String('Another existing facility')
                raw = encode_record(root)
            replacements.append(raw)
        self.world.db[key] = b''.join(replacements)
        original = dict(self.world.db.items())
        with self.assertRaisesRegex(ValueError, 'another use'):
            apply_patch(self.world, self.plan, self.root / 'rollback.json')
        self.assertEqual(dict(self.world.db.items()), original)

    def test_return_command_mismatch_refused(self):
        self.plan.pop('protected_anchor_mode')  # The ordinary installer remains strict.
        key = chunk_key([10051, 65, 10077])
        records = parse_records(self.world.db[key])
        replacements = []
        for at, root, raw in records:
            if at == (10051, 65, 10077):
                root['Command'] = n.String('tp @p 0 100 0')
                raw = encode_record(root)
            replacements.append(raw)
        self.world.db[key] = b''.join(replacements)
        with self.assertRaisesRegex(ValueError, 'protected return command mismatch'):
            prepare_patch(self.world, self.plan)

    def test_absent_historical_anchor_preserved_without_reconstruction(self):
        anchor = self.plan['protected_anchor']
        points = {tuple(anchor[role]) for role in ('command_block', 'sign', 'button')}
        for at in points:
            self.world.palette[at] = n.Compound({'name': n.String('minecraft:air'), 'states': n.Compound()})
        for key in {chunk_key(at) for at in points}:
            self.world.db[key] = b''.join(raw for at, _, raw in parse_records(self.world.db[key]) if at not in points)
        report = apply_patch(self.world, self.plan, self.root / 'rollback.json')
        self.assertTrue(report['anchor_missing'])
        self.assertTrue(report['protected_unchanged'])
        self.assertFalse(report['historical_anchor_matches'])
        self.assertTrue(all(not row['block_entity_present'] for row in report['protected']))
        self.assertTrue(all(row['block']['name'] == 'minecraft:air' for row in report['protected']))
        self.assertTrue(all(row['historical_expected']['name'] != 'minecraft:air' for row in report['protected']))

    def test_changed_historical_command_preserved_in_observed_mode(self):
        at = tuple(self.plan['protected_anchor']['command_block'])
        key = chunk_key(at)
        encoded = []
        for position, root, raw in parse_records(self.world.db[key]):
            if position == at:
                root['Command'] = n.String('tp @p 12 100 34')
                raw = encode_record(root)
            encoded.append(raw)
        self.world.db[key] = b''.join(encoded)
        report = apply_patch(self.world, self.plan, self.root / 'rollback.json')
        self.assertTrue(report['protected_unchanged'])
        self.assertFalse(report['historical_anchor_matches'])
        self.assertEqual(report['protected'][0]['command'], 'tp @p 12 100 34')

    def test_unrelated_leveldb_mutation_is_detected(self):
        def corrupt_then_verify(*args, **kwargs):
            self.world.db[b'unrelated-actor-record'] = b'corrupted'
            return verify_patch(*args, **kwargs)
        with patch('junket_catalog_world.verify_patch', side_effect=corrupt_then_verify):
            with self.assertRaisesRegex(ValueError, 'unexpected LevelDB keys'):
                apply_patch(self.world, self.plan, self.root / 'rollback.json')


if __name__ == '__main__':
    unittest.main()
