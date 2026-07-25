from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories import RandomReactionRepository
from bot.repositories.random_reactions import (
    DEFAULT_RANDOM_REACTION_COOLDOWN_SECONDS,
    DEFAULT_RANDOM_REACTION_EMOJI,
    DEFAULT_RANDOM_REACTION_PROBABILITY_PERCENT,
    default_random_reaction_settings,
)
from bot.services.random_reactions import join_channel_ids, split_channel_ids


router = APIRouter()


def build_random_reaction_form(
    enabled: Optional[str],
    emoji: str,
    probability_percent: str,
    cooldown_seconds: str,
    target_channel_ids: str,
    excluded_channel_ids: str,
) -> Dict[str, object]:
    return {
        "enabled": enabled == "on",
        "emoji": str(emoji or "").strip(),
        "probability_percent": str(probability_percent or "").strip(),
        "cooldown_seconds": str(cooldown_seconds or "").strip(),
        "target_channel_ids": join_channel_ids(split_channel_ids(target_channel_ids)),
        "excluded_channel_ids": join_channel_ids(split_channel_ids(excluded_channel_ids)),
    }


def form_from_settings(settings: Dict[str, object]) -> Dict[str, object]:
    probability = settings.get("probability_percent")
    cooldown = settings.get("cooldown_seconds")
    return {
        "enabled": bool(settings.get("enabled")),
        "emoji": str(settings.get("emoji") or DEFAULT_RANDOM_REACTION_EMOJI),
        "probability_percent": str(DEFAULT_RANDOM_REACTION_PROBABILITY_PERCENT if probability is None else probability),
        "cooldown_seconds": str(DEFAULT_RANDOM_REACTION_COOLDOWN_SECONDS if cooldown is None else cooldown),
        "target_channel_ids": join_channel_ids(split_channel_ids(settings.get("target_channel_ids"))),
        "excluded_channel_ids": join_channel_ids(split_channel_ids(settings.get("excluded_channel_ids"))),
    }


def validate_random_reaction_form(form: Dict[str, object]) -> Tuple[List[str], float, int]:
    errors: List[str] = []
    emoji = str(form.get("emoji") or "").strip()
    if not emoji:
        errors.append("絵文字を入力してください。")

    try:
        probability = float(str(form.get("probability_percent") or "").strip())
    except ValueError:
        probability = 0.0
        errors.append("確率は0〜100の数値で入力してください。")
    if probability < 0 or probability > 100:
        errors.append("確率は0〜100の範囲で入力してください。")

    try:
        cooldown = int(str(form.get("cooldown_seconds") or "").strip())
    except ValueError:
        cooldown = 0
        errors.append("クールダウン秒数は0以上の整数で入力してください。")
    if cooldown < 0:
        errors.append("クールダウン秒数は0以上の整数で入力してください。")

    return errors, probability, cooldown


def register_random_reaction_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/random-reactions")
    async def random_reactions_page(
        request: Request,
        guild_id: str,
        saved: str = Query(""),
        error: str = Query(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], bot_id)
        with get_connection() as connection:
            try:
                settings = RandomReactionRepository(connection, bot_id=current_selected_bot_id()).get(guild_id)
            except Exception:
                settings = default_random_reaction_settings(current_selected_bot_id(), guild_id)

        return templates.TemplateResponse(
            request,
            "random_reactions.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "form": form_from_settings(settings),
                "can_edit": role_allows(server["role"], "editor"),
                "saved": saved,
                "error": error,
            },
        )

    @router.post("/guilds/{guild_id}/random-reactions")
    async def save_random_reactions(
        request: Request,
        guild_id: str,
        enabled: Optional[str] = Form(None),
        emoji: str = Form(""),
        probability_percent: str = Form(""),
        cooldown_seconds: str = Form(""),
        target_channel_ids: str = Form(""),
        excluded_channel_ids: str = Form(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="random reaction edit denied")

        form = build_random_reaction_form(
            enabled,
            emoji,
            probability_percent,
            cooldown_seconds,
            target_channel_ids,
            excluded_channel_ids,
        )
        errors, probability, cooldown = validate_random_reaction_form(form)
        if errors:
            return templates.TemplateResponse(
                request,
                "random_reactions.html",
                {
                    "user": user,
                    "server": server,
                    "guild_id": guild_id,
                    "form": form,
                    "can_edit": True,
                    "saved": "",
                    "error": " / ".join(errors),
                },
                status_code=400,
            )

        with get_connection() as connection:
            RandomReactionRepository(connection, bot_id=current_selected_bot_id()).upsert(
                guild_id,
                bool(form["enabled"]),
                str(form["emoji"]),
                probability,
                cooldown,
                str(form["target_channel_ids"]),
                str(form["excluded_channel_ids"]),
                user["user_id"],
            )
            connection.commit()

        return RedirectResponse(url="/guilds/{0}/random-reactions?saved=1".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/random-reactions/toggle")
    async def toggle_random_reactions(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="random reaction toggle denied")
        with get_connection() as connection:
            RandomReactionRepository(connection, bot_id=current_selected_bot_id()).toggle_enabled(guild_id, user["user_id"])
            connection.commit()
        return RedirectResponse(url="/guilds/{0}?message={1}".format(guild_id, quote("更新しました。")), status_code=303)
