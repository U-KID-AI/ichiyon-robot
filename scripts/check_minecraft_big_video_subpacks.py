"""Offline V3/load-view fixtures. No test claims to emulate BDS pack transfer."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from build_minecraft_big_video_subpacks import (
    ROOT, PACK, MANIFEST, generated_files, validate_layout, validate_manifest,
    selected_subpack, selected_files, enable_report, pause_state,
    manifest_for_threshold, threshold_from_observed_rp_tiers,
)
from build_minecraft_rp_tier_probe import probe_files, build_archive, UUID as PROBE_UUID
from zipfile import ZipFile

sys.path.insert(0, str(ROOT))
from bot.services import minecraft_resource_packs as packs


class SubpackChecks(unittest.TestCase):
    pack = ROOT / PACK

    def test_v3_contract_and_generated_black_assets(self):
        validate_layout(self.pack)
        self.assertEqual(MANIFEST['header']['uuid'], packs.VIDEO_BIG_UUID)
        self.assertEqual(MANIFEST['metadata']['authors'], ['Ichiyon'])
        self.assertEqual(MANIFEST['capabilities'], ['pbr'])
        for name in ('version', 'min_engine_version'):
            self.assertIsInstance(MANIFEST['header'][name], str)
        self.assertEqual([(s['folder_name'], s['memory_performance_tier']) for s in MANIFEST['subpacks']],
                         [('lightweight', 1)])
        self.assertNotIn('memory_tier', json.dumps(MANIFEST))

    def test_wrong_manifest_versions_tiers_or_uuid_rejected(self):
        for section, key, value in [(None, 'format_version', 2), ('header', 'version', [1, 0, 2]),
                                    ('header', 'uuid', 'other')]:
            doc = copy.deepcopy(MANIFEST)
            (doc[section] if section else doc)[key] = value
            with self.assertRaises(ValueError): validate_manifest(doc)
        for tier in (0, 2, 4, True):
            doc = copy.deepcopy(MANIFEST); doc['subpacks'][0]['memory_performance_tier'] = tier
            with self.assertRaises(ValueError): validate_manifest(doc)
        with self.assertRaises(ValueError): validate_manifest(manifest_for_threshold(3))

    def test_engine_selection_model_no_device_name_table(self):
        self.assertEqual([selected_subpack(t) for t in range(1, 6)], ['lightweight'] * 5)
        # Every potential measured threshold, without adopting one in source.
        for minimum in range(2, 6):
            for tier in range(1, 7):
                expected = 'full' if tier >= minimum else 'lightweight'
                self.assertEqual(selected_subpack(tier, full_min_tier=minimum), expected)
                self.assertEqual(selected_subpack(tier, 'lightweight', full_min_tier=minimum), 'lightweight')
                if tier < minimum:
                    with self.assertRaises(ValueError): selected_subpack(tier, 'full', full_min_tier=minimum)
        for tier in range(1, 6): self.assertEqual(selected_subpack(tier, 'lightweight'), 'lightweight')
        for tier in range(1, 6):
            with self.assertRaises(ValueError): selected_subpack(tier, 'full')
        for invalid in (0, 1, 6, True, '3'):
            with self.assertRaises(ValueError): manifest_for_threshold(invalid)
        for unknown in (None, 0, True, 'Console', 'Switch 2'):
            with self.assertRaises(ValueError): selected_subpack(unknown)

    def test_protected_unknown_or_overlapping_tiers_fail_closed(self):
        for pixel in range(1, 6):
            for es in range(1, 6):
                for sourui in range(1, 6):
                    threshold = threshold_from_observed_rp_tiers(pixel, es, sourui)
                    if max(1, es, sourui) < pixel:
                        self.assertEqual(threshold, pixel)
                    else:
                        self.assertIsNone(threshold)
                    for protected in (1, es, sourui):
                        self.assertEqual(selected_subpack(protected, full_min_tier=threshold), 'lightweight')
        for unknown in (None, True, 'High', 'Mobile', 0, 6):
            for tiers in ((unknown, 1, 1), (4, unknown, 1), (4, 1, unknown)):
                self.assertIsNone(threshold_from_observed_rp_tiers(*tiers))

    def test_probe_is_tiny_separate_and_observes_all_five_overlays(self):
        files = probe_files()
        manifest = json.loads(files['manifest.json'])
        self.assertNotEqual(PROBE_UUID, MANIFEST['header']['uuid'])
        self.assertEqual([s['memory_performance_tier'] for s in manifest['subpacks']], list(range(1, 6)))
        self.assertLess(sum(map(len, files.values())), 16384)
        self.assertFalse(any('atlas' in p or p.endswith('.ogg') or 'scripts/' in p for p in files))
        self.assertIn(b'SELECTION UNVERIFIED', files['texts/en_US.lang'])
        for tier in range(1, 6):
            prefix = f'subpacks/tier_{tier}/'
            self.assertIn(f'RP TIER {tier}'.encode(), files[prefix + 'texts/ja_JP.lang'])
            from io import BytesIO
            with Image.open(BytesIO(files[prefix + 'textures/blocks/cobblestone.png'])) as texture:
                self.assertEqual(texture.size, (16, 16))
        with tempfile.TemporaryDirectory() as temporary:
            archive = build_archive(Path(temporary) / 'probe.mcpack')
            with ZipFile(archive) as zipfile:
                self.assertEqual({p: zipfile.read(p) for p in zipfile.namelist()}, files)
            with self.assertRaises(FileExistsError): build_archive(archive)

    def test_low_selected_view_has_no_atlas_bytes_references_or_animation(self):
        for tier in (1, 2, 3, 4, 5):
            view = selected_files(self.pack, selected_subpack(tier, 'lightweight' if tier >= 3 else None))
            self.assertFalse(any('atlas' in p or p.endswith('.ogg') for p in view))
            for path, data in view.items():
                if path.endswith(('.json', '.material')):
                    self.assertNotIn('atlas_', data.decode(), path)
                    self.assertNotIn('uv_anim', data.decode(), path)
                    self.assertNotIn("q.property('ichiyon:frame')", data.decode(), path)
            textures = [p for p in view if p.endswith('.png')]
            self.assertEqual(textures, ['textures/entity/video_screen_big/black.png'])
            with Image.open(self.pack / textures[0]) as black:
                self.assertEqual(black.size, (1, 1))
                self.assertEqual(black.getpixel((0, 0)), (0, 0, 0))

    def test_base_and_low_reject_even_unreferenced_atlas(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            # Validation uses file placement before payload decoding; tiny sentinels suffice.
            for path, data in generated_files(self.pack).items():
                target = pack / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
            full = pack / 'subpacks/full'
            for path in ('models/entity/video_screen_big.geo.json', 'textures/entity/video_screen_big/black.png'):
                target = full / path; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.pack / 'subpacks/full' / path, target)
            textures = full / 'textures/entity/video_screen_big'
            for i in range(26): (textures / f'atlas_{i:03d}.png').write_bytes(b'fixture')
            validate_layout(pack)
            for prefix in ('', 'subpacks/lightweight/'):
                leaked = pack / (prefix + 'textures/entity/video_screen_big/atlas_000.png')
                leaked.parent.mkdir(parents=True, exist_ok=True); leaked.write_bytes(b'heavy-even-if-unreferenced')
                with self.assertRaises(ValueError): validate_layout(pack)
                leaked.unlink()

    def test_full_preserves_all_media_bytes_and_video_contract(self):
        lock = json.loads((ROOT / 'minecraft/video_screen_big/full_media.lock.json').read_bytes())['files']
        self.assertEqual(len(lock), 27)
        full = self.pack / 'subpacks/full'
        for path, expected in lock.items():
            data = (full / path).read_bytes()
            self.assertEqual(len(data), expected['bytes'], path)
            self.assertEqual(hashlib.sha256(data).hexdigest(), expected['sha256'], path)
        out = json.loads((ROOT / 'minecraft/video_screen_big/build_report.json').read_bytes())['output']
        self.assertEqual((out['duration'], out['frameWidth'], out['frameHeight'], out['fps'], out['atlasCount']),
                         (510.15, 128, 72, 20, 26))
        self.assertEqual(out['frameCount'], 10203)
        view = selected_files(self.pack, 'full')
        self.assertEqual(sum('/atlas_' in p for p in view), 26)
        self.assertIn('sounds/video_screen_big/audio.ogg', view)
        for path, expected in lock.items(): self.assertEqual(hashlib.sha256(view[path]).hexdigest(), expected['sha256'])

    def test_production_remains_paused_and_other_rps_stay_v2(self):
        self.assertFalse(packs.BIG_VIDEO_ENABLED)
        self.assertNotIn(packs.VIDEO_BIG, packs.RESOURCE_PACKS)
        self.assertEqual(packs.RETIRED_RESOURCE_PACKS[packs.VIDEO_BIG.rstrip('/')], packs.VIDEO_BIG_UUID)
        js = (ROOT / 'minecraft/behavior_packs/import_structures/scripts/wall_displays_config.js').read_text()
        self.assertIn('export const BIG_VIDEO_ENABLED = false;', js)
        for pack in packs.RESOURCE_PACKS:
            manifest = json.loads((ROOT / 'minecraft' / pack / 'manifest.json').read_bytes())
            self.assertEqual(manifest['format_version'], 2)
            self.assertEqual(manifest['capabilities'], ['pbr'])

    def test_flag_flip_alone_cannot_compile_big_v3_into_managed_archive(self):
        with patch.object(packs, 'RESOURCE_PACKS', (*packs.RESOURCE_PACKS, packs.VIDEO_BIG)):
            with self.assertRaisesRegex(ValueError, 'V3/subpack enable blocked'):
                packs.split_resource_packs(ROOT / 'minecraft', {})

    def test_enable_checker_is_blocked_and_does_not_promise_selective_download(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/build_minecraft_big_video_subpacks.py'), '--check-enable'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertEqual(report, enable_report())
        self.assertEqual(report['enable_status'], 'blocked')
        self.assertIsNone(report['full_min_tier'])
        self.assertEqual(set(report['selection'].values()), {'lightweight'})
        self.assertIn('download/cache', report['network_delivery'])
        self.assertIn('decode/load/residency', ' '.join(report['blocking_requirements']))
        # A full RP archive includes BOTH subpacks. A local light view must never
        # be presented as proof of what the server sends to a low-tier client.
        whole_pack_paths = [p.relative_to(self.pack).as_posix() for p in self.pack.rglob('*') if p.is_file()]
        self.assertEqual(sum('/atlas_' in p for p in whole_pack_paths), 26)

    def test_readiness_reports_flag_drift_instead_of_assuming_pause(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            py = root / 'bot/services/minecraft_resource_packs.py'
            js = root / 'minecraft/behavior_packs/import_structures/scripts/wall_displays_config.js'
            py.parent.mkdir(parents=True); js.parent.mkdir(parents=True)
            py.write_text('BIG_VIDEO_ENABLED = False\n'); js.write_text('export const BIG_VIDEO_ENABLED = false;\n')
            self.assertFalse(any(pause_state(root).values()))
            py.write_text('BIG_VIDEO_ENABLED = True\n')
            self.assertTrue(pause_state(root)['compiler_enabled'])
            js.write_text('export const BIG_VIDEO_ENABLED = true;\n')
            self.assertTrue(pause_state(root)['runtime_enabled'])
            py.write_text('BIG_VIDEO_ENABLED = unknown\n')
            with self.assertRaises(ValueError): pause_state(root)


if __name__ == '__main__':
    unittest.main()
