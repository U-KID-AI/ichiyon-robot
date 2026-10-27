"""Restore eight PRIVATE original bank skull skins lost by Chunker.

The physical skull blocks/structure are untouched. Small protected, anchored
head-only entities cover the fallback skin, using the original profile PNGs.
"""
from __future__ import annotations
from junket_private_profile import private_value

import base64
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import urlopen

import nbtlib as n
from PIL import Image

from build_junket_bank_source import PRIVATE, inside, needed_chunks
from junket_java import JavaWorld

BP = 'behavior_packs/ichiyon_junket_bp'
RP = 'resource_packs/ichiyon_junket_rp'

CACHE = PRIVATE/'bank-expansion-20261025/head-textures'
STRUCTURE = PRIVATE/'bank-expansion-20261025/source-bank-candidate/assets/ichiyon_junket_original_bp/structures/junket/karasu_bank.mcstructure'
INNER_REGIONS = [(0,8,32,16), (8,0,24,8)]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def skin_url(raw):
    parsed = urlparse(raw)
    match = re.fullmatch('/texture/([a-f0-9]{32,64})', parsed.path)
    if parsed.scheme not in ('http','https') or parsed.hostname != 'textures.minecraft.net' or not match or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('Skull profile is not a canonical Minecraft texture URL')
    return 'https://textures.minecraft.net'+parsed.path, match[1]


def source_heads(source_zip):
    world = JavaWorld(source_zip); rows = []
    try:
        for cx,cz in sorted(needed_chunks()):
            for entity in world.chunk(cx,cz).get('block_entities',[]):
                at = [int(entity[k]) for k in 'xyz']
                if not inside(at) or str(entity.get('id')) != 'minecraft:skull':
                    continue
                block = world.block(*at)
                if str(block['Name']) != 'minecraft:player_head':
                    raise ValueError('Bank source skull is not an upright player head')
                properties = [p for p in entity.get('profile',{}).get('properties',[]) if str(p.get('name')) == 'textures']
                if len(properties) != 1:
                    raise ValueError('Bank skull texture profile missing or ambiguous')
                encoded = str(properties[0]['value'])
                profile = json.loads(base64.b64decode(encoded, validate=True))
                url, texture_id = skin_url(profile['textures']['SKIN']['url'])
                rotation = int(str(block['Properties']['rotation']))
                if not 0 <= rotation < 16:
                    raise ValueError('Invalid Java standing skull rotation')
                rows.append({'source_position': at, 'source_rotation_step': rotation,
                             'rotation_degrees': rotation*22.5, 'texture_url': url,
                             'texture_id': texture_id, 'profile_sha256': sha256(encoded.encode()).hexdigest()})
    finally:
        world.archive.close()
    rows.sort(key=lambda r:r['source_position'])
    if {tuple(r['source_position']) for r in rows} != {tuple(point) for point in private_value("bank.head_positions")} or len(rows) != 8:
        raise ValueError('Original bank head positions/count changed; inspect source before updating')
    return rows


def native_rotations(structure_path):
    structure = n.load(structure_path, byteorder='little')
    if list(map(int,structure['size'])) != private_value("bank.structure_size"):
        raise ValueError('Unexpected bank structure footprint')
    result = {}
    for value in structure['structure']['palette']['default']['block_position_data'].values():
        tile = value['block_entity_data']
        if str(tile.get('id')) != 'Skull':
            continue
        if int(tile['SkullType']) != 3:
            raise ValueError('Converted bank skull is not a player head')
        at = tuple(int(tile[k])+offset for k,offset in zip('xyz',private_value("bank.bounds")[0]))
        result[at] = float(tile['Rotation'])
    return result


def original_skin(row, cache):
    cache.mkdir(parents=True, exist_ok=True)
    target = cache/(row['texture_id']+'.png')
    if not target.exists():
        with urlopen(row['texture_url'], timeout=30) as response:
            if urlparse(response.url).hostname != 'textures.minecraft.net':
                raise ValueError('Unexpected Minecraft texture redirect')
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Unexpectedly large Minecraft skull skin')
        # Texture IDs historically omit leading zeroes; compare normalized SHA.
        if sha256(raw).hexdigest().lstrip('0') != row['texture_id'].lstrip('0'):
            raise ValueError('Downloaded texture digest does not match original profile')
        image = Image.open(BytesIO(raw)); image.verify()
        target.write_bytes(raw)
    raw = target.read_bytes()
    if sha256(raw).hexdigest().lstrip('0') != row['texture_id'].lstrip('0'):
        raise ValueError('Cached bank skin was modified')
    image = Image.open(BytesIO(raw)).convert('RGBA')
    if image.size not in ((64,32),(64,64)):
        raise ValueError('Unsupported original head skin dimensions')
    # No pixel edits/resampling: all six sampled original inner faces must be
    # opaque so the retained native fallback skull can never show through.
    alpha = image.getchannel('A')
    if any(alpha.crop(region).getextrema() != (255,255) for region in INNER_REGIONS):
        raise ValueError('Original head inner skin has transparency; explicit conversion needed')
    hat = alpha.crop((32,0,64,16)).getextrema()[1] > 0
    return raw, image.size, hat


