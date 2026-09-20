"""Static, non-executing test registry for the Phase 2B local runner.

Phase 2B must never execute repository Python code outside the Codex sandbox.
Python changes are syntax-compiled in memory only.
"""

from dataclasses import dataclass
from pathlib import Path


MAX_PYTHON_FILE_BYTES = 1024 * 1024
MAX_PYTHON_FILES = 200


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
    # runner/process_terminator are intentionally unused in production.
    # They remain in the signature so LocalRunner has a stable interface.
    del runner, timeout, process_terminator

    selected = select_tests(changed_files)
    if not selected:
        return []

    _, python_files = selected

    if len(python_files) > MAX_PYTHON_FILES:
        return [
            TestResult(
                "python-syntax",
                1,
                f"too many Python files: {len(python_files)}",
            )
        ]

    root = repo_root.resolve()

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

        candidate = root / relative
        resolved = candidate.resolve()

        if root not in resolved.parents:
            raise TestRegistryError("Python test path escapes repository")

        try:
            size = resolved.stat().st_size
        except OSError as exc:
            raise TestRegistryError("Python test file cannot be inspected") from exc

        if size > MAX_PYTHON_FILE_BYTES:
            return [
                TestResult(
                    "python-syntax",
                    1,
                    f"{relative}: file is too large",
                )
            ]

        try:
            source = resolved.read_text(encoding="utf-8")
            compile(source, relative, "exec", dont_inherit=True)
        except (OSError, UnicodeError) as exc:
            return [
                TestResult(
                    "python-syntax",
                    1,
                    f"{relative}: cannot read UTF-8 source ({type(exc).__name__})",
                )
            ]
        except SyntaxError as exc:
            return [
                TestResult(
                    "python-syntax",
                    1,
                    f"{relative}:{exc.lineno or 0}: {exc.msg}",
                )
            ]

    message = f"syntax checked {len(python_files)} Python file(s)"
    return [TestResult("python-syntax", 0, message[:max_output_bytes])]
