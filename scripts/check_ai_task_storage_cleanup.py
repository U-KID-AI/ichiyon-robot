"""Offline evidence-contract checks. No production reads, writes, or cleanup."""

import copy
import json
import unittest
from unittest.mock import patch

import ai_task_storage_cleanup as cleanup
import ai_task_storage_retention_graph as graph
from check_ai_task_storage_retention import (CURRENT, PREVIOUS, OLD, NOW,
    NO_EXTRA_RETENTION, backup, common, fixture as retention_fixture, image, image_id)


def fixture():
    snapshot = retention_fixture()
    snapshot['captured_end_at'] = NOW
    snapshot['operation_observation'] = dict(process_scan_complete=True, lock_held=False,
                                             lock_identity=[2049, 258219], operation_shas=[])
    for category in ('releases', 'backups'):
        for item in snapshot[category]:
            item['path'] = '/home/ubuntu/ichiyon-' + category + '/' + item['id']
    path = '/home/ubuntu/ichiyon-releases/.prepare-' + 'd' * 40 + '.abcdefgh'
    snapshot['staging'] = [dict(common(path), path=path, operation_sha='d' * 40,
                                validation='incomplete', ready=False)]
    snapshot['images'].append(image('e'))
    snapshot['cleanup_evidence'] = dict(
        schema_version=1, source_root=cleanup.EVIDENCE_ROOT, source_verified=True,
        observed_at=NOW, process_scan_complete=True, open_file_scan_complete=True,
        lease_scan_complete=True, migration_reference_scan_complete=True,
        reference_scan_complete=True,
        lock=dict(path=cleanup.DEPLOY_LOCK, regular_file=True, no_symlink=True,
                  identity_stable=True, kernel_scan_complete=True, inode=258219,
                  device=2049, identity=[2049, 258219], held=False, holder_pids=[]),
        records=[])
    attest(snapshot)
    return snapshot


def receipt(category, node):
    target_sha = (node['id'] if category == 'releases' else node.get('target_release')
                  if category == 'backups' else node.get('operation_sha', OLD))
    return dict(
        category=category, id=node['id'], object_sha256=cleanup.object_digest(node),
        source_path=cleanup.EVIDENCE_ROOT + '/operations/' + '1' * 32 + '.json',
        source_verified=True, source_no_symlinks=True, source_stable=True,
        source_checksum_verified=True, object_identity_stable=True,
        no_symlink_escape=True, no_mount_crossing=True, content_inventory_verified=True,
        content_sha256='3' * 64, open_paths=[], active_processes=[],
        format_validation='legacy_verified' if node.get('validation') == 'legacy' else 'current_verified',
        image_identity_verified=True,
        operation=dict(id='1' * 32, ownership_verified=True,
            boot_id='12345678-1234-1234-1234-123456789abc', owner_pid=123,
            owner_start_ticks=456, target_sha=target_sha,
            state='failed', terminal_receipt_verified=True,
            owner_process_state='terminated_identity_verified', lease_state='terminal_verified',
            created_at=NOW - 42 * 86400, completed_at=NOW - 40 * 86400),
        recovery=dict(unique_data=False, evidence_preserved=True, incident_review_complete=True,
            disposition_validation='independent_restore_rehearsal', proof_sha256='4' * 64),
        backup_validation=dict(ready=True, checksum_manifest=True, checksums_match=True,
            pg_restore_list=True, tar_complete=True, previous_reference=True, rollback_contract=True))


def attest(snapshot):
    evidence = snapshot['cleanup_evidence']
    evidence['records'] = [receipt(category, node) for category in cleanup.CATEGORIES
                           for node in snapshot[category]]
    evidence['inventory_sha256'] = cleanup.inventory_digest(snapshot)
    return snapshot


def plan(snapshot, policy=None):
    return graph.build_plan(snapshot, NO_EXTRA_RETENTION if policy is None else policy)


def node(result, category='staging', identity=None):
    nodes = result['cleanup']['nodes'][category]
    return nodes[0] if identity is None else next(item for item in nodes if item['id'] == identity)


def record(snapshot, category='staging', identity=None):
    return next(item for item in snapshot['cleanup_evidence']['records']
                if item['category'] == category and (identity is None or item['id'] == identity))


