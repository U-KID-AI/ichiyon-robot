from typing import Any, Dict, List, Optional, Set

from bot.repositories.base import fetch_all, fetch_one


ROLE_LEVELS = {
    "viewer": 1,
    "editor": 2,
    "guild_admin": 3,
    "global_admin": 4,
}


def role_allows(role: Optional[str], required_role: str) -> bool:
    return ROLE_LEVELS.get(role or "", 0) >= ROLE_LEVELS.get(required_role, 0)


def enabled_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "t", "yes", "on")


class PermissionRepository:
    def __init__(self, connection) -> None:
        self.connection = connection
        self._table_columns: Dict[str, Set[str]] = {}

    def _columns(self, table_name: str) -> Set[str]:
        cached = self._table_columns.get(table_name)
        if cached is not None:
            return cached
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                """,
                (table_name,),
            )
            columns = {str(row[0]) for row in cursor.fetchall()}
        self._table_columns[table_name] = columns
        return columns

    def _has_table(self, table_name: str) -> bool:
        return bool(self._columns(table_name))

    def _with_legacy_admin_defaults(self, row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        columns = self._columns("admin_users")
        if "enabled" not in columns and "enabled" not in row:
            row["enabled"] = True
        if "display_name" not in columns and "display_name" not in row:
            row["display_name"] = ""
        if "can_manage_users" not in columns and "can_manage_users" not in row:
            row["can_manage_users"] = False
        if "last_login_at" not in columns and "last_login_at" not in row:
            row["last_login_at"] = None
        return row

    def get_admin_user(self, discord_user_id: str) -> Optional[Dict[str, Any]]:
        discord_user_id = str(discord_user_id or "").strip()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM admin_users
                WHERE discord_user_id = %s
                   OR TRIM(discord_user_id) = %s
                ORDER BY CASE WHEN discord_user_id = %s THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (discord_user_id, discord_user_id, discord_user_id),
            )
            return self._with_legacy_admin_defaults(fetch_one(cursor))

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
        if not enabled_value(admin_user.get("enabled")):
            return False
        return bool(admin_user.get("role") == "global_admin")

    def get_admin_login_status(self, discord_user_id: str) -> Dict[str, Any]:
        normalized_user_id = str(discord_user_id or "").strip()
        if not normalized_user_id:
            return {
                "can_login": False,
                "reason": "empty_user_id",
                "discord_user_id": normalized_user_id,
                "registered": False,
                "enabled": False,
                "role": "",
                "bot_permission_count": 0,
            }
        columns = self._columns("admin_users")
        selected = ["au.discord_user_id", "au.role"]
        for column_name in ("display_name", "enabled", "can_manage_users"):
            if column_name in columns:
                selected.append("au.{0}".format(column_name))
        if self._has_table("bot_permissions"):
            selected.append(
                """
                (
                    SELECT COUNT(*)
                    FROM bot_permissions bp
                    WHERE bp.discord_user_id = au.discord_user_id
                       OR TRIM(bp.discord_user_id) = TRIM(au.discord_user_id)
                ) AS bot_permission_count
                """.strip()
            )
        else:
            selected.append("0 AS bot_permission_count")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    {selected}
                FROM admin_users au
                WHERE au.discord_user_id = %s
                   OR TRIM(au.discord_user_id) = %s
                ORDER BY CASE WHEN au.discord_user_id = %s THEN 0 ELSE 1 END
                LIMIT 1
                """.format(selected=",\n                    ".join(selected)),
                (normalized_user_id, normalized_user_id, normalized_user_id),
            )
            admin_user = self._with_legacy_admin_defaults(fetch_one(cursor))
        if not admin_user:
            return {
                "can_login": False,
                "reason": "not_registered",
                "discord_user_id": normalized_user_id,
                "registered": False,
                "enabled": False,
                "role": "",
                "bot_permission_count": 0,
            }
        is_enabled = enabled_value(admin_user.get("enabled"))
        return {
            "can_login": is_enabled,
            "reason": "ok" if is_enabled else "disabled",
            "discord_user_id": str(admin_user.get("discord_user_id") or normalized_user_id),
            "registered": True,
            "enabled": is_enabled,
            "role": admin_user.get("role") or "",
            "display_name": admin_user.get("display_name") or "",
            "bot_permission_count": int(admin_user.get("bot_permission_count") or 0),
        }

    def can_login_admin(self, discord_user_id: str) -> bool:
        return bool(self.get_admin_login_status(discord_user_id).get("can_login"))

    def can_manage_users(self, discord_user_id: str) -> bool:
        admin_user = self.get_admin_user(discord_user_id)
        if not admin_user:
            return False
        if not enabled_value(admin_user.get("enabled")):
            return False
        return bool(admin_user.get("can_manage_users") or admin_user.get("role") == "global_admin")

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
        if not self._has_table("bot_permissions") or not self._has_table("bot_instances"):
            return []
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
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
        bots_by_id: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            bot_id = str(row.get("bot_id") or "")
            if not bot_id:
                continue
            current = bots_by_id.get(bot_id)
            if current is None or ROLE_LEVELS.get(row.get("role") or "", 0) > ROLE_LEVELS.get(current.get("role") or "", 0):
                bots_by_id[bot_id] = row
        return sorted(
            bots_by_id.values(),
            key=lambda item: (
                str(item.get("display_name") or ""),
                str(item.get("bot_id") or ""),
            ),
        )

    def can_access_bot(self, bot_id: str, discord_user_id: str) -> bool:
        if not self._has_table("bot_permissions"):
            admin_user = self.get_admin_user(discord_user_id)
            return bool(admin_user and enabled_value(admin_user.get("enabled")))
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
        if not self._has_table("bot_guilds"):
            return []
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
        if not self._has_table("bot_guilds"):
            return False
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
        if not self._has_table("bot_permissions"):
            return self.list_manageable_guilds(discord_user_id)
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
        if not self._has_table("bot_permissions"):
            return self.can_access_guild(guild_id, discord_user_id)
        for guild in self.list_manageable_guilds_for_bot(bot_id, discord_user_id):
            if str(guild.get("guild_id")) == str(guild_id):
                return True
        return False

    def list_admin_users(self) -> List[Dict[str, Any]]:
        order_by = "discord_user_id ASC"
        if "display_name" in self._columns("admin_users"):
            order_by = "COALESCE(display_name, discord_user_id) ASC, discord_user_id ASC"
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM admin_users
                ORDER BY {order_by}
                """.format(order_by=order_by)
            )
            return [self._with_legacy_admin_defaults(row) for row in fetch_all(cursor)]

    def upsert_admin_user(
        self,
        discord_user_id: str,
        display_name: str,
        role: str,
        enabled: bool,
        can_manage_users: bool,
    ) -> Dict[str, Any]:
        columns = self._columns("admin_users")
        with self.connection.cursor() as cursor:
            if {"display_name", "enabled", "can_manage_users"} <= columns:
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
            else:
                cursor.execute(
                    """
                    INSERT INTO admin_users (
                        discord_user_id,
                        role
                    )
                    VALUES (%s, %s)
                    ON CONFLICT (discord_user_id) DO UPDATE
                    SET role = EXCLUDED.role,
                        updated_at = NOW()
                    RETURNING *
                    """,
                    (discord_user_id, role),
                )
            return self._with_legacy_admin_defaults(fetch_one(cursor))

    def count_enabled_global_admins(self, exclude_discord_user_id: Optional[str] = None) -> int:
        enabled_clause = "AND enabled = TRUE" if "enabled" in self._columns("admin_users") else ""
        with self.connection.cursor() as cursor:
            if exclude_discord_user_id:
                normalized = str(exclude_discord_user_id or "").strip()
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM admin_users
                    WHERE role = 'global_admin'
                      {enabled_clause}
                      AND NOT (discord_user_id = %s OR TRIM(discord_user_id) = %s)
                    """.format(enabled_clause=enabled_clause),
                    (normalized, normalized),
                )
            else:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM admin_users
                    WHERE role = 'global_admin'
                      {enabled_clause}
                    """.format(enabled_clause=enabled_clause)
                )
            row = cursor.fetchone()
            return int(row[0] if row else 0)

    def admin_user_exists(self, discord_user_id: str) -> bool:
        return self.get_admin_user(discord_user_id) is not None

    def update_admin_user_id(self, old_discord_user_id: str, new_discord_user_id: str) -> None:
        old_id = str(old_discord_user_id or "").strip()
        new_id = str(new_discord_user_id or "").strip()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE admin_users
                SET discord_user_id = %s,
                    updated_at = NOW()
                WHERE discord_user_id = %s
                   OR TRIM(discord_user_id) = %s
                """,
                (new_id, old_id, old_id),
            )
            if self._has_table("bot_permissions"):
                cursor.execute(
                    """
                    UPDATE bot_permissions
                    SET discord_user_id = %s,
                        updated_at = NOW()
                    WHERE discord_user_id = %s
                       OR TRIM(discord_user_id) = %s
                    """,
                    (new_id, old_id, old_id),
                )

    def delete_admin_user_with_permissions(self, discord_user_id: str) -> None:
        normalized = str(discord_user_id or "").strip()
        with self.connection.cursor() as cursor:
            if self._has_table("bot_permissions"):
                cursor.execute(
                    """
                    DELETE FROM bot_permissions
                    WHERE discord_user_id = %s
                       OR TRIM(discord_user_id) = %s
                    """,
                    (normalized, normalized),
                )
            cursor.execute(
                """
                DELETE FROM admin_users
                WHERE discord_user_id = %s
                   OR TRIM(discord_user_id) = %s
                """,
                (normalized, normalized),
            )

    def set_last_login(self, discord_user_id: str) -> None:
        if "last_login_at" not in self._columns("admin_users"):
            return
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE admin_users
                SET last_login_at = NOW(),
                    updated_at = NOW()
                WHERE discord_user_id = %s
                   OR TRIM(discord_user_id) = %s
                """,
                (str(discord_user_id or "").strip(), str(discord_user_id or "").strip()),
            )

    def list_bot_permissions(self, discord_user_id: str) -> List[Dict[str, Any]]:
        if not self._has_table("bot_permissions"):
            return []
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
        if not self._has_table("bot_permissions"):
            if permissions:
                raise RuntimeError("bot_permissions table is not available")
            return
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
