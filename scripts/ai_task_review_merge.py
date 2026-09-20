"""Read-only automatic review/merge gate for Phase 2D."""

import json
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from uuid import UUID

from ai_task_process import communicate_bounded
from ai_task_safety import (
    expected_branch,
    is_reparse_point,
    validate_sha,
)


EXPECTED_REPOSITORY = "U-KID-AI/ichiyon-robot"
EXPECTED_BASE = "main"
EXPECTED_WORKFLOW_NAME = "checks"
EXPECTED_WORKFLOW_PATH = ".github/workflows/checks.yml"

MAX_GH_OUTPUT_BYTES = 128 * 1024
MAX_EXPECTED_FILES = 100
MAX_WORKFLOW_RUNS = 10

PR_URL_PATTERN = re.compile(
    r"^https://github\.com/U-KID-AI/ichiyon-robot/pull/([1-9][0-9]*)$"
)


class ReviewMergeSafetyError(RuntimeError):
    pass


class ReviewPendingError(ReviewMergeSafetyError):
    pass


class ReviewCIFailedError(ReviewMergeSafetyError):
    pass


@dataclass(frozen=True)
class ReviewGateResult:
    pr_number: int
    pr_url: str
    head_sha: str
    base_sha: str
    workflow_run_id: int
    changed_files: tuple[str, ...]


