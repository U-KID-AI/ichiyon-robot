"""Offline deployment and retry checks: in-memory SQL and fixed API/client.

SQLite executes the repository predicates, with only PostgreSQL placeholders,
NOW() and the heartbeat interval translated. No external database is used.
"""

import inspect
import io
import json
import re
import sqlite3
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Importing application config must not read .env or inherited credentials.
with patch("dotenv.load_dotenv"), patch("os.environ", {}):
    from admin import ai_tasks_internal as api
    from bot.repositories import ai_tasks as repository
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
import ai_task_api_client as client_module
from ai_task_api_client import RunnerAPIClient, RunnerAPIError
from check_ai_task_phase2d_control_plane import TASK_ID, CLAIM_TOKEN, HEAD_SHA, MERGE_SHA, PR_URL

OWNER = dict(task_id=TASK_ID, runner_id="runner-1", claim_token=CLAIM_TOKEN)
METADATA = dict(commit_sha=HEAD_SHA, pr_number=123, pr_url=PR_URL,
                test_summary="tests=0", changed_files_summary="src/main.py",
                ci_workflow_run_id=900, review_summary="No findings.", merge_commit_sha=MERGE_SHA)
PROOF = dict(deployed_commit_sha=MERGE_SHA, deployment_summary="Deployment verified.")


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS " + name)


def rejects(call):
    try:
        call()
    except (ValueError, HTTPException):
        return True
    return False


