from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories import VoiceLineRepository
from bot.repositories.voice_lines import DEFAULT_REVIVE_LINE


router = APIRouter()


def register_voice_line_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/voice-lines")
    async def voice_line_form(request: Request, guild_id: str, saved: Optional[str] = None):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        can_edit = role_allows(server["role"], "editor")
        with get_connection() as connection:
            row = VoiceLineRepository(connection).get(bot_id, guild_id)
        return templates.TemplateResponse(
            request,
            "voice_lines.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "bot_id": bot_id,
                "voice_line": row or {},
                "join_line": (row or {}).get("join_line") or "",
                "revive_line": (row or {}).get("revive_line") or "",
                "enabled": True if row is None else bool(row.get("enabled")),
                "default_revive_line": DEFAULT_REVIVE_LINE,
                "can_edit": can_edit,
                "saved": saved,
            },
        )

    @router.post("/guilds/{guild_id}/voice-lines")
    async def save_voice_line(
        request: Request,
        guild_id: str,
        join_line: str = Form(""),
        revive_line: str = Form(""),
        enabled: Optional[str] = Form(None),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="voice line edit denied")
        with get_connection() as connection:
            VoiceLineRepository(connection).upsert(
                bot_id,
                guild_id,
                join_line.strip(),
                revive_line.strip(),
                enabled == "on",
                user["user_id"],
            )
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/voice-lines?saved=1".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/voice-lines/toggle")
    async def toggle_voice_line(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="voice line toggle denied")
        with get_connection() as connection:
            VoiceLineRepository(connection).toggle_enabled(bot_id, guild_id, user["user_id"])
            connection.commit()
        return RedirectResponse(url="/guilds/{0}".format(guild_id), status_code=303)
