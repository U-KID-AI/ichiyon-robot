"""Build a private native-world overlay. Never writes installed packs/worlds.

The release adapter combines these two JUNKET packs with the currently installed
managed catalog, not repository copies of users' DB-managed assets.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
from junket_render_validation import (validate_render_pack, create_interaction_anchor,
                                      correct_verified_monitors, validate_vibrant_manifest)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_files(source, target, folders=None):
    for path in sorted(source.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if folders is not None and rel.parts[0] not in folders:
            continue
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def overlay_entities(source, target, client=False):
    kind = 'minecraft:client_entity' if client else 'minecraft:entity'
    section = 'entity' if client else 'entities'
    for path in sorted((source / section).glob('*.json')):
        identifier = json.loads(path.read_text(encoding='utf-8-sig'))[kind]['description']['identifier']
        for old in (target / section).glob('*.json'):
            if json.loads(old.read_text(encoding='utf-8-sig'))[kind]['description']['identifier'] == identifier:
                old.unlink()  # Only the new private output tree, never source packs.
        destination = target / section / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def omit_empty_entity_properties(bp):
    """Omit invalid empty BDS property schemas in the private output only."""
    normalized = []
    for path in sorted((bp / 'entities').glob('*.json')):
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        description = value['minecraft:entity']['description']
        if description.get('properties') == {}:
            del description['properties']
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            normalized.append(description['identifier'])
    return normalized


def explicit_fixed_fixture_health(bp):
    """Give undamageable display fixtures an explicit, positive health pool.

    Do not touch vanilla NPCs or gameplay creatures. These are already persistent
    nonphysical props with unconditional damage immunity; this removes reliance
    on the engine's absent-health behavior without claiming a proven death cause.
    """
    runtime = json.loads((bp / 'scripts/all17_original_generated.json').read_text(encoding='utf-8-sig'))
    games = runtime if isinstance(runtime, list) else runtime.get('venues', runtime.get('games', []))
    fixture_types = {fixture['type'] for game in games for fixture in game['fixtures']}
    changed = []
    for path in sorted((bp / 'entities').glob('*.json')):
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        entity = value['minecraft:entity']
        identifier = entity['description']['identifier']
        components = entity.get('components', {})
        triggers = components.get('minecraft:damage_sensor', {}).get('triggers', [])
        if isinstance(triggers, dict):
            triggers = [triggers]
        immune = any(trigger.get('cause') == 'all' and trigger.get('deals_damage') in ('no', False)
                     and not any(key in trigger for key in ('filters', 'on_damage', 'event')) for trigger in triggers)
        physics = components.get('minecraft:physics', {})
        if (identifier in fixture_types and identifier.startswith(('junket:', 'junket_original:'))
                and 'minecraft:health' not in components and 'minecraft:persistent' in components
                and physics.get('has_gravity') is False and physics.get('has_collision') is False and immune):
            components['minecraft:health'] = {'value': 20, 'max': 20}
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            changed.append(identifier)
    return changed


def configure_interactive_source_entities(bp):
    """The Hope apparatus is a breakable game object, not a fixed decoration."""
    runtime = json.loads((bp / 'scripts/all17_original_generated.json').read_text(encoding='utf-8-sig'))
    games = runtime if isinstance(runtime, list) else runtime.get('venues', runtime.get('games', []))
    targets = [fixture for game in games if game['id'] == 'hope'
               for fixture in game['fixtures'] if fixture['role'] == 'hope_code']
    if not targets:
        return []
    if len(targets) != 1 or targets[0]['type'] != 'junket_original:hope_code':
        raise ValueError('Unexpected source Hope apparatus identity')
    identifier = targets[0]['type']
    uses = [(game['id'], fixture['role']) for game in games for fixture in game['fixtures']
            if fixture['type'] == identifier]
    if uses != [('hope', 'hope_code')]:
        raise ValueError('Interactive Hope entity is also used as decoration')
    paths = [path for path in (bp / 'entities').glob('*.json')
             if json.loads(path.read_text(encoding='utf-8-sig'))['minecraft:entity']['description']['identifier'] == identifier]
    if len(paths) != 1:
        raise ValueError('Missing/duplicate source Hope apparatus entity')
    value = json.loads(paths[0].read_text(encoding='utf-8-sig'))
    components = value['minecraft:entity']['components']
    components['minecraft:health'] = {'value': 3, 'max': 3}
    # Disjoint filters avoid an all-damage immunity trigger overriding attacks.
    # Official has_damage uses "attack" even though damage_sensor's cause uses
    # "entity_attack". Environmental damage and projectiles remain blocked.
    components['minecraft:damage_sensor'] = {'triggers': [
        {'cause': 'entity_attack', 'deals_damage': 'yes'},
        {'cause': 'all', 'deals_damage': 'no',
         'on_damage': {'filters': {'test': 'has_damage', 'subject': 'self',
                                   'operator': 'not', 'value': 'attack'}}}]}
    paths[0].write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return [identifier]


def legacy_damage_sensor_fields(value):
    """Yield legacy-schema string fields, including component group overrides."""
    version = value.get('format_version', '')
    if not re.fullmatch(r'\d+\.\d+\.\d+', str(version)) or tuple(map(int, version.split('.'))) >= (1, 21, 40):
        return
    entity = value.get('minecraft:entity', {})
    containers = [entity.get('components', {})] + list(entity.get('component_groups', {}).values())
    for components in containers:
        sensor = components.get('minecraft:damage_sensor', {})
        triggers = sensor.get('triggers', [])
        if isinstance(triggers, dict):
            triggers = [triggers]
        for field in [sensor] + triggers:
            if isinstance(field.get('deals_damage'), str):
                yield field


def normalize_legacy_damage_sensors(bp):
    """Use booleans for pre-1.21.40 damage_sensor schemas in output packs.

    1.21.40 introduced the string enum and automatic upgrading of older content.
    Feeding "no" to the older boolean schema is not a valid immunity guarantee.
    Do not upgrade unrelated entity schemas or alter source asset files.
    """
    changed = []
    for path in sorted((bp / 'entities').glob('*.json')):
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        fields = list(legacy_damage_sensor_fields(value))
        for field in fields:
            if field['deals_damage'] not in ('yes', 'no'):
                raise ValueError('Legacy damage sensor cannot represent: ' + field['deals_damage'])
            field['deals_damage'] = field['deals_damage'] == 'yes'
        if fields:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            changed.append({'identifier': value['minecraft:entity']['description']['identifier'],
                            'format_version': value['format_version'], 'normalized_fields': len(fields)})
    return changed


def create_protected_guide(bp, rp):
    """A fixed custom guide avoids vanilla NPC creative-hit removal behavior.

    Uses the existing vanilla wandering-trader client binding; no source-world
    model or skin is copied into the repository.
    """
    entity = {'format_version': '1.21.0', 'minecraft:entity': {
        'description': {'identifier': 'junket:guide', 'is_spawnable': False, 'is_summonable': True},
        'components': {
            'minecraft:health': {'value': 20, 'max': 20},
            'minecraft:physics': {'has_gravity': False, 'has_collision': False},
            'minecraft:collision_box': {'width': .6, 'height': 1.95},
            'minecraft:pushable': {'is_pushable': False, 'is_pushable_by_piston': False},
            'minecraft:damage_sensor': {'triggers': [{'cause': 'all', 'deals_damage': False}]},
            'minecraft:persistent': {},
            'minecraft:nameable': {'always_show': True, 'allow_name_tag_renaming': False},
            'minecraft:interact': {'interactions': [{'on_interact': {'event': 'junket:touch', 'target': 'self'},
                                                     'interact_text': 'action.interact.junket'}]}},
        'events': {'junket:touch': {}}}}
    client = json.loads((ROOT / 'minecraft/resource_packs/ichiyon_junket_rp/entity/junket_hope_judge.entity.json').read_text(encoding='utf-8'))
    client['minecraft:client_entity']['description']['identifier'] = 'junket:guide'
    (bp / 'entities').mkdir(parents=True, exist_ok=True)
    (rp / 'entity').mkdir(parents=True, exist_ok=True)
    (bp / 'entities/guide.json').write_text(json.dumps(entity, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (rp / 'entity/junket_guide.entity.json').write_text(json.dumps(client, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def protect_fixture_motion(bp):
    """Protect fixed actors without preventing scripted game-piece teleports.

    Only manifest-owned custom fixtures are changed. Spawned gameplay creatures
    and unrelated entities remain untouched. Hope code retains attack damage.
    """
    runtime = json.loads((bp / 'scripts/all17_original_generated.json').read_text(encoding='utf-8-sig'))
    games = runtime if isinstance(runtime, list) else runtime.get('venues', runtime.get('games', []))
    types = {fixture['type'] for game in games for fixture in game['fixtures']
             if fixture['type'].startswith(('junket:', 'junket_original:'))}
    changed = []
    desired = {'minecraft:physics': {'has_gravity': False, 'has_collision': False},
               'minecraft:pushable': {'is_pushable': False, 'is_pushable_by_piston': False},
               'minecraft:knockback_resistance': {'value': 1.0},
               'minecraft:movement': {'value': 0.0}}
    for path in sorted((bp / 'entities').glob('*.json')):
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        entity = value['minecraft:entity']; identifier = entity['description']['identifier']
        if identifier not in types:
            continue
        components = entity.setdefault('components', {})
        differences = {key: {'before': deepcopy(components.get(key)), 'after': desired[key]}
                       for key in desired if components.get(key) != desired[key]}
        components.update(deepcopy(desired))
        if differences:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            changed.append({'identifier': identifier, 'components': differences})
    return changed


def validate(bp, rp):
    entities = {}
    for path in (bp / 'entities').glob('*.json'):
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        description = value['minecraft:entity']['description']
        entity = description['identifier']
        if list(legacy_damage_sensor_fields(value)):
            raise ValueError('String damage sensor in legacy entity schema: ' + entity)
        if description.get('properties') == {}:
            raise ValueError('Empty entity properties: ' + entity)
        if entity in entities:
            raise ValueError('Duplicate entity: ' + entity)
        entities[entity] = path.name
    geometry = set()
    for path in (rp / 'models').rglob('*.json'):
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        for item in value.get('minecraft:geometry', []):
            identifier = item['description']['identifier']
            if identifier in geometry:
                raise ValueError('Duplicate geometry: ' + identifier)
            geometry.add(identifier)
    clients = set()
    for path in (rp / 'entity').glob('*.json'):
        client = json.loads(path.read_text(encoding='utf-8-sig'))['minecraft:client_entity']['description']
        if client['identifier'] in clients:
            raise ValueError('Duplicate client entity: ' + client['identifier'])
        clients.add(client['identifier'])
        for value in client.get('geometry', {}).values():
            if value not in geometry and value not in {'geometry.villager_v2'}:
                raise ValueError('Missing geometry: ' + value)
        for value in client.get('textures', {}).values():
            if (not (rp / (value + '.png')).is_file()
                    and value != 'textures/entity/wandering_trader'
                    and not value.startswith(('textures/entity/armor_stand', 'textures/entity/villager2/'))):
                raise ValueError('Missing texture: ' + value)
    if not entities.keys() <= clients:
        raise ValueError('Missing clients: ' + ','.join(entities.keys() - clients))
    runtime_path = bp / 'scripts/all17_original_generated.json'
    runtime = json.loads(runtime_path.read_text(encoding='utf-8-sig'))
    games = runtime if isinstance(runtime, list) else runtime.get('venues', runtime.get('games', []))
    fixture_count = 0
    for game in games:
        for fixture in game['fixtures']:
            fixture_count += 1
            identifier = fixture['type']
            if not identifier.startswith('minecraft:') and (identifier not in entities or identifier not in clients):
                raise ValueError(f'Missing runtime fixture entity: {game["id"]}/{fixture["role"]} {identifier}')
    for path in (bp / 'scripts').glob('*.js'):
        for relative in re.findall(r'(?:from\s*|import\s*)[\'"](\.[^\'"]+)[\'"]', path.read_text(encoding='utf-8-sig')):
            if not (path.parent / relative).is_file():
                raise ValueError(f'Missing script import: {path.name} {relative}')
    return {'entities': len(entities), 'client_entities': len(clients), 'geometry': len(geometry),
            'runtime_fixtures_resolved': fixture_count, 'render_resources': validate_render_pack(rp),
            'vibrant_visuals': validate_vibrant_manifest(rp)}


def read_private_runtime(path, layouts):
    """Accept reviewed private data without embedding its coordinate recipes."""
    if path is None:
        raise ValueError('--runtime-manifest is required; retain the private runtime generator and its JSON separately')
    from build_junket_warp import EXPECTED_IDS
    document = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    venues = document.get('venues', [])
    if len(venues) != len(EXPECTED_IDS) or {v.get('id') for v in venues} != set(EXPECTED_IDS):
        raise ValueError('Private runtime must contain each of the seventeen games exactly once')
    games = {v['id']: v for v in json.loads(Path(layouts).read_text(encoding='utf-8-sig'))['games']}
    for venue in venues:
        game = games[venue['id']]
        if venue.get('distribution') != 'private-local-only' or venue.get('installation') != 'native-preinstalled':
            raise ValueError('Runtime is not a private native venue manifest')
        if venue.get('origin') != game['production_origin'] or venue.get('size') != game['size']:
            raise ValueError('Private runtime bounds differ from selected layouts')
        arrival = venue.get('destination', {})
        if [arrival.get(axis) for axis in 'xyz'] != game['destination']:
            raise ValueError('Private runtime arrival differs from selected layouts')
    return venues


def build(layouts, original_assets, source_zip, output, render_assets=None, runtime_manifest=None):
    # Reject absent/mismatched private input before creating any output tree.
    runtime_venues = read_private_runtime(runtime_manifest, layouts)
    output.mkdir(parents=True, exist_ok=False)
    # Preserve archived converted assets; all deployment and repair references
    # use newly derived barrier-free tiles in a separate private authoring tree.
    source_layouts = layouts
    from junket_barriers import derive_layouts, validate_barrier_free_bundle
    layouts, barrier_report = derive_layouts(layouts, output.with_name(output.name + '-barrier-source'))
    bp = output / 'packs/behavior_packs/ichiyon_junket_bp'
    rp = output / 'packs/resource_packs/ichiyon_junket_rp'
    copy_files(ROOT / 'minecraft/behavior_packs/ichiyon_junket_bp', bp)
    copy_files(ROOT / 'minecraft/resource_packs/ichiyon_junket_rp', rp)
    overlay_entities(original_assets / 'ichiyon_junket_assets_bp', bp)
    for beam in (original_assets / 'ichiyon_junket_assets_bp/structures/junket').glob('temperance_beam_*.mcstructure'):
        shutil.copy2(beam, bp / 'structures/junket' / beam.name)
    overlay_entities(original_assets / 'ichiyon_junket_assets_rp', rp, client=True)
    copy_files(original_assets / 'ichiyon_junket_assets_rp', rp,
               {'models', 'textures', 'render_controllers', 'animations', 'animation_controllers'})
    source_assets = source_layouts.parent / 'assets'
    copy_files(source_assets / 'ichiyon_junket_original_bp', bp, {'entities', 'structures'})
    copy_files(source_assets / 'ichiyon_junket_original_rp', rp,
               {'entity', 'models', 'textures', 'render_controllers', 'animations', 'animation_controllers'})
    if render_assets:
        copy_files(render_assets / 'ichiyon_junket_original_bp', bp, {'entities'})
        copy_files(render_assets / 'ichiyon_junket_original_rp', rp,
                   {'entity', 'models', 'textures', 'render_controllers', 'animations', 'animation_controllers'})
    from junket_source_access import repair_structures
    derived = {v['id']: v for v in json.loads(layouts.read_text(encoding='utf-8-sig'))['games']}
    for venue in runtime_venues:
        venue['repair_structures'] = repair_structures(derived[venue['id']])
    (bp / 'scripts/all17_original_generated.js').write_text(
        '// PRIVATE LOCAL ORIGINAL VENUES. Do not redistribute.\nexport const ORIGINAL_VENUES = '
        + json.dumps(runtime_venues, ensure_ascii=False, separators=(',', ':')) + ';\n', encoding='utf-8')
    (bp / 'scripts/all17_original_generated.json').write_text(
        json.dumps({'venues': runtime_venues}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    from junket_catalog_world import native_plan
    from build_junket_warp import runtime_source
    plan = native_plan(json.loads(layouts.read_text(encoding='utf-8-sig')))
    (bp / 'scripts/junket_warp_generated.js').write_text(runtime_source(plan), encoding='utf-8')
    manifest = json.loads((bp / 'manifest.json').read_text(encoding='utf-8-sig'))
    for module in manifest['modules']:
        if module['type'] == 'script':
            module['entry'] = 'scripts/all17_reference_main.js'
    (bp / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    create_protected_guide(bp, rp)
    create_interaction_anchor(bp, rp)
    monitor_corrections = correct_verified_monitors(rp)
    normalized = omit_empty_entity_properties(bp)
    interactive_entities = configure_interactive_source_entities(bp)
    damage_sensors = normalize_legacy_damage_sensors(bp)
    motion_protection = protect_fixture_motion(bp)
    explicit_health = explicit_fixed_fixture_health(bp)
    report = validate(bp, rp)
    report['barrier_revision'] = barrier_report
    report['empty_entity_properties_omitted'] = normalized
    report['fixed_fixture_health_defined'] = explicit_health
    report['interactive_source_entities_configured'] = interactive_entities
    report['legacy_damage_sensors_normalized'] = damage_sensors
    report['fixture_motion_protected'] = motion_protection
    report['verified_monitor_corrections'] = monitor_corrections
    report['protected_guide'] = {'identifier': 'junket:guide', 'visual_binding': 'vanilla wandering trader',
                                 'vanilla_npc_component': False, 'creative_hit_removal_verified': False}
    layout = json.loads(layouts.read_text(encoding='utf-8-sig'))
    runtime = json.loads((bp / 'scripts/all17_original_generated.json').read_text(encoding='utf-8-sig'))
    runtime_games = runtime if isinstance(runtime, list) else runtime.get('venues', runtime.get('games', []))
    runtime_map = {game['id']: game for game in runtime_games}
    # Structures carry relative bundle paths; the native writer never follows
    # arbitrary file paths from an unpacked user archive.
    for game in layout['games']:
        game['block_overlays'] = runtime_map[game['id']].get('block_overlays', [])
        for structure in game['structures']:
            path = Path(structure['path'])
            identifier = structure['identifier']
            if not re.fullmatch(r'junket:[a-z0-9_]+', identifier):
                raise ValueError('Invalid native structure identifier: ' + identifier)
            # A source extension such as the original Hope gallery may live in
            # its own private extraction directory. Copy only that named tile,
            # retaining the exact SHA; never replace the source assets tree.
            rel = Path('structures/junket') / (identifier.split(':')[1] + '.mcstructure')
            destination = bp / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            if digest(path) != structure['sha256']:
                raise ValueError('Source structure fingerprint mismatch')
            shutil.copy2(path, destination)
            if digest(destination) != structure['sha256']:
                raise ValueError('Source structure fingerprint mismatch')
            structure['path'] = 'packs/behavior_packs/ichiyon_junket_bp/' + rel.as_posix()
    report['barrier_free_validation'] = validate_barrier_free_bundle(bp, runtime_games)
    (output / 'source-layouts.json').write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'warp-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(ROOT / 'scripts/minecraft/junket_games.json', output / 'junket-catalog.json')
    notice = ('PRIVATE JUNKET CRAFT 1.09 source-derived materials. No redistribution permission is asserted.\n'
              'Do not upload to GitHub or public download sites. This directory is a local/private deployment artifact.\n'
              'Automated checks do not prove a two-player gameplay completion.\n')
    (output / 'PRIVATE-NOTICE.txt').write_text(notice, encoding='utf-8')
    files = {p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob('*')) if p.is_file()}
    report.update({'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                   'source_zip_sha256': digest(source_zip), 'files': files,
                   'private_runtime_sha256': digest(runtime_manifest),
                   'games': [g['id'] for g in layout['games']], 'real_two_player_test': False,
                   'production_applied': False})
    (output / 'bundle-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = output.with_suffix('.zip')
    with ZipFile(archive, 'x', ZIP_DEFLATED) as package:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                package.write(path, path.relative_to(output).as_posix())
    with ZipFile(archive, 'r') as package:
        if package.testzip() is not None:
            raise ValueError('Bundle CRC failed')
    print(json.dumps({'directory': str(output), 'archive': str(archive), 'sha256': digest(archive),
                      'validation': report | {'files': len(files)}}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layouts', type=Path, required=True)
    parser.add_argument('--original-assets', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--render-assets', type=Path, help='Private corrected original BP/RP asset root')
    parser.add_argument('--runtime-manifest', type=Path, required=True,
                        help='Reviewed private runtime JSON generated outside the public repository')
    args = parser.parse_args()
    build(args.layouts.resolve(), args.original_assets.resolve(), args.source.resolve(), args.output.resolve(),
          args.render_assets.resolve() if args.render_assets else None, args.runtime_manifest.resolve())


if __name__ == '__main__':
    main()
