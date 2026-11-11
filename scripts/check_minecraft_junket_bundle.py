"""Regression checks for private bundle entity schema normalization."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / 'minecraft'))
from build_junket_all17_bundle import omit_empty_entity_properties, overlay_entities, validate


class EntityPropertiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.bp = self.root / 'bp'
        self.rp = self.root / 'rp'

    def entity(self, name, properties):
        value = {'format_version': '1.21.0', 'minecraft:entity': {
            'description': {'identifier': 'junket:' + name, 'properties': properties},
            'components': {'minecraft:persistent': {}, 'minecraft:collision_box': {'width': 0.6, 'height': 0.6}}}}
        path = self.source / 'entities' / (name + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path, value

    def test_output_omits_empty_properties_without_changing_source_or_components(self):
        source, original = self.entity('diamond', {})
        before = source.read_bytes()
        overlay_entities(self.source, self.bp)
        self.assertEqual(omit_empty_entity_properties(self.bp), ['junket:diamond'])
        actual = json.loads((self.bp / 'entities/diamond.json').read_text(encoding='utf-8'))
        expected = deepcopy(original)
        del expected['minecraft:entity']['description']['properties']
        self.assertEqual(actual, expected)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(omit_empty_entity_properties(self.bp), [])

    def test_nonempty_properties_remain_byte_identical(self):
        source, _ = self.entity('card', {'junket:frame': {'type': 'int', 'range': [0, 15], 'default': 0, 'client_sync': True}})
        overlay_entities(self.source, self.bp)
        self.assertEqual(omit_empty_entity_properties(self.bp), [])
        self.assertEqual((self.bp / 'entities/card.json').read_bytes(), source.read_bytes())

    def test_validator_rejects_empty_schema_then_accepts_normalized_entity(self):
        self.entity('partition', {})
        overlay_entities(self.source, self.bp)
        with self.assertRaisesRegex(ValueError, 'Empty entity properties: junket:partition'):
            validate(self.bp, self.rp)
        client = self.rp / 'entity/partition.json'
        client.parent.mkdir(parents=True)
        client.write_text(json.dumps({'minecraft:client_entity': {'description': {'identifier': 'junket:partition'}}}), encoding='utf-8')
        (self.rp / 'manifest.json').write_text(json.dumps({
            'format_version': 2,
            'header': {'name': 'Synthetic bundle fixture',
                       'uuid': '10000000-0000-4000-8000-000000000001',
                       'version': [1, 0, 0], 'min_engine_version': [1, 26, 44]},
            'modules': [{'type': 'resources',
                         'uuid': '10000000-0000-4000-8000-000000000002',
                         'version': [1, 0, 0]}],
            'capabilities': ['pbr'],
        }), encoding='utf-8')
        runtime = self.bp / 'scripts/all17_original_generated.json'
        runtime.parent.mkdir(parents=True)
        runtime.write_text(json.dumps({'venues': [{'id': 'sparrow', 'fixtures': [{'role': 'partition', 'type': 'junket:partition'}]}]}), encoding='utf-8')
        omit_empty_entity_properties(self.bp)
        self.assertEqual(validate(self.bp, self.rp)['runtime_fixtures_resolved'], 1)


if __name__ == '__main__':
    unittest.main()
