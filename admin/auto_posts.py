import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from admin.ux import is_test_data, parse_show_test_data, save_uploaded_image
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
        show_test_data: str = Query("false"),
        message: str = Query(""),
        error: str = Query(""),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")

        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        filters = normalize_filters(q, enabled, has_image, channel_id, show_test_data)
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
                "message": message,
                "error": error,
            },
        )

    @router.post("/guilds/{guild_id}/auto-posts/bulk-enabled")
    async def bulk_set_auto_posts_enabled(
        request: Request,
        guild_id: str,
        action: str = Form(""),
        post_ids: List[int] = Form([]),
    ):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto post bulk update denied")
        # TODO(v3): bot_id権限を導入したら、guild権限だけでなくBot単位の操作権限も確認する。
        if not post_ids:
            return RedirectResponse(
                url="/guilds/{0}/auto-posts?error={1}".format(guild_id, quote("項目を選択してね")),
                status_code=303,
            )
        if action not in ("on", "off"):
            return RedirectResponse(
                url="/guilds/{0}/auto-posts?error={1}".format(guild_id, quote("操作を選んでね")),
                status_code=303,
            )
        with get_connection() as connection:
            repository = AutoPostRepository(connection, bot_id=current_selected_bot_id())
            updated_count = repository.bulk_set_enabled(guild_id, post_ids, action == "on")
            connection.commit()
        failed_count = max(0, len(post_ids) - updated_count)
        return RedirectResponse(
            url="/guilds/{0}/auto-posts?message={1}".format(
                guild_id,
                quote("成功{0}件 / 失敗{1}件".format(updated_count, failed_count)),
            ),
            status_code=303,
        )

    @router.post("/guilds/{guild_id}/auto-posts/{post_id}/toggle")
    async def toggle_auto_post(request: Request, guild_id: str, post_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto post toggle denied")

        with get_connection() as connection:
            repository = AutoPostRepository(connection, bot_id=current_selected_bot_id())
            if repository.get_by_id(guild_id, post_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto post not found")
            repository.toggle_enabled(guild_id, post_id)
            connection.commit()

        return RedirectResponse(url="/guilds/{0}/auto-posts".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/auto-posts/{post_id}/copy")
    async def copy_auto_post(request: Request, guild_id: str, post_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto post copy denied")

        with get_connection() as connection:
            repository = AutoPostRepository(connection, bot_id=current_selected_bot_id())
            copied = repository.copy_post(guild_id, post_id)
            if copied is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auto post not found")
            connection.commit()

        return RedirectResponse(
            url="/guilds/{0}/auto-posts/{1}".format(guild_id, copied["id"]),
            status_code=303,
        )

    @router.post("/guilds/{guild_id}/auto-posts/{post_id}/delete")
    async def delete_auto_post(request: Request, guild_id: str, post_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="サーバーを見る権限がありません。")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="削除する権限がありません。")
        with get_connection() as connection:
            repository = AutoPostRepository(connection, bot_id=current_selected_bot_id())
            if repository.get_by_id(guild_id, post_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="自動投稿が見つかりません。")
            repository.delete_post(guild_id, post_id)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/auto-posts".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/auto-posts/new")
    async def new_auto_post_page(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))
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
        image_upload: Optional[UploadFile] = File(None),
        delete_image: Optional[str] = Form(None),
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
        if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], selected_bot_id(request))

        with get_connection() as connection:
            repository = AutoPostRepository(connection, bot_id=current_selected_bot_id())
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
        image_upload: Optional[UploadFile] = File(None),
        delete_image: Optional[str] = Form(None),
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


def normalize_filters(
    q: Optional[str],
    enabled: str,
    has_image: str,
    channel_id: str,
    show_test_data: str = "false",
) -> Dict[str, Any]:
    return {
        "q": (q or "").strip(),
        "enabled": enabled if enabled in ("all", "true", "false") else "all",
        "has_image": has_image if has_image in ("all", "true", "false") else "all",
        "channel_id": channel_id.strip(),
        "show_test_data": parse_show_test_data(show_test_data),
    }


def parse_bool(value: str) -> Optional[bool]:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def list_post_rows(guild_id: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        repository = AutoPostRepository(connection, bot_id=current_selected_bot_id())
        posts = repository.list_posts(
            guild_id,
            query=filters["q"] or None,
            enabled=parse_bool(filters["enabled"]),
            has_image=parse_bool(filters["has_image"]),
            channel_id=filters["channel_id"] or None,
        )
    return [
        build_post_view(post, guild_id)
        for post in posts
        if filters["show_test_data"] or not row_is_hidden_test_data(post)
    ]


def build_post_view(post: Dict[str, Any], guild_id: str) -> Dict[str, Any]:
    row = build_form_from_post(post)
    row["body_summary"] = summarize(row["body"])
    row["has_image"] = bool(row["image_path"])
    row["schedule_summary"] = summarize_schedule(row)
    row["next_run_at"] = ""
    row["edit_url"] = "/guilds/{0}/auto-posts/{1}".format(guild_id, row["id"])
    row["toggle_url"] = "/guilds/{0}/auto-posts/{1}/toggle".format(guild_id, row["id"])
    row["copy_url"] = "/guilds/{0}/auto-posts/{1}/copy".format(guild_id, row["id"])
    row["delete_url"] = "/guilds/{0}/auto-posts/{1}/delete".format(guild_id, row["id"])
    return row


def row_is_hidden_test_data(row: Dict[str, Any]) -> bool:
    return is_test_data(row.get("name")) or is_test_data(row.get("body"))


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
        errors.append("投稿名を入力。")
    if not form["body"] and not form["image_path"]:
        errors.append("本文か画像を入力。")
    if not form["channel_id"]:
        errors.append("投稿チャンネルIDを入力。")
    if values.get("schedule_type") not in SCHEDULE_TYPES:
        errors.append("スケジュール種別を選択。")
    if not TIME_PATTERN.match(form["time"]) or form["time"] > "23:59":
        errors.append("時刻は HH:MM 形式。")

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
            errors.append("月は1〜12。")
        if day is None or day < 1 or day > 31:
            errors.append("日は1〜31。")
        config["month"] = month
        config["day"] = day
    elif schedule_type == "monthly":
        if day is None or day < 1 or day > 31:
            errors.append("日は1〜31。")
        config["day"] = day
    elif schedule_type == "weekly":
        if weekday not in WEEKDAYS or not weekday:
            errors.append("曜日を選択。")
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
    if not can_access_guild(guild_id, user["user_id"], selected_bot_id(request)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
    server = find_server(guild_id, user["user_id"], selected_bot_id(request))
    if not role_allows(server["role"], "editor"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auto post editing denied")

    if values.get("delete_image"):
        values["image_path"] = ""
    uploaded_path, upload_error = await save_uploaded_image(values.get("image_upload"), "auto_posts")
    if uploaded_path:
        values["image_path"] = uploaded_path
    form, errors, schedule_value = build_form(values)
    if upload_error:
        errors.append(upload_error)
    with get_connection() as connection:
        repository = AutoPostRepository(connection, bot_id=current_selected_bot_id())
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
