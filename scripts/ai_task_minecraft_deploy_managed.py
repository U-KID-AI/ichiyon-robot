"""Runner orchestration only: the deployed app owns pack generation and apply."""

import math
import re
import time
from uuid import UUID

from ai_task_api_client import RunnerAPIError
from ai_task_deploy_config import DeploymentError
from ai_task_minecraft_deploy import DeploymentResult, validate_sha

PENDING = frozenset({"queued", "stopping", "installing", "starting"})
FAILED = frozenset({"failed", "recovery_failed", "rolled_back", "cancelled"})


def operation(payload, sha, operation_id=None, digest=None):
    if not isinstance(payload, dict) or payload.get("merge_sha") != sha:
        raise DeploymentError("Minecraft app SHA missing or mismatched")
    job = payload.get("operation")
    if not isinstance(job, dict):
        raise DeploymentError("Minecraft operation missing")
    try:
        if str(UUID(job["operation_id"])) != job["operation_id"]:
            raise ValueError
    except (KeyError, ValueError, TypeError, AttributeError):
        raise DeploymentError("Minecraft operation ID invalid") from None
    if operation_id is not None and job["operation_id"] != operation_id:
        raise DeploymentError("Minecraft operation superseded")
    actual = job.get("catalog_digest")
    if (not isinstance(actual, str) or re.fullmatch(r"[0-9a-f]{64}", actual) is None
            or (digest is not None and actual != digest)):
        raise DeploymentError("Minecraft catalog digest missing or mismatched")
    state = job.get("status")
    if not isinstance(state, str) or state not in PENDING | FAILED | {"succeeded"}:
        raise DeploymentError("Minecraft operation status unknown")
    if state in FAILED:
        raise DeploymentError(f"Minecraft operation {job['operation_id']} ended {state}; no completion proof")
    return job


def verified_result(payload, sha, operation_id, digest):
    job = operation(payload, sha, operation_id, digest)
    runtime = payload.get("runtime")
    if job["status"] != "succeeded":
        raise DeploymentError("Minecraft operation is not terminal succeeded")
    if job.get("installed") is not True or job.get("active_digest") != digest:
        raise DeploymentError("Minecraft installed/active catalog digest mismatch")
    if payload.get("current_catalog_digest") != digest:
        raise DeploymentError("Minecraft DB assets changed or missing since generation")
    if not isinstance(runtime, dict):
        raise DeploymentError("Minecraft runtime proof missing")
    container = runtime.get("container")
    bridge = runtime.get("bridge")
    # Control API bridge.responding is mc-monitor status-bedrock (UDP), not HTTP.
    if (not isinstance(container, dict) or container.get("state") != "running"
            or container.get("health") != "healthy" or not isinstance(bridge, dict)
            or bridge.get("responding") is not True):
        raise DeploymentError("Minecraft Docker/Bedrock health not verified")
    return DeploymentResult(
        sha, f"Minecraft managed release SHA={sha}; operation={operation_id}; status=succeeded; "
        f"catalog={digest}; DB assets=preserved; installed=true; active={digest}; "
        "Docker=running/healthy; Bedrock=responding.",
        frozenset({"minecraft"}),
    )


class ManagedMinecraftDeployAdapter:
    def __init__(self, client, *, timeout=1800, poll_interval=5, attempt=None,
                 clock=time.monotonic, sleep=time.sleep):
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0
                   for v in (timeout, poll_interval)):
            raise DeploymentError("Minecraft poll timing invalid")
        self.client = client
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.attempt = None if attempt is None else str(UUID(attempt))
        self.clock = clock
        self.sleep = sleep

    def deploy(self, merge_sha, *, stop_event=None, refresh_source=True):
        validate_sha(merge_sha)
        if type(refresh_source) is not bool:
            raise DeploymentError("Minecraft source mode invalid")
        deadline = self.clock() + self.timeout

        def check_wait():
            if stop_event is not None and stop_event.is_set():
                raise DeploymentError("Minecraft deployment lease lost; operation may still be running")
            if self.clock() >= deadline:
                raise DeploymentError("Minecraft operation timed out; no completion proof")

        try:
            check_wait()
            accepted = self.client.minecraft_release(merge_sha, attempt=self.attempt)
            if not isinstance(accepted, dict):
                raise DeploymentError("Minecraft release response invalid")
            digest = accepted.get("expected_catalog_digest")
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise DeploymentError("Minecraft expected catalog digest missing")
            job = operation(accepted, merge_sha, digest=digest)
            operation_id = job["operation_id"]
            # Even an idempotent POST/202 must be followed by fresh terminal proof.
            while True:
                check_wait()
                payload = self.client.minecraft_release_status(merge_sha, operation_id)
                check_wait()
                job = operation(payload, merge_sha, operation_id, digest)
                if job["status"] == "succeeded":
                    return verified_result(payload, merge_sha, operation_id, digest)
                delay = min(self.poll_interval, max(0, deadline - self.clock()))
                if stop_event is None:
                    self.sleep(delay)
                else:
                    stop_event.wait(delay)
        except RunnerAPIError as exc:
            # The existing client redacts credentials. Never retry via raw packs.
            raise DeploymentError(str(exc)) from None
