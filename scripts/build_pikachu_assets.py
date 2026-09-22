"""Build the hand-authored Bedrock model and its flat-colour UV atlas, offline.

This is a small articulated model, not a voxel conversion of the reference.
Uses only the standard library; never opens world or environment files.
"""
import json
from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parent.parent
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"
COLORS = [(250, 210, 40), (37, 29, 24), (231, 57, 48),
          (126, 76, 37), (255, 251, 231), (229, 173, 25)]


def geometry():
    bones = [{"name": "root", "pivot": [0, 0, 0]},
             {"name": "body_root", "parent": "root", "pivot": [0, 5, 0]}]

    def bone(name, origin, size, color=0, parent="body_root", pivot=None, rotation=None):
        # Each face samples a solid swatch with a one-pixel bleed margin.
        uv = {face: {"uv": [color * 8 + 1, 1], "uv_size": [6, 6]}
              for face in ("north", "south", "east", "west", "up", "down")}
        item = {"name": name, "parent": parent, "pivot": pivot or origin,
                "cubes": [{"origin": origin, "size": size, "uv": uv}]}
        if rotation:
            item["rotation"] = rotation
        bones.append(item)

    bone("body", [-3.5, 2, -2.5], [7, 7, 5])
    bone("chest", [-3, 8, -2.25], [6, 2, 4.5])
    bone("head", [-4.5, 9, -3.5], [9, 6.5, 6])
    bone("muzzle", [-2.8, 9.5, -3.9], [5.6, 2.8, 0.7])
    for side, sign in (("left", 1), ("right", -1)):
        x = sign * 2.6
        bone(side + "_ear", [x-0.85, 14.5, -0.8], [1.7, 5, 1.4],
             pivot=[x, 14.5, 0], rotation=[-5, 0, -sign*19])
        bone(side + "_ear_tip", [x-0.85, 19.5, -0.8], [1.7, 2, 1.4], 1,
             parent=side + "_ear")
        bone(side + "_arm", [sign*3.6-0.9, 4.8, -2.5], [1.8, 4, 2],
             pivot=[sign*3.6, 8.5, -1.5], rotation=[-12, 0, sign*12])
        bone(side + "_foot", [sign*2-1.25, 0, -3.2], [2.5, 2, 4.2],
             pivot=[sign*2, 1, 0])
        bone(side + "_eye", [sign*2-0.65, 12.3, -3.57], [1.3, 1.65, 0.1], 1)
        bone(side + "_eye_light", [sign*2-0.3, 13.2, -3.64], [0.5, 0.55, 0.1], 4)
        bone(side + "_cheek", [sign*3.3-0.8, 10.3, -3.62], [1.6, 1.5, 0.15], 2)
    bone("nose", [-0.35, 11.8, -4], [0.7, 0.4, 0.2], 1)
    bone("mouth_left", [-1, 10.6, -4], [1, 0.18, 0.12], 1, rotation=[0, 0, -8])
    bone("mouth_right", [0, 10.6, -4], [1, 0.18, 0.12], 1, rotation=[0, 0, 8])
    for y in (4.5, 6.6):
        bone("back_stripe_" + str(y), [-2.8, y, 2.48], [5.6, 0.9, 0.12], 3)
    # Broad lightning silhouette extends behind the body; narrow brown root.
    bone("tail_base", [-0.65, 2.8, 2], [1.3, 3.5, 1.2], 3,
         pivot=[0, 3, 2.5], rotation=[35, 0, -25])
    bone("tail_lower", [-0.6, 5, 3.4], [3.8, 1.8, 1], 5, rotation=[0, 0, 25])
    bone("tail_middle", [1.7, 5.9, 3.4], [1.8, 4, 1], rotation=[0, 0, -25])
    bone("tail_lightning", [0.4, 9, 3.4], [5.8, 3.2, 1], rotation=[0, 0, 18])
    return {"format_version": "1.12.0", "minecraft:geometry": [{
        "description": {"identifier": "geometry.pikachu", "texture_width": 64,
                        "texture_height": 16, "visible_bounds_width": 2,
                        "visible_bounds_height": 2, "visible_bounds_offset": [0, 0.7, 0]},
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
