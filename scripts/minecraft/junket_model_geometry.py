"""Source-only Java model surface conversion; no copyrighted artwork included.

The coordinate/UV conventions match Blockbench's Java and Bedrock codecs:
https://github.com/JannisX11/blockbench/blob/master/js/formats/bedrock/bedrock.js
The X coordinate is reflected, face *keys* are retained, and both UV axes of
up/down faces are inverted. A face omitted from Bedrock UV is not rendered.
"""
from copy import deepcopy
import json
import math
from io import BytesIO

AXIS = {'west': 0, 'east': 0, 'down': 1, 'up': 1, 'north': 2, 'south': 2}
POSITIVE = {'east', 'up', 'south'}
UNDEFINED = '__junket_missing__'
PLANE_THICKNESS = .0001  # model pixels, not blocks; avoids degenerate cube primitives
REPEAT = '#junket_repeat='


def texture_atlas(resources, vanilla, models, size=(1024, 1024), padding=2):
    """Pack only resolved artwork, with extruded borders for filtered sampling."""
    from PIL import Image
    textures = sorted({resolved for raw in models.values() for model in [clean_model(raw)] for element in model['elements']
                       for face in element['faces'].values()
                       if (resolved := resolve_texture(model, face['texture'])) is not None})
    sheet = Image.new('RGBA', size)
    atlas = {}
    x = y = row = 0
    for texture in textures:
        original, _, repeated = texture.partition(REPEAT)
        name = 'assets/minecraft/textures/' + original.removeprefix('minecraft:') + '.png'
        archive = resources if name in resources.namelist() else vanilla
        picture = Image.open(BytesIO(archive.read(name))).convert('RGBA')
        if picture.height > picture.width and name + '.mcmeta' in archive.namelist():
            picture = picture.crop((0, 0, picture.width, picture.width))
        if repeated:
            left, top, columns, rows = map(int, repeated.split(','))
            tile = picture
            picture = Image.new('RGBA', (tile.width * columns, tile.height * rows))
            for column in range(columns):
                for row_index in range(rows):
                    picture.paste(tile, (column * tile.width, row_index * tile.height))
        if x + picture.width + 2 * padding > size[0]:
            x = 0
            y += row
            row = 0
        if picture.width + 2 * padding > size[0] or y + picture.height + 2 * padding > size[1]:
            raise ValueError('Original model atlas overflow: ' + texture)
        sheet.paste(picture, (x + padding, y + padding))
        atlas[texture] = (x + padding, y + padding, picture.width, picture.height)
        for dx in range(-padding, picture.width + padding):
            for dy in range(-padding, picture.height + padding):
                if 0 <= dx < picture.width and 0 <= dy < picture.height:
                    continue
                sheet.putpixel((x + padding + dx, y + padding + dy), picture.getpixel((
                    min(picture.width - 1, max(0, dx)), min(picture.height - 1, max(0, dy)))))
        x += picture.width + 2 * padding
        row = max(row, picture.height + 2 * padding)
    return sheet, atlas


def resolve_texture(model, value):
    visited = set()
    while value.startswith('#'):
        if value in visited:
            raise ValueError('Cyclic model texture alias: ' + value)
        visited.add(value)
        value = model.get('textures', {}).get(value[1:])
        if value is None:
            return None
    return None if value == UNDEFINED else value


