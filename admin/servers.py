from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from bot.db import get_connection
from bot.repositories import PermissionRepository


router = APIRouter()


def register_server_routes(templates: Jinja2Templates) -> None:
    @router.get("/servers")
    async def servers_page(request: Request):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        servers = list_manageable_servers(user["user_id"])
        return templates.TemplateResponse(
            request,
            "servers.html",
            {
                "user": user,
                "servers": servers,
            },
        )

    @router.get("/guilds/{guild_id}")
    async def guild_top(
        request: Request,
        guild_id: str,
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)

        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        return templates.TemplateResponse(
            request,
            "guild_top.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
            },
        )


def list_manageable_servers(discord_user_id: str) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = PermissionRepository(connection)
        return repository.list_manageable_guilds(discord_user_id)


def can_access_guild(guild_id: str, discord_user_id: str) -> bool:
    with get_connection() as connection:
        repository = PermissionRepository(connection)
        return repository.can_access_guild(guild_id, discord_user_id)


def find_server(guild_id: str, discord_user_id: str) -> Dict[str, Any]:
    for server in list_manageable_servers(discord_user_id):
        if server["guild_id"] == guild_id:
            return server
    return {"guild_id": guild_id, "name": guild_id, "icon_url": None, "role": ""}
