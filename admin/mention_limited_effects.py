from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from admin.ux import EFFECT_TYPE_LABELS, is_test_data, parse_show_test_data
from bot.db import get_connection
from bot.repositories import MentionLimitedEffectRepository, SpecialEffectRepository


router = APIRouter()
MENTION_EFFECT_TARGET = "mention_reaction_choice"


def register_mention_limited_effect_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/mention-reactions/limited")
    async def limited_effects_page(
        request: Request,
        guild_id: str,
        q: Optional[str] = Query(None),
        enabled: str = Query("all"),
        show_test_data: str = Query("false"),
        message: str = Query(""),
        error: str = Query(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        filters = normalize_filters(q, enabled, show_test_data)
        entries = list_entry_rows(guild_id, server["role"], filters)
        return templates.TemplateResponse(
            request,
            "mention_limited_effects.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "filters": filters,
                "entries": entries,
                "can_create": role_allows(server["role"], "editor"),
                "message": message,
                "error": error,
            },
        )

    @router.post("/guilds/{guild_id}/mention-reactions/limited/bulk-enabled")
    async def bulk_set_limited_effects_enabled(
        request: Request,
        guild_id: str,
        action: str = Form(""),
        entry_ids: List[int] = Form([]),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not entry_ids:
            return RedirectResponse(url="/guilds/{0}/mention-reactions/limited?error={1}".format(guild_id, quote("項目を選択してね")), status_code=303)
        if action not in ("on", "off"):
            return RedirectResponse(url="/guilds/{0}/mention-reactions/limited?error={1}".format(guild_id, quote("操作を選んでね")), status_code=303)
        updated_count = 0
        with get_connection() as connection:
            repository = MentionLimitedEffectRepository(connection, bot_id=current_selected_bot_id())
            for entry_id in entry_ids:
                entry = repository.get_by_id(guild_id, entry_id)
                if entry is None or not can_manage_entry(server["role"], entry):
                    continue
                if repository.set_enabled(guild_id, entry_id, action == "on") is not None:
                    updated_count += 1
            connection.commit()
        failed_count = max(0, len(entry_ids) - updated_count)
        return RedirectResponse(
            url="/guilds/{0}/mention-reactions/limited?message={1}".format(guild_id, quote("成功{0}件 / 失敗{1}件".format(updated_count, failed_count))),
            status_code=303,
        )

    @router.get("/guilds/{guild_id}/mention-reactions/limited/new")
    async def new_limited_effect_page(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="limited effect creation denied")
        return render_form(templates, request, server, guild_id, "new", default_form(), [], True)

    @router.post("/guilds/{guild_id}/mention-reactions/limited/new")
    async def create_limited_effect(
        request: Request,
        guild_id: str,
        discord_user_id: str = Form(""),
        display_name: str = Form(""),
        effect_tag_id: str = Form(""),
        description: str = Form(""),
        enabled: Optional[str] = Form(None),
    ):
        return await save_limited_effect(
            templates,
            request,
            guild_id,
            None,
            discord_user_id,
            display_name,
            effect_tag_id,
            description,
            enabled,
        )

    @router.get("/guilds/{guild_id}/mention-reactions/limited/{entry_id}")
    async def edit_limited_effect_page(request: Request, guild_id: str, entry_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = MentionLimitedEffectRepository(connection, bot_id=current_selected_bot_id())
            entry = repository.get_by_id(guild_id, entry_id)
            if entry is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="limited effect not found")
        return render_form(
            templates,
            request,
            server,
            guild_id,
            "edit",
            build_form_from_entry(entry),
            [],
            can_manage_entry(server["role"], entry),
            entry_id=entry_id,
        )

    @router.post("/guilds/{guild_id}/mention-reactions/limited/{entry_id}")
    async def update_limited_effect(
        request: Request,
        guild_id: str,
        entry_id: int,
        discord_user_id: str = Form(""),
        display_name: str = Form(""),
        effect_tag_id: str = Form(""),
        description: str = Form(""),
        enabled: Optional[str] = Form(None),
    ):
        return await save_limited_effect(
            templates,
            request,
            guild_id,
            entry_id,
            discord_user_id,
            display_name,
            effect_tag_id,
            description,
            enabled,
        )

    @router.post("/guilds/{guild_id}/mention-reactions/limited/{entry_id}/toggle")
    async def toggle_limited_effect(request: Request, guild_id: str, entry_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = MentionLimitedEffectRepository(connection, bot_id=current_selected_bot_id())
            entry = repository.get_by_id(guild_id, entry_id)
            if entry is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="limited effect not found")
            if not can_manage_entry(server["role"], entry):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="limited effect toggle denied")
            repository.toggle_enabled(guild_id, entry_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/mention-reactions/limited".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/mention-reactions/limited/{entry_id}/delete")
    async def delete_limited_effect(request: Request, guild_id: str, entry_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = MentionLimitedEffectRepository(connection, bot_id=current_selected_bot_id())
            entry = repository.get_by_id(guild_id, entry_id)
            if entry is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="limited effect not found")
            if not can_manage_entry(server["role"], entry):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="limited effect delete denied")
            repository.delete_entry(guild_id, entry_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/mention-reactions/limited".format(guild_id), status_code=303)


def normalize_filters(q: Optional[str], enabled: str, show_test_data: str) -> Dict[str, Any]:
    return {
        "q": (q or "").strip(),
        "enabled": enabled if enabled in ("all", "true", "false") else "all",
        "show_test_data": parse_show_test_data(show_test_data),
    }


def parse_bool(value: str) -> Optional[bool]:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def list_entry_rows(guild_id: str, role: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = MentionLimitedEffectRepository(connection, bot_id=current_selected_bot_id())
        entries = repository.list_entries(
            guild_id,
            query=filters["q"] or None,
            enabled=parse_bool(filters["enabled"]),
        )
    rows = []
    for entry in entries:
        if not filters["show_test_data"] and row_is_hidden_test_data(entry):
            continue
        row = build_form_from_entry(entry)
        row["effect_type_label"] = EFFECT_TYPE_LABELS.get(row["effect_type"], row["effect_type"])
        row["can_manage"] = can_manage_entry(role, entry)
        row["edit_url"] = "/guilds/{0}/mention-reactions/limited/{1}".format(guild_id, entry["id"])
        row["toggle_url"] = "/guilds/{0}/mention-reactions/limited/{1}/toggle".format(guild_id, entry["id"])
        row["delete_url"] = "/guilds/{0}/mention-reactions/limited/{1}/delete".format(guild_id, entry["id"])
        rows.append(row)
    return rows


def row_is_hidden_test_data(row: Dict[str, Any]) -> bool:
    return (
        is_test_data(row.get("discord_user_id"))
        or is_test_data(row.get("display_name"))
        or is_test_data(row.get("description"))
    )


def can_manage_entry(role: str, entry: Dict[str, Any]) -> bool:
    if entry.get("effect_tag_admin_only"):
        return role_allows(role, "guild_admin")
    return role_allows(role, "editor")


def default_form() -> Dict[str, Any]:
    return {
        "id": None,
        "discord_user_id": "",
        "display_name": "",
        "effect_tag_id": "",
        "effect_tag_name": "",
        "effect_tag_color": "",
        "effect_type": "",
        "effect_type_label": "",
        "effect_tag_admin_only": False,
        "description": "",
        "enabled": True,
    }


def build_form_from_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    form = default_form()
    form.update(
        {
            "id": entry.get("id"),
            "discord_user_id": entry.get("discord_user_id") or "",
            "display_name": entry.get("display_name") or "",
            "effect_tag_id": "" if entry.get("effect_tag_id") is None else str(entry.get("effect_tag_id")),
            "effect_tag_name": entry.get("effect_tag_name") or "",
            "effect_tag_color": entry.get("effect_tag_color") or "#6B7280",
            "effect_type": entry.get("effect_type") or "",
            "effect_type_label": EFFECT_TYPE_LABELS.get(entry.get("effect_type"), entry.get("effect_type") or ""),
            "effect_tag_admin_only": bool(entry.get("effect_tag_admin_only")),
            "description": entry.get("description") or "",
            "enabled": bool(entry.get("enabled")),
        }
    )
    return form


def build_form(
    discord_user_id: str,
    display_name: str,
    effect_tag_id: str,
    description: str,
    enabled: Optional[str],
) -> Tuple[Dict[str, Any], List[str]]:
    form = default_form()
    form.update(
        {
            "discord_user_id": discord_user_id.strip(),
            "display_name": display_name.strip(),
            "effect_tag_id": effect_tag_id.strip(),
            "description": description.strip(),
            "enabled": enabled == "on",
        }
    )
    errors = []
    if not form["discord_user_id"]:
        errors.append("対象ユーザーIDを入力。")
    elif not form["discord_user_id"].isdigit():
        errors.append("対象ユーザーIDは数字。")
    try:
        tag_id = int(form["effect_tag_id"])
    except (TypeError, ValueError):
        tag_id = 0
    if tag_id <= 0:
        errors.append("特殊効果タグを選択。")
    return form, errors


def list_available_tags(guild_id: str, role: str) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        tags = SpecialEffectRepository(connection, bot_id=current_selected_bot_id()).list_tags(
            guild_id,
            target_type=MENTION_EFFECT_TARGET,
            enabled=None,
        )
    rows = []
    for tag in tags:
        if tag.get("admin_only") and not role_allows(role, "guild_admin"):
            continue
        row = dict(tag)
        row["effect_type_label"] = EFFECT_TYPE_LABELS.get(row.get("effect_type"), row.get("effect_type"))
        rows.append(row)
    return rows


def validate_tag(guild_id: str, role: str, tag_id: int) -> Optional[str]:
    with get_connection() as connection:
        tag = SpecialEffectRepository(connection, bot_id=current_selected_bot_id()).get_by_id(guild_id, tag_id)
    if tag is None:
        return "特殊効果タグが見つからない。"
    if tag.get("target_type") != MENTION_EFFECT_TARGET:
        return "メンション反応向けタグだけ選択可。"
    if tag.get("admin_only") and not role_allows(role, "guild_admin"):
        return "管理者限定タグはサーバー管理者以上だけ。"
    return None


async def save_limited_effect(
    templates: Jinja2Templates,
    request: Request,
    guild_id: str,
    entry_id: Optional[int],
    discord_user_id: str,
    display_name: str,
    effect_tag_id: str,
    description: str,
    enabled: Optional[str],
):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
    server = find_server(guild_id, user["user_id"], selected_bot_id(request))
    if not role_allows(server["role"], "editor"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="limited effect editing denied")

    form, errors = build_form(discord_user_id, display_name, effect_tag_id, description, enabled)
    tag_id = int(form["effect_tag_id"]) if form["effect_tag_id"].isdigit() else 0
    if tag_id > 0:
        tag_error = validate_tag(guild_id, server["role"], tag_id)
        if tag_error:
            errors.append(tag_error)

    if errors:
        return render_form(
            templates,
            request,
            server,
            guild_id,
            "new" if entry_id is None else "edit",
            form,
            errors,
            True,
            entry_id=entry_id,
            status_code=400,
        )

    with get_connection() as connection:
        repository = MentionLimitedEffectRepository(connection, bot_id=current_selected_bot_id())
        if entry_id is None:
            entry = repository.create_entry(
                guild_id,
                form["discord_user_id"],
                form["display_name"],
                tag_id,
                form["description"],
                form["enabled"],
            )
            connection.commit()
            return RedirectResponse(
                url="/guilds/{0}/mention-reactions/limited/{1}".format(guild_id, entry["id"]),
                status_code=303,
            )

        existing = repository.get_by_id(guild_id, entry_id)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="limited effect not found")
        if not can_manage_entry(server["role"], existing):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="limited effect editing denied")
        repository.update_entry(
            guild_id,
            entry_id,
            form["discord_user_id"],
            form["display_name"],
            tag_id,
            form["description"],
            form["enabled"],
        )
        connection.commit()
    return RedirectResponse(url="/guilds/{0}/mention-reactions/limited/{1}".format(guild_id, entry_id), status_code=303)


def render_form(
    templates: Jinja2Templates,
    request: Request,
    server: Dict[str, Any],
    guild_id: str,
    mode: str,
    form: Dict[str, Any],
    errors: List[str],
    can_edit: bool,
    entry_id: Optional[int] = None,
    status_code: int = 200,
):
    tags = list_available_tags(guild_id, server["role"])
    return templates.TemplateResponse(
        request,
        "mention_limited_effect_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "mode": mode,
            "entry_id": entry_id,
            "entry": form,
            "errors": errors,
            "tags": tags,
            "can_edit": can_edit,
        },
        status_code=status_code,
    )
