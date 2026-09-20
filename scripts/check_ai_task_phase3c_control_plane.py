"""Offline Phase 3C-1D checks: in-memory SQL, fixed API/client and fake runner.

SQLite executes the repository predicates, with only PostgreSQL placeholders,
NOW() and the heartbeat interval translated. No external database is used.
"""

import inspect
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Importing application config must not read .env or inherited credentials.
with patch("dotenv.load_dotenv"), patch("os.environ", {}):
    from admin import ai_tasks_internal as api
    from bot.repositories import ai_tasks as repository
from fastapi import HTTPException
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
        self.db.execute("""CREATE TABLE ai_tasks (
            task_id TEXT, bot_id TEXT, runner_id TEXT, claim_token TEXT, status TEXT,
            lease_expires_at INTEGER, heartbeat_at INTEGER, claimed_at INTEGER,
            commit_sha TEXT, pr_number INTEGER, pr_url TEXT, test_summary TEXT,
            changed_files_summary TEXT, ci_workflow_run_id INTEGER, review_summary TEXT,
            merge_commit_sha TEXT, current_step TEXT, progress_summary TEXT,
            result_summary TEXT, deployment_started_at INTEGER, updated_at INTEGER,
            deployed_commit_sha TEXT, deployment_summary TEXT, deployed_at INTEGER,
            completed_at INTEGER, error_message TEXT, base_commit_sha TEXT)""")
        self.reset()

    def reset(self, status="testing", lease=200):
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
                sql = sql.replace("(%s * INTERVAL '1 second')", "%s").replace("NOW()", "100").replace("%s", "?")
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
        ("heartbeat", "progress", "testing", "fail", "needs-human", "ready-for-review", "deploying", "completed")})
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
    # Complete fake orchestration includes ordering, absent deployer, bad proof,
    # ambiguous durable transition and lease loss on either side of deployment.
    from check_ai_task_local_runner import main as check_runner
    with patch("os.environ", {}), patch("tempfile.tempdir", str(ROOT)):
        check_runner()
    print("AI task Phase 3C-1D checks passed")


if __name__ == "__main__":
    main()
