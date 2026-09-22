"""Build the reference-faithful Bedrock model and flat-colour atlas, offline.

Voxel geometry is derived directly from the checked-in structure.
Uses only the standard library; never opens world or environment files.
"""
import json
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parent.parent
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"
COLORS = [(250, 210, 40), (37, 29, 24), (231, 57, 48),
          (126, 76, 37), (255, 251, 231), (44, 21, 26)]


REFERENCE = ROOT / "minecraft/behavior_packs/import_structures/structures/pikachu_reference.mcstructure"
SCALE = 0.7
BLOCK_COLORS = {"yellow_wool": 0, "black_wool": 1, "red_wool": 2,
                "brown_wool": 3, "white_wool": 4, "nether_brick_fence": 5}


def reference_blocks():
    """Read the checked-in little-endian NBT asset, never a live world."""
    data = REFERENCE.read_bytes()
    offset = 0

    def number(fmt):
        nonlocal offset
        value = struct.unpack_from("<" + fmt, data, offset)[0]
        offset += struct.calcsize(fmt)
        return value

    def string():
        nonlocal offset
        length = number("H")
        value = data[offset:offset + length].decode("utf-8")
        offset += length
        return value

    def payload(kind):
        if kind in (1, 2, 3, 4, 5, 6):
            return number({1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}[kind])
        if kind == 8:
            return string()
        if kind == 9:
            child = number("B")
            return [payload(child) for _ in range(number("i"))]
        if kind == 10:
            result = {}
            while True:
                child = number("B")
                if child == 0:
                    return result
                name = string()
                result[name] = payload(child)
        if kind in (7, 11, 12):
            return [number({7: "b", 11: "i", 12: "q"}[kind])
                    for _ in range(number("i"))]
        raise ValueError("Unsupported reference NBT tag")

    if number("B") != 10:
        raise ValueError("Expected compound reference")
    string()
    document = payload(10)
    if offset != len(data) or document["size"] != [8, 23, 20]:
        raise ValueError("Unexpected reference layout")
    structure = document["structure"]
    palette = structure["palette"]["default"]["block_palette"]
    indices = structure["block_indices"][0]
    if len(indices) != 8 * 23 * 20:
        raise ValueError("Unexpected block layer size")
    blocks = {}
    for x in range(8):
        for y in range(23):
            for z in range(20):
                index = indices[x * 23 * 20 + y * 20 + z]
                if index == -1:
                    continue
                name = palette[index]["name"].split(":", 1)[1]
                if name != "air":
                    if name not in BLOCK_COLORS:
                        raise ValueError("Unexpected reference block")
                    blocks[x, y, z] = name
    return blocks


def geometry():
    # Reference +X faces model -Z. Preserve all asymmetry, hollow sections,
    # ear/face/arm/back silhouettes; only the two rigid legs are articulated.
    bones = [{"name": "root", "pivot": [0, 0, 0]},
             {"name": "body_root", "parent": "root", "pivot": [0, 4 * SCALE, 0]}]
    for name, z in (("body", 10), ("left_foot", 14), ("right_foot", 5)):
        bones.append({"name": name, "parent": "body_root",
                      "pivot": [(z - 10) * SCALE, 4 * SCALE, 0], "cubes": []})
    by_name = {bone["name"]: bone for bone in bones}

    def cube(bone, x, y, z, dx, dy, dz, color):
        uv = {face: {"uv": [color * 8 + 1, 1], "uv_size": [6, 6]}
              for face in ("north", "south", "east", "west", "up", "down")}
        by_name[bone]["cubes"].append({
            "origin": [round((z - 10) * SCALE, 6), round(y * SCALE, 6),
                       round((4 - x - dx) * SCALE, 6)],
            "size": [round(dz * SCALE, 6), round(dy * SCALE, 6), round(dx * SCALE, 6)],
            "uv": uv})

    blocks = reference_blocks()
    consumed = set()
    for (x, y, z), name in sorted(blocks.items()):
        if (x, y, z) in consumed:
            continue
        bone = "body" if y >= 4 else ("left_foot" if z >= 10 else "right_foot")
        color = BLOCK_COLORS[name]
        if name == "nether_brick_fence":
            cube(bone, x + .375, y, z + .375, .25, 1, .25, color)
            # The reference's two neighbouring fence posts form the mouth.
            for neighbour in (-1, 1):
                if blocks.get((x, y, z + neighbour)) == name:
                    for height in (.375, .75):
                        cube(bone, x + .4375, y + height,
                             z + (.625 if neighbour == 1 else 0),
                             .125, .1875, .375, color)
            continue
        def available(xx, yy, zz):
            target_bone = "body" if yy >= 4 else ("left_foot" if zz >= 10 else "right_foot")
            return (blocks.get((xx, yy, zz)) == name and
                    (xx, yy, zz) not in consumed and target_bone == bone)

        # Greedy cuboids reduce draw geometry without filling holes or changing
        # a single coloured voxel. The check expands them back to source cells.
        dz = 1
        while available(x, y, z + dz):
            dz += 1
        dy = 1
        while all(available(x, y + dy, zz) for zz in range(z, z + dz)):
            dy += 1
        dx = 1
        while all(available(x + dx, yy, zz)
                  for yy in range(y, y + dy) for zz in range(z, z + dz)):
            dx += 1
        consumed.update((xx, yy, zz) for xx in range(x, x + dx)
                        for yy in range(y, y + dy) for zz in range(z, z + dz))
        cube(bone, x, y, z, dx, dy, dz, color)
    return {"format_version": "1.12.0", "minecraft:geometry": [{
        "description": {"identifier": "geometry.pikachu", "texture_width": 64,
                        "texture_height": 16, "visible_bounds_width": 2,
                        "visible_bounds_height": 2, "visible_bounds_offset": [0, 0.5, 0]},
        "bones": bones}]}


def texture():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    row = b"".join(bytes(COLORS[min(x // 8, 5)]) + b"\xff" for x in range(64))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 16, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + row) * 16)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    (RP / "models/entity/pikachu.geo.json").write_text(
        json.dumps(geometry(), indent=2) + "\n", encoding="utf-8")
    (RP / "textures/entity/pikachu.png").write_bytes(texture())
