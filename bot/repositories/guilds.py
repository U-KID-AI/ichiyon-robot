from typing import Any, Dict, Iterable, Optional

from bot.repositories.base import fetch_one


class GuildRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def get(self, guild_id: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM guilds
                WHERE guild_id = %s
                """,
                (guild_id,),
            )
            return fetch_one(cursor)

    def upsert(
        self,
        guild_id: str,
        name: str,
        icon_url: Optional[str] = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO guilds (guild_id, name, icon_url, enabled)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (guild_id) DO UPDATE
                SET name = EXCLUDED.name,
                    icon_url = EXCLUDED.icon_url,
                    enabled = EXCLUDED.enabled,
                    updated_at = NOW()
                RETURNING *
                """,
                (guild_id, name, icon_url, enabled),
            )
            return fetch_one(cursor)

    def upsert_from_discord_guild(self, guild) -> Dict[str, Any]:
        guild_id = str(guild.id)
        name = str(getattr(guild, "name", guild_id))
        icon_url = self._get_icon_url(guild)
        return self.upsert(guild_id, name, icon_url)

    def initialize_feature_flags(
        self,
        guild_id: str,
        feature_keys: Iterable[str],
        enabled: bool = False,
    ) -> int:
        inserted_or_updated = 0
        with self.connection.cursor() as cursor:
            for feature_key in feature_keys:
                cursor.execute(
                    """
                    INSERT INTO feature_flags (guild_id, feature_key, enabled)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (guild_id, feature_key) DO NOTHING
                    """,
                    (guild_id, feature_key, enabled),
                )
                inserted_or_updated += cursor.rowcount

        return inserted_or_updated

    def ensure_from_discord_guild(
        self,
        guild,
        feature_keys: Optional[Iterable[str]] = None,
        feature_enabled: bool = False,
    ) -> Dict[str, Any]:
        guild_row = self.upsert_from_discord_guild(guild)
        if feature_keys is not None:
            self.initialize_feature_flags(
                guild_row["guild_id"],
                feature_keys,
                feature_enabled,
            )
        return guild_row

    def _get_icon_url(self, guild) -> Optional[str]:
        icon = getattr(guild, "icon", None)
        if icon is None:
            return None

        url = getattr(icon, "url", None)
        if url is None:
            return None

        return str(url)
