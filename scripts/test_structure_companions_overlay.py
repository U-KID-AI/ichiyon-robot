"""Synthetic private-overlay conversion; no original structures or material tiles."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import nbtlib as nbt
from PIL import Image

from build_structure_companions import BP, ROOT, SPECS, build, tumaini_behavior
from check_structure_companions import verify


class CompanionOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'inputs'
        self.cache = self.root / 'tiles'
        self.source.mkdir()
        self.cache.mkdir()
        fixture = nbt.File({'size': nbt.List[nbt.Int]([2, 1, 1]),
            'structure': nbt.Compound({
                'block_indices': nbt.List[nbt.List[nbt.Int]]([nbt.List[nbt.Int]([0, 0])]),
                'palette': nbt.Compound({'default': nbt.Compound({
                    'block_palette': nbt.List[nbt.Compound]([
                        nbt.Compound({'name': nbt.String('minecraft:stone'), 'states': nbt.Compound()})])})})})})
        for filename, _ in SPECS.values():
            fixture.save(self.source / filename, gzipped=False, byteorder='little')
        tile = self.cache / 'stone.png'
        Image.new('RGBA', (16, 16), (12, 34, 56, 255)).save(tile)
        faces = ('north', 'south', 'east', 'west', 'up', 'down')
        digest = hashlib.sha256(tile.read_bytes()).hexdigest()
        mapping = {'stone': {**{face: tile.name for face in faces},
                             'sha256': {face: digest for face in faces}}}
        (self.cache / 'materials.json').write_text(json.dumps(mapping), encoding='utf-8')
        self.output = self.root / 'overlay'

    def run_build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            build(self.source, self.cache, self.root / 'proof.json', self.output)

    def test_generated_overlay_roundtrips_without_changing_public_packs_or_inputs(self):
        inputs = {p: p.read_bytes() for p in self.source.iterdir()}
        public = {p: p.read_bytes() for p in (BP / 'entities/taketumi.json', BP / 'entities/avatar.json')}
        self.run_build()
        for key in SPECS:
            result = verify(key, self.source, self.cache, self.output)
            self.assertTrue(result['geometry_exact_at_rest'])
            self.assertTrue(result['source_unchanged'])
        self.assertEqual(inputs, {p: p.read_bytes() for p in inputs})
        self.assertEqual(public, {p: p.read_bytes() for p in public})
        meta = json.loads((self.output / 'integration.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['bridge_import'], 'import "./ikarun.js";')
        self.assertFalse(any(p.endswith(('main.js', 'manifest.json')) for p in meta['files']))
        self.assertIn('behavior_packs/ichiyon_avatar_bp/entities/ikarun.json', meta['files'])
        self.assertIn('behavior_packs/import_structures/scripts/ikarun_core.js', meta['files'])
        self.assertEqual(json.loads((self.output / 'behavior_packs/ichiyon_avatar_bp/entities/tumaini.json').read_text()), tumaini_behavior())

    def test_existing_candidate_is_never_overwritten(self):
        self.output.mkdir()
        marker = self.output / 'keep.txt'
        marker.write_text('previous candidate', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            self.run_build()
        self.assertEqual(marker.read_text(), 'previous candidate')

    def test_public_pack_output_is_rejected_before_writes(self):
        with self.assertRaises(ValueError):
            build(self.source, self.cache, self.root / 'proof.json', ROOT / 'minecraft')

    def test_changed_material_tile_is_rejected(self):
        Image.new('RGBA', (16, 16), (77, 88, 99, 255)).save(self.cache / 'stone.png')
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            self.run_build()
        self.assertFalse((self.root / 'proof.json').exists())


if __name__ == '__main__':
    unittest.main()
