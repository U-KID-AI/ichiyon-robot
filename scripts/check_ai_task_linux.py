"""Offline desktop-style Codex and ordinary Git checks; no remote operations."""

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_task_codex import CodexAdapter
from ai_task_git import GitAdapter
from ai_task_publish import GitPublisher
from ai_task_runner_config import RunnerConfig


def desktop_environment(root):
    """Synthetic values only: never inspect or print the user's credentials."""
    return {
        "HOME": str(root),
        "XDG_CONFIG_HOME": str(root / "config"),
        "CODEX_HOME": str(root / "codex"),
        "CODEX_SQLITE_HOME": str(root / "codex-state"),
        "GH_CONFIG_DIR": str(root / "gh"),
        "GH_TOKEN": "offline-gh-placeholder",
        "GITHUB_TOKEN": "offline-github-placeholder",
        "AI_TASK_RUNNER_API_TOKEN": "offline-api-placeholder",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "offline.environment",
        "GIT_CONFIG_VALUE_0": "inherited-value",
        "GIT_CONFIG_GLOBAL": str(root / "gitconfig"),
        "GIT_CONFIG_SYSTEM": str(root / "system-gitconfig"),
        "GIT_ASKPASS": str(root / "askpass"),
        "SSH_AUTH_SOCK": str(root / "agent.sock"),
        "HTTPS_PROXY": "http://offline.invalid:8080",
        "PROJECT_SETTING": "normal-desktop-setting",
    }


