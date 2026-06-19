from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


class AutoReactionRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_reactions(
        self,
        guild_id: str,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

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

    def find_trigger_matches(
        self,
        guild_id: str,
        content: str,
        enabled: Optional[bool] = True,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, content, content, content]
        where = [
            "guild_id = %s",
            "((match_type = 'exact' AND %s = trigger_text) "
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
