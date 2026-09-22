"""Wait for the task PR's actual CI checks, with repair feedback on failure."""

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from uuid import UUID

from ai_task_github import (
    EXPECTED_BASE, EXPECTED_REPOSITORY, GitHubAdapter, GitHubError,
)
from ai_task_runtime import expected_branch, validate_sha


class CIMonitorError(GitHubError):
    pass


class ReviewPendingError(CIMonitorError):
    pass


class ReviewCIFailedError(CIMonitorError):
    repairable = True


@dataclass(frozen=True)
class ReviewGateResult:
    pr_number: int
    pr_url: str
    head_sha: str
    base_sha: str
    workflow_run_id: int
    changed_files: tuple[str, ...]


class ReviewMergeGate(GitHubAdapter):
    error_type = CIMonitorError
    CHECK_FIELDS = "name,state,bucket,link,workflow,description"

    @staticmethod
    def _valid_pr_number(value: int) -> bool:
        return type(value) is int and value > 0

    @staticmethod
    def _expected_pr_url(pr_number: int) -> str:
        return f"https://github.com/{EXPECTED_REPOSITORY}/pull/{pr_number}"

    def _read_pr(self, cwd: Path, pr_number: int, *, stop_event=None) -> dict:
        value = self._run_json(
            ("api", "--method", "GET", f"repos/{EXPECTED_REPOSITORY}/pulls/{pr_number}"),
            cwd=cwd, stop_event=stop_event,
        )
        if not isinstance(value, dict):
            raise self.error_type(f"Invalid PR object returned by GitHub: {value!r}")
        return value

    @staticmethod
    def _validate_pr(
        item: dict, *, task_id: UUID, commit_sha: str, pr_number: int,
        pr_url: str, expected_draft: bool,
    ) -> str:
        # Draft state and base SHA are observations, not review-policy bindings.
        base, head = item.get("base") or {}, item.get("head") or {}
        branch = expected_branch(task_id)
        if (
            item.get("number") != pr_number or item.get("html_url") != pr_url
            or item.get("state") != "open" or item.get("merged_at") is not None
            or base.get("ref") != EXPECTED_BASE or head.get("ref") != branch
            or head.get("sha") != commit_sha
            or (base.get("repo") or {}).get("full_name") != EXPECTED_REPOSITORY
            or (head.get("repo") or {}).get("full_name") != EXPECTED_REPOSITORY
        ):
            raise CIMonitorError(
                f"Expected open PR #{pr_number} ({pr_url}) for {branch}@{commit_sha} "
                f"targeting {EXPECTED_BASE}; GitHub returned: {json.dumps(item, ensure_ascii=False)}"
            )
        base_sha = base.get("sha")
        if not isinstance(base_sha, str) or re.fullmatch(r"[0-9a-fA-F]{40}", base_sha) is None:
            raise CIMonitorError(f"Invalid base commit in PR #{pr_number}: {base_sha!r}")
        return base_sha

    def _read_changed_files(self, cwd: Path, pr_number: int, *, stop_event=None) -> tuple[str, ...]:
        pages = self._run_json(
            ("api", "--method", "GET", "--paginate", "--slurp",
             f"repos/{EXPECTED_REPOSITORY}/pulls/{pr_number}/files?per_page=100"),
            cwd=cwd, stop_event=stop_event,
        )
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise CIMonitorError(f"Invalid PR file pages returned by GitHub: {pages!r}")
        filenames = []
        for page in pages:
            for item in page:
                if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                    raise CIMonitorError(f"Invalid PR file returned by GitHub: {item!r}")
                filenames.append(item["filename"])
        return tuple(sorted(set(filenames)))

    @staticmethod
    def _run_id(check: dict) -> int:
        match = re.match(
            rf"https://github\.com/{re.escape(EXPECTED_REPOSITORY)}/actions/runs/([1-9][0-9]*)(?:/|$)",
            check.get("link") or "",
        )
        return int(match.group(1)) if match else 0

    def _checks(self, cwd: Path, pr_number: int, *, stop_event=None, required=True):
        result = self._run(
            ("pr", "checks", str(pr_number), "--repo", EXPECTED_REPOSITORY,
             "--json", self.CHECK_FIELDS, *(("--required",) if required else ())),
            cwd=cwd, stop_event=stop_event,
        )
        if not result.stdout.strip() and re.search(r"no (?:required )?checks reported", result.stderr, re.I):
            if required:
                return self._checks(cwd, pr_number, stop_event=stop_event, required=False)
            raise ReviewPendingError(
                f"PR #{pr_number} has no CI checks yet", stdout=result.stdout, stderr=result.stderr,
            )
        try:
            checks = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise self._command_error(
                f"Could not read PR #{pr_number} checks (exit {result.returncode}): {exc}", result,
            ) from exc
        if result.returncode not in (0, 1, 8):
            raise self._command_error(f"PR checks exited with status {result.returncode}", result)
        if not isinstance(checks, list) or any(
            not isinstance(check, dict)
            or check.get("bucket") not in {"pass", "fail", "pending", "skipping", "cancel"}
            for check in checks
        ):
            raise self._command_error("GitHub returned invalid PR checks", result)
        if not checks and required and result.returncode == 0:
            return self._checks(cwd, pr_number, stop_event=stop_event, required=False)
        return checks, result

    def _read_checks(self, cwd: Path, pr_number: int, *, stop_event=None) -> int:
        checks, result = self._checks(cwd, pr_number, stop_event=stop_event)
        failed = [check for check in checks if check["bucket"] in {"fail", "cancel"}]
        if failed:
            details = [f"CI failed for PR #{pr_number}:"]
            for check in failed:
                details.append(
                    f"{check.get('workflow', '')} / {check.get('name', '')}: "
                    f"{check.get('state', check['bucket'])}\n"
                    f"{check.get('description', '')}\n{check.get('link', '')}"
                )
            for run_id in sorted({self._run_id(check) for check in failed} - {0}):
                try:
                    logs = self._run(
                        ("run", "view", str(run_id), "--repo", EXPECTED_REPOSITORY, "--log-failed"),
                        cwd=cwd, timeout=120, stop_event=stop_event,
                    )
                except CIMonitorError as exc:
                    if stop_event is not None and stop_event.is_set():
                        raise
                    details.append(f"Failed to retrieve logs for run {run_id}: {exc}")
                else:
                    details.append(
                        f"Run {run_id} failed-job logs (exit {logs.returncode}):\n"
                        f"stdout:\n{logs.stdout}\nstderr:\n{logs.stderr}"
                    )
            raise ReviewCIFailedError(
                "\n".join(details), stdout=result.stdout, stderr=result.stderr,
                returncode=result.returncode,
            )
        if result.returncode == 1:
            raise self._command_error("GitHub could not inspect PR checks (exit 1)", result)
        if (
            not checks or result.returncode == 8
            or any(check["bucket"] == "pending" for check in checks)
            or not any(check["bucket"] == "pass" for check in checks)
        ):
            raise ReviewPendingError(
                f"Waiting for successful CI on PR #{pr_number}",
                stdout=result.stdout, stderr=result.stderr, returncode=result.returncode,
            )
        # External check providers have no Actions run ID; zero means unavailable.
        return max((self._run_id(check) for check in checks if check["bucket"] == "pass"), default=0)

    def inspect_candidate(
        self, cwd: Path, task_id: UUID, commit_sha: str, pr_number: int,
        pr_url: str, expected_files: Sequence[str], *, expected_draft: bool,
        stop_event=None,
    ) -> ReviewGateResult:
        if not isinstance(task_id, UUID):
            raise CIMonitorError("task ID must be UUID")
        validate_sha(commit_sha)
        if not self._valid_pr_number(pr_number) or pr_url != self._expected_pr_url(pr_number):
            raise CIMonitorError(f"Invalid task PR identity: #{pr_number} {pr_url!r}")
        identity = dict(task_id=task_id, commit_sha=commit_sha, pr_number=pr_number,
                        pr_url=pr_url, expected_draft=expected_draft)
        self._validate_pr(self._read_pr(cwd, pr_number, stop_event=stop_event), **identity)
        workflow_run_id = self._read_checks(cwd, pr_number, stop_event=stop_event)
        actual_files = self._read_changed_files(cwd, pr_number, stop_event=stop_event)
        base_sha = self._validate_pr(
            self._read_pr(cwd, pr_number, stop_event=stop_event), **identity,
        )
        return ReviewGateResult(pr_number, pr_url, commit_sha, base_sha, workflow_run_id, actual_files)

    def wait_for_draft_candidate(
        self, cwd: Path, task_id: UUID, commit_sha: str, pr_number: int,
        pr_url: str, expected_files: Sequence[str], *, timeout: float,
        interval: float, stop_event=None,
    ) -> ReviewGateResult:
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0
            for value in (timeout, interval)
        ):
            raise CIMonitorError("CI timeout and interval must be finite positive numbers")
        deadline = time.monotonic() + timeout
        while True:
            if stop_event is not None and stop_event.is_set():
                raise CIMonitorError("Stopped while waiting for CI")
            try:
                return self.inspect_draft_candidate(
                    cwd, task_id, commit_sha, pr_number, pr_url, expected_files, stop_event=stop_event,
                )
            except ReviewPendingError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CIMonitorError(f"Timed out waiting for CI: {exc}") from exc
                delay = min(interval, remaining)
                if stop_event is not None:
                    if stop_event.wait(delay):
                        raise CIMonitorError(f"Stopped while waiting for CI: {exc}") from exc
                else:
                    time.sleep(delay)

    def inspect_draft_candidate(
        self, cwd: Path, task_id: UUID, commit_sha: str, pr_number: int,
        pr_url: str, expected_files: Sequence[str], *, stop_event=None,
    ) -> ReviewGateResult:
        return self.inspect_candidate(
            cwd, task_id, commit_sha, pr_number, pr_url, expected_files,
            expected_draft=True, stop_event=stop_event,
        )
