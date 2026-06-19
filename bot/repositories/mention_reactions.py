from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one, normalize_reaction_kind


class MentionReactionRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_reactions(
        self,
        guild_id: str,
        enabled: Optional[bool] = None,
        reaction_kind: Optional[str] = None,
        include_system: bool = True,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        normalized_kind = normalize_reaction_kind(reaction_kind)
        if normalized_kind is not None:
            where.append("reaction_kind = %s")
            params.append(normalized_kind)

        if not include_system:
            where.append("is_system = FALSE")

        sql = """
            SELECT *
            FROM mention_reactions
            WHERE {where}
            ORDER BY sort_order ASC, LENGTH(keyword) DESC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def get_by_id(self, guild_id: str, reaction_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mention_reactions
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, reaction_id),
            )
            return fetch_one(cursor)

    def get_by_key(self, guild_id: str, reaction_key: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mention_reactions
                WHERE guild_id = %s AND reaction_key = %s
                """,
                (guild_id, reaction_key),
            )
            return fetch_one(cursor)

    def find_keyword_matches(
        self,
        guild_id: str,
        content: str,
        enabled: Optional[bool] = True,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, content, content, content]
        where = [
            "guild_id = %s",
            "((match_type = 'exact' AND %s = keyword) "
            "OR (match_type = 'contains' AND POSITION(keyword IN %s) > 0) "
            "OR (match_type = 'regex' AND %s ~ keyword))",
        ]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        sql = """
            SELECT *
            FROM mention_reactions
            WHERE {where}
            ORDER BY LENGTH(keyword) DESC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def search_by_keyword(
        self,
        guild_id: str,
        keyword: str,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, "%{0}%".format(keyword)]
        where = ["guild_id = %s", "keyword ILIKE %s"]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        sql = """
            SELECT *
            FROM mention_reactions
            WHERE {where}
            ORDER BY LENGTH(keyword) DESC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def list_choices(
        self,
        guild_id: str,
        mention_reaction_id: int,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, mention_reaction_id]
        where = ["guild_id = %s", "mention_reaction_id = %s"]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        sql = """
            SELECT *
            FROM mention_reaction_choices
            WHERE {where}
            ORDER BY sort_order ASC, id ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def get_choice(self, guild_id: str, choice_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mention_reaction_choices
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, choice_id),
            )
            return fetch_one(cursor)

    def list_search_handlers(
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
            FROM mention_search_handlers
            WHERE {where}
            ORDER BY handler_key ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)
