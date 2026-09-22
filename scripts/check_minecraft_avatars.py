"""Offline asset/contract checks only; never imports bot settings or opens a world."""

import ast
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
from typing import Optional
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
BP = ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp"
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"
SKINS = ("kiana", "mei", "bronya", "albert")
SOURCE_HASHES = {
    "kiana": "e8da98024eb073ed6e9afe6383f706a2b5797da29ffb8f4d0b17bb4565439b24",
    "mei": "8f0f102e63a0ab401162f986a0ec2a4d2514b354f041e8ecbcb841434025fcef",
    "bronya": "f89392da708fb345d7bcc775a4521f87e1493f1458b39dc1aecfe14fbbef4e48",
    "albert": "73099fa79f8e8e10393a937056faa4c8b33002b667201e72fd14638049b97c47",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class AvatarChecks(unittest.TestCase):
    def test_all_pack_json_parses(self):
        packs = ROOT / "minecraft"
        for directory in (packs / "behavior_packs", packs / "resource_packs"):
            for path in directory.rglob("*.json"):
                with self.subTest(path=path.relative_to(ROOT)):
                    read_json(path)

    def setUp(self):
        self.document = read_json(BP / "entities/avatar.json")
        self.entity = self.document["minecraft:entity"]
        self.client = read_json(RP / "entity/avatar.entity.json")["minecraft:client_entity"]["description"]

    def test_single_summonable_entity_and_default(self):
        description = self.entity["description"]
        self.assertEqual(description["identifier"], "ichiyon:avatar")
        self.assertTrue(description["is_summonable"])
        self.assertFalse(description["is_spawnable"])
        self.assertFalse(description["is_experimental"])
        self.assertNotIn("runtime_identifier", description)
        self.assertEqual(self.entity["components"]["minecraft:variant"]["value"], 0)
        identifiers = [read_json(p)["minecraft:entity"]["description"]["identifier"]
                       for p in (BP / "entities").glob("*.json")]
        self.assertEqual(identifiers.count("ichiyon:avatar"), 1)
        for skin in SKINS:
            self.assertNotIn(f"ichiyon:{skin}", identifiers)


    def test_home_bound_mobile_and_damageable(self):
        components = self.entity["components"]

        self.assertEqual(self.document["format_version"], "1.26.10")

        self.assertEqual(
            components["minecraft:movement"],
            {"value": 0.12},
        )

        self.assertEqual(
            components["minecraft:physics"],
            {
                "has_gravity": False,
                "has_collision": True,
                "push_towards_closest_space": False,
            },
        )

        self.assertEqual(
            components["minecraft:home"],
            {
                "restriction_radius": 2,
                "restriction_type": "all_movement",
            },
        )

        self.assertEqual(
            components["minecraft:navigation.walk"],
            {
                "avoid_water": True,
                "can_path_over_water": False,
                "can_pass_doors": False,
                "can_open_doors": False,
            },
        )

        self.assertEqual(
            components["minecraft:movement.basic"],
            {},
        )

        self.assertEqual(
            components["minecraft:behavior.look_at_player"],
            {
                "priority": 1,
                "look_distance": 8,
                "probability": 0.12,
                "look_time": {
                    "min": 2,
                    "max": 5,
                },
                "angle_of_view_horizontal": 120,
                "angle_of_view_vertical": 80,
            },
        )

        self.assertEqual(
            components["minecraft:behavior.random_look_around"],
            {
                "priority": 2,
                "probability": 0.08,
                "look_time": {
                    "min": 2,
                    "max": 4,
                },
                "min_angle_of_view_horizontal": -70,
                "max_angle_of_view_horizontal": 70,
            },
        )

        self.assertEqual(
            components["minecraft:behavior.random_stroll"],
            {
                "priority": 3,
                "speed_multiplier": 0.65,
                "interval": 80,
                "xz_dist": 2,
                "y_dist": 1,
            },
        )

        self.assertEqual(
            components["minecraft:health"],
            {
                "value": 20,
                "max": 20,
            },
        )

        # Damage must reach HP now.
        self.assertNotIn("minecraft:damage_sensor", components)

        # Attacks may damage the avatar, but should not launch it away.
        self.assertEqual(
            components["minecraft:knockback_resistance"],
            {
                "value": 1,
                "max": 1,
            },
        )

        self.assertIn("minecraft:persistent", components)
        self.assertIn("minecraft:fire_immune", components)

        self.assertEqual(
            components["minecraft:collision_box"],
            {
                "width": 0.6,
                "height": 1.8,
            },
        )

        self.assertEqual(
            components["minecraft:is_collidable"],
            {},
        )

        # Player/block pushing remains disabled.
        for key in components:
            self.assertFalse(
                key.startswith("minecraft:pushable"),
                key,
            )

        # It is allowed to look and stroll, but must not gain combat,
        # riding, despawn or unrelated autonomous behavior.
        forbidden = (
            "minecraft:behavior.melee",
            "minecraft:behavior.ranged",
            "minecraft:behavior.hurt_by_target",
            "minecraft:behavior.nearest_attackable_target",
            "minecraft:attack",
            "minecraft:despawn",
            "minecraft:leashable",
            "minecraft:rideable",
            "minecraft:timer",
        )

        for group in [
            components,
            *self.entity["component_groups"].values(),
        ]:
            self.assertFalse(
                any(key.startswith(forbidden) for key in group),
                group,
            )

        self.assertNotIn(
            "minecraft:entity_spawned",
            self.entity["events"],
        )

        self.assertFalse(
            (BP / "spawn_rules/avatar.json").exists()
        )

    def test_creative_proxies_transform_to_shared_entity(self):
        for index, skin in enumerate(SKINS):
            proxy = read_json(BP / f"entities/avatar_{skin}_placer.json")
            entity = proxy["minecraft:entity"]
            self.assertEqual(proxy["format_version"], self.document["format_version"])
            self.assertEqual(entity["description"], {
                **self.entity["description"], "identifier": f"ichiyon:avatar_{skin}_placer",
                "is_spawnable": False,
            })
            self.assertEqual(entity["components"], {
                **self.entity["components"], "minecraft:variant": {"value": index},
                "minecraft:transformation": {
                    "into": f"ichiyon:avatar<ichiyon:{skin}>", "delay": 0,
                },
            })
            self.assertNotIn("events", entity)
            self.assertNotIn("component_groups", entity)
            client = read_json(RP / f"entity/avatar_{skin}_placer.entity.json")
            self.assertEqual(client["minecraft:client_entity"]["description"], {
                **self.client, "identifier": f"ichiyon:avatar_{skin}_placer",
            })
            for locale in ("ja_JP", "en_US"):
                lines = (RP / f"texts/{locale}.lang").read_text(encoding="utf-8").splitlines()
                for prefix in ("entity.", "item.spawn_egg.entity."):
                    key = f"{prefix}ichiyon:avatar_{skin}_placer.name="
                    self.assertEqual(sum(line.startswith(key) for line in lines), 1)
                    value = next(line[len(key):] for line in lines if line.startswith(key))
                    self.assertNotIn("?", value)
                    self.assertTrue(value.strip())


    def test_egg_identifiers_and_resolved_skin(self):
        # Follow egg -> BP transformation -> target event -> variant -> RP texture.
        # This is a static contract, not a simulation of Bedrock transformation.
        entities = {}
        clients = {}
        for directory in (ROOT / "minecraft/behavior_packs").iterdir():
            for path in (directory / "entities").glob("*.json"):
                entity = read_json(path)["minecraft:entity"]
                identifier = entity["description"]["identifier"]
                self.assertNotIn(identifier, entities, str(path))
                entities[identifier] = entity
        for directory in (ROOT / "minecraft/resource_packs").iterdir():
            for path in (directory / "entity").glob("*.json"):
                client = read_json(path)["minecraft:client_entity"]["description"]
                identifier = client["identifier"]
                self.assertNotIn(identifier, clients, str(path))
                clients[identifier] = client
        eggs = {identifier for identifier, entity in entities.items()
                if identifier.startswith("ichiyon:avatar")
                and entity["description"].get("is_spawnable", False)}
        self.assertEqual(eggs, set())  # Common selector replaces per-character creative eggs.
        for directory in (ROOT / "minecraft/behavior_packs").iterdir():
            for path in (directory / "items").glob("*.json"):
                item = read_json(path)["minecraft:item"]
                self.assertNotIn(item["description"]["identifier"],
                                 {f"{identifier}_spawn_egg" for identifier in eggs})
                placer = item.get("components", {}).get("minecraft:entity_placer", {})
                if placer.get("entity", "").startswith("ichiyon:avatar"):
                    self.assertEqual(item["description"]["identifier"], "ichiyon:avatar_selector")
        controller = read_json(RP / "render_controllers/avatar.render_controllers.json")
        textures = controller["render_controllers"]["controller.render.ichiyon.avatar"]["arrays"]["textures"]["Array.skins"]
        for index, skin in enumerate(SKINS):
            identifier = f"ichiyon:avatar_{skin}_placer"
            self.assertIn("spawn_egg", clients[identifier])
            transform = entities[identifier]["components"]["minecraft:transformation"]
            self.assertTrue(transform["into"].endswith(">"))
            target_id, event_id = transform["into"][:-1].split("<")
            self.assertEqual(target_id, "ichiyon:avatar")
            target = entities[target_id]
            event = target["events"][event_id]
            components = dict(target["components"])
            for group in event["add"]["component_groups"]:
                components.update(target["component_groups"][group])
            variant = components["minecraft:variant"]["value"]
            self.assertEqual(variant, index)
            self.assertTrue(textures[variant].startswith("Texture."))
            texture_key = textures[variant][len("Texture."):]
            self.assertEqual(clients[target_id]["textures"][texture_key],
                             f"textures/entity/cosmetics/skin_{index + 1}")
        for locale in ("ja_JP", "en_US"):
            lines = (RP / f"texts/{locale}.lang").read_text(encoding="utf-8").splitlines()
            names = [next(line.split("=", 1)[1] for line in lines
                          if line.startswith(f"item.spawn_egg.entity.{identifier}.name="))
                     for identifier in (f"ichiyon:avatar_{skin}_placer" for skin in SKINS)]
            self.assertEqual(len(set(names)), 4)

    def test_idle_walk_and_look_animation_contract(self):
        self.assertEqual(
            self.client["animations"],
            {
                "idle": "animation.ichiyon.avatar.idle",
                "walk": "animation.ichiyon.avatar.walk",
                "look_at_target": "animation.common.look_at_target",
            },
        )

        self.assertEqual(
            self.client["scripts"],
            {
                "animate": [
                    "idle",
                    {
                        "walk": "query.modified_move_speed",
                    },
                    "look_at_target",
                ],
            },
        )

        animations = read_json(
            RP / "animations/avatar.animation.json"
        )["animations"]

        self.assertEqual(
            set(animations),
            {
                "animation.ichiyon.avatar.idle",
                "animation.ichiyon.avatar.walk",
            },
        )

        idle = animations[
            "animation.ichiyon.avatar.idle"
        ]

        self.assertIs(idle["loop"], True)
        self.assertEqual(idle["animation_length"], 8)

        # Head is intentionally NOT animated by idle:
        # look_at_target owns head movement.
        self.assertEqual(
            set(idle["bones"]),
            {
                "body",
                "rightArm",
                "leftArm",
            },
        )

        self.assertNotIn("head", idle["bones"])

        for bone in idle["bones"].values():
            self.assertEqual(
                set(bone),
                {"rotation"},
            )

        walk = animations[
            "animation.ichiyon.avatar.walk"
        ]

        self.assertIs(walk["loop"], True)

        self.assertEqual(
            walk["anim_time_update"],
            "query.modified_distance_moved * 2.5",
        )

        self.assertEqual(
            set(walk["bones"]),
            {
                "body",
                "rightArm",
                "leftArm",
                "rightLeg",
                "leftLeg",
            },
        )

        # Walking must actually animate both legs.
        right_leg = walk["bones"]["rightLeg"]["rotation"][0]
        left_leg = walk["bones"]["leftLeg"]["rotation"][0]

        self.assertIn("* 28", right_leg)
        self.assertIn("* -28", left_leg)

        geometry = read_json(
            RP / "models/entity/avatar.geo.json"
        )["minecraft:geometry"][0]

        bone_names = {
            bone["name"]
            for bone in geometry["bones"]
        }

        for required in (
            "body",
            "head",
            "rightArm",
            "leftArm",
            "rightLeg",
            "leftLeg",
        ):
            self.assertIn(required, bone_names)

    def test_robot_contract_and_parser_without_settings_import(self):
        # Execute only inert assignments and the pure parser, never bot.config/dotenv.
        service = (ROOT / "bot/services/minecraft_bridge.py").read_text(encoding="utf-8")
        tree = ast.parse(service)
        nodes = [node for node in tree.body if isinstance(node, ast.Assign) or
                 isinstance(node, ast.FunctionDef) and node.name in ("parse_minecraft_command", "_result_error_message")]
        namespace = {"re": re, "Optional": Optional,
                     "is_valid_minecraft_player_name": lambda value: bool(re.fullmatch(r"[A-Za-z0-9_]{1,16}", value))}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "avatar_parser_contract", "exec"), namespace)
        parser = namespace["parse_minecraft_command"]
        expected = {f"avatar_{skin}_{action}_near_player" for skin in SKINS for action in ("spawn", "remove")}
        expected.add("avatar_all_remove_near_player")
        self.assertEqual(set(namespace["_AVATAR_COMMANDS"].values()), expected)
        repo = ast.parse((ROOT / "bot/repositories/minecraft_bridge.py").read_text(encoding="utf-8"))
        allowed = next(ast.literal_eval(node.value) for node in repo.body if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "MINECRAFT_COMMAND_TYPES" for t in node.targets))
        sql = (ROOT / "migrations/064_add_minecraft_avatar_commands.sql").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r"'([a-z0-9_]+)'", sql)), set(allowed))
        bridge = (ROOT / "minecraft/behavior_packs/import_structures/scripts/avatar_commands.js").read_text(encoding="utf-8")
        entries = dict(re.findall(r"^  (avatar_\w+): (\{[^\n]+\}),$", bridge, re.M))
        self.assertEqual(set(entries), expected)
        for label, command in namespace["_AVATAR_COMMANDS"].items():
            self.assertEqual(parser(f"マイクラ {label} Player_123"), (command, "Player_123", True))
            self.assertIn(command, namespace["_SUCCESS_MESSAGES"])
            for bad in ("", "@e", "a;kill", "a/b", "a b", "x" * 17):
                self.assertIsNone(parser(f"マイクラ {label} {bad}")[1])
        self.assertIsNone(parser("マイクラ avatar_unknown Player_123")[0])
        for label, command in namespace["_COMMAND_TYPES_BY_TEXT"].items():
            self.assertEqual(parser(f"マイクラ {label} Player_123"), (command, "Player_123", True))
        for reason in ("avatar_spawn_failed", "avatar_remove_failed", "avatar_cleanup_failed"):
            self.assertIn("マネキン", namespace["_result_error_message"]("Player_123", reason))
        for index, skin in enumerate(SKINS):
            self.assertEqual(entries[f"avatar_{skin}_spawn_near_player"],
                             f'{{ action: "spawn", variant: {index}, event: "ichiyon:{skin}" }}')
            self.assertEqual(entries[f"avatar_{skin}_remove_near_player"],
                             f'{{ action: "remove", variant: {index} }}')
        self.assertEqual(entries["avatar_all_remove_near_player"], '{ action: "remove", variant: null }')
        for contract in ('Object.prototype.hasOwnProperty.call(AVATAR_COMMANDS, command.type)',
                         'helpers.isValidPlayerName(playerName)', 'helpers.findOnlinePlayer(playerName)',
                         'spawnEntity("ichiyon:avatar", helpers.playerForwardSpawnLocation(player))',
                         'spawned.triggerEvent(spec.event)', 'spawned.setRotation({ x: 0, y: player.getRotation().y })',
                         'type: "ichiyon:avatar", location: player.location, maxDistance: 16',
                         'entity.getComponent("minecraft:variant")', 'variant.value === spec.variant',
                         'matching.slice(0, 1)', 'for (const entity of selected) entity.remove()',
                         '"avatar_cleanup_failed"', '"avatar_remove_failed"'):
            self.assertIn(contract, bridge)
        self.assertNotRegex(bridge, r"runCommand|eval\(|new Function|HttpRequest|teleport|runInterval")
        main = (ROOT / "minecraft/behavior_packs/import_structures/scripts/main.js").read_text(encoding="utf-8")
        self.assertIn('import { handleAvatarCommand } from "./avatar_commands.js";', main)
        self.assertIn('await handleAvatarCommand(command, {', main)

    def test_all_variant_transitions_and_repeated_selection(self):
        groups = self.entity["component_groups"]
        variant_groups = {f"ichiyon:avatar_{s}" for s in SKINS} | {f"ichiyon:cosmetic_{i}" for i in range(1, 5)}
        # Exercise all pairs and re-selection; each event must clear every old group.
        for first, second in itertools.product(SKINS, repeat=2):
            active = set()
            for skin in (first, second, second):
                event = self.entity["events"][f"ichiyon:{skin}"]
                self.assertEqual(set(event["remove"]["component_groups"]), variant_groups)
                active.difference_update(event["remove"]["component_groups"])
                active.update(event["add"]["component_groups"])
                self.assertEqual(active, {f"ichiyon:avatar_{skin}"})
                self.assertEqual(groups[next(iter(active))], {
                    "minecraft:variant": {"value": SKINS.index(skin)},
                })
        remove = self.entity["events"]["ichiyon:remove"]["add"]["component_groups"]
        self.assertEqual(groups[remove[0]], {"minecraft:instant_despawn": {}})

    def test_render_references_and_original_pngs(self):
        controllers = read_json(RP / "render_controllers/avatar.render_controllers.json")["render_controllers"]
        self.assertEqual(self.client["identifier"], "ichiyon:avatar")
        controller = controllers[self.client["render_controllers"][0]]
        self.assertEqual(controller["arrays"]["textures"]["Array.skins"][:4],
                         [f"Texture.skin_{i}" for i in range(1, 5)])
        self.assertEqual(len(controller["arrays"]["textures"]["Array.skins"]), 127)
        self.assertEqual(set(controller["arrays"]["textures"]["Array.skins"][4:]), {"Texture.deleted_skin"})
        self.assertEqual(self.client["textures"]["deleted_skin"], "textures/entity/cosmetics/deleted_skin")
        self.assertEqual(controller["textures"], ["Array.skins[query.variant]"])
        self.assertEqual(controller["materials"], [{"*": "Material.default"}])
        self.assertEqual(self.client["materials"]["default"], "entity_alphatest")
        self.assertEqual(controller["geometry"], "Array.models[query.variant]")
        for skin in SKINS:
            source = ROOT / "minecraft/avatar_skins/source" / f"{skin}.png"
            texture = RP / (self.client["textures"][skin] + ".png")
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), SOURCE_HASHES[skin])
            self.assertEqual(texture.read_bytes(), source.read_bytes())
            with Image.open(texture) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (64, 64))
                image.load()

    def test_classic_skin_uvs_and_outer_layer_parenting(self):
        geometry = read_json(RP / "models/entity/avatar.geo.json")["minecraft:geometry"][0]
        self.assertEqual(geometry["description"]["identifier"], self.client["geometry"]["default"])
        self.assertEqual(geometry["description"]["texture_width"], 64)
        self.assertEqual(geometry["description"]["texture_height"], 64)
        bones = {bone["name"]: bone for bone in geometry["bones"]}
        # The lower half of a 64x64 skin holds independent LEFT limb UVs.
        parts = [
            ("head", "hat", [8, 8, 8], [0, 0], [32, 0], 0.5),
            ("body", "jacket", [8, 12, 4], [16, 16], [16, 32], 0.25),
            ("rightArm", "rightSleeve", [4, 12, 4], [40, 16], [40, 32], 0.25),
            ("leftArm", "leftSleeve", [4, 12, 4], [32, 48], [48, 48], 0.25),
            ("rightLeg", "rightTrouser", [4, 12, 4], [0, 16], [0, 32], 0.25),
            ("leftLeg", "leftTrouser", [4, 12, 4], [16, 48], [0, 48], 0.25),
        ]
        self.assertEqual(len(bones), 13)
        for base, overlay, size, uv, outer_uv, inflate in parts:
            inner = bones[base]["cubes"][0]
            outer = bones[overlay]["cubes"][0]
            self.assertEqual(bones[overlay]["parent"], base)
            self.assertEqual(bones[overlay]["pivot"], bones[base]["pivot"])
            self.assertEqual(inner["size"], size)
            self.assertEqual(outer["size"], size)
            self.assertEqual(inner["origin"], outer["origin"])
            self.assertEqual(inner["uv"], uv)
            self.assertEqual(outer["uv"], outer_uv)
            self.assertEqual(outer["inflate"], inflate)
            for cube in (inner, outer):
                self.assertFalse(cube.get("mirror", False))
                u, v = cube["uv"]
                x, y, z = cube["size"]
                self.assertLessEqual(u + 2 * (x + z), 64)
                self.assertLessEqual(v + y + z, 64)

    def test_pack_versions_and_localized_names(self):
        for pack, version in ((BP, [1, 0, 34]), (RP, [1, 0, 38])):
            manifest = read_json(pack / "manifest.json")
            self.assertEqual(manifest["header"]["version"], version)
            self.assertTrue(all(m["version"] == version for m in manifest["modules"]))
            self.assertEqual(manifest["header"]["min_engine_version"], [1, 26, 45])
        for locale in ("ja_JP", "en_US"):
            text = (RP / "texts" / f"{locale}.lang").read_text(encoding="utf-8")
            self.assertEqual(text.count("entity.ichiyon:avatar.name="), 2)


if __name__ == "__main__":
    unittest.main()
