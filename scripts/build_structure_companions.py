"""Private structure-to-entity conversion, using the existing Pikachu voxel scale.

Original structures are read-only. Vanilla material tiles are taken from an
explicit local cache; no fabricated fallback texture or guessed block is used.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from PIL import Image
import nbtlib

from build_pikachu_assets import SCALE

ROOT = Path(__file__).resolve().parents[1]
BP = ROOT / 'minecraft/behavior_packs/ichiyon_avatar_bp'
RP = ROOT / 'minecraft/resource_packs/ichiyon_avatar_rp'
TEMPLATES = ROOT / 'scripts/templates/structure_companions'
FACES = ('north', 'south', 'east', 'west', 'up', 'down')
SPECS = {'ikarun': ('イカルン.mcstructure', '-x'), 'tumaini': ('トゥマイニ.mcstructure', '+x')}
EXCLUDED = {'minecraft:air', 'minecraft:structure_block', 'minecraft:light_block_15'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_structure(path):
    root = nbtlib.load(path, gzipped=False, byteorder='little')
    size = tuple(map(int, root['size']))
    structure = root['structure']
    palette = structure['palette']['default']['block_palette']
    indices = structure['block_indices'][0]
    if len(indices) != math.prod(size):
        raise ValueError('Invalid voxel layer dimensions')
    blocks, omitted = {}, {}
    for x in range(size[0]):
        for y in range(size[1]):
            for z in range(size[2]):
                raw = int(indices[(x * size[1] + y) * size[2] + z])
                if raw < 0:
                    continue
                block = palette[raw]
                name, states = str(block['name']), block['states'].unpack()
                if name in EXCLUDED or name == 'minecraft:grass_block':
                    if name == 'minecraft:grass_block' and y != 0:
                        raise ValueError('Grass outside the capture ground cannot be omitted')
                    omitted[name] = omitted.get(name, 0) + 1
                    continue
                blocks[x, y, z] = (name, states)
    if not blocks:
        raise ValueError('Empty source model')
    bounds = [[min(p[i] for p in blocks), max(p[i] for p in blocks) + 1] for i in range(3)]
    return blocks, {'capture_size': size, 'body_bounds': bounds, 'excluded_capture_cells': omitted,
                    'body_voxels': len(blocks), 'source_sha256': sha(path)}


def block_parts(name, states):
    """Axis-aligned Bedrock stair quarters and wall buttons, in source units."""
    if name.endswith('_stairs'):
        direction = int(states['weirdo_direction'])
        corner = str(states.get('minecraft:corner', 'none'))
        if direction not in range(4) or corner not in ('none', 'outer_left', 'outer_right', 'inner_left', 'inner_right'):
            raise ValueError('Unsupported stair orientation')
        upside = bool(states['upside_down_bit'])
        base_y, step_y = (.5, 0) if upside else (0, .5)
        # Ascending direction: east, west, south, north.
        front = ({(1, 0), (1, 1)}, {(0, 0), (0, 1)}, {(0, 1), (1, 1)}, {(0, 0), (1, 0)})[direction]
        left = ({(0, 0), (1, 0)}, {(0, 1), (1, 1)}, {(1, 0), (1, 1)}, {(0, 0), (0, 1)})[direction]
        turn = left if corner.endswith('left') else {(x, z) for x in range(2) for z in range(2)} - left
        quarters = front if corner == 'none' else front & turn if corner.startswith('outer') else front | turn
        return [((0, base_y, 0), (1, .5, 1))] + [((x / 2, step_y, z / 2), (.5, .5, .5)) for x, z in sorted(quarters)]
    if name.endswith('_button'):
        facing = int(states['facing_direction'])
        if facing == 4:
            return [((.875, .375, .3125), (.125, .25, .375))]
        raise ValueError('Source button orientation not yet supported')
    return [((0, 0, 0), (1, 1, 1))]


def bone_for(key, p):
    x, y, z = p
    if key == 'ikarun':
        if y <= 4:
            return ('front_' if x < 12 else 'rear_') + ('left_leg' if z < 8 else 'right_leg')
        if x >= 18:
            return 'tail'
        if x <= 8 and y >= 9:
            return 'head'
        return 'body'
    if z <= 4 and y >= 5:
        return 'right_hand'
    if z >= 17 and y >= 5:
        return 'left_hand'
    return 'body'


def source_cuboids(key, blocks):
    """Same greedy exact-voxel merging as build_pikachu_assets; stairs stay shaped."""
    consumed, cubes = set(), []
    for p, (name, states) in sorted(blocks.items()):
        if p in consumed:
            continue
        x, y, z = p
        bone = bone_for(key, p)
        if name.endswith(('_stairs', '_button')):
            for origin, size in block_parts(name, states):
                cubes.append((bone, name, tuple(p[i] + origin[i] for i in range(3)), size))
            continue
        def available(xx, yy, zz):
            cell = (xx, yy, zz)
            return cell not in consumed and blocks.get(cell) == (name, states) and bone_for(key, cell) == bone
        dz = 1
        while available(x, y, z + dz):
            dz += 1
        dy = 1
        while all(available(x, y + dy, zz) for zz in range(z, z + dz)):
            dy += 1
        dx = 1
        while all(available(x + dx, yy, zz) for yy in range(y, y + dy) for zz in range(z, z + dz)):
            dx += 1
        consumed.update((xx, yy, zz) for xx in range(x, x + dx) for yy in range(y, y + dy) for zz in range(z, z + dz))
        cubes.append((bone, name, p, (dx, dy, dz)))
    return cubes


class Atlas:
    def __init__(self, cache):
        self.cache = Path(cache)
        self.mapping = json.loads((self.cache / 'materials.json').read_text(encoding='utf-8'))
        self.image = Image.new('RGBA', (2048, 2048))
        self.x, self.y, self.row_height = 1, 1, 0
        self.patches, self.sources = {}, {}

    def patch(self, name, face, width, height):
        base = name.split(':')[-1].removesuffix('_stairs')
        entry = self.mapping[base]
        path = entry[face]
        image_path = self.cache / path
        if sha(image_path) != entry['sha256'][face]:
            raise ValueError('Vanilla material fingerprint mismatch')
        self.sources[path] = entry['sha256'][face]
        w, h = max(1, round(width * 16)), max(1, round(height * 16))
        key = (path, w, h)
        if key in self.patches:
            return self.patches[key]
        tile = Image.open(image_path).convert('RGBA')
        if tile.size != (16, 16):
            raise ValueError('Expected a 16px vanilla material tile')
        if self.x + w + 1 > self.image.width:
            self.x, self.y, self.row_height = 1, self.y + self.row_height + 2, 0
        if self.y + h + 1 > self.image.height:
            raise ValueError('Model material atlas exceeds the bounded 2048px texture')
        patch = Image.new('RGBA', (w, h))
        for x in range(0, w, 16):
            for y in range(0, h, 16):
                patch.paste(tile, (x, y))
        self.image.paste(patch, (self.x, self.y))
        uv = {'uv': [self.x, self.y], 'uv_size': [w, h]}
        self.patches[key] = uv
        self.x += w + 2
        self.row_height = max(self.row_height, h)
        return uv

    def save(self, path):
        height = 2 ** math.ceil(math.log2(self.y + self.row_height + 1))
        self.image.crop((0, 0, self.image.width, height)).save(path)
        return [self.image.width, height]


def model(key, blocks, info, cache):
    bounds = info['body_bounds']
    center_x, center_z = sum(bounds[0]) / 2, sum(bounds[2]) / 2
    floor = bounds[1][0]
    front = SPECS[key][1]
    def transform(p):
        x, y, z = p
        return [round(((-z + center_z) if front == '-x' else (z - center_z)) * SCALE, 6),
                round((y - floor) * SCALE, 6),
                round(((x - center_x) if front == '-x' else (-x + center_x)) * SCALE, 6)]
    pivots = ({'body': (12, 7, 8.5), 'head': (8, 10, 8.5), 'tail': (18, 6, 8.5),
              'front_left_leg': (7, 5, 5), 'front_right_leg': (7, 5, 12),
              'rear_left_leg': (16, 5, 5), 'rear_right_leg': (16, 5, 12)} if key == 'ikarun' else
             {'body': (8, 7, 11), 'left_hand': (8, 8, 18), 'right_hand': (8, 8, 3)})
    bones = [{'name': 'root', 'pivot': [0, 0, 0]}, {'name': 'body_root', 'parent': 'root', 'pivot': transform((center_x, floor, center_z))}]
    bones += [{'name': name, 'parent': 'body_root', 'pivot': transform(p), 'cubes': []} for name, p in pivots.items()]
    by_name = {b['name']: b for b in bones}
    atlas = Atlas(cache)
    cuboids = source_cuboids(key, blocks)
    for bone, name, (x, y, z), (dx, dy, dz) in cuboids:
        if name == 'minecraft:honey_block':
            translucent = bone + '_translucent'
            if translucent not in by_name:
                child = {'name': translucent, 'parent': bone, 'pivot': by_name[bone]['pivot'], 'cubes': []}
                bones.append(child)
                by_name[translucent] = child
            bone = translucent
        a, b = transform((x, y, z)), transform((x + dx, y + dy, z + dz))
        origin = [min(a[i], b[i]) for i in range(3)]
        size = [round(abs(a[i] - b[i]), 6) for i in range(3)]
        uv = {face: atlas.patch(name, face, dx if face in ('east', 'west') else dz, dx if face in ('up', 'down') else dy) for face in FACES}
        by_name[bone]['cubes'].append({'origin': origin, 'size': size, 'uv': uv})
    dimensions = [(hi - lo) * SCALE / 16 for lo, hi in bounds]
    geometry = {'format_version': '1.12.0', 'minecraft:geometry': [{'description': {
        'identifier': 'geometry.' + key, 'texture_width': 2048, 'texture_height': 2048,
        'visible_bounds_width': max(dimensions[0], dimensions[2]) + 1,
        'visible_bounds_height': dimensions[1] + 2, 'visible_bounds_offset': [0, dimensions[1] / 2 + .5, 0]}, 'bones': bones}]}
    return geometry, atlas, {'cubes': len(cuboids), 'source_forward': front, 'voxel_pixel_scale': SCALE,
                             'world_dimensions_xyz': [dimensions[2], dimensions[1], dimensions[0]],
                             'scale_source': 'scripts/build_pikachu_assets.py:SCALE', 'vanilla_tiles': atlas.sources}


def animations(key):
    def anim(bones, length=2.4, loop=True):
        return {'loop': loop, 'animation_length': length, 'bones': bones}
    if key == 'ikarun':
        values = {
            'hover': anim({'body_root': {'position': [0, '0.55 + math.sin(query.life_time * 75) * 0.4', 0]},
                           'head': {'rotation': ['math.sin(query.life_time * 40) * 2', 0, 0]},
                           'tail': {'rotation': [0, 'math.sin(query.life_time * 80) * 6', 0]}}),
            'move': anim({'body_root': {'rotation': ['math.clamp(query.modified_move_speed * 5, 0, 6)', 0, 0]},
                         'front_left_leg': {'rotation': ['math.sin(query.life_time * 90) * 4', 0, 0]},
                         'rear_right_leg': {'rotation': ['-math.sin(query.life_time * 90) * 4', 0, 0]}}),
            'heal': anim({'head': {'rotation': {'0': [0,0,0], '.4': [-10,0,0], '1': [0,0,0]}},
                         'body_root': {'position': {'0': [0,0,0], '.4': [0,.6,0], '1': [0,0,0]}}}, 1, False),
            'hurt': anim({'body_root': {'rotation': [0, 0, 'math.sin(query.hurt_time * 30) * 3']}}, .4, False)}
    else:
        values = {
            'idle': anim({'body': {'position': [0, 'math.sin(query.life_time * 70) * .15', 0]},
                          'left_hand': {'rotation': [0, 0, 'math.sin(query.life_time * 60) * 4']},
                          'right_hand': {'rotation': [0, 0, '-math.sin(query.life_time * 60) * 4']}}),
            'walk': anim({'body_root': {'position': [0, 'math.abs(math.sin(query.life_time * 240)) * .35', 0],
                                      'rotation': [0, 0, 'math.sin(query.life_time * 120) * 3']},
                          'left_hand': {'rotation': ['math.sin(query.life_time * 180) * 12', 0, 0]},
                          'right_hand': {'rotation': ['-math.sin(query.life_time * 180) * 12', 0, 0]}}),
            'attack': anim({'left_hand': {'rotation': ['-math.sin(variable.attack_time * 180) * 35', 0, 0]},
                           'right_hand': {'rotation': ['-math.sin(variable.attack_time * 180) * 35', 0, 0]}}, .65, False),
            'hurt': anim({'body_root': {'rotation': [0, 0, 'math.sin(query.hurt_time * 30) * 5']}}, .4, False)}
    return {'format_version': '1.8.0', 'animations': {'animation.' + key + '.' + name: value for name, value in values.items()}}


def client(key):
    condition = ({'hover': '1.0', 'move': 'query.is_moving', 'heal': "query.property('ichiyon:healing')", 'hurt': 'query.hurt_time > 0'}
                 if key == 'ikarun' else {
                     'idle': '!query.is_moving && variable.attack_time <= 0.0 && query.hurt_time <= 0.0',
                     'walk': 'query.is_moving && variable.attack_time <= 0.0 && query.hurt_time <= 0.0',
                     'attack': 'variable.attack_time > 0.0 && query.hurt_time <= 0.0',
                     'hurt': 'query.hurt_time > 0.0'})
    return {'format_version': '1.10.0', 'minecraft:client_entity': {'description': {
        'identifier': 'ichiyon:' + key, 'materials': {'default': 'entity_alphatest', 'translucent': 'entity_alphablend'}, 'textures': {'default': 'textures/entity/' + key},
        'geometry': {'default': 'geometry.' + key}, 'render_controllers': ['controller.render.structure_companion'],
        'animations': {name: 'animation.' + key + '.' + name for name in condition},
        'scripts': {'animate': [{name: value} for name, value in condition.items()]},
        'spawn_egg': {'base_color': '#E8E8E8' if key == 'ikarun' else '#DDD0A2', 'overlay_color': '#159AAA' if key == 'ikarun' else '#FACF36'}}}}


def tumaini_behavior():
    # Shared exact combat/home configuration; cosmetic identifier/family only differ.
    document = deepcopy(json.loads((BP / 'entities/taketumi.json').read_text(encoding='utf-8')))
    entity = document['minecraft:entity']
    entity['description']['identifier'] = 'ichiyon:tumaini'
    family = entity['components']['minecraft:type_family']['family']
    family[:] = ['tumaini' if v == 'taketumi' else v for v in family]
    return document


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build(source, cache, report, output):
    """Write an explicit private overlay; never alter the public authoring packs."""
    source, cache, output, report = map(Path, (source, cache, output, report))
    if output.resolve().is_relative_to((ROOT / 'minecraft').resolve()):
        raise ValueError('Use a private output directory outside the public Minecraft packs')
    # Refuse reusing a previous candidate so a failed conversion cannot damage it.
    output.mkdir(parents=True, exist_ok=False)
    bp = output / 'behavior_packs/ichiyon_avatar_bp'
    rp = output / 'resource_packs/ichiyon_avatar_rp'
    result = {'source_assets_unchanged': True, 'client_visual_test': False, 'models': {}}
    for key, (filename, front) in SPECS.items():
        blocks, info = read_structure(source / filename)
        geo, atlas, model_info = model(key, blocks, info, cache)
        texture_path = rp / f'textures/entity/{key}.png'
        texture_path.parent.mkdir(parents=True, exist_ok=True)
        width, height = atlas.save(texture_path)
        geo['minecraft:geometry'][0]['description'].update(texture_width=width, texture_height=height)
        write_json(rp / f'models/entity/{key}.geo.json', geo)
        write_json(rp / f'entity/{key}.entity.json', client(key))
        write_json(rp / f'animations/{key}.animation.json', animations(key))
        result['models'][key] = info | model_info | {'atlas_size': [width, height], 'source': str(source / filename)}
    write_json(bp / 'entities/tumaini.json', tumaini_behavior())
    write_json(bp / 'entities/ikarun.json', json.loads((TEMPLATES / 'ikarun.json').read_text(encoding='utf-8')))
    write_json(rp / 'render_controllers/structure_companion.render_controllers.json', {
        'format_version': '1.8.0', 'render_controllers': {'controller.render.structure_companion': {
            'geometry': 'Geometry.default', 'materials': [{'*': 'Material.default'}, {'*_translucent': 'Material.translucent'}],
            'textures': ['Texture.default']}}})
    result['taketumi_source_sha256'] = sha(BP / 'entities/taketumi.json')
    bridge = Path('behavior_packs/import_structures/scripts')
    for name in ('companion_owner.js', 'friendly_healing.js', 'ikarun_core.js', 'ikarun.js', 'taketumi_leash.js'):
        target = output / bridge / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / 'minecraft' / bridge / name).read_bytes())
    # Existing private/static overlay assembly merges these additions and pins
    # the result. This generator does not replace registries or deploy anything.
    write_json(output / 'integration.json', {
        'bridge_import': 'import "./ikarun.js";',
        'language_entries': {
            'en_US': {'entity.ichiyon:ikarun.name': 'Ikarun', 'entity.ichiyon:tumaini.name': 'Tumaini'},
            'ja_JP': {'entity.ichiyon:ikarun.name': '\u30a4\u30ab\u30eb\u30f3', 'entity.ichiyon:tumaini.name': '\u30c8\u30a5\u30de\u30a4\u30cb'},
        },
        'files': sorted(p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()),
        'client_rendering_verified': False,
    })
    write_json(report, result)
    print(json.dumps({'models': {k: {n: v[n] for n in ('body_voxels', 'cubes', 'world_dimensions_xyz', 'atlas_size')} for k, v in result['models'].items()}}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--vanilla-cache', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New private overlay directory; never a public pack')
    args = parser.parse_args()
    build(args.source, args.vanilla_cache, args.report, args.output)
