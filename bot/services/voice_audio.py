import json
import mimetypes
import os
import re
import subprocess
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import discord

from bot import config
from bot.db import get_connection
from bot.repositories.audio_assets import AudioAssetRepository
from bot.repositories.music_settings import DEFAULT_FOREGROUND_VOLUME_PERCENT
from bot.services.voice.mixer import ensure_mixer_playing, get_mixer
from bot.services.voice.session import voice_state_key


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AUDIO_ROOT = (PROJECT_ROOT / "assets" / "audio").resolve()
MANAGED_AUDIO_ROOT = Path(os.getenv("AUDIO_ASSETS_DIR") or PROJECT_ROOT / "data" / "audio-assets").resolve()
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a"}
DEFAULT_AUDIO_ASSET_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_AUDIO_ASSET_MAX_DURATION_SECONDS = 300
_FOREGROUND_QUEUES: Dict[str, Deque[Dict[str, Any]]] = {}
_FOREGROUND_ACTIVE: Dict[str, bool] = {}


def list_audio_files() -> List[Path]:
    if not AUDIO_ROOT.exists() or not AUDIO_ROOT.is_dir():
        return []
    files = [
        path
        for path in AUDIO_ROOT.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
    ]
    return sorted(files, key=lambda path: path.name.lower())


def format_audio_file_list(files: List[Path]) -> str:
    if not files:
        return "登録されている音声ファイルがありません。"
    lines = ["登録されている音声ファイル:"]
    lines.extend("- {0} ({1})".format(path.stem, path.name) for path in files)
    return "\n".join(lines)


def resolve_audio_file(name: str) -> Optional[Path]:
    requested = str(name or "").strip()
    if not requested:
        return None
    raw_path = Path(requested)
    if raw_path.name != requested or raw_path.is_absolute():
        return None

    suffix = raw_path.suffix.lower()
    if suffix:
        if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
            return None
        candidates = [AUDIO_ROOT / raw_path.name]
    else:
        candidates = [AUDIO_ROOT / "{0}{1}".format(requested, ext) for ext in sorted(SUPPORTED_AUDIO_EXTENSIONS)]

    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(AUDIO_ROOT)
        except ValueError:
            continue
        if resolved.is_file() and resolved.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
            return resolved
    return None


def audio_asset_max_bytes() -> int:
    try:
        return max(1, int(os.getenv("AUDIO_ASSET_MAX_BYTES") or DEFAULT_AUDIO_ASSET_MAX_BYTES))
    except ValueError:
        return DEFAULT_AUDIO_ASSET_MAX_BYTES


def audio_asset_max_duration_seconds() -> int:
    try:
        return max(1, int(os.getenv("AUDIO_ASSET_MAX_DURATION_SECONDS") or DEFAULT_AUDIO_ASSET_MAX_DURATION_SECONDS))
    except ValueError:
        return DEFAULT_AUDIO_ASSET_MAX_DURATION_SECONDS


def sanitize_audio_filename(filename: str) -> str:
    name = Path(str(filename or "")).name
    stem = Path(name).stem
    suffix = Path(name).suffix.lower()
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    if not safe_stem:
        safe_stem = "audio"
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        suffix = ".bin"
    return "{0}{1}".format(safe_stem[:80], suffix)


def build_audio_asset_storage_path(bot_id: str, guild_id: str, original_filename: str) -> str:
    safe_name = sanitize_audio_filename(original_filename)
    return "{0}/{1}/{2}_{3}".format(bot_id, guild_id, uuid.uuid4().hex, safe_name)


