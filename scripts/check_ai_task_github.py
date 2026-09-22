"""Offline checks for ordinary task PR creation, reuse and command diagnostics."""

import io
import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_task_github import GitHubAdapter, GitHubError
from ai_task_process import ProcessResult

TASK_ID = UUID("00000000-0000-0000-0000-000000000001")
COMMIT_SHA = "a" * 40
BRANCH = f"ai/task/{TASK_ID}"
GH = Path(sys.executable).resolve()


def pr_item(**changes):
    return dict(
        number=123, url="https://github.com/U-KID-AI/ichiyon-robot/pull/123",
        isDraft=False, baseRefName="main", headRefName=BRANCH,
        headRefOid=COMMIT_SHA, state="OPEN", **changes,
    )


def result(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class GitHubChecks(unittest.TestCase):
    def test_create_ordinary_pr_and_reuse_ready_pr_after_repair(self):
        item = pr_item()
        calls = []
        exists = False

        def runner(argv, **kwargs):
            nonlocal exists
            calls.append((argv[1:], kwargs))
            if argv[1:3] == ["pr", "list"]:
                self.assertIn("open", argv)
                return result(json.dumps([item] if exists else []))
            self.assertEqual(argv[1:3], ["pr", "create"])
            self.assertNotIn("--draft", argv)
            self.assertIn(BRANCH, argv)
            exists = True
            return result(item["url"])

        adapter = GitHubAdapter(GH, runner=runner)
        first = adapter.ensure_draft_pr(ROOT, TASK_ID, COMMIT_SHA)
        repaired_sha = "b" * 40
        item["headRefOid"] = repaired_sha
        reused = adapter.ensure_draft_pr(ROOT, TASK_ID, repaired_sha)
        self.assertEqual(first.number, reused.number)
        self.assertEqual(reused.head_sha, repaired_sha)
        self.assertEqual(sum(args[:2] == ["pr", "create"] for args, _ in calls), 1)
        self.assertTrue(all(not kwargs["shell"] for _, kwargs in calls))

    def test_merged_predecessor_does_not_block_new_pr_on_same_branch(self):
        item = pr_item()
        item.update(number=124, url=item["url"].replace("123", "124"))
        calls = []
        responses = iter([result("[]"), result(item["url"]), result(json.dumps([item]))])

        def runner(argv, **kwargs):
            calls.append(argv)
            return next(responses)

        created = GitHubAdapter(GH, runner=runner).ensure_draft_pr(ROOT, TASK_ID, COMMIT_SHA)
        self.assertEqual(created.number, 124)
        self.assertEqual(calls[0][calls[0].index("--state") + 1], "open")
        self.assertEqual(calls[1][1:3], ["pr", "create"])

    def test_reuses_draft_too_and_confirms_ambiguous_creation(self):
        item = pr_item()
        item["isDraft"] = True
        responses = iter([result("[]"), result("created", "connection lost", 1),
                          result(json.dumps([item]))])
        adapter = GitHubAdapter(GH, runner=lambda *a, **kw: next(responses))
        self.assertEqual(adapter.ensure_draft_pr(ROOT, TASK_ID, COMMIT_SHA).number, 123)

    def test_identity_mismatches_are_operational_errors(self):
        for field, value in (("headRefName", "other"), ("headRefOid", "f" * 40),
                             ("baseRefName", "other"), ("url", "https://example.com/pull/123")):
            with self.subTest(field=field):
                item = pr_item()
                item[field] = value
                adapter = GitHubAdapter(GH, runner=lambda *a, **kw: result(json.dumps([item])))
                with self.assertRaises(GitHubError) as raised:
                    adapter.ensure_draft_pr(ROOT, TASK_ID, COMMIT_SHA)
                self.assertIn(str(value), str(raised.exception))

    def test_full_diagnostics_and_secret_redaction(self):
        output = "compiler output\n" + "diagnostic line\n" * 12000 + "\nlast line"
        adapter = GitHubAdapter(GH, runner=lambda *a, **kw: result(output, "denied test-token-value", 1))
        with patch.dict(os.environ, {"GH_TOKEN": "test-token-value"}):
            with self.assertRaises(GitHubError) as raised:
                adapter.ensure_draft_pr(ROOT, TASK_ID, COMMIT_SHA)
        self.assertIn(output, str(raised.exception))
        self.assertIn("denied [redacted]", str(raised.exception))
        self.assertNotIn("test-token-value", raised.exception.stderr)

    def test_create_failure_preserves_mutation_and_followup_output(self):
        responses = iter([result("[]"), result("create output", "create rejected", 1),
                          result("followup output", "inspection unavailable", 1)])
        adapter = GitHubAdapter(GH, runner=lambda *a, **kw: next(responses))
        with self.assertRaises(GitHubError) as raised:
            adapter.ensure_draft_pr(ROOT, TASK_ID, COMMIT_SHA)
        for expected in ("create output", "create rejected", "followup output", "inspection unavailable"):
            self.assertIn(expected, str(raised.exception))

    def test_no_argv_allowlist_and_normal_environment(self):
        calls = []
        adapter = GitHubAdapter(GH, runner=lambda argv, **kw: calls.append((argv, kw)) or result("ok"))
        with patch.dict(os.environ, {"GH_TOKEN": "fake-cli-auth"}):
            adapter._run(("repo", "view"), cwd=ROOT)
        self.assertEqual(calls[0][0][1:], ["repo", "view"])
        self.assertEqual(calls[0][1]["env"]["GH_TOKEN"], "fake-cli-auth")

    def test_timeout_preserves_stdout_and_stderr(self):
        def runner(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 1, output=b"partial stdout", stderr=b"partial stderr")
        with self.assertRaises(GitHubError) as raised:
            GitHubAdapter(GH, runner=runner)._run(("repo", "view"), cwd=ROOT, timeout=1)
        self.assertIn("partial stdout", str(raised.exception))
        self.assertIn("partial stderr", str(raised.exception))

    def test_stopped_operation_never_starts(self):
        stop = threading.Event()
        stop.set()
        def unexpected(*args, **kwargs):
            self.fail("a process was started after cancellation")
        adapter = GitHubAdapter(GH, runner=unexpected, popen=unexpected)
        with self.assertRaisesRegex(GitHubError, "Stopped"):
            adapter.ensure_draft_pr(ROOT, TASK_ID, COMMIT_SHA, stop_event=stop)

    def test_stop_aware_process_retains_large_output(self):
        output = "z" * 160000 + "\nlast line"
        process = SimpleNamespace(
            stdout=io.BytesIO(output.encode()), stderr=io.BytesIO(b"warning"),
            poll=lambda: 0, wait=lambda **kw: 0, returncode=0,
        )
        adapter = GitHubAdapter(GH, popen=lambda *a, **kw: process)
        received = adapter._run(("repo", "view"), cwd=ROOT, stop_event=threading.Event())
        self.assertEqual(received.stdout, output)
        self.assertEqual(received.stderr, "warning")
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_active_stop_and_timeout_preserve_partial_output(self):
        adapter = GitHubAdapter(GH, popen=lambda *a, **kw: object())
        for flags in (dict(stopped=True), dict(timed_out=True)):
            with self.subTest(flags=flags):
                with patch("ai_task_github.communicate_bounded",
                           return_value=ProcessResult(-1, "partial output", "partial error", **flags)):
                    with self.assertRaises(GitHubError) as raised:
                        adapter._run(("repo", "view"), cwd=ROOT, stop_event=threading.Event())
                self.assertIn("partial output", str(raised.exception))
                self.assertIn("partial error", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
