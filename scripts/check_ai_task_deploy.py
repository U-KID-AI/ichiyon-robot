"""Offline deployment checks. Never invokes SSH, Docker or the remote protocol."""

import copy
from dataclasses import replace
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from ai_task_deploy import ProductionDeployAdapter, SUMMARY, parse_proof
from ai_task_deploy_config import DeployConfig, DeploymentSafetyError, normal_file
from ai_task_process import ProcessResult
from ai_task_safety import is_protected_path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = ROOT / 'scripts/ai_task_deploy_remote.sh'
SHA = 'a' * 40
PROOF = f'DEPLOY_RESULT=SUCCESS\nDEPLOYED_COMMIT_SHA={SHA}\nDEPLOY_SUMMARY={SUMMARY}\n'
SCRIPT = REMOTE.read_text(encoding='utf-8')
HELPER = SCRIPT.split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        files = [base / ('ssh.exe' if os.name == 'nt' else 'ssh'),
                 base / 'fake-key', base / 'fake-known-hosts']
        for p in files:
            p.touch()  # Empty local fixtures, never credential files.
        self.config = DeployConfig(files[0], 'example.invalid', 'ubuntu', files[1], files[2],
                                   (base / 'repo', base / 'worktrees'))
        self.adapter = ProductionDeployAdapter(self.config)

    def test_invalid_input_never_launches(self):
        with patch('ai_task_deploy.subprocess.Popen') as popen:
            for value in (None, 3, '', 'A' * 40, 'a' * 39, 'a' * 41, SHA + '\n',
                          SHA + ';id', '--help', 'task/hello', '$(id)', ['a']):
                with self.subTest(value=value), self.assertRaises(DeploymentSafetyError):
                    self.adapter.deploy(value)
            with self.assertRaises(TypeError):
                self.adapter.deploy(SHA, task='Discord text')
            with self.assertRaises(TypeError):
                self.adapter.deploy(SHA, host='example.invalid')
            popen.assert_not_called()

        self.assertEqual(list(inspect.signature(self.adapter.deploy).parameters), ['merge_sha', 'stop_event'])

    def test_late_lease_loss(self):
        stop = threading.Event()
        def lose_lease(*args, **kwargs):
            stop.set()
            return ProcessResult(0, PROOF, '')
        with patch('ai_task_deploy.subprocess.Popen'), patch(
                'ai_task_deploy.communicate_bounded', side_effect=lose_lease):
            with self.assertRaises(DeploymentSafetyError):
                self.adapter.deploy(SHA, stop_event=stop)

    def test_fixed_transport_and_stdin(self):
        stop = threading.Event()
        with patch('ai_task_deploy.subprocess.Popen') as popen, patch(
                'ai_task_deploy.communicate_bounded', return_value=ProcessResult(0, PROOF, '')) as communicate:
            result = self.adapter.deploy(SHA, stop_event=stop)
        self.assertEqual(result.deployed_commit_sha, SHA)
        args, kwargs = popen.call_args
        argv = args[0]
        self.assertEqual(argv[0], str(self.config.ssh_path))
        self.assertEqual(argv[-5:], ['ubuntu@example.invalid', 'bash', '-s', '--', SHA])
        for option in ('BatchMode=yes', 'IdentitiesOnly=yes', 'StrictHostKeyChecking=yes',
                       'ConnectTimeout=15', 'ServerAliveInterval=15', 'ServerAliveCountMax=3',
                       'ForwardAgent=no', 'ClearAllForwardings=yes', 'PermitLocalCommand=no'):
            self.assertIn(option, argv)
        self.assertIn('UserKnownHostsFile="' + self.config.known_hosts_path.as_posix() + '"', argv)
        self.assertEqual(argv[1:4], ['-F', 'none', '-T'])
        self.assertIs(kwargs['shell'], False)
        self.assertEqual(kwargs['stdin'], subprocess.PIPE)
        self.assertEqual(communicate.call_args.kwargs['input_text'], SCRIPT)
        self.assertIsNone(communicate.call_args.kwargs['stop_event'])
        self.assertEqual(communicate.call_args.kwargs['timeout'], self.config.timeout)
        self.assertEqual(communicate.call_args.kwargs['max_output_bytes'], self.config.max_output_bytes)

    def test_failure_flags_and_no_leaks(self):
        for result in (ProcessResult(1, PROOF, 'sensitive'), ProcessResult(0, PROOF, 'warning'),
                       ProcessResult(0, PROOF, '', timed_out=True),
                       ProcessResult(0, PROOF, '', stopped=True),
                       ProcessResult(0, PROOF, '', stdin_cleanup_failed=True)):
            with patch('ai_task_deploy.subprocess.Popen'), patch(
                    'ai_task_deploy.communicate_bounded', return_value=result):
                with self.assertRaises(DeploymentSafetyError) as error:
                    self.adapter.deploy(SHA)
                self.assertEqual(str(error.exception), 'deployment failed closed')
                self.assertTrue(error.exception.__suppress_context__)
        with patch('ai_task_deploy.subprocess.Popen', side_effect=OSError('sensitive path')):
            with self.assertRaises(DeploymentSafetyError) as error:
                self.adapter.deploy(SHA)
            self.assertNotIn('sensitive', str(error.exception))
        stop = threading.Event()
        stop.set()
        with patch('ai_task_deploy.subprocess.Popen') as popen:
            with self.assertRaises(DeploymentSafetyError):
                self.adapter.deploy(SHA, stop_event=stop)
            popen.assert_not_called()

    def test_exact_proof(self):
        self.assertEqual(parse_proof(PROOF, SHA).summary, SUMMARY)
        invalid = ['', PROOF * 2, PROOF.replace(SHA, 'b' * 40), PROOF.replace('SUCCESS', 'OK'),
                   PROOF.replace('DEPLOY_RESULT=SUCCESS\n', ''), PROOF + 'log\n',
                   PROOF.replace('DEPLOY_SUMMARY=', ' DEPLOY_SUMMARY='), PROOF.rstrip('\n')]
        for summary in ('', 'x' * 4001, 'safe\nunsafe', 'safe\runsafe', '\x00', '\x1b', '\x7f', '\u0085', 'unreviewed text'):
            invalid.append(PROOF.replace(SUMMARY, summary))
        for marker in PROOF.splitlines(keepends=True):
            invalid.extend([PROOF + marker, PROOF.replace(marker, '')])
        for value in invalid:
            with self.subTest(value=value[:80]), self.assertRaises(DeploymentSafetyError):
                parse_proof(value, SHA)

    def test_config_shapes_and_limits(self):
        for host in ('-host', 'host name', 'host\n', 'a/b', 'a:b', 'a@b', 'a;b',
                     'https://a', 'a?b', 'a#b', 'a$(id)', '', '256.1.1.1', 'a..b', 'a.' ):
            with self.subTest(host=host), self.assertRaises(DeploymentSafetyError):
                replace(self.config, ssh_host=host).validate()
        for host in ('example.invalid', '127.0.0.1', 'host-1'):
            replace(self.config, ssh_host=host).validate()
        for field, value in [('ssh_user', 'root'), ('ssh_user', 'ubuntu '),
                             ('timeout', float('nan')), ('timeout', float('inf')),
                             ('timeout', 0), ('timeout', -1), ('timeout', 7201),
                             ('max_output_bytes', 0), ('max_output_bytes', 1048577),
                             ('max_output_bytes', True), ('excluded_roots', ())]:
            with self.subTest(field=field, value=value), self.assertRaises(DeploymentSafetyError):
                replace(self.config, **{field: value}).validate()
        for field in ('ssh_path', 'ssh_key_path', 'known_hosts_path'):
            for value in (Path('relative'), Path(self.temp.name), Path(self.temp.name) / 'missing'):
                with self.subTest(field=field), self.assertRaises(DeploymentSafetyError):
                    replace(self.config, **{field: value}).validate()
            with self.assertRaises(DeploymentSafetyError):
                replace(self.config, excluded_roots=(Path(self.temp.name),)).validate()
        with patch('ai_task_deploy_config.is_reparse_point', return_value=True):
            with self.assertRaises(DeploymentSafetyError):
                self.config.validate()
        with patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaises(DeploymentSafetyError):
                self.config.validate()
        wrong = Path(self.temp.name) / 'other-executable'
        wrong.touch()
        with self.assertRaises(DeploymentSafetyError):
            replace(self.config, ssh_path=wrong).validate()
        with self.assertRaises(DeploymentSafetyError):
            normal_file(ROOT / 'scripts/ai_task_deploy.py', (ROOT,))

    def test_environment_only_configuration(self):
        values = dict(SSH_PATH=str(self.config.ssh_path), SSH_HOST='example.invalid', SSH_USER='ubuntu',
                      SSH_KEY_PATH=str(self.config.ssh_key_path), KNOWN_HOSTS_PATH=str(self.config.known_hosts_path))
        env = {'AI_TASK_RUNNER_DEPLOY_' + k: v for k, v in values.items()}
        with patch.dict(os.environ, env, clear=True), patch.object(Path, 'read_text', side_effect=AssertionError('no reads')):
            config = DeployConfig.from_environment(repo_root=ROOT, worktree_root=Path(self.temp.name) / 'worktrees')
            self.assertEqual(config.ssh_host, 'example.invalid')
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(DeploymentSafetyError):
            DeployConfig.from_environment(repo_root=ROOT, worktree_root=ROOT)


