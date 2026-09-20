"""Allowlisted, non-shell Git operations for the local AI task runner."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from uuid import UUID

from ai_task_safety import is_reparse_point, is_protected_path, validate_changed_paths


EXPECTED_ORIGIN = "https://github.com/U-KID-AI/ichiyon-robot.git"
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


class GitSafetyError(RuntimeError):
    pass


class GitDiffCheckError(GitSafetyError):
    pass


def _safe_relative(value: str) -> bool:
    return (isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_. /-]+", value))
            and not Path(value).is_absolute()
            and all(part not in ("", ".", "..") and not part.endswith((".", " "))
                    for part in value.split("/")))


def repairable_paths(cwd: Path, paths: list[str]) -> list[str]:
    """Classify policy edits only after checking every filesystem boundary.

    Credential paths are never restored, opened, or included in diagnostics.
    Use a conservative portable filename vocabulary for recovery operations.
    """
    root = cwd.resolve()
    if cwd.is_symlink() or is_reparse_point(cwd):
        raise GitSafetyError("worktree filesystem boundary violation")
    protected = []
    for relative in paths:
        parts = relative.lower().split("/")
        if (not _safe_relative(relative)
                or any(part == ".git" or part.startswith(".env")
                       or part in ("secrets", ".ssh", "production", "staging")
                       or any(word in part for word in ("secret", "credential", "token", "cookie", "private_key", "private-key"))
                       or part in ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa") for part in parts)
                or relative.lower().endswith((".pem", ".key", ".p12", ".pfx", ".crt", ".cer"))):
            raise GitSafetyError("unsafe or credential changed path")
        candidate = root
        for part in relative.split("/"):
            candidate = candidate / part
            if candidate.is_symlink():
                raise GitSafetyError("changed path is a symlink")
            try:
                candidate.lstat()
            except FileNotFoundError:
                continue
            if is_reparse_point(candidate):
                raise GitSafetyError("changed path is a reparse point")
        if root not in candidate.resolve().parents:
            raise GitSafetyError("changed path escapes repository")
        if is_protected_path(relative):
            protected.append(relative)
        else:
            validate_changed_paths(cwd, [relative])
    return protected


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
        if not self._is_allowed_argv(tuple(args)):
            raise GitSafetyError("Git operation is not allowlisted")
        result = self._runner([str(self.git_path), *args], cwd=str((cwd or self.repo_root).resolve()), shell=False,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
        return GitResult(result.returncode, result.stdout, result.stderr)

    @staticmethod
    def _is_allowed_argv(args: tuple[str, ...]) -> bool:
        if args in (("status", "--porcelain=v1"), ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
                    ("rev-parse", "--show-toplevel"), ("rev-parse", "origin/main"), ("rev-parse", "HEAD"),
                    ("rev-parse", "--abbrev-ref", "HEAD"), ("remote", "get-url", "origin"),
                    ("fetch", "origin", "main"), ("worktree", "list", "--porcelain"),
                    ("diff", "--check"), ("diff", "--stat")):
            return True
        if len(args) == 6 and args[0:3] == ("worktree", "add", "-b"):
            return bool(re.fullmatch(r"ai/task/[0-9a-fA-F-]{36}", args[3]) and Path(args[4]).is_absolute() and SHA_PATTERN.fullmatch(args[5]) is not None)
        if len(args) == 6 and args[:3] == ("restore", "--staged", "--source") and args[4:] == ("--", "."):
            return bool(SHA_PATTERN.fullmatch(args[3]))
        if len(args) == 6 and args[:3] == ("ls-tree", "-z", "--full-tree") and args[4] == "--":
            return bool(SHA_PATTERN.fullmatch(args[3]) and _safe_relative(args[5]))
        if len(args) == 6 and args[:2] == ("restore", "--worktree") and args[2] == "--source":
            return bool(SHA_PATTERN.fullmatch(args[3]) and args[4] == "--"
                        and args[5].startswith(":(literal)")
                        and _safe_relative(args[5][10:]) and is_protected_path(args[5][10:]))
        return False

    def unstage(self, cwd: Path, task_id: UUID, base_sha: str) -> None:
        self.validate_worktree(task_id, cwd, base_sha)
        self.snapshot(cwd)
        repairable_paths(cwd, self.changed_files(cwd))
        if self._run(("restore", "--staged", "--source", base_sha, "--", "."), cwd).returncode:
            raise GitSafetyError("automatic unstage failed")
        if self.staged_files(cwd):
            raise GitSafetyError("Git index remains staged after automatic unstage")

    def restore_protected(self, cwd: Path, task_id: UUID, base_sha: str, paths: list[str]) -> None:
        self.validate_worktree(task_id, cwd, base_sha)
        self.snapshot(cwd)
        if repairable_paths(cwd, paths) != paths:
            raise GitSafetyError("recovery requires only protected paths")
        for relative in paths:
            entry = self._run(("ls-tree", "-z", "--full-tree", base_sha, "--", relative), cwd)
            if entry.returncode:
                raise GitSafetyError("protected base path inspection failed")
            if entry.stdout:
                records = entry.stdout.rstrip("\0").split("\0")
                if len(records) != 1 or not re.fullmatch(
                        r"100(?:644|755) blob [0-9a-f]{40}\t" + re.escape(relative), records[0]):
                    raise GitSafetyError("protected base path is not a regular file")
                if self._run(("restore", "--worktree", "--source", base_sha, "--", ":(literal)" + relative), cwd).returncode:
                    raise GitSafetyError("protected path restoration failed")
            else:
                candidate = cwd / relative
                if candidate.exists():
                    if not candidate.is_file():
                        raise GitSafetyError("protected untracked path is not a file")
                    candidate.unlink()
        # A removed project Codex layer must not leave an empty directory that
        # would block the next sandbox invocation. Never recursively delete.
        parents = set()
        for relative in paths:
            if relative.startswith(".codex/"):
                parent = (cwd / relative).parent
                while parent != cwd:
                    parents.add(parent)
                    parent = parent.parent
        for parent in sorted(parents, key=lambda path: len(path.parts), reverse=True):
            if parent.exists() and not parent.is_symlink() and not is_reparse_point(parent):
                try:
                    parent.rmdir()  # Only empty directories; ignored contents remain fail-closed.
                except OSError:
                    pass
        if set(paths) & set(self.changed_files(cwd)):
            raise GitSafetyError("protected paths remain changed after restoration")

    def require_source_repo(self) -> None:
        if not self.repo_root.is_dir() or not (self.repo_root / ".git").exists():
            raise GitSafetyError("source repository is invalid")
        root = self._run(("rev-parse", "--show-toplevel"))
        if root.returncode != 0 or Path(root.stdout.strip()).resolve() != self.repo_root:
            raise GitSafetyError("source repository root mismatch")
        remote = self._run(("remote", "get-url", "origin"))
        if remote.returncode != 0 or remote.stdout.strip().rstrip("/") != EXPECTED_ORIGIN.rstrip("/"):
            raise GitSafetyError("origin repository mismatch")
        status = self._run(("status", "--porcelain=v1"))
        if status.returncode != 0 or status.stdout:
            raise GitSafetyError("source repository is not clean")

    def fetch_main(self) -> str:
        result = self._run(("fetch", "origin", "main"), timeout=120)
        if result.returncode != 0:
            raise GitSafetyError("origin fetch failed")
        sha = self._run(("rev-parse", "origin/main"))
        value = sha.stdout.strip()
        if sha.returncode != 0 or not SHA_PATTERN.fullmatch(value):
            raise GitSafetyError("origin/main SHA is invalid")
        return value

    @staticmethod
    def expected_branch(task_id: UUID) -> str:
        return "ai/task/" + str(task_id)

    @staticmethod
    def expected_worktree_name(task_id: UUID) -> str:
        return "ai-task-" + str(task_id)

    def add_worktree(self, task_id: UUID, worktree_root: Path, base_sha: str) -> Path:
        if not isinstance(task_id, UUID) or not SHA_PATTERN.fullmatch(base_sha):
            raise GitSafetyError("invalid worktree input")
        name = self.expected_worktree_name(task_id)
        worktree_root = worktree_root.resolve()
        worktree_root.mkdir(parents=True, exist_ok=True)
        target = (worktree_root / name).resolve()
        if target.parent != worktree_root or target.exists() or target.is_symlink():
            raise GitSafetyError("worktree path already exists or escapes root")
        result = self._run(("worktree", "add", "-b", self.expected_branch(task_id), str(target), base_sha))
        if result.returncode != 0:
            raise GitSafetyError("worktree creation failed")
        return target

    def snapshot(self, cwd: Path) -> GitSnapshot:
        head_result = self._run(("rev-parse", "HEAD"), cwd)
        branch_result = self._run(("rev-parse", "--abbrev-ref", "HEAD"), cwd)
        worktree_result = self._run(("worktree", "list", "--porcelain"), self.repo_root)
        origin_result = self._run(("remote", "get-url", "origin"), cwd)
        head = head_result.stdout.strip()
        branch = branch_result.stdout.strip()
        worktrees = worktree_result.stdout
        origin = origin_result.stdout.strip()
        worktree_paths = self.parse_worktree_porcelain(worktrees) if worktree_result.returncode == 0 else set()
        normalized_cwd = self._normalized_path(cwd.resolve())
        normalized_repo = self._normalized_path(self.repo_root)
        if (head_result.returncode != 0 or not SHA_PATTERN.fullmatch(head)
                or branch_result.returncode != 0 or not branch
                or worktree_result.returncode != 0 or not worktree_paths
                or normalized_cwd not in worktree_paths or normalized_repo not in worktree_paths
                or origin_result.returncode != 0 or origin.rstrip("/") != EXPECTED_ORIGIN.rstrip("/")):
            raise GitSafetyError("Git snapshot validation failed")
        return GitSnapshot(head, branch, worktrees, origin)

    @staticmethod
    def _normalized_path(path: Path) -> str:
        return str(path).replace("\\", "/").rstrip("/").casefold()

    @classmethod
    def parse_worktree_porcelain(cls, value: str) -> set[str]:
        if not isinstance(value, str) or not value.strip():
            raise GitSafetyError("Git worktree output is empty")
        normalized = value.replace("\r\n", "\n")
        if not normalized.endswith("\n\n"):
            raise GitSafetyError("Git worktree output is missing its record terminator")
        paths = set()
        for block in normalized[:-2].split("\n\n"):
            lines = [line for line in block.split("\n") if line]
            if not lines or not lines[0].startswith("worktree "):
                raise GitSafetyError("Git worktree output is malformed")
            raw_path = lines[0][len("worktree "):]
            if not raw_path or any(line.startswith("worktree ") for line in lines[1:]):
                raise GitSafetyError("Git worktree output is malformed")
            if not any(line.startswith("HEAD ") and SHA_PATTERN.fullmatch(line[5:]) for line in lines[1:]):
                raise GitSafetyError("Git worktree output is malformed")
            allowed = ("HEAD ", "branch refs/heads/", "detached", "bare", "locked", "prunable", "reason ")
            if any(not line.startswith(allowed) for line in lines[1:]):
                raise GitSafetyError("Git worktree output is malformed")
            paths.add(cls._normalized_path(Path(raw_path)))
        return paths

    def validate_worktree(self, task_id: UUID, path: Path, base_sha: str) -> None:
        if not isinstance(task_id, UUID) or not SHA_PATTERN.fullmatch(base_sha):
            raise GitSafetyError("invalid worktree input")
        if path.name != self.expected_worktree_name(task_id):
            raise GitSafetyError("worktree name mismatch")
        for candidate in (path, *path.parents):
            if candidate.is_symlink() or is_reparse_point(candidate):
                raise GitSafetyError("worktree reparse point is not allowed")
        expected = path.resolve()
        root = self._run(("rev-parse", "--show-toplevel"), expected)
        head = self._run(("rev-parse", "HEAD"), expected)
        branch = self._run(("rev-parse", "--abbrev-ref", "HEAD"), expected)
        if root.returncode != 0 or Path(root.stdout.strip()).resolve() != expected:
            raise GitSafetyError("worktree root mismatch")
        if head.returncode != 0 or head.stdout.strip() != base_sha:
            raise GitSafetyError("worktree base mismatch")
        if branch.returncode != 0 or branch.stdout.strip() != self.expected_branch(task_id):
            raise GitSafetyError("worktree branch mismatch")
        if path.is_symlink() or is_reparse_point(path):
            raise GitSafetyError("worktree reparse point is not allowed")

    def changed_files(self, cwd: Path) -> list[str]:
        result = self._run(("status", "--porcelain=v1", "-z", "--untracked-files=all"), cwd)
        if result.returncode != 0:
            raise GitSafetyError("changed file inspection failed")
        return self.parse_status_z(result.stdout)

    def staged_files(self, cwd: Path) -> list[str]:
        result = self._run(("status", "--porcelain=v1", "-z", "--untracked-files=all"), cwd)
        if result.returncode != 0:
            raise GitSafetyError("staged file inspection failed")
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
            if len(record) < 4:
                raise GitSafetyError("invalid NUL status record")
            status = record[:2]
            entries.append((status, record[3:]))
            if status[0] in "RC" or status[1] in "RC":
                if index >= len(records) or not records[index]:
                    raise GitSafetyError("invalid rename status record")
                entries.append((status, records[index]))
                index += 1
        return entries

    def diff_check(self, cwd: Path) -> str:
        result = self._run(("diff", "--check"), cwd)
        if result.returncode != 0:
            raise GitDiffCheckError("git diff --check failed")
        return result.stdout

    def diff_stat(self, cwd: Path) -> str:
        result = self._run(("diff", "--stat"), cwd)
        if result.returncode != 0:
            raise GitSafetyError("git diff stat failed")
        return result.stdout[:4000]
