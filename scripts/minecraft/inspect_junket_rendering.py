"""Private artwork fidelity, model normals and observer evidence for 17 venues.

Output is numerical/source evidence, never a substitute for client rendering.
"""
import argparse
from collections import Counter
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
from zipfile import ZipFile

from PIL import Image
from inspect_junket_original_quality import source_local_normal
from junket_model_geometry import AXIS, REPEAT, resolve_texture
from junket_render_validation import read, write

NORMALS = {'north': [0, 0, -1], 'south': [0, 0, 1], 'east': [1, 0, 0],
           'west': [-1, 0, 0], 'up': [0, 1, 0], 'down': [0, -1, 0]}


def world_normal(normal, transformation, yaw_pitch):
    # Java 1.21.3 DisplayRenderer fixed: rotationYXZ(-yaw,+pitch,0), then
    # display transformation, then ItemDisplayRenderer's Y-pi item rotation.
    x, y, z = source_local_normal([-normal[0], normal[1], -normal[2]], transformation)
    pitch = math.radians(yaw_pitch[1]); yaw = math.radians(-yaw_pitch[0])
    y, z = math.cos(pitch)*y-math.sin(pitch)*z, math.sin(pitch)*y+math.cos(pitch)*z
    return [round(math.cos(yaw)*x+math.sin(yaw)*z, 8), round(y, 8),
            round(-math.sin(yaw)*x+math.cos(yaw)*z, 8)]


