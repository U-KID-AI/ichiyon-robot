from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import set_selected_bot_id
from admin.role_labels import ROLE_DESCRIPTIONS, ROLE_LABELS
from bot.db import get_connection
from bot.repositories import PermissionRepository
from bot.repositories.bot_instances import BotInstanceRepository


router = APIRouter()
VALID_ADMIN_ROLES = {"global_admin", "viewer"}
VALID_PERMISSION_ROLES = {"global_admin", "guild_admin", "editor", "viewer"}


def register_bot_routes(templates: Jinja2Templates) -> None:
    @router.get("/admin")
    async def admin_home(request: Request):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        with get_connection() as connection:
            permissions = PermissionRepository(connection)
            return templates.TemplateResponse(
                request,
                "admin_home.html",
                {
                    "user": user,
                    "can_manage_users": permissions.can_manage_users(user["user_id"]),
                },
            )

    @router.get("/bots")
    async def bot_list(request: Request):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        with get_connection() as connection:
            bots = PermissionRepository(connection).list_manageable_bots(user["user_id"])
        return templates.TemplateResponse(
            request,
            "bots.html",
            {
                "user": user,
                "bots": bots,
            },
        )

    @router.get("/bots/{bot_id}/guilds")
    async def bot_guild_list(request: Request, bot_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        with get_connection() as connection:
            permissions = PermissionRepository(connection)
            if not permissions.can_access_bot(bot_id, user["user_id"]):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
            bot = BotInstanceRepository(connection).get(bot_id)
            guilds = permissions.list_manageable_guilds_for_bot(bot_id, user["user_id"])
        if not bot:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
        set_selected_bot_id(request, bot_id)
        return templates.TemplateResponse(
            request,
            "bot_guilds.html",
            {
                "user": user,
                "bot": bot,
                "servers": guilds,
            },
        )

    @router.get("/bots/{bot_id}/guilds/{guild_id}")
    async def select_bot_guild(request: Request, bot_id: str, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        with get_connection() as connection:
            permissions = PermissionRepository(connection)
            if not permissions.can_access_bot_guild(bot_id, guild_id, user["user_id"]):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="guild not found")
        set_selected_bot_id(request, bot_id)
        return RedirectResponse(url="/guilds/{0}".format(guild_id), status_code=303)

    @router.get("/admin/users")
    async def user_list(request: Request, message: Optional[str] = None, error: Optional[str] = None):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        with get_connection() as connection:
            permissions = PermissionRepository(connection)
            if not permissions.can_manage_users(user["user_id"]):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user management denied")
            rows = permissions.list_admin_users()
        return templates.TemplateResponse(
            request,
            "admin_users.html",
            {
                    "user": user,
                    "users": rows,
                    "message": message,
                    "error": error,
                },
            )

    @router.get("/admin/users/new")
    async def new_user(request: Request):
        return await render_user_form(templates, request, None)

    @router.get("/admin/users/{discord_user_id}")
    async def edit_user(request: Request, discord_user_id: str):
        return await render_user_form(templates, request, discord_user_id)

    @router.post("/admin/users/new")
    async def create_user(
        request: Request,
        discord_user_id: str = Form(...),
        display_name: str = Form(""),
        role: str = Form("viewer"),
        enabled: Optional[str] = Form(None),
        can_manage_users: Optional[str] = Form(None),
        bot_roles: List[str] = Form([]),
        guild_roles: List[str] = Form([]),
    ):
        return await save_user_with_id_edit(
            request,
            discord_user_id,
            display_name,
            role,
            enabled,
            can_manage_users,
            bot_roles,
            guild_roles,
            original_discord_user_id=None,
        )

    @router.post("/admin/users/{discord_user_id}")
    async def update_user(
        request: Request,
        discord_user_id: str,
        display_name: str = Form(""),
        role: str = Form("viewer"),
        enabled: Optional[str] = Form(None),
        can_manage_users: Optional[str] = Form(None),
        bot_roles: List[str] = Form([]),
        guild_roles: List[str] = Form([]),
    ):
        return await save_user_with_id_edit(
            request,
            discord_user_id,
            display_name,
            role,
            enabled,
            can_manage_users,
            bot_roles,
            guild_roles,
            original_discord_user_id=discord_user_id,
        )

    @router.post("/admin/users/{discord_user_id}/delete")
    async def delete_user(request: Request, discord_user_id: str, confirm_delete: Optional[str] = Form(None)):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        target_id = discord_user_id.strip()
        if confirm_delete != "on":
            return RedirectResponse(url="/admin/users?error={0}".format(quote("削除確認にチェックしてください。")), status_code=303)
        if target_id == str(user["user_id"]):
            return RedirectResponse(url="/admin/users?error={0}".format(quote("自分自身は削除できません。")), status_code=303)
        with get_connection() as connection:
            permissions = PermissionRepository(connection)
            if not permissions.can_manage_users(user["user_id"]):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user management denied")
            target_user = permissions.get_admin_user(target_id)
            if not target_user:
                return RedirectResponse(url="/admin/users?error={0}".format(quote("対象ユーザーが見つかりません。")), status_code=303)
            if target_user.get("role") == "global_admin" and permissions.count_enabled_global_admins(exclude_discord_user_id=target_id) <= 0:
                return RedirectResponse(url="/admin/users?error={0}".format(quote("最後の全体管理者は削除できません。")), status_code=303)
            permissions.delete_admin_user_with_permissions(target_id)
            connection.commit()
        return RedirectResponse(url="/admin/users?message={0}".format(quote("ユーザーを削除しました。")), status_code=303)


async def render_user_form(
    templates: Jinja2Templates,
    request: Request,
    discord_user_id: Optional[str],
    errors: Optional[List[str]] = None,
    form: Optional[Dict[str, Any]] = None,
):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    with get_connection() as connection:
        permissions = PermissionRepository(connection)
        if not permissions.can_manage_users(user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user management denied")
        row = permissions.get_admin_user(discord_user_id) if discord_user_id else None
        bots = BotInstanceRepository(connection).list_enabled()
        guilds = permissions.list_manageable_guilds(user["user_id"])
        bot_permissions = permissions.list_bot_permissions(discord_user_id) if discord_user_id else []

    selected_bot_roles = {
        item["bot_id"]: item["role"]
        for item in bot_permissions
        if item.get("guild_id") is None
    }
    selected_guild_roles = {
        "{0}:{1}".format(item["bot_id"], item["guild_id"]): item["role"]
        for item in bot_permissions
        if item.get("guild_id") is not None
    }
    if form is None:
        form = {
            "discord_user_id": discord_user_id or "",
            "display_name": row.get("display_name") if row else "",
            "role": row.get("role") if row else "viewer",
            "enabled": True if row is None else bool(row.get("enabled", True)),
            "can_manage_users": bool(row.get("can_manage_users")) if row else False,
        }
    return templates.TemplateResponse(
        request,
        "admin_user_form.html",
        {
            "user": user,
            "target_user": row,
            "form": form,
            "errors": errors or [],
            "bots": bots,
            "guilds": guilds,
            "selected_bot_roles": selected_bot_roles,
            "selected_guild_roles": selected_guild_roles,
            "valid_roles": sorted(VALID_PERMISSION_ROLES),
            "role_labels": ROLE_LABELS,
            "role_descriptions": ROLE_DESCRIPTIONS,
        },
        status_code=400 if errors else 200,
    )


async def save_user(
    request: Request,
    discord_user_id: str,
    display_name: str,
    role: str,
    enabled: Optional[str],
    can_manage_users: Optional[str],
    bot_roles: List[str],
    guild_roles: List[str],
):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)

    discord_user_id = discord_user_id.strip()
    form = {
        "discord_user_id": discord_user_id,
        "display_name": display_name.strip(),
        "role": role,
        "enabled": enabled == "on",
        "can_manage_users": can_manage_users == "on",
    }
    errors: List[str] = []
    if not discord_user_id:
        errors.append("Discord user ID is required.")
    if role not in VALID_ADMIN_ROLES:
        errors.append("管理画面ロールが不正です。")

    parsed_permissions: List[Dict[str, Any]] = []
    for value in bot_roles + guild_roles:
        parsed = parse_permission_value(value)
        if parsed is None:
            continue
        parsed_permissions.append(parsed)

    if errors:
        templates = request.app.state.templates
        return await render_user_form(templates, request, discord_user_id or None, errors, form)

    with get_connection() as connection:
        permissions = PermissionRepository(connection)
        if not permissions.can_manage_users(user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user management denied")
        permissions.upsert_admin_user(
            discord_user_id,
            form["display_name"],
            role,
            form["enabled"],
            form["can_manage_users"],
        )
        permissions.replace_bot_permissions(discord_user_id, parsed_permissions)
        connection.commit()
    return RedirectResponse(url="/admin/users/{0}".format(discord_user_id), status_code=303)


async def save_user_with_id_edit(
    request: Request,
    discord_user_id: str,
    display_name: str,
    role: str,
    enabled: Optional[str],
    can_manage_users: Optional[str],
    bot_roles: List[str],
    guild_roles: List[str],
    original_discord_user_id: Optional[str] = None,
):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)

    discord_user_id = discord_user_id.strip()
    original_discord_user_id = (original_discord_user_id or "").strip()
    is_edit = bool(original_discord_user_id)
    id_changed = is_edit and discord_user_id != original_discord_user_id
    form = {
        "discord_user_id": discord_user_id,
        "display_name": display_name.strip(),
        "role": role,
        "enabled": enabled == "on",
        "can_manage_users": can_manage_users == "on",
    }
    errors: List[str] = []
    if not discord_user_id:
        errors.append("Discord user ID is required.")
    if role not in VALID_ADMIN_ROLES:
        errors.append("管理画面ロールが不正です。")
    if id_changed and original_discord_user_id == str(user["user_id"]):
        errors.append("自分自身のDiscord User IDは変更できません。")

    parsed_permissions: List[Dict[str, Any]] = []
    for value in bot_roles + guild_roles:
        parsed = parse_permission_value(value)
        if parsed is None:
            continue
        parsed_permissions.append(parsed)

    if errors:
        templates = request.app.state.templates
        return await render_user_form(templates, request, original_discord_user_id or None, errors, form)

    with get_connection() as connection:
        permissions = PermissionRepository(connection)
        if not permissions.can_manage_users(user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user management denied")
        existing_target = permissions.get_admin_user(original_discord_user_id) if is_edit else None
        if is_edit and not existing_target:
            templates = request.app.state.templates
            return await render_user_form(templates, request, original_discord_user_id, ["対象ユーザーが見つかりません。"], form)
        if id_changed and permissions.admin_user_exists(discord_user_id):
            templates = request.app.state.templates
            return await render_user_form(templates, request, original_discord_user_id, ["変更先Discord User IDは既に登録されています。"], form)
        if not is_edit and permissions.admin_user_exists(discord_user_id):
            templates = request.app.state.templates
            return await render_user_form(templates, request, None, ["このDiscord User IDは既に登録されています。"], form)
        if existing_target and existing_target.get("role") == "global_admin":
            will_remain_global_admin = role == "global_admin" and form["enabled"]
            if not will_remain_global_admin and permissions.count_enabled_global_admins(exclude_discord_user_id=original_discord_user_id) <= 0:
                templates = request.app.state.templates
                return await render_user_form(templates, request, original_discord_user_id, ["最後の全体管理者を無効化・降格できません。"], form)
        if id_changed:
            permissions.update_admin_user_id(original_discord_user_id, discord_user_id)
        permissions.upsert_admin_user(
            discord_user_id,
            form["display_name"],
            role,
            form["enabled"],
            form["can_manage_users"],
        )
        permissions.replace_bot_permissions(discord_user_id, parsed_permissions)
        connection.commit()
    return RedirectResponse(url="/admin/users?message={0}".format(quote("ユーザーを保存しました。")), status_code=303)


def parse_permission_value(value: str) -> Optional[Dict[str, Any]]:
    parts = value.split(":")
    if len(parts) not in (2, 3):
        return None
    role = parts[-1]
    if role not in VALID_PERMISSION_ROLES:
        return None
    if len(parts) == 2:
        return {"bot_id": parts[0], "guild_id": None, "role": role}
    return {"bot_id": parts[0], "guild_id": parts[1], "role": role}
