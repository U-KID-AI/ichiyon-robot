"""Private Lucy model conversion; invoked by build_junket_venue."""
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile
from PIL import Image


def build_lucy(source, vanilla, bp, rp, geometry, write_json):
    prefix = 'assets/minecraft/models/item/game/lucy/'
    with ZipFile(source) as archive, ZipFile(vanilla) as base:
        with ZipFile(BytesIO(archive.read(next(n for n in archive.namelist() if n.endswith('/resources.zip'))))) as resources:
            models = {Path(n).stem: json.loads(resources.read(n))
                      for n in resources.namelist() if n.startswith(prefix) and n.endswith('.json')}
            from junket_model_geometry import texture_atlas
            sheet, atlas = texture_atlas(resources, base, models, (128, 128))
            sheet.save(rp / 'textures/entity/junket_lucy.png')

    groups = {
        'box': ['box'] + [f'box-{index}' for index in range(2, 11)] + ['box-open-1', 'box-open-2'],
        'keyhole': ['keyhole-close', 'keyhole-open', 'keyhole-close-key', 'keyhole-open-key'],
        'doll': ['lucy', 'lucy-down', 'lucy-middle', 'lucy-open', 'lucy-up', 'lucy-up-key'],
        'key': ['key'],
        'tv': ['tv'],
    }
    geos = []
    for frames in groups.values():
        for frame in frames:
            geo = geometry(models[frame], 'geometry.junket.lucy_' + frame.replace('-', '_'), atlas, (128, 128))
            geo['description'].update(visible_bounds_width=8, visible_bounds_height=10)
            geos.append(geo)
    write_json(rp / 'models/entity/lucy.geo.json',
               {'format_version': '1.21.0', 'minecraft:geometry': geos})

    scales = {'box': .3, 'keyhole': .4, 'doll': 2, 'key': 1, 'tv': 1}
    for name, frames in groups.items():
        identifier = 'junket:lucy_' + name
        prop = {'junket:lucy_frame': {
            'type': 'int', 'range': [0, len(frames) - 1], 'default': 0, 'client_sync': True,
        }} if len(frames) > 1 else {}
        components = {
            'minecraft:physics': {'has_gravity': False, 'has_collision': False},
            'minecraft:collision_box': {'width': .7 if name in ('box', 'doll') else .2, 'height': .8},
            'minecraft:pushable': {'is_pushable': False, 'is_pushable_by_piston': False},
            'minecraft:damage_sensor': {'triggers': [{'cause': 'all', 'deals_damage': 'no'}]},
            'minecraft:persistent': {},
            'minecraft:nameable': {'always_show': True, 'allow_name_tag_renaming': False},
            'minecraft:scale': {'value': scales[name]},
        }
        if name in ('box', 'keyhole', 'doll'):
            components['minecraft:interact'] = {'interactions': [{
                'on_interact': {'event': 'junket:touch', 'target': 'self'},
                'interact_text': 'action.interact.junket',
            }]}
        write_json(bp / f'entities/lucy_{name}.json', {
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
            'textures': {'default': 'textures/entity/junket_lucy'},
            'geometry': {f'f{index}': 'geometry.junket.lucy_' + frame.replace('-', '_')
                         for index, frame in enumerate(frames)},
            'render_controllers': ['controller.render.junket.lucy_' + name],
        }
        if name == 'tv':
            animation = 'animation.junket.lucy_tv_scale'
            desc.update(animations={'pose': animation}, scripts={'animate': ['pose']})
            write_json(rp / 'animations/lucy_tv.json', {
                'format_version': '1.8.0',
                'animations': {animation: {
                    'loop': 'hold_on_last_frame', 'animation_length': .05,
                    'bones': {'root': {'scale': [2.5, 2.5, 1]}},
                }},
            })
        write_json(rp / f'entity/lucy_{name}.entity.json', {
            'format_version': '1.10.0',
            'minecraft:client_entity': {'description': desc},
        })
        write_json(rp / f'render_controllers/lucy_{name}.json', {
            'format_version': '1.8.0',
            'render_controllers': {'controller.render.junket.lucy_' + name: {
                'arrays': {'geometries': {'Array.frames': [f'Geometry.f{index}' for index in range(len(frames))]}},
                'geometry': "Array.frames[query.property('junket:lucy_frame')]" if prop else 'Geometry.f0',
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
            'Box, keyhole and doll item-display scales become Bedrock entity scale values.',
            'The monitor keeps the source nonuniform 2.5:2.5:1 constant scale.',
        ],
    }
