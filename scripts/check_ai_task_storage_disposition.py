"""Offline P1c-2B recovery proofs; fixture files stay in TemporaryDirectory.

No production inventory, Docker command, database connection or cleanup runs.
Descriptor/mode fixtures require POSIX; Docker-save content fixtures also run on
Windows using the existing portable regular-file reader.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import ai_task_backup as backup
import ai_task_storage_disposition as disposition
import ai_task_storage_evidence as evidence
import ai_task_storage_maintenance as maintenance
import ai_task_storage_retention as retention
from ai_task_storage_cleanup import CATEGORIES, inventory_digest, object_digest
from check_ai_task_storage_cleanup import fixture, receipt


TARGET_SHA = 'd' * 40
ARCHIVE_ID = 'b' * 64
RECEIPT_SHA = 'f' * 64


def docker_save(path, *, layer=b'fixture-layer', expected_layer=None,
                rootfs=None, use_rootfs=True, malformed_config=False):
    """Small synthetic Docker-save envelope; no Docker process is started."""
    expected_layer = layer if expected_layer is None else expected_layer
    if rootfs is None and use_rootfs:
        rootfs = dict(type='layers', diff_ids=['sha256:' + hashlib.sha256(expected_layer).hexdigest()])
    config = [] if malformed_config else dict(rootfs=rootfs)
    encoded = json.dumps(config, sort_keys=True).encode('utf-8')
    ident = 'sha256:' + hashlib.sha256(encoded).hexdigest()
    files = [('config.json', encoded), ('layer.tar', layer),
             ('manifest.json', json.dumps([dict(Config='config.json', Layers=['layer.tar'])]).encode('utf-8'))]
    with tarfile.open(path, 'w') as archive:
        for name, payload in files:
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return ident


class ImageArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'image.tar'

    def test_valid_config_and_all_layer_bytes_match_image_identity(self):
        ident = docker_save(self.path)
        disposition.image_archive_identity(self.path, ident)
        with self.assertRaises(maintenance.MaintenanceError):
            disposition.image_archive_identity(self.path, 'sha256:' + '0' * 64)

    def test_tampered_layer_is_rejected_even_with_original_config(self):
        ident = docker_save(self.path, layer=b'replaced', expected_layer=b'original')
        with self.assertRaises(maintenance.MaintenanceError):
            disposition.image_archive_identity(self.path, ident)

    def test_layer_count_mismatch_is_rejected(self):
        ident = docker_save(self.path, rootfs=dict(type='layers', diff_ids=[]))
        with self.assertRaises(maintenance.MaintenanceError):
            disposition.image_archive_identity(self.path, ident)

    def test_null_rootfs_and_non_object_config_are_fixed_diagnostics(self):
        for arguments in (dict(rootfs=None, use_rootfs=False), dict(malformed_config=True)):
            with self.subTest(arguments=arguments):
                ident = docker_save(self.path, **arguments)
                with self.assertRaises(maintenance.MaintenanceError):
                    disposition.image_archive_identity(self.path, ident)

    def test_path_escape_and_links_in_save_archive_are_rejected(self):
        for name, kind in (('../outside', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)):
            with self.subTest(name=name):
                with tarfile.open(self.path, 'w') as archive:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = '/outside'
                    archive.addfile(member)
                with self.assertRaises(maintenance.MaintenanceError):
                    disposition.image_archive_identity(self.path, 'sha256:' + 'a' * 64)


@unittest.skipUnless(os.name == 'posix', 'trusted-directory descriptors and owner modes require POSIX')
class DispositionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reviews = self.root / 'cleanup-dispositions'
        self.reviews.mkdir(mode=0o700)
        self.preserved = self.root / 'cleanup-preserved' / ARCHIVE_ID
        self.preserved.mkdir(parents=True, mode=0o700)
        self.source = self.root / ('.prepare-' + TARGET_SHA + '.abcdefgh')
        self.source.mkdir(mode=0o700)
        (self.source / 'payload.txt').write_bytes(b'independently recoverable fixture\n')
        (self.source / 'payload.txt').chmod(0o600)
        self.payload = self.preserved / 'payload'
        self.rehearsal = self.preserved / 'rehearsal'
        shutil.copytree(self.source, self.payload)
        shutil.copytree(self.source, self.rehearsal)
        self.addCleanup(patch.stopall)
        patch.object(disposition, 'ROOT', self.root).start()
        patch.object(evidence, '_uid', return_value=os.getuid()).start()
        # Only our exact temporary object is in scope. No test can authorize a
        # production path by broadening the global protocol validator.
        patch.object(disposition, '_target_valid', side_effect=self.in_fixture_scope).start()
        self.configure_snapshot()
        self.publish_reviews()

    def in_fixture_scope(self, category, node):
        return category == 'images' and node.get('path') is None and node.get('id') == self.node['id'] or \
            category != 'images' and node.get('path') == str(self.source)

    def configure_snapshot(self, category='staging', *, ready=False):
        self.snapshot = fixture()
        for name in CATEGORIES:
            self.snapshot[name] = []
        self.category = category
        self.node = dict(id=str(self.source) if category == 'staging' else TARGET_SHA,
                         path=str(self.source), operation_sha=TARGET_SHA, target_release=TARGET_SHA,
                         ready=ready, validation='incomplete' if category == 'staging' else 'verified',
                         managed=True, references_complete=True, backup_format_version=2,
                         lock_held_during_observation=False)
        self.snapshot[category] = [self.node]
        self.record = receipt(category, self.node)
        self.snapshot['cleanup_evidence']['records'] = [self.record]
        self.snapshot['durable_operations'] = dict(operations=[dict(
            operation_id=self.record['operation']['id'], terminal=True, receipt_sha256=RECEIPT_SHA)])
        self.snapshot['cleanup_evidence']['inventory_sha256'] = inventory_digest(self.snapshot)

    def write_envelope(self, path, body, *, checksum=None):
        envelope = dict(body=body, sha256=maintenance.digest(body) if checksum is None else checksum)
        path.write_text(json.dumps(envelope, sort_keys=True), encoding='utf-8')
        path.chmod(0o600)
        return envelope

    def publish_reviews(self, *, proof_changes=None, review_changes=None):
        self.binding = dict(category=self.category, id=self.node['id'],
            operation_id=self.record['operation']['id'], receipt_sha256=RECEIPT_SHA,
            object_sha256=disposition.review_object_digest(self.node))
        if self.category == 'images':
            content = retention.digest_file(self.preserved / 'image.tar')
        else:
            content = maintenance.digest(disposition.content_inventory(maintenance.target_tree(self.source)))
        self.proof = dict(binding=copy.deepcopy(self.binding), content_sha256=content,
            restored_content_sha256=content, database_restore_verified=True, image_restore_verified=True)
        self.proof.update(proof_changes or {})
        verification = self.write_envelope(self.preserved / 'verification.json', self.proof)
        self.review = dict(version=1, binding=copy.deepcopy(self.binding), archive_id=ARCHIVE_ID,
            content_sha256=content, verification_sha256=verification['sha256'], unique_data=False,
            incident_review_complete=True, disposition_validation='independent_restore_rehearsal')
        self.review.update(review_changes or {})
        self.review_path = self.reviews / (maintenance.digest([self.category, self.node['id']]) + '.json')
        self.write_envelope(self.review_path, self.review)

    def attest(self):
        disposition.attest_recovery_dispositions(self.snapshot)
        self.assertEqual(self.snapshot['cleanup_evidence']['inventory_sha256'], inventory_digest(self.snapshot))
        return self.record

    def assert_no_authority(self):
        self.assertIs(self.record['content_inventory_verified'], False)
        self.assertIsNot(self.record['recovery'].get('evidence_preserved'), True)
        self.assertFalse(any(self.record.get('backup_validation', {}).values()))

    def assert_invalid(self):
        self.attest()
        self.assert_no_authority()
        self.assertIs(self.snapshot['cleanup_evidence']['source_verified'], False)
        self.assertIn('DISPOSITION_INVALID_OR_CHANGED', self.snapshot['recovery_disposition_errors'])

    def test_real_source_preserved_and_restored_hashes_complete_bound_proof(self):
        record = self.attest()
        self.assertIs(record['content_inventory_verified'], True)
        self.assertIs(record['recovery']['evidence_preserved'], True)
        self.assertEqual(record['content_sha256'], self.review['content_sha256'])
        self.assertEqual(record['recovery']['proof_sha256'], self.review['verification_sha256'])
        self.assertIs(self.snapshot['cleanup_evidence']['source_verified'], True)
        self.assertEqual(self.snapshot['recovery_disposition_errors'], [])

    def test_missing_review_clears_prior_authority_and_self_flags(self):
        self.attest()
        self.assertIs(self.record['content_inventory_verified'], True)
        self.review_path.unlink()
        self.attest()
        self.assert_no_authority()
        self.assertEqual(self.snapshot['recovery_disposition_errors'], [])

    def test_maintenance_lock_observation_does_not_invalidate_reviewed_target(self):
        # Isolate target identity serialization; the fresh eligibility classifier
        # is exercised independently by the maintenance/cleanup fixture suites.
        def eligible(snapshot):
            nodes = {category: [] for category in CATEGORIES}
            nodes['staging'] = [dict(id=self.node['id'], classification='SAFE_TO_CLEAN')]
            return dict(cleanup=dict(nodes=nodes))
        with patch.object(maintenance, 'build_plan', side_effect=eligible) as classifier, \
             patch.object(maintenance, '_target_valid', side_effect=self.in_fixture_scope):
            self.attest()
            approved = maintenance.maintenance_plan(self.snapshot)
            raw_before = self.record['object_sha256']
            self.node['lock_held_during_observation'] = True
            self.record['object_sha256'] = object_digest(self.node)
            self.attest()
            fresh = maintenance.maintenance_plan(self.snapshot)
        self.assertNotEqual(raw_before, self.record['object_sha256'])
        self.assertIs(self.record['content_inventory_verified'], True)
        self.assertEqual(approved['body']['targets'], fresh['body']['targets'])
        self.assertNotEqual(approved['body']['inventory_sha256'], fresh['body']['inventory_sha256'])
        self.assertEqual(classifier.call_count, 2)

    def test_changes_other_than_lock_observation_invalidate_review(self):
        for name, value in (('validation', 'invalid'), ('active', True),
                            ('allocated_bytes', 123), ('recovery_archive_ids', ['c' * 64])):
            with self.subTest(field=name):
                self.configure_snapshot()
                self.publish_reviews()
                self.node[name] = value
                self.record['object_sha256'] = object_digest(self.node)
                self.assert_invalid()

    def test_invalid_review_checksum_is_never_accepted(self):
        self.write_envelope(self.review_path, self.review, checksum='0' * 64)
        self.assert_invalid()

    def test_duplicate_review_keys_cannot_override_operator_decisions(self):
        checksum = maintenance.digest(self.review)
        self.review_path.write_text('{"body":' + json.dumps(self.review) + ',"sha256":"' +
            checksum + '","sha256":"' + checksum + '"}', encoding='utf-8')
        self.assert_invalid()

    def test_review_binding_must_match_exact_object_and_receipt(self):
        self.review['binding']['receipt_sha256'] = '0' * 64
        self.write_envelope(self.review_path, self.review)
        self.assert_invalid()

    def test_missing_operator_decisions_do_not_authorize_cleanup(self):
        self.review.update(unique_data=None, incident_review_complete=False, disposition_validation=None)
        self.write_envelope(self.review_path, self.review)
        self.assert_invalid()

    def test_copies_of_the_same_inode_are_not_independent_rehearsals(self):
        tree = maintenance.target_tree(self.source)
        with patch.object(maintenance, 'target_tree', return_value=tree):
            self.assert_invalid()

    def test_changed_source_or_rehearsal_bytes_reject_original_review(self):
        for path in (self.source / 'payload.txt', self.rehearsal / 'payload.txt'):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(b'changed data')
                self.assert_invalid()
                path.write_bytes(original)
                self.snapshot['cleanup_evidence']['source_verified'] = True

    def test_source_change_during_proof_is_detected_by_second_observation(self):
        real_tree = maintenance.target_tree
        calls = 0
        def racing_tree(path):
            nonlocal calls
            result = real_tree(path)
            if Path(path) == self.source:
                calls += 1
                if calls == 1:
                    (self.source / 'payload.txt').write_bytes(b'changed after first read')
            return result
        with patch.object(maintenance, 'target_tree', side_effect=racing_tree):
            self.assert_invalid()

    def test_malformed_verification_body_is_a_fixed_failure(self):
        verification = self.write_envelope(self.preserved / 'verification.json', [])
        self.review['verification_sha256'] = verification['sha256']
        self.write_envelope(self.review_path, self.review)
        self.assert_invalid()

    def test_review_symlink_and_unsafe_mode_are_rejected(self):
        original = self.review_path.read_bytes()
        sibling = self.root / 'review-copy.json'
        sibling.write_bytes(original)
        sibling.chmod(0o600)
        self.review_path.unlink()
        self.review_path.symlink_to(sibling)
        self.assert_invalid()
        self.review_path.unlink()
        self.review_path.write_bytes(original)
        self.review_path.chmod(0o644)
        self.snapshot['cleanup_evidence']['source_verified'] = True
        self.assert_invalid()

    def test_verified_complete_backup_uses_independent_parser_and_contract(self):
        self.configure_snapshot('backups', ready=True)
        self.publish_reviews()
        proof = dict(format_version=2, checksum_verified=True, pg_restore_list=True,
                     persistence_tar_verified=True)
        with patch.object(backup, 'validate_backup', return_value=proof) as validate:
            self.attest()
        validate.assert_called_once_with(self.source,
            releases_root=Path('/home/ubuntu/ichiyon-releases'),
            recovery_root=Path('/home/ubuntu/ichiyon-recovery-archives'),
            dump_validator=retention.production_dump_list)
        self.assertTrue(all(self.record['backup_validation'].values()))

    def test_complete_backup_without_database_rehearsal_cannot_be_attested(self):
        self.configure_snapshot('backups', ready=True)
        self.publish_reviews(proof_changes=dict(database_restore_verified=False))
        with patch.object(backup, 'validate_backup') as validate:
            self.assert_invalid()
        validate.assert_not_called()

    def test_backup_validator_failure_does_not_leave_partial_authority(self):
        self.configure_snapshot('backups', ready=True)
        self.publish_reviews()
        with patch.object(backup, 'validate_backup', side_effect=backup.BackupError('invalid_backup')):
            self.assert_invalid()

    def test_ready_backup_staging_uses_target_sha_and_protocol_owner(self):
        self.source.rename(self.root / ('.backup-' + TARGET_SHA + '.abcdefgh'))
        self.source = self.root / ('.backup-' + TARGET_SHA + '.abcdefgh')
        self.configure_snapshot(ready=True)
        # Real staging observation remains incomplete; only the independent
        # staged validator establishes completed-backup obligations.
        self.node.pop('backup_format_version')
        self.publish_reviews()
        proof = dict(format_version=2, checksum_verified=True, pg_restore_list=True,
                     persistence_tar_verified=True)
        with patch.object(backup, 'validate_staged_backup', return_value=proof) as validate:
            self.attest()
        validate.assert_called_once_with(self.source, TARGET_SHA,
            releases_root=Path('/home/ubuntu/ichiyon-releases'),
            recovery_root=Path('/home/ubuntu/ichiyon-recovery-archives'),
            dump_validator=retention.production_dump_list, expected_uid=os.getuid())
        self.assertIs(self.record['content_inventory_verified'], True)
        self.assertTrue(all(self.record['backup_validation'].values()))

    def test_explicit_staging_owner_preserves_default_owner_enforcement(self):
        stage = self.root / ('.backup-' + TARGET_SHA + '.abcdefgh')
        self.source.rename(stage)
        owner = os.getuid()
        # Simulate a privileged frontend without chown or privilege changes.
        # Only the explicitly supplied protocol owner may replace euid checks.
        with patch.object(backup.os, 'geteuid', return_value=owner + 1):
            with self.assertRaises(backup.BackupError):
                backup.stage_directory(stage, TARGET_SHA, self.root)
            self.assertEqual(backup.stage_directory(stage, TARGET_SHA, self.root,
                             expected_uid=owner), (stage, self.root))
            for invalid in (owner + 2, True, -1):
                with self.subTest(invalid=invalid), self.assertRaises(backup.BackupError):
                    backup.stage_directory(stage, TARGET_SHA, self.root, expected_uid=invalid)

    def test_real_image_save_archive_completes_bound_recovery_proof(self):
        self.configure_snapshot('images')
        self.node['id'] = docker_save(self.preserved / 'image.tar')
        self.node['path'] = None
        self.record['id'] = self.node['id']
        self.record['object_sha256'] = object_digest(self.node)
        self.publish_reviews()
        self.attest()
        self.assertIs(self.record['content_inventory_verified'], True)
        self.assertIs(self.record['recovery']['evidence_preserved'], True)

    def test_image_archive_change_during_validation_is_detected(self):
        self.configure_snapshot('images')
        self.node['id'] = docker_save(self.preserved / 'image.tar')
        self.node['path'] = None
        self.record['id'] = self.node['id']
        self.record['object_sha256'] = object_digest(self.node)
        self.publish_reviews()
        real_validator = disposition.image_archive_identity
        def racing_archive(path, ident):
            real_validator(path, ident)
            with path.open('ab') as stream:
                stream.write(b'changed after independent archive inspection')
        with patch.object(disposition, 'image_archive_identity', side_effect=racing_archive):
            self.assert_invalid()


if __name__ == '__main__':
    unittest.main(verbosity=2)
