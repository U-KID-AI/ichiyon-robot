"""Offline lead locator checks; no settings, network, or world access."""

import json
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(path.read_text(encoding="utf-8"),
                      object_pairs_hook=pairs, parse_constant=reject_constant)


class LeadAnchorChecks(unittest.TestCase):
    def test_client_resources_and_complete_hierarchy(self):
        eggs = {
            "molcar": ("#642287", "#243184"),
            "molcar2": ("#E0E0D0", "#B0B0B0"),
            "molcar3": ("#967219", "#781814"),
            "gonta": ("#F1D900", "#111111"),
        }
        for name, (base, overlay) in eggs.items():
            with self.subTest(entity=name):
                data = read_json(RP / f"entity/{name}.entity.json")
                self.assertEqual(data["format_version"], "1.10.0")
                client = data["minecraft:client_entity"]["description"]
                self.assertEqual(client["identifier"], f"ichiyon:{name}")
                self.assertEqual(client["spawn_egg"], {
                    "base_color": base, "overlay_color": overlay})
                self.assertEqual(client["materials"], {"default": "entity_alphatest"})
                self.assertEqual(client["render_controllers"], ["controller.render.default"])
                for texture in client["textures"].values():
                    path = RP / (texture + ".png")
                    self.assertTrue(path.is_file())
                    self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                geometry = read_json(RP / f"models/entity/{name}.geo.json")["minecraft:geometry"][0]
                self.assertEqual(client["geometry"], {"default": geometry["description"]["identifier"]})
                bones = {b["name"]: b for b in geometry["bones"]}
                self.assertEqual(len(bones), len(geometry["bones"]))
                for start in bones:
                    seen = set()
                    current = start
                    while current is not None:
                        self.assertIn(current, bones)
                        self.assertNotIn(current, seen)
                        seen.add(current)
                        current = bones[current].get("parent")
                animations = read_json(RP / f"animations/{name}.animation.json")["animations"]
                for ref in client["animations"].values():
                    self.assertIn(ref, animations)
                    self.assertLessEqual(set(animations[ref]["bones"]), set(bones))

    def test_parent_transforms_and_animation_envelope(self):
        for name, expected in (("molcar", (0, 7.5, -8.5)), ("molcar2", (0, 7.5, -8.5)),
                               ("molcar3", (0, 7.5, -8.5)), ("gonta", (0, 4, -4))):
            client = read_json(RP / f"entity/{name}.entity.json")["minecraft:client_entity"]["description"]
            bones = {b["name"]: b for b in read_json(RP / f"models/entity/{name}.geo.json")["minecraft:geometry"][0]["bones"]}
            point = bones["body"]["locators"]["lead"]
            chain = []
            bone = "body"
            while bone:
                self.assertNotIn(bone, chain)
                chain.append(bone)
                bone = bones[bone].get("parent")
            self.assertEqual(chain, ["body", "root" if name != "gonta" else "gonta_body"])

            def transform(position, rotation):
                result = list(point)
                for bone in chain:
                    pivot = bones[bone]["pivot"]
                    angles = bones[bone].get("rotation", [0, 0, 0])
                    angles = [a + b for a, b in zip(angles, rotation if bone == "body" else [0, 0, 0])]
                    x, y, z = [v - p for v, p in zip(result, pivot)]
                    a, b, c = map(math.radians, angles)
                    y, z = y * math.cos(a) - z * math.sin(a), y * math.sin(a) + z * math.cos(a)
                    x, z = x * math.cos(b) + z * math.sin(b), -x * math.sin(b) + z * math.cos(b)
                    x, y = x * math.cos(c) - y * math.sin(c), x * math.sin(c) + y * math.cos(c)
                    result = [v + p for v, p in zip((x, y, z), pivot)]
                    if bone == "body":
                        result = [v + p for v, p in zip(result, position)]
                return result

            for actual, wanted in zip(transform([0, 0, 0], [0, 0, 0]), expected):
                self.assertAlmostEqual(actual, wanted)
            animations = read_json(RP / f"animations/{name}.animation.json")["animations"]
            for animation in animations.values():
                for bone in chain:
                    channels = animation["bones"].get(bone, {})
                    if name == "gonta" or bone != "body":
                        self.assertEqual(channels, {})
                        continue
                    self.assertLessEqual(set(channels), {"position", "rotation"})
                    positions = channels.get("position", {"0": [0, 0, 0]}).values()
                    rotations = channels.get("rotation", {"0": [0, 0, 0]}).values()
                    for pos in positions:
                        self.assertEqual((pos[0], pos[2]), (0, 0))
                        self.assertTrue(0 <= pos[1] <= .24)
                        for rot in rotations:
                            self.assertEqual(rot[:2], [0, 0])
                            self.assertLessEqual(abs(rot[2]), 1.8)
                            x, y, z = transform(pos, rot)
                            self.assertAlmostEqual(x, 0)
                            self.assertTrue(7.23 < y < 8.01)
                            self.assertTrue(-8.55 < z < -8.44)

    def test_world_reference_uses_current_manifest(self):
        from ai_task_minecraft_deploy import sync_world_json
        manifest = read_json(RP / "manifest.json")
        version = manifest["header"]["version"]
        self.assertEqual(version, [1, 0, 45])
        self.assertTrue(all(m["version"] == version for m in manifest["modules"]))
        old = [{"pack_id": manifest["header"]["uuid"], "version": [1, 0, 31]}]
        result = json.loads(sync_world_json(json.dumps(old), [json.dumps(manifest)]))
        self.assertEqual(result, [{**old[0], "version": version}])

    def test_lead_locators_attach_to_body_surface(self):
        # Model-space points must be on actual body cubes, not collision bounds
        # or animated wheels/legs. Inherit body/root transforms through the bone.
        for name in ("molcar", "molcar2", "molcar3", "gonta"):
            with self.subTest(entity=name):
                client = read_json(RP / f"entity/{name}.entity.json")["minecraft:client_entity"]["description"]
                geometry = read_json(RP / f"models/entity/{name}.geo.json")["minecraft:geometry"][0]
                self.assertEqual(client["geometry"]["default"], geometry["description"]["identifier"])
                self.assertNotIn("locators", client)
                owners = [b for b in geometry["bones"] if "lead" in b.get("locators", {})]
                self.assertEqual([b["name"] for b in owners], ["body"])
                point = owners[0]["locators"]["lead"]
                self.assertEqual(len(point), 3)
                self.assertTrue(all(type(v) in (int, float) and math.isfinite(v) for v in point))
                body = next(b for b in geometry["bones"] if b["name"] == "body")
                def on_surface(cube):
                    low = cube["origin"]
                    high = [o + s for o, s in zip(low, cube["size"])]
                    return (all(lo <= p <= hi for p, lo, hi in zip(point, low, high))
                            and any(p in (lo, hi) for p, lo, hi in zip(point, low, high)))
                self.assertTrue(any(on_surface(c) for c in body["cubes"]))

    def test_all_pack_json_parses(self):
        for kind in ("behavior_packs", "resource_packs"):
            for path in (ROOT / "minecraft" / kind).rglob("*.json"):
                with self.subTest(path=path.relative_to(ROOT)):
                    read_json(path)


if __name__ == "__main__":
    unittest.main()
