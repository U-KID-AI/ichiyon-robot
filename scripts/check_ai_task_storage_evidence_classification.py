"""Offline proof that only fully observed no-op success can lose its raw history."""
import copy
import unittest

import ai_task_storage_evidence_classification as classification


TARGET = 'a' * 40
PREVIOUS = 'b' * 40
IMAGE = 'sha256:' + '1' * 64
OTHER_IMAGE = 'sha256:' + '2' * 64
MIGRATION = '3' * 64
OPERATION = 'c' * 32


def identity(inode=123, mode=0o40700):
    return dict(device=1, inode=inode, signature=[1, inode, mode, 4096, 0, 100, 100])


def fixture():
    """Only classifier inputs; the frontend separately validates full receipts."""
    runtime = dict(target_release=identity(), target_image=dict(id=IMAGE, revision=TARGET),
                   migration_container=None, image_ids=[IMAGE],
                   image_tags={'ichiyon-robot-app:' + TARGET: IMAGE}, observation_complete=True)
    stage = '/home/ubuntu/ichiyon-releases/.prepare-' + TARGET + '.abcdefgh'
    payload = dict(operation_id=OPERATION, target_sha=TARGET, previous_sha=TARGET,
                   created_at=100, completed_at=110, state='succeeded', phase='ancillary_cleanup',
                   rollback_result='not_needed', initial_names=dict(releases=[TARGET], backups=[PREVIOUS]),
                   initial_runtime=copy.deepcopy(runtime), runtime=copy.deepcopy(runtime),
                   staging=[dict(kind='prepare', path=stage, created_identity=identity(124), created_at=101)],
                   events=[dict(phase='prepare', at=101), dict(phase='reconcile', at=103),
                           dict(phase='health', at=105), dict(phase='cleanup', at=108),
                           dict(phase='ancillary_cleanup', at=109)],
                   omitted_event_count=0)
    observation = dict(version=1, operation_id=OPERATION, observed_at=111, current_sha=TARGET,
                       references_complete=True, names=copy.deepcopy(payload['initial_names']),
                       runtime=copy.deepcopy(runtime),
                       stages=[dict(kind='prepare', path=stage, status='absent', identity=None)],
                       health_verified=True, reconciliation_verified=True, cleanup_completed=True,
                       ancillary_cleanup_verified=True)
    return payload, observation


def add_stage(payload, observation, kind, status='absent'):
    root = '/home/ubuntu/ichiyon-backups' if kind == 'backup' else '/home/ubuntu/ichiyon-releases'
    path = root + '/.' + kind + '-' + TARGET + '.abcdefgh'
    payload['staging'].append(dict(kind=kind, path=path, created_identity=identity(200), created_at=102))
    observation['stages'].append(dict(kind=kind, path=path, status=status,
                                      identity=identity(200) if status == 'present' else None))
    return path.rsplit('/', 1)[-1]


def migration():
    return dict(id=MIGRATION, image_id=IMAGE, status='exited', name='ichiyon-robot-migrate-' + TARGET)


