"""Connected aquarium glass: low-opacity body with same-color seam culling."""
import argparse
from io import BytesIO
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BP = "behavior_packs/ichiyon_avatar_bp/"
RP = "resource_packs/ichiyon_aquarium_glass_rp/"
GROUP = "ichiyon:itemGroup.aquarium_glass"

COLORS = [
    ("clear", "無色", (204, 235, 240)),
    ("white", "白", (237, 240, 241)),
    ("light_gray", "薄灰", (175, 185, 188)),
    ("gray", "灰", (91, 108, 119)),
    ("black", "黒", (43, 51, 62)),
    ("red", "赤", (191, 70, 76)),
    ("orange", "橙", (230, 143, 62)),
    ("yellow", "黄", (233, 212, 87)),
    ("lime", "黄緑", (155, 197, 85)),
    ("green", "緑", (74, 135, 91)),
    ("light_blue", "水色", (117, 196, 224)),
    ("cyan", "シアン", (70, 171, 178)),
    ("blue", "青", (83, 121, 196)),
    ("purple", "紫", (134, 105, 178)),
    ("magenta", "マゼンタ", (192, 107, 182)),
    ("pink", "ピンク", (231, 167, 184)),
    ("brown", "茶", (141, 105, 80)),
]

DIRECTIONS = {
    "west": (0, -1),
    "east": (0, 1),
    "down": (1, -1),
    "up": (1, 1),
    "north": (2, -1),
    "south": (2, 1),
}

HEADER_UUID = "fe55c87e-d7a2-4e3c-88fb-43009f65df75"
MODULE_UUID = "a3d30f1b-8445-4e46-a670-e53433ec8d7a"

PACK_VERSION = [1, 1, 0]

CLEAR_ALPHA = 8
COLORED_ALPHA = 18
RIM_ALPHA = 128

RIM_WIDTH = 0.5
RIM_DEPTH = 0.0625
RIM_OFFSET = 0.002

BLOCK_MIN = [-8, 0, -8]
BLOCK_MAX = [8, 16, 8]
BLOCK_SIZE = [16, 16, 16]


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def identifier(color):
    return "ichiyon:aquarium_glass_" + color


def face_uv(face, material):
    return {
        face: {
            "uv": [0, 0],
            "uv_size": [32, 32],
            "material_instance": material,
        }
    }


def geometry_and_culling():
    # One real full-volume body cube. Each face can be culled independently.
    body_uv = {
        face: {
            "uv": [0, 0],
            "uv_size": [32, 32],
            "material_instance": "body",
        }
        for face in DIRECTIONS
    }

    bones = [{
        "name": "body",
        "pivot": [0, 0, 0],
        "cubes": [{
            "origin": BLOCK_MIN.copy(),
            "size": BLOCK_SIZE.copy(),
            "uv": body_uv,
        }],
    }]

    rules = []

    # Hide only the touching face when the exact same Aquarium Glass block
    # identifier exists in that direction.
    for face in DIRECTIONS:
        rules.append({
            "geometry_part": {
                "bone": "body",
                "cube": 0,
                "face": face,
            },
            "direction": face,
            "condition": "same_block",
            "cull_against_full_and_opaque": True,
        })

    # Four positive-volume rims on each of the six faces.
    for face, (normal_axis, face_sign) in DIRECTIONS.items():
        for edge, (edge_axis, edge_sign) in DIRECTIONS.items():
            if edge_axis == normal_axis:
                continue

            name = f"{face}_{edge}"
            origin = BLOCK_MIN.copy()
            size = BLOCK_SIZE.copy()

            # Positive volume: zero-thickness cubes are forbidden.
            size[normal_axis] = RIM_DEPTH

            # Keep almost all of the rim depth inside the block, but put
            # the rendered outward face 0.002 model units in front of the
            # body face to avoid coplanar z-fighting.
            if face_sign > 0:
                origin[normal_axis] = (
                    BLOCK_MAX[normal_axis] - RIM_DEPTH + RIM_OFFSET
                )
            else:
                origin[normal_axis] = (
                    BLOCK_MIN[normal_axis] - RIM_OFFSET
                )

            size[edge_axis] = RIM_WIDTH

            if edge_sign > 0:
                origin[edge_axis] = BLOCK_MAX[edge_axis] - RIM_WIDTH
            else:
                origin[edge_axis] = BLOCK_MIN[edge_axis]

            bones.append({
                "name": name,
                "pivot": [0, 0, 0],
                "cubes": [{
                    "origin": origin,
                    "size": size,
                    # Only the outward-facing surface is visible.
                    "uv": face_uv(face, "edge"),
                }],
            })

            # Same block in face-normal direction -> this face is internal.
            rules.append({
                "geometry_part": {"bone": name},
                "direction": face,
                "condition": "same_block",
                "cull_against_full_and_opaque": True,
            })

            # Same block through this edge -> remove per-block grid line.
            # Opaque non-glass framing must not remove the outside rim.
            rules.append({
                "geometry_part": {"bone": name},
                "direction": edge,
                "condition": "same_block",
                "cull_against_full_and_opaque": False,
            })

    geometry = {
        "format_version": "1.12.0",
        "minecraft:geometry": [{
            "description": {
                "identifier": "geometry.ichiyon.aquarium_glass",
                "texture_width": 32,
                "texture_height": 32,
            },
            "bones": bones,
        }],
    }

    culling = {
        "format_version": "1.21.80",
        "minecraft:block_culling_rules": {
            "description": {
                "identifier": "ichiyon:aquarium_glass",
            },
            "rules": rules,
        },
    }

    return geometry, culling


