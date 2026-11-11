"""Private Temperance asset conversion; invoked by build_junket_venue."""
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile
from PIL import Image


def build_temperance(source, vanilla, bp, rp, geometry, write_json):
    with ZipFile(source) as archive, ZipFile(vanilla) as base:
        with ZipFile(BytesIO(archive.read(next(n for n in archive.namelist() if n.endswith('/resources.zip'))))) as resources:
            models = {Path(n).stem: json.loads(resources.read(n)) for n in resources.namelist()
                      if n.startswith('assets/minecraft/models/item/game/temperance/') and n.endswith('.json')}
            from junket_model_geometry import texture_atlas
            sheet, atlas = texture_atlas(resources, base, models, (256, 256))
            sheet.save(rp / 'textures/entity/junket_temperance.png')
    geos = []
    for name, model in models.items():
        geo = geometry(model, 'geometry.junket.temperance_' + name.replace('-', '_'), atlas)
        geo['description'].update(texture_height=256, visible_bounds_width=16, visible_bounds_height=20)
        for bone in geo['bones']:
            bone['parent'] = 'root'
        geo['bones'].insert(0, {'name': 'root', 'pivot': [0, 0, 0]})
        geos.append(geo)
    write_json(rp / 'models/entity/temperance.geo.json', {'format_version': '1.21.0', 'minecraft:geometry': geos})
    groups = {
        'card': ['card-unknown', 'card-1', 'card-1-reversed', 'card-10', 'card-10-reversed', 'card-2r', 'card-2r-reversed', 'card-player1', 'card-player2'],
        'frame': ['frame', 'frame-folded'], 'balance': ['balance'], 'base': ['base'],
        'tv': ['tv-score', 'tv-black', 'tv'], 'stone': ['stone'],
    }
    for name, frames in groups.items():
        identifier = 'junket:temperance_' + name
        prop = {'junket:temperance_frame': {'type': 'int', 'range': [0, len(frames)-1], 'default': 0, 'client_sync': True}} if len(frames)>1 else {}
        components = {'minecraft:physics': {'has_gravity': False, 'has_collision': False},
                      'minecraft:collision_box': {'width': .7 if name == 'card' else .1, 'height': .8 if name == 'card' else .1},
                      'minecraft:pushable': {'is_pushable': False, 'is_pushable_by_piston': False},
                      'minecraft:damage_sensor': {'triggers': [{'cause': 'all', 'deals_damage': 'no'}]},
                      'minecraft:persistent': {}, 'minecraft:nameable': {'always_show': True, 'allow_name_tag_renaming': False}}
        if name == 'card':
            components['minecraft:interact'] = {'interactions': [{'on_interact': {'event': 'junket:touch', 'target': 'self'}, 'interact_text': 'action.interact.junket'}]}
        write_json(bp / f'entities/temperance_{name}.json', {'format_version': '1.21.0', 'minecraft:entity': {
            'description': {'identifier': identifier, 'is_spawnable': False, 'is_summonable': True, 'properties': prop},
            'components': components, 'events': {'junket:touch': {}}}})
        desc = {'identifier': identifier, 'materials': {'default': 'entity_alphatest'},
                'textures': {'default': 'textures/entity/junket_temperance'},
                'geometry': {f'f{i}': 'geometry.junket.temperance_' + f.replace('-', '_') for i, f in enumerate(frames)},
                'render_controllers': ['controller.render.junket.temperance_' + name]}
        if name in ('balance', 'tv'):
            # Constant transform: no time expression, motion, looping or server timer.
            animation = 'animation.junket.temperance_' + name + '_scale'
            desc.update(animations={'pose': animation}, scripts={'animate': ['pose']})
            write_json(rp / f'animations/temperance_{name}.json', {'format_version': '1.8.0', 'animations': {
                animation: {'loop': 'hold_on_last_frame', 'animation_length': .05,
                            'bones': {'root': {'scale': [5, 3, 5] if name == 'balance' else [2.4, 2, 1]}}}}})
        write_json(rp / f'entity/temperance_{name}.entity.json', {'format_version': '1.10.0', 'minecraft:client_entity': {'description': desc}})
        write_json(rp / f'render_controllers/temperance_{name}.json', {'format_version': '1.8.0', 'render_controllers': {'controller.render.junket.temperance_' + name: {
            'arrays': {'geometries': {'Array.frames': [f'Geometry.f{i}' for i in range(len(frames))]}},
            'geometry': "Array.frames[query.property('junket:temperance_frame')]" if prop else 'Geometry.f0',
            'materials': [{'*': 'Material.default'}], 'textures': ['Texture.default']}}})
    return {'models': len(models), 'atlas_size': [256, 256], 'elements': {k: len(v['elements']) for k, v in models.items()},
            'adaptations': ['Undefined faces are omitted by the shared source surface converter.', 'Constant root scale preserves Java nonuniform display transforms.']}
