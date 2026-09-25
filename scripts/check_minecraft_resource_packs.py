"""Focused archive coverage and independent resource-pack update regressions."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bot.services.minecraft_cosmetics import asset
from bot.services import minecraft_cosmetics_pack as compiler
from bot.services.minecraft_cosmetics_pack import BP, BRIDGE, PACKS, builtin_assets, compile_files, pack_zip
from bot.services.minecraft_resource_packs import (
    LEGACY, LEGACY_UUID, RESOURCE_PACKS, SPLIT_RESOURCE_PACKS, DIRECT_RESOURCE_PACKS,
    REGISTRIES, SKINS, ACCESSORIES, POSTERS, VIDEO, VIDEO_BIG, RECORDS, split_resource_packs,
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
            outputs = [self.split[pack + relative] for pack in SPLIT_RESOURCE_PACKS if pack + relative in self.split]
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
            self.assertEqual(proof["packs"], [p.rstrip("/") for p in (BP, BRIDGE, *RESOURCE_PACKS)])
            self.assertEqual(len(RESOURCE_PACKS), 8)
            self.assertEqual(proof["retired_packs"], [{"path": LEGACY.rstrip("/"), "uuid": LEGACY_UUID}])
            direct = {p.relative_to(self.root).as_posix(): p for pack in DIRECT_RESOURCE_PACKS
                      for p in (self.root / pack).rglob("*") if p.is_file()}
            self.assertEqual(set(archive.namelist()) - {"cosmetics-build.json"}, set(self.split) | direct.keys())
            for name, path in direct.items():
                self.assertEqual(archive.read(name), path.read_bytes(), name)
            for name, data in self.split.items():
                self.assertEqual(archive.read(name), data, name)

    def test_direct_pack_never_receives_legacy_registries_or_language_files(self):
        self.assertEqual([p for p in self.split if p.startswith(VIDEO_BIG)], [VIDEO_BIG + "manifest.json"])
        from bot.services import minecraft_resource_packs as packs
        with patch.object(packs, "RESOURCE_PACKS", SPLIT_RESOURCE_PACKS):
            previous = split_resource_packs(self.root, self.source)
        self.assertEqual(previous, {p: data for p, data in self.split.items() if not p.startswith(DIRECT_RESOURCE_PACKS)})

    def test_direct_content_only_bumps_its_pack_after_six_pack_install(self):
        from scripts.minecraft.minecraft_cosmetics_apply import unpack, content_hash
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "minecraft"
            # Use a small direct fixture, independently of the pending video encode.
            def ignore(directory, names):
                return [name for name in names if name != "manifest.json"] if Path(directory) == self.root / VIDEO_BIG else []
            shutil.copytree(self.root, source, ignore=ignore)
            media = source / VIDEO_BIG / "fixture.bin"
            media.write_bytes(b"first direct media")
            first = root / "first"
            live = root / "live"
            live.mkdir()
            unpack(pack_zip(source, self.records, 12), first, live)
            for pack in (BP, BRIDGE, *SPLIT_RESOURCE_PACKS):
                shutil.copytree(first / pack, live / pack)
            original = {pack: content_hash(live / pack) for pack in SPLIT_RESOURCE_PACKS}
            second = root / "second"
            unpack(pack_zip(source, self.records, 13), second, live)
            for pack in SPLIT_RESOURCE_PACKS:
                self.assertEqual(content_hash(second / pack), original[pack])
                self.assertEqual((second / pack / "manifest.json").read_bytes(), (live / pack / "manifest.json").read_bytes())
            self.assertEqual(json.loads((second / VIDEO_BIG / "manifest.json").read_bytes())["header"]["version"], [1, 0, 0])
            for pack in (BP, BRIDGE, *RESOURCE_PACKS):
                shutil.copytree(second / pack, live / pack, dirs_exist_ok=True)
            original.update({pack: content_hash(live / pack) for pack in DIRECT_RESOURCE_PACKS})
            media.write_bytes(b"changed direct media")
            third = root / "third"
            unpack(pack_zip(source, self.records, 14), third, live)
            for pack in RESOURCE_PACKS:
                version = json.loads((third / pack / "manifest.json").read_bytes())["header"]["version"]
                self.assertEqual(version, [1, 0, 1] if pack == VIDEO_BIG else [1, 0, 0])
                if pack != VIDEO_BIG:
                    self.assertEqual(content_hash(third / pack), original[pack])
                shutil.copytree(third / pack, live / pack, dirs_exist_ok=True)
            fourth = root / "fourth"
            unpack(pack_zip(source, self.records, 15), fourth, live)
            for pack in RESOURCE_PACKS:
                self.assertEqual((fourth / pack / "manifest.json").read_bytes(), (live / pack / "manifest.json").read_bytes())

    def test_compiler_and_standalone_control_limits_agree_and_fail_closed(self):
        from scripts.minecraft import minecraft_cosmetics_apply as deploy
        from bot.services import minecraft_control as client
        for name, value in (("MAX_ARCHIVE", 192 * 1024 * 1024), ("MAX_EXPANDED", 512 * 1024 * 1024),
                            ("MAX_FILE", 8 * 1024 * 1024), ("MAX_FILES", 16384)):
            self.assertEqual(getattr(compiler, name), value)
            self.assertEqual(getattr(deploy, name), value)
            with self.subTest(limit=name), patch.object(compiler, name, 1), self.assertRaises(ValueError):
                pack_zip(self.root, self.records, 12)
        self.assertEqual(client.MAX_ARCHIVE, deploy.MAX_ARCHIVE)

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
