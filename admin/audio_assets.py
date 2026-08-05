import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import current_selected_bot_id, selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories.audio_assets import AudioAssetRepository
from bot.services.voice_audio import (
    MANAGED_AUDIO_ROOT,
    build_audio_asset_storage_path,
    resolve_audio_asset_storage_path,
    validate_audio_asset_file,
)


router = APIRouter()


def register_audio_asset_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/audio-assets")
    async def audio_assets_page(request: Request, guild_id: str, message: str = "", error: str = ""):
        user, server = require_audio_access(request, guild_id)
        with get_connection() as connection:
            assets = AudioAssetRepository(connection, bot_id=current_selected_bot_id()).list_assets(guild_id)
        return templates.TemplateResponse(
            request,
            "audio_assets.html",
            {
                "user": user,
                "server": server,
                "guild_id": guild_id,
                "assets": assets,
                "can_edit": role_allows(server["role"], "editor"),
                "message": message,
                "error": error,
            },
        )

    @router.get("/guilds/{guild_id}/audio-assets/new")
    async def new_audio_asset_page(request: Request, guild_id: str):
        user, server = require_audio_editor(request, guild_id)
        return render_audio_form(templates, request, server, guild_id, default_audio_form(), "new")

    @router.post("/guilds/{guild_id}/audio-assets/new")
    async def create_audio_asset(
        request: Request,
        guild_id: str,
        display_name: str = Form(""),
        description: str = Form(""),
        category: str = Form(""),
        default_volume: str = Form("50"),
        enabled: Optional[str] = Form(None),
        audio_file: UploadFile = File(...),
    ):
        user, server = require_audio_editor(request, guild_id)
        form = collect_audio_form(display_name, description, category, default_volume, enabled)
        errors = validate_audio_form(form, require_file=True)
        saved_path = None
        if not errors:
            try:
                saved_path, duration_ms, mime_type = save_uploaded_audio(guild_id, audio_file)
                form.update(
                    {
                        "storage_path": saved_path,
                        "original_filename": audio_file.filename or "",
                        "mime_type": mime_type,
                        "duration_ms": duration_ms,
                    }
                )
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            return render_audio_form(templates, request, server, guild_id, form, "new", errors, status_code=400)
        with get_connection() as connection:
            AudioAssetRepository(connection, bot_id=current_selected_bot_id()).create_asset(guild_id, form)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/audio-assets?message=created".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/audio-assets/{asset_id}")
    async def edit_audio_asset_page(request: Request, guild_id: str, asset_id: int):
        user, server = require_audio_access(request, guild_id)
        with get_connection() as connection:
            asset = AudioAssetRepository(connection, bot_id=current_selected_bot_id()).get_asset(guild_id, asset_id)
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audio asset not found")
        return render_audio_form(
            templates,
            request,
            server,
            guild_id,
            form_from_asset(asset),
            "edit",
            can_edit=role_allows(server["role"], "editor"),
            asset_id=asset_id,
        )

    @router.post("/guilds/{guild_id}/audio-assets/{asset_id}")
    async def update_audio_asset(
        request: Request,
        guild_id: str,
        asset_id: int,
        display_name: str = Form(""),
        description: str = Form(""),
        category: str = Form(""),
        default_volume: str = Form("50"),
        enabled: Optional[str] = Form(None),
        audio_file: Optional[UploadFile] = File(None),
    ):
        user, server = require_audio_editor(request, guild_id)
        form = collect_audio_form(display_name, description, category, default_volume, enabled)
        errors = validate_audio_form(form, require_file=False)
        if audio_file is not None and audio_file.filename:
            try:
                storage_path, duration_ms, mime_type = save_uploaded_audio(guild_id, audio_file)
                form.update(
                    {
                        "storage_path": storage_path,
                        "original_filename": audio_file.filename or "",
                        "mime_type": mime_type,
                        "duration_ms": duration_ms,
                    }
                )
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            form["id"] = asset_id
            return render_audio_form(templates, request, server, guild_id, form, "edit", errors, asset_id=asset_id, status_code=400)
        with get_connection() as connection:
            updated = AudioAssetRepository(connection, bot_id=current_selected_bot_id()).update_asset(guild_id, asset_id, form)
            if updated is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audio asset not found")
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/audio-assets?message=updated".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/audio-assets/{asset_id}/toggle")
    async def toggle_audio_asset(request: Request, guild_id: str, asset_id: int):
        user, server = require_audio_editor(request, guild_id)
        with get_connection() as connection:
            repo = AudioAssetRepository(connection, bot_id=current_selected_bot_id())
            asset = repo.get_asset(guild_id, asset_id)
            if asset is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audio asset not found")
            repo.set_enabled(guild_id, asset_id, not bool(asset.get("enabled")))
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/audio-assets".format(guild_id), status_code=303)

    @router.get("/guilds/{guild_id}/audio-assets/{asset_id}/preview")
    async def preview_audio_asset(request: Request, guild_id: str, asset_id: int):
        user, server = require_audio_access(request, guild_id)
        with get_connection() as connection:
            asset = AudioAssetRepository(connection, bot_id=current_selected_bot_id()).get_asset(guild_id, asset_id)
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audio asset not found")
        path = resolve_audio_asset_storage_path(str(asset.get("storage_path") or ""))
        if path is None or not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audio file not found")
        return FileResponse(path, media_type=str(asset.get("mime_type") or "application/octet-stream"))


