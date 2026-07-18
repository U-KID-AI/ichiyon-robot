import logging
from typing import Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories.youtube_n_pull import YouTubeNPullRepository, normalize_command_name
from bot.services.youtube_n_pull import fetch_source_videos, is_youtube_source_url


router = APIRouter()
logger = logging.getLogger(__name__)
ADMIN_REFRESH_ERROR_MESSAGE = "キャッシュ更新に失敗しました。管理者ログを確認してください。"


def register_youtube_n_pull_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/youtube-n-pull")
    async def youtube_n_pull_page(request: Request, guild_id: str, message: str = "", error: str = ""):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        with get_connection() as connection:
            repository = YouTubeNPullRepository(connection, bot_id=current_selected_bot_id())
            presets = repository.list_presets(guild_id)
        return templates.TemplateResponse(
            request,
            "youtube_n_pull.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "presets": presets,
                "can_edit": role_allows(server["role"], "editor"),
                "message": message,
                "error": error,
            },
        )

    @router.get("/guilds/{guild_id}/youtube-n-pull/new")
    async def new_youtube_n_pull_page(request: Request, guild_id: str):
        user, server = require_editor(request, guild_id)
        return render_form(templates, request, server, guild_id, "new", default_form(), [], [], True)

    @router.post("/guilds/{guild_id}/youtube-n-pull/new")
    async def create_youtube_n_pull(
        request: Request,
        guild_id: str,
        display_name: str = Form(""),
        command_name: str = Form(""),
        aliases: str = Form(""),
        category: str = Form(""),
        enabled: Optional[str] = Form(None),
        max_pulls: str = Form("100"),
        cache_ttl_hours: str = Form("24"),
        include_shorts: Optional[str] = Form(None),
        include_live: Optional[str] = Form(None),
        include_archived_live: Optional[str] = Form(None),
        min_duration_seconds: str = Form(""),
        max_duration_seconds: str = Form(""),
        include_title_terms: str = Form(""),
        exclude_title_terms: str = Form(""),
        sources_text: str = Form(""),
    ):
        form = collect_form_values(locals())
        return save_youtube_n_pull(request, templates, guild_id, None, form)

    @router.get("/guilds/{guild_id}/youtube-n-pull/{preset_id}")
    async def edit_youtube_n_pull_page(request: Request, guild_id: str, preset_id: int):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        with get_connection() as connection:
            repository = YouTubeNPullRepository(connection, bot_id=current_selected_bot_id())
            preset = repository.get_preset(guild_id, preset_id)
            if preset is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="youtube n pull preset not found")
            sources = repository.list_sources(preset_id)
            preview = repository.list_cache_preview(preset_id, 20)
        return render_form(
            templates,
            request,
            server,
            guild_id,
            "edit",
            form_from_preset(preset, sources),
            [],
            preview,
            role_allows(server["role"], "editor"),
            preset_id=preset_id,
        )

    @router.post("/guilds/{guild_id}/youtube-n-pull/{preset_id}")
    async def update_youtube_n_pull(
        request: Request,
        guild_id: str,
        preset_id: int,
        display_name: str = Form(""),
        command_name: str = Form(""),
        aliases: str = Form(""),
        category: str = Form(""),
        enabled: Optional[str] = Form(None),
        max_pulls: str = Form("100"),
        cache_ttl_hours: str = Form("24"),
        include_shorts: Optional[str] = Form(None),
        include_live: Optional[str] = Form(None),
        include_archived_live: Optional[str] = Form(None),
        min_duration_seconds: str = Form(""),
        max_duration_seconds: str = Form(""),
        include_title_terms: str = Form(""),
        exclude_title_terms: str = Form(""),
        sources_text: str = Form(""),
    ):
        form = collect_form_values(locals())
        return save_youtube_n_pull(request, templates, guild_id, preset_id, form)

    @router.post("/guilds/{guild_id}/youtube-n-pull/{preset_id}/toggle")
    async def toggle_youtube_n_pull(request: Request, guild_id: str, preset_id: int):
        user, server = require_editor(request, guild_id)
        with get_connection() as connection:
            repository = YouTubeNPullRepository(connection, bot_id=current_selected_bot_id())
            if repository.toggle_preset(guild_id, preset_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="youtube n pull preset not found")
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/youtube-n-pull".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/youtube-n-pull/{preset_id}/delete")
    async def delete_youtube_n_pull(request: Request, guild_id: str, preset_id: int):
        user, server = require_editor(request, guild_id)
        with get_connection() as connection:
            repository = YouTubeNPullRepository(connection, bot_id=current_selected_bot_id())
            if not repository.delete_preset(guild_id, preset_id):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="youtube n pull preset not found")
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/youtube-n-pull".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/youtube-n-pull/{preset_id}/refresh")
    async def refresh_youtube_n_pull(request: Request, guild_id: str, preset_id: int):
        user, server = require_editor(request, guild_id)
        bot_id = current_selected_bot_id()
        with get_connection() as connection:
            repository = YouTubeNPullRepository(connection, bot_id=bot_id)
            preset = repository.get_preset(guild_id, preset_id)
            if preset is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="youtube n pull preset not found")
            sources = repository.list_sources(preset_id, enabled=True)
            videos = []
            try:
                for source in sources:
                    videos.extend(fetch_source_videos(source, guild_id, preset))
                dedup = {}
                for video in videos:
                    dedup.setdefault(video["video_id"], video)
                if not dedup:
                    raise RuntimeError("no valid youtube videos found")
                repository.replace_cache_videos(preset_id, list(dedup.values()))
                repository.mark_cache_refresh(preset_id, "")
                connection.commit()
                message = quote("キャッシュを更新しました: {0}件".format(len(dedup)))
                return RedirectResponse(url="/guilds/{0}/youtube-n-pull?message={1}".format(guild_id, message), status_code=303)
            except Exception as exc:
                connection.rollback()
                logger.exception(
                    "youtube_n_pull admin refresh failed: bot_instance_id=%s guild_id=%s preset_id=%s error_type=%s error=%s",
                    bot_id,
                    guild_id,
                    preset_id,
                    type(exc).__name__,
                    str(exc),
                )
                try:
                    repository.mark_cache_refresh(preset_id, type(exc).__name__)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    logger.exception(
                        "youtube_n_pull admin refresh error mark failed: bot_instance_id=%s guild_id=%s preset_id=%s",
                        bot_id,
                        guild_id,
                        preset_id,
                    )
                error = quote(ADMIN_REFRESH_ERROR_MESSAGE)
                return RedirectResponse(url="/guilds/{0}/youtube-n-pull?error={1}".format(guild_id, error), status_code=303)


