import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories import ReactionThresholdRepository


router = APIRouter()


DEFAULT_CONFIG = {
    "enabled": True,
    "threshold": 5,
    "reply_message": "同じリアクションが{threshold}個ついた",
    "allowed_channel_ids": [],
    "ignored_channel_ids": [],
    "target_emojis": [],
    "ignored_emojis": [],
    "once_per_message_emoji": True,
}


def parse_config_json(value: str) -> Dict[str, Any]:
    if not value.strip():
        return dict(DEFAULT_CONFIG)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("config must be object")
    return parsed


def config_text(config: Dict[str, Any]) -> str:
    return json.dumps(config or DEFAULT_CONFIG, ensure_ascii=False, indent=2)


def register_reaction_threshold_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/reaction-thresholds")
    async def list_rules(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            rules = ReactionThresholdRepository(connection).list_rules(guild_id, enabled=None)
        return templates.TemplateResponse(
            request,
            "reaction_thresholds.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "rules": rules,
                "can_edit": role_allows(server["role"], "editor"),
            },
        )

    @router.get("/guilds/{guild_id}/reaction-thresholds/new")
    async def new_rule(request: Request, guild_id: str):
        return await render_form(request, guild_id, None, None)

    @router.post("/guilds/{guild_id}/reaction-thresholds/new")
    async def create_rule(
        request: Request,
        guild_id: str,
        name: str = Form(...),
        enabled: Optional[str] = Form(None),
        config_json: str = Form(""),
    ):
        return await save_rule(request, guild_id, None, name, enabled, config_json)

    @router.get("/guilds/{guild_id}/reaction-thresholds/{rule_id}")
    async def edit_rule(request: Request, guild_id: str, rule_id: int):
        with get_connection() as connection:
            rule = ReactionThresholdRepository(connection).get_by_id(guild_id, rule_id)
        return await render_form(request, guild_id, rule_id, rule)

    @router.post("/guilds/{guild_id}/reaction-thresholds/{rule_id}")
    async def update_rule(
        request: Request,
        guild_id: str,
        rule_id: int,
        name: str = Form(...),
        enabled: Optional[str] = Form(None),
        config_json: str = Form(""),
    ):
        return await save_rule(request, guild_id, rule_id, name, enabled, config_json)

    @router.post("/guilds/{guild_id}/reaction-thresholds/{rule_id}/delete")
    async def delete_rule(request: Request, guild_id: str, rule_id: int):
        user, server = require_editor(request, guild_id)
        with get_connection() as connection:
            ReactionThresholdRepository(connection).delete_rule(guild_id, rule_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/reaction-thresholds".format(guild_id), status_code=303)

    async def render_form(request: Request, guild_id: str, rule_id: Optional[int], rule: Optional[Dict[str, Any]], error: str = ""):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        can_edit = role_allows(server["role"], "editor")
        if rule_id is not None and rule is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reaction threshold rule not found")
        data = rule or {"name": "", "enabled": True, "config_json": DEFAULT_CONFIG}
        return templates.TemplateResponse(
            request,
            "reaction_threshold_form.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "rule_id": rule_id,
                "rule": data,
                "config_text": config_text(data.get("config_json") or DEFAULT_CONFIG),
                "can_edit": can_edit,
                "error": error,
            },
        )

    def require_editor(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="reaction threshold editing denied")
        return user, server

    async def save_rule(
        request: Request,
        guild_id: str,
        rule_id: Optional[int],
        name: str,
        enabled: Optional[str],
        config_json: str,
    ):
        require_editor(request, guild_id)
        try:
            parsed = parse_config_json(config_json)
        except ValueError as exc:
            return await render_form(
                request,
                guild_id,
                rule_id,
                {"id": rule_id, "name": name, "enabled": enabled == "on", "config_json": DEFAULT_CONFIG},
                "詳細設定のJSONが不正。",
            )
        with get_connection() as connection:
            repository = ReactionThresholdRepository(connection)
            if rule_id is None:
                row = repository.create_rule(guild_id, name.strip(), enabled == "on", parsed)
                connection.commit()
                rule_id = int(row["id"])
            else:
                repository.update_rule(guild_id, rule_id, name.strip(), enabled == "on", parsed)
                connection.commit()
        return RedirectResponse(url="/guilds/{0}/reaction-thresholds/{1}".format(guild_id, rule_id), status_code=303)
