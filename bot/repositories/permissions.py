from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


class PermissionRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def get_admin_user(self, discord_user_id: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM admin_users
                WHERE discord_user_id = %s
                """,
                (discord_user_id,),
            )
            return fetch_one(cursor)

    def list_guild_permissions(self, discord_user_id: str) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM guild_permissions
                WHERE discord_user_id = %s
                ORDER BY guild_id ASC
                """,
                (discord_user_id,),
            )
            return fetch_all(cursor)

    def get_guild_permission(
        self,
        guild_id: str,
        discord_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM guild_permissions
                WHERE guild_id = %s AND discord_user_id = %s
                """,
                (guild_id, discord_user_id),
            )
            return fetch_one(cursor)

    def has_global_admin(self, discord_user_id: str) -> bool:
        admin_user = self.get_admin_user(discord_user_id)
        return bool(admin_user and admin_user.get("role") == "global_admin")

    def list_manageable_guilds(self, discord_user_id: str) -> List[Dict[str, Any]]:
        if self.has_global_admin(discord_user_id):
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        g.guild_id,
                        g.name,
                        g.icon_url,
                        g.enabled,
                        'global_admin' AS role
                    FROM guilds g
                    ORDER BY g.name ASC, g.guild_id ASC
                    """
                )
                return fetch_all(cursor)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    g.guild_id,
                    g.name,
                    g.icon_url,
                    g.enabled,
                    p.role
                FROM guild_permissions p
                JOIN guilds g ON g.guild_id = p.guild_id
                WHERE p.discord_user_id = %s
                ORDER BY g.name ASC, g.guild_id ASC
                """,
                (discord_user_id,),
            )
            return fetch_all(cursor)

    def can_access_guild(self, guild_id: str, discord_user_id: str) -> bool:
        if self.has_global_admin(discord_user_id):
            return True
        return self.get_guild_permission(guild_id, discord_user_id) is not None
