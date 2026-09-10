from typing import Dict, List, Optional

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories.persona_draws import PersonaDrawRepository, normalize_persona_trigger


router = APIRouter()


def register_persona_draw_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/persona-draws")
    async def persona_draws_page(request: Request, guild_id: str, message: str = "", error: str = ""):
        user, server = require_access(request, guild_id)
        with get_connection() as connection:
            draws = PersonaDrawRepository(connection, bot_id=current_selected_bot_id()).list_draws(guild_id)
        return templates.TemplateResponse(
            request,
            "persona_draws.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "draws": draws,
                "can_edit": role_allows(server["role"], "editor"),
                "message": message,
                "error": error,
            },
        )

    @router.get("/guilds/{guild_id}/persona-draws/new")
    async def new_persona_draw_page(request: Request, guild_id: str):
        user, server = require_editor(request, guild_id)
        return render_form(templates, request, server, guild_id, default_form(), "new")

    @router.post("/guilds/{guild_id}/persona-draws/new")
    async def create_persona_draw(
        request: Request,
        guild_id: str,
        name: str = Form(""),
        trigger_text: str = Form(""),
        reroll_enabled: Optional[str] = Form(None),
        reroll_probability_percent: str = Form("10"),
        max_rerolls: str = Form("1"),
        enabled: Optional[str] = Form(None),
        candidates_text: str = Form(""),
        reroll_lines_text: str = Form(""),
    ):
        user, server = require_editor(request, guild_id)
        form = collect_form(
            name,
            trigger_text,
            reroll_enabled,
            reroll_probability_percent,
            max_rerolls,
            enabled,
            candidates_text,
            reroll_lines_text,
        )
        errors = validate_form(form)
        if errors:
            return render_form(templates, request, server, guild_id, form, "new", errors, status_code=400)
        save_persona_draw(guild_id, form)
        return RedirectResponse(url="/guilds/{0}/persona-draws?message=created".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/persona-draws/{draw_id}")
    async def edit_persona_draw_page(request: Request, guild_id: str, draw_id: int):
        user, server = require_access(request, guild_id)
        form = load_persona_draw_form(guild_id, draw_id)
        if form is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="persona draw not found")
        return render_form(
            templates,
            request,
            server,
            guild_id,
            form,
            "edit",
            can_edit=role_allows(server["role"], "editor"),
            draw_id=draw_id,
        )

    @router.post("/guilds/{guild_id}/persona-draws/{draw_id}")
    async def update_persona_draw(
        request: Request,
        guild_id: str,
        draw_id: int,
        name: str = Form(""),
        trigger_text: str = Form(""),
        reroll_enabled: Optional[str] = Form(None),
        reroll_probability_percent: str = Form("10"),
        max_rerolls: str = Form("1"),
        enabled: Optional[str] = Form(None),
        candidates_text: str = Form(""),
        reroll_lines_text: str = Form(""),
    ):
        user, server = require_editor(request, guild_id)
        form = collect_form(
            name,
            trigger_text,
            reroll_enabled,
            reroll_probability_percent,
            max_rerolls,
            enabled,
            candidates_text,
            reroll_lines_text,
        )
        errors = validate_form(form)
        if errors:
            return render_form(templates, request, server, guild_id, form, "edit", errors, draw_id=draw_id, status_code=400)
        if save_persona_draw(guild_id, form, draw_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="persona draw not found")
        return RedirectResponse(url="/guilds/{0}/persona-draws?message=updated".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/persona-draws/{draw_id}/toggle")
    async def toggle_persona_draw(request: Request, guild_id: str, draw_id: int):
        user, server = require_editor(request, guild_id)
        with get_connection() as connection:
            repo = PersonaDrawRepository(connection, bot_id=current_selected_bot_id())
            draw = repo.get_draw(guild_id, draw_id)
            if draw is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="persona draw not found")
            repo.set_enabled(guild_id, draw_id, not bool(draw.get("enabled")))
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/persona-draws".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/persona-draws/{draw_id}/delete")
    async def delete_persona_draw(request: Request, guild_id: str, draw_id: int):
        user, server = require_editor(request, guild_id)
        with get_connection() as connection:
            PersonaDrawRepository(connection, bot_id=current_selected_bot_id()).delete_draw(guild_id, draw_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/persona-draws?message=deleted".format(guild_id), status_code=303)


def require_access(request: Request, guild_id: str):
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    bot_id = selected_bot_id(request)
    if not can_access_guild(guild_id, user["user_id"], bot_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
    return user, find_server(guild_id, user["user_id"], bot_id)


def require_editor(request: Request, guild_id: str):
    user, server = require_access(request, guild_id)
    if not role_allows(server["role"], "editor"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="persona draw edit denied")
    return user, server


def default_form() -> Dict:
    return {
        "name": "",
        "trigger_text": "",
        "trigger_key": "",
        "reroll_enabled": True,
        "reroll_probability_percent": 10,
        "max_rerolls": 1,
        "enabled": True,
        "candidates_text": "",
        "reroll_lines_text": "",
    }


def collect_form(
    name,
    trigger_text,
    reroll_enabled,
    reroll_probability_percent,
    max_rerolls,
    enabled,
    candidates_text,
    reroll_lines_text,
) -> Dict:
    trigger = str(trigger_text or "").strip()
    return {
        "name": str(name or "").strip(),
        "trigger_text": trigger,
        "trigger_key": normalize_persona_trigger(trigger),
        "reroll_enabled": reroll_enabled == "on",
        "reroll_probability_percent": parse_int(reroll_probability_percent, 10),
        "max_rerolls": parse_int(max_rerolls, 1),
        "enabled": enabled == "on",
        "candidates_text": str(candidates_text or "").strip(),
        "reroll_lines_text": str(reroll_lines_text or "").strip(),
    }


def parse_int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def split_lines(value: str) -> List[str]:
    lines = []
    seen = set()
    for line in str(value or "").splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned in seen:
            continue
        lines.append(cleaned)
        seen.add(cleaned)
    return lines


def validate_form(form: Dict) -> List[str]:
    errors = []
    if not form["name"]:
        errors.append("名前を入力してください。")
    if not form["trigger_text"]:
        errors.append("呼び出しフレーズを入力してください。")
    if not form["trigger_key"]:
        errors.append("呼び出しフレーズが不正です。")
    if not split_lines(form["candidates_text"]):
        errors.append("抽選候補を1件以上入力してください。")
    if form["reroll_probability_percent"] < 0 or form["reroll_probability_percent"] > 100:
        errors.append("再抽選確率は0〜100で指定してください。")
    if form["max_rerolls"] < 0 or form["max_rerolls"] > 10:
        errors.append("最大再抽選回数は0〜10で指定してください。")
    return errors


def save_persona_draw(guild_id: str, form: Dict, draw_id: Optional[int] = None) -> Optional[Dict]:
    with get_connection() as connection:
        repo = PersonaDrawRepository(connection, bot_id=current_selected_bot_id())
        draw = repo.upsert_draw(guild_id, form, draw_id)
        if draw is None:
            connection.rollback()
            return None
        repo.replace_candidates(guild_id, int(draw["id"]), split_lines(form["candidates_text"]))
        repo.replace_reroll_lines(guild_id, int(draw["id"]), split_lines(form["reroll_lines_text"]))
        connection.commit()
        return draw


def load_persona_draw_form(guild_id: str, draw_id: int) -> Optional[Dict]:
    with get_connection() as connection:
        repo = PersonaDrawRepository(connection, bot_id=current_selected_bot_id())
        draw = repo.get_draw(guild_id, draw_id)
        if draw is None:
            return None
        candidates = repo.list_candidates(guild_id, draw_id)
        reroll_lines = repo.list_reroll_lines(guild_id, draw_id)
    form = default_form()
    form.update(draw)
    form["candidates_text"] = "\n".join(str(row.get("body") or "") for row in candidates)
    form["reroll_lines_text"] = "\n".join(str(row.get("body") or "") for row in reroll_lines)
    return form


def render_form(templates, request, server, guild_id, form, mode, errors=None, can_edit=True, draw_id=None, status_code=200):
    return templates.TemplateResponse(
        request,
        "persona_draw_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "draw": form,
            "mode": mode,
            "errors": errors or [],
            "can_edit": can_edit,
            "draw_id": draw_id,
        },
        status_code=status_code,
    )
