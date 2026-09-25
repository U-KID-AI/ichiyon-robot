"""Geometry adjacency, alpha, inventory and managed-build regressions."""
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.build_aquarium_glass import (
    BP, RP, GROUP, COLORS, DIRECTIONS,
    CLEAR_ALPHA, COLORED_ALPHA, PACK_VERSION,
    generated_files, identifier,
)
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
            image = Image.open(
                BytesIO(
                    self.files[
                        RP
                        + f'textures/blocks/'
                        f'aquarium_glass_{color}_body.png'
                    ]
                )
            )

            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(
                image.size,
                (16, 16),
            )

            self.assertEqual(
                len(set(image.getdata())),
                1,
            )

            self.assertEqual(
                image.getpixel((8, 8))[3],
                (
                    CLEAR_ALPHA
                    if color == 'clear'
                    else COLORED_ALPHA
                ),
            )

            self.assertNotIn(
                RP
                + f'textures/blocks/'
                f'aquarium_glass_{color}_edge.png',
                self.files,
            )
    def test_canonical_single_full_cube_geometry(self):
        geometry = self.doc(
            RP
            + 'models/blocks/'
            + 'aquarium_glass.geo.json'
        )

        self.assertEqual(
            geometry['format_version'],
            '1.26.50',
        )

        bones = geometry[
            'minecraft:geometry'
        ][0]['bones']

        self.assertEqual(len(bones), 1)
        self.assertEqual(
            bones[0]['name'],
            'glass',
        )

        cubes = bones[0]['cubes']
        self.assertEqual(len(cubes), 1)

        cube = cubes[0]

        self.assertEqual(
            cube['origin'],
            [-8, 0, -8],
        )

        self.assertEqual(
            cube['size'],
            [16, 16, 16],
        )

        self.assertEqual(
            set(cube['uv']),
            {
                'down',
                'up',
                'north',
                'south',
                'west',
                'east',
            },
        )

        for face in cube['uv'].values():
            self.assertNotIn(
                'material_instance',
                face,
            )
    def test_no_zero_size_cube_anywhere(self):
        bones = self.doc(
            RP
            + 'models/blocks/'
            + 'aquarium_glass.geo.json'
        )['minecraft:geometry'][0]['bones']

        for bone in bones:
            for cube in bone['cubes']:
                self.assertTrue(
                    all(
                        value > 0
                        for value
                        in cube['size']
                    )
                )
    def test_canonical_six_face_same_block_culling(self):
        rules = self.doc(
            RP
            + 'block_culling/'
            + 'aquarium_glass.json'
        )[
            'minecraft:block_culling_rules'
        ]['rules']

        self.assertEqual(
            len(rules),
            6,
        )

        self.assertEqual(
            {
                rule['direction']
                for rule in rules
            },
            set(DIRECTIONS),
        )

        for rule in rules:
            face = rule['direction']

            self.assertEqual(
                rule['condition'],
                'same_block',
            )

            self.assertEqual(
                rule['geometry_part'],
                {
                    'bone': 'glass',
                    'cube': 0,
                    'face': face,
                },
            )
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

    def test_all_17_colors_use_canonical_wildcard_material(self):
        self.assertEqual(
            len(COLORS),
            17,
        )

        for color, _, _ in COLORS:
            stem = 'aquarium_glass_' + color

            block = self.doc(
                BP
                + f'blocks/{stem}.json'
            )['minecraft:block']

            self.assertEqual(
                block['format_version']
                if 'format_version' in block
                else '1.26.50',
                '1.26.50',
            )

            components = block[
                'components'
            ]

            self.assertEqual(
                components[
                    'minecraft:geometry'
                ],
                {
                    'identifier':
                        'geometry.ichiyon.aquarium_glass',
                    'culling':
                        'ichiyon:aquarium_glass',
                },
            )

            materials = components[
                'minecraft:material_instances'
            ]

            self.assertEqual(
                set(materials),
                {'*'},
            )

            self.assertEqual(
                materials['*']['texture'],
                f'aquarium_glass_{color}_body',
            )

            self.assertEqual(
                materials['*']['render_method'],
                'blend',
            )
    def test_pack_version_and_uuid_stability(self):
        manifest = self.doc(
            RP + 'manifest.json'
        )

        self.assertEqual(
            manifest['header']['version'],
            PACK_VERSION,
        )

        self.assertEqual(
            manifest['modules'][0]['version'],
            PACK_VERSION,
        )

        self.assertEqual(
            manifest['header']['uuid'],
            'fe55c87e-d7a2-4e3c-88fb-43009f65df75',
        )

        self.assertEqual(
            manifest['modules'][0]['uuid'],
            'a3d30f1b-8445-4e46-a670-e53433ec8d7a',
        )
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
