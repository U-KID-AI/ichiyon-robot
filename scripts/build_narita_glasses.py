"""Compile the head-slot Narita glasses into the existing avatar pack layout.

The caller merges atlas/localization entries with the existing managed catalog.
This compiler never edits a manifest, player skin or installed server pack.
"""
from io import BytesIO
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

ITEM = 'ichiyon:narita_glasses'
BP = 'behavior_packs/ichiyon_avatar_bp/'
RP = 'resource_packs/ichiyon_avatar_rp/'
BRIDGE = 'behavior_packs/import_structures/'
ROOT = Path(__file__).resolve().parents[1]
# Measured eye line. The old voxel ring was centered at 28.5; the client
# measurement lowered that center by 1.5, and this mesh keeps Y=27.
FRAME_CENTER_Y = 27.0
# Every member is at least 1 model pixel on each axis. The previous ring was
# 0.25 cubes with 0.3-deep bars, so each row became a separate scanline and
# the verticals dropped out as the head pitched. Moving that mesh did not
# change the fragment size.
FRAME_DEPTH = 1.0
# Standard hat layer is z=-4.5. Keep a 0.5px gap, then extend forward.
FRAME_BACK_Z = -5.0
FRAME_FRONT_Z = FRAME_BACK_Z - FRAME_DEPTH
TEMPLE_INNER_X = 4.8
TEMPLE_THICKNESS = 1.0
TEMPLE_OUTER_X = TEMPLE_INNER_X + TEMPLE_THICKNESS
LENS_HALF = 1.75
# Regular octagon with the same flat-to-flat width as the square lens.
# Vertices land on the diagonals; they are not extra cubes stuck outside it.
OCTAGON_HALF = LENS_HALF * math.tan(math.pi / 8)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def cube(origin, size, spin=False):
    item = {'origin': list(origin), 'size': list(size), 'uv': [0, 0]}
    if spin:
        item['pivot'] = [origin[0] + size[0] / 2, origin[1] + size[1] / 2, origin[2] + size[2] / 2]
        item['rotation'] = [0, 0, 45]
    return item


def lens(center_x, round_corners):
    # Wearer's right is negative X, matching vanilla armor's rightArm.
    # The square lens is four bars whose top and bottom span the full width.
    cx, cy, z, depth = center_x, FRAME_CENTER_Y, FRAME_FRONT_Z, FRAME_DEPTH
    if not round_corners:
        top_width = LENS_HALF * 2
        top_x = cx - top_width / 2
        return [
            cube([top_x, cy + 0.75, z], [top_width, 1, depth]),
            cube([top_x, cy - LENS_HALF, z], [top_width, 1, depth]),
            cube([cx - LENS_HALF, cy - 0.75, z], [1, 1.5, depth]),
            cube([cx + LENS_HALF - 1, cy - 0.75, z], [1, 1.5, depth]),
        ]
    # Eight equal bars. Diagonals are the same bar rolled about the lens
    # center, on a slightly nearer plane so the joints do not z-fight.
    width = OCTAGON_HALF * 2
    inner = LENS_HALF - 1
    cardinals = [
        cube([cx - OCTAGON_HALF, cy + inner, z], [width, 1, depth]),
        cube([cx - OCTAGON_HALF, cy - LENS_HALF, z], [width, 1, depth]),
        cube([cx + inner, cy - OCTAGON_HALF, z], [1, width, depth]),
        cube([cx - LENS_HALF, cy - OCTAGON_HALF, z], [1, width, depth]),
    ]
    diagonals = []
    for index, degrees in enumerate((45, 135, 225, 315)):
        front = z - (index + 1) / 16
        item = cube([cx - OCTAGON_HALF, cy + inner, front], [width, 1, depth])
        item['pivot'] = [cx, cy, front + depth / 2]
        item['rotation'] = [0, 0, degrees]
        diagonals.append(item)
    return cardinals + diagonals


