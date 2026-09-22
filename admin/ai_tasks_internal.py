import hmac
import logging
import os
from typing import Optional
from urllib.parse import unquote, urlsplit
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field, StrictInt, StrictStr

from bot import config
from bot.db import get_connection
from bot.repositories.ai_tasks import (
    AITaskRepository,
    is_valid_pr_url,
    is_valid_runner_id,
    is_valid_sha1,
    validate_pr_number,
    validate_workflow_run_id,
)
from scripts.ai_task_diagnostics import redact_secrets


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/ai-tasks", tags=["internal-ai-tasks"])


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class RunnerRequest(StrictModel):
    runner_id: str = Field(..., min_length=1, max_length=64)


class HeartbeatRequest(RunnerRequest):
    claim_token: UUID


class ProgressRequest(HeartbeatRequest):
    current_step: Optional[str] = Field(default=None, max_length=500)
    progress_summary: Optional[str] = Field(default=None, max_length=4000)
    base_commit_sha: Optional[str] = Field(default=None, max_length=40)
    test_summary: Optional[str] = Field(default=None, max_length=8000)
    changed_files_summary: Optional[str] = Field(default=None, max_length=8000)


class FailureRequest(HeartbeatRequest):
    error_message: str = Field(..., min_length=1, max_length=4000)


class NeedsHumanRequest(HeartbeatRequest):
    reason: str = Field(..., min_length=1, max_length=4000)


class RetryRequest(HeartbeatRequest):
    reason: StrictStr = Field(..., min_length=1, max_length=4000)


class ReadyForReviewRequest(HeartbeatRequest):
    commit_sha: StrictStr = Field(..., min_length=40, max_length=40)
    pr_number: StrictInt
    pr_url: StrictStr = Field(..., max_length=200)
    test_summary: StrictStr = Field(..., max_length=8000)
    changed_files_summary: StrictStr = Field(..., max_length=8000)


class DeployingRequest(ReadyForReviewRequest):
    ci_workflow_run_id: Optional[StrictInt] = None
    review_summary: StrictStr = Field(
        ...,
        min_length=1,
        max_length=2000,
    )
    merge_commit_sha: StrictStr = Field(
        ...,
        min_length=40,
        max_length=40,
    )


class CompletedRequest(HeartbeatRequest):
    deployed_commit_sha: StrictStr = Field(..., min_length=40, max_length=40)
    deployment_summary: StrictStr = Field(..., min_length=1, max_length=4000)


def require_runner_token(authorization: Optional[str]) -> None:
    expected = config.AI_TASK_RUNNER_API_TOKEN
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AI task runner is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    supplied = authorization[len("Bearer "):]
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def _validate_runner_id(runner_id: str) -> None:
    if not is_valid_runner_id(runner_id):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid runner_id")


def _redact_runner_diagnostic(text: str, request: HeartbeatRequest) -> str:
    database_url = os.environ.get("DATABASE_URL", "")
    secrets = [config.AI_TASK_RUNNER_API_TOKEN, config.TOKEN, str(request.claim_token), database_url]
    try:
        password = urlsplit(database_url).password
    except ValueError:
        password = None
    if password:
        secrets.extend((password, unquote(password)))
    return redact_secrets(text, secrets=secrets)


def _connection_error(exc: Exception, *, owned_request: Optional[HeartbeatRequest] = None) -> HTTPException:
    detail = "AI task service is temporarily unavailable"
    if owned_request is not None:
        detail = _redact_runner_diagnostic(
            f"AI task database operation failed: {type(exc).__name__}: {exc}",
            owned_request,
        )
        logger.error("%s", detail)
    else:
        logger.error("AI task control-plane database operation failed: %s", type(exc).__name__)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def _rollback_safely(connection) -> None:
    if connection is not None:
        try:
            connection.rollback()
        except Exception:
            pass


def _require_row(row) -> dict:
    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="AI task state is no longer available")
    return row


@router.post("/claim")
def claim_task(request: RunnerRequest, authorization: Optional[str] = Header(default=None)):
    require_runner_token(authorization)
    _validate_runner_id(request.runner_id)
    try:
        with get_connection() as connection:
            try:
                repository = AITaskRepository(connection)
                repository.expire_stale_leases()
                row = repository.claim_next_task(runner_id=request.runner_id)
                connection.commit()
                if row is None:
                    return {"task": None}
                return {"task": {
                    "task_id": row["task_id"],
                    "description": row["description"],
                    "branch_name": row["branch_name"],
                    "worktree_name": row["worktree_name"],
                    "claim_token": row["claim_token"],
                    "lease_expires_at": row["lease_expires_at"],
                }}
            except HTTPException:
                _rollback_safely(connection)
                raise
            except Exception as exc:
                _rollback_safely(connection)
                raise _connection_error(exc) from None
    except HTTPException:
        raise
    except Exception as exc:
        raise _connection_error(exc) from None


def _run_owned_operation(task_id: UUID, request: HeartbeatRequest, operation):
    _validate_runner_id(request.runner_id)
    try:
        with get_connection() as connection:
            try:
                row = operation(AITaskRepository(connection), task_id, request)
                if row is None:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="AI task state is no longer available")
                connection.commit()
                response = {"task_id": row["task_id"], "status": row.get("status")}
                if "lease_expires_at" in row:
                    response["lease_expires_at"] = row["lease_expires_at"]
                return response
            except HTTPException:
                _rollback_safely(connection)
                raise
            except Exception as exc:
                _rollback_safely(connection)
                raise _connection_error(exc, owned_request=request) from None
    except HTTPException:
        raise
    except Exception as exc:
        raise _connection_error(exc, owned_request=request) from None


