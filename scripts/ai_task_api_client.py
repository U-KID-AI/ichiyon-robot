"""Fixed-operation client for the Phase 2A AI task Control Plane."""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TASK_ID_PATTERN = re.compile(r"^[0-9a-fA-F-]{36}$")
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
PR_URL_PATTERN = re.compile(
    r"^https://github\.com/U-KID-AI/ichiyon-robot/pull/([1-9][0-9]*)$"
)


@dataclass(frozen=True)
class ClaimedTask:
    task_id: uuid.UUID
    description: str
    branch_name: str
    worktree_name: str
    claim_token: uuid.UUID
    lease_expires_at: str


class RunnerAPIError(RuntimeError):
    pass


class RunnerAPIClient:
    def __init__(self, base_url: str, token: str, runner_id: str, *, timeout: float = 15.0,
                 max_response_bytes: int = 256 * 1024,
                 requester: Callable[..., bytes] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.runner_id = runner_id
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._requester = requester or self._request

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None) -> bytes:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = response.read(self.max_response_bytes + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RunnerAPIError("Control Plane request failed") from exc
        if len(data) > self.max_response_bytes:
            raise RunnerAPIError("Control Plane response is too large")
        return data

    def _json(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            raw = self._requester(method, path, payload)
            if not isinstance(raw, bytes) or len(raw) > self.max_response_bytes:
                raise RunnerAPIError("Control Plane response is too large or invalid")
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RunnerAPIError("Control Plane returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise RunnerAPIError("Control Plane returned an invalid response")
        return value

    def claim(self) -> ClaimedTask | None:
        response = self._json("POST", "/internal/ai-tasks/claim", {"runner_id": self.runner_id})
        raw = response.get("task")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise RunnerAPIError("invalid claim response")
        required = ("task_id", "description", "branch_name", "worktree_name", "claim_token", "lease_expires_at")
        if any(key not in raw for key in required):
            raise RunnerAPIError("incomplete claim response")
        try:
            task_id = uuid.UUID(str(raw["task_id"]))
            claim_token = uuid.UUID(str(raw["claim_token"]))
        except (ValueError, TypeError) as exc:
            raise RunnerAPIError("invalid claim UUID") from exc
        description = raw["description"]
        if not isinstance(description, str) or not 1 <= len(description) <= 4000:
            raise RunnerAPIError("invalid claim description")
        if not all(isinstance(raw[key], str) and raw[key] for key in ("branch_name", "worktree_name", "lease_expires_at")):
            raise RunnerAPIError("invalid claim metadata")
        lease_value = raw["lease_expires_at"].replace("Z", "+00:00")
        try:
            lease = datetime.fromisoformat(lease_value)
        except ValueError as exc:
            raise RunnerAPIError("invalid lease timestamp") from exc
        if lease.tzinfo is None or lease <= datetime.now(timezone.utc):
            raise RunnerAPIError("claim lease is expired")
        return ClaimedTask(task_id, description, raw["branch_name"], raw["worktree_name"], claim_token, raw["lease_expires_at"])

    def heartbeat(self, task_id: uuid.UUID, claim_token: uuid.UUID) -> dict[str, Any]:
        return self._owned("heartbeat", task_id, claim_token)

    def progress(self, task_id: uuid.UUID, claim_token: uuid.UUID, **fields: str) -> dict[str, Any]:
        allowed = {"current_step", "progress_summary", "base_commit_sha", "test_summary", "changed_files_summary"}
        if not fields or any(key not in allowed for key in fields):
            raise ValueError("invalid progress fields")
        return self._owned("progress", task_id, claim_token, fields)

    def mark_testing(self, task_id: uuid.UUID, claim_token: uuid.UUID) -> dict[str, Any]:
        return self._owned("testing", task_id, claim_token)

    def mark_failed(self, task_id: uuid.UUID, claim_token: uuid.UUID, error_message: str) -> dict[str, Any]:
        return self._owned("fail", task_id, claim_token, {"error_message": error_message})

    def mark_needs_human(self, task_id: uuid.UUID, claim_token: uuid.UUID, reason: str) -> dict[str, Any]:
        return self._owned("needs-human", task_id, claim_token, {"reason": reason})

    def ready_for_review(
        self,
        task_id: uuid.UUID,
        claim_token: uuid.UUID,
        *,
        commit_sha: str,
        pr_number: int,
        pr_url: str,
        test_summary: str,
        changed_files_summary: str,
    ) -> dict[str, Any]:
        match = (
            PR_URL_PATTERN.fullmatch(pr_url)
            if isinstance(pr_url, str)
            else None
        )

        if (
            not isinstance(commit_sha, str)
            or SHA_PATTERN.fullmatch(commit_sha) is None
            or not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or pr_number <= 0
            or pr_number > 2_147_483_647
            or match is None
            or int(match.group(1)) != pr_number
            or not isinstance(test_summary, str)
            or len(test_summary) > 8000
            or not isinstance(changed_files_summary, str)
            or len(changed_files_summary) > 8000
        ):
            raise ValueError("invalid ready-for-review metadata")

        return self._owned(
            "ready-for-review",
            task_id,
            claim_token,
            {
                "commit_sha": commit_sha,
                "pr_number": pr_number,
                "pr_url": pr_url,
                "test_summary": test_summary,
                "changed_files_summary": changed_files_summary,
            },
        )

    def _owned(self, operation: str, task_id: uuid.UUID, claim_token: uuid.UUID,
               fields: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if operation not in {"heartbeat", "progress", "testing", "fail", "needs-human", "ready-for-review"}:
            raise ValueError("operation is not allowlisted")
        if not isinstance(task_id, uuid.UUID) or not isinstance(claim_token, uuid.UUID):
            raise ValueError("task identifiers must be UUIDs")
        payload = {"runner_id": self.runner_id, "claim_token": str(claim_token)}
        if fields:
            payload.update(fields)
        return self._json("POST", f"/internal/ai-tasks/{task_id}/{operation}", payload)
