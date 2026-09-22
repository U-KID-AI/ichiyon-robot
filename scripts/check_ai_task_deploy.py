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
from ai_task_deploy_config import DeployConfig, DeploymentError, normal_file
from ai_task_diagnostics import redact_secrets
from ai_task_process import ProcessResult

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
        # Model a separate trusted installation even when TMPDIR is inside the
        # checkout. Fixtures must stay outside that modeled installation.
        installation = base / 'runner-install' / 'scripts' / 'ai_task_deploy_config.py'
        installation_patch = patch('ai_task_deploy_config.__file__', str(installation))
        installation_patch.start()
        self.addCleanup(installation_patch.stop)
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
                with self.subTest(value=value), self.assertRaises(DeploymentError):
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
            with self.assertRaises(DeploymentError):
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
        for result in (ProcessResult(1, PROOF, 'permission denied; password=hidden-value'),
                       ProcessResult(0, PROOF, '', timed_out=True),
                       ProcessResult(0, PROOF, '', stopped=True),
                       ProcessResult(0, PROOF, '', stdin_cleanup_failed=True)):
            with patch('ai_task_deploy.subprocess.Popen'), patch(
                    'ai_task_deploy.communicate_bounded', return_value=result):
                with self.assertRaises(DeploymentError) as error:
                    self.adapter.deploy(SHA)
                self.assertIn('deployment transport failed', str(error.exception))
                self.assertNotIn('hidden-value', str(error.exception))
                if result.returncode:
                    self.assertIn('permission denied', str(error.exception))
                self.assertTrue(error.exception.__suppress_context__)
        with patch('ai_task_deploy.subprocess.Popen', side_effect=OSError('missing key ' + str(self.config.ssh_key_path))):
            with self.assertRaises(DeploymentError) as error:
                self.adapter.deploy(SHA)
            self.assertIn(str(self.config.ssh_key_path), str(error.exception))
            self.assertIn('missing key', str(error.exception))
        stop = threading.Event()
        stop.set()
        with patch('ai_task_deploy.subprocess.Popen') as popen:
            with self.assertRaises(DeploymentError):
                self.adapter.deploy(SHA, stop_event=stop)
            popen.assert_not_called()

    def test_successful_transport_stderr_is_not_a_failure(self):
        with patch('ai_task_deploy.subprocess.Popen'), patch(
                'ai_task_deploy.communicate_bounded', return_value=ProcessResult(0, PROOF, 'SSH warning')):
            self.assertEqual(self.adapter.deploy(SHA).deployed_commit_sha, SHA)

    def test_transport_preserves_full_stderr_and_rollback_result(self):
        diagnostic = ('BEGIN host=example.invalid key=' + str(self.config.ssh_key_path) + '\n' +
                      'migration output\n' * 10000 + 'END password=fixture-secret\n')

        def launch(*args, **kwargs):
            kwargs['stderr'].write(diagnostic.encode('utf-8'))
            kwargs['stderr'].flush()
            kwargs['stdout'].write(b'full stdout diagnostic\n' * 10000)
            kwargs['stdout'].flush()
            return object()

        with patch('ai_task_deploy.subprocess.Popen', side_effect=launch), patch(
                'ai_task_deploy.communicate_bounded', return_value=ProcessResult(1, 'DEPLOY_ERROR=ROLLED_BACK\n', '')):
            with self.assertRaises(DeploymentError) as error:
                self.adapter.deploy(SHA)
        detail = str(error.exception)
        self.assertIn('BEGIN host=example.invalid key=' + str(self.config.ssh_key_path), detail)
        self.assertIn('END password=[redacted]', detail)
        self.assertIn('DEPLOY_ERROR=ROLLED_BACK', detail)
        self.assertNotIn('fixture-secret', detail)
        self.assertEqual(detail.count('migration output\n'), 10000)
        self.assertEqual(detail.count('full stdout diagnostic\n'), 10000)

    def test_success_after_large_stdout_logs(self):
        def launch(*args, **kwargs):
            kwargs['stdout'].write(('build log\n' * 10000 + PROOF).encode())
            kwargs['stdout'].flush()
            return object()

        with patch('ai_task_deploy.subprocess.Popen', side_effect=launch), patch(
                'ai_task_deploy.communicate_bounded', return_value=ProcessResult(0, '', '')):
            self.assertEqual(self.adapter.deploy(SHA).deployed_commit_sha, SHA)

    def test_full_diagnostics_redact_only_secret_values(self):
        with patch.dict(os.environ, {'EXAMPLE_TOKEN': 'known-credential-value'}):
            detail = redact_secrets('operation failed known-credential-value ' +
                'https://user:db-pass@example/db Bearer bearer-value ' +
                'password="two words" api_key=key-value ' +
                '-----BEGIN PRIVATE KEY-----\n' + ('private-data\n' * 400) +
                '-----END PRIVATE KEY-----')
        self.assertIn('operation failed', detail)
        for secret in ('known-credential-value', 'db-pass', 'bearer-value', 'two words', 'key-value', 'private-data'):
            self.assertNotIn(secret, detail)
        diagnostic = f'host example.invalid key file {self.config.ssh_key_path}\n' + ('diagnostic\n' * 10000)
        self.assertEqual(str(DeploymentError(diagnostic)), diagnostic)

    def test_proof_requires_success_and_exact_sha_not_summary_or_log_format(self):
        self.assertEqual(parse_proof(PROOF, SHA).summary, SUMMARY)
        valid = [PROOF * 2, PROOF + 'log\n', 'preflight ok\n' + PROOF,
                 PROOF.replace('DEPLOY_SUMMARY=', ' DEPLOY_SUMMARY='), PROOF.rstrip('\n'),
                 PROOF.replace(f'DEPLOY_SUMMARY={SUMMARY}\n', ''), PROOF.replace('\n', '\r\n')]
        for summary in ('', 'x' * 4001, 'first\nsecond', '\u65e5\u672c\u8a9e', 'deployment complete'):
            valid.append(PROOF.replace(SUMMARY, summary))
        for value in valid:
            self.assertEqual(parse_proof(value, SHA).deployed_commit_sha, SHA)
        invalid = ['', PROOF.replace(SHA, 'b' * 40), PROOF.replace('SUCCESS', 'FAILED'),
                   PROOF.replace('DEPLOY_RESULT=SUCCESS\n', ''),
                   PROOF.replace('DEPLOYED_COMMIT_SHA=' + SHA + '\n', ''),
                   PROOF + 'DEPLOY_RESULT=FAILED\n', PROOF + 'DEPLOYED_COMMIT_SHA=' + 'b' * 40]
        for value in invalid:
            with self.subTest(value=value[:80]), self.assertRaises(DeploymentError):
                parse_proof(value, SHA)
        with self.assertRaises(DeploymentError) as error:
            parse_proof('build failed; token=fixture-secret', SHA, stderr='daemon unavailable')
        self.assertIn('build failed', str(error.exception))
        self.assertIn('daemon unavailable', str(error.exception))
        self.assertNotIn('fixture-secret', str(error.exception))

    def test_config_shapes_and_limits(self):
        for host in ('-host', 'host name', 'host\n', 'a/b', 'a:b', 'a@b', 'a;b',
                     'https://a', 'a?b', 'a#b', 'a$(id)', '', '256.1.1.1', 'a..b', 'a.' ):
            with self.subTest(host=host), self.assertRaises(DeploymentError):
                replace(self.config, ssh_host=host).validate()
        for host in ('example.invalid', '127.0.0.1', 'host-1'):
            replace(self.config, ssh_host=host).validate()
        for field, value in [('ssh_user', 'root'), ('ssh_user', 'ubuntu '),
                             ('timeout', float('nan')), ('timeout', float('inf')),
                             ('timeout', 0), ('timeout', -1), ('timeout', 7201),
                             ('max_output_bytes', 0), ('max_output_bytes', 1048577),
                             ('max_output_bytes', True)]:
            with self.subTest(field=field, value=value), self.assertRaises(DeploymentError):
                replace(self.config, **{field: value}).validate()
        for field in ('ssh_path', 'ssh_key_path', 'known_hosts_path'):
            for value in (Path('relative'), Path(self.temp.name), Path(self.temp.name) / 'missing'):
                with self.subTest(field=field), self.assertRaises(DeploymentError):
                    replace(self.config, **{field: value}).validate()
        replace(self.config, excluded_roots=(Path(self.temp.name),)).validate()
        replace(self.config, excluded_roots=()).validate()
        wrong = Path(self.temp.name) / 'other-executable'
        wrong.touch()
        replace(self.config, ssh_path=wrong).validate()
        self.assertEqual(normal_file(ROOT / 'scripts/ai_task_deploy.py', (ROOT,)),
                         (ROOT / 'scripts/ai_task_deploy.py').resolve())
        punctuation = Path(self.temp.name) / "transport file's $name%"
        punctuation.touch()
        self.assertEqual(normal_file(punctuation, ()), punctuation.resolve())

    def test_environment_only_configuration(self):
        values = dict(SSH_PATH=str(self.config.ssh_path), SSH_HOST='example.invalid', SSH_USER='ubuntu',
                      SSH_KEY_PATH=str(self.config.ssh_key_path), KNOWN_HOSTS_PATH=str(self.config.known_hosts_path))
        env = {'AI_TASK_RUNNER_DEPLOY_' + k: v for k, v in values.items()}
        with patch.dict(os.environ, env, clear=True), patch.object(Path, 'read_text', side_effect=AssertionError('no reads')):
            config = DeployConfig.from_environment(repo_root=Path(self.temp.name) / 'repo', worktree_root=Path(self.temp.name) / 'worktrees')
            self.assertEqual(config.ssh_host, 'example.invalid')
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(DeploymentError, 'AI_TASK_RUNNER_DEPLOY_SSH_PATH'):
            DeployConfig.from_environment(repo_root=ROOT, worktree_root=ROOT)