@router.post("/{task_id}/heartbeat")
def heartbeat(task_id: UUID, request: HeartbeatRequest, authorization: Optional[str] = Header(default=None)):
    require_runner_token(authorization)
    return _run_owned_operation(task_id, request, lambda repo, tid, req: repo.heartbeat(task_id=tid, runner_id=req.runner_id, claim_token=req.claim_token))


@router.post("/{task_id}/progress")
def progress(task_id: UUID, request: ProgressRequest, authorization: Optional[str] = Header(default=None)):
    require_runner_token(authorization)
    if all(value is None for value in (request.current_step, request.progress_summary, request.base_commit_sha, request.test_summary, request.changed_files_summary)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one progress field is required")
    if request.base_commit_sha is not None and not is_valid_sha1(request.base_commit_sha):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid base commit SHA")
    return _run_owned_operation(task_id, request, lambda repo, tid, req: repo.update_runner_progress(
        task_id=tid, runner_id=req.runner_id, claim_token=req.claim_token,
        current_step=req.current_step, progress_summary=req.progress_summary,
        base_commit_sha=req.base_commit_sha, test_summary=req.test_summary,
        changed_files_summary=req.changed_files_summary,
    ))


@router.post("/{task_id}/testing")
def testing(task_id: UUID, request: HeartbeatRequest, authorization: Optional[str] = Header(default=None)):
    require_runner_token(authorization)
    return _run_owned_operation(task_id, request, lambda repo, tid, req: repo.mark_testing(task_id=tid, runner_id=req.runner_id, claim_token=req.claim_token))


@router.post("/{task_id}/fail")
def fail(task_id: UUID, request: FailureRequest, authorization: Optional[str] = Header(default=None)):
    require_runner_token(authorization)
    return _run_owned_operation(task_id, request, lambda repo, tid, req: repo.mark_failed(task_id=tid, runner_id=req.runner_id, claim_token=req.claim_token, error_message=req.error_message))


@router.post("/{task_id}/retry")
def retry(task_id: UUID, request: RetryRequest, authorization: Optional[str] = Header(default=None)):
    require_runner_token(authorization)
    reason = _redact_runner_diagnostic(request.reason, request)[:4000]
    return _run_owned_operation(task_id, request, lambda repo, tid, req: repo.retry(
        task_id=tid, runner_id=req.runner_id, claim_token=req.claim_token, reason=reason,
    ))


@router.post("/{task_id}/needs-human")
def needs_human(task_id: UUID, request: NeedsHumanRequest, authorization: Optional[str] = Header(default=None)):
    require_runner_token(authorization)
    return _run_owned_operation(task_id, request, lambda repo, tid, req: repo.mark_needs_human(task_id=tid, runner_id=req.runner_id, claim_token=req.claim_token, reason=req.reason))


@router.post("/{task_id}/ready-for-review")
def ready_for_review(task_id: UUID, request: ReadyForReviewRequest, authorization: Optional[str] = Header(default=None)):
    require_runner_token(authorization)
    _validate_runner_id(request.runner_id)
    try:
        validate_pr_number(request.pr_number)
        if not is_valid_sha1(request.commit_sha) or not is_valid_pr_url(request.pr_url, request.pr_number):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid review metadata")
    return _run_owned_operation(task_id, request, lambda repo, tid, req: repo.mark_ready_for_review(
        task_id=tid, runner_id=req.runner_id, claim_token=req.claim_token,
        commit_sha=req.commit_sha, pr_number=req.pr_number, pr_url=req.pr_url,
        test_summary=req.test_summary, changed_files_summary=req.changed_files_summary,
    ))


@router.post("/{task_id}/deploying")
def deploying(
    task_id: UUID,
    request: DeployingRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_runner_token(authorization)
    _validate_runner_id(request.runner_id)

    try:
        validate_pr_number(request.pr_number)
        validate_workflow_run_id(
            request.ci_workflow_run_id
        )

        if (
            not is_valid_sha1(request.commit_sha)
            or not is_valid_sha1(
                request.merge_commit_sha
            )
            or not is_valid_pr_url(
                request.pr_url,
                request.pr_number,
            )
        ):
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid reviewed merge metadata",
        )

    return _run_owned_operation(
        task_id,
        request,
        lambda repo, tid, req:
            repo.mark_deploying(
                task_id=tid,
                runner_id=req.runner_id,
                claim_token=req.claim_token,
                commit_sha=req.commit_sha,
                pr_number=req.pr_number,
                pr_url=req.pr_url,
                test_summary=req.test_summary,
                changed_files_summary=(
                    req.changed_files_summary
                ),
                ci_workflow_run_id=(
                    req.ci_workflow_run_id
                ),
                review_summary=req.review_summary,
                merge_commit_sha=(
                    req.merge_commit_sha
                ),
            ),
    )


@router.post("/{task_id}/completed")
def completed(task_id: UUID, request: CompletedRequest, authorization: Optional[str] = Header(default=None)):
    require_runner_token(authorization)
    if not is_valid_sha1(request.deployed_commit_sha):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid deployed commit SHA")
    return _run_owned_operation(task_id, request, lambda repo, tid, req: repo.mark_completed(
        task_id=tid, runner_id=req.runner_id, claim_token=req.claim_token,
        deployed_commit_sha=req.deployed_commit_sha, deployment_summary=req.deployment_summary,
    ))