class LinuxChecks(unittest.TestCase):
    def test_config_accepts_desktop_paths_and_optional_credential_manager(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            tasks = root / "tasks"
            tools = repo / "tools"
            home = repo / ".codex"
            for path in (repo, tasks, tools, home):
                path.mkdir()
            for name in ("codex", "git", "gh"):
                (tools / name).touch()
            env = {
                "AI_TASK_RUNNER_API_BASE_URL": "https://runner.example",
                "AI_TASK_RUNNER_API_TOKEN": "offline-placeholder",
                "AI_TASK_RUNNER_ID": "linux-test",
                "AI_TASK_RUNNER_REPO_ROOT": str(repo),
                "AI_TASK_RUNNER_WORKTREE_ROOT": str(tasks),
                "CODEX_HOME": str(home),
                **{"AI_TASK_RUNNER_" + name.upper() + "_PATH": str(tools / name)
                   for name in ("codex", "git", "gh")},
            }
            for platform in ("linux", "win32"):
                with self.subTest(platform=platform), patch.dict(os.environ, env, clear=True), \
                        patch("sys.platform", platform):
                    config = RunnerConfig.from_environment()
                    self.assertIsNone(config.gcm_path)
                    self.assertEqual(config.repo_root, repo.resolve())
                    self.assertEqual(config.worktree_root, tasks.resolve())
                    self.assertEqual(config.codex_home, home.resolve())
                    for name in ("codex", "git", "gh"):
                        self.assertEqual(getattr(config, name + "_path"), (tools / name).resolve())
                    with patch.dict(os.environ, {"AI_TASK_RUNNER_CODEX_HOME": str(tasks)}):
                        self.assertEqual(RunnerConfig.from_environment().codex_home, tasks.resolve())
                    for name in ("git", "gh"):
                        key = "AI_TASK_RUNNER_" + name.upper() + "_PATH"
                        with self.subTest(required=key), patch.dict(os.environ):
                            os.environ.pop(key)
                            with self.assertRaisesRegex(ValueError, key + " is required"):
                                RunnerConfig.from_environment()
                    for value in ("", "   "):
                        with self.subTest(gcm=value), patch.dict(os.environ, {"AI_TASK_RUNNER_GCM_PATH": value}):
                            self.assertIsNone(RunnerConfig.from_environment().gcm_path)
                    gcm = tools / "legacy-credential-manager"
                    gcm.touch()
                    with patch.dict(os.environ, {"AI_TASK_RUNNER_GCM_PATH": " " + str(gcm) + " "}):
                        self.assertEqual(RunnerConfig.from_environment().gcm_path,
                                         gcm.resolve() if platform == "win32" else None)
                    for value in ("relative-gcm", str(tools / "missing-gcm"), str(tools)):
                        with self.subTest(gcm=value), patch.dict(os.environ, {"AI_TASK_RUNNER_GCM_PATH": value}):
                            if platform == "win32":
                                with self.assertRaisesRegex(ValueError, "Git Credential Manager path"):
                                    RunnerConfig.from_environment()
                            else:
                                self.assertIsNone(RunnerConfig.from_environment().gcm_path)

    def test_codex_full_access_and_environment_on_both_platforms(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = desktop_environment(root)
            for platform in ("linux", "win32"):
                with self.subTest(platform=platform):
                    calls = []

                    def fake(argv, **kwargs):
                        calls.append((argv, kwargs))
                        stdin = SimpleNamespace(data=b"", closed=False)
                        stdin.write = lambda value: setattr(stdin, "data", stdin.data + value)
                        stdin.close = lambda: setattr(stdin, "closed", True)
                        return SimpleNamespace(
                            stdin=stdin, stdout=io.BytesIO(), stderr=io.BytesIO(),
                            returncode=0, poll=lambda: 0, wait=lambda **kw: 0,
                        )

                    with patch("sys.platform", platform), patch.dict(os.environ, env, clear=True):
                        adapter = CodexAdapter(Path(sys.executable), popen=fake)
                        result = adapter.run(root, root / "out", "offline prompt", timeout=1)
                        self.assertEqual(dict(os.environ), env)
                    argv, kwargs = calls[0]
                    self.assertEqual(argv[:2], [str(Path(sys.executable).resolve()), "exec"])
                    self.assertEqual(argv[argv.index("--sandbox") + 1], "danger-full-access")
                    self.assertIn('approval_policy="never"', argv)
                    self.assertIn('shell_environment_policy.inherit="all"', argv)
                    self.assertIn("shell_environment_policy.ignore_default_excludes=true", argv)
                    self.assertFalse(any(value in argv for value in (
                        "--ignore-user-config", "--ephemeral", "workspace-write", "read-only",
                        "allow_login_shell=false", "allow_managed_hooks_only=true",
                    )))
                    self.assertFalse(any(value.startswith(("windows.", "sandbox_workspace_write.")) for value in argv))
                    self.assertEqual(argv[argv.index("-C") + 1], str(root.resolve()))
                    self.assertEqual(argv[argv.index("-o") + 1], str((root / "out").resolve()))
                    self.assertEqual(argv[-1], "-")
                    self.assertNotIn("offline prompt", argv)
                    self.assertEqual(adapter._process.stdin.data, b"offline prompt")
                    self.assertTrue(adapter._process.stdin.closed)
                    self.assertEqual(kwargs["env"], env)
                    self.assertEqual(kwargs["cwd"], str(root.resolve()))
                    self.assertFalse(kwargs["shell"])
                    self.assertEqual(kwargs.get("start_new_session", False), os.name == "posix")
                    self.assertEqual(result.returncode, 0)

    def test_codex_home_override_preserves_other_desktop_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = desktop_environment(root)
            home = root / "selected-codex-home"
            with patch.dict(os.environ, env, clear=True):
                actual = CodexAdapter(Path(sys.executable))._environment(home)
                self.assertEqual(actual, dict(env, CODEX_HOME=str(home.resolve())))
                self.assertEqual(dict(os.environ), env)

    def test_git_and_publisher_keep_normal_argv_and_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = desktop_environment(root)
            calls = []

            def fake(argv, **kwargs):
                inherited = kwargs.get("env")
                calls.append((argv, kwargs, dict(os.environ) if inherited is None else dict(inherited)))
                return SimpleNamespace(returncode=0, stdout="inherited-value\n", stderr="")

            executable = Path(sys.executable).resolve()
            git = GitAdapter(root, executable, runner=fake)
            publisher = GitPublisher(executable, runner=fake)
            with patch.dict(os.environ, env, clear=True):
                for args in (("config", "--get", "offline.environment"), ("desktop-alias",)):
                    git._run(args, cwd=root)
                    publisher._run(args, cwd=root)
                self.assertEqual(dict(os.environ), env)
            self.assertEqual(len(calls), 4)
            for (argv, kwargs, actual_env), args in zip(calls, (
                ("config", "--get", "offline.environment"),
                ("config", "--get", "offline.environment"),
                ("desktop-alias",), ("desktop-alias",),
            )):
                self.assertEqual(argv, [str(executable), *args])
                self.assertFalse(kwargs["shell"])
                self.assertEqual(kwargs["cwd"], str(root.resolve()))
                for key, value in env.items():
                    self.assertEqual(actual_env.get(key), value, key)

    @unittest.skipUnless(sys.platform == "linux", "Linux Git configuration integration")
    def test_real_git_uses_user_config_environment_alias_and_credential_helper(self):
        executable = shutil.which("git")
        self.assertIsNotNone(executable, "Git is required for the offline Linux integration check")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            included = root / "included.gitconfig"
            helper = root / "tools with spaces" / "credential_fixture.py"
            helper.parent.mkdir()
            record = root / "helper.json"
            helper.write_text(
                "import json, os, sys\nfrom pathlib import Path\n"
                + "Path(" + repr(str(record)) + ").write_text(json.dumps({"
                + "'argv': sys.argv[1:], 'input': sys.stdin.read(), "
                + "'setting': os.environ.get('PROJECT_SETTING')}))\n",
                encoding="utf-8",
            )
            # Isolate this fixture from the user's real credentials and Git config.
            env = {
                "PATH": os.defpath,
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "offline.environment",
                "GIT_CONFIG_VALUE_0": "inherited-value",
                "PROJECT_SETTING": "normal-desktop-setting",
            }
            with patch.dict(os.environ, env, clear=True):
                def setup(*args):
                    subprocess.run([executable, *args], cwd=repo, shell=False, check=True,
                                   capture_output=True, text=True, timeout=5)

                setup("init", "-q")
                setup("config", "--file", str(root / ".gitconfig"), "include.path", str(included))
                setup("config", "--file", str(included), "offline.probe", "user-config-loaded")
                setup("config", "--file", str(included), "alias.desktop-probe", "config --get offline.probe")
                setup("config", "--file", str(included), "credential.helper",
                      shlex.join([sys.executable, str(helper)]))
                adapters = (GitAdapter(repo, Path(executable)), GitPublisher(Path(executable)))
                for adapter in adapters:
                    for args, expected in (
                        (("desktop-probe",), "user-config-loaded"),
                        (("config", "--get", "offline.environment"), "inherited-value"),
                    ):
                        with self.subTest(adapter=type(adapter).__name__, args=args):
                            result = adapter._run(args, cwd=repo)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(result.stdout.strip(), expected)
                # Credential reject invokes only our local fixture, never a network service.
                subprocess.run(
                    [executable, "credential", "reject"], cwd=repo,
                    env=adapters[1]._base_environment(),
                    input="protocol=https\nhost=offline.invalid\n\n", text=True,
                    check=True, shell=False, capture_output=True, timeout=5,
                )
            recorded = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(recorded["argv"], ["erase"])
            self.assertIn("host=offline.invalid", recorded["input"])
            self.assertEqual(recorded["setting"], "normal-desktop-setting")


if __name__ == "__main__":
    unittest.main(verbosity=2)