def inspect(layouts, model_report, resources_zip, vanilla, asset_root, output):
    layouts = read(layouts); models = read(model_report)
    report = {'schema': 1, 'private_only': True, 'client_rendering_verified': False,
              'atlas_pixel_checks': [], 'games': [], 'basis': {
                  'face_uv_codec': 'Blockbench Java/Bedrock codec: reflect X, retain face keys, invert both up/down UV axes',
                  'uv_rotation_schema': 'Microsoft geometry.v1.21.0 supports clockwise 0/90/180/270',
                  'java_renderer': 'Official Java 1.21.3 client: DisplayRenderer fixed rotationYXZ(-yaw,+pitch,0); ItemDisplayRenderer adds Y pi inside display transform',
                  'global_180_degree_defect_established': False,
                  'monitor_exception': 'Sparrow main and Fire four small score/result screens only: source purpose plus observed Windows back-face baseline; model-centre turn, not global source yaw change',
                  'other_model_client_front_back_mirror_status': 'unverified; all source rotations/transforms retained'},
              'references': [
                  'https://github.com/JannisX11/blockbench/blob/master/js/formats/bedrock/bedrock.js',
                  'https://learn.microsoft.com/en-us/minecraft/creator/reference/content/visualreference/geometry.v1.21.0',
                  'https://www.minecraft.net/en-us/article/minecraft-snapshot-23w16a']}
    with ZipFile(resources_zip) as resources, ZipFile(vanilla) as base:
        for gid, atlas in models['atlases'].items():
            sheet = Image.open(asset_root / f'ichiyon_junket_original_rp/textures/entity/original_{gid}.png').convert('RGBA')
            for texture, (x, y, width, height) in atlas['textures'].items():
                original, _, repeat = texture.partition(REPEAT)
                name = 'assets/minecraft/textures/' + original.removeprefix('minecraft:') + '.png'
                archive = resources if name in resources.namelist() else base
                picture = Image.open(BytesIO(archive.read(name))).convert('RGBA')
                if picture.height > picture.width and name+'.mcmeta' in archive.namelist():
                    picture = picture.crop((0, 0, picture.width, picture.width))
                if repeat:
                    _, _, columns, rows = map(int, repeat.split(',')); tile = picture
                    picture = Image.new('RGBA', (tile.width*columns, tile.height*rows))
                    for col in range(columns):
                        for row in range(rows): picture.paste(tile, (col*tile.width, row*tile.height))
                actual = sheet.crop((x, y, x+width, y+height))
                if actual.size != picture.size or actual.tobytes() != picture.tobytes():
                    raise ValueError('Source artwork pixels differ: '+gid+'/'+texture)
                report['atlas_pixel_checks'].append({'game': gid, 'texture': texture, 'source_file': name,
                    'rgba_sha256': hashlib.sha256(picture.tobytes()).hexdigest(), 'pixels_match': True})
        for game in layouts['games']:
            gid = game['id']; display_rows=[]; model_rows=[]
            group='sos' if gid=='silence' else gid
            for model_name in models['models'][gid]:
                candidates=[name for name in resources.namelist()
                            if name.startswith(f'assets/minecraft/models/item/game/{group}/')
                            and name.endswith('/'+model_name+'.json')]
                if len(candidates)!=1:raise ValueError('Ambiguous source model: '+gid+'/'+model_name)
                source_name=candidates[0]
                model= json.loads(resources.read(source_name)); areas=Counter(); faces=Counter()
                for element in model['elements']:
                    for face, data in element['faces'].items():
                        area=math.prod(element['to'][i]-element['from'][i] for i in range(3) if i!=AXIS[face])
                        if area > 0 and resolve_texture(model,data['texture']) is not None:
                            areas[face]+=area;faces[face]+=1
                model_rows.append({'name':model_name,'source_sha256':hashlib.sha256(resources.read(source_name)).hexdigest(),
                    'source_display_contexts':model.get('display',{}),'source_face_area':dict(areas),'source_face_counts':dict(faces),
                    'repairs':models['surface_repairs'][gid+'/'+model_name]})
            for row in game['summons']:
                if row['type']!='item_display' or not row.get('model') or not row['model'].startswith('game/'+group+'/'):continue
                if row['item'] in ('air','minecraft:air'):continue
                normals={face:world_normal(normal,row['transformation'],row['rotation']) for face,normal in NORMALS.items()}
                evidence={'source_line':row['line'],'model':row['model'],'source_position':row['location'],
                    'source_yaw_pitch':row['rotation'],'source_transform':row['transformation'],
                    'face_normals_after_item_display_transform':normals,'source_tags':row['tags']}
                if '/tv' in row['model']:
                    cameras=[]
                    for seat in game['source_seats']:
                        eye=[seat[0],seat[1]+1.62,seat[2]]
                        d=[eye[i]-row['location'][i] for i in range(3)];length=math.sqrt(sum(v*v for v in d))
                        toward=[-v for v in d]
                        cameras.append({'feet':[seat[i]+game['translation'][i] for i in range(3)],
                            'yaw':round(math.degrees(math.atan2(-toward[0],toward[2])),3),
                            'pitch':round(-math.degrees(math.atan2(toward[1],math.hypot(toward[0],toward[2]))),3),
                            'north_face_viewer_cosine':round(sum(a*b for a,b in zip(normals['north'],d))/max(length,1e-9),6)})
                    evidence['source_seat_views']=cameras
                    evidence['nearest_seat_is_not_automatic_facing_authority']=True
                display_rows.append(evidence)
            repair_counts=Counter(event.get('reason',event['action']) for row in model_rows for event in row['repairs'])
            report['games'].append({'id':gid,'models':model_rows,'source_displays':display_rows,
                'repair_counts':dict(repair_counts),'model_count':len(model_rows),'display_count':len(display_rows)})
    report['totals']={'games':len(report['games']),'models':sum(g['model_count'] for g in report['games']),
        'source_displays':sum(g['display_count'] for g in report['games']),'atlas_tiles_verified':len(report['atlas_pixel_checks'])}
    write(output,report)
    print(json.dumps(report['totals']))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['layouts','model-report','resources-zip','vanilla','asset-root','output']:
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();inspect(a.layouts,a.model_report,a.resources_zip,a.vanilla,a.asset_root,a.output)
