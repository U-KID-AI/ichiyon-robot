"""Fixed production adapter; intentionally not connected to runner startup."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ai_task_deploy_config import DeployConfig, DeploymentSafetyError, normal_file
from ai_task_process import communicate_bounded, managed_process_options


SUMMARY = "Immutable app deployment verified."
SCRIPT = Path(__file__).absolute().with_name("ai_task_deploy_remote.sh")


@dataclass(frozen=True)
class DeploymentResult:
    deployed_commit_sha: str
    summary: str


def parse_proof(stdout: str, sha: str) -> DeploymentResult:
    # The protocol emits only these three lines. Reject all unsolicited output.
    expected = f"DEPLOY_RESULT=SUCCESS\nDEPLOYED_COMMIT_SHA={sha}\nDEPLOY_SUMMARY={SUMMARY}\n"
    if stdout != expected:
        raise DeploymentSafetyError("deployment proof rejected")
    return DeploymentResult(sha, SUMMARY)


class ProductionDeployAdapter:
    def __init__(self, config: DeployConfig):
        config.validate()
        self.config = config

    def deploy(self, merge_sha: str, *, stop_event=None) -> DeploymentResult:
        if not isinstance(merge_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", merge_sha):
            raise DeploymentSafetyError("deployment SHA rejected")
        try:
            self.config.validate()
            if stop_event is not None and stop_event.is_set():
                raise DeploymentSafetyError("deployment stopped")
            # No cwd or task-supplied script path. Reject linked installation files.
            script = normal_file(SCRIPT, ()).read_text(encoding="utf-8")
            c = self.config
            argv = [str(c.ssh_path), "-F", "none", "-T",
                    "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", 'UserKnownHostsFile="' + c.known_hosts_path.as_posix() + '"',
                    "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
                    "-o", "ServerAliveCountMax=3", "-o", "ForwardAgent=no",
                    "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no",
                    "-i", str(c.ssh_key_path), c.ssh_user + "@" + c.ssh_host,
                    "bash", "-s", "--", merge_sha]
            process = subprocess.Popen(argv, **managed_process_options(), shell=False, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Once the fixed production transaction has started, transient Control
            # Plane lease loss must not kill SSH mid-backup/migration/cutover.
            # Lease loss is still checked before launch and immediately after the
            # remote protocol returns, so Control Plane completion stays fail-closed.
            result = communicate_bounded(process, input_text=script, timeout=c.timeout,
                                         max_output_bytes=c.max_output_bytes, stop_event=None)
            if (result.timed_out or result.stopped or result.stdin_cleanup_failed
                    or result.returncode != 0 or result.stderr
                    or (stop_event is not None and stop_event.is_set())
                    or len(result.stdout.encode("utf-8")) >= c.max_output_bytes):
                raise DeploymentSafetyError("deployment transport failed")
            return parse_proof(result.stdout, merge_sha)
        except Exception:
            # Suppress chained exceptions too: Popen errors can include key paths.
            raise DeploymentSafetyError("deployment failed closed") from None
