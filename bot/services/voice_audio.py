import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import discord

from bot import config


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AUDIO_ROOT = (PROJECT_ROOT / "assets" / "audio").resolve()
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg"}


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


def extract_reaction_audio_file(row: Dict[str, Any]) -> str:
    for key in ("audio_config_json", "config_json"):
        audio_file = extract_audio_file_from_config(row.get(key))
        if audio_file:
            return audio_file
    return extract_audio_file_from_config(row)


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
        source = discord.FFmpegPCMAudio(str(audio_path))
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
    if voice_client.is_playing() or voice_client.is_paused():
        log_voice_audio("play_skipped", guild_id, channel_id, audio_file, reaction_type, reaction_key, "already_playing")
        return False, "already_playing"

    audio_path = resolve_audio_file(audio_file)
    if audio_path is None:
        log_voice_audio("play_skipped", guild_id, channel_id, audio_file, reaction_type, reaction_key, "file_not_found")
        return False, "file_not_found"

    return play_audio_on_voice_client(voice_client, audio_path, guild_id, channel_id, reaction_type, reaction_key)
