from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


class AutoReactionRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_reactions(
        self,
        guild_id: str,
        query: Optional[str] = None,
        enabled: Optional[bool] = None,
        has_image: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

        if query:
            like_query = "%{0}%".format(query)
            where.append(
                "(trigger_text ILIKE %s OR COALESCE(response_text, '') ILIKE %s OR COALESCE(emoji_internal, '') ILIKE %s)"
            )
            params.extend([like_query, like_query, like_query])

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        if has_image is True:
            where.append("COALESCE(image_path, '') <> ''")
        elif has_image is False:
            where.append("COALESCE(image_path, '') = ''")

        sql = """
            SELECT *
            FROM reactions
            WHERE {where}
            ORDER BY priority DESC, LENGTH(trigger_text) DESC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def get_by_id(self, guild_id: str, reaction_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM reactions
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, reaction_id),
            )
            return fetch_one(cursor)

    def trigger_exists(
        self,
        guild_id: str,
        trigger_text: str,
        match_type: str,
        exclude_reaction_id: Optional[int] = None,
    ) -> bool:
        params = [guild_id, trigger_text, match_type]
        where = ["guild_id = %s", "trigger_text = %s", "match_type = %s"]
        if exclude_reaction_id is not None:
            where.append("id <> %s")
            params.append(exclude_reaction_id)

        sql = """
            SELECT 1
            FROM reactions
            WHERE {where}
            LIMIT 1
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone() is not None

    def create_reaction(
        self,
        guild_id: str,
        trigger_text: str,
        response_text: Optional[str],
        image_path: Optional[str],
        emoji_internal: Optional[str],
        match_type: str,
        priority: int,
        enabled: bool,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO reactions (
                    guild_id,
                    trigger_text,
                    response_text,
                    image_path,
                    emoji_internal,
                    match_type,
                    priority,
                    enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    guild_id,
                    trigger_text,
                    response_text,
                    image_path,
                    emoji_internal,
                    match_type,
                    priority,
                    enabled,
                ),
            )
            return fetch_one(cursor)

    def update_reaction(
        self,
        guild_id: str,
        reaction_id: int,
        trigger_text: str,
        response_text: Optional[str],
        image_path: Optional[str],
        emoji_internal: Optional[str],
        match_type: str,
        priority: int,
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reactions
                SET trigger_text = %s,
                    response_text = %s,
                    image_path = %s,
                    emoji_internal = %s,
                    match_type = %s,
                    priority = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    trigger_text,
                    response_text,
                    image_path,
                    emoji_internal,
                    match_type,
                    priority,
                    enabled,
                    guild_id,
                    reaction_id,
                ),
            )
            return fetch_one(cursor)

    def set_enabled(
        self,
        guild_id: str,
        reaction_id: int,
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reactions
                SET enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, guild_id, reaction_id),
            )
            return fetch_one(cursor)

    def bulk_set_enabled(self, guild_id: str, reaction_ids: List[int], enabled: bool) -> int:
        if not reaction_ids:
            return 0
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reactions
                SET enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s
                  AND id = ANY(%s)
                """,
                (enabled, guild_id, reaction_ids),
            )
            return cursor.rowcount

    def toggle_enabled(
        self,
        guild_id: str,
        reaction_id: int,
    ) -> Optional[Dict[str, Any]]:
        reaction = self.get_by_id(guild_id, reaction_id)
        if reaction is None:
            return None
        return self.set_enabled(guild_id, reaction_id, not bool(reaction["enabled"]))

    def copy_reaction(self, guild_id: str, reaction_id: int) -> Optional[Dict[str, Any]]:
        source = self.get_by_id(guild_id, reaction_id)
        if source is None:
            return None
        copied = self.create_reaction(
            guild_id,
            "{0} コピー".format(str(source.get("trigger_text") or "").strip()),
            source.get("response_text") or None,
            source.get("image_path") or None,
            source.get("emoji_internal") or None,
            str(source.get("match_type") or "contains"),
            int(source.get("priority") or 0),
            False,
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO special_effect_assignments (
                    guild_id,
                    special_effect_tag_id,
                    target_type,
                    target_id,
                    enabled
                )
                SELECT guild_id,
                       special_effect_tag_id,
                       target_type,
                       %s,
                       enabled
                FROM special_effect_assignments
                WHERE guild_id = %s
                  AND target_type = 'auto_reaction'
                  AND target_id = %s
                ON CONFLICT (special_effect_tag_id, target_type, target_id) DO NOTHING
                """,
                (copied["id"], guild_id, reaction_id),
            )
        return copied

    def delete_reaction(self, guild_id: str, reaction_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM special_effect_assignments
                WHERE guild_id = %s
                  AND target_type = 'auto_reaction'
                  AND target_id = %s
                """,
                (guild_id, reaction_id),
            )
            cursor.execute(
                """
                DELETE FROM reactions
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, reaction_id),
            )
            return cursor.rowcount > 0

    def find_trigger_matches(
        self,
        guild_id: str,
        content: str,
        enabled: Optional[bool] = True,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, content, content, content, content]
        where = [
            "guild_id = %s",
            "((match_type = 'exact' AND %s = trigger_text) "
            "OR (match_type = 'prefix' AND POSITION(trigger_text IN %s) = 1) "
            "OR (match_type = 'contains' AND POSITION(trigger_text IN %s) > 0) "
            "OR (match_type = 'regex' AND %s ~ trigger_text))",
        ]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        sql = """
            SELECT *
            FROM reactions
            WHERE {where}
            ORDER BY priority DESC, LENGTH(trigger_text) DESC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)
