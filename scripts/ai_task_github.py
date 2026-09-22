"""GitHub CLI operations for task pull requests."""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from uuid import UUID

from ai_task_diagnostics import redact_secrets
from ai_task_process import communicate_bounded, managed_process_options
from ai_task_runtime import expected_branch, validate_sha


EXPECTED_REPOSITORY = "U-KID-AI/ichiyon-robot"
EXPECTED_BASE = "main"
PR_URL_PATTERN = re.compile(
    r"^https://github\.com/U-KID-AI/ichiyon-robot/pull/([1-9][0-9]*)$"
)


def output_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


class GitHubError(RuntimeError):
    """GitHub operation failure with complete, redacted diagnostics."""

    repairable = False

    def __init__(self, message: str, *, stdout="", stderr="", returncode=None):
        self.stdout = redact_secrets(output_text(stdout))
        self.stderr = redact_secrets(output_text(stderr))
        self.returncode = returncode
        details = redact_secrets(message)
        if self.stdout:
            details += f"\nstdout:\n{self.stdout}"
        if self.stderr:
            details += f"\nstderr:\n{self.stderr}"
        super().__init__(details)
        self.feedback = details


@dataclass(frozen=True)
class DraftPullRequest:
    number: int
    url: str
    head_sha: str


class GitHubAdapter:
    JSON_FIELDS = "number,url,isDraft,baseRefName,headRefName,headRefOid,state"
    error_type = GitHubError

    def __init__(
        self,
        gh_path: Path,
        *,
        runner: Callable[..., object] | None = None,
        popen: Callable[..., object] | None = None,
    ) -> None:
        self.gh_path = gh_path.resolve()
        if not self.gh_path.is_file():
            raise self.error_type(f"GitHub CLI executable not found: {self.gh_path}")
        self._runner = runner or subprocess.run
        self._popen = popen or subprocess.Popen

    @staticmethod
    def _expected_title(task_id: UUID) -> str:
        return f"chore(ai): task {task_id}"

    @staticmethod
    def _expected_body(task_id: UUID, commit_sha: str) -> str:
        return f"Automated AI task {task_id}.\n\nCommit: {commit_sha}\n"

    @staticmethod
    def _environment() -> dict[str, str]:
        environment = os.environ.copy()
        environment.update({
            "GH_HOST": "github.com",
            "GH_PROMPT_DISABLED": "1",
            "GH_PAGER": "cat",
            "PAGER": "cat",
            "NO_COLOR": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
        })
        return environment

    def _command_error(self, message, result):
        return self.error_type(
            message,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 60,
        stop_event=None,
    ):
        argv = [str(self.gh_path), *args]
        operation = "gh " + " ".join(args)
        if stop_event is not None and stop_event.is_set():
            raise self.error_type(f"Stopped before {operation}")
        options = dict(cwd=str(cwd.resolve()), shell=False, env=self._environment())
        try:
            if stop_event is None:
                return self._runner(
                    argv, **options, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout, check=False,
                )
            process = self._popen(
                argv, **options, **managed_process_options(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            # Keep shared process-tree cancellation handling without clipping logs.
            try:
                result = communicate_bounded(
                    process, input_text=None, timeout=timeout,
                    max_output_bytes=sys.maxsize, stop_event=stop_event,
                )
            finally:
                for name in ("stdin", "stdout", "stderr"):
                    stream = getattr(process, name, None)
                    if stream is not None:
                        stream.close()
        except subprocess.TimeoutExpired as exc:
            raise self.error_type(
                f"Timed out after {timeout}s: {operation}",
                stdout=exc.stdout, stderr=exc.stderr,
            ) from exc
        except OSError as exc:
            raise self.error_type(f"Could not execute {operation}: {exc}") from exc
        if result.stopped:
            raise self._command_error(f"Stopped: {operation}", result)
        if result.timed_out:
            raise self._command_error(f"Timed out after {timeout}s: {operation}", result)
        if result.stdin_cleanup_failed:
            raise self._command_error(f"Process cleanup failed: {operation}", result)
        return result

    def _run_json(self, args: Sequence[str], *, cwd: Path, stop_event=None):
        result = self._run(args, cwd=cwd, stop_event=stop_event)
        operation = "gh " + " ".join(args)
        if result.returncode != 0:
            raise self._command_error(
                f"{operation} exited with status {result.returncode}", result,
            )
        try:
            return json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise self._command_error(f"Invalid JSON from {operation}: {exc}", result) from exc

    def _list_open(self, cwd: Path, branch: str, *, stop_event=None) -> list[dict]:
        value = self._run_json(
            ("pr", "list", "--repo", EXPECTED_REPOSITORY, "--state", "open",
             "--head", branch, "--limit", "2", "--json", self.JSON_FIELDS),
            cwd=cwd, stop_event=stop_event,
        )
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise self.error_type(f"Invalid PR list returned by GitHub: {value!r}")
        return value

    @staticmethod
    def _validate_pr(item: dict, *, task_id: UUID, commit_sha: str) -> DraftPullRequest:
        number, url = item.get("number"), item.get("url")
        branch = expected_branch(task_id)
        if (
            type(number) is not int or number <= 0
            or url != f"https://github.com/{EXPECTED_REPOSITORY}/pull/{number}"
            or item.get("baseRefName") != EXPECTED_BASE
            or item.get("headRefName") != branch
            or item.get("headRefOid") != commit_sha
            or item.get("state") != "OPEN"
        ):
            raise GitHubError(
                f"Expected an open {branch}@{commit_sha} PR targeting {EXPECTED_BASE}; "
                f"GitHub returned: {json.dumps(item, ensure_ascii=False)}"
            )
        return DraftPullRequest(number, url, commit_sha)

    def ensure_draft_pr(
        self, cwd: Path, task_id: UUID, commit_sha: str, *, stop_event=None,
    ) -> DraftPullRequest:
        """Create or reuse an ordinary open PR; keep the legacy method name."""
        if not isinstance(task_id, UUID):
            raise GitHubError("task ID must be UUID")
        validate_sha(commit_sha)
        branch = expected_branch(task_id)
        existing = self._list_open(cwd, branch, stop_event=stop_event)
        if existing:
            if len(existing) != 1:
                raise GitHubError(f"Multiple open PRs for {branch}: {existing!r}")
            return self._validate_pr(existing[0], task_id=task_id, commit_sha=commit_sha)
        created = self._run(
            ("pr", "create", "--repo", EXPECTED_REPOSITORY, "--base", EXPECTED_BASE,
             "--head", branch, "--title", self._expected_title(task_id),
             "--body", self._expected_body(task_id, commit_sha)),
            cwd=cwd, timeout=120, stop_event=stop_event,
        )
        # A disconnected create may have succeeded; confirm the task PR before retrying.
        try:
            after = self._list_open(cwd, branch, stop_event=stop_event)
            if len(after) != 1:
                raise GitHubError(f"Expected one open PR for {branch}, got {after!r}")
            return self._validate_pr(after[0], task_id=task_id, commit_sha=commit_sha)
        except GitHubError as exc:
            raise self._command_error(
                f"PR creation could not be confirmed (exit {created.returncode}): {exc}",
                created,
            ) from exc
