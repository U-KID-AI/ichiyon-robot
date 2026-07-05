import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from admin.ux import (
    ADDITIONAL_POST_TIMING_LABELS,
    COOLDOWN_SCOPE_LABELS,
    EFFECT_TYPE_LABELS,
    EXPIRES_TYPE_LABELS,
    TARGET_TYPE_LABELS,
    TRIGGER_TIMING_LABELS,
    is_test_data,
    parse_show_test_data,
)
from bot.db import get_connection
from bot.repositories import SpecialEffectRepository


router = APIRouter()

TARGET_TYPES = ("mention_reaction_choice", "auto_reaction", "ng_word")
TRIGGER_TIMINGS = ("choice_selected", "auto_reaction_triggered", "ng_word_detected")
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
    "mention_suffix_guard",
    "custom",
)
ADDITIONAL_POST_TIMINGS = ("none", "tag_triggered", "effect_success", "effect_end")
EXPIRES_TYPES = ("immediate", "next_bot_action", "next_special_roll", "seconds", "count", "permanent")
COOLDOWN_SCOPES = ("none", "guild", "channel", "user", "assigned_event")
COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


def register_special_effect_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/special-effects")
    async def special_effects_page(
        request: Request,
        guild_id: str,
        q: Optional[str] = Query(None),
        effect_type: str = Query("all"),
        enabled: str = Query("all"),
        admin_only: str = Query("all"),
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
        filters = normalize_filters(q, effect_type, enabled, admin_only, show_test_data)
        tags = list_tag_rows(guild_id, server["role"], filters)
        return templates.TemplateResponse(
            request,
            "special_effects.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "filters": filters,
                "tags": tags,
                "effect_types": EFFECT_TYPES,
                "effect_type_labels": EFFECT_TYPE_LABELS,
                "can_create": role_allows(server["role"], "editor"),
                "message": message,
                "error": error,
            },
        )

    @router.post("/guilds/{guild_id}/special-effects/bulk-enabled")
    async def bulk_set_special_effects_enabled(
        request: Request,
        guild_id: str,
        action: str = Form(""),
        tag_ids: List[int] = Form([]),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="special effect bulk update denied")
        # TODO(v3): bot_id権限を導入したら、guild権限だけでなくBot単位の操作権限も確認する。
        if not tag_ids:
            return RedirectResponse(
                url="/guilds/{0}/special-effects?error={1}".format(guild_id, quote("項目を選択してね")),
                status_code=303,
            )
        if action not in ("on", "off"):
            return RedirectResponse(
                url="/guilds/{0}/special-effects?error={1}".format(guild_id, quote("操作を選んでね")),
                status_code=303,
            )

        updated_count = 0
        with get_connection() as connection:
            repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
            for tag_id in tag_ids:
                tag = repository.get_by_id(guild_id, tag_id)
                if tag is None or not can_edit_tag(server["role"], tag):
                    continue
                if repository.set_enabled(guild_id, tag_id, action == "on") is not None:
                    updated_count += 1
            connection.commit()
        failed_count = max(0, len(tag_ids) - updated_count)
        return RedirectResponse(
            url="/guilds/{0}/special-effects?message={1}".format(
                guild_id,
                quote("成功{0}件 / 失敗{1}件".format(updated_count, failed_count)),
            ),
            status_code=303,
        )

    @router.post("/guilds/{guild_id}/special-effects/{tag_id}/toggle")
    async def toggle_special_effect(request: Request, guild_id: str, tag_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
            tag = repository.get_by_id(guild_id, tag_id)
            if tag is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="special effect tag not found")
            if not can_edit_tag(server["role"], tag):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="special effect toggle denied")
            repository.toggle_enabled(guild_id, tag_id)
            connection.commit()

        return RedirectResponse(url="/guilds/{0}/special-effects".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/special-effects/{tag_id}/copy")
    async def copy_special_effect(request: Request, guild_id: str, tag_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
            tag = repository.get_by_id(guild_id, tag_id)
            if tag is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="special effect tag not found")
            if not can_edit_tag(server["role"], tag):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="special effect copy denied")
            copied = repository.copy_tag(guild_id, tag_id)
            if copied is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="special effect tag not found")
            connection.commit()

        return RedirectResponse(
            url="/guilds/{0}/special-effects/{1}".format(guild_id, copied["id"]),
            status_code=303,
        )

    @router.post("/guilds/{guild_id}/special-effects/{tag_id}/delete")
    async def delete_special_effect(request: Request, guild_id: str, tag_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="サーバーを見る権限がありません。")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
            tag = repository.get_by_id(guild_id, tag_id)
            if tag is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="特殊効果タグが見つかりません。")
            if not can_edit_tag(server["role"], tag):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="削除する権限がありません。")
            if not tag.get("is_deletable", True):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="固定タグのため削除不可。")
            repository.delete_tag(guild_id, tag_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/special-effects".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/special-effects/new")
    async def new_special_effect_page(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="special effect creation denied")

        return render_form(
            templates,
            request,
            server,
            guild_id,
            "new",
            default_form(),
            [],
            role_allows(server["role"], "guild_admin"),
        )

    @router.post("/guilds/{guild_id}/special-effects/new")
    async def create_special_effect(
        request: Request,
        guild_id: str,
        name: str = Form(""),
        description: str = Form(""),
        color: str = Form("#6B7280"),
        enabled: Optional[str] = Form(None),
        admin_only: Optional[str] = Form(None),
        priority: str = Form("0"),
        target_type: str = Form("mention_reaction_choice"),
        trigger_timing: str = Form("choice_selected"),
        effect_type: str = Form("message"),
        effect_config_json: str = Form("{}"),
        additional_text: str = Form(""),
        additional_post_timing: str = Form("none"),
        expires_type: str = Form("permanent"),
        expires_value: str = Form(""),
        cooldown_seconds: str = Form("0"),
        cooldown_scope: str = Form("none"),
        max_multiplier: str = Form(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="special effect creation denied")

        form, errors = build_form(
            name,
            description,
            color,
            enabled,
            admin_only,
            priority,
            target_type,
            trigger_timing,
            effect_type,
            effect_config_json,
            additional_text,
            additional_post_timing,
            expires_type,
            expires_value,
            cooldown_seconds,
            cooldown_scope,
            max_multiplier,
        )
        if form["admin_only"] and not role_allows(server["role"], "guild_admin"):
            errors.append("管理者限定タグはサーバー管理者以上だけ作成可。")

        if errors:
            return render_form(
                templates,
                request,
                server,
                guild_id,
                "new",
                form,
                errors,
                role_allows(server["role"], "guild_admin"),
                status_code=400,
            )

        with get_connection() as connection:
            repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
            tag = save_new_tag(repository, guild_id, form, user["user_id"])
            connection.commit()

        return RedirectResponse(
            url="/guilds/{0}/special-effects/{1}".format(guild_id, tag["id"]),
            status_code=303,
        )

    @router.get("/guilds/{guild_id}/special-effects/{tag_id}")
    async def edit_special_effect_page(request: Request, guild_id: str, tag_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
            tag = repository.get_by_id(guild_id, tag_id)
            if tag is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="special effect tag not found")

        return render_form(
            templates,
            request,
            server,
            guild_id,
            "edit",
            build_form_from_tag(tag),
            [],
            role_allows(server["role"], "guild_admin"),
            tag_id=tag_id,
            can_edit=can_edit_tag(server["role"], tag),
        )

    @router.post("/guilds/{guild_id}/special-effects/{tag_id}")
    async def update_special_effect(
        request: Request,
        guild_id: str,
        tag_id: int,
        name: str = Form(""),
        description: str = Form(""),
        color: str = Form("#6B7280"),
        enabled: Optional[str] = Form(None),
        admin_only: Optional[str] = Form(None),
        priority: str = Form("0"),
        target_type: str = Form("mention_reaction_choice"),
        trigger_timing: str = Form("choice_selected"),
        effect_type: str = Form("message"),
        effect_config_json: str = Form("{}"),
        additional_text: str = Form(""),
        additional_post_timing: str = Form("none"),
        expires_type: str = Form("permanent"),
        expires_value: str = Form(""),
        cooldown_seconds: str = Form("0"),
        cooldown_scope: str = Form("none"),
        max_multiplier: str = Form(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
            tag = repository.get_by_id(guild_id, tag_id)
            if tag is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="special effect tag not found")
            if not can_edit_tag(server["role"], tag):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="special effect editing denied")

            form, errors = build_form(
                name,
                description,
                color,
                enabled,
                admin_only,
                priority,
                target_type,
                trigger_timing,
                effect_type,
                effect_config_json,
                additional_text,
                additional_post_timing,
                expires_type,
                expires_value,
                cooldown_seconds,
                cooldown_scope,
                max_multiplier,
            )
            if form["admin_only"] != bool(tag["admin_only"]) and not role_allows(server["role"], "guild_admin"):
                errors.append("管理者限定の変更はサーバー管理者以上だけ。")

            if errors:
                return render_form(
                    templates,
                    request,
                    server,
                    guild_id,
                    "edit",
                    form,
                    errors,
                    role_allows(server["role"], "guild_admin"),
                    tag_id=tag_id,
                    can_edit=True,
                    status_code=400,
                )

            save_existing_tag(repository, guild_id, tag_id, form, user["user_id"])
            connection.commit()

        return RedirectResponse(url="/guilds/{0}/special-effects/{1}".format(guild_id, tag_id), status_code=303)


def normalize_filters(
    q: Optional[str],
    effect_type: str,
    enabled: str,
    admin_only: str,
    show_test_data: str = "false",
) -> Dict[str, Any]:
    return {
        "q": (q or "").strip(),
        "effect_type": effect_type if effect_type in EFFECT_TYPES or effect_type == "all" else "all",
        "enabled": enabled if enabled in ("all", "true", "false") else "all",
        "admin_only": admin_only if admin_only in ("all", "true", "false") else "all",
        "show_test_data": parse_show_test_data(show_test_data),
    }


def list_tag_rows(guild_id: str, role: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = SpecialEffectRepository(connection, bot_id=current_selected_bot_id())
        tags = repository.list_tags(
            guild_id,
            query=filters["q"] or None,
            effect_type=None if filters["effect_type"] == "all" else filters["effect_type"],
            enabled=parse_bool(filters["enabled"]),
            admin_only=parse_bool(filters["admin_only"]),
        )

    rows = []
    for tag in tags:
        if not filters["show_test_data"] and row_is_hidden_test_data(tag):
            continue
        row = build_form_from_tag(tag)
        row["id"] = tag["id"]
        row["can_toggle"] = can_edit_tag(role, tag)
        row["can_delete"] = can_edit_tag(role, tag) and bool(tag.get("is_deletable", True))
        row["has_additional_text"] = bool(row["additional_text"].strip())
        row["effect_type_label"] = EFFECT_TYPE_LABELS.get(row["effect_type"], row["effect_type"])
        row["target_type_label"] = TARGET_TYPE_LABELS.get(row["target_type"], row["target_type"])
        row["trigger_timing_label"] = TRIGGER_TIMING_LABELS.get(row["trigger_timing"], row["trigger_timing"])
        row["additional_post_timing_label"] = ADDITIONAL_POST_TIMING_LABELS.get(
            row["additional_post_timing"],
            row["additional_post_timing"],
        )
        row["expires_type_label"] = EXPIRES_TYPE_LABELS.get(row["expires_type"], row["expires_type"])
        row["cooldown_scope_label"] = COOLDOWN_SCOPE_LABELS.get(row["cooldown_scope"], row["cooldown_scope"])
        row["edit_url"] = "/guilds/{0}/special-effects/{1}".format(guild_id, tag["id"])
        row["toggle_url"] = "/guilds/{0}/special-effects/{1}/toggle".format(guild_id, tag["id"])
        row["copy_url"] = "/guilds/{0}/special-effects/{1}/copy".format(guild_id, tag["id"])
        row["delete_url"] = "/guilds/{0}/special-effects/{1}/delete".format(guild_id, tag["id"])
        rows.append(row)
    return rows


def row_is_hidden_test_data(row: Dict[str, Any]) -> bool:
    return is_test_data(row.get("name")) or is_test_data(row.get("description"))


def parse_bool(value: str) -> Optional[bool]:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def can_edit_tag(role: str, tag: Dict[str, Any]) -> bool:
    if tag.get("admin_only"):
        return role_allows(role, "guild_admin")
    return role_allows(role, "editor")


def default_form() -> Dict[str, Any]:
    return {
        "id": None,
        "name": "",
        "description": "",
        "color": "#6B7280",
        "enabled": True,
        "admin_only": False,
        "priority": 0,
        "target_type": "mention_reaction_choice",
        "trigger_timing": "choice_selected",
        "effect_type": "message",
        "effect_config_json": "{}",
        "effect_config_summary": "{}",
        "additional_text": "",
        "additional_post_timing": "none",
        "expires_type": "permanent",
        "expires_value": "",
        "cooldown_seconds": 0,
        "cooldown_scope": "none",
        "max_multiplier": "",
        "max_multiplier_label": "制限なし",
    }


def build_form_from_tag(tag: Dict[str, Any]) -> Dict[str, Any]:
    config = tag.get("effect_config_json") or {}
    if not isinstance(config, str):
        config_text = json.dumps(config, ensure_ascii=False, indent=2)
    else:
        config_text = config
    form = default_form()
    form.update(
        {
            "id": tag.get("id"),
            "name": tag.get("name") or "",
            "description": tag.get("description") or "",
            "color": tag.get("color") or "#6B7280",
            "enabled": bool(tag.get("enabled")),
            "admin_only": bool(tag.get("admin_only")),
            "priority": int(tag.get("priority") or 0),
            "target_type": tag.get("target_type") or "mention_reaction_choice",
            "trigger_timing": tag.get("trigger_timing") or "choice_selected",
            "effect_type": tag.get("effect_type") or "message",
            "effect_config_json": config_text,
            "effect_config_summary": compact_json(config_text),
            "additional_text": tag.get("additional_text") or "",
            "additional_post_timing": tag.get("additional_post_timing") or "none",
            "expires_type": tag.get("expires_type") or "permanent",
            "expires_value": "" if tag.get("expires_value") is None else str(tag.get("expires_value")),
            "cooldown_seconds": int(tag.get("cooldown_seconds") or 0),
            "cooldown_scope": tag.get("cooldown_scope") or "none",
            "max_multiplier": "" if tag.get("max_multiplier") is None else str(tag.get("max_multiplier")),
            "max_multiplier_label": format_max_multiplier(tag.get("max_multiplier")),
        }
    )
    return form


def build_form(
    name: str,
    description: str,
    color: str,
    enabled: Optional[str],
    admin_only: Optional[str],
    priority: str,
    target_type: str,
    trigger_timing: str,
    effect_type: str,
    effect_config_json: str,
    additional_text: str,
    additional_post_timing: str,
    expires_type: str,
    expires_value: str,
    cooldown_seconds: str,
    cooldown_scope: str,
    max_multiplier: str,
) -> Tuple[Dict[str, Any], List[str]]:
    errors = []
    form = default_form()
    form.update(
        {
            "name": name.strip(),
            "description": description.strip(),
            "color": color.strip() or "#6B7280",
            "enabled": enabled == "on",
            "admin_only": admin_only == "on",
            "target_type": target_type,
            "trigger_timing": trigger_timing,
            "effect_type": effect_type,
            "effect_config_json": effect_config_json.strip() or "{}",
            "additional_text": additional_text,
            "additional_post_timing": additional_post_timing,
            "expires_type": expires_type,
            "cooldown_scope": cooldown_scope,
            "max_multiplier": max_multiplier.strip(),
        }
    )
    form["priority"] = parse_int(priority, 0)
    form["expires_value"] = "" if expires_value.strip() == "" else parse_int(expires_value, -1)
    form["cooldown_seconds"] = parse_int(cooldown_seconds, 0)

    if not form["name"]:
        errors.append("タグ名を入力。")
    if not COLOR_PATTERN.match(form["color"]):
        errors.append("タグ色は #RRGGBB 形式。")
    if form["target_type"] not in TARGET_TYPES:
        errors.append("付与できる対象を選択。")
    if form["trigger_timing"] not in TRIGGER_TIMINGS:
        errors.append("発動タイミングを選択。")
    if form["effect_type"] not in EFFECT_TYPES:
        errors.append("効果の種類を選択。")
    if form["additional_post_timing"] not in ADDITIONAL_POST_TIMINGS:
        errors.append("追加投稿タイミングを選択。")
    if form["expires_type"] not in EXPIRES_TYPES:
        errors.append("有効期限タイプを選択。")
    if isinstance(form["expires_value"], int) and form["expires_value"] < 0:
        errors.append("有効期限値は0以上の整数。")
    if form["cooldown_seconds"] < 0:
        errors.append("クールタイム秒数は0以上の整数。")
    if form["cooldown_scope"] not in COOLDOWN_SCOPES:
        errors.append("クールタイム単位を選択。")
    if form["cooldown_scope"] == "none":
        form["cooldown_seconds"] = 0
    if form["max_multiplier"]:
        try:
            parsed_multiplier = float(form["max_multiplier"])
        except ValueError:
            parsed_multiplier = 0.0
        if parsed_multiplier <= 0:
            errors.append("最大倍率は0より大きい数値。")
        else:
            form["max_multiplier"] = parsed_multiplier
            form["max_multiplier_label"] = format_max_multiplier(parsed_multiplier)
    else:
        form["max_multiplier"] = None
        form["max_multiplier_label"] = "制限なし"

    try:
        parsed_json = json.loads(form["effect_config_json"])
        if not isinstance(parsed_json, dict):
            errors.append("詳細設定はオブジェクト形式。")
        else:
            form["effect_config"] = parsed_json
            form["effect_config_summary"] = compact_json(form["effect_config_json"])
    except json.JSONDecodeError:
        errors.append("詳細設定のJSONが不正。")
        form["effect_config"] = {}
        form["effect_config_summary"] = form["effect_config_json"]

    return form, errors


def parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def compact_json(value: str) -> str:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value or "{}"
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def format_max_multiplier(value: Any) -> str:
    if value in (None, ""):
        return "制限なし"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "制限なし"
    if number <= 0:
        return "制限なし"
    if number.is_integer():
        return "{0}倍".format(int(number))
    return "{0:g}倍".format(number)


def save_new_tag(
    repository: SpecialEffectRepository,
    guild_id: str,
    form: Dict[str, Any],
    updated_by: str,
) -> Dict[str, Any]:
    return repository.create_tag(
        guild_id,
        form["name"],
        form["description"],
        form["color"],
        form["admin_only"],
        form["enabled"],
        form["priority"],
        form["target_type"],
        form["trigger_timing"],
        form["effect_type"],
        form["effect_config"],
        form["additional_text"],
        form["additional_post_timing"],
        form["expires_type"],
        None if form["expires_value"] == "" else form["expires_value"],
        form["cooldown_seconds"],
        form["cooldown_scope"],
        form["max_multiplier"],
        updated_by,
    )


def save_existing_tag(
    repository: SpecialEffectRepository,
    guild_id: str,
    tag_id: int,
    form: Dict[str, Any],
    updated_by: str,
):
    return repository.update_tag(
        guild_id,
        tag_id,
        form["name"],
        form["description"],
        form["color"],
        form["admin_only"],
        form["enabled"],
        form["priority"],
        form["target_type"],
        form["trigger_timing"],
        form["effect_type"],
        form["effect_config"],
        form["additional_text"],
        form["additional_post_timing"],
        form["expires_type"],
        None if form["expires_value"] == "" else form["expires_value"],
        form["cooldown_seconds"],
        form["cooldown_scope"],
        form["max_multiplier"],
        updated_by,
    )


def render_form(
    templates: Jinja2Templates,
    request: Request,
    server: Dict[str, Any],
    guild_id: str,
    mode: str,
    form: Dict[str, Any],
    errors: List[str],
    can_set_admin_only: bool,
    tag_id: Optional[int] = None,
    can_edit: bool = True,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "special_effect_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "mode": mode,
            "tag_id": tag_id,
            "tag": form,
            "errors": errors,
            "can_edit": can_edit,
            "can_set_admin_only": can_set_admin_only,
            "target_types": TARGET_TYPES,
            "trigger_timings": TRIGGER_TIMINGS,
            "effect_types": EFFECT_TYPES,
            "effect_type_labels": EFFECT_TYPE_LABELS,
            "target_type_labels": TARGET_TYPE_LABELS,
            "trigger_timing_labels": TRIGGER_TIMING_LABELS,
            "additional_post_timing_labels": ADDITIONAL_POST_TIMING_LABELS,
            "expires_type_labels": EXPIRES_TYPE_LABELS,
            "cooldown_scope_labels": COOLDOWN_SCOPE_LABELS,
            "additional_post_timings": ADDITIONAL_POST_TIMINGS,
            "expires_types": EXPIRES_TYPES,
            "cooldown_scopes": COOLDOWN_SCOPES,
        },
        status_code=status_code,
    )
