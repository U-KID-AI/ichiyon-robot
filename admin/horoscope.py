import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories.horoscope import HoroscopeRepository
from bot.services.mezamashi_horoscope import build_horoscope_messages


router = APIRouter()
TIME_PATTERN = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def register_horoscope_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/horoscope-settings")
    async def horoscope_settings_page(
        request: Request,
        guild_id: str,
        message: str = "",
        error: str = "",
    ):
        access = require_editor(request, guild_id, check_editor=False)
        if isinstance(access, RedirectResponse):
            return access
        user, server = access
        settings = load_settings(guild_id)
        latest_status = load_latest_status()
        return templates.TemplateResponse(
            request,
            "horoscope_settings.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "settings": settings,
                "latest_status": latest_status,
                "can_edit": role_allows(server["role"], "editor"),
                "message": message,
                "error": error,
            },
        )

    @router.post("/guilds/{guild_id}/horoscope-settings")
    async def update_horoscope_settings(
        request: Request,
        guild_id: str,
        enabled: Optional[str] = Form(None),
        ranking_command_enabled: Optional[str] = Form(None),
        zodiac_command_enabled: Optional[str] = Form(None),
        auto_post_enabled: Optional[str] = Form(None),
        auto_post_channel_id: str = Form(""),
        auto_post_time: str = Form("07:10"),
    ):
        access = require_editor(request, guild_id, check_editor=True)
        if isinstance(access, RedirectResponse):
            return access
        user, _server = access
        auto_post_time = (auto_post_time or "07:10").strip()
        if not TIME_PATTERN.match(auto_post_time):
            return RedirectResponse(
                url="/guilds/{0}/horoscope-settings?error={1}".format(guild_id, quote("投稿時刻は HH:MM 形式で入力してください。")),
                status_code=303,
            )
        with get_connection() as connection:
            repository = HoroscopeRepository(connection, bot_id=current_selected_bot_id())
            repository.upsert_settings(
                guild_id,
                enabled=enabled == "on",
                ranking_command_enabled=ranking_command_enabled == "on",
                zodiac_command_enabled=zodiac_command_enabled == "on",
                auto_post_enabled=auto_post_enabled == "on",
                auto_post_channel_id=(auto_post_channel_id or "").strip(),
                auto_post_time=auto_post_time,
            )
            connection.commit()
        return RedirectResponse(
            url="/guilds/{0}/horoscope-settings?message={1}".format(guild_id, quote("保存しました。")),
            status_code=303,
        )


def require_editor(request: Request, guild_id: str, *, check_editor: bool):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    bot_id = selected_bot_id(request)
    if not can_access_guild(guild_id, user["user_id"], bot_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
    server = find_server(guild_id, user["user_id"], bot_id)
    if check_editor and not role_allows(server["role"], "editor"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="horoscope settings update denied")
    return user, server


def load_settings(guild_id: str) -> Dict[str, Any]:
    with get_connection() as connection:
        return HoroscopeRepository(connection, bot_id=current_selected_bot_id()).get_settings(guild_id)


def load_latest_status() -> Dict[str, Any]:
    try:
        with get_connection() as connection:
            row = HoroscopeRepository(connection, bot_id=current_selected_bot_id()).get_latest_cache()
    except Exception as exc:
        return {"ok": False, "message": "取得状態を確認できません: {0}".format(exc)}
    if row is None:
        return {"ok": False, "message": "未取得"}
    payload = row.get("payload_json") or {}
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    ranking = payload.get("ranking") if isinstance(payload, dict) else []
    return {
        "ok": len(ranking) == 12,
        "target_date": str(row.get("target_date") or payload.get("date") or ""),
        "fetched_at": str(row.get("fetched_at") or ""),
        "count": len(ranking) if isinstance(ranking, list) else 0,
    }


async def preview_horoscope_message() -> List[str]:
    return await build_horoscope_messages()
