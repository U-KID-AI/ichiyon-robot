import sys
from pathlib import Path
from typing import Any, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin.bots import parse_permission_value
from admin.role_labels import role_description, role_label
from bot.repositories.permissions import PermissionRepository, enabled_value, role_allows


AUTH_PATH = PROJECT_ROOT / "admin" / "auth.py"
ADMIN_BOTS_PATH = PROJECT_ROOT / "admin" / "bots.py"
ADMIN_USERS_TEMPLATE_PATH = PROJECT_ROOT / "admin" / "templates" / "admin_users.html"
ADMIN_USER_FORM_TEMPLATE_PATH = PROJECT_ROOT / "admin" / "templates" / "admin_user_form.html"
PERMISSIONS_PATH = PROJECT_ROOT / "bot" / "repositories" / "permissions.py"


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


class _Column:
    def __init__(self, name: str) -> None:
        self.name = name


class _Cursor:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection
        self.description: List[_Column] = []
        self.rows: List[tuple] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.connection.executed.append(sql)
        self.description, self.rows = self.connection.respond(sql, params or ())

    def fetchall(self) -> List[tuple]:
        return list(self.rows)

    def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False


class _Connection:
    def __init__(self, columns: dict) -> None:
        self.columns = columns
        self.executed: List[str] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def respond(self, sql: str, params: Any) -> tuple:
        if "information_schema.columns" in sql:
            names = self.columns.get(params[0], [])
            return ([_Column("column_name")], [(name,) for name in names])
        if "COUNT(*)" in sql and "admin_users" in sql:
            return ([_Column("count")], [(1,)])
        if "FROM admin_users" in sql:
            if "au.enabled" in sql:
                return (
                    [_Column("discord_user_id"), _Column("role"), _Column("enabled"), _Column("bot_permission_count")],
                    [("9001", "global_admin", False, 0)],
                )
            if "au.discord_user_id" in sql:
                return (
                    [_Column("discord_user_id"), _Column("role"), _Column("bot_permission_count")],
                    [("9001", "global_admin", 0)],
                )
            return (
                [_Column("discord_user_id"), _Column("role"), _Column("created_at"), _Column("updated_at")],
                [("9001", "global_admin", None, None)],
            )
        if "FROM guilds" in sql:
            return (
                [_Column("guild_id"), _Column("name"), _Column("icon_url"), _Column("enabled"), _Column("role")],
                [("1", "lab", None, True, "global_admin")],
            )
        return ([_Column("ok")], [])


def check_legacy_admin_schema(results: List[Tuple[str, bool, Any]]) -> None:
    legacy = _Connection(
        {
            "admin_users": ["discord_user_id", "role", "created_at", "updated_at"],
        }
    )
    repository = PermissionRepository(legacy)
    status_map = repository.get_admin_login_status("9001")
    login_sql = "\n".join(sql for sql in legacy.executed if "FROM admin_users au" in sql)
    record(
        results,
        "pre-027 admin login does not require enabled column",
        status_map.get("can_login") is True
        and status_map.get("enabled") is True
        and "au.enabled" not in login_sql
        and "bot_permissions" not in login_sql,
        status_map.get("reason"),
    )
    record(
        results,
        "pre-027 global admin can list guilds without bot_permissions",
        repository.list_manageable_guilds_for_bot("ichiyon", "9001")[0]["guild_id"] == "1"
        and not any("FROM bot_permissions" in sql for sql in legacy.executed),
        "legacy guild list",
    )

    current = _Connection(
        {
            "admin_users": ["discord_user_id", "role", "display_name", "enabled", "can_manage_users", "last_login_at"],
            "bot_permissions": ["id", "bot_id", "discord_user_id", "guild_id", "role"],
            "bot_instances": ["bot_id"],
            "bot_guilds": ["bot_id", "guild_id"],
        }
    )
    current_repository = PermissionRepository(current)
    disabled = current_repository.get_admin_login_status("9001")
    current_sql = "\n".join(sql for sql in current.executed if "FROM admin_users au" in sql)
    record(
        results,
        "current schema still honors disabled admin users",
        disabled.get("can_login") is False and "au.enabled" in current_sql and "bot_permissions" in current_sql,
        disabled.get("reason"),
    )


