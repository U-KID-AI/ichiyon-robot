"""Static and fake based checks for the AI task control plane."""

import inspect
import re
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with patch("dotenv.load_dotenv"), patch("os.environ", {}):
    from admin import ai_tasks_internal as api
    from bot import config
    from bot.repositories import ai_tasks as repository


MIGRATION = (ROOT / "migrations/060_add_ai_task_runner_fields.sql").read_text(encoding="utf-8")
COMPOSE_SOURCE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
REPO_SOURCE = inspect.getsource(repository.AITaskRepository)
API_SOURCE = inspect.getsource(api)


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def rejects(call):
    try:
        call()
    except (ValueError, HTTPException):
        return True
    return False


class FakeConnection:
    def __init__(self):
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False
        self.rollback_before_close = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.closed = True

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        if self.closed:
            raise RuntimeError("rollback after close")
        self.rollback_count += 1
        self.rollback_before_close = True


class FakeCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))


def runtime_api_checks():
    task_id = UUID("00000000-0000-0000-0000-000000000001")
    claim_token = UUID("00000000-0000-0000-0000-000000000002")
    request = api.RunnerRequest(runner_id="runner-1")
    row = {
        "task_id": task_id,
        "description": "task",
        "branch_name": "ai/task/1",
        "worktree_name": "ai-task-1",
        "claim_token": claim_token,
        "lease_expires_at": "future",
        "status": "running",
    }

    class FakeRepository:
        def __init__(self, connection):
            self.connection = connection

        def expire_stale_leases(self):
            self.connection.expired = True

        def claim_next_task(self, *, runner_id):
            self.connection.claim_runner_id = runner_id
            return row

        def heartbeat(self, **kwargs):
            return {"task_id": task_id, "status": "running"}

    old_get_connection = api.get_connection
    old_repository = api.AITaskRepository
    old_token = config.AI_TASK_RUNNER_API_TOKEN
    connection = FakeConnection()
    config.AI_TASK_RUNNER_API_TOKEN = "test-runner-secret"
    api.get_connection = lambda: connection
    api.AITaskRepository = FakeRepository
    try:
        result = api.claim_task(request, "Bearer test-runner-secret")
        check("claim uses context manager connection", connection.closed and connection.claim_runner_id == "runner-1")
        check("claim commits once", connection.commit_count == 1)
        heartbeat_request = api.HeartbeatRequest(runner_id="runner-1", claim_token=claim_token)
        api.heartbeat(task_id, heartbeat_request, "Bearer test-runner-secret")
        check("owned operation commits", connection.commit_count == 2)

        class FailingRepository(FakeRepository):
            def expire_stale_leases(self):
                raise RuntimeError("SQL details must not escape")

        api.AITaskRepository = FailingRepository
        failing_connection = FakeConnection()
        api.get_connection = lambda: failing_connection
        try:
            api.claim_task(request, "Bearer test-runner-secret")
            raise AssertionError("DB error did not raise")
        except HTTPException as exc:
            check("DB error returns fixed 503", exc.status_code == 503 and "SQL details" not in str(exc.detail))
        check("DB error rolls back before close", failing_connection.rollback_count == 1 and failing_connection.rollback_before_close and failing_connection.commit_count == 0 and failing_connection.closed)

        api.get_connection = lambda: (_ for _ in ()).throw(RuntimeError("connection details must not escape"))
        try:
            api.claim_task(request, "Bearer test-runner-secret")
            raise AssertionError("connection failure did not raise")
        except HTTPException as exc:
            check("connection failure returns fixed 503", exc.status_code == 503 and "connection details" not in str(exc.detail))

        for authorization in (None, "Bearer wrong"):
            with patch.object(api, "get_connection") as connect:
                try:
                    api.retry(task_id, api.RetryRequest(runner_id="runner-1", claim_token=claim_token, reason="retry"), authorization)
                    raise AssertionError("unauthorized retry accepted")
                except HTTPException as exc:
                    check("retry auth remains public and fixed", exc.status_code == 401 and exc.detail == "Unauthorized" and not connect.called)
        with patch.object(config, "AI_TASK_RUNNER_API_TOKEN", ""), patch.object(api, "get_connection") as connect:
            try:
                api.retry(task_id, api.RetryRequest(runner_id="runner-1", claim_token=claim_token, reason="retry"), None)
                raise AssertionError("unconfigured runner accepted")
            except HTTPException as exc:
                check("unconfigured runner remains fixed 503", exc.status_code == 503 and exc.detail == "AI task runner is not configured" and not connect.called)

        failure = RuntimeError(f"constraint ai_tasks_merge_commit_sha_format failed; test-runner-secret; {claim_token}; password=db-test-value")
        failing_connection = FakeConnection()
        api.get_connection = lambda: failing_connection
        class OwnedFailureRepository(FakeRepository):
            def retry(self, **kwargs):
                raise failure
        api.AITaskRepository = OwnedFailureRepository
        with patch.object(api.logger, "error") as log:
            try:
                api.retry(task_id, api.RetryRequest(runner_id="runner-1", claim_token=claim_token, reason="deploy exited 1"), "Bearer test-runner-secret")
                raise AssertionError("owned failure accepted")
            except HTTPException as exc:
                check("owned DB failure includes actual error", exc.status_code == 503 and "RuntimeError" in exc.detail and "ai_tasks_merge_commit_sha_format" in exc.detail)
                check("owned DB failure redacts response and log", all(value not in exc.detail + str(log.call_args) for value in ("test-runner-secret", str(claim_token), "db-test-value")))
                check("owned error suppresses raw traceback context", exc.__suppress_context__)
        check("owned DB error rolls back before close", failing_connection.rollback_count == 1 and failing_connection.rollback_before_close and failing_connection.closed and failing_connection.commit_count == 0)

        api.get_connection = lambda: (_ for _ in ()).throw(RuntimeError("connection refused password=db-test-value"))
        try:
            api.heartbeat(task_id, heartbeat_request, "Bearer test-runner-secret")
            raise AssertionError("owned connection failure accepted")
        except HTTPException as exc:
            check("owned connection failure is actionable and redacted", exc.status_code == 503 and "connection refused" in exc.detail and "db-test-value" not in exc.detail)
        database_url = "postgresql://runner:database%2Ftest-secret@db.invalid/tasks"
        with patch.dict("os.environ", {"DATABASE_URL": database_url}), patch.object(config, "TOKEN", "discord-test-value"), patch.object(api.logger, "error") as log:
            error = api._connection_error(RuntimeError(f"connection failed {database_url} database/test-secret discord-test-value"), owned_request=heartbeat_request)
            check("owned DB errors redact DSN and decoded password", "connection failed" in error.detail
                  and all(value not in error.detail + str(log.call_args) for value in (database_url, "database%2Ftest-secret", "database/test-secret", "discord-test-value")))
    finally:
        api.get_connection = old_get_connection
        api.AITaskRepository = old_repository
        config.AI_TASK_RUNNER_API_TOKEN = old_token


