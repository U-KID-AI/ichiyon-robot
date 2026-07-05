import json
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories import MentionReactionRepository, ReactionThresholdRepository


router = APIRouter()


DEFAULT_CONFIG = {
    "enabled": True,
    "threshold": 2,
    "reply_source_type": "mention_reaction",
    "reply_reaction_key": "quote",
    "reply_message": "リアクションが集まってるな",
    "allowed_channel_ids": [],
    "ignored_channel_ids": [],
    "target_emojis": [],
    "ignored_emojis": [],
    "once_per_message_emoji": True,
}


def split_lines(value: str) -> list:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in (value or "").replace(",", "\n").splitlines() if item.strip()]


def with_defaults(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if config is None:
        return dict(DEFAULT_CONFIG)
    has_source_type = "reply_source_type" in config
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    if not has_source_type or not merged.get("reply_source_type"):
        merged["reply_source_type"] = "fixed"
    return merged


def config_text(config: Dict[str, Any]) -> str:
    return json.dumps(with_defaults(config), ensure_ascii=False, indent=2)


def build_rule_config(
    threshold: str,
    reply_source_type: str,
    reply_reaction_key: str,
    reply_message: str,
    allowed_channel_ids: str,
    ignored_channel_ids: str,
    target_emojis: str,
    ignored_emojis: str,
    once_per_message_emoji: Optional[str],
    extra_config_json: str,
) -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if extra_config_json.strip():
        parsed = json.loads(extra_config_json)
        if not isinstance(parsed, dict):
            raise ValueError("extra config must be object")
        config.update(parsed)
    config.update(
        {
            "threshold": int(threshold),
            "reply_source_type": reply_source_type if reply_source_type in ("fixed", "mention_reaction") else "fixed",
            "reply_reaction_key": reply_reaction_key.strip(),
            "reply_message": reply_message.strip(),
            "allowed_channel_ids": split_lines(allowed_channel_ids),
            "ignored_channel_ids": split_lines(ignored_channel_ids),
            "target_emojis": split_lines(target_emojis),
            "ignored_emojis": split_lines(ignored_emojis),
            "once_per_message_emoji": once_per_message_emoji == "on",
        }
    )
    if int(config["threshold"]) < 1:
        raise ValueError("threshold must be positive")
    return config


def register_reaction_threshold_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/reaction-thresholds")
    async def list_rules(
        request: Request,
        guild_id: str,
        message: str = Query(""),
        error: str = Query(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        with get_connection() as connection:
            rules = ReactionThresholdRepository(connection, bot_id=current_selected_bot_id()).list_rules(guild_id, enabled=None)
        return templates.TemplateResponse(
            request,
            "reaction_thresholds.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "rules": rules,
                "can_edit": role_allows(server["role"], "editor"),
                "message": message,
                "error": error,
            },
        )

    @router.post("/guilds/{guild_id}/reaction-thresholds/bulk-enabled")
    async def bulk_set_rules_enabled(
        request: Request,
        guild_id: str,
        action: str = Form(""),
        rule_ids: List[int] = Form([]),
    ):
        require_editor(request, guild_id)
        if not rule_ids:
            return RedirectResponse(url="/guilds/{0}/reaction-thresholds?error={1}".format(guild_id, quote("項目を選択してね")), status_code=303)
        if action not in ("on", "off"):
            return RedirectResponse(url="/guilds/{0}/reaction-thresholds?error={1}".format(guild_id, quote("操作を選んでね")), status_code=303)
        with get_connection() as connection:
            repository = ReactionThresholdRepository(connection, bot_id=current_selected_bot_id())
            updated_count = repository.bulk_set_enabled(guild_id, rule_ids, action == "on")
            connection.commit()
        failed_count = max(0, len(rule_ids) - updated_count)
        return RedirectResponse(
            url="/guilds/{0}/reaction-thresholds?message={1}".format(guild_id, quote("成功{0}件 / 失敗{1}件".format(updated_count, failed_count))),
            status_code=303,
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
        threshold: str = Form("2"),
        reply_source_type: str = Form("mention_reaction"),
        reply_reaction_key: str = Form("quote"),
        reply_message: str = Form(""),
        allowed_channel_ids: str = Form(""),
        ignored_channel_ids: str = Form(""),
        target_emojis: str = Form(""),
        ignored_emojis: str = Form(""),
        once_per_message_emoji: Optional[str] = Form(None),
        extra_config_json: str = Form(""),
    ):
        return await save_rule(
            request,
            guild_id,
            None,
            name,
            enabled,
            threshold,
            reply_source_type,
            reply_reaction_key,
            reply_message,
            allowed_channel_ids,
            ignored_channel_ids,
            target_emojis,
            ignored_emojis,
            once_per_message_emoji,
            extra_config_json,
        )

    @router.get("/guilds/{guild_id}/reaction-thresholds/{rule_id}")
    async def edit_rule(request: Request, guild_id: str, rule_id: int):
        with get_connection() as connection:
            rule = ReactionThresholdRepository(connection, bot_id=current_selected_bot_id()).get_by_id(guild_id, rule_id)
        return await render_form(request, guild_id, rule_id, rule)

    @router.post("/guilds/{guild_id}/reaction-thresholds/{rule_id}")
    async def update_rule(
        request: Request,
        guild_id: str,
        rule_id: int,
        name: str = Form(...),
        enabled: Optional[str] = Form(None),
        threshold: str = Form("2"),
        reply_source_type: str = Form("mention_reaction"),
        reply_reaction_key: str = Form("quote"),
        reply_message: str = Form(""),
        allowed_channel_ids: str = Form(""),
        ignored_channel_ids: str = Form(""),
        target_emojis: str = Form(""),
        ignored_emojis: str = Form(""),
        once_per_message_emoji: Optional[str] = Form(None),
        extra_config_json: str = Form(""),
    ):
        return await save_rule(
            request,
            guild_id,
            rule_id,
            name,
            enabled,
            threshold,
            reply_source_type,
            reply_reaction_key,
            reply_message,
            allowed_channel_ids,
            ignored_channel_ids,
            target_emojis,
            ignored_emojis,
            once_per_message_emoji,
            extra_config_json,
        )

    @router.post("/guilds/{guild_id}/reaction-thresholds/{rule_id}/delete")
    async def delete_rule(request: Request, guild_id: str, rule_id: int):
        user, server = require_editor(request, guild_id)
        with get_connection() as connection:
            ReactionThresholdRepository(connection, bot_id=current_selected_bot_id()).delete_rule(guild_id, rule_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/reaction-thresholds".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/reaction-thresholds/{rule_id}/toggle")
    async def toggle_rule(request: Request, guild_id: str, rule_id: int):
        require_editor(request, guild_id)
        with get_connection() as connection:
            repository = ReactionThresholdRepository(connection, bot_id=current_selected_bot_id())
            rule = repository.get_by_id(guild_id, rule_id)
            if rule is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reaction threshold rule not found")
            repository.set_enabled(guild_id, rule_id, not bool(rule.get("enabled")))
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/reaction-thresholds".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/reaction-thresholds/{rule_id}/copy")
    async def copy_rule(request: Request, guild_id: str, rule_id: int):
        require_editor(request, guild_id)
        with get_connection() as connection:
            repository = ReactionThresholdRepository(connection, bot_id=current_selected_bot_id())
            copied = repository.copy_rule(guild_id, rule_id)
            if copied is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reaction threshold rule not found")
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/reaction-thresholds/{1}".format(guild_id, copied["id"]), status_code=303)

    async def render_form(request: Request, guild_id: str, rule_id: Optional[int], rule: Optional[Dict[str, Any]], error: str = ""):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        can_edit = role_allows(server["role"], "editor")
        if rule_id is not None and rule is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reaction threshold rule not found")
        data = rule or {"name": "", "enabled": True, "config_json": DEFAULT_CONFIG}
        config = with_defaults(data.get("config_json") or DEFAULT_CONFIG)
        with get_connection() as connection:
            mention_reactions = MentionReactionRepository(connection, bot_id=current_selected_bot_id()).list_reactions(guild_id, enabled=True, reaction_kind="random_draw")
        return templates.TemplateResponse(
            request,
            "reaction_threshold_form.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "rule_id": rule_id,
                "rule": data,
                "config": config,
                "allowed_channel_ids": "\n".join(split_lines(config.get("allowed_channel_ids"))),
                "ignored_channel_ids": "\n".join(split_lines(config.get("ignored_channel_ids"))),
                "target_emojis": "\n".join(split_lines(config.get("target_emojis"))),
                "ignored_emojis": "\n".join(split_lines(config.get("ignored_emojis"))),
                "config_text": config_text(config),
                "mention_reactions": mention_reactions,
                "can_edit": can_edit,
                "error": error,
            },
        )

    def require_editor(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="reaction threshold editing denied")
        return user, server

    async def save_rule(
        request: Request,
        guild_id: str,
        rule_id: Optional[int],
        name: str,
        enabled: Optional[str],
        threshold: str,
        reply_source_type: str,
        reply_reaction_key: str,
        reply_message: str,
        allowed_channel_ids: str,
        ignored_channel_ids: str,
        target_emojis: str,
        ignored_emojis: str,
        once_per_message_emoji: Optional[str],
        extra_config_json: str,
    ):
        require_editor(request, guild_id)
        try:
            parsed = build_rule_config(
                threshold,
                reply_source_type,
                reply_reaction_key,
                reply_message,
                allowed_channel_ids,
                ignored_channel_ids,
                target_emojis,
                ignored_emojis,
                once_per_message_emoji,
                extra_config_json,
            )
        except ValueError as exc:
            return await render_form(
                request,
                guild_id,
                rule_id,
                {"id": rule_id, "name": name, "enabled": enabled == "on", "config_json": DEFAULT_CONFIG},
                "入力内容が不正。",
            )
        with get_connection() as connection:
            repository = ReactionThresholdRepository(connection, bot_id=current_selected_bot_id())
            if rule_id is None:
                row = repository.create_rule(guild_id, name.strip(), enabled == "on", parsed)
                connection.commit()
                rule_id = int(row["id"])
            else:
                repository.update_rule(guild_id, rule_id, name.strip(), enabled == "on", parsed)
                connection.commit()
        return RedirectResponse(url="/guilds/{0}/reaction-thresholds/{1}".format(guild_id, rule_id), status_code=303)