def head_geometry(identifier, size, has_hat):
    # Mojang humanoid head box UV: [0,0] inner and [32,0] hat. Keep the cube
    # size exactly eight pixels: inflate changes geometry, never UV extents.
    # Java standing skull step 0 and Bedrock actor yaw 0 both face south.
    bones = [{'name':'head', 'pivot':[0,4,0], 'cubes':[
        {'origin':[-4,0,-4], 'size':[8,8,8], 'uv':[0,0], 'inflate':.02}]}]
    if has_hat:
        bones.append({'name':'hat','parent':'head','pivot':[0,4,0],'cubes':[
            {'origin':[-4,0,-4],'size':[8,8,8],'uv':[32,0],'inflate':.25}]})
    return {'format_version':'1.16.0','minecraft:geometry':[{
        'description':{'identifier':identifier,'texture_width':size[0],'texture_height':size[1],
                       'visible_bounds_width':1.2,'visible_bounds_height':1.2,'visible_bounds_offset':[0,.25,0]},
        'bones':bones}]}


def compile_heads(source_zip, packs, *, structure_path=None, cache=None):
    """Write private additive head assets; return (fixtures, evidence).

    Caller extends BANK_HUB.fixtures with these eight records. The existing
    bank reconciliation supplies tag ownership, immobility and duplicate repair.
    """
    packs = Path(packs).resolve(); cache = Path(cache or CACHE).resolve()
    if not packs.is_relative_to(PRIVATE.resolve()) or not cache.is_relative_to(PRIVATE.resolve()):
        raise ValueError('Original skin assets must stay in private ignored storage')
    source_zip = Path(source_zip); structure_path = Path(structure_path or STRUCTURE)
    structure_sha = sha256(structure_path.read_bytes()).hexdigest()
    rows = source_heads(source_zip); native = native_rotations(structure_path)
    if set(native) != {tuple(point) for point in private_value("bank.head_positions")}:
        raise ValueError('Converted skull positions differ from original source')
    template = json.loads((packs/BP/'entities/guide.json').read_text('utf-8'))
    fixtures = []; generated = {}; evidence_rows = []
    for row in rows:
        at = row['source_position']
        if native[tuple(at)] != row['rotation_degrees']:
            raise ValueError(f'Java and Chunker skull rotations disagree: {at}')
        raw,size,hat = original_skin(row,cache)
        key = 'bank_head_'+row['texture_id'][:16]
        identifier = 'junket:'+key; geo = 'geometry.junket.'+key
        if key not in generated:
            skin_path = packs/RP/f'textures/entity/bank/heads/{key}.png'
            skin_path.parent.mkdir(parents=True,exist_ok=True); skin_path.write_bytes(raw)
            write_json(packs/RP/f'models/entity/bank/heads/{key}.geo.json',head_geometry(geo,size,hat))
            behavior = deepcopy(template); entity = behavior['minecraft:entity']
            entity['description']['identifier'] = identifier
            entity['components']['minecraft:collision_box'] = {'width':0,'height':0}
            entity['components']['minecraft:nameable']['always_show'] = False
            entity['components'].pop('minecraft:interact',None); entity.pop('events',None)
            write_json(packs/BP/f'entities/bank/heads/{key}.json',behavior)
            write_json(packs/RP/f'entity/bank/heads/{key}.entity.json',{
                'format_version':'1.10.0','minecraft:client_entity':{'description':{
                    'identifier':identifier,'materials':{'default':'entity_alphatest'},
                    'textures':{'default':f'textures/entity/bank/heads/{key}'},
                    'geometry':{'default':geo},'render_controllers':['controller.render.bank_head']}}})
            generated[key] = {'entity':identifier,'skin_sha256':sha256(raw).hexdigest(),
                              'skin_size':list(size),'has_original_hat':hat}
        position = [at[0]+private_value("bank.production_offset")[0]+.5,at[1]+private_value("bank.production_offset")[1],at[2]+private_value("bank.production_offset")[2]+.5]
        role = 'source_head_'+'_'.join(str(v).replace('-','n') for v in at)
        fixtures.append({'id':role,'type':identifier,'location':position,
                         'rotation':row['rotation_degrees'],'name':''})
        evidence_rows.append({**row,**generated[key],'fixture_id':role,'production_position':position,
                              'chunker_skull_rotation':native[tuple(at)],'rotation_matches':True,
                              'inner_extent_pixels':8.04,'hat_extent_pixels':8.5 if hat else None,
                              'original_texture_bytes_unchanged':True,'inner_face_alpha_min':255})
    write_json(packs/RP/'render_controllers/bank_heads.render_controllers.json',{
        'format_version':'1.8.0','render_controllers':{'controller.render.bank_head':{
            'geometry':'Geometry.default','materials':[{'*':'Material.default'}],'textures':['Texture.default']}}})
    if sha256(structure_path.read_bytes()).hexdigest() != structure_sha:
        raise AssertionError('Bank native structure was unexpectedly modified')
    evidence = {'schema':1,'source_archive_sha256':sha256(source_zip.read_bytes()).hexdigest(),
                'native_structure_sha256':structure_sha,'physical_head_blocks_unchanged':True,
                'heads':evidence_rows,'head_count':len(fixtures),'unique_skins':len(generated),
                'uv_reference':'https://raw.githubusercontent.com/Mojang/bedrock-samples/main/resource_pack/models/entity/humanoid.custom.geo.json',
                'client_visual_verified':False}
    write_json(packs.parent/'bank-heads-proof.json',evidence)
    return fixtures,evidence
