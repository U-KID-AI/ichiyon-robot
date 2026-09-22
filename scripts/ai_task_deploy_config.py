"""Trusted transport configuration. Never reads dotenv or credential contents."""

import ipaddress
import math
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ai_task_runtime import RuntimeOperationError
from ai_task_diagnostics import redact_secrets


class DeploymentError(RuntimeOperationError):
    """Deployment failure preserving diagnostics with credential values redacted."""

    def __init__(self, message):
        super().__init__(redact_secrets(message))


def diagnostic_text(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return redact_secrets(value)


def process_failure(label, result):
    details = [f"exit={result.returncode}"]
    for flag in ("timed_out", "stopped", "stdin_cleanup_failed"):
        if getattr(result, flag, False):
            details.append(flag)
    stderr = diagnostic_text(result.stderr).strip()
    message = label + " (" + ", ".join(details) + ")" + (": " + stderr if stderr else "")
    stdout = getattr(result, "stdout", None)
    if stdout:
        message += "\nstdout:\n" + diagnostic_text(stdout)
    return message


def proof_fields(stdout, keys, *, stderr=""):
    fields = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if separator and key in keys:
            if key in fields and fields[key] != value:
                proof_error(f"deployment proof has conflicting {key}", stdout, stderr)
            fields[key] = value
    return fields


def proof_error(reason, stdout, stderr=""):
    raise DeploymentError(f"{reason}\nstdout:\n{diagnostic_text(stdout)}\nstderr:\n{diagnostic_text(stderr)}")


def exception_detail(exc):
    if isinstance(exc, DeploymentError):
        return str(exc)
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"process timed out after {exc.timeout}s: " + diagnostic_text(exc.stderr)
    if isinstance(exc, subprocess.CalledProcessError):
        return process_failure("process failed", exc)
    return f"{type(exc).__name__}: {exc}"


def normal_file(path: Path, roots: tuple[Path, ...]) -> Path:
    # Keep the argument for callers using the old configuration shape. Trusted
    # transport files may be linked or installed alongside the source checkout.
    try:
        if not path.is_absolute() or not path.is_file():
            raise DeploymentError(f"deployment transport requires an existing absolute file: {path}")
        return path.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise DeploymentError("deployment transport file unavailable: " + exception_detail(exc)) from None


def validate_host(host: str) -> None:
    if not isinstance(host, str) or len(host) > 253:
        raise DeploymentError("deployment host rejected")
    if not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", p)
               for p in host.split(".")):
        raise DeploymentError("deployment host rejected")
    if re.fullmatch(r"[0-9.]+", host):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise DeploymentError("deployment host rejected") from None


@dataclass(frozen=True, repr=False)
class DeployConfig:
    ssh_path: Path
    ssh_host: str
    ssh_user: str
    ssh_key_path: Path
    known_hosts_path: Path
    excluded_roots: tuple[Path, ...] = field(repr=False)
    timeout: float = 1800
    max_output_bytes: int = 65536

    def validate(self) -> None:
        validate_host(self.ssh_host)
        if self.ssh_user != "ubuntu":
            raise DeploymentError("deployment configuration rejected")
        roots = (*self.excluded_roots, Path(__file__).resolve().parent.parent)
        for path in (self.ssh_path, self.ssh_key_path, self.known_hosts_path):
            normal_file(path, roots)
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 7200:
            raise DeploymentError("deployment timeout rejected")
        if type(self.max_output_bytes) is not int or not 8192 <= self.max_output_bytes <= 1048576:
            raise DeploymentError("deployment output limit rejected")

    @classmethod
    def from_environment(cls, *, repo_root: Path, worktree_root: Path) -> "DeployConfig":
        # Roots are supplied by trusted runner configuration, never task text.
        try:
            prefix = "AI_TASK_RUNNER_DEPLOY_"
            config = cls(
                ssh_path=Path(os.environ[prefix + "SSH_PATH"]),
                ssh_host=os.environ[prefix + "SSH_HOST"],
                ssh_user=os.environ[prefix + "SSH_USER"],
                ssh_key_path=Path(os.environ[prefix + "SSH_KEY_PATH"]),
                known_hosts_path=Path(os.environ[prefix + "KNOWN_HOSTS_PATH"]),
                excluded_roots=(repo_root, worktree_root, Path(__file__).resolve().parent.parent),
                timeout=float(os.environ.get(prefix + "TIMEOUT_SECONDS", "1800")),
                max_output_bytes=int(os.environ.get(prefix + "MAX_OUTPUT_BYTES", "65536")),
            )
            config.validate()
            return config
        except (KeyError, ValueError, TypeError, OSError) as exc:
            raise DeploymentError("deployment configuration invalid: " + exception_detail(exc)) from None
