"""Ordinary Git operations and task worktree integrity checks."""

import codecs
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from uuid import UUID

EXPECTED_ORIGIN = "https://github.com/U-KID-AI/ichiyon-robot.git"
SHA_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


class GitOperationError(RuntimeError):
    """A Git failure or a task worktree integrity failure."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "",
                 returncode: int | None = None) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.diagnostics = "\n".join(part for part in (stdout, stderr) if part)
        super().__init__(message + ("\n" + self.diagnostics if self.diagnostics else ""))


class GitDiffCheckError(GitOperationError):
    pass


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class GitSnapshot:
    head: str
    branch: str
    worktrees: str
    origin: str


class GitAdapter:
    def __init__(self, repo_root: Path, git_path: Path, *, runner: Callable[..., object] | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.git_path = git_path.resolve()
        self._runner = runner or subprocess.run

    def _run(self, args: Sequence[str], cwd: Path | None = None, timeout: float = 60) -> GitResult:
        try:
            result = self._runner(
                [str(self.git_path), *args], cwd=str((cwd or self.repo_root).resolve()), shell=False,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            def decode(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            raise GitOperationError(
                f"git {' '.join(args)} timed out after {timeout}s",
                stdout=decode(exc.stdout), stderr=decode(exc.stderr),
            ) from exc
        except OSError as exc:
            raise GitOperationError(f"git {' '.join(args)} could not start: {exc}") from exc
        return GitResult(result.returncode, result.stdout or "", result.stderr or "")

    def _checked(self, args: Sequence[str], cwd: Path | None = None, timeout: float = 60) -> GitResult:
        result = self._run(args, cwd, timeout)
        if result.returncode != 0:
            raise GitOperationError(
                f"git {' '.join(args)} failed (exit {result.returncode})",
                stdout=result.stdout, stderr=result.stderr, returncode=result.returncode,
            )
        return result

    def require_source_repo(self) -> None:
        if not self.repo_root.is_dir() or not (self.repo_root / ".git").exists():
            raise GitOperationError("source repository is invalid")
        root = self._checked(("rev-parse", "--show-toplevel"))
        if Path(root.stdout.strip()).resolve() != self.repo_root:
            raise GitOperationError("source repository root mismatch")
        remote = self._checked(("remote", "get-url", "origin"))
        if not remote.stdout.strip():
            raise GitOperationError("origin repository is missing")

    def fetch_main(self) -> str:
        self._checked(("fetch", "origin", "main"), timeout=120)
        sha = self._checked(("rev-parse", "origin/main"))
        value = sha.stdout.strip()
        if not SHA_PATTERN.fullmatch(value):
            raise GitOperationError("origin/main SHA is invalid", stdout=sha.stdout, stderr=sha.stderr)
        return value

    def integrate_main(self, cwd: Path) -> str:
        """Merge fetched main, retain edits/conflicts, and return that main commit."""
        self.snapshot(cwd)
        self._checked(("fetch", "origin", "main"), cwd, timeout=120)
        main = self._checked(("rev-parse", "origin/main"), cwd)
        main_sha = main.stdout.strip()
        if not SHA_PATTERN.fullmatch(main_sha):
            raise GitOperationError("origin/main SHA is invalid", stdout=main.stdout, stderr=main.stderr)
        self._checked(("merge", "--no-edit", main_sha), cwd, timeout=120)
        return main_sha

    @staticmethod
    def expected_branch(task_id: UUID) -> str:
        return "ai/task/" + str(task_id)

    @staticmethod
    def expected_worktree_name(task_id: UUID) -> str:
        return "ai-task-" + str(task_id)

    def add_worktree(self, task_id: UUID, worktree_root: Path, base_sha: str) -> Path:
        if not isinstance(task_id, UUID) or not SHA_PATTERN.fullmatch(base_sha):
            raise GitOperationError("invalid worktree input")
        worktree_root = worktree_root.resolve()
        worktree_root.mkdir(parents=True, exist_ok=True)
        target = worktree_root / self.expected_worktree_name(task_id)
        if target.parent != worktree_root or target.exists():
            raise GitOperationError("worktree path already exists or escapes root")
        self._checked(("worktree", "add", "-b", self.expected_branch(task_id), str(target), base_sha))
        return target

    def _common_dir(self, cwd: Path) -> Path:
        result = self._checked(("rev-parse", "--git-common-dir"), cwd)
        return (cwd / result.stdout.strip()).resolve()

    def _worktree_list(self) -> GitResult:
        try:
            return self._checked(("worktree", "list", "--porcelain", "-z"))
        except GitOperationError as exc:
            if exc.returncode != 129 or not re.search(
                r"(?m)^error: unknown (?:switch|option) [`'\"]-?z['\"]\r?$", exc.stderr,
            ):
                raise
        return self._checked(("worktree", "list", "--porcelain"))

    def snapshot(self, cwd: Path) -> GitSnapshot:
        root = self._checked(("rev-parse", "--show-toplevel"), cwd)
        if Path(root.stdout.strip()).resolve() != cwd.resolve():
            raise GitOperationError("worktree root mismatch")
        if self._common_dir(cwd) != self._common_dir(self.repo_root):
            raise GitOperationError("worktree belongs to a different repository")
        head_result = self._checked(("rev-parse", "HEAD"), cwd)
        branch_result = self._checked(("rev-parse", "--abbrev-ref", "HEAD"), cwd)
        worktree_result = self._worktree_list()
        origin_result = self._checked(("remote", "get-url", "origin"), cwd)
        head = head_result.stdout.strip()
        branch = branch_result.stdout.strip()
        worktrees = worktree_result.stdout
        origin = origin_result.stdout.strip()
        worktree_paths = self.parse_worktree_porcelain(worktrees)
        if (not SHA_PATTERN.fullmatch(head) or not branch or not origin
                or self._normalized_path(cwd.resolve()) not in worktree_paths
                or self._normalized_path(self.repo_root) not in worktree_paths):
            raise GitOperationError("Git snapshot validation failed")
        return GitSnapshot(head, branch, worktrees, origin)

    @staticmethod
    def _normalized_path(path: Path) -> str:
        return os.path.normcase(str(path)).replace(os.sep, "/").rstrip("/")

    @staticmethod
    def _unquote_worktree_path(value: str) -> str:
        if not value.startswith('"'):
            return value
        if not re.fullmatch(r'"(?:[^"\\\r\n]|\\(?:[abfnrtv"\\]|[0-3][0-7]{2}))*"', value):
            raise GitOperationError("Git worktree path quoting is malformed")
        # Git quotes UTF-8 bytes as three-digit octal escapes, not Unicode code points.
        decoded = codecs.escape_decode(value[1:-1].encode("utf-8"))[0].decode("utf-8", errors="replace")
        if not decoded or "\0" in decoded:
            raise GitOperationError("Git worktree path is malformed")
        return decoded

    @classmethod
    def parse_worktree_porcelain(cls, value: str) -> set[str]:
        """Accept NUL-delimited output, plus the legacy newline format."""
        if not isinstance(value, str) or not value:
            raise GitOperationError("Git worktree output is empty")
        separator = "\0" if "\0" in value else "\n"
        normalized = value if separator == "\0" else value.replace("\r\n", "\n")
        terminator = separator * 2
        if not normalized.endswith(terminator):
            raise GitOperationError("Git worktree output is missing its record terminator")
        paths = set()
        for block in normalized[:-2].split(terminator):
            lines = block.split(separator)
            if not lines or not lines[0].startswith("worktree "):
                raise GitOperationError("Git worktree output is malformed")
            raw_path = lines[0][len("worktree "):]
            if not raw_path or any(line.startswith("worktree ") for line in lines[1:]):
                raise GitOperationError("Git worktree output is malformed")
            if separator == "\n":
                raw_path = cls._unquote_worktree_path(raw_path)
            if ("bare" not in lines and not any(
                    line.startswith("HEAD ") and SHA_PATTERN.fullmatch(line[5:]) for line in lines[1:])):
                raise GitOperationError("Git worktree output is malformed")
            paths.add(cls._normalized_path(Path(raw_path)))
        return paths

    def validate_worktree(self, task_id: UUID, path: Path, base_sha: str) -> None:
        if not isinstance(task_id, UUID) or not SHA_PATTERN.fullmatch(base_sha):
            raise GitOperationError("invalid worktree input")
        if path.name != self.expected_worktree_name(task_id):
            raise GitOperationError("worktree name mismatch")
        snapshot = self.snapshot(path)
        if snapshot.branch != self.expected_branch(task_id):
            raise GitOperationError("worktree branch mismatch")
        # Ordinary task commits advance HEAD; the task base must remain an ancestor.
        self._checked(("merge-base", "--is-ancestor", base_sha, snapshot.head), path)

    def changed_files(self, cwd: Path, base_sha: str | None = None) -> list[str]:
        """Return pending paths and, optionally, changes already committed since base."""
        paths = []
        if base_sha is not None:
            if not SHA_PATTERN.fullmatch(base_sha):
                raise GitOperationError("invalid base SHA")
            committed = self._checked(("diff", "--name-only", "--no-renames", "-z", base_sha, "HEAD", "--"), cwd)
            paths.extend(path for path in committed.stdout.split("\0") if path)
        result = self._checked(("status", "--porcelain=v1", "-z", "--untracked-files=all"), cwd)
        paths.extend(self.parse_status_z(result.stdout))
        return list(dict.fromkeys(paths))

    def staged_files(self, cwd: Path) -> list[str]:
        result = self._checked(("status", "--porcelain=v1", "-z", "--untracked-files=all"), cwd)
        return [path for status, path in self.parse_status_entries_z(result.stdout) if status[0] not in (" ", "?")]

    @staticmethod
    def parse_status_z(value: str) -> list[str]:
        return [path for _, path in GitAdapter.parse_status_entries_z(value)]

    @staticmethod
    def parse_status_entries_z(value: str) -> list[tuple[str, str]]:
        records = value.split("\0")
        entries = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if len(record) < 4 or record[2] != " ":
                raise GitOperationError("invalid NUL status record")
            status = record[:2]
            entries.append((status, record[3:]))
            if status[0] in "RC" or status[1] in "RC":
                if index >= len(records) or not records[index]:
                    raise GitOperationError("invalid rename status record")
                entries.append((status, records[index]))
                index += 1
        return entries

    def diff_check(self, cwd: Path) -> str:
        result = self._run(("diff", "--check"), cwd)
        if result.returncode != 0:
            raise GitDiffCheckError("git diff --check failed", stdout=result.stdout,
                                    stderr=result.stderr, returncode=result.returncode)
        return result.stdout

    def diff_stat(self, cwd: Path, base_sha: str | None = None) -> str:
        if base_sha is not None and not SHA_PATTERN.fullmatch(base_sha):
            raise GitOperationError("invalid base SHA")
        return self._checked(("diff", "--stat", base_sha or "HEAD", "--"), cwd).stdout

    def diff(self, cwd: Path, base_sha: str) -> str:
        """Return the full committed task diff for the caller's redacted artifact."""
        if not SHA_PATTERN.fullmatch(base_sha):
            raise GitOperationError("invalid base SHA")
        return self._checked(("diff", base_sha, "HEAD", "--"), cwd).stdout
