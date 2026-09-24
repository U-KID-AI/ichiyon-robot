"""Offline Pillow/compiler contracts; does not write generated packs or contact BDS."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from zipfile import ZipFile

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from bot.services.minecraft_cosmetics import asset, poster_texture, MAX_UPLOAD
from bot.services.minecraft_cosmetics_pack import BP, RP, BRIDGE, builtin_assets, compile_files, pack_zip, catalog_digest
from check_minecraft_cosmetics import png, fixture_assets
from build_minecraft_cosmetics import local_catalog


def poster(width=3, height=2, identifier=1):
    return asset("poster", identifier, f"poster_{identifier}", "Managed poster", png((300, 300)), width=width, height=height)


class ImageChecks(unittest.TestCase):
    def test_all_dimensions_and_idempotent_normalization(self):
        for width in range(1, 11):
            for height in range(1, 11):
                with self.subTest(width=width, height=height):
                    record = poster(width, height)
                    self.assertEqual(Image.open(BytesIO(record["texture"])).size, (width * 64, height * 64))
                    self.assertEqual(record["texture"], poster_texture(record["texture"], width, height))

    def test_strict_dimensions_and_image_validation(self):
        for value in (0, 11, -1, True, 3.0, "3", None):
            for width, height in ((value, 2), (2, value)):
                with self.assertRaises(ValueError):
                    poster(width, height)
        for data in (b"bad image", b"", "file.png", png((4097, 1))):
            with self.assertRaises(ValueError):
                poster_texture(data, 3, 2)

    def test_center_crop_and_orientation(self):
        image = Image.new("RGB", (300, 100), "red")
        image.paste("lime", (100, 0, 200, 100))
        image.paste("blue", (200, 0, 300, 100))
        output = BytesIO()
        image.save(output, format="PNG")
        cropped = Image.open(BytesIO(poster_texture(output.getvalue(), 1, 1)))
        self.assertEqual(cropped.getpixel((32, 32)), (0, 255, 0, 255))
        self.assertEqual(cropped.getpixel((10, 32)), (0, 255, 0, 255))
        exif = Image.Exif()
        exif[274] = 6
        output = BytesIO()
        image.save(output, format="JPEG", exif=exif)
        rotated = Image.open(BytesIO(poster_texture(output.getvalue(), 1, 3)))
        self.assertGreater(rotated.getpixel((32, 10))[0], 240)
        self.assertGreater(rotated.getpixel((32, 180))[2], 240)

    def test_output_larger_than_upload_limit_can_reload(self):
        image = Image.frombytes("RGBA", (640, 640), random.Random(42).randbytes(640 * 640 * 4))
        output = BytesIO()
        image.save(output, format="PNG")
        self.assertGreater(len(output.getvalue()), MAX_UPLOAD)
        normalized = poster_texture(output.getvalue(), 10, 10)
        self.assertEqual(normalized, poster_texture(normalized, 10, 10))

    def test_static_webp_supported_and_animation_rejected(self):
        output = BytesIO()
        Image.new("RGB", (100, 100), "red").save(output, format="WEBP")
        self.assertEqual(Image.open(BytesIO(poster_texture(output.getvalue(), 1, 1))).size, (64, 64))
        output = BytesIO()
        Image.new("RGB", (10, 10), "red").save(output, format="PNG", save_all=True,
            append_images=[Image.new("RGB", (10, 10), "blue")], duration=100, loop=0)
        with self.assertRaises(ValueError):
            poster_texture(output.getvalue(), 1, 1)


class PackChecks(unittest.TestCase):
    def test_segment_geometry_loot_four_directions_and_creative(self):
        for width, height in ((1, 1), (3, 2), (1, 10), (10, 1), (10, 10)):
            with self.subTest(width=width, height=height):
                records = builtin_assets(ROOT / "minecraft") + fixture_assets() + [poster(width, height)]
                files = compile_files(ROOT / "minecraft", records)
                base = "poster_managed_1"
                catalog = json.loads(files["cosmetics/catalog.lock.json"])
                entry = catalog["posters"][0]
                self.assertEqual((entry["baseId"], entry["columns"], entry["rows"]), ("ichiyon:" + base, width, height))
                self.assertTrue(catalog["accessories"])
                geometries = json.loads(files[RP + f"models/blocks/{base}.geo.json"])["minecraft:geometry"]
                self.assertEqual(len(geometries), width * height + 1)
                for row in range(height):
                    for col in range(width):
                        name = f"{base}_r{row}c{col}"
                        block = json.loads(files[BP + f"blocks/{name}.json"])["minecraft:block"]
                        self.assertNotIn("menu_category", block["description"])
                        rotations = [p["components"]["minecraft:transformation"]["rotation"][1] for p in block["permutations"]]
                        self.assertEqual(rotations, [0, 180, 270, 90])
                        self.assertEqual(json.loads(files[BP + f"loot_tables/blocks/{name}.json"]), {"pools": []})
                        geo = next(g for g in geometries if g["description"]["identifier"] == "geometry." + name)
                        cube = geo["bones"][0]["cubes"][0]
                        self.assertEqual(cube["uv"]["north"], {"uv": [col * 64, (height - 1 - row) * 64], "uv_size": [64, 64]})
                        self.assertEqual(cube["size"], [16, 16, 0.03125])
                creative = json.loads(files[BP + "item_catalog/crafting_item_catalog.json"])
                items = [item for category in creative["minecraft:crafting_items_catalog"]["categories"]
                         for group in category["groups"] for item in group["items"]]
                self.assertEqual(items.count("ichiyon:" + base), 1)
                self.assertFalse(any(item.startswith("ichiyon:" + base + "_r") for item in items))
                self.assertIn("ichiyon:poster_raio", items)
                self.assertIn("ichiyon:accessory_1", items)
                for locale in ("ja_JP", "en_US"):
                    self.assertIn(f"tile.ichiyon:{base}.name=Managed poster", files[RP + f"texts/{locale}.lang"].decode())
                self.assertFalse(any("poster_raio" in path for path in files))

    def test_determinism_digest_and_existing_assets_in_archive(self):
        records = builtin_assets(ROOT / "minecraft") + fixture_assets() + [poster()]
        files = compile_files(ROOT / "minecraft", records)
        self.assertEqual(files, compile_files(ROOT / "minecraft", list(reversed(records))))
        changed = deepcopy(records)
        changed[-1] = poster(2, 3)
        self.assertNotEqual(catalog_digest(records), catalog_digest(changed))
        with self.assertRaises(ValueError):
            compile_files(ROOT / "minecraft", records + [poster()])
        with ZipFile(BytesIO(pack_zip(ROOT / "minecraft", records, 99))) as archive:
            fixed = BP + "blocks/poster_raio_r0c0.json"
            self.assertEqual(archive.read(fixed), (ROOT / "minecraft" / fixed).read_bytes())
            self.assertIn(BRIDGE + "scripts/poster_core.js", archive.namelist())
            self.assertIn(BP + "items/cosmetics/accessory_1.json", archive.namelist())

    def test_molcar_boost_movement_survives_database_pack_build(self):
        files = compile_files(ROOT / "minecraft", builtin_assets(ROOT / "minecraft") + [poster()])
        for molcar in ("molcar", "molcar2", "molcar3"):
            path = BP + f"entities/{molcar}.behavior.json"
            before = json.loads((ROOT / "minecraft" / path).read_bytes())["minecraft:entity"]
            after = json.loads(files[path])["minecraft:entity"]
            self.assertEqual(before["components"]["minecraft:movement"], {"value": 0.4, "max": 0.65})
            self.assertEqual(after["components"]["minecraft:movement"], before["components"]["minecraft:movement"])
            self.assertEqual(after["component_groups"], before["component_groups"])
            self.assertEqual(after["events"], before["events"])
        self.assertNotIn(BP + "entities/garbage_molcar.json", files)

    def test_batch_and_managed_revision_versions(self):
        records = builtin_assets(ROOT / "minecraft")
        for revision in (None, 22):
            files = compile_files(ROOT / "minecraft", records, revision=revision)
            for pack, patch in ((BP, 39), (RP, 44), (BRIDGE, 38)):
                manifest = json.loads(files[pack + "manifest.json"])
                expected = [1, 0, patch] if revision is None else [1, 1, revision]
                self.assertEqual(manifest["header"]["version"], expected)
                self.assertTrue(all(module["version"] == expected for module in manifest["modules"]))
            behavior = json.loads(files[BP + "manifest.json"])
            resource = json.loads(files[RP + "manifest.json"])
            for dependency in behavior.get("dependencies", []):
                if dependency.get("uuid") == resource["header"]["uuid"]:
                    self.assertEqual(dependency["version"], resource["header"]["version"])

    def test_local_builder_accepts_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "image.png").write_bytes(png())
            path = base / "assets.json"
            path.write_text(json.dumps([{"kind": "poster", "id": 1, "key": "poster_1", "name": "Local",
                                         "texture": "image.png", "width": 3, "height": 2}]), encoding="utf-8")
            self.assertEqual((local_catalog(path)[0]["width"], local_catalog(path)[0]["height"]), (3, 2))


if __name__ == "__main__":
    unittest.main()
