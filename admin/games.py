from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories.games import GameRepository


router = APIRouter()


def register_game_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/games")
    async def games_page(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        with get_connection() as connection:
            games = GameRepository(connection, bot_id=bot_id).list_games(100)
        return templates.TemplateResponse(
            request,
            "games.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "games": games,
                "can_refresh": role_allows(server["role"], "editor"),
                "provider_status": "Steam Store API / price history: unavailable",
            },
        )
