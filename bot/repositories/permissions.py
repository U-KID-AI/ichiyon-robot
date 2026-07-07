from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


ROLE_LEVELS = {
    "viewer": 1,
    "editor": 2,
    "guild_admin": 3,
    "global_admin": 4,
}


def role_allows(role: Optional[str], required_role: str) -> bool:
    return ROLE_LEVELS.get(role or "", 0) >= ROLE_LEVELS.get(required_role, 0)


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
        if not admin_user:
            return False
        if admin_user.get("enabled") is False:
            return False
        return bool(admin_user.get("role") == "global_admin")

    def can_login_admin(self, discord_user_id: str) -> bool:
        admin_user = self.get_admin_user(discord_user_id)
        if not admin_user:
            return False
        if admin_user.get("enabled") is False:
            return False
        return True

    def can_manage_users(self, discord_user_id: str) -> bool:
        if self.has_global_admin(discord_user_id):
            return True
        admin_user = self.get_admin_user(discord_user_id)
        if not admin_user:
            return False
        if admin_user.get("enabled") is False:
            return False
        return bool(admin_user.get("can_manage_users"))

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

    def list_manageable_bots(self, discord_user_id: str) -> List[Dict[str, Any]]:
        if self.has_global_admin(discord_user_id):
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT b.*, 'global_admin' AS role
                    FROM bot_instances b
                    WHERE b.enabled = TRUE
                    ORDER BY b.display_name ASC, b.bot_id ASC
                    """
                )
                return fetch_all(cursor)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT
                    b.*,
                    p.role
                FROM bot_permissions p
                JOIN bot_instances b ON b.bot_id = p.bot_id
                WHERE p.discord_user_id = %s
                  AND b.enabled = TRUE
                ORDER BY b.display_name ASC, b.bot_id ASC
                """,
                (discord_user_id,),
            )
            rows = fetch_all(cursor)
        return rows

    def can_access_bot(self, bot_id: str, discord_user_id: str) -> bool:
        if self.has_global_admin(discord_user_id):
            return True
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM bot_permissions
                WHERE bot_id = %s
                  AND discord_user_id = %s
                LIMIT 1
                """,
                (bot_id, discord_user_id),
            )
            if cursor.fetchone() is not None:
                return True
        return False

    def list_configured_guilds_for_bot(self, bot_id: str, role: str) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    g.guild_id,
                    g.name,
                    g.icon_url,
                    g.enabled,
                    %s AS role
                FROM bot_guilds bg
                JOIN guilds g ON g.guild_id = bg.guild_id
                WHERE bg.bot_id = %s
                  AND bg.enabled = TRUE
                ORDER BY g.name ASC, g.guild_id ASC
                """,
                (role, bot_id),
            )
            return fetch_all(cursor)

    def has_configured_guilds_for_bot(self, bot_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM bot_guilds
                WHERE bot_id = %s
                LIMIT 1
                """,
                (bot_id,),
            )
            return cursor.fetchone() is not None

    def list_manageable_guilds_for_bot(self, bot_id: str, discord_user_id: str) -> List[Dict[str, Any]]:
        if self.has_global_admin(discord_user_id):
            configured_rows = self.list_configured_guilds_for_bot(bot_id, "global_admin")
            if configured_rows:
                return configured_rows
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
                FROM bot_permissions p
                JOIN guilds g ON g.guild_id = p.guild_id
                WHERE p.discord_user_id = %s
                  AND p.bot_id = %s
                  AND p.guild_id IS NOT NULL
                  AND (
                      NOT EXISTS (
                          SELECT 1
                          FROM bot_guilds bg0
                          WHERE bg0.bot_id = %s
                      )
                      OR EXISTS (
                          SELECT 1
                          FROM bot_guilds bg
                          WHERE bg.bot_id = %s
                            AND bg.guild_id = p.guild_id
                            AND bg.enabled = TRUE
                      )
                  )
                ORDER BY g.name ASC, g.guild_id ASC
                """,
                (discord_user_id, bot_id, bot_id, bot_id),
            )
            rows = fetch_all(cursor)
        if rows:
            return rows

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT role
                FROM bot_permissions
                WHERE discord_user_id = %s
                  AND bot_id = %s
                  AND guild_id IS NULL
                ORDER BY role DESC
                LIMIT 1
                """,
                (discord_user_id, bot_id),
            )
            bot_level = fetch_one(cursor)

        if bot_level and role_allows(bot_level.get("role"), "viewer"):
            configured_rows = self.list_configured_guilds_for_bot(bot_id, bot_level.get("role") or "viewer")
            if configured_rows:
                return configured_rows
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        g.guild_id,
                        g.name,
                        g.icon_url,
                        g.enabled,
                        %s AS role
                    FROM guilds g
                    ORDER BY g.name ASC, g.guild_id ASC
                    """,
                    (bot_level.get("role") or "viewer",),
                )
                return fetch_all(cursor)

        return []

    def can_access_bot_guild(self, bot_id: str, guild_id: str, discord_user_id: str) -> bool:
        if self.has_global_admin(discord_user_id):
            if self.has_configured_guilds_for_bot(bot_id):
                for guild in self.list_configured_guilds_for_bot(bot_id, "global_admin"):
                    if str(guild.get("guild_id")) == str(guild_id):
                        return True
                return False
            return True
        for guild in self.list_manageable_guilds_for_bot(bot_id, discord_user_id):
            if str(guild.get("guild_id")) == str(guild_id):
                return True
        return False

    def list_admin_users(self) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM admin_users
                ORDER BY COALESCE(display_name, discord_user_id) ASC, discord_user_id ASC
                """
            )
            return fetch_all(cursor)

    def upsert_admin_user(
        self,
        discord_user_id: str,
        display_name: str,
        role: str,
        enabled: bool,
        can_manage_users: bool,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO admin_users (
                    discord_user_id,
                    display_name,
                    role,
                    enabled,
                    can_manage_users
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (discord_user_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    role = EXCLUDED.role,
                    enabled = EXCLUDED.enabled,
                    can_manage_users = EXCLUDED.can_manage_users,
                    updated_at = NOW()
                RETURNING *
                """,
                (discord_user_id, display_name, role, enabled, can_manage_users),
            )
            return fetch_one(cursor)

    def set_last_login(self, discord_user_id: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE admin_users
                SET last_login_at = NOW(),
                    updated_at = NOW()
                WHERE discord_user_id = %s
                """,
                (discord_user_id,),
            )

    def list_bot_permissions(self, discord_user_id: str) -> List[Dict[str, Any]]:
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

    def replace_bot_permissions(self, discord_user_id: str, permissions: List[Dict[str, Any]]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM bot_permissions
                WHERE discord_user_id = %s
                """,
                (discord_user_id,),
            )
            for permission in permissions:
                cursor.execute(
                    """
                    INSERT INTO bot_permissions (
                        bot_id,
                        discord_user_id,
                        guild_id,
                        role
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (bot_id, discord_user_id, guild_id) DO UPDATE
                    SET role = EXCLUDED.role
                    """,
                    (
                        permission["bot_id"],
                        discord_user_id,
                        permission.get("guild_id"),
                        permission["role"],
                    ),
                )
