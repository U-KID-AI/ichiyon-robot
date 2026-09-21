"""Offline Pikachu asset and state contracts; no engine/world/network access."""
import copy
import json
from pathlib import Path
import struct
import unittest
import zlib

from build_pikachu_assets import geometry, texture

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
            self.assertEqual(set(walk[foot]), {"position"})
            self.assertEqual(walk[foot]["position"][:2], [0, 0])
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

    def test_server_events_reentry_and_timer_recovery(self):
        # Interpret the small event vocabulary used by the actual JSON. This tests
        # both random branches and repeated requests; it is not a Bedrock emulator.
        for choice in (0, 1):
            groups, props = set(), {"ichiyon:spin": 0}

            def apply(node):
                filt = node.get("filters")
                if filt and props[filt["domain"]] != filt["value"]:
                    return
                for step in node.get("sequence", []):
                    apply(step)
                groups.difference_update(node.get("remove", {}).get("component_groups", []))
                groups.update(node.get("add", {}).get("component_groups", []))
                props.update(node.get("set_property", {}))
                if "randomize" in node:
                    apply(node["randomize"][choice])

            events = self.entity["events"]
            apply(events["minecraft:entity_spawned"])
            self.assertEqual(groups, {"ichiyon:mobile"})
            for _ in range(5):
                apply(events["ichiyon:spin_start"])
                self.assertEqual(groups, {"ichiyon:spinning"})
                snapshot = copy.deepcopy((groups, props))
                for _ in range(50):
                    apply(events["ichiyon:spin_start"])
                self.assertEqual((groups, props), snapshot)
                spinning = self.entity["component_groups"]["ichiyon:spinning"]
                self.assertFalse(any(k.startswith("minecraft:behavior.") for k in spinning))
                self.assertEqual(spinning["minecraft:movement"]["value"], 0)
                timer = spinning["minecraft:timer"]
                self.assertGreater(timer["time"], 0.45)
                self.assertFalse(timer["looping"])
                apply(events[timer["time_down_event"]["event"]])
                self.assertEqual(groups, {"ichiyon:mobile"})
                self.assertEqual(props["ichiyon:spin"], 0)
        weights = self.entity["events"]["ichiyon:spin_start"]["sequence"][0]["randomize"]
        self.assertEqual([w["weight"] for w in weights], [99, 1])
        interaction = self.entity["components"]["minecraft:interact"]["interactions"][0]
        self.assertGreater(interaction["cooldown"], timer["time"])

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
        targeting = mobile["minecraft:behavior.nearest_attackable_target"]
        self.assertTrue(targeting["reevaluate_description"])
        self.assertTrue(targeting["must_see"])
        self.assertTrue(targeting["must_reach"])
        self.assertEqual(targeting["within_radius"], 12)
        target = targeting["entity_types"][0]
        self.assertEqual(target["max_dist"], 12)
        filters = target["filters"]["all_of"]
        self.assertIn({"test": "is_sleeping", "subject": "other", "value": True}, filters)
        self.assertIn({"test": "is_family", "subject": "other", "value": "player"}, filters)
        around = mobile["minecraft:behavior.move_around_target"]
        self.assertGreaterEqual(around["destination_position_range"]["min"], 2.2)
        self.assertEqual(around["destination_pos_spread_degrees"], 360)
        self.assertGreater(around["height_difference_limit"], 0)
        self.assertIn({"test": "is_sleeping", "subject": "target", "value": True}, around["filters"]["all_of"])
        for components in [self.entity["components"], *self.entity["component_groups"].values()]:
            self.assertNotIn("minecraft:attack", components)
            self.assertFalse(any("melee_attack" in k or "ranged_attack" in k for k in components))


if __name__ == "__main__":
    unittest.main()