def require_audio_access(request: Request, guild_id: str):
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    bot_id = selected_bot_id(request)
    if not can_access_guild(guild_id, user["user_id"], bot_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
    return user, find_server(guild_id, user["user_id"], bot_id)


def require_audio_editor(request: Request, guild_id: str):
    user, server = require_audio_access(request, guild_id)
    if not role_allows(server["role"], "editor"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="audio asset edit denied")
    return user, server


def default_audio_form():
    return {
        "display_name": "",
        "description": "",
        "category": "",
        "default_volume": 50,
        "enabled": True,
    }


def collect_audio_form(display_name, description, category, default_volume, enabled):
    return {
        "display_name": str(display_name or "").strip(),
        "description": str(description or "").strip(),
        "category": str(category or "").strip(),
        "default_volume": parse_volume(default_volume),
        "enabled": enabled == "on",
    }


def form_from_asset(asset):
    form = default_audio_form()
    form.update(asset)
    return form


def parse_volume(value) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 50


def validate_audio_form(form, require_file: bool):
    errors = []
    if not form["display_name"]:
        errors.append("表示名を入力してください。")
    if form["default_volume"] < 0 or form["default_volume"] > 100:
        errors.append("音量は0〜100で指定してください。")
    if require_file and not form.get("storage_path"):
        pass
    return errors


def save_uploaded_audio(guild_id: str, upload: UploadFile):
    if upload is None or not upload.filename:
        raise ValueError("音声ファイルを選択してください。")
    bot_id = current_selected_bot_id()
    storage_path = build_audio_asset_storage_path(bot_id, guild_id, upload.filename)
    destination = resolve_audio_asset_storage_path(storage_path)
    if destination is None:
        raise ValueError("保存先を作成できません。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        shutil.copyfileobj(upload.file, output)
    try:
        duration_ms, mime_type = validate_audio_asset_file(destination)
    except Exception as exc:
        try:
            destination.unlink()
        except OSError:
            pass
        raise ValueError(str(exc)) from exc
    return storage_path, duration_ms, mime_type


def render_audio_form(templates, request, server, guild_id, form, mode, errors=None, can_edit=True, asset_id=None, status_code=200):
    return templates.TemplateResponse(
        request,
        "audio_asset_form.html",
        {
            "user": get_current_user(request),
            "server": server,
            "guild_id": guild_id,
            "asset": form,
            "mode": mode,
            "errors": errors or [],
            "can_edit": can_edit,
            "asset_id": asset_id,
            "audio_root": str(MANAGED_AUDIO_ROOT),
        },
        status_code=status_code,
    )
