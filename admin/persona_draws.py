from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates


router = APIRouter()


def persona_draw_redirect_url(guild_id: str) -> str:
    return "/guilds/{0}/mention-reactions?kind=random_draw".format(guild_id)


def register_persona_draw_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/persona-draws")
    async def persona_draws_page(request: Request, guild_id: str, message: str = "", error: str = ""):
        return RedirectResponse(url=persona_draw_redirect_url(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/persona-draws/new")
    async def new_persona_draw_page(request: Request, guild_id: str):
        return RedirectResponse(url="/guilds/{0}/mention-reactions/new".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/persona-draws/new")
    async def create_persona_draw(request: Request, guild_id: str):
        return RedirectResponse(url=persona_draw_redirect_url(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/persona-draws/{draw_id}")
    async def edit_persona_draw_page(request: Request, guild_id: str, draw_id: int):
        return RedirectResponse(url=persona_draw_redirect_url(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/persona-draws/{draw_id}")
    async def update_persona_draw(request: Request, guild_id: str, draw_id: int):
        return RedirectResponse(url=persona_draw_redirect_url(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/persona-draws/{draw_id}/toggle")
    async def toggle_persona_draw(request: Request, guild_id: str, draw_id: int):
        return RedirectResponse(url=persona_draw_redirect_url(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/persona-draws/{draw_id}/delete")
    async def delete_persona_draw(request: Request, guild_id: str, draw_id: int):
        return RedirectResponse(url=persona_draw_redirect_url(guild_id), status_code=303)