def resolve_audio_asset_storage_path(storage_path: str) -> Optional[Path]:
    raw = str(storage_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        return None
    candidate = (MANAGED_AUDIO_ROOT / raw).resolve()
    try:
        candidate.relative_to(MANAGED_AUDIO_ROOT)
    except ValueError:
        return None
    return candidate


def guess_audio_mime_type(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def probe_audio_duration_ms(path: Path) -> int:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, shell=False)
    if result.returncode != 0:
        raise ValueError("ffprobe rejected audio file")
    try:
        duration_seconds = float((result.stdout or "").strip())
    except ValueError as exc:
        raise ValueError("ffprobe returned invalid duration") from exc
    if duration_seconds <= 0:
        raise ValueError("audio duration must be positive")
    return int(duration_seconds * 1000)


def validate_audio_asset_file(path: Path) -> Tuple[int, str]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError("audio file not found")
    if resolved.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        raise ValueError("unsupported audio extension")
    size = resolved.stat().st_size
    if size <= 0:
        raise ValueError("audio file is empty")
    if size > audio_asset_max_bytes():
        raise ValueError("audio file is too large")
    duration_ms = probe_audio_duration_ms(resolved)
    if duration_ms > audio_asset_max_duration_seconds() * 1000:
        raise ValueError("audio file is too long")
    return duration_ms, guess_audio_mime_type(resolved)


def get_guild_voice_client(guild: Optional[discord.Guild]) -> Optional[discord.VoiceClient]:
    voice_client = get_raw_guild_voice_client(guild)
    if not is_voice_client_connected(voice_client):
        return None
    return voice_client


def get_raw_guild_voice_client(guild: Optional[discord.Guild]) -> Optional[discord.VoiceClient]:
    if guild is None:
        return None
    voice_client = getattr(guild, "voice_client", None)
    if isinstance(voice_client, discord.VoiceClient):
        return voice_client
    return voice_client


def is_voice_client_connected(voice_client: Optional[discord.VoiceClient]) -> bool:
    if voice_client is None:
        return False
    is_connected = getattr(voice_client, "is_connected", None)
    if not callable(is_connected):
        return False
    try:
        return bool(is_connected())
    except Exception:
        return False


async def cleanup_stale_voice_client(voice_client: Optional[discord.VoiceClient]) -> None:
    if voice_client is None or is_voice_client_connected(voice_client):
        return
    try:
        await voice_client.disconnect(force=True)
    except TypeError:
        try:
            await voice_client.disconnect()
        except Exception as exc:
            print("[WARN] stale voice client cleanup failed: error={0}".format(exc))
    except Exception as exc:
        print("[WARN] stale voice client cleanup failed: error={0}".format(exc))


def log_voice_audio(
    action: str,
    guild_id: str,
    channel_id: Optional[str],
    filename: Optional[str] = None,
    reaction_type: Optional[str] = None,
    reaction_key: Optional[str] = None,
    skipped_reason: Optional[str] = None,
) -> None:
    print(
        "[INFO] voice audio {0}: bot_instance_id={1} guild_id={2} channel_id={3} reaction_type={4} reaction_key={5} filename={6} skipped_reason={7}".format(
            action,
            config.BOT_INSTANCE_ID,
            guild_id,
            channel_id or "",
            reaction_type or "",
            reaction_key or "",
            filename or "",
            skipped_reason or "",
        )
    )


def normalize_audio_config(config_value: Any) -> Dict[str, Any]:
    if isinstance(config_value, dict):
        return config_value
    if isinstance(config_value, str):
        try:
            parsed = json.loads(config_value)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def extract_audio_file_from_config(config_value: Any) -> str:
    audio_config = normalize_audio_config(config_value)
    direct = str(audio_config.get("audio_file") or "").strip()
    if direct:
        return direct
    voice = audio_config.get("voice")
    if isinstance(voice, dict):
        nested = str(voice.get("audio_file") or "").strip()
        if nested:
            return nested
    return ""


def extract_audio_asset_id_from_config(config_value: Any) -> Optional[int]:
    audio_config = normalize_audio_config(config_value)
    raw_value = audio_config.get("audio_asset_id")
    if raw_value in (None, ""):
        voice = audio_config.get("voice")
        if isinstance(voice, dict):
            raw_value = voice.get("audio_asset_id")
    if raw_value in (None, ""):
        return None
    try:
        asset_id = int(raw_value)
    except (TypeError, ValueError):
        return None
    return asset_id if asset_id > 0 else None


def extract_audio_volume_percent_from_config(config_value: Any) -> Optional[int]:
    audio_config = normalize_audio_config(config_value)
    raw_value = audio_config.get("volume_percent")
    if raw_value in (None, ""):
        voice = audio_config.get("voice")
        if isinstance(voice, dict):
            raw_value = voice.get("volume_percent")
    if raw_value in (None, ""):
        return None
    try:
        volume = int(raw_value)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, volume))


def extract_reaction_audio_file(row: Dict[str, Any]) -> str:
    for key in ("audio_config_json", "config_json"):
        audio_file = extract_audio_file_from_config(row.get(key))
        if audio_file:
            return audio_file
    return extract_audio_file_from_config(row)


def extract_reaction_audio_asset_id(row: Dict[str, Any]) -> Optional[int]:
    for key in ("audio_config_json", "config_json"):
        asset_id = extract_audio_asset_id_from_config(row.get(key))
        if asset_id is not None:
            return asset_id
    return extract_audio_asset_id_from_config(row)


def extract_reaction_audio_volume_percent(row: Dict[str, Any]) -> Optional[int]:
    for key in ("audio_config_json", "config_json"):
        volume = extract_audio_volume_percent_from_config(row.get(key))
        if volume is not None:
            return volume
    return extract_audio_volume_percent_from_config(row)


