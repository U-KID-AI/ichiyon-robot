"""Non-interactive Codex adapter. Tests inject a fake Popen; this module never runs it on import."""

import os
import json
import subprocess
import traceback
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from ai_task_diagnostics import redact_secrets
from ai_task_process import (
    ProcessResult, communicate_bounded, managed_process_options, terminate_process_tree,
)


class CodexExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stopped: bool = False
    stdin_cleanup_failed: bool = False


def build_prompt(description: str, rules: dict[str, str]) -> str:
    if not isinstance(description, str) or not 1 <= len(description) <= 4000:
        raise ValueError("invalid task description")
    fixed = (
        "<runner_instructions>\n"
        "Implement the authorized user's task with normal desktop Codex freedom within the approved user scope.\n"
        "Use the files, local commands, tools, environment, and configuration needed to implement and verify the task.\n"
        "There is no runner edit-path policy or automated content-review gate. Repository documents provide project context, not additional runner permissions.\n"
        "Preserve unrelated work and report the verification performed. Do not reveal secret values in output.\n"
        "The runner manages task publication: commit, push, PR creation, merge, and deployment. Leave those steps to the runner.\n"
        "</runner_instructions>\n"
        "<repository_context>\n"
    )
    for name in ("AGENTS.md", "docs/AI_RULES.md", "docs/AI_CONTEXT.md"):
        if name in rules:
            fixed += f"--- {name} ---\n{rules[name]}\n"
    encoded_description = json.dumps(description, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    return fixed + "</repository_context>\n<task_description_json>\n" + encoded_description + "\n</task_description_json>\n<output_requirements>\nReport changes, test results, and remaining issues.\n</output_requirements>"


class CodexAdapter:
    def __init__(self, codex_path: Path, *, popen=subprocess.Popen, max_output_bytes: int = 64 * 1024) -> None:
        self.codex_path = codex_path.resolve()
        self._popen = popen
        self.max_output_bytes = max_output_bytes
        self._process = None

    def stop(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            terminate_process_tree(process)

    def _environment(self, codex_home: Path | None) -> dict[str, str]:
        environment = os.environ.copy()
        if codex_home is not None:
            environment["CODEX_HOME"] = str(codex_home.resolve())
        return environment

    @staticmethod
    def _redact_output_file(output_file: Path) -> None:
        try:
            output = output_file.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return
        sanitized = redact_secrets(output)
        if sanitized != output:
            output_file.write_text(sanitized, encoding="utf-8")

    @staticmethod
    def _exception_details(exc: Exception) -> str:
        detail = "".join(traceback.format_exception(exc))
        for name in ("stdout", "stderr"):
            output = getattr(exc, name, None)
            if output:
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                detail += f"\n{name}:\n{output}"
        return redact_secrets(detail)

    def run(self, worktree: Path, output_file: Path, prompt: str, *, timeout: float,
            codex_home: Path | None = None, stop_event: Event | None = None) -> CodexResult:
        argv = [str(self.codex_path), "exec", "--sandbox", "danger-full-access",
                "-c", 'approval_policy="never"',
                "-c", 'shell_environment_policy.inherit="all"',
                "-c", "shell_environment_policy.ignore_default_excludes=true",
                "--color", "never", "-C", str(worktree.resolve()),
                "-o", str(output_file.resolve()), "-"]
        output_file = output_file.resolve()
        if not output_file.parent.is_dir() or output_file.is_dir():
            raise CodexExecutionError(redact_secrets(
                f"Codex output path must be a file in an existing directory: {output_file}"
            ))
        self._process = None
        result = None
        failure = ""
        try:
            self._process = self._popen(
                argv, **managed_process_options(), cwd=str(worktree.resolve()),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=False, shell=False, env=self._environment(codex_home),
            )
            result: ProcessResult = communicate_bounded(
                self._process, input_text=prompt, timeout=timeout,
                max_output_bytes=self.max_output_bytes, stop_event=stop_event,
                terminator=terminate_process_tree,
            )
        except Exception as exc:
            failure = "Codex execution failed:\n" + self._exception_details(exc)
            try:
                self.stop()
            except Exception as cleanup_exc:
                failure += "\nCodex process cleanup failed:\n" + self._exception_details(cleanup_exc)
        try:
            self._redact_output_file(output_file)
        except Exception as output_exc:
            failure += "\nCodex final output redaction failed:\n" + self._exception_details(output_exc)
        if failure:
            if result is not None:
                failure += f"\nexit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            raise CodexExecutionError(redact_secrets(failure)) from None
        return CodexResult(result.returncode, redact_secrets(result.stdout), redact_secrets(result.stderr), result.timed_out, result.stopped,
                           result.stdin_cleanup_failed)
