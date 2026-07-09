from typing import Dict, Optional

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories import ScheduleTemplateRepository


router = APIRouter()


def register_schedule_template_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/schedule-templates")
    async def schedule_templates_page(request: Request, guild_id: str, message: str = "", error: str = ""):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        with get_connection() as connection:
            templates_rows = ScheduleTemplateRepository(connection, bot_id=current_selected_bot_id()).list_templates(guild_id)
        return templates.TemplateResponse(
            request,
            "schedule_templates.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "bot_id": bot_id,
                "templates": templates_rows,
                "can_edit": role_allows(server["role"], "editor"),
                "message": message,
                "error": error,
            },
        )

    @router.get("/guilds/{guild_id}/schedule-templates/new")
    async def new_schedule_template_page(request: Request, guild_id: str):
        user, server = require_editor(request, guild_id)
        return render_form(
            templates,
            request,
            server,
            guild_id,
            "new",
            default_form(),
            {},
            [],
            True,
        )

    @router.post("/guilds/{guild_id}/schedule-templates/new")
    async def create_schedule_template(
        request: Request,
        guild_id: str,
        name: str = Form(""),
        description: str = Form(""),
        is_enabled: Optional[str] = Form(None),
    ):
        form = collect_form_values(name, description, is_enabled, await request.form())
        return save_schedule_template(request, templates, guild_id, None, form)

    @router.get("/guilds/{guild_id}/schedule-templates/{template_id}")
    async def edit_schedule_template_page(request: Request, guild_id: str, template_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        with get_connection() as connection:
            repository = ScheduleTemplateRepository(connection, bot_id=current_selected_bot_id())
            template = repository.get_by_id(guild_id, template_id)
            if template is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule template not found")
            items = {int(item["day_index"]): item.get("content") or "" for item in repository.list_items(template_id)}
        return render_form(
            templates,
            request,
            server,
            guild_id,
            "edit",
            form_from_template(template),
            items,
            [],
            role_allows(server["role"], "editor"),
            template_id=template_id,
        )

    @router.post("/guilds/{guild_id}/schedule-templates/{template_id}")
    async def update_schedule_template(
        request: Request,
        guild_id: str,
        template_id: int,
        name: str = Form(""),
        description: str = Form(""),
        is_enabled: Optional[str] = Form(None),
    ):
        form = collect_form_values(name, description, is_enabled, await request.form())
        return save_schedule_template(request, templates, guild_id, template_id, form)

    @router.post("/guilds/{guild_id}/schedule-templates/{template_id}/toggle")
    async def toggle_schedule_template(request: Request, guild_id: str, template_id: int):
        user, server = require_editor(request, guild_id)
        with get_connection() as connection:
            repository = ScheduleTemplateRepository(connection, bot_id=current_selected_bot_id())
            if repository.toggle_enabled(guild_id, template_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule template not found")
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/schedule-templates".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/schedule-templates/{template_id}/delete")
    async def delete_schedule_template(request: Request, guild_id: str, template_id: int):
        user, server = require_editor(request, guild_id)
        with get_connection() as connection:
            repository = ScheduleTemplateRepository(connection, bot_id=current_selected_bot_id())
            if not repository.delete_template(guild_id, template_id):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule template not found")
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/schedule-templates".format(guild_id), status_code=303)


def require_editor(request: Request, guild_id: str):
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    bot_id = selected_bot_id(request)
    if not can_access_guild(guild_id, user["user_id"], bot_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
    server = find_server(guild_id, user["user_id"], bot_id)
    if not role_allows(server["role"], "editor"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="schedule template edit denied")
    return user, server


def default_form() -> Dict:
    return {"name": "", "description": "", "is_enabled": True}


def form_from_template(template: Dict) -> Dict:
    return {
        "name": template.get("name") or "",
        "description": template.get("description") or "",
        "is_enabled": bool(template.get("is_enabled")),
    }


def collect_form_values(name: str, description: str, is_enabled: Optional[str], form_data) -> Dict:
    day_contents = {}
    for day_index in range(1, 15):
        day_contents[day_index] = str(form_data.get("day_{0}".format(day_index)) or "").strip()
    return {
        "name": name.strip(),
        "description": description.strip(),
        "is_enabled": is_enabled == "on",
        "day_contents": day_contents,
    }


def validate_form(form: Dict) -> list:
    errors = []
    if not form["name"]:
        errors.append("テンプレート名を入力してください。")
    return errors


def save_schedule_template(
    request: Request,
    templates: Jinja2Templates,
    guild_id: str,
    template_id: Optional[int],
    form: Dict,
):
    user, server = require_editor(request, guild_id)
    errors = validate_form(form)
    if errors:
        return render_form(
            templates,
            request,
            server,
            guild_id,
            "new" if template_id is None else "edit",
            form,
            form["day_contents"],
            errors,
            True,
            template_id=template_id,
        )

    with get_connection() as connection:
        repository = ScheduleTemplateRepository(connection, bot_id=current_selected_bot_id())
        try:
            if template_id is None:
                template = repository.create_template(
                    guild_id,
                    form["name"],
                    form["description"],
                    form["is_enabled"],
                )
                template_id = int(template["id"])
            else:
                template = repository.update_template(
                    guild_id,
                    template_id,
                    form["name"],
                    form["description"],
                    form["is_enabled"],
                )
                if template is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule template not found")
            repository.replace_items(template_id, form["day_contents"])
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return RedirectResponse(url="/guilds/{0}/schedule-templates/{1}".format(guild_id, template_id), status_code=303)


def render_form(
    templates: Jinja2Templates,
    request: Request,
    server: Dict,
    guild_id: str,
    mode: str,
    form: Dict,
    items: Dict[int, str],
    errors: list,
    can_edit: bool,
    template_id: Optional[int] = None,
):
    day_values = {day: items.get(day, "") for day in range(1, 15)}
    return templates.TemplateResponse(
        request,
        "schedule_template_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "mode": mode,
            "template_id": template_id,
            "form": form,
            "day_values": day_values,
            "errors": errors,
            "can_edit": can_edit,
        },
    )
