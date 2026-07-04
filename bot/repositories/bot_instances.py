from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


class BotInstanceRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_enabled(self) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM bot_instances
                WHERE enabled = TRUE
                ORDER BY bot_id ASC
                """
            )
            return fetch_all(cursor)

    def get(self, bot_id: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM bot_instances
                WHERE bot_id = %s
                """,
                (bot_id,),
            )
            return fetch_one(cursor)


class BotPermissionRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_user_permissions(self, discord_user_id: str) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM bot_permissions
                WHERE discord_user_id = %s
                ORDER BY bot_id ASC, guild_id ASC
                """,
                (discord_user_id,),
            )
            return fetch_all(cursor)
