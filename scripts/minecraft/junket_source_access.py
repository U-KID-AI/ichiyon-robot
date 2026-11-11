"""Read-only source-voxel access and conservative service-button placement."""
from collections import deque
import hashlib
import math
from pathlib import Path

import nbtlib

AIR = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}


def plain(value):
    if isinstance(value, (dict, nbtlib.Compound)):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, nbtlib.String):
        return str(value)
    if isinstance(value, (nbtlib.Byte, nbtlib.Int, nbtlib.Short, nbtlib.Long)):
        return int(value)
    return value


def source_blocks(layout, overlays=()):
    blocks = {}
    for tile in layout['structures']:
        path = Path(tile['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != tile['sha256']:
            raise ValueError('Source tile changed: ' + str(path))
        root = nbtlib.load(path, gzipped=False, byteorder='little')
        sx, sy, sz = map(int, root['size'])
        palette = [plain(value) for value in root['structure']['palette']['default']['block_palette']]
        for index, raw in enumerate(root['structure']['block_indices'][0]):
            value = int(raw)
            if value < 0:
                continue
            position = (tile['origin'][0] + index // (sy * sz),
                        tile['origin'][1] + (index // sz) % sy,
                        tile['origin'][2] + index % sz)
            blocks[position] = palette[value]
    for overlay in overlays:
        at = overlay['location']
        blocks[tuple(int(at[a]) for a in 'xyz')] = {'name': overlay['block'], 'states': overlay.get('states', {})}
    return blocks


def name(blocks, position):
    return blocks.get(position, {'name': 'minecraft:air'})['name']


def clear(block):
    return block in AIR or 'light_block' in block


def passable(block):
    return clear(block) or any(value in block for value in ('_sign', ':standing_sign', ':wall_sign', '_button', '_carpet', '_banner'))


def floor(block):
    if 'double' in block and 'slab' in block:
        return True
    return not (passable(block) or any(part in block for part in
        ('water', 'lava', 'stairs', 'slab', 'fence', 'wall', 'door', 'pane', 'chain', 'skull', 'head',
         'pot', 'grindstone', 'scaffolding', 'lectern', 'lightning_rod', 'trapdoor', 'chest', 'ladder', 'rail')))


def reachable_cells(layout, blocks, starts=None, *, strict_clearance=False, blocked=()):
    origin = layout['production_origin']; size = layout['size']
    clearance = clear if strict_clearance else passable
    blocked = set(blocked)
    cells = set()
    for (x, y, z), block in blocks.items():
        if floor(block['name']) and y + 2 < origin[1] + size[1]:
            if ((x, y + 1, z) not in blocked and (x, y + 2, z) not in blocked and
                    clearance(name(blocks, (x, y + 1, z))) and clearance(name(blocks, (x, y + 2, z)))):
                cells.add((x, y + 1, z))
    starts = [tuple(math.floor(value) for value in at) for at in (starts or [layout['destination']])]
    for start in starts:
        if start not in cells:
            raise ValueError(f'{layout["id"]}: start is not a conservatively walkable source cell {start}')
    queue = deque(starts); paths = {start: None for start in starts}; distances = {start: 0 for start in starts}
    while queue:
        x, y, z = queue.popleft()
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            for dy in (0, 1, -1):
                candidate = (x + dx, y + dy, z + dz)
                # Step-up requires the old column's headroom too.
                if (candidate in cells and candidate not in paths and
                        (dy != 1 or clearance(name(blocks, (x, y + 2, z))))):
                    paths[candidate] = (x, y, z)
                    distances[candidate] = distances[(x, y, z)] + 1
                    queue.append(candidate)
    return paths, distances


def route(paths, at):
    result = []
    while at is not None:
        result.append(list(at)); at = paths[at]
    return list(reversed(result))


def service_controls(layout, venue, blocks=None):
    blocks = blocks or source_blocks(layout, venue.get('block_overlays', []))
    paths, distances = reachable_cells(layout, blocks)
    arrival = tuple(math.floor(value) for value in layout['destination'])
    occupied = {tuple(math.floor(value) for value in f['location']) for f in venue['fixtures']}
    occupied.update((arrival[0] + dx, arrival[1], arrival[2] + dz) for dx in range(-1, 2) for dz in range(-1, 2))
    dynamic = {tuple(int(o['location'][a]) for a in 'xyz') for o in venue.get('block_overlays', [])}
    candidates = [at for at in paths if at not in occupied and distances[at] <= 24
                  and clear(name(blocks, at)) and clear(name(blocks, (at[0], at[1] + 1, at[2])))
                  and name(blocks, (at[0], at[1] - 1, at[2])) != 'minecraft:barrier'
                  and (at[0], at[1] - 1, at[2]) not in dynamic]
    candidates.sort(key=lambda at: (distances[at], abs(at[1] - arrival[1]), at))
    candidate_set = set(candidates)
    controls = []
    for action, text in [('reception', '参加受付\n説明・再戦'), ('return', 'カタログへ戻る'),
                         ('reset', 'ゲームをリセット'), ('repair', '会場を修復')]:
        selected = None
        for button in candidates:
            if button in occupied:
                continue
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                sign = (button[0] + dx, button[1], button[2] + dz)
                if sign in candidate_set and sign not in occupied:
                    selected = button, sign; break
            if selected:
                break
        if selected is None:
            raise ValueError(f'{layout["id"]}: no reachable air cells for service control {action}')
        button, sign = selected
        occupied.update(selected)
        support = (button[0], button[1] - 1, button[2])
        controls.append({'action': action, 'button': list(button), 'support': list(support), 'sign': list(sign),
            'button_block': {'name': 'minecraft:stone_button', 'states': {'button_pressed_bit': False, 'facing_direction': 1}},
            'support_block': {k: v for k, v in blocks[support].items() if k in ('name', 'states')},
            'sign_block': {'name': 'minecraft:standing_sign', 'states': {'ground_sign_direction': 0}},
            'text': text,
            'source_evidence': {
                'button_original': name(blocks, button), 'sign_original': name(blocks, sign),
                'support_preserved': True, 'barriers_removed': 0}})
    # Preserve hardware placement, but prove access in the world after installing
    # every control. Carpet and buttons have collision despite isSolid == false;
    # a feet-cell teleport must end beside the control, never inside it.
    hardware = {tuple(control[key]) for control in controls for key in ('button', 'sign')}
    paths, distances = reachable_cells(layout, blocks, strict_clearance=True, blocked=hardware)
    for control in controls:
        x, y, z = control['button']
        stands = [(x + dx, y + dy, z + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
                  for dy in (0, -1, 1)]
        stands = [at for at in stands if at in paths]
        if not stands:
            raise ValueError(f'{layout["id"]}: no clear adjacent approach to service control {control["action"]}')
        stand = min(stands, key=lambda at: (abs(at[1] - y), distances[at], at))
        path = route(paths, stand)
        control['stand'] = [stand[0] + .5, stand[1], stand[2] + .5]
        control['path'] = path
        control['source_evidence'].update({'walk_steps': distances[stand], 'path': path,
            'path_clearance': 'air-or-light-only', 'excludes_installed_controls': True})
    return controls


def repair_structures(layout):
    result = []
    for tile in layout['structures']:
        offset = [a - b for a, b in zip(tile['origin'], layout['production_origin'])]
        if not all(0 <= at and at + count <= limit for at, count, limit in zip(offset, tile['size'], layout['size'])):
            raise ValueError('Repair tile escapes source venue bounds')
        result.append({'id': tile['identifier'], 'offset': offset, 'size': tile['size'], 'sha256': tile['sha256']})
    return result


def block_definition(blocks, at):
    block = blocks.get(tuple(at), {'name': 'minecraft:air', 'states': {}})
    return {'name': block['name'], 'states': dict(block.get('states', {}))}


def wall_support(block):
    """Only an existing opaque full block can support an entry panel."""
    return (floor(block) and block not in ('minecraft:sand', 'minecraft:red_sand', 'minecraft:gravel')
        and not any(value in block for value in ('barrier', 'glass', 'leaves', 'slime', 'honey', 'ice', 'concrete_powder'))
        and any(value in block for value in ('stone', 'concrete', 'terracotta', 'wool', 'bricks', 'quartz',
                                            'obsidian', 'planks', 'purpur', '_block', 'andesite', 'diorite', 'granite')))


def wall_panel(layout, venue, blocks, legacy=()):
    """Choose a reachable two-cell wall face near the original entry.

    This never creates a wall or removes source geometry. The block column
    carrying a button/sign is excluded from the resulting walking path.
    """
    paths, distances = reachable_cells(layout, blocks, strict_clearance=True)
    arrival = tuple(math.floor(v) for v in layout['destination'])
    legacy_cells = {tuple(c[k]) for c in legacy for k in ('button', 'sign')}
    dynamic = {tuple(int(o['location'][a]) for a in 'xyz') for o in venue.get('block_overlays', [])}
    props = [f['location'] for f in venue['fixtures'] if f['role'] not in ('reception', 'return')]
    def near_prop(at):
        return any(abs(p[1] - at[1]) < 2.5 and math.hypot(p[0] - at[0] - .5, p[2] - at[2] - .5) < 1.5 for p in props)
    def near_door(at):
        return any('door' in name(blocks, (at[0]+x, at[1]+y, at[2]+z))
                   for x in range(-2, 3) for y in (-1, 0, 1) for z in range(-2, 3))
    yaw = math.radians(layout.get('rotation', 0)); forward = (-math.sin(yaw), math.cos(yaw))
    candidates = []
    # Normal points from source wall towards the player; integer facing states
    # are shared by vanilla wall signs and buttons (N/S/W/E = 2/3/4/5).
    for stand in paths:
        x, y, z = stand
        if distances[stand] > 24 or abs(y-arrival[1]) > 1 or (x, y-1, z) in dynamic or near_prop(stand):
            continue
        for nx, nz, facing in ((0,-1,2),(0,1,3),(-1,0,4),(1,0,5)):
            button = (x-nx, y+1, z-nz); support = (x-2*nx, y+1, z-2*nz)
            for sign_side in (1, -1):
                tx, tz = -nz*sign_side, nx*sign_side
                sign = (button[0]+tx, button[1], button[2]+tz)
                sign_support = (support[0]+tx, support[1], support[2]+tz)
                if any(at in legacy_cells or at in dynamic or not clear(name(blocks, at))
                       or near_prop(at) or near_door(at) for at in (button, sign)):
                    continue
                if not all(at not in dynamic and wall_support(name(blocks, at)) for at in (support, sign_support)):
                    continue
                # A two-block-tall source wall face, not a table top or isolated
                # decorative cube, gives a stable player-height mounting area.
                if not all(wall_support(name(blocks, (at[0], y, at[2]))) for at in (support, sign_support)):
                    continue
                dx, dz = button[0]-arrival[0], button[2]-arrival[2]
                forward_distance = dx*forward[0]+dz*forward[1]
                score = (distances[stand] + 3*abs(y-arrival[1]) + max(0, -forward_distance)*.25,
                         near_prop(stand), distances[stand], button, sign)
                candidates.append((score, stand, button, support, sign, sign_support, facing))
    for _, stand, button, support, sign, sign_support, facing in sorted(candidates):
        try:
            actual_paths, actual_distances = reachable_cells(layout, blocks, strict_clearance=True, blocked=(button, sign))
        except ValueError:
            continue
        if stand not in actual_paths:
            continue
        path = route(actual_paths, stand)
        return {'action': 'menu', 'button': list(button), 'support': list(support), 'sign': list(sign),
            'sign_support': list(sign_support),
            'button_block': {'name': 'minecraft:stone_button', 'states': {'button_pressed_bit': False, 'facing_direction': facing}},
            'support_block': block_definition(blocks, support), 'sign_support_block': block_definition(blocks, sign_support),
            'sign_block': {'name': 'minecraft:wall_sign', 'states': {'facing_direction': facing}},
            'text': 'ゲーム操作', 'stand': [stand[0]+.5, stand[1], stand[2]+.5], 'path': path,
            'source_evidence': {'walk_steps': actual_distances[stand], 'path': path,
                'button_original': name(blocks, button), 'sign_original': name(blocks, sign),
                'button_original_block': block_definition(blocks, button), 'sign_original_block': block_definition(blocks, sign),
                'support_preserved': True, 'barriers_removed': 0, 'wall_mounted': True,
                'path_clearance': 'air-or-light-only', 'excludes_installed_controls': True,
                'legacy_cells_disjoint': True, 'interaction_distance': math.dist([v+.5 for v in button], [stand[0]+.5, stand[1]+1.62, stand[2]+.5])}}
    raise ValueError(f'{layout["id"]}: no original wall supports a clear, reachable entry panel')


def panel_guides(layout, venue, blocks, panel):
    """Place Bedrock-only guides beside the entry without covering its panel."""
    paths, distances = reachable_cells(layout, blocks, strict_clearance=True,
        blocked=[tuple(panel[key]) for key in ('button', 'sign')])
    arrival = layout['destination']; stand = panel['stand']
    dynamic = {tuple(int(o['location'][a]) for a in 'xyz') for o in venue.get('block_overlays', [])}
    props = [f['location'] for f in venue['fixtures'] if f['role'] not in ('reception','return')]
    protected = {tuple(at) for at in panel['path']}
    selected = []
    for role in ('reception', 'return'):
        candidates = []
        for at in paths:
            pos = [at[0]+.5, at[1], at[2]+.5]
            if at in protected or abs(at[1]-arrival[1]) > 1 or distances[at] > 16 or (at[0],at[1]-1,at[2]) in dynamic:
                continue
            if math.dist(pos, arrival) < 1.5 or math.dist(pos, stand) < 1.5:
                continue
            if any(abs(p[1]-pos[1])<2.5 and math.hypot(p[0]-pos[0],p[2]-pos[2])<1.6 for p in props+selected):
                continue
            # Reserve the button/sign columns and keep the approach unobstructed.
            if any(math.hypot(pos[0]-panel[key][0]-.5,pos[2]-panel[key][2]-.5)<1.6 for key in ('button','sign')):
                continue
            candidates.append(((abs(pos[1]-arrival[1]), math.dist(pos,arrival)+.25*math.dist(pos,stand),pos),pos))
        if not candidates:
            raise ValueError(f'{layout["id"]}: no safe entry position for {role} guide')
        pos = min(candidates)[1]; selected.append(pos)
    return dict(zip(('reception','return'),selected))
