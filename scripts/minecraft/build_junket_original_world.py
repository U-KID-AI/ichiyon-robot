"""Build original, redistributable JUNKET venues, models and runtime metadata.

This generator does not read the licensed map archive. It creates independently
authored block structures and textures that implement the 17 public game
descriptions preserved in ``junket_games.json``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import nbtlib as n
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "scripts/minecraft/junket_games.json"
BP = ROOT / "minecraft/behavior_packs/ichiyon_junket_bp"
RP = ROOT / "minecraft/resource_packs/ichiyon_junket_rp"
STRUCTURES = BP / "structures/junket"
ENTITIES = BP / "entities"
RP_ENTITIES = RP / "entity"
RP_MODELS = RP / "models/entity"
RP_RENDER = RP / "render_controllers"
RP_TEXTURES = RP / "textures/entity/junket"
RP_MANIFEST = RP / "manifest.json"
GENERATED = BP / "scripts/junket_venue_generated.js"

BLOCK_VERSION = 18186752
WORLD_ORIGIN = (20000, 65, 20000)
SPACING = 96

SIZES = {
    "sparrow": (27, 13, 29),
    "temperance": (29, 33, 62),
    "lucy": (15, 11, 5),
    "silence": (31, 13, 33),
    "jinn": (28, 38, 37),
    "ace": (31, 14, 29),
    "grail": (29, 14, 31),
    "hope": (23, 14, 27),
    "auction": (29, 14, 31),
    "fire": (37, 14, 29),
    "pig": (31, 14, 29),
    "light": (29, 14, 29),
    "mine": (37, 16, 39),
    "clock": (31, 14, 31),
    "walker": (31, 14, 29),
    "garden": (33, 14, 33),
    "rabbit": (33, 14, 31),
}

SOURCE_ORIGINS = {
    "sparrow": (210, 54, 198),
    "temperance": (80, 99, 205),
    "lucy": (194, 55, 209),
    "silence": (79, 55, 281),
    "jinn": (80, 120, 230),
}

STYLES = {
    "sparrow": ("smooth_quartz", "crimson_planks", "red_wool", "birch_slab"),
    "temperance": ("quartz_block", "polished_diorite", "light_blue_wool", "cherry_slab"),
    "lucy": ("blackstone", "polished_blackstone", "magenta_wool", "crimson_slab"),
    "silence": ("smooth_basalt", "deepslate_tiles", "yellow_wool", "dark_oak_slab"),
    "jinn": ("sandstone", "cut_sandstone", "orange_wool", "jungle_slab"),
    "ace": ("deepslate_tiles", "polished_deepslate", "green_wool", "spruce_slab"),
    "grail": ("smooth_quartz", "quartz_bricks", "purple_wool", "quartz_slab"),
    "hope": ("tuff_bricks", "polished_tuff", "magenta_wool", "bamboo_slab"),
    "auction": ("smooth_stone", "stone_bricks", "red_wool", "sandstone_slab"),
    "fire": ("blackstone", "gilded_blackstone", "orange_wool", "crimson_slab"),
    "pig": ("mud_bricks", "packed_mud", "pink_wool", "spruce_slab"),
    "light": ("polished_blackstone_bricks", "blackstone", "yellow_wool", "dark_oak_slab"),
    "mine": ("nether_bricks", "red_nether_bricks", "lime_wool", "warped_slab"),
    "clock": ("deepslate_bricks", "deepslate_tiles", "blue_wool", "crimson_slab"),
    "walker": ("stone_bricks", "cracked_stone_bricks", "red_wool", "oak_slab"),
    "garden": ("mossy_stone_bricks", "moss_block", "lime_wool", "bamboo_slab"),
    "rabbit": ("granite", "polished_granite", "pink_wool", "birch_slab"),
}

SPECIAL_ENTITIES = {
    "sparrow": ["bigbox", "littlebox", "diamond", "tv", "partition"],
    "temperance": ["temperance_balance", "temperance_base", "temperance_card",
                   "temperance_frame", "temperance_stone", "temperance_tv"],
    "lucy": ["lucy_box", "lucy_doll", "lucy_key", "lucy_keyhole", "lucy_tv"],
    "silence": ["silence_cd", "silence_covering", "silence_jacket", "silence_tv"],
    "jinn": ["jinn_card", "jinn_covering", "jinn_food", "jinn_frame",
             "jinn_glass", "jinn_lever", "jinn_tv"],
}


def load_games():
    value = json.loads(CATALOG.read_text(encoding="utf-8"))
    games = value["games"]
    if len(games) != 17 or set(game["id"] for game in games) != set(SIZES):
        raise ValueError("venue generator must cover the verified 17-game inventory")
    return games


def block(name: str) -> n.Compound:
    return n.Compound({
        "name": n.String(name),
        "states": n.Compound(),
        "version": n.Int(BLOCK_VERSION),
    })


AIR = block("minecraft:air")


class Structure:
    def __init__(self, size):
        self.size = tuple(size)
        self.blocks = {}

    def set(self, x, y, z, name):
        if not (0 <= x < self.size[0] and 0 <= y < self.size[1] and 0 <= z < self.size[2]):
            return
        self.blocks[(x, y, z)] = name

    def fill(self, lo, hi, name):
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    self.set(x, y, z, name)

    def save(self, path: Path):
        sx, sy, sz = self.size
        palette, index = [AIR], {"minecraft:air": 0}
        values = []
        for x in range(sx):
            for y in range(sy):
                for z in range(sz):
                    name = self.blocks.get((x, y, z), "minecraft:air")
                    if name not in index:
                        index[name] = len(palette)
                        palette.append(block(name))
                    values.append(index[name])
        root = n.File({
            "format_version": n.Int(1),
            "size": n.List[n.Int]([n.Int(v) for v in self.size]),
            "structure_world_origin": n.List[n.Int]([n.Int(0), n.Int(0), n.Int(0)]),
            "structure": n.Compound({
                "block_indices": n.List[n.List[n.Int]]([
                    n.List[n.Int]([n.Int(v) for v in values]),
                    n.List[n.Int]([n.Int(-1) for _ in values]),
                ]),
                "entities": n.List[n.Compound]([]),
                "palette": n.Compound({"default": n.Compound({
                    "block_palette": n.List[n.Compound](palette),
                    "block_position_data": n.Compound(),
                })}),
            }),
        })
        path.parent.mkdir(parents=True, exist_ok=True)
        root.save(path, byteorder="little")


def decorate(structure: Structure, game_id: str):
    sx, sy, sz = structure.size
    floor, wall, accent, trim = (f"minecraft:{name}" for name in STYLES[game_id])
    structure.fill((0, 0, 0), (sx - 1, 0, sz - 1), wall)
    structure.fill((1, 1, 1), (sx - 2, 1, sz - 2), floor)
    for y in range(2, sy - 1):
        structure.fill((0, y, 0), (sx - 1, y, 0), wall)
        structure.fill((0, y, sz - 1), (sx - 1, y, sz - 1), wall)
        structure.fill((0, y, 0), (0, y, sz - 1), wall)
        structure.fill((sx - 1, y, 0), (sx - 1, y, sz - 1), wall)
    for x in range(0, sx, 4):
        for z in range(0, sz, 4):
            structure.set(x, sy - 1, z, "minecraft:glowstone")
    structure.fill((1, sy - 1, 1), (sx - 2, sy - 1, sz - 2), trim)
    for x in range(1, sx - 1):
        structure.set(x, sy - 2, 0, accent if x % 3 else "minecraft:light")
        structure.set(x, sy - 2, sz - 1, accent if x % 3 else "minecraft:light")
    for z in range(1, sz - 1):
        structure.set(0, sy - 2, z, accent if z % 3 else "minecraft:light")
        structure.set(sx - 1, sy - 2, z, accent if z % 3 else "minecraft:light")
    entrance = sx // 2
    structure.fill((entrance - 1, 2, sz - 1), (entrance + 1, 4, sz - 1), "minecraft:air")
    structure.fill((entrance - 2, 2, 0), (entrance + 2, 3, 0), "minecraft:air")
    center_x, center_z = sx // 2, sz // 2
    structure.fill((center_x - 2, 2, center_z - 2), (center_x + 2, 2, center_z + 2), trim)
    structure.fill((center_x - 1, 3, center_z - 1), (center_x + 1, 3, center_z + 1), accent)
    for dx in (-6, 6):
        structure.fill((center_x + dx - 1, 2, 4), (center_x + dx + 1, 2, sz - 5),
                       "minecraft:sea_lantern" if game_id in ("light", "mine", "clock") else trim)
    motif = sum(ord(ch) for ch in game_id)
    for index in range(4):
        x = 3 + (motif + index * 5) % max(1, sx - 6)
        z = 4 + (motif * (index + 2)) % max(1, sz - 8)
        structure.set(x, 2, z, accent)
        structure.set(sx - 1 - x, 2, sz - 1 - z, trim)


def write_geometry():
    geometries = []
    for index, game in enumerate(load_games()):
        game_id = game["id"]
        width = 4 + index % 4
        height = 5 + index % 3
        geometry = {
            "description": {
                "identifier": f"geometry.junket.{game_id}",
                "texture_width": 64,
                "texture_height": 64,
                "visible_bounds_width": 5,
                "visible_bounds_height": 6,
                "visible_bounds_offset": [0, 2, 0],
            },
            "bones": [
                {"name": "body", "pivot": [0, 0, 0], "cubes": [
                    {"origin": [-width / 2, 0, -width / 2],
                     "size": [width, height, width], "uv": [0, 0]},
                ]},
                {"name": "crest", "pivot": [0, height, 0], "cubes": [
                    {"origin": [-1.5, height, -1], "size": [3, 2, 2], "uv": [32, 32]},
                ]},
            ],
        }
        geometries.append(geometry)
    path = RP_MODELS / "junket.geo.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "format_version": "1.21.0",
        "minecraft:geometry": geometries,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_texture(index: int, game_id: str):
    palette = (
        (38, 43, 52), (236, 239, 244), (214, 169, 42), (181, 55, 72),
        (35, 126, 99), (53, 91, 169), (142, 72, 177), (211, 105, 48),
    )
    image = Image.new("RGBA", (64, 64), palette[index % len(palette)] + (255,))
    draw = ImageDraw.Draw(image)
    accent = palette[(index + 3) % len(palette)]
    white = palette[(index + 1) % len(palette)]
    draw.rectangle((0, 0, 63, 63), fill=palette[index % len(palette)] + (255,))
    for y in range(0, 64, 8):
        draw.line((0, y, 63, y), fill=accent + (255,), width=2)
    motif = index % 4
    if motif == 0:
        draw.ellipse((16, 16, 47, 47), outline=white + (255,), width=4)
    elif motif == 1:
        draw.polygon([(32, 8), (56, 52), (8, 52)], outline=white + (255,))
    elif motif == 2:
        draw.rectangle((12, 12, 51, 51), outline=white + (255,), width=4)
        draw.line((12, 12, 51, 51), fill=white + (255,), width=3)
    else:
        for offset in (16, 32, 48):
            draw.arc((offset - 12, 12, offset + 12, 52), 0, 180, fill=white + (255,), width=4)
    path = RP_TEXTURES / f"{game_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def write_entities():
    entity_games = {}
    for game in load_games():
        entity_games[f"{game['id']}_prop"] = game["id"]
    for game_id, names in SPECIAL_ENTITIES.items():
        for name in names:
            entity_games[name] = game_id
    ENTITIES.mkdir(parents=True, exist_ok=True)
    RP_ENTITIES.mkdir(parents=True, exist_ok=True)
    RP_RENDER.mkdir(parents=True, exist_ok=True)
    RP_MANIFEST.write_text(json.dumps({
        "format_version": 2,
        "header": {
            "name": "Ichiyon JUNKET Original Models",
            "description": "Independently authored models for the 17 JUNKET games",
            "uuid": "d48f4748-5b7b-4ad5-857a-3491b6fa2b16",
            "version": [0, 8, 0],
            "min_engine_version": [1, 26, 44],
        },
        "modules": [{
            "type": "resources",
            "uuid": "403506cb-95a6-4dbc-8f2a-afddc2a2cd57",
            "version": [0, 8, 0],
        }],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, game_id in sorted(entity_games.items()):
        identifier = f"junket:{name}"
        behavior = {
            "format_version": "1.21.0",
            "minecraft:entity": {
                "description": {
                    "identifier": identifier,
                    "is_spawnable": False,
                    "is_summonable": True,
                    "properties": {
                        "junket:frame": {
                            "type": "int", "range": [0, 6], "default": 0, "client_sync": True,
                        },
                    },
                },
                "components": {
                    "minecraft:physics": {"has_gravity": False, "has_collision": False},
                    "minecraft:collision_box": {"width": 2.0, "height": 2.5},
                    "minecraft:pushable": {
                        "is_pushable": False, "is_pushable_by_piston": False,
                    },
                    "minecraft:damage_sensor": {
                        "triggers": [{"cause": "all", "deals_damage": "no"}],
                    },
                    "minecraft:persistent": {},
                    "minecraft:nameable": {
                        "always_show": True, "allow_name_tag_renaming": False,
                    },
                    "minecraft:interact": {
                        "interactions": [{
                            "on_interact": {"event": "junket:touch", "target": "self"},
                            "interact_text": "action.interact.junket",
                        }],
                    },
                },
                "events": {"junket:touch": {}},
            },
        }
        (ENTITIES / f"{name}.json").write_text(
            json.dumps(behavior, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        client = {
            "format_version": "1.10.0",
            "minecraft:client_entity": {
                "description": {
                    "identifier": identifier,
                    "materials": {"default": "entity_alphatest"},
                    "textures": {"default": f"textures/entity/junket/{game_id}"},
                    "geometry": {"default": f"geometry.junket.{game_id}"},
                    "render_controllers": ["controller.render.junket"],
                },
            },
        }
        (RP_ENTITIES / f"junket_{name}.entity.json").write_text(
            json.dumps(client, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RP_RENDER / "junket.render_controllers.json").write_text(json.dumps({
        "format_version": "1.8.0",
        "render_controllers": {
            "controller.render.junket": {
                "geometry": "Geometry.default",
                "materials": [{"*": "Material.default"}],
                "textures": ["Texture.default"],
            },
        },
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    texts = RP / "texts"
    texts.mkdir(parents=True, exist_ok=True)
    (texts / "languages.json").write_text('["ja_JP", "en_US"]\n', encoding="utf-8")
    for locale, value in (("ja_JP", "操作する"), ("en_US", "Interact")):
        path = texts / f"{locale}.lang"
        path.write_text(f"action.interact.junket={value}\n", encoding="utf-8")


def venue_plan(games):
    plan = []
    for index, game in enumerate(games):
        game_id = game["id"]
        size = SIZES[game_id]
        origin = [WORLD_ORIGIN[0] + index * SPACING, WORLD_ORIGIN[1], WORLD_ORIGIN[2]]
        special = game_id in SOURCE_ORIGINS
        runtime_origin = list(origin)
        if special:
            source = SOURCE_ORIGINS[game_id]
            arrival_delta = game["source_arrival"][1] - source[1]
            runtime_origin[1] = origin[1] + 2 - arrival_delta
            destination = {
                "x": runtime_origin[0] + game["source_arrival"][0] - source[0],
                "y": runtime_origin[1] + arrival_delta,
                "z": runtime_origin[2] + game["source_arrival"][2] - source[2],
            }
        else:
            destination = {
                "x": origin[0] + size[0] // 2 + 0.5,
                "y": origin[1] + 2,
                "z": origin[2] + size[2] - 5.5,
            }
        cx = origin[0] + size[0] // 2
        seats = []
        for seat in range(game["max_players"] or game["min_players"]):
            angle = (2 * math.pi * seat) / max(1, game["max_players"] or game["min_players"])
            seats.append({
                "x": round(cx + math.sin(angle) * 5, 2),
                "y": origin[1] + 2,
                "z": round(destination["z"] - 6 + math.cos(angle) * 4, 2),
            })
        plan.append({
            "id": game_id,
            "title": game["title"],
            "structure": f"junket:{game_id}_hall",
            "origin": origin,
            "runtime_origin": runtime_origin,
            "size": list(size),
            "destination": destination,
            "control": {
                "x": origin[0] + size[0] // 2 + 0.5,
                "y": origin[1] + 2.2,
                "z": origin[2] + size[2] // 2 + 0.5,
            },
            "return_point": {
                "x": origin[0] + size[0] // 2 - 2.5,
                "y": origin[1] + 2,
                "z": origin[2] + size[2] - 3.5,
            },
            "seats": seats,
            "special_runtime": special,
            "max_players": game["max_players"] or game["min_players"],
        })
    return plan


def runtime_source(plan):
    body = json.dumps({
        "VENUE_SCHEMA": 1,
        "VENUE_INSTALL_KEY": "junket:original_world_v1",
        "VENUES": plan,
    }, ensure_ascii=False, indent=2)
    return (
        "// Generated by scripts/minecraft/build_junket_original_world.py.\n"
        "// Original block structures and original model textures only.\n"
        f"export const VENUE_CONFIG = Object.freeze({body});\n"
    )


def write_runtime(plan):
    GENERATED.write_text(runtime_source(plan), encoding="utf-8")


def check():
    games = load_games()
    plan = venue_plan(games)
    if not GENERATED.exists() or GENERATED.read_text(encoding="utf-8") != runtime_source(plan):
        raise SystemExit("generated JUNKET venue runtime is stale")
    for game in games:
        path = STRUCTURES / f"{game['id']}_hall.mcstructure"
        if not path.exists():
            raise SystemExit(f"missing JUNKET venue structure: {path.relative_to(ROOT)}")
        with path.open("rb") as stream:
            root = n.File.parse(stream, byteorder="little")
        if [int(value) for value in root["size"]] != list(SIZES[game["id"]]):
            raise SystemExit(f"JUNKET venue size mismatch: {game['id']}")
    for name in SPECIAL_ENTITIES:
        for entity in SPECIAL_ENTITIES[name]:
            if not (ENTITIES / f"{entity}.json").exists():
                raise SystemExit(f"missing JUNKET entity: {entity}")
    print(f"JUNKET original world assets are current: {len(games)} venues")


def build():
    games = load_games()
    plan = venue_plan(games)
    STRUCTURES.mkdir(parents=True, exist_ok=True)
    for game in games:
        game_id = game["id"]
        structure = Structure(SIZES[game_id])
        decorate(structure, game_id)
        structure.save(STRUCTURES / f"{game_id}_hall.mcstructure")
    for index in range(13):
        beam = Structure((13, 13, 1))
        beam.fill((0, 0, 0), (12, 12, 0), "minecraft:light_blue_stained_glass")
        beam.fill((5, 0, 0), (7, 12, 0), "minecraft:glowstone")
        beam.save(STRUCTURES / f"temperance_beam_{index}.mcstructure")
    write_geometry()
    for index, game in enumerate(games):
        write_texture(index, game["id"])
    write_entities()
    write_runtime(plan)
    print(f"JUNKET original world assets: {len(games)} venues")


if __name__ == "__main__":
    check() if "--check" in sys.argv else build()