class RemoteChecks(unittest.TestCase):
    def setUp(self):
        self.h = types.ModuleType('offline_remote_helper')
        exec(compile(HELPER, '<reviewed remote helper>', 'exec'), self.h.__dict__)

    def test_remote_command_failure_keeps_stderr(self):
        h = self.h
        with patch.object(h.subprocess, 'run', return_value=types.SimpleNamespace(
                returncode=17, stdout=b'', stderr=b'docker daemon unavailable')) as run:
            with self.assertRaisesRegex(RuntimeError, 'exit=17.*docker daemon unavailable'):
                h.run(['docker', 'inspect', 'fixture'])
        self.assertEqual(run.call_args.kwargs['stderr'], subprocess.PIPE)
        self.assertNotIn('exec >/dev/null 2>&1', SCRIPT)
        self.assertIn('report_diagnostics', SCRIPT)

    def test_linked_installation_and_deployment_directories_are_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve()
            file = path / 'fixture'
            file.touch()
            with patch.object(Path, 'is_symlink', return_value=True):
                self.h.normal(path, True)
                self.h.normal(file)
                self.assertEqual(normal_file(file, (path,)), file.resolve())
        self.assertIn('tar --dereference', SCRIPT)

    def test_archive_accepts_normal_reviewed_files(self):
        h = self.h
        archive = io.BytesIO()
        names = ('fixtures/.env', 'fixtures/public.pem', 'fixtures/test.key',
                 'secrets/README.md', 'docs/space name.txt', 'docs/\u65e5\u672c\u8a9e.md')
        with tarfile.open(fileobj=archive, mode='w') as tar:
            for name in names:
                member = tarfile.TarInfo(name)
                member.size = 7
                tar.addfile(member, io.BytesIO(b'fixture'))
        with tempfile.TemporaryDirectory() as temp, patch.object(h, 'run', return_value=archive.getvalue()), \
                patch.object(h, 'container', return_value={'Image': 'fake-image'}), \
                patch.object(h, '__file__', str(REMOTE), create=True):
            root = Path(temp)
            h.prepare(root, SHA)
            for name in names:
                self.assertEqual((root / 'src' / name).read_bytes(), b'fixture')

    def test_archive_accepts_contained_symlink_and_cleanup_does_not_follow(self):
        h = self.h
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            stage = root / ('.prepare-' + SHA + '.12345678')
            stage.mkdir()
            outside = root / 'keep.txt'
            outside.write_bytes(b'keep')
            try:
                (stage / 'link').symlink_to(outside)
            except OSError as exc:
                if getattr(exc, 'winerror', None) == 1314:
                    self.skipTest('Windows symlink privilege unavailable')
                raise
            with patch.object(h, 'ROOT', root):
                h.cleanup(stage, SHA)
            self.assertEqual(outside.read_bytes(), b'keep')
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            target = tarfile.TarInfo('target.txt')
            target.size = 4
            tar.addfile(target, io.BytesIO(b'code'))
            link = tarfile.TarInfo('link.txt')
            link.type = tarfile.SYMTYPE
            link.linkname = 'target.txt'
            tar.addfile(link)
        with tempfile.TemporaryDirectory() as temp, patch.object(h, 'run', return_value=archive.getvalue()), \
                patch.object(h, 'container', return_value={'Image': 'fake-image'}), \
                patch.object(h, '__file__', str(REMOTE), create=True):
            h.prepare(Path(temp), SHA)
            self.assertEqual((Path(temp) / 'src/link.txt').read_bytes(), b'code')
            self.assertEqual(h.tree(Path(temp).resolve() / 'src')['link.txt'], ('symlink', 'target.txt'))

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

            legacy_image_id = (
                'sha256:' +
                ('1' * 64)
            )

            legacy_metadata = (
                'release_sha=' + SHA + '\n'
                'image_tag=ichiyon-robot-app:' + SHA + '\n'
                'image_id=' + legacy_image_id + '\n'
                'source=' + str(src) + '\n'
                'compose_base=' +
                str(src / 'docker-compose.yml') + '\n'
                'compose_overlay=' +
                str(release / 'compose.immutable.yml') + '\n'
                'shared_root=' + str(h.SHARED) + '\n'
                'database_volume=' + h.VOLUME + '\n'
                'network=' + h.NETWORK + '\n'
            )

            image_metadata = release / 'immutable-image.txt'

            image_metadata.write_text(
                legacy_metadata,
                encoding='utf-8',
            )

            with patch.object(h, 'ROOT', root):
                metadata = h.immutable_image_metadata(
                    release
                )

                self.assertEqual(
                    metadata['format'],
                    'phase3b',
                )

                self.assertEqual(
                    metadata['image_id'],
                    legacy_image_id,
                )

                self.assertEqual(
                    h.release(release),
                    SHA,
                )

                with patch.object(
                    h,
                    'image_check',
                    return_value=legacy_image_id,
                ) as image_check:
                    self.assertEqual(
                        h.previous_image_check(release),
                        legacy_image_id,
                    )

                    image_check.assert_called_once_with(
                        SHA,
                        require_revision=False,
                    )

                with patch.object(
                    h,
                    'image_check',
                    return_value='sha256:' + ('2' * 64),
                ), self.assertRaises(AssertionError):
                    h.previous_image_check(release)

                bad = legacy_metadata.replace(
                    'release_sha=' + SHA,
                    'release_sha=' + ('b' * 40),
                )

                image_metadata.write_text(
                    bad,
                    encoding='utf-8',
                )

                with self.assertRaises(AssertionError):
                    h.release(release)

                image_metadata.write_text(
                    legacy_metadata +
                    'unexpected=value\n',
                    encoding='utf-8',
                )

                with self.assertRaises(AssertionError):
                    h.release(release)

                image_metadata.write_text(
                    legacy_metadata +
                    'network=' + h.NETWORK + '\n',
                    encoding='utf-8',
                )

                with self.assertRaises(AssertionError):
                    h.release(release)

                image_metadata.write_text(
                    legacy_metadata,
                    encoding='utf-8',
                )

                # Legacy metadata must not be accepted for a new-format
                # release that has src/REVISION.
                (src / 'REVISION').write_text(
                    SHA + '\n',
                    encoding='utf-8',
                )

                with self.assertRaises(AssertionError):
                    h.release(release)

                # Current-format release metadata remains strict.
                image_metadata.write_text(
                    'ichiyon-robot-app:' +
                    SHA +
                    '\n',
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

        legacy = copy.deepcopy(config)
        legacy['volumes']['postgres_data']['external'] = False
        legacy['networks']['default']['external'] = False

        with patch.object(
            h,
            'release',
            return_value=SHA,
        ), patch.object(
            h,
            'compose',
            return_value=json.dumps(legacy),
        ), patch.object(
            h,
            'immutable_image_metadata',
            return_value={'format': 'phase3b'},
        ):
            h.contract(
                Path('/unused'),
                previous=True,
            )

        # The same non-external contract is never accepted for a new target.
        with patch.object(
            h,
            'release',
            return_value=SHA,
        ), patch.object(
            h,
            'compose',
            return_value=json.dumps(legacy),
        ):
            with self.assertRaises(AssertionError):
                h.contract(Path('/unused'))

        # A modern previous release remains under the modern strict contract.
        with patch.object(
            h,
            'release',
            return_value=SHA,
        ), patch.object(
            h,
            'compose',
            return_value=json.dumps(legacy),
        ), patch.object(
            h,
            'immutable_image_metadata',
            return_value={'format': 'current'},
        ):
            with self.assertRaises(AssertionError):
                h.contract(
                    Path('/unused'),
                    previous=True,
                )

        # Legacy compatibility never relaxes the fixed resource identities.
        for section, key in (
            ('volumes', 'postgres_data'),
            ('networks', 'default'),
        ):
            bad_legacy = copy.deepcopy(legacy)
            bad_legacy[section][key]['name'] = 'wrong-resource'

            with patch.object(
                h,
                'release',
                return_value=SHA,
            ), patch.object(
                h,
                'compose',
                return_value=json.dumps(bad_legacy),
            ), patch.object(
                h,
                'immutable_image_metadata',
                return_value={'format': 'phase3b'},
            ):
                with self.assertRaises(AssertionError):
                    h.contract(
                        Path('/unused'),
                        previous=True,
                    )

        self.assertIn(
            "elif mode == 'previous-contract':",
            HELPER,
        )

        self.assertEqual(
            SCRIPT.count(
                'python3 -I "$helper" previous-contract "$previous"'
            ),
            2,
        )

        self.assertIn(
            'previous=rollback',
            HELPER,
        )

        for field, value in [('image', 'latest'), ('build', {'context': '.'}), ('build', None), ('pull_policy', 'always'),
                             ('volumes', mounts + [dict(source='/', target='/app', type='bind')]),
                             ('networks', {'other': {}})]:
            bad = copy.deepcopy(config)
            bad['services']['admin'][field] = value
            with patch.object(h, 'release', return_value=SHA), patch.object(h, 'compose', return_value=json.dumps(bad)):
                with self.subTest(field=field), self.assertRaises(AssertionError):
                    h.contract(Path('/unused'))

    def test_archive_rejects_escapes(self):
        h = self.h
        for name, kind in [('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE),
                           ('link', tarfile.SYMTYPE), ('hardlink', tarfile.LNKTYPE)]:
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w') as tar:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = '/escape' if kind != tarfile.REGTYPE else ''
                tar.addfile(member, io.BytesIO(b''))
            with tempfile.TemporaryDirectory() as temp, patch.object(h, 'run', return_value=archive.getvalue()):
                with self.subTest(name=name), self.assertRaises((AssertionError, tarfile.FilterError)):
                    h.prepare(Path(temp), SHA)

    def test_image_revision_proof(self):
        h = self.h
        image = {'Id': 'sha256:fake', 'Config': {'Labels': {'org.opencontainers.image.revision': SHA}}}
        for proof in (b'IMAGE_REVISION_VERIFIED\n', b'wrong'):
            with patch.object(h, 'run', side_effect=[json.dumps([image]).encode(), proof]) as run:
                if proof == b'wrong':
                    with self.assertRaises(AssertionError):
                        h.image_check(SHA)
                else:
                    self.assertEqual(h.image_check(SHA), 'sha256:fake')
                    self.assertIn('none', run.call_args.args[0])
                    self.assertIn('--read-only', run.call_args.args[0])
                    self.assertEqual(
                        run.call_args.args[0][-1],
                        '1',
                    )
        image['Config']['Labels']['org.opencontainers.image.revision'] = 'b' * 40
        with patch.object(h, 'run', return_value=json.dumps([image]).encode()), self.assertRaises(AssertionError):
            h.image_check(SHA)

    def test_legacy_previous_image_revision_policy(self):
        h = self.h

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            release = root / SHA
            src = release / 'src'
            src.mkdir(parents=True)

            (release / 'immutable-image.txt').write_text(
                'ichiyon-robot-app:' + SHA + '\n',
                encoding='utf-8',
            )

            with patch.object(
                h,
                'release',
                return_value=SHA,
            ), patch.object(
                h,
                'image_check',
                return_value='image-id',
            ) as check:
                self.assertEqual(
                    h.previous_image_check(release),
                    'image-id',
                )

                check.assert_called_once_with(
                    SHA,
                    require_revision=False,
                )

                check.reset_mock()

                (src / 'REVISION').write_text(
                    SHA + '\n',
                    encoding='utf-8',
                )

                self.assertEqual(
                    h.previous_image_check(release),
                    'image-id',
                )

                check.assert_called_once_with(
                    SHA,
                    require_revision=True,
                )

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

    def test_rollback_health_uses_previous_image_check(self):
        h = self.h

        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp)

            mounts = [
                dict(
                    Source=s,
                    Destination=t,
                    RW=not r,
                    Type=k,
                )
                for s, t, r, k in h.expected_mounts()
            ]

            containers = {
                name: {
                    'Id': name,
                    'Image': 'image-id',
                    'Config': {
                        'Image':
                        'ichiyon-robot-app:' + SHA,
                    },
                    'State': {
                        'Running': True,
                        'StartedAt': 'fixed',
                    },
                    'RestartCount': 0,
                    'Mounts': copy.deepcopy(mounts),
                    'NetworkSettings': {
                        'Networks': {
                            h.NETWORK: {},
                        },
                    },
                }
                for name in h.APPS
            }

            def fake_run(args, **kwargs):
                if args[0] == 'curl':
                    return b'200'

                if args[:2] == ['docker', 'logs']:
                    return b'Logged in as '

                raise AssertionError(
                    'unexpected fake operation'
                )

            with patch.object(
                h,
                'release',
                return_value=SHA,
            ), patch.object(
                h,
                'contract',
            ) as contract, patch.object(
                h,
                'previous_image_check',
                return_value='image-id',
            ) as previous_image_check, patch.object(
                h,
                'image_check',
            ) as image_check, patch.object(
                h,
                'container',
                side_effect=containers.__getitem__,
            ), patch.object(
                h,
                'infra',
                return_value=['fixed'],
            ), patch.object(
                h,
                'run',
                side_effect=fake_run,
            ), patch.object(
                h.time,
                'sleep',
            ), patch.object(
                h.subprocess,
                'run',
                return_value=types.SimpleNamespace(
                    stderr=b'',
                ),
            ):
                h.health(
                    release,
                    ['fixed'],
                    migrations=False,
                    previous=True,
                )

                contract.assert_called_once_with(
                    release,
                    previous=True,
                )

                previous_image_check.assert_called_once_with(
                    release
                )

                image_check.assert_not_called()

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
                         "status == b'200'", "b'Logged in as '", 'IMAGE_REVISION_VERIFIED',
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

    def test_repository_paths_and_ci(self):
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