def _voice_channel_id(voice_client: discord.VoiceClient) -> str:
    current_channel = getattr(voice_client, "channel", None)
    return str(getattr(current_channel, "id", "") or "")


def play_audio_on_voice_client(
    voice_client: discord.VoiceClient,
    audio_path: Path,
    guild_id: str,
    channel_id: str,
    reaction_type: Optional[str] = None,
    reaction_key: Optional[str] = None,
) -> Tuple[bool, str]:
    filename = audio_path.name
    if not is_voice_client_connected(voice_client):
        log_voice_audio(
            "play_skipped",
            guild_id,
            channel_id,
            filename,
            reaction_type,
            reaction_key,
            "not_connected",
        )
        return False, "not_connected"

    def after_playback(error: Optional[Exception]) -> None:
        if error is not None:
            print(
                "[WARN] voice playback error: guild_id={0} channel_id={1} bot_instance_id={2} filename={3} error={4}".format(
                    guild_id,
                    channel_id,
                    config.BOT_INSTANCE_ID,
                    filename,
                    error,
                )
            )
            return
        log_voice_audio(
            "play_complete",
            guild_id,
            channel_id,
            filename,
            reaction_type,
            reaction_key,
        )

    try:
        raw_source = discord.FFmpegPCMAudio(str(audio_path))
        source = discord.PCMVolumeTransformer(raw_source, volume=DEFAULT_FOREGROUND_VOLUME_PERCENT / 100.0)
        voice_client.play(source, after=after_playback)
        log_voice_audio(
            "play_start",
            guild_id,
            channel_id,
            filename,
            reaction_type,
            reaction_key,
        )
        return True, "played"
    except (discord.ClientException, discord.OpusNotLoaded, OSError) as exc:
        log_voice_audio(
            "play_skipped",
            guild_id,
            channel_id,
            filename,
            reaction_type,
            reaction_key,
            "playback_error",
        )
        print(
            "[WARN] voice playback start failed: guild_id={0} channel_id={1} filename={2} error={3}".format(
                guild_id,
                channel_id,
                filename,
                exc,
            )
        )
        return False, "playback_error"


def _foreground_queue(guild_id: str) -> Deque[Dict[str, Any]]:
    key = voice_state_key(guild_id)
    if key not in _FOREGROUND_QUEUES:
        _FOREGROUND_QUEUES[key] = deque()
    return _FOREGROUND_QUEUES[key]


def _start_next_foreground_audio(voice_client: discord.VoiceClient, guild_id: str) -> None:
    key = voice_state_key(guild_id)
    if _FOREGROUND_ACTIVE.get(key):
        return
    queue = _foreground_queue(guild_id)
    if not queue:
        _FOREGROUND_ACTIVE[key] = False
        return
    item = queue.popleft()
    audio_path = item["audio_path"]
    channel_id = item.get("channel_id") or _voice_channel_id(voice_client)
    filename = audio_path.name
    if not is_voice_client_connected(voice_client):
        log_voice_audio("play_skipped", guild_id, channel_id, filename, item.get("reaction_type"), item.get("reaction_key"), "not_connected")
        _FOREGROUND_ACTIVE[key] = False
        return

    def after_playback(error: Optional[Exception]) -> None:
        if error is not None:
            print(
                "[WARN] foreground audio playback error: guild_id={0} channel_id={1} bot_instance_id={2} filename={3} error={4}".format(
                    guild_id,
                    channel_id,
                    config.BOT_INSTANCE_ID,
                    filename,
                    type(error).__name__,
                )
            )
        else:
            log_voice_audio("play_complete", guild_id, channel_id, filename, item.get("reaction_type"), item.get("reaction_key"))
        _FOREGROUND_ACTIVE[key] = False
        _start_next_foreground_audio(voice_client, guild_id)

    try:
        source = discord.FFmpegPCMAudio(str(audio_path))
        mixer = get_mixer(guild_id)
        mixer.set_tts_volume(max(0.0, min(1.0, int(item.get("volume_percent") or DEFAULT_FOREGROUND_VOLUME_PERCENT) / 100.0)))
        mixer.set_tts_source(source, after_playback)
        ensure_mixer_playing(voice_client, guild_id)
        _FOREGROUND_ACTIVE[key] = True
        log_voice_audio("play_start", guild_id, channel_id, filename, item.get("reaction_type"), item.get("reaction_key"))
    except (discord.ClientException, discord.OpusNotLoaded, OSError) as exc:
        _FOREGROUND_ACTIVE[key] = False
        log_voice_audio("play_skipped", guild_id, channel_id, filename, item.get("reaction_type"), item.get("reaction_key"), "playback_error")
        print(
            "[WARN] foreground audio playback start failed: guild_id={0} channel_id={1} filename={2} error={3}".format(
                guild_id,
                channel_id,
                filename,
                type(exc).__name__,
            )
        )
        _start_next_foreground_audio(voice_client, guild_id)


