"""Offline pack contracts; no world, environment, or external source access.

JavaScript checks are static contracts, not a Bedrock runtime simulation.
"""

import json
import math
import re
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
BP = ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp"
RP = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp"
IMPORT = ROOT / "minecraft/behavior_packs/import_structures"
POSTERS = {
    "raio": "ライオ", "trent": "トレント", "aurelia": "オーレリア",
    "killzael": "キルザエル", "caravan_mammoth": "キャラバンマンモス",
    "itsutake": "イツタケ", "cat_tuner": "キャットチューナー",
    "wilbert": "ウィルバート", "miltio": "ミルティオ", "ace": "エース",
    "eyes_eden": "アイズエデン", "akuki": "悪鬼",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dimensions(key):
    return (6, 4) if key == "akuki" else (4, 6)


class PosterChecks(unittest.TestCase):
    def setUp(self):
        self.blocks = read_json(RP / "blocks.json")
        self.terrain = read_json(RP / "textures/terrain_texture.json")["texture_data"]
        self.lang = {
            locale: (RP / "texts" / f"{locale}.lang").read_text(encoding="utf-8").splitlines()
            for locale in ("ja_JP", "en_US")
        }

    def test_versions(self):
        for pack, version in ((BP, [1, 0, 37]), (RP, [1, 0, 42]), (IMPORT, [1, 0, 33])):
            with self.subTest(pack=pack.name):
                manifest = read_json(pack / "manifest.json")
                self.assertEqual(manifest["header"]["version"], version)
                self.assertTrue(manifest["modules"])
                for module in manifest["modules"]:
                    self.assertEqual(module["version"], version)
        dependencies = read_json(IMPORT / "manifest.json")["dependencies"]
        self.assertEqual([d["version"] for d in dependencies
                          if d.get("module_name") == "@minecraft/server"], ["2.11.0-beta"])

    def test_blocks_loot_and_localization(self):
        for key, japanese in POSTERS.items():
            columns, rows = dimensions(key)
            base = f"poster_{key}"
            names = {base} | {f"{base}_r{r}c{c}" for r in range(rows) for c in range(columns)}
            for directory in (BP / "blocks", BP / "loot_tables/blocks"):
                self.assertEqual({p.stem for p in directory.glob(f"{base}_r*.json")}, names - {base})
            self.assertEqual({name for name in self.blocks if name.startswith(f"ichiyon:{base}_r")},
                             {f"ichiyon:{name}" for name in names - {base}})
            self.assertEqual(self.terrain[base]["textures"], f"textures/blocks/{base}")
            for locale, label in (("ja_JP", japanese + "ポスター"),
                                  ("en_US", key.replace("_", " ").title() + " Poster")):
                prefix = f"tile.ichiyon:{base}.name="
                self.assertEqual([line for line in self.lang[locale] if line.startswith(prefix)],
                                 [prefix + label])
            # Hidden segments intentionally have no localized inventory names.
            for name in sorted(names):
                with self.subTest(block=name):
                    block = read_json(BP / "blocks" / f"{name}.json")["minecraft:block"]
                    self.assertEqual(block["description"]["identifier"], f"ichiyon:{name}")
                    if name == base:
                        self.assertEqual(block["description"]["menu_category"],
                                         {"category": "construction"})
                    else:
                        self.assertNotIn("menu_category", block["description"])
                    components = block["components"]
                    self.assertIs(components["minecraft:collision_box"], False)
                    self.assertEqual(components["minecraft:geometry"], f"geometry.{name}")
                    self.assertEqual(components["minecraft:material_instances"]["*"]["texture"], base)
                    self.assertEqual(components["minecraft:loot"], f"loot_tables/blocks/{name}.json")
                    self.assertEqual(self.blocks[f"ichiyon:{name}"]["textures"], base)
                    loot = read_json(BP / "loot_tables/blocks" / f"{name}.json")
                    expected = {"pools": [{"rolls": 1, "entries": [
                        {"type": "item", "name": f"ichiyon:{base}"}]}]} if name == base else {"pools": []}
                    self.assertEqual(loot, expected)

    def test_creative_poster_group_contains_only_placeable_items(self):
        catalog = read_json(BP / "item_catalog/crafting_item_catalog.json")
        self.assertEqual(catalog["format_version"], "1.21.60")
        categories = catalog["minecraft:crafting_items_catalog"]["categories"]
        construction = next(c for c in categories if c["category_name"] == "construction")
        groups = [g for g in construction["groups"]
                  if g.get("group_identifier", {}).get("name") == "ichiyon:itemGroup.posters"]
        self.assertEqual(len(groups), 1)
        group = groups[0]
        expected = {"ichiyon:poster_irsia"} | {f"ichiyon:poster_{name}" for name in POSTERS}
        self.assertEqual(set(group["items"]), expected)
        self.assertEqual(len(group["items"]), len(expected))
        self.assertIn(group["group_identifier"]["icon"], expected)
        visible = set()
        for path in (BP / "blocks").glob("poster*.json"):
            description = read_json(path)["minecraft:block"]["description"]
            if "menu_category" in description:
                visible.add(description["identifier"])
                # Legacy block formats prefix group names with minecraft:.
                # The 1.21.60 crafting catalog owns grouping instead.
                self.assertEqual(description["menu_category"], {"category": "construction"})
        self.assertEqual(visible, expected)
        for locale, label in (("ja_JP", "ポスター"), ("en_US", "Posters")):
            self.assertEqual([line for line in self.lang[locale] if line.startswith("ichiyon:itemGroup.posters=")],
                             ["ichiyon:itemGroup.posters=" + label])

    def test_dedicated_geometry_and_pngs(self):
        for key in POSTERS:
            columns, rows = dimensions(key)
            base = f"geometry.poster_{key}"
            expected = {base} | {f"{base}_r{r}c{c}" for r in range(rows) for c in range(columns)}
            geometries = read_json(RP / "models/blocks" / f"poster_{key}.geo.json")["minecraft:geometry"]
            self.assertEqual(len(geometries), len(expected))
            self.assertEqual({g["description"]["identifier"] for g in geometries}, expected)
            with Image.open(RP / "textures/blocks" / f"poster_{key}.png") as image:
                self.assertEqual(image.format, "PNG")
                self.assertIn(image.mode, ("RGB", "RGBA"))
                image.load()
                if image.mode == "RGBA":
                    self.assertIsNotNone(image.getchannel("A").getbbox())
                width, height = image.size
            for geometry in geometries:
                with self.subTest(geometry=geometry["description"]["identifier"]):
                    description = geometry["description"]
                    self.assertEqual((description["texture_width"], description["texture_height"]),
                                     (width, height))
                    cubes = [cube for bone in geometry["bones"] for cube in bone.get("cubes", [])]
                    self.assertTrue(cubes)
                    for cube in cubes:
                        size, origin = cube["size"], cube["origin"]
                        self.assertEqual(len(size), 3)
                        self.assertEqual(len(origin), 3)
                        self.assertTrue(all(math.isfinite(v) and v > 0 for v in size))
                        self.assertTrue(all(math.isfinite(v) for v in origin))
                        self.assertLessEqual(max(size[:2]), 30)
                        self.assertAlmostEqual(size[2], 0.03125)
                        self.assertAlmostEqual(origin[2], 7.46875)
                        self.assertEqual(set(cube["uv"]), {"north"})
                        north = cube["uv"]["north"]
                        self.assertEqual(len(north["uv"]), 2)
                        self.assertEqual(len(north["uv_size"]), 2)
                        for start, extent, limit in zip(north["uv"], north["uv_size"], (width, height)):
                            self.assertTrue(math.isfinite(start) and math.isfinite(extent))
                            self.assertGreaterEqual(start, 0)
                            self.assertGreater(extent, 0)
                            self.assertLessEqual(start + extent, limit + 0.0001)

    def test_irsia_non_regression(self):
        for path in (BP / "blocks/poster_irsia.json", RP / "textures/blocks/poster_irsia.png",
                     RP / "models/blocks/poster_irsia.geo.json"):
            self.assertTrue(path.is_file(), str(path))
        self.assertEqual(self.blocks["ichiyon:poster_irsia"]["textures"], "poster_irsia")
        self.assertEqual(self.terrain["poster_irsia"]["textures"], "textures/blocks/poster_irsia")
        self.assertIn("tile.ichiyon:poster_irsia.name=イルシアポスター", self.lang["ja_JP"])
        self.assertTrue(any(line.startswith("tile.ichiyon:poster_irsia.name=")
                            and line.partition("=")[2] for line in self.lang["en_US"]))

    def test_script_contracts(self):
        script = (IMPORT / "scripts/main.js").read_text(encoding="utf-8")
        # Scope checks to the relevant function so unrelated text cannot satisfy them.
        def function(name):
            match = re.search(r"(?:async )?function " + name + r"\([^\n]*\) \{\n(.*?)\n\}", script, re.S)
            self.assertIsNotNone(match, name)
            return match.group(1)

        self.assertIn("const LARGE_POSTER_COLUMNS = 4;", script)
        self.assertIn("const LARGE_POSTER_ROWS = 6;", script)
        registry = re.search(r"const LARGE_POSTERS = \[(.*?)\n\];", script, re.S)
        self.assertIsNotNone(registry)
        entries = re.findall(r"\{([^{}]+)\}", registry.group(1))
        self.assertEqual(len(entries), 12)
        self.assertEqual(re.findall(r'baseId: "ichiyon:poster_(\w+)"', registry.group(1)), list(POSTERS))
        for key, entry in zip(POSTERS, entries):
            self.assertIn(f'segmentPrefix: "ichiyon:poster_{key}"', entry)
            if key == "akuki":
                self.assertIn("columns: 6, rows: 4", entry)
            else:
                self.assertNotRegex(entry, r"\b(?:columns|rows):")
            self.assertIn(f'poster_{key}: "ichiyon:poster_{key}"', script)
        for contract in ("row < (poster.rows || LARGE_POSTER_ROWS)",
                         "column < (poster.columns || LARGE_POSTER_COLUMNS)",
                         "id: `${poster.segmentPrefix}_r${row}c${column}`"):
            self.assertIn(contract, script)
        vectors = function("posterVectors")
        for direction in ("south", "east", "west"):
            self.assertIn(f'direction === "{direction}"', vectors)
        for vector in ("right: { x: 1, z: 0 }, wall: { x: 0, z: -1 }",
                       "right: { x: 0, z: -1 }, wall: { x: -1, z: 0 }",
                       "right: { x: 0, z: 1 }, wall: { x: 1, z: 0 }",
                       "right: { x: -1, z: 0 }, wall: { x: 0, z: 1 }"):
            self.assertIn(vector, vectors)
        ready = function("allLargePosterCellsReady")
        loop = "for (const segment of LARGE_POSTER_SEGMENTS.filter((candidate) => candidate.poster === poster))"
        self.assertIn(loop, ready)
        self.assertIn("if (!isReplaceablePosterTarget(block, baseLocation, poster))", ready)
        self.assertIn("if (!hasSolidPosterSupport(dimension, location, direction))", ready)
        expand = function("expandLargePoster")
        self.assertLess(expand.index("if (!allLargePosterCellsReady("), expand.index(loop))
        failure = expand[:expand.index(loop)]
        self.assertIn("setBlockType(dimension, baseLocation, AIR_BLOCK_TYPE);", failure)
        self.assertIn("returnLargePosterItem(player, baseLocation, poster);", failure)
        self.assertIn("return;", failure[failure.index("setBlockType"):])
        cleanup = function("cleanupLargePosterSegment")
        self.assertIn("removeLargePosterCells(block.dimension, baseLocation, direction, segment.poster);", cleanup)
        self.assertIn("returnLargePosterItem(player, block.location, segment.poster);", cleanup)
        remove = function("removeLargePosterCells")
        self.assertIn(loop, remove)
        self.assertIn("if (blockSegment && blockSegment.poster === poster)", remove)
        self.assertIn("block.setPermutation(BlockPermutation.resolve(AIR_BLOCK_TYPE));", remove)
        self.assertIn('return gameMode === "survival" || gameMode === "adventure";',
                      function("playerShouldReceivePosterDrop"))
        self.assertIn("if (!playerShouldReceivePosterDrop(player)) {\n    return;\n  }",
                      function("returnLargePosterItem"))
        self.assertIn("new ItemStack(poster.baseId, 1)", function("returnLargePosterItem"))
        self.assertIn("world.afterEvents.playerPlaceBlock.subscribe", script)
        self.assertIn("expandLargePoster(event.block, event.player);", script)
        self.assertIn("world.afterEvents.playerBreakBlock.subscribe", script)
        self.assertIn("cleanupLargePosterSegment(event.block, event.brokenBlockPermutation, event.player);", script)
        self.assertEqual(script.count("system.runInterval"), 2)
        self.assertIn("POLL_INTERVAL_TICKS", script)
        self.assertIn("Object.prototype.hasOwnProperty.call(ITEM_TYPES_BY_COMMAND, command.type)", script)
        self.assertIn("await handleGiveItem(command, ITEM_TYPES_BY_COMMAND[command.type]);", script)
        self.assertNotRegex(script, r"command\s*(?:\.\s*item\w*|\[\s*['\"]item)")
        self.assertNotRegex(script, r"BlockPermutation\.resolve\(\s*command")


if __name__ == "__main__":
    unittest.main()
