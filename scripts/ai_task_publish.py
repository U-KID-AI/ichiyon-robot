"""Stage, check, commit and push using the task worktree's normal Git index."""

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence
from uuid import UUID

from ai_task_git import GitAdapter, GitResult, GitOperationError, SHA_PATTERN
from ai_task_process import communicate_bounded, managed_process_options


class PublishError(GitOperationError):
    pass


class PublishDiffCheckError(PublishError):
    def __init__(self, stdout: str, stderr: str, returncode: int = 2) -> None:
        super().__init__("git diff --cached --check failed", stdout=stdout,
                         stderr=stderr, returncode=returncode)
        if not self.diagnostics:
            self.diagnostics = "git diff --cached --check failed without diagnostics"


@dataclass(frozen=True)
class PublishResult:
    commit_sha: str
    tree_sha: str
    changed_files: tuple[str, ...]


class GitPublisher:
    def __init__(
        self,
        git_path: Path,
        *,
        gcm_path: Path | None = None,
        gh_path: Path | None = None,
        runner: Callable[..., object] | None = None,
        popen: Callable[..., object] | None = None,
    ) -> None:
        self.git_path = git_path.resolve()
        if not self.git_path.is_file():
            raise PublishError("Git executable is missing")
        # Retained for constructor compatibility; Git uses its configured credentials.
        self.gcm_path = gcm_path
        self.gh_path = gh_path
        self._runner = runner or subprocess.run
        self._popen = popen or subprocess.Popen

    @staticmethod
    def _base_environment() -> dict[str, str]:
        environment = os.environ.copy()
        environment.setdefault("GIT_TERMINAL_PROMPT", "0")
        environment.setdefault("GCM_INTERACTIVE", "Never")
        return environment

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str] | None = None,
        timeout: float = 60,
        stop_event=None,
    ) -> GitResult:
        argv = [str(self.git_path), *args]
        env = dict(environment) if environment is not None else self._base_environment()
        if stop_event is not None and stop_event.is_set():
            raise PublishError("Git publish operation refused after lease loss")
        try:
            if stop_event is None:
                result = self._runner(
                    argv, cwd=str(cwd.resolve()), shell=False, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=timeout,
                    check=False, env=env,
                )
            else:
                process = self._popen(
                    argv, **managed_process_options(), cwd=str(cwd.resolve()), shell=False,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                )
                # Preserve full Git diagnostics while retaining timeout and process-tree cleanup.
                try:
                    result = communicate_bounded(
                        process, input_text=None, timeout=timeout, max_output_bytes=sys.maxsize,
                        stop_event=stop_event,
                    )
                finally:
                    for name in ("stdout", "stderr"):
                        stream = getattr(process, name, None)
                        if stream is not None:
                            stream.close()
                failure = (
                    "Git publish operation stopped after lease loss" if result.stopped else
                    "Git publish operation timed out" if result.timed_out else
                    "Git publish process cleanup failed" if result.stdin_cleanup_failed else None
                )
                if failure:
                    raise PublishError(failure, stdout=result.stdout, stderr=result.stderr,
                                             returncode=result.returncode)
        except subprocess.TimeoutExpired as exc:
            def decode(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            raise PublishError(
                f"git {' '.join(args)} timed out after {timeout}s",
                stdout=decode(exc.stdout), stderr=decode(exc.stderr),
            ) from exc
        except OSError as exc:
            raise PublishError(f"git {' '.join(args)} could not start: {exc}") from exc
        return GitResult(result.returncode, result.stdout or "", result.stderr or "")

    def _checked(self, args: Sequence[str], *, cwd: Path, timeout: float = 60,
                 stop_event=None) -> GitResult:
        result = self._run(args, cwd=cwd, timeout=timeout, stop_event=stop_event)
        if result.returncode != 0:
            raise PublishError(
                f"git {' '.join(args)} failed (exit {result.returncode})",
                stdout=result.stdout, stderr=result.stderr, returncode=result.returncode,
            )
        return result

    def _task_branch(self, cwd: Path, task_id: UUID, *, stop_event=None) -> str:
        if not isinstance(task_id, UUID):
            raise PublishError("task ID must be UUID")
        branch = GitAdapter.expected_branch(task_id)
        actual = self._checked(("rev-parse", "--abbrev-ref", "HEAD"), cwd=cwd, stop_event=stop_event)
        if actual.stdout.strip() != branch:
            raise PublishError("worktree branch mismatch", stdout=actual.stdout, stderr=actual.stderr)
        return branch

    def validate_candidate_diff_check(
        self, cwd: Path, base_sha: str, changed_files: list[str] | None = None, *, stop_event=None,
    ) -> None:
        """Stage all changes and give repairable whitespace feedback without undoing edits.

        base_sha and changed_files remain accepted for callers using the former interface;
        neither limits the paths Git stages.
        """
        self._checked(("add", "-A"), cwd=cwd, stop_event=stop_event)
        checked = self._run(("diff", "--cached", "--check"), cwd=cwd, stop_event=stop_event)
        if checked.returncode != 0:
            if checked.returncode in (1, 2, 3):
                raise PublishDiffCheckError(checked.stdout, checked.stderr, checked.returncode)
            raise PublishError("git diff --cached --check failed", stdout=checked.stdout,
                                     stderr=checked.stderr, returncode=checked.returncode)

    def commit(
        self, cwd: Path, task_id: UUID, base_sha: str, changed_files: list[str] | None = None,
        *, stop_event=None,
    ) -> PublishResult:
        """Commit all pending changes, or reuse a clean task HEAD on retry."""
        if not SHA_PATTERN.fullmatch(base_sha):
            raise PublishError("invalid base SHA")
        self._task_branch(cwd, task_id, stop_event=stop_event)
        self._checked(("merge-base", "--is-ancestor", base_sha, "HEAD"), cwd=cwd, stop_event=stop_event)
        self.validate_candidate_diff_check(cwd, base_sha, changed_files, stop_event=stop_event)
        pending = self._run(("diff", "--cached", "--quiet"), cwd=cwd, stop_event=stop_event)
        if pending.returncode not in (0, 1):
            raise PublishError("staged diff inspection failed", stdout=pending.stdout,
                                     stderr=pending.stderr, returncode=pending.returncode)
        # A resolved merge can have no tree changes and still require a merge commit.
        merge_path = self._checked(("rev-parse", "--git-path", "MERGE_HEAD"), cwd=cwd,
                                   stop_event=stop_event).stdout.strip()
        if pending.returncode == 1 or (cwd / merge_path).exists():
            self._checked(("commit", "-m", f"chore(ai): task {task_id}"), cwd=cwd, stop_event=stop_event)
        commit_sha = self._checked(("rev-parse", "HEAD"), cwd=cwd, stop_event=stop_event).stdout.strip()
        tree_sha = self._checked(("rev-parse", "HEAD^{tree}"), cwd=cwd, stop_event=stop_event).stdout.strip()
        changed = self._checked(
            ("diff", "--name-only", "--no-renames", "-z", base_sha, commit_sha, "--"),
            cwd=cwd, stop_event=stop_event,
        )
        return PublishResult(commit_sha, tree_sha, tuple(path for path in changed.stdout.split("\0") if path))

    def push_task_branch(
        self, cwd: Path, task_id: UUID, commit_sha: str, *, stop_event=None,
    ) -> str:
        if not SHA_PATTERN.fullmatch(commit_sha):
            raise PublishError("invalid commit SHA")
        branch = self._task_branch(cwd, task_id, stop_event=stop_event)
        head = self._checked(("rev-parse", "HEAD"), cwd=cwd, stop_event=stop_event)
        if head.stdout.strip() != commit_sha:
            raise PublishError("task HEAD changed before push", stdout=head.stdout, stderr=head.stderr)
        remote_ref = "refs/heads/" + branch
        self._checked(("push", "origin", f"HEAD:{remote_ref}"), cwd=cwd, timeout=180, stop_event=stop_event)
        after = self._checked(("ls-remote", "--heads", "origin", remote_ref), cwd=cwd,
                              timeout=120, stop_event=stop_event)
        verified = self._parse_ls_remote(after.stdout, remote_ref)
        if verified != commit_sha:
            raise PublishError("remote task branch SHA mismatch", stdout=after.stdout, stderr=after.stderr)
        return verified

    @staticmethod
    def _parse_ls_remote(value: str, expected_ref: str) -> str | None:
        if not value.strip():
            return None
        lines = value.splitlines()
        if len(lines) != 1 or "\t" not in lines[0]:
            raise PublishError("remote branch response is malformed", stdout=value)
        sha, ref = lines[0].split("\t", 1)
        if SHA_PATTERN.fullmatch(sha) is None or ref != expected_ref:
            raise PublishError("remote branch response is invalid", stdout=value)
        return sha
