"""Build LOCAL ONLY assets from the user's ZIP and a Chunker conversion.

No source artwork is embedded in this tool. Outputs belong in ignored private
storage and may not be published without permission from the respective owners.
Dependencies: nbtlib, amulet-leveldb, Pillow. Chunker is run separately, locally.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from PIL import Image
from junket_structure import ConvertedWorld
from build_junket_temperance import build_temperance
from build_junket_lucy import build_lucy
from build_junket_silence import build_silence
from build_junket_jinn import build_jinn

BP_UUID = 'a21e0c91-5936-482f-90f0-94b7967c4cf0'
RP_UUID = 'f5c06268-684c-4b1a-bf3b-37d90921ec75'
VERSION = [0, 6, 0]
BOUNDS = {'sparrow_hall': [[210, 54, 198], [236, 66, 226]],
          'temperance_hall': [[80, 99, 205], [108, 131, 266]],
          'lucy_hall': [[194, 55, 209], [208, 65, 213]],
          'silence_hall': [[79, 55, 281], [109, 67, 313]],
          'jinn_hall': [[80, 120, 230], [107, 157, 266]],
          'crow_reception': [[360, 55, -59], [373, 64, -50]]}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def geometry(model, identifier, atlas, texture_size=(256, 128)):
    bones = []
    for i, element in enumerate(model['elements']):
        a, b = element['from'], element['to']
        cube = {'origin': [8 - b[0], a[1] - 8, a[2] - 8],
                'size': [b[j] - a[j] for j in range(3)], 'uv': {}}
        for face, data in element['faces'].items():
            texture = data['texture']
            while texture.startswith('#'):
                texture = model['textures'][texture[1:]]
            x, y, width, height = atlas[texture]
            u, v, u2, v2 = data['uv']
            uv = {'uv': [x + u * width / 16, y + v * height / 16],
                  'uv_size': [(u2 - u) * width / 16, (v2 - v) * height / 16]}
            if data.get('rotation'):
                uv['uv_rotation'] = data['rotation']
            cube['uv'][{'east': 'west', 'west': 'east'}.get(face, face)] = uv
        bone = {'name': f'element_{i}', 'cubes': [cube]}
        rotation = element.get('rotation')
        if rotation and rotation['angle']:
            assert not rotation.get('rescale'), 'Rescaled rotations require manual review'
            origin = rotation['origin']
            bone['pivot'] = [8 - origin[0], origin[1] - 8, origin[2] - 8]
            bone['rotation'] = [0, 0, 0]
            axis = 'xyz'.index(rotation['axis'])
            bone['rotation'][axis] = rotation['angle'] * (-1 if axis < 2 else 1)
        bones.append(bone)
    return {'description': {'identifier': identifier, 'texture_width': texture_size[0], 'texture_height': texture_size[1],
                            'visible_bounds_width': 8, 'visible_bounds_height': 8, 'visible_bounds_offset': [0, 0, 0]},
            'bones': bones}


def build(source, converted, vanilla, output):
    output = output.resolve()
    private = Path('data/junket-private').resolve()
    if not output.is_relative_to(private):
        raise ValueError('Outputs must remain in data/junket-private')
    bp, rp = output / 'ichiyon_junket_assets_bp', output / 'ichiyon_junket_assets_rp'
    with ZipFile(source) as archive, ZipFile(vanilla) as base:
        resource_name = next(n for n in archive.namelist() if n.endswith('/resources.zip'))
        with ZipFile(BytesIO(archive.read(resource_name))) as resources:
            prefix = 'assets/minecraft/models/item/game/sparrow/'
            models = {Path(n).stem: json.loads(resources.read(n)) for n in resources.namelist()
                      if n.startswith(prefix) and n.endswith('.json')}
            # Original prepare/set functions use a thin oak-plank display as a screen.
            models['partition'] = {'textures': {'0': 'block/oak_planks'}, 'elements': [{
                'from': [7.6, -16, -14.4], 'to': [8.4, 32, 30.4],
                'faces': {face: {'texture': '#0', 'uv': [0, 0, 16, 16]} for face in ['north', 'south', 'east', 'west', 'up', 'down']}}]}
            textures = sorted({v for model in models.values() for v in model['textures'].values() if not v.startswith('#')})
            sheet, atlas, x, y, row = Image.new('RGBA', (256, 128)), {}, 0, 0, 0
            for texture in textures:
                name = 'assets/minecraft/textures/' + texture + '.png'
                image = Image.open(BytesIO((resources if name in resources.namelist() else base).read(name))).convert('RGBA')
                if x + image.width > 256:
                    x, y, row = 0, y + row, 0
                assert y + image.height <= 128
                sheet.paste(image, (x, y)); atlas[texture] = (x, y, image.width, image.height)
                x += image.width; row = max(row, image.height)
            (rp / 'textures/entity').mkdir(parents=True, exist_ok=True)
            sheet.save(rp / 'textures/entity/junket_sparrow.png')
            geometries = [geometry(model, 'geometry.junket.' + key.replace('-', '_'), atlas) for key, model in models.items()]
            # Match the source item_display's static, nonuniform monitor scale.
            for geo in geometries:
                if geo['description']['identifier'] in ('geometry.junket.tv', 'geometry.junket.tv_result'):
                    for bone in geo['bones']:
                        assert 'rotation' not in bone
                        for cube in bone['cubes']:
                            for key in ('origin', 'size'):
                                cube[key] = [v * scale for v, scale in zip(cube[key], (2.4, 2, 1))]
            write_json(rp / 'models/entity/sparrow.geo.json', {'format_version': '1.21.0', 'minecraft:geometry': geometries})
    for path, uuid, module, kind, name in [
        (bp, BP_UUID, '22eca126-f65f-4216-a797-404196b29222', 'data', 'Ichiyon JUNKET Local Venue'),
        (rp, RP_UUID, '4b34e45a-d702-498e-b8cc-b30487455a70', 'resources', 'Ichiyon JUNKET Local Models')]:
        manifest = {'format_version': 2, 'header': {'name': name, 'description': 'Private local conversion of JUNKET CRAFT 1.09. Do not redistribute.',
                    'uuid': uuid, 'version': VERSION, 'min_engine_version': [1, 26, 44]},
                    'modules': [{'type': kind, 'uuid': module, 'version': VERSION}]}
        if kind == 'data':
            manifest['dependencies'] = [{'uuid': RP_UUID, 'version': VERSION}]
        write_json(path / 'manifest.json', manifest)
    for name, scale, width in [('bigbox', .5, .6), ('littlebox', .25, .4), ('diamond', .5, .3), ('tv', 1, .2), ('partition', 1, .05)]:
        frames = ([name + '-closed'] + [f'{name}-open-{i}-{outcome}' for outcome in ['out', 'hit'] for i in range(1, 4)]) if name.endswith('box') else ([name, 'tv-result'] if name == 'tv' else [name])
        props = {'junket:frame': {'type': 'int', 'range': [0, len(frames) - 1], 'default': 0, 'client_sync': True}} if len(frames) > 1 else {}
        components = {'minecraft:physics': {'has_gravity': False, 'has_collision': False},
                      'minecraft:collision_box': {'width': width / scale, 'height': .6 / scale},
                      'minecraft:pushable': {'is_pushable': False, 'is_pushable_by_piston': False},
                      'minecraft:damage_sensor': {'triggers': [{'cause': 'all', 'deals_damage': 'no'}]},
                      'minecraft:persistent': {}, 'minecraft:scale': {'value': scale},
                      'minecraft:nameable': {'always_show': True, 'allow_name_tag_renaming': False},
                      'minecraft:interact': {'interactions': [{'on_interact': {'event': 'junket:touch', 'target': 'self'}, 'interact_text': 'action.interact.junket'}]}}
        write_json(bp / f'entities/{name}.json', {'format_version': '1.21.0', 'minecraft:entity': {
            'description': {'identifier': 'junket:' + name, 'is_spawnable': False, 'is_summonable': True, 'properties': props},
            'components': components, 'events': {'junket:touch': {}}}})
        write_json(rp / f'entity/{name}.entity.json', {'format_version': '1.10.0', 'minecraft:client_entity': {'description': {
            'identifier': 'junket:' + name, 'materials': {'default': 'entity_alphatest'},
            'textures': {'default': 'textures/entity/junket_sparrow'},
            'geometry': {f'f{i}': 'geometry.junket.' + frame.replace('-', '_') for i, frame in enumerate(frames)},
            'render_controllers': ['controller.render.junket.' + name]}}})
        write_json(rp / f'render_controllers/{name}.json', {'format_version': '1.8.0', 'render_controllers': {'controller.render.junket.' + name: {
            'arrays': {'geometries': {'Array.frames': [f'Geometry.f{i}' for i in range(len(frames))]}},
            'geometry': "Array.frames[query.property('junket:frame')]" if props else 'Geometry.f0',
            'materials': [{'*': 'Material.default'}], 'textures': ['Texture.default']}}})
    (rp / 'texts').mkdir(exist_ok=True)
    (rp / 'texts/ja_JP.lang').write_text('action.interact.junket=選択・操作\n', encoding='utf-8')
    (rp / 'texts/en_US.lang').write_text('action.interact.junket=Select / interact\n', encoding='utf-8')
    write_json(rp / 'texts/languages.json', ['en_US', 'ja_JP'])
    world = ConvertedWorld(converted)
    try:
        structures = {key: world.export(bounds, bp / f'structures/junket/{key}.mcstructure') for key, bounds in BOUNDS.items()}
        for i in range(13):
            key = f'temperance_beam_{i}'
            structures[key] = world.export([[88,101,263+i],[100,113,263+i]], bp / f'structures/junket/{key}.mcstructure')
    finally:
        world.db.close()
    report = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'structures': structures,
              'models': len(models), 'model_elements': {key: len(model['elements']) for key, model in models.items()},
              'atlas_size': [256, 128], 'original_sound_files': 0,
              'notes': ['No entities imported from Java; display objects are converted separately.',
                        '17 original models plus one screen reconstructed from the source display transformation.',
                        'Sparrow hall includes shared salon furnishings. Western adjoining room is excluded.',
                        'Crow reception is a counter module, not the entire hotel.',
                        'Custom head skins, Java sign formatting and banner patterns require visual review.']}
    report['temperance'] = build_temperance(source, vanilla, bp, rp, geometry, write_json)
    report['lucy'] = build_lucy(source, vanilla, bp, rp, geometry, write_json)
    report['silence'] = build_silence(source, vanilla, bp, rp, geometry, write_json)
    report['jinn'] = build_jinn(source, vanilla, bp, rp, geometry, write_json)
    write_json(output / 'conversion-report.json', report)
    with ZipFile(output / 'junket-local-venue-0.6.0.mcaddon', 'w', ZIP_DEFLATED) as bundle:
        for pack in [bp, rp]:
            for file in pack.rglob('*'):
                if file.is_file():
                    bundle.write(file, file.relative_to(output).as_posix())
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--converted', type=Path, default=Path('data/junket-private/conversion/bedrock-four-games'))
    parser.add_argument('--vanilla', type=Path, default=Path('data/junket-private/tools/java-1.21.3-client.jar'))
    parser.add_argument('--output', type=Path, default=Path('data/junket-private/assets'))
    args = parser.parse_args()
    build(args.source or next((Path.home() / 'Downloads').glob('*JUNKET*1.09*.zip')), args.converted, args.vanilla, args.output)
