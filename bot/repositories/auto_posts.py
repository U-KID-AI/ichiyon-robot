import json
from typing import Any, Dict, List, Optional

from bot import config
from bot.repositories.base import fetch_all, fetch_one


class AutoPostRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def list_posts(
        self,
        guild_id: str,
        query: Optional[str] = None,
        enabled: Optional[bool] = None,
        has_image: Optional[bool] = None,
        channel_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params = [self.bot_id, guild_id]
        where = ["bot_id = %s", "guild_id = %s"]

        if query:
            like_query = "%{0}%".format(query)
            where.append("(name ILIKE %s OR COALESCE(body, '') ILIKE %s)")
            params.extend([like_query, like_query])

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        if has_image is True:
            where.append("COALESCE(image_path, '') <> ''")
        elif has_image is False:
            where.append("COALESCE(image_path, '') = ''")

        if channel_id:
            where.append("channel_id = %s")
            params.append(channel_id)

        sql = """
            SELECT *
            FROM auto_posts
            WHERE {where}
            ORDER BY enabled DESC, name ASC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def get_by_id(self, guild_id: str, post_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM auto_posts
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, post_id),
            )
            return fetch_one(cursor)

    def list_enabled_posts(self) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM auto_posts
                WHERE bot_id = %s AND enabled = TRUE
                ORDER BY guild_id ASC, id ASC
                """,
                (self.bot_id,),
            )
            return fetch_all(cursor)

    def create_post(
        self,
        guild_id: str,
        name: str,
        body: Optional[str],
        image_path: Optional[str],
        channel_id: str,
        schedule_type: str,
        schedule_value: str,
        repeat_rule: Optional[str],
        enabled: bool,
        content_type: str = "static",
        content_config_json: str = "{}",
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO auto_posts (
                    bot_id,
                    guild_id,
                    name,
                    body,
                    image_path,
                    channel_id,
                    schedule_type,
                    schedule_value,
                    repeat_rule,
                    enabled,
                    content_type,
                    content_config_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    self.bot_id,
                    guild_id,
                    name,
                    body,
                    image_path,
                    channel_id,
                    schedule_type,
                    schedule_value,
                    repeat_rule,
                    enabled,
                    content_type,
                    content_config_json,
                ),
            )
            return fetch_one(cursor)

    def update_post(
        self,
        guild_id: str,
        post_id: int,
        name: str,
        body: Optional[str],
        image_path: Optional[str],
        channel_id: str,
        schedule_type: str,
        schedule_value: str,
        repeat_rule: Optional[str],
        enabled: bool,
        content_type: str = "static",
        content_config_json: str = "{}",
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE auto_posts
                SET name = %s,
                    body = %s,
                    image_path = %s,
                    channel_id = %s,
                    schedule_type = %s,
                    schedule_value = %s,
                    repeat_rule = %s,
                    enabled = %s,
                    content_type = %s,
                    content_config_json = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    name,
                    body,
                    image_path,
                    channel_id,
                    schedule_type,
                    schedule_value,
                    repeat_rule,
                    enabled,
                    content_type,
                    content_config_json,
                    self.bot_id,
                    guild_id,
                    post_id,
                ),
            )
            return fetch_one(cursor)

    def set_enabled(self, guild_id: str, post_id: int, enabled: bool) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE auto_posts
                SET enabled = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, self.bot_id, guild_id, post_id),
            )
            return fetch_one(cursor)

    def bulk_set_enabled(self, guild_id: str, post_ids: List[int], enabled: bool) -> int:
        if not post_ids:
            return 0
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE auto_posts
                SET enabled = %s,
                    updated_at = NOW()
                WHERE bot_id = %s
                  AND guild_id = %s
                  AND id = ANY(%s)
                """,
                (enabled, self.bot_id, guild_id, post_ids),
            )
            return cursor.rowcount

    def toggle_enabled(self, guild_id: str, post_id: int) -> Optional[Dict[str, Any]]:
        post = self.get_by_id(guild_id, post_id)
        if post is None:
            return None
        return self.set_enabled(guild_id, post_id, not bool(post["enabled"]))

    def copy_post(self, guild_id: str, post_id: int) -> Optional[Dict[str, Any]]:
        source = self.get_by_id(guild_id, post_id)
        if source is None:
            return None
        source_name = str(source.get("name") or "自動投稿").strip()
        schedule_value = source.get("schedule_value") or "{}"
        if not isinstance(schedule_value, str):
            schedule_value = json.dumps(schedule_value, ensure_ascii=False, sort_keys=True)
        return self.create_post(
            guild_id,
            "{0} コピー".format(source_name),
            source.get("body") or None,
            source.get("image_path") or None,
            str(source.get("channel_id") or ""),
            str(source.get("schedule_type") or "yearly"),
            schedule_value,
            source.get("repeat_rule") or None,
            False,
            str(source.get("content_type") or "static"),
            str(source.get("content_config_json") or "{}"),
        )

    def delete_post(self, guild_id: str, post_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM auto_posts
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, post_id),
            )
            return cursor.rowcount > 0

    def was_delivered(self, post_id: int, due_key: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM auto_post_delivery_history
                WHERE auto_post_id = %s AND due_key = %s
                  AND bot_id = %s
                """,
                (post_id, due_key, self.bot_id),
            )
            return cursor.fetchone() is not None

    def record_delivery(
        self,
        guild_id: str,
        post_id: int,
        due_key: str,
        channel_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO auto_post_delivery_history (
                    guild_id,
                    bot_id,
                    auto_post_id,
                    due_key,
                    delivered_at,
                    channel_id
                )
                VALUES (%s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (auto_post_id, due_key) DO NOTHING
                RETURNING *
                """,
                (guild_id, self.bot_id, post_id, due_key, channel_id),
            )
            return fetch_one(cursor)

    def update_last_posted_at(self, guild_id: str, post_id: int) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE auto_posts
                SET last_posted_at = NOW(),
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, post_id),
            )
