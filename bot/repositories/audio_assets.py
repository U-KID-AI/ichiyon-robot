from typing import Any, Dict, List, Optional

from bot import config
from bot.repositories.base import fetch_all, fetch_one


class AudioAssetRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def list_assets(
        self,
        guild_id: str,
        enabled: Optional[bool] = None,
        category: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [self.bot_id, guild_id]
        where = ["bot_id = %s", "guild_id = %s"]
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)
        if category is not None:
            where.append("category = %s")
            params.append(category)
        sql = """
            SELECT *
            FROM audio_assets
            WHERE {where}
            ORDER BY enabled DESC, category ASC, display_name ASC, id ASC
        """.format(where=" AND ".join(where))
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def list_categories(self, guild_id: str, enabled: Optional[bool] = True) -> List[str]:
        params: List[Any] = [self.bot_id, guild_id]
        where = ["bot_id = %s", "guild_id = %s"]
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT COALESCE(NULLIF(category, ''), '未分類') AS category_label
                FROM audio_assets
                WHERE {where}
                ORDER BY category_label ASC
                """.format(where=" AND ".join(where)),
                params,
            )
            return [str(row["category_label"]) for row in fetch_all(cursor)]

    def get_asset(self, guild_id: str, asset_id: int, enabled: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        params: List[Any] = [self.bot_id, guild_id, asset_id]
        where = ["bot_id = %s", "guild_id = %s", "id = %s"]
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM audio_assets WHERE {0}".format(" AND ".join(where)),
                params,
            )
            return fetch_one(cursor)

    def create_asset(self, guild_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audio_assets (
                    bot_id, guild_id, display_name, description, category, storage_path,
                    original_filename, mime_type, duration_ms, default_volume, enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    self.bot_id,
                    guild_id,
                    values["display_name"],
                    values.get("description", ""),
                    values.get("category", ""),
                    values["storage_path"],
                    values.get("original_filename", ""),
                    values.get("mime_type", ""),
                    values.get("duration_ms"),
                    values.get("default_volume", 50),
                    values.get("enabled", True),
                ),
            )
            return fetch_one(cursor)

    def update_asset(self, guild_id: str, asset_id: int, values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE audio_assets
                SET display_name = %s,
                    description = %s,
                    category = %s,
                    storage_path = COALESCE(%s, storage_path),
                    original_filename = COALESCE(%s, original_filename),
                    mime_type = COALESCE(%s, mime_type),
                    duration_ms = COALESCE(%s, duration_ms),
                    default_volume = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    values["display_name"],
                    values.get("description", ""),
                    values.get("category", ""),
                    values.get("storage_path"),
                    values.get("original_filename"),
                    values.get("mime_type"),
                    values.get("duration_ms"),
                    values.get("default_volume", 50),
                    values.get("enabled", True),
                    self.bot_id,
                    guild_id,
                    asset_id,
                ),
            )
            return fetch_one(cursor)

    def set_enabled(self, guild_id: str, asset_id: int, enabled: bool) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE audio_assets
                SET enabled = %s, updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, self.bot_id, guild_id, asset_id),
            )
            return fetch_one(cursor)
