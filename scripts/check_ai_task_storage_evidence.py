"""Offline deployment evidence, fixed-root ownership and read-only attestation."""
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import ai_task_storage_evidence as evidence
import ai_task_storage_retention_graph as graph
from check_ai_task_storage_retention import filesystem_state, forbid_mutations
from check_ai_task_storage_cleanup import fixture as cleanup_fixture


TARGET, PREVIOUS = 'a' * 40, 'b' * 40
OWNER = dict(boot_id='12345678-1234-1234-1234-123456789abc', pid=123, start_ticks=456, uid=0)


class Host:
    def __init__(self, root):
        self.home = root / 'home'
        self.releases = self.home / 'ichiyon-releases'
        self.backups = self.home / 'ichiyon-backups'
        self.evidence = self.home / 'ichiyon-storage-evidence'
        self.current = self.home / 'ichiyon-current'
        self.lock = self.home / 'ichiyon-deploy.lock'
        self.proc = root / 'proc'
        self.owner = dict(OWNER, uid=root.stat().st_uid)
        for path in (self.releases, self.backups, self.proc):
            path.mkdir(parents=True)
        release = self.releases / PREVIOUS
        release.mkdir()
        for name, value in [('REVISION', PREVIOUS), ('immutable-image.txt', 'ichiyon-robot-app:' + PREVIOUS),
                            ('rollback-images.txt', 'sha256:' + '1' * 64), ('persistence.txt', 'data'),
                            ('compose.immutable.yml', 'fixture metadata')]:
            (release / name).write_bytes((value + '\n').encode())
        self.lock.write_bytes(b'')
        self.lock.chmod(0o600)
        info = self.lock.stat()
        self.lock_identity = dict(path=str(self.lock), device=info.st_dev, inode=info.st_ino,
                                  owner_uid=self.owner['uid'], mode=0o600, descriptor=9)
        self.runtime = dict(target_release=None, target_image=None, migration_container=None, image_ids=[], observation_complete=True)

    def patched(self, legacy=True):
        stack = ExitStack()
        if legacy:
            # Frozen v1 fixtures continue exercising the compatibility reader;
            # production commands only use the indexed v2 public writer.
            for name in ('begin', 'record_stage', 'observe', 'finish'):
                stack.enter_context(patch.object(evidence, name, getattr(evidence, '_v1_' + name)))
        else:
            import ai_task_storage_evidence_store as store
            stack.enter_context(patch.object(store, 'ROOT', self.evidence))
            stack.enter_context(patch.object(store, '_uid', return_value=self.owner['uid']))
        for name, value in [('HOME', self.home), ('ROOT', self.evidence), ('RELEASES', self.releases),
                            ('BACKUPS', self.backups), ('CURRENT', self.current), ('LOCK', self.lock), ('PROC', self.proc)]:
            stack.enter_context(patch.object(evidence, name, value))
        stack.enter_context(patch.object(evidence, '_uid', return_value=self.owner['uid']))
        stack.enter_context(patch.object(evidence, '_current_sha', return_value=PREVIOUS))
        stack.enter_context(patch.object(evidence, '_owner', return_value=(self.owner, self.lock_identity)))
        stack.enter_context(patch.object(evidence, '_runtime', side_effect=lambda _: copy.deepcopy(self.runtime)))
        if os.name == 'nt':
            # NT stat has no POSIX directory permission/fsync contract. Linux CI
            # runs the same writer tests with the real permission and fsync code.
            stack.enter_context(patch.object(evidence, '_owner_mode', side_effect=lambda info, mode:
                evidence.need(info.st_uid == self.owner['uid'], 'evidence_owner_or_mode_invalid')))
            stack.enter_context(patch.object(evidence, '_fsync_directory'))
        return stack

    def begin(self):
        return evidence.begin(TARGET, self.owner['pid'])

    def stage(self, kind='prepare', suffix='abcdefgh'):
        root = self.backups if kind == 'backup' else self.releases
        path = root / ('.' + kind + '-' + TARGET + '.' + suffix)
        path.mkdir(mode=0o700)
        return path

    def active(self, opid):
        return evidence.read_receipt(self.evidence / 'active' / (opid + '.json'), False)

    def terminal(self, opid):
        return evidence.read_receipt(self.evidence / 'operations' / (opid + '.json'))


