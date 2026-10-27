"""Bounded mcstructure placement into a STOPPED, backed-up Bedrock world.

Only existing Overworld chunks are supported. Native biome tails, chunk version,
actors, players and all unrelated DB keys remain byte-for-byte unchanged. Old
JUNKET halls may be replaced inside their recorded bounds; any non-air block in
an extension outside those bounds is a collision, not permission to erase it.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import struct

import nbtlib as n

from build_junket_warp import EXPECTED_IDS
from junket_catalog_world import encode_record, parse_records
from junket_structure import ConvertedWorld

AIR = n.Compound({'name': n.String('minecraft:air'), 'states': n.Compound(),
                  'version': n.Int(18186752)})
AIR_NAMES = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}
SAFE_TILES = {'Sign', 'HangingSign', 'Banner', 'Skull', 'FlowerPot', 'CommandBlock'}


def prefix(cx, cz):
    return struct.pack('<ii', cx, cz)


def section_key(cx, sy, cz):
    return prefix(cx, cz) + b'/' + struct.pack('b', sy)


def decode_section(raw, sy):
    if raw is None:
        return {'version': 9, 'layers': [{'palette': [deepcopy(AIR)], 'indices': [0] * 4096}]}
    stream = BytesIO(raw)
    version, count = stream.read(2)
    if version not in (8, 9) or not 0 <= count <= 2:
        raise ValueError(f'unsupported subchunk format {version}/{count}')
    if version == 9 and struct.unpack('b', stream.read(1))[0] != sy:
        raise ValueError('subchunk Y mismatch')
    if count == 0:
        if stream.tell() != len(raw):
            raise ValueError('unparsed empty subchunk bytes')
        # A saved, fully empty subchunk may have no storages (not a missing
        # chunk). Materialize air for reads, while retaining its native bytes
        # unless a subsequent placement actually adds a block.
        return {'version': version, 'empty_encoding': raw,
                'layers': [{'palette': [deepcopy(AIR)], 'indices': [0] * 4096}]}
    layers = []
    for _ in range(count):
        header = stream.read(1)[0]
        bits = header >> 1
        if header & 1 or bits not in (0, 1, 2, 3, 4, 5, 6, 8, 16):
            raise ValueError(f'unsupported subchunk palette {header}')
        if bits:
            per = 32 // bits
            word_count = (4096 + per - 1) // per
            words = struct.unpack('<' + 'I' * word_count, stream.read(word_count * 4))
            size = struct.unpack('<i', stream.read(4))[0]
            indices = [(words[i // per] >> ((i % per) * bits)) & ((1 << bits) - 1)
                       for i in range(4096)]
        else:
            size, indices = 1, [0] * 4096
        if not 1 <= size <= 65536 or max(indices) >= size:
            raise ValueError('invalid native palette index')
        palette = [n.Compound(n.File.parse(stream, byteorder='little')) for _ in range(size)]
        layers.append({'palette': palette, 'indices': indices})
    if stream.tell() != len(raw):
        raise ValueError('unparsed native subchunk bytes')
    return {'version': version, 'layers': layers}


def encode_section(section, sy):
    if section.get('empty_encoding') is not None and all(
            str(layer['palette'][i]['name']) in AIR_NAMES
            for layer in section['layers'] for i in set(layer['indices'])):
        return section['empty_encoding']
    stream = BytesIO()
    version = section['version']
    stream.write(bytes((version, len(section['layers']))))
    if version == 9:
        stream.write(struct.pack('b', sy))
    for layer in section['layers']:
        # Keep the native palette order, including unused native entries.
        palette, indices = layer['palette'], layer['indices']
        bits = next(v for v in (0, 1, 2, 3, 4, 5, 6, 8, 16) if len(palette) <= (1 << v))
        stream.write(bytes((bits << 1,)))
        if bits:
            per = 32 // bits
            words = [0] * ((4096 + per - 1) // per)
            for i, value in enumerate(indices):
                words[i // per] |= value << ((i % per) * bits)
            stream.write(struct.pack('<' + 'I' * len(words), *words))
            stream.write(struct.pack('<i', len(palette)))
        for value in palette:
            stream.write(encode_record(n.File(value)))
    return stream.getvalue()


def bounds(origin, size):
    return [list(origin), [a + b - 1 for a, b in zip(origin, size)]]


def inside(at, box):
    return all(a <= value <= b for value, a, b in zip(at, *box))


def load_legacy(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    config = json.loads(text[text.index('Object.freeze(') + len('Object.freeze('):text.rindex(');')])
    return {item['id']: item for item in config['VENUES']}


def database_hashes(world):
    return {key: sha256(value).hexdigest() for key, value in world.db.items()}


class NativePlacement:
    def __init__(self, world):
        self.world = world
        self.sections = {}
        self.original_sections = {}
        self.changed_positions = set()
        self.changed_sections = set()
        self.columns = {}
        self.tiles = {}
        self.game_reports = []
        self.palette_signatures = {}

    def section(self, cx, sy, cz):
        key = section_key(cx, sy, cz)
        if key not in self.sections:
            raw = self.world.get(key)
            self.original_sections[key] = raw
            self.sections[key] = decode_section(raw, sy)
        return self.sections[key]

    def get(self, at, layer=0):
        x, y, z = at
        section = self.section(x // 16, y // 16, z // 16)
        if layer >= len(section['layers']):
            return AIR
        value = section['layers'][layer]
        return value['palette'][value['indices'][(x % 16) * 256 + (z % 16) * 16 + y % 16]]

    def set(self, at, value, layer=0):
        x, y, z = at
        if not -64 <= y < 320:
            raise ValueError(f'outside Bedrock Overworld build range: {at}')
        section = self.section(x // 16, y // 16, z // 16)
        if layer >= len(section['layers']):
            if str(value['name']) in AIR_NAMES:
                return
            section['layers'].append({'palette': [deepcopy(AIR)], 'indices': [0] * 4096})
        target = section['layers'][layer]
        index = (x % 16) * 256 + (z % 16) * 16 + y % 16
        if target['palette'][target['indices'][index]] == value:
            return
        lookup = target.get('lookup')
        if lookup is None:
            lookup = {entry.snbt(): i for i, entry in enumerate(target['palette'])}
            target['lookup'] = lookup
        # A structure palette is reused for thousands of cells. Keep its object
        # alive beside the cached signature so recycled Python IDs cannot alias.
        cached = self.palette_signatures.get(id(value))
        if cached is None:
            cached = (value, value.snbt())
            self.palette_signatures[id(value)] = cached
        signature = cached[1]
        if signature not in lookup:
            lookup[signature] = len(target['palette'])
            target['palette'].append(deepcopy(value))
        target['indices'][index] = lookup[signature]
        self.changed_positions.add(tuple(at))
        self.changed_sections.add(section_key(x // 16, y // 16, z // 16))
        self.columns.setdefault((x // 16, z // 16), set()).add((x % 16, z % 16))

    def require_chunks(self, boxes):
        missing = []
        for box in boxes:
            lo, hi = box
            for cx in range(lo[0] // 16, hi[0] // 16 + 1):
                for cz in range(lo[2] // 16, hi[2] // 16 + 1):
                    p = prefix(cx, cz)
                    biome = self.world.get(p + b'+')
                    if self.world.get(p + b',') is None or biome is None or len(biome) < 512:
                        missing.append((cx, cz))
        if missing:
            raise ValueError('generate/save target chunks using BDS before offline apply: ' +
                             json.dumps(sorted(set(missing))))

    def stage_game(self, game, legacy, structures_root=None, legacy_pack=None):
        new_box = bounds(game['production_origin'], game['size'])
        old_box = bounds(legacy['origin'], legacy['size'])
        self.require_chunks([new_box, old_box])
        legacy_root = None
        if legacy_pack is not None:
            legacy_path = Path(legacy_pack) / 'structures' / (legacy['structure'].replace(':', '/') + '.mcstructure')
            legacy_root = n.File.parse(BytesIO(legacy_path.read_bytes()), byteorder='little')
            if list(map(int, legacy_root['size'])) != list(legacy['size']):
                raise ValueError('legacy native structure size differs from original plan')
        # Clear only the former dedicated hall above its ground line. Keep any
        # historical below-ground foundations outside the new original hall.
        old_lo, old_hi = old_box
        new_lo, new_hi = new_box
        for x in range(old_lo[0], old_hi[0] + 1):
            for y in range(max(65, old_lo[1]), old_hi[1] + 1):
                for z in range(old_lo[2], old_hi[2] + 1):
                    if legacy_root is not None:
                        sx, sy, sz = legacy['size']
                        i = (x - old_lo[0]) * sy * sz + (y - old_lo[1]) * sz + z - old_lo[2]
                        old_palette = legacy_root['structure']['palette']['default']['block_palette']
                        old_indices = legacy_root['structure']['block_indices']
                        for layer in range(2):
                            actual = self.get((x, y, z), layer)
                            index = int(old_indices[layer][i]) if layer < len(old_indices) else -1
                            expected = old_palette[index] if index >= 0 else AIR
                            if (str(actual['name']) not in AIR_NAMES and
                                    (actual['name'] != expected['name'] or any(
                                        actual.get('states', {}).get(k) != v
                                        for k, v in expected.get('states', {}).items()))):
                                raise ValueError(
                                    f"unrelated or modified block in old {game['id']} hall: {(x, y, z)} "
                                    f"actual={actual.snbt()} expected={expected.snbt()}")
                    self.set((x, y, z), AIR)
                    self.set((x, y, z), AIR, 1)
        coverage = set()
        non_air = 0
        for tile in game['structures']:
            path = Path(tile['path'])
            if structures_root is not None:
                path = Path(structures_root) / path.name
            raw = path.read_bytes()
            if tile.get('sha256') and sha256(raw).hexdigest() != tile['sha256']:
                raise ValueError(f"structure digest mismatch: {game['id']} / {path}")
            root = n.File.parse(BytesIO(raw), byteorder='little')
            size = list(map(int, root['size']))
            if size != list(tile['size']):
                raise ValueError('structure/layout size mismatch')
            origin = tile['origin']
            palette = root['structure']['palette']['default']['block_palette']
            indices = root['structure']['block_indices']
            for x in range(size[0]):
                for y in range(size[1]):
                    for z in range(size[2]):
                        at = (origin[0] + x, origin[1] + y, origin[2] + z)
                        if not inside(at, new_box) or at in coverage:
                            raise ValueError(f'overlap/out-of-bounds structure cell: {at}')
                        coverage.add(at)
                        if not inside(at, old_box):
                            for layer in range(2):
                                current = self.get(at, layer)
                                if str(current['name']) not in AIR_NAMES:
                                    raise ValueError(f"existing building outside old {game['id']} hall: {at} {current['name']}")
                        i = x * size[1] * size[2] + y * size[2] + z
                        for layer in range(2):
                            palette_index = int(indices[layer][i]) if layer < len(indices) else -1
                            value = AIR if palette_index < 0 else palette[palette_index]
                            self.set(at, value, layer)
                            if layer == 0 and str(value['name']) not in AIR_NAMES:
                                non_air += 1
            for index, value in root['structure']['palette']['default'].get('block_position_data', {}).items():
                if 'block_entity_data' not in value:
                    continue
                index = int(index)
                local = [index // (size[1] * size[2]), index // size[2] % size[1], index % size[2]]
                at = tuple(a + b for a, b in zip(origin, local))
                entity = deepcopy(value['block_entity_data'])
                if str(entity.get('id')) not in SAFE_TILES - {'CommandBlock'}:
                    raise ValueError(f'unsafe source block entity: {at}')
                entity.update({k: n.Int(v) for k, v in zip(('x', 'y', 'z'), at)})
                self.tiles[at] = encode_record(n.File(entity))
        if len(coverage) != game['size'][0] * game['size'][1] * game['size'][2] or non_air < 10:
            raise ValueError(f"incomplete or empty original structure: {game['id']}")
        overlays = game.get('block_overlays', [])
        for overlay in overlays:
            location = overlay['location']
            raw_at = [location[k] for k in ('x', 'y', 'z')]
            at = tuple(int(v) for v in raw_at)
            if list(at) != raw_at or not inside(at, new_box):
                raise ValueError(f'gameplay overlay outside original hall: {raw_at}')
            name = overlay['block']
            if not name.startswith('minecraft:') or name in (
                    'minecraft:command_block', 'minecraft:chain_command_block',
                    'minecraft:repeating_command_block', 'minecraft:structure_block'):
                raise ValueError('unsupported gameplay overlay block')
            states = {k: n.Byte(v) if isinstance(v, bool) else n.Int(v)
                      if isinstance(v, int) else n.String(v)
                      for k, v in overlay.get('states', {}).items()}
            self.set(at, n.Compound({'name': n.String(name), 'states': n.Compound(states),
                                     'version': AIR['version']}))
            self.set(at, AIR, 1)
            # Overlay replaces the source block; its decorative tile cannot
            # remain attached to a different type such as a gameplay floor.
            self.tiles.pop(at, None)
        self.game_reports.append({'id': game['id'], 'origin': game['production_origin'],
                                  'size': game['size'], 'original_non_air_blocks': non_air,
                                  'structure_cells': len(coverage), 'old_bounds': old_box,
                                  'new_bounds': new_box, 'structures': len(game['structures']),
                                  'gameplay_block_overlays': len(overlays)})

    def values(self):
        values = {}
        for key, section in self.sections.items():
            raw = self.original_sections[key]
            sy = struct.unpack('b', key[-1:])[0]
            encoded = encode_section(section, sy)
            # Avoid materializing untouched all-air missing sections from reads.
            if raw is None and all(str(p['name']) in AIR_NAMES for layer in section['layers'] for p in layer['palette']):
                continue
            if raw != encoded:
                # Decoding/re-encoding an unchanged section could compact word
                # padding. Only write sections with an explicitly changed cell.
                if key in self.changed_sections:
                    values[key] = encoded
        all_boxes = [report['old_bounds'] for report in self.game_reports] + [report['new_bounds'] for report in self.game_reports]
        chunks = {(x // 16, z // 16) for x, _, z in self.changed_positions}
        chunks.update((x // 16, z // 16) for x, _, z in self.tiles)
        for cx, cz in chunks:
            p = prefix(cx, cz)
            old_raw = self.world.get(p + b'1') or b''
            output = []
            for at, root, raw in parse_records(old_raw):
                if any(inside(at, box) and at[1] >= 65 for box in all_boxes):
                    if str(root.get('id')) not in SAFE_TILES:
                        raise ValueError(f'unrelated/inventory block entity inside venue: {at} {root.get("id")}')
                else:
                    output.append(raw)
            output.extend(raw for at, raw in self.tiles.items() if at[0] // 16 == cx and at[2] // 16 == cz)
            new_raw = b''.join(output)
            if new_raw != old_raw:
                values[p + b'1'] = new_raw
        for (cx, cz), columns in self.columns.items():
            key = prefix(cx, cz) + b'+'
            raw = self.world.get(key)
            heights = list(struct.unpack('<256H', raw[:512]))
            for lx, lz in columns:
                highest = -65
                for sy in range(19, -5, -1):
                    section = self.section(cx, sy, cz)
                    primary = section['layers'][0]
                    for ly in range(15, -1, -1):
                        index = primary['indices'][lx * 256 + lz * 16 + ly]
                        if str(primary['palette'][index]['name']) not in AIR_NAMES:
                            highest = sy * 16 + ly
                            break
                    if highest != -65:
                        break
                heights[lz * 16 + lx] = highest + 65
            encoded = struct.pack('<256H', *heights) + raw[512:]
            if raw != encoded:
                values[key] = encoded
        return values


def apply_native(world, layouts, legacy, rollback_path, structures_root=None, legacy_pack=None):
    games = layouts['games']
    if [item['id'] for item in games] != EXPECTED_IDS:
        raise ValueError('all 17 source games in the recorded catalog order are required')
    before = database_hashes(world)
    placement = NativePlacement(world)
    for index, game in enumerate(games):
        if game['production_origin'] != [20000 + 96 * index, 65, 20000]:
            raise ValueError('production origin differs from original plan')
        placement.stage_game(game, legacy[game['id']], structures_root, legacy_pack)
    return commit_placement(world, placement, rollback_path, before)


def commit_placement(world, placement, rollback_path, before=None):
    """Commit a fully checked bounded placement and retain its exact rollback."""
    if before is None:
        before = database_hashes(world)
    values = placement.values()
    rollback_path = Path(rollback_path)
    rollback_path.parent.mkdir(parents=True, exist_ok=True)
    rollback = {'schema': 1, 'records': [
        {'key': key.hex(), 'base64': base64.b64encode(world.get(key)).decode('ascii')
         if world.get(key) is not None else None} for key in values]}
    with rollback_path.open('x', encoding='utf-8') as stream:
        json.dump(rollback, stream)
    world.db.putBatch(values)
    after = database_hashes(world)
    changed = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
    if changed != set(values):
        raise ValueError('unexpected database mutation outside the native placement transaction')
    world.section.cache_clear()
    for at in sorted(placement.changed_positions,
                     key=lambda at: (at[0] // 16, at[2] // 16, at[1] // 16, at)):
        for layer in range(2):
            expected = placement.get(at, layer)
            actual_layers = world.blocks(*at)
            # Empty native storages are intentionally not materialized. Their
            # implicit air has no palette version; a source archive's older
            # air version cannot survive that encoding and is not a block loss.
            if (layer >= len(actual_layers) and str(expected['name']) in AIR_NAMES
                    and not expected.get('states')):
                continue
            actual = actual_layers[layer] if layer < len(actual_layers) else AIR
            if dict(expected) != dict(actual):
                raise ValueError(f'persisted block differs from structure: {at}/{layer}')
    return {'verified': True, 'games': placement.game_reports,
            'changed_blocks': len(placement.changed_positions),
            'changed_leveldb_keys': [key.hex() for key in sorted(changed)],
            'unchanged_leveldb_keys': len(set(before) - changed),
            'unrelated_leveldb_keys_unchanged': True,
            'chunk_versions_and_biomes_preserved': True,
            'actors_and_players_preserved': True, 'gameplay_verified': False,
            'rollback_file': str(rollback_path)}


def apply_layouts(working, layout, rollbackpath, *, full_backup, world_is_stopped,
                  legacy_pack, structures_root=None):
    """Entry for the managed release transaction; no live-server control here."""
    if world_is_stopped is not True or not Path(full_backup).exists():
        raise ValueError('stopped world and completed full-world backup required')
    if isinstance(layout, (str, Path)):
        layout = json.loads(Path(layout).read_text(encoding='utf-8'))
    legacy = load_legacy(Path(legacy_pack) / 'scripts/junket_venue_generated.js')
    world = ConvertedWorld(working)
    try:
        return apply_native(world, layout, legacy, rollbackpath, structures_root, legacy_pack)
    finally:
        world.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', type=Path, required=True)
    parser.add_argument('--layouts', type=Path, required=True)
    parser.add_argument('--legacy-config', type=Path, required=True)
    parser.add_argument('--structures-root', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--rollback', type=Path, required=True)
    parser.add_argument('--full-backup', type=Path, required=True)
    parser.add_argument('--world-is-stopped', action='store_true', required=True)
    args = parser.parse_args()
    if not args.full_backup.exists():
        parser.error('a completed full-world backup must exist')
    world = ConvertedWorld(args.world)
    try:
        report = apply_native(world, json.loads(args.layouts.read_text(encoding='utf-8')),
                              load_legacy(args.legacy_config), args.rollback, args.structures_root)
    finally:
        world.db.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'verified': True, 'games': len(report['games']), 'report': str(args.report)}))


if __name__ == '__main__':
    main()
