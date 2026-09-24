"""Client for the AI task control plane's owned runner operations."""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

if __package__:
    from .ai_task_diagnostics import redact_secrets
else:
    from ai_task_diagnostics import redact_secrets


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
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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
        with urlopen(request, timeout=self.timeout) as response:
            data = response.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes:
                raise RunnerAPIError(f"HTTP {response.status}: response is too large")
            return data

    def _error(self, method: str, path: str, detail: str,
               payload: Mapping[str, Any] | None, *, status_code: int | None = None) -> RunnerAPIError:
        claim_token = str(payload.get("claim_token", "")) if payload else ""
        return RunnerAPIError(redact_secrets(
            f"Control Plane {method} {path}: {detail}",
            secrets=(self.token, claim_token),
        ), status_code=status_code)

    def _json(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            raw = self._requester(method, path, payload)
        except HTTPError as exc:
            try:
                data = exc.read(self.max_response_bytes + 1)
                # A truncated body could end halfway through an unlabelled secret.
                body = (f"<body exceeds {self.max_response_bytes} bytes>"
                        if len(data) > self.max_response_bytes else data.decode("utf-8", errors="replace"))
            except Exception as read_error:
                body = f"<body unreadable: {type(read_error).__name__}: {read_error}>"
            finally:
                exc.close()
            detail = f"HTTP {exc.code} {exc.reason}; body: {body}"
            raise self._error(method, path, detail, payload, status_code=exc.code) from None
        except Exception as exc:
            raise self._error(method, path, f"{type(exc).__name__}: {exc}", payload,
                              status_code=getattr(exc, "status_code", None)) from None
        if not isinstance(raw, bytes):
            raise self._error(method, path, f"invalid response type: {type(raw).__name__}", payload)
        if len(raw) > self.max_response_bytes:
            raise self._error(method, path, "response is too large", payload)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise self._error(method, path,
                              f"{type(exc).__name__}: {exc}; body: {raw.decode('utf-8', errors='replace')}",
                              payload) from None
        if not isinstance(value, dict):
            raise self._error(method, path, f"invalid response object; body: {raw.decode('utf-8')}", payload)
        return value

    def minecraft_release(self, sha: str, *, attempt: str | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("invalid release SHA")
        payload = {} if attempt is None else {"attempt": str(uuid.UUID(attempt))}
        return self._json("POST", "/internal/minecraft-release/" + sha, payload)

    def minecraft_release_status(self, sha: str, operation_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("invalid release SHA")
        operation_id = str(uuid.UUID(operation_id))
        return self._json("GET", f"/internal/minecraft-release/{sha}/operations/{operation_id}")

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

    def retry(self, task_id: uuid.UUID, claim_token: uuid.UUID, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not 1 <= len(reason) <= 4000:
            raise ValueError("invalid retry reason")
        reason = redact_secrets(reason, secrets=(self.token, str(claim_token)))[:4000]
        return self._owned("retry", task_id, claim_token, {"reason": reason})

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

    def mark_deploying(
        self,
        task_id: uuid.UUID,
        claim_token: uuid.UUID,
        *,
        commit_sha: str,
        pr_number: int,
        pr_url: str,
        test_summary: str,
        changed_files_summary: str,
        ci_workflow_run_id: int | None = None,
        review_summary: str,
        merge_commit_sha: str,
    ) -> dict[str, Any]:
        match = (
            PR_URL_PATTERN.fullmatch(pr_url)
            if isinstance(pr_url, str)
            else None
        )

        if (
            not isinstance(commit_sha, str)
            or SHA_PATTERN.fullmatch(
                commit_sha
            )
            is None
            or not isinstance(
                merge_commit_sha,
                str,
            )
            or SHA_PATTERN.fullmatch(
                merge_commit_sha
            )
            is None
            or not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or pr_number <= 0
            or pr_number > 2_147_483_647
            or match is None
            or int(match.group(1)) != pr_number
            or (ci_workflow_run_id is not None and (
                not isinstance(ci_workflow_run_id, int)
                or isinstance(ci_workflow_run_id, bool)
                or not 1 <= ci_workflow_run_id <= 9_223_372_036_854_775_807
            ))
            or not isinstance(
                test_summary,
                str,
            )
            or len(test_summary) > 8000
            or not isinstance(
                changed_files_summary,
                str,
            )
            or len(
                changed_files_summary
            )
            > 8000
            or not isinstance(
                review_summary,
                str,
            )
            or not 1
            <= len(review_summary)
            <= 2000
        ):
            raise ValueError(
                "invalid reviewed merge metadata"
            )

        fields = {
            "commit_sha": commit_sha,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "test_summary": test_summary,
            "changed_files_summary":
                changed_files_summary,
            "ci_workflow_run_id":
                ci_workflow_run_id,
            "review_summary":
                review_summary,
            "merge_commit_sha":
                merge_commit_sha,
        }

        # The durable deploying transition is idempotent. One retry covers
        # an ambiguous lost response after the database
        # may already have committed the deploying state.
        try:
            return self._owned(
                "deploying",
                task_id,
                claim_token,
                fields,
            )
        except RunnerAPIError:
            return self._owned(
                "deploying",
                task_id,
                claim_token,
                fields,
            )

    def mark_completed(
        self, task_id: uuid.UUID, claim_token: uuid.UUID, *,
        deployed_commit_sha: str, deployment_summary: str,
    ) -> dict[str, Any]:
        if (not isinstance(deployed_commit_sha, str)
                or SHA_PATTERN.fullmatch(deployed_commit_sha) is None
                or not isinstance(deployment_summary, str)
                or not 1 <= len(deployment_summary) <= 4000):
            raise ValueError("invalid deployment metadata")
        fields = {"deployed_commit_sha": deployed_commit_sha,
                  "deployment_summary": deployment_summary}
        # Retry the identical proof once if the committed response was lost.
        try:
            return self._owned("completed", task_id, claim_token, fields)
        except RunnerAPIError:
            return self._owned("completed", task_id, claim_token, fields)

    def _owned(self, operation: str, task_id: uuid.UUID, claim_token: uuid.UUID,
               fields: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if operation not in {"heartbeat", "progress", "testing", "retry", "fail", "needs-human", "ready-for-review", "deploying", "completed"}:
            raise ValueError("operation is not allowlisted")
        if not isinstance(task_id, uuid.UUID) or not isinstance(claim_token, uuid.UUID):
            raise ValueError("task identifiers must be UUIDs")
        payload = {"runner_id": self.runner_id, "claim_token": str(claim_token)}
        if fields:
            payload.update(fields)
        return self._json("POST", f"/internal/ai-tasks/{task_id}/{operation}", payload)
