"""Offline tunnel lifecycle and release regressions; all network/SSH are mocked."""

import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai_task_control_tunnel import ensure_control_plane_access, endpoint_ready
from ai_task_deploy_config import DeployConfig, DeploymentError
from ai_task_production_release import main


BASE = "http://127.0.0.1:18080"
SHA = "a" * 40


class TunnelChecks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        files = [root / name for name in ("ssh", "key", "known hosts")]
        for path in files:
            path.touch()
        self.config = DeployConfig(files[0], "example.invalid", "ubuntu", *files[1:], ())
        self.process = Mock()
        self.process.poll.return_value = None
        self.popen = Mock(return_value=self.process)
        self.request = Mock(side_effect=[False, True])

    def access(self, base=BASE, **kwargs):
        return ensure_control_plane_access(base, self.config, popen=self.popen,
                                           requester=self.request, **kwargs)

    def test_existing_endpoint_is_borrowed_never_stopped(self):
        self.request.side_effect = [True]
        with self.access():
            pass
        self.popen.assert_not_called()
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()

    def test_existing_endpoint_is_not_stopped_on_release_failure(self):
        self.request.side_effect = [True]
        with self.assertRaises(KeyboardInterrupt):
            with self.access():
                raise KeyboardInterrupt()
        self.popen.assert_not_called()
        self.process.terminate.assert_not_called()

    def test_start_ready_release_cleanup_and_secure_argv(self):
        with self.access():
            self.process.terminate.assert_not_called()
            self.assertEqual(self.request.call_count, 2)
        self.process.terminate.assert_called_once()
        self.process.wait.assert_called_once_with(timeout=5)
        args, kwargs = self.popen.call_args
        argv = args[0]
        self.assertEqual(argv[:5], [str(self.config.ssh_path), "-F", "none", "-N", "-T"])
        self.assertEqual(argv[-3:], ["-L", "127.0.0.1:18080:127.0.0.1:8000", "ubuntu@example.invalid"])
        for option in ("BatchMode=yes", "IdentitiesOnly=yes", "ExitOnForwardFailure=yes",
                       "StrictHostKeyChecking=yes", "ConnectTimeout=15", "ServerAliveInterval=15",
                       "ServerAliveCountMax=3", "ForwardAgent=no", "PermitLocalCommand=no",
                       "ControlMaster=no", "ControlPath=none", "ForkAfterAuthentication=no",
                       'UserKnownHostsFile="' + self.config.known_hosts_path.as_posix() + '"'):
            self.assertIn(option, argv)
        self.assertNotIn("ClearAllForwardings=yes", argv)
        self.assertIs(kwargs["shell"], False)
        for name in ("stdin", "stdout", "stderr"):
            self.assertEqual(kwargs[name], subprocess.DEVNULL)

    def test_localhost_and_other_port(self):
        with self.access("http://localhost:18765"):
            pass
        self.assertIn("127.0.0.1:18765:127.0.0.1:8000", self.popen.call_args.args[0])

    def test_remote_https_and_no_explicit_port_unchanged(self):
        for url in ("https://example.invalid", "https://localhost:18080", "http://example.invalid:18080",
                    "http://127.0.0.2:18080", "http://localhost"):
            with self.access(url):
                pass
        self.request.assert_not_called()
        self.popen.assert_not_called()

    def test_early_exit_and_exit_during_probe(self):
        for polls in ([255, 255], [None, 255, 255]):
            self.process.poll.side_effect = polls
            self.request.side_effect = [False, True]
            with self.assertRaisesRegex(DeploymentError, "tunnel exited"):
                with self.access():
                    self.fail("release started")
        self.process.kill.assert_not_called()

    def test_timeout_cleans_up(self):
        self.request.side_effect = None
        self.request.return_value = False
        ticks = iter([0, 1, 29, 30])
        with self.assertRaisesRegex(DeploymentError, "timed out"):
            with self.access(clock=lambda: next(ticks), sleep=Mock()):
                self.fail("release started")
        self.process.terminate.assert_called_once()

    def test_release_exception_and_keyboard_interrupt_cleanup(self):
        for error in (RuntimeError("app failed"), ValueError("Minecraft failed"), KeyboardInterrupt()):
            self.request.side_effect = [False, True]
            self.process.reset_mock()
            with self.assertRaises(type(error)):
                with self.access():
                    raise error
            self.process.terminate.assert_called_once()

    def test_probe_exception_cleanup(self):
        self.request.side_effect = [False, RuntimeError("HTTP probe failed")]
        with self.assertRaisesRegex(RuntimeError, "HTTP probe failed"):
            with self.access():
                self.fail("release started")
        self.process.terminate.assert_called_once()

    def test_cleanup_escalates_only_owned_process(self):
        self.process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), 0]
        with self.access():
            pass
        self.process.kill.assert_called_once()
        self.assertEqual(self.process.wait.call_count, 2)

    def test_spawn_failure_redacts_secrets(self):
        self.popen.side_effect = OSError("password=hidden-value Bearer fixture-token "
                                        "-----BEGIN PRIVATE KEY-----fixture-key-----END PRIVATE KEY-----")
        with self.assertRaisesRegex(DeploymentError, "could not start") as caught:
            with self.access():
                self.fail("release started")
        for secret in ("hidden-value", "fixture-token", "fixture-key"):
            self.assertNotIn(secret, str(caught.exception))
        self.process.terminate.assert_not_called()

    def test_probe_requires_http_200_no_redirect_or_credentials(self):
        with patch("ai_task_control_tunnel.http.client.HTTPConnection") as factory:
            connection = factory.return_value
            for status in (200, 301, 401, 404, 503):
                connection.getresponse.return_value.status = status
                self.assertEqual(endpoint_ready(BASE, timeout=2), status == 200)
            connection.request.assert_called_with("GET", "/openapi.json")
            factory.assert_called_with("127.0.0.1", 18080, timeout=2)
            self.assertEqual(connection.close.call_count, 5)
            connection.request.side_effect = ConnectionRefusedError()
            self.assertFalse(endpoint_ready(BASE, timeout=2))

    def test_main_wraps_both_deployments_and_preserves_sha_and_errors(self):
        # Invoke main only with synthetic env and fake transports/adapters.
        env = {"AI_TASK_RUNNER_API_BASE_URL": BASE, "AI_TASK_RUNNER_API_TOKEN": "fixture-token",
               "AI_TASK_RUNNER_ID": "offline", "AI_TASK_RUNNER_REPO_ROOT": str(self.config.ssh_path.parent),
               "AI_TASK_RUNNER_WORKTREE_ROOT": str(self.config.ssh_path.parent)}
        from ai_task_deploy import DeploymentResult as AppResult
        from ai_task_minecraft_deploy import DeploymentResult
        from ai_task_api_client import RunnerAPIError
        for failure in (None, "app", "minecraft", "api", "interrupt", "tunnel"):
            self.request.side_effect = [False, True]
            self.process.reset_mock()
            self.process.poll.return_value = 255 if failure == "tunnel" else None
            output = io.StringIO()
            with patch.dict(os.environ, env, clear=True), patch("sys.argv", ["release", "--merge-sha", SHA]), \
                    patch("ai_task_production_release.DeployConfig.from_environment", return_value=self.config), \
                    patch("ai_task_control_tunnel.subprocess.Popen", self.popen), \
                    patch("ai_task_control_tunnel.endpoint_ready", self.request), \
                    patch("ai_task_production_release.ProductionDeployAdapter") as apps, \
                    patch("ai_task_production_release.ManagedMinecraftDeployAdapter") as minecraft, \
                    patch("ai_task_production_release.RunnerAPIClient"), patch("sys.stdout", output):
                apps.return_value.deploy.return_value = AppResult(SHA, "app verified")
                minecraft.return_value.deploy.return_value = DeploymentResult(SHA, "Minecraft verified", frozenset({"minecraft"}))
                if failure == "app":
                    apps.return_value.deploy.side_effect = DeploymentError("LOCK_BUSY fixture-token")
                elif failure == "minecraft":
                    minecraft.return_value.deploy.side_effect = DeploymentError("Minecraft failed")
                elif failure == "api":
                    minecraft.return_value.deploy.side_effect = RunnerAPIError("API failed fixture-token")
                elif failure == "interrupt":
                    apps.return_value.deploy.side_effect = KeyboardInterrupt()
                if failure == "interrupt":
                    with self.assertRaises(KeyboardInterrupt):
                        main()
                else:
                    self.assertEqual(main(), int(failure is not None))
                if failure == "tunnel":
                    apps.assert_not_called()
                else:
                    apps.return_value.deploy.assert_called_once_with(SHA, stop_event=None)
                if failure not in ("app", "interrupt", "tunnel"):
                    minecraft.return_value.deploy.assert_called_once_with(SHA, stop_event=None)
                else:
                    minecraft.return_value.deploy.assert_not_called()
                self.assertNotIn("fixture-token", output.getvalue())
            if failure == "tunnel":
                self.process.terminate.assert_not_called()
            else:
                self.process.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