def generated_files(root=ROOT / "minecraft"):
    files = {}

    def put(path, value):
        files[path] = encoded(value)

    geometry, culling = geometry_and_culling()

    put(RP + "models/blocks/aquarium_glass.geo.json", geometry)
    put(RP + "block_culling/aquarium_glass.json", culling)

    put(RP + "manifest.json", {
        "format_version": 2,
        "header": {
            "name": "Ichiyon Aquarium Glass",
            "description": "Connected low-opacity aquarium glass with outer-only rims",
            "uuid": HEADER_UUID,
            "version": PACK_VERSION,
            "min_engine_version": [1, 26, 0],
        },
        "modules": [{
            "type": "resources",
            "uuid": MODULE_UUID,
            "version": PACK_VERSION,
        }],
    })

    terrain = {}
    blocks = {"format_version": [1, 1, 0]}

    labels = {
        "en_US": [GROUP + "=Aquarium Glass"],
        "ja_JP": [GROUP + "=アクアリウムガラス"],
    }

    for color, japanese, rgb in COLORS:
        stem = "aquarium_glass_" + color
        block_id = identifier(color)

        body_alpha = CLEAR_ALPHA if color == "clear" else COLORED_ALPHA

        edge_rgb = tuple(
            round(channel * 0.55 + 255 * 0.45)
            for channel in rgb
        )

        for material, tint, alpha in (
            ("body", rgb, body_alpha),
            ("edge", edge_rgb, RIM_ALPHA),
        ):
            key = stem + "_" + material

            image = Image.new(
                "RGBA",
                (32, 32),
                (*tint, alpha),
            )

            output = BytesIO()
            image.save(
                output,
                format="PNG",
                optimize=False,
                compress_level=9,
            )

            files[
                RP + "textures/blocks/" + key + ".png"
            ] = output.getvalue()

            terrain[key] = {
                "textures": "textures/blocks/" + key
            }

        english = (
            "Aquarium Glass"
            if color == "clear"
            else "Aquarium Glass " + color.replace("_", " ").title()
        )

        labels["en_US"].append(
            f"tile.{block_id}.name={english}"
        )

        labels["ja_JP"].append(
            f"tile.{block_id}.name=アクアリウムガラス（{japanese}）"
        )

        components = {
            "minecraft:geometry": {
                "identifier": "geometry.ichiyon.aquarium_glass",
                "culling": "ichiyon:aquarium_glass",
            },
            "minecraft:material_instances": {
                "body": {
                    "texture": stem + "_body",
                    "render_method": "blend",
                    "face_dimming": False,
                    "ambient_occlusion": False,
                },
                "edge": {
                    "texture": stem + "_edge",
                    "render_method": "blend",
                    "face_dimming": False,
                    "ambient_occlusion": False,
                },
            },
            "minecraft:collision_box": True,
            "minecraft:selection_box": True,
            "minecraft:light_dampening": 0,
            "minecraft:friction": 0.6,
            "minecraft:destructible_by_mining": {
                "seconds_to_destroy": 0.3
            },
            "minecraft:destructible_by_explosion": {
                "explosion_resistance": 1.5
            },
            "minecraft:map_color": "#" + "".join(
                f"{channel:02x}" for channel in rgb
            ),
            "minecraft:connection_rule": {
                "accepts_connections_from": "all"
            },
            "minecraft:loot": f"loot_tables/blocks/{stem}.json",
        }

        put(BP + f"blocks/{stem}.json", {
            "format_version": "1.26.0",
            "minecraft:block": {
                "description": {
                    "identifier": block_id,
                    "menu_category": {
                        "category": "construction",
                        "group": GROUP,
                    },
                },
                "components": components,
            },
        })

        put(BP + f"loot_tables/blocks/{stem}.json", {
            "pools": [{
                "rolls": 1,
                "entries": [{
                    "type": "item",
                    "name": block_id,
                    "conditions": [{
                        "condition": "match_tool",
                        "enchantments": [{
                            "enchantment": "silk_touch",
                            "levels": {"min": 1},
                        }],
                    }],
                }],
            }],
        })

        blocks[block_id] = {"sound": "glass"}

    put(RP + "textures/terrain_texture.json", {
        "resource_pack_name": "ichiyon_aquarium_glass_rp",
        "texture_name": "atlas.terrain",
        "padding": 8,
        "num_mip_levels": 4,
        "texture_data": terrain,
    })

    put(RP + "blocks.json", blocks)
    put(RP + "texts/languages.json", list(labels))

    for locale, lines in labels.items():
        files[RP + f"texts/{locale}.lang"] = (
            "\n".join(lines) + "\n"
        ).encode("utf-8")

    path = BP + "item_catalog/crafting_item_catalog.json"

    catalog = json.loads(
        (root / path).read_text(encoding="utf-8")
    )

    construction = next(
        category
        for category in catalog[
            "minecraft:crafting_items_catalog"
        ]["categories"]
        if category["category_name"] == "construction"
    )

    groups = construction["groups"]

    groups[:] = [
        group
        for group in groups
        if group.get("group_identifier", {}).get("name") != GROUP
    ]

    groups.append({
        "group_identifier": {
            "name": GROUP,
            "icon": identifier("clear"),
        },
        "items": [
            identifier(color[0])
            for color in COLORS
        ],
    })

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
            assert (
                actual
                if name.endswith(".png")
                else actual.replace(b"\r\n", b"\n")
            ) == data, name

        elif (
            not path.exists()
            or path.read_bytes().replace(b"\r\n", b"\n") != data
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    print(
        f"Aquarium glass: {len(COLORS)} blocks, "
        f"{len(files)} files "
        f"{'verified' if args.check else 'generated'}"
    )


if __name__ == "__main__":
    main()
