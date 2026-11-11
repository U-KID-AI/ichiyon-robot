"""Reusable private-model metadata adapters; no original coordinate recipes."""
import copy
import math
import re
from zipfile import ZipFile


def point(value):
    return dict(zip('xyz', value))

SOURCE_PROP_MODELS = {
    'junket:temperance_balance': 'game/temperance/balance',
    'junket:temperance_base': 'game/temperance/base',
    'junket:temperance_frame': 'game/temperance/frame',
    'junket:temperance_stone': 'game/temperance/stone',
    'junket:temperance_tv': 'game/temperance/tv',
    'junket:lucy_box': 'game/lucy/box',
    'junket:lucy_tv': 'game/lucy/tv',
    'junket:silence_covering': 'game/sos/covering',
    'junket:silence_tv': 'game/sos/tv',
    'junket:jinn_covering': 'game/jinn/covering',
    'junket:jinn_lever': 'game/jinn/lever',
    'junket:hope_code': 'game/hope/code',
}


def loading_probes(layout):
    """Select real source blocks inside every chunk, away from its boundary.

    Native placement checks every voxel separately. These points only establish
    Script API chunk availability and never authorize a building replacement.
    """
    import nbtlib
    best = {}
    floor_y = math.floor(layout['destination'][1]) - 1
    for tile in layout.get('structures', []):
        root = nbtlib.load(tile['path'], gzipped=False, byteorder='little')
        sx, sy, sz = map(int, root['size'])
        palette = root['structure']['palette']['default']['block_palette']
        names = [str(value['name']) for value in palette]
        for index, raw in enumerate(root['structure']['block_indices'][0]):
            value = int(raw)
            if value < 0 or names[value] in {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}:
                continue
            x = tile['origin'][0] + index // (sy * sz)
            y = tile['origin'][1] + (index // sz) % sy
            z = tile['origin'][2] + index % sz
            key = (x // 16, z // 16)
            name = names[value]
            # Prefer solid source floor near the chunk center, not its exact
            # x/z border. Keep several points to handle changed decorations.
            rank = (name in {'minecraft:water', 'minecraft:lava'},
                    abs(x % 16 - 8) + abs(z % 16 - 8), abs(y - floor_y), y)
            candidate = (rank, {'location': point([x, y, z]), 'block': name})
            candidates = best.setdefault(key, [])
            if len(candidates) < 3 or rank < candidates[-1][0]:
                candidates.append(candidate)
                candidates.sort(key=lambda item: item[0])
                del candidates[3:]
    return [{'chunk': list(key), 'candidates': [item[1] for item in candidates]}
            for key, candidates in sorted(best.items())]


def resolved_summons(archive, game, absolute):
    """Resolve source-local ^/~ anchors; no Java function is ever executed."""
    path = next(p for p in archive.namelist() if p.endswith(f'/game/function/{game}/prepare.mcfunction'))
    result = copy.deepcopy(absolute)
    def offset(tokens, at, yaw):
        values = [float(t[1:] or 0) if t[:1] in '^~' else float(t) for t in tokens]
        if all(t.startswith('^') for t in tokens):
            a = math.radians(yaw)
            return [at[0] + math.cos(a) * values[0] - math.sin(a) * values[2],
                    at[1] + values[1], at[2] + math.sin(a) * values[0] + math.cos(a) * values[2]]
        return [at[i] + v if tokens[i].startswith('~') else v for i, v in enumerate(values)]
    for line_number, line in enumerate(archive.read(path).decode('utf8').splitlines(), 1):
        if line.startswith('#') or ' run summon ' not in line:
            continue
        match = re.search(r' summon (\S+) (\S+) (\S+) (\S+) (\{.*)', line)
        if not match:
            continue
        entity, x, y, z, data = match.groups()
        if not any(t.startswith(('^', '~')) for t in (x, y, z)):
            continue
        before = line[:match.start()]
        wanted = re.findall(r'tag=([^,\]]+)', before)
        anchors = [a for a in result if all(t in a['tags'] for t in wanted)] if wanted else []
        positioned = re.search(r'positioned ([\d.~-]+|\^[\d.-]*) ([\d.~-]+|\^[\d.-]*) ([\d.~-]+|\^[\d.-]*)', before)
        if not anchors and positioned and not any(t.startswith(('~', '^')) for t in positioned.groups()):
            anchors = [{'location': [0, 0, 0], 'rotation': [0, 0], 'tags': []}]
        for anchor in anchors[:16]:
            at = anchor['location']; yaw = (anchor.get('rotation') or [0])[0]
            rotate = re.search(r'rotated (~?[\d.-]*) (?:~?[\d.-]*)', before)
            if rotate:
                token = rotate[1]; yaw = yaw + float(token[1:] or 0) if token.startswith('~') else float(token)
            try:
                if positioned:
                    at = offset(positioned.groups(), at, yaw)
                location = offset((x, y, z), at, yaw)
            except ValueError:
                continue
            tags = re.search(r'Tags:\[([^\]]*)\]', data)
            model = re.search(r'"minecraft:item_model":"([^"]+)"', data)
            rotation = re.search(r'Rotation:\[([\d.-]+)[fF]?,([\d.-]+)[fF]?\]', data)
            width = re.search(r'\bwidth:([\d.-]+)', data)
            height = re.search(r'\bheight:([\d.-]+)', data)
            result.append({'type': entity, 'line': line_number, 'location': location,
                           'tags': re.findall(r'"([^"]+)"', tags[1]) if tags else [],
                           'model': model[1] if model else None,
                           'rotation': [float(v) for v in rotation.groups()] if rotation else [0, 0],
                           'width': float(width[1]) if width else None,
                           'height': float(height[1]) if height else None})
    return result


def source_fixture_model(game, action):
    if game == 'ace':
        return 'game/ace/dice-a' if '_die_' in action or '_tray_' in action else 'game/ace/tv'
    if game == 'grail':
        return 'game/grail/grail-fake' if '_cup_' in action else 'game/grail/potion-ho'
    if game == 'auction':
        return f'game/auction/sign-{action.rsplit("_", 1)[-1]}'
    if game == 'fire':
        shape, heat, _ = action.split('_')
        return f'game/fire/card-{shape}-{heat}'
    if game == 'pig':
        return 'game/pig/card-' + {'1': 'scrow', '2': 'wood', '3': 'bricks', '4': 'wolf'}.get(action[-1], 'wolf')
    if game == 'light': return 'game/light/hole-guard'
    if game == 'mine': return 'game/mine/stone-open'
    if game == 'garden': return {'switch': 'game/garden/change', 'double': 'game/garden/double', 'open': 'game/garden/open'}.get(action, 'game/garden/jewel-1')
    if game == 'hope': return 'game/hope/floor-random'
    if game == 'rabbit': return 'game/rabbit/floor-normal' if action.startswith('floor_') else f'game/rabbit/guard-{action[-1]}'
    if game == 'walker': return 'game/walker/'+('walker-stop' if action.startswith('walker_') else
        'bomb-remove' if 'prev' in action else 'bomb-add' if 'next' in action else 'bomb-determine')
    if game == 'clock': return 'game/clock/short'
    return None


def protect_fixture_npcs(venue):
    """Protect every fixed guide, including game-specific progression guards."""
    for fixture in venue['fixtures']:
        if fixture['type'] == 'minecraft:npc':
            fixture['type'] = 'junket:guide'
            fixture['frame_property'] = 'none'
            fixture['properties'] = {}


def separate_hope_floor_interactions(venue):
    """Keep the original 4x4 floor visible and its existing click anchor inert."""
    if venue['id'] != 'hope':
        return
    displays = [f for f in venue['fixtures'] if f['role'].startswith('original_display_')
                and 'hope-floor' in f.get('source_tags', [])]
    controls = [f for f in venue['fixtures'] if re.fullmatch(r'control_(?:[1-9]|1[0-6])', f['role'])]
    if len(displays) != 16 or len(controls) != 16:
        raise ValueError('Hope needs all 16 original floors and existing interaction anchors')
    used = set()
    for fixture in controls:
        visual = min(displays, key=lambda d: math.dist(fixture['location'], d['location']))
        if visual['role'] in used:
            raise ValueError('Hope original floor to interaction mapping is not one-to-one')
        used.add(visual['role'])
        fixture['type'] = 'junket:interaction_anchor'
        fixture['frame_property'] = 'none'
        fixture['render_role'] = 'interaction-only'
        fixture['visual_source_role'] = visual['role']


def include_return_tag(fixture):
    """Tag order is not semantic, but generated artifacts must be reproducible."""
    fixture['tags'] = sorted(set(fixture.get('tags', [])) | {'junket:return'})


def apply_source_models(venue,models,layout):
    """Use original visuals while preserving role/action ids used by logic."""
    game=venue['id']; unresolved=[]
    for fixture in venue['fixtures']:
        source_model=fixture.get('source_model') or SOURCE_PROP_MODELS.get(fixture['type'])
        if not source_model:continue
        fixture['source_model']=source_model
        _, group, name=source_model.split('/',2)
        group={'sos':'silence'}.get(group,group)
        model=models['models'].get(group,{}).get(name)
        if model:
            fixture['type']=model['entity'];fixture['frame_property']='none'
            fixture.pop('frame_map',None)
            # A base model omits the source item_display's scale/translation/
            # pitch. Reuse the already converted display for this exact source
            # anchor; a similarly named prop elsewhere is not sufficient.
            displays=[display for display in models.get('displays',{}).get(game,[])
                      if display.get('source_model') == source_model and (
                          fixture.get('source_tag') in display.get('source_tags',[])
                          or (fixture.get('source_display_line') is not None
                              and fixture['source_display_line'] == display.get('source_line')))]
            if len(displays)==1:
                display=displays[0]
                fixture['type']=display['type']
                fixture['rotation']=display.get('rotation',{}).get('y',0)
                fixture['source_display_line']=display['source_line']
                fixture['source_display_transform_applied']=True
                source=next((s for s in layout.get('summons',[]) if s.get('line')==display['source_line']),{})
                fixture['source_rotation']=source.get('rotation',[fixture['rotation'],0])
                fixture['source_transformation']=copy.deepcopy(source.get('transformation',{}))
        else:unresolved.append(source_model)
    # Generic status slots previously cycled over the same original TV and
    # stacked duplicate meshes at y + 0.35. The source display decorations below
    # already contain each original anchor once. Status text now lives in the
    # game UI; discard only slots with an exact, in-bounds source counterpart.
    source_displays=models.get('displays',{}).get(game,[])
    def redundant_status(fixture):
        if not fixture['role'].startswith('display_') or fixture.get('source_display_line') is None:
            return False
        return any(display.get('source_line')==fixture['source_display_line']
                   and display.get('source_model')==fixture.get('source_model')
                   and all(venue['origin'][i] <= display['location'][axis] < venue['origin'][i]+venue['size'][i]
                           for i,axis in enumerate('xyz')) for display in source_displays)
    venue['fixtures']=[fixture for fixture in venue['fixtures'] if not redundant_status(fixture)]
    occupied_tags=set()
    for fixture in venue['fixtures']:
        if fixture.get('source_tag'):occupied_tags.add(fixture['source_tag'])
    # Keep dynamic Sparrow objects managed by its dedicated visual controller.
    if game=='sparrow':occupied_tags.update(['sparrow-box-big','sparrow-box-little','sparrow-tv-main'])
    for decoration in models.get('displays',{}).get(game,[]):
        if not all(venue['origin'][i] <= decoration['location'][axis] < venue['origin'][i]+venue['size'][i]
                   for i,axis in enumerate('xyz')):continue
        tags=decoration.get('source_tags',[])
        if any(t in occupied_tags for t in tags):continue
        if game=='rabbit' and any('floor-mark' in t for t in tags):continue
        # The dynamic floor control is the original floor model itself, so a
        # second fixed display must not remain after the floor falls away.
        if game=='rabbit' and 'rabbit-floor' in tags:continue
        fixture=copy.deepcopy(decoration)
        fixture['location']=[fixture['location'][a] for a in 'xyz']
        fixture['rotation']=fixture.get('rotation',{}).get('y',0)
        fixture['action']='display';fixture['frame_property']='none'
        venue['fixtures'].append(fixture)
    if unresolved:venue['unresolved_source_models']=sorted(set(unresolved))