def geometry():
    cy = FRAME_CENTER_Y
    bones = [
        {'name': 'waist', 'pivot': [0, 12, 0]},
        {'name': 'body', 'parent': 'waist', 'pivot': [0, 24, 0]},
        # Bind explicitly to the holder's head; coordinates stay in vanilla
        # player model space (head base Y=24), not entity feet space.
        {'name': 'head', 'parent': 'body', 'binding': "'head'", 'pivot': [0, 24, 0]},
        {'name': 'wearer_right_round', 'parent': 'head', 'pivot': [0, 24, 0], 'cubes': lens(-2.25, True)},
        {'name': 'wearer_left_square', 'parent': 'head', 'pivot': [0, 24, 0], 'cubes': lens(2.25, False)},
        {'name': 'bridge_and_temples', 'parent': 'head', 'pivot': [0, 24, 0], 'cubes': [
            cube([-.5, cy - .5, FRAME_FRONT_Z], [1, 1, FRAME_DEPTH]),
            cube([-TEMPLE_OUTER_X, cy - .5, FRAME_FRONT_Z], [TEMPLE_OUTER_X - 4, 1, FRAME_DEPTH]),
            cube([4, cy - .5, FRAME_FRONT_Z], [TEMPLE_OUTER_X - 4, 1, FRAME_DEPTH]),
            cube([-TEMPLE_OUTER_X, cy - .5, FRAME_BACK_Z], [TEMPLE_THICKNESS, 1, 3 - FRAME_BACK_Z]),
            cube([TEMPLE_INNER_X, cy - .5, FRAME_BACK_Z], [TEMPLE_THICKNESS, 1, 3 - FRAME_BACK_Z]),
        ]},
    ]
    return {'format_version': '1.16.0', 'minecraft:geometry': [{
        'description': {'identifier': 'geometry.ichiyon.narita_glasses', 'texture_width': 16,
                        'texture_height': 16, 'visible_bounds_width': 2,
                        'visible_bounds_height': 2, 'visible_bounds_offset': [0, 1.5, 0]},
        'bones': bones}]}


def png(image):
    target = BytesIO()
    image.save(target, format='PNG')
    return target.getvalue()


def compile_glasses(root=ROOT / 'minecraft'):
    root = Path(root)
    icon = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    # Front-facing inventory icon: viewer left circle, viewer right square.
    draw.ellipse((2, 10, 14, 22), outline=(20, 20, 24, 255), width=2)
    draw.rectangle((18, 10, 30, 22), outline=(20, 20, 24, 255), width=2)
    draw.line((14, 15, 18, 15), fill=(20, 20, 24, 255), width=2)
    draw.line((2, 11, 0, 8), fill=(20, 20, 24, 255), width=2)
    draw.line((30, 11, 31, 8), fill=(20, 20, 24, 255), width=2)
    texture = Image.new('RGBA', (16, 16), (15, 15, 18, 255))
    description = {'identifier': ITEM, 'menu_category': {'category': 'equipment', 'group': 'minecraft:itemGroup.name.helmet'}}
    files = {
        BP + 'items/narita_glasses.json': json_bytes({'format_version': '1.26.30', 'minecraft:item': {
            'description': description,
            'components': {'minecraft:display_name': {'value': 'item.ichiyon:narita_glasses.name'},
                           'minecraft:icon': ITEM, 'minecraft:max_stack_size': 1,
                           'minecraft:wearable': {'slot': 'slot.armor.head', 'protection': 0}}}}),
        RP + 'models/entity/narita_glasses.geo.json': json_bytes(geometry()),
        RP + 'textures/entity/narita_glasses.png': png(texture),
        RP + 'textures/items/narita_glasses.png': png(icon),
        RP + 'attachables/narita_glasses.player.json': json_bytes({'format_version': '1.20.30', 'minecraft:attachable': {
            # Custom attachables use the actual custom item identifier. The
            # native helmet's .player alias is not our item registration.
            'description': {'identifier': ITEM, 'item': {ITEM: "query.is_owner_identifier_any('minecraft:player')"},
                'materials': {'default': 'entity_alphatest'},
                'textures': {'default': 'textures/entity/narita_glasses'},
                'geometry': {'default': 'geometry.ichiyon.narita_glasses'},
                'animations': {'offset': 'animation.armor.helmet.offset'},
                'scripts': {'animate': ['offset']},
                'render_controllers': ['controller.render.ichiyon.narita_glasses']}}}),
        RP + 'render_controllers/narita_glasses.render_controllers.json': json_bytes({'format_version': '1.8.0',
            'render_controllers': {'controller.render.ichiyon.narita_glasses': {
                # Like the vanilla armor controller, never gate every bone on
                # an attachable animation context inside a render controller.
                'geometry': 'Geometry.default', 'materials': [{'*': 'Material.default'}], 'textures': ['Texture.default']}}}),
    }
    for name in ('narita_glasses_core.js', 'narita_glasses.js'):
        files[BRIDGE + 'scripts/' + name] = (root / BRIDGE / 'scripts' / name).read_bytes()
    return files


ATLAS_ENTRIES = {ITEM: {'textures': 'textures/items/narita_glasses'}}
LANG_ENTRIES = {'ja_JP': {'item.ichiyon:narita_glasses.name': '成田眼鏡'},
                'en_US': {'item.ichiyon:narita_glasses.name': 'Narita Glasses'}}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    files = compile_glasses()
    for name, data in files.items():
        target = args.output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (args.output / 'integration.json').write_bytes(json_bytes({
        'atlas_entries': ATLAS_ENTRIES, 'language_entries': LANG_ENTRIES,
        'bridge_import': 'import "./narita_glasses.js";', 'files': list(files),
        'client_rendering_verified': False}))
    print(json.dumps({'files': len(files), 'output': str(args.output), 'shared_files_modified': False}))
