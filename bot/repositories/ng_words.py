from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all


class NgWordRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_words(
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
            FROM ng_words
            WHERE {where}
            ORDER BY LENGTH(word) DESC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def find_matches(
        self,
        guild_id: str,
        content: str,
        enabled: Optional[bool] = True,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, content]
        where = ["guild_id = %s", "POSITION(word IN %s) > 0"]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        sql = """
            SELECT *
            FROM ng_words
            WHERE {where}
            ORDER BY LENGTH(word) DESC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)
