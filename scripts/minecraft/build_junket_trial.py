"""Build a small local trial world and standalone BP; never install/deploy them.

Requires nbtlib for Bedrock's little-endian level.dat. All world metadata is new;
no existing world, Java archive, DB-managed assets or player data is read.
"""
import argparse
from io import BytesIO
import json
from pathlib import Path
import struct
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / 'minecraft/behavior_packs/ichiyon_junket_bp'
RESOURCE_PACK = ROOT / 'minecraft/resource_packs/ichiyon_junket_rp'
ZIP_TIMESTAMP = (2026, 10, 18, 0, 0, 0)


def write_entry(archive, name, data):
    entry = ZipInfo(name, date_time=ZIP_TIMESTAMP)
    entry.compress_type = ZIP_DEFLATED
    entry.external_attr = 0o100644 << 16
    archive.writestr(entry, data)


def level_data():
    import nbtlib as n
    layers = {'biome_id': 1, 'block_layers': [
        {'block_name': 'minecraft:bedrock', 'count': 1},
        {'block_name': 'minecraft:dirt', 'count': 2},
        {'block_name': 'minecraft:grass_block', 'count': 1}],
        'encoding_version': 6, 'preset_id': 'ClassicFlat', 'world_version': 'version.post_1_18'}
    metadata = n.File({
        'LevelName': n.String('JUNKET Trial - All 17 Games'),
        'StorageVersion': n.Int(10), 'Generator': n.Int(2),
        'GameType': n.Int(1), 'ForceGameType': n.Byte(0), 'Difficulty': n.Int(0),
        'RandomSeed': n.Long(141), 'SpawnX': n.Int(0), 'SpawnY': n.Int(-60), 'SpawnZ': n.Int(0),
        'FlatWorldLayers': n.String(json.dumps(layers)), 'baseGameVersion': n.String('*'),
        'MinimumCompatibleClientVersion': n.List[n.Int]([1, 26, 44, 0, 0]),
        'lastOpenedWithVersion': n.List[n.Int]([1, 26, 52, 3, 0]),
        'commandsEnabled': n.Byte(1), 'hasBeenLoadedInCreative': n.Byte(1),
        'MultiplayerGame': n.Byte(1), 'MultiplayerGameIntent': n.Byte(1),
        'LANBroadcast': n.Byte(1), 'LANBroadcastIntent': n.Byte(1),
        'playerPermissionsLevel': n.Int(1), 'permissionsLevel': n.Int(0),
        'experiments': n.Compound({'experiments_ever_used': n.Byte(0), 'saved_with_toggled_experiments': n.Byte(0)}),
        'dodaylightcycle': n.Byte(0), 'domobspawning': n.Byte(0), 'Time': n.Long(6000),
    })
    output = BytesIO()
    metadata.write(output, byteorder='little')
    data = output.getvalue()
    return struct.pack('<II', 10, len(data)) + data


def build(output):
    manifest = json.loads((PACK / 'manifest.json').read_text(encoding='utf-8'))
    resource_manifest = json.loads((RESOURCE_PACK / 'manifest.json').read_text(encoding='utf-8'))
    version = '.'.join(map(str, manifest['header']['version']))
    files = {p.relative_to(PACK).as_posix(): p.read_bytes() for p in PACK.rglob('*') if p.is_file()}
    resources = {
        p.relative_to(RESOURCE_PACK).as_posix(): p.read_bytes()
        for p in RESOURCE_PACK.rglob('*') if p.is_file()
    }
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f'ichiyon_junket_bp-{version}.mcpack'
    with ZipFile(archive_path, 'w', ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            write_entry(archive, name, data)
    world_path = output / f'junket-trial-{version}.mcworld'
    with ZipFile(world_path, 'w', ZIP_DEFLATED) as archive:
        write_entry(archive, 'level.dat', level_data())
        write_entry(archive, 'levelname.txt', 'JUNKET Trial - All 17 Games'.encode())
        write_entry(archive, 'world_behavior_packs.json', json.dumps([
            {'pack_id': manifest['header']['uuid'], 'version': manifest['header']['version']}]).encode())
        write_entry(archive, 'world_resource_packs.json', json.dumps([
            {'pack_id': resource_manifest['header']['uuid'],
             'version': resource_manifest['header']['version']}]).encode())
        for name, data in sorted(files.items()):
            write_entry(archive, 'behavior_packs/ichiyon_junket_bp/' + name, data)
        for name, data in sorted(resources.items()):
            write_entry(archive, 'resource_packs/ichiyon_junket_rp/' + name, data)
    for path in (archive_path, world_path):
        with ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError('invalid archive')
        print(f'{path} ({path.stat().st_size} bytes)')
    return archive_path, world_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/junket-private/trial')
    args = parser.parse_args()
    build(args.output)
