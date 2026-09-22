"""Offline end-to-end PR, CI and squash-merge checks."""

import copy
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_task_auto_merge import AutoMergeAdapter, AutoMergeError, MergeOutcomeUnknownError
from ai_task_github import GitHubAdapter
from ai_task_review_merge import CIMonitorError, ReviewCIFailedError
from check_ai_task_review_merge import (
    BASE_SHA, BRANCH, COMMIT_SHA, FILES, GH, MERGE_SHA, PR_NUMBER, PR_URL, TASK_ID,
    FakeGitHub, check_item, result,
)


class RecoveringGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.confirmation_reads = 0
        self.read_failures = 0
        self.stale_reads = 0
        self.merge_timeout = False
        self.stop_on_confirmation = None
        self.confirmation_override = None

    def __call__(self, argv, **kwargs):
        received = super().__call__(argv, **kwargs)
        if argv[1:3] == ["pr", "merge"] and self.merge_timeout:
            raise subprocess.TimeoutExpired(argv, 120, output="merge response lost", stderr="transport timeout")
        if argv[1:4] == ["api", "--method", "GET"] and self.pr.get("merged_at"):
            self.confirmation_reads += 1
            if self.confirmation_override is not None:
                return result(self.confirmation_override)
            if self.stop_on_confirmation is not None:
                self.stop_on_confirmation.set()
            if self.confirmation_reads <= self.read_failures:
                return result(f"read output {self.confirmation_reads}", stderr="temporary read failure", code=1)
            if self.confirmation_reads <= self.read_failures + self.stale_reads:
                stale = copy.deepcopy(self.pr)
                stale.update(state="open", merged_at=None, merge_commit_sha=None)
                return result(stale)
        return received


