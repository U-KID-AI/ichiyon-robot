from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


class FeatureFlagRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_flags(self, guild_id: str) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM feature_flags
                WHERE guild_id = %s
                ORDER BY feature_key ASC
                """,
                (guild_id,),
            )
            return fetch_all(cursor)

    def get_flag(self, guild_id: str, feature_key: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM feature_flags
                WHERE guild_id = %s AND feature_key = %s
                """,
                (guild_id, feature_key),
            )
            return fetch_one(cursor)

    def is_enabled(self, guild_id: str, feature_key: str, default: bool = False) -> bool:
        flag = self.get_flag(guild_id, feature_key)
        if flag is None:
            return default
        return bool(flag["enabled"])

    def set_flag(
        self,
        guild_id: str,
        feature_key: str,
        enabled: bool,
        updated_by_discord_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO feature_flags (
                    guild_id,
                    feature_key,
                    enabled,
                    updated_by_discord_user_id
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (guild_id, feature_key) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    updated_by_discord_user_id = EXCLUDED.updated_by_discord_user_id,
                    updated_at = NOW()
                RETURNING *
                """,
                (guild_id, feature_key, enabled, updated_by_discord_user_id),
            )
            return fetch_one(cursor)
