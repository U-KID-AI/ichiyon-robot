"""Small fixtures only: no Docker, SSH, live worlds or environment copies."""
from contextlib import nullcontext
from io import StringIO
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import check_junket_isolated_resilience as isolated
import junket_native_release as native
import minecraft_storage as storage_core
from minecraft_storage import StorageCapacityError, snapshot_paths, validate_snapshot


class IsolatedStorageTests(unittest.TestCase):
    def test_binary_cache_copies_once_and_mount_is_read_only(self):
        with TemporaryDirectory() as temporary:
            project = Path(temporary)
            payload = b'fixture executable'
            expected = hashlib.sha256(payload).hexdigest()
            def copy(*args, **kwargs):
                self.assertEqual(args[:2], ('cp', isolated.PRODUCTION + ':/data/' + isolated.BINARY))
                Path(args[2]).write_bytes(payload)
            contract = {'/data/' + isolated.BINARY: {'sha256': expected}}
            # Runtime generation is Linux-only; on Windows the shared CAS test
            # must also create its new root with an explicit private ACL.
            storage_core._private_directory(project / '.minecraft-storage')
            with patch.object(isolated, 'PROJECT', project), patch.object(isolated, 'EXECUTABLES', contract), \
                    patch.object(isolated, 'exclusive_file_lock', side_effect=lambda path: nullcontext()), \
                    patch.object(isolated, 'docker', side_effect=copy) as docker:
                binary, first_bytes = isolated.prepare_shared_binary()
                repeated, next_bytes = isolated.prepare_shared_binary()
                self.assertEqual(binary, repeated)
                self.assertEqual(first_bytes, len(payload))
                self.assertEqual(next_bytes, 0)
                self.assertEqual(docker.call_count, 1)
                source = project / 'snapshot-source'
                (source / 'pack').mkdir(parents=True)
                (source / 'pack/config.json').write_text('{}')
                manifest = project / 'snapshot.json'
                snapshot_paths(source, ['pack'], manifest, project / '.minecraft-storage')
                validate_snapshot(manifest, project / '.minecraft-storage')
                self.assertEqual(binary.read_bytes(), payload, 'first runtime cache must coexist with later CAS snapshots')
                with patch.object(isolated.os, 'getuid', return_value=1001, create=True), \
                        patch.object(isolated.os, 'getgid', return_value=1001, create=True):
                    args = isolated.run_arguments('junket-isolated-test', 'token', 'operation', project / 'clone/data', isolated.IMAGE, binary)
                self.assertIn('type=bind,source=' + str(binary) + ',target=/data/' + isolated.BINARY + ',readonly', args)
                self.assertNotIn(str(project / 'data'), ' '.join(args))
                binary.chmod(0o755)
                binary.write_bytes(b'corrupt')
                binary.chmod(0o555)
                with self.assertRaisesRegex(ValueError, 'hash changed'):
                    isolated.prepare_shared_binary()
                self.assertEqual(docker.call_count, 1, 'corruption must not be overwritten')

    def test_shared_binary_mount_cannot_be_writable_or_foreign(self):
        with TemporaryDirectory() as temporary:
            project = Path(temporary)
            clone = project / 'clone'
            clone.mkdir()
            with patch.object(isolated, 'PROJECT', project):
                binary = isolated.binary_cache_path()
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b'fixture')
                binary.chmod(0o555)
                isolated.write_json(clone / 'runtime-binary.json', {'schema': 1, 'path': str(binary),
                    'sha256': isolated.EXECUTABLES['/data/' + isolated.BINARY]['sha256'], 'bytes': 7})
                server = isolated.IsolatedServer(clone, 'operation', 'token')
                server.created = True
                mount = {'Type': 'bind', 'Destination': '/data/' + isolated.BINARY, 'Source': str(binary), 'RW': False}
                info = {'Name': '/' + server.name, 'Image': isolated.IMAGE,
                        'Config': {'Labels': {isolated.LABEL: 'token', isolated.LABEL + '.operation': 'operation'}},
                        'HostConfig': {'NetworkMode': 'none', 'PortBindings': {}},
                        'Mounts': [{'Type': 'bind', 'Destination': '/data', 'Source': str(clone / 'data')}, mount]}
                with patch.object(isolated, 'docker', side_effect=lambda *a, **k: json.dumps([info])):
                    server.inspect_owned()
                    mount['RW'] = True
                    with self.assertRaisesRegex(ValueError, 'read-only'):
                        server.inspect_owned()
                    mount['RW'] = False
                    mount['Source'] = str(project / 'data' / isolated.BINARY)
                    with self.assertRaisesRegex(ValueError, 'read-only'):
                        server.inspect_owned()
                self.assertEqual(server.binary_path(), binary)
            legacy = isolated.IsolatedServer(project / 'legacy', 'operation', 'old')
            self.assertEqual(legacy.binary_path(), project / 'legacy/data' / isolated.BINARY)

    def test_reuse_fingerprint_changes_with_code_stage_and_adapter_inputs(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = root / 'probe.py'
            tool.write_text('first')
            operation = root / 'operation'
            (operation / 'stage').mkdir(parents=True)
            (operation / 'stage/runtime.js').write_text('same')
            (operation / 'preflight.json').write_text('{}')
            class Adapter:
                value = 'original'
                def storage_fingerprint(self):
                    return {'probe': self.value}
            adapter = Adapter()
            with patch.object(isolated, '__file__', str(tool)):
                def key():
                    return isolated.reuse_fingerprint(operation, {'sha256': 'backup'}, None, None, {'resources_sha256': 'runtime'}, adapter)
                first = key()
                tool.write_text('changed code')
                self.assertNotEqual(first, key())
                tool.write_text('first')
                (operation / 'stage/runtime.js').write_text('changed stage')
                self.assertNotEqual(first, key())
                (operation / 'stage/runtime.js').write_text('same')
                adapter.value = 'changed probe input'
                self.assertNotEqual(first, key())
                self.assertIsNone(isolated.reuse_fingerprint(operation, {}, None, None, {}, object()))

    def cached_fixture(self, root):
        operation = '10000000-0000-4000-8000-000000000001'
        token = '1' * 32
        fingerprint = '2' * 64
        directory = root / (operation + '-' + token)
        for name in ('data', 'stage'):
            (directory / name).mkdir(parents=True)
            (directory / name / 'kept.txt').write_text(name)
        proof = {'operation_id': operation, 'directory': str(directory), 'container': 'junket-isolated-' + token,
                 'storage_fingerprint': fingerprint, 'issues': [], 'isolated_server_resilience_verified': True,
                 'production_modified': False, 'final_stop': {'graceful_stop': True},
                 'full_backup': {'path': str(root / 'kept-backup.tar.gz')},
                 'storage_environment': {name: isolated.tree_digest(directory / name) for name in ('data', 'stage')}}
        source = directory / 'isolated-resilience-proof.json'
        isolated.write_json(source, proof)
        (root / '.reuse').mkdir()
        isolated.write_json(root / '.reuse' / (fingerprint + '.json'), {'directory': directory.name, 'proof_sha256': isolated.digest(source)})
        return operation, fingerprint, directory, source

    def test_reuses_only_unchanged_success_and_never_claims_a_fresh_test(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation, fingerprint, directory, source = self.cached_fixture(root)
            before = source.read_bytes()
            with patch.object(isolated, 'ISOLATED', root), \
                    patch.object(isolated.IsolatedServer, 'inspect_owned', return_value={'State': {'Running': False, 'ExitCode': 0}}):
                proof = isolated.reusable_result(fingerprint, operation)
                self.assertFalse(proof['validation_reuse']['fresh_bds_test_executed'])
                self.assertFalse(proof['validation_reuse']['new_environment_created'])
                self.assertEqual(proof['storage_receipt']['delta']['logical_bytes'], 0)
                self.assertEqual(before, source.read_bytes())
                with patch.object(isolated.StorageReceipt, 'finish', side_effect=OSError('secret accounting detail')):
                    reused = isolated.reusable_result(fingerprint, operation)
                self.assertTrue(reused['isolated_server_resilience_verified'])
                self.assertEqual(reused['storage_receipt_error'], 'OSError')
                self.assertNotIn('storage_receipt', reused)
                self.assertEqual(before, source.read_bytes())
                (directory / 'data/kept.txt').write_text('user changed this world')
                self.assertIsNone(isolated.reusable_result(fingerprint, operation))
                self.assertEqual((directory / 'data/kept.txt').read_text(), 'user changed this world')

    def test_active_cached_environment_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation, fingerprint, directory, source = self.cached_fixture(root)
            with patch.object(isolated, 'ISOLATED', root), \
                    patch.object(isolated.IsolatedServer, 'inspect_owned', return_value={'State': {'Running': True, 'ExitCode': 0}}):
                with self.assertRaisesRegex(ValueError, 'active'):
                    isolated.reusable_result(fingerprint, operation)

    def test_low_space_blocks_before_clone_creation_but_cached_proof_can_be_reused(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = (root / 'operation', root / 'archive', {}, [])
            with patch.object(isolated, 'ISOLATED', root / 'clones'), \
                    patch.object(isolated, 'validate_operation', return_value=results), \
                    patch.object(isolated, 'docker', return_value=isolated.IMAGE), \
                    patch.object(isolated, 'inspect_runtime_contract', return_value={}), \
                    patch.object(isolated, 'runtime_resource_fingerprint', return_value='runtime'), \
                    patch.object(isolated, 'reuse_fingerprint', return_value=None), \
                    patch.object(isolated, 'reusable_result', return_value=None) as reuse, \
                    patch.object(isolated, 'preparation_required_bytes', return_value=100), \
                    patch.object(isolated, 'check_capacity', side_effect=StorageCapacityError({'allowed': False})) as capacity:
                with self.assertRaises(StorageCapacityError):
                    isolated._run('operation')
                self.assertFalse((root / 'clones').exists())
                capacity.assert_called_once_with(isolated.PROJECT, platform='linux', purpose='large_test', required_bytes=100)
                capacity.reset_mock()
                reuse.return_value = {'validation_reuse': {'fresh_bds_test_executed': False}}
                self.assertIn('validation_reuse', isolated._run('operation'))
                capacity.assert_not_called()

    def test_new_run_copies_no_live_packs_and_mounts_an_empty_binary_placeholder(self):
        self._new_run()

    def test_receipt_failure_does_not_skip_proof_or_change_the_actual_test_result(self):
        for validation_error in (False, True):
            with self.subTest(validation_error=validation_error):
                self._new_run(receipt_error=True, validation_error=validation_error)

    def _new_run(self, receipt_error=False, validation_error=False):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            clones = root / 'clones'
            clones.mkdir()
            operation = root / 'operation'
            (operation / 'stage').mkdir(parents=True)
            binary = root / 'kept-binary'
            binary.write_bytes(b'kept executable')
            calls = []
            def docker(*args, **kwargs):
                calls.append(args)
                if args[0] == 'inspect':
                    return isolated.IMAGE
                self.assertEqual(args[0], 'cp')
                Path(args[2]).write_bytes(b'runtime fixture')
                return ''
            def install(source, destination, packs, **kwargs):
                (destination / 'stage').mkdir()
                (destination / 'stage/manifest.json').write_text('{}')
                (destination / 'data/restored-pack.json').write_text('{}')
                return {}
            class Server:
                def __init__(self, directory, operation, token):
                    self.name = 'junket-isolated-' + token
                    self.created = False
                def start_new(self, image):
                    self.created = True
                def wait_ready(self):
                    return {'server_fixture_initialization': True}
                def restart(self):
                    return {'stopped': True}
                def cleanup(self):
                    return {'stopped': True, 'graceful_stop': True}
            with patch.object(isolated, 'ISOLATED', clones), patch.object(isolated, 'PROJECT', root), \
                    patch.object(isolated, 'validate_operation', return_value=(operation, root / 'backup', {'path': str(root / 'backup')}, [])), \
                    patch.object(isolated, 'docker', side_effect=docker), \
                    patch.object(isolated, 'inspect_runtime_contract', return_value={}), \
                    patch.object(isolated, 'runtime_resource_fingerprint', return_value='resources'), \
                    patch.object(isolated, 'copied_resource_fingerprint', return_value='resources'), \
                    patch.object(isolated, 'reuse_fingerprint', return_value=None), \
                    patch.object(isolated, 'preparation_required_bytes', return_value=100), \
                    patch.object(isolated, 'check_capacity', return_value={'allowed': True}), \
                    patch.object(isolated, 'prepare_shared_binary', return_value=(binary, 0)), \
                    patch.object(isolated, 'extract_backup', return_value={'source': 'stopped_full_backup'}), \
                    patch.object(isolated, 'install_stage', side_effect=install), \
                    patch.object(isolated, 'IsolatedServer', Server), \
                    patch.object(isolated.checker, 'check_warp_destinations', return_value={'issues': []}), \
                    patch.object(isolated.checker, 'check_quality', return_value={'issues': []}), \
                    patch.object(isolated.checker, 'check_resilience', return_value={
                        'server_resilience_verified': not validation_error,
                        'issues': ['fixture validation failed'] if validation_error else []}), \
                    patch.object(isolated.checker, 'read_logs', return_value=''), \
                    patch.object(isolated, 'write_receipt', side_effect=OSError('secret accounting detail') if receipt_error else isolated.write_receipt):
                proof = isolated._run('operation')
            self.assertEqual(proof['isolated_server_resilience_verified'], not validation_error)
            copied = {args[1] for args in calls if args[0] == 'cp'}
            for name in (isolated.BINARY, 'behavior_packs', 'resource_packs', 'worlds'):
                self.assertNotIn(isolated.PRODUCTION + ':/data/' + name, copied)
            self.assertEqual((Path(proof['directory']) / 'data' / isolated.BINARY).stat().st_size, 0)
            self.assertEqual(binary.read_bytes(), b'kept executable')
            self.assertEqual(proof['storage_receipt']['shared_runtime_new_logical_bytes'], 0)
            self.assertEqual((Path(proof['directory']) / 'storage-receipt.json').is_file(), not receipt_error)
            saved = isolated.read_json(Path(proof['directory']) / 'isolated-resilience-proof.json')
            self.assertEqual(saved['isolated_server_resilience_verified'], not validation_error)
            if receipt_error:
                self.assertEqual(saved['storage_receipt_error'], 'OSError')
                self.assertNotIn('secret accounting detail', json.dumps(saved))

    def test_native_failed_preparation_retains_files_and_records_its_own_capacity(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation = '10000000-0000-4000-8000-000000000001'
            directory = root / 'junket-world-applications' / operation
            def prepare(args, operation):
                directory.mkdir(parents=True)
                (directory / 'partial-stage.txt').write_text('retain this diagnostic')
                raise ValueError('fixture preparation error')
            with patch.object(native, 'PROJECT', root), patch.object(native, '_release', side_effect=prepare), \
                    patch('sys.argv', ['native', '--operation', operation, '--bundle', str(root / 'private-input')]):
                with self.assertRaisesRegex(ValueError, 'fixture preparation error'):
                    native.main()
            record = isolated.read_json(directory / 'native-storage-receipt.json')
            self.assertEqual(record['before']['usage']['files'], 0)
            self.assertEqual(record['delta']['files'], 1)
            self.assertEqual(record['retained_environments'], [str(directory)])
            self.assertEqual(record['recovery_sources'], [str(root / 'private-input')])
            self.assertEqual((directory / 'partial-stage.txt').read_text(), 'retain this diagnostic')

    def test_native_receipt_failure_preserves_success_and_original_failure(self):
        for release_error in (False, True):
            with self.subTest(release_error=release_error), TemporaryDirectory() as temporary:
                root = Path(temporary)
                operation = '10000000-0000-4000-8000-000000000001'
                directory = root / 'junket-world-applications' / operation
                def release(args, operation):
                    directory.mkdir(parents=True)
                    if release_error:
                        raise ValueError('original release failure')
                    return 'release succeeded'
                output = StringIO()
                with patch.object(native, 'PROJECT', root), patch.object(native, '_release', side_effect=release), \
                        patch.object(storage_core, 'write_receipt', side_effect=OSError('secret accounting detail')), \
                        patch('sys.stderr', output), \
                        patch('sys.argv', ['native', '--operation', operation, '--bundle', str(root / 'private-input')]):
                    if release_error:
                        with self.assertRaisesRegex(ValueError, 'original release failure'):
                            native.main()
                    else:
                        self.assertEqual(native.main(), 'release succeeded')
                self.assertIn('OSError', output.getvalue())
                self.assertNotIn('secret accounting detail', output.getvalue())


if __name__ == '__main__':
    unittest.main()