class RemoteChecks(unittest.TestCase):
    def setUp(self):
        self.h = types.ModuleType('offline_remote_helper')
        exec(compile(HELPER, '<reviewed remote helper>', 'exec'), self.h.__dict__)

    def test_phase3b_legacy_release_without_src_revision(self):
        h = self.h

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            release = root / SHA
            src = release / 'src'
            src.mkdir(parents=True)

            (release / 'REVISION').write_text(
                SHA + '\n',
                encoding='utf-8',
            )
            (src / 'legacy.txt').write_text(
                'phase3b\n',
                encoding='utf-8',
            )

            for name in (
                'compose.immutable.yml',
                'persistence.txt',
                'rollback-images.txt',
                'validate-immutable-compose.py',
            ):
                (release / name).write_text(
                    'fixture\n',
                    encoding='utf-8',
                )

            (release / 'immutable-image.txt').write_text(
                'ichiyon-robot-app:' + SHA + '\n',
                encoding='utf-8',
            )

            with patch.object(h, 'ROOT', root):
                # Actual Phase 3B production shape: no src/REVISION.
                self.assertEqual(
                    h.release(release),
                    SHA,
                )

                # New-format marker, when present, remains strictly bound.
                (src / 'REVISION').write_text(
                    'b' * 40 + '\n',
                    encoding='utf-8',
                )

                with self.assertRaises(AssertionError):
                    h.release(release)

                (src / 'REVISION').write_text(
                    SHA + '\n',
                    encoding='utf-8',
                )

                self.assertEqual(
                    h.release(release),
                    SHA,
                )

    def test_compose_contract_with_fakes(self):
        h = self.h
        mounts = [dict(source=s, target=t, read_only=r, type=k) for s, t, r, k in h.expected_mounts()]
        config = {'volumes': {'postgres_data': {'name': h.VOLUME, 'external': True}},
                  'networks': {'default': {'name': h.NETWORK, 'external': True}},
                  'services': {s: {'image': 'ichiyon-robot-app:' + SHA, 'pull_policy': 'never',
                                   'volumes': copy.deepcopy(mounts), 'networks': {'default': {}}} for s in h.APPS}}
        config['services']['db'] = {'volumes': [{'source': 'postgres_data', 'target': '/var/lib/postgresql/data'}]}
        with patch.object(h, 'release', return_value=SHA), patch.object(h, 'compose', return_value=json.dumps(config)):
            h.contract(Path('/unused'))
        for field, value in [('image', 'latest'), ('build', {'context': '.'}), ('build', None), ('pull_policy', 'always'),
                             ('privileged', True), ('volumes', mounts + [dict(source='/', target='/app', type='bind')]),
                             ('networks', {'other': {}})]:
            bad = copy.deepcopy(config)
            bad['services']['admin'][field] = value
            with patch.object(h, 'release', return_value=SHA), patch.object(h, 'compose', return_value=json.dumps(bad)):
                with self.subTest(field=field), self.assertRaises(AssertionError):
                    h.contract(Path('/unused'))

    def test_archive_rejects_escapes_and_secret_files(self):
        h = self.h
        for name, kind in [('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE),
                           ('secrets/private', tarfile.REGTYPE), ('.git/config', tarfile.REGTYPE),
                           ('.env', tarfile.REGTYPE), ('link', tarfile.SYMTYPE), ('hardlink', tarfile.LNKTYPE)]:
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w') as tar:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = '/escape' if kind != tarfile.REGTYPE else ''
                tar.addfile(member, io.BytesIO(b''))
            with tempfile.TemporaryDirectory() as temp, patch.object(h, 'run', return_value=archive.getvalue()):
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    h.prepare(Path(temp), SHA)

    def test_image_secret_audit_rejects_nonzero_and_revision(self):
        h = self.h
        image = {'Id': 'sha256:fake', 'Config': {'Labels': {'org.opencontainers.image.revision': SHA}}}
        for count in (0, 1):
            with patch.object(h, 'run', side_effect=[json.dumps([image]).encode(), f'BAKED_SECRET_FILE_COUNT={count}\n'.encode()]) as run:
                if count:
                    with self.assertRaises(AssertionError):
                        h.image_check(SHA)
                else:
                    self.assertEqual(h.image_check(SHA), 'sha256:fake')
                    self.assertIn('none', run.call_args.args[0])
                    self.assertIn('--read-only', run.call_args.args[0])
        image['Config']['Labels']['org.opencontainers.image.revision'] = 'b' * 40
        with patch.object(h, 'run', return_value=json.dumps([image]).encode()), self.assertRaises(AssertionError):
            h.image_check(SHA)

    def test_health_checks_actual_state_with_fakes(self):
        h = self.h
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp)
            migrations = release / 'src/migrations'
            migrations.mkdir(parents=True)
            (migrations / '001_test.sql').touch()
            mounts = [dict(Source=s, Destination=t, RW=not r, Type=k)
                      for s, t, r, k in h.expected_mounts()]
            containers = {name: {'Id': name, 'Image': 'image-id',
                                'Config': {'Image': 'ichiyon-robot-app:' + SHA},
                                'State': {'Running': True, 'StartedAt': 'fixed'},
                                'RestartCount': 0, 'Mounts': copy.deepcopy(mounts),
                                'NetworkSettings': {'Networks': {h.NETWORK: {}}}}
                          for name in h.APPS}
            for scenario in ('healthy', 'stopped', 'restart', 'wrong_image', 'wrong_mount',
                             'admin_status', 'no_ready', 'migration_mismatch', 'infra_changed'):
                state = copy.deepcopy(containers)
                if scenario == 'stopped':
                    state['bot']['State']['Running'] = False
                if scenario == 'restart':
                    state['bot']['RestartCount'] = 1
                if scenario == 'wrong_image':
                    state['admin']['Image'] = 'old'
                if scenario == 'wrong_mount':
                    state['admin']['Mounts'][0]['Destination'] = '/app'

                def fake_run(args, **kwargs):
                    if args[0] == 'curl':
                        return b'503' if scenario == 'admin_status' else b'200'
                    if args[:2] == ['docker', 'logs']:
                        return b'' if scenario == 'no_ready' else b'Logged in as '
                    if args[:2] == ['docker', 'exec']:
                        return b'[]' if scenario == 'migration_mismatch' else b'["001_test"]'
                    raise AssertionError('unexpected fake operation')

                with patch.object(h, 'release', return_value=SHA), patch.object(h, 'contract'), \
                        patch.object(h, 'image_check', return_value='image-id'), \
                        patch.object(h, 'container', side_effect=state.__getitem__), \
                        patch.object(h, 'infra', return_value=['changed'] if scenario == 'infra_changed' else ['fixed']), \
                        patch.object(h, 'run', side_effect=fake_run), \
                        patch.object(h.time, 'sleep'), \
                        patch.object(h.subprocess, 'run', return_value=types.SimpleNamespace(stderr=b'')):
                    with self.subTest(scenario=scenario):
                        if scenario == 'healthy':
                            h.health(release, ['fixed'])
                        else:
                            with self.assertRaises(AssertionError):
                                h.health(release, ['fixed'])

    def test_infrastructure_snapshot(self):
        h = self.h
        containers = {name: {'Id': name, 'Name': '/ichiyon-robot-' + name,
                            'RestartCount': 0, 'State': {'Running': True, 'StartedAt': 'fixed'},
                            'NetworkSettings': {'Networks': {h.NETWORK: {}}},
                            'Mounts': [{'Name': h.VOLUME, 'Destination': '/var/lib/postgresql/data'}]}
                      for name in h.INFRA}
        with patch.object(h, 'container', side_effect=containers.__getitem__):
            self.assertEqual(h.infra(), [['db', 0, 'fixed'], ['youtube-vpn-proxy', 0, 'fixed']])
            containers['db']['Mounts'][0]['Name'] = 'wrong'
            with self.assertRaises(AssertionError):
                h.infra()

    def test_static_remote_invariants(self):
        for required in ('set -euo pipefail', '$# == 1', '^[0-9a-f]{40}$', 'flock -x',
                         'https://github.com/U-KID-AI/ichiyon-robot.git', 'refs/heads/main', 'FETCH_HEAD',
                         "'archive', '--format=tar'", "APPS = ('admin', 'bot', 'bot-irsia')",
                         "INFRA = ('db', 'youtube-vpn-proxy')", '--no-deps --no-build --pull never',
                         'stop admin bot bot-irsia', 'infra_same', "c['RestartCount'] == 0",
                         "status == b'200'", "b'Logged in as '", 'BAKED_SECRET_FILE_COUNT=0',
                         "m['Destination'] == '/app'", 'ichiyon-robot_postgres_data', 'ichiyon-robot_default',
                         "(path / 'checksums.sha256').read_text() == expected", "'pg_restore', '--list'", 'tarfile.open', 'scripts/migrate.py',
                         'actual == expected', 'quiesced=1', 'DEPLOY_ERROR=ROLLBACK_FAILED'):
            self.assertIn(required, SCRIPT)
        for forbidden in ('git pull', 'git reset', 'git clean', 'compose down', ' prune', 'eval ',
                          '\nsource ', 'bash -c', 'printenv', 'env |', 'set -x', '/home/ubuntu/ichiyon-robot/'):
            self.assertNotIn(forbidden, '\n'.join(l for l in SCRIPT.splitlines() if not l.lstrip().startswith('#')))
        for line in SCRIPT.splitlines():
            if 'pg_restore' in line:
                self.assertIn('--list', line)
        self.assertLess(SCRIPT.index('flock -x'), SCRIPT.index('helper=$(mktemp'))
        self.assertLess(SCRIPT.index('rev-parse FETCH_HEAD'), SCRIPT.index('if [[ $previous == "$release" ]]'))
        self.assertLess(SCRIPT.index('quiesced=1'), SCRIPT.index('compose "$previous" stop'))
        self.assertLess(SCRIPT.index('python3 -I "$helper" backup "$backup"'), SCRIPT.index('python3 -I "$helper" migrate "$release"'))
        self.assertLess(SCRIPT.index('python3 -I "$helper" migrate "$release"'), SCRIPT.index('compose "$release" up'))
        probe = SCRIPT.split('probe() {', 1)[1].split('\n}', 1)[0]
        for line in probe.splitlines():
            if line.lstrip().startswith('[['):
                self.assertIn('|| return 1', line)
        self.assertLess(SCRIPT.index('health "$release"', SCRIPT.index('compose "$release" up')),
                        SCRIPT.index('mv -Tf'))
        idempotent = SCRIPT.split('if [[ $previous == "$release" ]]; then', 1)[1].split('\nelse', 1)[0]
        self.assertIn('health "$release"', idempotent)
        self.assertNotIn(' up ', idempotent)
        self.assertNotIn('docker build', idempotent)
        self.assertEqual(SCRIPT.count('DEPLOY_RESULT=SUCCESS'), 1)
        for line in SCRIPT.splitlines():
            if 'compose "$' in line and (' up ' in line or ' stop ' in line):
                self.assertIn('admin bot bot-irsia', line)
                self.assertNotIn('youtube-vpn-proxy', line)
                if ' up ' in line:
                    self.assertIn('--no-deps', line)
            if "['docker', 'rm'," in line:
                self.assertTrue("name]" in line or "'ichiyon-robot-migrate-' + path.name]" in line)
                self.assertNotIn('--force', line)
        self.assertNotIn('docker stop', SCRIPT)
        self.assertNotIn('docker restart', SCRIPT)

    def test_private_creation_publication_and_cleanup(self):
        self.assertLess(SCRIPT.index('umask 077'), SCRIPT.index('exec 9>>'))
        self.assertLess(SCRIPT.index('umask 077'), SCRIPT.index('mktemp'))
        self.assertNotIn('mkdir -- "$backup"', SCRIPT)
        self.assertIn('mktemp -d "$backups_root/.backup-$sha.XXXXXXXX"', SCRIPT)
        self.assertLess(SCRIPT.index('backup "$backup_stage"'), SCRIPT.index('mv -Tn -- "$backup_stage"'))
        self.assertLess(SCRIPT.index('sync -f "$backup_stage"'), SCRIPT.index('mv -Tn -- "$backup_stage"'))
        self.assertIn('[[ ! -e $backup_stage ]]', SCRIPT)
        self.assertNotIn('rm -rf', SCRIPT)
        self.assertLess(SCRIPT.rindex('cleanup_temporaries'), SCRIPT.index("printf 'DEPLOY_RESULT=SUCCESS"))
        self.assertIn('rm -f -- "$infra_file"', SCRIPT)
        self.assertIn('rm -f -- "$helper"', SCRIPT)
        h = self.h
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with patch.object(h, 'ROOT', root):
                for name in ('.prepare-' + SHA + '.12345678', '.release-' + SHA + '.abcdefgh'):
                    path = root / name
                    path.mkdir()
                    (path / 'fixture').touch()
                    h.cleanup(path, SHA)
                    self.assertFalse(path.exists())
                for name in (SHA, '.prepare-' + 'b' * 40 + '.12345678', '.prepare-' + SHA + '.short', 'unrelated'):
                    path = root / name
                    path.mkdir()
                    with self.assertRaises(AssertionError):
                        h.cleanup(path, SHA)
                    self.assertTrue(path.exists())

    def test_backup_interrupted_and_corrupt_states(self):
        h = self.h
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            snapshot = root / 'snapshot'
            snapshot.write_bytes(b'[]')
            backup = root / SHA
            backup.mkdir()
            previous = root / ('b' * 40)
            (backup / 'READY').touch()
            with patch.object(h, 'run') as run:
                with self.assertRaises(AssertionError):
                    h.backup_validate(backup, previous, snapshot)
                run.assert_not_called()
            (backup / 'previous').write_text(str(previous) + '\n')
            (backup / 'infra.json').write_bytes(b'[]')
            (backup / 'production.dump').write_bytes(b'offline dump fixture')
            with tarfile.open(backup / 'persistence.tar', 'w') as tar:
                for name in ('data', 'assets/images', 'secrets', '.env'):
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.REGTYPE if name == '.env' else tarfile.DIRTYPE
                    tar.addfile(member)
            def checksums():
                (backup / 'checksums.sha256').write_text(''.join(
                    h.hashlib.sha256((backup / n).read_bytes()).hexdigest() + '  ' + n + '\n'
                    for n in ('production.dump', 'persistence.tar')))
            checksums()
            with patch.object(h, 'run', return_value=b'validated listing') as run:
                h.backup_validate(backup, previous, snapshot)
                self.assertEqual(run.call_args.args[0][-2:], ['pg_restore', '--list'])
                # Identical fixed-prefix staging shape can validate before publication.
                stage = root / ('.backup-' + SHA + '.12345678')
                backup.rename(stage)
                self.assertFalse(backup.exists())
                h.backup_validate(stage, previous, snapshot)
                stage.rename(backup)
                with self.assertRaises(AssertionError):
                    h.backup_validate(backup, root / 'other', snapshot)
                (backup / 'production.dump').write_bytes(b'corrupt')
                with self.assertRaises(AssertionError):
                    h.backup_validate(backup, previous, snapshot)
                checksums()
            with patch.object(h, 'run', side_effect=RuntimeError('invalid dump')):
                with self.assertRaises(RuntimeError):
                    h.backup_validate(backup, previous, snapshot)
            (backup / 'persistence.tar').write_bytes(b'invalid archive')
            checksums()
            with patch.object(h, 'run', return_value=b'ok'), self.assertRaises(tarfile.ReadError):
                h.backup_validate(backup, previous, snapshot)

    def test_migration_identity_and_bounded_reconciliation(self):
        h = self.h
        name = 'ichiyon-robot-migrate-' + SHA
        c = {'Name': '/' + name, 'Image': 'image-id', 'Mounts': [],
             'Path': 'python', 'Args': ['-I', 'scripts/migrate.py'],
             'Config': {'Labels': {'ichiyon.fixed-deploy.migration': 'true'},
                        'Image': 'ichiyon-robot-app:' + SHA, 'Entrypoint': ['python'],
                        'Cmd': ['-I', 'scripts/migrate.py'], 'WorkingDir': '/app'},
             'HostConfig': {'NetworkMode': h.NETWORK, 'Privileged': False,
                            'RestartPolicy': {'Name': 'no'}},
             'NetworkSettings': {'Networks': {h.NETWORK: {}}},
             'State': {'Running': False, 'Status': 'exited', 'ExitCode': 0}}
        def responses(container):
            return [b'id', b'id', json.dumps([container]), json.dumps([{'Id': 'image-id'}])]
        with patch.object(h, 'run', side_effect=responses(c)):
            self.assertEqual(h.migration_state(SHA), 0)
        for section, field, value in ((None, 'Name', '/arbitrary'), (None, 'Image', 'wrong'),
                                      ('Config', 'Cmd', ['arbitrary']), ('Config', 'Entrypoint', ['sh']),
                                      ('Config', 'Image', 'other'), ('Config', 'Labels', {}),
                                      ('Config', 'WorkingDir', '/other'), (None, 'Path', 'sh'),
                                      (None, 'Args', ['other']),
                                      ('HostConfig', 'NetworkMode', 'other'),
                                      ('NetworkSettings', 'Networks', {'other': {}}),
                                      ('State', 'Running', True), ('State', 'Status', 'created')):
            bad = copy.deepcopy(c)
            (bad if section is None else bad[section])[field] = value
            with patch.object(h, 'run', side_effect=responses(bad)), self.assertRaises((AssertionError, KeyError)):
                h.migration_state(SHA)
        with patch.object(h, 'run', side_effect=[b'id', b'id other'] ), self.assertRaises(AssertionError):
            h.migration_state(SHA)
        for state in (None, 0, 1):
            with patch.object(h, 'release', return_value=SHA), \
                    patch.object(h, 'migration_state', side_effect=[state, 0]), \
                    patch.object(h, 'migration_database') as database, patch.object(h, 'run') as run:
                h.migrate(Path('/fixture'))
                database.assert_called_once()
                if state == 0:
                    run.assert_not_called()
                else:
                    calls = run.call_args_list
                    if state == 1:
                        self.assertEqual(calls[0].args[0], ['docker', 'rm', name])
                    self.assertEqual(calls[-1].kwargs['timeout'], 1800)
                    self.assertEqual(calls[-1].args[0][-2:], ['-I', 'scripts/migrate.py'])
                    self.assertNotIn('--rm', calls[-1].args[0])
        with patch.object(h, 'release', return_value=SHA), patch.object(h, 'migration_state', return_value=0), \
                patch.object(h, 'migration_database', side_effect=AssertionError), patch.object(h, 'run') as run:
            with self.assertRaises(AssertionError):
                h.migrate(Path('/fixture'))
            run.assert_not_called()
        with patch.object(h, 'release', return_value=SHA), \
                patch.object(h, 'migration_state', side_effect=AssertionError), patch.object(h, 'run') as run:
            with self.assertRaises(AssertionError):
                h.migrate(Path('/fixture'))
            run.assert_not_called()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / 'src/migrations').mkdir(parents=True)
            (path / 'src/migrations/001.sql').touch()
            for actual in (b'["001"]', b'[]', b'["001", "extra"]'):
                with patch.object(h, 'run', return_value=actual):
                    if actual == b'["001"]':
                        h.migration_database(path)
                    else:
                        with self.assertRaises(AssertionError):
                            h.migration_database(path)
        idempotent = SCRIPT.split('if [[ $previous == "$release" ]]; then', 1)[1].split('\nelse', 1)[0]
        self.assertNotIn(' migrate ', idempotent)
        self.assertNotIn(' stop ', idempotent)

    def test_existing_immutable_release_without_new_markers(self):
        h = self.h
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / SHA
            (path / 'src').mkdir(parents=True)
            for name in ('REVISION', 'src/REVISION'):
                (path / name).write_text(SHA + '\n')
            (path / 'immutable-image.txt').write_text('ichiyon-robot-app:' + SHA + '\n')
            for name in ('compose.immutable.yml', 'persistence.txt', 'rollback-images.txt', 'validate-immutable-compose.py'):
                (path / name).touch()
            with patch.object(h, 'ROOT', root):
                self.assertEqual(h.release(path), SHA)
            self.assertEqual(len(list(path.iterdir())), 7)

    def test_protected_paths_and_ci(self):
        for path in ('Dockerfile', '.dockerignore', 'docker-compose.yml', 'docker-compose.prod.yml',
                     'scripts/ai_task_deploy.py', 'scripts/ai_task_deploy_config.py',
                     'scripts/ai_task_deploy_remote.sh', 'scripts/check_ai_task_deploy.py'):
            self.assertTrue(is_protected_path(path))
            self.assertTrue(is_protected_path(path.upper()))
        self.assertIn('secrets/', (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines())
        ci = (ROOT / '.github/workflows/checks.yml').read_text()
        for value in ('python-and-compose:', 'python scripts/check_ai_task_deploy.py', 'bash -n scripts/ai_task_deploy_remote.sh'):
            self.assertIn(value, ci)
        runner = (ROOT / 'scripts/ai_task_runner.py').read_text(encoding='utf-8')
        self.assertIn(
            'from ai_task_deploy import ProductionDeployAdapter',
            runner,
        )
        self.assertIn(
            'from ai_task_deploy_config import DeployConfig',
            runner,
        )
        self.assertIn('DeployConfig.from_environment(', runner)
        self.assertIn(
            'deployer=ProductionDeployAdapter(deploy_config)',
            ''.join(runner.split()),
        )
        self.assertIn(
            'except(ValueError,OSError,SafetyError)asexc:',
            ''.join(runner.split()),
        )


def bash_syntax():
    # Prefer Git Bash over the Windows WSL launcher, which may have no distribution.
    candidates = []
    git = shutil.which('git')
    if os.name == 'nt' and git:
        candidates.append(Path(git).parent.parent / 'bin/bash.exe')
    bash = shutil.which('bash')
    if bash:
        candidates.append(Path(bash))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        if os.name == 'nt' and 'system32' in str(candidate).lower():
            continue
        subprocess.run([str(candidate), '-n', str(REMOTE)], check=True, timeout=30)
        print('PASS bash -n (syntax only; protocol not executed)')
        return
    if os.name != 'nt':
        raise AssertionError('Linux CI requires bash')
    print('SKIP bash syntax: no local Bash interpreter (WSL launcher is not an interpreter)')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    bash_syntax()
