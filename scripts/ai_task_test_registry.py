"""Offline fixed test registry for Phase 2B."""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class TestResult:
    name: str
    returncode: int
    output: str
    timed_out: bool = False
    stopped: bool = False


ALLOWED_CHECKS = {
    "scripts/check_ai_tasks.py",
    "scripts/check_ai_task_control_plane.py",
    "scripts/check_admin_user_management.py",
    "scripts/check_admin_feature_flags.py",
}


class TestRegistryError(RuntimeError):
    pass


def select_tests(changed_files: list[str]) -> list[tuple[str, list[str]]]:
    selected: list[tuple[str, list[str]]] = []
    if any(path.endswith(".py") for path in changed_files):
        selected.append(("python-compile", [sys.executable, "-m", "compileall", *[path for path in changed_files if path.endswith(".py")]]))
    if any(path.startswith("admin/") or path.startswith("admin\\") for path in changed_files):
        selected.extend((check, [sys.executable, check]) for check in ("scripts/check_admin_user_management.py", "scripts/check_admin_feature_flags.py"))
    if any("ai_task" in path.replace("\\", "/") or path.startswith("migrations/") for path in changed_files):
        selected.extend((check, [sys.executable, check]) for check in ("scripts/check_ai_tasks.py", "scripts/check_ai_task_control_plane.py"))
    return selected


def run_tests(repo_root: Path, changed_files: list[str], *, runner: Callable[..., object] | None = None,
              timeout: float = 300, max_output_bytes: int = 64 * 1024, stop_event=None,
              process_terminator=None) -> list[TestResult]:
    if runner is not None:
        return _run_fake_tests(repo_root, changed_files, runner, timeout, max_output_bytes)
    from ai_task_process import ProcessTerminationError, communicate_bounded, terminate_process_tree
    run_terminator = process_terminator or terminate_process_tree
    results = []
    for name, argv in select_tests(changed_files):
        process = subprocess.Popen(argv, cwd=str(repo_root.resolve()), shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        try:
            result = communicate_bounded(process, input_text=None, timeout=timeout,
                                         max_output_bytes=max_output_bytes, stop_event=stop_event,
                                         terminator=run_terminator)
        except ProcessTerminationError as exc:
            raise TestRegistryError("test process termination could not be verified") from exc
        output = (result.stdout + "\n" + result.stderr)[-max_output_bytes:]
        results.append(TestResult(name, result.returncode, output, result.timed_out, result.stopped))
        if result.returncode != 0 or result.timed_out or result.stopped:
            break
    return results


def _run_fake_tests(repo_root, changed_files, runner, timeout, max_output_bytes):
    results = []
    for name, argv in select_tests(changed_files):
        result = runner(argv, cwd=str(repo_root.resolve()), shell=False, timeout=timeout, check=False)
        output = (result.stdout + "\n" + result.stderr)[-max_output_bytes:]
        results.append(TestResult(name, result.returncode, output))
        if result.returncode != 0:
            break
    return results
