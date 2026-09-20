"""Fixed-operation GitHub CLI adapter for Phase 2C."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from uuid import UUID

from ai_task_process import communicate_bounded
from ai_task_safety import expected_branch, is_reparse_point, validate_sha


EXPECTED_REPOSITORY = "U-KID-AI/ichiyon-robot"
EXPECTED_BASE = "main"
MAX_GH_OUTPUT_BYTES = 128 * 1024

PR_URL_PATTERN = re.compile(
    r"^https://github\.com/U-KID-AI/ichiyon-robot/pull/([1-9][0-9]*)$"
)


class GitHubSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class DraftPullRequest:
    number: int
    url: str
    head_sha: str


class GitHubAdapter:
    JSON_FIELDS = (
        "number,url,isDraft,baseRefName,headRefName,headRefOid,state"
    )

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
            raise GitHubSafetyError(
                "GitHub CLI executable is unsafe"
            )

        self.gh_path = resolved
        self._runner = runner or subprocess.run
        self._popen = popen or subprocess.Popen

    @staticmethod
    def _task_id_from_branch(
        branch: str,
    ) -> UUID | None:
        prefix = "ai/task/"

        if (
            not isinstance(branch, str)
            or not branch.startswith(prefix)
        ):
            return None

        try:
            task_id = UUID(branch[len(prefix):])
        except (ValueError, TypeError):
            return None

        if branch != expected_branch(task_id):
            return None

        return task_id

    @classmethod
    def _expected_title(
        cls,
        task_id: UUID,
    ) -> str:
        return f"chore(ai): task {task_id}"

    @classmethod
    def _expected_body(
        cls,
        task_id: UUID,
        commit_sha: str,
    ) -> str:
        return (
            f"Automated AI task `{task_id}`.\n\n"
            f"Commit: `{commit_sha}`\n\n"
            "Human review is required before merge."
        )

    @classmethod
    def _is_allowed_argv(
        cls,
        args: tuple[str, ...],
    ) -> bool:
        if (
            len(args) == 12
            and args[:7]
            == (
                "pr",
                "list",
                "--repo",
                EXPECTED_REPOSITORY,
                "--state",
                "open",
                "--head",
            )
            and cls._task_id_from_branch(args[7])
            is not None
            and args[8:12]
            == (
                "--limit",
                "2",
                "--json",
                cls.JSON_FIELDS,
            )
        ):
            return True

        if (
            len(args) == 13
            and args[:7]
            == (
                "pr",
                "create",
                "--repo",
                EXPECTED_REPOSITORY,
                "--base",
                EXPECTED_BASE,
                "--head",
            )
            and args[8] == "--title"
            and args[10] == "--body"
            and args[12] == "--draft"
        ):
            task_id = cls._task_id_from_branch(
                args[7]
            )

            if task_id is None:
                return False

            if (
                args[9]
                != cls._expected_title(task_id)
            ):
                return False

            match = re.search(
                r"Commit: `([0-9a-fA-F]{40})`",
                args[11],
            )

            if match is None:
                return False

            commit_sha = match.group(1)

            return (
                args[11]
                == cls._expected_body(
                    task_id,
                    commit_sha,
                )
            )

        return False

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
            raise GitHubSafetyError(
                "GitHub operation is not allowlisted"
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
                raise GitHubSafetyError(
                    "GitHub CLI output is too large"
                )

            return result

        if stop_event.is_set():
            raise GitHubSafetyError(
                "GitHub operation refused after lease loss"
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
            raise GitHubSafetyError(
                "GitHub operation stopped after lease loss"
            )

        if result.timed_out:
            raise GitHubSafetyError(
                "GitHub operation timed out"
            )

        if result.stdin_cleanup_failed:
            raise GitHubSafetyError(
                "GitHub process cleanup failed"
            )

        return result

    def _list_open(
        self,
        cwd: Path,
        branch: str,
        *,
        stop_event=None,
    ) -> list[dict]:
        result = self._run(
            (
                "pr",
                "list",
                "--repo",
                EXPECTED_REPOSITORY,
                "--state",
                "open",
                "--head",
                branch,
                "--limit",
                "2",
                "--json",
                self.JSON_FIELDS,
            ),
            cwd=cwd,
            stop_event=stop_event,
        )

        if result.returncode != 0:
            raise GitHubSafetyError(
                "GitHub PR inspection failed"
            )

        try:
            value = json.loads(result.stdout)
        except (
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise GitHubSafetyError(
                "GitHub returned invalid JSON"
            ) from exc

        if (
            not isinstance(value, list)
            or len(value) > 2
        ):
            raise GitHubSafetyError(
                "GitHub returned invalid PR data"
            )

        if any(
            not isinstance(item, dict)
            for item in value
        ):
            raise GitHubSafetyError(
                "GitHub returned invalid PR entry"
            )

        return value

    @staticmethod
    def _validate_pr(
        item: dict,
        *,
        task_id: UUID,
        commit_sha: str,
    ) -> DraftPullRequest:
        branch = expected_branch(task_id)

        number = item.get("number")
        url = item.get("url")

        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
            or number > 2_147_483_647
            or not isinstance(url, str)
            or item.get("isDraft") is not True
            or item.get("baseRefName")
            != EXPECTED_BASE
            or item.get("headRefName")
            != branch
            or item.get("headRefOid")
            != commit_sha
            or item.get("state") != "OPEN"
        ):
            raise GitHubSafetyError(
                "Draft PR metadata mismatch"
            )

        match = PR_URL_PATTERN.fullmatch(url)

        if (
            match is None
            or int(match.group(1)) != number
        ):
            raise GitHubSafetyError(
                "Draft PR URL mismatch"
            )

        return DraftPullRequest(
            number,
            url,
            commit_sha,
        )

    def ensure_draft_pr(
        self,
        cwd: Path,
        task_id: UUID,
        commit_sha: str,
        *,
        stop_event=None,
    ) -> DraftPullRequest:
        if not isinstance(task_id, UUID):
            raise GitHubSafetyError(
                "task ID must be UUID"
            )

        validate_sha(commit_sha)

        branch = expected_branch(task_id)

        existing = self._list_open(
            cwd,
            branch,
            stop_event=stop_event,
        )

        if existing:
            if len(existing) != 1:
                raise GitHubSafetyError(
                    "multiple open task PRs exist"
                )

            return self._validate_pr(
                existing[0],
                task_id=task_id,
                commit_sha=commit_sha,
            )

        title = self._expected_title(task_id)
        body = self._expected_body(
            task_id,
            commit_sha,
        )

        created = self._run(
            (
                "pr",
                "create",
                "--repo",
                EXPECTED_REPOSITORY,
                "--base",
                EXPECTED_BASE,
                "--head",
                branch,
                "--title",
                title,
                "--body",
                body,
                "--draft",
            ),
            cwd=cwd,
            timeout=120,
            stop_event=stop_event,
        )

        # Re-read GitHub state even after an
        # ambiguous create failure. If the exact
        # Draft PR exists, this is idempotently
        # successful.
        after = self._list_open(
            cwd,
            branch,
            stop_event=stop_event,
        )

        if len(after) != 1:
            if created.returncode != 0:
                raise GitHubSafetyError(
                    "Draft PR creation failed"
                )

            raise GitHubSafetyError(
                "Draft PR creation was not verifiable"
            )

        return self._validate_pr(
            after[0],
            task_id=task_id,
            commit_sha=commit_sha,
        )
