from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


class MentionLimitedEffectRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_entries(
        self,
        guild_id: str,
        query: Optional[str] = None,
        enabled: Optional[bool] = None,
        effect_tag_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["l.guild_id = %s"]
        if query:
            like_query = "%{0}%".format(query)
            where.append(
                """
                (
                    l.discord_user_id ILIKE %s
                    OR COALESCE(l.display_name, '') ILIKE %s
                    OR COALESCE(l.description, '') ILIKE %s
                    OR t.name ILIKE %s
                )
                """
            )
            params.extend([like_query, like_query, like_query, like_query])
        if enabled is not None:
            where.append("l.enabled = %s")
            params.append(enabled)
        if effect_tag_id is not None:
            where.append("l.effect_tag_id = %s")
            params.append(effect_tag_id)

        sql = """
            SELECT
                l.*,
                t.name AS effect_tag_name,
                t.color AS effect_tag_color,
                t.effect_type AS effect_type,
                t.enabled AS effect_tag_enabled,
                t.admin_only AS effect_tag_admin_only
            FROM mention_limited_effects l
            JOIN special_effect_tags t ON t.id = l.effect_tag_id
            WHERE {where}
            ORDER BY l.enabled DESC, COALESCE(l.display_name, l.discord_user_id), t.name
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def get_by_id(self, guild_id: str, entry_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    l.*,
                    t.name AS effect_tag_name,
                    t.color AS effect_tag_color,
                    t.effect_type AS effect_type,
                    t.enabled AS effect_tag_enabled,
                    t.admin_only AS effect_tag_admin_only
                FROM mention_limited_effects l
                JOIN special_effect_tags t ON t.id = l.effect_tag_id
                WHERE l.guild_id = %s AND l.id = %s
                """,
                (guild_id, entry_id),
            )
            return fetch_one(cursor)

    def create_entry(
        self,
        guild_id: str,
        discord_user_id: str,
        display_name: str,
        effect_tag_id: int,
        description: str,
        enabled: bool,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mention_limited_effects (
                    guild_id,
                    discord_user_id,
                    display_name,
                    effect_tag_id,
                    description,
                    enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (guild_id, discord_user_id, effect_tag_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    description = EXCLUDED.description,
                    enabled = EXCLUDED.enabled,
                    updated_at = NOW()
                RETURNING *
                """,
                (guild_id, discord_user_id, display_name, effect_tag_id, description, enabled),
            )
            return fetch_one(cursor)

    def update_entry(
        self,
        guild_id: str,
        entry_id: int,
        discord_user_id: str,
        display_name: str,
        effect_tag_id: int,
        description: str,
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mention_limited_effects
                SET discord_user_id = %s,
                    display_name = %s,
                    effect_tag_id = %s,
                    description = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (discord_user_id, display_name, effect_tag_id, description, enabled, guild_id, entry_id),
            )
            return fetch_one(cursor)

    def toggle_enabled(self, guild_id: str, entry_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mention_limited_effects
                SET enabled = NOT enabled,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (guild_id, entry_id),
            )
            return fetch_one(cursor)

    def delete_entry(self, guild_id: str, entry_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM mention_limited_effects
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, entry_id),
            )
            return cursor.rowcount > 0

    def list_effects_for_user(
        self,
        guild_id: str,
        discord_user_id: str,
        enabled: Optional[bool] = True,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, discord_user_id]
        where = ["l.guild_id = %s", "l.discord_user_id = %s"]
        if enabled is not None:
            where.append("l.enabled = %s")
            where.append("t.enabled = %s")
            params.extend([enabled, enabled])

        sql = """
            SELECT
                l.id AS limited_effect_id,
                l.discord_user_id,
                l.display_name AS limited_display_name,
                l.description AS limited_description,
                l.enabled AS limited_enabled,
                t.*
            FROM mention_limited_effects l
            JOIN special_effect_tags t ON t.id = l.effect_tag_id
            WHERE {where}
            ORDER BY t.priority DESC, t.name ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)
