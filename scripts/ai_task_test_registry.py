"""Lightweight syntax checks supplementing Codex's task-specific verification.

Codex may run local project tests and commands normally. This registry adds an
in-memory Python syntax check; it is not an execution or edit-policy allowlist.
"""

import math
import time
from dataclasses import dataclass
from pathlib import Path

from ai_task_diagnostics import redact_secrets


@dataclass(frozen=True)
class TestResult:
    name: str
    returncode: int
    output: str
    timed_out: bool = False
    stopped: bool = False


class TestRegistryError(RuntimeError):
    pass


def select_tests(changed_files: list[str]) -> list[tuple[str, list[str]]]:
    python_files = [
        path
        for path in changed_files
        if path.replace("\\", "/").lower().endswith(".py")
    ]
    if not python_files:
        return []
    return [("python-syntax", python_files)]


def run_tests(
    repo_root: Path,
    changed_files: list[str],
    *,
    runner=None,
    timeout: float = 300,
    max_output_bytes: int = 64 * 1024,
    stop_event=None,
    process_terminator=None,
) -> list[TestResult]:
    # Kept for callers that also inject subprocess-based test implementations.
    del runner, process_terminator
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("test timeout must be positive")
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise ValueError("test output limit must be a positive integer")
    deadline = time.monotonic() + timeout

    selected = select_tests(changed_files)
    if not selected:
        return []

    _, python_files = selected[0]

    root = repo_root.resolve()
    checked = 0

    for relative in python_files:
        if stop_event is not None and stop_event.is_set():
            return [
                TestResult(
                    "python-syntax",
                    -1,
                    "stopped because the task lease was lost",
                    stopped=True,
                )
            ]

        if time.monotonic() >= deadline:
            return [TestResult("python-syntax", -1, "syntax check timed out", timed_out=True)]

        try:
            source = (root / relative).read_bytes()
            compile(source, relative, "exec", dont_inherit=True)
            checked += 1
        except FileNotFoundError:
            # Deleted files (including rename sources) have no source to check.
            continue
        except (OSError, UnicodeError) as exc:
            return [
                TestResult(
                    "python-syntax",
                    1,
                    redact_secrets(f"{relative}: {type(exc).__name__}: {exc}")[:max_output_bytes],
                )
            ]
        except SyntaxError as exc:
            return [
                TestResult(
                    "python-syntax",
                    1,
                    redact_secrets(f"{relative}:{exc.lineno or 0}: {exc.msg}")[:max_output_bytes],
                )
            ]

    if stop_event is not None and stop_event.is_set():
        return [TestResult("python-syntax", -1, "syntax check stopped", stopped=True)]
    if time.monotonic() >= deadline:
        return [TestResult("python-syntax", -1, "syntax check timed out", timed_out=True)]
    message = f"syntax checked {checked} Python file(s)"
    return [TestResult("python-syntax", 0, message[:max_output_bytes])]
