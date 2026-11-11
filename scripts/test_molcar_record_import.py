"""Regression checks for adding a record without replacing the existing player/catalog."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from build_molcar_record import build, read_catalog, record_identifiers, RECORD_GROUP


class RecordImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.rp = self.root / 'minecraft/resource_packs/ichiyon_avatar_rp'
        self.catalog = self.root / 'minecraft/behavior_packs/import_structures/scripts/molcar_records_catalog.js'
        self.catalog.parent.mkdir(parents=True)
        self.original = {'itemId': 'ichiyon:record_lets_cooking_molcar',
                         'soundId': 'ichiyon:record.lets_cooking_molcar', 'durationSeconds': 261.24771,
                         'audioSha256': 'unchanged-audio', 'iconSha256': 'unchanged-icon'}
        self.catalog.write_text('export const molcarRecords = ' + json.dumps([self.original]) + ';', encoding='utf-8')
        for relative, value in [('textures/item_texture.json', {'texture_data': {'other': {'textures': 'other'}}}),
                                ('sounds/sound_definitions.json', {'sound_definitions': {'other': {'sounds': ['other']}}})]:
            p = self.rp / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(value), encoding='utf-8')
        for locale in ('ja_JP', 'en_US'):
            p = self.rp / f'texts/{locale}.lang';p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('unrelated=保持\n', encoding='utf-8')
        self.audio = self.root / 'input.ogg';self.audio.write_bytes(b'test audio fixture')
        self.icon = self.root / 'icon.png';Image.new('RGBA', (2, 2), (127, 30, 80, 255)).save(self.icon)
        self.probe = patch('build_molcar_record.subprocess.check_output', return_value=json.dumps({
            'format': {'duration': '61.008'}, 'streams': [{'codec_type': 'audio'}]}))
        self.probe.start()

    def tearDown(self):
        self.probe.stop();self.directory.cleanup()

    def add(self, key='chika_molcar'):
        return build(self.audio, self.icon, self.root, write=True, key=key, display_name='モルカーレコード：地下')

    def test_upsert_preserves_existing_record_and_unrelated_registrations(self):
        result = self.add()
        self.assertEqual(read_catalog(self.catalog), [self.original, result])
        self.assertIn('other', json.loads((self.rp/'textures/item_texture.json').read_text())['texture_data'])
        self.assertIn('other', json.loads((self.rp/'sounds/sound_definitions.json').read_text())['sound_definitions'])
        self.assertIn('unrelated=保持', (self.rp/'texts/ja_JP.lang').read_text(encoding='utf-8'))

    def test_repeat_import_is_idempotent_and_keeps_audio_source(self):
        initial = hashlib.sha256(self.audio.read_bytes()).hexdigest()
        result = self.add();before = self.catalog.read_bytes();self.add()
        self.assertEqual(before, self.catalog.read_bytes())
        self.assertEqual(len(read_catalog(self.catalog)), 2)
        self.assertEqual(hashlib.sha256(self.audio.read_bytes()).hexdigest(), initial)
        self.assertEqual((self.rp/'sounds/records/chika_molcar.ogg').read_bytes(), self.audio.read_bytes())
        self.assertEqual(result['audioSha256'], initial)

    def test_three_additions_keep_existing_order_and_vanilla_record_group(self):
        for key in ('shachiku_molcar', 'kanden_molcar', 'chika_molcar'):
            self.add(key)
            path = self.root/f'minecraft/behavior_packs/ichiyon_avatar_bp/items/record_{key}.json'
            item = json.loads(path.read_text(encoding='utf-8'))['minecraft:item']
            self.assertEqual(item['description']['menu_category'], {'category': 'items', 'group': RECORD_GROUP})
            self.assertNotIn('minecraft:record', item['components'], 'Keep the existing Molcar SoundInstance player')
        self.assertEqual(len(read_catalog(self.catalog)), 4)
        self.assertEqual(read_catalog(self.catalog)[0], self.original)

    def test_invalid_keys_fail_before_any_mutation(self):
        before = self.catalog.read_bytes()
        for key in ('../escape', '', 'x/y', 'UPPER', 'x'*65, 'ichiyon:bad'):
            with self.assertRaises(ValueError):self.add(key)
        self.assertEqual(self.catalog.read_bytes(), before)

    def test_duplicate_catalog_is_rejected_before_audio_copy(self):
        self.catalog.write_text('export const molcarRecords = ' + json.dumps([self.original, self.original]) + ';')
        with self.assertRaises(ValueError):self.add()
        self.assertFalse((self.rp/'sounds/records/chika_molcar.ogg').exists())

    def test_legacy_default_identifiers_are_unchanged(self):
        self.assertEqual(record_identifiers('lets_cooking_molcar'),
                         ('ichiyon:record_lets_cooking_molcar', 'ichiyon:record.lets_cooking_molcar'))


if __name__ == '__main__':
    unittest.main()
