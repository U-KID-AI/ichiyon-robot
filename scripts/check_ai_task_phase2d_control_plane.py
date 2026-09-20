"""Offline checks for Phase 2D completion state."""

import inspect
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from admin import ai_tasks_internal as api
from bot.repositories import ai_tasks as repository
from ai_task_api_client import RunnerAPIClient


TASK_ID = UUID(
    "00000000-0000-0000-0000-000000000001"
)
CLAIM_TOKEN = UUID(
    "00000000-0000-0000-0000-000000000002"
)

HEAD_SHA = "a" * 40
MERGE_SHA = "b" * 40
PR_NUMBER = 123
PR_URL = (
    "https://github.com/"
    "U-KID-AI/ichiyon-robot/pull/123"
)
WORKFLOW_RUN_ID = 900


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def rejects(call):
    try:
        call()
    except (ValueError, RuntimeError):
        return True
    return False


class FakeConnection:
    def cursor(self):
        return self.cursor_value


class FakeCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))


def run_repo(rows):
    connection = FakeConnection()
    cursor = FakeCursor()
    connection.cursor_value = cursor

    values = iter(rows)

    old_fetch_one = repository.fetch_one

    repository.fetch_one = (
        lambda _cursor: next(values)
    )

    return (
        repository.AITaskRepository(
            connection,
            bot_id="bot",
        ),
        cursor,
        old_fetch_one,
    )


def main():
    migration = (
        ROOT
        / "migrations"
        / "061_add_ai_task_phase2d_fields.sql"
    ).read_text(encoding="utf-8")

    for column in (
        "ci_workflow_run_id",
        "review_summary",
        "merge_commit_sha",
    ):
        check(
            f"Phase 2D migration column {column}",
            column in migration,
        )

    check(
        "Phase 2D migration validates merge SHA",
        "merge_commit_sha_format" in migration
        and "^[0-9a-fA-F]{40}$" in migration,
    )

    check(
        "Phase 2D migration validates workflow ID",
        "ci_workflow_run_id > 0"
        in migration,
    )

    repo_source = inspect.getsource(
        repository.AITaskRepository
    )

    check(
        "completion only transitions active testing task",
        "def mark_completed" in repo_source
        and "status = 'testing'"
        in repo_source[
            repo_source.index(
                "def mark_completed"
            ):
            repo_source.index(
                "def _transition_active_task"
            )
        ]
        and "lease_expires_at > NOW()"
        in repo_source[
            repo_source.index(
                "def mark_completed"
            ):
            repo_source.index(
                "def _transition_active_task"
            )
        ],
    )

    check(
        "completion records completed_at",
        "completed_at"
        in repo_source[
            repo_source.index(
                "def mark_completed"
            ):
            repo_source.index(
                "def _transition_active_task"
            )
        ],
    )

    check(
        "completion endpoint is fixed",
        hasattr(api, "completed")
        and hasattr(api, "CompletedRequest"),
    )

    requests = []

    def requester(method, path, payload):
        requests.append(
            (method, path, payload)
        )
        return json.dumps(
            {
                "task_id": str(TASK_ID),
                "status": "completed",
            }
        ).encode("utf-8")

    client = RunnerAPIClient(
        "https://runner.example.test",
        "secret",
        "runner-1",
        requester=requester,
    )

    response = client.mark_completed(
        TASK_ID,
        CLAIM_TOKEN,
        commit_sha=HEAD_SHA,
        pr_number=PR_NUMBER,
        pr_url=PR_URL,
        test_summary="tests=0",
        changed_files_summary="bot/example.py",
        ci_workflow_run_id=WORKFLOW_RUN_ID,
        review_summary="No findings.",
        merge_commit_sha=MERGE_SHA,
    )

    check(
        "API client completion operation is fixed",
        response["status"] == "completed"
        and len(requests) == 1
        and requests[0][0] == "POST"
        and requests[0][1]
        == (
            f"/internal/ai-tasks/"
            f"{TASK_ID}/completed"
        )
        and requests[0][2][
            "merge_commit_sha"
        ]
        == MERGE_SHA
        and requests[0][2][
            "ci_workflow_run_id"
        ]
        == WORKFLOW_RUN_ID,
    )

    check(
        "API client rejects invalid merge SHA",
        rejects(
            lambda: client.mark_completed(
                TASK_ID,
                CLAIM_TOKEN,
                commit_sha=HEAD_SHA,
                pr_number=PR_NUMBER,
                pr_url=PR_URL,
                test_summary="tests=0",
                changed_files_summary="x",
                ci_workflow_run_id=WORKFLOW_RUN_ID,
                review_summary="ok",
                merge_commit_sha="bad",
            )
        ),
    )

    check(
        "API client rejects invalid workflow ID",
        rejects(
            lambda: client.mark_completed(
                TASK_ID,
                CLAIM_TOKEN,
                commit_sha=HEAD_SHA,
                pr_number=PR_NUMBER,
                pr_url=PR_URL,
                test_summary="tests=0",
                changed_files_summary="x",
                ci_workflow_run_id=0,
                review_summary="ok",
                merge_commit_sha=MERGE_SHA,
            )
        ),
    )

    existing = {
        "task_id": TASK_ID,
        "status": "completed",
        "runner_id": "runner-1",
        "claim_token": CLAIM_TOKEN,
        "commit_sha": HEAD_SHA,
        "pr_number": PR_NUMBER,
        "pr_url": PR_URL,
        "test_summary": "tests=0",
        "changed_files_summary":
            "bot/example.py",
        "ci_workflow_run_id":
            WORKFLOW_RUN_ID,
        "review_summary":
            "No findings.",
        "merge_commit_sha":
            MERGE_SHA,
    }

    repo, _cursor, old_fetch_one = run_repo(
        [None, existing]
    )

    try:
        same = repo.mark_completed(
            task_id=TASK_ID,
            runner_id="runner-1",
            claim_token=CLAIM_TOKEN,
            commit_sha=HEAD_SHA,
            pr_number=PR_NUMBER,
            pr_url=PR_URL,
            test_summary="tests=0",
            changed_files_summary=(
                "bot/example.py"
            ),
            ci_workflow_run_id=(
                WORKFLOW_RUN_ID
            ),
            review_summary="No findings.",
            merge_commit_sha=MERGE_SHA,
        )
    finally:
        repository.fetch_one = old_fetch_one

    check(
        "completed retry with exact metadata is idempotent",
        same == existing,
    )

    different = dict(existing)
    different["merge_commit_sha"] = (
        "c" * 40
    )

    repo, _cursor, old_fetch_one = run_repo(
        [None, different]
    )

    try:
        mismatch = repo.mark_completed(
            task_id=TASK_ID,
            runner_id="runner-1",
            claim_token=CLAIM_TOKEN,
            commit_sha=HEAD_SHA,
            pr_number=PR_NUMBER,
            pr_url=PR_URL,
            test_summary="tests=0",
            changed_files_summary=(
                "bot/example.py"
            ),
            ci_workflow_run_id=(
                WORKFLOW_RUN_ID
            ),
            review_summary="No findings.",
            merge_commit_sha=MERGE_SHA,
        )
    finally:
        repository.fetch_one = old_fetch_one

    check(
        "completed retry with different metadata is rejected",
        mismatch is None,
    )

    print(
        "AI task Phase 2D control-plane checks passed"
    )


if __name__ == "__main__":
    main()