from typing import Any, Dict, Optional

from bot.repositories.base import fetch_one, json_dumps


class HoroscopeRepository:
    def __init__(self, connection, bot_id: str = "ichiyon") -> None:
        self.connection = connection
        self.bot_id = bot_id

    def get_settings(self, guild_id: str) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mezamashi_horoscope_settings
                WHERE bot_id = %s AND guild_id = %s
                """,
                (self.bot_id, guild_id),
            )
            row = fetch_one(cursor)
        if row is None:
            return {
                "bot_id": self.bot_id,
                "guild_id": guild_id,
                "enabled": True,
                "ranking_command_enabled": True,
                "zodiac_command_enabled": True,
                "auto_post_enabled": False,
                "auto_post_channel_id": "",
                "auto_post_time": "07:10",
            }
        return row

    def upsert_settings(
        self,
        guild_id: str,
        *,
        enabled: bool,
        ranking_command_enabled: bool,
        zodiac_command_enabled: bool,
        auto_post_enabled: bool,
        auto_post_channel_id: str,
        auto_post_time: str,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mezamashi_horoscope_settings (
                    bot_id,
                    guild_id,
                    enabled,
                    ranking_command_enabled,
                    zodiac_command_enabled,
                    auto_post_enabled,
                    auto_post_channel_id,
                    auto_post_time
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bot_id, guild_id) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    ranking_command_enabled = EXCLUDED.ranking_command_enabled,
                    zodiac_command_enabled = EXCLUDED.zodiac_command_enabled,
                    auto_post_enabled = EXCLUDED.auto_post_enabled,
                    auto_post_channel_id = EXCLUDED.auto_post_channel_id,
                    auto_post_time = EXCLUDED.auto_post_time,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    self.bot_id,
                    guild_id,
                    enabled,
                    ranking_command_enabled,
                    zodiac_command_enabled,
                    auto_post_enabled,
                    auto_post_channel_id,
                    auto_post_time,
                ),
            )
            return fetch_one(cursor)

    def get_cache_by_date(self, target_date: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mezamashi_horoscope_cache
                WHERE target_date = %s
                """,
                (target_date,),
            )
            return fetch_one(cursor)

    def get_latest_cache(self) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mezamashi_horoscope_cache
                ORDER BY target_date DESC, fetched_at DESC
                LIMIT 1
                """
            )
            return fetch_one(cursor)

    def upsert_cache(self, target_date: str, payload: Dict[str, Any], source_url: str) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mezamashi_horoscope_cache (
                    target_date,
                    payload_json,
                    source_url,
                    fetched_at
                )
                VALUES (%s, %s::JSONB, %s, NOW())
                ON CONFLICT (target_date) DO UPDATE
                SET payload_json = EXCLUDED.payload_json,
                    source_url = EXCLUDED.source_url,
                    fetched_at = NOW(),
                    updated_at = NOW()
                RETURNING *
                """,
                (target_date, json_dumps(payload), source_url),
            )
            return fetch_one(cursor)
