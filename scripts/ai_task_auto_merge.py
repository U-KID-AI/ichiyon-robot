"""High-privilege fixed GitHub mutations for Phase 2D."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from uuid import UUID

from ai_task_process import communicate_bounded
from ai_task_review_merge import (
    EXPECTED_BASE,
    EXPECTED_REPOSITORY,
    ReviewGateResult,
    ReviewMergeGate,
    ReviewMergeSafetyError,
)
from ai_task_safety import (
    expected_branch,
    is_reparse_point,
    validate_sha,
)


MAX_GH_OUTPUT_BYTES = 128 * 1024

READY_MUTATION = (
    "mutation($pullRequestId:ID!){"
    "markPullRequestReadyForReview("
    "input:{pullRequestId:$pullRequestId}"
    "){pullRequest{number isDraft}}}"
)

NODE_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9_=-]{1,200}$"
)


class AutoMergeSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class AutoMergeResult:
    pr_number: int
    pr_url: str
    head_sha: str
    base_sha: str
    workflow_run_id: int
    merge_sha: str


class AutoMergeAdapter:
    def __init__(
        self,
        gh_path: Path,
        *,
        gate: ReviewMergeGate | None = None,
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
            raise AutoMergeSafetyError(
                "GitHub CLI executable is unsafe"
            )

        self.gh_path = resolved
        self._runner = runner or subprocess.run
        self._popen = popen or subprocess.Popen

        self.gate = gate or ReviewMergeGate(
            resolved,
            runner=runner,
            popen=popen,
        )

    @staticmethod
    def _valid_pr_number(value: int) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 < value <= 2_147_483_647
        )

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
        if (
            len(args) == 4
            and args[:3]
            == ("api", "--method", "GET")
            and re.fullmatch(
                r"repos/U-KID-AI/ichiyon-robot/"
                r"pulls/[1-9][0-9]*",
                args[3],
            )
        ):
            return True

        if (
            len(args) == 6
            and args[:2] == ("api", "graphql")
            and args[2] == "-f"
            and args[3]
            == f"query={READY_MUTATION}"
            and args[4] == "-F"
            and args[5].startswith(
                "pullRequestId="
            )
        ):
            node_id = args[5].split("=", 1)[1]
            return (
                NODE_ID_PATTERN.fullmatch(node_id)
                is not None
            )

        if (
            len(args) == 8
            and args[:3]
            == ("api", "--method", "PUT")
            and re.fullmatch(
                r"repos/U-KID-AI/ichiyon-robot/"
                r"pulls/[1-9][0-9]*/merge",
                args[3],
            )
            and args[4:6]
            == ("-f", "merge_method=squash")
            and args[6] == "-f"
            and re.fullmatch(
                r"sha=[0-9a-fA-F]{40}",
                args[7],
            )
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
            raise AutoMergeSafetyError(
                "GitHub mutation is not allowlisted"
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

            size = (
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

            if size > MAX_GH_OUTPUT_BYTES:
                raise AutoMergeSafetyError(
                    "GitHub mutation output is too large"
                )

            return result

        if stop_event.is_set():
            raise AutoMergeSafetyError(
                "GitHub mutation refused after lease loss"
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
            raise AutoMergeSafetyError(
                "GitHub mutation stopped after lease loss"
            )

        if result.timed_out:
            raise AutoMergeSafetyError(
                "GitHub mutation timed out"
            )

        if result.stdin_cleanup_failed:
            raise AutoMergeSafetyError(
                "GitHub mutation cleanup failed"
            )

        return result

    def _read_pr(
        self,
        cwd: Path,
        pr_number: int,
        *,
        stop_event=None,
    ) -> dict:
        result = self._run(
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

        if result.returncode != 0:
            raise AutoMergeSafetyError(
                "GitHub PR read failed"
            )

        try:
            value = json.loads(result.stdout)
        except (
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise AutoMergeSafetyError(
                "GitHub returned invalid PR JSON"
            ) from exc

        if not isinstance(value, dict):
            raise AutoMergeSafetyError(
                "GitHub returned invalid PR object"
            )

        return value

    @staticmethod
    def _validate_pre_ready_pr(
        item: dict,
        *,
        task_id: UUID,
        commit_sha: str,
        pr_number: int,
        pr_url: str,
        base_sha: str,
    ) -> str:
        base = item.get("base")
        head = item.get("head")
        node_id = item.get("node_id")

        if (
            item.get("number") != pr_number
            or item.get("html_url") != pr_url
            or item.get("state") != "open"
            or item.get("draft") is not True
            or item.get("merged_at") is not None
            or not isinstance(base, dict)
            or base.get("ref") != EXPECTED_BASE
            or base.get("sha") != base_sha
            or not isinstance(head, dict)
            or head.get("ref")
            != expected_branch(task_id)
            or head.get("sha") != commit_sha
            or not isinstance(node_id, str)
            or NODE_ID_PATTERN.fullmatch(node_id)
            is None
        ):
            raise AutoMergeSafetyError(
                "PR changed before ready mutation"
            )

        return node_id

    @staticmethod
    def _same_gate(
        before: ReviewGateResult,
        after: ReviewGateResult,
    ) -> bool:
        return (
            before.pr_number == after.pr_number
            and before.pr_url == after.pr_url
            and before.head_sha == after.head_sha
            and before.base_sha == after.base_sha
            and before.workflow_run_id
            == after.workflow_run_id
            and before.changed_files
            == after.changed_files
        )

    @staticmethod
    def _validate_merged_pr(
        item: dict,
        *,
        task_id: UUID,
        commit_sha: str,
        pr_number: int,
        pr_url: str,
        base_sha: str,
    ) -> str:
        base = item.get("base")
        head = item.get("head")
        merge_sha = item.get("merge_commit_sha")

        if (
            item.get("number") != pr_number
            or item.get("html_url") != pr_url
            or item.get("state") != "closed"
            or item.get("draft") is not False
            or not isinstance(
                item.get("merged_at"),
                str,
            )
            or not item.get("merged_at")
            or not isinstance(base, dict)
            or base.get("ref") != EXPECTED_BASE
            or base.get("sha") != base_sha
            or not isinstance(head, dict)
            or head.get("ref")
            != expected_branch(task_id)
            or head.get("sha") != commit_sha
            or not isinstance(merge_sha, str)
        ):
            raise AutoMergeSafetyError(
                "merged PR metadata mismatch"
            )

        validate_sha(merge_sha)
        return merge_sha

    def ready_and_squash_merge(
        self,
        cwd: Path,
        task_id: UUID,
        commit_sha: str,
        pr_number: int,
        pr_url: str,
        expected_files: Sequence[str],
        *,
        stop_event=None,
    ) -> AutoMergeResult:
        if not isinstance(task_id, UUID):
            raise AutoMergeSafetyError(
                "task ID must be UUID"
            )

        validate_sha(commit_sha)

        if not self._valid_pr_number(pr_number):
            raise AutoMergeSafetyError(
                "invalid PR number"
            )

        before = self.gate.inspect_candidate(
            cwd,
            task_id,
            commit_sha,
            pr_number,
            pr_url,
            expected_files,
            expected_draft=True,
            stop_event=stop_event,
        )

        if stop_event is not None and stop_event.is_set():
            raise AutoMergeSafetyError(
                "lease lost before ready mutation"
            )

        pre_ready = self._read_pr(
            cwd,
            pr_number,
            stop_event=stop_event,
        )

        node_id = self._validate_pre_ready_pr(
            pre_ready,
            task_id=task_id,
            commit_sha=commit_sha,
            pr_number=pr_number,
            pr_url=pr_url,
            base_sha=before.base_sha,
        )

        ready_result = self._run(
            (
                "api",
                "graphql",
                "-f",
                f"query={READY_MUTATION}",
                "-F",
                f"pullRequestId={node_id}",
            ),
            cwd=cwd,
            stop_event=stop_event,
        )

        if stop_event is not None and stop_event.is_set():
            raise AutoMergeSafetyError(
                "lease lost after ready mutation"
            )

        try:
            after = self.gate.inspect_candidate(
                cwd,
                task_id,
                commit_sha,
                pr_number,
                pr_url,
                expected_files,
                expected_draft=False,
                stop_event=stop_event,
            )
        except ReviewMergeSafetyError as exc:
            if ready_result.returncode != 0:
                raise AutoMergeSafetyError(
                    "ready mutation failed"
                ) from exc
            raise AutoMergeSafetyError(
                "ready state could not be verified"
            ) from exc

        if not self._same_gate(before, after):
            raise AutoMergeSafetyError(
                "review gate changed after ready mutation"
            )

        if stop_event is not None and stop_event.is_set():
            raise AutoMergeSafetyError(
                "lease lost before merge mutation"
            )

        merge_result = self._run(
            (
                "api",
                "--method",
                "PUT",
                (
                    "repos/U-KID-AI/ichiyon-robot/"
                    f"pulls/{pr_number}/merge"
                ),
                "-f",
                "merge_method=squash",
                "-f",
                f"sha={commit_sha}",
            ),
            cwd=cwd,
            timeout=120,
            stop_event=stop_event,
        )

        final_pr = self._read_pr(
            cwd,
            pr_number,
            stop_event=stop_event,
        )

        merge_sha = self._validate_merged_pr(
            final_pr,
            task_id=task_id,
            commit_sha=commit_sha,
            pr_number=pr_number,
            pr_url=pr_url,
            base_sha=before.base_sha,
        )

        if merge_result.returncode == 0:
            try:
                payload = json.loads(
                    merge_result.stdout
                )
            except (
                TypeError,
                json.JSONDecodeError,
            ) as exc:
                raise AutoMergeSafetyError(
                    "merge returned invalid JSON"
                ) from exc

            if (
                not isinstance(payload, dict)
                or payload.get("merged") is not True
                or payload.get("sha") != merge_sha
            ):
                raise AutoMergeSafetyError(
                    "merge response mismatch"
                )

        return AutoMergeResult(
            pr_number=pr_number,
            pr_url=pr_url,
            head_sha=commit_sha,
            base_sha=before.base_sha,
            workflow_run_id=before.workflow_run_id,
            merge_sha=merge_sha,
        )