"""Safety checks shared by the local runner."""

import os
import re
from pathlib import Path
from uuid import UUID


PROTECTED_EXACT = {
    "Dockerfile", ".dockerignore", "docker-compose.yml", "docker-compose.prod.yml",
    "scripts/ai_task_deploy.py", "scripts/ai_task_deploy_config.py",
    "scripts/ai_task_deploy_remote.sh", "scripts/check_ai_task_deploy.py",
    "AGENTS.md", "docs/AI_RULES.md", ".gitattributes",
    "admin/ai_tasks_internal.py",
    "bot/repositories/ai_tasks.py", "scripts/ai_task_runner.py",
    "scripts/ai_task_runner_config.py", "scripts/ai_task_api_client.py",
    "scripts/ai_task_git.py", "scripts/ai_task_codex.py",
    "scripts/ai_task_test_registry.py", "scripts/ai_task_safety.py",
    "scripts/ai_task_process.py", "scripts/ai_task_publish.py",
    "scripts/ai_task_github.py",
    "scripts/ai_task_review_merge.py",
    "scripts/ai_task_auto_merge.py",
    "scripts/ai_task_code_review.py",
    "scripts/check_ai_task_review_merge.py",
    "scripts/check_ai_task_auto_merge.py",
    "scripts/check_ai_task_code_review.py",
    "scripts/check_ai_task_phase2d_control_plane.py",
    "docs/AI_CONTEXT.md", "docs/AI_RUNBOOK.md", "docs/AI_TASKS.md",
    ".gitmodules",
    "scripts/check_ai_tasks.py", "scripts/check_ai_task_control_plane.py",
    "scripts/check_ai_task_publish.py", "scripts/check_ai_task_github.py",
    "scripts/check_admin_user_management.py", "scripts/check_admin_feature_flags.py",
    "scripts/check_ai_task_local_runner.py",
}
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


class SafetyError(RuntimeError):
    pass


def is_reparse_point(path: Path) -> bool:
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        raise SafetyError("path safety inspection failed")


def validate_project_codex_layer(worktree: Path) -> None:
    candidate = worktree / ".codex"
    try:
        candidate.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SafetyError("project Codex layer cannot be inspected") from exc
    raise SafetyError("project Codex layer requires human review")


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
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/"):
        return True
    lower = normalized.lower()
    folded = normalized.casefold()
    protected = {path.casefold() for path in PROTECTED_EXACT}
    if folded in protected or folded.startswith(".github/workflows/"):
        return True
    if folded == ".codex" or folded.startswith(".codex/") or lower.endswith(".rules"):
        return True
    if lower.startswith("secrets/") or lower.startswith(".ssh/"):
        return True
    if lower == ".env" or lower.startswith(".env."):
        return True
    return lower.endswith((".pem", ".key", ".p12"))


def validate_changed_paths(repo_root: Path, changed_files: list[str]) -> None:
    root = repo_root.resolve()
    for relative in changed_files:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or is_protected_path(relative):
            raise SafetyError("protected or unsafe changed path")
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
            if current.is_symlink() or is_reparse_point(current):
                raise SafetyError("changed path parent is a reparse point")
            current = current.parent


def validate_sha(value: str) -> None:
    if not isinstance(value, str) or not SHA_PATTERN.fullmatch(value):
        raise SafetyError("invalid SHA")
