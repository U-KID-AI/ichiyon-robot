from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.servers import can_access_guild, find_server, role_allows
from admin.ux import is_test_data, parse_show_test_data
from bot import config as bot_config
from bot.db import get_connection
from bot.repositories import XUpdateWatchRepository
from bot.services.x_update_notifications import DEFAULT_CHECK_INTERVAL_SECONDS, DEFAULT_POST_TEMPLATE


router = APIRouter()


def register_x_update_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/x-updates")
    async def x_updates_page(
        request: Request,
        guild_id: str,
        q: Optional[str] = Query(None),
        enabled: str = Query("all"),
        show_test_data: str = Query("false"),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        filters = normalize_filters(q, enabled, show_test_data)
        watches = list_watch_rows(guild_id, filters)
        return templates.TemplateResponse(
            request,
            "x_updates.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "filters": filters,
                "watches": watches,
                "can_create": role_allows(server["role"], "guild_admin"),
            },
        )

    @router.post("/guilds/{guild_id}/x-updates/{watch_id}/toggle")
    async def toggle_x_update(request: Request, guild_id: str, watch_id: int):
        user, server = require_guild_admin(request, guild_id)
        with get_connection() as connection:
            repository = XUpdateWatchRepository(connection)
            if repository.get_by_id(bot_config.BOT_INSTANCE_ID, guild_id, watch_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="x update watch not found")
            repository.toggle_enabled(bot_config.BOT_INSTANCE_ID, guild_id, watch_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/x-updates".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/x-updates/{watch_id}/delete")
    async def delete_x_update(request: Request, guild_id: str, watch_id: int):
        user, server = require_guild_admin(request, guild_id)
        with get_connection() as connection:
            repository = XUpdateWatchRepository(connection)
            if repository.get_by_id(bot_config.BOT_INSTANCE_ID, guild_id, watch_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="x update watch not found")
            repository.delete_watch(bot_config.BOT_INSTANCE_ID, guild_id, watch_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/x-updates".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/x-updates/new")
    async def new_x_update_page(request: Request, guild_id: str):
        user, server = require_guild_admin(request, guild_id)
        return render_form(templates, request, server, guild_id, "new", default_form(), [], True)

    @router.post("/guilds/{guild_id}/x-updates/new")
    async def create_x_update(
        request: Request,
        guild_id: str,
        channel_id: str = Form(""),
        x_username: str = Form(""),
        x_user_id: str = Form(""),
        display_name: str = Form(""),
        enabled: Optional[str] = Form(None),
        include_replies: Optional[str] = Form(None),
        include_reposts: Optional[str] = Form(None),
        include_quotes: Optional[str] = Form(None),
        check_interval_seconds: str = Form("900"),
        post_template: str = Form(""),
    ):
        return await save_x_update(request, templates, guild_id, None, locals())

    @router.get("/guilds/{guild_id}/x-updates/{watch_id}")
    async def edit_x_update_page(request: Request, guild_id: str, watch_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        with get_connection() as connection:
            repository = XUpdateWatchRepository(connection)
            watch = repository.get_by_id(bot_config.BOT_INSTANCE_ID, guild_id, watch_id)
            if watch is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="x update watch not found")
            form = build_form_from_watch(watch)
        return render_form(
            templates,
            request,
            server,
            guild_id,
            "edit",
            form,
            [],
            role_allows(server["role"], "guild_admin"),
            watch_id=watch_id,
        )

    @router.post("/guilds/{guild_id}/x-updates/{watch_id}")
    async def update_x_update(
        request: Request,
        guild_id: str,
        watch_id: int,
        channel_id: str = Form(""),
        x_username: str = Form(""),
        x_user_id: str = Form(""),
        display_name: str = Form(""),
        enabled: Optional[str] = Form(None),
        include_replies: Optional[str] = Form(None),
        include_reposts: Optional[str] = Form(None),
        include_quotes: Optional[str] = Form(None),
        check_interval_seconds: str = Form("900"),
        post_template: str = Form(""),
    ):
        return await save_x_update(request, templates, guild_id, watch_id, locals())