class ClassificationTests(unittest.TestCase):
    def assert_class(self, expected, payload, observation):
        original = copy.deepcopy((payload, observation))
        result = classification.classify(payload, observation)
        self.assertEqual(result['classification'], expected)
        self.assertEqual((payload, observation), original, 'classification mutated its inputs')
        self.assertEqual(set(result), {'classification', 'reasons', 'noop_proof', 'object_keys'})
        self.assertTrue(result['reasons'])
        if expected == 'C':
            self.assertEqual(result['object_keys'], [])
            self.assertEqual(result['noop_proof'], dict(persistent_absence_verified=True,
                                                      health_verified=True, cleanup_verified=True))
        else:
            self.assertIsNone(result['noop_proof'])
        return result

    def test_actual_reconciliation_cleanup_and_health_prove_c(self):
        self.assert_class('C', *fixture())

    def test_same_sha_alone_is_not_noop_proof(self):
        for field in ('health_verified', 'reconciliation_verified', 'cleanup_completed', 'ancillary_cleanup_verified'):
            with self.subTest(field=field):
                payload, observation = fixture()
                observation[field] = False
                self.assert_class('B', payload, observation)

    def test_health_and_cleanup_events_are_required_and_ordered(self):
        for events in (['reconcile', 'cleanup'], ['reconcile', 'health'], ['health', 'reconcile', 'cleanup'],
                       ['cleanup', 'reconcile', 'health']):
            with self.subTest(events=events):
                payload, observation = fixture()
                payload['events'] = [dict(phase=phase, at=101 + index) for index, phase in enumerate(events)]
                self.assert_class('B', payload, observation)

    def test_backward_event_time_cannot_prove_c(self):
        payload, observation = fixture()
        payload['events'][2]['at'] = 102
        self.assert_class('B', payload, observation)

    def test_truncated_event_history_cannot_prove_c(self):
        payload, observation = fixture()
        payload['omitted_event_count'] = 1
        self.assert_class('B', payload, observation)

    def test_current_unknown_or_changed_cannot_prove_c(self):
        for current in (None, PREVIOUS):
            with self.subTest(current=current):
                payload, observation = fixture()
                observation['current_sha'] = current
                self.assert_class('B', payload, observation)

    def test_terminal_phase_must_follow_cleanup(self):
        payload, observation = fixture()
        payload['phase'] = 'health'
        self.assert_class('B', payload, observation)

    def test_ancillary_cleanup_requires_event_after_ordinary_cleanup(self):
        payload, observation = fixture()
        payload['events'] = payload['events'][:-1]
        self.assert_class('B', payload, observation)

    def test_leftover_stage_is_object_bearing(self):
        payload, observation = fixture()
        observation['stages'][0].update(status='present', identity=identity(124))
        result = self.assert_class('A', payload, observation)
        self.assertIn(('staging', '.prepare-' + TARGET + '.abcdefgh'), result['object_keys'])

    def test_symlink_presence_is_never_absence(self):
        payload, observation = fixture()
        observation['stages'][0].update(status='present', identity=identity(999, 0o120777))
        self.assert_class('A', payload, observation)

    def test_unknown_stage_is_incident_not_absence(self):
        payload, observation = fixture()
        observation['stages'][0]['status'] = 'unknown'
        self.assert_class('B', payload, observation)

    def test_unavailable_observation_remains_lossless_b(self):
        payload, observation = fixture()
        observation.update(current_sha=None, references_complete=False,
                           names=dict(releases=[], backups=[]), health_verified=False)
        observation['runtime'] = dict(target_release=None, target_image=None, migration_container=None,
                                      image_ids=[], image_tags={}, observation_complete=False)
        observation['stages'][0]['status'] = 'unknown'
        self.assert_class('B', payload, observation)

    def test_unknown_docker_at_any_stage_prevents_c(self):
        for source, name in (('payload', 'initial_runtime'), ('payload', 'runtime'), ('observation', 'runtime')):
            with self.subTest(source=source, name=name):
                payload, observation = fixture()
                (payload if source == 'payload' else observation)[name]['observation_complete'] = False
                self.assert_class('B', payload, observation)

    def test_legacy_missing_tags_retains_raw_evidence(self):
        for source, name in (('payload', 'initial_runtime'), ('payload', 'runtime'), ('observation', 'runtime')):
            with self.subTest(source=source, name=name):
                payload, observation = fixture()
                del (payload if source == 'payload' else observation)[name]['image_tags']
                self.assert_class('B', payload, observation)

    def test_new_target_image_is_indexed_and_retained(self):
        payload, observation = fixture()
        for runtime in (payload['runtime'], observation['runtime']):
            runtime['image_ids'].append(OTHER_IMAGE)
            runtime['target_image']['id'] = OTHER_IMAGE
            runtime['image_tags']['ichiyon-robot-app:' + TARGET] = OTHER_IMAGE
        result = self.assert_class('A', payload, observation)
        self.assertIn(('images', OTHER_IMAGE), result['object_keys'])
        self.assertNotIn(('images', IMAGE), result['object_keys'])

    def test_unowned_new_image_does_not_acquire_cleanup_authority(self):
        payload, observation = fixture()
        for runtime in (payload['runtime'], observation['runtime']):
            runtime['image_ids'].append(OTHER_IMAGE)
        result = self.assert_class('A', payload, observation)
        self.assertNotIn(('images', OTHER_IMAGE), result['object_keys'])

    def test_new_tag_for_existing_id_prevents_c(self):
        payload, observation = fixture()
        for runtime in (payload['runtime'], observation['runtime']):
            runtime['image_tags']['ichiyon-robot-app:rollback'] = IMAGE
        result = self.assert_class('A', payload, observation)
        self.assertNotIn(('images', IMAGE), result['object_keys'])

    def test_nontarget_retag_for_existing_id_prevents_c(self):
        payload, observation = fixture()
        for runtime in (payload['initial_runtime'], payload['runtime'], observation['runtime']):
            runtime['image_ids'].append(OTHER_IMAGE)
            runtime['image_tags']['ichiyon-robot-app:other'] = IMAGE
        for runtime in (payload['runtime'], observation['runtime']):
            runtime['image_tags']['ichiyon-robot-app:other'] = OTHER_IMAGE
        self.assert_class('A', payload, observation)

    def test_retag_to_existing_image_is_not_claimed_as_new_image(self):
        payload, observation = fixture()
        for runtime in (payload['initial_runtime'], payload['runtime'], observation['runtime']):
            runtime['image_ids'].append(OTHER_IMAGE)
        for runtime in (payload['runtime'], observation['runtime']):
            runtime['target_image']['id'] = OTHER_IMAGE
            runtime['image_tags']['ichiyon-robot-app:' + TARGET] = OTHER_IMAGE
        result = self.assert_class('A', payload, observation)
        self.assertNotIn(('images', OTHER_IMAGE), result['object_keys'])

    def test_remaining_migration_is_object_bearing_and_indexed(self):
        payload, observation = fixture()
        payload['runtime']['migration_container'] = migration()
        observation['runtime']['migration_container'] = migration()
        result = self.assert_class('A', payload, observation)
        self.assertIn(('migration', MIGRATION), result['object_keys'])

    def test_historical_migration_id_does_not_override_fresh_absence(self):
        payload, observation = fixture()
        payload['runtime']['migration_container'] = migration()
        self.assert_class('C', payload, observation)

    def test_missing_final_migration_observation_is_not_c(self):
        payload, observation = fixture()
        payload['runtime']['migration_container'] = migration()
        observation['runtime']['observation_complete'] = False
        result = self.assert_class('B', payload, observation)
        self.assertIn(('migration', MIGRATION), result['object_keys'])

    def test_failure_and_cancellation_take_priority_over_objects(self):
        for state in ('failed', 'cancelled'):
            with self.subTest(state=state):
                payload, observation = fixture()
                payload['state'] = state
                stage = add_stage(payload, observation, 'backup', 'present')
                result = self.assert_class('B', payload, observation)
                self.assertIn(('backups', TARGET), result['object_keys'])
                self.assertIn(('staging', stage), result['object_keys'])

    def test_every_rollback_result_other_than_not_needed_is_b(self):
        for rollback in ('not_attempted', 'succeeded', 'failed', 'unknown'):
            with self.subTest(rollback=rollback):
                payload, observation = fixture()
                payload['rollback_result'] = rollback
                self.assert_class('B', payload, observation)

    def test_rollback_event_cannot_be_hidden_by_success_status(self):
        payload, observation = fixture()
        payload['events'].insert(1, dict(phase='rollback', at=102))
        self.assert_class('B', payload, observation)

    def test_health_retry_is_lossless_incident_even_after_eventual_success(self):
        payload, observation = fixture()
        payload['events'].insert(2, dict(phase='health_retry', at=104))
        self.assert_class('B', payload, observation)

    def test_real_deployment_cannot_be_rollup_even_if_no_new_objects_observed(self):
        payload, observation = fixture()
        payload['previous_sha'] = PREVIOUS
        self.assert_class('A', payload, observation)

    def test_creation_phases_cannot_be_hidden_by_matching_final_inventory(self):
        for phase in ('release', 'backup', 'image', 'migrate'):
            with self.subTest(phase=phase):
                payload, observation = fixture()
                payload['events'].insert(1, dict(phase=phase, at=102))
                self.assert_class('B', payload, observation)

    def test_removed_release_or_backup_stages_still_prevent_noop_proof(self):
        for kind in ('release', 'backup'):
            with self.subTest(kind=kind):
                payload, observation = fixture()
                add_stage(payload, observation, kind)
                result = self.assert_class('B', payload, observation)
                self.assertIn(('releases' if kind == 'release' else 'backups', TARGET), result['object_keys'])

    def test_positive_object_observation_remains_a_before_creation_phase_guard(self):
        payload, observation = fixture()
        add_stage(payload, observation, 'backup', 'present')
        self.assert_class('A', payload, observation)

    def test_potential_owned_objects_keep_lossless_lookup_even_after_absence(self):
        payload, observation = fixture()
        payload['state'] = 'failed'
        release_stage = add_stage(payload, observation, 'release')
        backup_stage = add_stage(payload, observation, 'backup')
        result = self.assert_class('B', payload, observation)
        for key in (('releases', TARGET), ('backups', TARGET), ('staging', release_stage), ('staging', backup_stage)):
            self.assertIn(key, result['object_keys'])
        self.assertTrue(all('/' not in key for _, key in result['object_keys']))

    def test_new_published_backup_is_indexed(self):
        payload, observation = fixture()
        observation['names']['backups'].append(TARGET)
        result = self.assert_class('A', payload, observation)
        self.assertIn(('backups', TARGET), result['object_keys'])

    def test_replaced_release_identity_cannot_be_noop(self):
        payload, observation = fixture()
        for runtime in (payload['runtime'], observation['runtime']):
            runtime['target_release'] = identity(999)
        self.assert_class('A', payload, observation)

    def test_missing_current_release_or_image_identity_is_unknown(self):
        for key in ('target_release', 'target_image'):
            with self.subTest(key=key):
                payload, observation = fixture()
                for runtime in (payload['initial_runtime'], payload['runtime'], observation['runtime']):
                    runtime[key] = None
                    if key == 'target_image':
                        runtime['image_tags'] = {}
                self.assert_class('B', payload, observation)

    def test_disappearing_unrelated_inventory_is_not_noop(self):
        payload, observation = fixture()
        observation['names']['backups'] = []
        self.assert_class('B', payload, observation)

    def test_final_runtime_must_match_terminal_metadata(self):
        payload, observation = fixture()
        payload['runtime']['target_release'] = identity(999)
        self.assert_class('B', payload, observation)

    def test_active_operation_never_compacts(self):
        payload, observation = fixture()
        payload['state'] = 'running'
        with self.assertRaisesRegex(classification.ClassificationError, '^operation_not_terminal$'):
            classification.classify(payload, observation)

    def test_missing_stage_observation_fails_closed(self):
        payload, observation = fixture()
        observation['stages'] = []
        with self.assertRaisesRegex(classification.ClassificationError, '^stage_observation_unbound$'):
            classification.classify(payload, observation)

    def test_extra_stage_is_not_trusted(self):
        payload, observation = fixture()
        observation['stages'].append(dict(kind='backup', path='/home/ubuntu/ichiyon-backups/.backup-' + TARGET + '.abcdefgh',
                                          status='absent', identity=None))
        with self.assertRaisesRegex(classification.ClassificationError, '^stage_observation_unbound$'):
            classification.classify(payload, observation)

    def test_observation_must_bind_operation_and_timestamp(self):
        for field, value in (('operation_id', 'd' * 32), ('observed_at', 109), ('version', True)):
            with self.subTest(field=field):
                payload, observation = fixture()
                observation[field] = value
                with self.assertRaisesRegex(classification.ClassificationError, '^final_observation_unbound$'):
                    classification.classify(payload, observation)

    def test_flag_truthiness_cannot_replace_boolean_proof(self):
        payload, observation = fixture()
        observation['cleanup_completed'] = 1
        with self.assertRaisesRegex(classification.ClassificationError, '^observation_flag_invalid$'):
            classification.classify(payload, observation)

    def test_arbitrary_and_escaped_stage_paths_are_rejected(self):
        for path in ('/tmp/.prepare-' + TARGET + '.abcdefgh',
                     '/home/ubuntu/ichiyon-releases/../ichiyon-releases/.prepare-' + TARGET + '.abcdefgh',
                     '/home/ubuntu/ichiyon-releases//.prepare-' + TARGET + '.abcdefgh',
                     '/home/ubuntu/ichiyon-releases/.prepare-' + PREVIOUS + '.abcdefgh'):
            with self.subTest(path=path):
                payload, observation = fixture()
                payload['staging'][0]['path'] = path
                observation['stages'][0]['path'] = path
                with self.assertRaisesRegex(classification.ClassificationError, '^stage_binding_invalid$'):
                    classification.classify(payload, observation)

    def test_malformed_stage_kind_reports_fixed_error(self):
        payload, observation = fixture()
        payload['staging'][0]['kind'] = []
        with self.assertRaisesRegex(classification.ClassificationError, '^stage_binding_invalid$'):
            classification.classify(payload, observation)

    def test_nonfinite_and_enormous_timestamps_are_rejected(self):
        for value in (True, float('nan'), float('inf'), 10**1000):
            with self.subTest(value=str(value)[:15]):
                payload, observation = fixture()
                payload['created_at'] = value
                with self.assertRaisesRegex(classification.ClassificationError, '^operation_time_invalid$'):
                    classification.classify(payload, observation)

    def test_absent_stage_cannot_claim_an_identity(self):
        payload, observation = fixture()
        observation['stages'][0]['identity'] = identity()
        with self.assertRaisesRegex(classification.ClassificationError, '^stage_observation_invalid$'):
            classification.classify(payload, observation)

    def test_invalid_tag_binding_is_rejected(self):
        payload, observation = fixture()
        observation['runtime']['image_tags']['ichiyon-robot-app:' + TARGET] = OTHER_IMAGE
        with self.assertRaises(classification.ClassificationError):
            classification.classify(payload, observation)

    def test_summary_contains_no_cleanup_classification_or_object_keys(self):
        result = self.assert_class('C', *fixture())
        self.assertFalse({'safe_to_clean', 'ownership', 'deletable', 'delete_candidate'} & set(result))
        self.assertEqual(result['object_keys'], [])


if __name__ == '__main__':
    unittest.main()
