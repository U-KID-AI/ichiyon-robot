"""Fixed production adapter used by the runner after reviewed merge."""

import re
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from ai_task_deploy_config import (
    DeployConfig, DeploymentError, exception_detail, normal_file,
    process_failure, proof_error, proof_fields,
)
from ai_task_process import communicate_bounded, managed_process_options


SUMMARY = "Immutable app deployment verified."
SCRIPT = Path(__file__).absolute().with_name("ai_task_deploy_remote.sh")


@dataclass(frozen=True)
class DeploymentResult:
    deployed_commit_sha: str
    summary: str


def parse_proof(stdout: str, sha: str, *, stderr="") -> DeploymentResult:
    fields = proof_fields(stdout, {"DEPLOY_RESULT", "DEPLOYED_COMMIT_SHA"}, stderr=stderr)
    if fields.get("DEPLOY_RESULT") != "SUCCESS":
        proof_error("deployment success result missing or failed", stdout, stderr)
    if fields.get("DEPLOYED_COMMIT_SHA") != sha:
        proof_error("deployment SHA missing or mismatched", stdout, stderr)
    return DeploymentResult(sha, SUMMARY)


class ProductionDeployAdapter:
    def __init__(self, config: DeployConfig):
        config.validate()
        self.config = config

    def deploy(self, merge_sha: str, *, stop_event=None) -> DeploymentResult:
        if not isinstance(merge_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", merge_sha):
            raise DeploymentError("deployment SHA rejected")
        try:
            self.config.validate()
            if stop_event is not None and stop_event.is_set():
                raise DeploymentError("deployment stopped")
            # The installed protocol is sent on stdin, independent of task cwd.
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
            # Once the fixed production transaction has started, transient Control
            # Plane lease loss must not kill SSH mid-backup/migration/cutover.
            # Lease loss is still checked before launch and immediately after the
            # remote protocol returns; completion still requires a valid lease.
            # Spool both streams so verbose logs cannot truncate a final success
            # marker or the actual error. Process deadlines remain enforced.
            with tempfile.TemporaryFile() as diagnostics, tempfile.TemporaryFile() as output:
                try:
                    process = subprocess.Popen(argv, **managed_process_options(), shell=False, stdin=subprocess.PIPE,
                                               stdout=output, stderr=diagnostics)
                    result = communicate_bounded(process, input_text=script, timeout=c.timeout,
                                                 max_output_bytes=c.max_output_bytes, stop_event=None)
                except Exception as exc:
                    diagnostics.seek(0)
                    output.seek(0)
                    raise DeploymentError(exception_detail(exc) + "\n" +
                                          diagnostics.read().decode("utf-8", errors="replace") + "\nstdout:\n" +
                                          output.read().decode("utf-8", errors="replace")) from None
                diagnostics.seek(0)
                output.seek(0)
                result = replace(result, stderr=diagnostics.read().decode("utf-8", errors="replace") + result.stderr,
                                 stdout=output.read().decode("utf-8", errors="replace") + result.stdout)
            if (result.timed_out or result.stopped or result.stdin_cleanup_failed
                    or result.returncode != 0
                    or (stop_event is not None and stop_event.is_set())):
                detail = process_failure("deployment transport failed", result)
                if stop_event is not None and stop_event.is_set():
                    detail += "; deployment lease lost"
                raise DeploymentError(detail)
            return parse_proof(result.stdout, merge_sha, stderr=result.stderr)
        except Exception as exc:
            # Suppress the raw exception chain, but retain its operational cause.
            raise DeploymentError(exception_detail(exc)) from None
