import sys
from pathlib import Path
from typing import Any, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin.bots import parse_permission_value
from admin.role_labels import role_description, role_label
from bot.repositories.permissions import enabled_value, role_allows


AUTH_PATH = PROJECT_ROOT / "admin" / "auth.py"
ADMIN_BOTS_PATH = PROJECT_ROOT / "admin" / "bots.py"
ADMIN_USERS_TEMPLATE_PATH = PROJECT_ROOT / "admin" / "templates" / "admin_users.html"
ADMIN_USER_FORM_TEMPLATE_PATH = PROJECT_ROOT / "admin" / "templates" / "admin_user_form.html"
PERMISSIONS_PATH = PROJECT_ROOT / "bot" / "repositories" / "permissions.py"


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


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
        "変更" in role_description("editor") and "ユーザー管理" in role_description("global_admin"),
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
        and "最後の全体管理者" in admin_bots_source,
        "global admin guard",
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

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
