"""Offline cross-platform regressions; never invokes GitHub, Codex or SSH."""

import ast
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

from ai_task_codex import CodexAdapter
from ai_task_process import (
    ProcessTerminationError, managed_process_options, terminate_process_tree,
    communicate_bounded,
)
from ai_task_publish import GitPublisher, PublishSafetyError
from ai_task_runner_config import RunnerConfig
from ai_task_runner import LocalRunner
from check_ai_task_code_review import run_with, approve_payload

ROOT = Path(__file__).resolve().parents[1]


class LinuxChecks(unittest.TestCase):
    def test_config_without_gcm_and_windows_requirement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('source', 'tasks', 'codex-home'):
                (root / name).mkdir()
            for name in ('codex', 'git', 'gh', 'git-credential-manager.exe'):
                (root / name).touch()
            env = {
                'AI_TASK_RUNNER_API_BASE_URL': 'https://runner.example',
                'AI_TASK_RUNNER_API_TOKEN': 'offline-placeholder',
                'AI_TASK_RUNNER_ID': 'linux-test',
                'AI_TASK_RUNNER_REPO_ROOT': str(root / 'source'),
                'AI_TASK_RUNNER_WORKTREE_ROOT': str(root / 'tasks'),
                'AI_TASK_RUNNER_CODEX_HOME': str(root / 'codex-home'),
                **{'AI_TASK_RUNNER_' + name.upper() + '_PATH': str(root / name)
                   for name in ('codex', 'git', 'gh')},
            }
            with patch.dict(os.environ, env, clear=True), patch('sys.platform', 'linux'):
                config = RunnerConfig.from_environment()
                self.assertIsNone(config.gcm_path)
                self.assertEqual(config.gh_path, root / 'gh')
                with patch('ai_task_runner.GitPublisher') as publisher:
                    LocalRunner(config, client=object(), git=object(), codex=object(), github=object())
                    publisher.assert_called_once_with(config.git_path, gcm_path=None, gh_path=config.gh_path)
                with patch.dict(os.environ, {'AI_TASK_RUNNER_GH_PATH': str(root / 'absent')}):
                    with self.assertRaises(ValueError):
                        RunnerConfig.from_environment()
                with patch.dict(os.environ, {'AI_TASK_RUNNER_WORKTREE_ROOT': str(root / 'source')}):
                    with self.assertRaises(ValueError):
                        RunnerConfig.from_environment()
                with patch('sys.platform', 'win32'):
                    with self.assertRaises(ValueError):
                        RunnerConfig.from_environment()
                    with patch.dict(os.environ, {'AI_TASK_RUNNER_GCM_PATH': str(root / 'git-credential-manager.exe')}):
                        self.assertIsNotNone(RunnerConfig.from_environment().gcm_path)

    def test_linux_network_helper_and_sanitization(self):
        calls = []
        def fake(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0, stdout='core.repositoryformatversion\n', stderr='')
        exe = Path(sys.executable).resolve()
        publisher = GitPublisher(exe, gh_path=exe, runner=fake)
        publisher.gh_path = PurePosixPath('/trusted/gh')
        trusted_home = str((ROOT / '.ai-task-linux-test-home').resolve())
        with patch('sys.platform', 'linux'), patch.dict(os.environ, {
            'HOME': trusted_home,
            'GH_TOKEN': 'fake', 'GITHUB_TOKEN': 'fake', 'GH_CONFIG_DIR': '/untrusted',
            'GIT_CONFIG_COUNT': '99', 'GIT_CONFIG_VALUE_9': 'unsafe',
            'GIT_ASKPASS': '/untrusted', 'SSH_AUTH_SOCK': '/untrusted',
        }, clear=True):
            env = publisher._network_environment(ROOT)
            pairs = [(env[f'GIT_CONFIG_KEY_{i}'], env[f'GIT_CONFIG_VALUE_{i}'])
                     for i in range(int(env['GIT_CONFIG_COUNT']))]
            self.assertEqual(pairs[:3], [('credential.helper', ''),
                ('credential.https://github.com.helper', ''),
                ('credential.https://github.com.helper', '/trusted/gh auth git-credential')])
            for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GIT_ASKPASS', 'SSH_AUTH_SOCK', 'GIT_CONFIG_VALUE_9'):
                self.assertNotIn(key, env)
            self.assertEqual(env['HOME'], os.devnull)
            self.assertEqual(env['XDG_CONFIG_HOME'], os.devnull)
            self.assertEqual(env['GH_CONFIG_DIR'], str(Path(trusted_home) / '.config' / 'gh'))
            self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
            self.assertEqual(env['GIT_CONFIG_SYSTEM'], os.devnull)
            self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
            self.assertFalse(calls[0][1]['shell'])
            args = ('ls-remote', '--heads', 'https://github.com/U-KID-AI/ichiyon-robot.git',
                    'refs/heads/ai/task/00000000-0000-0000-0000-000000000001')
            publisher._run(args, cwd=ROOT, environment=env)
            self.assertEqual(calls[-1][0], [str(exe),
                *[arg for key, value in publisher._network_config() for arg in ('-c', key + '=' + value)],
                *args])
            for bad in (('push', 'origin', 'main'), ('ls-remote', '--heads', 'https://evil.invalid', args[-1]),
                        (*args[:-1], 'refs/heads/main')):
                with self.assertRaises(PublishSafetyError):
                    publisher._run(bad, cwd=ROOT, environment=env)
            with self.assertRaises(PublishSafetyError):
                GitPublisher(exe, runner=fake)._network_environment(ROOT)
            publisher.gh_path = PurePosixPath('/trusted tools/gh')
            self.assertEqual(publisher._network_environment(ROOT)['GIT_CONFIG_VALUE_2'],
                             '/trusted\\ tools/gh auth git-credential')
            for path in ('/tmp/gh;evil', '/tmp/$(evil)', '/tmp/gh\n', '/tmp/gh"', '/tmp/gh`evil`'):
                publisher.gh_path = PurePosixPath(path)
                with self.assertRaises(PublishSafetyError):
                    publisher._network_environment(ROOT)
            publisher.gh_path = PurePosixPath('/trusted/gh')
            for key in ('credential.helper', 'include.path', 'includeif.x.path',
                        'url.x.insteadof', 'http.proxy', 'core.askpass', 'extensions.worktreeconfig',
                        'remote.https://github.com/U-KID-AI/ichiyon-robot.git.url'):
                with patch.object(publisher, '_run', return_value=SimpleNamespace(returncode=0, stdout=key+'\n')):
                    with self.assertRaises(PublishSafetyError):
                        publisher._network_environment(ROOT)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux Git helper integration')
    def test_git_invokes_only_fixed_helper_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / 'repo'
            repo.mkdir()
            (root / '.gitconfig').write_text('[credential]\n helper = !exit 88\n[offline]\n probe = unsafe\n')
            helper = root / 'trusted tools' / 'gh'
            helper.parent.mkdir()
            record = root / 'argv.json'
            helper.write_text('#!' + sys.executable + '\nimport json, sys\n'
                + 'from pathlib import Path\n'
                + 'Path(' + repr(str(record)) + ').write_text(json.dumps(sys.argv[1:]))\n')
            helper.chmod(0o700)
            with patch.dict(os.environ, {'HOME': str(root)}, clear=True):
                publisher = GitPublisher(Path('/usr/bin/git'), gh_path=helper)
                base = publisher._base_environment()
                base.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
                subprocess.run(['/usr/bin/git', 'init', '-q', str(repo)], env=base,
                               check=True, shell=False, timeout=5, capture_output=True)
                env = publisher._network_environment(repo)
                config_args = [arg for key, value in publisher._network_config()
                               for arg in ('-c', key + '=' + value)]
                probe = subprocess.run(['/usr/bin/git', 'config', '--get', 'offline.probe'],
                    cwd=repo, env=env, capture_output=True, timeout=5)
                self.assertEqual(probe.returncode, 1)
                subprocess.run(['/usr/bin/git', *config_args, 'credential', 'reject'], cwd=repo, env=env,
                    input='protocol=https\nhost=github.com\n\n', text=True,
                    check=True, shell=False, timeout=5, capture_output=True)
            self.assertEqual(json.loads(record.read_text()), ['auth', 'git-credential', 'erase'])

    def test_codex_argv_both_platforms(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for platform in ('linux', 'win32'):
                calls = []
                def fake(argv, **kwargs):
                    calls.append((argv, kwargs))
                    return SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(),
                        returncode=0, poll=lambda: 0, wait=lambda **kw: 0)
                with patch('sys.platform', platform):
                    CodexAdapter(Path(sys.executable), popen=fake).run(root, root / 'out', 'offline', timeout=1)
                    _, review_calls, _ = run_with(json.dumps(approve_payload()))
                for argv, kwargs, network_enabled in (
                    (calls[0][0], calls[0][1], True),
                    (review_calls[0]['argv'], review_calls[0]['kwargs'], False),
                ):
                    windows = [arg for arg in argv if arg.startswith('windows.')]
                    self.assertEqual(windows, ['windows.sandbox="elevated"',
                        'windows.allowed_sandbox_implementations=["elevated"]'] if platform == 'win32' else [])
                    self.assertIn(
                        'sandbox_workspace_write.network_access=' + ('true' if network_enabled else 'false'),
                        argv,
                    )
                    self.assertFalse(kwargs['shell'])
                    if os.name == 'posix':
                        self.assertTrue(kwargs['start_new_session'])

    def test_every_shared_launch_has_session_options(self):
        count = 0
        for name in ('codex', 'code_review', 'publish', 'github', 'review_merge', 'auto_merge', 'deploy'):
            tree = ast.parse((ROOT / 'scripts' / f'ai_task_{name}.py').read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('Popen', '_popen'):
                    count += 1
                    self.assertTrue(any(k.arg is None and isinstance(k.value, ast.Call)
                        and isinstance(k.value.func, ast.Name) and k.value.func.id == 'managed_process_options'
                        for k in node.keywords), name)
        self.assertEqual(count, 7)
        with patch('ai_task_process.os.name', 'nt'):
            self.assertEqual(managed_process_options(), {})

    @unittest.skipUnless(os.name == 'posix', 'POSIX signals required')
    def test_posix_escalation_and_verification(self):
        process = SimpleNamespace(pid=43210, poll=lambda: 0)
        signals = []
        def killpg(pid, sig):
            self.assertEqual(pid, process.pid)
            signals.append(sig)
            if sig == 0 and signal.SIGKILL in signals:
                raise ProcessLookupError()
        with patch('os.getpgid', return_value=process.pid), patch('os.getpgrp', return_value=123), \
                patch('os.killpg', side_effect=killpg), patch('time.monotonic', side_effect=range(0, 100, 6)):
            terminate_process_tree(process)
        self.assertIn(signal.SIGTERM, signals)
        self.assertIn(signal.SIGKILL, signals)
        for failure in (PermissionError(), None):
            with patch('os.getpgid', return_value=process.pid), patch('os.getpgrp', return_value=123), \
                    patch('os.killpg', side_effect=failure), patch('time.monotonic', side_effect=range(0, 100, 6)):
                with self.assertRaises(ProcessTerminationError):
                    terminate_process_tree(process)
        with patch('os.getpgid', return_value=123), patch('os.killpg') as kill:
            with self.assertRaises(ProcessTerminationError):
                terminate_process_tree(process)
            kill.assert_not_called()
        with patch('os.getpgid', side_effect=ProcessLookupError()), patch('os.killpg', side_effect=ProcessLookupError()):
            terminate_process_tree(process)

    @unittest.skipUnless(os.name == 'posix', 'POSIX signals required')
    def test_real_group_with_grandchild(self):
        # Trusted fixture only. Parent reaps the child, so zombie lifetime does
        # not depend on the CI host's init. A pipe provides a readiness barrier.
        code = '''import os, signal, subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'])
def stop(sig, frame):
    child.wait(timeout=3)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
print('ready', flush=True)
signal.pause()
'''
        for cancelled in (False, True):
            self._real_group_cleanup(code, cancelled)

    def _real_group_cleanup(self, code, cancelled):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / 'process_fixture.py'
            fixture.write_text(code)
            process = subprocess.Popen([sys.executable, str(fixture)], shell=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **managed_process_options())
            try:
                import select
                self.assertTrue(select.select([process.stdout], [], [], 5)[0])
                self.assertEqual(process.stdout.readline(), b'ready\n')
                stop = threading.Event()
                if cancelled:
                    stop.set()
                result = communicate_bounded(process, input_text=None, timeout=0.1,
                                             max_output_bytes=100, stop_event=stop)
                self.assertEqual(result.timed_out, not cancelled)
                self.assertEqual(result.stopped, cancelled)
                self.assertIsNotNone(process.poll())
                with self.assertRaises(ProcessLookupError):
                    os.killpg(process.pid, 0)
            finally:
                if process.poll() is None:
                    terminate_process_tree(process)
                process.stdout.close()
                process.stderr.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
