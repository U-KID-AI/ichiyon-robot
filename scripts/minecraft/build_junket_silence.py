"""Private Sound of Silence model conversion; invoked by build_junket_venue."""
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile
from PIL import Image


def build_silence(source, vanilla, bp, rp, geometry, write_json):
    prefix = 'assets/minecraft/models/item/game/sos/'
    with ZipFile(source) as archive, ZipFile(vanilla) as base:
        with ZipFile(BytesIO(archive.read(next(n for n in archive.namelist() if n.endswith('/resources.zip'))))) as resources:
            models = {Path(n).stem: json.loads(resources.read(n))
                      for n in resources.namelist() if n.startswith(prefix) and n.endswith('.json')}
            for model in models.values():
                if any(face['texture'] == '#missing'
                       for element in model['elements'] for face in element['faces'].values()):
                    model['textures']['missing'] = 'block/black_concrete'
            textures = sorted({value for model in models.values()
                               for value in model['textures'].values() if not value.startswith('#')})
            sheet, atlas, x, y, row = Image.new('RGBA', (128, 128)), {}, 0, 0, 0
            for texture in textures:
                name = 'assets/minecraft/textures/' + texture + '.png'
                image = Image.open(BytesIO((resources if name in resources.namelist() else base).read(name))).convert('RGBA')
                if x + image.width > sheet.width:
                    x, y, row = 0, y + row, 0
                if y + image.height > sheet.height:
                    raise ValueError(f'Silence texture atlas overflow at {texture}')
                sheet.paste(image, (x, y))
                atlas[texture] = (x, y, image.width, image.height)
                x += image.width
                row = max(row, image.height)
            sheet.save(rp / 'textures/entity/junket_silence.png')

    groups = {
        'jacket': ['jacket-unknown', 'jacket-0', 'jacket-20', 'jacket-30', 'jacket-dirty'],
        'cd': ['cd-0', 'cd-20', 'cd-30'],
        'covering': ['covering'],
        'tv': ['tv'],
    }
    geos = []
    for frames in groups.values():
        for frame in frames:
            geo = geometry(models[frame], 'geometry.junket.silence_' + frame.replace('-', '_'), atlas, (128, 128))
            geo['description'].update(visible_bounds_width=10, visible_bounds_height=10)
            geos.append(geo)
    write_json(rp / 'models/entity/silence.geo.json',
               {'format_version': '1.21.0', 'minecraft:geometry': geos})

    for name, frames in groups.items():
        identifier = 'junket:silence_' + name
        prop = {'junket:silence_frame': {
            'type': 'int', 'range': [0, len(frames) - 1], 'default': 0, 'client_sync': True,
        }} if len(frames) > 1 else {}
        components = {
            'minecraft:physics': {'has_gravity': False, 'has_collision': False},
            'minecraft:collision_box': {'width': .5, 'height': .5},
            'minecraft:pushable': {'is_pushable': False, 'is_pushable_by_piston': False},
            'minecraft:damage_sensor': {'triggers': [{'cause': 'all', 'deals_damage': 'no'}]},
            'minecraft:persistent': {},
            'minecraft:nameable': {'always_show': True, 'allow_name_tag_renaming': False},
        }
        if name in ('jacket', 'cd', 'covering'):
            components['minecraft:interact'] = {'interactions': [{
                'on_interact': {'event': 'junket:touch', 'target': 'self'},
                'interact_text': 'action.interact.junket',
            }]}
        write_json(bp / f'entities/silence_{name}.json', {
            'format_version': '1.21.0',
            'minecraft:entity': {
                'description': {
                    'identifier': identifier, 'is_spawnable': False, 'is_summonable': True,
                    'properties': prop,
                },
                'components': components,
                'events': {'junket:touch': {}},
            },
        })
        desc = {
            'identifier': identifier,
            'materials': {'default': 'entity_alphatest'},
            'textures': {'default': 'textures/entity/junket_silence'},
            'geometry': {f'f{index}': 'geometry.junket.silence_' + frame.replace('-', '_')
                         for index, frame in enumerate(frames)},
            'render_controllers': ['controller.render.junket.silence_' + name],
        }
        transforms = {'cd': [.5, .1, .5], 'tv': [7, 7, 1]}
        if name in transforms:
            animation = 'animation.junket.silence_' + name + '_scale'
            desc.update(animations={'pose': animation}, scripts={'animate': ['pose']})
            write_json(rp / f'animations/silence_{name}.json', {
                'format_version': '1.8.0',
                'animations': {animation: {
                    'loop': 'hold_on_last_frame', 'animation_length': .05,
                    'bones': {'root': {'scale': transforms[name]}},
                }},
            })
        write_json(rp / f'entity/silence_{name}.entity.json', {
            'format_version': '1.10.0',
            'minecraft:client_entity': {'description': desc},
        })
        write_json(rp / f'render_controllers/silence_{name}.json', {
            'format_version': '1.8.0',
            'render_controllers': {'controller.render.junket.silence_' + name: {
                'arrays': {'geometries': {'Array.frames': [f'Geometry.f{index}' for index in range(len(frames))]}},
                'geometry': "Array.frames[query.property('junket:silence_frame')]" if prop else 'Geometry.f0',
                'materials': [{'*': 'Material.default'}],
                'textures': ['Texture.default'],
            }},
        })
    return {
        'models': len(models),
        'atlas_size': [128, 128],
        'groups': {name: len(frames) for name, frames in groups.items()},
        'elements': {name: len(model['elements']) for name, model in models.items()},
        'adaptations': [
            'CD and television retain their source nonuniform display scales.',
            'Undefined CD center faces use black concrete instead of the missing texture.',
        ],
    }
