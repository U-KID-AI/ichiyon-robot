"""Regression tests for normal-Git task execution and recoverable failures."""

from dataclasses import replace
import logging
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from ai_task_api_client import ClaimedTask
from ai_task_auto_merge import MergeOutcomeUnknownError
from ai_task_codex import CodexResult
from ai_task_diagnostics import redact_secrets
from ai_task_git import GitAdapter
from ai_task_publish import GitPublisher
from ai_task_runner import LeaseHeartbeat, LocalRunner, RunOutcome, test_feedback, run_idle_maintenance
from ai_task_runner_config import RunnerConfig
from ai_task_test_registry import TestResult, run_tests


class Client:
    def __init__(self, task):
        self.task, self.calls = task, []

    def claim(self):
        return self.task

    def progress(self, *args, **kwargs):
        self.calls.append(("progress", kwargs))

    def mark_testing(self, *args):
        self.calls.append(("testing", {}))

    def retry(self, *args):
        self.calls.append(("retry", {"reason": args[-1]}))

    def mark_failed(self, *args):
        self.calls.append(("failed", {"reason": args[-1]}))

    def mark_deploying(self, *args, **kwargs):
        self.calls.append(("deploying", kwargs))
        return {"task_id": str(self.task.task_id), "status": "deploying",
                "lease_expires_at": self.task.lease_expires_at}

    def mark_completed(self, *args, **kwargs):
        self.calls.append(("completed", kwargs))


