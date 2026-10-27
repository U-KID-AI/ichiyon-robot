"""Build independently authored JUNKET substitute venues and runtime metadata.

This generator does not read the licensed map archive, and the resulting halls
are not source-faithful reproductions of its architecture. It creates original
block structures, models and textures that implement the verified game rules in
``junket_games.json``. The private source archive is used only for offline
research; no licensed source asset is copied into this output.
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
HOPE_PIT_DEPTH = 4

SIZES = {
    "sparrow": (27, 13, 29),
    "temperance": (29, 33, 62),
    "lucy": (31, 14, 29),
    "silence": (31, 13, 33),
    "jinn": (28, 38, 37),
    "ace": (31, 14, 29),
    "grail": (29, 14, 31),
    "hope": (23, 18, 27),
    "auction": (29, 14, 31),
    "fire": (37, 14, 29),
    "pig": (31, 14, 29),
    "light": (29, 14, 29),
    "mine": (37, 16, 39),
    "clock": (31, 14, 31),
    "walker": (31, 26, 29),
    "garden": (33, 14, 33),
    "rabbit": (48, 14, 31),
}

SPECIAL_PROVIDERS = {
    "sparrow": {
        "collection": "junket:venue_fixture",
        "role_property": "junket:sparrow_role",
    },
    "temperance": {
        "collection": "junket:temperance_fixture",
        "role_property": "junket:temperance_role",
    },
    "lucy": {
        "collection": "junket:lucy_fixture",
        "role_property": "junket:lucy_role",
    },
    "silence": {
        "collection": "junket:silence_fixture",
        "role_property": "junket:silence_role",
    },
    "jinn": {
        "collection": "junket:jinn_fixture",
        "role_property": "junket:jinn_role",
    },
}

SPECIAL_LAYOUTS = {
    "sparrow": {
        "destination": (13.5, 2, 23.5),
        "return_point": (10.5, 2, 23.5),
        "seats": [(10.5, 2, 19.5), (16.5, 2, 19.5)],
        "fixtures": [
            ("reception", "minecraft:npc", (15.5, 2, 25.5), "ウラギリスズメ受付 / 参加・操作・再戦",
             ["junket:reception", "junket:sparrow:control"], {}),
            ("return", "minecraft:npc", (10.5, 2, 25.5), "入口へ戻る / JUNKET",
             ["junket:return"], {}),
            ("bigbox", "junket:bigbox", (7.5, 2, 15.5), "大箱（全額）",
             ["junket:venue_box"], {"junket:box": "big"}),
            ("littlebox", "junket:littlebox", (18.5, 2, 15.5), "小箱（半額）",
             ["junket:venue_box"], {"junket:box": "small"}),
            ("diamond", "junket:diamond", (13.5, 5.5, 13.5), "宝石（見本）", [], {}),
            ("tv", "junket:tv", (13.5, 5.2, 4.8), "ウラギリスズメ", [], {}),
        ],
        "points": {
            "box_pose": [(7.5, 2, 13.5), (18.5, 2, 13.5)],
            # The stage platform occupies y=2..3 and its diamond deck is y=4.
            # The partition is a 2.5-block entity spawned during the hide
            # animation, so both references must sit on the deck surface.
            "partition": (13.5, 5.0, 15.5),
            "stage_center": (13.5, 5.0, 15.5),
        },
    },
    "temperance": {
        "destination": (14.5, 2, 57.5),
        "return_point": (11.5, 2, 57.5),
        "seats": [(8.5, 2, 46.5), (20.5, 2, 46.5)],
        "fixtures": [
            ("reception", "minecraft:npc", (17.5, 2, 57.5), "ブルー・テンパランス受付 / 参加・再戦",
             ["junket:temperance_reception"], {}),
            ("return", "minecraft:npc", (11.5, 2, 57.5), "入口へ戻る / JUNKET",
             ["junket:return"], {}),
            ("tv", "junket:temperance_tv", (14.5, 6.2, 4.8), "ブルー・テンパランス", [], {}),
            ("base", "junket:temperance_base", (14.5, 2.5, 38.5), "", [], {}),
            ("balance0", "junket:temperance_balance", (8.5, 15.5, 44.0), "1P", [], {}),
            ("balance1", "junket:temperance_balance", (20.5, 15.5, 44.0), "2P", [], {}),
            ("stone0", "junket:temperance_stone", (8.5, 15.5, 45.5), "石 0", [], {}),
            ("stone1", "junket:temperance_stone", (20.5, 15.5, 45.5), "石 0", [], {}),
        ] + [
            (f"card{index}", "junket:temperance_card", (19.5, 3.5, z), f"カード {index + 1}",
             ["junket:temperance_control"], {"junket:temperance_slot": index})
            for index, z in enumerate((45.5, 47.0, 48.5))
        ] + [
            (f"frame{index}", "junket:temperance_frame", (19.5, 3.45, z), "",
             [], {"junket:temperance_slot": index})
            for index, z in enumerate((45.5, 47.0, 48.5))
        ],
        "points": {
            "beam_origin": (8.0, 10.0, 44.0),
            "card_start": (19.5, 3.5, 45.5),
            "balance_radius": 0.06,
            "stage_center": (14.5, 2, 38.5),
        },
    },
    "lucy": {
        "destination": (15.5, 2, 24.5),
        "return_point": (12.5, 2, 24.5),
        "seats": [(9.5, 2, 10.5), (20.5, 2, 10.5)],
        "fixtures": [
            ("reception", "minecraft:npc", (17.5, 2, 24.5), "気分屋ルーシー受付 / 参加・再戦",
             ["junket:lucy_reception"], {}),
            ("return", "minecraft:npc", (12.5, 2, 24.5), "入口へ戻る / JUNKET",
             ["junket:return"], {}),
            ("box0", "junket:lucy_box", (9.5, 2, 12.5), "1P金庫",
             ["junket:lucy_box"], {"junket:lucy_seat": 0}),
            ("box1", "junket:lucy_box", (20.5, 2, 12.5), "2P金庫",
             ["junket:lucy_box"], {"junket:lucy_seat": 1}),
            ("tv", "junket:lucy_tv", (15.5, 6.2, 4.8), "気分屋ルーシー", [], {}),
            ("doll", "junket:lucy_doll", (4.5, 2, 12.5), "", [], {}),
        ] + [
            (f"keyhole_{box}_{row}_{column}", "junket:lucy_keyhole",
             (center + column - 2, 3.0 + row, 8.0),
             f"{box + 1}P金庫 {row + 1}面 鍵穴 {column + 1}",
             ["junket:lucy_control", "junket:lucy_keyhole"],
             {"junket:lucy_box": box, "junket:lucy_row": row, "junket:lucy_column": column})
            for box, center in enumerate((9.5, 20.5))
            for row in range(5)
            for column in range(5)
        ] + [
            (f"confirm_{box}_{row}", "junket:lucy_prop", (center + 3.0, 3.0 + row, 9.0),
             f"{box + 1}P金庫 {row + 1}面 確定",
             ["junket:lucy_control", "junket:lucy_confirm"],
             {"junket:lucy_box": box, "junket:lucy_row": row})
            for box, center in enumerate((9.5, 20.5))
            for row in range(5)
        ],
        "points": {
            "keyhole_focus": (15.5, 4.5, 8.0),
            "vault_focus": (15.5, 2, 12.5),
        },
    },
    "silence": {
        "destination": (15.5, 2, 28.5),
        "return_point": (12.5, 2, 28.5),
        "seats": [(9.5, 2, 24.5), (20.5, 2, 24.5)],
        "fixtures": [
            ("reception", "minecraft:npc", (17.5, 2, 28.5), "サウンド・オブ・サイレンス受付 / 参加・再戦",
             ["junket:silence_reception"], {}),
            ("return", "minecraft:npc", (12.5, 2, 28.5), "入口へ戻る / JUNKET",
             ["junket:return"], {}),
            ("guard", "minecraft:npc", (15.5, 2, 10.5), "耳栓を使う / 再生中のみ",
             ["junket:silence_guard"], {}),
            ("setter", "junket:silence_covering", (13.5, 2, 13.0), "レコードをセット",
             ["junket:silence_control", "junket:silence_setter"], {}),
            ("returner", "junket:silence_covering", (17.5, 2, 13.0), "レコードを返却",
             ["junket:silence_control", "junket:silence_returner"], {}),
            ("tv", "junket:silence_tv", (15.5, 5.2, 4.8), "サウンド・オブ・サイレンス", [], {}),
            ("cover", "junket:silence_covering", (15.5, 3, 18.5), "",
             ["junket:silence_control", "junket:silence_cover"], {}),
        ] + [
            (f"jacket{index}", "junket:silence_jacket", (x, 3, 19), f"ジャケット {index + 1}",
             ["junket:silence_control", "junket:silence_jacket"], {"junket:silence_slot": index})
            for index, x in enumerate((10.5, 15.5, 20.5))
        ] + [
            (f"cd{index}", "junket:silence_cd", (x, 2.1, 17.8), "",
             ["junket:silence_control", "junket:silence_cd"], {"junket:silence_slot": index})
            for index, x in enumerate((10.5, 15.5, 20.5))
        ],
        "points": {
            "playback_seats": [(11.5, 2, 6.5), (13.5, 2, 6.5)],
            "sound_room_lo": (10, 2, 4),
            "sound_room_hi": (14, 4, 8),
            "jacket_row": [(10.5, 3, 19), (15.5, 3, 19), (20.5, 3, 19)],
        },
    },
    "jinn": {
        "destination": (14.5, 2, 32.5),
        "return_point": (11.5, 2, 32.5),
        "seats": [(6.5, 2, 9.5), (21.5, 2, 9.5)],
        "fixtures": [
            ("reception", "minecraft:npc", (17.5, 2, 32.5), "ジャックポット・ジニー受付 / 参加・再戦",
             ["junket:jinn_reception"], {}),
            ("return", "minecraft:npc", (11.5, 2, 32.5), "入口へ戻る / JUNKET",
             ["junket:return"], {}),
            ("tv", "junket:jinn_tv", (14.5, 6.2, 4.8), "ジャックポット・ジニー", [], {}),
            ("covering", "junket:jinn_covering", (14.5, 2.5, 11), "", [], {}),
            *[
                (f"card_{card}_{seat}", "junket:jinn_card", (x, 4.2, 11),
                 f"{seat + 1}P {name}", ["junket:jinn_card"],
                 {"junket:jinn_seat": seat, "junket:jinn_card_type": card})
                for seat, positions in enumerate(((3.5, 6.5, 9.5), (18.5, 21.5, 24.5)))
                for card, name, x in zip(("gold", "thief", "demon"),
                                         ("黄金カード", "盗賊カード", "魔人カード"), positions)
            ],
            ("lever0", "junket:jinn_lever", (6.5, 6.2, 13), "1Pレバー", ["junket:jinn_lever"], {"junket:jinn_seat": 0}),
            ("lever1", "junket:jinn_lever", (21.5, 6.2, 13), "2Pレバー", ["junket:jinn_lever"], {"junket:jinn_seat": 1}),
            ("surrender0", "minecraft:npc", (3.5, 2, 10.5), "1P 降参", ["junket:jinn_surrender"], {"junket:jinn_seat": 0}),
            ("surrender1", "minecraft:npc", (24.5, 2, 10.5), "2P 降参", ["junket:jinn_surrender"], {"junket:jinn_seat": 1}),
        ],
        "points": {
            "covering_focus": (14.5, 3.5, 11),
            "settlement": [(3.5, 2, 10.5), (24.5, 2, 10.5)],
        },
    },
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
    "hope": ["hope_tv", "hope_code", "hope_code_display"],
    "rabbit": ["rabbit_floor"],
    "walker": ["walker_label", "walker_bomb"],
}

SPECIAL_IDS = frozenset(SPECIAL_LAYOUTS)
GENERIC_ROTATION = 180

CONTROL_ACTIONS = {
    "ace": [
        "ace_table_100_up", "ace_table_100_down", "ace_table_10_up",
        "ace_table_10_down", "ace_table_1_up", "ace_table_1_down",
        "ace_table_confirm", "ace_raise_100_up", "ace_raise_100_down",
        "ace_raise_10_up", "ace_raise_10_down", "ace_raise_1_up",
        "ace_raise_1_down", "ace_raise_confirm",
    ] + [
        action
        for seat in range(1, 5)
        for action in (
            *[f"ace_die_{seat}_{die}" for die in range(1, 6)],
            f"ace_tray_{seat}", f"ace_call_{seat}", f"ace_drop_{seat}",
            f"ace_raise_{seat}", f"ace_reroll_confirm_{seat}",
        )
    ],
    "grail": [
        "grail_cup_1", "grail_cup_2", "grail_cup_3",
        "grail_potion_1", "grail_potion_2", "grail_potion_3",
    ],
    "hope": [f"floor_{index}" for index in range(1, 17)],
    "auction": [
        f"auction_bid_{seat}_{value}"
        for seat in range(1, 5)
        for value in range(1, 5)
    ],
    "fire": [
        "rock_hot_1", "scissors_hot_1", "paper_hot_1",
        "rock_ice_1", "scissors_ice_1", "paper_ice_1",
        "rock_hot_2", "scissors_hot_2", "paper_hot_2",
        "rock_ice_2", "scissors_ice_2", "paper_ice_2",
    ],
    "pig": [
        f"pig_card_{seat}_{card}"
        for seat in range(1, 5) for card in range(1, 5)
    ] + [
        f"pig_target_{seat}_{target}"
        for seat in range(1, 5) for target in range(1, 5) if target != seat
    ],
    "light": [
        f"light_grave_{seat}_{slot}"
        for seat in range(1, 3)
        for slot in range(1, 4)
    ],
    "mine": [
        f"mine_rock_{seat}_{slot}"
        for seat in range(1, 3)
        for slot in range(1, 5)
    ],
    "clock": [
        "clock_hour_plus", "clock_hour_minus", "clock_minute_plus",
        "clock_minute_minus", "clock_time_confirm",
        "clock_sector_cw", "clock_sector_ccw", "clock_sector_confirm",
    ],
    "walker": [
        "walker_stop_1", "walker_stop_2",
        "bomb_prev_1", "bomb_prev_2",
        "bomb_next_1", "bomb_next_2",
        "bomb_confirm_1", "bomb_confirm_2",
    ],
    "garden": ["switch", "double", "open", "pick_1", "pick_2"],
    "rabbit": [f"floor_{index}" for index in range(1, 61)]
              + [f"guard_{index}" for index in range(3, 7)],
}


def load_games():
    value = json.loads(CATALOG.read_text(encoding="utf-8"))
    games = value["games"]
    if len(games) != 17 or set(game["id"] for game in games) != set(SIZES):
        raise ValueError("venue generator must cover the verified 17-game inventory")
    return games


def _state_value(value):
    if isinstance(value, bool):
        return n.Byte(1 if value else 0)
    if isinstance(value, int):
        return n.Int(value)
    return n.String(str(value))


def block(name: str, states: dict | None = None) -> n.Compound:
    return n.Compound({
        "name": n.String(name),
        "states": n.Compound({
            str(key): _state_value(value) for key, value in sorted((states or {}).items())
        }),
        "version": n.Int(BLOCK_VERSION),
    })


AIR = block("minecraft:air")


class Structure:
    def __init__(self, size):
        self.size = tuple(size)
        self.blocks = {}
        self.block_states = {}

    def contains(self, x: int, y: int, z: int) -> bool:
        return 0 <= x < self.size[0] and 0 <= y < self.size[1] and 0 <= z < self.size[2]

    def set(self, x, y, z, name, states=None):
        if not self.contains(x, y, z):
            return
        position = (x, y, z)
        self.blocks[position] = name
        normalized = tuple(sorted((str(key), value) for key, value in (states or {}).items()))
        if normalized:
            self.block_states[position] = normalized
        else:
            self.block_states.pop(position, None)

    def set_required(self, x: int, y: int, z: int, name: str, states=None) -> None:
        if not self.contains(x, y, z):
            raise ValueError(
                f"required block is outside structure bounds: {(x, y, z)} size={self.size}")
        self.set(x, y, z, name, states)

    def fill(self, lo, hi, name, states=None):
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    self.set(x, y, z, name, states)

    def fill_required(self, lo, hi, name, states=None) -> None:
        for axis, (low, high, limit) in enumerate(zip(lo, hi, self.size)):
            if low < 0 or high >= limit or low > high:
                raise ValueError(
                    f"required fill is outside structure bounds on axis {axis}: "
                    f"{low}..{high} size={limit}")
        self.fill(lo, hi, name, states)

    def save(self, path: Path):
        sx, sy, sz = self.size
        palette, index = [AIR], {("minecraft:air", ()): 0}
        values = []
        for x in range(sx):
            for y in range(sy):
                for z in range(sz):
                    position = (x, y, z)
                    name = self.blocks.get(position, "minecraft:air")
                    palette_key = (name, self.block_states.get(position, ()))
                    if palette_key not in index:
                        index[palette_key] = len(palette)
                        palette.append(block(name, dict(palette_key[1])))
                    values.append(index[palette_key])
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

    def layout_rle(self):
        """Return a compact full-volume block layout for runtime verification."""
        palette = ["minecraft:air"]
        palette_index = {("minecraft:air", ()): 0}
        runs = []
        for x in range(self.size[0]):
            for y in range(self.size[1]):
                for z in range(self.size[2]):
                    position = (x, y, z)
                    name = self.blocks.get(position, "minecraft:air")
                    states = self.block_states.get(position, ())
                    palette_key = (name, states)
                    if palette_key not in palette_index:
                        palette_index[palette_key] = len(palette)
                        palette.append(name if not states else {"type": name, "states": dict(states)})
                    index = palette_index[palette_key]
                    if runs and runs[-1][0] == index:
                        runs[-1][1] += 1
                    else:
                        runs.append([index, 1])
        return {
            "order": "x,y,z",
            "cells": self.size[0] * self.size[1] * self.size[2],
            "palette": palette,
            "runs": ";".join(f"{index},{count}" for index, count in runs),
        }


def _feature(structure: Structure, meta: dict, x: int, y: int, z: int, name: str) -> None:
    structure.set_required(x, y, z, name)
    meta["features"].append([x, y, z, name])


def _fill_feature(structure: Structure, meta: dict, lo: tuple[int, int, int],
                  hi: tuple[int, int, int], name: str) -> None:
    structure.fill_required(lo, hi, name)
    for x in range(lo[0], hi[0] + 1):
        for y in range(lo[1], hi[1] + 1):
            for z in range(lo[2], hi[2] + 1):
                meta["features"].append([x, y, z, name])


def _control(meta: dict, label: str, x: float, y: float, z: float,
             action: str | None = None) -> None:
    control = {"label": label, "location": [round(x, 2), round(y, 2), round(z, 2)]}
    if action:
        control["action"] = action
    meta["controls"].append(control)


def _display(meta: dict, label: str, x: float, y: float, z: float) -> None:
    meta["displays"].append({"label": label, "location": [round(x, 2), round(y, 2), round(z, 2)]})


def decorate(structure: Structure, game_id: str) -> dict:
    """Build one independently authored hall and return its repair metadata."""
    sx, sy, sz = structure.size
    floor, wall, accent, trim = (f"minecraft:{name}" for name in STYLES[game_id])
    meta = {"design": game_id, "features": [], "controls": [], "displays": []}
    structure.fill((0, 0, 0), (sx - 1, 0, sz - 1), wall)
    structure.fill((1, 1, 1), (sx - 2, 1, sz - 2), floor)
    for y in range(2, sy - 1):
        structure.fill((0, y, 0), (sx - 1, y, 0), wall)
        structure.fill((0, y, sz - 1), (sx - 1, y, sz - 1), wall)
        structure.fill((0, y, 0), (0, y, sz - 1), wall)
        structure.fill((sx - 1, y, 0), (sx - 1, y, sz - 1), wall)
    structure.fill((1, sy - 1, 1), (sx - 2, sy - 1, sz - 2), trim)
    for x in range(0, sx, 4):
        for z in range(0, sz, 4):
            structure.set(x, sy - 1, z, "minecraft:glowstone")
    for x in range(1, sx - 1):
        structure.set(x, sy - 2, 0, accent if x % 3 else "minecraft:light")
        structure.set(x, sy - 2, sz - 1, accent if x % 3 else "minecraft:light")
    for z in range(1, sz - 1):
        structure.set(0, sy - 2, z, accent if z % 3 else "minecraft:light")
        structure.set(sx - 1, sy - 2, z, accent if z % 3 else "minecraft:light")
    entrance = sx // 2
    structure.fill((entrance - 1, 2, sz - 1), (entrance + 1, 4, sz - 1), "minecraft:air")
    structure.fill((entrance - 2, 2, 0), (entrance + 2, 3, 0), "minecraft:air")
    # A 3x3 clear arrival bay and two-block-high aisles are mandatory.
    structure.fill((entrance - 2, 2, sz - 7), (entrance + 2, 3, sz - 2), "minecraft:air")
    cx, cz = sx // 2, sz // 2
    structure.fill((cx - 1, 2, 3), (cx + 1, 3, sz - 8), "minecraft:air")

    if game_id == "sparrow":
        for side in (-1, 1):
            x = cx + side * 8
            structure.fill((x - 2, 2, 4), (x + 2, 4, 7), trim)
            structure.fill((x - 1, 3, 5), (x + 1, 4, 6), accent)
            _feature(structure, meta, x, 2, 8, "minecraft:diamond_block")
        _fill_feature(structure, meta, (cx - 4, 2, cz - 2), (cx + 4, 3, cz + 2),
                      "minecraft:crimson_planks")
        _fill_feature(structure, meta, (cx - 3, 4, cz - 1), (cx + 3, 4, cz + 1),
                      "minecraft:diamond_block")
        _fill_feature(structure, meta, (cx - 6, 4, 2), (cx + 6, 7, 2),
                      "minecraft:black_concrete")
        _fill_feature(structure, meta, (cx - 4, 5, 3), (cx + 4, 6, 3),
                      "minecraft:redstone_lamp")
        _display(meta, "両替所テレビ", cx + 0.5, 5.0, 3.7)
    elif game_id == "temperance":
        for side in (-1, 1):
            x = cx + side * 7
            _fill_feature(structure, meta, (x - 2, 2, cz - 5), (x + 2, 3, cz + 5),
                          "minecraft:polished_diorite")
            structure.fill((x, 4, cz - 5), (x, sy - 4, cz - 5), "minecraft:light_blue_stained_glass")
            structure.fill((x, 4, cz + 5), (x, sy - 4, cz + 5), "minecraft:light_blue_stained_glass")
        _fill_feature(structure, meta, (cx - 2, 2, cz - 1), (cx + 2, 4, cz + 1),
                      "minecraft:quartz_bricks")
        _fill_feature(structure, meta, (cx - 9, 5, 2), (cx + 9, 8, 2),
                      "minecraft:cherry_planks")
        _display(meta, "天秤表示", cx + 0.5, 6.0, 3.7)
    elif game_id == "lucy":
        for index, x in enumerate((cx - 10, cx - 5, cx, cx + 5, cx + 10)):
            structure.fill((x - 1, 2, 4), (x + 1, 4, 5), "minecraft:polished_blackstone")
            _feature(structure, meta, x, 3, 6, "minecraft:gold_block")
            _feature(structure, meta, x, 4, 6, "minecraft:magenta_wool")
            _control(meta, f"鍵穴{index + 1}", x + 0.5, 5.05, 5.5)
        _fill_feature(structure, meta, (cx - 2, 2, cz), (cx + 2, 3, cz + 2),
                      "minecraft:blackstone")
        _fill_feature(structure, meta, (cx - 5, 5, 2), (cx + 5, 7, 2),
                      "minecraft:black_concrete")
        _display(meta, "金庫表示", cx + 0.5, 6.0, 3.7)
    elif game_id == "silence":
        for side in (-1, 1):
            x = cx + side * 9
            _fill_feature(structure, meta, (x - 3, 2, 4), (x + 3, 3, 8),
                          "minecraft:deepslate_tiles")
            structure.fill((x - 3, 4, 4), (x + 3, 7, 4), "minecraft:yellow_wool")
            _control(meta, f"録音ブース{1 if side < 0 else 2}", x + 0.5, 4.05, 9.0)
        # Keep the jukebox behind the chooseable CD row at z=cz+1; the discs
        # are physical entities and must not be embedded in a solid block.
        _fill_feature(structure, meta, (cx - 2, 2, cz - 3), (cx + 2, 3, cz - 1),
                      "minecraft:jukebox")
        _fill_feature(structure, meta, (cx - 5, 4, 2), (cx + 5, 6, 2),
                      "minecraft:black_concrete")
        _display(meta, "共有時間表示", cx + 0.5, 5.0, 3.7)
    elif game_id == "jinn":
        for level, radius in enumerate((6, 4, 2)):
            y = 2 + level * 2
            structure.fill((cx - radius, y, cz - radius), (cx + radius, y + 1, cz + radius),
                           "minecraft:cut_sandstone" if level % 2 == 0 else "minecraft:sandstone")
        _fill_feature(structure, meta, (cx - 1, 8, cz - 1), (cx + 1, 10, cz + 1),
                      "minecraft:gold_block")
        for x in (cx - 8, cx + 8):
            structure.fill((x - 1, 2, cz - 6), (x + 1, 5, cz - 4), "minecraft:orange_wool")
            _control(meta, "レバー", x + 0.5, 6.05, cz - 5.0)
        _fill_feature(structure, meta, (cx - 7, 5, 2), (cx + 7, 9, 2),
                      "minecraft:black_concrete")
        _display(meta, "ジニー表示", cx + 0.5, 6.0, 3.7)
    elif game_id == "ace":
        # Source-faithful interaction model, independently built in Bedrock:
        # three-digit table/raise entry, four dice trays, five selectable dice
        # per seat, and separate call/drop/raise controls. The source uses
        # floating Java text/interaction windows; here they become fixed props
        # attached to the hall so they survive chunk unload and restart.
        _fill_feature(structure, meta, (cx - 5, 2, cz - 4), (cx + 5, 2, cz + 4),
                      "minecraft:green_wool")
        _fill_feature(structure, meta, (cx - 5, 2, cz - 4), (cx - 5, 2, cz + 4),
                      "minecraft:gold_block")
        _fill_feature(structure, meta, (cx + 5, 2, cz - 4), (cx + 5, 2, cz + 4),
                      "minecraft:gold_block")
        seat_points = ((cx - 5, cz), (cx, cz - 5), (cx + 5, cz), (cx, cz + 5))
        seat_colors = ("red", "yellow", "blue", "magenta")
        for index, (sx, sz) in enumerate(seat_points):
            _fill_feature(structure, meta, (sx, 1, sz), (sx + 1, 1, sz + 1),
                          f"minecraft:{seat_colors[index]}_wool")
        for index, x in enumerate(range(5, 15, 2)):
            _feature(structure, meta, x, 3, cz,
                     "minecraft:white_concrete" if index % 2 else "minecraft:black_concrete")

        # Shared three-digit entry console, matching the source order of
        # hundreds, tens, ones, then confirm.
        _fill_feature(structure, meta, (3, 2, 1), (28, 9, 1), "minecraft:black_concrete")
        shared = (
            ("ace_table_100_up", "場代 百の桁 +"), ("ace_table_100_down", "場代 百の桁 -"),
            ("ace_table_10_up", "場代 十の桁 +"), ("ace_table_10_down", "場代 十の桁 -"),
            ("ace_table_1_up", "場代 一の桁 +"), ("ace_table_1_down", "場代 一の桁 -"),
            ("ace_table_confirm", "場代 確定"),
        )
        for index, (action, label) in enumerate(shared):
            x = 4 + index * 3
            _feature(structure, meta, x, 3, 2, "minecraft:gold_block")
            _control(meta, label, x + 0.5, 4.05, 3.0, action)
        raise_shared = (
            ("ace_raise_100_up", "レイズ 百の桁 +"), ("ace_raise_100_down", "レイズ 百の桁 -"),
            ("ace_raise_10_up", "レイズ 十の桁 +"), ("ace_raise_10_down", "レイズ 十の桁 -"),
            ("ace_raise_1_up", "レイズ 一の桁 +"), ("ace_raise_1_down", "レイズ 一の桁 -"),
            ("ace_raise_confirm", "レイズ 確定"),
        )
        for index, (action, label) in enumerate(raise_shared):
            x = 4 + index * 3
            _feature(structure, meta, x, 6, 2, "minecraft:redstone_lamp")
            _control(meta, label, x + 0.5, 6.75, 3.0, action)
        _display(meta, "第nゲーム／場代・現在ベット", cx + 0.5, 7.0, cz + 4.5)
        _display(meta, "4席コイン・手札・結果", cx + 0.5, 9.4, cz + 4.5)

        # Four seat stations. Each has a full five-die rack and its own tray,
        # action controls, die selection and reroll confirmation. This replaces
        # the former card-seat stubs and keeps every rule action physical.
        seat_layouts = (
            (2.0, (4.0, 7.0, 10.0, 13.0, 16.0)),
            (float(sx - 3), (4.0, 7.0, 10.0, 13.0, 16.0)),
            (2.0, (18.0, 20.25, 22.5, 24.75, 27.0)),
            (float(sx - 3), (18.0, 20.25, 22.5, 24.75, 27.0)),
        )
        for seat, (x, positions) in enumerate(seat_layouts):
            number = seat + 1
            _fill_feature(structure, meta, (int(x) - 1, 2, int(positions[0]) - 1),
                          (int(x) + 1, 9, int(positions[-1]) + 1), "minecraft:black_concrete")
            for die, z in enumerate(positions):
                _control(meta, f"{number}P ダイス{die + 1} 選択", x + 0.5, 3.05, z + 0.5,
                         f"ace_die_{number}_{die + 1}")
            action_row = (
                (f"ace_tray_{number}", f"{number}P ダイストレイ／机"),
                (f"ace_call_{number}", f"{number}P コール"),
                (f"ace_drop_{number}", f"{number}P ドロップ"),
                (f"ace_raise_{number}", f"{number}P レイズ"),
                (f"ace_reroll_confirm_{number}", f"{number}P 振り直し確定"),
            )
            for index, (action, label) in enumerate(action_row):
                z = positions[index]
                _control(meta, label, x + 0.5, 5.75, z + 0.5, action)
        _feature(structure, meta, cx, 1, 4, "minecraft:sea_lantern")
        _feature(structure, meta, cx, 1, sz - 5, "minecraft:sea_lantern")
    elif game_id == "grail":
        # The source hides a true cup and a holy potion independently, then
        # the grail-side player chooses a cup and the potion-side player
        # chooses a potion. Keep the two racks physically separate so a press
        # can never be interpreted as the wrong role's choice.
        _fill_feature(structure, meta, (3, 4, 2), (25, 12, 2),
                      "minecraft:black_concrete")
        for x in range(5, 26, 4):
            _feature(structure, meta, x, 4, 3, "minecraft:redstone_lamp")
        _display(meta, "TV ラウンド・体力・聖水", cx + 0.5, 4.5, 3.2)
        _display(meta, "進行表示", cx + 0.5, 7.5, 3.2)
        _display(meta, "結果表示", cx + 0.5, 10.5, 3.2)

        rack_specs = (
            ("cup", (4, 8, 12), "minecraft:purple_stained_glass",
             "minecraft:purple_concrete", "聖杯棚"),
            ("potion", (16, 20, 24), "minecraft:lime_stained_glass",
             "minecraft:lime_concrete", "ポーション棚"),
        )
        for kind, positions, glass, floor_name, label in rack_specs:
            _fill_feature(structure, meta, (positions[0] - 1, 2, 7),
                          (positions[-1] + 1, 2, 9), "minecraft:quartz_bricks")
            _fill_feature(structure, meta, (positions[0] - 1, 1, 7),
                          (positions[-1] + 1, 1, 9), floor_name)
            _fill_feature(structure, meta, (positions[0] - 1, 6, 7),
                          (positions[-1] + 1, 6, 9), glass)
            for index, x in enumerate(positions):
                _control(meta, f"{label}{index + 1}", x + 0.5, 3.05, 8.5,
                         f"grail_{kind}_{index + 1}")
        _display(meta, "聖杯棚 見出し", 8.5, 7.8, 8.5)
        _display(meta, "ポーション棚 見出し", 20.5, 7.8, 8.5)

        # Central covered exchange table. The cover is a frame at head height
        # so players can still walk the center aisle and approach either rack.
        _fill_feature(structure, meta, (cx - 6, 2, 15), (cx - 2, 3, 17),
                      "minecraft:smooth_quartz")
        _fill_feature(structure, meta, (cx + 2, 2, 15), (cx + 6, 3, 17),
                      "minecraft:smooth_quartz")
        _fill_feature(structure, meta, (cx - 3, 5, 15), (cx + 3, 5, 17),
                      "minecraft:black_concrete")
        for x in (cx - 3, cx + 3):
            for z in (15, 17):
                _fill_feature(structure, meta, (x, 2, z), (x, 4, z),
                              "minecraft:quartz_pillar")
        _feature(structure, meta, cx - 4, 4, 16, "minecraft:amethyst_cluster")
        _feature(structure, meta, cx + 4, 4, 16, "minecraft:amethyst_cluster")
    elif game_id == "hope":
        # game:hope fills a 16x16 barrier with sixteen 4x4 floor markers. The
        # source anchors are display/NPC coordinates, not the geometric centre
        # of each marker, so keep the exact physical cells in runtime metadata.
        colors = {
            1: "minecraft:red_wool", 2: "minecraft:yellow_wool",
            3: "minecraft:lime_wool", 4: "minecraft:light_blue_wool",
            5: "minecraft:magenta_wool",
        }
        names = {1: "サイコロ", 2: "目", 3: "ビール", 4: "注射", 5: "ハート"}
        pad_symbols = (2, 1, 4, 1, 3, 5, 3, 2, 1, 2, 1, 5, 5, 4, 3, 4)
        meta["hope_pads"] = []
        board_min_x, board_min_z = cx - 8, cz - 8
        for index, symbol in enumerate(pad_symbols):
            row, column = divmod(index, 4)
            x = board_min_x + column * 4
            z = board_min_z + row * 4
            color = colors[symbol]
            _fill_feature(structure, meta, (x, 1, z), (x + 3, 1, z + 3), color)
            # The floor control entity occupies y=2..3; keep the pad lamp
            # above its interaction space instead of embedding the entity.
            _feature(structure, meta, x + 1, 4, z + 1, "minecraft:glowstone")
            center_x, center_z = x + 1.5, z + 1.5
            _control(meta, f"床{index + 1} {names[symbol]}",
                     center_x, 2.05, center_z, f"floor_{index + 1}")
            meta["controls"][-1]["properties"] = {
                "junket:hope_pad": index + 1,
                "junket:hope_symbol": symbol,
                "junket:hope_symbol_name": names[symbol],
            }
            meta["hope_pads"].append({
                "pad": index + 1, "symbol": symbol, "symbol_name": names[symbol],
                "x": x + 1, "y": 1, "z": z + 1,
                "center_x": center_x, "center_z": center_z, "floor": color,
                "cells": [[x + dx, 1, z + dz] for dx in range(4) for dz in range(4)],
            })
        _fill_feature(structure, meta, (cx - 7, 7, cz - 1), (cx + 7, 7, cz + 1),
                      "minecraft:magenta_stained_glass")
        # The source keeps eliminated participants in a separate observation
        # room. The generic arrival bay is not a spectator area, so build an
        # elevated side gallery inside this hall and leave the arena floor
        # unobstructed.
        # The 16x16 floor leaves only three free columns on the source's side.
        # Keep the gallery there rather than carving through the playable pads.
        gallery_x = 20
        structure.fill((19, 5, cz - 8), (21, 7, cz + 7), "minecraft:air")
        structure.fill((19, 4, cz - 8), (21, 4, cz + 7), "minecraft:smooth_stone")
        structure.fill((19, 5, cz - 8), (19, 6, cz + 7), "minecraft:glass")
        structure.fill((19, 5, cz - 8), (21, 6, cz - 8), "minecraft:glass")
        structure.fill((19, 5, cz + 7), (21, 6, cz + 7), "minecraft:glass")
        structure.fill((19, 2, cz), (19, 4, cz), "minecraft:ladder")
        _feature(structure, meta, gallery_x, 4, cz, "minecraft:sea_lantern")
        # A dark screen wall carries the three live display entities. It sits
        # above the two-block-high entrance aisle.
        _fill_feature(structure, meta, (cx - 6, 8, 2), (cx + 6, 11, 2),
                      "minecraft:black_concrete")
        _fill_feature(structure, meta, (cx - 5, 9, 1), (cx + 5, 10, 1),
                      "minecraft:magenta_concrete")
        _display(meta, "危険図柄・ラウンド", cx + 0.5, sy - 4, 2.7)
        _display(meta, "生存者表示", cx + 0.5, sy - 5, 2.7)
        _display(meta, "進行・残り時間", cx + 0.5, sy - 6, 2.7)
        # The source installs six item-display TV surfaces around the judge
        # code at (121,58,280) and (121,60,280). These are independently
        # authored panels at the equivalent local anchors. They are added
        # before the pit shift so clearance and the saved structure use the
        # same final coordinates as runtime.
        meta["fixtures"] = [
            {
                "role": f"hope_tv_{surface}", "type": "junket:hope_tv",
                "location": [x, y, z], "name": "",
                "tags": ["junket:hope:apparatus", "junket:hope:tv"],
                "properties": {"junket:hope_surface": surface},
                "action": "hope_tv",
            }
            for surface, (x, y, z) in enumerate((
                (10.5, 11.0, 6.6), (7.5, 11.0, 6.6),
                (12.5, 11.0, 6.6), (10.5, 12.0, 6.6),
                (8.5, 9.0, 6.6), (12.5, 9.0, 6.6),
            ), start=1)
        ] + [{
            "role": "hope_code", "type": "junket:hope_code",
            "location": [16.5, 3.0, 6.5], "name": "",
            "tags": ["junket:hope:apparatus", "junket:hope:code"],
            "properties": {}, "action": "hope_code",
        }, {
            "role": "hope_code_display", "type": "junket:hope_code_display",
            "location": [16.5, 5.0, 6.5], "name": "",
            "tags": ["junket:hope:apparatus", "junket:hope:code_display"],
            "properties": {}, "action": "hope_code_display",
        }]
        # Source item displays are non-solid entities, but the two-block
        # runtime footprint must not inherit a decorative lamp or wall block.
        # Carve it before validation so the saved structure and runtime use the
        # exact same positions after the pit shift.
        for fixture in meta["fixtures"]:
            x, y, z = (math.floor(value) for value in fixture["location"])
            for height in (y, y + 1):
                structure.set_required(x, height, z, "minecraft:air")
        # Physical spectator-warp plate from the source hope-warp marker.
        _fill_feature(structure, meta, (cx + 3, 1, cz + 10),
                      (cx + 4, 1, cz + 11), "minecraft:cyan_glazed_terracotta")
        _feature(structure, meta, cx + 3, 2, cz + 10, "minecraft:sea_lantern")
        _display(meta, "観戦部屋ワープ", cx + 4.0, 3.1, cz + 11.5)
        meta["points"] = {
            "spectator": (20.5, 5, cz + 0.5),
            "warp": (cx + 3.5, 2, cz + 11.5),
            # The source lose.mcfunction transfers eliminated players to a
            # separate F room at (119,57.7,303), not to the observer point.
            "lose": (cx + 6.5, 2, cz + 11.5),
        }
        meta["hope_ai_spawns"] = [
            {
                "id": index + 1,
                "pad": index + 1,
                # Source traders stand at the judge markers, two blocks
                # south of the matching floor centre, not on the player
                # interaction entity.
                "x": pad["center_x"],
                "y": 2,
                "z": pad["center_z"] + 2,
            }
            for index, pad in enumerate(meta["hope_pads"])
        ] + [{
            # The seventeenth source trader shares the final judge marker at
            # (121,56,294), giving 17 traders for 16 falling floors.
            "id": 17, "pad": 17,
            "x": meta["hope_pads"][15]["center_x"], "y": 2,
            "z": meta["hope_pads"][15]["center_z"] + 2,
        }]
        # These anchors are authored before the pit shift. Without explicit
        # values the generic fallbacks use the lowered world origin and place
        # entrants and seats underneath the raised 16x16 pad floor.
        meta["destination"] = (cx + 0.5, 2, sz - 5.5)
        meta["return_point"] = (cx - 2.5, 2, sz - 5.5)
        meta["reception_point"] = (cx + 2.5, 2, sz - 5.5)
        meta["seat_locations"] = [
            (pad["center_x"], 2, pad["center_z"]) for pad in meta["hope_pads"]
        ]
    elif game_id == "auction":
        # Independently authored auction floor based on the source layout:
        # four bidders sit around a central diamond table. Each bidder has one
        # physical card for every value 1-4; the rule machine consumes one card
        # per seat per set. Colours follow the source's red/orange/green/blue
        # player markers without copying the licensed models or textures.
        _fill_feature(structure, meta, (cx - 2, 2, cz - 2), (cx + 2, 3, cz + 2),
                      "minecraft:smooth_stone")
        _feature(structure, meta, cx, 4, cz, "minecraft:diamond_block")
        _feature(structure, meta, cx, 5, cz, "minecraft:dark_oak_pressure_plate")
        seat_colors = ("minecraft:red_wool", "minecraft:orange_wool",
                       "minecraft:green_wool", "minecraft:blue_wool")
        stations = (
            # (base lo, base hi, card positions, card normal)
            ((cx - 6, 2, 5), (cx + 6, 3, 7),
             [(cx - 6 + index * 4, 6) for index in range(4)], "north"),
            ((22, 2, cz - 6), (24, 3, cz + 6),
             [(23, cz - 6 + index * 4) for index in range(4)], "east"),
            ((cx - 6, 2, 23), (cx + 6, 3, 25),
             [(cx - 6 + index * 4, 24) for index in range(4)], "south"),
            ((4, 2, cz - 6), (6, 3, cz + 6),
             [(5, cz - 6 + index * 4) for index in range(4)], "west"),
        )
        for seat, ((lo, hi, cards, facing), color) in enumerate(
                zip(stations, seat_colors), start=1):
            structure.fill(lo, hi, "minecraft:dark_oak_planks")
            for value, (x, z) in enumerate(cards, start=1):
                structure.set(x, 3, z, color)
                _control(meta, f"{seat}P 札{value}", x + 0.5, 4.05, z + 0.5,
                         f"auction_bid_{seat}_{value}")
        _fill_feature(structure, meta, (cx - 5, 5, 2), (cx + 5, 7, 2),
                      "minecraft:black_concrete")
        _display(meta, "ラウンド・落札札", cx + 0.5, 6.0, 3.7)
        _display(meta, "チーム別ダイヤ・支払い", cx + 0.5, 5.0, 3.7)
        _display(meta, "入札状況", cx + 0.5, 4.0, 3.7)
        _display(meta, "競売ダイヤ", cx + 0.5, 6.05, cz + 0.5)
        _display(meta, "1P 入札席（北）", cx + 0.5, 5.05, 6.5)
        _display(meta, "2P 入札席（東）", 23.5, 5.05, cz + 0.5)
        _display(meta, "3P 入札席（南）", cx + 0.5, 5.05, 24.5)
        _display(meta, "4P 入札席（西）", 5.5, 5.05, cz + 0.5)
        meta["destination"] = (cx + 0.5, 2, 28.5)
        meta["return_point"] = (cx - 4.5, 2, 28.5)
        meta["reception_point"] = (cx + 4.5, 2, 28.5)
        meta["seat_locations"] = [
            (cx + 0.5, 2, 3.5), (25.5, 2, cz + 0.5),
            (cx + 0.5, 2, 26.5), (2.5, 2, cz + 0.5),
        ]
    elif game_id == "fire":
        # Independently authored substitute for the source hall: a white room
        # with two glass chambers, a central heat exchanger and twelve cards.
        # The licensed Java build, textures and item models are not embedded.
        for x in (1, sx - 2):
            structure.fill((x, 2, 1), (x, 7, sz - 2), "minecraft:white_concrete")
        structure.fill((1, 2, 1), (sx - 2, 5, 1), "minecraft:white_concrete")
        structure.fill((1, 2, sz - 2), (sx - 2, 5, sz - 2), "minecraft:white_concrete")
        _fill_feature(structure, meta, (cx - 3, 1, cz - 3), (cx + 3, 1, cz + 3),
                      "minecraft:polished_blackstone")
        _fill_feature(structure, meta, (cx - 2, 2, cz - 2), (cx + 2, 3, cz + 2),
                      "minecraft:gilded_blackstone")
        _feature(structure, meta, cx, 4, cz, "minecraft:magma_block")
        _feature(structure, meta, cx - 1, 4, cz, "minecraft:redstone_lamp")
        _feature(structure, meta, cx + 1, 4, cz, "minecraft:redstone_lamp")
        for side in (-1, 1):
            room_near = cx + side * 10
            room_far = cx + side * 14
            x0, x1 = sorted((room_near, room_far))
            room_z0, room_z1 = cz - 8, cz + 8
            _fill_feature(structure, meta, (x0, 1, room_z0), (x1, 1, room_z1),
                          "minecraft:white_concrete")
            inner = cx + side * 10
            for x in (x0, x1):
                for z in range(room_z0, room_z1 + 1):
                    # Leave a doorway on the central-hall side. The original
                    # rooms are entered from the aisle; a sealed glass box
                    # would teleport players into an inaccessible display.
                    if x == inner and cz - 2 <= z <= cz + 2:
                        continue
                    structure.set(x, 2, z, "minecraft:white_stained_glass")
                    structure.set(x, 3, z, "minecraft:white_stained_glass")
            _fill_feature(structure, meta, (inner, 4, cz - 2), (inner, 4, cz + 2),
                          "minecraft:white_concrete")
            for z in (room_z0, room_z1):
                for x in range(x0 + 1, x1):
                    structure.set(x, 2, z, "minecraft:white_stained_glass")
                    structure.set(x, 3, z, "minecraft:white_stained_glass")
            for x in range(x0 + 1, x1):
                for z in range(room_z0 + 1, room_z1):
                    structure.set(x, 4, z, "minecraft:white_concrete")
            _feature(structure, meta, cx + side * 3, 3, cz - 5,
                     "minecraft:crimson_trapdoor" if side < 0 else "minecraft:warped_trapdoor")

        cards = (("rock", "グー", 1), ("scissors", "チョキ", 2), ("paper", "パー", 3))
        for seat, side in enumerate((-1, 1)):
            x = cx + side * 12
            for heat_index, (heat, heat_label, base) in enumerate((
                    ("hot", "HOT", "minecraft:magma_block"),
                    ("ice", "ICE", "minecraft:blue_ice"))):
                for card_index, (choice, label, _) in enumerate(cards):
                    z = cz - 7 + card_index * 3 + heat_index
                    structure.fill((x, 2, z), (x, 3, z), base)
                    _control(meta, f"{seat + 1}P {label} {heat_label}",
                             x + 0.5, 5.05, z + 0.5,
                             f"{choice}_{heat}_{seat + 1}")
                    _feature(structure, meta, x, 4, z,
                             "minecraft:orange_concrete" if heat == "hot"
                             else "minecraft:light_blue_concrete")

        for index, x in enumerate((cx - 2, cx - 1, cx, cx + 1, cx + 2)):
            _feature(structure, meta, x, 6, 2,
                     ("minecraft:orange_concrete", "minecraft:redstone_lamp",
                      "minecraft:white_concrete", "minecraft:redstone_lamp",
                      "minecraft:light_blue_concrete")[index])
        _fill_feature(structure, meta, (cx - 5, 7, 2), (cx + 5, 8, 2),
                      "minecraft:black_concrete")
        _display(meta, "室温・風・最終決戦表示", cx + 0.5, 7.0, 3.7)
        _display(meta, "1P ガラス", cx - 9.5, 3.0, cz)
        _display(meta, "2P ガラス", cx + 9.5, 3.0, cz)
        _display(meta, "粛清執行装置", cx + 0.5, 3.0, cz + 4.5)
        meta["seat_locations"] = [
            (cx - 11.5, 2, cz + 5.5),
            (cx + 11.5, 2, cz + 5.5),
        ]
    elif game_id == "pig":
        card_names = ("わら", "木", "レンガ", "オオカミ")
        station_centers = ((cx - 9, cz - 6), (cx + 9, cz - 6),
                           (cx - 9, cz + 6), (cx + 9, cz + 6))
        team_colors = ("minecraft:red_wool", "minecraft:blue_wool",
                       "minecraft:blue_wool", "minecraft:red_wool")
        seat_locations = []
        for seat, (hx, hz) in enumerate(station_centers):
            inward = 1 if hz < cz else -1
            rack_z = hz + inward * 3
            target_z = hz + inward * 5
            color = team_colors[seat]
            structure.fill((hx - 2, 2, hz - 2), (hx + 2, 6, hz + 2),
                           "minecraft:mud_bricks")
            structure.fill((hx - 1, 3, hz - 1), (hx + 1, 5, hz + 1), color)
            _feature(structure, meta, hx, 7, hz, "minecraft:pink_wool")
            for card, name in enumerate(card_names, start=1):
                x = hx - 3 + (card - 1) * 2
                structure.set(x, 2, rack_z, "minecraft:stripped_spruce_log")
                structure.set(x, 3, rack_z, color)
                # Cards stand on the two-block rack (log + wool) so their
                # hitboxes and textures are not buried inside the display.
                _control(meta, f"{seat + 1}P {name}", x + 0.5, 4.05, rack_z + 0.5,
                         f"pig_card_{seat + 1}_{card}")
            for index, target in enumerate(target for target in range(1, 5)
                                           if target != seat + 1):
                x = hx - 2 + index * 2
                structure.set(x, 4, target_z, "minecraft:polished_blackstone_bricks")
                structure.set(x, 5, target_z, color)
                _control(meta, f"{seat + 1}P → {target}P", x + 0.5, 6.05,
                         target_z + 0.5, f"pig_target_{seat + 1}_{target}")
            seat_locations.append((hx + 2.5, 2, hz + inward * 4.5))
            _display(meta, f"{seat + 1}P 手札・子豚・酸素", hx + 0.5, 8.0, hz + 0.5)
        structure.fill((cx - 3, 1, cz - 2), (cx + 3, 1, cz + 2),
                       "minecraft:polished_blackstone")
        structure.fill((cx - 2, 2, cz - 1), (cx + 2, 3, cz + 1),
                       "minecraft:gold_block")
        structure.fill((cx - 1, 4, cz), (cx + 1, 5, cz), "minecraft:pink_wool")
        _feature(structure, meta, cx, 6, cz, "minecraft:gold_block")
        _fill_feature(structure, meta, (cx - 5, 8, 2), (cx + 5, 9, 2),
                      "minecraft:black_concrete")
        _display(meta, "試合状況・公開カード・襲撃結果", cx + 0.5, 9.0, 3.7)
        _display(meta, "宮殿・労働・酸素表示", cx + 0.5, 4.0, cz + 0.5)
        meta["seat_locations"] = seat_locations
    elif game_id == "light":
        # The source hall has three grave slots, six dolls (three per player),
        # two candle holes and six folder racks (three per player). The folder
        # positions are the physical controls; pressing them in order assigns
        # the source's three guards and then three tourists to grave slots.
        _display(meta, "ハート・松明・酸素表示", cx + 0.5, 6.0, 3.7)
        _display(meta, "ラウンド・手番", cx - 3.5, 5.3, 3.7)
        _display(meta, "門番・死者の判定", cx + 4.0, 5.3, 3.7)
        for index, x in enumerate((cx - 6, cx, cx + 6)):
            structure.fill((x - 1, 2, 5), (x + 1, 4, 5), "minecraft:polished_blackstone_bricks")
            _feature(structure, meta, x, 5, 5, "minecraft:candle")
        for seat, base_x, color in ((1, cx - 11, "minecraft:red_wool"),
                                    (2, cx + 11, "minecraft:blue_wool")):
            for index, z in enumerate((cz - 4, cz, cz + 4)):
                structure.fill((base_x - 1, 2, z - 1), (base_x + 1, 3, z + 1),
                               "minecraft:blackstone")
                structure.set(base_x, 3, z, color)
                _display(meta, f"{seat}P 人形{index + 1}", base_x + 0.5, 4.55, z + 0.5)
            rack_x = base_x + (2 if seat == 1 else -2)
            for index, z in enumerate((cz - 4, cz, cz + 4)):
                structure.fill((rack_x, 2, z), (rack_x, 3, z), "minecraft:blackstone")
                structure.set(rack_x, 3, z, color)
                _control(meta, f"{seat}P フォルダ{index + 1}",
                         rack_x + 0.5, 4.05, z + 0.5,
                         f"light_grave_{seat}_{index + 1}")
        for index, x in enumerate((cx - 6, cx, cx + 6)):
            _display(meta, f"墓所{index + 1}", x + 0.5, 6.05, 6.0)
        for index, x in enumerate((cx - 3, cx + 3)):
            structure.fill((x - 1, 2, cz + 3), (x + 1, 4, cz + 3),
                           "minecraft:polished_blackstone_bricks")
            _feature(structure, meta, x, 5, cz + 3, "minecraft:candle")
            _display(meta, f"ロウソク穴{index + 1}", x + 0.5, 5.75, cz + 3.5)
        _fill_feature(structure, meta, (cx - 2, 2, cz + 2), (cx + 2, 4, cz + 4),
                      "minecraft:blackstone")
        for x in (cx - 7, cx + 7):
            structure.fill((x, 2, cz - 3), (x, 5, cz + 3), "minecraft:yellow_wool")
        _fill_feature(structure, meta, (cx - 5, 5, 2), (cx + 5, 7, 2),
                      "minecraft:black_concrete")
        meta["destination"] = (cx + 0.5, 2, sz - 4.5)
        meta["return_point"] = (cx - 4.5, 2, sz - 4.5)
        meta["reception_point"] = (cx + 4.5, 2, sz - 4.5)
        meta["seat_locations"] = [(cx - 11.5, 2, cz + 7.5), (cx + 11.5, 2, cz + 7.5)]
    elif game_id == "mine":
        # Two mine shafts mirror the source's 1P/2P four-rock folders. Each
        # seat owns four physical rocks for both secret arrangement and the
        # two-rock mining choice; the central drills and ingot trays carry the
        # source's excavation and poison feedback.
        ores = ("minecraft:coal_block", "minecraft:iron_block", "minecraft:gold_block",
                "minecraft:redstone_block")
        _display(meta, "猛毒・HP表示", cx - 3.0, 6.0, 3.7)
        _display(meta, "インゴット・採掘数", cx + 3.0, 6.0, 3.7)
        _display(meta, "ラウンド・手番", cx + 0.5, 5.2, 3.7)
        for seat, center in ((1, cx - 9), (2, cx + 9)):
            for index, x in enumerate((center - 3, center - 1, center + 1, center + 3)):
                structure.fill((x - 1, 2, 5), (x + 1, 4, 7), "minecraft:nether_bricks")
                _feature(structure, meta, x, 3, 8, ores[index])
                _control(meta, f"{seat}P 岩{index + 1}", x + 0.5, 5.05, 8.0,
                         f"mine_rock_{seat}_{index + 1}")
            _display(meta, f"{seat}P 岩・カード", center + 0.5, 6.2, 8.5)
            structure.fill((center - 4, 2, cz + 3), (center + 4, 3, cz + 5),
                           "minecraft:polished_blackstone_bricks")
            _display(meta, f"{seat}P インゴット", center + 0.5, 4.05, cz + 4.5)
        for seat, center in ((1, cx - 9), (2, cx + 9)):
            structure.fill((center - 1, 2, cz - 2), (center + 1, 3, cz + 2),
                           "minecraft:polished_blackstone")
            structure.fill((center - 1, 4, cz - 2), (center + 1, 5, cz + 2),
                           "minecraft:iron_block")
            _feature(structure, meta, center, 3, cz, "minecraft:lodestone")
            _display(meta, f"{seat}P ドリル", center + 0.5, 6.05, cz + 0.5)
        _fill_feature(structure, meta, (cx - 2, 2, cz + 1), (cx + 2, 3, cz + 3),
                      "minecraft:red_nether_bricks")
        structure.fill((cx, 4, cz), (cx, 6, cz), "minecraft:iron_block")
        _fill_feature(structure, meta, (cx - 5, 5, 2), (cx + 5, 7, 2),
                      "minecraft:black_concrete")
        meta["destination"] = (cx + 0.5, 2, sz - 4.5)
        meta["return_point"] = (cx - 4.5, 2, sz - 4.5)
        meta["reception_point"] = (cx + 4.5, 2, sz - 4.5)
        meta["seat_locations"] = [(cx - 9.5, 2, cz + 8.5), (cx + 9.5, 2, cz + 8.5)]
    elif game_id == "clock":
        # Source-informed clock floor: twelve removable sectors over water.
        # The source uses a circular display over wool blocks; this is an
        # independently authored physical adaptation with a real water drop.
        face_x, face_z, radius = cx, 12, 8.5
        sector_blocks = (
            "red_wool", "pink_wool", "yellow_wool", "blue_wool",
            "orange_wool", "magenta_wool", "lime_wool", "light_blue_wool",
            "purple_wool", "green_wool", "light_gray_wool", "cyan_wool",
        )
        sector_cells = [[] for _ in sector_blocks]
        for x in range(face_x - 10, face_x + 11):
            for z in range(face_z - 10, face_z + 11):
                dx, dz = x - face_x, z - face_z
                distance = math.hypot(dx, dz)
                if distance <= radius:
                    structure.set(x, 0, z, "minecraft:water")
                    if distance <= 1.1:
                        _feature(structure, meta, x, 1, z, "minecraft:polished_blackstone")
                        continue
                    angle = math.atan2(dx, -dz)
                    if angle < 0:
                        angle += math.tau
                    sector = int(((angle + math.pi / 12) % math.tau) // (math.pi / 6)) % 12
                    floor_name = f"minecraft:{sector_blocks[sector]}"
                    _feature(structure, meta, x, 1, z, floor_name)
                    sector_cells[sector].append([x, 1, z])
                elif distance <= 10.2:
                    _feature(structure, meta, x, 1, z,
                             "minecraft:gilded_blackstone" if int(distance * 2) % 3 == 0
                             else "minecraft:polished_deepslate")
        _feature(structure, meta, face_x, 1, face_z, "minecraft:gold_block")
        # Overhead hands are intentionally above head height so they cannot
        # obstruct play; their time values are also shown on the wall displays.
        _fill_feature(structure, meta, (face_x, 7, face_z - 4), (face_x, 7, face_z + 3),
                      "minecraft:gold_block")
        _fill_feature(structure, meta, (face_x, 8, face_z), (face_x + 3, 8, face_z),
                      "minecraft:light_blue_concrete")
        _fill_feature(structure, meta, (cx - 8, 6, 1), (cx + 8, 9, 1),
                      "minecraft:black_concrete")
        for hour in range(12):
            angle = hour * math.tau / 12
            x = round(cx + math.sin(angle) * 7)
            y = round(7.5 - math.cos(angle) * 2.5)
            _feature(structure, meta, x, y, 2, "minecraft:gold_block")
        time_controls = (
            ("時 +1", "clock_hour_plus", 3, 6),
            ("時 -1", "clock_hour_minus", 3, 10),
            ("分 +5", "clock_minute_plus", 3, 14),
            ("分 -5", "clock_minute_minus", 3, 18),
            ("設定決定", "clock_time_confirm", 3, 22),
            ("区画 時計回り", "clock_sector_cw", 27, 8),
            ("区画 反時計回り", "clock_sector_ccw", 27, 13),
            ("区画 決定", "clock_sector_confirm", 27, 18),
        )
        for label, action, x, z in time_controls:
            _feature(structure, meta, x, 1, z, "minecraft:polished_blackstone")
            _feature(structure, meta, x, 2, z, "minecraft:gilded_blackstone")
            _control(meta, label, x + 0.5, 3.05, z + 0.5, action)
        _display(meta, "時計盤 現在時刻→次回時刻", cx + 0.5, 7.0, 2.7)
        _display(meta, "確定区画・必要数・カーソル", cx + 0.5, 9.0, 2.7)
        meta["seat_locations"] = [(11.5, 2, 18.5), (18.5, 2, 18.5)]
        meta["clock_sector_cells"] = [
            {
                "sector": index,
                "floor": f"minecraft:{sector_blocks[index]}",
                "cells": cells,
            }
            for index, cells in enumerate(sector_cells)
        ]
        # Runtime collision target for the independently authored clock-hand
        # sweep. The source uses item displays four blocks above the floor;
        # this adaptation reports the same x/z geometry and uses particles at
        # participant height because Bedrock has no item_display entity.
        meta["clock_geometry"] = {
            "center": [face_x, 2.15, face_z],
            "hour_reach": 5.2,
            "minute_reach": 8.0,
            "half_width": 0.7,
            "min_radius": 1.1,
        }
    elif game_id == "walker":
        # Independently placed local board for the 37-cell game graph. Its
        # row populations/colours are rule data; no private world coordinates
        # or captured building geometry are embedded in the public generator.
        platform_y = 14
        water_bottom = 3
        water_top = 10
        rows = (2, 3, 4, 3, 4, 5, 4, 3, 4, 3, 2)
        colors = ("red", "orange", "yellow", "lime", "green", "cyan",
                  "light_blue", "blue", "purple", "magenta", "pink")
        cells = []
        for row, (count, color) in enumerate(zip(rows, colors)):
            for column in range(count):
                cells.append({"floor": len(cells) + 1,
                              "x": int(16 + ((count - 1) / 2 - column) * 6),
                              "z": 22 - row * 2, "color": color})
        # Keep a genuine deep water basin under the suspended arena.
        structure.fill((1, water_bottom, 1), (sx - 2, water_top, 23), "minecraft:water")
        structure.fill((1, 1, 1), (sx - 2, 2, 23), "minecraft:prismarine_bricks")
        for cell in cells:
            x, z = cell["x"], cell["z"]
            color = f"minecraft:{cell['color']}_concrete"
            _fill_feature(structure, meta, (x - 2, platform_y, z - 2),
                          (x + 2, platform_y, z + 2), color)
        # The entry deck is separated from the live floor; participants are
        # moved to the two source start cells when the match starts.
        _fill_feature(structure, meta, (11, platform_y, 25), (19, platform_y, 27),
                      "minecraft:polished_deepslate")
        _fill_feature(structure, meta, (11, 15, 27), (19, 15, 27),
                      "minecraft:glass_pane")
        _fill_feature(structure, meta, (11, 15, 25), (11, 15, 27),
                      "minecraft:glass_pane")
        _fill_feature(structure, meta, (19, 15, 25), (19, 15, 27),
                      "minecraft:glass_pane")
        structure.fill((12, 2, 25), (18, 13, 27), "minecraft:air")
        # Four controls per seat sit on the occupied start cell, so the breaker
        # never has to step off the suspended floor to operate them.
        stations = {
            0: {"stop": "walker_stop_1", "prev": "bomb_prev_1",
                "next": "bomb_next_1", "confirm": "bomb_confirm_1"},
            1: {"stop": "walker_stop_2", "prev": "bomb_prev_2",
                "next": "bomb_next_2", "confirm": "bomb_confirm_2"},
        }
        for seat, cell_floor in enumerate((21, 17)):
            cell = next(value for value in cells if value["floor"] == cell_floor)
            x, z = cell["x"], cell["z"]
            _control(meta, f"{seat + 1}P 移動確定", x - 1.5, platform_y + 1.8, z - 1.5,
                     stations[seat]["stop"])
            _control(meta, f"{seat + 1}P 爆弾を前へ", x - 0.5, platform_y + 1.8, z - 1.5,
                     stations[seat]["prev"])
            _control(meta, f"{seat + 1}P 爆弾を次へ", x - 1.5, platform_y + 1.8, z + 1.5,
                     stations[seat]["next"])
            _control(meta, f"{seat + 1}P 爆弾を決定", x - 0.5, platform_y + 1.8, z + 1.5,
                     stations[seat]["confirm"])
        _display(meta, "ラウンド・手番", 12.5, 18.5, 26.5)
        _display(meta, "移動・爆弾", 15.5, 18.5, 26.5)
        _display(meta, "水位・勝敗", 18.5, 18.5, 26.5)
        meta["seat_locations"] = [
            (next(value for value in cells if value["floor"] == floor)["x"] + 0.5,
             platform_y + 1,
             next(value for value in cells if value["floor"] == floor)["z"] + 0.5)
            for floor in (21, 17)
        ]
        meta["seat_rotations"] = [-90, 90]
        meta["destination"] = (15.5, platform_y + 1, 25.5)
        meta["return_point"] = (12.5, platform_y + 1, 26.5)
        meta["reception_point"] = (18.5, platform_y + 1, 26.5)
        meta["walker"] = {
            "platform_y": platform_y,
            "water_bottom": water_bottom,
            "water_top": water_top,
            "min_x": 1,
            "max_x": sx - 2,
            "min_z": 1,
            "max_z": 23,
            "cells": cells,
        }
    elif game_id == "garden":
        # Source-informed two-player jewel shower: two collection bays, an
        # overhead gem device, a visible valve, and a shared card console.
        left, right = cx - 6, cx + 6
        bay_z = cz + 5
        floor_blocks = []
        for x, floor_name in ((right, "minecraft:emerald_block"), (left, "minecraft:lapis_block")):
            floor_blocks.append(floor_name)
            _fill_feature(structure, meta, (x - 2, 1, bay_z - 2), (x + 2, 1, bay_z + 2), floor_name)
            # The source opens the bay floor after a timeout and detects the
            # loser on brown wool. Keep that physical drop under each seat.
            for dx in range(-1, 2):
                for dz in range(-1, 2):
                    structure.set(x + dx, 0, bay_z + 1 + dz, "minecraft:brown_wool")
            for dx in range(-2, 3):
                for dz in range(-2, 3):
                    if abs(dx) == 2 or dz == -2:
                        structure.set(x + dx, 2, bay_z + dz, "minecraft:glass")
            structure.fill((x - 1, 2, bay_z - 1), (x + 1, 3, bay_z + 1), "minecraft:air")
            _feature(structure, meta, x, 2, bay_z - 2, "minecraft:chest")
        _fill_feature(structure, meta, (cx - 2, 7, cz - 2), (cx + 2, 9, cz + 2), "minecraft:glass")
        _fill_feature(structure, meta, (cx - 1, 7, cz - 1), (cx + 1, 8, cz + 1), "minecraft:emerald_block")
        _fill_feature(structure, meta, (cx - 3, 6, cz - 3), (cx + 3, 6, cz + 3), "minecraft:iron_bars")
        _feature(structure, meta, cx - 2, 5, cz + 2, "minecraft:lime_concrete")
        _feature(structure, meta, cx + 2, 5, cz + 2, "minecraft:red_concrete")
        _feature(structure, meta, cx, 5, cz + 2, "minecraft:observer")
        _fill_feature(structure, meta, (cx - 5, 1, cz + 9), (cx + 5, 2, cz + 9),
                      "minecraft:polished_deepslate")
        for index, (label, action) in enumerate((("切替", "switch"), ("宝石倍化", "double"), ("開放", "open"))):
            x = cx - 3 + index * 3
            _control(meta, label, x + 0.5, 3.05, cz + 9.5, action)
            structure.fill((x - 1, 3, cz + 9), (x + 1, 4, cz + 10), "minecraft:air")
        _control(meta, "1P宝石箱から1個戻す", right + 0.5, 3.05, bay_z - 1.5, "pick_1")
        _control(meta, "2P宝石箱から1個戻す", left + 0.5, 3.05, bay_z - 1.5, "pick_2")
        structure.fill((right - 1, 3, bay_z - 2), (right + 1, 4, bay_z - 1), "minecraft:air")
        structure.fill((left - 1, 3, bay_z - 2), (left + 1, 4, bay_z - 1), "minecraft:air")
        for label, x, y, z in (
            ("装置・総合表示", cx + 0.5, sy - 4, 2.7),
            ("1P宝石箱表示", right + 0.5, 4.0, bay_z - 1.5),
            ("2P宝石箱表示", left + 0.5, 4.0, bay_z - 1.5),
            ("弁表示", cx + 5.5, 5.0, cz + 3.5),
        ):
            _display(meta, label, x, y, z)
            structure.fill((math.floor(x) - 1, math.floor(y) - 1, math.floor(z) - 1),
                           (math.floor(x) + 1, math.floor(y) + 1, math.floor(z) + 1),
                           "minecraft:air")
        meta["seat_locations"] = [
            (right + 0.5, 2, bay_z + 1.5),
            (left + 0.5, 2, bay_z + 1.5),
        ]
        meta["drop_floor_blocks"] = floor_blocks
    elif game_id == "rabbit":
        # Source: game/rabbit/floor1.mcfunction places a 3x3 floor for every
        # anchor. Row-major numbering decreases along both local axes
        # toward tile 60. The hall therefore mirrors X locally while retaining
        # the row-major tile order used by every rule in board_games.js.
        board_min_x, board_max_x = 7, 42
        board_min_z, board_max_z = 8, 22
        structure.fill((board_min_x, 0, board_min_z), (board_max_x, 0, board_max_z),
                      "minecraft:stone")
        structure.fill((board_min_x, 1, board_min_z), (board_max_x, 1, board_max_z),
                      "minecraft:water")
        structure.fill((board_min_x, 2, board_min_z), (board_max_x, 2, board_max_z),
                      "minecraft:polished_andesite")
        structure.fill((board_min_x, 3, board_min_z), (board_max_x, 5, board_max_z),
                      "minecraft:air")
        # The four manual guard controls belong to p1 on the escape side. Put
        # them on a narrow safe shelf beside the board, never on the oni-side
        # tile 61 platform. The shelf remains outside every legal floor so it
        # cannot enter trap, move or water-loss checks.
        guard_shelf_min_x, guard_shelf_max_x = 43, 46
        structure.fill((guard_shelf_min_x, 0, board_min_z),
                       (guard_shelf_max_x, 0, board_max_z), "minecraft:stone")
        structure.fill((guard_shelf_min_x, 1, board_min_z),
                       (guard_shelf_max_x, 1, board_max_z),
                       "minecraft:polished_deepslate")
        structure.fill((guard_shelf_min_x, 2, board_min_z),
                       (guard_shelf_max_x, 2, board_max_z),
                       "minecraft:polished_andesite")
        structure.fill((guard_shelf_min_x, 3, board_min_z),
                       (guard_shelf_max_x, 5, board_max_z), "minecraft:air")
        guard_shelf = {
            "min_x": guard_shelf_min_x, "max_x": guard_shelf_max_x,
            "min_z": board_min_z, "max_z": board_max_z, "floor_y": 2,
        }
        rabbit_cells = []
        for tile in range(1, 61):
            row = (tile - 1) // 12
            column = (tile - 1) % 12
            min_x = board_max_x - column * 3 - 2
            # Source prepare.mcfunction places tile 1 at z=367 and tile 13 at
            # z=364. The row axis therefore decreases along Z as well as the
            # column axis decreasing along X.
            min_z = board_max_z - row * 3 - 2
            goal = tile in (12, 24, 36, 48, 60)
            block_name = "minecraft:gold_block" if goal else (
                "minecraft:granite" if tile % 2 else "minecraft:pink_wool")
            floor_cells = []
            water_cells = []
            for dx in range(3):
                for dz in range(3):
                    _feature(structure, meta, min_x + dx, 2, min_z + dz, block_name)
                    structure.set(min_x + dx, 1, min_z + dz, "minecraft:water")
                    floor_cells.append([min_x + dx, 2, min_z + dz])
                    water_cells.append([min_x + dx, 1, min_z + dz])
            label = f"床{tile}" + ("（ゴール）" if goal else "")
            _control(meta, label, min_x + 1.5, 3.05, min_z + 1.5, f"floor_{tile}")
            rabbit_cells.append({
                "tile": tile, "x": min_x + 1, "z": min_z + 1, "floor": block_name,
                "floor_cells": floor_cells, "water_cells": water_cells,
            })
        # Tile 61 is the oni's separate starting floor. It is tagged in the
        # source as rabbit-floor-61, not rabbit-floor, so it must never enter
        # the legal trap/move set or the water-drop observer.
        oni_min_x, oni_max_x = 1, 3
        oni_min_z, oni_max_z = 14, 16
        structure.fill((oni_min_x, 0, oni_min_z), (oni_max_x, 0, oni_max_z),
                      "minecraft:stone")
        structure.fill((oni_min_x, 1, oni_min_z), (oni_max_x, 1, oni_max_z),
                      "minecraft:deepslate_tiles")
        structure.fill((oni_min_x, 2, oni_min_z), (oni_max_x, 2, oni_max_z),
                      "minecraft:polished_blackstone")
        structure.fill((oni_min_x, 3, oni_min_z), (oni_max_x, 5, oni_max_z),
                      "minecraft:air")
        oni_floor_cells = [[cell_x, 2, cell_z]
                           for cell_x in range(oni_min_x, oni_max_x + 1)
                           for cell_z in range(oni_min_z, oni_max_z + 1)]
        # Keep the three-block space between tile 61 and the board open. This
        # preserves the source's distinct escape-side position instead of
        # placing the oni behind the board in positive Z.
        structure.fill((4, 0, board_min_z), (6, 2, board_max_z), "minecraft:air")
        # Keep the oni-side footplate tied to tile 61. Manual guard controls
        # are on the escape-side shelf above; this area remains a spectator
        # apron and must not duplicate active controls.
        structure.fill((oni_min_x, 0, 18), (oni_max_x, 0, 21), "minecraft:stone")
        structure.fill((oni_min_x, 1, 18), (oni_max_x, 1, 21),
                      "minecraft:polished_deepslate")
        structure.fill((oni_min_x, 2, 18), (oni_max_x, 4, 21), "minecraft:air")
        for index, (name, action) in enumerate((
            ("りょうたん", "guard_3"), ("チェダー", "guard_4"),
            ("ワッホイ", "guard_5"), ("チーくん", "guard_6"),
        )):
            _control(meta, f"お守り: {name}", 44.5, 3.05, 10.5 + index * 3, action)
        # Spectators remain on the oni-side edge, outside the 60 legal cells.
        structure.fill((1, 0, 23), (4, 0, 25), "minecraft:stone")
        structure.fill((1, 1, 23), (4, 1, 25), "minecraft:deepslate_tiles")
        structure.fill((1, 2, 23), (4, 4, 25), "minecraft:air")
        _display(meta, "ゴール・罠表示", cx + 0.5, sy - 4, 2.7)
        _display(meta, "残存・手番表示", 2.5, 4.5, 15.5)
        _display(meta, "ルール表示", cx + 0.5, 4.5, 26.0)
        meta["destination"] = (22.5, 2, 27.5)
        meta["return_point"] = (19.5, 2, 28.5)
        meta["reception_point"] = (25.5, 2, 28.5)
        meta["seat_locations"] = [
            (rabbit_cells[24]["x"] + 0.5, 3, rabbit_cells[24]["z"] + 0.5),
            (2.5, 3, 15.5),
        ]
        # rabbit/start.mcfunction faces the escaper west across the board
        # (yaw 90) and the oni east toward it (yaw -90). The local X axis is
        # intentionally mirrored like the source, so these rotations carry
        # over unchanged.
        meta["seat_rotations"] = [90, -90]
        meta["points"] = {"spectator": (2.5, 2, 24.5)}
        meta["rabbit"] = {
            "floor_y": 2, "water_y": 1, "stand_y": 3,
            "min_x": board_min_x, "max_x": board_max_x,
            "min_z": board_min_z, "max_z": board_max_z,
            "cells": rabbit_cells,
            "oni": {
                "tile": 61, "x": 2, "z": 15,
                "floor": "minecraft:polished_blackstone",
                "floor_cells": oni_floor_cells, "water_cells": [],
            },
            "guard_shelf": guard_shelf,
            "spectator": [2.5, 2, 24.5],
        }
        validate_rabbit_layout(meta, structure)
    else:
        raise ValueError(f"missing independently authored venue layout: {game_id}")
    return meta


def fire_geometries():
    """Original prop silhouettes for the source-informed Fire hall."""
    return [
        {
            "description": {
                "identifier": "geometry.junket.fire.card",
                "texture_width": 64, "texture_height": 64,
                "visible_bounds_width": 3, "visible_bounds_height": 4,
                "visible_bounds_offset": [0, 2, 0],
            },
            "bones": [
                {"name": "body", "pivot": [0, 0, 0], "cubes": [
                    {"origin": [-2, 0, -0.12], "size": [4, 4.8, 0.24], "uv": [0, 0]},
                    {"origin": [-2, 4.8, -0.1], "size": [4, 0.5, 0.2], "uv": [0, 40]},
                ]},
            ],
        },
        {
            "description": {
                "identifier": "geometry.junket.fire.tv",
                "texture_width": 64, "texture_height": 64,
                "visible_bounds_width": 9, "visible_bounds_height": 7,
                "visible_bounds_offset": [0, 3, 0],
            },
            "bones": [
                {"name": "body", "pivot": [0, 0, 0], "cubes": [
                    {"origin": [-3.5, 0, -0.18], "size": [7, 4, 0.36], "uv": [0, 0]},
                    {"origin": [-3.1, 0.4, -0.24], "size": [6.2, 3.2, 0.08], "uv": [0, 24]},
                ]},
            ],
        },
        {
            "description": {
                "identifier": "geometry.junket.fire.glass",
                "texture_width": 64, "texture_height": 64,
                "visible_bounds_width": 7, "visible_bounds_height": 8,
                "visible_bounds_offset": [0, 3, 0],
            },
            "bones": [
                {"name": "body", "pivot": [0, 0, 0], "cubes": [
                    {"origin": [-2.6, 0, -0.08], "size": [5.2, 5.6, 0.16], "uv": [0, 0]},
                    {"origin": [-2.8, -0.1, -0.12], "size": [5.6, 0.35, 0.24], "uv": [0, 44]},
                    {"origin": [-2.8, 5.45, -0.12], "size": [5.6, 0.35, 0.24], "uv": [0, 48]},
                ]},
            ],
        },
    ]


def hope_geometries():
    """Independently authored apparatus used by the source-informed Hope hall."""
    def panel(identifier, width, height, depth, visible_width, visible_height):
        return {
            "description": {
                "identifier": identifier,
                "texture_width": 64,
                "texture_height": 64,
                "visible_bounds_width": visible_width,
                "visible_bounds_height": visible_height,
                "visible_bounds_offset": [0, visible_height / 2, 0],
            },
            "bones": [{
                "name": "body",
                "pivot": [0, 0, 0],
                "cubes": [{
                    "origin": [-width / 2, 0, -depth / 2],
                    "size": [width, height, depth],
                    "uv": {
                        "north": {"uv": [0, 0], "uv_size": [64, 64]},
                        "south": {"uv": [0, 0], "uv_size": [64, 64]},
                        "east": {"uv": [0, 0], "uv_size": [4, 64]},
                        "west": {"uv": [0, 0], "uv_size": [4, 64]},
                        "up": {"uv": [0, 0], "uv_size": [64, 4]},
                        "down": {"uv": [0, 0], "uv_size": [64, 4]},
                    },
                }],
            }],
        }

    tv = panel("geometry.junket.hope.tv", 40, 32, 3, 6, 4)
    tv["bones"][0]["cubes"].extend([
        {"origin": [-21, -4, -4], "size": [42, 4, 8], "uv": [0, 56]},
        {"origin": [-18, -6, -5], "size": [6, 2, 10], "uv": [12, 56]},
        {"origin": [12, -6, -5], "size": [6, 2, 10], "uv": [12, 56]},
    ])
    display = panel("geometry.junket.hope.code_display", 44, 36, 3, 7, 4)
    display["bones"][0]["cubes"].extend([
        {"origin": [-23, 36, -5], "size": [46, 3, 10], "uv": [0, 52]},
        {"origin": [-23, -3, -5], "size": [46, 3, 10], "uv": [0, 52]},
    ])
    code = {
        "description": {
            "identifier": "geometry.junket.hope.code",
            "texture_width": 64,
            "texture_height": 64,
            "visible_bounds_width": 4,
            "visible_bounds_height": 5,
            "visible_bounds_offset": [0, 2, 0],
        },
        "bones": [{
            "name": "body",
            "pivot": [0, 0, 0],
            "cubes": [
                {"origin": [-13, 0, -13], "size": [26, 6, 26], "uv": [0, 52]},
                {"origin": [-7, 6, -7], "size": [14, 22, 14], "uv": [0, 24]},
                {"origin": [-10, 28, -9], "size": [20, 12, 18], "uv": [0, 0]},
                {"origin": [-1.5, 40, -1.5], "size": [3, 9, 3], "uv": [42, 6]},
                {"origin": [-4, 49, -4], "size": [8, 8, 8], "uv": [40, 40]},
            ],
        }],
    }
    return [tv, display, code]


def rabbit_geometries():
    """A 3x3 inlay tile matching one source rabbit floor anchor."""
    return [{
        "description": {
            "identifier": "geometry.junket.rabbit.floor",
            "texture_width": 48,
            "texture_height": 48,
            "visible_bounds_width": 4,
            "visible_bounds_height": 1,
            "visible_bounds_offset": [0, 0.25, 0],
        },
        "bones": [{
            "name": "body",
            "pivot": [0, 0, 0],
            "cubes": [{
                "origin": [-24, 0, -24],
                "size": [48, 0.75, 48],
                "uv": {
                    "up": {"uv": [0, 0], "uv_size": [48, 48]},
                    "down": {"uv": [0, 0], "uv_size": [48, 48]},
                    "north": {"uv": [0, 0], "uv_size": [48, 1]},
                    "south": {"uv": [0, 47], "uv_size": [48, 1]},
                    "east": {"uv": [47, 0], "uv_size": [1, 48]},
                    "west": {"uv": [0, 0], "uv_size": [1, 48]},
                },
            }],
        }],
    }]


def walker_geometries():
    """Flat floor glyph and a compact bomb marker for Walker."""
    return [{
        "description": {
            "identifier": "geometry.junket.walker.label",
            "texture_width": 64,
            "texture_height": 64,
            "visible_bounds_width": 5,
            "visible_bounds_height": 1,
            "visible_bounds_offset": [0, 0.15, 0],
        },
        "bones": [{
            "name": "body",
            "pivot": [0, 0, 0],
            "cubes": [{
                "origin": [-2.2, 0, -2.2],
                "size": [4.4, 0.03, 4.4],
                "uv": {
                    "up": {"uv": [0, 0], "uv_size": [64, 64]},
                    "down": {"uv": [0, 0], "uv_size": [64, 64]},
                    "north": {"uv": [0, 0], "uv_size": [64, 1]},
                    "south": {"uv": [0, 0], "uv_size": [64, 1]},
                    "east": {"uv": [0, 0], "uv_size": [1, 64]},
                    "west": {"uv": [0, 0], "uv_size": [1, 64]},
                },
            }],
        }],
    }, {
        "description": {
            "identifier": "geometry.junket.walker.bomb",
            "texture_width": 64,
            "texture_height": 64,
            "visible_bounds_width": 3,
            "visible_bounds_height": 3,
            "visible_bounds_offset": [0, 1.5, 0],
        },
        "bones": [{
            "name": "body",
            "pivot": [0, 0, 0],
            "cubes": [{
                "origin": [-1, 0, -1],
                "size": [2, 2, 2],
                "uv": {
                    "up": {"uv": [0, 0], "uv_size": [64, 64]},
                    "down": {"uv": [0, 0], "uv_size": [64, 64]},
                    "north": {"uv": [0, 0], "uv_size": [64, 64]},
                    "south": {"uv": [0, 0], "uv_size": [64, 64]},
                    "east": {"uv": [0, 0], "uv_size": [64, 64]},
                    "west": {"uv": [0, 0], "uv_size": [64, 64]},
                },
            }],
        }],
    }]


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
        if game_id == "fire":
            geometries.extend(fire_geometries())
        elif game_id == "hope":
            geometries.extend(hope_geometries())
        elif game_id == "rabbit":
            geometries.extend(rabbit_geometries())
        elif game_id == "walker":
            geometries.extend(walker_geometries())
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


def write_fire_textures():
    """Create original card, television and cracked-glass textures."""
    RP_TEXTURES.mkdir(parents=True, exist_ok=True)
    def card(name, border, accent, used=False):
        image = Image.new("RGBA", (64, 64), (238, 232, 218, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((1, 1, 62, 62), outline=border, width=4)
        draw.rectangle((6, 6, 57, 14), fill=accent)
        draw.rectangle((6, 49, 57, 57), fill=(46, 60, 78))
        for x, color in ((14, (218, 90, 44)), (32, (46, 116, 170)), (50, (224, 180, 48))):
            draw.ellipse((x - 7, 23, x + 7, 37), outline=color, width=3)
        draw.text((4, 16), "FIRE CARD", fill=(30, 34, 40))
        if used:
            draw.rectangle((5, 5, 58, 58), fill=(72, 76, 82, 148))
            draw.line((8, 8, 55, 55), fill=(32, 34, 38), width=5)
            draw.line((55, 8, 8, 55), fill=(32, 34, 38), width=5)
        image.save(RP_TEXTURES / name)

    card("fire_card.png", (42, 47, 55), (224, 132, 52))
    card("fire_card_used.png", (58, 58, 58), (90, 90, 90), used=True)
    card("fire_card_hot.png", (206, 64, 34), (255, 108, 34))
    card("fire_card_ice.png", (45, 112, 190), (72, 190, 235))
    card("fire_card_active.png", (230, 186, 42), (255, 238, 126))

    tv = Image.new("RGBA", (64, 64), (22, 24, 28, 255))
    draw = ImageDraw.Draw(tv)
    draw.rectangle((1, 1, 62, 62), outline=(120, 128, 136), width=3)
    draw.rectangle((5, 5, 58, 58), fill=(18, 77, 101))
    draw.rectangle((7, 8, 56, 18), fill=(222, 92, 34))
    draw.rectangle((7, 49, 56, 56), fill=(62, 168, 206))
    for y in (23, 29, 35, 41, 47):
        draw.line((9, y, 55, y), fill=(225, 234, 238), width=1)
    tv.save(RP_TEXTURES / "fire_tv.png")

    def glass(cracks, broken=False):
        image = Image.new("RGBA", (64, 64), (154, 218, 240, 92))
        draw = ImageDraw.Draw(image)
        if broken:
            draw.rectangle((0, 0, 63, 63), fill=(0, 0, 0, 0))
            for box in ((0, 0, 20, 20), (44, 0, 63, 16), (0, 46, 14, 63), (47, 43, 63, 63)):
                draw.rectangle(box, fill=(205, 239, 248, 190), outline=(67, 143, 174, 255), width=2)
            for line in ((22, 2, 45, 26), (44, 28, 62, 42), (16, 46, 36, 61)):
                draw.line(line, fill=(63, 106, 121, 220), width=4)
        else:
            draw.rectangle((1, 1, 62, 62), outline=(218, 245, 252, 255), width=3)
            draw.rectangle((7, 7, 56, 56), outline=(82, 171, 204, 180), width=2)
            for index in range(cracks):
                start = (30 + index * 3, 8)
                middle = (18 + index * 5, 28 + index * 2)
                end = (42 - index * 2, 55 - index * 2)
                draw.line((start, middle), fill=(30, 65, 78, 255), width=2)
                draw.line((middle, end), fill=(30, 65, 78, 255), width=2)
        image.save(RP_TEXTURES / ("fire_glass_broken.png" if broken else f"fire_glass_{cracks}.png"))

    for cracks in range(5):
        glass(cracks)
    glass(4, broken=True)


def write_hope_textures():
    """Original TV, code terminal and broken-display textures."""
    RP_TEXTURES.mkdir(parents=True, exist_ok=True)
    tv = Image.new("RGBA", (64, 64), (18, 22, 30, 255))
    draw = ImageDraw.Draw(tv)
    draw.rectangle((1, 1, 62, 62), outline=(116, 134, 155, 255), width=3)
    draw.rectangle((6, 5, 57, 50), fill=(26, 58, 78, 255))
    draw.rectangle((8, 7, 55, 17), fill=(67, 184, 198, 255))
    for y, color in ((22, (226, 190, 72)), (29, (87, 195, 148)),
                     (36, (203, 92, 112)), (43, (116, 136, 214))):
        draw.rectangle((9, y, 54, y + 4), fill=color + (255,))
    draw.line((10, 51, 53, 51), fill=(158, 169, 179, 255), width=2)
    tv.save(RP_TEXTURES / "hope_tv.png")

    code = Image.new("RGBA", (64, 64), (35, 43, 49, 255))
    draw = ImageDraw.Draw(code)
    draw.rectangle((0, 52, 63, 63), fill=(72, 78, 82, 255))
    draw.rectangle((22, 6, 41, 47), fill=(52, 71, 79, 255))
    draw.rectangle((25, 10, 38, 28), fill=(45, 196, 177, 255))
    for y in (13, 17, 21, 25):
        draw.line((27, y, 36, y), fill=(12, 60, 62, 255), width=1)
    draw.rectangle((27, 34, 37, 43), fill=(222, 170, 64, 255))
    draw.rectangle((26, 48, 38, 50), fill=(93, 225, 208, 255))
    code.save(RP_TEXTURES / "hope_code.png")

    def display(name, broken):
        image = Image.new("RGBA", (64, 64), (24, 29, 34, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((1, 1, 62, 62), outline=(121, 137, 148, 255), width=3)
        draw.rectangle((6, 6, 57, 57), fill=(15, 24, 26, 255))
        if broken:
            draw.rectangle((9, 9, 54, 54), fill=(72, 18, 24, 255))
            for line in ((10, 10, 53, 53), (53, 10, 10, 53),
                         (30, 8, 35, 55), (8, 31, 56, 27)):
                draw.line(line, fill=(218, 70, 58, 255), width=3)
            draw.rectangle((23, 26, 41, 39), fill=(35, 9, 12, 255),
                           outline=(238, 96, 70, 255), width=2)
        else:
            draw.rectangle((9, 9, 54, 54), fill=(15, 82, 74, 255))
            for y in (15, 21, 27, 33, 39, 45):
                draw.line((13, y, 50, y), fill=(83, 229, 188, 255), width=2)
            draw.rectangle((25, 20, 39, 41), fill=(10, 41, 38, 255),
                           outline=(237, 206, 90, 255), width=2)
            draw.polygon([(32, 23), (37, 34), (27, 34)], fill=(237, 206, 90, 255))
        image.save(RP_TEXTURES / name)

    display("hope_code_display.png", False)
    display("hope_code_display_broken.png", True)


def write_rabbit_textures():
    """Original four-state 3x3 floor inlays for Rabbit."""
    RP_TEXTURES.mkdir(parents=True, exist_ok=True)
    palettes = {
        "normal": ((113, 92, 74), (143, 117, 92), (210, 190, 156)),
        "trap": ((114, 31, 45), (178, 52, 67), (247, 175, 126)),
        "guarded": ((61, 83, 78), (174, 137, 57), (244, 220, 126)),
        "falling": ((24, 49, 78), (37, 91, 137), (133, 211, 239)),
    }
    for state, (dark, mid, light) in palettes.items():
        image = Image.new("RGBA", (48, 48), dark + (255,))
        draw = ImageDraw.Draw(image)
        for row in range(3):
            for column in range(3):
                x = column * 16
                y = row * 16
                base = mid if (row + column) % 2 == 0 else dark
                draw.rectangle((x + 1, y + 1, x + 14, y + 14), fill=base + (255,))
                draw.rectangle((x, y, x + 15, y + 15), outline=light + (255,), width=1)
                if state == "normal":
                    draw.line((x + 4, y + 8, x + 11, y + 8), fill=light + (255,), width=1)
                elif state == "trap":
                    draw.line((x + 4, y + 4, x + 11, y + 11), fill=light + (255,), width=2)
                    draw.line((x + 11, y + 4, x + 4, y + 11), fill=light + (255,), width=2)
                elif state == "guarded":
                    draw.polygon([(x + 8, y + 3), (x + 13, y + 6),
                                  (x + 11, y + 12), (x + 8, y + 14),
                                  (x + 5, y + 12), (x + 3, y + 6)],
                                 outline=light + (255,))
                else:
                    draw.polygon([(x + 3, y + 4), (x + 8, y + 9), (x + 13, y + 4)],
                                 outline=light + (255,))
                    draw.polygon([(x + 3, y + 9), (x + 8, y + 14), (x + 13, y + 9)],
                                 outline=light + (255,))
        image.save(RP_TEXTURES / f"rabbit_floor_{state}.png")


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
            "version": [0, 8, 6],
            "min_engine_version": [1, 26, 44],
        },
        "modules": [{
            "type": "resources",
            "uuid": "403506cb-95a6-4dbc-8f2a-afddc2a2cd57",
            "version": [0, 8, 6],
        }],
        "capabilities": ["pbr"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, game_id in sorted(entity_games.items()):
        identifier = f"junket:{name}"
        frame_maximum = 21 if name == "walker_label" else 11 if game_id == "fire" else 6
        properties = {} if name == "walker_bomb" else {
            "junket:frame": {
                "type": "int", "range": [0, frame_maximum], "default": 0,
                "client_sync": True,
            },
        }
        if name == "walker_label":
            properties["junket:floor"] = {
                "type": "int", "range": [1, 37], "default": 1,
                "client_sync": False,
            }
        components = {
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
        }
        if name in ("walker_label", "walker_bomb"):
            components["minecraft:collision_box"] = {"width": 0.1, "height": 0.1}
            components.pop("minecraft:nameable", None)
            components.pop("minecraft:interact", None)
        behavior = {
            "format_version": "1.21.0",
            "minecraft:entity": {
                "description": {
                    "identifier": identifier,
                    "is_spawnable": False,
                    "is_summonable": True,
                    "properties": properties,
                },
                "components": components,
                "events": {"junket:touch": {}},
            },
        }
        if name == "walker_bomb":
            behavior["minecraft:entity"].pop("events")
        (ENTITIES / f"{name}.json").write_text(
            json.dumps(behavior, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if game_id == "fire":
            description = {
                "identifier": identifier,
                "materials": {"default": "entity_alphablend"},
                "textures": {
                    "default": "textures/entity/junket/fire_card",
                    "used": "textures/entity/junket/fire_card_used",
                    "hot": "textures/entity/junket/fire_card_hot",
                    "ice": "textures/entity/junket/fire_card_ice",
                    "active": "textures/entity/junket/fire_card_active",
                    "television": "textures/entity/junket/fire_tv",
                    "glass0": "textures/entity/junket/fire_glass_0",
                    "glass1": "textures/entity/junket/fire_glass_1",
                    "glass2": "textures/entity/junket/fire_glass_2",
                    "glass3": "textures/entity/junket/fire_glass_3",
                    "glass4": "textures/entity/junket/fire_glass_4",
                    "glass5": "textures/entity/junket/fire_glass_broken",
                },
                "geometry": {
                    "default": "geometry.junket.fire.card",
                    "television": "geometry.junket.fire.tv",
                    "glass": "geometry.junket.fire.glass",
                },
                "render_controllers": ["controller.render.junket.fire"],
            }
        elif game_id == "hope" and name == "hope_tv":
            description = {
                "identifier": identifier,
                "materials": {"default": "entity_alphatest"},
                "textures": {"default": "textures/entity/junket/hope_tv"},
                "geometry": {"default": "geometry.junket.hope.tv"},
                "render_controllers": ["controller.render.junket.hope_tv"],
            }
        elif game_id == "hope" and name == "hope_code":
            description = {
                "identifier": identifier,
                "materials": {"default": "entity_alphatest"},
                "textures": {"default": "textures/entity/junket/hope_code"},
                "geometry": {"default": "geometry.junket.hope.code"},
                "render_controllers": ["controller.render.junket.hope_code"],
            }
        elif game_id == "hope" and name == "hope_code_display":
            description = {
                "identifier": identifier,
                "materials": {"default": "entity_alphatest"},
                "textures": {
                    "normal": "textures/entity/junket/hope_code_display",
                    "broken": "textures/entity/junket/hope_code_display_broken",
                },
                "geometry": {"default": "geometry.junket.hope.code_display"},
                "render_controllers": ["controller.render.junket.hope_code_display"],
            }
        elif game_id == "rabbit" and name == "rabbit_floor":
            description = {
                "identifier": identifier,
                "materials": {"default": "entity_alphatest"},
                "textures": {
                    "normal": "textures/entity/junket/rabbit_floor_normal",
                    "trap": "textures/entity/junket/rabbit_floor_trap",
                    "guarded": "textures/entity/junket/rabbit_floor_guarded",
                    "falling": "textures/entity/junket/rabbit_floor_falling",
                },
                "geometry": {"default": "geometry.junket.rabbit.floor"},
                "render_controllers": ["controller.render.junket.rabbit_floor"],
            }
        elif name == "walker_label":
            description = {
                "identifier": identifier,
                "materials": {"default": "entity_alphatest"},
                "textures": {
                    "a": "textures/entity/junket/walker_label_a",
                    "ka": "textures/entity/junket/walker_label_ka",
                    "sa": "textures/entity/junket/walker_label_sa",
                    "ta": "textures/entity/junket/walker_label_ta",
                    "na": "textures/entity/junket/walker_label_na",
                    "ha": "textures/entity/junket/walker_label_ha",
                    "ma": "textures/entity/junket/walker_label_ma",
                    "ya": "textures/entity/junket/walker_label_ya",
                    "ra": "textures/entity/junket/walker_label_ra",
                    "one": "textures/entity/junket/walker_label_one",
                    "six": "textures/entity/junket/walker_label_six",
                    "dead_o": "textures/entity/junket/walker_label_o",
                    "dead_ko": "textures/entity/junket/walker_label_ko",
                    "dead_so": "textures/entity/junket/walker_label_so",
                    "dead_to": "textures/entity/junket/walker_label_to",
                    "dead_no": "textures/entity/junket/walker_label_no",
                    "dead_ho": "textures/entity/junket/walker_label_ho",
                    "dead_mo": "textures/entity/junket/walker_label_mo",
                    "dead_yo": "textures/entity/junket/walker_label_yo",
                    "dead_ro": "textures/entity/junket/walker_label_ro",
                    "dead_five": "textures/entity/junket/walker_label_five",
                    "dead_zero": "textures/entity/junket/walker_label_zero",
                },
                "geometry": {"default": "geometry.junket.walker.label"},
                "render_controllers": ["controller.render.junket.walker_label"],
            }
        elif name == "walker_bomb":
            description = {
                "identifier": identifier,
                "materials": {"default": "entity_alphatest"},
                "textures": {"default": "textures/entity/junket/walker_bomb"},
                "geometry": {"default": "geometry.junket.walker.bomb"},
                "render_controllers": ["controller.render.junket"],
            }
        else:
            description = {
                "identifier": identifier,
                "materials": {"default": "entity_alphatest"},
                "textures": {"default": f"textures/entity/junket/{game_id}"},
                "geometry": {"default": f"geometry.junket.{game_id}"},
                "render_controllers": ["controller.render.junket"],
            }
        client = {
            "format_version": "1.10.0",
            "minecraft:client_entity": {"description": description},
        }
        (RP_ENTITIES / f"junket_{name}.entity.json").write_text(
            json.dumps(client, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    frame = "query.property('junket:frame')"
    fire_texture = " : ".join(
        [f"{frame} == {index} ? Texture.{key}" for index, key in enumerate((
            "default", "used", "hot", "ice", "active", "television",
            "glass0", "glass1", "glass2", "glass3", "glass4", "glass5",
        ))] + ["Texture.glass5"])
    (RP_RENDER / "junket.render_controllers.json").write_text(json.dumps({
        "format_version": "1.8.0",
        "render_controllers": {
            "controller.render.junket": {
                "geometry": "Geometry.default",
                "materials": [{"*": "Material.default"}],
                "textures": ["Texture.default"],
            },
            "controller.render.junket.fire": {
                "geometry": (f"{frame} == 5 ? Geometry.television : "
                             f"{frame} >= 6 ? Geometry.glass : Geometry.default"),
                "materials": [{"*": "Material.default"}],
                "textures": [fire_texture],
            },
            "controller.render.junket.hope_tv": {
                "geometry": "Geometry.default",
                "materials": [{"*": "Material.default"}],
                "textures": ["Texture.default"],
            },
            "controller.render.junket.hope_code": {
                "geometry": "Geometry.default",
                "materials": [{"*": "Material.default"}],
                "textures": ["Texture.default"],
            },
            "controller.render.junket.hope_code_display": {
                "geometry": "Geometry.default",
                "materials": [{"*": "Material.default"}],
                "textures": [f"{frame} == 1 ? Texture.broken : Texture.normal"],
            },
            "controller.render.junket.rabbit_floor": {
                "geometry": "Geometry.default",
                "materials": [{"*": "Material.default"}],
                "textures": [
                    f"{frame} == 1 ? Texture.trap : {frame} == 2 ? Texture.guarded : "
                    f"{frame} == 3 ? Texture.falling : Texture.normal"
                ],
            },
            "controller.render.junket.walker_label": {
                "geometry": "Geometry.default",
                "materials": [{"*": "Material.default"}],
                "textures": [" : ".join(
                    [f"{frame} == {index} ? Texture.{key}" for index, key in enumerate((
                        "a", "ka", "sa", "ta", "na", "ha", "ma", "ya", "ra",
                        "one", "six", "dead_o", "dead_ko", "dead_so", "dead_to",
                        "dead_no", "dead_ho", "dead_mo", "dead_yo", "dead_ro",
                        "dead_five",
                    ))] + ["Texture.dead_zero"]),
                ],
            },
        },
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    texts = RP / "texts"
    texts.mkdir(parents=True, exist_ok=True)
    (texts / "languages.json").write_text('["ja_JP", "en_US"]\n', encoding="utf-8")
    for locale, value in (("ja_JP", "操作する"), ("en_US", "Interact")):
        path = texts / f"{locale}.lang"
        path.write_text(f"action.interact.junket={value}\n", encoding="utf-8")


def _shift_meta_y(meta: dict, offset: int) -> None:
    for feature in meta["features"]:
        feature[1] += offset
    for entry in (*meta["controls"], *meta["displays"]):
        entry["location"][1] = round(entry["location"][1] + offset, 2)
    for key in ("destination", "return_point", "reception_point"):
        value = meta.get(key)
        if isinstance(value, (tuple, list)) and len(value) == 3:
            meta[key] = (value[0], round(value[1] + offset, 2), value[2])
    if meta.get("seat_locations"):
        meta["seat_locations"] = [
            (value[0], round(value[1] + offset, 2), value[2])
            for value in meta["seat_locations"]
        ]
    for fixture in meta.get("fixtures", []):
        fixture["location"][1] = round(fixture["location"][1] + offset, 2)
    for pad in meta.get("hope_pads", []):
        pad["y"] += offset
        for cell in pad["cells"]:
            cell[1] += offset
    for spawn in meta.get("hope_ai_spawns", []):
        spawn["y"] += offset
    points = meta.get("points", {})
    for key, value in tuple(points.items()):
        if isinstance(value, tuple) and len(value) == 3:
            points[key] = (value[0], value[1] + offset, value[2])
        elif isinstance(value, list) and value and isinstance(value[0], tuple):
            points[key] = [(point[0], point[1] + offset, point[2]) for point in value]


def build_venue(game):
    if game["id"] == "hope":
        # The source removes its barrier floor and lets the selected pads fall
        # into open space. Keep that physical pit inside the saved structure:
        # shift the authored fourteen-layer hall upward by four layers and
        # extend the .mcstructure downward so air survives save/reload.
        authored = Structure((23, 14, 27))
        meta = decorate(authored, "hope")
        structure = Structure(SIZES["hope"])
        sx, _, sz = structure.size
        foundation = f"minecraft:{STYLES['hope'][1]}"
        structure.fill((0, 0, 0), (sx - 1, HOPE_PIT_DEPTH - 1, sz - 1), foundation)
        for (x, y, z), name in authored.blocks.items():
            structure.set(x, y + HOPE_PIT_DEPTH, z, name,
                          dict(authored.block_states.get((x, y, z), ())))
        _shift_meta_y(meta, HOPE_PIT_DEPTH)
        for pad in meta["hope_pads"]:
            floor_y = pad["y"]
            for x, _, z in pad["cells"]:
                for y in range(floor_y):
                    structure.set_required(x, y, z, "minecraft:air")
        meta["hope_pit_depth"] = HOPE_PIT_DEPTH + 1
        validate_hope_layout(meta, structure)
        return structure, meta
    structure = Structure(SIZES[game["id"]])
    meta = decorate(structure, game["id"])
    return structure, meta


def structure_signature(structure: Structure, meta: dict):
    coords = {(x, y, z) for x, y, z, _ in meta["features"]}
    sx, sy, sz = structure.size
    coords.update({
        (0, 0, 0), (sx - 1, 0, sz - 1), (sx // 2, 0, sz // 2),
        (0, 2, 0), (sx - 1, 2, 0), (0, 2, sz - 1), (sx - 1, 2, sz - 1),
        (sx // 2, 2, 0), (sx // 2, 2, sz - 1), (0, 2, sz // 2), (sx - 1, 2, sz // 2),
        (sx // 2, sy - 1, sz // 2), (0, sy - 1, 0), (sx - 1, sy - 1, sz - 1),
    })
    solid = sorted((position for position, name in structure.blocks.items()
                    if name != "minecraft:air"), key=lambda value: (value[1], value[2], value[0]))
    step = max(1, len(solid) // 80)
    coords.update(solid[::step][:80])
    return [
        [x, y, z, structure.blocks[(x, y, z)]]
        for x, y, z in sorted(coords, key=lambda value: (value[1], value[2], value[0]))
        if structure.blocks.get((x, y, z), "minecraft:air") != "minecraft:air"
    ]


def choose_seats(structure: Structure, count: int, origin, destination):
    sx, _, sz = structure.size
    candidates = []
    for z in (sz - 8, sz - 9, sz - 7, sz - 10, sz - 11, sz - 12, sz - 13, sz - 14):
        for offset in (0, -2, 2, -4, 4, -6, 6, -8, 8, -10, 10):
            x = sx // 2 + offset
            if 1 <= x < sx - 1 and 2 <= z < sz - 1:
                position = (x, z)
                if any((item["x"] - origin[0] - 0.5, item["z"] - origin[2] - 0.5) == position for item in candidates):
                    continue
                candidates.append({
                    "x": round(origin[0] + x + 0.5, 2),
                    "y": origin[1] + 2,
                    "z": round(origin[2] + z + 0.5, 2),
                })
    if len(candidates) < count:
        raise ValueError(f"not enough clear seats: need {count}, found {len(candidates)}")
    return candidates[:count]


def clearance_cells(structure: Structure, destination, control, return_point, seats, origin,
                    extra_targets=()):
    local = []
    targets = [destination, control, return_point, *seats, *extra_targets]
    for target in targets:
        x = math.floor(target["x"] - origin[0])
        z = math.floor(target["z"] - origin[2])
        y = math.floor(target["y"] - origin[1])
        for height in (y, y + 1):
            if not structure.contains(x, height, z):
                raise ValueError(
                    f"clearance target is outside structure bounds: "
                    f"local=({x}, {height}, {z}) world="
                    f"({target['x']}, {target['y']}, {target['z']}) size={structure.size}")
            # Fixture and teleport coordinates are part of the venue contract.
            # Carve only the target column's Y and Y+1 cells even when a
            # decorative wall or pedestal currently occupies them; the runtime
            # and saved-structure checks must agree on this vertical clearance.
            structure.set_required(x, height, z, "minecraft:air")
            local.append([x, height, z])
    result = sorted({tuple(cell) for cell in local})
    for position in result:
        if structure.blocks.get(position, "minecraft:air") != "minecraft:air":
            raise ValueError(f"clearance cell was not written as air: {position}")
    return result


def validate_hope_layout(meta, structure):
    pads = meta.get("hope_pads", [])
    spawns = meta.get("hope_ai_spawns", [])
    if len(pads) != 16 or len(spawns) != 17:
        raise ValueError("Hope must have 16 pads and 17 AI spawns")
    occupied = {}
    for pad in pads:
        cells = {tuple(cell) for cell in pad["cells"]}
        if len(cells) != 16:
            raise ValueError(f"Hope pad {pad['pad']} does not contain 16 unique cells")
        xs = [cell[0] for cell in cells]
        zs = [cell[2] for cell in cells]
        if max(xs) - min(xs) != 3 or max(zs) - min(zs) != 3:
            raise ValueError(f"Hope pad {pad['pad']} is not 4x4")
        for cell in cells:
            if cell[1] != pad["y"]:
                raise ValueError(f"Hope pad {pad['pad']} has a non-floor cell: {cell}")
            for height in range(cell[1]):
                if structure.blocks.get((cell[0], height, cell[2]), "minecraft:air") != \
                        "minecraft:air":
                    raise ValueError(
                        f"Hope pad {pad['pad']} has no fall shaft at "
                        f"{(cell[0], height, cell[2])}")
            for height in (cell[1] + 1, cell[1] + 2):
                if structure.blocks.get((cell[0], height, cell[2]), "minecraft:air") != \
                        "minecraft:air":
                    raise ValueError(
                        f"Hope pad {pad['pad']} has blocked standing space at "
                        f"{(cell[0], height, cell[2])}")
            if cell in occupied:
                raise ValueError(
                    f"Hope pads {occupied[cell]} and {pad['pad']} overlap at {cell}")
            occupied[cell] = pad["pad"]
    if len(occupied) != 16 * 16:
        raise ValueError("Hope pads do not fill a 16x16 floor")
    if len({cell[0] for cell in occupied}) != 16 or len({cell[2] for cell in occupied}) != 16:
        raise ValueError("Hope pads do not form a contiguous 16x16 barrier")
    expected_apparatus = {
        "hope_tv_1": (10.5, 15.0, 6.6), "hope_tv_2": (7.5, 15.0, 6.6),
        "hope_tv_3": (12.5, 15.0, 6.6), "hope_tv_4": (10.5, 16.0, 6.6),
        "hope_tv_5": (8.5, 13.0, 6.6), "hope_tv_6": (12.5, 13.0, 6.6),
        "hope_code": (16.5, 7.0, 6.5),
        "hope_code_display": (16.5, 9.0, 6.5),
    }
    apparatus = {fixture["role"]: fixture for fixture in meta.get("fixtures", [])}
    if set(apparatus) != set(expected_apparatus):
        raise ValueError("Hope must have six TV panels, one code and one code display")
    for role, expected in expected_apparatus.items():
        actual = apparatus[role]["location"]
        if any(abs(actual[index] - expected[index]) > 0.001 for index in range(3)):
            raise ValueError(f"Hope apparatus {role} has a shifted location: {actual}")
        x, y, z = (math.floor(value) for value in actual)
        for height in (y, y + 1):
            if structure.blocks.get((x, height, z), "minecraft:air") != "minecraft:air":
                raise ValueError(f"Hope apparatus {role} is blocked at {(x, height, z)}")
    code = apparatus["hope_code"]["location"]
    display = apparatus["hope_code_display"]["location"]
    if abs(code[0] - display[0]) > 0.001 or abs(code[2] - display[2]) > 0.001 or \
            abs(display[1] - code[1] - 2.0) > 0.001:
        raise ValueError("Hope code display must be two blocks above the code target")
    for spawn in spawns:
        pad = pads[min(spawn["pad"], 16) - 1]
        expected = (pad["center_x"], pad["y"] + 1, pad["center_z"] + 2)
        if any(abs(spawn[axis] - expected[index]) > 0.001
               for index, axis in enumerate(("x", "y", "z"))):
            raise ValueError(f"Hope AI {spawn['id']} is not on its judge marker: {spawn}")
        standing = (math.floor(spawn["x"]), spawn["y"] - 1,
                    math.floor(spawn["z"]))
        if standing not in occupied:
            raise ValueError(f"Hope AI {spawn['id']} has no pad floor: {spawn}")
        for height in (spawn["y"], spawn["y"] + 1):
            if structure.blocks.get((standing[0], height, standing[2]),
                                    "minecraft:air") != "minecraft:air":
                raise ValueError(f"Hope AI {spawn['id']} spawn is blocked: {spawn}")
        for control in meta["controls"]:
            cx, cy, cz = control["location"]
            if math.hypot(spawn["x"] - cx, spawn["z"] - cz) < 1.05 and \
                    abs(spawn["y"] - cy) < 2:
                raise ValueError(
                    f"Hope AI {spawn['id']} overlaps its floor control: {control['label']}")
    for control in meta["controls"]:
        if not str(control.get("action", "")).startswith("floor_"):
            continue
        x, y, z = (math.floor(value) for value in control["location"])
        support = structure.blocks.get((x, y - 1, z), "minecraft:air")
        if support == "minecraft:air":
            raise ValueError(
                f"Hope floor control has no physical support: {control['label']} "
                f"at {(x, y - 1, z)}")


def validate_rabbit_layout(meta, structure):
    rabbit = meta.get("rabbit")
    if not rabbit or len(rabbit.get("cells", [])) != 60:
        raise ValueError("Rabbit must have exactly 60 legal floor cells")
    occupied = set()
    for cell in rabbit["cells"]:
        tile = cell["tile"]
        row, column = divmod(tile - 1, 12)
        expected_min_x = rabbit["max_x"] - column * 3 - 2
        expected_min_z = rabbit["max_z"] - row * 3 - 2
        cells = {tuple(value) for value in cell["floor_cells"]}
        if len(cells) != 9:
            raise ValueError(f"Rabbit tile {tile} does not contain nine floor cells")
        xs = [value[0] for value in cells]
        zs = [value[2] for value in cells]
        if min(xs) != expected_min_x or max(xs) != expected_min_x + 2 or \
                min(zs) != expected_min_z or max(zs) != expected_min_z + 2:
            raise ValueError(f"Rabbit tile {tile} is not the expected 3x3 footprint")
        for x, y, z in cells:
            for height in (y + 1, y + 2):
                if structure.blocks.get((x, height, z), "minecraft:air") != "minecraft:air":
                    raise ValueError(
                        f"Rabbit tile {tile} has blocked standing space at {(x, height, z)}")
        if occupied.intersection(cells):
            raise ValueError(f"Rabbit tile {tile} overlaps another tile")
        occupied.update(cells)
    if len(occupied) != 36 * 15:
        raise ValueError("Rabbit legal tiles must fill a 36x15 board")
    if len({cell[0] for cell in occupied}) != 36 or len({cell[2] for cell in occupied}) != 15:
        raise ValueError("Rabbit legal tiles must form one contiguous board")
    oni = rabbit.get("oni")
    if not oni or oni.get("tile") != 61 or len(oni.get("floor_cells", [])) != 9:
        raise ValueError("Rabbit tile 61 must be present as a separate 3x3 oni floor")
    oni_cells = {tuple(value) for value in oni["floor_cells"]}
    if len(oni_cells) != 9 or oni_cells.intersection(occupied):
        raise ValueError("Rabbit tile 61 must not overlap the 60 legal tiles")
    for x, y, z in oni_cells:
        for height in (y + 1, y + 2):
            if structure.blocks.get((x, height, z), "minecraft:air") != "minecraft:air":
                raise ValueError(
                    f"Rabbit tile 61 has blocked standing space at {(x, height, z)}")
    if any(cell["tile"] == 61 for cell in rabbit["cells"]):
        raise ValueError("Rabbit tile 61 must not be part of the legal tile list")
    for control in meta["controls"]:
        if not str(control.get("action", "")).startswith("floor_"):
            continue
        x, y, z = (math.floor(value) for value in control["location"])
        support = structure.blocks.get((x, y - 1, z), "minecraft:air")
        if support == "minecraft:air":
            raise ValueError(
                f"Rabbit floor control has no physical support: {control['label']} "
                f"at {(x, y - 1, z)}")


def _world_point(origin, local):
    return {
        "x": round(origin[0] + local[0], 3),
        "y": round(origin[1] + local[1], 3),
        "z": round(origin[2] + local[2], 3),
    }


def _special_layout(game_id, origin, title):
    source = SPECIAL_LAYOUTS[game_id]
    provider = SPECIAL_PROVIDERS[game_id]
    fixtures = []
    for role, entity_type, local, name, tags, properties in source["fixtures"]:
        fixtures.append({
            "role": role,
            "type": entity_type,
            "location": [_world_point(origin, local)[axis] for axis in ("x", "y", "z")],
            "name": name,
            "tags": [provider["collection"], f"junket:{game_id}:fixture:{role}", *tags],
            "properties": {
                provider["role_property"]: role,
                "junket:venue_action": role,
                **properties,
            },
            "action": role,
        })
    points = {}
    for name, value in source["points"].items():
        if isinstance(value, tuple):
            points[name] = _world_point(origin, value)
        elif isinstance(value, list) and value and isinstance(value[0], tuple):
            points[name] = [_world_point(origin, item) for item in value]
        else:
            points[name] = value
    destination = _world_point(origin, source["destination"])
    return_point = _world_point(origin, source["return_point"])
    seats = [_world_point(origin, value) for value in source["seats"]]
    return {
        "destination": destination,
        "return_point": return_point,
        "seats": seats,
        "reception": _dict_point(next(item for item in fixtures if item["role"] == "reception")["location"]),
        "control": _dict_point(next((item["location"] for item in fixtures if "junket:" + game_id + ":control" in item["tags"]), fixtures[0]["location"])),
        "fixtures": fixtures,
        "points": points,
        "provider": provider,
    }


def _dict_point(location):
    return {"x": location[0], "y": location[1], "z": location[2]}


def _special_clearance(manifest):
    targets = [manifest["destination"], manifest["return_point"], *manifest["seats"]]
    for fixture in manifest["fixtures"]:
        x, y, z = fixture["location"]
        targets.append({"x": x, "y": y, "z": z})
    for value in manifest["points"].values():
        if isinstance(value, dict):
            targets.append(value)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            targets.extend(value)
    return targets


def venue_plan(games):
    plan = []
    structures = {}
    for index, game in enumerate(games):
        game_id = game["id"]
        structure, meta = build_venue(game)
        size = SIZES[game_id]
        origin = [
            WORLD_ORIGIN[0] + index * SPACING,
            WORLD_ORIGIN[1] - (HOPE_PIT_DEPTH if game_id == "hope" else 0),
            WORLD_ORIGIN[2],
        ]
        special = game_id in SPECIAL_IDS
        special_manifest = _special_layout(game_id, origin, game["title"]) if special else None
        if special:
            destination = special_manifest["destination"]
            return_point = special_manifest["return_point"]
            seats = special_manifest["seats"]
            control = special_manifest["control"] or special_manifest["reception"]
            fixtures = special_manifest["fixtures"]
            extra_targets = _special_clearance(special_manifest)
            points = special_manifest["points"]
        else:
            destination = (_world_point(origin, meta["destination"])
                           if meta.get("destination") else {
                               "x": origin[0] + size[0] // 2 + 0.5,
                               "y": origin[1] + 2,
                               "z": origin[2] + size[2] - 5.5,
                           })
            seat_locations = meta.get("seat_locations")
            if seat_locations:
                seats = [{
                    "x": round(origin[0] + value[0], 2),
                    "y": round(origin[1] + value[1], 2),
                    "z": round(origin[2] + value[2], 2),
                } for value in seat_locations]
            else:
                seats = choose_seats(
                    structure, game["max_players"] or game["min_players"], origin, destination)
            control = {
                "x": origin[0] + meta["controls"][0]["location"][0],
                "y": origin[1] + meta["controls"][0]["location"][1],
                "z": origin[2] + meta["controls"][0]["location"][2],
            } if meta["controls"] else {
                "x": origin[0] + size[0] // 2 + 0.5,
                "y": origin[1] + 2.2,
                "z": origin[2] + size[2] // 2 + 0.5,
            }
            return_point = (_world_point(origin, meta["return_point"])
                            if meta.get("return_point") else {
                                "x": origin[0] + size[0] // 2 - 2.5,
                                "y": origin[1] + 2,
                                "z": origin[2] + size[2] - 3.5,
                            })
            points = meta.get("points", {})
            extra_targets = []
            fixtures = []
            reception_point = (_world_point(origin, meta["reception_point"])
                               if meta.get("reception_point") else {
                                   "x": origin[0] + size[0] // 2 + 2.5,
                                   "y": origin[1] + 2,
                                   "z": origin[2] + size[2] - 3.5,
                               })
            fixtures.append({
                "role": "reception", "type": "minecraft:npc",
                "location": [reception_point["x"], reception_point["y"], reception_point["z"]],
                "name": f"{game['title']}受付 / 参加・操作・再戦",
                "tags": [f"junket:{game_id}:fixture:reception", f"junket:{game_id}:reception", f"junket:{game_id}:control"],
                "properties": {"junket:venue_action": "reception"},
                "action": "reception",
            })
            actions = CONTROL_ACTIONS.get(game_id, [])
            for control_index, value in enumerate(meta["controls"]):
                action = value.get("action") or (
                    actions[control_index] if control_index < len(actions)
                    else f"control_{control_index + 1}"
                )
                control_type = ("junket:rabbit_floor"
                                if game_id == "rabbit" and action.startswith("floor_")
                                else f"junket:{game_id}_prop")
                fixtures.append({
                    "role": f"control_{control_index + 1}", "type": control_type,
                    "location": [origin[0] + value["location"][0], origin[1] + value["location"][1],
                                 origin[2] + value["location"][2]],
                    "name": value["label"],
                    "tags": [f"junket:{game_id}:fixture:control_{control_index + 1}", f"junket:{game_id}:control"],
                    "properties": {"junket:venue_action": action, **value.get("properties", {})},
                    "action": action,
                })
            for display_index, value in enumerate(meta["displays"]):
                fixtures.append({
                    "role": f"display_{display_index + 1}", "type": f"junket:{game_id}_prop",
                    "location": [origin[0] + value["location"][0], origin[1] + value["location"][1],
                                 origin[2] + value["location"][2]],
                    "name": value["label"],
                    "tags": [f"junket:{game_id}:fixture:display_{display_index + 1}", f"junket:{game_id}:display"],
                    "properties": {"junket:venue_action": "display"},
                    "action": "display",
                })
            fixtures.append({
                "role": "return", "type": "minecraft:npc",
                "location": [return_point["x"], return_point["y"], return_point["z"]],
                "name": "入口へ戻る / JUNKET",
                "tags": [f"junket:{game_id}:fixture:return", "junket:return"],
                "properties": {"junket:venue_action": "return"},
                "action": "return",
            })
            # Dedicated source apparatus such as Hope's six TV surfaces and
            # judge code use entity types that are not the generic game prop.
            # Keep their authored local anchors in the same fixture contract so
            # clearance, persistence verification and runtime repair all agree.
            for extra in meta.get("fixtures", []):
                fixtures.append({
                    "role": extra["role"], "type": extra["type"],
                    "location": [
                        origin[0] + extra["location"][0],
                        origin[1] + extra["location"][1],
                        origin[2] + extra["location"][2],
                    ],
                    "name": extra["name"],
                    "tags": list(extra.get("tags", [])),
                    "properties": dict(extra.get("properties", {})),
                    "action": extra.get("action", extra["role"]),
                })
            # Every fixture that the runtime creates must have the same empty
            # target column (Y and Y+1) in the generated layout and saved
            # structure. This is a vertical clearance, not a horizontal 2x2
            # footprint; adjacent walls and floors must remain intact.
            extra_targets = [_dict_point(fixture["location"]) for fixture in fixtures]
            for value in points.values():
                if isinstance(value, dict):
                    extra_targets.append(value)
                elif isinstance(value, list) and value and isinstance(value[0], dict):
                    extra_targets.extend(value)
        drop_areas = []
        clock_geometry = None
        hope_pads = []
        hope_ai_spawns = []
        if game_id == "garden":
            for seat_index, floor_name in enumerate(meta["drop_floor_blocks"]):
                drop_areas.append({
                    "seat": seat_index,
                    "floor": floor_name,
                    "radius": 1,
                    "detect": "minecraft:brown_wool",
                })
        elif game_id == "clock":
            source = meta["clock_geometry"]
            clock_geometry = {
                "center_x": origin[0] + source["center"][0],
                "center_y": origin[1] + source["center"][1],
                "center_z": origin[2] + source["center"][2],
                "hour_reach": source["hour_reach"],
                "minute_reach": source["minute_reach"],
                "half_width": source["half_width"],
                "min_radius": source["min_radius"],
            }
            for sector in meta["clock_sector_cells"]:
                drop_areas.append({
                    "sector": sector["sector"],
                    "floor": sector["floor"],
                    "detect": "minecraft:water",
                    "cells": [{
                        "x": origin[0] + cell[0],
                        "y": origin[1] + cell[1],
                        "z": origin[2] + cell[2],
                    } for cell in sector["cells"]],
                })
        elif game_id == "hope":
            for pad in meta["hope_pads"]:
                x = origin[0] + pad["x"]
                y = origin[1] + pad["y"]
                z = origin[2] + pad["z"]
                cells = [{
                    "x": origin[0] + cell[0],
                    "y": origin[1] + cell[1],
                    "z": origin[2] + cell[2],
                } for cell in pad["cells"]]
                hope_pads.append({
                    "pad": pad["pad"], "symbol": pad["symbol"],
                    "symbol_name": pad["symbol_name"],
                    "x": x, "y": y + 1, "z": z, "floor": pad["floor"],
                    "center_x": origin[0] + pad["center_x"],
                    "center_y": y + 1,
                    "center_z": origin[2] + pad["center_z"],
                    "cells": cells,
                })
                drop_areas.append({
                    "pad": pad["pad"], "symbol": pad["symbol"],
                    "floor": pad["floor"], "cells": cells,
                })
            for spawn in meta["hope_ai_spawns"]:
                hope_ai_spawns.append({
                    "id": spawn["id"], "pad": spawn["pad"],
                    "x": origin[0] + spawn["x"],
                    "y": origin[1] + spawn["y"],
                    "z": origin[2] + spawn["z"],
                })
        walker = None
        if game_id == "walker":
            source = meta["walker"]
            walker = {
                "platform_y": origin[1] + source["platform_y"],
                "water_bottom": origin[1] + source["water_bottom"],
                "water_top": origin[1] + source["water_top"],
                "min_x": origin[0] + source["min_x"],
                "max_x": origin[0] + source["max_x"],
                "min_z": origin[2] + source["min_z"],
                "max_z": origin[2] + source["max_z"],
                "cells": [{
                    "floor": cell["floor"],
                    "color": cell["color"],
                    "x": origin[0] + cell["x"],
                    "z": origin[2] + cell["z"],
                } for cell in source["cells"]],
            }
        rabbit = None
        if game_id == "rabbit":
            source = meta["rabbit"]
            rabbit = {
                "floor_y": origin[1] + source["floor_y"],
                "water_y": origin[1] + source["water_y"],
                "stand_y": origin[1] + source["stand_y"],
                "min_x": origin[0] + source["min_x"],
                "max_x": origin[0] + source["max_x"],
                "min_z": origin[2] + source["min_z"],
                "max_z": origin[2] + source["max_z"],
                "spectator": {
                    "x": origin[0] + source["spectator"][0],
                    "y": origin[1] + source["spectator"][1],
                    "z": origin[2] + source["spectator"][2],
                },
                "cells": [{
                    "tile": cell["tile"],
                    "x": origin[0] + cell["x"],
                    "z": origin[2] + cell["z"],
                    "floor": cell["floor"],
                    "floor_cells": [[origin[0] + x, origin[1] + y, origin[2] + z]
                                    for x, y, z in cell["floor_cells"]],
                    "water_cells": [[origin[0] + x, origin[1] + y, origin[2] + z]
                                    for x, y, z in cell["water_cells"]],
                } for cell in source["cells"]],
                "oni": {
                    "tile": 61,
                    "x": origin[0] + source["oni"]["x"],
                    "z": origin[2] + source["oni"]["z"],
                    "floor": source["oni"]["floor"],
                    "floor_cells": [[origin[0] + x, origin[1] + y, origin[2] + z]
                                    for x, y, z in source["oni"]["floor_cells"]],
                    "water_cells": [],
                },
                "guard_shelf": {
                    "min_x": origin[0] + source["guard_shelf"]["min_x"],
                    "max_x": origin[0] + source["guard_shelf"]["max_x"],
                    "min_z": origin[2] + source["guard_shelf"]["min_z"],
                    "max_z": origin[2] + source["guard_shelf"]["max_z"],
                    "floor_y": origin[1] + source["guard_shelf"]["floor_y"],
                },
            }
            for cell in rabbit["cells"]:
                drop_areas.append({
                    "tile": cell["tile"], "floor": cell["floor"],
                    "detect": "minecraft:water", "cells": cell["floor_cells"],
                    "water_cells": cell["water_cells"],
                })
        if game_id == "hope":
            # Player and AI spawn points are runtime destinations. Include the
            # exact saved-structure cells in the clearance contract so a stale
            # fixture or decorative block cannot silently block a trader.
            extra_targets.extend({
                "x": origin[0] + spawn["x"],
                "y": origin[1] + spawn["y"],
                "z": origin[2] + spawn["z"],
            } for spawn in meta["hope_ai_spawns"])
            extra_targets.append(_world_point(origin, points["spectator"]))
            extra_targets.append(_world_point(origin, points["warp"]))
            extra_targets.append(_world_point(origin, points["lose"]))
        clear = clearance_cells(
            structure, destination, control, return_point, seats, origin, extra_targets)
        structures[game_id] = structure
        plan.append({
            "id": game_id,
            "title": game["title"],
            "structure": f"junket:{game_id}_hall",
            "design": meta["design"],
            "origin": origin,
            "origin_block": f"minecraft:{STYLES[game_id][1]}",
            "size": list(size),
            "destination": destination,
            "rotation": game.get("rotation", 0) if special else GENERIC_ROTATION,
            "control": control,
            "return_point": return_point,
            "seats": seats,
            "seat_rotations": meta.get("seat_rotations"),
            "points": points,
            "hope_spectator": (
                _world_point(origin, points["spectator"])
                if isinstance(points.get("spectator"), (tuple, list)) and len(points["spectator"]) == 3
                else points.get("spectator")
            ),
            "hope_warp": (
                _world_point(origin, points["warp"])
                if isinstance(points.get("warp"), (tuple, list)) and len(points["warp"]) == 3
                else points.get("warp")
            ),
            "hope_lose": (
                _world_point(origin, points["lose"])
                if isinstance(points.get("lose"), (tuple, list)) and len(points["lose"]) == 3
                else points.get("lose")
            ),
            "drop_areas": drop_areas,
            "hope_pit_depth": HOPE_PIT_DEPTH + 1 if game_id == "hope" else 0,
            "hope_pads": hope_pads,
            "hope_ai_spawns": hope_ai_spawns,
            "walker": walker,
            "rabbit": rabbit,
            "clock_geometry": clock_geometry,
            "signature": structure_signature(structure, meta),
            "layout": structure.layout_rle(),
            "clearance": [list(cell) for cell in clear],
            "fixtures": fixtures,
            "legacy_blocks": sorted({f"minecraft:{name}" for name in STYLES[game_id]} | {
                "minecraft:glowstone", "minecraft:light", "minecraft:sea_lantern",
            }),
            "provider": SPECIAL_PROVIDERS.get(game_id),
            "special_runtime": special,
            "max_players": game["max_players"] or game["min_players"],
        })
    return plan, structures


def runtime_source(plan):
    body = json.dumps({
        "VENUE_SCHEMA": 9,
        "VENUE_INSTALL_KEY": "junket:managed_venues_v12",
        "VENUES": plan,
    }, ensure_ascii=False, indent=2)
    return (
        "// Generated by scripts/minecraft/build_junket_original_world.py.\n"
        "// Independently authored substitute structures and model textures.\n"
        "// Licensed JUNKET CRAFT source assets are not included.\n"
        f"export const VENUE_CONFIG = Object.freeze({body});\n"
    )


def write_runtime(plan):
    GENERATED.write_text(runtime_source(plan), encoding="utf-8")


def _canonical_state(value):
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else value
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _expected_cells(structure: Structure):
    sx, sy, sz = structure.size
    for x in range(sx):
        for y in range(sy):
            for z in range(sz):
                position = (x, y, z)
                name = structure.blocks.get(position, "minecraft:air")
                states = tuple(sorted(
                    (str(key), _canonical_state(value))
                    for key, value in structure.block_states.get(position, ())
                ))
                yield position, name, states


def _saved_cells(root, size):
    sx, sy, sz = size
    structure = root["structure"]
    indices = list(structure["block_indices"][0])
    if len(indices) != sx * sy * sz:
        raise ValueError(
            f"saved block index count mismatch: {len(indices)} != {sx * sy * sz}")
    palette = list(structure["palette"]["default"]["block_palette"])
    for x in range(sx):
        for y in range(sy):
            for z in range(sz):
                cell = x * sy * sz + y * sz + z
                index = int(indices[cell])
                if index < 0:
                    yield (x, y, z), "minecraft:air", ()
                    continue
                if index >= len(palette):
                    raise ValueError(f"saved palette index out of range at {(x, y, z)}: {index}")
                entry = palette[index]
                states = tuple(sorted(
                    (str(key), _canonical_state(value))
                    for key, value in (entry.get("states") or {}).items()
                ))
                yield (x, y, z), str(entry["name"]), states


def _verify_saved_structure(game_id, structure: Structure, plan_entry):
    path = STRUCTURES / f"{game_id}_hall.mcstructure"
    if not path.exists():
        raise SystemExit(f"missing JUNKET venue structure: {path.relative_to(ROOT)}")
    with path.open("rb") as stream:
        root = n.File.parse(stream, byteorder="little")
    size = tuple(int(value) for value in root["size"])
    if list(size) != list(structure.size):
        raise SystemExit(f"JUNKET venue size mismatch: {game_id}")
    expected = list(_expected_cells(structure))
    saved = list(_saved_cells(root, size))
    saved_by_position = {position: (name, states) for position, name, states in saved}
    for (position, wanted_name, wanted_states), (_, saved_name, saved_states) in zip(expected, saved):
        if wanted_name != saved_name or wanted_states != saved_states:
            raise SystemExit(
                f"JUNKET venue block mismatch: {game_id} at {position} "
                f"expected={wanted_name}{wanted_states} saved={saved_name}{saved_states}")
    for cell in plan_entry["clearance"]:
        local = (math.floor(cell[0]), math.floor(cell[1]), math.floor(cell[2]))
        if not structure.contains(*local):
            raise SystemExit(
                f"JUNKET clearance outside structure: {game_id} at {cell} local={local}")
        if structure.blocks.get(local, "minecraft:air") != "minecraft:air":
            raise SystemExit(
                f"JUNKET clearance is not air in generated layout: {game_id} at {local}")
        if saved_by_position.get(local, ("minecraft:air", ()))[0] != "minecraft:air":
            raise SystemExit(
                f"JUNKET clearance is not air in saved structure: {game_id} at {local}")
    # Every runtime target must map to a carved clearance cell. This catches a
    # destination or fixture that was generated against a different origin than
    # the saved structure, even if both coordinate sets look plausible alone.
    origin = plan_entry["origin"]
    targets = [plan_entry["destination"], plan_entry["control"],
               plan_entry["return_point"], *plan_entry["seats"]]
    targets.extend({"x": value["location"][0], "y": value["location"][1],
                    "z": value["location"][2]} for value in plan_entry["fixtures"])
    if plan_entry["special_runtime"]:
        for value in plan_entry["points"].values():
            if isinstance(value, dict):
                targets.append(value)
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                targets.extend(value)
    if game_id == "hope":
        targets.extend(plan_entry["hope_ai_spawns"])
        if plan_entry["hope_spectator"]:
            targets.append(plan_entry["hope_spectator"])
        if plan_entry["hope_warp"]:
            targets.append(plan_entry["hope_warp"])
        if plan_entry["hope_lose"]:
            targets.append(plan_entry["hope_lose"])
    clearance = {tuple(cell) for cell in plan_entry["clearance"]}
    expected_clearance = set()
    for target in targets:
        x = math.floor(target["x"] - origin[0])
        y = math.floor(target["y"] - origin[1])
        z = math.floor(target["z"] - origin[2])
        for height in (y, y + 1):
            expected_clearance.add((x, height, z))
            if (x, height, z) not in clearance:
                raise SystemExit(
                    f"JUNKET runtime target lacks clearance: {game_id} "
                    f"world=({target['x']}, {target['y']}, {target['z']}) "
                    f"local=({x}, {height}, {z})")
    if clearance != expected_clearance:
        extra = sorted(clearance - expected_clearance)
        missing = sorted(expected_clearance - clearance)
        raise SystemExit(
            f"JUNKET clearance shape mismatch: {game_id} "
            f"extra={extra[:8]} missing={missing[:8]}")
    floor_targets = [
        plan_entry["destination"], plan_entry["return_point"], *plan_entry["seats"],
    ]
    floor_targets.extend(
        {"x": value["location"][0], "y": value["location"][1], "z": value["location"][2]}
        for value in plan_entry["fixtures"] if value["role"] in ("reception", "return")
    )
    if game_id == "hope":
        floor_targets.extend((plan_entry["hope_warp"], plan_entry["hope_lose"]))
    for target in floor_targets:
        x = math.floor(target["x"] - origin[0])
        y = math.floor(target["y"] - origin[1]) - 1
        z = math.floor(target["z"] - origin[2])
        if not structure.contains(x, y, z):
            raise SystemExit(
                f"JUNKET runtime target floor is outside structure: {game_id} "
                f"world=({target['x']}, {target['y']}, {target['z']}) local=({x}, {y}, {z})")
        if structure.blocks.get((x, y, z), "minecraft:air") == "minecraft:air":
            raise SystemExit(
                f"JUNKET runtime target has no generated floor: {game_id} "
                f"world=({target['x']}, {target['y']}, {target['z']}) local=({x}, {y}, {z})")
        if saved_by_position.get((x, y, z), ("minecraft:air", ()))[0] == "minecraft:air":
            raise SystemExit(
                f"JUNKET runtime target has no saved floor: {game_id} "
                f"world=({target['x']}, {target['y']}, {target['z']}) local=({x}, {y}, {z})")
    if plan_entry["layout"] != structure.layout_rle():
        raise SystemExit(f"JUNKET runtime layout mismatch: {game_id}")
    if game_id == "hope":
        spawns = plan_entry["hope_ai_spawns"]
        pads = plan_entry["hope_pads"]
        if len(pads) != 16 or len(spawns) != 17:
            raise SystemExit("JUNKET Hope must have exactly 16 pads and 17 AI spawns")
        apparatus = {
            fixture["role"]: fixture for fixture in plan_entry["fixtures"]
            if fixture["role"].startswith("hope_tv_") or fixture["role"] in
            ("hope_code", "hope_code_display")
        }
        expected_roles = {f"hope_tv_{index}" for index in range(1, 7)} | {
            "hope_code", "hope_code_display",
        }
        if set(apparatus) != expected_roles:
            raise SystemExit(
                "JUNKET Hope must persist six TV surfaces, one code and one code display")
        for role, fixture in apparatus.items():
            if fixture["type"] != (
                    "junket:hope_tv" if role.startswith("hope_tv_")
                    else f"junket:{role}"):
                raise SystemExit(f"JUNKET Hope apparatus type mismatch: {role}")
        if [spawn["id"] for spawn in spawns] != list(range(1, 18)):
            raise SystemExit("JUNKET Hope AI spawn ids must be exactly 1..17")
        if [spawn["pad"] for spawn in spawns] != list(range(1, 18)):
            raise SystemExit("JUNKET Hope AI spawn pads must be exactly 1..17")
        floor_cells = set()
        pad_cell_sets = {}
        for pad in pads:
            cells = {(cell["x"], cell["y"], cell["z"]) for cell in pad["cells"]}
            if len(cells) != 16:
                raise SystemExit(f"JUNKET Hope pad {pad['pad']} must contain 16 unique cells")
            xs = [cell[0] for cell in cells]
            zs = [cell[2] for cell in cells]
            if max(xs) - min(xs) != 3 or max(zs) - min(zs) != 3:
                raise SystemExit(f"JUNKET Hope pad {pad['pad']} must be exactly 4x4")
            overlap = floor_cells.intersection(cells)
            if overlap:
                raise SystemExit(f"JUNKET Hope pad {pad['pad']} overlaps another pad: {overlap}")
            floor_cells.update(cells)
            pad_cell_sets[pad["pad"]] = cells
            for cell in cells:
                local = (
                    math.floor(cell[0] - plan_entry["origin"][0]),
                    math.floor(cell[1] - plan_entry["origin"][1]),
                    math.floor(cell[2] - plan_entry["origin"][2]),
                )
                if structure.blocks.get(local, "minecraft:air") != pad["floor"]:
                    raise SystemExit(
                        f"JUNKET Hope pad {pad['pad']} floor mismatch at {local}")
                if saved_by_position.get(local, ("minecraft:air", ()))[0] != pad["floor"]:
                    raise SystemExit(
                        f"JUNKET Hope saved pad {pad['pad']} floor mismatch at {local}")
                for height in range(local[1]):
                    shaft = (local[0], height, local[2])
                    if structure.blocks.get(shaft, "minecraft:air") != "minecraft:air":
                        raise SystemExit(
                            f"JUNKET Hope fall shaft is blocked in generated layout at {shaft}")
                    if saved_by_position.get(
                            shaft, ("minecraft:air", ()))[0] != "minecraft:air":
                        raise SystemExit(
                            f"JUNKET Hope fall shaft is blocked in saved structure at {shaft}")
                for height in (local[1] + 1, local[1] + 2):
                    standing = (local[0], height, local[2])
                    if structure.blocks.get(standing, "minecraft:air") != "minecraft:air":
                        raise SystemExit(
                            f"JUNKET Hope pad {pad['pad']} standing space is blocked "
                            f"in generated layout at {standing}")
                    if saved_by_position.get(
                            standing, ("minecraft:air", ()))[0] != "minecraft:air":
                        raise SystemExit(
                            f"JUNKET Hope pad {pad['pad']} standing space is blocked "
                            f"in saved structure at {standing}")
        if len(floor_cells) != 256:
            raise SystemExit("JUNKET Hope pads must fill a 16x16 floor")
        if len({cell[0] for cell in floor_cells}) != 16 or len({cell[2] for cell in floor_cells}) != 16:
            raise SystemExit("JUNKET Hope pads must form one contiguous 16x16 barrier")
        for spawn in spawns:
            local = (
                math.floor(spawn["x"] - plan_entry["origin"][0]),
                math.floor(spawn["y"] - plan_entry["origin"][1]),
                math.floor(spawn["z"] - plan_entry["origin"][2]),
            )
            if not structure.contains(*local):
                raise SystemExit(f"JUNKET Hope AI spawn outside structure: {spawn}")
            if structure.blocks.get(local, "minecraft:air") != "minecraft:air":
                raise SystemExit(f"JUNKET Hope AI spawn is blocked in generated layout: {spawn}")
            if saved_by_position.get(local, ("minecraft:air", ()))[0] != "minecraft:air":
                raise SystemExit(f"JUNKET Hope AI spawn is blocked in saved structure: {spawn}")
            pad = pads[min(spawn["pad"], 16) - 1]
            expected = (pad["center_x"], pad["center_y"], pad["center_z"] + 2)
            if any(abs(spawn[axis] - expected[index]) > 0.001
                   for index, axis in enumerate(("x", "y", "z"))):
                raise SystemExit(f"JUNKET Hope AI spawn {spawn['id']} misses its judge marker")
            standing = (math.floor(spawn["x"]), math.floor(spawn["y"]) - 1,
                        math.floor(spawn["z"]))
            if standing not in floor_cells:
                raise SystemExit(f"JUNKET Hope AI spawn {spawn['id']} has no pad floor")
        spectator = plan_entry["hope_spectator"]
        spectator_local = (
            math.floor(spectator["x"] - plan_entry["origin"][0]),
            math.floor(spectator["y"] - plan_entry["origin"][1]),
            math.floor(spectator["z"] - plan_entry["origin"][2]),
        )
        if not structure.contains(*spectator_local):
            raise SystemExit("JUNKET Hope spectator point is outside the structure")
        if structure.blocks.get(spectator_local, "minecraft:air") != "minecraft:air":
            raise SystemExit("JUNKET Hope spectator point is blocked in generated layout")
        if saved_by_position.get(spectator_local, ("minecraft:air", ()))[0] != "minecraft:air":
            raise SystemExit("JUNKET Hope spectator point is blocked in saved structure")
        warp = plan_entry["hope_warp"]
        warp_local = (
            math.floor(warp["x"] - plan_entry["origin"][0]),
            math.floor(warp["y"] - plan_entry["origin"][1]),
            math.floor(warp["z"] - plan_entry["origin"][2]),
        )
        if not structure.contains(*warp_local):
            raise SystemExit("JUNKET Hope warp point is outside the structure")
        if structure.blocks.get(warp_local, "minecraft:air") != "minecraft:air":
            raise SystemExit("JUNKET Hope warp point is blocked in generated layout")
        if saved_by_position.get(warp_local, ("minecraft:air", ()))[0] != "minecraft:air":
            raise SystemExit("JUNKET Hope warp point is blocked in saved structure")
        warp_floor = (warp_local[0], warp_local[1] - 1, warp_local[2])
        if structure.blocks.get(warp_floor, "minecraft:air") == "minecraft:air":
            raise SystemExit("JUNKET Hope warp point has no physical floor")
        lose = plan_entry["hope_lose"]
        lose_local = (
            math.floor(lose["x"] - plan_entry["origin"][0]),
            math.floor(lose["y"] - plan_entry["origin"][1]),
            math.floor(lose["z"] - plan_entry["origin"][2]),
        )
        if not structure.contains(*lose_local):
            raise SystemExit("JUNKET Hope lose point is outside the structure")
        if structure.blocks.get(lose_local, "minecraft:air") != "minecraft:air":
            raise SystemExit("JUNKET Hope lose point is blocked in generated layout")
        if saved_by_position.get(lose_local, ("minecraft:air", ()))[0] != "minecraft:air":
            raise SystemExit("JUNKET Hope lose point is blocked in saved structure")
        lose_floor = (lose_local[0], lose_local[1] - 1, lose_local[2])
        if structure.blocks.get(lose_floor, "minecraft:air") == "minecraft:air":
            raise SystemExit("JUNKET Hope lose point has no physical floor")
        if lose_local == spectator_local or lose_local == warp_local:
            raise SystemExit("JUNKET Hope observer and elimination routes must be distinct")
    elif game_id == "rabbit":
        rabbit = plan_entry["rabbit"]
        cells = rabbit["cells"]
        if len(cells) != 60:
            raise SystemExit("JUNKET Rabbit must contain exactly 60 legal tiles")
        occupied = set()
        for cell in cells:
            floor = {(value[0], value[1], value[2]) for value in cell["floor_cells"]}
            water = {(value[0], value[1], value[2]) for value in cell["water_cells"]}
            if len(floor) != 9 or len(water) != 9:
                raise SystemExit(f"JUNKET Rabbit tile {cell['tile']} must have nine cells")
            if occupied.intersection(floor):
                raise SystemExit(f"JUNKET Rabbit tile {cell['tile']} overlaps another tile")
            occupied.update(floor)
            xs = [value[0] for value in floor]
            zs = [value[2] for value in floor]
            if max(xs) - min(xs) != 2 or max(zs) - min(zs) != 2:
                raise SystemExit(f"JUNKET Rabbit tile {cell['tile']} must be exactly 3x3")
            for value in floor:
                local = (
                    math.floor(value[0] - plan_entry["origin"][0]),
                    math.floor(value[1] - plan_entry["origin"][1]),
                    math.floor(value[2] - plan_entry["origin"][2]),
                )
                if structure.blocks.get(local, "minecraft:air") != cell["floor"]:
                    raise SystemExit(
                        f"JUNKET Rabbit tile {cell['tile']} floor mismatch at {local}")
                if saved_by_position.get(local, ("minecraft:air", ()))[0] != cell["floor"]:
                    raise SystemExit(
                        f"JUNKET Rabbit saved tile {cell['tile']} mismatch at {local}")
                for height in (local[1] + 1, local[1] + 2):
                    standing = (local[0], height, local[2])
                    if structure.blocks.get(standing, "minecraft:air") != "minecraft:air":
                        raise SystemExit(
                            f"JUNKET Rabbit tile {cell['tile']} standing space is blocked "
                            f"in generated layout at {standing}")
                    if saved_by_position.get(
                            standing, ("minecraft:air", ()))[0] != "minecraft:air":
                        raise SystemExit(
                            f"JUNKET Rabbit tile {cell['tile']} standing space is blocked "
                            f"in saved structure at {standing}")
            for value in water:
                local = (
                    math.floor(value[0] - plan_entry["origin"][0]),
                    math.floor(value[1] - plan_entry["origin"][1]),
                    math.floor(value[2] - plan_entry["origin"][2]),
                )
                if structure.blocks.get(local) != "minecraft:water":
                    raise SystemExit(
                        f"JUNKET Rabbit tile {cell['tile']} lacks water at {local}")
        if len(occupied) != 36 * 15:
            raise SystemExit("JUNKET Rabbit legal tiles must fill a 36x15 board")
        if len({value[0] for value in occupied}) != 36 or len({value[2] for value in occupied}) != 15:
            raise SystemExit("JUNKET Rabbit legal tiles must form one contiguous board")
        oni = rabbit.get("oni") or {}
        oni_cells = {(value[0], value[1], value[2])
                     for value in oni.get("floor_cells", [])}
        if oni.get("tile") != 61 or len(oni_cells) != 9 or oni_cells.intersection(occupied):
            raise SystemExit("JUNKET Rabbit tile 61 must be a separate 3x3 floor")
        for value in oni_cells:
            local = (
                math.floor(value[0] - plan_entry["origin"][0]),
                math.floor(value[1] - plan_entry["origin"][1]),
                math.floor(value[2] - plan_entry["origin"][2]),
            )
            if structure.blocks.get(local, "minecraft:air") != oni["floor"]:
                raise SystemExit(f"JUNKET Rabbit tile 61 floor mismatch at {local}")
            if saved_by_position.get(local, ("minecraft:air", ()))[0] != oni["floor"]:
                raise SystemExit(f"JUNKET Rabbit saved tile 61 mismatch at {local}")
            for height in (local[1] + 1, local[1] + 2):
                standing = (local[0], height, local[2])
                if structure.blocks.get(standing, "minecraft:air") != "minecraft:air":
                    raise SystemExit(
                        f"JUNKET Rabbit tile 61 standing space is blocked "
                        f"in generated layout at {standing}")
                if saved_by_position.get(
                        standing, ("minecraft:air", ()))[0] != "minecraft:air":
                    raise SystemExit(
                        f"JUNKET Rabbit tile 61 standing space is blocked "
                        f"in saved structure at {standing}")
        drop_tiles = [area.get("tile") for area in plan_entry["drop_areas"]]
        if len(drop_tiles) != 60 or 61 in drop_tiles:
            raise SystemExit("JUNKET Rabbit tile 61 must never be a trap/drop target")
        guard_fixtures = [fixture for fixture in plan_entry["fixtures"]
                          if str(fixture.get("action", "")).startswith("guard_")]
        if sorted(fixture["action"] for fixture in guard_fixtures) != [
                "guard_3", "guard_4", "guard_5", "guard_6"]:
            raise SystemExit("JUNKET Rabbit must expose guard_3 through guard_6")
        shelf = rabbit.get("guard_shelf") or {}
        shelf_min_x = math.floor(shelf.get("min_x", 0) - plan_entry["origin"][0])
        shelf_max_x = math.floor(shelf.get("max_x", -1) - plan_entry["origin"][0])
        shelf_min_z = math.floor(shelf.get("min_z", 0) - plan_entry["origin"][2])
        shelf_max_z = math.floor(shelf.get("max_z", -1) - plan_entry["origin"][2])
        for fixture in guard_fixtures:
            local_x = math.floor(fixture["location"][0] - plan_entry["origin"][0])
            local_y = math.floor(fixture["location"][1] - plan_entry["origin"][1])
            local_z = math.floor(fixture["location"][2] - plan_entry["origin"][2])
            if (rabbit["min_x"] <= local_x <= rabbit["max_x"] and
                    rabbit["min_z"] <= local_z <= rabbit["max_z"]):
                raise SystemExit(
                    f"JUNKET Rabbit {fixture['action']} must stay outside the 60 legal floors")
            if not (shelf_min_x <= local_x <= shelf_max_x and
                    shelf_min_z <= local_z <= shelf_max_z):
                raise SystemExit(
                    f"JUNKET Rabbit {fixture['action']} is not on the escape-side shelf")
            support = (local_x, local_y - 1, local_z)
            if structure.blocks.get(support) != "minecraft:polished_andesite":
                raise SystemExit(
                    f"JUNKET Rabbit {fixture['action']} lacks its escape-side shelf floor")
            if saved_by_position.get(support, ("minecraft:air", ()))[0] != \
                    "minecraft:polished_andesite":
                raise SystemExit(
                    f"JUNKET Rabbit saved {fixture['action']} shelf floor mismatch")
            for height in (local_y, local_y + 1):
                standing = (local_x, height, local_z)
                if structure.blocks.get(standing, "minecraft:air") != "minecraft:air":
                    raise SystemExit(
                        f"JUNKET Rabbit {fixture['action']} standing space is blocked")
                if saved_by_position.get(
                        standing, ("minecraft:air", ()))[0] != "minecraft:air":
                    raise SystemExit(
                        f"JUNKET Rabbit saved {fixture['action']} standing space is blocked")


def check():
    games = load_games()
    plan, structures = venue_plan(games)
    if not GENERATED.exists() or GENERATED.read_text(encoding="utf-8") != runtime_source(plan):
        raise SystemExit("generated JUNKET venue runtime is stale")
    for entry in plan:
        _verify_saved_structure(entry["id"], structures[entry["id"]], entry)
    for name in SPECIAL_ENTITIES:
        for entity in SPECIAL_ENTITIES[name]:
            if not (ENTITIES / f"{entity}.json").exists():
                raise SystemExit(f"missing JUNKET entity: {entity}")
    print(f"JUNKET original world assets are current: {len(games)} venues")


def build():
    games = load_games()
    plan, structures = venue_plan(games)
    STRUCTURES.mkdir(parents=True, exist_ok=True)
    for game_id, structure in structures.items():
        structure.save(STRUCTURES / f"{game_id}_hall.mcstructure")
    # Do not wait for a separate --check invocation to discover a mismatch
    # between the runtime clearance contract and the file that was just saved.
    # This keeps build output and the deployable structure atomic in intent.
    for entry in plan:
        _verify_saved_structure(entry["id"], structures[entry["id"]], entry)
    for index in range(13):
        beam = Structure((13, 13, 1))
        beam.fill((0, 0, 0), (12, 12, 0), "minecraft:light_blue_stained_glass")
        beam.fill((5, 0, 0), (7, 12, 0), "minecraft:glowstone")
        beam.save(STRUCTURES / f"temperance_beam_{index}.mcstructure")
    write_geometry()
    for index, game in enumerate(games):
        write_texture(index, game["id"])
    write_fire_textures()
    write_hope_textures()
    write_rabbit_textures()
    write_entities()
    write_runtime(plan)
    print(f"JUNKET original world assets: {len(games)} venues")


if __name__ == "__main__":
    check() if "--check" in sys.argv else build()
