"""Source-evidenced arrival changes and additive upper-floor completion only."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import math
import re
from pathlib import Path

import nbtlib as n

from junket_catalog_world import parse_records
from junket_native_world import AIR, AIR_NAMES, NativePlacement, bounds, inside, commit_placement
from junket_structure import ConvertedWorld


def reception_corrections(current, previous):
    if current.get('reception_revision') != 1:
        raise ValueError('Reception correction needs an explicit source revision')
    if (not re.fullmatch(r'[0-9a-f]{64}', current.get('source_sha256', ''))
            or current.get('source_sha256') != previous.get('source_sha256')):
        raise ValueError('Original source archive changed')
    if [g['id'] for g in current['games']] != [g['id'] for g in previous['games']]:
        raise ValueError('Reception correction cannot change game inventory')
    result = []
    for game, old in zip(current['games'], previous['games']):
        for key in ('production_origin', 'translation', 'block_overlays'):
            if game.get(key) != old.get(key):
                raise ValueError('Reception correction changed original gameplay/placement: ' + key)
        clean = lambda tile: {k: v for k, v in tile.items() if k != 'path'}
        count = len(old['structures'])
        if [clean(t) for t in game['structures'][:count]] != [clean(t) for t in old['structures']]:
            raise ValueError('Reception correction changed an existing source tile')
        a, b = old['size'], game['size']
        if a[0] != b[0] or a[2] != b[2] or not 0 <= b[1] - a[1] <= 64:
            raise ValueError('Only bounded upward source extensions are allowed')
        arrival = game['destination']
        if len(arrival) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in arrival):
            raise ValueError('Invalid reception arrival')
        if not all(lo <= value < lo + size for lo, value, size in zip(game['production_origin'], arrival, b)):
            raise ValueError('Reception arrival is outside the source building')
        additions = game['structures'][count:]
        changed = arrival != old['destination'] or b != a or bool(additions)
        if changed and not game.get('reception_evidence'):
            raise ValueError('Changed reception lacks original-source evidence')
        extension_origin = [game['production_origin'][0], game['production_origin'][1] + a[1], game['production_origin'][2]]
        extension_size = [b[0], b[1] - a[1], b[2]]
        extension_box = bounds(extension_origin, extension_size)
        if bool(additions) != bool(extension_size[1]):
            raise ValueError('Extension bounds/tiles are inconsistent')
        for tile in additions:
            box = bounds(tile['origin'], tile['size'])
            if not all(inside(at, extension_box) for at in box):
                raise ValueError('Additional tile overlaps the existing building or escapes upper bounds')
        if changed:
            result.append({'id': game['id'], 'old_destination': old['destination'], 'destination': arrival,
                           'evidence': game['reception_evidence'], 'production_origin': extension_origin,
                           'size': extension_size, 'structures': additions})
    return result


def same_block(a, b):
    return a['name'] == b['name'] and dict(a.get('states', {})) == dict(b.get('states', {}))


def stage_extension(placement, extension):
    """Do not clear or overwrite occupied space belonging to another build."""
    expected = {}
    for tile in extension['structures']:
        raw = Path(tile['path']).read_bytes()
        if sha256(raw).hexdigest() != tile['sha256']:
            raise ValueError('Source extension fingerprint mismatch')
        root = n.File.parse(BytesIO(raw), byteorder='little')
        size = list(map(int, root['size']))
        if size != tile['size'] or len(root['structure'].get('entities', [])):
            raise ValueError('Extension size differs or contains unmanaged actors')
        palette = root['structure']['palette']['default']['block_palette']
        indices = root['structure']['block_indices']
        for x in range(size[0]):
            for y in range(size[1]):
                for z in range(size[2]):
                    at = tuple(tile['origin'][i] + v for i, v in enumerate((x, y, z)))
                    if at in expected:
                        raise ValueError('Overlapping source extension tiles')
                    index = x * size[1] * size[2] + y * size[2] + z
                    values = []
                    for layer in range(2):
                        pi = int(indices[layer][index]) if layer < len(indices) else -1
                        value = AIR if pi < 0 else palette[pi]
                        actual = placement.get(at, layer)
                        if str(actual['name']) not in AIR_NAMES and not same_block(actual, value):
                            raise ValueError('Source extension collides with an existing block: ' + str(at))
                        values.append(value)
                    expected[at] = values
    # Reuse the existing encoder/coverage/NBT safety checks, restricted to only
    # this new upper slice; the lower original building is never staged.
    game = {k: deepcopy(extension[k]) for k in ('id', 'production_origin', 'size', 'structures')}
    placement.stage_game(game, {'origin': game['production_origin'], 'size': game['size']})
    box = bounds(game['production_origin'], game['size'])
    for cx, cz in {(x // 16, z // 16) for x, _, z in expected}:
        from junket_native_world import prefix
        for at, _, raw in parse_records(placement.world.get(prefix(cx, cz) + b'1') or b''):
            if inside(at, box) and placement.tiles.get(at) != raw:
                raise ValueError('Source extension would replace an unrelated block entity: ' + str(at))


def apply_reception_corrections(working, current, previous, rollback_path, *, full_backup, world_is_stopped):
    if world_is_stopped is not True or not Path(full_backup).is_file():
        raise ValueError('Stopped world and completed full backup required')
    changes = reception_corrections(current, previous)
    world = ConvertedWorld(working)
    try:
        placement = NativePlacement(world)
        for change in changes:
            if change['structures']:
                stage_extension(placement, change)
        proof = commit_placement(world, placement, rollback_path)
        proof.update({'mode': 'additive_source_upper_floor', 'reception_changes': changes,
                      'existing_building_blocks_replaced': 0})
        return proof
    finally:
        world.db.close()