def repository_runtime_checks():
    task_id = UUID("00000000-0000-0000-0000-000000000001")
    claim_token = UUID("00000000-0000-0000-0000-000000000002")
    connection = FakeConnection()
    cursor = FakeCursor()
    connection.cursor = lambda: cursor
    old_fetch_one = repository.fetch_one
    repository.fetch_one = lambda current_cursor: None
    try:
        repo = repository.AITaskRepository(connection, bot_id="bot")
        repo.claim_next_task(runner_id="runner-1")
        claim_params = cursor.calls[-1][1]
        check("claim passes lease constant", repository.RUNNER_LEASE_SECONDS in claim_params)
        repo.heartbeat(task_id=task_id, runner_id="runner-1", claim_token=claim_token)
        heartbeat_params = cursor.calls[-1][1]
        check("heartbeat passes lease constant", repository.RUNNER_LEASE_SECONDS in heartbeat_params)
        check("heartbeat passes deployment lease constant", repository.DEPLOYMENT_LEASE_SECONDS in heartbeat_params)
    finally:
        repository.fetch_one = old_fetch_one


def idempotency_checks():
    task_id = UUID("00000000-0000-0000-0000-000000000001")
    claim_token = UUID("00000000-0000-0000-0000-000000000002")
    sha = "a" * 40
    existing_ready = {
        "task_id": task_id, "status": "ready_for_review", "commit_sha": sha,
        "pr_number": 12, "pr_url": "https://github.com/U-KID-AI/ichiyon-robot/pull/12",
        "test_summary": "tests", "changed_files_summary": "files",
    }
    old_fetch_one = repository.fetch_one
    try:
        def run_repo(rows):
            connection = FakeConnection()
            cursor = FakeCursor()
            connection.cursor = lambda: cursor
            values = iter(rows)
            repository.fetch_one = lambda current_cursor: next(values)
            return repository.AITaskRepository(connection, bot_id="bot")

        repo = run_repo([None, existing_ready])
        same = repo.mark_ready_for_review(
            task_id=task_id, runner_id="runner-1", claim_token=claim_token,
            commit_sha=sha, pr_number=12, pr_url=existing_ready["pr_url"],
            test_summary="tests", changed_files_summary="files",
        )
        check("ready retry with same metadata succeeds", same == existing_ready)
        repo = run_repo([None, existing_ready])
        different = repo.mark_ready_for_review(
            task_id=task_id, runner_id="runner-1", claim_token=claim_token,
            commit_sha="b" * 40, pr_number=12, pr_url=existing_ready["pr_url"],
            test_summary="tests", changed_files_summary="files",
        )
        check("ready retry with different metadata is rejected", different is None)

        failed = {"task_id": task_id, "status": "failed", "error_message": "same"}
        repo = run_repo([None, failed])
        check("failed same payload retry succeeds", repo.mark_failed(task_id=task_id, runner_id="runner-1", claim_token=claim_token, error_message="same") == failed)
        repo = run_repo([None, failed])
        check("failed different payload retry is rejected", repo.mark_failed(task_id=task_id, runner_id="runner-1", claim_token=claim_token, error_message="different") is None)

        human = {"task_id": task_id, "status": "needs_human", "progress_summary": "same"}
        repo = run_repo([None, human])
        check("needs_human same payload retry succeeds", repo.mark_needs_human(task_id=task_id, runner_id="runner-1", claim_token=claim_token, reason="same") == human)
        repo = run_repo([None, human])
        check("needs_human different payload retry is rejected", repo.mark_needs_human(task_id=task_id, runner_id="runner-1", claim_token=claim_token, reason="different") is None)

        testing_row = {"task_id": task_id, "status": "testing", "runner_id": "runner-1", "claim_token": claim_token}
        repo = run_repo([testing_row])
        check("testing initial transition succeeds", repo.mark_testing(task_id=task_id, runner_id="runner-1", claim_token=claim_token) == testing_row)
        repo = run_repo([None, testing_row])
        check("testing same claim retry succeeds", repo.mark_testing(task_id=task_id, runner_id="runner-1", claim_token=claim_token) == testing_row)
        repo = run_repo([None, None])
        check("testing different runner is rejected", repo.mark_testing(task_id=task_id, runner_id="runner-2", claim_token=claim_token) is None)
        repo = run_repo([None, None])
        check("testing different claim token is rejected", repo.mark_testing(task_id=task_id, runner_id="runner-1", claim_token=UUID("00000000-0000-0000-0000-000000000003")) is None)
        repo = run_repo([None, {"task_id": task_id, "status": "failed", "runner_id": "runner-1", "claim_token": claim_token}])
        check("testing other status is rejected", repo.mark_testing(task_id=task_id, runner_id="runner-1", claim_token=claim_token) is None)
    finally:
        repository.fetch_one = old_fetch_one


