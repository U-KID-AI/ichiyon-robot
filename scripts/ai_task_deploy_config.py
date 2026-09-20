"""Trusted transport configuration. Never reads dotenv or credential contents."""

import ipaddress
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ai_task_safety import SafetyError, is_reparse_point


class DeploymentSafetyError(SafetyError):
    """Safe, fixed deployment failure; never includes transport output."""


def normal_file(path: Path, roots: tuple[Path, ...]) -> Path:
    try:
        if any(ord(c) < 32 or ord(c) == 127 or c in '\"\'%$' for c in str(path)):
            raise ValueError
        if ':' in path.as_posix()[len(path.drive):]:
            raise ValueError
        if not path.is_absolute() or not path.is_file():
            raise ValueError
        for part in (path, *path.parents):
            if part.is_symlink() or is_reparse_point(part):
                raise ValueError
        resolved = path.resolve(strict=True)
        if any(resolved == root.resolve() or root.resolve() in resolved.parents for root in roots):
            raise ValueError
        return resolved
    except (OSError, ValueError, SafetyError):
        raise DeploymentSafetyError("deployment transport path rejected") from None


def validate_host(host: str) -> None:
    if not isinstance(host, str) or len(host) > 253:
        raise DeploymentSafetyError("deployment host rejected")
    if not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", p)
               for p in host.split(".")):
        raise DeploymentSafetyError("deployment host rejected")
    if re.fullmatch(r"[0-9.]+", host):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise DeploymentSafetyError("deployment host rejected") from None


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
        if self.ssh_user != "ubuntu" or not self.excluded_roots:
            raise DeploymentSafetyError("deployment configuration rejected")
        roots = (*self.excluded_roots, Path(__file__).resolve().parent.parent)
        for path in (self.ssh_path, self.ssh_key_path, self.known_hosts_path):
            normal_file(path, roots)
        if self.ssh_path.name != ("ssh.exe" if os.name == "nt" else "ssh"):
            raise DeploymentSafetyError("deployment executable rejected")
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 7200:
            raise DeploymentSafetyError("deployment timeout rejected")
        if type(self.max_output_bytes) is not int or not 8192 <= self.max_output_bytes <= 1048576:
            raise DeploymentSafetyError("deployment output limit rejected")

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
        except (KeyError, ValueError, TypeError, OSError):
            raise DeploymentSafetyError("deployment configuration rejected") from None
