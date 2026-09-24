"""Focused archive coverage and independent resource-pack update regressions."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot.services.minecraft_cosmetics import asset
from bot.services.minecraft_cosmetics_pack import PACKS, builtin_assets, compile_files, pack_zip
from bot.services.minecraft_resource_packs import (
    LEGACY, LEGACY_UUID, RESOURCE_PACKS, REGISTRIES, SKINS, ACCESSORIES, POSTERS,
    VIDEO, RECORDS, split_resource_packs,
)
from scripts.check_minecraft_cosmetics import fixture_assets, png


class SplitChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT / "minecraft"
        cls.records = builtin_assets(cls.root) + fixture_assets()
        cls.records.append(asset("poster", 1, "managed", "Managed", png((128, 192)), width=2, height=3))
        cls.source = {p.relative_to(cls.root).as_posix(): p.read_bytes()
                      for pack in PACKS for p in (cls.root / pack).rglob("*") if p.is_file()}
        cls.static = dict(cls.source)
        cls.source.update(compile_files(cls.root, cls.records, revision=12))
        cls.split = split_resource_packs(cls.root, cls.source)

    def test_every_asset_byte_and_registry_entry_preserved_once(self):
        for path, raw in self.source.items():
            if not path.startswith(LEGACY):
                self.assertEqual(self.split[path], raw)
                continue
            relative = path[len(LEGACY):]
            outputs = [self.split[pack + relative] for pack in RESOURCE_PACKS if pack + relative in self.split]
            if relative == "manifest.json":
                continue
            if relative in REGISTRIES:
                field = REGISTRIES[relative]
                def entries(data):
                    doc = json.loads(data)
                    return doc[field] if field else {k: v for k, v in doc.items() if k != "format_version"}
                merged = {}
                for output in outputs:
                    part = entries(output)
                    self.assertFalse(merged.keys() & part.keys(), relative)
                    merged.update(part)
                self.assertEqual(merged, entries(raw), relative)
            elif relative.endswith(".lang"):
                original = [line for line in raw.decode("utf-8-sig").splitlines()
                            if not line.startswith(("pack.name=", "pack.description="))]
                result = [line for output in outputs for line in output.decode().splitlines()]
                self.assertCountEqual(result, original, relative)
            elif relative == "texts/languages.json":
                self.assertTrue(all(output == raw for output in outputs))
            else:
                self.assertEqual(outputs, [raw], relative)
        self.assertFalse(any(path.startswith(LEGACY) for path in self.split))

    def test_manifest_uuid_and_archive_contract(self):
        uuids = set()
        for pack in RESOURCE_PACKS:
            doc = json.loads(self.split[pack + "manifest.json"])
            for section in [doc["header"], *doc["modules"]]:
                self.assertNotIn(section["uuid"], uuids)
                self.assertNotEqual(section["uuid"], LEGACY_UUID)
                uuids.add(section["uuid"])
                self.assertEqual(section["version"], [1, 0, 0])
        with ZipFile(BytesIO(pack_zip(self.root, self.records, 12))) as archive:
            proof = json.loads(archive.read("cosmetics-build.json"))
            self.assertEqual(len(proof["packs"]), 8)
            self.assertEqual(proof["retired_packs"], [{"path": LEGACY.rstrip("/"), "uuid": LEGACY_UUID}])
            self.assertEqual(set(archive.namelist()) - {"cosmetics-build.json"}, set(self.split))

    def changed_packs(self, files):
        def payload(source, pack):
            return {p: data for p, data in source.items() if p.startswith(pack)}
        after = split_resource_packs(self.root, files)
        return {p for p in RESOURCE_PACKS if payload(after, p) != payload(self.split, p)}

    def test_db_changes_only_update_the_owning_resource_pack(self):
        for kind, expected in (("skin", SKINS), ("accessory", ACCESSORIES), ("poster", POSTERS)):
            records = deepcopy(self.records)
            index = next(i for i, a in enumerate(records) if a["kind"] == kind)
            # Deletion also changes the appropriate client bindings / UI entries.
            records.pop(index)
            files = dict(self.static)
            files.update(compile_files(self.root, records, revision=13))
            self.assertEqual(self.changed_packs(files), {expected}, kind)
            records = deepcopy(self.records)
            record = next(a for a in records if a["kind"] == kind)
            records.append(asset(kind, 20, "added", "Added", record["texture"],
                                 **{k: record[k] for k in ("model", "slot", "geometry", "icon", "width", "height") if k in record}))
            files = dict(self.static)
            files.update(compile_files(self.root, records, revision=14))
            self.assertEqual(self.changed_packs(files), {expected}, kind)

    def test_media_changes_and_revision_only_are_independent(self):
        for relative, expected in (("sounds/records/lets_cooking_molcar.ogg", RECORDS),
                                   ("textures/entity/video_screen/atlas_000.png", VIDEO)):
            files = dict(self.source)
            files[LEGACY + relative] += b"test change"
            self.assertEqual(self.changed_packs(files), {expected})
        files = dict(self.source)
        files.update(compile_files(self.root, self.records, revision=13))
        self.assertEqual(self.changed_packs(files), set())

    def test_real_archive_control_api_roundtrip_keeps_media_versions(self):
        from scripts.minecraft.minecraft_cosmetics_apply import unpack
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "live"
            live.mkdir()
            first = root / "first"
            unpack(pack_zip(self.root, self.records, 12), first, live)
            proof = json.loads((first / "cosmetics-build.json").read_bytes())
            for pack in proof["packs"]:
                shutil.copytree(first / pack, live / pack)
            records = self.records + [asset("skin", 20, "new_skin", "New skin", png())]
            second = root / "second"
            unpack(pack_zip(self.root, records, 13), second, live)
            for pack in RESOURCE_PACKS:
                before = json.loads((live / pack / "manifest.json").read_bytes())["header"]["version"]
                after = json.loads((second / pack / "manifest.json").read_bytes())["header"]["version"]
                self.assertEqual(after, [1, 0, 1] if pack == SKINS else before)
            for pack in proof["packs"]:
                shutil.copytree(second / pack, live / pack, dirs_exist_ok=True)
            third = root / "third"
            unpack(pack_zip(self.root, records, 14), third, live)
            for pack in RESOURCE_PACKS:
                self.assertEqual((second / pack / "manifest.json").read_bytes(),
                                 (third / pack / "manifest.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
