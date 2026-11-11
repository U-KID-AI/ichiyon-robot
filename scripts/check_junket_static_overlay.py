"""Static asset deltas must survive DB recompiles without replacing DB content."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/minecraft'))
from check_minecraft_junket_overlay import OverlayChecks
import minecraft_cosmetics_apply as deploy
from minecraft_junket_overlay import JUNKET_PACKS, preserve_overlay
from junket_static_overlay import (CATALOG, MAIN, apply_overlay, digest,
                                   load_overlay, plan_overlay, prepare_native_overlay,
                                   verify_native_stage)

BP = 'behavior_packs/ichiyon_avatar_bp'
RP = 'resource_packs/ichiyon_test_rp'
ITEM = BP + '/items/record_new.json'
ATLAS = RP + '/textures/item_texture.json'
LANG = RP + '/texts/ja_JP.lang'
MODULE = 'behavior_packs/import_structures/scripts/new_feature.js'


class StaticOverlayChecks(unittest.TestCase):
    make_pack = OverlayChecks.make_pack

    def setUp(self):
        OverlayChecks.setUp(self)
        self.payload = self.source.parent / 'static-overlay'
        self.old_row = {'itemId': 'ichiyon:record_old', 'soundId': 'ichiyon:record.old', 'durationSeconds': 10}
        self.new_row = {'itemId': 'ichiyon:record_new', 'soundId': 'ichiyon:record.new', 'durationSeconds': 20}
        deploy.atomic_json(self.stage / ATLAS, {'resource_pack_name': 'DB', 'texture_data': {'db_hat': {'textures': 'textures/db_hat'}}})
        (self.stage / LANG).parent.mkdir(parents=True)
        (self.stage / LANG).write_bytes('## DB labels\r\nitem.db_hat=帽子'.encode())
        (self.stage / MAIN).write_text('import "./old_feature.js";\nconsole.warn("keep");\n', encoding='utf-8')
        (self.stage / CATALOG).write_text('// Generated catalog\nexport const molcarRecords = ' + json.dumps([self.old_row]) + ';\n', encoding='utf-8')
        self.operations = [
            {'kind': 'file', 'path': ITEM, 'sha256': digest(b'{"new":true}\n')},
            {'kind': 'file', 'path': MODULE, 'sha256': digest(b'// new feature\n')},
            {'kind': 'json_keys', 'path': ATLAS, 'object_path': ['texture_data'],
             'changes': {'new_record': {'value': {'textures': 'textures/new_record'}}}},
            {'kind': 'lang', 'path': LANG, 'changes': {'item.new_record': {'value': '新曲'}}},
            {'kind': 'js_catalog', 'path': CATALOG, 'changes': {self.new_row['itemId']: {'value': self.new_row}}},
            {'kind': 'js_imports', 'path': MAIN, 'imports': ['./new_feature.js']},
        ]
        self.files = {ITEM: b'{"new":true}\n', MODULE: b'// new feature\n'}

    def payload_hash(self, operations=None):
        for relative, data in self.files.items():
            path = self.payload / 'files' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        deploy.atomic_json(self.payload / 'manifest.json', {'schema': 1, 'operations': operations or self.operations})
        return digest((self.payload / 'manifest.json').read_bytes())

    def snapshot(self):
        return {p.relative_to(self.stage).as_posix(): p.read_bytes() for p in self.stage.rglob('*') if p.is_file()}

    def test_adds_every_kind_and_preserves_unknown_db_entries_and_source(self):
        expected = self.payload_hash()
        source_before = {p: p.read_bytes() for p in self.payload.rglob('*') if p.is_file()}
        result = apply_overlay(self.payload, self.stage, expected)
        self.assertEqual(len(result['files']), 6)
        atlas = deploy.read_json(self.stage / ATLAS)
        self.assertEqual(atlas['resource_pack_name'], 'DB')
        self.assertEqual(atlas['texture_data']['db_hat'], {'textures': 'textures/db_hat'})
        self.assertEqual((self.stage / LANG).read_text(encoding='utf-8'), '## DB labels\nitem.db_hat=帽子\nitem.new_record=新曲\n')
        main = (self.stage / MAIN).read_text()
        self.assertIn('import "./old_feature.js";', main)
        self.assertEqual(main.count('import "./new_feature.js";'), 1)
        self.assertIn('"ichiyon:record_old"', (self.stage / CATALOG).read_text())
        self.assertEqual(source_before, {p: p.read_bytes() for p in self.payload.rglob('*') if p.is_file()})

    def test_repeat_is_pack_byte_idempotent(self):
        expected = self.payload_hash()
        apply_overlay(self.payload, self.stage, expected)
        before = {pack: deploy.content_hash(self.stage / pack) for pack in self.normal}
        result = apply_overlay(self.payload, self.stage, expected)
        self.assertFalse(any(item['changed'] for item in result['files']))
        self.assertEqual(before, {pack: deploy.content_hash(self.stage / pack) for pack in self.normal})

    def test_commented_import_does_not_suppress_real_feature_installation(self):
        (self.stage / MAIN).write_text('// import "./new_feature.js";\nconsole.warn("keep");\n', encoding='utf-8')
        apply_overlay(self.payload, self.stage, self.payload_hash())
        self.assertTrue((self.stage / MAIN).read_text().startswith('import "./new_feature.js";\n'))

    def test_future_db_additions_survive_reapplication(self):
        expected = self.payload_hash()
        apply_overlay(self.payload, self.stage, expected)
        atlas = deploy.read_json(self.stage / ATLAS)
        atlas['texture_data']['later_db_hat'] = {'textures': 'later_web_upload'}
        deploy.atomic_json(self.stage / ATLAS, atlas)
        (self.stage / RP / 'later_db_asset.png').write_bytes(b'db upload')
        apply_overlay(self.payload, self.stage, expected)
        self.assertEqual(deploy.read_json(self.stage / ATLAS)['texture_data']['later_db_hat'], {'textures': 'later_web_upload'})
        self.assertEqual((self.stage / RP / 'later_db_asset.png').read_bytes(), b'db upload')

    def test_unknown_same_key_conflict_is_detected_before_any_mutation(self):
        expected = self.payload_hash()
        atlas = deploy.read_json(self.stage / ATLAS)
        atlas['texture_data']['new_record'] = {'textures': 'changed_in_admin'}
        deploy.atomic_json(self.stage / ATLAS, atlas)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'conflicts with managed content'):
            apply_overlay(self.payload, self.stage, expected)
        self.assertEqual(before, self.snapshot())

    def test_file_replacement_requires_exact_before_or_already_after(self):
        old = b'old generated asset'
        (self.stage / ITEM).parent.mkdir(parents=True)
        (self.stage / ITEM).write_bytes(old)
        operations = deepcopy(self.operations)
        operations[0]['before_sha256'] = digest(old)
        expected = self.payload_hash(operations)
        apply_overlay(self.payload, self.stage, expected)
        apply_overlay(self.payload, self.stage, expected)
        (self.stage / ITEM).write_bytes(b'future edited content')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'conflicts with managed file'):
            apply_overlay(self.payload, self.stage, expected)
        self.assertEqual(before, self.snapshot())

    def test_key_update_requires_exact_before_value(self):
        operations = deepcopy(self.operations)
        operations[2]['changes'] = {'db_hat': {'before': {'textures': 'textures/db_hat'}, 'value': {'textures': 'repaired_hat'}}}
        expected = self.payload_hash(operations)
        apply_overlay(self.payload, self.stage, expected)
        self.assertEqual(deploy.read_json(self.stage / ATLAS)['texture_data']['db_hat'], {'textures': 'repaired_hat'})
        apply_overlay(self.payload, self.stage, expected)

    def test_hash_tamper_and_missing_import_block_before_changes(self):
        expected = self.payload_hash()
        (self.payload / 'files' / ITEM).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            load_overlay(self.payload, expected)
        self.payload_hash()
        before = self.snapshot()
        operations = [row for row in self.operations if row['path'] != MODULE]
        expected = self.payload_hash(operations)
        with self.assertRaisesRegex(ValueError, 'module is missing'):
            apply_overlay(self.payload, self.stage, expected)
        self.assertEqual(before, self.snapshot())

    def test_forbids_pack_replacement_db_catalog_path_traversal_and_duplicate_paths(self):
        for relative in (BP + '/manifest.json', 'behavior_packs/import_structures/scripts/cosmetics_catalog.js',
                         'behavior_packs/ichiyon_junket_bp/file.json', BP + '/../escape.json'):
            operations = [{'kind': 'file', 'path': relative, 'sha256': 'a' * 64}]
            expected = self.payload_hash(operations)
            with self.assertRaises(ValueError): load_overlay(self.payload, expected)
        expected = self.payload_hash([self.operations[0], self.operations[0]])
        with self.assertRaisesRegex(ValueError, 'exactly one'): load_overlay(self.payload, expected)

    def test_rejects_duplicate_catalog_ids_without_modifying_stage(self):
        (self.stage / CATALOG).write_text('export const molcarRecords = ' + json.dumps([self.old_row, self.old_row]) + ';', encoding='utf-8')
        expected = self.payload_hash()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Duplicate Molcar catalog'):
            apply_overlay(self.payload, self.stage, expected)
        self.assertEqual(before, self.snapshot())

    def test_pinned_native_integration_keeps_db_and_applies_private_delta(self):
        expected = self.payload_hash()
        self.marker['schema'] = 2
        self.marker['static_overlay'] = {'manifest_sha256': expected}
        deploy.atomic_json(self.project / 'junket-native-overlay.json', self.marker)
        validated = preserve_overlay(self.project)
        self.assertEqual(validated['schema'], 2)
        self.assertEqual(validated['static_overlay'], self.marker['static_overlay'])
        preserve_overlay(self.project, self.stage)
        self.assertEqual((self.stage / ITEM).read_bytes(), self.files[ITEM])
        self.assertIn('db_hat', deploy.read_json(self.stage / ATLAS)['texture_data'])
        for pack, expected_hash in self.marker['packs'].items():
            self.assertEqual(deploy.content_hash(self.stage / pack), expected_hash)

    def test_pinned_conflict_does_not_even_replace_junket_stage(self):
        expected = self.payload_hash()
        self.marker['schema'] = 2
        self.marker['static_overlay'] = {'manifest_sha256': expected}
        deploy.atomic_json(self.project / 'junket-native-overlay.json', self.marker)
        (self.stage / ITEM).parent.mkdir(parents=True)
        (self.stage / ITEM).write_bytes(b'other owner')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'conflicts with managed file'):
            preserve_overlay(self.project, self.stage)
        self.assertEqual(before, self.snapshot())

    def test_native_preparation_inherits_existing_private_pin(self):
        expected = self.payload_hash()
        self.marker['schema'] = 2
        self.marker['static_overlay'] = {'manifest_sha256': expected}
        deploy.atomic_json(self.project / 'junket-native-overlay.json', self.marker)
        next_directory = self.project / 'next-native-operation'
        pin, result = prepare_native_overlay(self.project, self.project / 'bundle', next_directory, self.stage)
        self.assertEqual(pin, self.marker['static_overlay'])
        self.assertEqual((next_directory / 'static-overlay/manifest.json').read_bytes(), (self.payload / 'manifest.json').read_bytes())
        self.assertEqual((self.stage / ITEM).read_bytes(), self.files[ITEM])
        self.assertEqual(len(result['files']), 6)

    def test_new_delta_cannot_silently_remove_previous_owned_keys(self):
        expected = self.payload_hash()
        self.marker['schema'] = 2
        self.marker['static_overlay'] = {'manifest_sha256': expected}
        deploy.atomic_json(self.project / 'junket-native-overlay.json', self.marker)
        incoming = self.project / 'bundle/static-overlay'
        shutil.copytree(self.payload, incoming)
        manifest = deploy.read_json(incoming / 'manifest.json')
        manifest['operations'] = [row for row in manifest['operations'] if row['kind'] != 'js_catalog']
        deploy.atomic_json(incoming / 'manifest.json', manifest)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'drops an existing owned path'):
            prepare_native_overlay(self.project, incoming.parent, self.project / 'next', self.stage)
        self.assertEqual(before, self.snapshot())

    def test_schema_two_without_static_pin_cannot_silently_become_two_pack_overlay(self):
        self.marker['schema'] = 2
        deploy.atomic_json(self.project / 'junket-native-overlay.json', self.marker)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'schema 2 requires static_overlay'):
            preserve_overlay(self.project, self.stage)
        with self.assertRaisesRegex(ValueError, 'schema 2 requires static_overlay'):
            prepare_native_overlay(self.project, self.project / 'bundle', self.project / 'next', self.stage)
        self.assertEqual(before, self.snapshot())

    def test_static_pin_cannot_be_downgraded_to_legacy_schema(self):
        self.marker['static_overlay'] = {'manifest_sha256': self.payload_hash()}
        deploy.atomic_json(self.project / 'junket-native-overlay.json', self.marker)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'requires pinned JUNKET overlay schema 2'):
            preserve_overlay(self.project, self.stage)
        with self.assertRaisesRegex(ValueError, 'requires pinned JUNKET overlay schema 2'):
            prepare_native_overlay(self.project, self.project / 'bundle', self.project / 'next', self.stage)
        self.assertEqual(before, self.snapshot())

    def native_proof(self):
        shutil.copytree(self.stage, self.live)
        baseline = {pack: deploy.content_hash(self.live / pack) for pack in self.normal}
        result = apply_overlay(self.payload, self.stage, self.payload_hash())
        return baseline, result

    def test_native_proof_confirms_exact_files_and_original_pack_semantics(self):
        baseline, result = self.native_proof()
        deploy.prepare_manifests(self.stage, self.live)
        proof = verify_native_stage(self.stage, self.live, baseline, result)
        self.assertEqual(set(proof['static_overlay_packs']), set(self.normal))
        self.assertEqual(proof['static_overlay_packs'][BP]['before_content_hash'], baseline[BP])

    def test_native_proof_rejects_unlisted_change_inside_a_patched_pack(self):
        baseline, result = self.native_proof()
        (self.stage / RP / 'content.txt').write_text('wrong replacement')
        with self.assertRaisesRegex(ValueError, 'Undeclared managed file'):
            verify_native_stage(self.stage, self.live, baseline, result)

    def test_native_proof_rejects_manifest_identity_change_even_in_patched_pack(self):
        baseline, result = self.native_proof()
        manifest = deploy.read_json(self.stage / BP / 'manifest.json')
        manifest['header']['name'] = 'unexpected metadata replacement'
        deploy.atomic_json(self.stage / BP / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'identity or dependency'):
            verify_native_stage(self.stage, self.live, baseline, result)

    def revision_fixture(self):
        old_hash = self.payload_hash()
        self.fresh = self.project / 'fresh-managed-archive'
        shutil.copytree(self.stage, self.fresh)
        apply_overlay(self.payload, self.stage, old_hash)
        self.marker['schema'] = 2
        self.marker['static_overlay'] = {'manifest_sha256': old_hash}
        deploy.atomic_json(self.project / 'junket-native-overlay.json', self.marker)
        incoming = self.project / 'revision-bundle/static-overlay'
        shutil.copytree(self.payload, incoming)
        manifest = deploy.read_json(incoming / 'manifest.json')
        revised = b'{"new":true,"visual_fix":true}\n'
        item = next(row for row in manifest['operations'] if row['path'] == ITEM)
        manifest['owned_file_revision'] = {'previous_manifest_sha256': old_hash,
                                           'files': {ITEM: item['sha256']}}
        item['reviewed_before_sha256'] = [item['sha256']]
        item['sha256'] = digest(revised)
        (incoming / 'files' / ITEM).write_bytes(revised)
        deploy.atomic_json(incoming / 'manifest.json', manifest)
        return incoming, manifest, revised

    def test_reviewed_revision_changes_only_allowed_file_and_remains_idempotent(self):
        incoming, manifest, revised = self.revision_fixture()
        before = self.snapshot()
        pin, result = prepare_native_overlay(self.project, incoming.parent, self.project / 'reviewed-operation', self.stage)
        after = self.snapshot()
        self.assertEqual({path for path in before.keys() | after.keys() if before.get(path) != after.get(path)},
                         {ITEM, 'cosmetics-build.json'})
        self.assertEqual(after[ITEM], revised)
        self.assertEqual(result['owned_file_revision'], manifest['owned_file_revision'])
        repeated = apply_overlay(incoming, self.stage, pin['manifest_sha256'])
        self.assertFalse(any(row['changed'] for row in repeated['files']))
        # Future managed builds may omit these private additions entirely. The
        # exact reviewed final payload is restored without depending on Git audio.
        apply_overlay(incoming, self.fresh, pin['manifest_sha256'])
        self.assertEqual((self.fresh / ITEM).read_bytes(), revised)
        self.assertEqual(deploy.read_json(self.fresh / ATLAS)['texture_data']['db_hat'], {'textures': 'textures/db_hat'})

    def test_reviewed_revision_rejects_wrong_pin_old_hash_extra_history_and_guards(self):
        incoming, original, _ = self.revision_fixture()
        cases = ('wrong_pin', 'wrong_old_payload', 'extra_history', 'changed_original_guard')
        for case in cases:
            manifest = deepcopy(original)
            item = next(row for row in manifest['operations'] if row['path'] == ITEM)
            if case == 'wrong_pin':
                manifest['owned_file_revision']['previous_manifest_sha256'] = 'f' * 64
            elif case == 'wrong_old_payload':
                manifest['owned_file_revision']['files'][ITEM] = 'f' * 64
                item['reviewed_before_sha256'] = ['f' * 64]
            elif case == 'extra_history':
                item['reviewed_before_sha256'].append('f' * 64)
            else:
                item['before_sha256'] = 'f' * 64
            deploy.atomic_json(incoming / 'manifest.json', manifest)
            before = self.snapshot()
            with self.subTest(case=case), self.assertRaises(ValueError):
                prepare_native_overlay(self.project, incoming.parent, self.project / ('rejected-' + case), self.stage)
            self.assertEqual(before, self.snapshot())

    def test_reviewed_revision_rejects_any_unapproved_registry_or_file_change_or_operation_loss(self):
        incoming, original, _ = self.revision_fixture()
        for case in ('registry', 'file', 'drop', 'add'):
            manifest = deepcopy(original)
            if case == 'registry':
                operation = next(row for row in manifest['operations'] if row['path'] == ATLAS)
                operation['changes']['unapproved'] = {'value': {'textures': 'other'}}
            elif case == 'file':
                operation = next(row for row in manifest['operations'] if row['path'] == MODULE)
                changed = b'// unapproved runtime edit\n'
                operation['sha256'] = digest(changed)
                (incoming / 'files' / MODULE).write_bytes(changed)
            elif case == 'drop':
                manifest['operations'] = [row for row in manifest['operations'] if row['path'] != LANG]
            else:
                manifest['operations'].append({'kind': 'lang', 'path': RP + '/texts/new.lang',
                                               'changes': {'unapproved': {'value': 'new'}}})
            deploy.atomic_json(incoming / 'manifest.json', manifest)
            before = self.snapshot()
            with self.subTest(case=case), self.assertRaises(ValueError):
                prepare_native_overlay(self.project, incoming.parent, self.project / ('unapproved-' + case), self.stage)
            self.assertEqual(before, self.snapshot())
            (incoming / 'files' / MODULE).write_bytes(self.files[MODULE])

    def test_reviewed_revision_requires_original_payload_and_rejects_unknown_installed_file(self):
        incoming, _, _ = self.revision_fixture()
        original = (self.stage / ITEM).read_bytes()
        for case in ('installed_edited', 'source_missing'):
            if case == 'installed_edited':
                (self.stage / ITEM).write_bytes(b'unknown installed edit')
            else:
                (self.payload / 'files' / ITEM).unlink()
            before = self.snapshot()
            with self.subTest(case=case), self.assertRaises((ValueError, FileNotFoundError)):
                prepare_native_overlay(self.project, incoming.parent, self.project / case, self.stage)
            self.assertEqual(before, self.snapshot())
            (self.stage / ITEM).write_bytes(original)
            (self.payload / 'files' / ITEM).write_bytes(self.files[ITEM])

    def test_reviewed_revision_accepts_regenerated_base_without_private_addition(self):
        incoming, _, revised = self.revision_fixture()
        self.assertFalse((self.fresh / ITEM).exists())
        _, result = prepare_native_overlay(self.project, incoming.parent, self.project / 'regenerated-native', self.fresh)
        self.assertEqual((self.fresh / ITEM).read_bytes(), revised)
        proof = next(row for row in result['files'] if row['path'] == ITEM)
        self.assertIsNone(proof['before_sha256'])

    def test_reviewed_revision_does_not_allow_missing_required_original_base_file(self):
        original = b'original managed item'
        (self.stage / ITEM).parent.mkdir(parents=True)
        (self.stage / ITEM).write_bytes(original)
        self.operations[0]['before_sha256'] = digest(original)
        incoming, _, _ = self.revision_fixture()
        (self.fresh / ITEM).unlink()
        before = {p.relative_to(self.fresh).as_posix(): p.read_bytes() for p in self.fresh.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'conflicts with managed file'):
            prepare_native_overlay(self.project, incoming.parent, self.project / 'missing-required-base', self.fresh)
        self.assertEqual(before, {p.relative_to(self.fresh).as_posix(): p.read_bytes() for p in self.fresh.rglob('*') if p.is_file()})

    def test_reviewed_revision_rejects_tampered_new_payload_before_stage_writes(self):
        incoming, _, _ = self.revision_fixture()
        (incoming / 'files' / ITEM).write_bytes(b'tampered after review')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            prepare_native_overlay(self.project, incoming.parent, self.project / 'tampered', self.stage)
        self.assertEqual(before, self.snapshot())

    def test_future_managed_archive_rejects_unrecognized_revision_bytes(self):
        incoming, _, _ = self.revision_fixture()
        expected = digest((incoming / 'manifest.json').read_bytes())
        (self.stage / ITEM).write_bytes(b'new web-managed edit')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'conflicts with managed file'):
            apply_overlay(incoming, self.stage, expected)
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__': unittest.main()
