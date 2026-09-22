"""Offline checks for CI readiness, diagnostics, cancellation and task identity."""

import copy
import io
import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_task_review_merge import (
    ReviewCIFailedError, ReviewMergeGate, CIMonitorError, ReviewPendingError,
)

TASK_ID = UUID("00000000-0000-0000-0000-000000000001")
COMMIT_SHA = "a" * 40
BASE_SHA = "b" * 40
MERGE_SHA = "c" * 40
BRANCH = f"ai/task/{TASK_ID}"
PR_NUMBER = 123
REPO = "U-KID-AI/ichiyon-robot"
PR_URL = f"https://github.com/{REPO}/pull/{PR_NUMBER}"
GH = Path(sys.executable).resolve()
FILES = (".github/workflows/new.yml", "migrations/999.sql", "scripts/new.py")


def result(value="", *, stderr="", code=0):
    return SimpleNamespace(
        stdout=value if isinstance(value, str) else json.dumps(value), stderr=stderr, returncode=code,
    )


def pr_item():
    return dict(
        number=PR_NUMBER, html_url=PR_URL, state="open", draft=False, merged_at=None,
        mergeable=None,
        base=dict(ref="main", sha=BASE_SHA, repo=dict(full_name=REPO)),
        head=dict(ref=BRANCH, sha=COMMIT_SHA, repo=dict(full_name=REPO)),
    )


def check_item(bucket="pass", *, run_id=900, name="test"):
    return dict(
        name=name, workflow="any-workflow-name", state=bucket.upper(),
        bucket=bucket, description=f"{name} detail",
        link=f"https://github.com/{REPO}/actions/runs/{run_id}/job/100",
    )


class FakeGitHub:
    """All calls, including mutations, are local in-memory fixtures."""

    def __init__(self):
        self.pr = pr_item()
        self.after_pr = None
        self.pr_reads = 0
        self.checks = [check_item()]
        self.required_checks = None
        self.check_sequence = []
        self.check_error = None
        self.pages = [[dict(filename=name) for name in FILES]]
        self.logs = result("tests/test_feature.py:42: AssertionError: expected 2 got 1", stderr="job warning")
        self.calls = []
        self.listed = True
        self.ready_code = 0
        self.ready_succeeds = True
        self.merge_code = 0
        self.merge_succeeds = True
        self.merge_base = "d" * 40
        self.merge_head = COMMIT_SHA
        self.after_merge_read_error = False
        self.stop_after_ready = None

    def __call__(self, argv, **kwargs):
        args = argv[1:]
        self.calls.append(args)
        if args[:2] == ["pr", "list"]:
            return result([dict(
                number=self.pr["number"], url=self.pr["html_url"], isDraft=self.pr["draft"],
                baseRefName=self.pr["base"]["ref"], headRefName=self.pr["head"]["ref"],
                headRefOid=self.pr["head"]["sha"], state="OPEN",
            )] if self.listed else [])
        if args[:2] == ["pr", "create"]:
            self.listed = True
            return result(self.pr["html_url"])
        if args[:2] == ["pr", "checks"]:
            if self.check_error:
                return self.check_error
            if "--required" in args and self.required_checks is not None:
                if not self.required_checks:
                    return result("", stderr=f"no required checks reported on the '{BRANCH}' branch", code=1)
                return result(self.required_checks)
            checks = self.check_sequence.pop(0) if self.check_sequence else self.checks
            code = 1 if any(c["bucket"] in {"fail", "cancel"} for c in checks) else (
                8 if any(c["bucket"] == "pending" for c in checks) else 0
            )
            return result(checks, code=code)
        if args[:2] == ["run", "view"]:
            return self.logs
        if args[:2] == ["pr", "ready"]:
            if self.ready_succeeds:
                self.pr["draft"] = False
            if self.stop_after_ready is not None:
                self.stop_after_ready.set()
            return result("ready stdout", stderr="ready stderr", code=self.ready_code)
        if args[:2] == ["pr", "merge"]:
            if self.merge_succeeds:
                self.pr.update(state="closed", draft=False, merged_at="2026-09-23T00:00:00Z",
                               merge_commit_sha=MERGE_SHA)
                self.pr["base"]["sha"] = self.merge_base
                self.pr["head"]["sha"] = self.merge_head
            return result("merge stdout", stderr="merge stderr", code=self.merge_code)
        if args[:3] == ["api", "--method", "GET"]:
            if "/files?" in args[-1]:
                assert "--paginate" in args and "--slurp" in args
                return result(self.pages)
            assert args[-1] == f"repos/{REPO}/pulls/{PR_NUMBER}", args
            self.pr_reads += 1
            if self.pr.get("merged_at") and self.after_merge_read_error:
                return result("read stdout", stderr="read unavailable", code=1)
            return result(self.after_pr if self.after_pr is not None and self.pr_reads > 1 else self.pr)
        raise AssertionError(args)

    def popen(self, argv, **kwargs):
        received = self(argv, **kwargs)
        return SimpleNamespace(
            stdout=io.BytesIO(received.stdout.encode()), stderr=io.BytesIO(received.stderr.encode()),
            returncode=received.returncode, poll=lambda: received.returncode, wait=lambda **kw: received.returncode,
        )