class ReviewMergeGate:
    def __init__(
        self,
        gh_path: Path,
        *,
        runner: Callable[..., object] | None = None,
        popen: Callable[..., object] | None = None,
    ) -> None:
        resolved = gh_path.resolve()

        if (
            not resolved.is_absolute()
            or not resolved.is_file()
            or resolved.is_symlink()
            or is_reparse_point(resolved)
        ):
            raise ReviewMergeSafetyError(
                "GitHub CLI executable is unsafe"
            )

        self.gh_path = resolved
        self._runner = runner or subprocess.run
        self._popen = popen or subprocess.Popen

    @staticmethod
    def _valid_pr_number(value: int) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 < value <= 2_147_483_647
        )

    @staticmethod
    def _safe_expected_files(
        values: Sequence[str],
    ) -> tuple[str, ...]:
        if (
            isinstance(values, (str, bytes))
            or len(values) < 1
            or len(values) > MAX_EXPECTED_FILES
        ):
            raise ReviewMergeSafetyError(
                "invalid expected changed files"
            )

        cleaned = []

        for value in values:
            if (
                not isinstance(value, str)
                or not value
                or "\0" in value
                or "\\" in value
                or value.startswith("/")
                or value.startswith("-")
                or ".." in value.split("/")
            ):
                raise ReviewMergeSafetyError(
                    "unsafe expected changed file"
                )

            cleaned.append(value)

        if len(set(cleaned)) != len(cleaned):
            raise ReviewMergeSafetyError(
                "duplicate expected changed file"
            )

        return tuple(sorted(cleaned))

    @staticmethod
    def _environment() -> dict[str, str]:
        allowed = {
            "APPDATA",
            "COMSPEC",
            "HOME",
            "HOMEDRIVE",
            "HOMEPATH",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "USERDOMAIN",
            "USERNAME",
            "USERPROFILE",
            "WINDIR",
        }

        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in allowed
        }

        environment.update(
            {
                "GH_HOST": "github.com",
                "GH_PROMPT_DISABLED": "1",
                "GH_PAGER": "cat",
                "PAGER": "cat",
                "NO_COLOR": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
            }
        )

        return environment

    @classmethod
    def _is_allowed_argv(
        cls,
        args: tuple[str, ...],
    ) -> bool:
        prefix = (
            "api",
            "--method",
            "GET",
        )

        if args[:3] != prefix:
            return False

        if len(args) == 4:
            endpoint = args[3]

            if re.fullmatch(
                r"repos/U-KID-AI/ichiyon-robot/pulls/[1-9][0-9]*",
                endpoint,
            ):
                return True

            if (
                endpoint
                == (
                    "repos/U-KID-AI/ichiyon-robot/"
                    "git/ref/heads/main"
                )
            ):
                return True

        if len(args) == 8:
            endpoint = args[3]

            if (
                re.fullmatch(
                    r"repos/U-KID-AI/ichiyon-robot/"
                    r"pulls/[1-9][0-9]*/files",
                    endpoint,
                )
                and args[4:6] == ("-f", "per_page=100")
                and args[6] == "-f"
                and re.fullmatch(
                    r"page=[12]",
                    args[7],
                )
            ):
                return True

        if len(args) == 10:
            if (
                args[3]
                == (
                    "repos/U-KID-AI/ichiyon-robot/"
                    "actions/runs"
                )
                and args[4:6]
                == ("-f", "event=pull_request")
                and args[6] == "-f"
                and re.fullmatch(
                    r"head_sha=[0-9a-fA-F]{40}",
                    args[7],
                )
                and args[8:10]
                == ("-f", "per_page=10")
            ):
                return True

        return False

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 60,
        stop_event=None,
    ):
        values = tuple(args)

        if not self._is_allowed_argv(values):
            raise ReviewMergeSafetyError(
                "GitHub review operation is not allowlisted"
            )

        argv = [
            str(self.gh_path),
            *values,
        ]

        environment = self._environment()

        if stop_event is None:
            result = self._runner(
                argv,
                cwd=str(cwd.resolve()),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=environment,
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""

            output_size = (
                len(
                    stdout.encode(
                        "utf-8",
                        errors="replace",
                    )
                )
                + len(
                    stderr.encode(
                        "utf-8",
                        errors="replace",
                    )
                )
            )

            if output_size > MAX_GH_OUTPUT_BYTES:
                raise ReviewMergeSafetyError(
                    "GitHub review output is too large"
                )

            return result

        if stop_event.is_set():
            raise ReviewMergeSafetyError(
                "GitHub review refused after lease loss"
            )

        process = self._popen(
            argv,
            cwd=str(cwd.resolve()),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )

        result = communicate_bounded(
            process,
            input_text=None,
            timeout=timeout,
            max_output_bytes=MAX_GH_OUTPUT_BYTES,
            stop_event=stop_event,
        )

        if result.stopped:
            raise ReviewMergeSafetyError(
                "GitHub review stopped after lease loss"
            )

        if result.timed_out:
            raise ReviewMergeSafetyError(
                "GitHub review timed out"
            )

        if result.stdin_cleanup_failed:
            raise ReviewMergeSafetyError(
                "GitHub review process cleanup failed"
            )

        return result

    def _run_json(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        stop_event=None,
    ):
        result = self._run(
            args,
            cwd=cwd,
            stop_event=stop_event,
        )

        if result.returncode != 0:
            raise ReviewMergeSafetyError(
                "GitHub review inspection failed"
            )

        try:
            return json.loads(result.stdout)
        except (
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise ReviewMergeSafetyError(
                "GitHub returned invalid review JSON"
            ) from exc

    @staticmethod
    def _expected_pr_url(
        pr_number: int,
    ) -> str:
        return (
            "https://github.com/"
            "U-KID-AI/ichiyon-robot/pull/"
            f"{pr_number}"
        )

    def _read_pr(
        self,
        cwd: Path,
        pr_number: int,
        *,
        stop_event=None,
    ) -> dict:
        value = self._run_json(
            (
                "api",
                "--method",
                "GET",
                (
                    "repos/U-KID-AI/ichiyon-robot/"
                    f"pulls/{pr_number}"
                ),
            ),
            cwd=cwd,
            stop_event=stop_event,
        )

        if not isinstance(value, dict):
            raise ReviewMergeSafetyError(
                "GitHub returned invalid PR object"
            )

        return value

    @staticmethod
    def _validate_pr(
        item: dict,
        *,
        task_id: UUID,
        commit_sha: str,
        pr_number: int,
        pr_url: str,
        expected_draft: bool,
    ) -> str:
        branch = expected_branch(task_id)

        base = item.get("base")
        head = item.get("head")

        mergeable = item.get("mergeable")

        if mergeable is None:
            raise ReviewPendingError(
                "GitHub mergeability is still pending"
            )

        if mergeable is not True:
            raise ReviewMergeSafetyError(
                "PR is not mergeable"
            )

        if (
            item.get("number") != pr_number
            or item.get("html_url") != pr_url
            or item.get("state") != "open"
            or item.get("draft") is not expected_draft
            or item.get("merged_at") is not None
            or not isinstance(base, dict)
            or base.get("ref") != EXPECTED_BASE
            or not isinstance(head, dict)
            or head.get("ref") != branch
            or head.get("sha") != commit_sha
        ):
            raise ReviewMergeSafetyError(
                "Draft PR review metadata mismatch"
            )

        base_sha = base.get("sha")

        if (
            not isinstance(base_sha, str)
            or re.fullmatch(
                r"[0-9a-fA-F]{40}",
                base_sha,
            )
            is None
        ):
            raise ReviewMergeSafetyError(
                "Draft PR base SHA is invalid"
            )

        base_repo = base.get("repo")
        head_repo = head.get("repo")

        if (
            not isinstance(base_repo, dict)
            or base_repo.get("full_name")
            != EXPECTED_REPOSITORY
            or not isinstance(head_repo, dict)
            or head_repo.get("full_name")
            != EXPECTED_REPOSITORY
        ):
            raise ReviewMergeSafetyError(
                "Draft PR repository mismatch"
            )

        return base_sha

    def _read_main_sha(
        self,
        cwd: Path,
        *,
        stop_event=None,
    ) -> str:
        value = self._run_json(
            (
                "api",
                "--method",
                "GET",
                (
                    "repos/U-KID-AI/ichiyon-robot/"
                    "git/ref/heads/main"
                ),
            ),
            cwd=cwd,
            stop_event=stop_event,
        )

        if (
            not isinstance(value, dict)
            or value.get("ref") != "refs/heads/main"
        ):
            raise ReviewMergeSafetyError(
                "GitHub main ref is invalid"
            )

        obj = value.get("object")

        if (
            not isinstance(obj, dict)
            or obj.get("type") != "commit"
        ):
            raise ReviewMergeSafetyError(
                "GitHub main ref object is invalid"
            )

        sha = obj.get("sha")

        if (
            not isinstance(sha, str)
            or re.fullmatch(
                r"[0-9a-fA-F]{40}",
                sha,
            )
            is None
        ):
            raise ReviewMergeSafetyError(
                "GitHub main ref SHA is invalid"
            )

        return sha

    def _read_changed_files(
        self,
        cwd: Path,
        pr_number: int,
        *,
        stop_event=None,
    ) -> tuple[str, ...]:
        collected = []

        for page in (1, 2):
            value = self._run_json(
                (
                    "api",
                    "--method",
                    "GET",
                    (
                        "repos/U-KID-AI/ichiyon-robot/"
                        f"pulls/{pr_number}/files"
                    ),
                    "-f",
                    "per_page=100",
                    "-f",
                    f"page={page}",
                ),
                cwd=cwd,
                stop_event=stop_event,
            )

            if not isinstance(value, list):
                raise ReviewMergeSafetyError(
                    "GitHub returned invalid PR files"
                )

            if page == 2 and value:
                raise ReviewMergeSafetyError(
                    "PR changed-file count exceeds limit"
                )

            for item in value:
                if not isinstance(item, dict):
                    raise ReviewMergeSafetyError(
                        "GitHub returned invalid PR file"
                    )

                filename = item.get("filename")

                if (
                    not isinstance(filename, str)
                    or not filename
                ):
                    raise ReviewMergeSafetyError(
                        "GitHub PR filename is invalid"
                    )

                collected.append(filename)

        if (
            not collected
            or len(collected) > MAX_EXPECTED_FILES
            or len(set(collected)) != len(collected)
        ):
            raise ReviewMergeSafetyError(
                "GitHub PR changed-file set is invalid"
            )

        return tuple(sorted(collected))

    def _read_workflow_run(
        self,
        cwd: Path,
        *,
        task_id: UUID,
        commit_sha: str,
        pr_number: int,
        base_sha: str,
        stop_event=None,
    ) -> int:
        value = self._run_json(
            (
                "api",
                "--method",
                "GET",
                (
                    "repos/U-KID-AI/ichiyon-robot/"
                    "actions/runs"
                ),
                "-f",
                "event=pull_request",
                "-f",
                f"head_sha={commit_sha}",
                "-f",
                "per_page=10",
            ),
            cwd=cwd,
            stop_event=stop_event,
        )

        if not isinstance(value, dict):
            raise ReviewMergeSafetyError(
                "GitHub returned invalid workflow data"
            )

        runs = value.get("workflow_runs")

        if (
            not isinstance(runs, list)
            or len(runs) > MAX_WORKFLOW_RUNS
        ):
            raise ReviewMergeSafetyError(
                "GitHub workflow runs are invalid"
            )

        exact = []

        for run in runs:
            if not isinstance(run, dict):
                raise ReviewMergeSafetyError(
                    "GitHub workflow run is invalid"
                )

            if (
                run.get("name") != EXPECTED_WORKFLOW_NAME
                or run.get("path") != EXPECTED_WORKFLOW_PATH
                or run.get("event") != "pull_request"
                or run.get("head_sha") != commit_sha
                or run.get("head_branch")
                != expected_branch(task_id)
            ):
                continue

            pull_requests = run.get("pull_requests")

            if not isinstance(pull_requests, list):
                continue

            if not any(
                isinstance(item, dict)
                and item.get("number") == pr_number
                and isinstance(item.get("base"), dict)
                and item["base"].get("ref")
                == EXPECTED_BASE
                and item["base"].get("sha")
                == base_sha
                and isinstance(item.get("head"), dict)
                and item["head"].get("ref")
                == expected_branch(task_id)
                and item["head"].get("sha")
                == commit_sha
                for item in pull_requests
            ):
                continue

            run_id = run.get("id")

            if (
                not isinstance(run_id, int)
                or isinstance(run_id, bool)
                or run_id <= 0
            ):
                raise ReviewMergeSafetyError(
                    "GitHub workflow run ID is invalid"
                )

            exact.append(run)

        if not exact:
            raise ReviewPendingError(
                "exact CI workflow run is not available yet"
            )

        latest = max(
            exact,
            key=lambda item: item["id"],
        )

        if latest.get("status") != "completed":
            raise ReviewPendingError(
                "latest exact CI workflow is still running"
            )

        if latest.get("conclusion") != "success":
            raise ReviewCIFailedError(
                "latest exact CI workflow failed"
            )

        return latest["id"]

    def inspect_candidate(
        self,
        cwd: Path,
        task_id: UUID,
        commit_sha: str,
        pr_number: int,
        pr_url: str,
        expected_files: Sequence[str],
        *,
        expected_draft: bool,
        stop_event=None,
    ) -> ReviewGateResult:
        if not isinstance(task_id, UUID):
            raise ReviewMergeSafetyError(
                "task ID must be UUID"
            )

        if not isinstance(expected_draft, bool):
            raise ReviewMergeSafetyError(
                "expected draft state must be boolean"
            )

        validate_sha(commit_sha)

        if not self._valid_pr_number(pr_number):
            raise ReviewMergeSafetyError(
                "invalid PR number"
            )

        if not isinstance(pr_url, str):
            raise ReviewMergeSafetyError(
                "invalid PR URL"
            )

        match = PR_URL_PATTERN.fullmatch(pr_url)

        if (
            match is None
            or int(match.group(1)) != pr_number
            or pr_url
            != self._expected_pr_url(pr_number)
        ):
            raise ReviewMergeSafetyError(
                "PR URL mismatch"
            )

        expected = self._safe_expected_files(
            expected_files
        )

        before = self._read_pr(
            cwd,
            pr_number,
            stop_event=stop_event,
        )

        base_sha = self._validate_pr(
            before,
            task_id=task_id,
            commit_sha=commit_sha,
            pr_number=pr_number,
            pr_url=pr_url,
            expected_draft=expected_draft,
        )

        main_before = self._read_main_sha(
            cwd,
            stop_event=stop_event,
        )

        if main_before != base_sha:
            raise ReviewMergeSafetyError(
                "PR base is not current main"
            )

        actual_files = self._read_changed_files(
            cwd,
            pr_number,
            stop_event=stop_event,
        )

        if actual_files != expected:
            raise ReviewMergeSafetyError(
                "PR changed files differ from tested files"
            )

        workflow_run_id = self._read_workflow_run(
            cwd,
            task_id=task_id,
            commit_sha=commit_sha,
            pr_number=pr_number,
            base_sha=base_sha,
            stop_event=stop_event,
        )

        after = self._read_pr(
            cwd,
            pr_number,
            stop_event=stop_event,
        )

        after_base_sha = self._validate_pr(
            after,
            task_id=task_id,
            commit_sha=commit_sha,
            pr_number=pr_number,
            pr_url=pr_url,
            expected_draft=expected_draft,
        )

        main_after = self._read_main_sha(
            cwd,
            stop_event=stop_event,
        )

        # GitHub may update volatile PR fields asynchronously while
        # this gate is running. The second _validate_pr() call above
        # revalidates every security-critical PR binding, so full object
        # equality is neither required nor stable.
        if (
            after_base_sha != base_sha
            or main_after != main_before
            or main_after != base_sha
        ):
            raise ReviewMergeSafetyError(
                "validated PR binding or main changed during review gate"
            )

        return ReviewGateResult(
            pr_number=pr_number,
            pr_url=pr_url,
            head_sha=commit_sha,
            base_sha=base_sha,
            workflow_run_id=workflow_run_id,
            changed_files=actual_files,
        )

    def wait_for_draft_candidate(
        self,
        cwd: Path,
        task_id: UUID,
        commit_sha: str,
        pr_number: int,
        pr_url: str,
        expected_files: Sequence[str],
        *,
        timeout: float,
        interval: float,
        stop_event=None,
    ) -> ReviewGateResult:
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
            or not isinstance(interval, (int, float))
            or isinstance(interval, bool)
            or not math.isfinite(interval)
            or interval <= 0
        ):
            raise ReviewMergeSafetyError(
                "invalid CI wait limits"
            )

        deadline = time.monotonic() + float(timeout)

        while True:
            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                raise ReviewMergeSafetyError(
                    "CI wait refused after lease loss"
                )

            try:
                return self.inspect_draft_candidate(
                    cwd,
                    task_id,
                    commit_sha,
                    pr_number,
                    pr_url,
                    expected_files,
                    stop_event=stop_event,
                )
            except ReviewPendingError:
                remaining = (
                    deadline - time.monotonic()
                )

                if remaining <= 0:
                    raise ReviewMergeSafetyError(
                        "timed out waiting for exact CI"
                    )

                delay = min(
                    float(interval),
                    remaining,
                )

                if stop_event is not None:
                    if stop_event.wait(delay):
                        raise ReviewMergeSafetyError(
                            "lease lost while waiting for CI"
                        )
                else:
                    time.sleep(delay)

    def inspect_draft_candidate(
        self,
        cwd: Path,
        task_id: UUID,
        commit_sha: str,
        pr_number: int,
        pr_url: str,
        expected_files: Sequence[str],
        *,
        stop_event=None,
    ) -> ReviewGateResult:
        return self.inspect_candidate(
            cwd,
            task_id,
            commit_sha,
            pr_number,
            pr_url,
            expected_files,
            expected_draft=True,
            stop_event=stop_event,
        )
