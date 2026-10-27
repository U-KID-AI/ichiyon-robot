"""Validate the actual RP resource graph and source-converted surfaces.

This checks files/data, not a running client. Built-in bindings are explicit;
an unknown resource never passes by being silently treated as a vanilla asset.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from PIL import Image

BUILTIN_MATERIALS = {'entity_alphatest', 'entity_alphablend', 'wandering_trader'}
BUILTIN_CONTROLLERS = {'controller.render.wandering_trader'}
BUILTIN_TEXTURES = {'textures/entity/wandering_trader'}
BUILTIN_GEOMETRIES = {'geometry.villager_v2'}
SPECIAL_FILES = {'sparrow.geo.json', 'temperance.geo.json', 'lucy.geo.json', 'silence.geo.json', 'jinn.geo.json'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def validate_vibrant_manifest(rp):
    """Reject a newly built RP that disables the world's Vibrant Visuals mode.

    This validates opt-in metadata, not client graphics or device support.
    Built-in materials and unspecified PBR properties retain engine defaults.
    https://learn.microsoft.com/en-us/minecraft/creator/documents/vibrantvisuals/vvresourcepacks
    """
    manifest = read(rp / 'manifest.json')
    capabilities = manifest.get('capabilities', [])
    engine = manifest.get('header', {}).get('min_engine_version', [])
    if not isinstance(capabilities, list) or 'pbr' not in capabilities:
        raise ValueError('JUNKET RP must retain the pbr capability for Vibrant Visuals')
    if (not isinstance(engine, list) or len(engine) != 3
            or any(type(value) is not int or value < 0 for value in engine)
            or tuple(engine) < (1, 21, 120)):
        raise ValueError('JUNKET Vibrant Visuals requires min_engine_version >= 1.21.120')
    if not any(module.get('type') == 'resources' for module in manifest.get('modules', [])):
        raise ValueError('JUNKET Vibrant Visuals declaration must belong to a resource pack')
    return {'capabilities': capabilities, 'min_engine_version': engine,
            'manifest_compatible': True, 'client_rendering_verified': False}


def validate_render_pack(rp):
    geos, controllers = {}, {}
    surface_count = 0
    for path in sorted((rp / 'models').rglob('*.json')):
        for geo in read(path).get('minecraft:geometry', []):
            identifier = geo['description']['identifier']
            if identifier in geos:
                raise ValueError('Duplicate geometry: ' + identifier)
            geos[identifier] = geo
            strict = path.name.startswith('original_') or path.name in SPECIAL_FILES
            width, height = (geo['description'][k] for k in ('texture_width', 'texture_height'))
            for bone in geo.get('bones', []):
                for cube in bone.get('cubes', []):
                    values = cube['origin'] + cube['size']
                    if any(not math.isfinite(v) for v in values) or any(v < 0 for v in cube['size']):
                        raise ValueError('Invalid cube extent: ' + identifier)
                    if strict and any(v == 0 for v in cube['size']):
                        raise ValueError('Degenerate source cube: ' + identifier)
                    if not isinstance(cube.get('uv'), dict):
                        continue  # vanilla-style box UV is verified by the pack schema
                    for face, uv in cube['uv'].items():
                        surface_count += 1
                        start, size = uv['uv'], uv['uv_size']
                        if uv.get('uv_rotation', 0) not in (0, 90, 180, 270):
                            raise ValueError('Invalid UV rotation: ' + identifier)
                        for axis, limit in enumerate((width, height)):
                            ends = [start[axis], start[axis] + size[axis]]
                            if any(not math.isfinite(v) or v < -1e-6 or v > limit + 1e-6 for v in ends):
                                raise ValueError(f'UV outside texture: {identifier}/{face} {ends} limit {limit}')
    for path in sorted((rp / 'render_controllers').rglob('*.json')):
        for name, controller in read(path).get('render_controllers', {}).items():
            if name in controllers:
                raise ValueError('Duplicate render controller: ' + name)
            controllers[name] = controller
    textures, clients, resolved = {}, {}, Counter()
    for path in sorted((rp / 'entity').glob('*.json')):
        client = read(path)['minecraft:client_entity']['description']
        identifier = client['identifier']
        if identifier in clients:
            raise ValueError('Duplicate client entity: ' + identifier)
        clients[identifier] = client
        for value in client.get('materials', {}).values():
            if value not in BUILTIN_MATERIALS:
                raise ValueError(f'Unknown material: {identifier} {value}')
        for value in client.get('textures', {}).values():
            if value in BUILTIN_TEXTURES:
                continue
            file = rp / (value + '.png')
            if not file.is_file():
                raise ValueError(f'Missing texture: {identifier} {value}')
            if value not in textures:
                with Image.open(file) as image:
                    image.load()
                    textures[value] = {'size': list(image.size), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
        for value in client.get('geometry', {}).values():
            if value not in geos and value not in BUILTIN_GEOMETRIES:
                raise ValueError(f'Missing geometry: {identifier} {value}')
        for reference in client.get('render_controllers', []):
            name = reference if isinstance(reference, str) else next(iter(reference))
            if name in BUILTIN_CONTROLLERS:
                continue
            if name not in controllers:
                raise ValueError(f'Missing render controller: {identifier} {name}')
            controller = controllers[name]
            for kind, aliases in [('Geometry', 'geometry'), ('Texture', 'textures'), ('Material', 'materials')]:
                for alias in re.findall(r'\b' + kind + r'\.([a-zA-Z0-9_]+)', json.dumps(controller)):
                    if alias not in client.get(aliases, {}):
                        raise ValueError(f'Unbound render alias: {identifier} {kind}.{alias}')
                    resolved[aliases] += 1
    return {'client_entities': len(clients), 'geometries': len(geos), 'face_uvs': surface_count,
            'textures': textures, 'render_controllers': len(controllers), 'resolved_aliases': dict(resolved),
            'client_rendering_verified': False}


def create_interaction_anchor(bp, rp):
    """Retain the existing 0.6 x 0.6 interaction hitbox without a duplicate floor."""
    write(bp / 'entities/junket_interaction_anchor.json', {'format_version': '1.21.0', 'minecraft:entity': {
        'description': {'identifier': 'junket:interaction_anchor', 'is_spawnable': False, 'is_summonable': True},
        'components': {'minecraft:collision_box': {'width': .6, 'height': .6},
                       'minecraft:physics': {'has_gravity': False, 'has_collision': False},
                       'minecraft:pushable': {'is_pushable': False, 'is_pushable_by_piston': False},
                       'minecraft:damage_sensor': {'triggers': [{'cause': 'all', 'deals_damage': False}]},
                       'minecraft:health': {'value': 20, 'max': 20}, 'minecraft:persistent': {},
                       'minecraft:movement': {'value': 0}, 'minecraft:knockback_resistance': {'value': 1},
                       'minecraft:interact': {'interactions': [{'on_interact': {'event': 'junket:touch', 'target': 'self'},
                           'interact_text': 'action.interact.junket'}]}}, 'events': {'junket:touch': {}}}})
    write(rp / 'entity/junket_interaction_anchor.entity.json', {'format_version': '1.10.0', 'minecraft:client_entity': {
        'description': {'identifier': 'junket:interaction_anchor', 'materials': {'default': 'entity_alphatest'},
                        'textures': {'default': 'textures/entity/junket_interaction_anchor'},
                        'geometry': {'default': 'geometry.junket.interaction_anchor'},
                        'render_controllers': ['controller.render.junket_interaction_anchor']}}})
    write(rp / 'models/entity/interaction_anchor.geo.json', {'format_version': '1.21.0', 'minecraft:geometry': [{
        'description': {'identifier': 'geometry.junket.interaction_anchor', 'texture_width': 1, 'texture_height': 1,
                        'visible_bounds_width': 1, 'visible_bounds_height': 1}, 'bones': []}]})
    write(rp / 'render_controllers/interaction_anchor.json', {'format_version': '1.8.0', 'render_controllers': {
        'controller.render.junket_interaction_anchor': {'geometry': 'Geometry.default',
            'materials': [{'*': 'Material.default'}], 'textures': ['Texture.default']}}})
    (rp / 'textures/entity').mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (1, 1)).save(rp / 'textures/entity/junket_interaction_anchor.png')


def centre_turn(geo):
    """Turn a monitor about its own horizontal centre, not the entity anchor."""
    geo = deepcopy(geo)
    cubes = [cube for bone in geo['bones'] for cube in bone.get('cubes', [])]
    if not cubes:
        raise ValueError('Cannot orient an empty monitor')
    if any(any(bone.get('rotation', [0, 0, 0])) for bone in geo['bones']):
        raise ValueError('Monitor correction requires explicitly reviewed nonzero bone rotations')
    low = [min(cube['origin'][i] for cube in cubes) for i in range(3)]
    high = [max(cube['origin'][i] + cube['size'][i] for cube in cubes) for i in range(3)]
    pivot = [(a+b)/2 for a, b in zip(low, high)]
    for bone in geo['bones']:
        if not bone.get('parent'):
            bone['parent'] = 'verified_monitor_facing'
    geo['bones'].insert(0, {'name': 'verified_monitor_facing', 'pivot': pivot, 'rotation': [0, 180, 0]})
    geo['description']['identifier'] += '_viewer_facing'
    return geo, pivot


def correct_verified_monitors(rp):
    """Only targets with source-purpose and Windows client baseline evidence.

    Never infer an audience monitor's facing from its nearest player seat. The
    five targets are Sparrow's shared player monitor and Fire's four explicitly
    tagged small player score/result monitors. Fire's large audience screen is
    deliberately excluded. No fixture/entity anchor rotation is changed.
    """
    targets = {'junket:tv', 'junket_original:fire_display_1', 'junket_original:fire_display_2'}
    geometries = {g['description']['identifier']: g for path in (rp / 'models').rglob('*.json')
                  for g in read(path).get('minecraft:geometry', [])}
    changed, clones = [], {}
    for path in (rp / 'entity').glob('*.json'):
        value = read(path)
        client = value['minecraft:client_entity']['description']
        if client['identifier'] not in targets:
            continue
        for alias, identifier in list(client['geometry'].items()):
            if identifier.endswith('_viewer_facing'):
                continue
            if identifier not in clones:
                clone, pivot = centre_turn(geometries[identifier])
                clones[identifier] = clone
                changed.append({'entity': client['identifier'], 'geometry': identifier,
                                'pivot': pivot, 'rotation': [0, 180, 0],
                                'reason': 'player-facing monitor; source tags and Windows client back-face baseline confirmed'})
            client['geometry'][alias] = clones[identifier]['description']['identifier']
        write(path, value)
    if targets - {read(p)['minecraft:client_entity']['description']['identifier'] for p in (rp / 'entity').glob('*.json')}:
        raise ValueError('Verified monitor entity missing from candidate')
    if clones:
        write(rp / 'models/entity/verified_monitor_facing.geo.json', {
            'format_version': '1.21.0', 'minecraft:geometry': list(clones.values())})
    return changed
