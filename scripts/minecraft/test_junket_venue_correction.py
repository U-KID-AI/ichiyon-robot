"""Bounded source completion must never rewrite existing buildings or inventories."""
from copy import deepcopy
from hashlib import sha256
import unittest
import nbtlib as n

from junket_native_world import AIR, NativePlacement, commit_placement, database_hashes, section_key
from junket_venue_correction import reception_corrections, stage_extension
import test_junket_native_world as native_test


class ReceptionCorrectionTests(unittest.TestCase):
    def layout(self):
        previous = {'source_sha256': 'a' * 64, 'games': [{'id': 'hope',
            'production_origin': [20000, 65, 20000], 'translation': [19900, 31, 19700],
            'size': [19, 35, 40], 'destination': [20016, 87, 20023],
            'structures': [{'path': 'old', 'sha256': 'b' * 64, 'origin': [20000, 65, 20000], 'size': [19, 35, 40]}]}]}
        current = deepcopy(previous)
        current['reception_revision'] = 1
        game = current['games'][0]
        game.update(size=[19, 40, 40], destination=[20010, 101, 20014.5], reception_evidence={'source_command': 'tp source gallery'})
        game['structures'].append({'path': 'new', 'sha256': 'c' * 64, 'origin': [20000, 100, 20000], 'size': [19, 5, 40]})
        return current, previous

    def test_accepts_only_additive_gallery_and_sourced_entry(self):
        current, previous = self.layout()
        change = reception_corrections(current, previous)[0]
        self.assertEqual(change['production_origin'], [20000, 100, 20000])
        self.assertEqual(change['size'], [19, 5, 40])
        self.assertEqual(len(change['structures']), 1)

    def test_rejects_unrelated_or_unproven_changes(self):
        edits = [lambda c: c.update(source_sha256='d' * 64),
                 lambda c: c['games'][0].update(production_origin=[1, 2, 3]),
                 lambda c: c['games'][0].update(reception_evidence=None),
                 lambda c: c['games'][0].update(block_overlays=[{'block': 'air'}]),
                 lambda c: c['games'][0].update(destination=[20010, 130, 20014]),
                 lambda c: c['games'][0]['structures'][0].update(sha256='d' * 64),
                 lambda c: c['games'][0]['structures'][1].update(origin=[20000, 99, 20000])]
        for edit in edits:
            current, previous = self.layout()
            edit(current)
            with self.assertRaises(ValueError):
                reception_corrections(current, previous)


class ExtensionDatabaseTests(unittest.TestCase):
    setUp = native_test.NativeWorldChecks.setUp
    tearDown = native_test.NativeWorldChecks.tearDown

    def extension(self):
        game = deepcopy(self.game)
        game['production_origin'][1] = 70
        game['structures'][0]['origin'][1] = 70
        game['structures'][0]['sha256'] = sha256(self.root.joinpath('original.mcstructure').read_bytes()).hexdigest()
        return game

    def test_additive_restore_preserves_lower_building_actor_and_foreign_chest(self):
        lower = self.world.blocks(20000, 65, 20000)
        foreign = self.world.get(self.p + b'1')
        placement = NativePlacement(self.world)
        stage_extension(placement, self.extension())
        proof = commit_placement(self.world, placement, self.root / 'rollback.json')
        self.assertTrue(proof['unrelated_leveldb_keys_unchanged'])
        self.assertEqual(self.world.blocks(20000, 65, 20000), lower)
        for key, value in self.metadata.items():
            self.assertEqual(self.world.get(key), value)
        self.assertIn(foreign, self.world.get(self.p + b'1'))
        self.assertEqual(str(self.world.blocks(20000, 70, 20000)[0]['name']), 'minecraft:gold_block')

    def test_conflicting_building_is_refused_before_any_write(self):
        occupied = NativePlacement(self.world)
        occupied.set((20000, 70, 20000), native_test.block('diamond_block'))
        commit_placement(self.world, occupied, self.root / 'occupied-rollback.json')
        before = database_hashes(self.world)
        with self.assertRaisesRegex(ValueError, 'collides'):
            stage_extension(NativePlacement(self.world), self.extension())
        self.assertEqual(database_hashes(self.world), before)

    def test_repeating_identical_extension_preserves_every_database_value(self):
        placement = NativePlacement(self.world)
        stage_extension(placement, self.extension())
        commit_placement(self.world, placement, self.root / 'first.json')
        before = database_hashes(self.world)
        placement = NativePlacement(self.world)
        stage_extension(placement, self.extension())
        proof = commit_placement(self.world, placement, self.root / 'second.json')
        self.assertEqual(database_hashes(self.world), before)
        self.assertEqual(proof['changed_leveldb_keys'], [])

    def test_source_air_version_does_not_materialize_empty_storages(self):
        for sy, raw in ((6, None), (7, bytes((9, 0, 7)))):
            key = section_key(1250, sy, 1250)
            if raw is not None:
                self.world.db.putBatch({key: raw})
            source_air = deepcopy(AIR)
            source_air['version'] = n.Int(18168865)
            placement = NativePlacement(self.world)
            placement.set((20000, sy * 16 + 4, 20000), source_air)
            proof = commit_placement(self.world, placement, self.root / f'air-{sy}.json')
            self.assertTrue(proof['verified'])
            self.assertEqual(self.world.get(key), raw)


if __name__ == '__main__':
    unittest.main()