def enqueue_foreground_audio(
    voice_client: discord.VoiceClient,
    audio_path: Path,
    guild_id: str,
    channel_id: str,
    volume_percent: Optional[int] = None,
    reaction_type: Optional[str] = None,
    reaction_key: Optional[str] = None,
) -> Tuple[bool, str]:
    if not is_voice_client_connected(voice_client):
        log_voice_audio("play_skipped", guild_id, channel_id, audio_path.name, reaction_type, reaction_key, "not_connected")
        return False, "not_connected"
    if audio_path is None or not audio_path.is_file():
        log_voice_audio("play_skipped", guild_id, channel_id, str(audio_path), reaction_type, reaction_key, "file_not_found")
        return False, "file_not_found"
    _foreground_queue(guild_id).append(
        {
            "audio_path": audio_path,
            "channel_id": channel_id,
            "volume_percent": volume_percent if volume_percent is not None else DEFAULT_FOREGROUND_VOLUME_PERCENT,
            "reaction_type": reaction_type,
            "reaction_key": reaction_key,
        }
    )
    _start_next_foreground_audio(voice_client, guild_id)
    return True, "queued"


def stop_foreground_audio(guild_id: str) -> None:
    key = voice_state_key(guild_id)
    _FOREGROUND_QUEUES.pop(key, None)
    _FOREGROUND_ACTIVE[key] = False
    mixer = get_mixer(guild_id)
    mixer.clear_tts(call_after=False)


def resolve_audio_asset_path_from_row(asset: Dict[str, Any]) -> Optional[Path]:
    return resolve_audio_asset_storage_path(str(asset.get("storage_path") or ""))


async def play_audio_asset_row(
    guild: discord.Guild,
    asset: Dict[str, Any],
    volume_percent: Optional[int] = None,
    reaction_type: Optional[str] = None,
    reaction_key: Optional[str] = None,
) -> Tuple[bool, str]:
    guild_id = str(getattr(guild, "id", "") or "")
    voice_client = get_guild_voice_client(guild)
    if voice_client is None:
        log_voice_audio("play_skipped", guild_id, None, str(asset.get("id") or ""), reaction_type, reaction_key, "not_connected")
        return False, "not_connected"
    audio_path = resolve_audio_asset_path_from_row(asset)
    if audio_path is None or not audio_path.is_file():
        log_voice_audio("play_skipped", guild_id, _voice_channel_id(voice_client), str(asset.get("id") or ""), reaction_type, reaction_key, "file_not_found")
        return False, "file_not_found"
    return enqueue_foreground_audio(
        voice_client,
        audio_path,
        guild_id,
        _voice_channel_id(voice_client),
        volume_percent if volume_percent is not None else int(asset.get("default_volume") or DEFAULT_FOREGROUND_VOLUME_PERCENT),
        reaction_type,
        reaction_key,
    )


async def play_audio_asset_by_id(
    message: discord.Message,
    asset_id: int,
    volume_percent: Optional[int] = None,
    reaction_type: str = "audio_asset",
    reaction_key: str = "",
) -> Tuple[bool, str]:
    guild = getattr(message, "guild", None)
    if guild is None:
        return False, "no_guild"
    guild_id = str(guild.id)
    with get_connection() as connection:
        asset = AudioAssetRepository(connection).get_asset(guild_id, int(asset_id), enabled=True)
    if asset is None:
        return False, "asset_not_found"
    return await play_audio_asset_row(guild, asset, volume_percent, reaction_type, reaction_key or str(asset_id))


async def play_reaction_audio(
    message: discord.Message,
    audio_file: str,
    reaction_type: str,
    reaction_key: str,
) -> Tuple[bool, str]:
    if not audio_file:
        return False, "not_configured"

    guild = getattr(message, "guild", None)
    guild_id = str(getattr(guild, "id", "") or "")
    voice_client = get_guild_voice_client(guild)
    if voice_client is None:
        log_voice_audio("play_skipped", guild_id, None, audio_file, reaction_type, reaction_key, "not_connected")
        return False, "not_connected"

    channel_id = _voice_channel_id(voice_client)
    audio_path = resolve_audio_file(audio_file)
    if audio_path is None:
        log_voice_audio("play_skipped", guild_id, channel_id, audio_file, reaction_type, reaction_key, "file_not_found")
        return False, "file_not_found"

    return enqueue_foreground_audio(voice_client, audio_path, guild_id, channel_id, DEFAULT_FOREGROUND_VOLUME_PERCENT, reaction_type, reaction_key)