class EvidenceTests(unittest.TestCase):
    def test_success_publishes_complete_checksummed_terminal_once(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                stage = host.stage()
                evidence.record_stage(opid, 123, 'prepare', str(stage))
                evidence.observe(opid, 123, 'health')
                result = evidence.finish(opid, 123, 'succeeded', 'not_needed')
                receipt = host.terminal(opid)
                self.assertEqual(result['terminal_state'], 'succeeded')
                self.assertEqual(receipt['owner'], host.owner)
                self.assertEqual(receipt['target_sha'], TARGET)
                self.assertEqual(receipt['previous_sha'], PREVIOUS)
                self.assertEqual(receipt['staging'][0]['created_identity']['inode'], stage.stat().st_ino)
                self.assertEqual(receipt['reference_snapshot_sha256'], evidence.digest(receipt['reference_snapshot']))
                self.assertTrue(receipt['completed_at'] >= receipt['created_at'])
                self.assertEqual(list((host.evidence / '.pending').iterdir()), [])
                with self.assertRaises(evidence.EvidenceError):
                    evidence.finish(opid, 123, 'succeeded', 'not_needed')

    def test_failed_cancelled_and_rollback_result_are_distinct_terminal_facts(self):
        for state, rollback in [('failed', 'not_attempted'), ('failed', 'succeeded'),
                                ('failed', 'failed'), ('cancelled', 'succeeded')]:
            with self.subTest(state=state, rollback=rollback), tempfile.TemporaryDirectory() as directory:
                host = Host(Path(directory))
                with host.patched():
                    opid = host.begin()
                    evidence.observe(opid, 123, 'rollback')
                    evidence.finish(opid, 123, state, rollback)
                    result = host.terminal(opid)
                self.assertEqual(result['state'], state)
                self.assertEqual(result['rollback_result'], rollback)

    def test_active_or_interrupted_publish_is_never_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                with self.assertRaises((evidence.EvidenceError, OSError)):
                    evidence.read_receipt(host.evidence / 'active' / (opid + '.json'))
                (host.evidence / '.pending' / (opid + '.interrupted.tmp')).write_bytes(b'{"READY":true}')
                self.assertEqual(list((host.evidence / 'operations').iterdir()), [])
                snapshot = cleanup_fixture()
                evidence.enrich_snapshot(snapshot)
                self.assertEqual(snapshot['durable_operations']['terminal_count'], 0)
                self.assertEqual(snapshot['durable_operations']['interrupted_publish_count'], 1)
                self.assertFalse(snapshot['cleanup_evidence']['source_verified'])

    def test_checksum_modification_and_ready_only_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                evidence.finish(opid, 123, 'succeeded', 'not_needed')
                path = host.evidence / 'operations' / (opid + '.json')
                original = path.read_bytes()
                document = json.loads(original)
                document['payload']['state'] = 'failed'
                path.write_bytes(evidence.encoded(document))
                with self.assertRaises(evidence.EvidenceError):
                    host.terminal(opid)
                path.write_bytes(b'{"READY":true}')
                with self.assertRaises(evidence.EvidenceError):
                    host.terminal(opid)

    def test_wrong_boot_pid_start_or_lock_identity_reject_updates(self):
        for field, value in [('boot_id', '87654321-1234-1234-1234-123456789abc'), ('pid', 321), ('start_ticks', 999)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                host = Host(Path(directory))
                with host.patched():
                    opid = host.begin()
                    changed = dict(host.owner, **{field: value})
                    with patch.object(evidence, '_owner', return_value=(changed, host.lock_identity)), self.assertRaises(evidence.EvidenceError):
                        evidence.observe(opid, 123, 'health')
                    changed_lock = dict(host.lock_identity, inode=host.lock_identity['inode'] + 1)
                    with patch.object(evidence, '_owner', return_value=(host.owner, changed_lock)), self.assertRaises(evidence.EvidenceError):
                        evidence.finish(opid, 123, 'failed', 'unknown')

    def test_legacy_stage_and_wrong_sha_or_path_cannot_be_enrolled(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            old = host.stage()
            with host.patched():
                opid = host.begin()
                for path in (str(old), str(host.home / 'other'), str(host.releases / ('../.prepare-' + TARGET + '.abcdefgh')),
                             str(host.releases / ('.prepare-' + PREVIOUS + '.abcdefgh'))):
                    with self.subTest(path=path), self.assertRaises(evidence.EvidenceError):
                        evidence.record_stage(opid, 123, 'prepare', path)
                new = host.stage('prepare', 'newstage')
                evidence.record_stage(opid, 123, 'prepare', str(new))
                with self.assertRaises(evidence.EvidenceError):
                    evidence.record_stage(opid, 123, 'prepare', str(new))

    def test_receipt_path_escape_and_operation_id_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                for bad in ('../' + opid, 'x' * 32, opid + '.json', True):
                    with self.subTest(value=bad), self.assertRaises(evidence.EvidenceError):
                        evidence.observe(bad, 123, 'health')
                with self.assertRaises(evidence.EvidenceError):
                    evidence.read_receipt(host.home / (opid + '.json'))

    def test_symlink_root_and_stage_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            outside = Path(directory) / 'outside'
            outside.mkdir()
            try:
                host.evidence.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('OS does not permit symlink creation')
            with host.patched(), self.assertRaises(evidence.EvidenceError):
                host.begin()
            self.assertEqual(list(outside.iterdir()), [])
            host.evidence.unlink()
            with host.patched():
                opid = host.begin()
                stage = host.releases / ('.prepare-' + TARGET + '.abcdefgh')
                stage.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(evidence.EvidenceError):
                    evidence.record_stage(opid, 123, 'prepare', str(stage))

    def test_permissions_are_fixed_and_wrong_owner_or_mode_rejected(self):
        with patch.object(evidence, '_uid', return_value=1000):
            evidence._owner_mode(types.SimpleNamespace(st_uid=1000, st_mode=stat.S_IFREG | 0o600), 0o600)
            for uid, mode in [(0, 0o600), (1000, 0o644), (1000, 0o666)]:
                with self.subTest(uid=uid, mode=mode), self.assertRaises(evidence.EvidenceError):
                    evidence._owner_mode(types.SimpleNamespace(st_uid=uid, st_mode=stat.S_IFREG | mode), 0o600)
        if os.name != 'nt':
            with tempfile.TemporaryDirectory() as directory:
                host = Host(Path(directory))
                with host.patched():
                    opid = host.begin()
                    self.assertEqual(stat.S_IMODE(host.evidence.stat().st_mode), 0o700)
                    self.assertEqual(stat.S_IMODE((host.evidence / 'active' / (opid + '.json')).stat().st_mode), 0o600)

    def test_terminal_active_binding_rejects_even_rechecksummed_wrong_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                evidence.finish(opid, 123, 'succeeded', 'not_needed')
                path = host.evidence / 'operations' / (opid + '.json')
                document = json.loads(path.read_bytes())
                document['payload']['owner']['start_ticks'] += 1
                document['sha256'] = evidence.digest(document['payload'])
                path.write_bytes(evidence.encoded(document))
                with self.assertRaises(evidence.EvidenceError):
                    host.terminal(opid)

    def test_migration_identity_survives_normal_container_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                migration = dict(id='2' * 64, image_id='sha256:' + '3' * 64,
                                 status='exited', name='ichiyon-robot-migrate-' + TARGET)
                host.runtime['migration_container'] = migration
                evidence.observe(opid, 123, 'migrate')
                host.runtime['migration_container'] = None
                evidence.observe(opid, 123, 'health')
                evidence.finish(opid, 123, 'succeeded', 'not_needed')
                self.assertEqual(host.terminal(opid)['runtime']['migration_container'], migration)

    def test_docker_absence_requires_successful_inventory_and_failed_inspect_is_unknown(self):
        image = 'sha256:' + '4' * 64
        for failure in ('none', 'image-list', 'image-inspect', 'migration-list', 'migration-inspect'):
            with self.subTest(failure=failure):
                def run(args, **kwargs):
                    self.assertFalse(kwargs['shell'])
                    listing = args[2] == 'ls'
                    kind = 'image' if args[1] == 'image' else 'migration'
                    if failure == kind + ('-list' if listing else '-inspect'):
                        return types.SimpleNamespace(returncode=1, stdout=b'', stderr=b'PRIVATE_FIXTURE_ERROR')
                    if args[1:3] == ['image', 'ls']:
                        body = image + ' ichiyon-robot-app:' + TARGET + '\n' if failure == 'image-inspect' else ''
                    elif args[1:3] == ['container', 'ls']:
                        body = '5' * 64 + '\n' if failure == 'migration-inspect' else ''
                    else:
                        self.fail('unexpected successful inspection')
                    return types.SimpleNamespace(returncode=0, stdout=body.encode(), stderr=b'')
                with patch.object(evidence, '_directory_identity', return_value=None), patch('subprocess.run', side_effect=run):
                    result = evidence._runtime(TARGET)
                self.assertEqual(result['observation_complete'], failure == 'none')
                self.assertNotIn('PRIVATE_FIXTURE_ERROR', json.dumps(result))

    def test_image_creation_requires_absence_from_complete_initial_id_inventory(self):
        identity = 'sha256:' + '4' * 64
        payload = dict(target_sha=TARGET,
            initial_runtime=dict(target_image=None, image_ids=[], observation_complete=True),
            runtime=dict(target_image=dict(id=identity, revision=TARGET), image_ids=[identity], observation_complete=True))
        self.assertTrue(evidence._bound(payload, 'images', {'id': identity}))
        # A dangling existing image retagged during deployment was not created
        # by this operation and cannot acquire an invented ownership record.
        payload['initial_runtime']['image_ids'] = [identity]
        self.assertFalse(evidence._bound(payload, 'images', {'id': identity}))
        payload['initial_runtime']['image_ids'] = []
        payload['initial_runtime']['observation_complete'] = False
        self.assertFalse(evidence._bound(payload, 'images', {'id': identity}))
        payload['initial_runtime']['observation_complete'] = True
        payload['runtime']['observation_complete'] = False
        self.assertFalse(evidence._bound(payload, 'images', {'id': identity}))

    def test_missing_docker_after_initial_inventory_is_unknown_not_absent(self):
        with patch.object(evidence, '_image_inventory', return_value=([], {})), \
                patch.object(evidence, '_directory_identity', side_effect=FileNotFoundError()), \
                patch.object(evidence, '_docker', side_effect=FileNotFoundError('fixture docker gone')):
            result = evidence._runtime(TARGET)
        self.assertFalse(result['observation_complete'])
        self.assertIsNone(result['migration_container'])
        self.assertNotIn('fixture docker gone', json.dumps(result))

    def test_unavailable_boot_never_proves_owner_termination(self):
        payload = {'owner': dict(OWNER)}
        for terminal in (False, True):
            with self.subTest(terminal=terminal), patch.object(evidence, '_boot', side_effect=FileNotFoundError()), \
                    patch.object(evidence, '_process') as process:
                self.assertEqual(evidence._owner_state(payload, terminal), 'unknown')
                process.assert_not_called()
        with patch.object(evidence, '_boot', return_value=OWNER['boot_id']), \
                patch.object(evidence, '_process', side_effect=FileNotFoundError()):
            self.assertEqual(evidence._owner_state(payload, True), 'terminated_identity_verified')
            self.assertEqual(evidence._owner_state(payload, False), 'unknown_without_terminal_receipt')

    def test_stage_signature_requires_complete_integer_directory_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                evidence.record_stage(opid, 123, 'prepare', str(host.stage()))
                payload = host.active(opid)
                for index, value in ((2, stat.S_IFREG | 0o600), (3, True), (4, 'unknown'), (5, -1)):
                    with self.subTest(index=index):
                        changed = copy.deepcopy(payload)
                        changed['staging'][0]['created_identity']['signature'][index] = value
                        with self.assertRaises(evidence.EvidenceError):
                            evidence._validate(changed, opid, False)

    def test_docker_boundary_rejects_every_mutating_or_arbitrary_command(self):
        with patch('subprocess.run') as run:
            for args in (['docker', 'prune'], ['docker', 'image', 'rm', 'fixture'],
                         ['rm', '/tmp/fixture'], ['docker', 'build', '.'],
                         ['docker', 'container', 'inspect', '--format', '{{.Config.Env}}', '5' * 64]):
                with self.subTest(args=args), self.assertRaises(evidence.EvidenceError):
                    evidence._docker_run(args)
            run.assert_not_called()

    def test_events_bounded_without_deleting_any_old_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                for _ in range(evidence.MAX_EVENTS + 3):
                    evidence.observe(opid, 123, 'health')
                active = host.active(opid)
                self.assertEqual(len(active['events']), evidence.MAX_EVENTS)
                self.assertEqual(active['omitted_event_count'], 4)
                evidence.finish(opid, 123, 'succeeded', 'not_needed')
                old = filesystem_state(host.evidence / 'operations')
                second = host.begin()
                evidence.finish(second, 123, 'failed', 'not_attempted')
                self.assertEqual((host.evidence / 'operations' / (opid + '.json')).read_bytes(),
                    evidence.encoded(json.loads((host.evidence / 'operations' / (opid + '.json')).read_bytes())) + b'\n')
                self.assertEqual(len(list((host.evidence / 'operations').iterdir())), 2)

    def test_receipt_reader_and_collector_are_read_only_and_do_not_enroll_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                stage = host.stage()
                evidence.record_stage(opid, 123, 'prepare', str(stage))
                evidence.finish(opid, 123, 'failed', 'not_attempted')
                snapshot = cleanup_fixture()
                snapshot['staging'][0].update(id=str(stage), path=str(stage), operation_sha=TARGET)
                before = filesystem_state(host.home)
                with forbid_mutations(), patch('subprocess.run', side_effect=AssertionError('process')):
                    evidence.enrich_snapshot(snapshot)
                self.assertEqual(filesystem_state(host.home), before)
                self.assertEqual(snapshot['durable_operations']['terminal_count'], 1)
                self.assertEqual(snapshot['staging'][0]['operation_id'], opid)
                self.assertTrue(all('operation_id' not in n for n in snapshot['releases'] + snapshot['backups']))
                planned = graph.build_plan(snapshot)
                self.assertEqual(planned['inventory']['durable_operations']['terminal_count'], 1)
                self.assertEqual(planned['nodes']['staging'][0]['operation_id'], opid)
                self.assertTrue(planned['nodes']['staging'][0]['ownership_verified'])
                self.assertFalse(any(n['classification'] == 'SAFE_TO_CLEAN' for rows in planned['cleanup']['nodes'].values() for n in rows))

    def test_receipt_changed_after_read_does_not_claim_stable_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                stage = host.stage()
                evidence.record_stage(opid, 123, 'prepare', str(stage))
                evidence.finish(opid, 123, 'failed', 'not_attempted')
                path = host.evidence / 'operations' / (opid + '.json')
                snapshot = cleanup_fixture()
                snapshot['staging'][0].update(id=str(stage), path=str(stage), operation_sha=TARGET)
                original = evidence._bound
                changed = False
                def racing(payload, category, node):
                    nonlocal changed
                    result = original(payload, category, node)
                    if result and not changed:
                        path.write_bytes(path.read_bytes() + b' ')
                        changed = True
                    return result
                with patch.object(evidence, '_bound', racing):
                    evidence.enrich_snapshot(snapshot)
                self.assertFalse(snapshot['cleanup_evidence']['source_verified'])
                self.assertFalse(snapshot['cleanup_evidence']['records'][0]['source_stable'])
                self.assertFalse(snapshot['staging'][0]['ownership_verified'])
                self.assertIn('RECEIPT_CHANGED_DURING_OBSERVATION', snapshot['durable_operations']['errors'])

    def test_atomic_publish_failure_leaves_no_terminal_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            with host.patched():
                opid = host.begin()
                with patch.object(os, 'replace', side_effect=OSError('fixture interrupted publication')):
                    with self.assertRaises(OSError):
                        evidence.finish(opid, 123, 'failed', 'not_attempted')
                self.assertFalse((host.evidence / 'operations' / (opid + '.json')).exists())
                self.assertEqual(host.active(opid)['state'], 'running')
                self.assertEqual(len(list((host.evidence / '.pending').iterdir())), 1)

    @unittest.skipIf(os.name == 'nt', 'real /proc/flock test runs on Linux CI')
    def test_real_external_flock_child_can_exit_while_bash_fd9_still_proves_ownership(self):
        if not shutil.which('bash') or not shutil.which('flock'):
            self.skipTest('bash/flock unavailable')
        with tempfile.TemporaryDirectory() as directory:
            host = Host(Path(directory))
            host.current.symlink_to(host.releases / PREVIOUS)
            helper = Path(directory) / 'verify.py'
            helper.write_text('''import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import ai_task_storage_evidence as e
import ai_task_storage_evidence_store as s
home=Path(sys.argv[2])
e.HOME=home;e.ROOT=home/'ichiyon-storage-evidence';e.RELEASES=home/'ichiyon-releases';e.BACKUPS=home/'ichiyon-backups';e.CURRENT=home/'ichiyon-current';e.LOCK=home/'ichiyon-deploy.lock'
e._uid=lambda:os.getuid()
s.ROOT=e.ROOT;s._uid=e._uid
e._runtime=lambda target:dict(target_release=None,target_image=None,migration_container=None,image_ids=[],observation_complete=True)
op=e.begin(sys.argv[4],int(sys.argv[3]));e.observe(op,int(sys.argv[3]),'health');e.finish(op,int(sys.argv[3]),'succeeded','not_needed')
print('VERIFIED')
''', encoding='utf8')
            # Keep the owner shell alive while its Python child checks /proc.
            # A final external command can otherwise replace bash via exec,
            # unlike the real deployment shell with its EXIT trap and tail.
            command = ('exec 9>>"$1/ichiyon-deploy.lock"; chmod 600 "$1/ichiyon-deploy.lock"; '
                       'flock -x 9; "$2" "$3" "$4" "$1" "$$" "$5"; '
                       'evidence_status=$?; exit "$evidence_status"')
            result = subprocess.run(['bash', '-c', command, 'evidence-fixture', str(host.home), sys.executable,
                str(helper), str(Path(__file__).resolve().parent), TARGET], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'VERIFIED')


if __name__ == '__main__':
    unittest.main()
