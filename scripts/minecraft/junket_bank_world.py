"""Exact-ledger bank placement for a stopped, backed-up Bedrock working copy.

The authorized survey footprint is fixed.  No legacy game hall cleanup is used.
All terrain changes must match the coordinate/state ledger from the stopped
production survey.  NativePlacement retains the world codecs and rollback path.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

import nbtlib as n

from junket_catalog_world import encode_record, parse_records
from junket_native_world import (AIR, AIR_NAMES, SAFE_TILES, NativePlacement, bounds,
                                commit_placement, decode_section, inside, prefix)
from junket_structure import ConvertedWorld

ORIGIN = [-194, 63, 284]
SIZE = [46, 31, 30]
BOX = bounds(ORIGIN, SIZE)
ARRIVAL = [-186.5, 65, 304.5]
ENTRANCE = [-189.5, 65, 304.5]
NPCS = [[-172.5, 65, z] for z in (301.5, 298.5, 305.5, 308.5)]
BUTTON = [-187, 66, 309]
SIGN = [-186, 66, 309]


def digest_json(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')).hexdigest()


def positions():
    for x in range(ORIGIN[0], ORIGIN[0]+SIZE[0]):
        for y in range(ORIGIN[1], ORIGIN[1]+SIZE[1]):
            for z in range(ORIGIN[2], ORIGIN[2]+SIZE[2]):
                yield (x, y, z)


def rle(values):
    result = []
    for value in values:
        if result and result[-1][0] == value:
            result[-1][1] += 1
        else:
            result.append([value, 1])
    return result


def unroll(rows, palette_count):
    values = []
    for index, count in rows:
        if not isinstance(count, int) or not 0 < count <= SIZE[0]*SIZE[1]*SIZE[2]:
            raise ValueError('Invalid bank before-ledger run')
        if not isinstance(index, int) or not 0 <= index < palette_count:
            raise ValueError('Invalid bank before-ledger palette index')
        values.extend([index]*count)
    if len(values) != SIZE[0]*SIZE[1]*SIZE[2]:
        raise ValueError('Incomplete bank before ledger')
    return values


def default_overlays():
    sign = n.Compound({'id': n.String('Sign'), 'x': n.Int(SIGN[0]), 'y': n.Int(SIGN[1]),
        'z': n.Int(SIGN[2]), 'IsWaxed': n.Byte(1),
        'FrontText': n.Compound({'Text': n.String('JUNKET CRAFT\n17ゲーム選択\nボタンをタップ\nカラス銀行'),
            'SignTextColor': n.Int(-16777216), 'IgnoreLighting': n.Byte(0), 'PersistFormatting': n.Byte(1)}),
        'BackText': n.Compound({'Text': n.String(''), 'SignTextColor': n.Int(-16777216),
            'IgnoreLighting': n.Byte(0), 'PersistFormatting': n.Byte(1)})})
    return [
        {'location': dict(zip('xyz', BUTTON)), 'block': 'minecraft:stone_button',
         'states': {'facing_direction': 2, 'button_pressed_bit': False}},
        {'location': dict(zip('xyz', SIGN)), 'block': 'minecraft:birch_wall_sign',
         'states': {'facing_direction': 2}, 'block_entity_snbt': sign.snbt()},
    ]


def validate_empty_plot(at, block, layer):
    """Recognize only the surveyed dirt/grass and coordinate-bounded dividers."""
    x, y, z = at
    divider = z in (310, 311) or (x in (-170, -169) and 293 <= z <= 313)
    expected = ('minecraft:air' if layer else 'minecraft:dirt' if y == 63 else
                ('minecraft:dirt' if divider else 'minecraft:grass_block') if y == 64 else
                'minecraft:netherite_block' if y == 65 and divider else 'minecraft:air')
    if str(block['name']) != expected or block.get('states'):
        raise ValueError(f'Bank footprint is not the surveyed empty plot: {at}/{layer}: {block.snbt()}')


def footprint_snapshot(placement, *, verify_plot=False):
    palette = []; lookup = {}; layers = [[], []]; counts = Counter()
    for at in positions():
        for layer in range(2):
            block = placement.get(at, layer)
            if verify_plot:
                validate_empty_plot(at, block, layer)
            signature = block.snbt()
            if signature not in lookup:
                lookup[signature] = len(palette); palette.append(signature)
            layers[layer].append(lookup[signature])
            if layer == 0:
                counts[str(block['name'])] += 1
    tiles = []
    for cx in range(BOX[0][0]//16, BOX[1][0]//16+1):
        for cz in range(BOX[0][2]//16, BOX[1][2]//16+1):
            for at, _, raw in parse_records(placement.world.get(prefix(cx, cz)+b'1') or b''):
                if inside(at, BOX):
                    tiles.append({'at': list(at), 'base64': base64.b64encode(raw).decode('ascii')})
    if verify_plot and tiles:
        raise ValueError('Unexpected existing block entity in surveyed empty bank plot')
    return {'order': 'x,y,z; z fastest; absolute origin + local index',
            'palette_snbt': palette, 'layers_rle': [rle(v) for v in layers],
            'block_entities': sorted(tiles, key=lambda v: v['at']), 'primary_counts': dict(counts)}


def prepare_bank_plan(world_path, structure_path, output_path, overlays=None):
    raw = Path(structure_path).read_bytes()
    source = n.File.parse(BytesIO(raw), byteorder='little')
    if list(map(int, source['size'])) != SIZE:
        raise ValueError('Bank structure size does not match surveyed footprint')
    world = ConvertedWorld(world_path)
    try:
        placement = NativePlacement(world); placement.require_chunks([BOX])
        before = footprint_snapshot(placement, verify_plot=True)
    finally:
        world.db.close()
    plan = {'schema': 1, 'id': 'karasu_bank', 'origin': ORIGIN, 'size': SIZE,
            'bounds': BOX, 'source_structure_sha256': sha256(raw).hexdigest(),
            'before': before, 'before_sha256': digest_json(before),
            'block_overlays': default_overlays() if overlays is None else overlays,
            'arrival': ARRIVAL, 'entrance': ENTRANCE, 'source_npc_positions': NPCS,
            'underground_protected_through_y': 62}
    plan['plan_sha256'] = digest_json(plan)
    path = Path(output_path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
    return plan


def validate_plan(plan):
    expected_hash = plan.get('plan_sha256')
    unsigned = {k: v for k, v in plan.items() if k != 'plan_sha256'}
    if expected_hash != digest_json(unsigned):
        raise ValueError('Bank plan digest mismatch')
    if plan['schema'] != 1 or plan['origin'] != ORIGIN or plan['size'] != SIZE or plan['bounds'] != BOX:
        raise ValueError('Bank plan targets an unauthorized footprint')
    if plan['before_sha256'] != digest_json(plan['before']):
        raise ValueError('Bank before-ledger digest mismatch')
    if plan.get('underground_protected_through_y') != 62:
        raise ValueError('Bank plan underground protection differs')
    if plan.get('block_overlays') != default_overlays():
        raise ValueError('Bank panel differs from the recorded two-control contract')


def compare_before(placement, plan):
    before = plan['before']; palette = before['palette_snbt']
    layers = [unroll(row, len(palette)) for row in before['layers_rle']]
    if len(layers) != 2:
        raise ValueError('Both bank terrain layers are required')
    for index, at in enumerate(positions()):
        for layer in range(2):
            actual = placement.get(at, layer)
            if actual.snbt() != palette[layers[layer][index]]:
                raise ValueError(f'Bank live copy differs from approved before ledger: {at}/{layer}')
    actual = footprint_snapshot(placement)
    if actual['block_entities'] != before['block_entities']:
        raise ValueError('Bank block entities changed since the before ledger')


def overlay_block(overlay):
    name = overlay['block']
    if name not in ('minecraft:stone_button', 'minecraft:birch_wall_sign'):
        raise ValueError('Bank overlay type is not an approved panel control')
    states = {k: n.Byte(v) if isinstance(v, bool) else n.Int(v) if isinstance(v, int)
              else n.String(v) for k, v in overlay.get('states', {}).items()}
    return n.Compound({'name': n.String(name), 'states': n.Compound(states), 'version': AIR['version']})


class BankPlacement(NativePlacement):
    def stage_bank(self, plan, structure_path):
        validate_plan(plan)
        self.require_chunks([BOX]); compare_before(self, plan)
        raw = Path(structure_path).read_bytes()
        if sha256(raw).hexdigest() != plan['source_structure_sha256']:
            raise ValueError('Bank source structure digest mismatch')
        source = n.File.parse(BytesIO(raw), byteorder='little')
        if list(map(int, source['size'])) != SIZE or source['structure']['entities']:
            raise ValueError('Bank source size/entities changed')
        data = source['structure']; palette = data['palette']['default']['block_palette']
        forbidden = {'minecraft:barrier', 'minecraft:command_block', 'minecraft:chain_command_block',
                     'minecraft:repeating_command_block', 'minecraft:structure_block', 'minecraft:jigsaw'}
        if any(str(v['name']) in forbidden for v in palette):
            raise ValueError('Executable or invisible barrier block in bank source')
        indices = data['block_indices']
        if len(indices) != 2 or any(len(layer) != SIZE[0]*SIZE[1]*SIZE[2] for layer in indices):
            raise ValueError('Incomplete bank structure layers')
        for index, at in enumerate(positions()):
            for layer in range(2):
                entry = int(indices[layer][index])
                self.set(at, AIR if entry < 0 else palette[entry], layer)
        for index, entry in data['palette']['default'].get('block_position_data', {}).items():
            local = [int(index)//(SIZE[1]*SIZE[2]), int(index)//SIZE[2] % SIZE[1], int(index) % SIZE[2]]
            at = tuple(a+b for a, b in zip(ORIGIN, local))
            if not inside(at, BOX):
                raise ValueError('Source bank block entity outside structure')
            entity = deepcopy(entry['block_entity_data'])
            if str(entity.get('id')) not in SAFE_TILES - {'CommandBlock'}:
                raise ValueError('Unsafe bank source block entity')
            entity.update({k: n.Int(v) for k, v in zip('xyz', at)})
            self.tiles[at] = encode_record(n.File(entity))
        seen = set()
        for overlay in plan['block_overlays']:
            at = tuple(overlay['location'][k] for k in 'xyz')
            if at not in (tuple(BUTTON), tuple(SIGN)) or at in seen or any(type(v) is not int for v in at):
                raise ValueError('Bank panel overlay location is not approved')
            seen.add(at); self.set(at, overlay_block(overlay)); self.set(at, AIR, 1)
            self.tiles.pop(at, None)
            if 'block_entity_snbt' in overlay:
                entity = n.parse_nbt(overlay['block_entity_snbt'])
                if at != tuple(SIGN) or str(entity.get('id')) != 'Sign':
                    raise ValueError('Bank panel block entity must be the approved sign')
                entity.update({k: n.Int(v) for k, v in zip('xyz', at)})
                self.tiles[at] = encode_record(n.File(entity))
        self.game_reports.append({'id': 'karasu_bank', 'origin': ORIGIN, 'size': SIZE,
            'old_bounds': BOX, 'new_bounds': BOX, 'structures': 1,
            'structure_cells': SIZE[0]*SIZE[1]*SIZE[2], 'gameplay_block_overlays': len(seen)})
        for at in (BUTTON, SIGN):
            backing = (at[0], at[1], at[2]+1)
            if str(self.get(backing)['name']) != 'minecraft:smooth_quartz':
                raise ValueError(f'Bank panel source-wall support missing: {backing}')

    def values(self):
        # Keep NativePlacement's block encoding, heights and native biome tail.
        # Replace only this bank's exact block entity footprint, including Y63/64;
        # the legacy venue cleanup has a hard-coded Y65 threshold and is not used.
        tiles, reports = self.tiles, self.game_reports
        self.tiles, self.game_reports = {}, []
        try:
            values = super().values()
        finally:
            self.tiles, self.game_reports = tiles, reports
        chunks = {(at[0]//16, at[2]//16) for at in self.changed_positions | set(self.tiles)}
        for cx, cz in chunks:
            key = prefix(cx, cz)+b'1'; old = self.world.get(key) or b''
            kept = [raw for at, _, raw in parse_records(old) if not inside(at, BOX)]
            kept.extend(raw for at, raw in self.tiles.items() if (at[0]//16, at[2]//16) == (cx, cz))
            new = b''.join(kept)
            if new != old:
                values[key] = new
        # Every cell outside the authorized footprint, even in a shared chunk
        # or the Y48..63 subchunk, must retain its exact block palette entry.
        outside = 0
        for key in self.changed_sections:
            import struct
            cx, cz = struct.unpack('<ii', key[:8]); sy = struct.unpack('b', key[-1:])[0]
            old = decode_section(self.original_sections[key], sy)
            for lx in range(16):
                for lz in range(16):
                    for ly in range(16):
                        at = (cx*16+lx, sy*16+ly, cz*16+lz)
                        if inside(at, BOX):
                            continue
                        i = lx*256+lz*16+ly
                        for layer in range(2):
                            expected = old['layers'][layer]['palette'][old['layers'][layer]['indices'][i]] if layer < len(old['layers']) else AIR
                            if self.get(at, layer) != expected:
                                raise ValueError(f'Bank staging modified protected cell: {at}/{layer}')
                        outside += 1
        self.outside_cells_verified = outside
        return values


def verify_bank(world, placement, plan):
    count = 0
    for at in positions():
        actual = world.blocks(*at)
        for layer in range(2):
            expected = placement.get(at, layer)
            block = actual[layer] if layer < len(actual) else AIR
            # An implicit missing air storage has no palette version.
            if str(expected['name']) in AIR_NAMES and str(block['name']) in AIR_NAMES and not expected.get('states') and not block.get('states'):
                continue
            if expected != block:
                raise ValueError(f'Saved bank structure cell differs: {at}/{layer}')
        count += 1
    access = []
    import math
    for role, point in [('arrival', ARRIVAL), ('entrance', ENTRANCE)]:
        x, y, z = map(math.floor, point)
        blocks = [placement.get((x, yy, z)) for yy in (y-1, y, y+1)]
        names = [str(b['name']) for b in blocks]
        if names[0] in AIR_NAMES or any(name not in AIR_NAMES for name in names[1:]):
            raise ValueError(f'Bank {role} lacks floor or clearance: {names}')
        access.append({'role': role, 'position': point, 'floor_feet_head': names})
    npc = []
    for point in NPCS:
        x, y, z = map(math.floor, point)
        npc.append({'position': point, 'floor_feet_head': [str(placement.get((x, yy, z))['name']) for yy in (y-1, y, y+1)]})
    if str(placement.get(tuple(BUTTON))['name']) != 'minecraft:stone_button' or str(placement.get(tuple(SIGN))['name']) != 'minecraft:birch_wall_sign':
        raise ValueError('Bank panel blocks missing')
    # Verify actual persisted block entity payloads, not just staging metadata.
    for at, expected in placement.tiles.items():
        records = {pos: raw for pos, _, raw in parse_records(world.get(prefix(at[0]//16, at[2]//16)+b'1') or b'')}
        if records.get(at) != expected:
            raise ValueError(f'Bank saved decoration/sign differs: {at}')
    return {'structure_and_overlay_cells_verified': count, 'layers_verified': 2,
            'block_entities_verified': len(placement.tiles), 'access': access,
            'source_npc_positions': npc, 'button': BUTTON, 'sign': SIGN,
            'panel_backing': 'minecraft:smooth_quartz', 'panel_facing_direction': 2,
            'outside_footprint_cells_verified': placement.outside_cells_verified,
            'outside_footprint_unchanged': True, 'underground_through_y62_unchanged': True,
            'source_structure_sha256': plan['source_structure_sha256'], 'plan_sha256': plan['plan_sha256']}


def apply_bank(working, plan, structure_path, rollback_path, *, full_backup, world_is_stopped):
    """Entry point for the existing managed release / isolated BDS runner."""
    if world_is_stopped is not True or not Path(full_backup).exists():
        raise ValueError('Bank placement requires stopped world and completed full backup')
    if isinstance(plan, (str, Path)):
        plan = json.loads(Path(plan).read_text('utf-8'))
    world = ConvertedWorld(working)
    try:
        placement = BankPlacement(world); placement.stage_bank(plan, structure_path)
        result = commit_placement(world, placement, rollback_path)
        result['bank'] = verify_bank(world, placement, plan)
        return result
    finally:
        world.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', type=Path, required=True)
    parser.add_argument('--structure', type=Path, required=True)
    parser.add_argument('--prepare-plan', type=Path)
    parser.add_argument('--apply-plan', type=Path)
    parser.add_argument('--rollback', type=Path)
    parser.add_argument('--full-backup', type=Path)
    parser.add_argument('--world-stopped', action='store_true')
    args = parser.parse_args()
    if bool(args.prepare_plan) == bool(args.apply_plan):
        parser.error('Choose exactly one of --prepare-plan or --apply-plan')
    if args.prepare_plan:
        plan = prepare_bank_plan(args.world, args.structure, args.prepare_plan)
        print(json.dumps({'plan': str(args.prepare_plan), 'plan_sha256': plan['plan_sha256'],
                          'before_counts': plan['before']['primary_counts']}))
    else:
        if not args.rollback or not args.full_backup:
            parser.error('Apply requires --rollback and --full-backup')
        print(json.dumps(apply_bank(args.world, args.apply_plan, args.structure, args.rollback,
                                   full_backup=args.full_backup, world_is_stopped=args.world_stopped)))


if __name__ == '__main__':
    main()
