"""Remove JUNKET-only invisible boundaries without deleting physical game floors.

Original archives remain immutable. Deployment/repair structures are derived
copies. A stopped-world migration changes only cells currently named barrier;
ordinary blocks, tile entities, actors, and all cells outside the recorded
seventeen original venue bounds are retained by the native transaction.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re

import nbtlib as n

from junket_native_world import AIR, EXPECTED_IDS, NativePlacement, bounds, inside, commit_placement
from junket_structure import ConvertedWorld

BARRIER = 'minecraft:barrier'
# This source-used full cube has no directional states. Using a state-free
# palette avoids the pillar_axis default needed by smooth_quartz in native NBT.
VISIBLE_FLOOR = 'minecraft:quartz_bricks'
POLICY_REVISION = 1


def block(name, version=None):
    return n.Compound({'name': n.String(name), 'states': n.Compound(),
                       'version': version if version is not None else AIR['version']})


def source_floor_policy(game):
    """Load reviewed source-layout data; never guess a missing floor height."""
    if game['id'] not in ('hope', 'clock', 'rabbit', 'garden'):
        return {'planes': []}
    policy = game.get('source_floor_policy')
    if policy is None:
        from junket_private_profile import private_value
        policy = private_value('barrier.floor_policy')[game['id']]
    if not isinstance(policy, dict) or not isinstance(policy.get('planes'), list):
        raise ValueError('Explicit private source floor policy required')
    for plane in policy['planes']:
        if not isinstance(plane.get('y'), int):
            raise ValueError('Private source floor plane must have an integer height')
        for axis in ('x', 'z'):
            limit = plane.get(axis)
            if limit is not None and (not isinstance(limit, list) or len(limit) != 2 or
                    not all(isinstance(v, int) for v in limit) or limit[0] > limit[1]):
                raise ValueError('Invalid private source floor bounds')
    return policy


def floor_replacement(game, at):
    """Retain only evidenced support planes from the private layout/profile."""
    source = tuple(int(a - d) for a, d in zip(at, game['translation']))
    x, y, z = source
    policy = source_floor_policy(game)
    supported = any(y == plane['y'] and
                    all(axis not in plane or plane[axis][0] <= value <= plane[axis][1]
                        for axis, value in (('x', x), ('z', z)))
                    for plane in policy['planes'])
    if not supported:
        return 'minecraft:air'
    if game['id'] == 'garden':
        if not any(abs(x - seat[0]) <= 1 and abs(z - seat[2]) <= 1
                   for seat in game.get('source_seats', [])):
            return 'minecraft:air'
    if game['id'] == 'clock':
        offset = game.get('floor_template', {}).get('source_target_offset')
        if not isinstance(offset, list) or len(offset) != 3:
            raise ValueError('Private clock floor template offset is required')
        for item in game.get('floor_template', {}).get('cells', []):
            mapped = tuple(a + d for a, d in zip(item['location'], offset))
            name = item['block']['name']
            if mapped == source and name.endswith('_wool'):
                return name
    return VISIBLE_FLOOR


def load_structure(tile):
    raw = Path(tile['path']).read_bytes()
    if sha256(raw).hexdigest() != tile['sha256']:
        raise ValueError('Source structure fingerprint mismatch: ' + str(tile['path']))
    root = n.File.parse(BytesIO(raw), byteorder='little')
    if list(map(int, root['size'])) != tile['size']:
        raise ValueError('Source structure size mismatch')
    return root


def cell_location(tile, index):
    _, sy, sz = tile['size']
    return tuple(a + b for a, b in zip(tile['origin'], (index // (sy * sz), index // sz % sy, index % sz)))


def derive_structure(game, tile, path):
    root = load_structure(tile)
    palette = root['structure']['palette']['default']['block_palette']
    changes = Counter()
    lookup = {p.snbt(): i for i, p in enumerate(palette)}
    for layer, indices in enumerate(root['structure']['block_indices']):
        for index, pi in enumerate(indices):
            if int(pi) < 0 or str(palette[int(pi)]['name']) != BARRIER:
                continue
            name = floor_replacement(game, cell_location(tile, index)) if layer == 0 else 'minecraft:air'
            value = block(name, palette[int(pi)].get('version', AIR['version']))
            signature = value.snbt()
            if signature not in lookup:
                lookup[signature] = len(palette)
                palette.append(value)
            indices[index] = n.Int(lookup[signature])
            changes[name] += 1
    # Remove unused barrier palette entries too: repair files must not carry a
    # hidden barrier definition that a later index rewrite could reintroduce.
    remap = {}
    kept = []
    for i, value in enumerate(palette):
        if str(value['name']) != BARRIER:
            remap[i] = len(kept)
            kept.append(value)
    for indices in root['structure']['block_indices']:
        for index, pi in enumerate(indices):
            if int(pi) >= 0:
                indices[index] = n.Int(remap[int(pi)])
    root['structure']['palette']['default']['block_palette'] = n.List[n.Compound](kept)
    path.parent.mkdir(parents=True, exist_ok=True)
    root.save(path, byteorder='little')
    result = deepcopy(tile)
    result.update(path=str(path.resolve()), sha256=sha256(path.read_bytes()).hexdigest(),
                  bytes=path.stat().st_size, palette=len(kept),
                  barrier_source_sha256=tile['sha256'], barrier_replacements=dict(changes))
    if 'blocks' in result:
        result['blocks'].pop(BARRIER, None)
        for name, count in changes.items():
            result['blocks'][name] = result['blocks'].get(name, 0) + count
    return result


def derive_layouts(source, output):
    """Create a new private source-layout tree; never mutate an earlier build."""
    layout = json.loads(Path(source).read_text(encoding='utf-8-sig'))
    if layout.get('barrier_revision') == POLICY_REVISION:
        return Path(source), {'already_derived': True}
    if [g['id'] for g in layout['games']] != EXPECTED_IDS:
        raise ValueError('Exactly the recorded seventeen venues are required')
    for game in layout['games']:
        game['source_floor_policy'] = deepcopy(source_floor_policy(game))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    report = {'policy_revision': POLICY_REVISION, 'games': [], 'production_applied': False}
    for game in layout['games']:
        replacements = Counter()
        converted = []
        for tile in game['structures']:
            path = output / 'structures' / (tile['identifier'].split(':')[1] + '.mcstructure')
            result = derive_structure(game, tile, path)
            replacements.update(result['barrier_replacements'])
            converted.append(result)
        game['structures'] = converted
        for probe in game.get('safe_arrival_probes', []):
            if probe.get('block', {}).get('name') == BARRIER:
                at = probe.get('location') or [a + d for a, d in zip(probe['source'], game['translation'])]
                probe['block'] = {'name': floor_replacement(game, at), 'states': {}}
        game['barrier_policy'] = {'revision': POLICY_REVISION, 'replacements': dict(replacements),
            'boundary': 'air', 'supported_floors': 'visible removable floors at original elevations'}
        if 'block_counts' in game:
            game['block_counts'].pop(BARRIER, None)
            for name, count in replacements.items():
                game['block_counts'][name] = game['block_counts'].get(name, 0) + count
        report['games'].append({'id': game['id'], 'barriers_replaced': sum(replacements.values()),
                               'replacement_blocks': dict(replacements)})
    layout['barrier_revision'] = POLICY_REVISION
    layout['barriers'] = 'removed inside original venue bounds; physical game floors remain visible and removable'
    destination = output / 'source-layouts.json'
    destination.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'barrier-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return destination, report


def validate_barrier_free_bundle(bp, runtime_games):
    """Fail final packaging if repair or dynamic metadata can reinstall barriers."""
    if BARRIER in json.dumps(runtime_games, ensure_ascii=False):
        raise ValueError('Runtime metadata retains a barrier reference')
    structures = 0
    for path in Path(bp).joinpath('structures').rglob('*.mcstructure'):
        root = n.load(path, gzipped=False, byteorder='little')
        palette = root['structure']['palette']['default']['block_palette']
        if any(str(p.get('name')) == BARRIER for p in palette):
            raise ValueError('Final pack retains a barrier structure: ' + str(path))
        structures += 1
    # Comparison/safety checks may mention barrier. Placement literals may not.
    for path in Path(bp).rglob('*'):
        if not path.is_file() or path.suffix not in ('.js', '.mcfunction'):
            continue
        text = path.read_text(encoding='utf-8-sig')
        placement = r'\b(?:setblock(?:\s+[^\s\n]+){3}|fill(?:\s+[^\s\n]+){6})\s+(?:minecraft:)?barrier\b'
        if re.search(placement, text):
            raise ValueError('Function retains barrier placement: ' + str(path))
        if re.search(r'\b(?:setType|resolve)\s*\(\s*[\'"]minecraft:barrier[\'"]', text):
            raise ValueError('Script retains barrier placement: ' + str(path))
    fixture_types = {f['type'] for game in runtime_games for f in game.get('fixtures', [])
                     if f['type'].startswith(('junket:', 'junket_original:'))}
    checked = set()
    for path in Path(bp).joinpath('entities').glob('*.json'):
        entity = json.loads(path.read_text(encoding='utf-8-sig'))['minecraft:entity']
        identifier = entity['description']['identifier']
        if identifier not in fixture_types:
            continue
        if entity.get('components', {}).get('minecraft:physics', {}).get('has_collision') is not False:
            raise ValueError('Fixed fixture can obstruct players: ' + identifier)
        for group in entity.get('component_groups', {}).values():
            if group.get('minecraft:physics', {}).get('has_collision') is True:
                raise ValueError('Fixed fixture group restores collision: ' + identifier)
        checked.add(identifier)
    if checked != fixture_types:
        raise ValueError('Fixed fixture collision definition is missing')
    return {'structures_checked': structures, 'runtime_barrier_references': 0,
            'fixed_entity_types_without_movement_collision': len(checked),
            'scope': 'pack data and known literal placement calls; BDS execution tested separately'}


def normalized_overlay(overlay):
    return {k: v for k, v in overlay.items() if k != 'block'}


def validate_barrier_revision(current, previous):
    """Accept only the explicit same-source, same-bounds barrier migration."""
    if current.get('barrier_revision') != POLICY_REVISION:
        raise ValueError('Explicit barrier policy revision required')
    if current.get('source_sha256') != previous.get('source_sha256') or len(current.get('source_sha256', '')) != 64:
        raise ValueError('Barrier revision changed original archive')
    if [g['id'] for g in current['games']] != EXPECTED_IDS or [g['id'] for g in previous['games']] != EXPECTED_IDS:
        raise ValueError('Barrier revision changed game inventory')
    reports = []
    for game, old in zip(current['games'], previous['games']):
        source_floor_policy(game)  # Preflight: reject missing private policy before world shutdown.
        for field in ('production_origin', 'translation', 'size', 'destination', 'floor_template', 'source_seats'):
            if game.get(field) != old.get(field):
                raise ValueError('Barrier revision changed non-barrier placement: ' + field)
        if len(game['structures']) != len(old['structures']):
            raise ValueError('Barrier revision changed tile inventory')
        changed = 0
        for tile, before_tile in zip(game['structures'], old['structures']):
            for field in ('origin', 'size', 'identifier'):
                if tile[field] != before_tile[field]:
                    raise ValueError('Barrier revision moved a source tile')
            after, before = load_structure(tile), load_structure(before_tile)
            a, b = after['structure'], before['structure']
            if a.get('entities') != b.get('entities') or a['palette']['default'].get('block_position_data') != b['palette']['default'].get('block_position_data'):
                raise ValueError('Barrier revision changed entities or block entity data')
            ap, bp = a['palette']['default']['block_palette'], b['palette']['default']['block_palette']
            if any(str(p['name']) == BARRIER for p in ap):
                raise ValueError('Barrier remains in revised repair structure')
            if len(a['block_indices']) != len(b['block_indices']):
                raise ValueError('Barrier revision changed structure layers')
            for layer, (ai, bi) in enumerate(zip(a['block_indices'], b['block_indices'])):
                if len(ai) != len(bi):
                    raise ValueError('Barrier revision changed structure coverage')
                for index, (av, bv) in enumerate(zip(ai, bi)):
                    av = ap[int(av)] if int(av) >= 0 else AIR
                    bv = bp[int(bv)] if int(bv) >= 0 else AIR
                    if av == bv:
                        continue
                    expected = floor_replacement(game, cell_location(tile, index)) if layer == 0 else 'minecraft:air'
                    if str(bv['name']) != BARRIER or str(av['name']) != expected or av.get('states'):
                        raise ValueError('Barrier revision changed an ordinary source block')
                    changed += 1
        overlays, old_overlays = game.get('block_overlays', []), old.get('block_overlays', [])
        if len(overlays) != len(old_overlays):
            raise ValueError('Barrier revision changed gameplay floor coverage')
        for item, before in zip(overlays, old_overlays):
            if normalized_overlay(item) != normalized_overlay(before):
                raise ValueError('Barrier revision changed floor coordinates or states')
            if item['block'] != before['block']:
                at = tuple(item['location'][k] for k in 'xyz')
                expected = floor_replacement(game, at)
                # Hope's existing coloured pad states are already specified by
                # its runtime; use neutral support beneath the source models.
                if before['block'] != BARRIER or expected == 'minecraft:air' or item['block'] != expected:
                    raise ValueError('Barrier revision changed a non-barrier gameplay floor')
            if item['block'] == BARRIER:
                raise ValueError('Barrier remains in runtime floor overlay')
        reports.append({'id': game['id'], 'changed_source_barriers': changed})
    return reports


def stage_barrier_removal(placement, games):
    """Scan only recorded volumes; every write requires actual barrier identity."""
    reports = []
    for game in games:
        box = bounds(game['production_origin'], game['size'])
        placement.require_chunks([box])
        lo, hi = box
        count = Counter()
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    at = (x, y, z)
                    for layer in range(2):
                        value = placement.get(at, layer)
                        if str(value['name']) != BARRIER:
                            continue
                        name = floor_replacement(game, at) if layer == 0 else 'minecraft:air'
                        placement.set(at, block(name, value.get('version', AIR['version'])), layer)
                        count[name] += 1
        reports.append({'id': game['id'], 'bounds': box, 'barriers_removed': sum(count.values()),
                        'replacements': dict(count), 'non_barrier_blocks_replaced': 0})
    return reports


def apply_barrier_revision(working, current, previous, rollback_path, *, full_backup, world_is_stopped):
    if world_is_stopped is not True or not Path(full_backup).is_file():
        raise ValueError('Stopped world and completed full backup required')
    source = validate_barrier_revision(current, previous)
    world = ConvertedWorld(working)
    try:
        placement = NativePlacement(world)
        games = stage_barrier_removal(placement, current['games'])
        # No game_reports bounding boxes are added: NativePlacement therefore
        # keeps every block-entity record, including within each venue.
        proof = commit_placement(world, placement, rollback_path)
        proof.update(mode='barrier_only_visible_floor_revision', games=games,
                     source_validation=source, non_barrier_blocks_replaced=0,
                     all_target_barriers_removed=True)
        return proof
    finally:
        world.db.close()
