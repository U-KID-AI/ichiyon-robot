import json
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.servers import can_access_guild, find_server, role_allows
from admin.ux import (
    ADDITIONAL_POST_TIMING_LABELS,
    COOLDOWN_SCOPE_LABELS,
    EFFECT_TYPE_LABELS,
    MATCH_TYPE_LABELS,
    is_test_data,
    parse_show_test_data,
    save_uploaded_image,
)
from bot.db import get_connection
from bot.repositories import AutoReactionRepository, SpecialEffectRepository


router = APIRouter()

MATCH_TYPES = ("exact", "prefix", "regex", "contains")
TARGET_TYPE = "auto_reaction"
EFFECT_TYPES = (
    "probability_message",
    "message",
    "reaction",
    "counter_delta",
    "counter_set",
    "probability_multiplier",
    "next_action_count",
    "mode_roll",
    "mode_enter",
    "temporary_state",
    "ng_behavior",
    "extra_choice",
    "destroy",
)
DUPLICATE_ERROR = "同じ呼び出しワードと一致方式の自動反応が登録済み。"


def register_auto_reaction_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/auto-reactions")
    async def auto_reactions_page(
        request: Request,
        guild_id: str,
        q: Optional[str] = Query(None),
        enabled: str = Query("all"),
        has_image: str = Query("all"),
        has_effects: str = Query("all"),
        show_test_data: str = Query("false"),
        message: str = Query(""),
        error: str = Query(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        filters = normalize_filters(q, enabled, has_image, has_effects, show_test_data)
        reactions = list_reaction_rows(guild_id, server["role"], filters)
        return templates.TemplateResponse(
            request,
            "auto_reactions.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "filters": filters,
                "reactions": reactions,
                "can_create": role_allows(server["role"], "editor"),
                "message": message,
                "error": error,
            },
        )

    @router.post("/guilds/{guild_id}/auto-reactions/bulk-enabled")
    async def bulk_set_auto_reactions_enabled(
        request: Request,
        guild_id: str,
        action: str = Form(""),
        reaction_ids: List[int] = Form([]),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto reaction bulk update denied")
        if not reaction_ids:
            return RedirectResponse(url="/guilds/{0}/auto-reactions?error={1}".format(guild_id, quote("項目を選択してね")), status_code=303)
        if action not in ("on", "off"):
            return RedirectResponse(url="/guilds/{0}/auto-reactions?error={1}".format(guild_id, quote("操作を選んでね")), status_code=303)
        with get_connection() as connection:
            repository = AutoReactionRepository(connection)
            updated_count = repository.bulk_set_enabled(guild_id, reaction_ids, action == "on")
            connection.commit()
        failed_count = max(0, len(reaction_ids) - updated_count)
        return RedirectResponse(
            url="/guilds/{0}/auto-reactions?message={1}".format(guild_id, quote("成功{0}件 / 失敗{1}件".format(updated_count, failed_count))),
            status_code=303,
        )

    @router.post("/guilds/{guild_id}/auto-reactions/{reaction_id}/toggle")
    async def toggle_auto_reaction(request: Request, guild_id: str, reaction_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto reaction toggle denied")

        with get_connection() as connection:
            repository = AutoReactionRepository(connection)
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto reaction not found")
            repository.toggle_enabled(guild_id, reaction_id)
            connection.commit()

        return RedirectResponse(url="/guilds/{0}/auto-reactions".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/auto-reactions/{reaction_id}/copy")
    async def copy_auto_reaction(request: Request, guild_id: str, reaction_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto reaction copy denied")
        with get_connection() as connection:
            repository = AutoReactionRepository(connection)
            copied = repository.copy_reaction(guild_id, reaction_id)
            if copied is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto reaction not found")
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/auto-reactions/{1}".format(guild_id, copied["id"]), status_code=303)

    @router.post("/guilds/{guild_id}/auto-reactions/{reaction_id}/delete")
    async def delete_auto_reaction(request: Request, guild_id: str, reaction_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="サーバーを見る権限がありません。")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="削除する権限がありません。")
        with get_connection() as connection:
            repository = AutoReactionRepository(connection)
            if repository.get_by_id(guild_id, reaction_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="自動反応が見つかりません。")
            repository.delete_reaction(guild_id, reaction_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/auto-reactions".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/auto-reactions/new")
    async def new_auto_reaction_page(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto reaction creation denied")

        return render_form(templates, request, server, guild_id, "new", default_form(), [], True)

    @router.post("/guilds/{guild_id}/auto-reactions/new")
    async def create_auto_reaction(
        request: Request,
        guild_id: str,
        trigger_text: str = Form(""),
        response_text: str = Form(""),
        image_path: str = Form(""),
        image_upload: Optional[UploadFile] = File(None),
        emoji_internal: str = Form(""),
        match_type: str = Form("contains"),
        priority: str = Form("0"),
        enabled: Optional[str] = Form(None),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto reaction creation denied")

        uploaded_path, upload_error = await save_uploaded_image(image_upload, "auto_reactions")
        if uploaded_path:
            image_path = uploaded_path
        form, errors = build_form(trigger_text, response_text, image_path, emoji_internal, match_type, priority, enabled)
        if upload_error:
            errors.append(upload_error)
        with get_connection() as connection:
            repository = AutoReactionRepository(connection)
            if not errors and repository.trigger_exists(guild_id, form["trigger_text"], form["match_type"]):
                errors.append(DUPLICATE_ERROR)
            if not errors:
                reaction = repository.create_reaction(
                    guild_id,
                    form["trigger_text"],
                    form["response_text"] or None,
                    form["image_path"] or None,
                    form["emoji_internal"] or None,
                    form["match_type"],
                    form["priority"],
                    form["enabled"],
                )
                connection.commit()
                return RedirectResponse(
                    url="/guilds/{0}/auto-reactions/{1}".format(guild_id, reaction["id"]),
                    status_code=303,
                )

        return render_form(templates, request, server, guild_id, "new", form, errors, True, status_code=400)

    @router.get("/guilds/{guild_id}/auto-reactions/{reaction_id}")
    async def edit_auto_reaction_page(request: Request, guild_id: str, reaction_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            repository = AutoReactionRepository(connection)
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto reaction not found")
            row = build_reaction_view(connection, guild_id, reaction, server["role"])

        return render_form(
            templates,
            request,
            server,
            guild_id,
            "edit",
            row,
            [],
            role_allows(server["role"], "editor"),
            reaction_id=reaction_id,
        )

    @router.post("/guilds/{guild_id}/auto-reactions/{reaction_id}")
    async def update_auto_reaction(
        request: Request,
        guild_id: str,
        reaction_id: int,
        trigger_text: str = Form(""),
        response_text: str = Form(""),
        image_path: str = Form(""),
        image_upload: Optional[UploadFile] = File(None),
        delete_image: Optional[str] = Form(None),
        emoji_internal: str = Form(""),
        match_type: str = Form("contains"),
        priority: str = Form("0"),
        enabled: Optional[str] = Form(None),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto reaction editing denied")

        if delete_image:
            image_path = ""
        uploaded_path, upload_error = await save_uploaded_image(image_upload, "auto_reactions")
        if uploaded_path:
            image_path = uploaded_path
        form, errors = build_form(trigger_text, response_text, image_path, emoji_internal, match_type, priority, enabled)
        if upload_error:
            errors.append(upload_error)
        with get_connection() as connection:
            repository = AutoReactionRepository(connection)
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto reaction not found")
            if not errors and repository.trigger_exists(guild_id, form["trigger_text"], form["match_type"], reaction_id):
                errors.append(DUPLICATE_ERROR)
            if not errors:
                repository.update_reaction(
                    guild_id,
                    reaction_id,
                    form["trigger_text"],
                    form["response_text"] or None,
                    form["image_path"] or None,
                    form["emoji_internal"] or None,
                    form["match_type"],
                    form["priority"],
                    form["enabled"],
                )
                connection.commit()
                return RedirectResponse(
                    url="/guilds/{0}/auto-reactions/{1}".format(guild_id, reaction_id),
                    status_code=303,
                )
            form["id"] = reaction_id
            form["effects"] = list_effects_for_target(connection, guild_id, reaction_id, server["role"])

        return render_form(
            templates,
            request,
            server,
            guild_id,
            "edit",
            form,
            errors,
            True,
            reaction_id=reaction_id,
            status_code=400,
        )

    @router.get("/guilds/{guild_id}/auto-reactions/{reaction_id}/effects")
    async def auto_reaction_effects_page(
        request: Request,
        guild_id: str,
        reaction_id: int,
        q: Optional[str] = Query(None),
        effect_type: str = Query("all"),
        admin_only: str = Query("all"),
        include_disabled: str = Query("false"),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        filters = normalize_assignment_filters(q, effect_type, admin_only, include_disabled)
        with get_connection() as connection:
            repository = AutoReactionRepository(connection)
            reaction = repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto reaction not found")
            tags = list_assignable_effect_rows(connection, guild_id, reaction_id, server["role"], filters)

        return templates.TemplateResponse(
            request,
            "auto_reaction_effects.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "reaction": build_form_from_reaction(reaction),
                "filters": filters,
                "tags": tags,
                "effect_types": EFFECT_TYPES,
                "effect_type_labels": EFFECT_TYPE_LABELS,
            },
        )

    @router.post("/guilds/{guild_id}/auto-reactions/{reaction_id}/effects")
    async def update_auto_reaction_effects(
        request: Request,
        guild_id: str,
        reaction_id: int,
        tag_id: int = Form(...),
        action: str = Form(...),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            auto_repository = AutoReactionRepository(connection)
            reaction = auto_repository.get_by_id(guild_id, reaction_id)
            if reaction is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto reaction not found")
            effect_repository = SpecialEffectRepository(connection)
            tag = effect_repository.get_by_id(guild_id, tag_id)
            if tag is None or tag.get("target_type") != TARGET_TYPE:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="special effect tag not found")
            if not can_manage_effect_assignment(server["role"], tag):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="special effect assignment denied")
            if action == "assign":
                effect_repository.assign_tag(guild_id, tag_id, TARGET_TYPE, reaction_id)
            elif action == "unassign":
                effect_repository.unassign_tag(guild_id, tag_id, TARGET_TYPE, reaction_id)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown assignment action")
            connection.commit()

        return RedirectResponse(url="/guilds/{0}/auto-reactions/{1}".format(guild_id, reaction_id), status_code=303)


def normalize_filters(
    q: Optional[str],
    enabled: str,
    has_image: str,
    has_effects: str,
    show_test_data: str = "false",
) -> Dict[str, Any]:
    return {
        "q": (q or "").strip(),
        "enabled": enabled if enabled in ("all", "true", "false") else "all",
        "has_image": has_image if has_image in ("all", "true", "false") else "all",
        "has_effects": has_effects if has_effects in ("all", "true", "false") else "all",
        "show_test_data": parse_show_test_data(show_test_data),
    }


def parse_bool(value: str) -> Optional[bool]:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def list_reaction_rows(guild_id: str, role: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = AutoReactionRepository(connection)
        reactions = repository.list_reactions(
            guild_id,
            query=filters["q"] or None,
            enabled=parse_bool(filters["enabled"]),
            has_image=parse_bool(filters["has_image"]),
        )
        rows = [
            build_reaction_view(connection, guild_id, reaction, role)
            for reaction in reactions
            if filters["show_test_data"] or not row_is_hidden_test_data(reaction)
        ]

    if filters["has_effects"] == "true":
        rows = [row for row in rows if row["effects"]]
    elif filters["has_effects"] == "false":
        rows = [row for row in rows if not row["effects"]]
    return rows


def build_reaction_view(connection, guild_id: str, reaction: Dict[str, Any], role: str) -> Dict[str, Any]:
    row = build_form_from_reaction(reaction)
    row["response_summary"] = summarize(row["response_text"])
    row["has_image"] = bool(row["image_path"])
    row["effects"] = list_effects_for_target(connection, guild_id, int(row["id"]), role)
    row["edit_url"] = "/guilds/{0}/auto-reactions/{1}".format(guild_id, row["id"])
    row["toggle_url"] = "/guilds/{0}/auto-reactions/{1}/toggle".format(guild_id, row["id"])
    row["copy_url"] = "/guilds/{0}/auto-reactions/{1}/copy".format(guild_id, row["id"])
    row["delete_url"] = "/guilds/{0}/auto-reactions/{1}/delete".format(guild_id, row["id"])
    row["effects_url"] = "/guilds/{0}/auto-reactions/{1}/effects".format(guild_id, row["id"])
    row["match_type_label"] = MATCH_TYPE_LABELS.get(row["match_type"], row["match_type"])
    row["can_delete"] = role_allows(role, "editor")
    return row


def row_is_hidden_test_data(row: Dict[str, Any]) -> bool:
    return (
        is_test_data(row.get("trigger_text"))
        or is_test_data(row.get("response_text"))
        or is_test_data(row.get("emoji_internal"))
    )


def summarize(value: str) -> str:
    if not value:
        return "なし"
    if len(value) <= 40:
        return value
    return "{0}...".format(value[:40])


def default_form() -> Dict[str, Any]:
    return {
        "id": None,
        "trigger_text": "",
        "response_text": "",
        "image_path": "",
        "emoji_internal": "",
        "match_type": "contains",
        "priority": 0,
        "enabled": True,
        "effects": [],
    }


def build_form_from_reaction(reaction: Dict[str, Any]) -> Dict[str, Any]:
    form = default_form()
    form.update(
        {
            "id": reaction.get("id"),
            "trigger_text": reaction.get("trigger_text") or "",
            "response_text": reaction.get("response_text") or "",
            "image_path": reaction.get("image_path") or "",
            "emoji_internal": reaction.get("emoji_internal") or "",
            "match_type": reaction.get("match_type") or "contains",
            "priority": int(reaction.get("priority") or 0),
            "enabled": bool(reaction.get("enabled")),
        }
    )
    return form


def build_form(
    trigger_text: str,
    response_text: str,
    image_path: str,
    emoji_internal: str,
    match_type: str,
    priority: str,
    enabled: Optional[str],
) -> Tuple[Dict[str, Any], List[str]]:
    form = default_form()
    try:
        priority_value = int(priority)
    except ValueError:
        priority_value = 0
        priority_error = True
    else:
        priority_error = False

    form.update(
        {
            "trigger_text": trigger_text.strip(),
            "response_text": response_text.strip(),
            "image_path": image_path.strip(),
            "emoji_internal": emoji_internal.strip(),
            "match_type": match_type if match_type in MATCH_TYPES else "contains",
            "priority": priority_value,
            "enabled": enabled == "on",
        }
    )

    errors = []
    if not form["trigger_text"]:
        errors.append("呼び出しワードを入力。")
    if match_type not in MATCH_TYPES:
        errors.append("一致方式を選択。")
    if priority_error:
        errors.append("優先度は整数。")
    return form, errors


def render_form(
    templates: Jinja2Templates,
    request: Request,
    server: Dict[str, Any],
    guild_id: str,
    mode: str,
    form: Dict[str, Any],
    errors: List[str],
    can_edit: bool,
    reaction_id: Optional[int] = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "auto_reaction_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "mode": mode,
            "reaction_id": reaction_id,
            "reaction": form,
            "errors": errors,
            "can_edit": can_edit,
            "match_types": MATCH_TYPES,
            "match_type_labels": MATCH_TYPE_LABELS,
        },
        status_code=status_code,
    )


def can_see_effect_tag(role: str, tag: Dict[str, Any]) -> bool:
    if tag.get("admin_only"):
        return role_allows(role, "guild_admin")
    return True


def can_manage_effect_assignment(role: str, tag: Dict[str, Any]) -> bool:
    if tag.get("admin_only"):
        return role_allows(role, "guild_admin")
    return role_allows(role, "editor")


def list_effects_for_target(connection, guild_id: str, reaction_id: int, role: str) -> List[Dict[str, Any]]:
    repository = SpecialEffectRepository(connection)
    effects = repository.list_for_target(guild_id, TARGET_TYPE, reaction_id, enabled=None)
    return [
        build_effect_view(effect, role)
        for effect in effects
        if effect.get("assignment_enabled") and can_see_effect_tag(role, effect)
    ]


def build_effect_view(effect: Dict[str, Any], role: str) -> Dict[str, Any]:
    row = dict(effect)
    row["can_manage"] = can_manage_effect_assignment(role, effect)
    row["effect_config_summary"] = compact_json(effect.get("effect_config_json"))
    row["effect_type_label"] = EFFECT_TYPE_LABELS.get(row.get("effect_type"), row.get("effect_type"))
    row["additional_post_timing_label"] = ADDITIONAL_POST_TIMING_LABELS.get(
        row.get("additional_post_timing"),
        row.get("additional_post_timing"),
    )
    row["cooldown_scope_label"] = COOLDOWN_SCOPE_LABELS.get(row.get("cooldown_scope"), row.get("cooldown_scope"))
    return row


def compact_json(value) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return "{}"


def normalize_assignment_filters(
    query: Optional[str],
    effect_type: str,
    admin_only: str,
    include_disabled: str,
) -> Dict[str, Any]:
    return {
        "q": (query or "").strip(),
        "effect_type": effect_type if effect_type in EFFECT_TYPES or effect_type == "all" else "all",
        "admin_only": admin_only if admin_only in ("all", "true", "false") else "all",
        "include_disabled": include_disabled == "true",
    }


def list_assignable_effect_rows(
    connection,
    guild_id: str,
    reaction_id: int,
    role: str,
    filters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    repository = SpecialEffectRepository(connection)
    tags = repository.list_tags(
        guild_id,
        query=filters["q"] or None,
        effect_type=None if filters["effect_type"] == "all" else filters["effect_type"],
        target_type=TARGET_TYPE,
        enabled=None if filters["include_disabled"] else True,
        admin_only=parse_bool(filters["admin_only"]),
    )
    assigned = {
        int(effect["id"]): bool(effect["assignment_enabled"])
        for effect in repository.list_for_target(guild_id, TARGET_TYPE, reaction_id, enabled=None)
    }
    rows = []
    for tag in tags:
        if not can_see_effect_tag(role, tag):
            continue
        row = build_effect_view(tag, role)
        row["assigned"] = assigned.get(int(tag["id"]), False)
        rows.append(row)
    return rows
