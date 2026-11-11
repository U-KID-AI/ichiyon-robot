"""Extract the original Karasu Bank into PRIVATE, reusable Bedrock source assets.

This never edits a live world or chooses a production placement.  Chunker is
used only for source chunks missing from the existing conversion.  Original
block furniture is preserved; Java commands/interaction hitboxes are not run.
"""
from __future__ import annotations
from junket_private_profile import private_value

import argparse
from collections import Counter
from contextlib import contextmanager
from io import BytesIO
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib

import nbtlib as n
import nbtlib.tag

from build_junket_all17_source import CompositeWorld, plain
from build_junket_venue import write_json
from junket_java import JavaWorld
from junket_structure import ConvertedWorld


ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / 'data/junket-private'
OLD_PRIVATE = ROOT / 'data/junket-private'
# Ground + complete roof, western canopy, trees, side columns and eastern vault.
# The neighboring hotel begins at Z=-42; the city boundary fence is Z=-38.
SOURCE_FUNCTION = 'datapacks/test/data/lobby/function/building/all.mcfunction'
BLOCK_ENTITIES = {'Sign', 'HangingSign', 'Banner', 'Skull', 'FlowerPot'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def decode_java_string(raw):
    """NBT uses Java modified UTF-8 (supplementary glyphs are surrogate pairs).

    nbtlib's ordinary UTF-8 replacement decoder turns the bank's bird glyph into
    six replacement characters.  Decode it without changing the source bytes.
    """
    value = raw.replace(b'\xc0\x80', b'\0').decode('utf-8', 'surrogatepass')
    return value.encode('utf-16-le', 'surrogatepass').decode('utf-16-le')


@contextmanager
def java_strings():
    original = nbtlib.tag.read_string
    def read(fileobj, byteorder='big'):
        length = nbtlib.tag.read_numeric(nbtlib.tag.USHORT, fileobj, byteorder)
        return decode_java_string(fileobj.read(length))
    nbtlib.tag.read_string = read
    try:
        yield
    finally:
        nbtlib.tag.read_string = original


def inside(pos, bounds=None):
    bounds = private_value("bank.bounds") if bounds is None else bounds
    return len(pos) == 3 and all(a <= v < b + 1 for a, v, b in zip(bounds[0], pos, bounds[1]))


def needed_chunks(bounds=None):
    bounds = private_value("bank.bounds") if bounds is None else bounds
    lo, hi = bounds
    return {(x, z) for x in range(lo[0]//16, hi[0]//16 + 1)
            for z in range(lo[2]//16, hi[2]//16 + 1)}


def read_entities(world, bounds=None):
    bounds = private_value("bank.bounds") if bounds is None else bounds
    rows = []
    regions = {(x//32, z//32) for x, z in needed_chunks(bounds)}
    with java_strings():
        for rx, rz in sorted(regions):
            member = f'{world.root}entities/r.{rx}.{rz}.mca'
            raw = world.archive.read(member)
            for idx in range(1024):
                offset = int.from_bytes(raw[idx*4:idx*4+3], 'big') * 4096
                if not offset:
                    continue
                length = int.from_bytes(raw[offset:offset+4], 'big')
                if raw[offset+4] != 2:
                    raise ValueError('Unsupported Java entity chunk compression')
                chunk = n.File.parse(BytesIO(zlib.decompress(raw[offset+5:offset+4+length])))
                for entity in chunk.get('Entities', []):
                    pos = list(map(float, entity.get('Pos', [])))
                    if not inside(pos, bounds):
                        continue
                    # Retain authoritative presentation fields, not Java AI,
                    # trades, DeathTime=19, UUIDs, inventories or commands.
                    keep = ('id', 'Pos', 'Rotation', 'Tags', 'text', 'transformation',
                            'billboard', 'background', 'text_opacity', 'shadow',
                            'alignment', 'line_width', 'see_through', 'item',
                            'item_display', 'block_state', 'VillagerData')
                    data = {k: plain(entity[k]) for k in keep if k in entity}
                    data['source_region'] = member.removeprefix(world.root)
                    data['local_position'] = [v-a for v, a in zip(pos, bounds[0])]
                    rows.append(data)
    rows.sort(key=lambda v: (v['id'], v['Pos'], v.get('text', '')))
    for i, row in enumerate(rows):
        row['role'] = f'bank_source_{i}'
        if row['id'] == 'minecraft:interaction':
            row['migration'] = 'metadata_only_no_collision_or_execution'
        elif row['id'] == 'minecraft:villager':
            row['migration'] = 'protected_guide_at_source_position_no_original_trades'
        elif row['id'] == 'minecraft:text_display':
            row['migration'] = 'source_text_visual_required'
        else:
            row['migration'] = 'requires_model_conversion'
    return rows


def ensure_conversion(args, world):
    old = ConvertedWorld(args.converted)
    try:
        existing = {struct.unpack('<ii', key[:8]) for key in old.db.keys()
                    if len(key) == 10 and key[8:9] == b'/'}
    finally:
        old.db.close()
    required = needed_chunks()
    missing = sorted(required - existing)
    supplement = args.output / 'bedrock-supplement'
    provenance = {'reused_chunks': sorted(required & existing), 'converted_chunks': missing,
                  'source_sha256': sha(args.source), 'format': 'BEDROCK_1_21_130'}
    if not missing:
        return ConvertedWorld(args.converted), provenance
    if not (supplement / 'db/CURRENT').exists():
        java = args.output / 'java-supplement'
        (java / 'region').mkdir(parents=True, exist_ok=True)
        (java / 'level.dat').write_bytes(world.archive.read(world.root + 'level.dat'))
        provenance['source_regions'] = {}
        for rx, rz in sorted({(x//32, z//32) for x, z in missing}):
            member = f'region/r.{rx}.{rz}.mca'
            content = world.archive.read(world.root + member)
            (java / member).write_bytes(content)
            provenance['source_regions'][member] = hashlib.sha256(content).hexdigest()
        pruning = {'configs': {'minecraft:overworld': {'include': True, 'regions': [
            {'minChunkX': x, 'maxChunkX': x, 'minChunkZ': z, 'maxChunkZ': z}
            for x, z in missing]}, 'minecraft:the_nether': {'include': False},
            'minecraft:the_end': {'include': False}}}
        write_json(args.output / 'pruning.json', pruning)
        command = [str(args.chunker), '-i', str(java), '-o', str(supplement),
                   '-f', provenance['format'], '-p', str(args.output / 'pruning.json')]
        # Leave failure logs/output intact. No live world path is accepted here.
        with (args.output / 'chunker.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'Chunker failed ({result.returncode}); see private chunker.log')
    write_json(args.output / 'conversion-provenance.json', provenance)
    return CompositeWorld(args.converted, supplement), provenance


def inspect_structure(path, converted, bounds=None):
    bounds = private_value("bank.bounds") if bounds is None else bounds
    """Read the actual saved structure and compare every cell to the conversion."""
    saved = n.load(path, byteorder='little')
    size = list(map(int, saved['size']))
    expected_size = [b-a+1 for a, b in zip(*bounds)]
    assert size == expected_size
    data = saved['structure']; palette = data['palette']['default']['block_palette']
    excluded = {'minecraft:barrier', 'minecraft:command_block', 'minecraft:chain_command_block',
                'minecraft:repeating_command_block', 'minecraft:structure_block', 'minecraft:jigsaw'}
    counts = Counter(); compared = 0
    for ix in range(size[0]):
        for iy in range(size[1]):
            for iz in range(size[2]):
                index = ix*size[1]*size[2] + iy*size[2] + iz
                source = converted.blocks(bounds[0][0]+ix, bounds[0][1]+iy, bounds[0][2]+iz)
                for layer in range(2):
                    block_index = int(data['block_indices'][layer][index])
                    block = None if block_index < 0 else palette[block_index]
                    original = source[layer] if layer < len(source) else None
                    if original is None or str(original['name']) in excluded:
                        assert block is None or str(block['name']) == 'minecraft:air'
                    else:
                        assert block is not None and block.snbt() == n.Compound(original).snbt()
                    if layer == 0:
                        counts[str(block['name']) if block else 'minecraft:air'] += 1
                compared += 1
    assert not data['entities'], 'Java entities must be instantiated via scoped runtime'
    assert not (excluded & counts.keys())
    for entry in data['palette']['default']['block_position_data'].values():
        assert str(entry['block_entity_data']['id']) in BLOCK_ENTITIES
    return {'verified_cells': compared, 'block_counts': dict(counts),
            'non_air_blocks': sum(v for k, v in counts.items() if k != 'minecraft:air'),
            'entities_in_structure': 0, 'barriers': counts['minecraft:barrier']}


def build(args):
    args.output = args.output.resolve()
    if not args.output.is_relative_to(PRIVATE.resolve()):
        raise ValueError('Original assets must stay in ignored data/junket-private')
    manifest_path = args.output / 'bank-source.json'
    if manifest_path.exists():
        raise FileExistsError('Completed output exists; choose a new candidate directory')
    args.output.mkdir(parents=True, exist_ok=True)
    world = JavaWorld(args.source)
    entities = read_entities(world)
    # The source bank contains no item/block display furniture. Its desks,
    # chairs, lamps and vault are block furniture and are exported directly.
    model_entities = [e for e in entities if e['id'] in ('minecraft:item_display', 'minecraft:block_display')]
    if model_entities:
        raise ValueError('Source changed: resolve new model displays before declaring faithful extraction')
    converted, provenance = ensure_conversion(args, world)
    target = args.output / 'assets/ichiyon_junket_original_bp/structures/junket/karasu_bank.mcstructure'
    try:
        for x, z in needed_chunks():
            assert converted.section(x, 3, z), f'Missing bank ground chunk: {(x, z)}'
        export = converted.export(private_value("bank.bounds"), target)
        verified = inspect_structure(target, converted)
        arrivals = []
        for label, point in [('entrance', private_value("bank.entrance")), ('arrival', private_value("bank.arrival"))]:
            blocks = [converted.blocks(int(point[0]), y, int(point[2]//1))[0]
                      for y in private_value("bank.arrival_probe_heights")]
            names = [str(b['name']) for b in blocks]
            assert names[0] != 'minecraft:air' and names[1:] == ['minecraft:air']*2, (label, names)
            arrivals.append({'role': label, 'source': point, 'local': [v-a for v, a in zip(point, private_value("bank.bounds")[0])],
                             'floor_feet_head': names})
    finally:
        if hasattr(converted, 'close'):
            converted.close()
        else:
            converted.db.close()
    function = world.archive.read(world.root + SOURCE_FUNCTION)
    write_json(args.output / 'bank-entities.json', entities)
    manifest = {'schema': 1, 'source_archive_sha256': sha(args.source),
        'source_bounds': private_value("bank.bounds"), 'size': export['size'], 'production_position': None,
        'structure_id': 'junket:karasu_bank', 'structure_path': str(target.relative_to(args.output)),
        'structure_sha256': sha(target), 'export': export, 'verification': verified,
        'original_reception': private_value("bank.reception"), 'access': arrivals,
        'entity_counts': dict(Counter(e['id'] for e in entities)), 'entities': entities,
        'original_item_display_models': 0,
        'furniture_source': 'Original Java blocks via existing/new scoped Chunker conversion; no substitute models.',
        'source_function': SOURCE_FUNCTION, 'source_function_sha256': hashlib.sha256(function).hexdigest(),
        'conversion': provenance,
        'bound_evidence': private_value("bank.bound_evidence"),
        'limitations': ['Original Java entity AI/trades/commands are not executable in Bedrock.',
            'Four village guides and ten text displays require protected scoped runtime initialization.',
            'Java interaction entity is metadata only; no invisible collision entity is copied.',
            'No PC/mobile client rendering has been verified by this extraction.']}
    write_json(manifest_path, manifest)
    world.archive.close()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--converted', type=Path, default=OLD_PRIVATE/'conversion/bedrock-four-games')
    parser.add_argument('--chunker', type=Path, default=OLD_PRIVATE/'tools/chunker/chunker-cli/chunker-cli.exe')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = build(args)
    print(json.dumps({'output': str(args.output), 'size': report['size'],
                      'verification': report['verification'], 'entities': report['entity_counts']}))


if __name__ == '__main__':
    main()
