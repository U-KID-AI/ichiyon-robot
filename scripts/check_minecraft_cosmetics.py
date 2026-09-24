"""Offline validation/compiler regression checks; never loads app configuration."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot.services.minecraft_cosmetics import asset, png_bytes, geometry_bytes, json_bytes, MAX_UPLOAD
from bot.services.minecraft_cosmetics_pack import BP, RP, BRIDGE, MOLCARS, builtin_assets, compile_files, pack_zip, catalog_digest
from bot.services.minecraft_cosmetics_pack import ACCESSORY_GROUPS, _creative_accessory_catalog
from scripts.build_minecraft_cosmetics import generated_matches
# unittest discovers this class through the existing CI entry point as well.
from scripts.check_build_minecraft_cosmetics import BuilderChecks


def png(size=(64, 64)):
    output = BytesIO()
    Image.new("RGBA", size, (80, 160, 240, 255)).save(output, format="PNG")
    return output.getvalue()


def geometry():
    return {"format_version": "1.12.0", "minecraft:geometry": [{"description": {"identifier": "geometry.test", "texture_width": 64, "texture_height": 64},
            "bones": [{"name": "test_hat", "pivot": [0, 14, 0], "cubes": [{"origin": [-4, 14, -4], "size": [8, 1, 8], "uv": [0, 0]}]}]}]}


def fixture_assets():
    return [asset("skin", 5, "test_slim", "テスト用スリム", png(), model="slim"),
            asset("accessory", 1, "test_hat", "テスト用帽子", png(), geometry=json_bytes(geometry()), icon=png((16, 16)), slot="hat")]


class AssetChecks(unittest.TestCase):
    def test_png_dimensions_and_type(self):
        self.assertEqual(png_bytes(png(), skin=True), png_bytes(png_bytes(png(), skin=True), skin=True))
        for data in (png((64, 32)), png((128, 128)), b"not-png", b"x" * (MAX_UPLOAD + 1), "filename"):
            with self.subTest(data_type=type(data).__name__), self.assertRaises(ValueError):
                png_bytes(data, skin=True)

    def test_model_rejects_cycles_missing_parent_scripts_nan_and_wrong_dimensions(self):
        mutations = [lambda m: m["bones"][0].update(parent="test_hat"), lambda m: m["bones"][0].update(parent="missing"),
                     lambda m: m["bones"][0].update(poly_mesh={}), lambda m: m["bones"][0].update(rotation=["query.health", 0, 0]),
                     lambda m: m["bones"][0].update(rotation=[float("nan"), 0, 0]), lambda m: m["description"].update(texture_width=128)]
        for mutate in mutations:
            value = geometry(); mutate(value["minecraft:geometry"][0])
            with self.assertRaises(ValueError):
                geometry_bytes(json.dumps(value).encode(), png())

    def test_asset_ids_names_and_slots(self):
        for kwargs in ({"asset_id": 0}, {"asset_id": True}, {"asset_id": 128}, {"key": "../escape"}, {"name": "bad\nname"}, {"model": "unknown"}):
            args = dict(kind="skin", asset_id=5, key="test", name="test", texture=png()); args.update(kwargs)
            with self.assertRaises(ValueError): asset(**args)
        with self.assertRaises(ValueError):
            asset("accessory", 1, "hat", "Hat", png(), geometry=json_bytes(geometry()), icon=png(), slot="script")


class PackChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.minecraft = ROOT / "minecraft"
        cls.records = builtin_assets(cls.minecraft) + fixture_assets()
        cls.files = compile_files(cls.minecraft, cls.records)

    def decoded(self, path):
        return json.loads(self.files[path])

    def test_stable_ids_duplicate_rejection_and_digest(self):
        with self.assertRaises(ValueError): compile_files(self.minecraft, self.records + [self.records[-1]])
        self.assertIn(BRIDGE + "scripts/cosmetics_catalog.js", compile_files(self.minecraft, self.records[1:]))
        self.assertEqual(catalog_digest(self.records), catalog_digest(list(reversed(self.records))))
        changed = deepcopy(self.records); changed[-1]["name"] = "another name"
        self.assertNotEqual(catalog_digest(changed), catalog_digest(self.records))

    def test_pack_generation_survives_deleted_initial_skins(self):
        without_initial = fixture_assets()
        files = compile_files(self.minecraft, without_initial)
        catalog = json.loads(files["cosmetics/catalog.lock.json"])
        self.assertEqual([skin["id"] for skin in catalog["skins"]], [5])
        controller = json.loads(files[RP + "render_controllers/avatar.render_controllers.json"])["render_controllers"]["controller.render.ichiyon.avatar"]
        textures = controller["arrays"]["textures"]["Array.skins"]
        self.assertEqual(textures[:4], ["Texture.deleted_skin"] * 4)
        self.assertEqual(textures[4], "Texture.skin_5")
        avatar = json.loads(files[BP + "entities/avatar.json"])["minecraft:entity"]
        self.assertNotIn("ichiyon:cosmetic_1", avatar["component_groups"])
        self.assertIn("ichiyon:cosmetic_5", avatar["component_groups"])
        self.assertIn(RP + "textures/entity/cosmetics/deleted_skin.png", files)

    def test_vanilla_player_behavior_and_animation_retained(self):
        vanilla = json.loads((self.minecraft / "cosmetics/vendor/player.behavior.json").read_bytes())
        player = self.decoded(BP + "entities/player.json")
        player["minecraft:entity"]["description"].pop("properties")
        self.assertEqual(player, vanilla)
        vanilla_client = json.loads((self.minecraft / "cosmetics/vendor/player.entity.json").read_bytes())["minecraft:client_entity"]["description"]
        client = self.decoded(RP + "entity/player.entity.json")["minecraft:client_entity"]["description"]
        for key in ("animations", "scripts", "enable_attachables", "materials"):
            self.assertEqual(client[key], vanilla_client[key])
        for condition in vanilla_client["render_controllers"]:
            key, expression = next(iter(condition.items()))
            self.assertIn({key: f"({expression}) && query.property('ichiyon:skin_id') == 0"}, client["render_controllers"])

    def test_slim_geometry_and_registry_are_connected(self):
        geometries = self.decoded(RP + "models/entity/cosmetics_player.geo.json")["minecraft:geometry"]
        classic, slim = [{b["name"]: b for b in g["bones"]} for g in geometries]
        for name in ("rightArm", "leftArm", "rightSleeve", "leftSleeve"):
            self.assertEqual(classic[name]["cubes"][0]["size"][0], 4)
            self.assertEqual(slim[name]["cubes"][0]["size"][0], 3)
        controller = self.decoded(RP + "render_controllers/avatar.render_controllers.json")["render_controllers"]["controller.render.ichiyon.avatar"]
        self.assertEqual(controller["arrays"]["geometries"]["Array.models"][4], "Geometry.slim")
        self.assertEqual(controller["arrays"]["textures"]["Array.skins"][4], "Texture.skin_5")
        avatar = self.decoded(BP + "entities/avatar.json")["minecraft:entity"]
        self.assertEqual(avatar["component_groups"]["ichiyon:cosmetic_5"]["minecraft:variant"]["value"], 4)
        for name in ("ichiyon:cosmetic_5", "ichiyon:kiana"):
            self.assertIn("ichiyon:cosmetic_5", avatar["events"][name]["remove"]["component_groups"])

    def test_accessory_all_three_molcars_preserves_animation_riding_and_leads(self):
        for molcar in MOLCARS:
            path = BP + f"entities/{molcar}.behavior.json"
            before = json.loads((self.minecraft / path).read_bytes())["minecraft:entity"]
            after = self.decoded(path)["minecraft:entity"]
            self.assertEqual({k: v for k, v in after["components"].items() if k != "minecraft:interact"},
                             {k: v for k, v in before["components"].items() if k != "minecraft:interact"})
            interactions = after["components"]["minecraft:interact"]["interactions"]
            self.assertTrue(any(i["interact_text"] == "action.interact.ichiyon_cosmetic_equip" for i in interactions))
            self.assertTrue(all(i["use_item"] is False for i in interactions))
            self.assertIn("minecraft:rideable", after["components"])
            self.assertIn("minecraft:leashable", after["components"])
            self.assertTrue(after["description"]["properties"]["ichiyon:hat"]["client_sync"])
            client_path = RP + f"entity/{molcar}.entity.json"
            original = json.loads((self.minecraft / client_path).read_bytes())["minecraft:client_entity"]["description"]
            client = self.decoded(client_path)["minecraft:client_entity"]["description"]
            self.assertEqual(client["scripts"], original["scripts"])
            self.assertIn("controller.render.default", client["render_controllers"])
            overlay = self.decoded(RP + f"models/entity/cosmetics/{molcar}_cosmetic_1.geo.json")["minecraft:geometry"][0]
            bones = {b["name"]: b for b in overlay["bones"]}
            self.assertEqual(bones["cosmetic_1_test_hat"]["parent"], "body")
            self.assertFalse(any(b.get("cubes") or b.get("locators") for name, b in bones.items() if not name.startswith("cosmetic_")))
            self.assertIn(RP + "textures/entity/cosmetics/accessory_1.png", self.files)
            self.assertIn(BP + "items/cosmetics/accessory_1.json", self.files)

    def test_creative_inventory_groups_all_slots_with_stable_order(self):
        accessories = [asset("accessory", i, f"accessory_{i}", f"Accessory {i}", png(),
                             geometry=json_bytes(geometry()), icon=png((16, 16)), slot=slot)
                       for slot, i in [("back", 16), ("face", 6), ("hat", 3), ("neck", 11), ("hat", 1)]]
        files = compile_files(self.minecraft, builtin_assets(self.minecraft) + accessories)
        catalog = json.loads(files[BP + "item_catalog/crafting_item_catalog.json"])
        self.assertEqual(catalog["format_version"], "1.21.60")
        categories = catalog["minecraft:crafting_items_catalog"]["categories"]
        self.assertEqual([c["category_name"] for c in categories], ["construction", "equipment"])
        source_catalog = json.loads((self.minecraft / BP / "item_catalog/crafting_item_catalog.json").read_text())
        self.assertEqual(categories[0], source_catalog["minecraft:crafting_items_catalog"]["categories"][0])
        groups = categories[1]["groups"]
        self.assertEqual([g["group_identifier"]["name"] for g in groups], list(ACCESSORY_GROUPS.values()))
        self.assertEqual(groups[0]["items"], ["ichiyon:accessory_1", "ichiyon:accessory_3"])
        all_items = [item for group in groups for item in group["items"]]
        self.assertEqual(len(all_items), len(set(all_items)))
        self.assertEqual(set(all_items), {f"ichiyon:accessory_{a['id']}" for a in accessories})
        for group in groups:
            self.assertEqual(group["group_identifier"]["icon"], group["items"][0])
        for a in accessories:
            desc = json.loads(files[BP + f"items/cosmetics/accessory_{a['id']}.json"])["minecraft:item"]["description"]
            self.assertEqual(desc["menu_category"], {"category": "equipment", "group": ACCESSORY_GROUPS[a["slot"]]})
            for locale in ("ja_JP", "en_US"):
                lines = files[RP + f"texts/{locale}.lang"].decode().splitlines()
                self.assertEqual(sum(line.startswith(ACCESSORY_GROUPS[a["slot"]] + "=") for line in lines), 1)
        self.assertIn("モルカー：頭", files[RP + "texts/ja_JP.lang"].decode())

    def test_creative_catalog_refresh_retains_unrelated_groups_and_does_not_mutate_input(self):
        foreign = {"group_identifier": {"name": "other:tools", "icon": "other:hammer"}, "items": ["other:hammer"]}
        loose = {"items": ["other:block"]}
        old = {"format_version": "1.21.60", "minecraft:crafting_items_catalog": {"categories": [
            {"category_name": "construction", "groups": [loose]},
            {"category_name": "equipment", "groups": [foreign, {
                "group_identifier": {"name": ACCESSORY_GROUPS["face"], "icon": "ichiyon:accessory_99"},
                "items": ["ichiyon:accessory_99"]}]}]}}
        original = deepcopy(old)
        hat = [fixture_assets()[-1]]
        refreshed = _creative_accessory_catalog(old, hat)
        self.assertEqual(old, original)
        self.assertEqual(refreshed["minecraft:crafting_items_catalog"]["categories"][0]["groups"], [loose])
        equipment = refreshed["minecraft:crafting_items_catalog"]["categories"][1]["groups"]
        self.assertEqual(equipment[0], foreign)
        self.assertEqual(len(equipment), 2)
        self.assertEqual(equipment[1]["group_identifier"]["name"], ACCESSORY_GROUPS["hat"])
        self.assertNotIn("accessory_99", json.dumps(refreshed))
        self.assertEqual(_creative_accessory_catalog(refreshed, hat), refreshed)
        empty = _creative_accessory_catalog(refreshed, [])
        self.assertEqual(empty["minecraft:crafting_items_catalog"]["categories"][1]["groups"], [foreign])

    def test_no_accessories_has_no_empty_creative_group(self):
        files = compile_files(self.minecraft, builtin_assets(self.minecraft))
        self.assertEqual(json.loads(files[BP + "item_catalog/crafting_item_catalog.json"]),
                         json.loads((self.minecraft / BP / "item_catalog/crafting_item_catalog.json").read_text()))
        self.assertNotIn("ichiyon:itemGroup.molcar_", files[RP + "texts/ja_JP.lang"].decode())
        empty = _creative_accessory_catalog(None, [])
        self.assertEqual(empty["minecraft:crafting_items_catalog"]["categories"], [])

    def test_complete_archive_determinism_and_versions(self):
        data = pack_zip(self.minecraft, self.records, 9)
        self.assertEqual(data, pack_zip(self.minecraft, self.records, 9))
        with ZipFile(BytesIO(data)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), len(set(names)))
            self.assertFalse(any(".." in n or n.startswith("/") for n in names))
            self.assertIn(BRIDGE + "scripts/main.js", names)
            self.assertIn("resource_packs/ichiyon_core_rp/textures/entity/pikachu.png", names)
            self.assertFalse(any(name.startswith(RP) for name in names))
            self.assertEqual(json.loads(archive.read("cosmetics-build.json"))["catalog_digest"], catalog_digest(self.records))
            for pack in (BP, BRIDGE):
                original = json.loads((self.minecraft / pack / "manifest.json").read_bytes())
                manifest = json.loads(archive.read(pack + "manifest.json"))
                self.assertEqual(manifest["header"]["uuid"], original["header"]["uuid"])
                self.assertEqual(manifest["header"]["version"], [1, 1, 9])
                self.assertTrue(all(m["version"] == [1, 1, 9] for m in manifest["modules"]))

    def test_built_in_generation_is_current(self):
        for name, data in compile_files(self.minecraft, builtin_assets(self.minecraft)).items():
            actual = (self.minecraft / name).read_bytes()
            self.assertTrue(generated_matches(name, actual, data), name)

    def test_bridge_dependency_modules_are_permitted(self):
        manifest = json.loads((self.minecraft / BRIDGE / "manifest.json").read_bytes())
        permissions = json.loads((self.minecraft / "config/2fbc1c02-0c4d-4e98-a851-c1e41337c7a8/permissions.json").read_bytes())
        modules = {d["module_name"] for d in manifest["dependencies"] if "module_name" in d}
        self.assertTrue(modules <= set(permissions["allowed_modules"]))


if __name__ == "__main__":
    unittest.main()
