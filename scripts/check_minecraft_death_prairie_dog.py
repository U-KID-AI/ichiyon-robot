"""Independent source, export, BP/RP, integration and executable runtime checks."""
import base64
from copy import deepcopy
import hashlib
import json
import subprocess
import unittest

from export_death_prairie_dog import ANIMATION_IDS, ROOT, RP, SOURCE, SOURCE_SHA, export, green_texture
from build_death_prairie_dog_behavior import behavior
from check_death_prairie_pose import DeathPrairiePoseChecks


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class DeathPrairieDogChecks(unittest.TestCase):
    def test_immutable_source_and_reproducible_export(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA)
        for path, expected in export().items():
            actual = path.read_bytes()
            if path.suffix == ".json":
                actual = actual.replace(b"\r\n", b"\n")
            self.assertEqual(actual, expected, str(path))
        self.assertIn("*.bbmodel -text", (SOURCE.parent / ".gitattributes").read_text())

    def test_green_identified_from_pixels_not_id_or_index(self):
        src = read(SOURCE)
        texture, png = green_texture(src)
        self.assertEqual(texture["uuid"], "b6f63003-076c-39f0-aebb-f621d82f6216")
        self.assertEqual(texture["id"], "3")
        self.assertEqual(hashlib.sha256(png).hexdigest(), "2e93d3a2968a9897261b4e5b7c0a36fb07028539f2754b0a8798a1d210ad3315")
        self.assertEqual((RP / "textures/entity/death_prairie_dog.png").read_bytes(), png)
        src["textures"].reverse()
        for i, t in enumerate(src["textures"]):
            t["id"] = str(100+i)
        self.assertEqual(green_texture(src)[1], png)
        for t in src["textures"]:
            if t["uuid"] != texture["uuid"]:
                self.assertNotEqual(base64.b64decode(t["source"].split(",")[1]), png)
        src["textures"].append(deepcopy(green_texture(src)[0]))
        with self.assertRaises(AssertionError):
            green_texture(src)

    def test_every_cube_bone_parent_pivot_rotation_and_uv(self):
        src = read(SOURCE)
        geo = read(RP / "models/entity/death_prairie_dog.geo.json")["minecraft:geometry"][0]
        self.assertEqual([geo["description"][k] for k in ("texture_width", "texture_height")], [64, 64])
        self.assertEqual(len(geo["bones"]), 8)
        self.assertEqual(sum(len(b.get("cubes", [])) for b in geo["bones"]), 52)
        elements = {e["uuid"]: e for e in src["elements"]}
        groups = {g["uuid"]: g for g in src["groups"]}
        exported = {b["name"]: b for b in geo["bones"]}
        self.assertEqual(set(exported), {g["name"] for g in groups.values()})
        def visit(node, parent=None):
            group = groups[node["uuid"]]
            bone = exported[group["name"]]
            self.assertEqual(bone.get("parent"), parent)
            x, y, z = bone["pivot"]
            self.assertEqual([-x, y, z], group["origin"])
            x, y, z = bone.get("rotation", [0, 0, 0])
            self.assertEqual([-x, -y, z], group["rotation"])
            original = [elements[c] for c in node["children"] if isinstance(c, str)]
            self.assertEqual(len(original), len(bone.get("cubes", [])))
            for cube, out in zip(original, bone.get("cubes", [])):
                self.assertEqual(out["origin"], [-cube["to"][0], *cube["from"][1:]])
                self.assertEqual(out["size"], [b-a for a, b in zip(cube["from"], cube["to"])])
                self.assertEqual(out.get("inflate", 0), cube.get("inflate", 0))
                if any(cube.get("rotation", [0, 0, 0])):
                    x, y, z = out["rotation"]
                    self.assertEqual([-x, -y, z], cube["rotation"])
                    x, y, z = out["pivot"]
                    self.assertEqual([-x, y, z], cube["origin"])
                if cube["box_uv"]:
                    self.assertEqual(out["uv"], cube.get("uv_offset", [0, 0]))
                    self.assertEqual(out.get("mirror", False), cube.get("mirror_uv", False))
                else:
                    for face, data in cube["faces"].items():
                        if data.get("texture") is None:
                            self.assertNotIn(face, out["uv"])
                            continue
                        u, v = out["uv"][face]["uv"]
                        du, dv = out["uv"][face]["uv_size"]
                        restored = [u+du, v+dv, u, v] if face in ("up", "down") else [u, v, u+du, v+dv]
                        self.assertEqual(restored, data["uv"])
                        self.assertEqual(out["uv"][face].get("uv_rotation", 0), data.get("rotation", 0))
            for child in node["children"]:
                if isinstance(child, dict):
                    visit(child, group["name"])
        for node in src["outliner"]:
            visit(node)

    def test_every_animation_keyframe_and_global_rotation(self):
        src = read(SOURCE)
        exported = read(RP / "animations/death_prairie_dog.animation.json")["animations"]
        self.assertEqual(set(exported), set(ANIMATION_IDS.values()))
        total = 0
        for anim in src["animations"]:
            dst = exported[ANIMATION_IDS[anim["name"]]]
            self.assertEqual(dst["loop"], True if anim["loop"] == "loop" else "hold_on_last_frame")
            self.assertEqual(dst["animation_length"], anim["length"])
            expected_keys = 0
            for animator in anim["animators"].values():
                keys = animator.get("keyframes", [])
                if animator.get("rotation_global"):
                    self.assertNotIn("relative_to", dst["bones"][animator["name"]])
                for key in keys:
                    channel = key["channel"]
                    value = dst["bones"][animator["name"]][channel][str(float(key["time"]))]
                    x, y, z = value
                    restored = [-x, -y, z] if channel == "rotation" else [-x, y, z] if channel == "position" else value
                    original = [float(key["data_points"][0][a]) for a in "xyz"]
                    self.assertEqual(restored, original)
                    expected_keys += 1
            actual_keys = sum(len(b[c]) for b in dst["bones"].values() for c in ("position", "rotation", "scale") if isinstance(b.get(c), dict))
            self.assertEqual(actual_keys, expected_keys)
            total += actual_keys
        self.assertEqual(total, 27)

    def test_spawn_egg_single_green_texture_and_integration_localization(self):
        definition = read(ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp/entities/death_prairie_dog.json")
        self.assertEqual(definition, behavior())
        bp = definition["minecraft:entity"]
        client = read(RP / "entity/death_prairie_dog.entity.json")["minecraft:client_entity"]["description"]
        self.assertTrue(bp["description"]["is_spawnable"])
        self.assertTrue(bp["description"]["is_summonable"])
        self.assertNotIn("runtime_identifier", bp["description"])
        self.assertEqual(client["identifier"], bp["description"]["identifier"])
        self.assertEqual(client["textures"], {"default": "textures/entity/death_prairie_dog"})
        self.assertEqual(client["geometry"], {"default": "geometry.death_prairie_dog"})
        self.assertIn("spawn_egg", client)
        self.assertEqual(set(client["animations"].values()) - {"controller.animation.death_prairie_dog.state"}, set(ANIMATION_IDS.values()))
        integration = read(ROOT / "docs/death_prairie_dog_integration.json")
        for lang in ("ja_JP", "en_US"):
            self.assertEqual(len(integration["localization"][lang]), 2)
            self.assertIn("entity.ichiyon:death_prairie_dog.name", integration["localization"][lang])
            self.assertIn("item.spawn_egg.entity.ichiyon:death_prairie_dog.name", integration["localization"][lang])
        self.assertEqual(integration["localization"]["ja_JP"]["entity.ichiyon:death_prairie_dog.name"], "\u30c7\u30b9\u30d7\u30ec\u30fc\u30ea\u30fc\u30c9\u30c3\u30b0")
        self.assertEqual(integration["main_import"], 'import "./death_prairie_dog.js";')

    def test_runtime_and_controller_regressions(self):
        subprocess.run(["node", str(ROOT / "scripts/check_minecraft_death_prairie_dog.mjs")], check=True)


if __name__ == "__main__":
    unittest.main()
