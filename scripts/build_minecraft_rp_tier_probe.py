"""Build a tiny, isolated V3 RP-tier probe; never modify Minecraft production.

The selected cobblestone label/texture identifies the selected overlay. It is an
RP tier observation ONLY on a fresh import/world with no manual subpack choice,
after this V3 selection mechanism is verified on that exact Bedrock build.
There is no Script API, device lookup, Big media, credential or network request.
"""
import argparse
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED, ZipInfo

from PIL import Image, ImageDraw

UUID = '301f1595-9d5b-569a-991a-c6b5f5d6a842'
MODULE_UUID = 'a2c560bb-2e88-5ea4-829f-e934e5f4e87e'
VERSION = '1.0.0'


def probe_files():
    manifest = {
        'format_version': 3,
        'header': {'name': 'Ichiyon RP tier probe (isolated test)',
                   'description': 'Cobblestone shows selected RP subpack; no video assets',
                   'uuid': UUID, 'version': VERSION, 'min_engine_version': '1.26.52'},
        'modules': [{'type': 'resources', 'uuid': MODULE_UUID, 'version': VERSION}],
        'metadata': {'authors': ['Ichiyon']},
        'subpacks': [{'folder_name': f'tier_{tier}', 'name': f'RP tier {tier}',
                      'memory_performance_tier': tier} for tier in range(1, 6)],
    }
    files = {'manifest.json': (json.dumps(manifest, indent=2) + '\n').encode()}
    # 0 is a visible failure sentinel: the base must never masquerade as tier 1.
    for tier in range(6):
        prefix = '' if tier == 0 else f'subpacks/tier_{tier}/'
        label = f'RP TIER {tier}' if tier else 'RP PROBE BASE - SELECTION UNVERIFIED'
        for locale in ('en_US', 'ja_JP'):
            files[f'{prefix}texts/{locale}.lang'] = f'tile.cobblestone.name={label}\n'.encode()
        files[f'{prefix}texts/languages.json'] = b'["en_US", "ja_JP"]\n'
        colors = ['#dd00dd', '#304080', '#307040', '#806020', '#804020', '#803060']
        picture = Image.new('RGB', (16, 16), colors[tier])
        draw = ImageDraw.Draw(picture)
        draw.text((5, 2), str(tier), fill='white')
        png = BytesIO(); picture.save(png, format='PNG')
        files[f'{prefix}textures/blocks/cobblestone.png'] = png.getvalue()
    return files


def build_archive(destination):
    destination = Path(destination)
    # Never silently overwrite a pack the operator may already be testing.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as stream, ZipFile(stream, 'w') as archive:
        for name, content in sorted(probe_files().items()):
            info = ZipInfo(name, date_time=(2026, 10, 2, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, content)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    path = build_archive(args.output)
    print(json.dumps({'path': str(path.resolve()), 'bytes': path.stat().st_size,
                      'uuid': UUID, 'version': VERSION, 'production_modified': False,
                      'actual_client_selection_verified': False}))


if __name__ == '__main__':
    main()