class ReviewChecks(unittest.TestCase):
    def gate(self, backend):
        return ReviewMergeGate(GH, runner=backend, popen=backend.popen)

    def inspect(self, backend, files=FILES):
        return self.gate(backend).inspect_draft_candidate(
            ROOT, TASK_ID, COMMIT_SHA, PR_NUMBER, PR_URL, files,
        )

    def test_success_with_concurrent_main_change_and_no_file_or_workflow_policy(self):
        backend = FakeGitHub()
        backend.after_pr = copy.deepcopy(backend.pr)
        backend.after_pr["base"]["sha"] = "e" * 40
        backend.pages = [[dict(filename=f"scripts/file{i}.py") for i in range(2100)]]
        checked = self.inspect(backend, files=("unrelated/old-expectation",))
        self.assertEqual(checked.base_sha, "e" * 40)
        self.assertEqual(checked.workflow_run_id, 900)
        self.assertEqual(len(checked.changed_files), 2100)
        self.assertFalse(any("git/ref/heads/main" in " ".join(args) for args in backend.calls))

    def test_successful_ci_result_and_skipped_jobs(self):
        backend = FakeGitHub()
        backend.checks.append(check_item("skipping", run_id=901))
        checked = self.inspect(backend, files=())
        self.assertEqual(checked.changed_files, tuple(sorted(FILES)))
        self.assertEqual(checked.workflow_run_id, 900)

    def test_required_checks_decide_readiness_when_configured(self):
        backend = FakeGitHub()
        backend.required_checks = [check_item()]
        backend.checks = [check_item("fail", name="optional-job")]
        self.assertEqual(self.inspect(backend).workflow_run_id, 900)
        self.assertEqual(sum(args[:2] == ["pr", "checks"] for args in backend.calls), 1)
        backend.required_checks = [check_item("fail", name="required-job")]
        with self.assertRaises(ReviewCIFailedError) as raised:
            self.inspect(backend)
        self.assertIn("required-job", str(raised.exception))

    def test_no_required_checks_falls_back_to_reported_ci(self):
        backend = FakeGitHub()
        backend.required_checks = []
        self.assertEqual(self.inspect(backend).workflow_run_id, 900)
        calls = [args for args in backend.calls if args[:2] == ["pr", "checks"]]
        self.assertIn("--required", calls[0])
        self.assertNotIn("--required", calls[1])
        backend.checks = [check_item("fail")]
        with self.assertRaises(ReviewCIFailedError):
            self.inspect(backend)

    def test_failed_ci_includes_full_repair_feedback_and_logs(self):
        backend = FakeGitHub()
        backend.checks = [check_item(), check_item("fail", run_id=901, name="integration"),
                          check_item("pending", run_id=902)]
        logs = "traceback\n" + "diagnostic line\n" * 12000 + "\nAssertionError final line"
        backend.logs = result(logs, stderr="failed job stderr")
        with self.assertRaises(ReviewCIFailedError) as raised:
            self.inspect(backend)
        error = raised.exception
        self.assertTrue(error.repairable)
        self.assertEqual(error.feedback, str(error))
        for text in (logs, "failed job stderr", "integration detail", "/actions/runs/901/job/100"):
            self.assertIn(text, str(error))
        self.assertEqual([args[2] for args in backend.calls if args[:2] == ["run", "view"]], ["901"])

    def test_cancelled_ci_and_log_retrieval_failure_are_repairable(self):
        backend = FakeGitHub()
        backend.checks = [check_item("cancel")]
        backend.logs = result("no log body", stderr="log download failed", code=1)
        with self.assertRaises(ReviewCIFailedError) as raised:
            self.inspect(backend)
        self.assertIn("log download failed", str(raised.exception))
        self.assertIn("CANCEL", str(raised.exception))
        self.assertTrue(raised.exception.repairable)

    def test_external_check_failures_keep_provider_diagnostics(self):
        backend = FakeGitHub()
        backend.checks = [dict(check_item("fail"), link="https://ci.example.org/build/12")]
        with self.assertRaises(ReviewCIFailedError) as raised:
            self.inspect(backend)
        self.assertIn("https://ci.example.org/build/12", str(raised.exception))
        self.assertFalse(any(args[:2] == ["run", "view"] for args in backend.calls))
        backend.checks[0]["bucket"] = "pass"
        self.assertEqual(self.inspect(backend).workflow_run_id, 0)

    def test_pending_empty_and_all_skipped_never_pass(self):
        for checks in ([], [check_item("pending")], [check_item("skipping")]):
            backend = FakeGitHub()
            backend.checks = checks
            with self.subTest(checks=checks), self.assertRaises(ReviewPendingError):
                self.inspect(backend)

    def test_real_api_failures_preserve_stdout_and_stderr(self):
        backend = FakeGitHub()
        backend.check_error = result("API stdout", stderr="HTTP 403 rate limited", code=1)
        with self.assertRaises(CIMonitorError) as raised:
            self.inspect(backend)
        self.assertIn("API stdout", str(raised.exception))
        self.assertIn("HTTP 403 rate limited", str(raised.exception))
        self.assertFalse(raised.exception.repairable)

    def test_new_head_or_wrong_repository_during_ci_is_not_merged(self):
        for field in ("sha", "repo"):
            backend = FakeGitHub()
            backend.after_pr = copy.deepcopy(backend.pr)
            backend.after_pr["head"][field] = "f" * 40 if field == "sha" else dict(full_name="other/repo")
            with self.subTest(field=field), self.assertRaises(CIMonitorError):
                self.inspect(backend)

    def test_waits_from_pending_until_success(self):
        backend = FakeGitHub()
        backend.check_sequence = [[check_item("pending")], [check_item()]]
        checked = self.gate(backend).wait_for_draft_candidate(
            ROOT, TASK_ID, COMMIT_SHA, PR_NUMBER, PR_URL, FILES, timeout=1, interval=0.001,
        )
        self.assertEqual(checked.workflow_run_id, 900)
        self.assertEqual(sum(args[:2] == ["pr", "checks"] for args in backend.calls), 2)

    def test_timeout_reports_last_ci_output(self):
        backend = FakeGitHub()
        backend.checks = [check_item("pending", name="slow job")]
        with self.assertRaisesRegex(CIMonitorError, "Timed out.*") as raised:
            self.gate(backend).wait_for_draft_candidate(
                ROOT, TASK_ID, COMMIT_SHA, PR_NUMBER, PR_URL, FILES, timeout=0.002, interval=0.001,
            )
        self.assertIn("slow job", str(raised.exception))

    def test_stop_during_ci_wait(self):
        backend = FakeGitHub()
        gate = self.gate(backend)
        stop = threading.Event()
        with patch.object(gate, "inspect_draft_candidate", side_effect=ReviewPendingError("pending")):
            with patch.object(stop, "wait", return_value=True):
                with self.assertRaisesRegex(CIMonitorError, "Stopped"):
                    gate.wait_for_draft_candidate(
                        ROOT, TASK_ID, COMMIT_SHA, PR_NUMBER, PR_URL, FILES,
                        timeout=1, interval=0.001, stop_event=stop,
                    )


if __name__ == "__main__":
    unittest.main()
