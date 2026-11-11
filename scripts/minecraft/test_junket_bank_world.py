"""Bank-native placement regression tests; synthetic worlds, no client claims."""
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest

import leveldb
import nbtlib as n

from junket_bank_world import (BOX, ORIGIN, SIZE, BankPlacement, apply_bank, digest_json,
                               prepare_bank_plan, positions, validate_plan)
from junket_catalog_world import encode_record, parse_records
from junket_native_world import (AIR, NativePlacement, decode_section, encode_section,
                                 prefix, section_key)
from junket_structure import ConvertedWorld


def block(name, states=None):
    return n.Compound({'name': n.String('minecraft:'+name), 'states': n.Compound(states or {}),
                       'version': n.Int(18168865)})


class BankWorldChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory(); cls.base = Path(cls.shared.name)
        cls.seed = cls.base/'seed'; cls.seed.mkdir()
        db = leveldb.LevelDB(str(cls.seed/'db'), create_if_missing=True)
        palette = [AIR, block('dirt'), block('grass_block'), block('netherite_block')]
        for cx in range(BOX[0][0]//16, BOX[1][0]//16+1):
            for cz in range(BOX[0][2]//16, BOX[1][2]//16+1):
                p = prefix(cx, cz)
                db[p+b','] = b'\x2a'; db[p+b'+'] = b'\0'*512+b'biome-tail-protected'
                for sy in (3, 4):
                    section = decode_section(None, sy)
                    section['layers'][0]['palette'] = deepcopy(palette)
                    for lx in range(16):
                        for lz in range(16):
                            x, z = cx*16+lx, cz*16+lz
                            divider = z in (310, 311) or (x in (-170, -169) and 293 <= z <= 313)
                            for ly in range(16):
                                y = sy*16+ly
                                val = 1 if y <= 63 else (1 if divider else 2) if y == 64 else 3 if y == 65 and divider else 0
                                section['layers'][0]['indices'][lx*256+lz*16+ly] = val
                    db[section_key(cx, sy, cz)] = encode_section(section, sy)
        db[b'actorprefix-original-villager'] = b'original-entity-state'
        db[b'~local_player'] = b'player-inventory-location'
        # Unrelated chest in a shared chunk immediately outside the footprint.
        cls.protected_at = (-195, 66, 284)
        tile = n.File({'id': n.String('Chest'), **{k: n.Int(v) for k, v in zip('xyz', cls.protected_at)},
                       'CustomName': n.String('Protected inventory')})
        cls.tile_key = prefix(-195//16, 284//16)+b'1'
        cls.protected_tile = encode_record(tile); db[cls.tile_key] = cls.protected_tile
        db.close()
        # A synthetic two-layer source with a decorative sign; source generation
        # fidelity is separately checked against the private Chunker conversion.
        palette = [AIR, block('sandstone'), block('polished_andesite'), block('birch_wall_sign'), block('water'), block('smooth_quartz')]
        layers = [[], []]
        for x in range(SIZE[0]):
            for y in range(SIZE[1]):
                for z in range(SIZE[2]):
                    layers[0].append(1 if y == 0 else 2 if y == 1 else 3 if (x,y,z)==(5,4,5) else 5 if (x,y,z) in ((7,3,26),(8,3,26)) else 0)
                    layers[1].append(4 if (x,y,z)==(5,4,5) else -1)
        i = 5*SIZE[1]*SIZE[2]+4*SIZE[2]+5
        source = n.File({'format_version': n.Int(1), 'size': n.List[n.Int](SIZE),
            'structure_world_origin': n.List[n.Int]([0,0,0]), 'structure': n.Compound({
                'entities': n.List[n.Compound](), 'block_indices': n.List[n.List[n.Int]]([n.List[n.Int](v) for v in layers]),
                'palette': n.Compound({'default': n.Compound({'block_palette': n.List[n.Compound](palette),
                    'block_position_data': n.Compound({str(i): n.Compound({'block_entity_data': n.Compound({
                        'id': n.String('Sign'), 'x': n.Int(5), 'y': n.Int(4), 'z': n.Int(5),
                        'FrontText': n.Compound({'Text': n.String('Original bank decoration')})})})})})})})})
        cls.structure = cls.base/'source.mcstructure'; source.save(cls.structure, byteorder='little')
        cls.plan = prepare_bank_plan(cls.seed, cls.structure, cls.base/'before.json')

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.working = self.root/'world'; shutil.copytree(self.seed, self.working)

    def tearDown(self):
        self.temp.cleanup()

    def test_ledger_covers_every_cell_and_is_coordinate_bound(self):
        validate_plan(self.plan)
        self.assertEqual(self.plan['origin'], ORIGIN)
        self.assertEqual(sum(c for _, c in self.plan['before']['layers_rle'][0]), 42780)
        self.assertEqual(sum(c for _, c in self.plan['before']['layers_rle'][1]), 42780)
        self.assertEqual(self.plan['before']['primary_counts']['minecraft:netherite_block'], 130)

    def test_edited_ledger_or_footprint_is_rejected(self):
        plan = deepcopy(self.plan); plan['origin'][0] -= 1
        with self.assertRaisesRegex(ValueError, 'digest'):
            validate_plan(plan)
        plan['plan_sha256'] = digest_json({k:v for k,v in plan.items() if k != 'plan_sha256'})
        with self.assertRaisesRegex(ValueError, 'unauthorized'):
            validate_plan(plan)

    def test_live_block_changed_after_survey_rejected_without_write(self):
        world = ConvertedWorld(self.working)
        try:
            p = NativePlacement(world); p.set((-180, 65, 300), block('diamond_block'))
            world.db.putBatch(p.values())
            before = {k:v for k,v in world.db.items()}
            with self.assertRaisesRegex(ValueError, 'before ledger'):
                BankPlacement(world).stage_bank(self.plan, self.structure)
            self.assertEqual({k:v for k,v in world.db.items()}, before)
        finally:
            world.db.close()

    def test_existing_inventory_in_plot_is_rejected(self):
        world = ConvertedWorld(self.working)
        try:
            at = (-180, 65, 300); key = prefix(at[0]//16, at[2]//16)+b'1'
            world.db[key] = encode_record(n.File({'id': n.String('Chest'), **{k:n.Int(v) for k,v in zip('xyz',at)}}))
            with self.assertRaisesRegex(ValueError, 'block entities changed'):
                BankPlacement(world).stage_bank(self.plan, self.structure)
        finally:
            world.db.close()

    def test_backup_and_stop_required(self):
        for stopped, backup in [(False, self.seed), (True, self.root/'missing')]:
            with self.assertRaisesRegex(ValueError, 'stopped world'):
                apply_bank(self.working, self.plan, self.structure, self.root/'rollback.json',
                           full_backup=backup, world_is_stopped=stopped)

    def test_native_commit_preserves_outside_underground_actors_and_two_layers(self):
        world = ConvertedWorld(self.working)
        try:
            p = NativePlacement(world)
            p.set(self.protected_at, block('chest'))
            p.set((-180, 62, 300), block('diamond_block'))
            p.set(self.protected_at, block('water'), 1)
            world.db.putBatch(p.values())
            before_db = {k:v for k,v in world.db.items()}
        finally:
            world.db.close()
        proof = apply_bank(self.working, self.plan, self.structure, self.root/'rollback.json',
                           full_backup=self.seed, world_is_stopped=True)
        self.assertTrue(proof['verified']); self.assertTrue(proof['actors_and_players_preserved'])
        self.assertEqual(proof['bank']['structure_and_overlay_cells_verified'], 42780)
        self.assertTrue(proof['bank']['underground_through_y62_unchanged'])
        self.assertGreater(proof['bank']['outside_footprint_cells_verified'], 0)
        world = ConvertedWorld(self.working)
        try:
            p = NativePlacement(world)
            self.assertEqual(str(p.get((-180,62,300))['name']), 'minecraft:diamond_block')
            self.assertEqual(str(p.get(self.protected_at)['name']), 'minecraft:chest')
            self.assertEqual(str(p.get(self.protected_at,1)['name']), 'minecraft:water')
            self.assertEqual(str(p.get(tuple(a+b for a,b in zip(ORIGIN,[5,4,5])),1)['name']), 'minecraft:water')
            protected_records = {at: raw for at,_,raw in parse_records(world.get(self.tile_key))}
            self.assertEqual(protected_records[self.protected_at], self.protected_tile)
            for key in (b'actorprefix-original-villager', b'~local_player'):
                self.assertEqual(world.get(key), before_db[key])
            self.assertTrue((self.root/'rollback.json').exists())
        finally:
            world.db.close()

    def test_overlay_outside_panel_rejected_before_write(self):
        plan = deepcopy(self.plan); plan['block_overlays'][0]['location']['x'] += 10
        plan['plan_sha256'] = digest_json({k:v for k,v in plan.items() if k!='plan_sha256'})
        world = ConvertedWorld(self.working)
        try:
            before = {k:v for k,v in world.db.items()}
            with self.assertRaisesRegex(ValueError, 'two-control contract'):
                BankPlacement(world).stage_bank(plan, self.structure)
            self.assertEqual({k:v for k,v in world.db.items()}, before)
        finally:
            world.db.close()


if __name__ == '__main__':
    unittest.main()
