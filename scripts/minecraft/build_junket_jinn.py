"""Private Jackpot Jinn model conversion; invoked by build_junket_venue."""
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile
from PIL import Image


def build_jinn(source, vanilla, bp, rp, geometry, write_json):
    prefix = 'assets/minecraft/models/item/game/jinn/'
    with ZipFile(source) as archive, ZipFile(vanilla) as base:
        with ZipFile(BytesIO(archive.read(next(n for n in archive.namelist() if n.endswith('/resources.zip'))))) as resources:
            models = {Path(n).stem: json.loads(resources.read(n))
                      for n in resources.namelist() if n.startswith(prefix) and n.endswith('.json')}
            textures = sorted({value for model in models.values()
                               for value in model['textures'].values() if not value.startswith('#')})
            sheet, atlas, x, y, row = Image.new('RGBA', (256, 256)), {}, 0, 0, 0
            for texture in textures:
                name = 'assets/minecraft/textures/' + texture + '.png'
                image = Image.open(BytesIO((resources if name in resources.namelist() else base).read(name))).convert('RGBA')
                if x + image.width > sheet.width:
                    x, y, row = 0, y + row, 0
                if y + image.height > sheet.height:
                    raise ValueError(f'Jinn texture atlas overflow at {texture}')
                sheet.paste(image, (x, y))
                atlas[texture] = (x, y, image.width, image.height)
                x += image.width
                row = max(row, image.height)
            sheet.save(rp / 'textures/entity/junket_jinn.png')

    groups = {
        'card': ['card-unknown', 'card-gold', 'card-thief', 'card-demon'],
        'covering': ['covering'],
        'food': ['food'],
        'frame': ['frame-folded'],
        'glass': ['glass', 'glass-re'],
        'lever': ['lever'],
        'tv': ['tv-score'],
    }
    geos = []
    for frames in groups.values():
        for frame in frames:
            geo = geometry(models[frame], 'geometry.junket.jinn_' + frame.replace('-', '_'), atlas, (256, 256))
            geo['description'].update(visible_bounds_width=14, visible_bounds_height=14)
            geos.append(geo)
    write_json(rp / 'models/entity/jinn.geo.json',
               {'format_version': '1.21.0', 'minecraft:geometry': geos})

    for name, frames in groups.items():
        identifier = 'junket:jinn_' + name
        prop = {'junket:jinn_frame': {
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
        if name in ('card', 'lever', 'covering'):
            components['minecraft:interact'] = {'interactions': [{
                'on_interact': {'event': 'junket:touch', 'target': 'self'},
                'interact_text': 'action.interact.junket',
            }]}
        write_json(bp / f'entities/jinn_{name}.json', {
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
            'textures': {'default': 'textures/entity/junket_jinn'},
            'geometry': {f'f{index}': 'geometry.junket.jinn_' + frame.replace('-', '_')
                         for index, frame in enumerate(frames)},
            'render_controllers': ['controller.render.junket.jinn_' + name],
        }
        transforms = {'covering': [3, 1, 3], 'glass': [.8, 1.1, .8], 'tv': [3.2, 3, 1]}
        if name in transforms:
            animation = 'animation.junket.jinn_' + name + '_scale'
            desc.update(animations={'pose': animation}, scripts={'animate': ['pose']})
            write_json(rp / f'animations/jinn_{name}.json', {
                'format_version': '1.8.0',
                'animations': {animation: {
                    'loop': 'hold_on_last_frame', 'animation_length': .05,
                    'bones': {'root': {'scale': transforms[name]}},
                }},
            })
        write_json(rp / f'entity/jinn_{name}.entity.json', {
            'format_version': '1.10.0',
            'minecraft:client_entity': {'description': desc},
        })
        write_json(rp / f'render_controllers/jinn_{name}.json', {
            'format_version': '1.8.0',
            'render_controllers': {'controller.render.junket.jinn_' + name: {
                'arrays': {'geometries': {'Array.frames': [f'Geometry.f{index}' for index in range(len(frames))]}},
                'geometry': "Array.frames[query.property('junket:jinn_frame')]" if prop else 'Geometry.f0',
                'materials': [{'*': 'Material.default'}],
                'textures': ['Texture.default'],
            }},
        })
    return {
        'models': len(models),
        'atlas_size': [256, 256],
        'groups': {name: len(frames) for name, frames in groups.items()},
        'elements': {name: len(model['elements']) for name, model in models.items()},
        'adaptations': [
            'Coin glass, covering and television retain their source nonuniform display scales.',
            'Card faces are selected with a synced integer property.',
        ],
    }
