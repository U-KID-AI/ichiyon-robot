import asyncio
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import discord

from bot import config
from bot.db import get_connection
from bot.repositories.tts_settings import (
    DEFAULT_TTS_SPEAKER_ID,
    DEFAULT_TTS_VOLUME_PERCENT,
    TTSSettingsRepository,
    default_tts_settings,
)
from bot.services.voice.ducking import DuckingConfig
from bot.services.voice.mixer import clear_mixer, ensure_mixer_playing, get_mixer
from bot.services.voice.models import TTSItem
from bot.services.voice.session import (
    clear_voice_session_state,
    get_voice_session_state,
    voice_state_key,
)
from bot.services.voice.text_normalizer import (
    is_code_block_only,
    is_url_only_text,
    normalize_tts_text,
    stable_pitch_for_user,
)


VOICEVOX_URL_ENV = "VOICEVOX_ENGINE_URL"
VOICEVOX_TIMEOUT_ENV = "VOICEVOX_TIMEOUT_SECONDS"
VOICEVOX_DEFAULT_URL = "http://voicevox-engine:50021"
VOICEVOX_DEFAULT_TIMEOUT_SECONDS = 10
TTS_START_COMMAND = "読み上げ開始"
TTS_STOP_COMMAND = "読み上げ停止"
TTS_TEMP_PREFIX = "ichiyon-tts-"


@dataclass
class SynthesizedSpeech:
    path: Path
    duration_ms: int


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def voicevox_base_url() -> str:
    return os.getenv(VOICEVOX_URL_ENV, VOICEVOX_DEFAULT_URL).rstrip("/")


def voicevox_timeout_seconds() -> int:
    return _env_int(VOICEVOX_TIMEOUT_ENV, VOICEVOX_DEFAULT_TIMEOUT_SECONDS, 1)


def normalize_tts_command(command_text: Optional[str]) -> str:
    return "".join(str(command_text or "").strip().split())


def classify_tts_command(command_text: Optional[str]) -> Optional[str]:
    normalized = normalize_tts_command(command_text)
    if normalized == TTS_START_COMMAND:
        return "start"
    if normalized == TTS_STOP_COMMAND:
        return "stop"
    return None


def load_tts_settings(guild_id: str) -> Dict[str, Any]:
    try:
        with get_connection() as connection:
            return TTSSettingsRepository(connection).get(guild_id)
    except Exception:
        return default_tts_settings(config.BOT_INSTANCE_ID, guild_id)


def tts_feature_enabled(guild_id: str) -> bool:
    return bool(load_tts_settings(guild_id).get("enabled"))


def tts_auto_join_enabled(guild_id: str) -> bool:
    settings = load_tts_settings(guild_id)
    return bool(settings.get("enabled")) and bool(settings.get("auto_join_enabled"))


def activate_tts_session(guild_id: str, text_channel_id: str, force_enabled: Optional[bool] = None) -> bool:
    settings = load_tts_settings(guild_id)
    enabled = bool(settings.get("enabled"))
    if force_enabled is not None:
        enabled = enabled and bool(force_enabled)
    else:
        enabled = enabled and bool(settings.get("auto_join_enabled"))
    session = get_voice_session_state(guild_id)
    session.bound_text_channel_id = str(text_channel_id or "")
    session.tts_enabled = enabled
    session.generation_id += 1
    print(
        "[INFO] tts session bind: bot_instance_id={0} guild_id={1} channel_id={2} enabled={3}".format(
            config.BOT_INSTANCE_ID,
            guild_id,
            session.bound_text_channel_id,
            enabled,
        )
    )
    return enabled


async def stop_tts_session(guild_id: str) -> None:
    session = get_voice_session_state(guild_id)
    session.tts_enabled = False
    session.tts_queue.clear()
    session.current_tts = None
    session.generation_id += 1
    task = session.tts_worker_task
    if task is not None and hasattr(task, "cancel"):
        task.cancel()
    get_mixer(guild_id).clear_tts(call_after=False)
    print("[INFO] tts stopped: bot_instance_id={0} guild_id={1}".format(config.BOT_INSTANCE_ID, guild_id))


