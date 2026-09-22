"""Task identity, worktree naming, and publication metadata helpers.

Repository edits and project configuration are not subject to a separate runner
content policy. Keep this module independent of Codex and publication adapters.
"""

import re
from pathlib import Path
from uuid import UUID


SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


class RuntimeOperationError(RuntimeError):
    pass


def expected_branch(task_id: UUID) -> str:
    return "ai/task/" + str(task_id)


def expected_worktree_name(task_id: UUID) -> str:
    return "ai-task-" + str(task_id)


def validate_claim_names(task_id: UUID, branch_name: str, worktree_name: str) -> None:
    if not isinstance(task_id, UUID):
        raise RuntimeOperationError("invalid task ID")
    if branch_name != expected_branch(task_id) or worktree_name != expected_worktree_name(task_id):
        raise RuntimeOperationError("claim names do not match task ID")


def task_worktree_path(worktree_root: Path, task_id: UUID) -> Path:
    if not isinstance(task_id, UUID):
        raise RuntimeOperationError("invalid task ID")
    root = worktree_root.resolve()
    target = root / expected_worktree_name(task_id)
    # A dangling link still occupies the task's name; never overwrite it.
    if target.exists() or target.is_symlink():
        raise RuntimeOperationError("worktree already exists")
    return target


def validate_sha(value: str) -> None:
    if not isinstance(value, str) or not SHA_PATTERN.fullmatch(value):
        raise RuntimeOperationError("invalid SHA")
