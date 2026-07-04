from datetime import date
from typing import Any, Dict, Optional

from bot.repositories.base import fetch_one


class DeckSearchSettingsRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def get(self, bot_id: str, guild_id: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM deck_search_settings
                WHERE bot_id = %s AND guild_id = %s
                """,
                (bot_id, guild_id),
            )
            return fetch_one(cursor)

    def upsert(
        self,
        bot_id: str,
        guild_id: str,
        fetch_since_date: Optional[date],
        max_lookback_days: int,
        updated_by: Optional[str],
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO deck_search_settings (
                    bot_id,
                    guild_id,
                    fetch_since_date,
                    max_lookback_days,
                    updated_by
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (bot_id, guild_id) DO UPDATE
                SET fetch_since_date = EXCLUDED.fetch_since_date,
                    max_lookback_days = EXCLUDED.max_lookback_days,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    bot_id,
                    guild_id,
                    fetch_since_date,
                    max_lookback_days,
                    updated_by,
                ),
            )
            return fetch_one(cursor)

    def clear_fetch_since_date(
        self,
        bot_id: str,
        guild_id: str,
        updated_by: Optional[str],
        max_lookback_days: int = 30,
    ) -> Dict[str, Any]:
        current = self.get(bot_id, guild_id)
        days = int(current.get("max_lookback_days") or max_lookback_days) if current else max_lookback_days
        return self.upsert(bot_id, guild_id, None, days, updated_by)
