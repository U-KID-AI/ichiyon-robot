"""Offline capacity fault injection; never connects to Docker or production."""

from contextlib import redirect_stdout
from dataclasses import replace
import io
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import ai_task_storage as storage

SHA = 'a' * 40
MIB = storage.MIB
GIB = storage.GIB


def fixture(phase='deploy-start', available=30 * GIB, inodes=1000000):
    capacity = storage.Capacity(1, 45 * GIB, available, 6000000, inodes)
    categories = tuple(name for name in storage.CATEGORIES
                       if phase == 'runner' or name not in ('source', 'worktrees'))
    return storage.Measurements(
        phase, storage.TreeSize(160 * MIB, 175 * MIB, 2000, 180 * MIB),
        storage.TreeSize(320 * MIB, 350 * MIB, 13500, 360 * MIB),
        1300 * MIB, phase == 'pre-stop', 15 * MIB, 475 * MIB,
        {name: capacity for name in categories})


class StorageTests(unittest.TestCase):
    def test_one_filesystem_sums_costs_but_counts_reserve_and_availability_once(self):
        measured = fixture()
        records = storage.evaluate(measured)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.available_bytes, 30 * GIB)
        reserve = (45 * GIB * 5 + 99) // 100
        self.assertEqual(record.required_bytes,
                         sum(value[0] for value in storage.requirements(measured).values()) + reserve)
        self.assertEqual(record.filesystem, 'releases+backups+shared+docker+temp+control')

    def test_low_bytes_fail_even_if_duplicate_mount_availability_would_pass(self):
        measured = fixture(available=3 * GIB)
        with self.assertRaises(storage.StorageError) as context:
            storage.evaluate(measured)
        self.assertEqual(context.exception.category, 'BYTES')
        self.assertEqual(context.exception.records[0].available_bytes, 3 * GIB)

    def test_separate_filesystems_cannot_borrow_free_space(self):
        measured = fixture()
        capacities = dict(measured.capacities)
        capacities['docker'] = storage.Capacity(2, 20 * GIB, GIB, 1000000, 500000)
        measured = replace(measured, capacities=capacities)
        with self.assertRaises(storage.StorageError) as context:
            storage.evaluate(measured)
        records = context.exception.records
        self.assertEqual(len(records), 2)
        self.assertGreater(records[1].required_bytes, records[1].available_bytes)
        self.assertEqual(records[1].filesystem, 'docker')

    def test_same_device_uses_least_available_sample(self):
        measured = fixture()
        capacities = dict(measured.capacities)
        capacities['shared'] = replace(capacities['shared'], available_bytes=2 * GIB)
        with self.assertRaises(storage.StorageError) as context:
            storage.evaluate(replace(measured, capacities=capacities))
        self.assertEqual(context.exception.records[0].available_bytes, 2 * GIB)

    def test_inode_exhaustion_blocks_despite_large_byte_headroom(self):
        with self.assertRaises(storage.StorageError) as context:
            storage.evaluate(fixture(inodes=1000))
        self.assertEqual(context.exception.category, 'INODES')
        self.assertGreater(context.exception.records[0].available_bytes,
                           context.exception.records[0].required_bytes)

    def test_post_build_rechecks_remaining_capacity_and_keeps_rollback_reserve(self):
        start = storage.evaluate(fixture())[0]
        stop = storage.evaluate(fixture('pre-stop'))[0]
        self.assertGreater(start.required_bytes, stop.required_bytes)
        # This host can write a backup, but cannot also retain rollback/safety.
        backup_only = storage.requirements(fixture('pre-stop'))['backups'][0]
        self.assertGreater(stop.required_bytes, backup_only + 512 * MIB)
        with self.assertRaises(storage.StorageError):
            storage.evaluate(fixture('pre-stop', available=backup_only + 512 * MIB))

    def test_prebuild_actual_image_and_source_growth_raise_estimate(self):
        initial = fixture('pre-build')
        enlarged = replace(initial, image_bytes=3 * GIB,
                           source=storage.TreeSize(2 * GIB, 2 * GIB, 10000, 2 * GIB))
        self.assertGreater(storage.evaluate(enlarged)[0].required_bytes,
                           storage.evaluate(initial)[0].required_bytes)

    def test_existing_target_image_avoids_build_budget_but_not_rollback(self):
        measured = fixture('pre-build')
        missing = storage.requirements(measured)
        existing = storage.requirements(replace(measured, target_image_exists=True))
        self.assertEqual(missing['docker'][0] - existing['docker'][0],
                         3 * measured.image_bytes)
        self.assertGreaterEqual(existing['docker'][0], 512 * MIB)

    def test_runner_covers_worktrees_and_jdk_temporary_extraction(self):
        runner = storage.requirements(fixture('runner'))
        deploy = storage.requirements(fixture())
        self.assertGreater(runner['worktrees'][0], 0)
        self.assertEqual(runner['source'][0], 360 * MIB)
        self.assertEqual(runner['temp'][0] - deploy['temp'][0], 950 * MIB)

    def test_runner_fetch_checks_separately_mounted_source_repository(self):
        measured = fixture('runner')
        capacities = dict(measured.capacities)
        capacities['source'] = storage.Capacity(12, 10 * GIB, 512 * MIB, 100000, 90000)
        with self.assertRaises(storage.StorageError) as context:
            storage.evaluate(replace(measured, capacities=capacities))
        source = next(record for record in context.exception.records if record.filesystem == 'source')
        self.assertGreater(source.required_bytes, source.available_bytes)

    def test_same_sha_reconciliation_uses_small_measured_budget(self):
        measured = fixture('reconcile', available=GIB, inodes=10000)
        record = storage.evaluate(measured)[0]
        self.assertLess(record.required_bytes, GIB)
        costs = storage.requirements(measured)
        for category in ('backups', 'shared', 'docker'):
            self.assertEqual(costs[category], [0, 0])
        with self.assertRaises(storage.StorageError):
            storage.evaluate(fixture(available=GIB, inodes=10000))

    def test_measurement_failure_is_closed_and_strips_paths_and_secrets(self):
        with patch.object(storage, 'measure', side_effect=PermissionError('/secret/password=hunter2')):
            with self.assertRaises(storage.StorageError) as context:
                storage.check_storage('deploy-start', SHA)
        self.assertEqual(str(context.exception),
                         'INSUFFICIENT_STORAGE phase=deploy-start reason=MEASUREMENT_UNAVAILABLE')
        self.assertNotIn('secret', '\n'.join(context.exception.format_lines()))
        self.assertIn('STORAGE_CHECK_PHASE=deploy-start', context.exception.format_lines())
        self.assertIn('STORAGE_CHECK_PHASE=deploy-start', storage.safe_diagnostics(
            '\n'.join(context.exception.format_lines())))

    def test_runner_exception_contains_safe_numeric_capacity_diagnostics(self):
        with self.assertRaises(storage.StorageError) as context:
            storage.evaluate(fixture('runner', available=7))
        self.assertIn('STORAGE_AVAILABLE_BYTES=7', str(context.exception))
        self.assertIn('STORAGE_REQUIRED_BYTES=', str(context.exception))
        invalid = storage.StorageRecord('runner', '/secret/password=value', 1, 2, 3, 4)
        error = storage.StorageError('runner', 'BYTES', [invalid])
        self.assertNotIn('secret', str(error))
        self.assertEqual(error.category, 'MEASUREMENT_UNAVAILABLE')

    def test_cli_failure_uses_machine_readable_safe_fields_only(self):
        output = io.StringIO()
        with patch.object(storage, 'measure', return_value=fixture(available=1)), redirect_stdout(output):
            status = storage.main(['deploy-start', SHA])
        self.assertEqual(status, 1)
        self.assertIn('STORAGE_AVAILABLE_BYTES=1', output.getvalue())
        self.assertIn('STORAGE_REASON=BYTES', output.getvalue())
        self.assertIn('DEPLOY_ERROR=INSUFFICIENT_STORAGE', output.getvalue())

    def test_cli_rejects_arbitrary_request_without_probing(self):
        with patch.object(storage, 'measure') as measure, redirect_stdout(io.StringIO()):
            self.assertEqual(storage.main(['password=secret', SHA]), 1)
            self.assertEqual(storage.main(['pre-stop', SHA, '0']), 1)
        measure.assert_not_called()

    def test_safe_diagnostics_rejects_arbitrary_path_and_command_output(self):
        record = storage.evaluate(fixture())[0].format_line()
        output = storage.safe_diagnostics('\n'.join([
            'password=secret', '/home/arbitrary/file', record,
            record.replace('STORAGE_FS=releases+', 'STORAGE_FS=/secret+'),
            record + ' token=secret', 'STORAGE_REASON=BYTES']))
        self.assertEqual(output, 'DEPLOY_ERROR=INSUFFICIENT_STORAGE\n' + record + '\nSTORAGE_REASON=BYTES')
        self.assertEqual(storage.safe_diagnostics('secret'),
                         'DEPLOY_ERROR=INSUFFICIENT_STORAGE\nSTORAGE_REASON=MEASUREMENT_UNAVAILABLE')

    def test_statvfs_uses_unprivileged_available_bytes_and_inodes(self):
        fake = types.SimpleNamespace(f_frsize=4096, f_blocks=10000, f_bavail=200,
                                     f_bfree=999, f_files=5000, f_favail=400,
                                     f_ffree=999, f_flag=0)
        path = types.SimpleNamespace(stat=lambda: types.SimpleNamespace(st_dev=17))
        with patch.object(storage.os, 'statvfs', return_value=fake, create=True):
            capacity = storage._capacity(path)
        self.assertEqual(capacity.available_bytes, 200 * 4096)
        self.assertEqual(capacity.available_inodes, 400)

    def test_unavailable_inode_measurement_fails_closed(self):
        fake = types.SimpleNamespace(f_frsize=4096, f_blocks=10000, f_bavail=200,
                                     f_files=0, f_favail=0, f_flag=0)
        path = types.SimpleNamespace(stat=lambda: types.SimpleNamespace(st_dev=17))
        with patch.object(storage.os, 'statvfs', return_value=fake, create=True):
            with self.assertRaises(ValueError):
                storage._capacity(path)

    def test_metadata_tar_bound_includes_sparse_apparent_size_and_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / 'sparse').open('wb') as stream:
                stream.seek(2 * MIB)
                stream.write(b'x')
            (root / 'small').write_bytes(b'abc')
            value = storage.tree_size(root, dereference=True)
        self.assertEqual(value.payload, 2 * MIB + 4)
        self.assertEqual(value.entries, 3)
        self.assertGreater(value.tar_bytes, value.payload + 3 * 512)
        self.assertEqual(value.tar_bytes % 10240, 0)

    def test_directory_symlink_cycle_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            try:
                (root / 'loop').symlink_to(root, target_is_directory=True)
            except OSError:
                self.skipTest('OS does not permit fixture symlinks')
            with self.assertRaises(ValueError):
                storage.tree_size(root, dereference=True)

    def test_source_root_symlink_counts_contents_while_interior_link_stays_link(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'actual'
            root.mkdir()
            (root / 'content').write_bytes(b'content' * 1000)
            try:
                (base / 'alias').symlink_to(root, target_is_directory=True)
                (root / 'interior').symlink_to(root / 'content')
            except OSError:
                self.skipTest('OS does not permit fixture symlinks')
            real = storage.tree_size(root)
            alias = storage.tree_size(base / 'alias')
            self.assertEqual(real, alias)
            self.assertGreater(real.payload, 7000)
            self.assertLess(real.payload, 14000)

    def test_failed_image_listing_is_not_treated_as_missing_image(self):
        with patch.object(storage, '_command', side_effect=OSError('Docker unavailable')):
            with self.assertRaises(OSError):
                storage._optional_image('ichiyon-robot-app:' + SHA)

    def test_same_current_sha_selects_reconcile_without_db_or_image_probe(self):
        class CurrentRelease:
            parent = storage.RELEASES
            name = SHA
            def __truediv__(self, part):
                return Path('/unused') / part
        fake_current = types.SimpleNamespace(resolve=lambda strict: CurrentRelease())
        capacity = storage.Capacity(1, 45 * GIB, GIB, 6000000, 10000)
        with patch.object(storage, 'CURRENT', fake_current), patch.object(
                storage.Path, 'resolve', return_value=storage.RELEASES), patch.object(
                storage.Path, 'is_absolute', return_value=True), patch.object(
                storage.Path, 'exists', return_value=False), patch.object(
                storage, 'tree_size', return_value=fixture().source) as tree, patch.object(
                storage, '_capacity', return_value=capacity), patch.object(
                storage, '_command', return_value='/var/lib/docker') as command:
            result = storage.check_storage('deploy-start', SHA)
        self.assertEqual(result[0].phase, 'reconcile')
        self.assertEqual(tree.call_count, 1)
        command.assert_called_once_with(['docker', 'info', '--format', '{{.DockerRootDir}}'])

    def test_split_control_filesystem_cannot_fill_while_data_mounts_have_space(self):
        measured = fixture()
        capacities = dict(measured.capacities)
        capacities['control'] = storage.Capacity(8, 10 * GIB, 512 * MIB, 100000, 90000)
        with self.assertRaises(storage.StorageError) as context:
            storage.evaluate(replace(measured, capacities=capacities))
        control = next(record for record in context.exception.records if record.filesystem == 'control')
        self.assertEqual(control.required_bytes, GIB + MIB)
        self.assertGreater(control.required_bytes, control.available_bytes)

    def test_reconcile_ignores_full_separate_backup_and_shared_filesystems(self):
        measured = fixture('reconcile', available=GIB, inodes=10000)
        capacities = dict(measured.capacities)
        capacities['backups'] = storage.Capacity(2, GIB, 0, 10000, 0)
        capacities['shared'] = storage.Capacity(3, GIB, 0, 10000, 0)
        records = storage.evaluate(replace(measured, capacities=capacities))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].filesystem, 'releases+docker+temp+control')


if __name__ == '__main__':
    unittest.main()
