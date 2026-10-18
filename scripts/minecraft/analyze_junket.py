"""Read a local JUNKET ZIP without extracting or executing any source content.

Only derived inventory/coordinates/hashes are written. Optional NBT inspection
uses nbtlib, installed in the local development environment (pip install nbtlib).
"""
import argparse
from collections import Counter
import gzip
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from zipfile import ZipFile
import zlib

SCHEMA = 1


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inspect_world(archive, root, points):
    import nbtlib
    level = nbtlib.File.parse(BytesIO(gzip.decompress(archive.read(root + 'level.dat'))))
    result = {'version': str(level['Data']['Version']['Name']), 'chunks': []}
    for label, point in points.items():
        x, _, z = point
        cx, cz = int(x) // 16, int(z) // 16
        name = f'{root}region/r.{cx // 32}.{cz // 32}.mca'
        region = archive.read(name)
        index = 4 * (cx % 32 + 32 * (cz % 32))
        offset = int.from_bytes(region[index:index + 3], 'big') * 4096
        if not offset:
            raise ValueError(f'missing chunk at {label}')
        length = int.from_bytes(region[offset:offset + 4], 'big')
        if region[offset + 4] != 2:
            raise ValueError('unsupported region compression')
        chunk = nbtlib.File.parse(BytesIO(zlib.decompress(region[offset + 5:offset + 4 + length])))
        palette = sorted({str(p['Name']) for section in chunk['sections']
                          if 3 <= int(section['Y']) <= 7
                          for p in section.get('block_states', {}).get('palette', [])})
        result['chunks'].append({'anchor': label, 'chunk': [cx, cz],
                                 'data_version': int(chunk['DataVersion']),
                                 'palette_y48_127': palette})
    return result


def analyze(path, *, world_details=False):
    before = digest(path)
    with ZipFile(path, 'r') as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('duplicate ZIP entries')
        roots = [n[:-len('level.dat')] for n in names if n.endswith('/level.dat')]
        if len(roots) != 1:
            raise ValueError('expected exactly one Java world')
        root = roots[0]
        functions = {n: archive.read(n).decode('utf-8-sig') for n in names if n.endswith('.mcfunction')}
        def source(tail):
            return functions[root + 'datapacks/test/data/' + tail]
        tp = source('lobby/function/system/tp.mcfunction')
        matches = re.findall(r'matches (1|101) run tp @s (-?\d+) (-?\d+) (-?\d+)', tp)
        if len(matches) != 2 or matches[0][1:] != matches[1][1:]:
            raise ValueError('sparrow reception anchors changed; inspect source')
        arrival = [int(v) for v in matches[0][1:]]
        building = source('lobby/function/building/all.mcfunction')
        hotel_signs = [[float(v) for v in m] for m in re.findall(
            r'summon text_display ([-\d.]+) ([-\d.]+) ([-\d.]+) [^\r\n]*CROW HOTEL', building)]
        receptions = {}
        for role in ('single', 'multi', 'mix'):
            match = re.search(r'summon villager ([-\d.]+) ([-\d.]+) ([-\d.]+) [^\r\n]*"lobby-' + role + '"', building)
            if match:
                receptions[role] = [float(v) for v in match.groups()]
        start = source('game/function/sparrow/start.mcfunction')
        seats = [[int(v) for v in m] for m in re.findall(r'tp @p\[tag=sparrow-player-[12]\] (-?\d+) (-?\d+) (-?\d+)', start)]
        selected = {n: text for n, text in functions.items() if '/function/sparrow/' in n}
        report = {
            'schema': SCHEMA, 'source': {'filename': path.name, 'bytes': path.stat().st_size, 'sha256': before},
            'entries': len(names), 'mcfunctions': len(functions),
            'datapacks': sorted({n[len(root + 'datapacks/'):].split('/')[0] for n in names if n.startswith(root + 'datapacks/')} - {''}),
            'resource_archives': [n for n in names if n.endswith('.zip')],
            'venues': {'crow_hotel': {'signs': hotel_signs, 'receptions': receptions, 'bounds': 'unverified'},
                       'sparrow': {'arrival': arrival, 'seats': seats, 'bounds': 'unverified'}},
            'sparrow': {'functions': len(selected), 'namespaces': dict(Counter(n.split('/data/')[1].split('/')[0] for n in selected)),
                        'source_files': {n[len(root):]: hashlib.sha256(archive.read(n)).hexdigest() for n in sorted(selected)},
                        'function_dependencies': sorted({target for text in selected.values() for target in re.findall(r'\bfunction ([\w:./-]+)', text)})},
        }
        if world_details:
            report['world'] = inspect_world(archive, root, {'hotel': receptions['multi'], 'sparrow': seats[0]})
    if digest(path) != before:
        raise RuntimeError('source changed while analyzing')
    report['source_unchanged'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--world-details', action='store_true')
    args = parser.parse_args()
    candidates = [args.zip] if args.zip else list((Path.home() / 'Downloads').glob('*JUNKET*1.09*.zip'))
    if len(candidates) != 1 or not candidates[0].is_file():
        parser.error('specify exactly one existing local source with --zip')
    if args.output.resolve() == candidates[0].resolve():
        parser.error('output must not be the source ZIP')
    report = analyze(candidates[0], world_details=args.world_details)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Read-only analysis: {report['mcfunctions']} functions; source SHA256 {report['source']['sha256']}")
    print(f'Report: {args.output}')


if __name__ == '__main__':
    main()
