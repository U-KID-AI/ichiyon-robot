from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


class AutoPostRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_posts(
        self,
        guild_id: str,
        query: Optional[str] = None,
        enabled: Optional[bool] = None,
        has_image: Optional[bool] = None,
        channel_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

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
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, post_id),
            )
            return fetch_one(cursor)

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
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO auto_posts (
                    guild_id,
                    name,
                    body,
                    image_path,
                    channel_id,
                    schedule_type,
                    schedule_value,
                    repeat_rule,
                    enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    guild_id,
                    name,
                    body,
                    image_path,
                    channel_id,
                    schedule_type,
                    schedule_value,
                    repeat_rule,
                    enabled,
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
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
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
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, guild_id, post_id),
            )
            return fetch_one(cursor)

    def toggle_enabled(self, guild_id: str, post_id: int) -> Optional[Dict[str, Any]]:
        post = self.get_by_id(guild_id, post_id)
        if post is None:
            return None
        return self.set_enabled(guild_id, post_id, not bool(post["enabled"]))

    def delete_post(self, guild_id: str, post_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM auto_posts
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, post_id),
            )
            return cursor.rowcount > 0
