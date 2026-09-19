import uuid
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
