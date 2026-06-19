import json
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories import AutoPostRepository


router = APIRouter()

SCHEDULE_TYPES = ("once", "yearly", "monthly", "weekly", "daily")
WEEKDAYS = ("", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DEFAULT_TIMEZONE = "Asia/Tokyo"
TIME_PATTERN = re.compile(r"^[0-2][0-9]:[0-5][0-9]$")


def register_auto_post_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/auto-posts")
    async def auto_posts_page(
        request: Request,
        guild_id: str,
        q: Optional[str] = Query(None),
        enabled: str = Query("all"),
        has_image: str = Query("all"),
        channel_id: str = Query(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"])
        filters = normalize_filters(q, enabled, has_image, channel_id)
        posts = list_post_rows(guild_id, filters)
        return templates.TemplateResponse(
            request,
            "auto_posts.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "filters": filters,
                "posts": posts,
                "can_create": role_allows(server["role"], "editor"),
            },
        )

    @router.post("/guilds/{guild_id}/auto-posts/{post_id}/toggle")
    async def toggle_auto_post(request: Request, guild_id: str, post_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto post toggle denied")

        with get_connection() as connection:
            repository = AutoPostRepository(connection)
            if repository.get_by_id(guild_id, post_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto post not found")
            repository.toggle_enabled(guild_id, post_id)
            connection.commit()

        return RedirectResponse(url="/guilds/{0}/auto-posts".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/auto-posts/new")
    async def new_auto_post_page(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto post creation denied")

        return render_form(templates, request, server, guild_id, "new", default_form(), [], True)

    @router.post("/guilds/{guild_id}/auto-posts/new")
    async def create_auto_post(
        request: Request,
        guild_id: str,
        name: str = Form(""),
        body: str = Form(""),
        image_path: str = Form(""),
        channel_id: str = Form(""),
        schedule_type: str = Form("yearly"),
        month: str = Form(""),
        day: str = Form(""),
        weekday: str = Form(""),
        time: str = Form("09:00"),
        timezone: str = Form(DEFAULT_TIMEZONE),
        enabled: Optional[str] = Form(None),
    ):
        return await save_auto_post(
            request,
            templates,
            guild_id,
            None,
            locals(),
        )

    @router.get("/guilds/{guild_id}/auto-posts/{post_id}")
    async def edit_auto_post_page(request: Request, guild_id: str, post_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"])

        with get_connection() as connection:
            repository = AutoPostRepository(connection)
            post = repository.get_by_id(guild_id, post_id)
            if post is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto post not found")
            form = build_form_from_post(post)

        return render_form(
            templates,
            request,
            server,
            guild_id,
            "edit",
            form,
            [],
            role_allows(server["role"], "editor"),
            post_id=post_id,
        )

    @router.post("/guilds/{guild_id}/auto-posts/{post_id}")
    async def update_auto_post(
        request: Request,
        guild_id: str,
        post_id: int,
        name: str = Form(""),
        body: str = Form(""),
        image_path: str = Form(""),
        channel_id: str = Form(""),
        schedule_type: str = Form("yearly"),
        month: str = Form(""),
        day: str = Form(""),
        weekday: str = Form(""),
        time: str = Form("09:00"),
        timezone: str = Form(DEFAULT_TIMEZONE),
        enabled: Optional[str] = Form(None),
    ):
        values = locals()
        return await save_auto_post(request, templates, guild_id, post_id, values)


def normalize_filters(q: Optional[str], enabled: str, has_image: str, channel_id: str) -> Dict[str, str]:
    return {
        "q": (q or "").strip(),
        "enabled": enabled if enabled in ("all", "true", "false") else "all",
        "has_image": has_image if has_image in ("all", "true", "false") else "all",
        "channel_id": channel_id.strip(),
    }


def parse_bool(value: str) -> Optional[bool]:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def list_post_rows(guild_id: str, filters: Dict[str, str]) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = AutoPostRepository(connection)
        posts = repository.list_posts(
            guild_id,
            query=filters["q"] or None,
            enabled=parse_bool(filters["enabled"]),
            has_image=parse_bool(filters["has_image"]),
            channel_id=filters["channel_id"] or None,
        )
    return [build_post_view(post, guild_id) for post in posts]


def build_post_view(post: Dict[str, Any], guild_id: str) -> Dict[str, Any]:
    row = build_form_from_post(post)
    row["body_summary"] = summarize(row["body"])
    row["has_image"] = bool(row["image_path"])
    row["schedule_summary"] = summarize_schedule(row)
    row["next_run_at"] = ""
    row["edit_url"] = "/guilds/{0}/auto-posts/{1}".format(guild_id, row["id"])
    row["toggle_url"] = "/guilds/{0}/auto-posts/{1}/toggle".format(guild_id, row["id"])
    return row


def default_form() -> Dict[str, Any]:
    return {
        "id": None,
        "name": "",
        "body": "",
        "image_path": "",
        "channel_id": "",
        "schedule_type": "yearly",
        "month": "",
        "day": "",
        "weekday": "",
        "time": "09:00",
        "timezone": DEFAULT_TIMEZONE,
        "repeat_rule": "",
        "enabled": True,
        "last_posted_at": None,
        "schedule_value": "",
    }


def build_form_from_post(post: Dict[str, Any]) -> Dict[str, Any]:
    form = default_form()
    schedule = parse_schedule_value(post.get("schedule_value"))
    form.update(
        {
            "id": post.get("id"),
            "name": post.get("name") or "",
            "body": post.get("body") or "",
            "image_path": post.get("image_path") or "",
            "channel_id": post.get("channel_id") or "",
            "schedule_type": post.get("schedule_type") or "yearly",
            "month": str(schedule.get("month") or ""),
            "day": str(schedule.get("day") or ""),
            "weekday": schedule.get("weekday") or "",
            "time": schedule.get("time") or "09:00",
            "timezone": schedule.get("timezone") or DEFAULT_TIMEZONE,
            "repeat_rule": post.get("repeat_rule") or "",
            "enabled": bool(post.get("enabled")),
            "last_posted_at": post.get("last_posted_at"),
            "schedule_value": post.get("schedule_value") or "",
        }
    )
    return form


def parse_schedule_value(value) -> Dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {"legacy_value": str(value)}
    if isinstance(parsed, dict):
        return parsed
    return {"legacy_value": str(value)}


def build_form(values: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str], str]:
    form = default_form()
    form.update(
        {
            "name": str(values.get("name", "")).strip(),
            "body": str(values.get("body", "")).strip(),
            "image_path": str(values.get("image_path", "")).strip(),
            "channel_id": str(values.get("channel_id", "")).strip(),
            "schedule_type": values.get("schedule_type") if values.get("schedule_type") in SCHEDULE_TYPES else "yearly",
            "month": str(values.get("month", "")).strip(),
            "day": str(values.get("day", "")).strip(),
            "weekday": str(values.get("weekday", "")).strip(),
            "time": str(values.get("time", "")).strip(),
            "timezone": str(values.get("timezone", "")).strip() or DEFAULT_TIMEZONE,
            "enabled": values.get("enabled") == "on",
        }
    )

    errors = []
    if not form["name"]:
        errors.append("Post name is required.")
    if not form["body"] and not form["image_path"]:
        errors.append("Body or image path is required.")
    if not form["channel_id"]:
        errors.append("Channel ID is required.")
    if values.get("schedule_type") not in SCHEDULE_TYPES:
        errors.append("Schedule type is invalid.")
    if not TIME_PATTERN.match(form["time"]) or form["time"] > "23:59":
        errors.append("Time must be HH:MM.")

    schedule, schedule_errors = build_schedule_config(form)
    errors.extend(schedule_errors)
    schedule_value = json.dumps(schedule, ensure_ascii=False, sort_keys=True)
    form["schedule_value"] = schedule_value
    return form, errors, schedule_value


def build_schedule_config(form: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    errors = []
    schedule_type = form["schedule_type"]
    config = {
        "type": schedule_type,
        "time": form["time"],
        "timezone": form["timezone"],
    }

    month = parse_int(form["month"])
    day = parse_int(form["day"])
    weekday = form["weekday"]

    if schedule_type in ("once", "yearly"):
        if month is None or month < 1 or month > 12:
            errors.append("Month must be 1-12 for once/yearly schedules.")
        if day is None or day < 1 or day > 31:
            errors.append("Day must be 1-31 for once/yearly schedules.")
        config["month"] = month
        config["day"] = day
    elif schedule_type == "monthly":
        if day is None or day < 1 or day > 31:
            errors.append("Day must be 1-31 for monthly schedules.")
        config["day"] = day
    elif schedule_type == "weekly":
        if weekday not in WEEKDAYS or not weekday:
            errors.append("Weekday is required for weekly schedules.")
        config["weekday"] = weekday

    return config, errors


def parse_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def save_auto_post(
    request: Request,
    templates: Jinja2Templates,
    guild_id: str,
    post_id: Optional[int],
    values: Dict[str, Any],
):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not can_access_guild(guild_id, user["user_id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
    server = find_server(guild_id, user["user_id"])
    if not role_allows(server["role"], "editor"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto post editing denied")

    form, errors, schedule_value = build_form(values)
    with get_connection() as connection:
        repository = AutoPostRepository(connection)
        if post_id is not None and repository.get_by_id(guild_id, post_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto post not found")

        if not errors:
            if post_id is None:
                created = repository.create_post(
                    guild_id,
                    form["name"],
                    form["body"] or None,
                    form["image_path"] or None,
                    form["channel_id"],
                    form["schedule_type"],
                    schedule_value,
                    form["repeat_rule"] or None,
                    form["enabled"],
                )
                connection.commit()
                return RedirectResponse(
                    url="/guilds/{0}/auto-posts/{1}".format(guild_id, created["id"]),
                    status_code=303,
                )

            repository.update_post(
                guild_id,
                post_id,
                form["name"],
                form["body"] or None,
                form["image_path"] or None,
                form["channel_id"],
                form["schedule_type"],
                schedule_value,
                form["repeat_rule"] or None,
                form["enabled"],
            )
            connection.commit()
            return RedirectResponse(url="/guilds/{0}/auto-posts/{1}".format(guild_id, post_id), status_code=303)

    return render_form(
        templates,
        request,
        server,
        guild_id,
        "new" if post_id is None else "edit",
        form,
        errors,
        True,
        post_id=post_id,
        status_code=400,
    )


def summarize(value: str) -> str:
    if not value:
        return "-"
    if len(value) <= 40:
        return value
    return "{0}...".format(value[:40])


def summarize_schedule(row: Dict[str, Any]) -> str:
    schedule_type = row.get("schedule_type") or "yearly"
    time = row.get("time") or "09:00"
    timezone = row.get("timezone") or DEFAULT_TIMEZONE
    if schedule_type in ("once", "yearly"):
        return "{0} / {1}/{2} {3} {4}".format(schedule_type, row.get("month") or "-", row.get("day") or "-", time, timezone)
    if schedule_type == "monthly":
        return "monthly / day {0} {1} {2}".format(row.get("day") or "-", time, timezone)
    if schedule_type == "weekly":
        return "weekly / {0} {1} {2}".format(row.get("weekday") or "-", time, timezone)
    return "daily / {0} {1}".format(time, timezone)


def render_form(
    templates: Jinja2Templates,
    request: Request,
    server: Dict[str, Any],
    guild_id: str,
    mode: str,
    form: Dict[str, Any],
    errors: List[str],
    can_edit: bool,
    post_id: Optional[int] = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "auto_post_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "mode": mode,
            "post_id": post_id,
            "post": form,
            "errors": errors,
            "can_edit": can_edit,
            "schedule_types": SCHEDULE_TYPES,
            "weekdays": WEEKDAYS,
            "schedule_summary": summarize_schedule(form),
        },
        status_code=status_code,
    )
