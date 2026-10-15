"""Generated equipment, persistence and source-preservation contracts (offline)."""
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot.services.minecraft_cosmetics import SLOTS, asset, json_bytes
from bot.services.minecraft_cosmetics_pack import BP, RP, builtin_assets, compile_files, catalog_digest
from bot.services.minecraft_resource_packs import ACCESSORIES, owner, entry_owner, split_resource_packs
from scripts.check_minecraft_cosmetics import png, geometry


class PriorityOneChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT / "minecraft"
        cls.records = builtin_assets(cls.root) + [
            asset("accessory", i + 1, f"test_{slot}", slot, png(), slot=slot,
                  geometry=json_bytes(geometry()), icon=png((16, 16))) for i, slot in enumerate(SLOTS)]
        cls.files = compile_files(cls.root, cls.records, revision=77)

    def decoded(self, path):
        return json.loads(self.files.get(path, (self.root / path).read_bytes() if (self.root / path).exists() else b"{}"))

    def test_generated_equipment_retains_every_db_slot_and_catalog(self):
        self.assertEqual(SLOTS, ("hat", "face", "neck", "back"))
        lock = self.decoded("cosmetics/catalog.lock.json")
        self.assertEqual(lock["digest"], catalog_digest(self.records))
        self.assertEqual([a["id"] for a in lock["accessories"]], [1, 2, 3, 4])
        for name in ("molcar", "molcar2", "molcar3"):
            entity = self.decoded(BP + f"entities/{name}.behavior.json")["minecraft:entity"]
            props = entity["description"]["properties"]
            self.assertEqual(props["ichiyon:float_ring"], {"type": "int", "range": [0, 1], "default": 0, "client_sync": True})
            for slot in SLOTS:
                self.assertEqual(props[f"ichiyon:{slot}"]["range"], [0, 127])
            components = entity["components"]
            self.assertEqual(components["minecraft:movement"], {"value": .4, "max": .65})
            for key in ("minecraft:rideable", "minecraft:leashable", "minecraft:input_ground_controlled", "minecraft:can_power_jump"):
                self.assertIn(key, components)
            self.assertNotIn("minecraft:behavior.float", components)
            buoyant = entity["component_groups"]["ichiyon:float_ring"]["minecraft:buoyant"]
            self.assertEqual(buoyant["movement_type"], "none")
            self.assertEqual(buoyant["base_buoyancy"], .55)
            self.assertEqual(entity["component_groups"]["ichiyon:float_ring"]["minecraft:underwater_movement"], {"value": .4})
            self.assertFalse(buoyant["apply_gravity"])
            self.assertEqual(entity["component_groups"]["ichiyon:normal_water"]["minecraft:underwater_movement"], {"value": .02})
            self.assertEqual(buoyant["drag_down_on_buoyancy_removed"], 0)
            self.assertEqual(buoyant["liquid_blocks"], ["minecraft:water", "minecraft:flowing_water"])
            self.assertEqual(entity["component_groups"]["ichiyon:zombie_molcar"]["minecraft:addrider"],
                             {"riders": [{"entity_type": "minecraft:zombie"}]})
        self.assertFalse(any("garbage_molcar" in name for name in self.files))

    def test_native_sensor_repairs_saved_property_and_restores_normal_water(self):
        entity = self.decoded(BP + "entities/molcar.behavior.json")["minecraft:entity"]
        groups = entity["component_groups"]
        def matches(f, props, components):
            if "all_of" in f: return all(matches(x, props, components) for x in f["all_of"])
            if "any_of" in f: return any(matches(x, props, components) for x in f["any_of"])
            value = props[f["domain"]] == f["value"] if f["test"] == "int_property" else f["value"] in components
            return not value if f.get("operator") == "not" else value
        def reconcile(props, active):
            components = set(entity["components"]).union(*(groups[g] for g in active))
            events = []
            for trigger in entity["components"]["minecraft:environment_sensor"]["triggers"]:
                if matches(trigger["filters"], props, components):
                    event = entity["events"][trigger["event"]]
                    active.difference_update(event["remove"]["component_groups"])
                    active.update(event["add"]["component_groups"])
                    events.append(trigger["event"])
            return events
        for saved in (0, 1):
            # Old entity, missing groups, and stale/opposite groups after load.
            for active in (set(), {"ichiyon:normal_water"}, {"ichiyon:float_ring"}):
                props = json.loads(json.dumps({"ichiyon:float_ring": saved}))
                reconcile(props, active)
                self.assertEqual(active, {"ichiyon:float_ring" if saved else "ichiyon:normal_water"})
                self.assertEqual(reconcile(props, active), [])  # no repeated AI reset
                props["ichiyon:float_ring"] = 1 - saved
                reconcile(props, active)
                self.assertEqual(active, {"ichiyon:normal_water" if saved else "ichiyon:float_ring"})

    def test_render_assets_and_split_archive_ownership(self):
        for name in ("molcar", "molcar2", "molcar3"):
            client = self.decoded(RP + f"entity/{name}.entity.json")["minecraft:client_entity"]["description"]
            self.assertEqual(len(client["render_controllers"]), 6)  # base, four DB items, ring
            geo = self.decoded(RP + f"models/entity/cosmetics/{name}_float_ring.geo.json")["minecraft:geometry"][0]
            self.assertEqual(geo["description"]["identifier"], client["geometry"]["float_ring"])
            original = self.decoded(RP + f"models/entity/{name}.geo.json")["minecraft:geometry"][0]
            skeleton = [{k: v for k, v in b.items() if k not in ("cubes", "locators", "poly_mesh")} for b in original["bones"]]
            self.assertEqual(geo["bones"][:-1], skeleton)
            ring = geo["bones"][-1]
            self.assertEqual(ring["parent"], "body")
            self.assertEqual(len(ring["cubes"]), 12)
            self.assertFalse(any("locators" in b for b in geo["bones"]))
            self.assertEqual(client["animations"]["float_ring"], "animation.molcar.float_ring")
        for path, data in self.files.items():
            if "molcar_float_ring" in path and path.endswith(".png"):
                with Image.open(BytesIO(data)) as image:
                    self.assertEqual(image.size, (32, 32))
                    self.assertIsNotNone(image.getbbox())
            if path.startswith(RP) and "molcar_float_ring" in path:
                self.assertEqual(owner(path[len(RP):]), ACCESSORIES)
        self.assertEqual(entry_owner("item.ichiyon:molcar_float_ring.name"), ACCESSORIES)
        split = split_resource_packs(self.root, self.files)
        self.assertIn(ACCESSORIES + "textures/entity/molcar_float_ring.png", split)
        self.assertEqual(self.decoded(BP + "recipes/molcar_float_ring.json")["minecraft:recipe_shaped"]["result"]["item"], "ichiyon:molcar_float_ring")

    def test_mokuro_anchor_on_chest_without_model_translation(self):
        geo = self.decoded(RP + "models/entity/mokuro.geo.json")["minecraft:geometry"][0]
        body = next(b for b in geo["bones"] if b["name"] == "body")
        self.assertEqual(body["locators"], {"lead": [0, 6, -5]})
        self.assertEqual(body["parent"], "bone")
        self.assertNotIn("rotation", body)
        self.assertTrue(any(c["origin"] == [-3, 5, -5] and c["size"] == [6, 7, 1] for c in body["cubes"]))
        bp = self.decoded(BP + "entities/mokuro.json")["minecraft:entity"]
        self.assertEqual(bp["components"]["minecraft:collision_box"], {"width": .75, "height": .875})
        self.assertEqual(bp["components"]["minecraft:damage_sensor"]["triggers"], [{"cause": "fall", "deals_damage": "no"}])
        self.assertNotIn("minecraft:scale", bp["components"])


if __name__ == "__main__":
    unittest.main()