def clean_model(model, report=None):
    """Omit invalid artwork/zero-area faces and identical duplicate surfaces.

    Different authored materials on a coincident surface are retained in source
    order with a sub-pixel separation. This also preserves transparent overlays;
    arbitrarily dropping one of two different textures would lose source art.
    """
    model = deepcopy(model)
    events = report if report is not None else []
    seen = {}
    extra = []
    for i, element in enumerate(model.get('elements', [])):
        a, b = element['from'], element['to']
        size = [b[j] - a[j] for j in range(3)]
        if any(v < 0 for v in size):
            raise ValueError(f'Negative source cube extent: element {i}')
        for face, data in list(element.get('faces', {}).items()):
            axis = AXIS[face]
            area = math.prod(size[j] for j in range(3) if j != axis)
            texture = resolve_texture(model, data['texture'])
            reason = 'zero_area' if area == 0 else 'undefined_texture' if texture is None else None
            if reason:
                del element['faces'][face]
                events.append({'element': i, 'face': face, 'action': 'omit', 'reason': reason})
                continue
            data['texture'] = texture
            uv = data.get('uv')
            if uv is None:
                # Java's default block-face UV projection.
                uv = {'down': [a[0], 16-b[2], b[0], 16-a[2]],
                      'up': [a[0], a[2], b[0], b[2]],
                      'north': [16-b[0], 16-b[1], 16-a[0], 16-a[1]],
                      'south': [a[0], 16-b[1], b[0], 16-a[1]],
                      'west': [a[2], 16-b[1], b[2], 16-a[1]],
                      'east': [16-b[2], 16-b[1], 16-a[2], 16-a[1]]}[face]
            if any(v < 0 or v > 16 for v in uv):
                # Bake an expanded repeat region; never clamp away source UV
                # coverage or let a face sample another model's atlas tile.
                left, top = math.floor(min(uv[0], uv[2])/16), math.floor(min(uv[1], uv[3])/16)
                columns = max(1, math.ceil(max(uv[0], uv[2])/16)-left)
                rows = max(1, math.ceil(max(uv[1], uv[3])/16)-top)
                texture += REPEAT + ','.join(map(str, [left, top, columns, rows]))
                data['texture'] = texture
                events.append({'element': i, 'face': face, 'action': 'repeat_uv', 'source_uv': uv,
                               'repeat_region': [left, top, columns, rows]})
                uv = [(uv[0]-16*left)/columns, (uv[1]-16*top)/rows,
                      (uv[2]-16*left)/columns, (uv[3]-16*top)/rows]
            data['uv'] = uv
            key = json.dumps([face, b[axis] if face in POSITIVE else a[axis],
                              [(a[j], b[j]) for j in range(3) if j != axis],
                              element.get('rotation')], sort_keys=True)
            material = json.dumps([texture, uv, data.get('rotation', 0)])
            prior = seen.setdefault(key, [])
            if material in prior:
                del element['faces'][face]
                events.append({'element': i, 'face': face, 'action': 'omit', 'reason': 'duplicate_surface'})
                continue
            if prior:
                overlay = deepcopy(element)
                overlay['faces'] = {face: data}
                offset = len(prior) * .008 * (1 if face in POSITIVE else -1)
                overlay['from'][axis] += offset
                overlay['to'][axis] += offset
                extra.append(overlay)
                del element['faces'][face]
                events.append({'element': i, 'face': face, 'action': 'separate_coplanar', 'offset_pixels': offset})
            prior.append(material)
    model['elements'] = [e for e in model.get('elements', []) + extra if e.get('faces')]
    return model


def geometry(model, identifier, atlas, texture_size=(256, 128), report=None):
    model = clean_model(model, report)
    bones = []
    for i, element in enumerate(model['elements']):
        a, b = list(element['from']), list(element['to'])
        zero_axes = [j for j in range(3) if a[j] == b[j]]
        # Preserve the centre of a double-sided authored sheet, but give the
        # cube valid nonzero extents. Only its authored area faces are emitted.
        for j in zero_axes:
            a[j] -= PLANE_THICKNESS / 2
            b[j] += PLANE_THICKNESS / 2
        cube = {'origin': [8-b[0], a[1]-8, a[2]-8],
                'size': [b[j]-a[j] for j in range(3)], 'uv': {}}
        for face, data in element['faces'].items():
            texture = data['texture']
            if texture not in atlas:
                raise ValueError(f'Missing atlas texture: {identifier} {texture}')
            x, y, width, height = atlas[texture]
            u, v, u2, v2 = data['uv']
            if face in ('up', 'down'):
                u, v, u2, v2 = u2, v2, u, v
            uv = {'uv': [x+u*width/16, y+v*height/16],
                  'uv_size': [(u2-u)*width/16, (v2-v)*height/16]}
            if data.get('rotation'):
                uv['uv_rotation'] = data['rotation']
            cube['uv'][face] = uv
        bone = {'name': f'element_{i}', 'cubes': [cube]}
        rotation = element.get('rotation')
        if rotation and rotation['angle']:
            if rotation.get('rescale'):
                raise ValueError('Rescaled rotations require explicit conversion')
            origin = rotation['origin']
            bone['pivot'] = [8-origin[0], origin[1]-8, origin[2]-8]
            bone['rotation'] = [0, 0, 0]
            axis = 'xyz'.index(rotation['axis'])
            bone['rotation'][axis] = rotation['angle'] * (-1 if axis < 2 else 1)
        bones.append(bone)
    return {'description': {'identifier': identifier, 'texture_width': texture_size[0],
                            'texture_height': texture_size[1], 'visible_bounds_width': 8,
                            'visible_bounds_height': 8, 'visible_bounds_offset': [0, 0, 0]},
            'bones': bones}
