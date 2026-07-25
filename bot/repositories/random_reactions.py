from decimal import Decimal
from typing import Any, Dict, Optional

from bot import config
from bot.repositories.base import fetch_one


DEFAULT_RANDOM_REACTION_EMOJI = "🍞"
DEFAULT_RANDOM_REACTION_PROBABILITY_PERCENT = 1.0
DEFAULT_RANDOM_REACTION_COOLDOWN_SECONDS = 600


def default_random_reaction_settings(bot_id: str, guild_id: str) -> Dict[str, Any]:
    return {
        "bot_id": bot_id,
        "guild_id": guild_id,
        "enabled": False,
        "emoji": DEFAULT_RANDOM_REACTION_EMOJI,
        "probability_percent": DEFAULT_RANDOM_REACTION_PROBABILITY_PERCENT,
        "cooldown_seconds": DEFAULT_RANDOM_REACTION_COOLDOWN_SECONDS,
        "target_channel_ids": "",
        "excluded_channel_ids": "",
        "updated_by_discord_user_id": None,
    }


def row_to_settings(row: Optional[Dict[str, Any]], bot_id: str, guild_id: str) -> Dict[str, Any]:
    settings = default_random_reaction_settings(bot_id, guild_id)
    if row is None:
        return settings
    settings.update(row)
    probability = settings.get("probability_percent")
    if isinstance(probability, Decimal):
        settings["probability_percent"] = float(probability)
    return settings


class RandomReactionRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def get(self, guild_id: str) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM random_reaction_settings
                WHERE bot_id = %s AND guild_id = %s
                """,
                (self.bot_id, guild_id),
            )
            return row_to_settings(fetch_one(cursor), self.bot_id, guild_id)

    def upsert(
        self,
        guild_id: str,
        enabled: bool,
        emoji: str,
        probability_percent: float,
        cooldown_seconds: int,
        target_channel_ids: str,
        excluded_channel_ids: str,
        updated_by_discord_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO random_reaction_settings (
                    bot_id,
                    guild_id,
                    enabled,
                    emoji,
                    probability_percent,
                    cooldown_seconds,
                    target_channel_ids,
                    excluded_channel_ids,
                    updated_by_discord_user_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bot_id, guild_id) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    emoji = EXCLUDED.emoji,
                    probability_percent = EXCLUDED.probability_percent,
                    cooldown_seconds = EXCLUDED.cooldown_seconds,
                    target_channel_ids = EXCLUDED.target_channel_ids,
                    excluded_channel_ids = EXCLUDED.excluded_channel_ids,
                    updated_by_discord_user_id = EXCLUDED.updated_by_discord_user_id,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    self.bot_id,
                    guild_id,
                    enabled,
                    emoji,
                    probability_percent,
                    cooldown_seconds,
                    target_channel_ids,
                    excluded_channel_ids,
                    updated_by_discord_user_id,
                ),
            )
            return row_to_settings(fetch_one(cursor), self.bot_id, guild_id)

    def set_enabled(
        self,
        guild_id: str,
        enabled: bool,
        updated_by_discord_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = self.get(guild_id)
        probability = current.get("probability_percent")
        cooldown = current.get("cooldown_seconds")
        return self.upsert(
            guild_id,
            enabled,
            str(current.get("emoji") or DEFAULT_RANDOM_REACTION_EMOJI),
            float(DEFAULT_RANDOM_REACTION_PROBABILITY_PERCENT if probability is None else probability),
            int(DEFAULT_RANDOM_REACTION_COOLDOWN_SECONDS if cooldown is None else cooldown),
            str(current.get("target_channel_ids") or ""),
            str(current.get("excluded_channel_ids") or ""),
            updated_by_discord_user_id,
        )

    def toggle_enabled(
        self,
        guild_id: str,
        updated_by_discord_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = self.get(guild_id)
        return self.set_enabled(guild_id, not bool(current.get("enabled")), updated_by_discord_user_id)
