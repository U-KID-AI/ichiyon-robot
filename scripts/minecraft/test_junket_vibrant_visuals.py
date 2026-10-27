"""World-level graphics-mode compatibility; these tests do not render a client."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from junket_render_validation import validate_vibrant_manifest


ROOT = Path(__file__).resolve().parents[2]
RP = ROOT / 'minecraft/resource_packs/ichiyon_junket_rp'


class VibrantVisualsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rp = Path(self.temp.name)
        self.manifest = json.loads((RP / 'manifest.json').read_text(encoding='utf-8'))

    def validate(self, value):
        path = self.rp / 'manifest.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        before = path.read_bytes()
        result = validate_vibrant_manifest(self.rp)
        self.assertEqual(path.read_bytes(), before)
        return result

    def test_source_manifest_opts_in_without_claiming_client_success(self):
        result = validate_vibrant_manifest(RP)
        self.assertTrue(result['manifest_compatible'])
        self.assertFalse(result['client_rendering_verified'])
        self.assertEqual(self.manifest['header']['uuid'], 'd48f4748-5b7b-4ad5-857a-3491b6fa2b16')
        self.assertGreater(tuple(self.manifest['header']['version']), (0, 8, 5))
        for module in self.manifest['modules']:
            self.assertEqual(module['version'], self.manifest['header']['version'])

    def test_previous_missing_capability_is_rejected(self):
        for value in (None, [], ['raytraced'], 'pbr'):
            with self.subTest(capabilities=value):
                manifest = deepcopy(self.manifest)
                if value is None:
                    manifest.pop('capabilities', None)
                else:
                    manifest['capabilities'] = value
                with self.assertRaisesRegex(ValueError, 'pbr capability'):
                    self.validate(manifest)

    def test_documented_minimum_engine_and_newer_are_accepted(self):
        for engine in ([1, 21, 120], [1, 26, 44]):
            with self.subTest(engine=engine):
                manifest = deepcopy(self.manifest)
                manifest['header']['min_engine_version'] = engine
                self.assertTrue(self.validate(manifest)['manifest_compatible'])

    def test_old_or_malformed_engine_is_rejected(self):
        for engine in ([1, 21, 119], [1, 20, 1000], [1, 26], '1.26.44', [1, 26, True]):
            with self.subTest(engine=engine):
                manifest = deepcopy(self.manifest)
                manifest['header']['min_engine_version'] = engine
                with self.assertRaisesRegex(ValueError, 'min_engine_version'):
                    self.validate(manifest)

    def test_behavior_pack_cannot_satisfy_resource_requirement(self):
        manifest = deepcopy(self.manifest)
        manifest['modules'][0]['type'] = 'data'
        with self.assertRaisesRegex(ValueError, 'resource pack'):
            self.validate(manifest)


if __name__ == '__main__':
    unittest.main()
