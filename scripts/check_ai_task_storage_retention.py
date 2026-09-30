"""Offline retention graph and read-only capability checks; no production I/O."""

import copy
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

import ai_task_storage_retention_graph as graph


NOW = 1790553600
NO_EXTRA_RETENTION = {'backup_latest': 0, 'backup_daily_days': 0, 'release_latest': 0}
CURRENT = 'a' * 40
PREVIOUS = 'b' * 40
OLD = 'c' * 40
RESTORE = 'd' * 40


def image_id(letter):
    return 'sha256:' + letter * 64


def common(identity, size=4096, validation='verified'):
    return {
        'id': identity, 'path': '/fixture/' + identity, 'allocated_bytes': size,
        'size_complete': True, 'validation': validation,
        'created_at': NOW - 30 * 86400, 'managed': True,
        'references_complete': True,
    }


def release(sha, image=None, **updates):
    result = common(sha)
    result.update(sha=sha, image_id=image or image_id(sha[0]), rollback_images=[])
    result.update(updates)
    return result


def image(letter, size=100, layers=None, **updates):
    identity = image_id(letter)
    result = common(identity, size=0)
    result.update(tags=['ichiyon-robot-app:' + letter * 40], size_bytes=size,
                  layers=[] if layers is None else layers)
    result.update(updates)
    return result


def backup(identity, target, previous, **updates):
    result = common(identity, size=8192)
    result.update(target_release=target, previous_release=previous,
                  required_images=[], ready=True, checksums_verified=True)
    result.update(updates)
    return result


def fixture():
    return {
        'collected_at': NOW, 'references_complete': True, 'collection_errors': [],
        'releases': [release(CURRENT), release(PREVIOUS), release(OLD)],
        'backups': [], 'images': [image('a'), image('b'), image('c')],
        'staging': [], 'containers': [], 'current_release': CURRENT,
        'runtime_releases': [CURRENT], 'previous_known_good': PREVIOUS,
        'explicit_pins': {'releases': [], 'images': [], 'backups': []},
        'layers': [], 'docker_layers_complete': False,
        'unknown_reference_kinds': [],
    }


def plan(snapshot, policy=None):
    return graph.build_plan(snapshot, NO_EXTRA_RETENTION if policy is None else policy)


def node(result, category, identity):
    return next(item for item in result['nodes'][category] if item['id'] == identity)


