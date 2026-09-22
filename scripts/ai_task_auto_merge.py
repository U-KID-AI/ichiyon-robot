"""Squash-merge the task PR after its actual CI checks succeed."""

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from uuid import UUID

from ai_task_github import GitHubAdapter, GitHubError
from ai_task_review_merge import EXPECTED_BASE, EXPECTED_REPOSITORY, ReviewMergeGate
from ai_task_runtime import expected_branch, validate_sha


class AutoMergeError(GitHubError):
    pass


class MergeOutcomeUnknownError(AutoMergeError):
    """Reconcile the same PR before retrying implementation or publication."""

    merge_outcome_unknown = True


class _UnmergedPRError(AutoMergeError):
    """GitHub returned a valid task PR that has not been merged."""


@dataclass(frozen=True)
class AutoMergeResult:
    pr_number: int
    pr_url: str
    head_sha: str
    base_sha: str
    workflow_run_id: int
    merge_sha: str


class AutoMergeAdapter(GitHubAdapter):
    error_type = AutoMergeError

    def __init__(
        self, gh_path: Path, *, gate: ReviewMergeGate | None = None,
        runner: Callable[..., object] | None = None, popen: Callable[..., object] | None = None,
    ) -> None:
        super().__init__(gh_path, runner=runner, popen=popen)
        self.gate = gate or ReviewMergeGate(gh_path, runner=runner, popen=popen)

    def _read_pr(self, cwd: Path, pr_number: int, *, stop_event=None) -> dict:
        return ReviewMergeGate._read_pr(self, cwd, pr_number, stop_event=stop_event)

    @staticmethod
    def _validate_merged_pr(
        item: dict, *, task_id: UUID, commit_sha: str, pr_number: int,
        pr_url: str, base_sha: str,
    ) -> str:
        base, head = item.get("base") or {}, item.get("head") or {}
        merge_sha = item.get("merge_commit_sha")
        if (
            item.get("number") != pr_number or item.get("html_url") != pr_url
            or not isinstance(base, dict) or not isinstance(head, dict)
            or base.get("ref") != EXPECTED_BASE
            or head.get("ref") != expected_branch(task_id) or head.get("sha") != commit_sha
            or not isinstance(base.get("repo"), dict) or not isinstance(head.get("repo"), dict)
            or (base.get("repo") or {}).get("full_name") != EXPECTED_REPOSITORY
            or (head.get("repo") or {}).get("full_name") != EXPECTED_REPOSITORY
            or not isinstance(base.get("sha"), str)
            or re.fullmatch(r"[0-9a-fA-F]{40}", base["sha"]) is None
        ):
            raise AutoMergeError(
                f"Merge of task PR #{pr_number} at {commit_sha} was not confirmed; "
                f"GitHub returned: {json.dumps(item, ensure_ascii=False)}"
            )
        if item.get("state") in ("open", "closed") and "merged_at" in item and item["merged_at"] is None:
            raise _UnmergedPRError(
                f"GitHub confirms task PR #{pr_number} is not merged: {json.dumps(item, ensure_ascii=False)}"
            )
        if (
            item.get("state") != "closed"
            or not isinstance(item.get("merged_at"), str) or not item["merged_at"]
            or not isinstance(merge_sha, str) or re.fullmatch(r"[0-9a-fA-F]{40}", merge_sha) is None
        ):
            raise AutoMergeError(
                f"Invalid merge metadata for task PR #{pr_number}: {json.dumps(item, ensure_ascii=False)}"
            )
        return merge_sha

    def ready_and_squash_merge(
        self, cwd: Path, task_id: UUID, commit_sha: str, pr_number: int,
        pr_url: str, expected_files: Sequence[str], *, stop_event=None,
    ) -> AutoMergeResult:
        if not isinstance(task_id, UUID):
            raise AutoMergeError("task ID must be UUID")
        validate_sha(commit_sha)
        if (
            not ReviewMergeGate._valid_pr_number(pr_number)
            or pr_url != ReviewMergeGate._expected_pr_url(pr_number)
        ):
            raise AutoMergeError(f"Invalid task PR identity: #{pr_number} {pr_url!r}")
        identity = dict(task_id=task_id, commit_sha=commit_sha, pr_number=pr_number, pr_url=pr_url)
        current = self._read_pr(cwd, pr_number, stop_event=stop_event)
        if current.get("merged_at"):
            merge_sha = self._validate_merged_pr(current, **identity, base_sha="")
            # A retry after a lost merge response must not rerun the merge or gate.
            return AutoMergeResult(
                pr_number, pr_url, commit_sha, current["base"]["sha"], 0, merge_sha,
            )
        ReviewMergeGate._validate_pr(current, **identity, expected_draft=False)
        if current.get("draft"):
            ready = self._run(
                ("pr", "ready", str(pr_number), "--repo", EXPECTED_REPOSITORY),
                cwd=cwd, stop_event=stop_event,
            )
            try:
                current = self._read_pr(cwd, pr_number, stop_event=stop_event)
                ReviewMergeGate._validate_pr(current, **identity, expected_draft=False)
                if current.get("draft") is not False:
                    raise AutoMergeError(f"PR #{pr_number} is still a draft")
            except GitHubError as exc:
                raise self._command_error(
                    f"Could not mark PR #{pr_number} ready (exit {ready.returncode}): {exc}", ready,
                ) from exc
        # Ready events may start CI. Preserve repairable CI exceptions for the runner.
        checked = self.gate.wait_for_draft_candidate(
            cwd, task_id, commit_sha, pr_number, pr_url, expected_files,
            timeout=900, interval=5, stop_event=stop_event,
        )
        try:
            merge = self._run(
                ("pr", "merge", str(pr_number), "--repo", EXPECTED_REPOSITORY,
                 "--squash", "--match-head-commit", commit_sha),
                cwd=cwd, timeout=120, stop_event=stop_event,
            )
        except GitHubError as exc:
            if stop_event is not None and stop_event.is_set():
                raise
            command_diagnostics = str(exc)
        else:
            command_diagnostics = str(self._command_error(
                f"Merge command exited with status {merge.returncode}", merge,
            ))
        # The request may have reached GitHub even if the CLI timed out. Retry only
        # confirmation, never the mutation or implementation, after that point.
        failures = []
        for attempt in range(3):
            try:
                final_pr = self._read_pr(cwd, pr_number, stop_event=stop_event)
                merge_sha = self._validate_merged_pr(final_pr, **identity, base_sha=checked.base_sha)
                return AutoMergeResult(
                    pr_number, pr_url, commit_sha, final_pr["base"]["sha"],
                    checked.workflow_run_id, merge_sha,
                )
            except GitHubError as exc:
                failures.append(f"Confirmation attempt {attempt + 1}: {exc}")
                if stop_event is not None and stop_event.is_set():
                    raise AutoMergeError(
                        f"Stopped confirming merge of PR #{pr_number}: {exc}\n{command_diagnostics}"
                    ) from exc
                if attempt == 2:
                    if isinstance(exc, _UnmergedPRError):
                        raise AutoMergeError(
                            f"Squash merge of PR #{pr_number} failed; GitHub confirms it is not merged.\n"
                            + command_diagnostics + "\n" + "\n".join(failures)
                        ) from exc
                    error = MergeOutcomeUnknownError(
                        f"Squash merge outcome for PR #{pr_number} is unconfirmed after 3 reads. "
                        "Reconcile this PR before retrying Codex or creating another PR.\n"
                        + command_diagnostics + "\n" + "\n".join(failures)
                    )
                    error.pr_number = pr_number
                    error.pr_url = pr_url
                    error.head_sha = commit_sha
                    raise error from exc
            delay = attempt + 1
            if stop_event is not None:
                if stop_event.wait(delay):
                    raise AutoMergeError(
                        f"Stopped confirming merge of PR #{pr_number}\n{command_diagnostics}\n"
                        + "\n".join(failures)
                    )
            else:
                time.sleep(delay)
