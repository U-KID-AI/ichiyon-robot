"""Export a bounded mcstructure from a PRIVATE Chunker output (not a live world).

Requires nbtlib and amulet-leveldb. Preserves Chunker's block states and safe
decorative block entities; excludes inventories, command blocks and entities.
"""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import struct
import leveldb
import nbtlib as n


class ConvertedWorld:
    def __init__(self, path):
        self.db = leveldb.LevelDB(str(Path(path) / 'db'))

    def get(self, key):
        try:
            return self.db[key]
        except KeyError:
            return None

    @lru_cache(maxsize=128)
    def section(self, cx, sy, cz):
        data = self.get(struct.pack('<ii', cx, cz) + b'/' + struct.pack('b', sy))
        if data is None:
            return []
        stream = BytesIO(data)
        version, layers = stream.read(2)
        assert version in (8, 9), version
        if version == 9:
            assert struct.unpack('b', stream.read(1))[0] == sy
        result = []
        for _ in range(layers):
            header = stream.read(1)[0]
            assert not header & 1, 'Network palette unsupported'
            bits = header >> 1
            if bits:
                count = (4096 + 32 // bits - 1) // (32 // bits)
                words = struct.unpack('<' + 'I' * count, stream.read(count * 4))
                size = struct.unpack('<i', stream.read(4))[0]
            else:
                words, size = (), 1
            palette = [n.File.parse(stream, byteorder='little') for _ in range(size)]
            result.append((bits, words, palette))
        return result

    def blocks(self, x, y, z):
        result = []
        for bits, words, palette in self.section(x // 16, y // 16, z // 16):
            i = (x % 16) * 256 + (z % 16) * 16 + y % 16
            index = (words[i // (32 // bits)] >> ((i % (32 // bits)) * bits)) & ((1 << bits) - 1) if bits else 0
            result.append(palette[index])
        return result

    def export(self, bounds, path):
        lo, hi = bounds
        size = [b - a + 1 for a, b in zip(lo, hi)]
        assert max(size) <= 64
        air = n.Compound({'name': n.String('minecraft:air'), 'states': n.Compound(), 'version': n.Int(18168865)})
        palette, lookup = [air], {air.snbt(): 0}
        indices = [[], []]
        removed = {}
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    blocks = self.blocks(x, y, z)
                    for layer in range(2):
                        if layer >= len(blocks):
                            indices[layer].append(-1 if layer else 0)
                            continue
                        block = blocks[layer]
                        name = str(block['name'])
                        if name in ('minecraft:barrier', 'minecraft:command_block', 'minecraft:repeating_command_block', 'minecraft:chain_command_block', 'minecraft:structure_block', 'minecraft:jigsaw'):
                            removed[name] = removed.get(name, 0) + 1
                            block = air
                        key = block.snbt()
                        if key not in lookup:
                            lookup[key] = len(palette)
                            palette.append(n.Compound(block))
                        indices[layer].append(lookup[key])
        positions = n.Compound()
        for cx in range(lo[0] // 16, hi[0] // 16 + 1):
            for cz in range(lo[2] // 16, hi[2] // 16 + 1):
                stream = BytesIO(self.get(struct.pack('<ii', cx, cz) + b'1') or b'')
                while stream.tell() < len(stream.getbuffer()):
                    tile = n.File.parse(stream, byteorder='little')
                    pos = [int(tile[k]) for k in ('x', 'y', 'z')]
                    if not all(a <= v <= b for a, v, b in zip(lo, pos, hi)):
                        continue
                    if str(tile.get('id')) not in ('Sign', 'HangingSign', 'Banner', 'Skull', 'FlowerPot'):
                        continue
                    local = [v - a for v, a in zip(pos, lo)]
                    index = local[0] * size[1] * size[2] + local[1] * size[2] + local[2]
                    for k, v in zip(('x', 'y', 'z'), local):
                        tile[k] = n.Int(v)
                    # Signs must not carry executable click events.
                    for value in tile.values():
                        if isinstance(value, n.Compound):
                            value.pop('TextOwner', None)
                    positions[str(index)] = n.Compound({'block_entity_data': n.Compound(tile)})
        result = n.File({'format_version': n.Int(1), 'size': n.List[n.Int](size),
                         'structure_world_origin': n.List[n.Int]([0, 0, 0]),
                         'structure': n.Compound({'block_indices': n.List[n.List[n.Int]]([n.List[n.Int](v) for v in indices]),
                            'entities': n.List[n.Compound](), 'palette': n.Compound({'default': n.Compound({
                                'block_palette': n.List[n.Compound](palette), 'block_position_data': positions})})})})
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        result.save(path, byteorder='little')
        return {'source_bounds': bounds, 'size': size, 'palette': len(palette), 'decorations': len(positions), 'excluded': removed, 'bytes': path.stat().st_size}