def require_editor(request: Request, guild_id: str):
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    bot_id = selected_bot_id(request)
    if not can_access_guild(guild_id, user["user_id"], bot_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
    server = find_server(guild_id, user["user_id"], bot_id)
    if not role_allows(server["role"], "editor"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="youtube n pull edit denied")
    return user, server


def default_form() -> Dict:
    return {
        "display_name": "",
        "command_name": "",
        "aliases": "",
        "category": "",
        "enabled": True,
        "max_pulls": "100",
        "cache_ttl_hours": "24",
        "include_shorts": False,
        "include_live": False,
        "include_archived_live": False,
        "min_duration_seconds": "",
        "max_duration_seconds": "",
        "include_title_terms": "",
        "exclude_title_terms": "",
        "sources_text": "",
    }


def form_from_preset(preset: Dict, sources: List[Dict]) -> Dict:
    form = default_form()
    form.update(
        {
            "display_name": preset.get("display_name") or "",
            "command_name": preset.get("command_name") or "",
            "aliases": preset.get("aliases") or "",
            "category": preset.get("category") or "",
            "enabled": bool(preset.get("enabled")),
            "max_pulls": str(preset.get("max_pulls") or 100),
            "cache_ttl_hours": str(int(preset.get("cache_ttl_seconds") or 86400) // 3600),
            "include_shorts": bool(preset.get("include_shorts")),
            "include_live": bool(preset.get("include_live")),
            "include_archived_live": bool(preset.get("include_archived_live")),
            "min_duration_seconds": "" if preset.get("min_duration_seconds") is None else str(preset.get("min_duration_seconds")),
            "max_duration_seconds": "" if preset.get("max_duration_seconds") is None else str(preset.get("max_duration_seconds")),
            "include_title_terms": preset.get("include_title_terms") or "",
            "exclude_title_terms": preset.get("exclude_title_terms") or "",
            "sources_text": "\n".join(source_line(source) for source in sources),
        }
    )
    return form


def source_line(source: Dict) -> str:
    parts = [source.get("source_type") or "channel", source.get("source_url") or ""]
    if not source.get("enabled", True):
        parts.append("off")
    return ", ".join(parts)


def collect_form_values(values: Dict) -> Dict:
    form = default_form()
    for key in form:
        if key in values:
            form[key] = values[key]
    form["display_name"] = str(form["display_name"] or "").strip()
    form["command_name"] = str(form["command_name"] or "").strip()
    form["aliases"] = str(form["aliases"] or "").strip()
    form["category"] = str(form["category"] or "").strip()
    form["enabled"] = values.get("enabled") == "on"
    form["include_shorts"] = values.get("include_shorts") == "on"
    form["include_live"] = values.get("include_live") == "on"
    form["include_archived_live"] = values.get("include_archived_live") == "on"
    form["sources"] = parse_sources(str(values.get("sources_text") or ""))
    return form


def parse_int(value: str, default: Optional[int] = None) -> Optional[int]:
    value = str(value or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_sources(value: str) -> List[Dict]:
    sources = []
    for index, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 1:
            source_type, source_url = infer_source_type(parts[0]), parts[0]
            enabled = True
        else:
            source_type, source_url = parts[0].lower(), parts[1]
            enabled = not any(part.lower() == "off" for part in parts[2:])
        sources.append({"source_type": source_type, "source_url": source_url, "priority": index * 100, "enabled": enabled})
    return sources


def infer_source_type(url: str) -> str:
    return "playlist" if "list=" in url or "/playlist" in url else "channel"


def validate_form(form: Dict) -> List[str]:
    errors = []
    if not form["display_name"]:
        errors.append("表示名を入力してください。")
    if not form["command_name"]:
        errors.append("コマンド名を入力してください。")
    max_pulls = parse_int(form["max_pulls"])
    if max_pulls is None or max_pulls < 1 or max_pulls > 100:
        errors.append("最大Nは1〜100で入力してください。")
    cache_ttl_hours = parse_int(form["cache_ttl_hours"])
    if cache_ttl_hours is None or cache_ttl_hours < 1:
        errors.append("キャッシュ時間は1時間以上で入力してください。")
    min_duration = parse_int(form["min_duration_seconds"])
    max_duration = parse_int(form["max_duration_seconds"])
    if min_duration is not None and min_duration < 0:
        errors.append("最小動画時間は0以上で入力してください。")
    if max_duration is not None and max_duration < 0:
        errors.append("最大動画時間は0以上で入力してください。")
    if min_duration is not None and max_duration is not None and min_duration > max_duration:
        errors.append("最小動画時間は最大動画時間以下にしてください。")
    for source in form.get("sources") or []:
        if source["source_type"] not in ("channel", "playlist"):
            errors.append("ソース種別は channel または playlist にしてください。")
        if not is_youtube_source_url(source["source_url"]):
            errors.append("YouTubeのURLだけ登録できます: {0}".format(source["source_url"]))
    if form["enabled"] and not [source for source in form.get("sources") or [] if source.get("enabled")]:
        errors.append("有効にする場合は、少なくとも1件の有効なソースを登録してください。")
    return errors


def repository_values(form: Dict) -> Dict:
    return {
        "display_name": form["display_name"],
        "command_name": form["command_name"],
        "aliases": form["aliases"],
        "category": form["category"],
        "enabled": bool(form["enabled"]),
        "max_pulls": parse_int(form["max_pulls"], 100),
        "cache_ttl_seconds": parse_int(form["cache_ttl_hours"], 24) * 3600,
        "include_shorts": bool(form["include_shorts"]),
        "include_live": bool(form["include_live"]),
        "include_archived_live": bool(form["include_archived_live"]),
        "min_duration_seconds": parse_int(form["min_duration_seconds"]),
        "max_duration_seconds": parse_int(form["max_duration_seconds"]),
        "include_title_terms": form["include_title_terms"],
        "exclude_title_terms": form["exclude_title_terms"],
    }


def save_youtube_n_pull(request: Request, templates: Jinja2Templates, guild_id: str, preset_id: Optional[int], form: Dict):
    user, server = require_editor(request, guild_id)
    errors = validate_form(form)
    if errors:
        return render_form(templates, request, server, guild_id, "new" if preset_id is None else "edit", form, errors, [], True, preset_id)

    with get_connection() as connection:
        repository = YouTubeNPullRepository(connection, bot_id=current_selected_bot_id())
        try:
            if preset_id is None:
                preset = repository.create_preset(guild_id, repository_values(form))
                preset_id = int(preset["id"])
            else:
                preset = repository.update_preset(guild_id, preset_id, repository_values(form))
                if preset is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="youtube n pull preset not found")
            repository.replace_sources(preset_id, form["sources"])
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return RedirectResponse(url="/guilds/{0}/youtube-n-pull/{1}".format(guild_id, preset_id), status_code=303)


def render_form(
    templates: Jinja2Templates,
    request: Request,
    server: Dict,
    guild_id: str,
    mode: str,
    form: Dict,
    errors: List[str],
    preview: List[Dict],
    can_edit: bool,
    preset_id: Optional[int] = None,
):
    return templates.TemplateResponse(
        request,
        "youtube_n_pull_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "mode": mode,
            "preset_id": preset_id,
            "form": form,
            "errors": errors,
            "preview": preview,
            "can_edit": can_edit,
            "command_key": normalize_command_name(form.get("command_name") or ""),
        },
    )
