"""Validate glasses geometry, wearer orientation and existing pack integration."""
from io import BytesIO
import json
import unittest
from PIL import Image
from build_narita_glasses import compile_glasses, geometry, ITEM, BP, RP, ATLAS_ENTRIES, LANG_ENTRIES


class GlassesTests(unittest.TestCase):
    def test_wearable_is_head_only_and_non_armored(self):
        value = json.loads(compile_glasses()[BP + 'items/narita_glasses.json'])['minecraft:item']
        self.assertEqual(value['description']['menu_category'], {'category': 'equipment', 'group': 'minecraft:itemGroup.name.helmet'})
        self.assertEqual(value['components']['minecraft:wearable'], {'slot': 'slot.armor.head', 'protection': 0})

    def test_wearer_right_circle_and_left_square(self):
        bones = {b['name']: b for b in geometry()['minecraft:geometry'][0]['bones']}
        right, left = bones['wearer_right_round']['cubes'], bones['wearer_left_square']['cubes']
        self.assertTrue(all(c['origin'][0] + c['size'][0] < 0 for c in right))
        self.assertTrue(all(c['origin'][0] > 0 for c in left))
        self.assertEqual(len(right), 8)
        self.assertEqual(sum('rotation' in c for c in right), 4)
        self.assertTrue(all(c['rotation'][2] % 45 == 0 and c['rotation'][2] % 90 != 0 for c in right if 'rotation' in c))
        self.assertEqual(len(left), 4)
        self.assertTrue(all('rotation' not in c for c in left))
        self.assertGreater(max(c['size'][0] for c in left), max(c['size'][0] for c in right if 'rotation' not in c))
        self.assertTrue(all(min(c['size']) >= 1 for c in right + left))
        self.assertTrue(all(b['parent'] == 'head' for n, b in bones.items() if n.startswith('wearer_')))
        self.assertNotIn('mirror', bones['wearer_right_round'])

    def test_front_frames_and_temples_clear_the_skin(self):
        bones = geometry()['minecraft:geometry'][0]['bones']
        for bone in bones:
            if bone['name'].startswith('wearer_'):
                for c in bone['cubes']:
                    # Rear face stays in front of the inflated hat (z=-4.5).
                    # Checking only the front face would miss a deep bar whose
                    # back sinks into the hair layer.
                    self.assertLessEqual(c['origin'][2] + c['size'][2], -4.95)
                    self.assertGreaterEqual(min(c['size']), 1)
                    self.assertGreaterEqual(c['origin'][1], 24)
                    self.assertLessEqual(c['origin'][1] + c['size'][1], 32)
        self.assertLess(sum(len(b.get('cubes', [])) for b in bones), 100)
        for bone in bones:
            for c in bone.get('cubes', []):
                self.assertGreaterEqual(min(c['size']), 1)
        temples = next(b for b in bones if b['name'] == 'bridge_and_temples')['cubes'][-2:]
        self.assertAlmostEqual(temples[0]['origin'][0] + temples[0]['size'][0], -4.8)
        self.assertAlmostEqual(temples[1]['origin'][0], 4.8)
        for c in temples:
            self.assertAlmostEqual(c['origin'][2] + c['size'][2], 3)

    def test_no_overlapping_circular_voxels(self):
        cells = geometry()['minecraft:geometry'][0]['bones'][3]['cubes']
        locations = {tuple(c['origin']) for c in cells}
        self.assertEqual(len(locations), len(cells))
        for a in cells:
            for b in cells:
                if a is b:
                    continue
                overlap = all(max(a['origin'][i], b['origin'][i]) <
                              min(a['origin'][i]+a['size'][i], b['origin'][i]+b['size'][i]) for i in range(3))
                # Joining bars share a silhouette but sit on separate planes.
                if overlap:
                    self.assertGreaterEqual(abs(a['origin'][2] - b['origin'][2]), 0.05)

    def test_assets_reference_one_id_and_do_not_modify_player_skin(self):
        files = compile_glasses()
        desc = json.loads(files[RP + 'attachables/narita_glasses.player.json'])['minecraft:attachable']['description']
        self.assertEqual(desc['identifier'], ITEM)
        self.assertEqual(list(desc['item']), [ITEM])
        self.assertEqual(desc['item'][ITEM], "query.is_owner_identifier_any('minecraft:player')")
        self.assertEqual(desc['geometry']['default'], 'geometry.ichiyon.narita_glasses')
        self.assertNotIn('parent_setup', desc['scripts'])
        self.assertFalse(any('/entity/player' in p or 'manifest.json' in p for p in files))
        for p in ('textures/items/narita_glasses.png', 'textures/entity/narita_glasses.png'):
            with Image.open(BytesIO(files[RP+p])) as img:
                self.assertEqual(img.mode, 'RGBA')
        self.assertEqual(ATLAS_ENTRIES[ITEM]['textures'], 'textures/items/narita_glasses')
        self.assertEqual(LANG_ENTRIES['ja_JP']['item.'+ITEM+'.name'], '成田眼鏡')

    def test_equipped_model_binds_to_head_without_body_offset_or_scale(self):
        bones = {b['name']: b for b in geometry()['minecraft:geometry'][0]['bones']}
        self.assertEqual(bones['head']['binding'], "'head'")
        self.assertEqual(bones['head']['pivot'], [0, 24, 0])
        for name in ('wearer_right_round', 'wearer_left_square', 'bridge_and_temples'):
            self.assertEqual(bones[name]['parent'], 'head')
            self.assertEqual(bones[name]['pivot'], bones['head']['pivot'])
            # Eye-centered band. The round corners reach Y=25..29 and the
            # center stays at 27, on the face rather than the forehead.
            for c in bones[name]['cubes']:
                self.assertGreaterEqual(c['origin'][1] - 24, 1)
                self.assertLessEqual(c['origin'][1] + c['size'][1] - 24, 5)
        self.assertFalse(any('scale' in b or 'rotation' in b for b in bones.values()))

    def test_frame_center_matches_client_measured_eye_height(self):
        measured_lowering = (729 - 704) / (776 - 643) * 8
        self.assertAlmostEqual(measured_lowering, 1.5, delta=.01)
        bones = {b['name']: b for b in geometry()['minecraft:geometry'][0]['bones']}
        for name in ('wearer_right_round', 'wearer_left_square'):
            cells = bones[name]['cubes']
            low = min(c['origin'][1] for c in cells)
            high = max(c['origin'][1] + c['size'][1] for c in cells)
            self.assertEqual((low + high) / 2, 27.0)
        # The round lens is an octagon around its center. A rotated corner that
        # travels past this radius is the old gear tooth.
        import math
        right = bones['wearer_right_round']['cubes']
        center_x = -2.25
        for cube in right:
            pivot = cube.get('pivot', [cube['origin'][0] + cube['size'][0] / 2,
                                        cube['origin'][1] + cube['size'][1] / 2,
                                        cube['origin'][2] + cube['size'][2] / 2])
            angle = math.radians(cube.get('rotation', [0, 0, 0])[2])
            cos, sin = math.cos(angle), math.sin(angle)
            for dx in (0, cube['size'][0]):
                for dy in (0, cube['size'][1]):
                    local_x = cube['origin'][0] + dx - pivot[0]
                    local_y = cube['origin'][1] + dy - pivot[1]
                    world_x = pivot[0] + local_x * cos - local_y * sin
                    world_y = pivot[1] + local_x * sin + local_y * cos
                    radius = math.hypot(world_x - center_x, world_y - 27)
                    self.assertLessEqual(radius, 1.90)
                    self.assertGreaterEqual(radius, 0.70)

    def test_render_controller_cannot_hide_inventory_or_third_person_model(self):
        files = compile_glasses()
        controller = json.loads(files[RP + 'render_controllers/narita_glasses.render_controllers.json'])['render_controllers']['controller.render.ichiyon.narita_glasses']
        self.assertNotIn('part_visibility', controller)
        self.assertEqual(controller['geometry'], 'Geometry.default')
        self.assertEqual(controller['materials'], [{'*': 'Material.default'}])
        self.assertEqual(controller['textures'], ['Texture.default'])
        with Image.open(BytesIO(files[RP + 'textures/entity/narita_glasses.png'])) as texture:
            self.assertEqual(texture.getchannel('A').getextrema(), (255, 255))

    def test_head_rotations_keep_frame_on_face(self):
        import math
        # The chosen fixture's local front point must rotate with the head;
        # no frame can be bound to waist/body while merely looking correct idle.
        bones = {b['name']: b for b in geometry()['minecraft:geometry'][0]['bones']}
        for name in ('wearer_right_round', 'wearer_left_square'):
            self.assertEqual(bones[name]['parent'], 'head')
            for c in bones[name]['cubes']:
                local = [c['origin'][0], c['origin'][1] - 24, c['origin'][2]]
                for degrees in (-90, -45, 0, 45, 90):
                    angle = math.radians(degrees)
                    point = [local[0]*math.cos(angle)+local[2]*math.sin(angle), local[1],
                             -local[0]*math.sin(angle)+local[2]*math.cos(angle)]
                    front = [math.sin(angle), 0, math.cos(angle)]
                    self.assertLess(sum(p*f for p, f in zip(point, front)), -4.5)

    def test_compiler_is_deterministic(self):
        self.assertEqual(compile_glasses(), compile_glasses())


if __name__ == '__main__':
    unittest.main()