async def reset_voice_session(guild_id: str) -> None:
    await stop_tts_session(guild_id)
    clear_mixer(guild_id)
    clear_voice_session_state(guild_id)


def _attachment_types(message: discord.Message) -> Iterable[str]:
    for attachment in getattr(message, "attachments", []) or []:
        yield str(getattr(attachment, "content_type", "") or "")


def should_tts_read_message(message: discord.Message, command_text: Optional[str]) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    if getattr(message, "webhook_id", None):
        return False
    if getattr(message, "guild", None) is None:
        return False
    if command_text is not None:
        return False
    content = str(getattr(message, "content", "") or "")
    if not content.strip() and not (getattr(message, "attachments", []) or []):
        return False
    if is_code_block_only(content):
        return False
    if is_url_only_text(content):
        return False
    return True


def synthesize_voicevox_to_file(text: str, settings: Dict[str, Any], pitch_scale: float) -> SynthesizedSpeech:
    started = time.perf_counter()
    speaker_id = int(settings.get("speaker_id") or DEFAULT_TTS_SPEAKER_ID)
    timeout = voicevox_timeout_seconds()
    base_url = voicevox_base_url()
    query_params = urllib.parse.urlencode({"text": text, "speaker": speaker_id})
    query_request = urllib.request.Request(
        "{0}/audio_query?{1}".format(base_url, query_params),
        data=b"",
        method="POST",
    )
    with urllib.request.urlopen(query_request, timeout=timeout) as response:
        query = json.loads(response.read().decode("utf-8"))
    query["speedScale"] = float(settings.get("speed_scale") or 1.0)
    query["pitchScale"] = float(query.get("pitchScale") or 0.0) + float(pitch_scale)
    query["volumeScale"] = 1.0
    synth_params = urllib.parse.urlencode({"speaker": speaker_id})
    synth_request = urllib.request.Request(
        "{0}/synthesis?{1}".format(base_url, synth_params),
        data=json.dumps(query).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(synth_request, timeout=timeout) as response:
        wav_bytes = response.read()
    handle = tempfile.NamedTemporaryFile(prefix=TTS_TEMP_PREFIX, suffix=".wav", delete=False)
    path = Path(handle.name)
    try:
        handle.write(wav_bytes)
    finally:
        handle.close()
    return SynthesizedSpeech(path=path, duration_ms=max(0, int((time.perf_counter() - started) * 1000)))


def cleanup_tts_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        if path.exists():
            path.unlink()
    except Exception as exc:
        print("[WARN] tts temp cleanup failed: path_suffix={0} error={1}".format(path.suffix, type(exc).__name__))


async def _play_tts_item(message: discord.Message, item: TTSItem, settings: Dict[str, Any]) -> None:
    guild = message.guild
    if guild is None or str(getattr(guild, "id", "") or "") != item.guild_id:
        return
    voice_client = getattr(guild, "voice_client", None)
    is_connected = getattr(voice_client, "is_connected", None)
    if voice_client is None or not callable(is_connected) or not is_connected():
        return
    session = get_voice_session_state(item.guild_id)
    if item.generation_id != session.generation_id or not session.tts_enabled:
        return
    pitch = stable_pitch_for_user(item.author_id, float(settings.get("pitch_variation") or 0.0)) if settings.get("user_pitch_enabled") else 0.0
    try:
        speech = await asyncio.to_thread(synthesize_voicevox_to_file, item.text, settings, pitch)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        print(
            "[WARN] tts synth skipped: bot_instance_id={0} guild_id={1} channel_id={2} error={3}".format(
                config.BOT_INSTANCE_ID,
                item.guild_id,
                item.channel_id,
                type(exc).__name__,
            )
        )
        return
    if item.generation_id != session.generation_id or not session.tts_enabled:
        cleanup_tts_file(speech.path)
        return
    done = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _after(error: Optional[Exception]) -> None:
        cleanup_tts_file(speech.path)
        if error is not None:
            print("[WARN] tts playback error: bot_instance_id={0} guild_id={1} error={2}".format(config.BOT_INSTANCE_ID, item.guild_id, type(error).__name__))
        loop.call_soon_threadsafe(done.set)

    try:
        source = discord.FFmpegPCMAudio(str(speech.path))
        mixer = get_mixer(item.guild_id)
        mixer.set_tts_volume(float(settings.get("tts_volume_percent") or DEFAULT_TTS_VOLUME_PERCENT) / 100.0)
        mixer.configure_ducking(
            DuckingConfig(
                enabled=bool(settings.get("ducking_enabled")),
                music_gain=float(settings.get("ducking_music_gain") or 0.5),
                attack_ms=int(settings.get("ducking_attack_ms") or 100),
                release_ms=int(settings.get("ducking_release_ms") or 300),
            )
        )
        mixer.set_tts_source(source, _after)
        ensure_mixer_playing(voice_client, item.guild_id)
        print(
            "[INFO] tts play_start: bot_instance_id={0} guild_id={1} channel_id={2} synth_ms={3}".format(
                config.BOT_INSTANCE_ID,
                item.guild_id,
                item.channel_id,
                speech.duration_ms,
            )
        )
        await done.wait()
    except Exception as exc:
        cleanup_tts_file(speech.path)
        print("[WARN] tts playback skipped: bot_instance_id={0} guild_id={1} error={2}".format(config.BOT_INSTANCE_ID, item.guild_id, type(exc).__name__))


async def _tts_worker(message: discord.Message, guild_id: str, generation_id: int) -> None:
    session = get_voice_session_state(guild_id)
    while session.tts_enabled and session.generation_id == generation_id:
        if not session.tts_queue:
            session.tts_worker_task = None
            return
        item = session.tts_queue.popleft()
        if item.generation_id != generation_id:
            continue
        session.current_tts = item
        settings = load_tts_settings(guild_id)
        await _play_tts_item(message, item, settings)
        session.current_tts = None
    session.tts_worker_task = None


async def maybe_enqueue_tts(message: discord.Message, command_text: Optional[str]) -> bool:
    if not should_tts_read_message(message, command_text):
        return False
    guild_id = str(getattr(message.guild, "id", "") or "")
    channel_id = str(getattr(message.channel, "id", "") or "")
    session = get_voice_session_state(guild_id)
    if not session.tts_enabled or session.bound_text_channel_id != channel_id:
        return False
    settings = load_tts_settings(guild_id)
    if not settings.get("enabled"):
        return False
    text = normalize_tts_text(str(getattr(message, "content", "") or ""), _attachment_types(message), int(settings.get("max_text_length") or 300))
    if not text:
        return False
    queue_limit = int(settings.get("queue_limit") or 50)
    if len(session.tts_queue) >= queue_limit:
        print("[WARN] tts queue full: bot_instance_id={0} guild_id={1} channel_id={2} limit={3}".format(config.BOT_INSTANCE_ID, guild_id, channel_id, queue_limit))
        return False
    item = TTSItem(
        text=text,
        author_id=str(getattr(message.author, "id", "") or ""),
        guild_id=guild_id,
        channel_id=channel_id,
        generation_id=session.generation_id,
    )
    session.tts_queue.append(item)
    if session.tts_worker_task is None or getattr(session.tts_worker_task, "done", lambda: True)():
        session.tts_worker_task = asyncio.create_task(_tts_worker(message, guild_id, session.generation_id))
    return True


async def handle_tts_command(message: discord.Message, command_text: Optional[str]) -> bool:
    command = classify_tts_command(command_text)
    if command is None:
        return False
    guild = getattr(message, "guild", None)
    if guild is None:
        await message.channel.send("読み上げコマンドはサーバー内で使ってください。")
        return True
    guild_id = str(guild.id)
    if command == "stop":
        await stop_tts_session(guild_id)
        await message.channel.send("読み上げを停止しました。")
        return True
    activate_tts_session(guild_id, str(getattr(message.channel, "id", "") or ""), force_enabled=True)
    await message.channel.send("このチャンネルの読み上げを開始しました。")
    return True
