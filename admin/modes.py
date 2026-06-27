import json
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.servers import can_access_guild, find_server, role_allows
from admin.ux import (
    BEHAVIOR_LABELS,
    CONDITION_TYPE_LABELS,
    COOLDOWN_PERIOD_LABELS,
    COOLDOWN_RESET_LABELS,
    COOLDOWN_TYPE_LABELS,
    RESET_TYPE_LABELS,
    is_test_data,
    parse_show_test_data,
    save_uploaded_image,
)
from bot.db import get_connection
from bot.repositories import CounterRepository, ModeRepository


router = APIRouter()

BEHAVIOR_TYPES = ("reply", "offline")
COOLDOWN_TYPES = ("none", "duration", "once_per_period")
COOLDOWN_PERIODS = ("none", "monthly")
COOLDOWN_RESETS = ("none", "month_start", "day")
TRIGGER_TYPES = ("probability", "counter_threshold", "period_not_triggered", "manual", "schedule")
EXIT_TYPES = ("duration", "manual")
RESET_TYPES = ("none", "daily", "monthly", "monthly_day", "manual")


def register_mode_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/modes")
    async def modes_page(
        request: Request,
        guild_id: str,
        q: Optional[str] = Query(None),
        enabled: str = Query("all"),
        behavior_type: str = Query("all"),
        admin_only: str = Query("all"),
        show_test_data: str = Query("false"),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        filters = normalize_filters(q, enabled, behavior_type, admin_only, show_test_data)
        modes = list_mode_rows(guild_id, server["role"], filters)
        return templates.TemplateResponse(
            request,
            "modes.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "filters": filters,
                "modes": modes,
                "can_create": role_allows(server["role"], "editor"),
            },
        )

    @router.post("/guilds/{guild_id}/modes/{mode_id}/toggle")
    async def toggle_mode(request: Request, guild_id: str, mode_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            repository = ModeRepository(connection)
            mode = repository.get_by_id(guild_id, mode_id)
            if mode is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mode not found")
            if not can_edit_mode(server["role"], mode):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mode toggle denied")
            repository.toggle_enabled(guild_id, mode_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/modes".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/modes/{mode_id}/delete")
    async def delete_mode(request: Request, guild_id: str, mode_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="サーバーを見る権限がありません。")
        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            repository = ModeRepository(connection)
            mode = repository.get_by_id(guild_id, mode_id)
            if mode is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="モードが見つかりません。")
            if not can_edit_mode(server["role"], mode):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="削除する権限がありません。")
            if not mode.get("is_deletable", True):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="固定モードのため削除不可。")
            repository.delete_mode(guild_id, mode_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/modes".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/modes/new")
    async def new_mode_page(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mode creation denied")
        return render_form(
            templates,
            request,
            server,
            guild_id,
            "new",
            default_mode_form(),
            [],
            True,
            role_allows(server["role"], "guild_admin"),
        )

    @router.post("/guilds/{guild_id}/modes/new")
    async def create_mode(
        request: Request,
        guild_id: str,
        mode_key: str = Form(""),
        name: str = Form(""),
        description: str = Form(""),
        behavior_type: str = Form("reply"),
        enabled: Optional[str] = Form(None),
        admin_only: Optional[str] = Form(None),
        is_deletable: Optional[str] = Form(None),
        mode_nickname: str = Form(""),
        mode_icon_path: str = Form(""),
        mode_icon_upload: Optional[UploadFile] = File(None),
        delete_mode_icon: Optional[str] = Form(None),
        enter_message: str = Form(""),
        exit_message: str = Form(""),
        enter_gif_path: str = Form(""),
        enter_gif_upload: Optional[UploadFile] = File(None),
        delete_enter_gif: Optional[str] = Form(None),
        exit_gif_path: str = Form(""),
        exit_gif_upload: Optional[UploadFile] = File(None),
        delete_exit_gif: Optional[str] = Form(None),
        enter_notify_channel_id: str = Form(""),
        exit_notify_channel_id: str = Form(""),
        reaction_channel_ids: str = Form(""),
        ignore_channel_ids: str = Form(""),
        cooldown_type: str = Form("none"),
        cooldown_seconds: str = Form("0"),
        cooldown_period: str = Form("none"),
        cooldown_reset: str = Form("none"),
        cooldown_day: str = Form(""),
    ):
        values = locals()
        values.pop("request")
        values.pop("guild_id")
        return await save_mode(request, templates, guild_id, None, values)

    @router.get("/guilds/{guild_id}/modes/{mode_id}")
    async def edit_mode_page(request: Request, guild_id: str, mode_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            repository = ModeRepository(connection)
            mode = repository.get_by_id(guild_id, mode_id)
            if mode is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mode not found")
            form = build_mode_view(connection, guild_id, mode)
            counters = CounterRepository(connection).list_counters(guild_id)
        return render_form(
            templates,
            request,
            server,
            guild_id,
            "edit",
            form,
            [],
            can_edit_mode(server["role"], mode),
            role_allows(server["role"], "guild_admin"),
            mode_id=mode_id,
            counters=counters,
        )

    @router.post("/guilds/{guild_id}/modes/{mode_id}")
    async def update_mode(
        request: Request,
        guild_id: str,
        mode_id: int,
        mode_key: str = Form(""),
        name: str = Form(""),
        description: str = Form(""),
        behavior_type: str = Form("reply"),
        enabled: Optional[str] = Form(None),
        admin_only: Optional[str] = Form(None),
        is_deletable: Optional[str] = Form(None),
        mode_nickname: str = Form(""),
        mode_icon_path: str = Form(""),
        mode_icon_upload: Optional[UploadFile] = File(None),
        delete_mode_icon: Optional[str] = Form(None),
        enter_message: str = Form(""),
        exit_message: str = Form(""),
        enter_gif_path: str = Form(""),
        enter_gif_upload: Optional[UploadFile] = File(None),
        delete_enter_gif: Optional[str] = Form(None),
        exit_gif_path: str = Form(""),
        exit_gif_upload: Optional[UploadFile] = File(None),
        delete_exit_gif: Optional[str] = Form(None),
        enter_notify_channel_id: str = Form(""),
        exit_notify_channel_id: str = Form(""),
        reaction_channel_ids: str = Form(""),
        ignore_channel_ids: str = Form(""),
        cooldown_type: str = Form("none"),
        cooldown_seconds: str = Form("0"),
        cooldown_period: str = Form("none"),
        cooldown_reset: str = Form("none"),
        cooldown_day: str = Form(""),
    ):
        values = locals()
        values.pop("request")
        values.pop("guild_id")
        values.pop("mode_id")
        return await save_mode(request, templates, guild_id, mode_id, values)

    @router.post("/guilds/{guild_id}/modes/{mode_id}/reply-choices")
    async def create_reply_choice(
        request: Request,
        guild_id: str,
        mode_id: int,
        choice_name: str = Form(""),
        body: str = Form(""),
        image_path: str = Form(""),
        image_upload: Optional[UploadFile] = File(None),
        delete_image: Optional[str] = Form(None),
        appearance_rate: str = Form("1"),
        choice_enabled: Optional[str] = Form(None),
    ):
        return await save_reply_choice(
            request,
            guild_id,
            mode_id,
            None,
            choice_name,
            body,
            image_path,
            image_upload,
            delete_image,
            appearance_rate,
            choice_enabled,
        )

    @router.post("/guilds/{guild_id}/modes/{mode_id}/reply-choices/{choice_id}")
    async def update_reply_choice(
        request: Request,
        guild_id: str,
        mode_id: int,
        choice_id: int,
        choice_name: str = Form(""),
        body: str = Form(""),
        image_path: str = Form(""),
        image_upload: Optional[UploadFile] = File(None),
        delete_image: Optional[str] = Form(None),
        appearance_rate: str = Form("1"),
        choice_enabled: Optional[str] = Form(None),
    ):
        return await save_reply_choice(
            request,
            guild_id,
            mode_id,
            choice_id,
            choice_name,
            body,
            image_path,
            image_upload,
            delete_image,
            appearance_rate,
            choice_enabled,
        )

    @router.post("/guilds/{guild_id}/modes/{mode_id}/reply-choices/{choice_id}/delete")
    async def delete_reply_choice(request: Request, guild_id: str, mode_id: int, choice_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="サーバーを見る権限がありません。")
        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            repository = ModeRepository(connection)
            mode = repository.get_by_id(guild_id, mode_id)
            if mode is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="モードが見つかりません。")
            if not can_edit_mode(server["role"], mode):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="削除する権限がありません。")
            choice = repository.get_reply_choice(guild_id, choice_id)
            if choice is None or int(choice["mode_id"]) != mode_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="返答候補が見つかりません。")
            repository.delete_reply_choice(guild_id, choice_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/modes/{1}".format(guild_id, mode_id), status_code=303)

    @router.post("/guilds/{guild_id}/modes/{mode_id}/trigger-conditions")
    async def create_trigger_condition(
        request: Request,
        guild_id: str,
        mode_id: int,
        condition_type: str = Form("manual"),
        group_operator: str = Form("AND"),
        condition_config_json: str = Form("{}"),
        condition_enabled: Optional[str] = Form(None),
        new_count_key: str = Form(""),
        new_count_name: str = Form(""),
        new_count_description: str = Form(""),
        new_count_initial_value: str = Form("0"),
        new_count_reset_type: str = Form("none"),
    ):
        return await save_trigger_condition(
            request,
            guild_id,
            mode_id,
            None,
            condition_type,
            group_operator,
            condition_config_json,
            condition_enabled,
            new_count_key,
            new_count_name,
            new_count_description,
            new_count_initial_value,
            new_count_reset_type,
        )

    @router.post("/guilds/{guild_id}/modes/{mode_id}/trigger-conditions/{condition_id}")
    async def update_trigger_condition(
        request: Request,
        guild_id: str,
        mode_id: int,
        condition_id: int,
        condition_type: str = Form("manual"),
        group_operator: str = Form("AND"),
        condition_config_json: str = Form("{}"),
        condition_enabled: Optional[str] = Form(None),
        new_count_key: str = Form(""),
        new_count_name: str = Form(""),
        new_count_description: str = Form(""),
        new_count_initial_value: str = Form("0"),
        new_count_reset_type: str = Form("none"),
    ):
        return await save_trigger_condition(
            request,
            guild_id,
            mode_id,
            condition_id,
            condition_type,
            group_operator,
            condition_config_json,
            condition_enabled,
            new_count_key,
            new_count_name,
            new_count_description,
            new_count_initial_value,
            new_count_reset_type,
        )

    @router.post("/guilds/{guild_id}/modes/{mode_id}/exit-conditions")
    async def create_exit_condition(
        request: Request,
        guild_id: str,
        mode_id: int,
        condition_type: str = Form("duration"),
        condition_config_json: str = Form("{}"),
        condition_enabled: Optional[str] = Form(None),
    ):
        return await save_exit_condition(
            request,
            guild_id,
            mode_id,
            None,
            condition_type,
            condition_config_json,
            condition_enabled,
        )

    @router.post("/guilds/{guild_id}/modes/{mode_id}/exit-conditions/{condition_id}")
    async def update_exit_condition(
        request: Request,
        guild_id: str,
        mode_id: int,
        condition_id: int,
        condition_type: str = Form("duration"),
        condition_config_json: str = Form("{}"),
        condition_enabled: Optional[str] = Form(None),
    ):
        return await save_exit_condition(
            request,
            guild_id,
            mode_id,
            condition_id,
            condition_type,
            condition_config_json,
            condition_enabled,
        )


def normalize_filters(
    q: Optional[str],
    enabled: str,
    behavior_type: str,
    admin_only: str,
    show_test_data: str = "false",
) -> Dict[str, Any]:
    return {
        "q": (q or "").strip(),
        "enabled": enabled if enabled in ("all", "true", "false") else "all",
        "behavior_type": behavior_type if behavior_type in ("all", "reply", "offline") else "all",
        "admin_only": admin_only if admin_only in ("all", "true", "false") else "all",
        "show_test_data": parse_show_test_data(show_test_data),
    }


def parse_bool(value: str) -> Optional[bool]:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def can_edit_mode(role: str, mode: Dict[str, Any]) -> bool:
    if mode.get("admin_only"):
        return role_allows(role, "guild_admin")
    return role_allows(role, "editor")


def list_mode_rows(guild_id: str, role: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = ModeRepository(connection)
        modes = repository.list_modes(
            guild_id,
            query=filters["q"] or None,
            enabled=parse_bool(filters["enabled"]),
            behavior_type=None if filters["behavior_type"] == "all" else filters["behavior_type"],
            admin_only=parse_bool(filters["admin_only"]),
        )
        return [
            build_mode_view(connection, guild_id, mode, include_children=False, role=role)
            for mode in modes
            if filters["show_test_data"] or not row_is_hidden_test_data(mode)
        ]


def row_is_hidden_test_data(row: Dict[str, Any]) -> bool:
    return is_test_data(row.get("mode_key")) or is_test_data(row.get("name")) or is_test_data(row.get("description"))


def build_mode_view(
    connection,
    guild_id: str,
    mode: Dict[str, Any],
    include_children: bool = True,
    role: str = "viewer",
) -> Dict[str, Any]:
    repository = ModeRepository(connection)
    row = default_mode_form()
    row.update(
        {
            "id": mode.get("id"),
            "mode_key": mode.get("mode_key") or "",
            "name": mode.get("name") or "",
            "description": mode.get("description") or "",
            "behavior_type": mode.get("behavior_type") or "reply",
            "mode_nickname": get_mode_nickname_from_config(mode),
            "mode_icon_path": mode.get("mode_icon_path") or "",
            "enter_message": mode.get("enter_message") or "",
            "exit_message": mode.get("exit_message") or "",
            "enter_gif_path": mode.get("enter_gif_path") or "",
            "exit_gif_path": mode.get("exit_gif_path") or "",
            "enter_notify_channel_id": mode.get("enter_notify_channel_id") or "",
            "exit_notify_channel_id": mode.get("exit_notify_channel_id") or "",
            "reaction_channel_ids": join_json_list(mode.get("reaction_channel_ids")),
            "ignore_channel_ids": join_json_list(mode.get("ignore_channel_ids")),
            "enabled": bool(mode.get("enabled")),
            "admin_only": bool(mode.get("admin_only")),
            "is_deletable": bool(mode.get("is_deletable", True)),
        }
    )

    cooldown = mode.get("cooldown_config_json") or {}
    if isinstance(cooldown, str):
        try:
            cooldown = json.loads(cooldown)
        except json.JSONDecodeError:
            cooldown = {}
    row.update(
        {
            "cooldown_type": cooldown.get("type", "none"),
            "cooldown_seconds": cooldown.get("seconds", 0),
            "cooldown_period": cooldown.get("period", "none"),
            "cooldown_reset": cooldown.get("reset", "none"),
            "cooldown_day": "" if cooldown.get("day") is None else cooldown.get("day"),
            "cooldown_summary": summarize_cooldown(cooldown),
            "can_toggle": can_edit_mode(role, mode),
            "can_delete": can_edit_mode(role, mode) and bool(mode.get("is_deletable", True)),
            "behavior_type_label": BEHAVIOR_LABELS.get(mode.get("behavior_type") or "reply", mode.get("behavior_type") or "reply"),
            "edit_url": "/guilds/{0}/modes/{1}".format(guild_id, mode.get("id")),
            "toggle_url": "/guilds/{0}/modes/{1}/toggle".format(guild_id, mode.get("id")),
            "delete_url": "/guilds/{0}/modes/{1}/delete".format(guild_id, mode.get("id")),
        }
    )

    replies = repository.list_reply_choices(guild_id, int(mode["id"]))
    triggers = repository.list_trigger_conditions(guild_id, int(mode["id"]))
    exits = repository.list_exit_conditions(guild_id, int(mode["id"]))
    row["reply_count"] = len(replies)
    row["trigger_count"] = len(triggers)
    row["exit_count"] = len(exits)
    if include_children:
        row["reply_choices"] = replies
        row["trigger_conditions"] = [format_condition(item) for item in triggers]
        row["exit_conditions"] = [format_condition(item) for item in exits]
        row["state"] = repository.get_mode_state(guild_id)
    return row


def join_json_list(value) -> str:
    if isinstance(value, list):
        return ", ".join([str(item) for item in value])
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return ", ".join([str(item) for item in parsed])
        except json.JSONDecodeError:
            return value
    return ""


def split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def summarize_cooldown(config: Dict[str, Any]) -> str:
    if not config or config.get("type", "none") == "none":
        return "なし"
    if config.get("type") == "duration":
        return "{0}秒".format(config.get("seconds", 0))
    if config.get("type") == "once_per_period":
        period = COOLDOWN_PERIOD_LABELS.get(config.get("period"), config.get("period"))
        if config.get("reset") == "day":
            return "期間内1回 / {0} / 毎月{1}日リセット".format(
                period,
                config.get("day"),
            )
        
        reset_value = config.get("reset")
        if isinstance(reset_value, dict):
            reset_key = reset_value.get("type") or reset_value.get("unit") or reset_value.get("value")
            reset_detail = reset_value.get("day") or reset_value.get("at")
        else:
            reset_key = reset_value
            reset_detail = None
    
        if isinstance(reset_key, (dict, list)):
            reset_key = ""
            
        reset = COOLDOWN_RESET_LABELS.get(reset_key, reset_key or "")
        if reset_detail:
            reset = "{0} {1}".format(reset, reset_detail)
            
        return "期間内1回 / {0} / {1}リセット".format(period, reset)


def format_condition(item: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(item)
    config = row.get("condition_config_json") or {}
    if not isinstance(config, str):
        row["condition_config_text"] = json.dumps(config, ensure_ascii=False, sort_keys=True)
    else:
        row["condition_config_text"] = config
    if row.get("condition_type") == "duration_elapsed":
        row["ui_condition_type"] = "duration"
    else:
        row["ui_condition_type"] = row.get("condition_type")
    row["condition_type_label"] = CONDITION_TYPE_LABELS.get(row["ui_condition_type"], row["ui_condition_type"])
    return row


def default_mode_form() -> Dict[str, Any]:
    return {
        "id": None,
        "mode_key": "",
        "name": "",
        "description": "",
        "behavior_type": "reply",
        "enabled": True,
        "admin_only": False,
        "is_deletable": True,
        "mode_nickname": "",
        "mode_icon_path": "",
        "enter_message": "",
        "exit_message": "",
        "enter_gif_path": "",
        "exit_gif_path": "",
        "enter_notify_channel_id": "",
        "exit_notify_channel_id": "",
        "reaction_channel_ids": "",
        "ignore_channel_ids": "",
        "cooldown_type": "none",
        "cooldown_seconds": 0,
        "cooldown_period": "none",
        "cooldown_reset": "none",
        "cooldown_day": "",
        "cooldown_summary": "none",
        "reply_choices": [],
        "trigger_conditions": [],
        "exit_conditions": [],
        "state": None,
    }


def build_mode_form(values: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    form = default_mode_form()
    for key in form.keys():
        if key in values:
            form[key] = values[key]
    form["mode_key"] = str(values.get("mode_key", "")).strip()
    form["name"] = str(values.get("name", "")).strip()
    form["description"] = str(values.get("description", "")).strip()
    form["behavior_type"] = values.get("behavior_type") if values.get("behavior_type") in BEHAVIOR_TYPES else "reply"
    form["enabled"] = values.get("enabled") == "on"
    form["admin_only"] = values.get("admin_only") == "on"
    form["is_deletable"] = values.get("is_deletable") == "on"
    for key in (
        "mode_icon_path",
        "mode_nickname",
        "enter_message",
        "exit_message",
        "enter_gif_path",
        "exit_gif_path",
        "enter_notify_channel_id",
        "exit_notify_channel_id",
        "reaction_channel_ids",
        "ignore_channel_ids",
    ):
        form[key] = str(values.get(key, "")).strip()

    cooldown, cooldown_errors = build_cooldown(values)
    form.update(
        {
            "cooldown_type": cooldown.get("type", "none"),
            "cooldown_seconds": cooldown.get("seconds", 0),
            "cooldown_period": cooldown.get("period", "none"),
            "cooldown_reset": cooldown.get("reset", "none"),
            "cooldown_day": "" if cooldown.get("day") is None else cooldown.get("day"),
            "cooldown_summary": summarize_cooldown(cooldown),
        }
    )

    errors = list(cooldown_errors)
    if not form["mode_key"]:
        errors.append("モードキーを入力。")
    if not form["name"]:
        errors.append("モード名を入力。")
    return form, errors, cooldown


def normalize_json_dict(value) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def get_mode_nickname_from_config(mode: Dict[str, Any]) -> str:
    appearance = normalize_json_dict(mode.get("appearance_config_json"))
    for key in ("nickname", "bot_nickname", "display_name"):
        value = appearance.get(key)
        if value:
            return str(value)
    return ""


def build_appearance_config(existing: Optional[Dict[str, Any]], mode_nickname: str) -> Dict[str, Any]:
    appearance = normalize_json_dict((existing or {}).get("appearance_config_json"))
    nickname = mode_nickname.strip()
    if nickname:
        appearance["nickname"] = nickname
    else:
        appearance.pop("nickname", None)
    return appearance


def build_cooldown(values: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    cooldown_type = values.get("cooldown_type") if values.get("cooldown_type") in COOLDOWN_TYPES else "none"
    errors = []
    config = {"type": cooldown_type}
    if cooldown_type == "duration":
        seconds = parse_int(values.get("cooldown_seconds"), 0)
        if seconds <= 0:
            errors.append("時間指定のクールタイムは1秒以上。")
        config["seconds"] = seconds
    elif cooldown_type == "once_per_period":
        config["period"] = values.get("cooldown_period") if values.get("cooldown_period") in COOLDOWN_PERIODS else "monthly"
        config["reset"] = values.get("cooldown_reset") if values.get("cooldown_reset") in COOLDOWN_RESETS else "month_start"
        if config["reset"] == "day":
            day = parse_int(values.get("cooldown_day"), 0)
            if day < 1 or day > 31:
                errors.append("リセット日は1〜31。")
            config["day"] = day
    return config, errors


def parse_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def save_mode(
    request: Request,
    templates: Jinja2Templates,
    guild_id: str,
    mode_id: Optional[int],
    values: Dict[str, Any],
):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not can_access_guild(guild_id, user["user_id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

    server = find_server(guild_id, user["user_id"])
    upload_errors = []
    if values.get("delete_mode_icon"):
        values["mode_icon_path"] = ""
    if values.get("delete_enter_gif"):
        values["enter_gif_path"] = ""
    if values.get("delete_exit_gif"):
        values["exit_gif_path"] = ""
    for upload_key, path_key, category in (
        ("mode_icon_upload", "mode_icon_path", "mode_icons"),
        ("enter_gif_upload", "enter_gif_path", "mode_gifs"),
        ("exit_gif_upload", "exit_gif_path", "mode_gifs"),
    ):
        uploaded_path, upload_error = await save_uploaded_image(values.get(upload_key), category)
        if uploaded_path:
            values[path_key] = uploaded_path
        if upload_error:
            upload_errors.append(upload_error)
    form, errors, cooldown = build_mode_form(values)
    errors.extend(upload_errors)
    with get_connection() as connection:
        repository = ModeRepository(connection)
        existing = repository.get_by_id(guild_id, mode_id) if mode_id is not None else None
        if mode_id is not None and existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mode not found")
        if existing is not None and not can_edit_mode(server["role"], existing):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mode editing denied")
        if mode_id is None and not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mode creation denied")
        if form["admin_only"] and not role_allows(server["role"], "guild_admin"):
            errors.append("管理者限定モードはサーバー管理者以上だけ保存可。")
        if not errors and repository.mode_key_exists(guild_id, form["mode_key"], mode_id):
            errors.append("このモードキーは同じサーバーで使用済み。")

        if errors:
            counters = CounterRepository(connection).list_counters(guild_id)
            return render_form(
                templates,
                request,
                server,
                guild_id,
                "new" if mode_id is None else "edit",
                form,
                errors,
                True,
                role_allows(server["role"], "guild_admin"),
                mode_id=mode_id,
                counters=counters,
                status_code=400,
            )

        if mode_id is None:
            appearance_config = build_appearance_config(existing, form["mode_nickname"])
            created = repository.create_mode(
                guild_id,
                form["mode_key"],
                form["name"],
                form["description"],
                form["behavior_type"],
                form["mode_icon_path"],
                form["enter_message"],
                form["exit_message"],
                form["enter_gif_path"],
                form["exit_gif_path"],
                form["enter_notify_channel_id"],
                form["exit_notify_channel_id"],
                split_csv(form["reaction_channel_ids"]),
                split_csv(form["ignore_channel_ids"]),
                cooldown,
                appearance_config,
                form["enabled"],
                form["admin_only"],
                form["is_deletable"],
            )
            connection.commit()
            return RedirectResponse(url="/guilds/{0}/modes/{1}".format(guild_id, created["id"]), status_code=303)

        appearance_config = build_appearance_config(existing, form["mode_nickname"])
        repository.update_mode(
            guild_id,
            mode_id,
            form["mode_key"],
            form["name"],
            form["description"],
            form["behavior_type"],
            form["mode_icon_path"],
            form["enter_message"],
            form["exit_message"],
            form["enter_gif_path"],
            form["exit_gif_path"],
            form["enter_notify_channel_id"],
            form["exit_notify_channel_id"],
            split_csv(form["reaction_channel_ids"]),
            split_csv(form["ignore_channel_ids"]),
            cooldown,
            appearance_config,
            form["enabled"],
            form["admin_only"],
            form["is_deletable"],
        )
        connection.commit()
    return RedirectResponse(url="/guilds/{0}/modes/{1}".format(guild_id, mode_id), status_code=303)


def get_edit_context(request: Request, guild_id: str, mode_id: int) -> Tuple[Dict[str, Any], Any, Any]:
    user = get_current_user(request)
    if user is None:
        return {}, None, None
    if not can_access_guild(guild_id, user["user_id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

    server = find_server(guild_id, user["user_id"])
    context = get_connection()
    connection = context.__enter__()
    repository = ModeRepository(connection)
    mode = repository.get_by_id(guild_id, mode_id)
    if mode is None:
        context.__exit__(None, None, None)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mode not found")
    if not can_edit_mode(server["role"], mode):
        context.__exit__(None, None, None)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mode editing denied")
    return server, connection, context


async def save_reply_choice(
    request: Request,
    guild_id: str,
    mode_id: int,
    choice_id: Optional[int],
    name: str,
    body: str,
    image_path: str,
    image_upload: Optional[UploadFile],
    delete_image: Optional[str],
    appearance_rate: str,
    enabled: Optional[str],
):
    server, connection, context = get_edit_context(request, guild_id, mode_id)
    if connection is None:
        return RedirectResponse(url="/login", status_code=303)
    try:
        if delete_image:
            image_path = ""
        uploaded_path, upload_error = await save_uploaded_image(image_upload, "mode_reply_choices")
        if upload_error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=upload_error)
        if uploaded_path:
            image_path = uploaded_path
        if not name.strip() or (not body.strip() and not image_path.strip()):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid reply choice")
        repository = ModeRepository(connection)
        rate = max(parse_int(appearance_rate, 1), 1)
        if choice_id is None:
            repository.create_reply_choice(
                guild_id,
                mode_id,
                name.strip(),
                body.strip() or None,
                image_path.strip() or None,
                rate,
                enabled == "on",
            )
        else:
            choice = repository.get_reply_choice(guild_id, choice_id)
            if choice is None or int(choice["mode_id"]) != mode_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reply choice not found")
            repository.update_reply_choice(
                guild_id,
                choice_id,
                name.strip(),
                body.strip() or None,
                image_path.strip() or None,
                rate,
                enabled == "on",
            )
        connection.commit()
    finally:
        context.__exit__(None, None, None)
    return RedirectResponse(url="/guilds/{0}/modes/{1}".format(guild_id, mode_id), status_code=303)


async def save_trigger_condition(
    request: Request,
    guild_id: str,
    mode_id: int,
    condition_id: Optional[int],
    condition_type: str,
    group_operator: str,
    config_text: str,
    enabled: Optional[str],
    new_count_key: str,
    new_count_name: str,
    new_count_description: str,
    new_count_initial_value: str,
    new_count_reset_type: str,
):
    server, connection, context = get_edit_context(request, guild_id, mode_id)
    if connection is None:
        return RedirectResponse(url="/login", status_code=303)
    try:
        if condition_type not in TRIGGER_TYPES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid trigger type")
        config = parse_json_config(config_text)
        counter_repository = CounterRepository(connection)
        if condition_type == "counter_threshold" and new_count_key.strip():
            counter_key = new_count_key.strip()
            if counter_repository.get_by_key(guild_id, counter_key) is not None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="counter key already exists")
            counter_repository.create_counter(
                guild_id,
                counter_key,
                new_count_name.strip() or counter_key,
                new_count_description.strip() or None,
                parse_int(new_count_initial_value, 0),
                new_count_reset_type if new_count_reset_type in RESET_TYPES else "none",
            )
            config["counter_key"] = counter_key

        repository = ModeRepository(connection)
        op = group_operator if group_operator in ("AND", "OR") else "AND"
        if condition_id is None:
            repository.create_trigger_condition(guild_id, mode_id, condition_type, config, op, enabled == "on")
        else:
            condition = repository.get_trigger_condition(guild_id, condition_id)
            if condition is None or int(condition["mode_id"]) != mode_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="trigger condition not found")
            repository.update_trigger_condition(guild_id, condition_id, condition_type, config, op, enabled == "on")
        connection.commit()
    finally:
        context.__exit__(None, None, None)
    return RedirectResponse(url="/guilds/{0}/modes/{1}".format(guild_id, mode_id), status_code=303)


async def save_exit_condition(
    request: Request,
    guild_id: str,
    mode_id: int,
    condition_id: Optional[int],
    condition_type: str,
    config_text: str,
    enabled: Optional[str],
):
    server, connection, context = get_edit_context(request, guild_id, mode_id)
    if connection is None:
        return RedirectResponse(url="/login", status_code=303)
    try:
        if condition_type not in EXIT_TYPES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid exit type")
        db_type = "duration_elapsed" if condition_type == "duration" else "manual"
        config = parse_json_config(config_text)
        repository = ModeRepository(connection)
        if condition_id is None:
            repository.create_exit_condition(guild_id, mode_id, db_type, config, enabled == "on")
        else:
            condition = repository.get_exit_condition(guild_id, condition_id)
            if condition is None or int(condition["mode_id"]) != mode_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="exit condition not found")
            repository.update_exit_condition(guild_id, condition_id, db_type, config, enabled == "on")
        connection.commit()
    finally:
        context.__exit__(None, None, None)
    return RedirectResponse(url="/guilds/{0}/modes/{1}".format(guild_id, mode_id), status_code=303)


def parse_json_config(value: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="config json is invalid")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="config json must be object")
    return parsed


def render_form(
    templates: Jinja2Templates,
    request: Request,
    server: Dict[str, Any],
    guild_id: str,
    mode: str,
    form: Dict[str, Any],
    errors: List[str],
    can_edit: bool,
    can_set_admin_only: bool,
    mode_id: Optional[int] = None,
    counters: Optional[List[Dict[str, Any]]] = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "mode_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "mode": mode,
            "mode_id": mode_id,
            "mode_data": form,
            "errors": errors,
            "can_edit": can_edit,
            "can_set_admin_only": can_set_admin_only,
            "behavior_types": BEHAVIOR_TYPES,
            "behavior_labels": BEHAVIOR_LABELS,
            "cooldown_types": COOLDOWN_TYPES,
            "cooldown_type_labels": COOLDOWN_TYPE_LABELS,
            "cooldown_periods": COOLDOWN_PERIODS,
            "cooldown_period_labels": COOLDOWN_PERIOD_LABELS,
            "cooldown_resets": COOLDOWN_RESETS,
            "cooldown_reset_labels": COOLDOWN_RESET_LABELS,
            "trigger_types": TRIGGER_TYPES,
            "condition_type_labels": CONDITION_TYPE_LABELS,
            "exit_types": EXIT_TYPES,
            "reset_types": RESET_TYPES,
            "reset_type_labels": RESET_TYPE_LABELS,
            "counters": counters or [],
        },
        status_code=status_code,
    )
