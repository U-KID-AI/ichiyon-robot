"""Source-preservation and entity contracts, plus executable Script regression."""
import base64
import hashlib
import json
import subprocess
import unittest

from export_mokuro import ANIMATION_IDS, ROOT, RP, SOURCE, SOURCE_SHA, export, position, rotation

BP = ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class MokuroChecks(unittest.TestCase):
    def test_source_and_exact_extraction(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA)
        src = read(SOURCE)
        png = (RP / "textures/entity/mokuro.png").read_bytes()
        self.assertEqual(png, base64.b64decode(src["textures"][0]["source"].split(",")[1]))
        self.assertEqual([int.from_bytes(png[i:i+4], "big") for i in (16, 20)], [128, 128])
        for path, expected in export().items():
            actual = path.read_bytes()
            if path.suffix == ".json":
                actual = actual.replace(b"\r\n", b"\n")
            self.assertEqual(actual, expected, str(path))

    def test_all_cubes_bones_and_uv_basis_preserved(self):
        src = read(SOURCE)
        geo = read(RP / "models/entity/mokuro.geo.json")["minecraft:geometry"][0]
        self.assertEqual(geo["description"]["texture_width"], 64)
        self.assertEqual(geo["description"]["texture_height"], 64)
        self.assertEqual(len(geo["bones"]), 7)
        self.assertEqual(sum(len(b.get("cubes", [])) for b in geo["bones"]), 45)
        for group in src["groups"]:
            bone = next(b for b in geo["bones"] if b["name"] == group["name"])
            self.assertEqual(position(bone["pivot"]), group["origin"])
        self.assertEqual({b["name"] for b in geo["bones"] if "parent" not in b}, {"bone", "regL", "regR"})

    def test_every_original_keyframe_timing_angles_loop(self):
        src = read(SOURCE)
        exported = read(RP / "animations/mokuro.animation.json")["animations"]
        self.assertEqual(set(exported), {"animation.mokuro.walk", "animation.mokuro.flap", "animation.mokuro.open_wings", "animation.mokuro.roll"})
        for anim in src["animations"]:
            dst = exported[ANIMATION_IDS[anim["name"]]]
            self.assertEqual(dst["loop"], True if anim["loop"] == "loop" else "hold_on_last_frame")
            self.assertEqual(dst["animation_length"], anim["length"])
            for animator in anim["animators"].values():
                for key in animator.get("keyframes", []):
                    value = dst["bones"][animator["name"]][key["channel"]][str(float(key["time"]))]
                    restored = rotation(value) if key["channel"] == "rotation" else position(value)
                    self.assertEqual(restored, [float(key["data_points"][0][a]) for a in "xyz"])

    def test_friendly_mobile_and_carried_components(self):
        entity = read(BP / "entities/mokuro.json")["minecraft:entity"]
        self.assertTrue(entity["description"]["is_spawnable"])
        self.assertTrue(entity["description"]["is_summonable"])
        self.assertNotIn("runtime_identifier", entity["description"])
        self.assertEqual(entity["components"]["minecraft:health"], {"value": 20, "max": 20})
        self.assertIn("minecraft:persistent", entity["components"])
        mobile = entity["component_groups"]["ichiyon:mokuro_mobile"]
        carried = entity["component_groups"]["ichiyon:mokuro_carried"]
        self.assertIn("minecraft:behavior.random_stroll", mobile)
        self.assertEqual(mobile["minecraft:behavior.random_stroll"]["interval"], 40)
        self.assertEqual(mobile["minecraft:movement"]["value"], 0.22)
        for event in ("minecraft:entity_spawned", "ichiyon:mokuro_detach"):
            self.assertIn("ichiyon:mokuro_mobile", entity["events"][event]["add"]["component_groups"])
        self.assertIn("ichiyon:mokuro_mobile", entity["events"]["ichiyon:mokuro_attach"]["remove"]["component_groups"])
        self.assertIn("minecraft:leashable", mobile)
        self.assertEqual(mobile["minecraft:leashable"]["presets"], [{"soft_distance": 4, "hard_distance": 6, "max_distance": 12}])
        interaction = entity["components"]["minecraft:interact"]["interactions"][0]
        self.assertEqual(interaction["on_interact"]["filters"]["all_of"], [
            {"test": "is_family", "subject": "other", "value": "player"}
        ])
        self.assertFalse(any(k.startswith("minecraft:behavior.") or k.startswith("minecraft:navigation.") for k in carried))
        self.assertEqual(carried["minecraft:physics"], {"has_gravity": False, "has_collision": False})
        for components in [entity["components"], *entity["component_groups"].values()]:
            self.assertFalse(any("attack" in k or "damage_sensor" in k for k in components))
            self.assertNotIn("minecraft:pushable", components)
        self.assertFalse((BP / "spawn_rules/mokuro.json").exists())

    def test_client_wiring_and_localization(self):
        client = read(RP / "entity/mokuro.entity.json")["minecraft:client_entity"]["description"]
        self.assertEqual(client["identifier"], "ichiyon:mokuro")
        self.assertEqual(client["geometry"]["default"], "geometry.mokuro")
        self.assertIn("spawn_egg", client)
        for name in ("walk", "flap", "open_wings", "roll"):
            self.assertEqual(client["animations"][name], f"animation.mokuro.{name}")
        states = read(RP / "animation_controllers/mokuro.controller.json")["animation_controllers"]["controller.animation.mokuro.state"]["states"]
        self.assertEqual(states["glide"]["animations"], ["open_wings"])
        self.assertEqual(states["walk"]["animations"], ["walk"])
        self.assertEqual(states["flap"]["animations"], ["flap"])
        self.assertNotIn("animations", states["carried"])
        self.assertEqual(states["roll"]["animations"], ["roll"])
        self.assertTrue(any("ichiyon:boosting" in expression for transition in states["carried"]["transitions"] for expression in transition.values()))
        for lang in ("ja_JP", "en_US"):
            text = (RP / f"texts/{lang}.lang").read_text(encoding="utf-8")
            for key in ("entity.ichiyon:mokuro.name", "item.spawn_egg.entity.ichiyon:mokuro.name", "action.interact.ichiyon_mokuro"):
                self.assertEqual(sum(line.startswith(key + "=") for line in text.splitlines()), 1)

    def test_runtime_regressions(self):
        subprocess.run(["node", str(ROOT / "scripts/check_minecraft_mokuro.mjs")], check=True)


if __name__ == "__main__":
    unittest.main()
