from typing import Any, Dict, Optional

from bot import config
from bot.repositories.base import fetch_one


DEFAULT_MUSIC_VOLUME_PERCENT = 40
DEFAULT_FOREGROUND_VOLUME_PERCENT = 50


class MusicSettingsRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def get(self, guild_id: str) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM bot_music_settings
                WHERE bot_id = %s AND guild_id = %s
                """,
                (self.bot_id, guild_id),
            )
            row = fetch_one(cursor)
        if row is not None:
            return row
        return {
            "bot_id": self.bot_id,
            "guild_id": guild_id,
            "music_volume_percent": DEFAULT_MUSIC_VOLUME_PERCENT,
            "foreground_volume_percent": DEFAULT_FOREGROUND_VOLUME_PERCENT,
        }

    def upsert(
        self,
        guild_id: str,
        music_volume_percent: Optional[int] = None,
        foreground_volume_percent: Optional[int] = None,
    ) -> Dict[str, Any]:
        current = self.get(guild_id)
        music_volume = int(
            DEFAULT_MUSIC_VOLUME_PERCENT
            if music_volume_percent is None
            else music_volume_percent
        )
        foreground_volume = int(
            current.get("foreground_volume_percent")
            if foreground_volume_percent is None
            else foreground_volume_percent
        )
        if music_volume_percent is None:
            music_volume = int(current.get("music_volume_percent") or DEFAULT_MUSIC_VOLUME_PERCENT)
        if foreground_volume_percent is None:
            foreground_volume = int(current.get("foreground_volume_percent") or DEFAULT_FOREGROUND_VOLUME_PERCENT)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bot_music_settings (
                    bot_id,
                    guild_id,
                    music_volume_percent,
                    foreground_volume_percent
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (bot_id, guild_id) DO UPDATE
                SET music_volume_percent = EXCLUDED.music_volume_percent,
                    foreground_volume_percent = EXCLUDED.foreground_volume_percent,
                    updated_at = NOW()
                RETURNING *
                """,
                (self.bot_id, guild_id, music_volume, foreground_volume),
            )
            return fetch_one(cursor)
