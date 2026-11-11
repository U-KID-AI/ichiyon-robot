"""Packaging safety tests; these are not a Minecraft client playthrough."""
import json
from pathlib import Path
import tempfile
import unittest

from junket_private_bundle import private_manifests, select_conversion, selected_files, sha256, write_zip


class PrivateBundleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def conversion(self):
        source = self.root / "original.zip"
        source.write_bytes(b"private source archive fixture")
        bounds = [[1, 2, 3], [4, 5, 6]]
        (self.root / "conversion-report.json").write_text(json.dumps({
            "source_sha256": sha256(source.read_bytes()),
            "structures": {"selected": {"source_bounds": bounds}},
        }), encoding="utf-8")
        for relative, data in {
            "ichiyon_junket_assets_bp/structures/junket/selected.mcstructure": b"selected hall",
            "ichiyon_junket_assets_bp/structures/junket/other.mcstructure": b"must not copy",
            "ichiyon_junket_assets_bp/entities/box.json": b"selected box",
            "ichiyon_junket_assets_rp/textures/selected.png": b"selected artwork",
            "ichiyon_junket_assets_rp/textures/other.png": b"must not copy",
        }.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return source, {"structures": {"selected": bounds}, "entities": ["box"],
                        "resources": ["textures/selected.png"]}

    def test_explicit_subset_preserves_bytes_and_provenance(self):
        source, selection = self.conversion()
        bp, rp, evidence = select_conversion(self.root, source, **selection)
        self.assertEqual(set(bp), {"structures/junket/selected.mcstructure", "entities/box.json"})
        self.assertEqual(rp, {"textures/selected.png": b"selected artwork"})
        self.assertEqual(evidence["source_sha256"], sha256(source.read_bytes()))
        self.assertEqual(evidence["original_asset_sha256"]["rp/textures/selected.png"], sha256(b"selected artwork"))

    def test_wrong_original_is_rejected(self):
        source, selection = self.conversion()
        source.write_bytes(b"a different version")
        with self.assertRaisesRegex(ValueError, "original ZIP"):
            select_conversion(self.root, source, **selection)

    def test_wrong_source_bounds_are_rejected(self):
        source, selection = self.conversion()
        selection["structures"]["selected"] = [[10, 20, 30], [40, 50, 60]]
        with self.assertRaisesRegex(ValueError, "requested original venue"):
            select_conversion(self.root, source, **selection)

    def test_traversal_and_absolute_selections_are_rejected(self):
        for name in ("../private.txt", "/private.txt", "entity/../../private.txt", "entity\\private.txt"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                selected_files(self.root, [name])

    def test_duplicate_selection_is_rejected(self):
        (self.root / "asset.txt").write_bytes(b"asset")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            selected_files(self.root, ["asset.txt", "asset.txt"])

    def test_uuid_isolation_and_only_api_dependencies_retained(self):
        current = {"header": {"uuid": "existing-bp", "min_engine_version": [1, 26, 44]},
                   "dependencies": [{"module_name": "@minecraft/server", "version": "2.0.0"},
                                    {"uuid": "existing-public-rp", "version": [9, 9, 9]}]}
        packs = [private_manifests(current, entry="scripts/trial.js", version=[0, 1, 0], label="Trial")
                 for _ in range(2)]
        ids = []
        for bp, rp in packs:
            self.assertEqual(rp['capabilities'], ['pbr'])
            self.assertNotIn('capabilities', bp)
            ids += [bp["header"]["uuid"], rp["header"]["uuid"]]
            ids += [module["uuid"] for pack in (bp, rp) for module in pack["modules"]]
            self.assertEqual(bp["modules"][1]["entry"], "scripts/trial.js")
            self.assertEqual(bp["dependencies"], [current["dependencies"][0],
                             {"uuid": rp["header"]["uuid"], "version": [0, 1, 0]}])
        self.assertEqual(len(ids), len(set(ids)))

    def test_existing_archive_is_not_overwritten(self):
        target = self.root / "trial.mcpack"
        result = write_zip(target, {"notice.txt": b"private"})
        before = target.read_bytes()
        self.assertEqual(result["zip_crc"], "PASS")
        with self.assertRaises(FileExistsError):
            write_zip(target, {"notice.txt": b"replacement"})
        self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
