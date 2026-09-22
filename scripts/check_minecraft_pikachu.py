"""Offline Pikachu asset and state contracts; no engine/world/network access."""
import copy
import re
from collections import Counter
import json
from pathlib import Path
import struct
import unittest
import zlib

from build_pikachu_assets import (geometry, texture, reference_blocks, REFERENCE,
                                  SCALE, BLOCK_COLORS)

ROOT = Path(__file__).resolve().parent.parent
BP = ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp"
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"


def read(path):
    return json.loads(path.read_text())


class PikachuChecks(unittest.TestCase):
    def setUp(self):
        self.entity = read(BP / "entities/pikachu.json")["minecraft:entity"]
        self.client = read(RP / "entity/pikachu.entity.json")["minecraft:client_entity"]["description"]
        self.animations = read(RP / "animations/pikachu.animation.json")["animations"]
        self.controller = read(RP / "animation_controllers/pikachu.controller.json")["animation_controllers"]

    def test_json_and_entity_references(self):
        for pack in (BP, RP):
            for path in pack.rglob("*.json"):
                read(path)
        identifiers = [read(p)["minecraft:entity"]["description"]["identifier"]
                       for p in (BP / "entities").glob("*.json")]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(self.client["identifier"], self.entity["description"]["identifier"])
        self.assertTrue(self.entity["description"]["is_spawnable"])
        self.assertTrue(self.entity["description"]["is_summonable"])
        for value in self.client["animations"].values():
            self.assertIn(value, {**self.animations, **self.controller})
        for state in self.controller["controller.animation.pikachu.spin"]["states"].values():
            for alias in state.get("animations", []):
                self.assertIn(alias, self.client["animations"])
        self.assertEqual(self.client["geometry"]["default"], geometry()["minecraft:geometry"][0]["description"]["identifier"])
        self.assertTrue((RP / (self.client["textures"]["default"] + ".png")).is_file())
        self.assertEqual(set(self.client["spawn_egg"]), {"base_color", "overlay_color"})
        for lang in ("ja_JP", "en_US"):
            lines = (RP / f"texts/{lang}.lang").read_text().splitlines()
            for key in ("entity.ichiyon:pikachu.name", "item.spawn_egg.entity.ichiyon:pikachu.name",
                        "action.interact.ichiyon_pikachu_spin"):
                self.assertEqual(sum(line.startswith(key + "=") for line in lines), 1)

    def test_geometry_atlas_and_animation_bones(self):
        actual = read(RP / "models/entity/pikachu.geo.json")
        self.assertEqual(actual, geometry())
        geo = actual["minecraft:geometry"][0]
        bones = {b["name"]: b for b in geo["bones"]}
        self.assertEqual(len(bones), len(geo["bones"]))
        for bone in bones.values():
            seen = {bone["name"]}
            node = bone
            while "parent" in node:
                self.assertNotIn(node["parent"], seen)
                seen.add(node["parent"])
                node = bones[node["parent"]]
            for cube in bone.get("cubes", []):
                self.assertTrue(all(size > 0 for size in cube["size"]))
                for face in cube["uv"].values():
                    for start, size, limit in zip(face["uv"], face["uv_size"], (64, 16)):
                        self.assertGreaterEqual(start, 0)
                        self.assertLessEqual(start + size, limit)
        for animation in self.animations.values():
            self.assertTrue(set(animation["bones"]) <= bones.keys())
        png = (RP / "textures/entity/pikachu.png").read_bytes()
        self.assertEqual(png, texture())
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        pos, compressed = 8, b""
        while pos < len(png):
            size = struct.unpack_from(">I", png, pos)[0]
            kind, data = png[pos+4:pos+8], png[pos+8:pos+8+size]
            self.assertEqual(zlib.crc32(kind + data), struct.unpack_from(">I", png, pos+8+size)[0])
            if kind == b"IDAT":
                compressed += data
            pos += 12 + size
        self.assertEqual(len(zlib.decompress(compressed)), (64 * 4 + 1) * 16)
        walk = self.animations["animation.pikachu.walk"]["bones"]
        for foot in ("left_foot", "right_foot"):
            self.assertEqual(set(walk[foot]), {"rotation"})
            self.assertEqual(walk[foot]["rotation"][1:], [0, 0])
            self.assertEqual(bones[foot]["pivot"][1], 4 * SCALE)
            self.assertFalse(any(b.get("parent") == foot for b in bones.values()))
            self.assertEqual(bones[foot]["parent"], "body_root")
        self.assertEqual(walk["body_root"]["rotation"][:2], [0, 0])

    def test_spin_is_one_turn_without_translation_or_loop(self):
        for name, sign in (("clockwise", 1), ("counterclockwise", -1)):
            animation = self.animations["animation.pikachu." + name]
            self.assertEqual(animation["loop"], "hold_on_last_frame")
            self.assertEqual(animation["animation_length"], 0.45)
            self.assertEqual(set(animation["bones"]), {"root"})
            self.assertEqual(set(animation["bones"]["root"]), {"rotation"})
            frames = animation["bones"]["root"]["rotation"]
            values = [frames[t] for t in sorted(frames, key=float)]
            self.assertEqual(values[0], [0, 0, 0])
            self.assertEqual(values[-1], [0, sign * 360, 0])
            self.assertEqual([v[1]*sign for v in values], sorted(v[1]*sign for v in values))
            self.assertTrue(all(v[0] == v[2] == 0 for v in values))

    def test_server_lifecycle_reentry_cooldown_and_reload(self):
        # Execute this entity's event/controller vocabulary. Engine scheduling,
        # physics and networking still require Bedrock validation.
        lifecycle = read(BP / "animation_controllers/pikachu.controller.json")["animation_controllers"]
        desc = self.entity["description"]
        self.assertEqual(desc["scripts"]["animate"], ["spin_lifecycle"])
        controller = lifecycle[desc["animations"]["spin_lifecycle"]]
        states = controller["states"]
        self.assertEqual(controller["initial_state"], "cooldown")
        self.assertEqual(set(states), {"idle", "spinning", "cooldown"})
        self.assertEqual(states["spinning"]["transitions"], [{"cooldown": "query.state_time >= 0.7"}])
        self.assertEqual(states["cooldown"]["transitions"], [{"idle": "query.state_time >= 0.5"}])
        for state in states.values():
            for event in state.get("on_entry", []):
                self.assertTrue(event.startswith("@s "))
                self.assertIn(event[3:], self.entity["events"])
            for transition in state.get("transitions", []):
                self.assertTrue(set(transition) <= states.keys())

        for choice in (0, 1):
            groups = {"ichiyon:spinning"}  # old saved, permanently locked entity
            props = {"ichiyon:spin": (-1, 1)[choice], "ichiyon:spin_busy": False}
            components = {}

            def matches(filt):
                if "all_of" in filt:
                    return all(matches(f) for f in filt["all_of"])
                return props[filt["domain"]] == filt["value"]

            def apply(node):
                if "filters" in node and not matches(node["filters"]):
                    return
                for step in node.get("sequence", []):
                    apply(step)
                for name in node.get("remove", {}).get("component_groups", []):
                    if name in groups:
                        for key in self.entity["component_groups"][name]:
                            components.pop(key, None)
                    groups.discard(name)
                for name in node.get("add", {}).get("component_groups", []):
                    groups.add(name)
                    components.update(self.entity["component_groups"][name])
                props.update(node.get("set_property", {}))
                if "randomize" in node:
                    apply(node["randomize"][choice])

            def enter(state):
                for event in states[state].get("on_entry", []):
                    apply(self.entity["events"][event[3:]])

            def condition(expression, elapsed):
                if " || " in expression:
                    return any(condition(part, elapsed) for part in expression.split(" || "))
                if expression.startswith("query.state_time >= "):
                    return elapsed >= float(expression.split(" >= ")[1])
                match = re.fullmatch(r"query.property\('([^']+)'\)( != 0)?", expression)
                self.assertIsNotNone(match)
                return bool(props[match[1]])

            state = controller["initial_state"]
            elapsed = 0
            enter(state)

            def tick():
                nonlocal state, elapsed
                elapsed = round(elapsed + .05, 3)
                for transition in states[state].get("transitions", []):
                    target, expression = next(iter(transition.items()))
                    if condition(expression, elapsed):
                        state, elapsed = target, 0
                        enter(state)
                        break

            for _ in range(10):
                tick()
            for cycle in range(100):
                self.assertEqual(state, "idle")
                self.assertEqual(groups, {"ichiyon:mobile", "ichiyon:ready"})
                self.assertEqual(props, {"ichiyon:spin": 0, "ichiyon:spin_busy": False})
                self.assertIn("minecraft:interact", components)
                self.assertTrue(components["minecraft:physics"]["has_gravity"])
                self.assertEqual(components["minecraft:movement"]["value"], .16)
                apply(self.entity["events"]["ichiyon:spin_start"])
                tick()
                self.assertEqual(state, "spinning")
                self.assertEqual(groups, {"ichiyon:spinning"})
                self.assertEqual(props["ichiyon:spin"], (1, -1)[choice])
                self.assertNotIn("minecraft:interact", components)
                for _ in range(14):
                    snapshot = copy.deepcopy((groups, props, components))
                    for _ in range(50):
                        apply(self.entity["events"]["ichiyon:spin_start"])
                    self.assertEqual((groups, props, components), snapshot)
                    tick()
                self.assertEqual(state, "cooldown")
                self.assertEqual(props["ichiyon:spin"], 0)
                self.assertTrue(props["ichiyon:spin_busy"])
                self.assertIn("minecraft:behavior.random_stroll", components)
                for _ in range(10):
                    apply(self.entity["events"]["ichiyon:spin_start"])
                    self.assertEqual(props["ichiyon:spin"], 0)
                    self.assertNotIn("minecraft:interact", components)
                    tick()
                # Loading during either a spin or cooldown resets via initial_state.
                if cycle in (10, 20):
                    apply(self.entity["events"]["ichiyon:spin_start"])
                    if cycle == 20:
                        apply(self.entity["events"]["ichiyon:spin_end"])
                    state, elapsed = controller["initial_state"], 0
                    enter(state)
                    for _ in range(10):
                        tick()
        weights = self.entity["events"]["ichiyon:spin_start"]["sequence"][0]["randomize"]
        self.assertEqual([w["weight"] for w in weights], [99, 1])
        interaction = self.entity["component_groups"]["ichiyon:ready"]["minecraft:interact"]["interactions"][0]
        self.assertEqual(interaction["cooldown"], 1.2)
        self.assertIn({"test": "bool_property", "domain": "ichiyon:spin_busy", "value": False},
                      interaction["on_interact"]["filters"]["all_of"])

    def test_reference_voxels_preserved(self):
        blocks = reference_blocks()
        self.assertEqual(Counter(blocks.values()), {
            "yellow_wool": 607, "black_wool": 14, "brown_wool": 12,
            "red_wool": 8, "white_wool": 2, "nether_brick_fence": 2})
        reconstructed = {}
        for bone in geometry()["minecraft:geometry"][0]["bones"]:
            for cube in bone.get("cubes", []):
                color = int((cube["uv"]["north"]["uv"][0] - 1) / 8)
                if color == BLOCK_COLORS["nether_brick_fence"]:
                    continue
                ox, oy, oz = [round(v / SCALE) for v in cube["origin"]]
                width, height, depth = [round(v / SCALE) for v in cube["size"]]
                for z in range(ox + 10, ox + 10 + width):
                    for y in range(oy, oy + height):
                        for x in range(4 - oz - depth, 4 - oz):
                            self.assertNotIn((x, y, z), reconstructed)
                            reconstructed[x, y, z] = color
        self.assertEqual(reconstructed, {pos: BLOCK_COLORS[name] for pos, name in blocks.items()
                                         if name != "nether_brick_fence"})

    def test_client_spin_transitions_and_walk_gating(self):
        controller = self.controller["controller.animation.pikachu.spin"]
        self.assertEqual(controller["initial_state"], "idle")
        states = controller["states"]
        self.assertEqual(set(states), {"idle", "clockwise", "counterclockwise"})
        self.assertFalse(states["idle"].get("animations"))
        self.assertEqual(states["idle"]["transitions"], [
            {"clockwise": "query.property('ichiyon:spin') == 1"},
            {"counterclockwise": "query.property('ichiyon:spin') == -1"},
        ])
        for direction in ("clockwise", "counterclockwise"):
            self.assertEqual(states[direction]["animations"], [direction])
            # Stay on the final frame until the server releases the spin lock;
            # animation completion must not restart a turn while still locked.
            self.assertEqual(states[direction]["transitions"], [
                {"idle": "query.property('ichiyon:spin') == 0"},
            ])
        self.assertEqual(self.client["scripts"]["animate"], [
            "spin", {"walk": "query.is_moving && query.property('ichiyon:spin') == 0"},
        ])
        prop = self.entity["description"]["properties"]["ichiyon:spin"]
        self.assertEqual(prop, {"type": "int", "range": [-1, 1],
                                "default": 0, "client_sync": True})

    def test_sleep_range_wakeup_and_noncombat(self):
        mobile = self.entity["component_groups"]["ichiyon:mobile"]

        # Sleeping-player gathering must be noncombat so Creative/invulnerable
        # players are not rejected by attack-target acquisition.
        self.assertNotIn("minecraft:behavior.nearest_attackable_target", mobile)
        self.assertNotIn("minecraft:behavior.move_around_target", mobile)

        follow = mobile["minecraft:behavior.follow_mob"]
        self.assertEqual(follow["priority"], 1)
        self.assertEqual(follow["search_range"], 12)
        self.assertEqual(follow["speed_multiplier"], 1.8)
        self.assertGreaterEqual(follow["speed_multiplier"],
                                2 * mobile["minecraft:behavior.random_stroll"]["speed_multiplier"])
        self.assertEqual(follow["stop_distance"], 2.5)
        self.assertFalse(follow["use_home_position_restriction"])

        filters = follow["filters"]["all_of"]
        self.assertIn(
            {"test": "is_family", "subject": "other", "value": "player"},
            filters,
        )
        self.assertIn(
            {"test": "is_sleeping", "subject": "other", "value": True},
            filters,
        )

        # No game-mode restriction: Creative sleepers are intentionally eligible.
        serialized = json.dumps(follow).lower()
        self.assertNotIn("game_mode", serialized)
        self.assertNotIn("gamemode", serialized)

        # stop_distance keeps the mob away from the player's exact position.
        self.assertGreater(follow["stop_distance"], 2.0)
        self.assertLess(follow["stop_distance"], follow["search_range"])

        for components in [
            self.entity["components"],
            *self.entity["component_groups"].values(),
        ]:
            self.assertNotIn("minecraft:attack", components)
            self.assertFalse(
                any(
                    "melee_attack" in key
                    or "ranged_attack" in key
                    or "nearest_attackable_target" in key
                    for key in components
                )
            )


if __name__ == "__main__":
    unittest.main()
