"""Rendering conversion regressions; these do not simulate a Minecraft client."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from PIL import Image
from junket_model_geometry import geometry, clean_model, texture_atlas, resolve_texture, REPEAT
from junket_render_validation import (centre_turn, create_interaction_anchor, validate_render_pack,
                                      correct_verified_monitors, write)


def model(faces=None):
    return {'textures': {'a': 'block/test'}, 'elements': [{'from': [2, 3, 4], 'to': [7, 9, 12],
        'faces': faces or {name: {'texture': '#a', 'uv': [1, 2, 11, 14]}
                           for name in ('north', 'south', 'east', 'west', 'up', 'down')}}]}


class RenderingTests(unittest.TestCase):
    def test_java_bedrock_face_names_and_up_down_uv_convention(self):
        value = geometry(model(), 'geometry.test', {'block/test': [16, 32, 16, 16]})
        cube = value['bones'][0]['cubes'][0]
        self.assertEqual(cube['origin'], [1, -5, -4])
        self.assertEqual(cube['size'], [5, 6, 8])
        self.assertEqual(cube['uv']['east'], {'uv': [17, 34], 'uv_size': [10, 12]})
        self.assertEqual(cube['uv']['up'], {'uv': [27, 46], 'uv_size': [-10, -12]})
        self.assertEqual(cube['uv']['down'], cube['uv']['up'])

    def test_east_and_west_artwork_is_not_swapped(self):
        m = model({'east': {'texture': '#a', 'uv': [0, 0, 4, 4]},
                   'west': {'texture': '#a', 'uv': [8, 8, 16, 16]}})
        uv = geometry(m, 'geometry.test', {'block/test': [0, 0, 16, 16]})['bones'][0]['cubes'][0]['uv']
        self.assertEqual(uv['east']['uv'], [0, 0])
        self.assertEqual(uv['west']['uv'], [8, 8])

    def test_hope_style_degenerate_missing_back_face_never_generates_checker(self):
        m = model(); m['elements'][0]['from'][1] = m['elements'][0]['to'][1]
        m['elements'][0]['faces']['down']['texture'] = '#missing'
        repairs = []
        geo = geometry(m, 'geometry.hope', {'block/test': [0, 0, 16, 16]}, report=repairs)
        cube = geo['bones'][0]['cubes'][0]
        self.assertEqual(set(cube['uv']), {'up'})
        self.assertGreater(cube['size'][1], 0)
        self.assertTrue(any(r.get('reason') == 'undefined_texture' for r in repairs))
        self.assertEqual(m['elements'][0]['faces']['down']['texture'], '#missing')

    def test_duplicate_surface_removed_but_transparent_overlay_retained(self):
        m = model(); m['elements'].append(deepcopy(m['elements'][0]))
        repaired = clean_model(m)
        self.assertEqual(len(repaired['elements']), 1)
        m['elements'][1]['faces']['north']['uv'] = [0, 0, 16, 16]
        repairs = []; repaired = clean_model(m, repairs)
        self.assertEqual(len(repaired['elements']), 2)
        self.assertEqual(repaired['elements'][1]['from'][2], 4-.008)
        self.assertEqual(repaired, clean_model(repaired))

    def test_texture_alias_cycle_fails_instead_of_hanging(self):
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            resolve_texture({'textures': {'a': '#b', 'b': '#a'}}, '#a')

    def test_out_of_range_uv_bakes_repeat_instead_of_clamping_or_atlas_bleed(self):
        m = model({'east': {'texture': '#a', 'uv': [16.5, 0, 0, 16]}})
        source = BytesIO(); picture = Image.new('RGBA', (16, 16), (0, 0, 0, 255))
        for x in range(16):
            for y in range(16): picture.putpixel((x, y), (x*16, y*16, 0, 255))
        image = BytesIO(); picture.save(image, format='PNG')
        with ZipFile(source, 'w') as z: z.writestr('assets/minecraft/textures/block/test.png', image.getvalue())
        with ZipFile(BytesIO(source.getvalue())) as z: sheet, atlas = texture_atlas(z, z, {'test': m}, (64, 64))
        key = 'block/test' + REPEAT + '0,0,2,1'
        self.assertIn(key, atlas)
        x, y, width, height = atlas[key]
        self.assertEqual((width, height), (32, 16))
        self.assertEqual(sheet.getpixel((x, y)), sheet.getpixel((x+16, y)))
        self.assertEqual(sheet.getpixel((x-1, y+3)), sheet.getpixel((x, y+3)))
        uv = geometry(m, 'geometry.repeat', atlas, (64, 64))['bones'][0]['cubes'][0]['uv']['east']
        self.assertEqual(uv['uv'][0], x+16.5)
        self.assertEqual(uv['uv_size'][0], -16.5)

    def test_local_pivot_turn_preserves_monitor_extents_and_does_not_modify_source(self):
        original = geometry(model(), 'geometry.monitor', {'block/test': [0, 0, 16, 16]})
        clone, pivot = centre_turn(original)
        self.assertEqual(pivot, [3.5, -2, 0])
        self.assertEqual(clone['bones'][0]['rotation'], [0, 180, 0])
        self.assertNotIn('parent', original['bones'][0])
        self.assertEqual(clone['bones'][1]['cubes'], original['bones'][0]['cubes'])

    def test_transparent_anchor_preserves_existing_hitbox_and_protection(self):
        with TemporaryDirectory() as temp:
            bp, rp = Path(temp)/'bp', Path(temp)/'rp';create_interaction_anchor(bp, rp)
            c=json.loads((bp/'entities/junket_interaction_anchor.json').read_text('utf8'))['minecraft:entity']['components']
            self.assertEqual(c['minecraft:collision_box'], {'width': .6, 'height': .6})
            self.assertEqual(c['minecraft:health'], {'value': 20, 'max': 20})
            self.assertFalse(c['minecraft:physics']['has_collision'])
            self.assertFalse(c['minecraft:damage_sensor']['triggers'][0]['deals_damage'])
            self.assertEqual(validate_render_pack(rp)['client_entities'], 1)

    def test_actual_render_alias_and_missing_material_are_rejected(self):
        with TemporaryDirectory() as temp:
            bp, rp = Path(temp)/'bp', Path(temp)/'rp';create_interaction_anchor(bp, rp)
            p=rp/'render_controllers/interaction_anchor.json'; v=json.loads(p.read_text('utf8'))
            v['render_controllers']['controller.render.junket_interaction_anchor']['geometry']='Geometry.missing';write(p,v)
            with self.assertRaisesRegex(ValueError, 'Unbound render alias'):validate_render_pack(rp)
            create_interaction_anchor(bp, rp)
            p=rp/'entity/junket_interaction_anchor.entity.json';v=json.loads(p.read_text('utf8'))
            v['minecraft:client_entity']['description']['materials']['default']='undefined_material';write(p,v)
            with self.assertRaisesRegex(ValueError, 'Unknown material'):validate_render_pack(rp)


if __name__ == '__main__':
    unittest.main()