class Heartbeat:
    def __init__(self, *args, **kwargs):
        self.lost = Event()
    def start(self): pass
    def stop(self): pass
    def update_lease(self, value):
        LeaseHeartbeat._parse_lease(value)
        self.lease_expires_at = value


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git_path = Path(shutil.which("git"))
        self.git_cmd(self.root, "init", "--bare", str(self.root / "remote.git"))
        self.git_cmd(self.source, "init", "-b", "main")
        self.git_cmd(self.source, "config", "user.name", "Runner test")
        self.git_cmd(self.source, "config", "user.email", "runner@example.invalid")
        self.git_cmd(self.source, "config", "core.autocrlf", "false")
        self.git_cmd(self.source, "remote", "add", "origin", str(self.root / "remote.git"))
        (self.source / "docs").mkdir()
        (self.source / "docs/remove.txt").write_text("obsolete\n", newline="\n")
        (self.source / "old.py").write_text("VALUE = 1\n", newline="\n")
        (self.source / "Dockerfile").write_text("FROM scratch\n", newline="\n")
        self.git_cmd(self.source, "add", "-A")
        self.git_cmd(self.source, "commit", "-m", "base")
        self.git_cmd(self.source, "push", "-u", "origin", "main")
        self.base = self.git_cmd(self.source, "rev-parse", "HEAD").strip()
        task_id = uuid4()
        self.task = ClaimedTask(task_id, "Implement a multi-directory change",
            f"ai/task/{task_id}", f"ai-task-{task_id}", uuid4(), "2099-01-01T00:00:00Z")
        self.config = RunnerConfig("http://localhost", "private-test-token", "test-runner",
            self.source, self.root / "tasks", Path(sys.executable), None, self.git_path,
            5, 10, gh_path=Path(sys.executable), max_attempts=4)
        self.client = Client(self.task)
        self.prompts, self.events = [], []
        self.codex_calls = 0
        self.whitespace_first = False
        self.break_stage = None
        self.failed_stages = set()
        self.deploy_failures = 0

    def git_cmd(self, cwd, *args):
        result = subprocess.run([str(self.git_path), *args], cwd=cwd,
            text=True, encoding="utf-8", capture_output=True, timeout=20)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return result.stdout

    def maybe_fail(self, stage):
        self.events.append(stage)
        if self.break_stage == stage and stage not in self.failed_stages:
            self.failed_stages.add(stage)
            raise RuntimeError(f"{stage}: src/example.py:42: actual diagnostic PASSWORD=hidden-value")

    def codex_run(self, worktree, output, prompt, **kwargs):
        self.codex_calls += 1
        self.prompts.append(prompt)
        self.maybe_fail("codex")
        for directory in ("bot", "migrations", ".github/workflows", "scripts"):
            (worktree / directory).mkdir(parents=True, exist_ok=True)
        source = "VALUE = 2  \n" if self.whitespace_first and self.codex_calls == 1 else "VALUE = 2\n"
        (worktree / "bot/new_module.py").write_text(source, newline="\n")
        (worktree / "migrations/999_fixture.sql").write_text("SELECT 1;\n", newline="\n")
        (worktree / ".github/workflows/fixture.yml").write_text("name: fixture\n", newline="\n")
        (worktree / "Dockerfile").write_text("FROM scratch\n# updated\n", newline="\n")
        (worktree / "scripts/new_check.py").write_text("assert 2 == 2\n", newline="\n")
        if (worktree / "docs/remove.txt").exists():
            (worktree / "docs/remove.txt").unlink()
        if (worktree / "old.py").exists():
            (worktree / "old.py").rename(worktree / "scripts/renamed.py")
        if self.codex_calls > 1:
            (worktree / "docs/retry.txt").write_text(f"attempt={self.codex_calls}\n", newline="\n")
        return CodexResult(0, "tests run", "")

    def make_runner(self):
        parent = self
        class Publisher(GitPublisher):
            def commit(self, *args, **kwargs):
                parent.maybe_fail("commit")
                return super().commit(*args, **kwargs)
            def push_task_branch(self, *args, **kwargs):
                parent.maybe_fail("push")
                return super().push_task_branch(*args, **kwargs)
        class GitHub:
            def ensure_draft_pr(self, *args, **kwargs):
                parent.maybe_fail("pr")
                return SimpleNamespace(number=42, url="https://github.com/U-KID-AI/ichiyon-robot/pull/42")
        class CI:
            def wait_for_draft_candidate(self, *args, **kwargs):
                parent.maybe_fail("ci")
        class Merge:
            def ready_and_squash_merge(self, cwd, task_id, sha, number, url, changed, **kwargs):
                parent.maybe_fail("merge")
                return SimpleNamespace(merge_sha=sha, workflow_run_id=123)
        class Deployer:
            def deploy(self, sha, **kwargs):
                parent.events.append("deploy")
                if parent.deploy_failures:
                    parent.deploy_failures -= 1
                    raise RuntimeError("deploy: build.py:80: image build failed")
                return SimpleNamespace(deployed_commit_sha=sha, summary="deployment complete")
        return LocalRunner(self.config, client=self.client,
            git=GitAdapter(self.source, self.git_path),
            codex=SimpleNamespace(run=self.codex_run, stop=lambda: None),
            publisher=Publisher(self.git_path), github=GitHub(), review_gate=CI(),
            auto_merger=Merge(), deployer=Deployer(), heartbeat_factory=Heartbeat)

    def test_untracked_whitespace_repairs_then_real_commit_and_push(self):
        self.whitespace_first = True
        runner = self.make_runner()
        self.assertEqual(runner.run_once(), RunOutcome.SUCCESS)
        self.assertEqual(self.codex_calls, 2)
        self.assertIn("trailing whitespace", self.prompts[1])
        self.assertIn("bot/new_module.py", self.prompts[1])
        worktree = self.config.worktree_root / self.task.worktree_name
        self.assertEqual(self.git_cmd(worktree, "status", "--porcelain"), "")
        head = self.git_cmd(worktree, "rev-parse", "HEAD").strip()
        remote = self.git_cmd(self.root, "--git-dir", str(self.root / "remote.git"),
                              "rev-parse", self.task.branch_name).strip()
        self.assertEqual(head, remote)
        changes = self.git_cmd(worktree, "diff", "--name-status", self.base, "HEAD")
        for name in ("Dockerfile", ".github/workflows/fixture.yml", "migrations/999_fixture.sql",
                     "docs/remove.txt", "bot/new_module.py", "scripts/renamed.py"):
            self.assertIn(name, changes)
        self.assertEqual(self.client.calls[-1][0], "completed")
        self.assertNotIn("needs_human", [name for name, _ in self.client.calls])

    def test_each_actual_operation_failure_returns_diagnostics_to_codex(self):
        for stage in ("codex", "commit", "push", "pr", "ci", "merge"):
            with self.subTest(stage=stage):
                # Each subtest needs its own task branch and worktree.
                self.task = ClaimedTask(uuid4(), "Implement changes", "", "", uuid4(), "2099-01-01T00:00:00Z")
                self.task = ClaimedTask(self.task.task_id, self.task.description,
                    f"ai/task/{self.task.task_id}", f"ai-task-{self.task.task_id}",
                    self.task.claim_token, self.task.lease_expires_at)
                self.client = Client(self.task)
                self.prompts, self.codex_calls = [], 0
                self.break_stage, self.failed_stages = stage, set()
                self.assertEqual(self.make_runner().run_once(), RunOutcome.SUCCESS)
                self.assertEqual(self.codex_calls, 2)
                self.assertIn("src/example.py:42: actual diagnostic", self.prompts[1])
                self.assertNotIn("hidden-value", self.prompts[1])
                self.assertIn("retry", [name for name, _ in self.client.calls])

    def test_deploy_transport_retry_does_not_repeat_codex_or_merge(self):
        self.deploy_failures = 1
        self.assertEqual(self.make_runner().run_once(), RunOutcome.SUCCESS)
        self.assertEqual(self.codex_calls, 1)
        self.assertEqual(self.events.count("merge"), 1)
        self.assertEqual(self.events.count("deploy"), 2)

    def test_unavailable_merge_confirmation_does_not_create_a_duplicate_pr(self):
        runner = self.make_runner()
        def unresolved(*args, **kwargs):
            raise MergeOutcomeUnknownError("GitHub unavailable after 3 merge confirmation reads")
        runner.auto_merger.ready_and_squash_merge = unresolved
        self.assertEqual(runner.run_once(), RunOutcome.FAILED)
        self.assertEqual(self.codex_calls, 1)
        self.assertEqual(self.events.count("pr"), 1)
        self.assertIn("3 merge confirmation reads", self.client.calls[-1][1]["reason"])

    def test_completion_api_retry_does_not_repeat_edit_merge_or_deploy(self):
        calls = []
        original = self.client.mark_completed
        def complete(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("HTTP 503 recording completed task")
            original(*args, **kwargs)
        self.client.mark_completed = complete
        self.config = replace(self.config, poll_seconds=0.001)
        self.assertEqual(self.make_runner().run_once(), RunOutcome.SUCCESS)
        self.assertEqual(self.codex_calls, 1)
        self.assertEqual(self.events.count("merge"), 1)
        self.assertEqual(self.events.count("deploy"), 1)
        self.assertEqual(calls[0], calls[1])

    def test_metadata_transport_errors_retry_only_the_control_operation(self):
        for operation in ("progress", "mark_deploying", "retry"):
            with self.subTest(operation=operation):
                task_id = uuid4()
                self.task = ClaimedTask(task_id, "Implement changes", f"ai/task/{task_id}",
                    f"ai-task-{task_id}", uuid4(), "2099-01-01T00:00:00Z")
                self.client = Client(self.task)
                self.codex_calls, self.events, self.prompts = 0, [], []
                self.whitespace_first = operation == "retry"
                original = getattr(self.client, operation)
                calls = []
                def flaky(*args, **kwargs):
                    calls.append((args, kwargs))
                    if len(calls) == 1:
                        raise RuntimeError("HTTP 503 metadata service unavailable")
                    return original(*args, **kwargs)
                setattr(self.client, operation, flaky)
                self.config = replace(self.config, poll_seconds=0.001)
                self.assertEqual(self.make_runner().run_once(), RunOutcome.SUCCESS)
                self.assertEqual(self.codex_calls, 2 if operation == "retry" else 1)
                self.assertEqual(self.events.count("merge"), 1)
                self.assertEqual(self.events.count("deploy"), 1)
                self.assertEqual(calls[0], calls[1])

    def test_deploy_code_failure_can_return_to_edit_test_publish(self):
        self.deploy_failures = 2
        self.assertEqual(self.make_runner().run_once(), RunOutcome.SUCCESS)
        self.assertEqual(self.codex_calls, 2)
        self.assertIn("image build failed", self.prompts[1])
        self.assertEqual(self.events.count("merge"), 2)

    def test_test_failure_feedback_is_not_reduced_to_known_phrases(self):
        runner = self.make_runner()
        calls = []
        def tests(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return [TestResult("project-test", 1, "custom/test.py:17: expected 42, got 41")]
            return run_tests(*args, **kwargs)
        runner.test_runner = tests
        self.assertEqual(runner.run_once(), RunOutcome.SUCCESS)
        self.assertIn("custom/test.py:17: expected 42, got 41", self.prompts[1])

    def test_exhaustion_records_actual_exception_and_keeps_edits(self):
        runner = self.make_runner()
        runner.test_runner = lambda *a, **k: [TestResult("project-test", 1, "src/file.py:9: unresolved failure")]
        self.assertEqual(runner.run_once(), RunOutcome.FAILED)
        self.assertEqual(self.codex_calls, self.config.max_attempts)
        self.assertIn("src/file.py:9: unresolved failure", self.client.calls[-1][1]["reason"])
        self.assertTrue((self.config.worktree_root / self.task.worktree_name / "bot/new_module.py").exists())

    def test_redaction_keeps_hashes_paths_and_non_secret_long_values(self):
        text = "a" * 80 + " src/long_file_name.py:100\nPASSWORD=secret-value\nAuthorization: Bearer abc.def\n"
        cleaned = redact_secrets(text)
        self.assertIn("a" * 80, cleaned)
        self.assertIn("src/long_file_name.py:100", cleaned)
        self.assertNotIn("secret-value", cleaned)
        self.assertNotIn("abc.def", cleaned)
        item = TestResult("custom", 1, "tail diagnostic " + "x" * 5000)
        self.assertIn("x" * 5000, test_feedback(item))

    def test_idle_maintenance_only_without_task(self):
        deployer = SimpleNamespace(catch_up=lambda: self.events.append("catch_up"))
        for outcome in RunOutcome:
            run_idle_maintenance(deployer, outcome)
        self.assertEqual(self.events, ["catch_up"])


if __name__ == "__main__":
    from check_ai_task_heartbeat import HeartbeatTests, LeaseClientTests, DeploymentLeaseTests
    from check_ai_task_runner import InitialFetchTests, MainRefreshTests

    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
