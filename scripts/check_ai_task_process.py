"""Offline process lifecycle checks, independent of runner orchestration."""

import ast
import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai_task_codex import CodexAdapter, CodexExecutionError
from ai_task_process import (
    ProcessResult,
    ProcessTerminationError,
    communicate_bounded,
    managed_process_options,
    terminate_process_tree,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.pid = 43210
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"stdout")
        self.stderr = io.BytesIO(b"stderr")
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("offline process", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class BlockingStream:
    def __init__(self, on_enter=None):
        self.entered = threading.Event()
        self.released = threading.Event()
        self.finished = threading.Event()
        self.on_enter = on_enter

    def write(self, value):
        self.entered.set()
        if self.on_enter:
            self.on_enter()
        self.released.wait()

    def read(self, size):
        self.write(None)
        self.finished.set()
        return b""

    def close(self):
        self.finished.set()


class ProcessChecks(unittest.TestCase):
    def communicate(self, process, **kwargs):
        options = dict(input_text=None, timeout=1, max_output_bytes=32)
        options.update(kwargs)
        return communicate_bounded(process, **options)

    def test_exit_codes_output_and_stdin_close(self):
        for code in (0, 7, -9):
            with self.subTest(returncode=code):
                process = FakeProcess(code)
                terminator = Mock()
                result = self.communicate(process, terminator=terminator)
                self.assertEqual(result.returncode, code)
                self.assertEqual((result.stdout, result.stderr), ("stdout", "stderr"))
                self.assertFalse(result.timed_out or result.stopped or result.stdin_cleanup_failed)
                self.assertTrue(process.stdin.closed)
                terminator.assert_not_called()

    def test_unknown_returncode_is_not_success(self):
        process = FakeProcess(None)
        process.poll = lambda: 0
        process.wait = lambda **kwargs: None
        self.assertEqual(self.communicate(process).returncode, -1)

    def test_blocked_stdin_is_covered_by_timeout_and_cancellation(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                stop = threading.Event()
                process = FakeProcess(None)
                stream = process.stdin = BlockingStream(stop.set if cancelled else None)

                def terminate(proc):
                    proc.kill()
                    stream.released.set()

                terminator = Mock(side_effect=terminate)
                try:
                    started = time.monotonic()
                    result = self.communicate(
                        process, input_text="x" * 100000, timeout=0.1,
                        stop_event=stop, terminator=terminator,
                    )
                    self.assertLess(time.monotonic() - started, 5)
                    self.assertTrue(stream.entered.is_set())
                    self.assertTrue(stream.finished.is_set())
                    self.assertEqual(result.returncode, -9)
                    self.assertEqual(result.timed_out, not cancelled)
                    self.assertEqual(result.stopped, cancelled)
                    self.assertFalse(result.stdin_cleanup_failed)
                    terminator.assert_called_once_with(process)
                finally:
                    stream.released.set()
                    self.assertTrue(stream.finished.wait(2))

    def test_unfinished_stdin_writer_is_reported_and_fixture_is_released(self):
        process = FakeProcess(None)
        stream = process.stdin = BlockingStream()
        terminator = Mock(side_effect=lambda proc: proc.kill())
        try:
            result = self.communicate(process, input_text="x", timeout=0.1, terminator=terminator)
            self.assertTrue(result.timed_out)
            self.assertTrue(result.stdin_cleanup_failed)
            self.assertEqual(result.returncode, -9)
            terminator.assert_called_once_with(process)
        finally:
            stream.released.set()
            self.assertTrue(stream.finished.wait(2))

    def test_broken_pipe_still_closes_stdin(self):
        process = FakeProcess()
        process.stdin = Mock()
        process.stdin.write.side_effect = BrokenPipeError()
        result = self.communicate(process, input_text="offline prompt")
        process.stdin.write.assert_called_once_with(b"offline prompt")
        process.stdin.close.assert_called_once_with()
        self.assertFalse(result.stdin_cleanup_failed)

    def test_combined_output_budget_still_drains_both_streams(self):
        for limit in (0, 100):
            with self.subTest(limit=limit):
                process = FakeProcess()
                process.stdout = io.BytesIO(b"o" * 20000)
                process.stderr = io.BytesIO(b"e" * 20000)
                result = self.communicate(process, max_output_bytes=limit)
                self.assertEqual(len(result.stdout.encode()) + len(result.stderr.encode()), limit)
                self.assertEqual(process.stdout.tell(), 20000)
                self.assertEqual(process.stderr.tell(), 20000)

    def test_invalid_output_is_decoded_without_losing_exit_status(self):
        process = FakeProcess(2)
        process.stdout = io.BytesIO(b"before\xffafter")
        process.stderr = io.StringIO("text stream")
        result = self.communicate(process)
        self.assertEqual(result.stdout, "before\ufffdafter")
        self.assertEqual(result.stderr, "text stream")
        self.assertEqual(result.returncode, 2)

    def test_cancellation_remains_visible_after_parent_exit(self):
        stop = threading.Event()
        stop.set()
        process = FakeProcess()
        terminator = Mock()
        result = self.communicate(process, stop_event=stop, terminator=terminator)
        self.assertTrue(result.stopped)
        self.assertFalse(result.timed_out)
        terminator.assert_called_once_with(process)

    def test_termination_failure_is_not_reported_as_a_process_result(self):
        stop = threading.Event()
        stop.set()
        process = FakeProcess(None)
        self.addCleanup(process.stdin.close)
        terminator = Mock(side_effect=ProcessTerminationError("offline termination failure"))
        with self.assertRaisesRegex(ProcessTerminationError, "offline termination failure"):
            self.communicate(process, stop_event=stop, terminator=terminator)

    def test_wait_failure_attempts_tree_cleanup(self):
        process = FakeProcess()
        process.wait = Mock(side_effect=subprocess.TimeoutExpired("offline", 5))
        terminator = Mock()
        self.communicate(process, terminator=terminator)
        process.wait.assert_called_once_with(timeout=5)
        terminator.assert_called_once_with(process)

    def test_descendant_output_pipes_are_cleaned_after_parent_exit(self):
        process = FakeProcess()
        stream = process.stdout = BlockingStream()
        terminator = Mock(side_effect=lambda proc: stream.released.set())
        try:
            result = self.communicate(process, terminator=terminator)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(stream.finished.is_set())
            terminator.assert_called_once_with(process)
        finally:
            stream.released.set()
            self.assertTrue(stream.finished.wait(2))

    def test_unreleased_output_pipe_cannot_look_successful(self):
        process = FakeProcess()
        stream = process.stdout = BlockingStream()
        terminator = Mock()
        try:
            with self.assertRaisesRegex(ProcessTerminationError, "output cleanup"):
                self.communicate(process, terminator=terminator)
            terminator.assert_called_once_with(process)
        finally:
            stream.released.set()
            self.assertTrue(stream.finished.wait(2))

    def test_invalid_limits_fail_before_io(self):
        for options in ({"timeout": 0}, {"timeout": -1}, {"max_output_bytes": -1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.communicate(object(), **options)

    def test_managed_session_options(self):
        for platform, expected in (("nt", {}), ("posix", {"start_new_session": True})):
            with self.subTest(platform=platform), patch("ai_task_process.os.name", platform):
                self.assertEqual(managed_process_options(), expected)

    def test_shared_process_launches_use_managed_sessions(self):
        launches = set()
        for path in (ROOT / "scripts").glob("ai_task_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("Popen", "_popen")):
                    continue
                launches.add(path.stem)
                self.assertTrue(any(
                    keyword.arg is None and isinstance(keyword.value, ast.Call)
                    and isinstance(keyword.value.func, ast.Name)
                    and keyword.value.func.id == "managed_process_options"
                    for keyword in node.keywords
                ), f"{path.name}:{node.lineno}")
        self.assertIn("ai_task_codex", launches)


class WindowsTerminationChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.taskkill = self.root / "System32" / "taskkill.exe"
        self.taskkill.parent.mkdir()
        self.taskkill.touch()
        self.missing_root = self.root / "missing"

    def terminate(self, process, runner, root=None):
        with patch("ai_task_process.os.name", "nt"):
            terminate_process_tree(process, runner=runner, system_root=root or self.root)

    def test_taskkill_uses_pid_and_entire_tree(self):
        process = FakeProcess(None)

        def kill_tree(*args, **kwargs):
            process.returncode = -9
            return Mock(returncode=0)

        runner = Mock(side_effect=kill_tree)
        self.terminate(process, runner)
        runner.assert_called_once_with(
            [str(self.taskkill.resolve()), "/PID", str(process.pid), "/T", "/F"],
            shell=False, capture_output=True, timeout=10, check=False,
        )
        self.assertFalse(process.terminated)

    def test_taskkill_exception_attempts_parent_cleanup_but_still_fails(self):
        process = FakeProcess(None)
        with self.assertRaisesRegex(ProcessTerminationError, "taskkill invocation failed"):
            self.terminate(process, Mock(side_effect=TimeoutError()))
        self.assertTrue(process.terminated)
        self.assertIsNotNone(process.poll())

    def test_failed_tree_kill_does_not_accept_parent_cleanup_as_success(self):
        process = FakeProcess(None)
        with self.assertRaisesRegex(ProcessTerminationError, "tree termination failed"):
            self.terminate(process, Mock(return_value=Mock(returncode=1)))
        self.assertTrue(process.terminated)

    def test_taskkill_success_must_still_verify_parent_exit(self):
        process = FakeProcess(None)
        with patch("ai_task_process.time.monotonic", side_effect=range(0, 100, 6)):
            with self.assertRaisesRegex(ProcessTerminationError, "tree termination failed"):
                self.terminate(process, Mock(return_value=Mock(returncode=0)))
        self.assertTrue(process.terminated)

    def test_taskkill_exit_race_is_safe(self):
        self.terminate(FakeProcess(0), Mock(return_value=Mock(returncode=1)))

    def test_missing_taskkill_is_not_success(self):
        runner = Mock()
        process = FakeProcess(None)
        with self.assertRaisesRegex(ProcessTerminationError, "unavailable"):
            self.terminate(process, runner, self.missing_root)
        runner.assert_not_called()
        self.assertTrue(process.terminated)

    def test_unverifiable_parent_fails_after_terminate_and_kill(self):
        process = FakeProcess(None)
        process.terminate = Mock()
        process.kill = Mock()
        with self.assertRaisesRegex(ProcessTerminationError, "could not be verified"):
            self.terminate(process, Mock(return_value=Mock(returncode=1)))
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()


@unittest.skipUnless(os.name == "posix", "POSIX process groups required")
class PosixTerminationChecks(unittest.TestCase):
    def test_sigterm_escalates_to_sigkill_and_verifies_group_disappearance(self):
        process = FakeProcess()
        signals = []

        def killpg(pid, sig):
            self.assertEqual(pid, process.pid)
            signals.append(sig)
            if sig == 0 and signal.SIGKILL in signals:
                raise ProcessLookupError()

        with patch("os.getpgid", return_value=process.pid), patch("os.getpgrp", return_value=123), \
                patch("os.killpg", side_effect=killpg), \
                patch("time.monotonic", side_effect=range(0, 100, 6)):
            terminate_process_tree(process)
        self.assertEqual(signals, [signal.SIGTERM, 0, signal.SIGKILL, 0])

    def test_permission_denial_or_surviving_group_is_not_success(self):
        process = FakeProcess()
        for failure in (PermissionError(), None):
            with self.subTest(failure=failure), patch("os.getpgid", return_value=process.pid), \
                    patch("os.getpgrp", return_value=123), patch("os.killpg", side_effect=failure), \
                    patch("time.monotonic", side_effect=range(0, 100, 6)):
                with self.assertRaises(ProcessTerminationError):
                    terminate_process_tree(process)

    def test_non_dedicated_group_is_never_signalled(self):
        with patch("os.getpgid", return_value=123), patch("os.getpgrp", return_value=123), \
                patch("os.killpg") as kill:
            with self.assertRaises(ProcessTerminationError):
                terminate_process_tree(FakeProcess())
            kill.assert_not_called()

    def test_own_group_and_invalid_pid_are_never_signalled(self):
        for pid in (None, True, 0, 1, -1, 123):
            process = FakeProcess()
            process.pid = pid
            with self.subTest(pid=pid), patch("os.getpgrp", return_value=123), \
                    patch("os.killpg") as kill:
                with self.assertRaises(ProcessTerminationError):
                    terminate_process_tree(process)
                kill.assert_not_called()

    def test_disappeared_group_requires_exited_parent(self):
        for returncode in (0, None):
            with self.subTest(returncode=returncode), patch("os.getpgid", side_effect=ProcessLookupError()), \
                    patch("os.getpgrp", return_value=123), patch("os.killpg", side_effect=ProcessLookupError()):
                if returncode is None:
                    with self.assertRaisesRegex(ProcessTerminationError, "escaped"):
                        terminate_process_tree(FakeProcess(returncode))
                else:
                    terminate_process_tree(FakeProcess(returncode))

    def test_real_timeout_and_cancellation_kill_grandchild_group(self):
        # The parent reaps its child, avoiding dependence on the host's PID 1.
        code = """import signal, subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import signal; signal.alarm(20); signal.pause()'])
def stop(sig, frame):
    child.wait(timeout=3)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
signal.alarm(20)
print('ready', flush=True)
signal.pause()
"""
        import select

        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                process = subprocess.Popen(
                    [sys.executable, "-c", code], shell=False,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, **managed_process_options(),
                )
                try:
                    self.assertTrue(select.select([process.stdout], [], [], 5)[0])
                    self.assertEqual(process.stdout.readline(), b"ready\n")
                    stop = threading.Event()
                    if cancelled:
                        stop.set()
                    result = communicate_bounded(
                        process, input_text=None, timeout=0.1, max_output_bytes=100, stop_event=stop,
                    )
                    self.assertEqual(result.timed_out, not cancelled)
                    self.assertEqual(result.stopped, cancelled)
                    self.assertFalse(result.stdin_cleanup_failed)
                    self.assertIsNotNone(process.poll())
                    with self.assertRaises(ProcessLookupError):
                        os.killpg(process.pid, 0)
                finally:
                    try:
                        terminate_process_tree(process)
                    finally:
                        process.stdout.close()
                        process.stderr.close()


class CodexProcessChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.process = FakeProcess()
        self.popen = Mock(return_value=self.process)
        self.adapter = CodexAdapter(Path(sys.executable), popen=self.popen)

    def run_codex(self, **kwargs):
        return self.adapter.run(self.root, self.root / "result.txt", "offline prompt", timeout=1, **kwargs)

    def test_process_flags_are_preserved(self):
        for flags in ({"timed_out": True}, {"stopped": True}, {"stdin_cleanup_failed": True}):
            result = ProcessResult(-9, "stdout", "stderr", **flags)
            with self.subTest(flags=flags), patch("ai_task_codex.communicate_bounded", return_value=result) as communicate:
                stop = threading.Event()
                actual = self.run_codex(stop_event=stop)
                self.assertEqual(actual.returncode, -9)
                self.assertEqual(actual.timed_out, result.timed_out)
                self.assertEqual(actual.stopped, result.stopped)
                self.assertEqual(actual.stdin_cleanup_failed, result.stdin_cleanup_failed)
                self.assertIs(communicate.call_args.kwargs["stop_event"], stop)

    def test_process_handling_failure_attempts_cleanup(self):
        self.process.returncode = None
        failure = ProcessTerminationError("offline process failure")
        with patch("ai_task_codex.communicate_bounded", side_effect=failure), \
                patch("ai_task_codex.terminate_process_tree") as terminate:
            with self.assertRaises(CodexExecutionError) as caught:
                self.run_codex()
            terminate.assert_called_once_with(self.process)
            self.assertIn("offline process failure", str(caught.exception))

    def test_process_and_cleanup_failure_remains_an_error(self):
        self.process.returncode = None
        cleanup = ProcessTerminationError("offline cleanup failure")
        with patch("ai_task_codex.communicate_bounded", side_effect=ProcessTerminationError("offline process failure")), \
                patch("ai_task_codex.terminate_process_tree", side_effect=cleanup):
            with self.assertRaises(CodexExecutionError) as caught:
                self.run_codex()
            self.assertIn("offline process failure", str(caught.exception))
            self.assertIn("offline cleanup failure", str(caught.exception))

    def test_stop_only_terminates_active_process(self):
        with patch("ai_task_codex.terminate_process_tree") as terminate:
            self.adapter.stop()
            self.adapter._process = self.process
            self.adapter.stop()
            terminate.assert_not_called()
            self.process.returncode = None
            self.adapter.stop()
            terminate.assert_called_once_with(self.process)


if __name__ == "__main__":
    unittest.main(verbosity=2)
