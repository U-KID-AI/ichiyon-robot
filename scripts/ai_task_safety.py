"""Repository boundary checks shared by the local runner.

The AI task runner behaves like a remote-controlled desktop Codex session
inside its dedicated worktree. These checks protect Git/worktree integrity
and path traversal boundaries, but they do not block ordinary repository
edits by filename.
"""

import os
import re
from pathlib import Path
from uuid import UUID


SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


class SafetyError(RuntimeError):
    pass


def is_reparse_point(path: Path) -> bool:
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        raise SafetyError("path safety inspection failed")


def validate_project_codex_layer(worktree: Path) -> None:
    """Permit project Codex files, but fail on filesystem boundary tricks."""
    candidate = worktree / ".codex"
    try:
        candidate.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SafetyError("project Codex layer cannot be inspected") from exc
    if candidate.is_symlink() or is_reparse_point(candidate):
        raise SafetyError("project Codex layer is a reparse point")


def expected_branch(task_id: UUID) -> str:
    return "ai/task/" + str(task_id)


def expected_worktree_name(task_id: UUID) -> str:
    return "ai-task-" + str(task_id)


def validate_claim_names(task_id: UUID, branch_name: str, worktree_name: str) -> None:
    if not isinstance(task_id, UUID):
        raise SafetyError("invalid task ID")
    if branch_name != expected_branch(task_id) or worktree_name != expected_worktree_name(task_id):
        raise SafetyError("claim names do not match task ID")


def task_worktree_path(worktree_root: Path, task_id: UUID) -> Path:
    root = worktree_root.resolve()
    if worktree_root.is_symlink() or is_reparse_point(worktree_root) or is_reparse_point(root):
        raise SafetyError("worktree root is a reparse point")
    target = (root / expected_worktree_name(task_id)).resolve()
    if target.parent != root or target == root:
        raise SafetyError("worktree path escapes root")
    if target.exists() or target.is_symlink():
        raise SafetyError("worktree already exists")
    return target


def is_protected_path(relative_path: str) -> bool:
    """Return True only for paths that can corrupt or escape the Git worktree."""
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
        return True
    lower = normalized.lower()
    if lower == ".git" or lower.startswith(".git/"):
        return True
    return False


def validate_changed_paths(repo_root: Path, changed_files: list[str]) -> None:
    root = repo_root.resolve()
    for relative in changed_files:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or is_protected_path(relative):
            raise SafetyError(f"unsafe changed path: {relative}")
        resolved = (root / path).resolve()
        if root not in resolved.parents and resolved != root:
            raise SafetyError("changed path escapes repository")
        candidate = root / path
        try:
            candidate.lstat()
        except FileNotFoundError:
            # A missing path is valid for a tracked deletion. Parent paths
            # are still inspected below.
            pass
        except OSError as exc:
            raise SafetyError("changed path cannot be inspected") from exc
        else:
            if candidate.is_symlink() or is_reparse_point(candidate):
                raise SafetyError("symlink changed path is not allowed")
        current = candidate.parent
        while current != root and root in current.parents:
            try:
                current.lstat()
            except FileNotFoundError:
                current = current.parent
                continue
            except OSError as exc:
                raise SafetyError("changed path parent cannot be inspected") from exc
            if current.is_symlink() or is_reparse_point(current):
                raise SafetyError("changed path parent is a reparse point")
            current = current.parent


def validate_sha(value: str) -> None:
    if not isinstance(value, str) or not SHA_PATTERN.fullmatch(value):
        raise SafetyError("invalid SHA")
