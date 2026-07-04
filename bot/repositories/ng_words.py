from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


class NgWordRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_words(
        self,
        guild_id: str,
        query: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

        if query:
            where.append("word ILIKE %s")
            params.append("%{0}%".format(query))

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

    def get_by_id(self, guild_id: str, word_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM ng_words
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, word_id),
            )
            return fetch_one(cursor)

    def word_exists(
        self,
        guild_id: str,
        word: str,
        exclude_word_id: Optional[int] = None,
    ) -> bool:
        params = [guild_id, word]
        where = ["guild_id = %s", "word = %s"]
        if exclude_word_id is not None:
            where.append("id <> %s")
            params.append(exclude_word_id)

        sql = """
            SELECT 1
            FROM ng_words
            WHERE {where}
            LIMIT 1
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone() is not None

    def create_word(
        self,
        guild_id: str,
        word: str,
        enabled: bool,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ng_words (guild_id, word, enabled)
                VALUES (%s, %s, %s)
                RETURNING *
                """,
                (guild_id, word, enabled),
            )
            return fetch_one(cursor)

    def update_word(
        self,
        guild_id: str,
        word_id: int,
        word: str,
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ng_words
                SET word = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (word, enabled, guild_id, word_id),
            )
            return fetch_one(cursor)

    def set_enabled(
        self,
        guild_id: str,
        word_id: int,
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ng_words
                SET enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, guild_id, word_id),
            )
            return fetch_one(cursor)

    def bulk_set_enabled(self, guild_id: str, word_ids: List[int], enabled: bool) -> int:
        if not word_ids:
            return 0
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ng_words
                SET enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s
                  AND id = ANY(%s)
                """,
                (enabled, guild_id, word_ids),
            )
            return cursor.rowcount

    def toggle_enabled(
        self,
        guild_id: str,
        word_id: int,
    ) -> Optional[Dict[str, Any]]:
        word = self.get_by_id(guild_id, word_id)
        if word is None:
            return None
        return self.set_enabled(guild_id, word_id, not bool(word["enabled"]))

    def copy_word(self, guild_id: str, word_id: int) -> Optional[Dict[str, Any]]:
        source = self.get_by_id(guild_id, word_id)
        if source is None:
            return None
        base_word = "{0} コピー".format(str(source.get("word") or "").strip())
        copied_word = base_word
        suffix = 2
        while self.word_exists(guild_id, copied_word):
            copied_word = "{0} {1}".format(base_word, suffix)
            suffix += 1
        copied = self.create_word(guild_id, copied_word, False)
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
                  AND target_type = 'ng_word'
                  AND target_id = %s
                ON CONFLICT (special_effect_tag_id, target_type, target_id) DO NOTHING
                """,
                (copied["id"], guild_id, word_id),
            )
        return copied

    def delete_word(self, guild_id: str, word_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM special_effect_assignments
                WHERE guild_id = %s
                  AND target_type = 'ng_word'
                  AND target_id = %s
                """,
                (guild_id, word_id),
            )
            cursor.execute(
                """
                DELETE FROM ng_words
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, word_id),
            )
            return cursor.rowcount > 0

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
