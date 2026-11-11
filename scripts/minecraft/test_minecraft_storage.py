"""Local fixture-only checks: no server, production tree, Docker, or Git."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import minecraft_storage as storage


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'live'
        (self.source / 'behavior_packs/example/scripts').mkdir(parents=True)
        (self.source / 'behavior_packs/example/empty').mkdir()
        (self.source / 'behavior_packs/example/manifest.json').write_text('{"version":[1,2,3]}')
        (self.source / 'behavior_packs/example/scripts/main.js').write_text('original implementation')
        (self.source / 'permissions.json').write_text('{"permissions":[]}')
        self.paths = ['behavior_packs/example', 'permissions.json']
        self.store = self.root / 'private-store'
        self.manifest = self.root / 'snapshot.json'

    def snapshot(self, manifest=None):
        return storage.snapshot_paths(self.source, self.paths, manifest or self.manifest, self.store)

    def resign(self, edit):
        envelope = json.loads(self.manifest.read_text())
        edit(envelope['body'])
        envelope['sha256'] = hashlib.sha256(storage._canonical(envelope['body'])).hexdigest()
        self.manifest.write_text(json.dumps(envelope))

    def test_repeated_snapshot_uses_existing_blobs_and_restore_is_independent(self):
        first = self.snapshot()
        blobs = sorted(self.store.glob('objects/*/*'))
        second = self.snapshot(self.root / 'second.json')
        self.assertEqual(second['new_blob_bytes'], 0)
        self.assertEqual(first['logical_bytes'], second['reused_blob_bytes'])
        self.assertEqual(blobs, sorted(self.store.glob('objects/*/*')))
        manifest = storage.validate_snapshot(self.manifest, self.store)
        self.assertEqual(manifest['paths'], self.paths)
        target = self.root / 'restore'
        receipt = storage.restore_snapshot(self.manifest, self.store, target)
        self.assertFalse(receipt['shared_inodes'])
        self.assertTrue((target / 'behavior_packs/example/empty').is_dir())
        main = target / 'behavior_packs/example/scripts/main.js'
        entry = next(row for row in manifest['entries'] if row['path'].endswith('main.js'))
        blob = storage._blob(self.store, entry['sha256'])
        self.assertFalse(os.path.samefile(main, blob))
        self.assertFalse(os.path.samefile(main, self.source / entry['path']))
        main.write_text('edited restored live implementation')
        self.assertEqual(blob.read_text(), 'original implementation')
        self.assertEqual((self.source / entry['path']).read_text(), 'original implementation')
        storage.validate_snapshot(self.manifest, self.store)

    def test_recovery_context_holds_lock_until_restore_and_rejects_late_use(self):
        snapshot = self.snapshot()
        with storage.snapshot_recovery(self.manifest, self.store,
                                       expected_checksum=snapshot['snapshot_sha256']) as restore:
            with self.assertRaises(storage.StorageIntegrityError):
                storage.validate_snapshot(self.manifest, self.store)
            restore(self.root / 'recovery')
        storage.validate_snapshot(self.manifest, self.store)
        with self.assertRaisesRegex(storage.StorageIntegrityError, 'no longer active'):
            restore(self.root / 'late-restore')
        self.assertFalse((self.root / 'late-restore').exists())

    def test_recovery_checksum_is_checked_before_the_caller_changes_live_data(self):
        self.snapshot()
        with self.assertRaisesRegex(storage.StorageIntegrityError, 'changed before'):
            with storage.snapshot_recovery(self.manifest, self.store, expected_checksum='0' * 64):
                self.fail('Caller must not enter its live mutation region')
        self.assertEqual((self.source / 'permissions.json').read_text(), '{"permissions":[]}')

    def test_mode_mtime_and_selected_subtree_restore(self):
        source = self.source / 'behavior_packs/example/scripts/main.js'
        os.chmod(source, 0o640)
        os.utime(source, ns=(1_700_000_000_123_000_000,) * 2)
        self.snapshot()
        destination = self.root / 'selected'
        destination.mkdir()
        (destination / 'unrelated.txt').write_text('keep')
        storage.restore_snapshot(self.manifest, self.store, destination,
                                 paths=['behavior_packs/example/scripts'])
        restored = destination / 'behavior_packs/example/scripts/main.js'
        self.assertEqual(restored.stat().st_mtime_ns, source.stat().st_mtime_ns)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(restored.stat().st_mode), 0o640)
        self.assertEqual((destination / 'unrelated.txt').read_text(), 'keep')
        self.assertFalse((destination / 'permissions.json').exists())

    def test_equal_bytes_with_different_modes_share_blob_not_restored_inode(self):
        (self.source / 'public.txt').write_text('same bytes')
        (self.source / 'private.txt').write_text('same bytes')
        os.chmod(self.source / 'public.txt', 0o644)
        os.chmod(self.source / 'private.txt', 0o600)
        result = storage.snapshot_paths(self.source, ['public.txt', 'private.txt'], self.manifest, self.store)
        self.assertEqual(result['new_blob_files'], 1)
        self.assertEqual(result['new_blob_bytes'], len(b'same bytes'))
        destination = self.root / 'restored'
        storage.restore_snapshot(self.manifest, self.store, destination)
        self.assertFalse(os.path.samefile(destination / 'public.txt', destination / 'private.txt'))
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE((destination / 'public.txt').stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE((destination / 'private.txt').stat().st_mode), 0o600)

    def test_case_aliased_overlapping_roots_are_rejected(self):
        with self.assertRaisesRegex(storage.StorageIntegrityError, 'Overlapping'):
            storage.snapshot_paths(self.source, ['BEHAVIOR_PACKS/example', 'behavior_packs'],
                                   self.manifest, self.store)

    def test_empty_initial_deployment_snapshot_restores_nothing(self):
        result = storage.snapshot_paths(self.source, [], self.manifest, self.store)
        self.assertEqual(result['files'], 0)
        self.assertEqual(result['new_blob_bytes'], 0)
        self.assertEqual(result['new_blob_allocated_bytes'], 0)
        manifest = storage.validate_snapshot(self.manifest, self.store)
        self.assertEqual(manifest['paths'], [])
        self.assertEqual(manifest['entries'], [])
        destination = self.root / 'existing-live'
        destination.mkdir()
        (destination / 'keep.txt').write_text('unrelated live content')
        restored = storage.restore_snapshot(self.manifest, self.store, destination, paths=[])
        self.assertEqual(restored['files'], 0)
        self.assertEqual((destination / 'keep.txt').read_text(), 'unrelated live content')
        absent = self.root / 'absent-live'
        storage.restore_snapshot(self.manifest, self.store, absent, paths=[])
        self.assertFalse(absent.exists())

    def test_empty_snapshot_accepts_absent_data_root_without_creating_it(self):
        absent_source = self.root / 'not-created-data'
        result = storage.snapshot_paths(absent_source, [], self.manifest, self.store)
        self.assertEqual(result['files'], 0)
        self.assertFalse(absent_source.exists())
        self.assertEqual(storage.validate_snapshot(self.manifest, self.store)['paths'], [])
        with self.assertRaises(storage.StorageIntegrityError):
            storage.snapshot_paths(absent_source, ['permissions.json'], self.root / 'nonempty.json', self.store)
        self.assertFalse((self.root / 'nonempty.json').exists())
        self.assertFalse(absent_source.exists())

    def test_existing_private_runtime_cache_coexists_with_first_cas_snapshot(self):
        storage._private_directory(self.store)
        runtime = self.store / 'runtime'
        runtime.mkdir(mode=0o700)
        executable_directory = runtime / ('a' * 64)
        executable_directory.mkdir(mode=0o700)
        executable = executable_directory / 'bedrock_server-fixture'
        executable.write_bytes(b'pre-existing independent verified runtime')
        os.chmod(executable, 0o500)
        before = (executable.read_bytes(), executable.stat().st_ino, executable.stat().st_mtime_ns)
        self.assertFalse((self.store / 'store.json').exists())
        self.snapshot()
        storage.validate_snapshot(self.manifest, self.store)
        self.assertTrue((self.store / 'store.json').is_file())
        self.assertEqual(before, (executable.read_bytes(), executable.stat().st_ino, executable.stat().st_mtime_ns))
        self.assertEqual(len(list(runtime.rglob('bedrock_server-fixture'))), 1)

    def windows_dacl(self, path, replacement=None):
        api = storage._windows_security_api()
        ctypes, types, advapi, kernel = api
        descriptor = ctypes.c_void_p()
        if replacement is not None:
            self.assertTrue(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                replacement, 1, ctypes.byref(descriptor), None))
            try:
                self.assertTrue(advapi.SetFileSecurityW(str(path), 0x80000004, descriptor))
            finally:
                kernel.LocalFree(descriptor)
        descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        self.assertEqual(advapi.GetNamedSecurityInfoW(str(path), 1, 4, None, None,
                                                    ctypes.byref(dacl), None, ctypes.byref(descriptor)), 0)
        text = types.LPWSTR()
        advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            ctypes.c_void_p, types.DWORD, types.DWORD, ctypes.POINTER(types.LPWSTR), ctypes.c_void_p]
        try:
            self.assertTrue(advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, 1, 4, ctypes.byref(text), None))
            return text.value
        finally:
            if text:
                kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
            kernel.LocalFree(descriptor)

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL contract')
    def test_existing_inherited_windows_acl_is_rejected_without_changes(self):
        self.store.mkdir()
        before = self.windows_dacl(self.store)
        with self.assertRaisesRegex(storage.StorageIntegrityError, 'Windows store ACL'):
            self.snapshot()
        self.assertEqual(self.windows_dacl(self.store), before)
        self.assertFalse((self.store / 'store.json').exists())
        self.assertFalse((self.store / 'objects').exists())
        self.assertFalse((self.store / '.snapshot.lock').exists())
        self.assertFalse(self.manifest.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL contract')
    def test_private_windows_acl_is_accepted_and_later_wide_acl_blocks_reads(self):
        self.snapshot()
        storage._verify_windows_private_acl(self.store)
        private = self.windows_dacl(self.store)
        storage.validate_snapshot(self.manifest, self.store)
        self.assertEqual(self.windows_dacl(self.store), private)
        sid = storage._windows_current_sid()
        wide = 'D:P(A;OICI;FA;;;' + sid + ')(A;OICI;FA;;;SY)(A;OICI;GR;;;WD)'
        before = self.windows_dacl(self.store, wide)
        with self.assertRaisesRegex(storage.StorageIntegrityError, 'another principal'):
            storage.validate_snapshot(self.manifest, self.store)
        target = self.root / 'restore'
        with self.assertRaisesRegex(storage.StorageIntegrityError, 'another principal'):
            storage.restore_snapshot(self.manifest, self.store, target)
        self.assertFalse(target.exists())
        self.assertEqual(self.windows_dacl(self.store), before)

    def test_corrupt_blob_fails_before_writing_any_restore_output(self):
        self.snapshot()
        blob = next(self.store.glob('objects/*/*'))
        os.chmod(blob, 0o600)
        blob.write_bytes(b'broken')
        target = self.root / 'restore'
        with self.assertRaises(storage.StorageIntegrityError):
            storage.restore_snapshot(self.manifest, self.store, target)
        self.assertFalse(target.exists())
        with self.assertRaises(storage.StorageIntegrityError):
            self.snapshot(self.root / 'refuse-repair.json')
        self.assertEqual(blob.read_bytes(), b'broken')

    def test_uncommitted_manifest_store_mismatch_and_overwrite_are_rejected(self):
        self.snapshot()
        other = self.root / 'other-store'
        storage.snapshot_paths(self.source, self.paths, self.root / 'other.json', other)
        with self.assertRaises(storage.StorageIntegrityError):
            storage.validate_snapshot(self.manifest, other)
        with self.assertRaises(storage.StorageIntegrityError):
            self.snapshot()
        incomplete = self.root / 'incomplete.json'
        incomplete.write_text('{"body":{}}')
        with self.assertRaises(storage.StorageIntegrityError):
            storage.validate_snapshot(incomplete, self.store)
        destination = self.root / 'restore'
        destination.mkdir()
        (destination / 'permissions.json').write_text('untouched')
        with self.assertRaises(storage.StorageIntegrityError):
            storage.restore_snapshot(self.manifest, self.store, destination)
        self.assertEqual((destination / 'permissions.json').read_text(), 'untouched')
        self.assertFalse((destination / 'behavior_packs').exists())

    def test_source_hardlink_and_symlink_escape_are_rejected(self):
        source = self.source / 'permissions.json'
        os.link(source, self.root / 'outside-hardlink')
        with self.assertRaises(storage.StorageIntegrityError):
            self.snapshot()
        (self.root / 'outside-hardlink').unlink()
        try:
            (self.source / 'unsafe').symlink_to(self.root, target_is_directory=True)
        except OSError:
            return  # Windows accounts may lack symlink creation privilege.
        with self.assertRaises(storage.StorageIntegrityError):
            storage.snapshot_paths(self.source, ['unsafe'], self.manifest, self.store)

    def test_manifest_traversal_case_collision_and_missing_parent_are_rejected(self):
        self.snapshot()
        original = self.manifest.read_bytes()
        for mutation in (
            lambda body: body['entries'][0].update(path='../escape'),
            lambda body: body['entries'].append(dict(body['entries'][0], path=body['entries'][0]['path'].upper())),
            lambda body: body['entries'].remove(next(row for row in body['entries'] if row['path'].endswith('/scripts'))),
        ):
            self.manifest.write_bytes(original)
            self.resign(mutation)
            with self.assertRaises(storage.StorageIntegrityError):
                storage.validate_snapshot(self.manifest, self.store)
        self.assertFalse((self.root / 'escape').exists())

    def test_busy_store_is_rejected_and_lockfile_is_retained(self):
        self.snapshot()
        lock = self.store / '.snapshot.lock'
        identity = lock.stat().st_ino
        with storage._locked_store(self.store):
            with self.assertRaisesRegex(storage.StorageIntegrityError, 'busy'):
                self.snapshot(self.root / 'second.json')
        self.assertEqual(lock.stat().st_ino, identity)
        self.assertFalse((self.root / 'second.json').exists())

    def test_process_crash_releases_lock_and_keeps_snapshot_recoverable(self):
        self.snapshot()
        code = ('import os,sys; import minecraft_storage as s; '
                'lock=s._locked_store(sys.argv[1]); lock.__enter__(); os._exit(7)')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(self.store)],
                                cwd=Path(storage.__file__).parent, capture_output=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        storage.validate_snapshot(self.manifest, self.store)
        storage.restore_snapshot(self.manifest, self.store, self.root / 'after-crash')
        second = self.snapshot(self.root / 'second.json')
        self.assertEqual(second['new_blob_bytes'], 0)

    def test_mutation_before_commit_does_not_publish_a_manifest(self):
        real_entries = storage._entries
        calls = 0

        def changed(source, paths):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.source / 'permissions.json').write_text('changed')
            return real_entries(source, paths)

        with patch.object(storage, '_entries', side_effect=changed):
            with self.assertRaisesRegex(storage.StorageIntegrityError, 'changed'):
                self.snapshot()
        self.assertFalse(self.manifest.exists())
        with storage._locked_store(self.store):
            pass

    def test_blob_hardlink_is_rejected_before_restore(self):
        self.snapshot()
        blob = next(self.store.glob('objects/*/*'))
        os.link(blob, self.root / 'blob-alias')
        with self.assertRaises(storage.StorageIntegrityError):
            storage.validate_snapshot(self.manifest, self.store)


class CapacityTests(unittest.TestCase):
    def test_thresholds_large_test_gate_and_recovery_exemption(self):
        with TemporaryDirectory() as temp:
            usage = shutil._ntuple_diskusage(100_000_000_000, 91_000_000_000, 9_000_000_000)
            with patch.object(storage.shutil, 'disk_usage', return_value=usage):
                with self.assertRaises(storage.StorageCapacityError) as context:
                    storage.check_capacity(temp, platform='ubuntu', purpose='large_test')
                self.assertFalse(context.exception.result['allowed'])
                recovery = storage.check_capacity(temp, platform='ubuntu', purpose='recovery', required_bytes=20_000_000_000)
                self.assertTrue(recovery['allowed'])
                self.assertTrue(recovery['recovery_exempt'])
                self.assertIn('LOW_DISK_SPACE', recovery['warnings'])
                windows = storage.check_capacity(temp, platform='windows', purpose='large_test')
                self.assertTrue(windows['allowed'])
                self.assertEqual(windows['warning_threshold_bytes'], 100_000_000_000)
                self.assertIn('LOW_DISK_SPACE', windows['warnings'])
            with patch.object(storage.shutil, 'disk_usage', return_value=usage._replace(free=11_000_000_000)):
                with self.assertRaises(storage.StorageCapacityError):
                    storage.check_capacity(temp, platform='linux', purpose='large_test', required_bytes=2_000_000_000)

    def test_receipt_scopes_one_operation_and_can_resume_baseline(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'unrelated-private-material').write_bytes(b'untouched')
            operation = root / 'operation'
            receipt = storage.StorageReceipt(operation, 'test-operation')
            baseline = json.loads(json.dumps(receipt.before))
            operation.mkdir()
            (operation / 'proof.json').write_bytes(b'12345')
            resumed = storage.StorageReceipt(operation, 'test-operation', before=baseline)
            result = resumed.finish(retained_environments=[operation], recovery_sources=['retained-backup'],
                                    new_blob_bytes=7)
            self.assertEqual(result['after']['usage']['logical_bytes'], 5)
            self.assertEqual(result['delta']['logical_bytes'], 5)
            self.assertEqual(result['metadata']['new_blob_bytes'], 7)
            self.assertEqual(result['recovery_sources'], ['retained-backup'])
            storage.write_receipt(operation / 'storage-receipt.json', result)
            self.assertEqual(json.loads((operation / 'storage-receipt.json').read_text()), result)
            with self.assertRaises(ValueError):
                storage.StorageReceipt(root / 'another', 'other', before=baseline)
            with self.assertRaises(ValueError):
                storage.StorageReceipt(operation, 'test-operation', before={})


if __name__ == '__main__':
    unittest.main()
