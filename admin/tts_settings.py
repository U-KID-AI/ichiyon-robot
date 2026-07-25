from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import get_current_user
from admin.bot_context import selected_bot_id
from admin.servers import can_access_guild, find_server, role_allows
from bot.db import get_connection
from bot.repositories.tts_settings import (
    DEFAULT_DUCKING_ATTACK_MS,
    DEFAULT_DUCKING_MUSIC_GAIN,
    DEFAULT_DUCKING_RELEASE_MS,
    DEFAULT_TTS_CREDIT_TEXT,
    DEFAULT_TTS_MAX_TEXT_LENGTH,
    DEFAULT_TTS_PITCH_VARIATION,
    DEFAULT_TTS_QUEUE_LIMIT,
    DEFAULT_TTS_SPEAKER_ID,
    DEFAULT_TTS_SPEAKER_NAME,
    DEFAULT_TTS_SPEED_SCALE,
    DEFAULT_TTS_VOLUME_PERCENT,
    TTSSettingsRepository,
)


router = APIRouter()


def register_tts_setting_routes(templates: Jinja2Templates) -> None:
    @router.get("/guilds/{guild_id}/tts-settings")
    async def tts_settings_page(request: Request, guild_id: str, saved: Optional[str] = None, error: Optional[str] = None):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        with get_connection() as connection:
            settings = TTSSettingsRepository(connection, bot_id=bot_id).get(guild_id)
        return templates.TemplateResponse(
            request,
            "tts_settings.html",
            {
                "user": user,
                "guild_id": guild_id,
                "server": server,
                "bot_id": bot_id,
                "settings": settings,
                "saved": saved,
                "error": error,
                "can_edit": role_allows(server["role"], "editor"),
                "voicevox_credit_note": "VOICEVOXで生成した音声は、利用した話者名を含むクレジット表記が必要です。",
            },
        )

    @router.post("/guilds/{guild_id}/tts-settings")
    async def save_tts_settings(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tts settings edit denied")
        form = await request.form()
        values, errors = parse_tts_settings_form(dict(form))
        if errors:
            return RedirectResponse(url="/guilds/{0}/tts-settings?error={1}".format(guild_id, "validation"), status_code=303)
        with get_connection() as connection:
            TTSSettingsRepository(connection, bot_id=bot_id).upsert(guild_id, values)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}/tts-settings?saved=1".format(guild_id), status_code=303)

    @router.post("/guilds/{guild_id}/tts-settings/toggle")
    async def toggle_tts_settings(request: Request, guild_id: str):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        bot_id = selected_bot_id(request)
        if not can_access_guild(guild_id, user["user_id"], bot_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="guild access denied")
        server = find_server(guild_id, user["user_id"], bot_id)
        if not role_allows(server["role"], "editor"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tts settings toggle denied")
        with get_connection() as connection:
            repository = TTSSettingsRepository(connection, bot_id=bot_id)
            settings = repository.get(guild_id)
            values = dict(settings)
            values["enabled"] = not bool(settings.get("enabled"))
            repository.upsert(guild_id, values)
            connection.commit()
        return RedirectResponse(url="/guilds/{0}".format(guild_id), status_code=303)


def _bool_form(form: Dict[str, Any], key: str) -> bool:
    return str(form.get(key) or "").lower() in ("on", "true", "1", "yes")


def _int_form(form: Dict[str, Any], key: str, default: int, minimum: int, maximum: int, errors: List[str]) -> int:
    try:
        value = int(str(form.get(key) or default).strip())
    except ValueError:
        errors.append(key)
        return default
    if value < minimum or value > maximum:
        errors.append(key)
        return default
    return value


def _float_form(form: Dict[str, Any], key: str, default: float, minimum: float, maximum: float, errors: List[str]) -> float:
    try:
        value = float(str(form.get(key) or default).strip())
    except ValueError:
        errors.append(key)
        return default
    if value < minimum or value > maximum:
        errors.append(key)
        return default
    return value


def parse_tts_settings_form(form: Dict[str, Any]):
    errors: List[str] = []
    speaker_id = _int_form(form, "speaker_id", DEFAULT_TTS_SPEAKER_ID, 0, 10000, errors)
    values = {
        "enabled": _bool_form(form, "enabled"),
        "auto_join_enabled": _bool_form(form, "auto_join_enabled"),
        "speaker_id": speaker_id,
        "speaker_name": str(form.get("speaker_name") or DEFAULT_TTS_SPEAKER_NAME).strip()[:100],
        "tts_volume_percent": _int_form(form, "tts_volume_percent", DEFAULT_TTS_VOLUME_PERCENT, 0, 100, errors),
        "speed_scale": _float_form(form, "speed_scale", DEFAULT_TTS_SPEED_SCALE, 0.5, 2.0, errors),
        "user_pitch_enabled": _bool_form(form, "user_pitch_enabled"),
        "pitch_variation": _float_form(form, "pitch_variation", DEFAULT_TTS_PITCH_VARIATION, 0.0, 0.2, errors),
        "max_text_length": _int_form(form, "max_text_length", DEFAULT_TTS_MAX_TEXT_LENGTH, 1, 1000, errors),
        "queue_limit": _int_form(form, "queue_limit", DEFAULT_TTS_QUEUE_LIMIT, 1, 200, errors),
        "ducking_enabled": _bool_form(form, "ducking_enabled"),
        "ducking_music_gain": _float_form(form, "ducking_music_gain", DEFAULT_DUCKING_MUSIC_GAIN, 0.0, 1.0, errors),
        "ducking_attack_ms": _int_form(form, "ducking_attack_ms", DEFAULT_DUCKING_ATTACK_MS, 0, 5000, errors),
        "ducking_release_ms": _int_form(form, "ducking_release_ms", DEFAULT_DUCKING_RELEASE_MS, 0, 5000, errors),
        "credit_text": str(form.get("credit_text") or DEFAULT_TTS_CREDIT_TEXT).strip()[:200],
    }
    return values, errors
