"""Real-Git regressions for refreshing main after a known task merge failure."""

import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import check_ai_task_local_runner as fixtures
from ai_task_auto_merge import AutoMergeError, MergeOutcomeUnknownError
from ai_task_codex import CodexResult
from ai_task_git import GitAdapter, GitOperationError
from ai_task_runner import LocalRunner, RunOutcome, RunnerAPIError


class TrackingGit(GitAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.integrations = []
        self.diff_bases = []

    def integrate_main(self, cwd):
        self.integrations.append(cwd)
        return super().integrate_main(cwd)

    def changed_files(self, cwd, base_sha=None):
        self.diff_bases.append(base_sha)
        return super().changed_files(cwd, base_sha)


class MainRefreshTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RunnerTests(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        f = self.fixture
        self.runner = f.make_runner()
        self.git = TrackingGit(f.source, f.git_path)
        self.runner.git = self.git
        self.worktree = f.config.worktree_root / f.task.worktree_name
        self.upstream = f.root / "upstream"
        self.first_edits = {"task.txt": "requested task change\n"}
        self.before_retry = lambda cwd, prompt: None
        self.on_merge = lambda cwd, sha: None
        self.merge_calls = []
        self.runner.codex.run = self.codex_run
        self.runner.auto_merger.ready_and_squash_merge = self.merge

    def git_cmd(self, cwd, *args):
        return self.fixture.git_cmd(cwd, *args)

    @staticmethod
    def write(cwd, name, text):
        path = cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def prepare_upstream(self):
        if not self.upstream.exists():
            f = self.fixture
            self.git_cmd(f.root, "clone", "--config", "core.autocrlf=false", "--branch", "main",
                         str(f.root / "remote.git"), str(self.upstream))
            self.git_cmd(self.upstream, "config", "user.name", "Concurrent main test")
            self.git_cmd(self.upstream, "config", "user.email", "main@example.invalid")
            self.git_cmd(self.upstream, "config", "core.autocrlf", "false")

    def push_main(self):
        self.git_cmd(self.upstream, "add", "-A")
        self.git_cmd(self.upstream, "commit", "-m", "concurrent main update")
        self.git_cmd(self.upstream, "push", "origin", "main")
        return self.git_cmd(self.upstream, "rev-parse", "HEAD").strip()

    def advance_main(self, name="main.txt", text="concurrent main change\n"):
        self.prepare_upstream()
        self.write(self.upstream, name, text)
        return self.push_main()

    def codex_run(self, cwd, output, prompt, **kwargs):
        f = self.fixture
        f.codex_calls += 1
        f.prompts.append(prompt)
        if f.codex_calls == 1:
            for name, text in self.first_edits.items():
                self.write(cwd, name, text)
        else:
            self.before_retry(cwd, prompt)
        return CodexResult(0, "task edits preserved", "")

    def merge(self, cwd, task_id, sha, number, url, changed, **kwargs):
        self.merge_calls.append((sha, tuple(changed)))
        self.on_merge(cwd, sha)
        return SimpleNamespace(merge_sha=sha, workflow_run_id=123)

    def test_known_merge_failure_refreshes_before_unchanged_codex_retry(self):
        f = self.fixture
        main = []

        def merge(cwd, sha):
            if len(self.merge_calls) == 1:
                main.append(self.advance_main())
                self.assertEqual(self.git_cmd(cwd, "rev-parse", "origin/main").strip(), f.base)
                raise AutoMergeError("PR is behind main; update the branch")
            self.git_cmd(cwd, "merge-base", "--is-ancestor", main[0], sha)

        def before_retry(cwd, prompt):
            self.assertIn("Runner integrated origin/main", prompt)
            self.assertIn("PR is behind main", prompt)
            self.assertEqual((cwd / "main.txt").read_text(), "concurrent main change\n")
            self.assertEqual((cwd / "task.txt").read_text(), "requested task change\n")
            self.git_cmd(cwd, "merge-base", "--is-ancestor", main[0], "HEAD")

        self.on_merge, self.before_retry = merge, before_retry
        self.assertEqual(self.runner.run_once(), RunOutcome.SUCCESS)
        self.assertEqual(f.codex_calls, 2)
        self.assertEqual(self.git.diff_bases, [f.base, f.base])
        self.assertEqual(len(self.git.integrations), 1)
        self.assertNotEqual(self.merge_calls[0][0], self.merge_calls[1][0])
        self.assertIn("task.txt", self.merge_calls[1][1])
        remote = self.git_cmd(f.root, "--git-dir", str(f.root / "remote.git"),
                              "rev-parse", f.task.branch_name).strip()
        self.assertEqual(remote, self.merge_calls[1][0])

    def test_conflicts_and_pending_edits_reach_codex_and_publish_after_resolution(self):
        f = self.fixture
        self.first_edits = {"old.py": "VALUE = 2\n"}
        main = []

        def merge(cwd, sha):
            if len(self.merge_calls) == 1:
                main.append(self.advance_main("old.py", "VALUE = 3\n"))
                self.write(cwd, "pending.txt", "keep this unfinished edit\n")
                raise AutoMergeError("PR merge conflict with current main")
            self.git_cmd(cwd, "merge-base", "--is-ancestor", main[0], sha)

        def before_retry(cwd, prompt):
            self.assertIn("CONFLICT", prompt)
            self.assertIn("old.py", prompt)
            self.assertIn("retained for repair", prompt)
            self.assertIn("UU old.py", self.git_cmd(cwd, "status", "--porcelain"))
            self.assertIn("<<<<<<<", (cwd / "old.py").read_text())
            self.assertEqual((cwd / "pending.txt").read_text(), "keep this unfinished edit\n")
            self.assertEqual(self.git_cmd(cwd, "rev-parse", "MERGE_HEAD").strip(), main[0])
            self.write(cwd, "old.py", "VALUE = 4\n")

        self.on_merge, self.before_retry = merge, before_retry
        self.assertEqual(self.runner.run_once(), RunOutcome.SUCCESS)
        self.assertEqual(f.codex_calls, 2)
        self.assertEqual(self.git.diff_bases, [f.base, f.base])
        self.assertEqual((self.worktree / "old.py").read_text(), "VALUE = 4\n")
        self.assertEqual(self.git_cmd(self.worktree, "status", "--porcelain"), "")
        parents = self.git_cmd(self.worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()
        self.assertEqual(len(parents), 3)
        self.assertIn(main[0], parents[1:])

    def test_dirty_file_blocking_merge_is_retained_in_retry_feedback(self):
        f = self.fixture

        def merge(cwd, sha):
            if len(self.merge_calls) == 1:
                self.advance_main("old.py", "VALUE = 3\n")
                self.write(cwd, "old.py", "VALUE = 99\n")
                raise AutoMergeError("PR is behind main")

        def before_retry(cwd, prompt):
            self.assertIn("would be overwritten by merge", prompt)
            self.assertIn("old.py", prompt)
            self.assertEqual((cwd / "old.py").read_text(), "VALUE = 99\n")
            self.assertIn(" M old.py", self.git_cmd(cwd, "status", "--porcelain"))

        self.on_merge, self.before_retry = merge, before_retry
        self.assertEqual(self.runner.run_once(), RunOutcome.SUCCESS)
        self.assertEqual(f.codex_calls, 2)
        self.assertEqual(self.git.diff_bases, [f.base, f.base])
        self.assertEqual((self.worktree / "old.py").read_text(), "VALUE = 99\n")

    def test_unknown_merge_outcome_never_refreshes_or_repeats_codex(self):
        def merge(cwd, sha):
            self.advance_main()
            raise MergeOutcomeUnknownError("GitHub merge outcome unavailable")

        self.on_merge = merge
        self.assertEqual(self.runner.run_once(), RunOutcome.FAILED)
        self.assertEqual(self.fixture.codex_calls, 1)
        self.assertEqual(len(self.merge_calls), 1)
        self.assertEqual(self.git.integrations, [])
        self.assertEqual(self.git_cmd(self.worktree, "rev-parse", "origin/main").strip(), self.fixture.base)

    def test_nonmerge_failure_does_not_trigger_main_integration(self):
        self.fixture.break_stage = "ci"
        self.assertEqual(self.runner.run_once(), RunOutcome.SUCCESS)
        self.assertEqual(self.fixture.codex_calls, 2)
        self.assertEqual(self.git.integrations, [])
        self.assertEqual(self.git.diff_bases, [self.fixture.base, self.fixture.base])

    def test_after_completed_merge_uses_fetched_main_not_integration_head_as_base(self):
        f = self.fixture
        f.deploy_failures = 2
        main = []

        def merge(cwd, sha):
            if len(self.merge_calls) == 1:
                self.prepare_upstream()
                self.git_cmd(self.upstream, "fetch", "origin", f.task.branch_name)
                self.git_cmd(self.upstream, "merge", "--squash", sha)
                main.append(self.push_main())
                self.write(cwd, "pending-repair.txt", "committed repair not yet on main\n")
                self.git_cmd(cwd, "add", "-A")
                self.git_cmd(cwd, "commit", "-m", "pending local repair")

        def before_retry(cwd, prompt):
            self.assertIn("image build failed", prompt)
            self.git_cmd(cwd, "merge-base", "--is-ancestor", main[0], "HEAD")
            self.assertNotEqual(self.git_cmd(cwd, "rev-parse", "HEAD").strip(), main[0])
            self.assertEqual((cwd / "pending-repair.txt").read_text(), "committed repair not yet on main\n")

        self.on_merge, self.before_retry = merge, before_retry
        self.assertEqual(self.runner.run_once(), RunOutcome.SUCCESS)
        self.assertEqual(f.codex_calls, 2)
        self.assertEqual(self.git.diff_bases, [f.base, main[0]])
        self.assertEqual(self.merge_calls[1][1], ("pending-repair.txt",))
        self.assertIn(main[0], [data["base_commit_sha"] for name, data in f.client.calls
                               if name == "progress" and "base_commit_sha" in data])

    def test_fetch_failure_preserves_task_edits_and_full_feedback(self):
        def merge(cwd, sha):
            if len(self.merge_calls) == 1:
                raise AutoMergeError("PR is behind main")

        def unavailable(cwd):
            raise GitOperationError("git fetch failed", stdout="fetch output", stderr="remote unavailable")

        self.on_merge = merge
        self.git.integrate_main = unavailable
        self.assertEqual(self.runner.run_once(), RunOutcome.SUCCESS)
        self.assertEqual(self.fixture.codex_calls, 2)
        self.assertIn("fetch output", self.fixture.prompts[1])
        self.assertIn("remote unavailable", self.fixture.prompts[1])
        self.assertEqual((self.worktree / "task.txt").read_text(), "requested task change\n")


class InitialFetchTests(unittest.TestCase):
    def setUp(self):
        task_id = uuid4()
        self.task = fixtures.ClaimedTask(task_id, "Fetch retry", f"ai/task/{task_id}",
            f"ai-task-{task_id}", uuid4(), "2099-01-01T00:00:00Z")
        self.runner = LocalRunner.__new__(LocalRunner)
        self.runner.config = SimpleNamespace(max_attempts=3, poll_seconds=5, heartbeat_seconds=30,
            api_token="private-fetch-token", worktree_root=Path("unused-fake-worktrees"))
        self.runner.git = Mock()
        self.runner.client = Mock()
        self.runner.codex = Mock()
        self.heartbeat = Mock()
        self.heartbeat.lost.is_set.return_value = False
        self.heartbeat.lost.wait.return_value = False
        self.runner.heartbeat_factory = Mock(return_value=self.heartbeat)
        self.runner.git.add_worktree.side_effect = RuntimeError("reached worktree creation")

    @staticmethod
    def fetch_error():
        return GitOperationError("git fetch origin main failed", stdout="fetch stdout",
                                 stderr="HTTP 503 private-fetch-token", returncode=128)

    def test_initial_fetch_retries_only_fetch_then_creates_one_worktree(self):
        self.runner.git.fetch_main.side_effect = [self.fetch_error(), "a" * 40]
        with self.assertRaisesRegex(RuntimeError, "reached worktree creation"):
            self.runner._process(self.task)
        self.assertEqual(self.runner.git.fetch_main.call_count, 2)
        self.runner.git.require_source_repo.assert_called_once()
        self.runner.git.add_worktree.assert_called_once_with(
            self.task.task_id, self.runner.config.worktree_root, "a" * 40)
        self.heartbeat.lost.wait.assert_called_once_with(5)
        progress = "\n".join(call.kwargs.get("progress_summary", "")
                             for call in self.runner.client.progress.call_args_list)
        self.assertIn("fetch stdout", progress)
        self.assertIn("HTTP 503", progress)
        self.assertNotIn("private-fetch-token", progress)
        self.runner.codex.run.assert_not_called()
        self.heartbeat.stop.assert_called_once()

    def test_initial_fetch_exhaustion_never_creates_worktree(self):
        self.runner.git.fetch_main.side_effect = self.fetch_error()
        with self.assertRaises(GitOperationError) as raised:
            self.runner._process(self.task)
        self.assertIn("fetch stdout", str(raised.exception))
        self.assertEqual(self.runner.git.fetch_main.call_count, 3)
        self.assertEqual(self.heartbeat.lost.wait.call_count, 2)
        self.runner.git.add_worktree.assert_not_called()
        self.runner.codex.run.assert_not_called()
        self.heartbeat.stop.assert_called_once()

    def test_lease_loss_during_fetch_backoff_stops_retry(self):
        self.runner.git.fetch_main.side_effect = self.fetch_error()
        def lose_lease(delay):
            self.heartbeat.lost.is_set.return_value = True
            return True
        self.heartbeat.lost.wait.side_effect = lose_lease
        with self.assertRaises(RunnerAPIError):
            self.runner._process(self.task)
        self.runner.git.fetch_main.assert_called_once()
        self.runner.git.add_worktree.assert_not_called()

    def test_lease_loss_during_successful_fetch_prevents_worktree_creation(self):
        def fetch():
            self.heartbeat.lost.is_set.return_value = True
            return "a" * 40
        self.runner.git.fetch_main.side_effect = fetch
        with self.assertRaises(RunnerAPIError):
            self.runner._process(self.task)
        self.runner.git.fetch_main.assert_called_once()
        self.runner.git.add_worktree.assert_not_called()
        self.heartbeat.lost.wait.assert_not_called()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
