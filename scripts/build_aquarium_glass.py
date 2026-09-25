"""Canonical connected Aquarium Glass using one custom full cube."""
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

DIRECTIONS = (
    "down",
    "up",
    "north",
    "south",
    "west",
    "east",
)

HEADER_UUID = "fe55c87e-d7a2-4e3c-88fb-43009f65df75"
MODULE_UUID = "a3d30f1b-8445-4e46-a670-e53433ec8d7a"

PACK_VERSION = [1, 2, 0]

# No separate rim in this canonical rendering pass.
# These are intentionally far below the previous 64/255 diagnostic.
CLEAR_ALPHA = 24
COLORED_ALPHA = 36


def encoded(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def identifier(color):
    return "ichiyon:aquarium_glass_" + color


def geometry_and_culling():
    # Match the current canonical custom-glass structure:
    # one ordinary full cube, no material_instance names in the geometry.
    uv = {
        "north": {"uv": [0, 0], "uv_size": [16, 16]},
        "east":  {"uv": [0, 0], "uv_size": [16, 16]},
        "south": {"uv": [0, 0], "uv_size": [16, 16]},
        "west":  {"uv": [0, 0], "uv_size": [16, 16]},
        "up":    {"uv": [16, 16], "uv_size": [-16, -16]},
        "down":  {"uv": [16, 16], "uv_size": [-16, -16]},
    }

    geometry = {
        "format_version": "1.26.50",
        "minecraft:geometry": [{
            "description": {
                "identifier": "geometry.ichiyon.aquarium_glass",
                "texture_width": 16,
                "texture_height": 16,
            },
            "bones": [{
                "name": "glass",
                "pivot": [0, 0, 0],
                "cubes": [{
                    "origin": [-8, 0, -8],
                    "size": [16, 16, 16],
                    "uv": uv,
                }],
            }],
        }],
    }

    rules = [
        {
            "condition": "same_block",
            "direction": face,
            "geometry_part": {
                "bone": "glass",
                "cube": 0,
                "face": face,
            },
        }
        for face in DIRECTIONS
    ]

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

    put(
        RP + "models/blocks/aquarium_glass.geo.json",
        geometry,
    )

    put(
        RP + "block_culling/aquarium_glass.json",
        culling,
    )

    put(
        RP + "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "Ichiyon Aquarium Glass",
                "description":
                    "Canonical connected translucent aquarium glass",
                "uuid": HEADER_UUID,
                "version": PACK_VERSION,
                "min_engine_version": [1, 26, 50],
            },
            "modules": [{
                "type": "resources",
                "uuid": MODULE_UUID,
                "version": PACK_VERSION,
            }],
        },
    )

    terrain = {}
    blocks = {"format_version": [1, 1, 0]}

    labels = {
        "en_US": [GROUP + "=Aquarium Glass"],
        "ja_JP": [GROUP + "=アクアリウムガラス"],
    }

    for color, japanese, rgb in COLORS:
        stem = "aquarium_glass_" + color
        block_id = identifier(color)

        alpha = (
            CLEAR_ALPHA
            if color == "clear"
            else COLORED_ALPHA
        )

        key = stem + "_body"

        image = Image.new(
            "RGBA",
            (16, 16),
            (*rgb, alpha),
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
            else
            "Aquarium Glass "
            + color.replace("_", " ").title()
        )

        labels["en_US"].append(
            f"tile.{block_id}.name={english}"
        )

        labels["ja_JP"].append(
            f"tile.{block_id}.name="
            f"アクアリウムガラス（{japanese}）"
        )

        components = {
            "minecraft:light_dampening": 0,

            "minecraft:geometry": {
                "identifier":
                    "geometry.ichiyon.aquarium_glass",
                "culling":
                    "ichiyon:aquarium_glass",
            },

            # Deliberately use only the wildcard material,
            # matching the canonical custom-glass pattern.
            "minecraft:material_instances": {
                "*": {
                    "texture": key,
                    "render_method": "blend",
                }
            },

            "minecraft:collision_box": True,
            "minecraft:selection_box": True,
            "minecraft:friction": 0.6,

            "minecraft:destructible_by_mining": {
                "seconds_to_destroy": 0.3,
            },

            "minecraft:destructible_by_explosion": {
                "explosion_resistance": 1.5,
            },

            "minecraft:map_color":
                "#"
                + "".join(
                    f"{channel:02x}"
                    for channel in rgb
                ),

            "minecraft:connection_rule": {
                "accepts_connections_from": "all"
            },

            "minecraft:loot":
                f"loot_tables/blocks/{stem}.json",
        }

        put(
            BP + f"blocks/{stem}.json",
            {
                "format_version": "1.26.50",
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
            },
        )

        put(
            BP
            + f"loot_tables/blocks/{stem}.json",
            {
                "pools": [{
                    "rolls": 1,
                    "entries": [{
                        "type": "item",
                        "name": block_id,
                        "conditions": [{
                            "condition":
                                "match_tool",
                            "enchantments": [{
                                "enchantment":
                                    "silk_touch",
                                "levels": {
                                    "min": 1
                                },
                            }],
                        }],
                    }],
                }],
            },
        )

        blocks[block_id] = {
            "sound": "glass"
        }

    put(
        RP + "textures/terrain_texture.json",
        {
            "resource_pack_name":
                "ichiyon_aquarium_glass_rp",
            "texture_name": "atlas.terrain",
            "padding": 8,
            "num_mip_levels": 4,
            "texture_data": terrain,
        },
    )

    put(
        RP + "blocks.json",
        blocks,
    )

    put(
        RP + "texts/languages.json",
        list(labels),
    )

    for locale, lines in labels.items():
        files[
            RP + f"texts/{locale}.lang"
        ] = (
            "\n".join(lines) + "\n"
        ).encode("utf-8")

    path = (
        BP
        + "item_catalog/"
        + "crafting_item_catalog.json"
    )

    catalog = json.loads(
        (root / path).read_text(
            encoding="utf-8"
        )
    )

    construction = next(
        category
        for category
        in catalog[
            "minecraft:crafting_items_catalog"
        ]["categories"]
        if category["category_name"]
        == "construction"
    )

    groups = construction["groups"]

    groups[:] = [
        group
        for group in groups
        if group
        .get("group_identifier", {})
        .get("name")
        != GROUP
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
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--check",
        action="store_true",
    )

    args = parser.parse_args()

    files = generated_files()

    # Remove obsolete rim textures from 1.1.0.
    texture_dir = (
        ROOT
        / "minecraft"
        / RP
        / "textures"
        / "blocks"
    )

    for obsolete in texture_dir.glob(
        "aquarium_glass_*_edge.png"
    ):
        if args.check:
            assert not obsolete.exists(), obsolete
        else:
            obsolete.unlink()

    for name, data in files.items():
        path = ROOT / "minecraft" / name

        if args.check:
            assert path.exists(), name

            actual = path.read_bytes()

            assert (
                actual
                if name.endswith(".png")
                else actual.replace(
                    b"\r\n",
                    b"\n",
                )
            ) == data, name

        elif (
            not path.exists()
            or path.read_bytes().replace(
                b"\r\n",
                b"\n",
            )
            != data
        ):
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            path.write_bytes(data)

    print(
        f"Aquarium glass: "
        f"{len(COLORS)} blocks, "
        f"{len(files)} files "
        f"{'verified' if args.check else 'generated'}"
    )


if __name__ == "__main__":
    main()
