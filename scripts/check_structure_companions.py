"""Check generated meshes against the original NBT, not against golden generated files."""
from __future__ import annotations
import argparse
from collections import Counter
import itertools
import json
from pathlib import Path
from PIL import Image
from build_structure_companions import read_structure, SPECS, SCALE, sha


def expected_half_cells(blocks):
    cells, buttons = {}, []
    for (x,y,z),(name,states) in blocks.items():
        if name.endswith('_button'):
            assert states['facing_direction'] == 4
            buttons.append(((x+.875,y+.375,z+.3125),(x+1,y+.625,z+.6875),name))
            continue
        for hx,hy,hz in itertools.product(range(2), repeat=3):
            filled=True
            if name.endswith('_stairs'):
                d=states['weirdo_direction']; side=[hx==1,hx==0,hz==1,hz==0][d]
                left=[hz==0,hz==1,hx==1,hx==0][d]
                corner=states.get('minecraft:corner','none')
                if corner.startswith('outer'): side=side and (left if corner.endswith('left') else not left)
                elif corner.startswith('inner'): side=side or (left if corner.endswith('left') else not left)
                base=hy==(1 if states['upside_down_bit'] else 0)
                filled=base or side
            if filled: cells[x*2+hx,y*2+hy,z*2+hz]=name
    return cells, sorted(buttons)


def verify(key, source, cache, output):
    RP = output / 'resource_packs/ichiyon_avatar_rp'
    path=source/SPECS[key][0]
    blocks,info=read_structure(path)
    expected,buttons=expected_half_cells(blocks)
    b=info['body_bounds']; cx=sum(b[0])/2; cz=sum(b[2])/2; floor=b[1][0]
    def inverse(p):
        x,y,z=[v/SCALE for v in p]
        return (round((z+cx) if key=='ikarun' else (-z+cx),6),round(y+floor,6),round((-x+cz) if key=='ikarun' else (x+cz),6))
    mesh=json.loads((RP/f'models/entity/{key}.geo.json').read_text())['minecraft:geometry'][0]
    atlas=Image.open(RP/f'textures/entity/{key}.png').convert('RGBA')
    mapping=json.loads((cache/'materials.json').read_text())
    assert atlas.size==(mesh['description']['texture_width'],mesh['description']['texture_height'])
    actual,actual_buttons={},[]; cubes=0; bone_names={bone['name'] for bone in mesh['bones']}
    for bone in mesh['bones']:
        if 'parent' in bone: assert bone['parent'] in bone_names
        for cube in bone.get('cubes',[]):
            assert not cube.get('rotation') and not cube.get('mirror')
            cubes+=1
            a=inverse(cube['origin']); c=inverse([cube['origin'][i]+cube['size'][i] for i in range(3)])
            lo=tuple(min(a[i],c[i]) for i in range(3)); hi=tuple(max(a[i],c[i]) for i in range(3))
            if any(abs(v*2-round(v*2))>1e-5 for v in lo+hi):
                match=[item for item in buttons if item[:2]==(lo,hi)]; assert len(match)==1,(key,lo,hi)
                actual_buttons+=match; name=match[0][2]
            else:
                names=set()
                for p in itertools.product(*(range(round(lo[i]*2),round(hi[i]*2)) for i in range(3))):
                    assert p not in actual,('overlapping generated cuboids',key,p)
                    assert p in expected,('extra voxel outside source',key,p)
                    names.add(expected[p]); actual[p]=True
                assert len(names)==1,('merged different source materials',key,names)
                name=names.pop()
            material=mapping[name.split(':')[-1].removesuffix('_stairs')]
            for face,uv in cube['uv'].items():
                x,y=uv['uv']; w,h=uv['uv_size']
                assert x>=0 and y>=0 and x+w<=atlas.width and y+h<=atlas.height
                tilepath=cache/material[face]; assert sha(tilepath)==material['sha256'][face]
                tile=Image.open(tilepath).convert('RGBA')
                for u,v in [(0,0),(w-1,h-1),(w//2,h//2)]: assert atlas.getpixel((x+u,y+v))==tile.getpixel((u%16,v%16)),(key,name,face)
                # Source +X/+Z rotate into model Z/X, so top face height must follow model Z.
                model_width=cube['size'][2] if face in ('east','west') else cube['size'][0]
                model_height=cube['size'][2] if face in ('up','down') else cube['size'][1]
                assert w==max(1,round(model_width/SCALE*16)) and h==max(1,round(model_height/SCALE*16)),(key,face,w,h)
            if name=='minecraft:honey_block': assert bone['name'].endswith('_translucent')
    assert set(actual)==set(expected),('missing source body cells',len(expected)-len(actual))
    assert sorted(actual_buttons)==buttons
    animation=json.loads((RP/f'animations/{key}.animation.json').read_text())['animations']
    for value in animation.values(): assert set(value['bones']).issubset(bone_names)
    client=json.loads((RP/f'entity/{key}.entity.json').read_text())['minecraft:client_entity']['description']
    assert all(a in animation for a in client['animations'].values())
    assert client['materials']['translucent']=='entity_alphablend'
    return {'source_sha256':info['source_sha256'],'source_body_voxels':len(blocks),'verified_half_voxels':len(actual),
            'verified_buttons':len(buttons),'cubes':cubes,'source_materials':dict(Counter(n for n,_ in blocks.values())),
            'geometry_exact_at_rest':True,'texture_tiles_match':True,'source_unchanged':sha(path)==info['source_sha256']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--vanilla-cache',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    results={key:verify(key,a.source,a.vanilla_cache,a.output) for key in SPECS}
    a.report.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for key,r in results.items(): print(f"PASS {key}: {r['source_body_voxels']} original cells / {r['verified_half_voxels']} shaped subcells / {r['cubes']} cubes, exact rest shape and original material tiles")
    print('Client rendering, animation appearance and mobile interaction remain unverified by this static check.')
