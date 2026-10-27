"""Private source evidence for model transforms and fixed-entity protection.

Reads original metadata/models and a built bundle. Does not access a server or
claim that algebraic/source checks establish client-rendered front/back faces.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / 'data/junket-private'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rotate_quaternion(vector, quaternion):
    length = math.sqrt(sum(component * component for component in quaternion))
    if not length:
        raise ValueError('Zero source quaternion')
    x, y, z, w = [component / length for component in quaternion]
    vx, vy, vz = vector
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return [vx + w * tx + y * tz - z * ty,
            vy + w * ty + z * tx - x * tz,
            vz + w * tz + x * ty - y * tx]


def source_local_normal(normal, transformation):
    """Inverse-transpose normal for L * S * R, before entity renderer/yaw."""
    result = rotate_quaternion(normal, transformation.get('right_rotation', [0, 0, 0, 1]))
    scale = transformation.get('scale', [1, 1, 1])
    if any(value == 0 for value in scale):
        return None
    result = [value / factor for value, factor in zip(result, scale)]
    result = rotate_quaternion(result, transformation.get('left_rotation', [0, 0, 0, 1]))
    length = math.sqrt(sum(value * value for value in result))
    return [round(value / length, 8) for value in result]


def inspect(layouts_path, source, bundle, candidate, output):
    output = output.resolve()
    if not output.is_relative_to(PRIVATE.resolve()):
        raise ValueError('Original evidence must remain private')
    layouts = read(layouts_path)
    baseline_manifest = bundle / 'packs/behavior_packs/ichiyon_junket_bp/scripts/all17_original_generated.json'
    baseline = {game['id']: game for game in read(baseline_manifest)['venues']}
    proposed = {game['id']: game for game in read(candidate)['venues']}
    entities = {}
    for path in (bundle / 'packs/behavior_packs/ichiyon_junket_bp/entities').glob('*.json'):
        value = read(path)['minecraft:entity']
        entities[value['description']['identifier']] = value
    report = {'schema': 1, 'distribution': 'private-local-only', 'baseline_bundle': bundle.name,
              'candidate_manifest_sha256': digest(candidate), 'baseline_manifest_sha256': digest(baseline_manifest),
              'source_zip_sha256': digest(source), 'client_rendering_verified': False,
              'building_coordinate_changes': False, 'retained_fixture_position_changes': [], 'games': [],
              'documentation': [
                  'https://learn.microsoft.com/en-us/minecraft/creator/reference/content/visualreference/geometry.v1.21.0',
                  'https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/entitycomponents/minecraftcomponent_damage_sensor',
                  'https://learn.microsoft.com/en-us/minecraft/creator/documents/entitycomponentsguide'],
              'basis_limitations': [
                  'Model north/south/up/down normals follow the source face definitions. Item-display entity yaw/pitch is recorded separately from model-local normals.',
                  'The converter reflects source X and swaps east/west UV faces. No unverified global 180-degree flip or vertical/mirror correction is applied.',
                  'Bedrock entity renderer root basis and final front/back/readable-text appearance require client visual evidence; coordinate algebra alone does not prove them.',
                  'Generic Bedrock controls remain adapters at source seats; they are not claimed to be original Java interaction entities.',
                  'Absent health is not established as the cause of Lucy disappearance. Fixed fixtures already disable damage, gravity, collision and natural despawn.']}
    with ZipFile(source) as archive:
        resource_name = next(name for name in archive.namelist() if name.endswith('/resources.zip'))
        with ZipFile(BytesIO(archive.read(resource_name))) as resources:
            for layout in layouts['games']:
                gid = layout['id']
                unchanged_keys = ('origin', 'size', 'source_bounds', 'destination', 'seats',
                                  'points', 'return_point', 'block_overlays')
                unchanged = {key: baseline[gid].get(key) == proposed[gid].get(key) for key in unchanged_keys}
                if not all(unchanged.values()):
                    raise ValueError('Unexpected building/game coordinate change: ' + gid)
                old = {fixture['role']: fixture for fixture in baseline[gid]['fixtures']}
                new = {fixture['role']: fixture for fixture in proposed[gid]['fixtures']}
                removed = [{'role': role, 'type': fixture['type'], 'location': fixture['location']}
                           for role, fixture in old.items() if role not in new]
                if any(not fixture['role'].startswith('display_') for fixture in removed):
                    raise ValueError('Unexpected non-status fixture removal: ' + gid)
                for role in old.keys() & new.keys():
                    if old[role]['location'] != new[role]['location']:
                        raise ValueError('Retained fixture moved: ' + gid + '/' + role)
                safety = Counter()
                for fixture in baseline[gid]['fixtures']:
                    entity = entities.get(fixture['type'])
                    if entity is None:
                        safety['vanilla_fixtures'] += 1
                        continue
                    components = entity.get('components', {})
                    safety['custom_fixtures'] += 1
                    safety['explicit_health'] += 'minecraft:health' in components
                    safety['persistent'] += 'minecraft:persistent' in components
                    physics = components.get('minecraft:physics', {})
                    safety['gravity_disabled'] += physics.get('has_gravity') is False
                    safety['collision_disabled'] += physics.get('has_collision') is False
                    triggers = components.get('minecraft:damage_sensor', {}).get('triggers', [])
                    triggers = [triggers] if isinstance(triggers, dict) else triggers
                    safety['all_damage_disabled'] += any(t.get('cause') == 'all' and t.get('deals_damage') in ('no', False) for t in triggers)
                    safety['despawn_component'] += any(key in components for key in ('minecraft:despawn', 'minecraft:instant_despawn'))
                changes = []
                for fixture in proposed[gid]['fixtures']:
                    previous = old.get(fixture['role'], {})
                    changed = {key: {'before': previous.get(key), 'after': fixture.get(key)}
                               for key in ('type', 'rotation', 'location') if previous.get(key) != fixture.get(key)}
                    if changed:
                        changes.append({'role': fixture['role'], 'changes': changed,
                                        'source_model': fixture.get('source_model'),
                                        'source_display_line': fixture.get('source_display_line'),
                                        'source_transformation': fixture.get('source_transformation')})
                displays = []
                for summon in layout['summons']:
                    model = summon.get('model')
                    if summon['type'] != 'item_display' or not model:
                        continue
                    model_path = 'assets/minecraft/models/item/' + model + '.json'
                    if model_path not in resources.namelist():
                        continue
                    model_data = json.loads(resources.read(model_path))
                    transform = summon.get('transformation', {})
                    displays.append({'source_line': summon['line'], 'source_model': model,
                                     'source_location': summon['location'], 'source_tags': summon['tags'],
                                     'source_yaw_pitch': summon['rotation'], 'transformation': transform,
                                     'model_north_normal_before_entity_yaw': source_local_normal([0, 0, -1], transform),
                                     'model_up_normal_before_entity_yaw': source_local_normal([0, 1, 0], transform),
                                     'source_model_elements': len(model_data['elements']),
                                     'source_face_names': sorted({face for element in model_data['elements'] for face in element['faces']}),
                                     'source_model_sha256': hashlib.sha256(resources.read(model_path)).hexdigest()})
                report['games'].append({'id': gid, 'title': layout['title'],
                    'source_prepare_sha256': layout['source_function_sha256'],
                    'source_bounds': layout['source_bounds'], 'production_translation': layout['translation'],
                    'building_orientation': 'translation only; existing Chunker block states and mcstructure voxels unchanged',
                    'source_sign_blocks': {name: count for name, count in layout.get('block_counts', {}).items() if 'sign' in name},
                    'source_display_evidence': displays, 'candidate_fixture_changes': changes,
                    'removed_generated_status_meshes': removed,
                    'retained_fixture_positions_unchanged': True,
                    'coordinate_invariants': unchanged,
                    'baseline_fixed_entity_safety': dict(safety),
                    'baseline_fixture_count': len(baseline[gid]['fixtures']), 'candidate_fixture_count': len(proposed[gid]['fixtures']),
                    'client_front_back_mirroring_vertical_status': 'unverified; no axis flip guessed'})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'games': len(report['games']),
                      'changed_fixtures': sum(len(game['candidate_fixture_changes']) for game in report['games']),
                      'removed_generated_status_meshes': sum(len(game['removed_generated_status_meshes']) for game in report['games'])}, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layouts', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inspect(args.layouts, args.source, args.bundle, args.candidate, args.output)


if __name__ == '__main__':
    main()