def require_guild_admin(request: Request, guild_id: str):
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    if not can_access_guild(guild_id, user["user_id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
    server = find_server(guild_id, user["user_id"])
    if not role_allows(server["role"], "guild_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="x update editing denied")
    return user, server


def normalize_filters(q: Optional[str], enabled: str, show_test_data: str = "false") -> Dict[str, Any]:
    return {
        "q": (q or "").strip(),
        "enabled": enabled if enabled in ("all", "true", "false") else "all",
        "show_test_data": parse_show_test_data(show_test_data),
    }


def parse_bool_filter(value: str) -> Optional[bool]:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def list_watch_rows(guild_id: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = XUpdateWatchRepository(connection)
        watches = repository.list_watches(
            bot_config.BOT_INSTANCE_ID,
            guild_id,
            query=filters["q"] or None,
            enabled=parse_bool_filter(filters["enabled"]),
        )
    return [
        build_watch_view(watch, guild_id)
        for watch in watches
        if filters["show_test_data"] or not row_is_hidden_test_data(watch)
    ]


def row_is_hidden_test_data(row: Dict[str, Any]) -> bool:
    return is_test_data(row.get("x_username")) or is_test_data(row.get("display_name"))


def build_watch_view(watch: Dict[str, Any], guild_id: str) -> Dict[str, Any]:
    row = build_form_from_watch(watch)
    row["edit_url"] = "/guilds/{0}/x-updates/{1}".format(guild_id, row["id"])
    row["toggle_url"] = "/guilds/{0}/x-updates/{1}/toggle".format(guild_id, row["id"])
    row["delete_url"] = "/guilds/{0}/x-updates/{1}/delete".format(guild_id, row["id"])
    return row


def default_form() -> Dict[str, Any]:
    return {
        "id": None,
        "channel_id": "",
        "x_username": "",
        "x_user_id": "",
        "display_name": "",
        "enabled": True,
        "include_replies": False,
        "include_reposts": False,
        "include_quotes": False,
        "check_interval_seconds": DEFAULT_CHECK_INTERVAL_SECONDS,
        "last_seen_post_id": "",
        "last_posted_post_id": "",
        "last_checked_at": None,
        "last_success_at": None,
        "last_error": "",
        "post_template": DEFAULT_POST_TEMPLATE,
    }


def build_form_from_watch(watch: Dict[str, Any]) -> Dict[str, Any]:
    form = default_form()
    form.update(
        {
            "id": watch.get("id"),
            "channel_id": watch.get("channel_id") or "",
            "x_username": watch.get("x_username") or "",
            "x_user_id": watch.get("x_user_id") or "",
            "display_name": watch.get("display_name") or "",
            "enabled": bool(watch.get("enabled")),
            "include_replies": bool(watch.get("include_replies")),
            "include_reposts": bool(watch.get("include_reposts")),
            "include_quotes": bool(watch.get("include_quotes")),
            "check_interval_seconds": int(watch.get("check_interval_seconds") or DEFAULT_CHECK_INTERVAL_SECONDS),
            "last_seen_post_id": watch.get("last_seen_post_id") or "",
            "last_posted_post_id": watch.get("last_posted_post_id") or "",
            "last_checked_at": watch.get("last_checked_at"),
            "last_success_at": watch.get("last_success_at"),
            "last_error": watch.get("last_error") or "",
            "post_template": watch.get("post_template") or DEFAULT_POST_TEMPLATE,
        }
    )
    return form


def build_form(values: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    form = default_form()
    form.update(
        {
            "channel_id": str(values.get("channel_id") or "").strip(),
            "x_username": str(values.get("x_username") or "").strip().lstrip("@"),
            "x_user_id": str(values.get("x_user_id") or "").strip(),
            "display_name": str(values.get("display_name") or "").strip(),
            "enabled": values.get("enabled") == "on",
            "include_replies": values.get("include_replies") == "on",
            "include_reposts": values.get("include_reposts") == "on",
            "include_quotes": values.get("include_quotes") == "on",
            "post_template": str(values.get("post_template") or "").strip() or DEFAULT_POST_TEMPLATE,
        }
    )
    errors = []
    try:
        interval = int(str(values.get("check_interval_seconds") or "").strip())
    except ValueError:
        interval = DEFAULT_CHECK_INTERVAL_SECONDS
        errors.append("確認間隔は数字で入力。")
    if interval < 60:
        errors.append("確認間隔は60秒以上。")
    form["check_interval_seconds"] = interval
    if not form["channel_id"]:
        errors.append("通知チャンネルIDを入力。")
    if not form["x_username"]:
        errors.append("Xユーザー名を入力。")
    return form, errors


async def save_x_update(
    request: Request,
    templates: Jinja2Templates,
    guild_id: str,
    watch_id: Optional[int],
    values: Dict[str, Any],
):
    user, server = require_guild_admin(request, guild_id)
    form, errors = build_form(values)
    with get_connection() as connection:
        repository = XUpdateWatchRepository(connection)
        if watch_id is not None and repository.get_by_id(bot_config.BOT_INSTANCE_ID, guild_id, watch_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="x update watch not found")
        if not errors:
            if watch_id is None:
                created = repository.create_watch(
                    bot_config.BOT_INSTANCE_ID,
                    guild_id,
                    form["channel_id"],
                    form["x_username"],
                    form["x_user_id"] or None,
                    form["display_name"] or None,
                    form["enabled"],
                    form["include_replies"],
                    form["include_reposts"],
                    form["include_quotes"],
                    form["check_interval_seconds"],
                    form["post_template"] or None,
                )
                connection.commit()
                return RedirectResponse(
                    url="/guilds/{0}/x-updates/{1}".format(guild_id, created["id"]),
                    status_code=303,
                )
            repository.update_watch(
                bot_config.BOT_INSTANCE_ID,
                guild_id,
                watch_id,
                form["channel_id"],
                form["x_username"],
                form["x_user_id"] or None,
                form["display_name"] or None,
                form["enabled"],
                form["include_replies"],
                form["include_reposts"],
                form["include_quotes"],
                form["check_interval_seconds"],
                form["post_template"] or None,
            )
            connection.commit()
            return RedirectResponse(url="/guilds/{0}/x-updates/{1}".format(guild_id, watch_id), status_code=303)
    return render_form(
        templates,
        request,
        server,
        guild_id,
        "new" if watch_id is None else "edit",
        form,
        errors,
        True,
        watch_id=watch_id,
        status_code=400,
    )


def render_form(
    templates: Jinja2Templates,
    request: Request,
    server: Dict[str, Any],
    guild_id: str,
    mode: str,
    form: Dict[str, Any],
    errors: List[str],
    can_edit: bool,
    watch_id: Optional[int] = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "x_update_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "mode": mode,
            "watch_id": watch_id,
            "watch": form,
            "errors": errors,
            "can_edit": can_edit,
            "default_template": DEFAULT_POST_TEMPLATE,
        },
        status_code=status_code,
    )
