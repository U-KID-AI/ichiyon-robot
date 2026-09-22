"""Configuration for the Windows/Linux local AI task runner.

This module intentionally reads only process environment variables. It does not
load dotenv files or import the Bot configuration.
"""

import os
import sys
import re
import math
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


RUNNER_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
LOCAL_HTTP_HOSTS = {"127.0.0.1", "localhost"}


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def validate_api_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API URL must not contain credentials, query, or fragment")
    if parsed.scheme == "https" and parsed.hostname:
        return value.rstrip("/")
    if parsed.scheme == "http" and parsed.hostname in LOCAL_HTTP_HOSTS:
        return value.rstrip("/")
    raise ValueError("API URL must use HTTPS, except localhost HTTP")


def validate_runner_id(value: str) -> str:
    if not RUNNER_ID_PATTERN.fullmatch(value):
        raise ValueError("invalid runner ID")
    return value


def _validate_root(path: Path, name: str) -> Path:
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"{name} must be an existing absolute directory")
    return path.resolve()


def _validate_executable(path: Path, name: str, repo_root: Path, worktree_root: Path) -> Path:
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{name} must be an existing absolute file")
    return path.resolve()


def _validate_gcm_executable(
    path: Path,
    git_path: Path,
    repo_root: Path,
    worktree_root: Path,
) -> Path:
    return _validate_executable(
        path,
        "Git Credential Manager path",
        repo_root,
        worktree_root,
    )


@dataclass(frozen=True)
class RunnerConfig:
    api_base_url: str
    api_token: str
    runner_id: str
    repo_root: Path
    worktree_root: Path
    codex_path: Path
    codex_home: Path | None
    git_path: Path
    poll_seconds: float
    codex_timeout_seconds: float
    api_timeout_seconds: float = 15.0
    max_api_response_bytes: int = 256 * 1024
    heartbeat_seconds: float = 30.0
    gh_path: Path | None = None
    gcm_path: Path | None = None
    max_attempts: int = 5

    def __post_init__(self):
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 10:
            raise ValueError("max attempts must be an integer from 1 through 10")

    @classmethod
    def from_environment(cls) -> "RunnerConfig":
        attempts = os.environ.get("AI_TASK_RUNNER_MAX_ATTEMPTS", "5")
        if not re.fullmatch(r"[0-9]+", attempts) or not 1 <= int(attempts) <= 10:
            raise ValueError("AI_TASK_RUNNER_MAX_ATTEMPTS must be an integer from 1 through 10")
        poll_seconds = float(os.environ.get("AI_TASK_RUNNER_POLL_SECONDS", "5"))
        timeout = float(os.environ.get("AI_TASK_RUNNER_CODEX_TIMEOUT_SECONDS", "1800"))
        if not math.isfinite(poll_seconds) or poll_seconds < 5:
            raise ValueError("poll interval must be at least 5 seconds")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Codex timeout must be positive")
        codex_home_value = os.environ.get("AI_TASK_RUNNER_CODEX_HOME", "").strip()
        if not codex_home_value:
            codex_home_value = os.environ.get("CODEX_HOME", "").strip() or str(Path.home() / ".codex")
        repo_root = _validate_root(Path(_required("AI_TASK_RUNNER_REPO_ROOT")), "repo root")
        worktree_root = _validate_root(Path(_required("AI_TASK_RUNNER_WORKTREE_ROOT")), "worktree root")
        codex_path = _validate_executable(Path(_required("AI_TASK_RUNNER_CODEX_PATH")), "Codex path", repo_root, worktree_root)
        git_path = _validate_executable(Path(_required("AI_TASK_RUNNER_GIT_PATH")), "Git path", repo_root, worktree_root)
        gh_path = _validate_executable(Path(_required("AI_TASK_RUNNER_GH_PATH")), "GitHub CLI path", repo_root, worktree_root)
        gcm_path = None
        if sys.platform == "win32":
            gcm_value = os.environ.get("AI_TASK_RUNNER_GCM_PATH", "").strip()
            if gcm_value:
                gcm_path = _validate_gcm_executable(
                    Path(gcm_value), git_path, repo_root, worktree_root,
                )
        elif sys.platform != "linux":
            raise ValueError("runner supports Windows and Linux only")
        codex_home = _validate_root(Path(codex_home_value), "Codex home")
        return cls(
            api_base_url=validate_api_base_url(_required("AI_TASK_RUNNER_API_BASE_URL")),
            api_token=_required("AI_TASK_RUNNER_API_TOKEN"),
            runner_id=validate_runner_id(_required("AI_TASK_RUNNER_ID")),
            repo_root=repo_root,
            worktree_root=worktree_root,
            codex_path=codex_path,
            codex_home=codex_home,
            git_path=git_path,
            poll_seconds=poll_seconds,
            gh_path=gh_path,
            gcm_path=gcm_path,
            codex_timeout_seconds=timeout,
            max_attempts=int(attempts),
        )