class CleanupEvidenceTests(unittest.TestCase):
    def test_complete_terminal_staging_proposal_is_safe_but_never_authorized(self):
        result = plan(fixture())
        self.assertEqual(node(result)['classification'], 'SAFE_TO_CLEAN')
        self.assertEqual(result['nodes']['staging'][0]['classification'], 'NEEDS_REVIEW')
        self.assertTrue(result['cleanup']['read_only'])
        self.assertTrue(result['cleanup']['plan_only'])
        self.assertFalse(result['cleanup']['execution_authorized'])
        self.assertIn('recollect', result['cleanup']['future_execution_requirement'])

    def test_all_three_staging_types_require_the_same_terminal_proof(self):
        for parent, kind in (('releases', 'prepare'), ('releases', 'release'), ('backups', 'backup')):
            with self.subTest(kind=kind):
                snapshot = fixture()
                stage = snapshot['staging'][0]
                stage['id'] = stage['path'] = '/home/ubuntu/ichiyon-' + parent + '/.' + kind + '-' + 'd' * 40 + '.abcdefgh'
                attest(snapshot)
                self.assertEqual(node(plan(snapshot))['classification'], 'SAFE_TO_CLEAN')

    def test_existing_orphan_without_evidence_is_review_even_if_ancient(self):
        snapshot = fixture()
        snapshot.pop('cleanup_evidence')
        snapshot['staging'][0]['created_at'] = 0
        snapshot['staging'][0]['ctime'] = 0
        result = node(plan(snapshot))
        self.assertEqual(result['classification'], 'NEEDS_REVIEW')
        self.assertIn('OWNERSHIP_AND_TERMINAL_RECEIPT_UNAVAILABLE', result['reasons'])

    def test_missing_and_forged_true_flags_cannot_complete_proof(self):
        for name in ('source_verified', 'process_scan_complete', 'open_file_scan_complete',
                     'lease_scan_complete', 'migration_reference_scan_complete', 'reference_scan_complete'):
            for invalid in (None, False, 'true', 1):
                with self.subTest(name=name, value=invalid):
                    snapshot = fixture()
                    snapshot['cleanup_evidence'][name] = invalid
                    self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_schema_boolean_is_not_version_one(self):
        snapshot = fixture()
        snapshot['cleanup_evidence']['schema_version'] = True
        self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_external_evidence_paths_and_unknown_protocol_targets_are_rejected(self):
        for path in ('/tmp/receipt.json', cleanup.EVIDENCE_ROOT + '/../receipt.json',
                     cleanup.EVIDENCE_ROOT + '/operations/../../receipt.json'):
            with self.subTest(path=path):
                snapshot = fixture()
                record(snapshot)['source_path'] = path
                self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')
        for path in ('/tmp/.prepare-' + 'd' * 40 + '.abcdefgh',
                     '/home/ubuntu/ichiyon-releases/../.prepare-' + 'd' * 40 + '.abcdefgh',
                     '/home/ubuntu/.ichiyon-deploy-helper.abcdefgh.py'):
            with self.subTest(path=path):
                snapshot = fixture()
                snapshot['staging'][0]['path'] = snapshot['staging'][0]['id'] = path
                attest(snapshot)
                self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_lock_file_existence_is_not_idle_flock_proof(self):
        for field, value in (('held', True), ('held', None), ('holder_pids', [456]),
                             ('no_symlink', False), ('regular_file', False),
                             ('identity_stable', False), ('kernel_scan_complete', False),
                             ('path', '/tmp/deploy.lock'), ('inode', True),
                             ('inode', 123), ('identity', None)):
            with self.subTest(field=field, value=value):
                snapshot = fixture()
                snapshot['cleanup_evidence']['lock'][field] = value
                self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_any_external_active_lock_blocks_all_safe_proposals(self):
        snapshot = fixture()
        snapshot['operation_observation']['lock_held'] = True
        attest(snapshot)
        result = plan(snapshot)['cleanup']
        self.assertFalse(any(item['classification'] == 'SAFE_TO_CLEAN'
                             for values in result['nodes'].values() for item in values))

    def test_inventory_current_pointer_change_invalidates_prior_evidence(self):
        snapshot = fixture()
        snapshot['current_release'] = PREVIOUS
        result = node(plan(snapshot))
        self.assertIn('EVIDENCE_INVENTORY_BINDING_INVALID', result['reasons'])
        self.assertEqual(result['classification'], 'NEEDS_REVIEW')

    def test_raced_node_identity_or_content_is_never_accepted(self):
        snapshot = fixture()
        snapshot['staging'][0]['allocated_bytes'] += 4096
        snapshot['cleanup_evidence']['inventory_sha256'] = cleanup.inventory_digest(snapshot)
        result = node(plan(snapshot))
        self.assertIn('OPERATION_OBJECT_IDENTITY_MISMATCH', result['reasons'])
        self.assertEqual(result['classification'], 'NEEDS_REVIEW')

    def test_object_symlinks_mounts_and_unverified_hashes_fail_closed(self):
        for field in ('object_identity_stable', 'no_symlink_escape', 'no_mount_crossing',
                      'content_inventory_verified', 'source_no_symlinks', 'source_checksum_verified'):
            with self.subTest(field=field):
                snapshot = fixture()
                record(snapshot)[field] = False
                self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_active_staging_is_active_even_with_other_invalid_evidence(self):
        snapshot = fixture()
        snapshot['staging'][0]['active'] = True
        attest(snapshot)
        snapshot['cleanup_evidence']['source_verified'] = False
        self.assertEqual(node(plan(snapshot))['classification'], 'ACTIVE')
        for field, value in (('owner_process_state', 'alive'), ('lease_state', 'active'), ('state', 'running')):
            with self.subTest(field=field):
                snapshot = fixture()
                record(snapshot)['operation'][field] = value
                self.assertEqual(node(plan(snapshot))['classification'], 'ACTIVE')

    def test_open_fd_cwd_mmap_or_process_reference_prevents_cleanup(self):
        for field, value in (('open_paths', ['/fixture/open']), ('active_processes', [123])):
            with self.subTest(field=field):
                snapshot = fixture()
                record(snapshot)[field] = value
                self.assertEqual(node(plan(snapshot))['classification'], 'ACTIVE')

    def test_pid_missing_or_reused_cannot_prove_termination(self):
        for field, value in (('owner_pid', 0), ('owner_start_ticks', True),
                             ('boot_id', 'old-host'), ('owner_process_state', 'pid_absent'),
                             ('terminal_receipt_verified', False), ('lease_state', 'expired')):
            with self.subTest(field=field):
                snapshot = fixture()
                record(snapshot)['operation'][field] = value
                self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_active_protocol_sha_overrides_a_claimed_terminal_receipt(self):
        snapshot = fixture()
        snapshot['operation_observation']['operation_shas'] = ['d' * 40]
        attest(snapshot)
        self.assertEqual(node(plan(snapshot))['classification'], 'ACTIVE')

    def test_owner_target_sha_must_match_release_and_backup_identity(self):
        snapshot = fixture()
        record(snapshot, 'releases', OLD)['operation']['target_sha'] = 'f' * 40
        self.assertEqual(node(plan(snapshot), 'releases', OLD)['classification'], 'NEEDS_REVIEW')

    def test_terminal_receipt_preservation_uses_lifecycle_not_mtime(self):
        snapshot = fixture()
        snapshot['staging'][0]['created_at'] = 0
        attest(snapshot)
        record(snapshot)['operation']['completed_at'] = NOW - 86400
        result = node(plan(snapshot))
        self.assertEqual(result['classification'], 'NEEDS_REVIEW')
        self.assertIn('PRESERVATION_PERIOD_NOT_ELAPSED', result['reasons'])

    def test_invalid_lifecycle_dates_include_nan_boolean_future_and_reversal(self):
        for field, value in (('created_at', float('nan')), ('created_at', True),
                             ('created_at', -1), ('completed_at', NOW + 1),
                             ('created_at', NOW), ('completed_at', 'yesterday'),
                             ('completed_at', 10 ** 1000)):
            with self.subTest(field=field, value=value):
                snapshot = fixture()
                record(snapshot)['operation'][field] = value
                self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_observation_epoch_cannot_be_future_or_unknown(self):
        for value in (NOW + 1, float('nan'), None, True):
            with self.subTest(value=value):
                snapshot = fixture()
                snapshot['cleanup_evidence']['observed_at'] = value
                self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_preserved_unique_recovery_data_and_incident_review_are_required(self):
        for field, value in (('unique_data', True), ('evidence_preserved', False),
                             ('incident_review_complete', False), ('disposition_validation', 'looks_old'),
                             ('proof_sha256', None)):
            with self.subTest(field=field):
                snapshot = fixture()
                record(snapshot)['recovery'][field] = value
                self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_ready_backup_staging_requires_full_validation_not_ready_alone(self):
        snapshot = fixture()
        stage = snapshot['staging'][0]
        stage['id'] = stage['path'] = '/home/ubuntu/ichiyon-backups/.backup-' + 'd' * 40 + '.abcdefgh'
        stage['ready'] = True
        attest(snapshot)
        self.assertEqual(node(plan(snapshot))['classification'], 'SAFE_TO_CLEAN')
        for key in list(record(snapshot)['backup_validation']):
            modified = copy.deepcopy(snapshot)
            record(modified)['backup_validation'][key] = False
            with self.subTest(key=key):
                self.assertEqual(node(plan(modified))['classification'], 'NEEDS_REVIEW')
        stage['ready'] = None
        attest(snapshot)
        self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_legacy_backup_needs_independent_full_format_and_restore_proof(self):
        snapshot = fixture()
        item = backup('legacy-fixture', OLD, PREVIOUS, validation='legacy')
        item['path'] = '/home/ubuntu/ichiyon-backups/legacy-fixture'
        snapshot['backups'].append(item)
        attest(snapshot)
        result = plan(snapshot)
        self.assertEqual(node(result, 'backups')['classification'], 'SAFE_TO_CLEAN')
        self.assertEqual(result['nodes']['backups'][0]['classification'], 'NEEDS_REVIEW')
        for key in list(record(snapshot, 'backups')['backup_validation']):
            modified = copy.deepcopy(snapshot)
            record(modified, 'backups')['backup_validation'][key] = False
            with self.subTest(key=key):
                self.assertEqual(node(plan(modified), 'backups')['classification'], 'NEEDS_REVIEW')
        record(snapshot, 'backups')['format_validation'] = None
        self.assertEqual(node(plan(snapshot), 'backups')['classification'], 'NEEDS_REVIEW')

    def test_current_and_rollback_release_are_never_cleanup_safe(self):
        result = plan(fixture())
        for identity in (CURRENT, PREVIOUS):
            self.assertEqual(node(result, 'releases', identity)['classification'], 'NEEDS_REVIEW')
        self.assertEqual(node(result, 'releases', OLD)['classification'], 'SAFE_TO_CLEAN')

    def test_policy_keep_cannot_be_overridden_by_terminal_evidence(self):
        snapshot = fixture()
        result = plan(snapshot, dict(NO_EXTRA_RETENTION, release_latest=100))
        self.assertEqual(node(result, 'releases', OLD)['classification'], 'NEEDS_REVIEW')

    def test_backup_previous_reference_protects_old_release(self):
        snapshot = fixture()
        item = backup('previous-user', PREVIOUS, OLD)
        item['path'] = '/home/ubuntu/ichiyon-backups/previous-user'
        snapshot['backups'].append(item)
        attest(snapshot)
        self.assertEqual(node(plan(snapshot), 'releases', OLD)['classification'], 'NEEDS_REVIEW')

    def test_referenced_staging_is_not_safe_even_from_a_delete_candidate(self):
        snapshot = fixture()
        snapshot['releases'][-1]['staging_refs'] = [snapshot['staging'][0]['id']]
        attest(snapshot)
        self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_image_pins_and_migration_container_are_preserved(self):
        snapshot = fixture()
        self.assertEqual(node(plan(snapshot), 'images', image_id('e'))['classification'], 'SAFE_TO_CLEAN')
        snapshot['containers'] = [dict(id='migration-fixture', image_id=image_id('e'),
                                       release_sha=OLD, running=False)]
        attest(snapshot)
        self.assertEqual(node(plan(snapshot), 'images', image_id('e'))['classification'], 'NEEDS_REVIEW')
        self.assertEqual(node(plan(snapshot), 'releases', OLD)['classification'], 'NEEDS_REVIEW')
        snapshot['containers'] = []
        snapshot['explicit_pins']['images'] = [image_id('e')]
        attest(snapshot)
        self.assertEqual(node(plan(snapshot), 'images', image_id('e'))['classification'], 'NEEDS_REVIEW')

    def test_literal_rollback_image_pin_survives_candidate_release(self):
        snapshot = fixture()
        snapshot['releases'][-1]['rollback_images'] = [image_id('e')]
        attest(snapshot)
        self.assertEqual(node(plan(snapshot), 'images', image_id('e'))['classification'], 'NEEDS_REVIEW')

    def test_candidate_parent_image_alias_is_resolved_to_exact_image(self):
        snapshot = fixture()
        snapshot['releases'][-1]['image_refs'] = ['ichiyon-robot-app:' + 'e' * 40]
        attest(snapshot)
        result = plan(snapshot)
        self.assertEqual(node(result, 'images', image_id('e'))['classification'], 'NEEDS_REVIEW')
        self.assertIn('releases:' + OLD, node(result, 'images', image_id('e'))['referenced_by'])

    def test_candidate_parent_unresolved_or_ambiguous_image_alias_fails_closed(self):
        for ambiguous in (False, True):
            with self.subTest(ambiguous=ambiguous):
                snapshot = fixture()
                alias = 'ichiyon-robot-app:' + ('e' if ambiguous else 'f') * 40
                snapshot['releases'][-1]['image_refs'] = [alias]
                if ambiguous:
                    snapshot['images'][0]['tags'].append(alias)
                attest(snapshot)
                self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_v2_recovery_reference_is_visible_in_retention_report(self):
        snapshot = fixture()
        item = backup('current-v2', CURRENT, PREVIOUS)
        item.update(path='/home/ubuntu/ichiyon-backups/current-v2', backup_format_version=2,
                    recovery_archive_ids=['e' * 64])
        snapshot['backups'].append(item)
        attest(snapshot)
        result = plan(snapshot)
        self.assertEqual(result['nodes']['backups'][0]['recovery_archive_ids'], ['e' * 64])
        self.assertEqual(result['nodes']['backups'][0]['backup_format_version'], 2)

    def test_incomplete_or_malformed_reference_observation_blocks_all_candidates(self):
        for change in ('global', 'unknown', 'malformed', 'collection_error'):
            with self.subTest(change=change):
                snapshot = fixture()
                if change == 'global':
                    snapshot['references_complete'] = False
                elif change == 'unknown':
                    snapshot['unknown_reference_kinds'] = ['images']
                elif change == 'malformed':
                    snapshot['releases'][-1]['staging_refs'] = [None]
                else:
                    snapshot['collection_errors'] = ['reference_changed']
                attest(snapshot)
                self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_duplicate_receipt_and_conflicting_object_fail_closed(self):
        snapshot = fixture()
        snapshot['cleanup_evidence']['records'].append(copy.deepcopy(record(snapshot)))
        self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')
        snapshot = fixture()
        item = copy.deepcopy(snapshot['staging'][0])
        item['allocated_bytes'] += 1
        snapshot['staging'].append(item)
        attest(snapshot)
        self.assertEqual(node(plan(snapshot))['classification'], 'NEEDS_REVIEW')

    def test_corrupt_metadata_or_unmanaged_object_never_promoted_by_receipt(self):
        for key, value in (('validation', 'invalid'), ('managed', False), ('size_complete', False)):
            with self.subTest(key=key):
                snapshot = fixture()
                snapshot['releases'][-1][key] = value
                attest(snapshot)
                self.assertEqual(node(plan(snapshot), 'releases', OLD)['classification'], 'NEEDS_REVIEW')

    def test_planning_is_pure_and_does_not_open_files_or_launch_processes(self):
        snapshot = fixture()
        before = copy.deepcopy(snapshot)
        with patch('builtins.open', side_effect=AssertionError('I/O')), \
                patch('subprocess.run', side_effect=AssertionError('process')), \
                patch('os.unlink', side_effect=AssertionError('delete')):
            result = plan(snapshot)
        self.assertEqual(snapshot, before)
        json.dumps(result, allow_nan=False)
        self.assertEqual(node(result)['classification'], 'SAFE_TO_CLEAN')


if __name__ == '__main__':
    unittest.main()
