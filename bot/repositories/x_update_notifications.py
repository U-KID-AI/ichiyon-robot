from datetime import datetime
from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


class XUpdateWatchRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_watches(
        self,
        bot_id: str,
        guild_id: str,
        query: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [bot_id, guild_id]
        where = ["bot_id = %s", "guild_id = %s"]
        if query:
            like_query = "%{0}%".format(query)
            where.append("(x_username ILIKE %s OR COALESCE(display_name, '') ILIKE %s)")
            params.extend([like_query, like_query])
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM x_update_watches
                WHERE {where}
                ORDER BY enabled DESC, x_username ASC, id ASC
                """.format(where=" AND ".join(where)),
                params,
            )
            return fetch_all(cursor)

    def list_due_enabled_watches(self, bot_id: str, now: datetime) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM x_update_watches
                WHERE bot_id = %s
                  AND enabled = TRUE
                  AND (
                    last_checked_at IS NULL
                    OR last_checked_at <= %s - (check_interval_seconds * INTERVAL '1 second')
                  )
                ORDER BY COALESCE(last_checked_at, TIMESTAMPTZ 'epoch') ASC, id ASC
                """,
                (bot_id, now),
            )
            return fetch_all(cursor)

    def get_by_id(self, bot_id: str, guild_id: str, watch_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM x_update_watches
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (bot_id, guild_id, watch_id),
            )
            return fetch_one(cursor)

    def create_watch(
        self,
        bot_id: str,
        guild_id: str,
        channel_id: str,
        x_username: str,
        x_user_id: Optional[str],
        display_name: Optional[str],
        enabled: bool,
        include_replies: bool,
        include_reposts: bool,
        include_quotes: bool,
        check_interval_seconds: int,
        post_template: Optional[str],
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO x_update_watches (
                    bot_id,
                    guild_id,
                    channel_id,
                    x_username,
                    x_user_id,
                    display_name,
                    enabled,
                    include_replies,
                    include_reposts,
                    include_quotes,
                    check_interval_seconds,
                    post_template
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    bot_id,
                    guild_id,
                    channel_id,
                    x_username,
                    x_user_id,
                    display_name,
                    enabled,
                    include_replies,
                    include_reposts,
                    include_quotes,
                    check_interval_seconds,
                    post_template,
                ),
            )
            return fetch_one(cursor)

    def update_watch(
        self,
        bot_id: str,
        guild_id: str,
        watch_id: int,
        channel_id: str,
        x_username: str,
        x_user_id: Optional[str],
        display_name: Optional[str],
        enabled: bool,
        include_replies: bool,
        include_reposts: bool,
        include_quotes: bool,
        check_interval_seconds: int,
        post_template: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE x_update_watches
                SET channel_id = %s,
                    x_username = %s,
                    x_user_id = %s,
                    display_name = %s,
                    enabled = %s,
                    include_replies = %s,
                    include_reposts = %s,
                    include_quotes = %s,
                    check_interval_seconds = %s,
                    post_template = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    channel_id,
                    x_username,
                    x_user_id,
                    display_name,
                    enabled,
                    include_replies,
                    include_reposts,
                    include_quotes,
                    check_interval_seconds,
                    post_template,
                    bot_id,
                    guild_id,
                    watch_id,
                ),
            )
            return fetch_one(cursor)

    def toggle_enabled(self, bot_id: str, guild_id: str, watch_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE x_update_watches
                SET enabled = NOT enabled,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (bot_id, guild_id, watch_id),
            )
            return fetch_one(cursor)

    def delete_watch(self, bot_id: str, guild_id: str, watch_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM x_update_watches
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (bot_id, guild_id, watch_id),
            )
            return cursor.rowcount > 0

    def update_user_identity(
        self,
        watch_id: int,
        x_user_id: str,
        display_name: Optional[str],
        x_username: Optional[str] = None,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE x_update_watches
                SET x_user_id = %s,
                    display_name = COALESCE(%s, display_name),
                    x_username = COALESCE(%s, x_username),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (x_user_id, display_name, x_username, watch_id),
            )

    def mark_checked_success(
        self,
        watch_id: int,
        last_seen_post_id: Optional[str],
        last_posted_post_id: Optional[str],
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE x_update_watches
                SET last_seen_post_id = COALESCE(%s, last_seen_post_id),
                    last_posted_post_id = COALESCE(%s, last_posted_post_id),
                    last_checked_at = NOW(),
                    last_success_at = NOW(),
                    last_error = NULL,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (last_seen_post_id, last_posted_post_id, watch_id),
            )

    def mark_checked_error(self, watch_id: int, error: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE x_update_watches
                SET last_checked_at = NOW(),
                    last_error = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (error[:500], watch_id),
            )

    def record_history(
        self,
        watch_id: int,
        post_id: str,
        post_url: str,
        post_text: Optional[str],
        posted_channel_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO x_update_post_history (
                    watch_id,
                    post_id,
                    post_url,
                    post_text,
                    posted_channel_id
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (watch_id, post_id) DO NOTHING
                RETURNING *
                """,
                (watch_id, post_id, post_url, post_text, posted_channel_id),
            )
            return fetch_one(cursor)

    def mark_history_posted(self, history_id: int, message_id: Optional[str]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE x_update_post_history
                SET posted_message_id = %s,
                    posted_at = NOW()
                WHERE id = %s
                """,
                (message_id, history_id),
            )
