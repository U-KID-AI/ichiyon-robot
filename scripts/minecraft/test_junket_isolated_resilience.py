"""No Docker/SSH: clone boundary and exact-source regression checks."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

import check_junket_isolated_resilience as isolated


class IsolatedResilienceTests(unittest.TestCase):
    def runtime_lines(self):
        rows = []
        for path, expected in isolated.EXECUTABLES.items():
            header = bytearray(20)
            header[:6] = b'\x7fELF\x02\x01'
            header[18:20] = expected['elf_machine'].to_bytes(2, 'little')
            rows.append(path + '|' + expected['sha256'] + '|' + header.hex())
        return '\n'.join(rows)

    def test_runtime_pin_accepts_only_inspected_architecture_and_executables(self):
        meta = 'linux arm64 ' + isolated.IMAGE
        lines = self.runtime_lines()
        proof = isolated.validate_runtime_contract(meta, lines)
        self.assertEqual(proof['launch'], [isolated.SERVER_RUNNER, isolated.EMULATOR, '/data/' + isolated.BINARY])
        self.assertEqual(len(proof['executable_files']), 3)
        for changed in ('linux amd64 ' + isolated.IMAGE, 'windows arm64 ' + isolated.IMAGE,
                        'linux arm64 sha256:' + '0' * 64):
            with self.assertRaisesRegex(ValueError, 'OS, architecture or digest'):
                isolated.validate_runtime_contract(changed, lines)
        first_sha = isolated.EXECUTABLES[isolated.SERVER_RUNNER]['sha256']
        with self.assertRaisesRegex(ValueError, 'digest or ELF architecture'):
            isolated.validate_runtime_contract(meta, lines.replace(first_sha, '0' * 64))
        with self.assertRaisesRegex(ValueError, 'digest or ELF architecture'):
            isolated.validate_runtime_contract(meta, lines.replace('b700', '3e00', 1))
        with self.assertRaisesRegex(ValueError, 'Missing runtime'):
            isolated.validate_runtime_contract(meta, '\n'.join(lines.splitlines()[:-1]))
        with self.assertRaisesRegex(ValueError, 'Unexpected runtime'):
            isolated.validate_runtime_contract(meta, lines + '\n' + lines.splitlines()[0])

    def test_runtime_inspection_is_read_only_and_does_not_use_entrypoint(self):
        with patch.object(isolated, 'docker', side_effect=['linux arm64 ' + isolated.IMAGE, self.runtime_lines()]) as call:
            isolated.inspect_runtime_contract()
        self.assertEqual(call.call_count, 2)
        args = call.call_args_list[-1].args
        self.assertEqual(args[:4], ('exec', isolated.PRODUCTION, 'sh', '-c'))
        self.assertIn('test -x "$path"', args[4])
        self.assertIn('sha256sum "$path"', args[4])
        self.assertNotIn('entry.sh', args[4])
        self.assertNotIn('worlds', args[4])

    def test_startup_collects_all_venue_errors_before_rejecting(self):
        with TemporaryDirectory() as temp:
            server = isolated.IsolatedServer(Path(temp), 'base', 'test')
            server.since = 'start'
            with patch.object(isolated.checker, 'read_logs', return_value='actor error') as logs, \
                    patch.object(isolated.checker, 'runtime_errors', return_value=['actor error']), \
                    patch.object(isolated.checker, 'parse_runtime_reports',
                                 side_effect=[(None, []), ({'ok': False}, [])]), \
                    patch.object(server, 'inspect_owned', return_value={'State': {'Running': True}}), \
                    patch.object(isolated.time, 'sleep'):
                with self.assertRaisesRegex(RuntimeError, 'Scripting/Actor errors'):
                    server.wait_ready()
                self.assertEqual(logs.call_count, 2)

    def candidate(self, root, extra=None, tamper=False, symlink=False):
        files = {name: b'{}' for name in isolated.BUNDLE_METADATA - {'bundle-report.json'}}
        for index, pack in enumerate(isolated.JUNKET_PACKS, 2):
            files['packs/' + pack + '/manifest.json'] = json.dumps({
                'header': {'uuid': 'fixed-' + str(index), 'version': [1, 2, 3]}, 'modules': []}).encode()
            files['packs/' + pack + '/candidate.js'] = b'candidate replacement'
        if extra:
            files[extra] = b'extra'
        report = {'schema': 1, 'production_applied': False, 'real_two_player_test': False,
                  'games': list(isolated.checker.EXPECTED_IDS), 'source_zip_sha256': 'a' * 64,
                  'files': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
        if tamper:
            files['packs/' + isolated.JUNKET_PACKS[0] + '/candidate.js'] = b'tampered'
        archive = root / 'candidate.zip'
        with ZipFile(archive, 'w') as target:
            for name, value in files.items():
                if symlink and name.endswith('/candidate.js'):
                    member = ZipInfo(name)
                    member.create_system = 3
                    member.external_attr = 0o120777 << 16
                    target.writestr(member, value)
                else:
                    target.writestr(name, value)
            target.writestr('bundle-report.json', json.dumps(report).encode())
        return archive

    def archive(self, root, extra=()):
        path = root / 'backup.tar.gz'
        with tarfile.open(path, 'w:gz') as target:
            for name, value in [('data/worlds/' + isolated.WORLD + '/level.dat', b'level'),
                                ('data/worlds/' + isolated.WORLD + '/db/CURRENT', b'manifest'), *extra]:
                member = tarfile.TarInfo(name)
                if not isinstance(value, tuple):
                    member.size = len(value)
                    target.addfile(member, BytesIO(value))
                else:
                    member.type = value[0]
                    member.linkname = '/tmp/foreign'
                    target.addfile(member)
        return path

    def test_standard_backup_and_identical_repeated_refs(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            refs = ('data/worlds/' + isolated.WORLD + '/world_behavior_packs.json', b'[]')
            archive = self.archive(root, [refs, refs])
            output = root / 'clone'
            result = isolated.extract_backup(archive, output)
            self.assertEqual(result['files'], 3)
            self.assertEqual((output / refs[0]).read_bytes(), b'[]')

    def test_archive_rejects_traversal_unknown_world_links_and_conflicts(self):
        cases = [('../outside', b'bad'), ('/absolute', b'bad'),
                 ('data/worlds/other/level.dat', b'bad'),
                 ('data/worlds/' + isolated.WORLD + '/link', (tarfile.SYMTYPE,)),
                 ('data/worlds/' + isolated.WORLD + '/hard', (tarfile.LNKTYPE,))]
        for extra in cases:
            with self.subTest(extra=extra), TemporaryDirectory() as temp:
                root = Path(temp)
                archive = self.archive(root, [extra])
                with self.assertRaises(ValueError):
                    isolated.extract_backup(archive, root / 'clone')
                self.assertFalse((root / 'clone').exists())
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self.archive(root, [('data/worlds/' + isolated.WORLD + '/level.dat', b'changed')])
            with self.assertRaisesRegex(ValueError, 'Conflicting duplicate'):
                isolated.extract_backup(archive, root / 'clone')

    def test_operation_hash_mismatch_rejected_before_any_copy(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            operation = '10000000-0000-4000-8000-000000000001'
            directory = root / 'operations' / operation
            directory.mkdir(parents=True)
            backups = root / 'backups'
            backups.mkdir()
            archive = backups / 'creative-before-restart-20261019-135920.tar.gz'
            archive.write_bytes(b'changed')
            backup = {'path': str(archive), 'sha256': '0' * 64, 'bytes': 7, 'server_was_stopped': True}
            isolated.write_json(directory / 'full-backup.json', backup)
            isolated.write_json(directory / 'preflight.json', {'world': isolated.WORLD})
            isolated.write_json(directory / 'native-release-proof.json', {'world': isolated.WORLD,
                'operation_id': operation, 'status': 'succeeded', 'installed': True, 'full_backup': backup})
            with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                isolated.validate_operation(operation, root / 'operations', backups)
            backup['sha256'] = isolated.digest(archive)
            backup['server_was_stopped'] = False
            isolated.write_json(directory / 'full-backup.json', backup)
            release = isolated.read_json(directory / 'native-release-proof.json')
            release['full_backup'] = backup
            isolated.write_json(directory / 'native-release-proof.json', release)
            with self.assertRaisesRegex(ValueError, 'while BDS was stopped'):
                isolated.validate_operation(operation, root / 'operations', backups)
            backup['server_was_stopped'] = True
            release['full_backup'] = backup
            isolated.write_json(directory / 'full-backup.json', backup)
            isolated.write_json(directory / 'native-release-proof.json', release)
            isolated.write_json(directory / 'preflight.json', {'world': isolated.WORLD,
                'source_reception_corrections': [{'id': 'hope', 'structures': [{'sha256': 'a' * 64}]}]})
            with self.assertRaisesRegex(ValueError, 'added source floors after its backup'):
                isolated.validate_operation(operation, root / 'operations', backups)

    def test_container_arguments_have_no_network_or_production_mount(self):
        with patch.object(isolated.os, 'getuid', return_value=1001, create=True), patch.object(isolated.os, 'getgid', return_value=1001, create=True):
            args = isolated.run_arguments('junket-isolated-test', 'token', 'operation', Path('/private/clone/data'), isolated.IMAGE)
        self.assertEqual(args[args.index('--network') + 1], 'none')
        self.assertEqual(args[args.index('--pull') + 1], 'never')
        self.assertEqual(args[args.index('--user') + 1], '1001:1001')
        self.assertEqual(args[args.index('--memory') + 1], '3g')
        self.assertEqual(args[args.index('--entrypoint') + 1], isolated.SERVER_RUNNER)
        self.assertEqual(args[-3:], [isolated.IMAGE, isolated.EMULATOR, '/data/' + isolated.BINARY])
        self.assertNotIn('/opt/bedrock-entry.sh', args)
        self.assertNotIn('-p', args)
        self.assertNotIn('--publish', args)
        self.assertNotIn(str(isolated.PROJECT / 'data'), ' '.join(args))
        with self.assertRaises(ValueError):
            isolated.run_arguments(isolated.PRODUCTION, 'token', 'operation', Path('/private'), isolated.IMAGE)

    def test_added_source_gallery_can_only_replay_with_matching_success_proof(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            changes = [{'id': 'hope', 'structures': [{'sha256': 'a' * 64}]}]
            preflight = {'source_reception_corrections': changes}
            with self.assertRaisesRegex(ValueError, 'verified replay proof'):
                isolated.baseline_replay_kind(root, preflight)
            proof = {'verified': True, 'mode': 'additive_source_upper_floor',
                     'reception_changes': changes, 'existing_building_blocks_replaced': 0}
            isolated.write_json(root / 'native-extension-proof.json', proof)
            self.assertEqual(isolated.baseline_replay_kind(root, preflight), 'reception')
            proof['existing_building_blocks_replaced'] = 1
            isolated.write_json(root / 'native-extension-proof.json', proof)
            with self.assertRaisesRegex(ValueError, 'Unverified source extension'):
                isolated.baseline_replay_kind(root, preflight)

    def test_barrier_replay_requires_only_barrier_success_evidence(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            changes = [{'id': 'hope', 'changed_source_barriers': 176}]
            preflight = {'source_barrier_corrections': changes}
            proof = {'verified': True, 'mode': 'barrier_only_visible_floor_revision',
                     'source_validation': changes, 'non_barrier_blocks_replaced': 0}
            isolated.write_json(root / 'native-barrier-proof.json', proof)
            self.assertEqual(isolated.baseline_replay_kind(root, preflight), 'barriers')
            proof['source_validation'] = []
            isolated.write_json(root / 'native-barrier-proof.json', proof)
            with self.assertRaisesRegex(ValueError, 'Unverified source barrier'):
                isolated.baseline_replay_kind(root, preflight)

    def test_recorded_lineage_recovery_requires_matching_managed_hashes_and_immutable_proofs(self):
        import uuid
        with TemporaryDirectory() as temp:
            root=Path(temp);current=root/'current';current.mkdir();previous=str(uuid.uuid4());prior=root/previous;prior.mkdir()
            packs={p:str(i)*64 for i,p in enumerate(isolated.JUNKET_PACKS)}
            active={'operation_id':'managed-before','packs':[{'path':p,'content_hash':h} for p,h in packs.items()]}
            isolated.write_json(current/'managed-active-before.json',active)
            isolated.write_json(prior/'native-release-proof.json',{'operation_id':previous,'status':'succeeded','installed':True,'world':isolated.WORLD})
            isolated.write_json(prior/'preflight.json',{'world':isolated.WORLD})
            recovery={'schema':1,'managed_operation_id':'managed-before','native_world_operation':{'operation_id':previous,'packs':packs},
                'verified_installed_packs':packs,'verified_files':{'junket-world-applications/'+previous+'/'+name:isolated.digest(prior/name)
                    for name in ('native-release-proof.json','preflight.json')}}
            isolated.write_json(current/'native-lineage-recovery.json',recovery)
            self.assertEqual(isolated.previous_native_operation(current),previous)
            recovery['managed_operation_id']='other';isolated.write_json(current/'native-lineage-recovery.json',recovery)
            with self.assertRaisesRegex(ValueError,'lineage does not match'):isolated.previous_native_operation(current)
            recovery['managed_operation_id']='managed-before';isolated.write_json(current/'native-lineage-recovery.json',recovery)
            isolated.write_json(prior/'preflight.json',{'world':'changed'})
            with self.assertRaisesRegex(ValueError,'fingerprint mismatch'):isolated.previous_native_operation(current)

    def test_owned_container_only_stop_and_restart(self):
        server = isolated.IsolatedServer(Path('/private/clone'), 'op', 'token')
        server.created = True
        info = {'Name': '/' + server.name, 'Image': isolated.IMAGE,
                'Config': {'Labels': {isolated.LABEL: 'token', isolated.LABEL + '.operation': 'op'}},
                'HostConfig': {'NetworkMode': 'none', 'PortBindings': {}},
                'Mounts': [{'Type': 'bind', 'Destination': '/data', 'Source': str(server.directory / 'data')}]}
        with patch.object(isolated, 'docker', return_value=json.dumps([info])):
            self.assertEqual(server.inspect_owned()['Name'], '/' + server.name)
        info['Mounts'][0]['Source'] = str(isolated.PROJECT / 'data')
        with patch.object(isolated, 'docker', return_value=json.dumps([info])) as docker:
            with self.assertRaisesRegex(ValueError, 'ownership'):
                server.cleanup()
            self.assertTrue(all(call.args[0] == 'inspect' for call in docker.call_args_list))

    def test_standard_installer_preserves_exact_staged_manifest_and_refs(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            operation = root / 'operation'
            stage = operation / 'stage'
            clone = root / 'clone'
            clone.mkdir()
            packs = ['behavior_packs/import_structures', 'behavior_packs/ichiyon_avatar_bp',
                     'behavior_packs/ichiyon_junket_bp', 'resource_packs/ichiyon_junket_rp']
            for index, pack in enumerate(packs):
                path = stage / pack
                path.mkdir(parents=True)
                isolated.write_json(path / 'manifest.json', {'header': {'uuid': 'fixed-' + str(index), 'version': [1, 2, 99]}, 'modules': []})
                (path / 'unchanged.js').write_text('constant', encoding='utf-8')
            isolated.write_json(stage / 'cosmetics-build.json', {'packs': packs})
            result = isolated.install_stage(operation, clone, packs)
            self.assertEqual(set(result), set(packs))
            self.assertEqual((stage / packs[2] / 'manifest.json').read_bytes(), (clone / 'data' / packs[2] / 'manifest.json').read_bytes())
            refs = isolated.read_json(clone / 'data/worlds' / isolated.WORLD / 'world_behavior_packs.json')
            self.assertEqual(refs[-1], {'pack_id': 'fixed-2', 'version': [1, 2, 99]})
            self.assertTrue((clone / 'data/config/6d8b87c7-5190-433a-bb59-05f72c6f3302/permissions.json').is_file())

    def test_candidate_archive_and_report_hashes_reject_tampering(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self.candidate(root)
            with self.assertRaisesRegex(ValueError, 'ZIP SHA256'):
                isolated.extract_candidate(archive, '0' * 64, root / 'candidate')
            self.assertFalse((root / 'candidate').exists())
            archive = self.candidate(root, tamper=True)
            with self.assertRaisesRegex(ValueError, 'file SHA256'):
                isolated.extract_candidate(archive, isolated.digest(archive), root / 'candidate')
            self.assertFalse((root / 'candidate').exists())

    def test_candidate_rejects_foreign_packs_traversal_and_links(self):
        for extra in ['packs/behavior_packs/ichiyon_avatar_bp/new.js', '../outside', '/absolute']:
            with self.subTest(extra=extra), TemporaryDirectory() as temp:
                root = Path(temp)
                archive = self.candidate(root, extra=extra)
                with self.assertRaises(ValueError):
                    isolated.extract_candidate(archive, isolated.digest(archive), root / 'candidate')
                self.assertFalse((root / 'candidate').exists())
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self.candidate(root, symlink=True)
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                isolated.extract_candidate(archive, isolated.digest(archive), root / 'candidate')

    def test_candidate_replaces_only_two_packs_and_keeps_original_operation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self.candidate(root)
            candidate = root / 'unpacked'
            proof = isolated.extract_candidate(archive, isolated.digest(archive), candidate)
            self.assertEqual(proof['sha256'], isolated.digest(archive))
            self.assertEqual(set(proof['source_packs']), set(isolated.JUNKET_PACKS))
            operation, clone = root / 'operation', root / 'clone'
            clone.mkdir()
            packs = ['behavior_packs/import_structures', 'behavior_packs/ichiyon_avatar_bp',
                     *isolated.JUNKET_PACKS, *['resource_packs/ichiyon_test' + str(i) + '_rp' for i in range(9)]]
            for index, pack in enumerate(packs):
                path = operation / 'stage' / pack
                path.mkdir(parents=True)
                isolated.write_json(path / 'manifest.json', {'header': {'uuid': 'fixed-' + str(index), 'version': [1, 2, 99]}, 'modules': []})
                (path / 'old.js').write_text('old', encoding='utf-8')
            isolated.write_json(operation / 'stage/cosmetics-build.json', {'packs': packs})
            before = isolated.tree_digest(operation)
            results = isolated.install_stage(operation, clone, packs, candidate=candidate)
            self.assertEqual(isolated.tree_digest(operation), before)
            self.assertEqual(len(set(packs) - set(isolated.JUNKET_PACKS)), 11)
            for pack in packs:
                expected = candidate / 'packs' / pack if pack in isolated.JUNKET_PACKS else operation / 'stage' / pack
                self.assertEqual(results[pack], isolated.tree_digest(expected))
                self.assertEqual(isolated.tree_digest(clone / 'data' / pack), isolated.tree_digest(expected))
            manifest = isolated.read_json(clone / 'data' / isolated.JUNKET_PACKS[0] / 'manifest.json')
            self.assertEqual(manifest['header']['version'], [1, 2, 3], 'candidate versions are not bumped in the clone')
            self.assertFalse((clone / 'data' / isolated.JUNKET_PACKS[0] / 'old.js').exists())


if __name__ == '__main__':
    unittest.main()