def filesystem_state(root):
    """Content, mode and mtime comparison; atime can change on a normal read."""
    result = {}
    for path in [root] + sorted(root.rglob('*')):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            content = ('link', os.readlink(str(path)))
        elif stat.S_ISREG(metadata.st_mode):
            content = ('file', hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            content = ('directory',)
        result[str(path.relative_to(root))] = (metadata.st_mode, metadata.st_mtime_ns, content)
    return result


def write_fixture_text(path, text):
    # The production protocol uses LF even when the fixture runs on Windows.
    with path.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(text)


def forbid_mutations():
    """Runtime tripwire around the actual collector, including write-capable opens."""
    import builtins
    stack = ExitStack()
    stack.attempts = []
    original_open = builtins.open
    original_path_open = Path.open
    original_os_open = os.open

    def checked_open(file, mode='r', *args, **kwargs):
        if any(character in mode for character in 'wax+'):
            stack.attempts.append('open')
            raise AssertionError('collector attempted a write-capable open')
        return original_open(file, mode, *args, **kwargs)

    def checked_path_open(path, mode='r', *args, **kwargs):
        if any(character in mode for character in 'wax+'):
            stack.attempts.append('Path.open')
            raise AssertionError('collector attempted a write-capable Path.open')
        return original_path_open(path, mode, *args, **kwargs)

    def checked_os_open(path, flags, *args, **kwargs):
        forbidden = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if flags & forbidden:
            stack.attempts.append('os.open')
            raise AssertionError('collector attempted write-capable os.open')
        return original_os_open(path, flags, *args, **kwargs)

    stack.enter_context(patch.object(builtins, 'open', checked_open))
    stack.enter_context(patch.object(Path, 'open', checked_path_open))
    stack.enter_context(patch.object(os, 'open', checked_os_open))
    def reject_mutation(operation):
        def reject(*args, **kwargs):
            stack.attempts.append(operation)
            raise AssertionError('collector attempted mutation: ' + operation)
        return reject
    for operation in ('unlink', 'remove', 'rename', 'replace', 'mkdir', 'rmdir',
                      'chmod', 'chown', 'symlink', 'link', 'utime', 'truncate'):
        if hasattr(os, operation):
            stack.enter_context(patch.object(os, operation, side_effect=reject_mutation(operation)))
    return stack


class HostFixture:
    """Small complete on-disk deployment inventory with a non-executing Docker fake."""

    def __init__(self, root):
        self.root = root
        self.paths = {'home': root / 'home', 'releases': root / 'home/ichiyon-releases',
                      'backups': root / 'home/ichiyon-backups',
                      'current': root / 'home/ichiyon-current',
                      'shared': root / 'home/ichiyon-shared'}
        self.proc_root = root / 'proc'
        for directory in (self.paths['releases'], self.paths['backups'],
                          self.paths['shared'], self.proc_root, root / 'docker'):
            directory.mkdir(parents=True, exist_ok=True)
        write_fixture_text(self.proc_root / 'locks', '')
        write_fixture_text(self.paths['home'] / 'ichiyon-deploy.lock', '')
        self.calls = []
        self.docker_state = {
            'info': {'DockerRootDir': str(root / 'docker'), 'Driver': 'overlay2'},
            'images': [], 'containers': [],
        }
        for sha in (CURRENT, PREVIOUS, OLD):
            target = self.paths['releases'] / sha
            (target / 'src').mkdir(parents=True)
            write_fixture_text(target / 'REVISION', sha + '\n')
            write_fixture_text(target / 'src/REVISION', sha + '\n')
            write_fixture_text(target / 'src/docker-compose.yml', 'services: {}\n')
            write_fixture_text(target / 'immutable-image.txt', 'ichiyon-robot-app:' + sha + '\n')
            write_fixture_text(target / 'rollback-images.txt', (image_id('b') + '\n') * 3)
            write_fixture_text(target / 'persistence.txt',
                'data RW /app/data\nassets/images RW /app/assets/images\nsecrets RO /app/secrets\n')
            write_fixture_text(target / 'compose.immutable.yml', 'services: {}\n')
            write_fixture_text(target / 'validate-immutable-compose.py', '# metadata only\n')
            self.docker_state['images'].append({
                'Id': image_id(sha[0]), 'RepoTags': ['ichiyon-robot-app:' + sha],
                'RepoDigests': [], 'Size': 100,
                'Created': '2026-09-01T00:00:00Z', 'RootFS': {'Layers': []},
                'Config': {'Labels': {'org.opencontainers.image.revision': sha}},
            })
        try:
            self.paths['current'].symlink_to(self.paths['releases'] / CURRENT,
                                            target_is_directory=True)
        except OSError as error:
            if os.name != 'nt':
                raise unittest.SkipTest('OS does not permit current-release fixture symlink') from error
            # A junction needs no Windows symlink privilege, resolves to the
            # same fixture directory, and stays wholly inside this temp tree.
            import _winapi
            source = self.paths['releases'] / CURRENT
            try:
                source.resolve().relative_to(root.resolve())
            except ValueError:
                raise AssertionError('fixture junction escaped its temp tree')
            _winapi.CreateJunction(str(source), str(self.paths['current']))
        for number, service in enumerate(('admin', 'bot', 'bot-irsia'), 1):
            self.docker_state['containers'].append({
                'Id': str(number) * 64, 'Image': image_id('a'),
                'Name': '/ichiyon-robot-' + service,
                'State': {'Running': True, 'Status': 'running',
                          'StartedAt': '2026-09-28T06:00:00Z'},
                'Config': {'Image': 'ichiyon-robot-app:' + CURRENT,
                           'Labels': {'com.docker.compose.project': 'ichiyon-robot',
                                      'com.docker.compose.service': service,
                                      'com.docker.compose.project.working_dir':
                                      str(self.paths['releases'] / CURRENT / 'src')}},
                'Mounts': [],
            })
        self.secret = 'MUST_NOT_PUBLISH_ENV_CONTENT_57e2'
        for item in self.docker_state['containers'] + self.docker_state['images']:
            item['Config']['Env'] = ['PASSWORD=' + self.secret]
            item['Config']['Cmd'] = ['secret-command', self.secret]
            item['Config']['Labels']['example.private.note'] = self.secret
        write_fixture_text(self.paths['shared'] / '.env', 'PASSWORD=' + self.secret + '\n')
        self.backup_path = self.paths['backups'] / CURRENT
        self.backup_path.mkdir()
        write_fixture_text(self.backup_path / 'previous', str(self.paths['releases'] / PREVIOUS) + '\n')
        write_fixture_text(self.backup_path / 'infra.json', json.dumps([
            ['4' * 64, 0, '2026-09-28T06:00:00Z'],
            ['5' * 64, 0, '2026-09-28T06:00:00Z']]) + '\n')
        (self.backup_path / 'production.dump').write_bytes(b'PGDMP\x01fixture')
        with tarfile.open(str(self.backup_path / 'persistence.tar'), 'w') as archive:
            for name in ('data', 'assets', 'assets/images', 'secrets'):
                entry = tarfile.TarInfo(name)
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
            entry = tarfile.TarInfo('.env')
            payload = ('PASSWORD=' + self.secret + '\n').encode()
            entry.size = len(payload)
            archive.addfile(entry, io.BytesIO(payload))
        self.write_checksums()
        write_fixture_text(self.backup_path / 'READY', '')

    def write_checksums(self):
        lines = []
        for name in ('production.dump', 'persistence.tar'):
            lines.append(hashlib.sha256((self.backup_path / name).read_bytes()).hexdigest()
                         + '  ' + name + '\n')
        write_fixture_text(self.backup_path / 'checksums.sha256', ''.join(lines))

    def docker(self, kind, identifiers=()):
        if kind not in ('info', 'images', 'containers'):
            raise AssertionError('non-allowlisted Docker capability')
        self.calls.append((kind, tuple(identifiers)))
        result = self.docker_state[kind]
        if identifiers and isinstance(result, list):
            result = [item for item in result if item['Id'] in identifiers]
        return copy.deepcopy(result)

    def collect(self, collector, **kwargs):
        docker = kwargs.pop('docker', self.docker)
        with ExitStack() as compatibility:
            if os.name == 'nt':
                # The empty fixture /proc/locks has no kernel device records;
                # Linux's numeric device helpers can be deterministic here.
                compatibility.enter_context(patch.object(os, 'major', return_value=0, create=True))
                compatibility.enter_context(patch.object(os, 'minor', return_value=0, create=True))
            return collector.collect_snapshot(paths=self.paths, docker=docker,
                                              proc_root=self.proc_root, **kwargs)


class RetentionGraphTests(unittest.TestCase):
    def assert_protected(self, item):
        self.assertIn(item['classification'], ('KEEP_REQUIRED', 'KEEP_POLICY'))
        self.assertTrue(item['reasons'])

    def test_current_release_and_image_are_never_candidates(self):
        result = plan(fixture())
        self.assertEqual(node(result, 'releases', CURRENT)['classification'], 'KEEP_REQUIRED')
        self.assertEqual(node(result, 'images', image_id('a'))['classification'], 'KEEP_REQUIRED')

    def test_previous_known_good_release_and_image_are_protected(self):
        result = plan(fixture())
        self.assert_protected(node(result, 'releases', PREVIOUS))
        self.assert_protected(node(result, 'images', image_id('b')))

    def test_retained_backup_protects_previous_release_and_its_image(self):
        snapshot = fixture()
        snapshot['releases'].append(release(RESTORE))
        snapshot['images'].append(image('d'))
        snapshot['backups'].append(backup('restore-point', OLD, RESTORE))
        snapshot['explicit_pins']['backups'].append('restore-point')
        result = plan(snapshot)
        self.assert_protected(node(result, 'backups', 'restore-point'))
        self.assert_protected(node(result, 'releases', RESTORE))
        self.assert_protected(node(result, 'images', image_id('d')))
        self.assert_protected(node(result, 'releases', OLD))
        self.assert_protected(node(result, 'images', image_id('c')))

    def test_retained_backup_missing_target_is_reviewed_and_keeps_known_previous(self):
        snapshot = fixture()
        snapshot['backups'].append(backup('restore-point', 'f' * 40, OLD))
        snapshot['explicit_pins']['backups'].append('restore-point')
        result = plan(snapshot)
        self.assertEqual(node(result, 'backups', 'restore-point')['classification'], 'NEEDS_REVIEW')
        self.assert_protected(node(result, 'releases', OLD))
        self.assert_protected(node(result, 'images', image_id('c')))

    def test_legacy_metadata_known_references_protect_transitive_restore_dependencies(self):
        snapshot = fixture()
        snapshot['releases'].append(release(RESTORE))
        snapshot['images'].append(image('d'))
        snapshot['backups'].append(backup('durable-restore', OLD, RESTORE))
        legacy = backup('legacy-manifest', OLD, PREVIOUS, validation='legacy')
        legacy.update(backup_refs=['durable-restore'], release_refs=[OLD],
                      image_refs=[image_id('c')])
        snapshot['backups'].append(legacy)
        result = plan(snapshot)
        self.assertEqual(node(result, 'backups', 'legacy-manifest')['classification'], 'NEEDS_REVIEW')
        for category, identity in (('backups', 'durable-restore'), ('releases', RESTORE),
                                   ('releases', OLD), ('images', image_id('c')),
                                   ('images', image_id('d'))):
            self.assert_protected(node(result, category, identity))

    def test_running_container_image_is_protected_even_without_release_reference(self):
        snapshot = fixture()
        snapshot['containers'].append({'id': '1' * 64, 'image_id': image_id('c'),
                                       'running': True, 'deployment_owned': False,
                                       'migration': False})
        result = plan(snapshot)
        self.assertEqual(node(result, 'images', image_id('c'))['classification'], 'KEEP_REQUIRED')

    def test_stopped_migration_container_protects_image_and_release(self):
        snapshot = fixture()
        snapshot['containers'].append({'id': '2' * 64, 'image_id': image_id('c'),
                                       'release_sha': OLD, 'running': False,
                                       'deployment_owned': True, 'migration': True})
        result = plan(snapshot)
        self.assert_protected(node(result, 'images', image_id('c')))
        self.assert_protected(node(result, 'releases', OLD))

    def test_unreferenced_verified_old_release_and_image_are_candidates(self):
        result = plan(fixture())
        for category, identity in (('releases', OLD), ('images', image_id('c'))):
            item = node(result, category, identity)
            self.assertEqual(item['classification'], 'DELETE_CANDIDATE')
            self.assertTrue(item['reasons'])

    def test_legacy_backup_and_corrupt_release_require_review(self):
        snapshot = fixture()
        snapshot['releases'][-1]['validation'] = 'invalid'
        snapshot['backups'].append(backup('legacy', OLD, PREVIOUS, validation='legacy'))
        result = plan(snapshot)
        self.assertEqual(node(result, 'releases', OLD)['classification'], 'NEEDS_REVIEW')
        self.assertEqual(node(result, 'backups', 'legacy')['classification'], 'NEEDS_REVIEW')

    def test_undecodable_reference_metadata_never_becomes_candidate(self):
        snapshot = fixture()
        snapshot['releases'][-1]['references_complete'] = False
        snapshot['releases'][-1]['validation'] = 'invalid'
        snapshot['unknown_reference_kinds'] = ['images', 'releases']
        result = plan(snapshot)
        for category in ('releases', 'images'):
            self.assertFalse(any(item['classification'] == 'DELETE_CANDIDATE'
                                 for item in result['nodes'][category]))

    def test_active_and_possibly_active_staging_are_active(self):
        snapshot = fixture()
        snapshot['staging'] = [dict(common('.prepare-active'), active=True),
                               dict(common('.backup-unknown'), possible_active=True)]
        result = plan(snapshot)
        self.assertEqual([item['classification'] for item in result['nodes']['staging']],
                         ['ACTIVE', 'ACTIVE'])

    def test_old_staging_is_not_candidate_from_mtime_or_free_lock_alone(self):
        snapshot = fixture()
        snapshot['staging'] = [dict(common('.release-old'), active=False,
                                   possible_active=False, lock_held=False,
                                   operation_owner=None)]
        item = plan(snapshot)['nodes']['staging'][0]
        self.assertEqual(item['classification'], 'NEEDS_REVIEW')
        self.assertTrue(item['reasons'])

    def test_open_or_active_release_and_backup_are_not_candidates(self):
        snapshot = fixture()
        snapshot['releases'][-1]['active'] = True
        snapshot['backups'].append(backup('open-backup', OLD, PREVIOUS, active=True))
        result = plan(snapshot)
        self.assertEqual(node(result, 'releases', OLD)['classification'], 'ACTIVE')
        self.assertEqual(node(result, 'backups', 'open-backup')['classification'], 'ACTIVE')

    def test_conflicting_duplicate_release_references_close_image_candidates(self):
        snapshot = fixture()
        snapshot['releases'].append(release(OLD, image_id('d')))
        snapshot['images'].append(image('d'))
        result = plan(snapshot)
        self.assertFalse(any(item['classification'] == 'DELETE_CANDIDATE'
                             for item in result['nodes']['images']))

    def test_missing_current_pointer_fails_closed_for_release_and_image_candidates(self):
        snapshot = fixture()
        snapshot['current_release'] = None
        result = plan(snapshot)
        self.assertFalse(any(item['classification'] == 'DELETE_CANDIDATE'
                             for category in ('releases', 'images')
                             for item in result['nodes'][category]))

    def test_same_image_referenced_by_multiple_releases_is_counted_once(self):
        snapshot = fixture()
        snapshot['releases'].append(release(RESTORE, image_id('c')))
        result = plan(snapshot)
        self.assertEqual(result['recovery']['docker']['candidate_image_count'], 1)
        self.assertEqual(result['recovery']['docker']['logical_image_bytes'], 100)

    def test_shared_docker_layers_are_counted_only_once_and_exclude_retained_layers(self):
        snapshot = fixture()
        snapshot['releases'].append(release(RESTORE))
        snapshot['images'] = [image('a', 150, ['base', 'live']), image('b', 100, ['base']),
                              image('c', 120, ['base', 'old-one']),
                              image('d', 150, ['base', 'old-one', 'old-two'])]
        snapshot['layers'] = [{'id': identity, 'size_bytes': size} for identity, size in
                              [('base', 100), ('live', 50), ('old-one', 20), ('old-two', 30)]]
        snapshot['docker_layers_complete'] = True
        estimate = plan(snapshot)['recovery']['docker']
        self.assertEqual(estimate['logical_image_bytes'], 270)
        self.assertEqual(estimate['unique_layer_upper_bound_bytes'], 50)
        self.assertEqual(estimate['guaranteed_reclaimable_bytes'], 0)

    def test_missing_layer_size_is_unknown_not_fabricated_reclaimable_bytes(self):
        snapshot = fixture()
        snapshot['images'][-1]['layers'] = ['unknown']
        snapshot['docker_layers_complete'] = True
        estimate = plan(snapshot)['recovery']['docker']
        self.assertIsNone(estimate['unique_layer_upper_bound_bytes'])
        self.assertEqual(estimate['guaranteed_reclaimable_bytes'], 0)

    def test_docker_reclaimable_is_not_accepted_as_candidate_capacity(self):
        snapshot = fixture()
        snapshot['docker_system_df_reclaimable_bytes'] = 10 ** 15
        estimate = plan(snapshot)['recovery']['docker']
        self.assertEqual(estimate['logical_image_bytes'], 100)
        self.assertEqual(estimate['guaranteed_reclaimable_bytes'], 0)

    def test_rollback_images_metadata_pins_image_even_for_old_release(self):
        snapshot = fixture()
        snapshot['releases'][-1]['rollback_images'] = [image_id('c')]
        result = plan(snapshot)
        self.assert_protected(node(result, 'images', image_id('c')))

    def test_explicit_release_pin_closes_over_image_dependencies(self):
        snapshot = fixture()
        snapshot['explicit_pins']['releases'] = [OLD]
        result = plan(snapshot)
        self.assert_protected(node(result, 'releases', OLD))
        self.assert_protected(node(result, 'images', image_id('c')))

    def test_unresolved_explicit_pin_disables_candidates_for_affected_reference_kinds(self):
        scopes = {'releases': ('releases', 'images'),
                  'backups': ('backups', 'releases', 'images'), 'images': ('images',)}
        for category, affected in scopes.items():
            with self.subTest(category=category):
                snapshot = fixture()
                snapshot['backups'].append(backup('old-complete', OLD, PREVIOUS))
                missing = image_id('f') if category == 'images' else 'f' * 40
                snapshot['explicit_pins'][category] = [missing]
                result = plan(snapshot)
                for kind in affected:
                    self.assertFalse(any(item['classification'] == 'DELETE_CANDIDATE'
                                         for item in result['nodes'][kind]))
                self.assertTrue(set(affected).issubset(result['unknown_reference_kinds']))

    def test_unresolved_image_pin_preserves_independent_backup_candidates(self):
        snapshot = fixture()
        snapshot['backups'].append(backup('old-complete', OLD, PREVIOUS))
        snapshot['explicit_pins']['images'] = [image_id('f')]
        result = plan(snapshot)
        self.assertEqual(node(result, 'backups', 'old-complete')['classification'], 'DELETE_CANDIDATE')
        self.assertNotIn('backups', result['unknown_reference_kinds'])

    def test_missing_previous_known_good_release_closes_release_and_image_candidates(self):
        snapshot = fixture()
        snapshot['previous_known_good'] = 'f' * 40
        result = plan(snapshot)
        for category in ('releases', 'images'):
            self.assertFalse(any(item['classification'] == 'DELETE_CANDIDATE'
                                 for item in result['nodes'][category]))
            self.assertIn(category, result['unknown_reference_kinds'])

    def test_absent_previous_known_good_pointer_closes_release_and_image_candidates(self):
        snapshot = fixture()
        snapshot['previous_known_good'] = None
        result = plan(snapshot)
        for category in ('releases', 'images'):
            self.assertFalse(any(item['classification'] == 'DELETE_CANDIDATE'
                                 for item in result['nodes'][category]))

    def test_snapshot_race_or_inventory_failure_suppresses_all_candidates(self):
        for reason in ('inventory_changed', 'docker_unavailable'):
            with self.subTest(reason=reason):
                snapshot = fixture()
                snapshot['references_complete'] = False
                snapshot['collection_errors'] = [reason]
                result = plan(snapshot)
                self.assertFalse(any(item['classification'] == 'DELETE_CANDIDATE'
                                     for items in result['nodes'].values() for item in items))

    def test_unmanaged_images_are_reviewed_instead_of_selected(self):
        snapshot = fixture()
        snapshot['images'][-1]['managed'] = False
        result = plan(snapshot)
        self.assertEqual(node(result, 'images', image_id('c'))['classification'], 'NEEDS_REVIEW')

    def test_incomplete_size_is_not_reported_as_confirmed_filesystem_recovery(self):
        snapshot = fixture()
        snapshot['releases'][-1]['size_complete'] = False
        result = plan(snapshot)
        self.assertEqual(result['recovery']['filesystem_confirmed_bytes'], 0)

    def test_plan_does_not_mutate_input_snapshot(self):
        snapshot = fixture()
        snapshot['releases'][-1]['rollback_images'] = [image_id('b')]
        before = copy.deepcopy(snapshot)
        plan(snapshot)
        self.assertEqual(snapshot, before)

    def test_every_object_has_classification_and_explained_reasons(self):
        snapshot = fixture()
        snapshot['backups'].append(backup('old-complete', OLD, PREVIOUS))
        snapshot['staging'].append(dict(common('.prepare-old'), possible_active=True))
        result = plan(snapshot)
        allowed = {'KEEP_REQUIRED', 'KEEP_POLICY', 'DELETE_CANDIDATE', 'NEEDS_REVIEW', 'ACTIVE'}
        for category, items in result['nodes'].items():
            for item in items:
                with self.subTest(category=category, identity=item['id']):
                    self.assertIn(item['classification'], allowed)
                    self.assertTrue(item['reasons'])
        json.dumps(result)

    def test_filesystem_hardlinks_are_counted_once_and_retained_owners_release_nothing(self):
        snapshot = fixture()
        snapshot['releases'].append(release(RESTORE))
        snapshot['images'].append(image('d'))
        snapshot['filesystem_allocations_complete'] = True
        snapshot['inode_allocations'] = [
            {'id': '1:100', 'allocated_bytes': 4096,
             'owners': ['releases:' + OLD, 'releases:' + RESTORE],
             'external_links': False, 'open': False},
            {'id': '1:101', 'allocated_bytes': 8192,
             'owners': ['releases:' + OLD, 'releases:' + CURRENT],
             'external_links': False, 'open': False},
            {'id': '1:102', 'allocated_bytes': 16384,
             'owners': ['releases:' + OLD], 'external_links': True, 'open': False},
            {'id': '1:103', 'allocated_bytes': 32768,
             'owners': ['releases:' + OLD], 'external_links': False, 'open': True},
        ]
        result = plan(snapshot)['recovery']
        self.assertEqual(result['filesystem_confirmed_bytes'], 4096)
        self.assertEqual(result['filesystem_by_category']['releases'], 4096)

    def test_missing_creation_time_cannot_evade_additional_retention_policy(self):
        snapshot = fixture()
        snapshot['releases'][-1].pop('created_at')
        snapshot['releases'].extend([release('d' * 40), release('e' * 40), release('f' * 40)])
        snapshot['images'].extend([image('d'), image('e'), image('f')])
        result = graph.build_plan(snapshot)
        self.assertEqual(node(result, 'releases', OLD)['classification'], 'NEEDS_REVIEW')

    def test_conflicting_layer_inventory_has_no_numeric_recovery_estimate(self):
        snapshot = fixture()
        for item in snapshot['images']:
            item['layers'] = ['layer-' + item['id'][-1]]
        snapshot['layers'] = [{'id': 'layer-a', 'size_bytes': 100},
                              {'id': 'layer-b', 'size_bytes': 100},
                              {'id': 'layer-c', 'size_bytes': 100},
                              {'id': 'layer-c', 'size_bytes': 1000}]
        snapshot['docker_layers_complete'] = True
        estimate = plan(snapshot)['recovery']['docker']
        self.assertIsNone(estimate['unique_layer_upper_bound_bytes'])

    def test_conflicting_inode_ownership_cannot_count_as_confirmed_recovery(self):
        snapshot = fixture()
        snapshot['filesystem_allocations_complete'] = True
        first = {'id': '1:100', 'allocated_bytes': 4096,
                 'owners': ['releases:' + OLD], 'external_links': False, 'open': False}
        second = dict(first, owners=['releases:' + CURRENT])
        snapshot['inode_allocations'] = [first, second]
        self.assertEqual(plan(snapshot)['recovery']['filesystem_confirmed_bytes'], 0)


class RetentionCollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ai_task_storage_retention as collector
        cls.collector = collector

    def v2_fixture(self, root, split=False):
        from check_ai_task_backup_restore import BackupFixture
        fixture = BackupFixture(root, split=split)
        recovery = root / 'ichiyon-recovery-archives'
        fixture.recovery.rename(recovery)
        fixture.recovery = recovery
        if fixture.archive is not None:
            fixture.archive = recovery / fixture.archive.name
        return fixture, dict(home=root, releases=fixture.releases,
            backups=fixture.path.parent, shared=root / 'shared', current=root / 'current')

    def test_production_v2_uses_only_fixed_docker_dump_list_without_mutation(self):
        for split in (False, True):
            with self.subTest(split=split), tempfile.TemporaryDirectory() as directory:
                fixture, paths = self.v2_fixture(Path(directory), split)
                before = filesystem_state(fixture.root)
                calls = []
                def list_only(argv, **options):
                    self.assertEqual(argv, ['docker', 'exec', '-i', 'ichiyon-robot-db',
                                            'pg_restore', '--list'])
                    self.assertIs(options['shell'], False)
                    self.assertIs(options['check'], False)
                    self.assertEqual(options['timeout'], 120)
                    self.assertEqual(options['stderr'], subprocess.DEVNULL)
                    self.assertEqual(options['stdin'].read(5), b'PGDMP')
                    calls.append(argv)
                    return types.SimpleNamespace(returncode=0, stdout=b'fixture TOC must not be reported')
                tripwire = forbid_mutations()
                with patch.object(self.collector, 'DEFAULT_PATHS', paths), tripwire, \
                        patch('subprocess.run', side_effect=list_only):
                    result = self.collector.read_backup(fixture.path, dict(paths), {})
                self.assertEqual(result['validation'], 'verified')
                self.assertTrue(result['checksum_verified'])
                self.assertTrue(result['ready'])
                self.assertEqual(len(calls), 1)
                self.assertEqual(tripwire.attempts, [])
                self.assertEqual(filesystem_state(fixture.root), before)
                self.assertNotIn('must not be reported', json.dumps(result))
                self.assertEqual(result['recovery_archive_ids'], [fixture.archive.name] if split else [])

    def test_alternate_v2_roots_preserve_local_pg_restore_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, paths = self.v2_fixture(Path(directory))
            with patch('subprocess.run', return_value=types.SimpleNamespace(
                    returncode=0, stdout=b'fixture TOC')) as command, \
                    patch.object(self.collector, 'production_dump_list',
                                 side_effect=AssertionError('must not use production')):
                result = self.collector.read_backup(fixture.path, paths, {})
            self.assertEqual(result['validation'], 'verified')
            self.assertEqual(command.call_args.args[0], ['pg_restore', '--list'])

    def test_production_v2_dump_parser_failure_never_verifies_backup(self):
        failures = [types.SimpleNamespace(returncode=1, stdout=b'failed'),
                    types.SimpleNamespace(returncode=0, stdout=b''),
                    FileNotFoundError('secret local tool failure'),
                    subprocess.TimeoutExpired('secret timeout command', 120)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                fixture, paths = self.v2_fixture(Path(directory))
                options = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
                with patch.object(self.collector, 'DEFAULT_PATHS', paths), patch('subprocess.run', **options):
                    result = self.collector.read_backup(fixture.path, paths, {})
                self.assertEqual(result['validation'], 'invalid')
                self.assertFalse(result['checksum_verified'])
                self.assertFalse(result['ready'])
                self.assertNotIn('secret', json.dumps(result))

    def test_production_v2_never_skips_checksum_or_disabled_validation(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as directory:
                fixture, paths = self.v2_fixture(Path(directory))
                if corrupt:
                    (fixture.path / 'production.dump').write_bytes(b'PGDMP-corrupted')
                with patch.object(self.collector, 'DEFAULT_PATHS', paths), \
                        patch('subprocess.run', side_effect=AssertionError('must not list unverified dump')) as command:
                    result = self.collector.read_backup(fixture.path, paths, {}, verify_checksums=corrupt)
                self.assertEqual(result['validation'], 'invalid' if corrupt else 'incomplete')
                self.assertFalse(result['checksum_verified'])
                command.assert_not_called()

    def test_production_v2_dump_changed_during_parser_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, paths = self.v2_fixture(Path(directory))
            def raced_parser(*args, **kwargs):
                (fixture.path / 'production.dump').write_bytes(b'PGDMP-replaced-during-parse')
                return types.SimpleNamespace(returncode=0, stdout=b'fixture TOC')
            with patch.object(self.collector, 'DEFAULT_PATHS', paths), \
                    patch('subprocess.run', side_effect=raced_parser):
                result = self.collector.read_backup(fixture.path, paths, {})
            self.assertEqual(result['validation'], 'invalid')
            self.assertFalse(result['checksum_verified'])

    def test_actual_collection_and_planning_change_no_files_or_docker_state(self):
        with tempfile.TemporaryDirectory() as directory:
            host = HostFixture(Path(directory))
            before = filesystem_state(host.root)
            docker_before = copy.deepcopy(host.docker_state)
            tripwire = forbid_mutations()
            with tripwire, patch('subprocess.run', side_effect=AssertionError('unmocked process')):
                snapshot = host.collect(self.collector)
                result = plan(snapshot)
            self.assertEqual(tripwire.attempts, [])
            self.assertEqual(filesystem_state(host.root), before)
            self.assertEqual(host.docker_state, docker_before)
            self.assertTrue(host.calls)
            self.assertTrue(snapshot['references_complete'], snapshot.get('collection_errors'))
            current = node(result, 'releases', CURRENT)
            self.assertIn('CURRENT_RELEASE', current['reasons'])
            self.assertNotEqual(current['classification'], 'DELETE_CANDIDATE')
            if os.name != 'nt':
                self.assertEqual(current['classification'], 'KEEP_REQUIRED')
            self.assertNotIn(host.secret, json.dumps(snapshot))
            self.assertNotIn(host.secret, json.dumps(result))

    def test_backup_checksum_mismatch_is_reviewed_not_deletable(self):
        with tempfile.TemporaryDirectory() as directory:
            host = HostFixture(Path(directory))
            (host.backup_path / 'production.dump').write_bytes(b'PGDMP\x01changed')
            result = plan(host.collect(self.collector))
            self.assertEqual(node(result, 'backups', CURRENT)['classification'], 'NEEDS_REVIEW')

    def test_identical_archive_cache_still_hashes_every_copy_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            host = HostFixture(Path(directory))
            second = host.paths['backups'] / OLD
            shutil.copytree(host.backup_path, second)
            cache, allocations = set(), {}
            with patch.object(self.collector, 'digest_file', wraps=self.collector.digest_file) as digest, \
                    patch.object(self.collector, 'validate_archive',
                                 wraps=self.collector.validate_archive) as archive:
                first_result = self.collector.read_backup(
                    host.backup_path, host.paths, allocations, validated_tar_hashes=cache)
                second_result = self.collector.read_backup(
                    second, host.paths, allocations, validated_tar_hashes=cache)
                self.assertEqual(first_result['validation'], 'verified')
                self.assertEqual(second_result['validation'], 'verified')
                self.assertEqual(digest.call_count, 4)
                self.assertEqual(archive.call_count, 1)
                self.assertEqual(len(cache), 1)
                target = second / 'persistence.tar'
                target.write_bytes(target.read_bytes() + b'changed-after-validation')
                corrupt_result = self.collector.read_backup(
                    second, host.paths, allocations, validated_tar_hashes=cache)
                self.assertEqual(corrupt_result['validation'], 'invalid')
                self.assertFalse(corrupt_result['checksum_verified'])
                self.assertEqual(digest.call_count, 6)
                self.assertEqual(archive.call_count, 1)

    def test_invalid_archive_never_populates_validation_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            host = HostFixture(Path(directory))
            with tarfile.open(str(host.backup_path / 'persistence.tar'), 'w') as archive:
                entry = tarfile.TarInfo('data')
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
            host.write_checksums()
            cache = set()
            with patch.object(self.collector, 'validate_archive',
                              wraps=self.collector.validate_archive) as validate:
                for _ in range(2):
                    result = self.collector.read_backup(
                        host.backup_path, host.paths, {}, validated_tar_hashes=cache)
                    self.assertEqual(result['validation'], 'invalid')
                self.assertEqual(validate.call_count, 2)
                self.assertEqual(cache, set())

    def test_backup_null_infrastructure_metadata_is_not_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            host = HostFixture(Path(directory))
            write_fixture_text(host.backup_path / 'infra.json', '[null,null]\n')
            snapshot = host.collect(self.collector)
            self.assertNotEqual(snapshot['backups'][0]['validation'], 'verified')
            result = plan(snapshot)
            self.assertEqual(node(result, 'backups', CURRENT)['classification'], 'NEEDS_REVIEW')

    def test_incomplete_process_and_lock_observation_suppresses_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            host = HostFixture(Path(directory))
            (host.proc_root / 'locks').unlink()
            snapshot = host.collect(self.collector)
            self.assertFalse(snapshot['operation_observation']['process_scan_complete'])
            self.assertFalse(snapshot['references_complete'])
            result = plan(snapshot)
            self.assertFalse(any(item['classification'] == 'DELETE_CANDIDATE'
                                 for items in result['nodes'].values() for item in items))

    def test_metadata_symlink_is_rejected_without_reading_external_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            host = HostFixture(Path(directory))
            metadata = host.paths['releases'] / OLD / 'rollback-images.txt'
            metadata.unlink()
            try:
                metadata.symlink_to(host.paths['shared'] / '.env')
            except OSError:
                self.skipTest('OS does not permit an individual file symlink')
            original_open = Path.open
            reads = []
            def watch_open(path, *args, **kwargs):
                if path == host.paths['shared'] / '.env':
                    reads.append(path)
                return original_open(path, *args, **kwargs)
            with patch.object(Path, 'open', watch_open):
                snapshot = host.collect(self.collector)
            result = plan(snapshot)
            self.assertEqual(reads, [])
            self.assertEqual(node(result, 'releases', OLD)['classification'], 'NEEDS_REVIEW')
            self.assertNotIn(host.secret, json.dumps(snapshot))

    def test_changed_docker_inventory_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            host = HostFixture(Path(directory))
            image_calls = 0
            def racing_docker(kind, identifiers=()):
                nonlocal image_calls
                result = host.docker(kind, identifiers)
                if kind == 'images':
                    image_calls += 1
                    if image_calls > 1:
                        result.pop()
                return result
            snapshot = host.collect(self.collector, docker=racing_docker)
            self.assertGreaterEqual(image_calls, 2)
            self.assertFalse(snapshot['references_complete'])
            result = plan(snapshot)
            self.assertFalse(any(item['classification'] == 'DELETE_CANDIDATE'
                                 for items in result['nodes'].values() for item in items))

    def test_changed_reference_file_with_same_inventory_names_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            host = HostFixture(Path(directory))
            image_calls = 0
            def racing_docker(kind, identifiers=()):
                nonlocal image_calls
                if kind == 'images':
                    image_calls += 1
                    if image_calls > 1:
                        path = host.paths['releases'] / OLD / 'rollback-images.txt'
                        before = path.stat()
                        write_fixture_text(path, (image_id('e') + '\n') * 3)
                        # Coarse filesystem clocks can otherwise hide a same-size
                        # fixture write in one tick. Explicitly advance its time.
                        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
                return host.docker(kind, identifiers)
            snapshot = host.collect(self.collector, docker=racing_docker)
            self.assertFalse(snapshot['references_complete'])
            self.assertIn('generation_content_changed_during_observation', snapshot['collection_errors'])
            result = plan(snapshot)
            self.assertFalse(any(item['classification'] == 'DELETE_CANDIDATE'
                                 for items in result['nodes'].values() for item in items))

    def test_docker_capability_rejects_mutating_or_arbitrary_requests_before_spawn(self):
        reader = self.collector.DockerReader()
        with patch('subprocess.run') as run:
            for kind in ('rm', 'rmi', 'prune', 'build', 'exec', 'restart', 'image prune',
                         'system prune', 'anything; rm -rf /'):
                with self.subTest(kind=kind), self.assertRaises((ValueError, RuntimeError)):
                    reader(kind)
            with self.assertRaises((ValueError, RuntimeError)):
                reader('images', ('--format={{.Config.Env}}; rm -rf /',))
            run.assert_not_called()

    def test_cli_has_no_apply_delete_or_policy_override_surface(self):
        from contextlib import redirect_stderr
        for flag in ('--apply', '--delete', '--prune', '--command=rm', '--backup-latest=0'):
            with self.subTest(flag=flag), patch.object(self.collector, 'collect_snapshot') as collect:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    self.collector.main([flag])
                self.assertNotEqual(error.exception.code, 0)
                collect.assert_not_called()

    def test_private_docker_process_boundary_also_rejects_mutating_argv(self):
        reader = self.collector.DockerReader()
        forbidden = [
            ['rm', '/fixture'], ['git', 'worktree', 'remove', '/fixture'],
            ['docker', 'image', 'rm', image_id('a')], ['docker', 'image', 'prune'],
            ['docker', 'system', 'prune'], ['docker', 'build', '.'],
            ['docker', 'exec', 'fixture', 'sh'], ['docker', 'restart', 'fixture'],
            ['docker', 'image', 'inspect', '--format={{.Config.Env}}'],
        ]
        with patch('subprocess.run') as run:
            for argv in forbidden:
                with self.subTest(argv=argv), self.assertRaises(ValueError):
                    reader._run(argv)
            run.assert_not_called()

    def test_nonregular_and_oversized_metadata_are_rejected_before_open(self):
        for mode, size in ((stat.S_IFIFO, 0), (stat.S_IFDIR, 0),
                           (stat.S_IFLNK, 10), (stat.S_IFREG, 65537)):
            fake = types.SimpleNamespace(lstat=lambda: types.SimpleNamespace(
                st_mode=mode, st_size=size))
            with self.subTest(mode=mode, size=size), patch.object(os, 'open') as opened:
                with self.assertRaises(ValueError):
                    self.collector.read_metadata(fake)
                opened.assert_not_called()

    def test_metadata_open_identity_change_is_rejected_without_leaking_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'metadata'
            path.write_text('SECRET_DO_NOT_REPORT')
            before = path.stat()
            changed = types.SimpleNamespace(st_dev=before.st_dev,
                                            st_ino=before.st_ino + 1)
            with patch.object(os, 'fstat', return_value=changed):
                with self.assertRaises(ValueError) as context:
                    self.collector.read_metadata(path)
            self.assertNotIn('SECRET', str(context.exception))

    def test_metadata_inplace_change_during_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'metadata'
            path.write_text('fixture')
            before = path.stat()
            fields = {name: getattr(before, name) for name in
                      ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
            fields['st_mtime_ns'] += 1
            changed = types.SimpleNamespace(**fields)
            with patch.object(os, 'fstat', side_effect=[before, changed]):
                with self.assertRaises(ValueError):
                    self.collector.read_metadata(path)

    def test_docker_discovered_identifiers_cannot_inject_options_or_commands(self):
        reader = self.collector.DockerReader()
        for invalid in ('--format={{.Config.Env}}', 'sha256:' + 'a' * 64 + ';rm', '/tmp/secret'):
            with self.subTest(invalid=invalid), patch('subprocess.run', return_value=
                    types.SimpleNamespace(returncode=0, stdout=invalid.encode())) as run:
                with self.assertRaises(ValueError):
                    reader('images')
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0],
                                 ['docker', 'image', 'ls', '--all', '--quiet', '--no-trunc'])
                self.assertIs(run.call_args.kwargs['shell'], False)


if __name__ == '__main__':
    unittest.main()