class Connection:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.commits = 0
        self.calls = []
        self.db.create_function("GREATEST", 2, lambda a, b: max(value for value in (a, b) if value is not None))
        self.db.create_function("regexp", 2, lambda pattern, value: value is not None and re.search(pattern, value) is not None)
        self.db.create_function("char_length", 1, lambda value: len(value) if value is not None else None)
        # Exercise the actual reviewed-merge/deployment CHECK expressions offline.
        constraints = []
        for name in ("061_add_ai_task_phase2d_fields.sql", "062_add_ai_task_deployment_fields.sql"):
            sql = (ROOT / "migrations" / name).read_text(encoding="utf-8")
            constraints.extend(re.findall(r"ADD CONSTRAINT\s+\w+\s+(CHECK\s*\(.*?\))(?=\s*[,;])", sql, re.S))
        check("deployment migration checks loaded", len(constraints) == 7)
        schema = """CREATE TABLE ai_tasks (
            task_id TEXT, bot_id TEXT, runner_id TEXT, claim_token TEXT, status TEXT,
            lease_expires_at INTEGER, heartbeat_at INTEGER, claimed_at INTEGER,
            attempt_count INTEGER DEFAULT 1,
            commit_sha TEXT, pr_number INTEGER, pr_url TEXT, test_summary TEXT,
            changed_files_summary TEXT, ci_workflow_run_id INTEGER, review_summary TEXT,
            merge_commit_sha TEXT, current_step TEXT, progress_summary TEXT,
            result_summary TEXT, deployment_started_at INTEGER, updated_at INTEGER,
            deployed_commit_sha TEXT, deployment_summary TEXT, deployed_at INTEGER,
            completed_at INTEGER, error_message TEXT, base_commit_sha TEXT,
            CHECK (length(progress_summary) <= 4000), CHECK (length(error_message) <= 4000),
            """ + ", ".join(constraints).replace(" ~ ", " REGEXP ") + ")"
        self.db.execute(schema)
        self.reset()

    def reset(self, status="testing", lease=200):
        self.now = 100
        self.db.execute("DELETE FROM ai_tasks")
        self.db.execute("INSERT INTO ai_tasks (task_id, bot_id, runner_id, claim_token, status, lease_expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (str(TASK_ID), "bot", "runner-1", str(CLAIM_TOKEN), status, lease))

    def row(self):
        return dict(self.db.execute("SELECT * FROM ai_tasks").fetchone())

    def commit(self):
        self.db.commit()
        self.commits += 1

    def rollback(self):
        self.db.rollback()

    @contextmanager
    def cursor(self):
        connection = self
        class Cursor:
            def execute(self, sql, params):
                connection.calls.append((sql, params))
                sql = sql.replace("(%s * INTERVAL '1 second')", "%s").replace("NOW()", str(connection.now)).replace("%s", "?")
                self.result = connection.db.execute(sql, tuple(str(p) if isinstance(p, UUID) else p for p in params))
                self.description = [SimpleNamespace(name=c[0]) for c in self.result.description]
            def fetchone(self):
                return self.result.fetchone()
            def fetchall(self):
                return self.result.fetchall()
        yield Cursor()


def check_repository():
    connection = Connection()
    repo = repository.AITaskRepository(connection, bot_id="bot")
    connection.db.execute("UPDATE ai_tasks SET merge_commit_sha=?", (MERGE_SHA,))
    check("no direct testing completion", repo.mark_completed(**OWNER, **PROOF) is None)
    check("testing to deploying", repo.mark_deploying(**OWNER, **METADATA)["status"] == "deploying")
    first = connection.row()
    check("reviewed merge metadata bound exactly", all(first[k] == v for k, v in METADATA.items())
          and first["current_step"] == "deploying" and first["deployment_started_at"] == 100)
    check("exact deploying retry", repo.mark_deploying(**OWNER, **METADATA) is not None and connection.row() == first)
    for key, value in METADATA.items():
        replacement = value + 1 if isinstance(value, int) else value + "different"
        if key.endswith("sha"):
            replacement = "c" * 40
        changed = dict(METADATA, **{key: replacement})
        if key == "pr_number":
            changed["pr_url"] = PR_URL[:-3] + str(replacement)
        if key == "pr_url":
            changed["pr_number"] = 124
            changed[key] = PR_URL[:-3] + "124"
        check("different deploying metadata rejected: " + key, repo.mark_deploying(**OWNER, **changed) is None)
    check("different deployment SHA rejected", repo.mark_completed(**OWNER, **dict(PROOF, deployed_commit_sha="c" * 40)) is None)
    check("deploying completion", repo.mark_completed(**OWNER, **PROOF)["status"] == "completed")
    completed = connection.row()
    check("completion preserves reviewed merge metadata", all(completed[k] == v for k, v in METADATA.items())
          and completed["deployed_at"] == 100 and completed["completed_at"] == 100
          and completed["current_step"] == "completed")
    check("exact completed retry leaves timestamps and metadata unchanged",
          repo.mark_completed(**OWNER, **PROOF) is not None and connection.row() == completed)
    connection.db.execute("UPDATE ai_tasks SET lease_expires_at=0")
    check("completed retry survives lease expiry", repo.mark_completed(**OWNER, **PROOF) is not None)
    for changed in (dict(PROOF, deployed_commit_sha="c" * 40), dict(PROOF, deployment_summary="different")):
        check("different completed proof rejected", repo.mark_completed(**OWNER, **changed) is None)
    check("wrong owner cannot retry", repo.mark_completed(**dict(OWNER, runner_id="other"), **PROOF) is None)
    for state in ("queued", "running", "needs_human", "failed", "cancelled", "ready_for_review"):
        connection.reset(state)
        connection.db.execute("UPDATE ai_tasks SET merge_commit_sha=?", (MERGE_SHA,))
        check("no transition from " + state, repo.mark_deploying(**OWNER, **METADATA) is None
              and repo.mark_completed(**OWNER, **PROOF) is None)
    connection.reset(lease=0)
    check("expired testing lease rejected", repo.mark_deploying(**OWNER, **METADATA) is None)
    connection.reset()
    repo.mark_deploying(**OWNER, **METADATA)
    connection.db.execute("UPDATE ai_tasks SET lease_expires_at=0")
    check("expired deploying lease rejected", repo.mark_deploying(**OWNER, **METADATA) is None
          and repo.mark_completed(**OWNER, **PROOF) is None)
    check("stale deploying fails closed", repo.expire_stale_leases()[0]["status"] == "needs_human")
    check("only queued tasks claimable", "status = 'queued'" in inspect.getsource(repo.claim_next_task))
    for operation, fields in ((repo.heartbeat, {}), (repo.update_runner_progress, {"progress_summary": "Waiting"}),
                              (repo.mark_failed, {"error_message": "Failed"}), (repo.mark_needs_human, {"reason": "Inspect"})):
        connection.reset("deploying")
        check("deploying permits " + operation.__name__, operation(**OWNER, **fields) is not None)
    check("generic status helper cannot bypass proof", rejects(lambda: repo.update_status(task_id=TASK_ID, status="completed")))
    connection.db.close()


def check_api_client():
    paths = {route.path for route in api.router.routes}
    check("only fixed routes", paths == {"/internal/ai-tasks/claim"} | {
        "/internal/ai-tasks/{task_id}/" + op for op in
        ("heartbeat", "progress", "testing", "retry", "fail", "needs-human", "ready-for-review", "deploying", "completed")})
    request_owner = {k: v for k, v in OWNER.items() if k != "task_id"}
    for model, fields in ((api.DeployingRequest, METADATA), (api.CompletedRequest, PROOF)):
        check("strict fixed request " + model.__name__, rejects(lambda: model(**request_owner, **fields, operation="shell")))
    check("completion rejects old metadata", rejects(lambda: api.CompletedRequest(**request_owner, **METADATA)))
    connection = Connection()
    repo = repository.AITaskRepository(connection, bot_id="bot")
    @contextmanager
    def get_connection():
        yield connection
    with patch.object(api, "require_runner_token"), patch.object(api, "get_connection", get_connection), patch.object(api, "AITaskRepository", return_value=repo):
        api.deploying(TASK_ID, api.DeployingRequest(**request_owner, **METADATA))
        check("deploying endpoint commits before returning", connection.commits == 1 and connection.row()["status"] == "deploying")
        api.completed(TASK_ID, api.CompletedRequest(**request_owner, **PROOF))
        check("completion endpoint commits proof", connection.commits == 2 and connection.row()["status"] == "completed")
        connection.reset()
        connection.db.commit()
        with patch.object(connection, "commit", side_effect=RuntimeError("commit failed")):
            try:
                api.deploying(TASK_ID, api.DeployingRequest(**request_owner, **METADATA))
                raise AssertionError("uncommitted deploying transition acknowledged")
            except HTTPException as exc:
                check("failed commit never acknowledges deploying", exc.status_code == 503
                      and connection.row()["status"] == "testing")
        for key, value in (("commit_sha", "z" * 40), ("merge_commit_sha", "z" * 40), ("pr_number", 0),
                           ("pr_number", 2_147_483_648), ("pr_url", PR_URL + "x"), ("ci_workflow_run_id", 0),
                           ("ci_workflow_run_id", 2**63), ("test_summary", "x" * 8001),
                           ("changed_files_summary", "x" * 8001), ("review_summary", "x" * 2001)):
            check("API rejects " + key, rejects(lambda: api.deploying(TASK_ID, api.DeployingRequest(**request_owner, **dict(METADATA, **{key: value})))))
        check("API rejects deployed SHA", rejects(lambda: api.completed(TASK_ID, api.CompletedRequest(**request_owner, **dict(PROOF, deployed_commit_sha="z" * 40)))))
    connection.db.close()
    requests = []
    def requester(method, path, payload):
        requests.append((method, path, payload.copy()))
        if len(requests) % 2:
            raise RunnerAPIError("response lost after commit")
        return json.dumps({"status": path.rsplit("/", 1)[-1]}).encode()
    client = RunnerAPIClient("https://runner.example.test", "dummy", "runner-1", requester=requester)
    for operation, fields in (("deploying", METADATA), ("completed", PROOF)):
        method = getattr(client, "mark_" + operation)
        method(TASK_ID, CLAIM_TOKEN, **fields)
        check("identical ambiguous retry: " + operation, requests[-1] == requests[-2]
              and requests[-1][0] == "POST" and requests[-1][1] == f"/internal/ai-tasks/{TASK_ID}/{operation}"
              and requests[-1][2] == dict(fields, runner_id="runner-1", claim_token=str(CLAIM_TOKEN)))
    for key, value in (("commit_sha", "bad"), ("merge_commit_sha", "bad"), ("pr_number", True),
                       ("pr_number", 2**31), ("pr_url", PR_URL + "x"), ("ci_workflow_run_id", 0),
                       ("ci_workflow_run_id", 2**63), ("test_summary", "x" * 8001),
                       ("changed_files_summary", "x" * 8001), ("review_summary", "")):
        check("client rejects " + key, rejects(lambda: client.mark_deploying(TASK_ID, CLAIM_TOKEN, **dict(METADATA, **{key: value}))))
    for key, value in (("deployed_commit_sha", "bad"), ("deployment_summary", ""), ("deployment_summary", "x" * 4001), ("deployment_summary", 123)):
        check("client rejects " + key, rejects(lambda: client.mark_completed(TASK_ID, CLAIM_TOKEN, **dict(PROOF, **{key: value}))))
    check("invalid requests never sent", len(requests) == 4)
    check("arbitrary operation rejected", rejects(lambda: client._owned("shell", TASK_ID, CLAIM_TOKEN)))
    failed_requests = []
    def unavailable(*args):
        failed_requests.append(args)
        raise RunnerAPIError("unavailable")
    client._requester = unavailable
    for operation, fields in ((client.mark_deploying, METADATA), (client.mark_completed, PROOF)):
        try:
            operation(TASK_ID, CLAIM_TOKEN, **fields)
            raise AssertionError("must propagate second failure")
        except RunnerAPIError:
            pass
    check("retry bounded to once", len(failed_requests) == 4)


def check_retry_repository():
    connection = Connection()
    repo = repository.AITaskRepository(connection, bot_id="bot")
    reason = "deploy exited 1: health endpoint returned HTTP 502"
    for state in ("running", "testing", "deploying"):
        connection.reset()
        repo.mark_deploying(**OWNER, **METADATA)
        connection.db.execute("UPDATE ai_tasks SET status=?, heartbeat_at=95, claimed_at=80", (state,))
        before = connection.row()
        result = repo.retry(**OWNER, reason=reason)
        after = connection.row()
        check("retry from " + state, result["status"] == "testing" and after["current_step"] == "retrying"
              and after["error_message"] == reason and after["progress_summary"] == reason)
        changed_fields = {"status", "current_step", "progress_summary", "error_message", "updated_at", "lease_expires_at"}
        check("retry preserves owner and all evidence from " + state,
              all(after[key] == value for key, value in before.items() if key not in changed_fields))
        expected_lease = max(before["lease_expires_at"], 280) if state == "deploying" else before["lease_expires_at"]
        check("retry returns appropriate lease from " + state, after["lease_expires_at"] == expected_lease
              and result["lease_expires_at"] == expected_lease)
        check("retry repeat succeeds within lease", repo.retry(**OWNER, reason=reason) is not None and connection.row() == after)
        for changes in ({"runner_id": "other"}, {"claim_token": UUID(int=3)}, {"task_id": UUID(int=4)}):
            check("retry rejects wrong " + next(iter(changes)), repo.retry(**dict(OWNER, **changes), reason=reason) is None and connection.row() == after)
        other_bot = repository.AITaskRepository(connection, bot_id="other")
        check("retry rejects wrong bot", other_bot.retry(**OWNER, reason=reason) is None and connection.row() == after)

    for state in ("running", "testing", "deploying"):
        for lease in (0, 100, None):
            connection.reset(state, lease=lease)
            before = connection.row()
            check("retry rejects expired/missing lease in " + state,
                  repo.retry(**OWNER, reason=reason) is None and connection.row() == before)
    for state in ("queued", "ready_for_review", "completed", "failed", "needs_human", "cancelled"):
        connection.reset(state)
        before = connection.row()
        check("retry rejects " + state, repo.retry(**OWNER, reason=reason) is None and connection.row() == before)
    for invalid in ("", "x" * 4001, None, 42):
        check("retry validates reason", rejects(lambda: repo.retry(**OWNER, reason=invalid)))

    connection.reset()
    repo.mark_deploying(**OWNER, **METADATA)
    repo.retry(**OWNER, reason=reason)
    next_metadata = dict(METADATA, commit_sha="c" * 40, pr_number=124, pr_url=PR_URL[:-3] + "124",
                         ci_workflow_run_id=901, merge_commit_sha="d" * 40,
                         review_summary="Second review.", test_summary="new tests passed",
                         changed_files_summary="src/fix.py")
    check("retry allows fresh commit PR CI and merge", repo.mark_deploying(**OWNER, **next_metadata)["status"] == "deploying"
          and all(connection.row()[key] == value for key, value in next_metadata.items()))
    check("fresh deployment clears old failure", connection.row()["error_message"] is None)
    check("old deployment proof no longer accepted", repo.mark_completed(**OWNER, **PROOF) is None)
    check("new deployment proof completes same task", repo.mark_completed(**OWNER, **dict(PROOF, deployed_commit_sha="d" * 40))["status"] == "completed")
    for state in ("testing", "deploying"):
        connection.reset()
        repo.mark_deploying(**OWNER, **next_metadata)
        if state == "testing":
            repo.retry(**OWNER, reason=reason)
        error = "retry attempts exhausted: " + reason
        check("exhaustion can fail from " + state, repo.mark_failed(**OWNER, error_message=error)["status"] == "failed")
        final = connection.row()
        check("exhaustion preserves merge evidence", all(final[key] == value for key, value in next_metadata.items())
              and final["error_message"] == error and final["completed_at"] == 100)
        check("exhaustion is idempotent", repo.mark_failed(**OWNER, error_message=error) is not None and connection.row() == final)
    connection.db.close()


def check_nullable_workflow_id():
    connection = Connection()
    repo = repository.AITaskRepository(connection, bot_id="bot")
    request_owner = {key: value for key, value in OWNER.items() if key != "task_id"}
    @contextmanager
    def get_connection():
        yield connection
    requests = []
    def requester(method, path, payload):
        requests.append((method, path, payload.copy()))
        if len(requests) % 2:
            raise RunnerAPIError("response lost after merge metadata committed")
        return b'{"status":"deploying"}'
    client = RunnerAPIClient("https://runner.example.test", "dummy", "runner-1", requester=requester)
    for omitted in (False, True):
        fields = dict(METADATA, ci_workflow_run_id=None)
        if omitted:
            del fields["ci_workflow_run_id"]
        connection.reset()
        result = repo.mark_deploying(**OWNER, **fields)
        check("deploying accepts absent workflow ID", result["status"] == "deploying" and result["ci_workflow_run_id"] is None)
        first = connection.row()
        check("absent workflow ID preserves merge evidence", all(first[key] == value for key, value in METADATA.items() if key != "ci_workflow_run_id")
              and "CI run None" not in first["result_summary"] and MERGE_SHA in first["result_summary"])
        check("absent workflow ID allows exact lost-response retry", repo.mark_deploying(**OWNER, **fields) is not None and connection.row() == first)
        check("deploying cannot rewrite absent workflow ID without retry transition", repo.mark_deploying(**OWNER, **METADATA) is None and connection.row() == first)
        repo.retry(**OWNER, reason="deployment failed")
        check("new deployment can replace absent workflow ID", repo.mark_deploying(**OWNER, **METADATA)["ci_workflow_run_id"] == METADATA["ci_workflow_run_id"])
        repo.retry(**OWNER, reason="deployment failed again")
        with patch.object(api, "require_runner_token"), patch.object(api, "get_connection", get_connection), patch.object(api, "AITaskRepository", return_value=repo):
            request = api.DeployingRequest(**request_owner, **fields)
            check("API request defaults absent workflow ID to null", request.ci_workflow_run_id is None)
            result = api.deploying(TASK_ID, request)
            check("API commits deployment without workflow ID", result["status"] == "deploying" and connection.row()["ci_workflow_run_id"] is None)
        check("successful deployment proof works without workflow ID", repo.mark_completed(**OWNER, **PROOF)["status"] == "completed")
        client.mark_deploying(TASK_ID, CLAIM_TOKEN, **fields)
        check("client sends null workflow ID on identical ambiguous retry", requests[-1] == requests[-2]
              and requests[-1][2]["ci_workflow_run_id"] is None and requests[-1][2]["merge_commit_sha"] == MERGE_SHA)
    repository.validate_workflow_run_id(None)
    repository.validate_workflow_run_id(2**63 - 1)
    for invalid in (0, -1, True, False, "900", 900.0, 2**63):
        check("repository rejects invalid non-null workflow ID", rejects(lambda: repository.validate_workflow_run_id(invalid)))
        check("client rejects invalid non-null workflow ID", rejects(lambda: client.mark_deploying(TASK_ID, CLAIM_TOKEN, **dict(METADATA, ci_workflow_run_id=invalid))))
        with patch.object(api, "require_runner_token"), patch.object(api, "_run_owned_operation") as operation:
            check("API rejects invalid non-null workflow ID", rejects(lambda: api.deploying(TASK_ID, api.DeployingRequest(**request_owner, **dict(METADATA, ci_workflow_run_id=invalid)))) and not operation.called)
    check("invalid workflow ID requests are never sent", len(requests) == 4)
    connection.db.close()


def check_retry_api():
    connection = Connection()
    repo = repository.AITaskRepository(connection, bot_id="bot")
    @contextmanager
    def get_connection():
        yield connection
    request_owner = {key: value for key, value in OWNER.items() if key != "task_id"}
    @contextmanager
    def configured():
        with patch.object(api.config, "AI_TASK_RUNNER_API_TOKEN", "runner-test-secret"), patch.object(api, "get_connection", get_connection), patch.object(api, "AITaskRepository", return_value=repo):
            yield
    with configured():
        repo.mark_deploying(**OWNER, **METADATA)
        connection.db.commit()
        request = api.RetryRequest(**request_owner, reason=f"HTTP 502 runner-test-secret {CLAIM_TOKEN}")
        result = api.retry(TASK_ID, request, "Bearer runner-test-secret")
        check("retry endpoint returns committed testing status and lease", result == {"task_id": str(TASK_ID), "status": "testing", "lease_expires_at": 1000} and connection.commits == 1)
        check("retry endpoint stores actual redacted reason", "HTTP 502" in connection.row()["error_message"]
              and "runner-test-secret" not in connection.row()["error_message"] and str(CLAIM_TOKEN) not in connection.row()["error_message"])
        for changes in ({"runner_id": "other"}, {"claim_token": UUID(int=3)}):
            before = connection.row()
            try:
                api.retry(TASK_ID, api.RetryRequest(**dict(request_owner, **changes), reason="failure"), "Bearer runner-test-secret")
                raise AssertionError("wrong owner accepted")
            except HTTPException as exc:
                check("retry ownership conflict returns 409", exc.status_code == 409 and connection.row() == before)
        connection.db.execute("UPDATE ai_tasks SET lease_expires_at=100")
        connection.db.commit()
        try:
            api.retry(TASK_ID, request, "Bearer runner-test-secret")
            raise AssertionError("expired retry accepted")
        except HTTPException as exc:
            check("retry expired lease returns 409", exc.status_code == 409)
        connection.reset()
        repo.mark_deploying(**OWNER, **METADATA)
        connection.db.commit()
        before = connection.row()
        with patch.object(connection, "commit", side_effect=RuntimeError("disk full password=db-test-secret")):
            try:
                api.retry(TASK_ID, request, "Bearer runner-test-secret")
                raise AssertionError("uncommitted retry acknowledged")
            except HTTPException as exc:
                check("retry commit failure rolls back and reports redacted error", exc.status_code == 503
                      and "disk full" in exc.detail and "db-test-secret" not in exc.detail and connection.row() == before)
    connection.db.close()

    app = FastAPI()
    app.include_router(api.router)
    with patch.object(api.config, "AI_TASK_RUNNER_API_TOKEN", "runner-test-secret"), patch.object(api, "_run_owned_operation", return_value={"task_id": str(TASK_ID), "status": "testing"}) as operation:
        with TestClient(app) as http:
            path = f"/internal/ai-tasks/{TASK_ID}/retry"
            body = dict(runner_id="runner-1", claim_token=str(CLAIM_TOKEN), reason="deploy failed")
            check("retry HTTP auth enforced", http.post(path, json=body).status_code == 401 and not operation.called)
            headers = {"Authorization": "Bearer runner-test-secret"}
            for invalid in ("", "x" * 4001, None, 12):
                check("retry HTTP rejects invalid reason", http.post(path, headers=headers, json=dict(body, reason=invalid)).status_code == 422)
            check("retry HTTP rejects arbitrary fields", http.post(path, headers=headers, json=dict(body, status="completed")).status_code == 422)
            check("retry HTTP accepts bounded reason", http.post(path, headers=headers, json=dict(body, reason="x" * 4000)).status_code == 200)


def check_deployment_leases():
    connection = Connection()
    repo = repository.AITaskRepository(connection, bot_id="bot")
    check("fixed normal and deployment durations", repository.RUNNER_LEASE_SECONDS == 180 and repository.DEPLOYMENT_LEASE_SECONDS == 900)
    for state, duration in (("running", 180), ("testing", 180), ("deploying", 900)):
        connection.reset(state)
        result = repo.heartbeat(**OWNER)
        check("heartbeat renews and returns lease for " + state, result["lease_expires_at"] == 100 + duration
              and connection.row()["lease_expires_at"] == result["lease_expires_at"] and result["heartbeat_at"] == 100)
        sql, params = connection.calls[-1]
        check("heartbeat durations and ownership are parameterized", params == (900, 180, TASK_ID, "bot", "runner-1", CLAIM_TOKEN)
              and sql.count("%s") == len(params) and "900" not in sql and "180" not in sql)
        connection.now = 130
        check("subsequent heartbeat uses current time for " + state, repo.heartbeat(**OWNER)["lease_expires_at"] == 130 + duration)
        connection.reset(state, lease=2000)
        check("heartbeat cannot shorten a later lease for " + state, repo.heartbeat(**OWNER)["lease_expires_at"] == 2000
              and connection.row()["lease_expires_at"] == 2000)

    connection.reset(lease=2000)
    check("deployment reservation cannot shorten existing lease", repo.mark_deploying(**OWNER, **METADATA)["lease_expires_at"] == 2000)
    check("retry preserves existing later deadline", repo.retry(**OWNER, reason="deployment failed")["lease_expires_at"] == 2000)
    connection.reset("deploying", lease=101)
    check("retry guarantees normal duration if reservation is nearly expired", repo.retry(**OWNER, reason="deployment failed")["lease_expires_at"] == 280)

    connection.reset(lease=101)
    start = len(connection.calls)
    result = repo.mark_deploying(**OWNER, **METADATA)
    first = connection.row()
    sql, params = connection.calls[-1]
    check("deploying reserves lease atomically with merge metadata", len(connection.calls) == start + 1
          and result["lease_expires_at"] == first["lease_expires_at"] == 1000
          and first["status"] == "deploying" and all(first[key] == value for key, value in METADATA.items()))
    check("deploying lease duration is a bound SQL parameter", params[-5:] == (900, TASK_ID, "bot", "runner-1", CLAIM_TOKEN)
          and sql.count("%s") == len(params) and "lease_expires_at = GREATEST(lease_expires_at, NOW() + (%s * INTERVAL '1 second'))" in sql)
    connection.now = 145
    repeated = repo.mark_deploying(**OWNER, **METADATA)
    check("idempotent deploying returns original lease without renewing", repeated["lease_expires_at"] == 1000 and connection.row() == first)
    check("idempotent deploying lookup retains lease guard", "lease_expires_at > NOW()" in connection.calls[-1][0])

    connection.now = 400
    check("deployment survives restart longer than normal lease", repo.expire_stale_leases() == []
          and repo.heartbeat(**OWNER)["lease_expires_at"] == 1300)
    renewed = connection.row()
    connection.now = 410
    check("deploying replay returns heartbeat-renewed lease unchanged", repo.mark_deploying(**OWNER, **METADATA)["lease_expires_at"] == 1300 and connection.row() == renewed)
    connection.now = 430
    result = repo.retry(**OWNER, reason="health endpoint failed")
    check("deployment retry preserves the later reservation", result["status"] == "testing"
          and result["lease_expires_at"] == connection.row()["lease_expires_at"] == 1300)
    sql, params = connection.calls[-1]
    check("retry lease duration is a bound SQL parameter", params[2] == 180 and sql.count("%s") == len(params))
    check("deployment retry keeps ownership and merge evidence", all(connection.row()[key] == renewed[key] for key in
          ("runner_id", "claim_token", "claimed_at", "attempt_count", *METADATA)))
    connection.now = 440
    check("retry replay does not extend reserved lease", repo.retry(**OWNER, reason="health endpoint failed")["lease_expires_at"] == 1300)
    connection.now = 450
    check("post-retry heartbeat cannot shorten the reservation", repo.heartbeat(**OWNER)["lease_expires_at"] == 1300)
    connection.now = 1121
    check("normal 180-second renewal catches up naturally", repo.heartbeat(**OWNER)["lease_expires_at"] == 1301)
    connection.now = 1140
    check("next deployment reserves a fresh 900 seconds", repo.mark_deploying(**OWNER, **METADATA)["lease_expires_at"] == 2040)
    connection.now = 1600
    check("valid deployment proof completes after normal lease window", repo.mark_completed(**OWNER, **PROOF)["status"] == "completed")

    operations = ((repo.heartbeat, {}), (repo.mark_deploying, METADATA), (repo.retry, {"reason": "retry"}))
    for state in ("running", "testing", "deploying"):
        for deadline in (None, 99, 100):
            connection.reset()
            repo.mark_deploying(**OWNER, **METADATA)
            connection.db.execute("UPDATE ai_tasks SET status=?, lease_expires_at=?", (state, deadline))
            before = connection.row()
            for operation, fields in operations:
                check("expired claim cannot be resurrected by " + operation.__name__ + " from " + state,
                      operation(**OWNER, **fields) is None and connection.row() == before)
    for state in ("testing", "deploying"):
        connection.reset()
        repo.mark_deploying(**OWNER, **METADATA)
        connection.db.execute("UPDATE ai_tasks SET status=?", (state,))
        before = connection.row()
        for changes in ({"runner_id": "other"}, {"claim_token": UUID(int=3)}, {"task_id": UUID(int=4)}):
            for operation, fields in operations:
                check("wrong owner cannot alter lease via " + operation.__name__, operation(**dict(OWNER, **changes), **fields) is None and connection.row() == before)
        other_repo = repository.AITaskRepository(connection, bot_id="other")
        for operation, fields in operations:
            check("wrong bot cannot alter lease via " + operation.__name__, getattr(other_repo, operation.__name__)(**OWNER, **fields) is None and connection.row() == before)
    for state in ("queued", "ready_for_review", "failed", "completed", "cancelled", "needs_human"):
        connection.reset(state, lease=1000)
        before = connection.row()
        for operation, fields in operations:
            check("lease operation leaves " + state + " unchanged", operation(**OWNER, **fields) is None and connection.row() == before)
    connection.reset()
    repo.mark_deploying(**OWNER, **METADATA)
    connection.now = 1001
    check("expired deployment still requires human inspection", repo.expire_stale_leases()[0]["status"] == "needs_human")
    connection.db.close()


def check_lease_responses():
    app = FastAPI()
    app.include_router(api.router)
    connection = SimpleNamespace(commit=Mock(), rollback=Mock())
    repo = Mock()
    @contextmanager
    def get_connection():
        yield connection
    owner = {"runner_id": "runner-1", "claim_token": str(CLAIM_TOKEN)}
    deadline = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    headers = {"Authorization": "Bearer runner-test-secret"}
    with patch.object(api.config, "AI_TASK_RUNNER_API_TOKEN", "runner-test-secret"), patch.object(api, "get_connection", get_connection), patch.object(api, "AITaskRepository", return_value=repo):
        with TestClient(app) as http:
            for endpoint, method, state, fields in (("deploying", "mark_deploying", "deploying", METADATA),
                                                   ("heartbeat", "heartbeat", "deploying", {}),
                                                   ("retry", "retry", "testing", {"reason": "deployment failed"})):
                row = {"task_id": TASK_ID, "status": state, "lease_expires_at": deadline,
                       "runner_id": "runner-1", "claim_token": CLAIM_TOKEN, "private": "runner-test-secret"}
                getattr(repo, method).return_value = row
                path = f"/internal/ai-tasks/{TASK_ID}/{endpoint}"
                for _ in range(2):
                    response = http.post(path, headers=headers, json=dict(owner, **fields))
                    payload = response.json()
                    check(endpoint + " initial/repeated response returns root timestamp", response.status_code == 200
                          and set(payload) == {"task_id", "status", "lease_expires_at"}
                          and payload["task_id"] == str(TASK_ID) and payload["status"] == state
                          and datetime.fromisoformat(payload["lease_expires_at"].replace("Z", "+00:00")) == deadline)
                    check(endpoint + " response does not expose tokens", str(CLAIM_TOKEN) not in response.text and "runner-test-secret" not in response.text)
                calls = getattr(repo, method).call_count
                check(endpoint + " lease response remains authenticated", http.post(path, json=dict(owner, **fields)).status_code == 401
                      and getattr(repo, method).call_count == calls)
                getattr(repo, method).return_value = None
                response = http.post(path, headers=headers, json=dict(owner, **fields))
                check(endpoint + " conflict does not report a lease", response.status_code == 409 and "lease_expires_at" not in response.json())
            check("lease responses commit before acknowledgment", connection.commit.call_count == 6)
            repo.heartbeat.return_value = {"task_id": TASK_ID, "status": "running"}
            response = http.post(f"/internal/ai-tasks/{TASK_ID}/heartbeat", headers=headers, json=owner)
            check("owned response omits absent lease field", response.json() == {"task_id": str(TASK_ID), "status": "running"})


def check_client_diagnostics():
    requests = []
    def requester(method, path, payload):
        requests.append((method, path, payload))
        return b'{"status":"testing"}'
    client = RunnerAPIClient("https://runner.example.test", "runner-test-secret", "runner-1", requester=requester)
    path = f"/internal/ai-tasks/{TASK_ID}/retry"
    client.retry(TASK_ID, CLAIM_TOKEN, reason=f"deploy HTTP 502 runner-test-secret {CLAIM_TOKEN}")
    check("client retry sends owned POST and redacted reason", requests[-1][0:2] == ("POST", path)
          and requests[-1][2]["runner_id"] == "runner-1" and requests[-1][2]["claim_token"] == str(CLAIM_TOKEN)
          and "HTTP 502" in requests[-1][2]["reason"] and "runner-test-secret" not in requests[-1][2]["reason"]
          and str(CLAIM_TOKEN) not in requests[-1][2]["reason"])
    for invalid in ("", "x" * 4001, None, 12):
        check("client retry validates reason", rejects(lambda: client.retry(TASK_ID, CLAIM_TOKEN, reason=invalid)))
    check("invalid retry requests not sent", len(requests) == 1)

    client._requester = client._request
    body = json.dumps({"detail": f"constraint violation runner-test-secret {CLAIM_TOKEN} password=db-test-secret"}).encode()
    for exc in (HTTPError(client.base_url + path, 503, "Unavailable", {}, io.BytesIO(body)),
                URLError("connection refused runner-test-secret"), TimeoutError("deadline expired password=db-test-secret"),
                OSError("socket reset runner-test-secret"), ValueError("invalid URL runner-test-secret")):
        with patch.object(client_module, "urlopen", side_effect=exc):
            try:
                client.retry(TASK_ID, CLAIM_TOKEN, reason="deploy failed")
                raise AssertionError("request failure swallowed")
            except RunnerAPIError as error:
                rendered = "".join(traceback.format_exception(error))
                check("client error includes method path and actual error", f"POST {path}" in str(error)
                      and ("HTTP 503" in str(error) and "constraint violation" in str(error)
                           if isinstance(exc, HTTPError) else type(exc).__name__ in str(error)))
                check("client error and traceback redact credentials", all(secret not in rendered for secret in
                      ("runner-test-secret", str(CLAIM_TOKEN), "db-test-secret")))
    for raw in (b'not JSON password=db-test-secret', b'["runner-test-secret"]', b'\xffrunner-test-secret'):
        client._requester = lambda *args: raw
        try:
            client.retry(TASK_ID, CLAIM_TOKEN, reason="failure")
            raise AssertionError("invalid response accepted")
        except RunnerAPIError as error:
            check("invalid response has request and redacted body", f"POST {path}" in str(error) and "body:" in str(error)
                  and "runner-test-secret" not in str(error) and "db-test-secret" not in str(error))
    client._requester = client._request
    client.max_response_bytes = 32
    oversized = io.BytesIO(b"x" * 24 + b"runner-test-secret")
    error = HTTPError(client.base_url + path, 502, "Bad Gateway", {}, oversized)
    with patch.object(client_module, "urlopen", side_effect=error):
        try:
            client.retry(TASK_ID, CLAIM_TOKEN, reason="failure")
            raise AssertionError("oversized HTTP error accepted")
        except RunnerAPIError as exc:
            check("oversized error retains request and status without partial secret", f"POST {path}" in str(exc)
                  and "HTTP 502 Bad Gateway" in str(exc) and "exceeds 32 bytes" in str(exc)
                  and "runner-t" not in str(exc) and oversized.closed)
    class UnreadableBody(io.BytesIO):
        def read(self, size=-1):
            raise OSError("body read failed runner-test-secret")
    unreadable = UnreadableBody()
    with patch.object(client_module, "urlopen", side_effect=HTTPError(client.base_url + path, 503, "Unavailable", {}, unreadable)):
        try:
            client.retry(TASK_ID, CLAIM_TOKEN, reason="failure")
            raise AssertionError("unreadable HTTP error accepted")
        except RunnerAPIError as exc:
            check("unreadable HTTP body retains actual redacted exception", "HTTP 503" in str(exc)
                  and "OSError: body read failed" in str(exc) and "runner-test-secret" not in str(exc)
                  and unreadable.closed)


def main():
    migration = (ROOT / "migrations/062_add_ai_task_deployment_fields.sql").read_text(encoding="utf-8")
    for status in repository.AI_TASK_STATUSES:
        check("migration preserves status " + status, "'" + status + "'" in migration)
    for field in ("deployed_commit_sha VARCHAR(40)", "deployment_summary TEXT", "deployment_started_at TIMESTAMPTZ", "deployed_at TIMESTAMPTZ",
                  "DROP CONSTRAINT ai_tasks_status_safe", "^[0-9a-fA-F]{40}$", "char_length(deployment_summary) <= 4000",
                  "deployed_at IS NULL OR deployed_commit_sha IS NOT NULL"):
        check("migration constraint/field " + field, field in migration)
    check_repository()
    check_api_client()
    check_retry_repository()
    check_nullable_workflow_id()
    check_retry_api()
    check_deployment_leases()
    check_lease_responses()
    check_client_diagnostics()
    print("AI task deployment and retry control-plane checks passed")


if __name__ == "__main__":
    main()
