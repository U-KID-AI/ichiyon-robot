"""Deterministic original clear glass; native neighbor culling, no tick scripts."""
import argparse
from io import BytesIO
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BP = "behavior_packs/ichiyon_avatar_bp/"
RP = "resource_packs/ichiyon_aquarium_glass_rp/"
GROUP = "ichiyon:itemGroup.aquarium_glass"
# Deliberately muted original palette, ordered like the requested creative group.
COLORS = [
    ("clear", "無色", (204, 235, 240)), ("white", "白", (237, 240, 241)),
    ("light_gray", "薄灰", (175, 185, 188)), ("gray", "灰", (91, 108, 119)),
    ("black", "黒", (43, 51, 62)), ("red", "赤", (191, 70, 76)),
    ("orange", "橙", (230, 143, 62)), ("yellow", "黄", (233, 212, 87)),
    ("lime", "黄緑", (155, 197, 85)), ("green", "緑", (74, 135, 91)),
    ("light_blue", "水色", (117, 196, 224)), ("cyan", "シアン", (70, 171, 178)),
    ("blue", "青", (83, 121, 196)), ("purple", "紫", (134, 105, 178)),
    ("magenta", "マゼンタ", (192, 107, 182)), ("pink", "ピンク", (231, 167, 184)),
    ("brown", "茶", (141, 105, 80)),
]
DIRECTIONS = {"west": (0, -1), "east": (0, 1), "down": (1, -1),
              "up": (1, 1), "north": (2, -1), "south": (2, 1)}
HEADER_UUID = "fe55c87e-d7a2-4e3c-88fb-43009f65df75"
MODULE_UUID = "a3d30f1b-8445-4e46-a670-e53433ec8d7a"


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def identifier(color):
    return "ichiyon:aquarium_glass_" + color


def geometry_and_culling():
    # One low-opacity face plus four separately culled edge strips per direction.
    # Strips are 1/64 block wide. A 0.002 model-unit offset avoids coplanar z-fighting.
    bones, rules = [], []
    for face, (normal, sign) in DIRECTIONS.items():
        base = [-8, 0, -8]
        size = [16, 16, 16]
        uv = {face: {"uv": [0, 0], "uv_size": [32, 32], "material_instance": "body"}}
        bones.append({"name": face, "pivot": [0, 0, 0], "cubes": [{"origin": base, "size": size, "uv": uv}]})
        rules.append({"geometry_part": {"bone": face}, "direction": face, "condition": "same_block"})
        for edge, (axis, edge_sign) in DIRECTIONS.items():
            if axis == normal:
                continue
            name = face + "_" + edge
            origin, extent = base.copy(), size.copy()
            origin[normal] += (16 if sign > 0 else 0) + sign * 0.002
            extent[normal] = 0
            origin[axis] += 15.75 if edge_sign > 0 else 0
            extent[axis] = 0.25
            bones.append({"name": name, "pivot": [0, 0, 0], "cubes": [{
                "origin": origin, "size": extent,
                "uv": {face: {"uv": [0, 0], "uv_size": [32, 32], "material_instance": "edge"}},
            }]})
            # The front edge must disappear at both a face neighbor and a lateral
            # glass neighbor. Opaque lateral framing does NOT erase the outer rim.
            for direction in (face, edge):
                rules.append({"geometry_part": {"bone": name}, "direction": direction,
                              "condition": "same_block", "cull_against_full_and_opaque": direction == face})
    geometry = {"format_version": "1.12.0", "minecraft:geometry": [{
        "description": {"identifier": "geometry.ichiyon.aquarium_glass", "texture_width": 32, "texture_height": 32},
        "bones": bones,
    }]}
    culling = {"format_version": "1.21.80", "minecraft:block_culling_rules": {
        "description": {"identifier": "ichiyon:aquarium_glass"}, "rules": rules}}
    return geometry, culling


