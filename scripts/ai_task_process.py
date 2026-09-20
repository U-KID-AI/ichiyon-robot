"""Bounded-output process helpers with Windows process-tree termination."""

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class ProcessTerminationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stopped: bool = False
    stdin_cleanup_failed: bool = False


class _SharedBudget:
    def __init__(self, limit: int) -> None:
        self.remaining = limit
        self.lock = threading.Lock()

    def take(self, chunk: bytes) -> bytes:
        with self.lock:
            if self.remaining <= 0:
                return b""
            value = chunk[:self.remaining]
            self.remaining -= len(value)
            return value


def _drain(stream, budget: _SharedBudget, target: bytearray) -> None:
    while True:
        chunk = stream.read(4096)
        if not chunk:
            return
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8", errors="replace")
        target.extend(budget.take(chunk))


def terminate_process_tree(process, *, runner: Callable[..., object] | None = None,
                           system_root: Path | None = None) -> None:
    def alive() -> bool:
        try:
            return process.poll() is None
        except Exception:
            return True

    def bounded_parent_cleanup() -> None:
        terminate = getattr(process, "terminate", None)
        try:
            if terminate:
                terminate()
        except Exception as exc:
            raise ProcessTerminationError("parent process termination failed") from exc
        try:
            process.wait(timeout=5)
        except Exception:
            kill = getattr(process, "kill", None)
            try:
                if kill:
                    kill()
            except Exception as exc:
                raise ProcessTerminationError("parent process kill failed") from exc
        if alive():
            raise ProcessTerminationError("process termination could not be verified")

    pid = getattr(process, "pid", None)
    if os.name == "nt" and isinstance(pid, int) and pid > 0:
        root = system_root or Path(os.environ.get("SystemRoot", r"C:\Windows"))
        taskkill = (root / "System32" / "taskkill.exe").resolve()
        if taskkill.is_file():
            command_runner = runner or subprocess.run
            try:
                result = command_runner([str(taskkill), "/PID", str(pid), "/T", "/F"], shell=False,
                                        capture_output=True, timeout=10, check=False)
            except Exception as exc:
                try:
                    bounded_parent_cleanup()
                except Exception:
                    pass
                raise ProcessTerminationError("taskkill invocation failed") from exc
            if getattr(result, "returncode", None) == 0:
                deadline = time.monotonic() + 5
                while alive() and time.monotonic() < deadline:
                    time.sleep(0.05)
                if not alive():
                    return
            elif not alive():
                return
            bounded_parent_cleanup()
            raise ProcessTerminationError("Windows process tree termination failed")
        bounded_parent_cleanup()
        raise ProcessTerminationError("Windows taskkill executable is unavailable")
    bounded_parent_cleanup()


def communicate_bounded(process, *, input_text: str | None, timeout: float, max_output_bytes: int,
                        stop_event=None, terminator: Callable[[object], None] | None = None) -> ProcessResult:
    if max_output_bytes < 0 or timeout <= 0:
        raise ValueError("invalid process limits")
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    budget = _SharedBudget(max_output_bytes)
    # Start the deadline before any potentially blocking stdin write begins.
    deadline = time.monotonic() + timeout
    readers = []
    for stream, target in ((getattr(process, "stdout", None), stdout_buffer), (getattr(process, "stderr", None), stderr_buffer)):
        if stream is not None:
            thread = threading.Thread(target=_drain, args=(stream, budget, target), daemon=True)
            thread.start()
            readers.append(thread)
    stdin = getattr(process, "stdin", None)
    stdin_writer = None
    writer_done = threading.Event()
    if stdin is not None and input_text is not None:
        def write_stdin():
            try:
                stdin.write(input_text.encode("utf-8"))
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    stdin.close()
                except Exception:
                    pass
                writer_done.set()
        stdin_writer = threading.Thread(target=write_stdin, name="ai-task-stdin", daemon=True)
        stdin_writer.start()
    timed_out = False
    stopped = False
    termination_requested = False
    while True:
        if stop_event is not None and stop_event.is_set():
            stopped = True
            if not termination_requested:
                termination_requested = True
                (terminator or terminate_process_tree)(process)
        if getattr(process, "poll")() is not None:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            if not termination_requested:
                termination_requested = True
                (terminator or terminate_process_tree)(process)
            break
        time.sleep(0.05)
    try:
        process.wait(timeout=5)
    except Exception:
        (terminator or terminate_process_tree)(process)
    stdin_cleanup_failed = False
    if stdin_writer is not None:
        stdin_writer.join(timeout=2)
        stdin_cleanup_failed = stdin_writer.is_alive() or not writer_done.is_set()
    elif stdin is not None:
        try:
            stdin.close()
        except Exception:
            stdin_cleanup_failed = True
    for thread in readers:
        thread.join(timeout=2)
    return ProcessResult(
        (getattr(process, "returncode", None) if getattr(process, "returncode", None) is not None else -1),
        bytes(stdout_buffer).decode("utf-8", errors="replace"),
        bytes(stderr_buffer).decode("utf-8", errors="replace"),
        timed_out=timed_out,
        stopped=stopped,
        stdin_cleanup_failed=stdin_cleanup_failed,
    )