class AutoMergeChecks(unittest.TestCase):
    def setUp(self):
        # Exercise backoff without real sleeps or network traffic.
        sleeper = patch("ai_task_auto_merge.time.sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def merge(self, backend, *, stop_event=None):
        return AutoMergeAdapter(GH, runner=backend, popen=backend.popen).ready_and_squash_merge(
            ROOT, TASK_ID, COMMIT_SHA, PR_NUMBER, PR_URL, FILES, stop_event=stop_event,
        )

    def test_complete_pr_create_ci_and_squash_merge_flow(self):
        backend = FakeGitHub()
        backend.listed = False
        pr = GitHubAdapter(GH, runner=backend).ensure_draft_pr(ROOT, TASK_ID, COMMIT_SHA)
        self.assertEqual(pr.number, PR_NUMBER)
        merged = self.merge(backend)
        self.assertEqual(merged.merge_sha, MERGE_SHA)
        self.assertEqual(merged.head_sha, COMMIT_SHA)
        self.assertEqual(merged.base_sha, backend.merge_base)
        self.assertEqual(merged.workflow_run_id, 900)
        calls = backend.calls
        merge = next(args for args in calls if args[:2] == ["pr", "merge"])
        self.assertIn("--squash", merge)
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], COMMIT_SHA)
        self.assertNotIn("--admin", merge)
        self.assertFalse(any(args[:2] == ["pr", "ready"] for args in calls))

    def test_legacy_draft_can_be_readied_and_merged(self):
        backend = FakeGitHub()
        backend.pr["draft"] = True
        self.assertEqual(self.merge(backend).merge_sha, MERGE_SHA)
        ready_index = next(i for i, args in enumerate(backend.calls) if args[:2] == ["pr", "ready"])
        checks_index = next(i for i, args in enumerate(backend.calls) if args[:2] == ["pr", "checks"])
        self.assertLess(ready_index, checks_index)

    def test_ci_failure_remains_repairable_and_no_merge_is_attempted(self):
        backend = FakeGitHub()
        backend.checks = [check_item("fail")]
        with self.assertRaises(ReviewCIFailedError) as raised:
            self.merge(backend)
        self.assertTrue(raised.exception.repairable)
        self.assertIn("AssertionError: expected 2 got 1", str(raised.exception))
        self.assertFalse(any(args[:2] == ["pr", "merge"] for args in backend.calls))

    def test_ready_pr_can_be_repaired_and_retried(self):
        backend = FakeGitHub()
        backend.checks = [check_item("fail")]
        with self.assertRaises(ReviewCIFailedError):
            self.merge(backend)
        repaired_sha = "e" * 40
        backend.pr["head"]["sha"] = repaired_sha
        pr = GitHubAdapter(GH, runner=backend).ensure_draft_pr(ROOT, TASK_ID, repaired_sha)
        backend.checks = [check_item()]
        backend.merge_head = repaired_sha
        merged = AutoMergeAdapter(GH, runner=backend).ready_and_squash_merge(
            ROOT, TASK_ID, repaired_sha, pr.number, pr.url, ("different/new-file.py",),
        )
        self.assertEqual(merged.head_sha, repaired_sha)
        self.assertEqual(merged.merge_sha, MERGE_SHA)

    def test_concurrent_main_updates_before_and_after_ci_are_allowed(self):
        backend = FakeGitHub()
        backend.pr["base"]["sha"] = "e" * 40
        backend.merge_base = "f" * 40
        merged = self.merge(backend)
        self.assertEqual(merged.base_sha, "f" * 40)
        self.assertEqual(merged.merge_sha, MERGE_SHA)
        self.assertFalse(any("git/ref/heads/main" in " ".join(args) for args in backend.calls))

    def test_lost_merge_response_confirmed_by_actual_pr(self):
        backend = FakeGitHub()
        backend.merge_code = 1
        self.assertEqual(self.merge(backend).merge_sha, MERGE_SHA)

    def test_already_merged_is_idempotent_without_ci_or_mutation(self):
        backend = FakeGitHub()
        backend.pr.update(state="closed", merged_at="2026-09-23T00:00:00Z", merge_commit_sha=MERGE_SHA)
        backend.pr["base"]["sha"] = "f" * 40
        backend.checks = [check_item("fail")]
        merged = self.merge(backend)
        self.assertEqual(merged.merge_sha, MERGE_SHA)
        self.assertEqual(merged.base_sha, "f" * 40)
        self.assertEqual(merged.workflow_run_id, 0)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][:3], ["api", "--method", "GET"])

    def test_retry_after_verification_response_was_lost(self):
        backend = FakeGitHub()
        backend.after_merge_read_error = True
        with self.assertRaises(AutoMergeError) as raised:
            self.merge(backend)
        self.assertIn("merge stdout", str(raised.exception))
        self.assertIn("read unavailable", str(raised.exception))
        backend.after_merge_read_error = False
        self.assertEqual(self.merge(backend).merge_sha, MERGE_SHA)
        self.assertEqual(sum(args[:2] == ["pr", "merge"] for args in backend.calls), 1)

    def test_transient_confirmation_failures_recover_inside_one_merge_call(self):
        backend = RecoveringGitHub()
        backend.read_failures = 2
        merged = self.merge(backend)
        self.assertEqual(merged.merge_sha, MERGE_SHA)
        self.assertEqual(merged.base_sha, backend.merge_base)
        self.assertEqual(merged.workflow_run_id, 900)
        self.assertEqual(backend.confirmation_reads, 3)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1, 2])
        self.assertEqual(sum(args[:2] == ["pr", "merge"] for args in backend.calls), 1)
        self.assertFalse(any(args[:2] == ["pr", "create"] for args in backend.calls))

    def test_lost_merge_response_and_stale_read_recover_without_second_mutation(self):
        backend = RecoveringGitHub()
        backend.merge_code = 1
        backend.read_failures = 1
        backend.stale_reads = 1
        self.assertEqual(self.merge(backend).merge_sha, MERGE_SHA)
        self.assertEqual(backend.confirmation_reads, 3)
        self.assertEqual(sum(args[:2] == ["pr", "merge"] for args in backend.calls), 1)

    def test_merge_command_timeout_still_reconciles_actual_result(self):
        backend = RecoveringGitHub()
        backend.merge_timeout = True
        backend.read_failures = 1
        self.assertEqual(self.merge(backend).merge_sha, MERGE_SHA)
        self.assertEqual(backend.confirmation_reads, 2)
        self.assertEqual(sum(args[:2] == ["pr", "merge"] for args in backend.calls), 1)

    def test_exhausted_confirmation_signals_unknown_outcome_with_all_diagnostics(self):
        backend = RecoveringGitHub()
        backend.merge_timeout = True
        backend.read_failures = 5
        with self.assertRaises(MergeOutcomeUnknownError) as raised:
            self.merge(backend)
        error = raised.exception
        self.assertTrue(error.merge_outcome_unknown)
        self.assertFalse(error.repairable)
        self.assertEqual((error.pr_number, error.pr_url, error.head_sha), (PR_NUMBER, PR_URL, COMMIT_SHA))
        for text in ("merge response lost", "transport timeout", "read output 1", "read output 2", "read output 3"):
            self.assertIn(text, str(error))
        self.assertEqual(backend.confirmation_reads, 3)
        self.assertEqual(sum(args[:2] == ["pr", "merge"] for args in backend.calls), 1)

    def test_stop_during_confirmation_backoff_does_not_retry(self):
        backend = RecoveringGitHub()
        backend.read_failures = 3
        stop = threading.Event()
        with patch.object(stop, "wait", side_effect=lambda delay: stop.set() or True) as wait:
            with self.assertRaisesRegex(AutoMergeError, "Stopped confirming merge") as raised:
                self.merge(backend, stop_event=stop)
        self.assertIn("merge stdout", str(raised.exception))
        self.assertIn("read output 1", str(raised.exception))
        self.assertEqual(backend.confirmation_reads, 1)
        wait.assert_called_once_with(1)
        self.sleep.assert_not_called()

    def test_real_merge_failure_includes_all_command_diagnostics(self):
        backend = FakeGitHub()
        backend.merge_succeeds = False
        backend.merge_code = 1
        with self.assertRaises(AutoMergeError) as raised:
            self.merge(backend)
        self.assertIs(type(raised.exception), AutoMergeError)
        self.assertFalse(getattr(raised.exception, "merge_outcome_unknown", False))
        self.assertIn("merge stdout", str(raised.exception))
        self.assertIn("merge stderr", str(raised.exception))
        self.assertIn('"state": "open"', str(raised.exception))

    def test_successful_read_of_closed_unmerged_pr_is_known_failure(self):
        backend = RecoveringGitHub()
        backend.confirmation_override = copy.deepcopy(backend.pr)
        backend.confirmation_override["state"] = "closed"
        with self.assertRaises(AutoMergeError) as raised:
            self.merge(backend)
        self.assertIs(type(raised.exception), AutoMergeError)
        self.assertIn("GitHub confirms it is not merged", str(raised.exception))
        self.assertIn("merge stdout", str(raised.exception))
        self.assertEqual(backend.confirmation_reads, 3)

    def test_timeout_followed_by_confirmed_open_pr_is_known_failure(self):
        backend = RecoveringGitHub()
        backend.merge_timeout = True
        backend.merge_succeeds = False
        with self.assertRaises(AutoMergeError) as raised:
            self.merge(backend)
        self.assertIs(type(raised.exception), AutoMergeError)
        self.assertIn("merge response lost", str(raised.exception))
        self.assertIn("GitHub confirms it is not merged", str(raised.exception))

    def test_invalid_confirmation_reads_remain_unknown(self):
        for response in ("not JSON", {}, {"base": "invalid"}):
            with self.subTest(response=response):
                backend = RecoveringGitHub()
                backend.confirmation_override = response
                with self.assertRaises(MergeOutcomeUnknownError) as raised:
                    self.merge(backend)
                self.assertTrue(raised.exception.merge_outcome_unknown)
                self.assertEqual(backend.confirmation_reads, 3)

    def test_missing_merge_state_is_invalid_not_confirmed_unmerged(self):
        backend = RecoveringGitHub()
        backend.confirmation_override = copy.deepcopy(backend.pr)
        del backend.confirmation_override["merged_at"]
        with self.assertRaises(MergeOutcomeUnknownError):
            self.merge(backend)
        self.assertEqual(backend.confirmation_reads, 3)

    def test_ready_failure_includes_diagnostics(self):
        backend = FakeGitHub()
        backend.pr["draft"] = True
        backend.ready_succeeds = False
        backend.ready_code = 1
        with self.assertRaises(AutoMergeError) as raised:
            self.merge(backend)
        self.assertIn("ready stdout", str(raised.exception))
        self.assertIn("ready stderr", str(raised.exception))
        self.assertFalse(any(args[:2] == ["pr", "merge"] for args in backend.calls))

    def test_head_identity_is_still_required(self):
        backend = FakeGitHub()
        backend.merge_head = "f" * 40
        with self.assertRaises(AutoMergeError):
            self.merge(backend)
        backend = FakeGitHub()
        backend.pr["head"]["ref"] = "someone-else"
        with self.assertRaises(CIMonitorError):
            self.merge(backend)
        self.assertEqual(len(backend.calls), 1)

    def test_stop_before_and_after_ready_prevents_merge(self):
        backend = FakeGitHub()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(AutoMergeError):
            self.merge(backend, stop_event=stop)
        self.assertEqual(backend.calls, [])
        stop.clear()
        backend.pr["draft"] = True
        backend.stop_after_ready = stop
        # The in-memory process has no OS process group to terminate.
        with patch("ai_task_process.terminate_process_tree") as terminate:
            with self.assertRaises((AutoMergeError, CIMonitorError)):
                self.merge(backend, stop_event=stop)
        terminate.assert_called_once()
        stopped_process = terminate.call_args.args[0]
        self.assertEqual(stopped_process.poll(), 0)
        self.assertTrue(stopped_process.stdout.closed)
        self.assertTrue(stopped_process.stderr.closed)
        self.assertFalse(any(args[:2] == ["pr", "merge"] for args in backend.calls))


if __name__ == "__main__":
    unittest.main()