def generated_files(root=ROOT / "minecraft"):
    files = {}
    def put(path, value):
        files[path] = encoded(value)
    geometry, culling = geometry_and_culling()
    put(RP + "models/blocks/aquarium_glass.geo.json", geometry)
    put(RP + "block_culling/aquarium_glass.json", culling)
    put(RP + "manifest.json", {"format_version": 2, "header": {
        "name": "Ichiyon Aquarium Glass", "description": "Original clear glass with connected edge culling",
        "uuid": HEADER_UUID, "version": [1, 0, 0], "min_engine_version": [1, 26, 0]},
        "modules": [{"type": "resources", "uuid": MODULE_UUID, "version": [1, 0, 0]}]})
    terrain, blocks = {}, {"format_version": [1, 1, 0]}
    labels = {"en_US": [GROUP + "=Aquarium Glass"], "ja_JP": [GROUP + "=アクアリウムガラス"]}
    for color, japanese, rgb in COLORS:
        stem = "aquarium_glass_" + color
        block_id = identifier(color)
        for material, alpha in (("body", 8 if color == "clear" else 24), ("edge", 104)):
            key = stem + "_" + material
            tint = rgb if material == "body" else tuple(round(c * 0.55 + 255 * 0.45) for c in rgb)
            image = Image.new("RGBA", (32, 32), (*tint, alpha))
            out = BytesIO()
            image.save(out, format="PNG", optimize=False, compress_level=9)
            files[RP + "textures/blocks/" + key + ".png"] = out.getvalue()
            terrain[key] = {"textures": "textures/blocks/" + key}
        english = "Aquarium Glass" + ("" if color == "clear" else " " + color.replace("_", " ").title())
        for locale, text in (("en_US", english), ("ja_JP", "アクアリウムガラス（" + japanese + "）")):
            labels[locale].append(f"tile.{block_id}.name={text}")
        components = {
            "minecraft:geometry": {"identifier": "geometry.ichiyon.aquarium_glass", "culling": "ichiyon:aquarium_glass"},
            "minecraft:material_instances": {material: {"texture": stem + "_" + material,
                "render_method": "blend", "face_dimming": False, "ambient_occlusion": False}
                for material in ("body", "edge")},
            "minecraft:collision_box": True, "minecraft:selection_box": True,
            "minecraft:light_dampening": 0, "minecraft:friction": 0.6,
            "minecraft:destructible_by_mining": {"seconds_to_destroy": 0.3},
            "minecraft:destructible_by_explosion": {"explosion_resistance": 1.5},
            "minecraft:map_color": "#" + "".join(f"{c:02x}" for c in rgb),
            "minecraft:connection_rule": {"accepts_connections_from": "all"},
            "minecraft:loot": f"loot_tables/blocks/{stem}.json",
        }
        put(BP + f"blocks/{stem}.json", {"format_version": "1.26.0", "minecraft:block": {
            "description": {"identifier": block_id, "menu_category": {"category": "construction", "group": GROUP}},
            "components": components}})
        put(BP + f"loot_tables/blocks/{stem}.json", {"pools": [{"rolls": 1, "entries": [{"type": "item", "name": block_id,
            "conditions": [{"condition": "match_tool", "enchantments": [{"enchantment": "silk_touch", "levels": {"min": 1}}]}]}]}]})
        blocks[block_id] = {"sound": "glass"}
    put(RP + "textures/terrain_texture.json", {"resource_pack_name": "ichiyon_aquarium_glass_rp",
        "texture_name": "atlas.terrain", "padding": 8, "num_mip_levels": 4, "texture_data": terrain})
    put(RP + "blocks.json", blocks)
    put(RP + "texts/languages.json", list(labels))
    for locale, lines in labels.items():
        files[RP + f"texts/{locale}.lang"] = ("\n".join(lines) + "\n").encode("utf-8")
    path = BP + "item_catalog/crafting_item_catalog.json"
    catalog = json.loads((root / path).read_text(encoding="utf-8"))
    construction = next(c for c in catalog["minecraft:crafting_items_catalog"]["categories"] if c["category_name"] == "construction")
    groups = construction["groups"]
    groups[:] = [g for g in groups if g.get("group_identifier", {}).get("name") != GROUP]
    groups.append({"group_identifier": {"name": GROUP, "icon": identifier("clear")},
                   "items": [identifier(c[0]) for c in COLORS]})
    put(path, catalog)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = generated_files()
    for name, data in files.items():
        path = ROOT / "minecraft" / name
        if args.check:
            assert path.exists(), name
            actual = path.read_bytes()
            assert (actual if name.endswith(".png") else actual.replace(b"\r\n", b"\n")) == data, name
        elif not path.exists() or path.read_bytes().replace(b"\r\n", b"\n") != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    print(f"Aquarium glass: {len(COLORS)} blocks, {len(files)} files {'verified' if args.check else 'generated'}")


if __name__ == "__main__":
    main()
