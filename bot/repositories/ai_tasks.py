import uuid
import re
from typing import Any, Dict, List, Optional

from bot import config
from bot.repositories.base import fetch_all, fetch_one


AI_TASK_STATUSES = (
    "queued",
    "running",
    "testing",
    "needs_human",
    "ready_for_review",
    "failed",
    "cancelled",
    "completed",
)
AI_TASK_STATUS_SET = frozenset(AI_TASK_STATUSES)
MAX_DESCRIPTION_LENGTH = 4000
MAX_PROGRESS_FIELD_LENGTH = 4000
RUNNER_LEASE_SECONDS = 180
RUNNER_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SHA1_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
PR_URL_PATTERN = re.compile(r"^https://github\.com/U-KID-AI/ichiyon-robot/pull/([1-9][0-9]*)$")
MAX_CURRENT_STEP_LENGTH = 500
MAX_TEST_SUMMARY_LENGTH = 8000
MAX_CHANGED_FILES_SUMMARY_LENGTH = 8000
MAX_REVIEW_SUMMARY_LENGTH = 2000
MAX_WORKFLOW_RUN_ID = 9_223_372_036_854_775_807


def is_valid_runner_id(value: str) -> bool:
    return isinstance(value, str) and RUNNER_ID_PATTERN.fullmatch(value) is not None


def is_valid_sha1(value: str) -> bool:
    return isinstance(value, str) and SHA1_PATTERN.fullmatch(value) is not None


def is_valid_pr_url(value: str, pr_number: int) -> bool:
    return isinstance(value, str) and PR_URL_PATTERN.fullmatch(value) is not None and value.endswith("/" + str(pr_number))


def validate_runner_id(value: str) -> None:
    if not is_valid_runner_id(value):
        raise ValueError("invalid runner_id")


def validate_sha1(value: str) -> None:
    if not is_valid_sha1(value):
        raise ValueError("invalid SHA-1")


def validate_pr_number(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("invalid PR number")


def validate_pr_url(value: str, pr_number: int) -> None:
    if not is_valid_pr_url(value, pr_number):
        raise ValueError("invalid PR URL")


def validate_workflow_run_id(value: int) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > MAX_WORKFLOW_RUN_ID
    ):
        raise ValueError("invalid workflow run ID")


class AITaskRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def create_task(
        self,
        *,
        task_id: uuid.UUID,
        guild_id: Optional[str],
        discord_channel_id: str,
        discord_message_id: str,
        requester_discord_user_id: str,
        description: str,
        branch_name: str,
        worktree_name: str,
    ) -> Dict[str, Any]:
        if not isinstance(task_id, uuid.UUID):
            raise ValueError("task_id must be a UUID")
        if not 1 <= len(description) <= MAX_DESCRIPTION_LENGTH:
            raise ValueError("description length is invalid")

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ai_tasks (
                    task_id, bot_id, guild_id, discord_channel_id,
                    discord_message_id, requester_discord_user_id,
                    description, status, branch_name, worktree_name
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s)
                RETURNING *
                """,
                (
                    task_id,
                    self.bot_id,
                    guild_id,
                    discord_channel_id,
                    discord_message_id,
                    requester_discord_user_id,
                    description,
                    branch_name,
                    worktree_name,
                ),
            )
            row = fetch_one(cursor)
        if row is None:
            raise RuntimeError("ai task insert returned no row")
        return row

    def get_task(self, task_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        if not isinstance(task_id, uuid.UUID):
            raise ValueError("task_id must be a UUID")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM ai_tasks
                WHERE task_id = %s AND bot_id = %s
                """,
                (task_id, self.bot_id),
            )
            return fetch_one(cursor)

    def list_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 20))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM ai_tasks
                WHERE bot_id = %s
                ORDER BY created_at DESC, task_id DESC
                LIMIT %s
                """,
                (self.bot_id, safe_limit),
            )
            return fetch_all(cursor)

    def update_status(
        self,
        *,
        task_id: uuid.UUID,
        status: str,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(task_id, uuid.UUID):
            raise ValueError("task_id must be a UUID")
        if status not in AI_TASK_STATUS_SET:
            raise ValueError("invalid ai task status")

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_tasks
                SET status = %s,
                    started_at = CASE
                        WHEN %s = 'running' AND started_at IS NULL THEN NOW()
                        ELSE started_at
                    END,
                    completed_at = CASE
                        WHEN %s IN ('failed', 'cancelled', 'completed') THEN COALESCE(completed_at, NOW())
                        ELSE completed_at
                    END,
                    updated_at = NOW()
                WHERE task_id = %s AND bot_id = %s
                RETURNING *
                """,
                (
                    status,
                    status,
                    status,
                    task_id,
                    self.bot_id,
                ),
            )
            return fetch_one(cursor)

    def update_progress(
        self,
        *,
        task_id: uuid.UUID,
        current_step: Optional[str] = None,
        progress_summary: Optional[str] = None,
        result_summary: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(task_id, uuid.UUID):
            raise ValueError("task_id must be a UUID")
        values = (current_step, progress_summary, result_summary, error_message)
        if any(value is not None and len(str(value)) > MAX_PROGRESS_FIELD_LENGTH for value in values):
            raise ValueError("progress field is too long")

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_tasks
                SET current_step = COALESCE(%s, current_step),
                    progress_summary = COALESCE(%s, progress_summary),
                    result_summary = COALESCE(%s, result_summary),
                    error_message = COALESCE(%s, error_message),
                    updated_at = NOW()
                WHERE task_id = %s AND bot_id = %s
                RETURNING *
                """,
                (
                    current_step,
                    progress_summary,
                    result_summary,
                    error_message,
                    task_id,
                    self.bot_id,
                ),
            )
            return fetch_one(cursor)

    def expire_stale_leases(self) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_tasks
                SET status = 'needs_human',
                    current_step = 'needs_human',
                    progress_summary = COALESCE(progress_summary, 'Runner lease expired; human inspection required.'),
                    updated_at = NOW()
                WHERE bot_id = %s
                  AND status IN ('running', 'testing')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < NOW()
                RETURNING task_id, status, runner_id, claimed_at, heartbeat_at, lease_expires_at
                """,
                (self.bot_id,),
            )
            return fetch_all(cursor)

    def claim_next_task(self, *, runner_id: str) -> Optional[Dict[str, Any]]:
        validate_runner_id(runner_id)
        claim_token = uuid.uuid4()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                WITH next_task AS (
                    SELECT task_id
                    FROM ai_tasks
                    WHERE bot_id = %s AND status = 'queued'
                    ORDER BY created_at ASC, task_id ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE ai_tasks AS task
                SET status = 'running',
                    runner_id = %s,
                    claim_token = %s,
                    claimed_at = NOW(),
                    heartbeat_at = NOW(),
                    lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                    attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                FROM next_task
                WHERE task.task_id = next_task.task_id
                RETURNING task.task_id, task.description, task.branch_name,
                          task.worktree_name, task.claim_token, task.lease_expires_at
                """,
                (self.bot_id, runner_id, claim_token, RUNNER_LEASE_SECONDS),
            )
            return fetch_one(cursor)

    def heartbeat(self, *, task_id: uuid.UUID, runner_id: str, claim_token: uuid.UUID) -> Optional[Dict[str, Any]]:
        validate_runner_id(runner_id)
        if not isinstance(task_id, uuid.UUID) or not isinstance(claim_token, uuid.UUID):
            raise ValueError("task_id and claim_token must be UUIDs")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_tasks
                SET heartbeat_at = NOW(),
                    lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                    updated_at = NOW()
                WHERE task_id = %s AND bot_id = %s AND runner_id = %s AND claim_token = %s
                  AND status IN ('running', 'testing')
                  AND lease_expires_at > NOW()
                RETURNING task_id, status, heartbeat_at, lease_expires_at
                """,
                (RUNNER_LEASE_SECONDS, task_id, self.bot_id, runner_id, claim_token),
            )
            return fetch_one(cursor)

    def mark_testing(self, *, task_id: uuid.UUID, runner_id: str, claim_token: uuid.UUID) -> Optional[Dict[str, Any]]:
        return self._transition_active_task(
            task_id=task_id, runner_id=runner_id, claim_token=claim_token,
            from_status="running", to_status="testing",
        )

    def update_runner_progress(
        self, *, task_id: uuid.UUID, runner_id: str, claim_token: uuid.UUID,
        current_step: Optional[str] = None, progress_summary: Optional[str] = None,
        base_commit_sha: Optional[str] = None, test_summary: Optional[str] = None,
        changed_files_summary: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(task_id, uuid.UUID) or not isinstance(claim_token, uuid.UUID):
            raise ValueError("task_id and claim_token must be UUIDs")
        validate_runner_id(runner_id)
        if current_step is not None and len(current_step) > MAX_CURRENT_STEP_LENGTH:
            raise ValueError("current_step is too long")
        if progress_summary is not None and len(progress_summary) > MAX_PROGRESS_FIELD_LENGTH:
            raise ValueError("progress_summary is too long")
        if test_summary is not None and len(test_summary) > MAX_TEST_SUMMARY_LENGTH:
            raise ValueError("test_summary is too long")
        if changed_files_summary is not None and len(changed_files_summary) > MAX_CHANGED_FILES_SUMMARY_LENGTH:
            raise ValueError("changed_files_summary is too long")
        if base_commit_sha is not None:
            validate_sha1(base_commit_sha)
        if all(value is None for value in (current_step, progress_summary, base_commit_sha, test_summary, changed_files_summary)):
            raise ValueError("at least one progress field is required")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_tasks
                SET current_step = COALESCE(%s, current_step),
                    progress_summary = COALESCE(%s, progress_summary),
                    base_commit_sha = COALESCE(%s, base_commit_sha),
                    test_summary = COALESCE(%s, test_summary),
                    changed_files_summary = COALESCE(%s, changed_files_summary),
                    updated_at = NOW()
                WHERE task_id = %s AND bot_id = %s AND runner_id = %s AND claim_token = %s
                  AND status IN ('running', 'testing')
                  AND lease_expires_at > NOW()
                RETURNING task_id, status, current_step, progress_summary,
                          base_commit_sha, test_summary, changed_files_summary
                """,
                (current_step, progress_summary, base_commit_sha, test_summary,
                 changed_files_summary, task_id, self.bot_id, runner_id, claim_token),
            )
            return fetch_one(cursor)

    def mark_failed(self, *, task_id: uuid.UUID, runner_id: str, claim_token: uuid.UUID, error_message: str) -> Optional[Dict[str, Any]]:
        return self._mark_terminal(
            task_id=task_id, runner_id=runner_id, claim_token=claim_token,
            status="failed", value=error_message, value_column="error_message",
        )

    def mark_needs_human(self, *, task_id: uuid.UUID, runner_id: str, claim_token: uuid.UUID, reason: str) -> Optional[Dict[str, Any]]:
        return self._mark_terminal(
            task_id=task_id, runner_id=runner_id, claim_token=claim_token,
            status="needs_human", value=reason, value_column="progress_summary",
        )

    def mark_ready_for_review(
        self, *, task_id: uuid.UUID, runner_id: str, claim_token: uuid.UUID,
        commit_sha: str, pr_number: int, pr_url: str,
        test_summary: str, changed_files_summary: str,
    ) -> Optional[Dict[str, Any]]:
        validate_runner_id(runner_id)
        if not isinstance(task_id, uuid.UUID) or not isinstance(claim_token, uuid.UUID):
            raise ValueError("task_id and claim_token must be UUIDs")
        validate_sha1(commit_sha)
        validate_pr_number(pr_number)
        validate_pr_url(pr_url, pr_number)
        if len(test_summary) > MAX_TEST_SUMMARY_LENGTH or len(changed_files_summary) > MAX_CHANGED_FILES_SUMMARY_LENGTH:
            raise ValueError("summary is too long")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_tasks
                SET status = 'ready_for_review', commit_sha = %s, pr_number = %s,
                    pr_url = %s, test_summary = %s, changed_files_summary = %s,
                    updated_at = NOW()
                WHERE task_id = %s AND bot_id = %s AND runner_id = %s AND claim_token = %s
                  AND status = 'testing' AND lease_expires_at > NOW()
                RETURNING task_id, status, commit_sha, pr_number, pr_url
                """,
                (commit_sha, pr_number, pr_url, test_summary, changed_files_summary,
                 task_id, self.bot_id, runner_id, claim_token),
            )
            row = fetch_one(cursor)
            if row is not None:
                return row
            cursor.execute(
                """
                SELECT task_id, status, runner_id, claim_token, commit_sha,
                       pr_number, pr_url, test_summary, changed_files_summary
                FROM ai_tasks
                WHERE task_id = %s AND bot_id = %s AND runner_id = %s AND claim_token = %s
                """,
                (task_id, self.bot_id, runner_id, claim_token),
            )
            existing = fetch_one(cursor)
            if not existing or existing.get("status") != "ready_for_review":
                return None
            if (
                existing.get("commit_sha") == commit_sha
                and existing.get("pr_number") == pr_number
                and existing.get("pr_url") == pr_url
                and existing.get("test_summary") == test_summary
                and existing.get("changed_files_summary") == changed_files_summary
            ):
                return existing
            return None

    def mark_completed(
        self,
        *,
        task_id: uuid.UUID,
        runner_id: str,
        claim_token: uuid.UUID,
        commit_sha: str,
        pr_number: int,
        pr_url: str,
        test_summary: str,
        changed_files_summary: str,
        ci_workflow_run_id: int,
        review_summary: str,
        merge_commit_sha: str,
    ) -> Optional[Dict[str, Any]]:
        validate_runner_id(runner_id)

        if (
            not isinstance(task_id, uuid.UUID)
            or not isinstance(claim_token, uuid.UUID)
        ):
            raise ValueError(
                "task_id and claim_token must be UUIDs"
            )

        validate_sha1(commit_sha)
        validate_pr_number(pr_number)
        validate_pr_url(pr_url, pr_number)
        validate_workflow_run_id(
            ci_workflow_run_id
        )
        validate_sha1(merge_commit_sha)

        if (
            len(test_summary)
            > MAX_TEST_SUMMARY_LENGTH
            or len(changed_files_summary)
            > MAX_CHANGED_FILES_SUMMARY_LENGTH
            or not isinstance(review_summary, str)
            or not 1 <= len(review_summary)
            <= MAX_REVIEW_SUMMARY_LENGTH
        ):
            raise ValueError(
                "completion summary is invalid"
            )

        result_summary = (
            "Auto review approved. CI run "
            + str(ci_workflow_run_id)
            + ". Merge commit "
            + merge_commit_sha
            + ". Review: "
            + review_summary
        )[:MAX_PROGRESS_FIELD_LENGTH]

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_tasks
                SET status = 'completed',
                    commit_sha = %s,
                    pr_number = %s,
                    pr_url = %s,
                    test_summary = %s,
                    changed_files_summary = %s,
                    ci_workflow_run_id = %s,
                    review_summary = %s,
                    merge_commit_sha = %s,
                    current_step = 'completed',
                    progress_summary =
                        'Automatically reviewed and merged.',
                    result_summary = %s,
                    completed_at =
                        COALESCE(completed_at, NOW()),
                    updated_at = NOW()
                WHERE task_id = %s
                  AND bot_id = %s
                  AND runner_id = %s
                  AND claim_token = %s
                  AND status = 'testing'
                  AND lease_expires_at > NOW()
                RETURNING
                    task_id,
                    status,
                    commit_sha,
                    pr_number,
                    pr_url,
                    ci_workflow_run_id,
                    review_summary,
                    merge_commit_sha
                """,
                (
                    commit_sha,
                    pr_number,
                    pr_url,
                    test_summary,
                    changed_files_summary,
                    ci_workflow_run_id,
                    review_summary,
                    merge_commit_sha,
                    result_summary,
                    task_id,
                    self.bot_id,
                    runner_id,
                    claim_token,
                ),
            )

            row = fetch_one(cursor)

            if row is not None:
                return row

            cursor.execute(
                """
                SELECT
                    task_id,
                    status,
                    runner_id,
                    claim_token,
                    commit_sha,
                    pr_number,
                    pr_url,
                    test_summary,
                    changed_files_summary,
                    ci_workflow_run_id,
                    review_summary,
                    merge_commit_sha
                FROM ai_tasks
                WHERE task_id = %s
                  AND bot_id = %s
                  AND runner_id = %s
                  AND claim_token = %s
                """,
                (
                    task_id,
                    self.bot_id,
                    runner_id,
                    claim_token,
                ),
            )

            existing = fetch_one(cursor)

            if (
                not existing
                or existing.get("status")
                != "completed"
            ):
                return None

            if (
                existing.get("commit_sha")
                == commit_sha
                and existing.get("pr_number")
                == pr_number
                and existing.get("pr_url")
                == pr_url
                and existing.get("test_summary")
                == test_summary
                and existing.get(
                    "changed_files_summary"
                )
                == changed_files_summary
                and existing.get(
                    "ci_workflow_run_id"
                )
                == ci_workflow_run_id
                and existing.get(
                    "review_summary"
                )
                == review_summary
                and existing.get(
                    "merge_commit_sha"
                )
                == merge_commit_sha
            ):
                return existing

            return None

    def _transition_active_task(self, *, task_id: uuid.UUID, runner_id: str, claim_token: uuid.UUID,
                                from_status: str, to_status: str) -> Optional[Dict[str, Any]]:
        validate_runner_id(runner_id)
        if from_status != "running" or to_status != "testing":
            raise ValueError("unsupported transition")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_tasks
                SET status = 'testing', updated_at = NOW()
                WHERE task_id = %s AND bot_id = %s AND runner_id = %s AND claim_token = %s
                  AND status = 'running' AND lease_expires_at > NOW()
                RETURNING task_id, status
                """,
                (task_id, self.bot_id, runner_id, claim_token),
            )
            row = fetch_one(cursor)
            if row is not None:
                return row
            cursor.execute(
                """
                SELECT task_id, status, runner_id, claim_token
                FROM ai_tasks
                WHERE task_id = %s AND bot_id = %s AND runner_id = %s AND claim_token = %s
                """,
                (task_id, self.bot_id, runner_id, claim_token),
            )
            existing = fetch_one(cursor)
            if existing and existing.get("status") == "testing":
                return existing
            return None

    def _mark_terminal(self, *, task_id: uuid.UUID, runner_id: str, claim_token: uuid.UUID,
                       status: str, value: str, value_column: str) -> Optional[Dict[str, Any]]:
        validate_runner_id(runner_id)
        if status not in ("failed", "needs_human") or value_column not in ("error_message", "progress_summary"):
            raise ValueError("unsupported terminal transition")
        if not isinstance(value, str) or not 1 <= len(value) <= MAX_PROGRESS_FIELD_LENGTH:
            raise ValueError("transition reason is invalid")
        # The two fixed statements keep the column choice in source code, never in request data.
        if status == "failed":
            sql = """
                UPDATE ai_tasks SET status = 'failed', error_message = %s,
                    completed_at = COALESCE(completed_at, NOW()), updated_at = NOW()
                WHERE task_id = %s AND bot_id = %s AND runner_id = %s AND claim_token = %s
                  AND status IN ('running', 'testing') AND lease_expires_at > NOW()
                RETURNING task_id, status, error_message, completed_at
            """
        else:
            sql = """
                UPDATE ai_tasks SET status = 'needs_human', current_step = 'needs_human',
                    progress_summary = %s, updated_at = NOW()
                WHERE task_id = %s AND bot_id = %s AND runner_id = %s AND claim_token = %s
                  AND status IN ('running', 'testing') AND lease_expires_at > NOW()
                RETURNING task_id, status, progress_summary
            """
        with self.connection.cursor() as cursor:
            cursor.execute(sql, (value, task_id, self.bot_id, runner_id, claim_token))
            row = fetch_one(cursor)
            if row is not None:
                return row
            cursor.execute(
                """
                SELECT task_id, status, error_message, progress_summary
                FROM ai_tasks
                WHERE task_id = %s AND bot_id = %s AND runner_id = %s AND claim_token = %s
                """,
                (task_id, self.bot_id, runner_id, claim_token),
            )
            existing = fetch_one(cursor)
            if (
                existing
                and existing.get("status") == status
                and (
                    (status == "failed" and existing.get("error_message") == value)
                    or (status == "needs_human" and existing.get("progress_summary") == value)
                )
            ):
                return existing
            return None
