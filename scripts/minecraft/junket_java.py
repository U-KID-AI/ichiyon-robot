"""Read-only Java Anvil access for local, separately licensed JUNKET assets."""
from functools import lru_cache
from io import BytesIO
from zipfile import ZipFile
import zlib
import nbtlib


class JavaWorld:
    def __init__(self, source):
        self.archive = ZipFile(source)
        self.root = next(n[:-9] for n in self.archive.namelist() if n.endswith('/level.dat'))

    @lru_cache(maxsize=8)
    def region(self, rx, rz):
        return self.archive.read(f'{self.root}region/r.{rx}.{rz}.mca')

    @lru_cache(maxsize=256)
    def chunk(self, cx, cz):
        data = self.region(cx // 32, cz // 32)
        index = 4 * (cx % 32 + 32 * (cz % 32))
        offset = int.from_bytes(data[index:index + 3], 'big') * 4096
        if not offset:
            return {}
        length = int.from_bytes(data[offset:offset + 4], 'big')
        assert data[offset + 4] == 2
        return nbtlib.File.parse(BytesIO(zlib.decompress(data[offset + 5:offset + 4 + length])))

    @lru_cache(maxsize=1024)
    def section(self, cx, sy, cz):
        return next((s.get('block_states') for s in self.chunk(cx, cz).get('sections', []) if int(s['Y']) == sy), None)

    def block(self, x, y, z):
        section = self.section(x // 16, y // 16, z // 16)
        if not section:
            return {'Name': 'minecraft:air'}
        palette = section['palette']
        if len(palette) == 1:
            return palette[0]
        bits = max(4, (len(palette) - 1).bit_length())
        per_long = 64 // bits
        index = (y % 16) * 256 + (z % 16) * 16 + x % 16
        entry = (int(section['data'][index // per_long]) >> (index % per_long * bits)) & ((1 << bits) - 1)
        return palette[entry]