def main() -> int:
    results: List[Tuple[str, bool, Any]] = []

    record(
        results,
        "bot level permission parses",
        parse_permission_value("irsia:guild_admin") == {"bot_id": "irsia", "guild_id": None, "role": "guild_admin"},
        parse_permission_value("irsia:guild_admin"),
    )
    record(
        results,
        "guild level permission parses",
        parse_permission_value("irsia:928619302213533736:editor")
        == {"bot_id": "irsia", "guild_id": "928619302213533736", "role": "editor"},
        parse_permission_value("irsia:928619302213533736:editor"),
    )
    record(
        results,
        "invalid role is ignored",
        parse_permission_value("irsia:owner") is None,
        parse_permission_value("irsia:owner"),
    )
    record(
        results,
        "role hierarchy allows guild admin to edit",
        role_allows("guild_admin", "editor"),
        "guild_admin >= editor",
    )
    record(
        results,
        "role hierarchy blocks viewer from edit",
        not role_allows("viewer", "editor"),
        "viewer < editor",
    )
    record(
        results,
        "enabled parser accepts postgres-style values",
        enabled_value(True) and enabled_value("t") and enabled_value("true") and enabled_value(1) and not enabled_value(False),
        "enabled parser",
    )
    record(
        results,
        "role labels are localized",
        role_label("viewer") == "閲覧のみ"
        and role_label("editor") == "編集者"
        and role_label("guild_admin") == "サーバー管理者"
        and role_label("global_admin") == "全体管理者",
        "viewer={0} editor={1} guild_admin={2} global_admin={3}".format(
            role_label("viewer"),
            role_label("editor"),
            role_label("guild_admin"),
            role_label("global_admin"),
        ),
    )
    record(
        results,
        "role descriptions are available",
        "変更" in role_description("editor") and "全サーバー" in role_description("global_admin"),
        role_description("global_admin"),
    )

    auth_source = AUTH_PATH.read_text(encoding="utf-8")
    admin_bots_source = ADMIN_BOTS_PATH.read_text(encoding="utf-8")
    admin_users_template = ADMIN_USERS_TEMPLATE_PATH.read_text(encoding="utf-8")
    admin_user_form_template = ADMIN_USER_FORM_TEMPLATE_PATH.read_text(encoding="utf-8")
    permission_source = PERMISSIONS_PATH.read_text(encoding="utf-8")
    login_log_source = auth_source.split("def log_admin_login_decision", 1)[1].split("def register_auth_routes", 1)[0]
    record(
        results,
        "oauth callback requires registered admin user",
        "get_admin_login_status(user_id)" in auth_source and "error=access_denied" in auth_source,
        "admin_users gate",
    )
    record(
        results,
        "oauth callback logs safe login decision",
        "Admin OAuth login decision" in auth_source
        and "discord_user_id={1}" in login_log_source
        and "bot_permissions={8}" in login_log_source
        and "token" not in login_log_source.lower(),
        "safe oauth decision log",
    )
    record(
        results,
        "existing session is revalidated against admin_users",
        "Failed to validate admin session" in auth_source and "request.session.pop(SESSION_USER_KEY, None)" in auth_source,
        "session gate",
    )
    record(
        results,
        "developer env ids are not admin login bypass",
        "developer_user_ids" not in permission_source
        and "DEVELOPER_USER_ID" not in permission_source
        and "ADMIN_DEVELOPER_USER_IDS" not in permission_source,
        "admin_users is source of truth",
    )
    record(
        results,
        "legacy guild permissions do not grant bot access",
        "return bot_id == \"ichiyon\" and bool(self.list_guild_permissions(discord_user_id))" not in permission_source
        and "if self.list_guild_permissions(discord_user_id):" not in permission_source,
        "no guild_permissions fallback",
    )
    record(
        results,
        "admin user id edit moves bot permissions",
        "def update_admin_user_id" in permission_source
        and "UPDATE admin_users" in permission_source
        and "UPDATE bot_permissions" in permission_source,
        "admin_users and bot_permissions are updated together",
    )
    record(
        results,
        "admin user delete removes bot permissions",
        "def delete_admin_user_with_permissions" in permission_source
        and "DELETE FROM bot_permissions" in permission_source
        and "DELETE FROM admin_users" in permission_source,
        "permissions are deleted before admin user",
    )
    record(
        results,
        "user management has delete route",
        "/admin/users/{discord_user_id}/delete" in admin_bots_source
        and "delete_admin_user_with_permissions" in admin_bots_source,
        "delete route",
    )
    record(
        results,
        "self destructive user operations are blocked",
        "自分自身は削除できません" in admin_bots_source
        and "自分自身のDiscord User IDは変更できません" in admin_bots_source,
        "self delete and self id edit guards",
    )
    record(
        results,
        "last global admin is protected",
        "count_enabled_global_admins" in admin_bots_source
        and "最後のユーザー管理編集者" in admin_bots_source,
        "user management editor guard",
    )
    record(
        results,
        "admin users list exposes guarded delete action",
        "confirm_delete" in admin_users_template
        and "/delete" in admin_users_template
        and "削除" in admin_users_template,
        "delete form",
    )
    record(
        results,
        "discord user id is editable with warning",
        "{% if target_user %}readonly{% endif %}" not in admin_user_form_template
        and "Bot権限・サーバー権限も新しいIDへ引き継がれます" in admin_user_form_template,
        "editable id warning",
    )
    record(
        results,
        "admin user role label is user management permission",
        "ユーザー管理権限" in admin_user_form_template
        and "管理画面ロール" not in admin_user_form_template
        and "user_management_role_labels" in admin_user_form_template
        and 'name="can_manage_users"' not in admin_user_form_template,
        "user management label and two role choices",
    )
    record(
        results,
        "bot guild access is decoupled from admin user role",
        "def list_manageable_bots" in permission_source
        and "def can_access_bot" in permission_source
        and "def list_manageable_guilds_for_bot" in permission_source
        and permission_source.split("def list_manageable_bots", 1)[1].split("def can_access_bot", 1)[0].count("has_global_admin") == 0
        and permission_source.split("def can_access_bot", 1)[1].split("def list_configured_guilds_for_bot", 1)[0].count("has_global_admin") == 0
        and permission_source.split("def list_manageable_guilds_for_bot", 1)[1].split("def can_access_bot_guild", 1)[0].count("has_global_admin") == 0
        and permission_source.split("def can_access_bot_guild", 1)[1].split("def list_admin_users", 1)[0].count("has_global_admin") == 0,
        "bot/guild settings use bot_permissions",
    )
    check_legacy_admin_schema(results)

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
