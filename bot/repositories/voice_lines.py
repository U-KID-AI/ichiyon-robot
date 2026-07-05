from typing import Any, Dict, Optional

from bot.repositories.base import fetch_one


DEFAULT_REVIVE_LINE = "まずは女子供から殺す"


def resolve_voice_line(
    row: Optional[Dict[str, Any]],
    line_key: str,
    default_value: str = "",
) -> Optional[str]:
    if row is not None and row.get("enabled") is False:
        return None
    if row is not None:
        value = row.get(line_key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default_value


class VoiceLineRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def get(self, bot_id: str, guild_id: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM bot_voice_lines
                WHERE bot_id = %s
                  AND guild_id = %s
                """,
                (bot_id, guild_id),
            )
            return fetch_one(cursor)

    def upsert(
        self,
        bot_id: str,
        guild_id: str,
        join_line: str,
        revive_line: str,
        enabled: bool,
        updated_by_discord_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bot_voice_lines (
                    bot_id,
                    guild_id,
                    join_line,
                    revive_line,
                    enabled,
                    updated_by_discord_user_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (bot_id, guild_id) DO UPDATE
                SET join_line = EXCLUDED.join_line,
                    revive_line = EXCLUDED.revive_line,
                    enabled = EXCLUDED.enabled,
                    updated_by_discord_user_id = EXCLUDED.updated_by_discord_user_id,
                    updated_at = NOW()
                RETURNING *
                """,
                (bot_id, guild_id, join_line, revive_line, enabled, updated_by_discord_user_id),
            )
            return fetch_one(cursor)

    def set_enabled(
        self,
        bot_id: str,
        guild_id: str,
        enabled: bool,
        updated_by_discord_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        existing = self.get(bot_id, guild_id)
        if existing is None:
            return self.upsert(
                bot_id,
                guild_id,
                "",
                "",
                enabled,
                updated_by_discord_user_id,
            )
        return self.upsert(
            bot_id,
            guild_id,
            str(existing.get("join_line") or ""),
            str(existing.get("revive_line") or ""),
            enabled,
            updated_by_discord_user_id,
        )

    def toggle_enabled(
        self,
        bot_id: str,
        guild_id: str,
        updated_by_discord_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        existing = self.get(bot_id, guild_id)
        current = True if existing is None else bool(existing.get("enabled"))
        return self.set_enabled(bot_id, guild_id, not current, updated_by_discord_user_id)
