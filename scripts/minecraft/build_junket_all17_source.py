"""Extract privately owned JUNKET source rooms and source models for all 17 games.

This is a local conversion tool, not a distributable asset generator. The output
is confined to ignored data/junket-private. Previously converted Chunker chunks
are used first; only absent chunks are read from the supplemental conversion.
"""
from __future__ import annotations
from junket_private_profile import private_value

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
from zipfile import ZipFile

import nbtlib as n
from PIL import Image

from junket_structure import ConvertedWorld
from build_junket_venue import geometry, write_json

ROOT = Path(__file__).resolve().parents[2]
OLD_PRIVATE = ROOT / 'data/junket-private'
OUTPUT = ROOT / 'data/junket-private/all17-reference'
# Room exteriors were checked against source block slices, roof elevations,
# source arrival points, register markers, and prepare.mcfunction positions.
# No substitute room architecture is generated here.
MODEL_GROUP = {'silence': 'sos'}
EXECUTABLE = {'minecraft:command_block','minecraft:chain_command_block',
              'minecraft:repeating_command_block','minecraft:structure_block','minecraft:jigsaw'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ensure_supplement(args):
    """Use Chunker only for requested source chunks absent from the old output."""
    if (args.supplement/'db/CURRENT').exists():return
    old=ConvertedWorld(args.converted)
    try:
        existing={struct.unpack('<ii',key[:8]) for key in old.db.keys() if len(key)==10 and key[8:9]==b'/'}
    finally:old.db.close()
    needed=set()
    for lo,hi in private_value("all17.bounds").values():
        needed.update((x,z) for x in range(lo[0]//16,hi[0]//16+1) for z in range(lo[2]//16,hi[2]//16+1))
    missing=sorted(needed-existing)
    java=args.output/'java-supplement';(java/'region').mkdir(parents=True,exist_ok=True)
    shutil.copy2(args.source_world/'level.dat',java/'level.dat')
    for rx,rz in {(x//32,z//32) for x,z in missing}:
        name=f'region/r.{rx}.{rz}.mca';shutil.copy2(args.source_world/name,java/name)
    pruning={'configs':{'minecraft:overworld':{'include':True,'regions':[
        {'minChunkX':x,'maxChunkX':x,'minChunkZ':z,'maxChunkZ':z} for x,z in missing]},
        'minecraft:the_nether':{'include':False},'minecraft:the_end':{'include':False}}}
    write_json(args.output/'pruning.json',pruning)
    executable=OLD_PRIVATE/'tools/chunker/chunker-cli/chunker-cli.exe'
    result=subprocess.run([str(executable),'-i',str(java),'-o',str(args.supplement),'-f','BEDROCK_1_21_130',
                           '-p',str(args.output/'pruning.json')],check=True,capture_output=True,text=True,encoding='utf-8')
    (args.output/'chunker-supplement.log').write_text(result.stdout+result.stderr,'utf-8')
    write_json(args.output/'supplement-provenance.json',{'source_sha256':sha(args.source),
       'source_regions':{str(p.relative_to(args.source_world)):sha(p) for p in (args.source_world/'region').glob('r.0.0.mca')},
       'new_chunks':missing,'existing_chunks_reused':len(needed&existing),'format':'BEDROCK_1_21_130'})


def plain(tag):
    if isinstance(tag, (n.Compound, dict)):
        return {str(k): plain(v) for k,v in tag.items()}
    if isinstance(tag, (n.List, list, tuple)):
        return [plain(v) for v in tag]
    if isinstance(tag,n.String): return str(tag)
    if isinstance(tag,(n.Float,n.Double)): return float(tag)
    if isinstance(tag,(n.Byte,n.Short,n.Int,n.Long)): return int(tag)
    return tag


def source_summons(path):
    """Resolve direct and `execute positioned` summons; retain unresolved counts."""
    result=[]; unresolved=[]
    for line_no,line in enumerate(path.read_text('utf-8').splitlines(),1):
        if 'summon ' not in line or line.lstrip().startswith('#'): continue
        match=re.search(r'\bsummon\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\{.*\})\s*$',line)
        if not match: continue
        positioned=re.search(r'\bpositioned\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)',line[:match.start()])
        try:
            coords=[]
            for axis,token in enumerate(match.group(2,3,4)):
                if token.startswith('~') and positioned:
                    coords.append(float(positioned[axis+1])+float(token[1:] or 0))
                else: coords.append(float(token))
            tag=n.parse_nbt(match[5]); data=plain(tag)
        except (ValueError,TypeError,KeyError,n.InvalidLiteral):
            unresolved.append(line_no); continue
        item=data.get('item',{}); model=item.get('components',{}).get('minecraft:item_model')
        result.append({'line':line_no,'type':match[1].removeprefix('minecraft:'),
                       'location':coords,'rotation':data.get('Rotation',[0,0]),
                       'tags':data.get('Tags',[]),'model':model,'item':item.get('id'),
                       'transformation':data.get('transformation',{}),
                       'width':data.get('width'),'height':data.get('height')})
    return result,unresolved


class CompositeWorld(ConvertedWorld):
    def __init__(self,old,supplement):
        self.old=ConvertedWorld(old); self.supplement=ConvertedWorld(supplement)
        self.existing_chunks={struct.unpack('<ii',key[:8]) for key in self.old.db.keys()
                              if len(key)==10 and key[8:9]==b'/'}

    def get(self,key):
        # Choose by chunk, never fill genuine source air with a different world.
        source=self.old if struct.unpack('<ii',key[:8]) in self.existing_chunks else self.supplement
        return source.get(key)

    def close(self):
        self.old.db.close(); self.supplement.db.close()


def export_structure(world,bounds,path):
    lo,hi=bounds; size=[b-a+1 for a,b in zip(lo,hi)]
    assert max(size)<=64
    air=n.Compound({'name':n.String('minecraft:air'),'states':n.Compound(),'version':n.Int(18168865)})
    palette=[air]; lookup={air.snbt():0}; indices=[[],[]]; excluded=Counter(); counts=Counter()
    for x in range(lo[0],hi[0]+1):
        for y in range(lo[1],hi[1]+1):
            for z in range(lo[2],hi[2]+1):
                layers=world.blocks(x,y,z)
                for layer in range(2):
                    if layer>=len(layers): indices[layer].append(0 if layer==0 else -1); continue
                    block=layers[layer]; name=str(block['name'])
                    if name in EXECUTABLE: excluded[name]+=1; block=air; name='minecraft:air'
                    if layer==0: counts[name]+=1
                    key=block.snbt()
                    if key not in lookup:lookup[key]=len(palette);palette.append(n.Compound(block))
                    indices[layer].append(lookup[key])
    positions=n.Compound()
    for cx in range(lo[0]//16,hi[0]//16+1):
        for cz in range(lo[2]//16,hi[2]//16+1):
            stream=BytesIO(world.get(struct.pack('<ii',cx,cz)+b'1') or b'')
            while stream.tell()<len(stream.getbuffer()):
                tile=n.File.parse(stream,byteorder='little'); pos=[int(tile[k]) for k in ('x','y','z')]
                if not all(a<=v<=b for a,v,b in zip(lo,pos,hi)):continue
                if str(tile.get('id')) not in ('Sign','HangingSign','Banner','Skull','FlowerPot'):continue
                local=[v-a for v,a in zip(pos,lo)]; index=local[0]*size[1]*size[2]+local[1]*size[2]+local[2]
                for k,v in zip(('x','y','z'),local):tile[k]=n.Int(v)
                for value in tile.values():
                    if isinstance(value,n.Compound): value.pop('TextOwner',None)
                positions[str(index)]=n.Compound({'block_entity_data':n.Compound(tile)})
    output=n.File({'format_version':n.Int(1),'size':n.List[n.Int](size),
                   'structure_world_origin':n.List[n.Int]([0,0,0]),
                   'structure':n.Compound({'block_indices':n.List[n.List[n.Int]]([n.List[n.Int](v) for v in indices]),
                   'entities':n.List[n.Compound](), 'palette':n.Compound({'default':n.Compound({
                   'block_palette':n.List[n.Compound](palette),'block_position_data':positions})})})})
    path.parent.mkdir(parents=True,exist_ok=True);output.save(path,byteorder='little')
    return {'source_bounds':bounds,'size':size,'palette':len(palette),'decorations':len(positions),
            'excluded':dict(excluded),'blocks':dict(counts),'sha256':sha(path),'bytes':path.stat().st_size}


def tiles(bounds):
    lo,hi=bounds
    for x in range(lo[0],hi[0]+1,64):
        for y in range(lo[1],hi[1]+1,64):
            for z in range(lo[2],hi[2]+1,64):
                yield [[x,y,z],[min(x+63,hi[0]),min(y+63,hi[1]),min(z+63,hi[2])]]


def make_layouts(args):
    catalog=json.loads((ROOT/'scripts/minecraft/junket_games.json').read_text('utf-8'))
    if sha(args.source)!=catalog['source']['sha256']:raise ValueError('Source ZIP differs from reviewed 1.09 archive')
    with ZipFile(args.source) as original:
        for relative in ('resources.zip','region/r.0.0.mca','level.dat'):
            archived=original.read('junket_craft_worlddata_ver1.09/'+relative)
            if hashlib.sha256(archived).hexdigest()!=sha(args.source_world/relative):
                raise ValueError('Extracted source differs from ZIP: '+relative)
    function_root=args.source_world/'datapacks/test/data/game/function'
    games=[]
    for index,game in enumerate(catalog['games']):
        gid=game['id']; bounds=private_value("all17.bounds")[gid]; origin=[20000+96*index,65,20000]
        offset=[p-q for p,q in zip(origin,bounds[0])]
        summons,unresolved=source_summons(function_root/gid/'prepare.mcfunction')
        seats=sorted((row for row in summons if any(re.fullmatch(r'register-\d+',tag) for tag in row['tags'])),
                     key=lambda row:next(int(t.split('-')[1]) for t in row['tags'] if re.fullmatch(r'register-\d+',t)))
        source_seats=[r['location'] for r in seats]
        games.append({'id':gid,'title':game['title'],'source_bounds':bounds,
          'size':[b-a+1 for a,b in zip(*bounds)],'source_arrival':game['source_arrival'],
          'source_seats':source_seats,'production_origin':origin,'translation':offset,
          'source_safe_arrival':private_value("all17.safe_arrivals").get(gid,game['source_arrival']),
          'destination':[p+q for p,q in zip(private_value("all17.safe_arrivals").get(gid,game['source_arrival']),offset)],'rotation':game['rotation'],
          'seats':[[p+q for p,q in zip(v,offset)] for v in source_seats],
          'summons':summons,'unresolved_relative_summon_lines':unresolved,
          'source_function':str(function_root/gid/'prepare.mcfunction'),
          'source_function_sha256':sha(function_root/gid/'prepare.mcfunction')})
        if gid=='hope':
            # Outside the moving 16-pad board, on the source ground between rooms.
            games[-1]['source_reception']=private_value("all17.hope_reception")
            games[-1]['source_return']=private_value("all17.hope_return")
    result={'schema':1,'distribution':'private-local-only','source_zip':str(args.source),
            'source_sha256':sha(args.source),'production_plan':'WORLD_ORIGIN=(20000,65,20000), SPACING=96; existing catalog order',
            'existing_conversion':str(args.converted),'supplemental_conversion':str(args.supplement),
            'barriers':'preserved; source stage collision floors and invisible safety boundaries',
            'games':games}
    write_json(args.output/'source-layouts.json',result)
    return result


def extract_rooms(args,layouts):
    world=CompositeWorld(args.converted,args.supplement)
    try:
        for game in layouts['games']:
            game['structures']=[]; total=Counter()
            for index,bounds in enumerate(tiles(game['source_bounds'])):
                name=f"{game['id']}_original_{index}"
                path=args.output/'assets/ichiyon_junket_original_bp/structures/junket'/f'{name}.mcstructure'
                report=export_structure(world,bounds,path)
                report.update({'identifier':f'junket:{name}','path':str(path),
                              'origin':[a+b for a,b in zip(bounds[0],game['translation'])]})
                game['structures'].append(report);total.update(report['blocks'])
            # Sample source solidity for warp destinations and original register seats.
            game['arrival_blocks']={str(dy):plain((world.blocks(*[int(math.floor(v))+(dy if axis==1 else 0)
                    for axis,v in enumerate(game['source_arrival'])]) or [{}])[0]) for dy in (-1,0,1)}
            game['safe_arrival_probes']=[]
            x,y,z=game['source_safe_arrival']
            for px in sorted({math.floor(x-.31),math.floor(x+.31)}):
                for pz in sorted({math.floor(z-.31),math.floor(z+.31)}):
                    for py in sorted({math.floor(y)-1,math.floor(y),math.floor(y+1.8)}):
                        block=(world.blocks(px,py,pz) or [{}])[0];name=str(block.get('name','minecraft:air'))
                        passable=name=='minecraft:air' or 'light_block' in name
                        if py>=math.floor(y) and not passable:raise ValueError(f'{game["id"]}: blocked arrival at {[px,py,pz]} {name}')
                        if py<math.floor(y) and passable:raise ValueError(f'{game["id"]}: unsupported arrival at {[px,py,pz]} {name}')
                        game['safe_arrival_probes'].append({'source':[px,py,pz],'location':[a+b for a,b in zip([px,py,pz],game['translation'])],'block':plain(block)})
            game['block_counts']=dict(total)
            game['solid_blocks']=sum(v for k,v in total.items() if k!='minecraft:air' and 'light_block' not in k)
            if game['id'] in ('clock','walker'):
                template_bounds=private_value("all17.floor_templates")[game["id"]]
                source_cells=[]
                for x in range(template_bounds[0][0],template_bounds[1][0]+1):
                    for z in range(template_bounds[0][2],template_bounds[1][2]+1):
                        y=template_bounds[0][1]; block=(world.blocks(x,y,z) or [{}])[0]
                        source_cells.append({'location':[x,y,z],'block':plain(block)})
                game['floor_template']={'bounds':template_bounds,'cells':source_cells,
                                        'source_target_offset':[0,22,0] if game['id']=='clock' else [0,20,2]}
            print(f"{game['id']}: {len(game['structures'])} source tiles, {game['solid_blocks']} solid blocks",flush=True)
    finally:world.close()
    write_json(args.output/'source-layouts.json',layouts)


def atlas_models(resources, vanilla, models):
    from junket_model_geometry import texture_atlas
    return texture_atlas(resources, vanilla, models)


def quaternion_euler(q):
    x,y,z,w=q
    length=math.sqrt(sum(v*v for v in q))
    if not length:return [0,0,0]
    x,y,z,w=[v/length for v in q]
    return [math.degrees(math.atan2(2*(w*x+y*z),1-2*(x*x+y*y))),
            math.degrees(math.asin(max(-1,min(1,2*(w*y-z*x))))),
            math.degrees(math.atan2(2*(w*z+x*y),1-2*(y*y+z*z)))]


def source_entity(bp,rp,key,geo,texture):
    identifier='junket_original:'+key
    write_json(bp/f'entities/{key}.json',{'format_version':'1.21.0','minecraft:entity':{
       'description':{'identifier':identifier,'is_spawnable':False,'is_summonable':True},
       'components':{'minecraft:physics':{'has_gravity':False,'has_collision':False},
       'minecraft:collision_box':{'width':.6,'height':.6},'minecraft:persistent':{},
       'minecraft:pushable':{'is_pushable':False,'is_pushable_by_piston':False},
       'minecraft:damage_sensor':{'triggers':[{'cause':'all','deals_damage':'no'}]},
       'minecraft:nameable':{'always_show':True,'allow_name_tag_renaming':False},
       'minecraft:interact':{'interactions':[{'on_interact':{'event':'junket:touch','target':'self'},
          'interact_text':'action.interact.junket'}]}},'events':{'junket:touch':{}}}})
    write_json(rp/f'entity/{key}.entity.json',{'format_version':'1.10.0','minecraft:client_entity':{'description':{
       'identifier':identifier,'materials':{'default':'entity_alphatest'},'textures':{'default':texture},
       'geometry':{'default':geo},'render_controllers':['controller.render.junket_original']}}})
    return identifier


def extract_models(args,layouts):
    bp=args.output/'assets/ichiyon_junket_original_bp';rp=args.output/'assets/ichiyon_junket_original_rp'
    report={'models':{},'displays':{},'surface_repairs':{},'atlases':{},'limitations':[]}
    with ZipFile(args.source_world/'resources.zip') as resources, ZipFile(args.vanilla) as vanilla:
        all_names=resources.namelist()
        for game in layouts['games']:
            gid=game['id']; group=MODEL_GROUP.get(gid,gid);prefix=f'assets/minecraft/models/item/game/{group}/'
            models={Path(name).stem:json.loads(resources.read(name)) for name in all_names if name.startswith(prefix) and name.endswith('.json')}
            sheet,atlas=atlas_models(resources,vanilla,models)
            report['atlases'][gid]={'size':list(sheet.size),'padding':2,'textures':atlas}
            (rp/'textures/entity').mkdir(parents=True,exist_ok=True)
            sheet.save(rp/f'textures/entity/original_{gid}.png')
            geos=[];mapping={}; transformed={}; displays=[]
            for name,model in models.items():
                key=re.sub('[^a-z0-9_]','_',gid+'_'+name)
                repairs=[]
                geo=geometry(model,'geometry.junket_original.'+key,atlas,(1024,1024),report=repairs)
                report['surface_repairs'][gid+'/'+name]=repairs
                geos.append(geo)
                mapping[name]={'entity':source_entity(bp,rp,key,geo['description']['identifier'],f'textures/entity/original_{gid}'),
                               'geometry':geo['description']['identifier'],'elements':len(model['elements'])}
            for row in game['summons']:
                if row['type']!='item_display' or not row['model'] or row['item'] in ('minecraft:air','air'):continue
                if not row['model'].startswith(f'game/{group}/'):continue
                model_name=row['model'].rsplit('/',1)[1]
                if model_name not in models:continue
                transform=row['transformation'];cache_key=json.dumps([model_name,transform,row['rotation'][1]],sort_keys=True)
                if cache_key not in transformed:
                    key=f'{gid}_display_{len(transformed)}';geo=geometry(models[model_name],'geometry.junket_original.'+key,atlas,(1024,1024))
                    scale=transform.get('scale',[1,1,1]);translate=transform.get('translation',[0,0,0])
                    for bone in geo['bones']:
                        for cube in bone.get('cubes',[]):
                            cube['origin']=[v*s+t*16 for v,s,t in zip(cube['origin'],scale,[-translate[0],translate[1],translate[2]])]
                            cube['size']=[v*s for v,s in zip(cube['size'],scale)]
                        if 'pivot' in bone:bone['pivot']=[v*s+t*16 for v,s,t in zip(bone['pivot'],scale,[-translate[0],translate[1],translate[2]])]
                        bone['parent']='display_root'
                    # Convert Java display quaternion rotations into a Bedrock root bone.
                    left=quaternion_euler(transform.get('left_rotation',[0,0,0,1]));right=quaternion_euler(transform.get('right_rotation',[0,0,0,1]))
                    rotation=[left[0]+right[0]+row['rotation'][1],-(left[1]+right[1]),-(left[2]+right[2])]
                    geo['bones'].insert(0,{'name':'display_root','pivot':[0,0,0],'rotation':rotation})
                    geo['description'].update(visible_bounds_width=64,visible_bounds_height=64,visible_bounds_offset=[0,8,0])
                    geos.append(geo);transformed[cache_key]=source_entity(bp,rp,key,geo['description']['identifier'],f'textures/entity/original_{gid}')
                displays.append({'role':f"original_display_{row['line']}",'type':transformed[cache_key],
                   'location':dict(zip('xyz',[p+q for p,q in zip(row['location'],game['translation'])])),
                   'rotation':{'x':0,'y':row['rotation'][0]},'name':'','tags':['junket:original_decoration',f'junket:{gid}:source_display'],
                   'properties':{},'source_tags':row['tags'],'source_model':row['model'],'source_line':row['line']})
            write_json(rp/f'models/entity/original_{gid}.geo.json',{'format_version':'1.21.0','minecraft:geometry':geos})
            report['models'][gid]=mapping;report['displays'][gid]=displays
            game['source_display_fixtures']=displays
            print(f'{gid}: {len(mapping)} original models, {len(displays)} resolved displays',flush=True)
    write_json(rp/'render_controllers/original.json',{'format_version':'1.8.0','render_controllers':{'controller.render.junket_original':{
       'geometry':'Geometry.default','materials':[{'*':'Material.default'}],'textures':['Texture.default']}}})
    report['limitations']+=['Java entity AI/animated Java rigs are not imported.',
       'Relative summons anchored to runtime entities require game-specific runtime fixtures.',
       'Composite display quaternion/nonuniform rotations require client visual verification.']
    write_json(args.output/'source-models.json',report);write_json(args.output/'source-layouts.json',layouts)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--source-world',type=Path,default=OLD_PRIVATE/'source-ver1.09/junket_craft_worlddata_ver1.09')
    parser.add_argument('--converted',type=Path,default=OLD_PRIVATE/'conversion/bedrock-four-games')
    parser.add_argument('--supplement',type=Path,default=OUTPUT/'bedrock-supplement')
    parser.add_argument('--vanilla',type=Path,default=OLD_PRIVATE/'tools/java-1.21.3-client.jar')
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--metadata-only',action='store_true')
    parser.add_argument('--models-only',action='store_true')
    args=parser.parse_args();args.output=args.output.resolve()
    if not args.output.is_relative_to(ROOT/'data/junket-private'):raise ValueError('Private output required')
    layouts=make_layouts(args) if not args.models_only else json.loads((args.output/'source-layouts.json').read_text('utf-8'))
    if args.metadata_only:return
    if not args.models_only:
        ensure_supplement(args)
        extract_rooms(args,layouts)
    extract_models(args,layouts)


if __name__=='__main__':main()