def main():
    columns = (
        "runner_id", "claim_token", "claimed_at", "heartbeat_at", "lease_expires_at",
        "attempt_count", "base_commit_sha", "commit_sha", "pr_number", "pr_url",
        "test_summary", "changed_files_summary", "discord_reported_at",
    )
    for column in columns:
        check(f"migration column {column}", re.search(rf"\b{column}\b", MIGRATION) is not None)
    check("attempt_count constraint", "attempt_count >= 0" in MIGRATION)
    check("lease index", "status, lease_expires_at" in MIGRATION)

    admin_compose = re.search(
        r"(?ms)^  admin:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        COMPOSE_SOURCE,
    )
    admin_body = admin_compose.group("body") if admin_compose else ""
    check(
        "admin compose passes runner token",
        "AI_TASK_RUNNER_API_TOKEN: ${AI_TASK_RUNNER_API_TOKEN:-}"
        in admin_body,
    )
    admin_port = re.search(
        r"(?m)^[ \t]+ADMIN_PORT:[ \t]+\$\{ADMIN_PORT:-(?P<default>[0-9]+)\}[ \t]*$",
        admin_body,
    )
    check("admin compose passes admin port", admin_port is not None)
    if admin_port is not None:
        default_port = admin_port.group("default")
        check(
            "admin compose command uses admin port",
            f"--port $${{ADMIN_PORT:-{default_port}}}" in admin_body,
        )
        check(
            "admin compose publishes admin port",
            f'"${{ADMIN_PORT:-{default_port}}}:${{ADMIN_PORT:-{default_port}}}"'
            in admin_body,
        )

    old_token = config.AI_TASK_RUNNER_API_TOKEN
    try:
        config.AI_TASK_RUNNER_API_TOKEN = "test-runner-secret"
        check("missing token rejected", rejects(lambda: api.require_runner_token(None)))
        check("wrong token rejected", rejects(lambda: api.require_runner_token("Bearer wrong")))
        api.require_runner_token("Bearer test-runner-secret")
        check("correct bearer accepted", True)
        check("token is header only", "Query" not in API_SOURCE and "token: str" not in API_SOURCE)
    finally:
        config.AI_TASK_RUNNER_API_TOKEN = old_token

    check("runner id validation", rejects(lambda: repository.validate_runner_id("bad runner id")))
    check("claim uses row lock", "FOR UPDATE SKIP LOCKED" in REPO_SOURCE)
    check("claim selects queued", "status = 'queued'" in REPO_SOURCE)
    check("claim increments attempts", "attempt_count = attempt_count + 1" in REPO_SOURCE)
    check("claim token is UUID", "claim_token = uuid.uuid4()" in REPO_SOURCE)
    check("lease uses application constant", "RUNNER_LEASE_SECONDS" in REPO_SOURCE and "%s * INTERVAL '1 second'" in REPO_SOURCE)
    check("heartbeat checks claim token", "claim_token = %s" in REPO_SOURCE)
    check("heartbeat checks runner", "runner_id = %s" in REPO_SOURCE)
    check("heartbeat rejects expired lease", "lease_expires_at > NOW()" in REPO_SOURCE)
    check("progress fields are fixed", "current_step = COALESCE" in REPO_SOURCE and "base_commit_sha = COALESCE" in REPO_SOURCE)
    progress_fields = api.ProgressRequest.model_fields if hasattr(api.ProgressRequest, "model_fields") else api.ProgressRequest.__fields__
    check("progress has no arbitrary column API", "column:" not in API_SOURCE and "column" not in progress_fields)
    check("progress has no arbitrary status", "status:" not in API_SOURCE and "status" not in progress_fields)
    check("testing only from running", "status = 'running' AND lease_expires_at > NOW()" in REPO_SOURCE)
    check("fail only active states", "status IN ('running', 'testing', 'deploying')" in REPO_SOURCE)
    check("needs human only active states", "status = 'needs_human'" in REPO_SOURCE)
    check("ready only testing", "status = 'testing' AND lease_expires_at > NOW()" in REPO_SOURCE)
    check("SHA validation", repository.is_valid_sha1("a" * 40) and not repository.is_valid_sha1("a" * 39))
    check("PR number validation", rejects(lambda: repository.validate_pr_number(0)))
    check("PR URL validation", repository.is_valid_pr_url("https://github.com/U-KID-AI/ichiyon-robot/pull/12", 12) and not repository.is_valid_pr_url("https://example.test/pull/12", 12))
    check("stale lease moves to human", "SET status = 'needs_human'" in REPO_SOURCE and "lease_expires_at < NOW()" in REPO_SOURCE)
    check("stale lease never queues", "status = 'queued'" not in REPO_SOURCE.split("def expire_stale_leases", 1)[1].split("def claim_next_task", 1)[0])
    check("terminal retry has idempotent lookup", "existing.get(\"status\") == status" in REPO_SOURCE)
    claim_response = API_SOURCE.split("return {\"task\": {", 1)[1].split("}}", 1)[0]
    check("responses contain no secrets", all(secret not in claim_response for secret in ("DATABASE_URL", "DISCORD_TOKEN", "AI_TASK_RUNNER_API_TOKEN")))
    check("no runner subprocess/git/codex", not re.search(r"\b(subprocess|git|codex)\b", API_SOURCE, re.IGNORECASE))
    check("SQL is parameterized", "execute(f" not in REPO_SOURCE and "%s" in REPO_SOURCE)
    check("claim body is strict", getattr(api.RunnerRequest.Config, "extra", None) == "forbid")
    check("fixed endpoint set", all(path in API_SOURCE for path in ("/claim", "/heartbeat", "/progress", "/testing", "/retry", "/fail", "/needs-human", "/ready-for-review")))
    runtime_api_checks()
    repository_runtime_checks()
    idempotency_checks()
    # This entry point is already run by CI; keep retry/lease coverage in that path.
    from scripts.check_ai_task_phase3c_control_plane import (
        check_retry_repository, check_nullable_workflow_id, check_retry_api,
        check_deployment_leases, check_lease_responses, check_client_diagnostics,
    )
    check_retry_repository()
    check_nullable_workflow_id()
    check_retry_api()
    check_deployment_leases()
    check_lease_responses()
    check_client_diagnostics()
    print("AI task control plane checks passed")


if __name__ == "__main__":
    main()
