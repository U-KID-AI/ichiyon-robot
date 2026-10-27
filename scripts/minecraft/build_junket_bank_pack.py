"""Add the original bank's protected guides and source text to PRIVATE packs.

Architecture comes exclusively from the verified source structure. Text keeps
its source wording/color/scale/position; local raster fonts are a Bedrock port,
not a claim of pixel-identical Java font rendering. No original trades execute.
"""
from copy import deepcopy
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from build_junket_bank_heads import compile_heads

ROOT = Path(__file__).resolve().parents[2]
BP = 'behavior_packs/ichiyon_junket_bp'
RP = 'resource_packs/ichiyon_junket_rp'
from junket_private_profile import private_value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def build(source, packs):
    source = json.loads(Path(source).read_text(encoding='utf-8'))
    template = json.loads((packs/BP/'entities/guide.json').read_text(encoding='utf-8'))
    fixtures=[]; evidence=[]
    colors={'white':'#FFFFFF','black':'#000000','gold':'#FFAA00','blue':'#5555FF','green':'#55FF55'}
    for e in source['entities']:
        if e['id']=='minecraft:interaction': continue
        key=e['role']; identifier='junket:bank_'+key
        location=[a+b for a,b in zip(e['Pos'],private_value("bank.production_offset"))]
        yaw=e['Rotation'][0]
        bp=deepcopy(template); description=bp['minecraft:entity']['description'];description['identifier']=identifier
        components=bp['minecraft:entity']['components']
        components['minecraft:nameable']['always_show']=False
        if e['id']=='minecraft:villager':
            butcher=e['VillagerData']['profession']=='minecraft:butcher'
            client={'format_version':'1.8.0','minecraft:client_entity':{'description':{
                'identifier':identifier,'materials':{'default':'villager_v2','masked':'villager_v2_masked'},
                'textures':{'base':'textures/entity/villager2/villager','biome':'textures/entity/villager2/biomes/biome_plains',
                            'profession':'textures/entity/villager2/professions/'+('butcher' if butcher else 'unskilled')},
                'geometry':{'default':'geometry.villager_v2'},'scripts':{'scale':'0.9375'},
                'render_controllers':['controller.render.bank_villager_base','controller.render.bank_villager_'+('butcher' if butcher else 'plain')]}}}
        else:
            components['minecraft:collision_box']={'width':0,'height':0}
            components.pop('minecraft:interact',None)
            text=json.loads(e['text']);content=text['text'];scale=e['transformation']['scale']
            font_path=Path('C:/Windows/Fonts/seguiemj.ttf') if content=='🐦' else Path('C:/Windows/Fonts/meiryob.ttc' if text.get('bold') else 'C:/Windows/Fonts/meiryo.ttc')
            font=ImageFont.truetype(str(font_path),48)
            box=font.getbbox(content); width=max(1,box[2]-box[0]);height=max(1,box[3]-box[1])
            image=Image.new('RGBA',(width+8,height+8),(0,0,0,0))
            ImageDraw.Draw(image).text((4-box[0],4-box[1]),content,font=font,fill=colors[text['color']])
            png=packs/RP/f'textures/entity/bank/{key}.png';png.parent.mkdir(parents=True,exist_ok=True);image.save(png)
            # Java text pixels are 0.025 blocks. CJK glyph advance ~9 pixels;
            # retain source aspect scale and avoid co-planar source walls.
            h=9*.025*float(scale[1])*16;w=max(9,len(content)*9)*.025*float(scale[0])*16
            geo='geometry.junket.bank_'+key
            uv={'uv':[0,0],'uv_size':list(image.size)}
            write(packs/RP/f'models/entity/bank/{key}.geo.json',{'format_version':'1.16.0','minecraft:geometry':[{
                'description':{'identifier':geo,'texture_width':image.width,'texture_height':image.height,
                    'visible_bounds_width':max(2,w/16+2),'visible_bounds_height':max(2,h/16+2),'visible_bounds_offset':[0,h/32,0]},
                'bones':[{'name':'text','pivot':[0,0,0],'cubes':[{'origin':[-w/2,0,-.1],'size':[w,h,.2],
                    'uv':{'north':uv,'south':uv}}]}]}]})
            location[0]-=math.sin(math.radians(yaw))*.04;location[2]+=math.cos(math.radians(yaw))*.04
            client={'format_version':'1.10.0','minecraft:client_entity':{'description':{
                # Each external face has readable UVs. The regular alphatest
                # inherits DisableCulling and also draws the opposite face's
                # mirrored reverse through this transparent 0.2px text plate.
                'identifier':identifier,'materials':{'default':'entity_alphatest_one_sided'},
                'textures':{'default':f'textures/entity/bank/{key}'},'geometry':{'default':geo},
                'render_controllers':['controller.render.bank_text']}}}
            evidence.append({'id':key,'text':content,'source_location':e['Pos'],'source_rotation':e['Rotation'],
                             'source_scale':scale,'font':font_path.name,'two_readable_faces':True,
                             'backface_culling':True,'surface_separation':.04})
        write(packs/BP/f'entities/bank/{key}.json',bp)
        write(packs/RP/f'entity/bank/{key}.entity.json',client)
        fixtures.append({'id':key,'type':identifier,'location':location,'rotation':yaw,'name':''})
    controllers={
        'controller.render.bank_text':{'geometry':'Geometry.default','materials':[{'*':'Material.default'}],'textures':['Texture.default']},
        'controller.render.bank_villager_base':{'geometry':'Geometry.default','materials':[{'*':'Material.default'}],'textures':['Texture.base']},
        'controller.render.bank_villager_plain':{'geometry':'Geometry.default','materials':[{'*':'Material.default'}],'textures':['Texture.biome','Texture.profession']},
        'controller.render.bank_villager_butcher':{'geometry':'Geometry.default','materials':[{'*':'Material.masked'}],'textures':['Texture.biome','Texture.profession']},
    }
    write(packs/RP/'render_controllers/bank.render_controllers.json',{'format_version':'1.8.0','render_controllers':controllers})
    heads, head_evidence=compile_heads(Path.home()/'Downloads/JUNKET CRAFT ver1.09(要解凍).zip',packs)
    fixtures.extend(heads)
    config={'enabled':True,'bounds':[-194,63,284,-149,93,313],
            'arrival':{'location':[-186.5,65,304.5],'rotation':0},
            'controls':[{'location':[-187,66,309],'type':'minecraft:stone_button'},
                        {'location':[-186,66,309],'type':'minecraft:birch_wall_sign'}],
            'fixtures':fixtures}
    scripts=packs/BP/'scripts'
    # Copy only the reviewed common integration; retain the existing private
    # generated game layout and the current original models in this candidate.
    for filename in ('main.js','all17_reference_main.js','warp.js','safe_warp.js','venue_presentation.js','bank_hub.js'):
        data=(ROOT/'minecraft'/BP/'scripts'/filename).read_bytes()
        (scripts/filename).write_bytes(data)
    (scripts/'bank_hub_generated.js').write_text('export const BANK_HUB = '+json.dumps(config,ensure_ascii=False,indent=2)+';\n',encoding='utf-8')
    # The production manifest uses the original-venue entry, not main.js.
    # Validate the packaged entry itself so a unit-tested but unreachable hub
    # cannot be shipped again.
    manifest=json.loads((packs/BP/'manifest.json').read_text(encoding='utf-8'))
    entries=[m['entry'] for m in manifest['modules'] if m['type']=='script']
    if entries!=['scripts/all17_reference_main.js']:
        raise ValueError('Bank integration requires the verified original-venue entry')
    entry=(packs/BP/entries[0]).read_text(encoding='utf-8')
    if 'catalog: bankCatalog' not in entry or 'createBankHub({ ...context, warp })' not in entry:
        raise ValueError('Active production entry is missing bank integration')
    write(packs.parent/'bank-presentation-proof.json',{'source_archive_sha256':source['source_archive_sha256'],
        'fixtures':fixtures,'text_port':evidence,'heads':head_evidence,'source_text_count':len(evidence),'source_villager_count':4,
        'original_shop_logic_ported':False,'client_visual_verified':False})
    return config
