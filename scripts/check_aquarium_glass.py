"""Geometry adjacency, alpha, inventory and managed-build regressions."""
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.build_aquarium_glass import BP, RP, GROUP, COLORS, DIRECTIONS, generated_files, identifier
from bot.services.minecraft_cosmetics_pack import builtin_assets, compile_files
from bot.services.minecraft_resource_packs import DIRECT_RESOURCE_PACKS
from scripts.aquarium_glass_migration import PLAN, MAPPING, cells, commands, verify


class GlassChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = generated_files()

    def doc(self, path):
        return json.loads(self.files[path])

    def test_reproducible_committed_outputs(self):
        for name, expected in self.files.items():
            raw = (ROOT / 'minecraft' / name).read_bytes()
            self.assertEqual(raw if name.endswith('.png') else raw.replace(b'\r\n', b'\n'), expected, name)

    def test_uniform_translucency_no_texture_grid(self):
        for color, _, _ in COLORS:
            for material in ('body', 'edge'):
                im = Image.open(BytesIO(self.files[RP + f'textures/blocks/aquarium_glass_{color}_{material}.png']))
                self.assertEqual(im.mode, 'RGBA')
                self.assertEqual(len(set(im.getdata())), 1)
                expected = 255 if color == 'red' and material == 'edge' else 235 if color == 'red' and material == 'body' else 104 if material == 'edge' else (8 if color == 'clear' else 24)
                self.assertEqual(im.getpixel((16, 16))[3], expected)

    def test_six_faces_and_twenty_four_thin_separate_rims(self):
        bones = self.doc(RP + 'models/blocks/aquarium_glass.geo.json')['minecraft:geometry'][0]['bones']
        self.assertEqual(len(bones), 30)
        for bone in bones:
            cube = bone['cubes'][0]
            self.assertEqual(len(cube['uv']), 1)
            if '_' in bone['name']:
                self.assertEqual(sorted(cube['size']), [0, 0.25, 16])

    def test_adjacent_glass_removes_internal_faces_and_rims_only(self):
        rules = self.doc(RP + 'block_culling/aquarium_glass.json')['minecraft:block_culling_rules']['rules']
        self.assertEqual(len(rules), 54)
        for face, (axis, _) in DIRECTIONS.items():
            self.assertEqual([r['direction'] for r in rules if r['geometry_part']['bone'] == face], [face])
            for edge, (edge_axis, _) in DIRECTIONS.items():
                if edge_axis == axis:
                    continue
                paired = [r for r in rules if r['geometry_part']['bone'] == face + '_' + edge]
                self.assertEqual({r['direction'] for r in paired}, {face, edge})
                self.assertTrue(all(r['condition'] == 'same_block' for r in paired))
                lateral = next(r for r in paired if r['direction'] == edge)
                self.assertFalse(lateral['cull_against_full_and_opaque'])

    def test_building_behavior_and_silk_touch_loot(self):
        for color, _, _ in COLORS:
            stem = 'aquarium_glass_' + color
            c = self.doc(BP + f'blocks/{stem}.json')['minecraft:block']['components']
            self.assertIs(c['minecraft:collision_box'], True)
            self.assertIs(c['minecraft:selection_box'], True)
            self.assertEqual(c['minecraft:light_dampening'], 0)
            self.assertFalse(any('tick' in k or 'custom' in k for k in c))
            entry = self.doc(BP + f'loot_tables/blocks/{stem}.json')['pools'][0]['entries'][0]
            self.assertEqual(entry['name'], identifier(color))
            self.assertEqual(entry['conditions'][0]['enchantments'][0]['enchantment'], 'silk_touch')

    def test_red_diagnostic_uses_full_block_without_custom_culling(self):
        c = self.doc(BP + 'blocks/aquarium_glass_red.json')['minecraft:block']['components']
        self.assertEqual(c['minecraft:geometry'], 'minecraft:geometry.full_block')
        self.assertEqual(set(c['minecraft:material_instances']), {'*'})
        self.assertEqual(c['minecraft:material_instances']['*']['render_method'], 'blend')
        self.assertNotIn('culling', c['minecraft:geometry'] if isinstance(c['minecraft:geometry'], dict) else {})
        other = self.doc(BP + 'blocks/aquarium_glass_blue.json')['minecraft:block']['components']
        self.assertIsInstance(other['minecraft:geometry'], dict)
        self.assertEqual(other['minecraft:geometry']['culling'], 'ichiyon:aquarium_glass')

    def test_inventory_order_and_managed_compiler_preservation(self):
        compiled = compile_files(ROOT / 'minecraft', builtin_assets(ROOT / 'minecraft'))
        path = BP + 'item_catalog/crafting_item_catalog.json'
        for document in (self.doc(path), json.loads(compiled[path])):
            categories = document['minecraft:crafting_items_catalog']['categories']
            groups = [g for c in categories for g in c['groups'] if g['group_identifier']['name'] == GROUP]
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]['items'], [identifier(c[0]) for c in COLORS])
        self.assertIn(RP, DIRECT_RESOURCE_PACKS)

    def test_no_vanilla_texture_replacement_and_complete_translations(self):
        blocks = self.doc(RP + 'blocks.json')
        self.assertEqual(set(blocks) - {'format_version'}, {identifier(c[0]) for c in COLORS})
        for locale in ('en_US', 'ja_JP'):
            text = self.files[RP + f'texts/{locale}.lang'].decode()
            for color, _, _ in COLORS:
                self.assertIn(f'tile.{identifier(color)}.name=', text)

    def test_exact_guarded_migration_and_reverse(self):
        plan = json.loads(PLAN.read_text())
        positions = cells(plan)
        self.assertEqual(len(positions), 1875)
        forward, reverse = commands(plan), commands(plan, reverse=True)
        self.assertEqual(len(forward), 502)
        for old, undo in zip(forward, reverse):
            self.assertTrue(old.startswith('execute in overworld run fill '))
            self.assertIn(' replace minecraft:', old)
            self.assertIn(' replace ichiyon:aquarium_glass_', undo)
            self.assertEqual(old.split()[5:11], undo.split()[5:11])

    def test_migration_verifies_all_other_surveyed_blocks(self):
        plan = json.loads(PLAN.read_text())
        before = {p: (old, {}) for p, old in cells(plan).items()}
        after = {p: (MAPPING[old], {}) for p, (old, _) in before.items()}
        outside = (-2, 95, 230)  # Neighboring building's black pane is excluded.
        before[outside] = after[outside] = ('minecraft:black_stained_glass_pane', {})
        self.assertEqual(verify(before, after, plan), 1875)
        after[outside] = ('minecraft:air', {})
        with self.assertRaisesRegex(AssertionError, 'outside allowlist'):
            verify(before, after, plan)

    def test_plan_rejects_overreach_and_stale_original(self):
        plan = json.loads(PLAN.read_text())
        plan['runs'][0][0] = 29
        with self.assertRaises(AssertionError):
            cells(plan)
        plan = json.loads(PLAN.read_text())
        before = {p: (old, {}) for p, old in cells(plan).items()}
        after = {p: (MAPPING[old], {}) for p, (old, _) in before.items()}
        before[next(iter(before))] = ('minecraft:stone', {})
        with self.assertRaisesRegex(AssertionError, 'original changed'):
            verify(before, after, plan)


if __name__ == '__main__':
    unittest.main()
