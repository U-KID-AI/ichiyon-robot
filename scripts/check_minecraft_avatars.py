"""Offline asset/contract checks only; never imports bot settings or opens a world."""

import hashlib
import itertools
import json
from pathlib import Path
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
    def setUp(self):
        self.document = read_json(BP / "entities/avatar.json")
        self.entity = self.document["minecraft:entity"]
        self.client = read_json(RP / "entity/avatar.entity.json")["minecraft:client_entity"]["description"]

    def test_single_summonable_entity_and_default(self):
        description = self.entity["description"]
        self.assertEqual(description["identifier"], "ichiyon:avatar")
        self.assertTrue(description["is_summonable"])
        self.assertTrue(description["is_spawnable"])
        self.assertFalse(description["is_experimental"])
        self.assertNotIn("runtime_identifier", description)
        self.assertEqual(self.entity["components"]["minecraft:variant"]["value"], 0)
        identifiers = [read_json(p)["minecraft:entity"]["description"]["identifier"]
                       for p in (BP / "entities").glob("*.json")]
        self.assertEqual(identifiers.count("ichiyon:avatar"), 1)
        for skin in SKINS:
            self.assertNotIn(f"ichiyon:{skin}", identifiers)

    def test_stationary_and_invulnerable_in_every_group(self):
        components = self.entity["components"]
        self.assertEqual(self.document["format_version"], "1.26.10")
        self.assertEqual(components["minecraft:movement"], {"value": 0})
        self.assertEqual(components["minecraft:physics"], {
            "has_gravity": False, "has_collision": False,
            "push_towards_closest_space": False,
        })
        self.assertEqual(components["minecraft:knockback_resistance"]["value"], 1)
        self.assertEqual(components["minecraft:damage_sensor"], {
            "triggers": [{"cause": "all", "deals_damage": "no"}],
        })
        self.assertIn("minecraft:persistent", components)
        self.assertIn("minecraft:fire_immune", components)
        # With format >= 1.26.10, omitting BOTH pushable_by_* disables pushing.
        forbidden = ("minecraft:behavior.", "minecraft:navigation.", "minecraft:movement.",
                     "minecraft:pushable", "minecraft:despawn", "minecraft:leashable",
                     "minecraft:rideable", "minecraft:attack", "minecraft:timer")
        for group in [components, *self.entity["component_groups"].values()]:
            self.assertFalse(any(key.startswith(forbidden) for key in group))
        self.assertNotIn("animations", self.client)
        self.assertNotIn("scripts", self.client)
        self.assertNotIn("minecraft:entity_spawned", self.entity["events"])
        self.assertFalse((BP / "spawn_rules/avatar.json").exists())

    def test_all_variant_transitions_and_repeated_selection(self):
        groups = self.entity["component_groups"]
        variant_groups = {f"ichiyon:avatar_{s}" for s in SKINS}
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
        self.assertEqual(controller["arrays"]["textures"]["Array.skins"],
                         [f"Texture.{skin}" for skin in SKINS])
        self.assertEqual(controller["textures"], ["Array.skins[query.variant]"])
        self.assertEqual(controller["materials"], [{"*": "Material.default"}])
        self.assertEqual(self.client["materials"]["default"], "entity_alphatest")
        self.assertEqual(controller["geometry"], "Geometry.default")
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
        for pack, version in ((BP, [1, 0, 25]), (RP, [1, 0, 27])):
            manifest = read_json(pack / "manifest.json")
            self.assertEqual(manifest["header"]["version"], version)
            self.assertTrue(all(m["version"] == version for m in manifest["modules"]))
            self.assertEqual(manifest["header"]["min_engine_version"], [1, 26, 45])
        for locale in ("ja_JP", "en_US"):
            text = (RP / "texts" / f"{locale}.lang").read_text(encoding="utf-8")
            self.assertEqual(text.count("entity.ichiyon:avatar.name="), 2)


if __name__ == "__main__":
    unittest.main()
