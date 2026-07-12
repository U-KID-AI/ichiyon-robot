import asyncio
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import discord

from bot import config
from bot.services.voice_audio import (
    cleanup_stale_voice_client,
    get_guild_voice_client,
    get_raw_guild_voice_client,
    is_voice_client_connected,
)

try:
    import yt_dlp
except ImportError:  # pragma: no cover - dependency availability is checked at runtime.
    yt_dlp = None


MUSIC_PLAY_PREFIXES = ("歌え", "流して", "音楽", "play")
MUSIC_SKIP_COMMANDS = {"スキップ", "skip", "次", "次の曲"}
MUSIC_PAUSE_COMMANDS = {"一時停止", "pause"}
MUSIC_RESUME_COMMANDS = {"再開", "resume"}
MUSIC_QUEUE_COMMANDS = {"キュー", "queue", "再生予定"}
MUSIC_NOW_COMMANDS = {"今何", "now", "nowplaying"}
DISCORD_CONNECTION_CLOSED = getattr(discord, "ConnectionClosed", discord.ClientException)
STREAM_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
STREAM_OPTIONS = "-vn"
YTDLP_COOKIES_FILE_ENV = "YTDLP_COOKIES_FILE"
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
}


@dataclass
class MusicTrack:
    title: str
    webpage_url: str
    stream_url: str
    requester_id: str
    duration: Optional[int] = None


@dataclass
class MusicState:
    queue: Deque[MusicTrack] = field(default_factory=deque)
    current: Optional[MusicTrack] = None
    text_channel: Optional[discord.abc.Messageable] = None
    stopping: bool = False


_MUSIC_STATES: Dict[str, MusicState] = {}


def get_music_state(guild_id: str) -> MusicState:
    if guild_id not in _MUSIC_STATES:
        _MUSIC_STATES[guild_id] = MusicState()
    return _MUSIC_STATES[guild_id]


def normalize_music_command(command_text: Optional[str]) -> str:
    return "".join(str(command_text or "").strip().lower().split())


def parse_music_command(command_text: Optional[str]) -> Tuple[Optional[str], str]:
    raw = str(command_text or "").strip()
    normalized = normalize_music_command(raw)
    if normalized in MUSIC_SKIP_COMMANDS:
        return "music_skip", ""
    if normalized in MUSIC_PAUSE_COMMANDS:
        return "music_pause", ""
    if normalized in MUSIC_RESUME_COMMANDS:
        return "music_resume", ""
    if normalized in MUSIC_QUEUE_COMMANDS:
        return "music_queue", ""
    if normalized in MUSIC_NOW_COMMANDS:
        return "music_now", ""

    raw_lower = raw.lower()
    for prefix in MUSIC_PLAY_PREFIXES:
        prefix_lower = prefix.lower()
        if raw_lower == prefix_lower:
            return "music_play", ""
        if raw_lower.startswith(prefix_lower + " "):
            return "music_play", raw[len(prefix) :].strip()
    return None, ""


def is_http_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def build_ytdl_options() -> Dict[str, object]:
    options: Dict[str, object] = dict(YTDL_OPTIONS)
    cookies_file = str(os.getenv(YTDLP_COOKIES_FILE_ENV) or "").strip()
    if cookies_file:
        options["cookiefile"] = cookies_file
    return options


def is_youtube_cookie_required_error(error: Exception) -> bool:
    message = str(error or "").lower()
    return any(
        marker in message
        for marker in (
            "sign in to confirm",
            "not a bot",
            "cookies-from-browser",
            "use --cookies",
            "cookie settings",
            "authentication",
        )
    )


def get_author_voice_channel(message: discord.Message):
    voice_state = getattr(message.author, "voice", None)
    return getattr(voice_state, "channel", None)


def voice_channel_id(voice_client: Optional[discord.VoiceClient]) -> str:
    channel = getattr(voice_client, "channel", None)
    return str(getattr(channel, "id", "") or "")


def log_music_action(
    action: str,
    guild_id: str,
    channel_id: Optional[str] = None,
    requester_id: Optional[str] = None,
    title: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    print(
        "[INFO] voice music {0}: bot_instance_id={1} guild_id={2} channel_id={3} requester_id={4} title={5} reason={6}".format(
            action,
            config.BOT_INSTANCE_ID,
            guild_id,
            channel_id or "",
            requester_id or "",
            title or "",
            reason or "",
        )
    )


def format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return ""
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "{0}:{1:02d}:{2:02d}".format(hours, minutes, sec)
    return "{0}:{1:02d}".format(minutes, sec)


def format_track(track: MusicTrack) -> str:
    duration = format_duration(track.duration)
    suffix = " ({0})".format(duration) if duration else ""
    return "{0}{1}\n{2}".format(track.title, suffix, track.webpage_url)


def format_queue(state: MusicState, limit: int = 10) -> str:
    lines: List[str] = []
    if state.current is not None:
        lines.append("再生中: {0}".format(format_track(state.current)))
    if state.queue:
        lines.append("待機中:")
        for index, track in enumerate(list(state.queue)[:limit], start=1):
            duration = format_duration(track.duration)
            suffix = " ({0})".format(duration) if duration else ""
            lines.append("{0}. {1}{2}".format(index, track.title, suffix))
        remaining = len(state.queue) - limit
        if remaining > 0:
            lines.append("...ほか {0} 件".format(remaining))
    if not lines:
        return "キューは空です。"
    return "\n".join(lines)[:1900]


def format_now_playing(state: MusicState) -> str:
    if state.current is None:
        return "現在再生中の曲はありません。"
    return "現在再生中:\n{0}\nリクエスト: <@{1}>".format(format_track(state.current), state.current.requester_id)


def extract_track_info(url: str, requester_id: str) -> MusicTrack:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    with yt_dlp.YoutubeDL(build_ytdl_options()) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("URL情報を取得できませんでした")
    if "entries" in info:
        entries = [entry for entry in info.get("entries") or [] if entry]
        if not entries:
            raise RuntimeError("再生できる項目がありません")
        info = entries[0]
    stream_url = str(info.get("url") or "").strip()
    if not stream_url:
        raise RuntimeError("ストリームURLを取得できませんでした")
    title = str(info.get("title") or "無題").strip()
    webpage_url = str(info.get("webpage_url") or url).strip()
    duration = info.get("duration")
    try:
        duration_value = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_value = None
    return MusicTrack(title=title, webpage_url=webpage_url, stream_url=stream_url, requester_id=requester_id, duration=duration_value)


async def ensure_music_voice_client(message: discord.Message) -> Optional[discord.VoiceClient]:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return None

    target_channel = get_author_voice_channel(message)
    if target_channel is None:
        await message.channel.send("先にVCに入ってから呼んでください。")
        return None

    guild_id = str(guild.id)
    target_channel_id = str(getattr(target_channel, "id", "") or "")
    voice_client = get_raw_guild_voice_client(guild)
    state = get_music_state(guild_id)
    try:
        if voice_client is not None and not is_voice_client_connected(voice_client):
            log_music_action("join_stale_cleanup", guild_id, target_channel_id, str(getattr(message.author, "id", "") or ""))
            await cleanup_stale_voice_client(voice_client)
            voice_client = None

        if voice_client is None:
            voice_client = await target_channel.connect()
            log_music_action("join", guild_id, target_channel_id, str(getattr(message.author, "id", "") or ""))
            return voice_client

        current_channel = getattr(voice_client, "channel", None)
        if is_voice_client_connected(voice_client) and getattr(current_channel, "id", None) == getattr(target_channel, "id", None):
            return voice_client

        if state.current is not None or voice_client.is_playing() or voice_client.is_paused():
            await message.channel.send("別のVCで再生中です。先に停止してください。")
            log_music_action("move_rejected", guild_id, voice_channel_id(voice_client), reason="already_playing")
            return None

        await voice_client.move_to(target_channel)
        log_music_action("move", guild_id, target_channel_id, str(getattr(message.author, "id", "") or ""))
        return voice_client
    except (
        RuntimeError,
        asyncio.TimeoutError,
        discord.ClientException,
        discord.Forbidden,
        discord.HTTPException,
        DISCORD_CONNECTION_CLOSED,
    ) as exc:
        print("[WARN] voice music connect failed: guild_id={0} channel_id={1} error={2}".format(guild_id, target_channel_id, exc))
        await cleanup_stale_voice_client(get_raw_guild_voice_client(guild))
        await message.channel.send("VCへの接続に失敗しました。権限や接続状態を確認してください。")
        return None
    except Exception as exc:
        print("[WARN] unexpected voice music connect failed: guild_id={0} channel_id={1} error={2}".format(guild_id, target_channel_id, exc))
        await cleanup_stale_voice_client(get_raw_guild_voice_client(guild))
        await message.channel.send("VCへの接続に失敗しました。")
        return None


def _schedule_after_callback(voice_client: discord.VoiceClient, guild_id: str, error: Optional[Exception]) -> None:
    client = getattr(voice_client, "client", None)
    loop = getattr(client, "loop", None)
    if loop is None:
        print("[WARN] voice music callback skipped without client loop: guild_id={0}".format(guild_id))
        return
    future = asyncio.run_coroutine_threadsafe(_handle_track_finished(voice_client, guild_id, error), loop)

    def _log_future_error(done_future) -> None:
        try:
            done_future.result()
        except Exception as exc:  # pragma: no cover - defensive callback logging.
            print("[WARN] voice music finish handler failed: guild_id={0} error={1}".format(guild_id, exc))

    future.add_done_callback(_log_future_error)


async def _handle_track_finished(
    voice_client: discord.VoiceClient,
    guild_id: str,
    error: Optional[Exception],
) -> None:
    state = get_music_state(guild_id)
    channel_id = voice_channel_id(voice_client)
    if error is not None:
        print("[WARN] voice music playback error: guild_id={0} channel_id={1} error={2}".format(guild_id, channel_id, error))
        log_music_action("playback_error", guild_id, channel_id, reason=str(error))
    else:
        log_music_action("play_finish", guild_id, channel_id, title=state.current.title if state.current else "")
    state.current = None
    if state.stopping:
        state.stopping = False
        log_music_action("queue_empty", guild_id, channel_id, reason="stopped")
        return
    await play_next_track(voice_client, guild_id)


async def play_next_track(voice_client: discord.VoiceClient, guild_id: str) -> bool:
    state = get_music_state(guild_id)
    if not is_voice_client_connected(voice_client):
        state.current = None
        log_music_action("queue_empty", guild_id, voice_channel_id(voice_client), reason="not_connected")
        return False
    if not state.queue:
        state.current = None
        log_music_action("queue_empty", guild_id, voice_channel_id(voice_client))
        return False

    track = state.queue.popleft()
    state.current = track
    channel_id = voice_channel_id(voice_client)
    try:
        source = discord.FFmpegPCMAudio(
            track.stream_url,
            before_options=STREAM_BEFORE_OPTIONS,
            options=STREAM_OPTIONS,
        )
        voice_client.play(source, after=lambda error: _schedule_after_callback(voice_client, guild_id, error))
        log_music_action("play_start", guild_id, channel_id, track.requester_id, track.title)
        return True
    except (discord.ClientException, discord.OpusNotLoaded, OSError) as exc:
        print("[WARN] voice music play start failed: guild_id={0} title={1} error={2}".format(guild_id, track.title, exc))
        log_music_action("playback_error", guild_id, channel_id, track.requester_id, track.title, str(exc))
        state.current = None
        return await play_next_track(voice_client, guild_id)


async def enqueue_music_url(message: discord.Message, url: str) -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    if not is_http_url(url):
        await message.channel.send("再生するURLを指定してください。")
        return True

    voice_client = await ensure_music_voice_client(message)
    if voice_client is None:
        return True

    guild_id = str(guild.id)
    requester_id = str(getattr(message.author, "id", "") or "")
    state = get_music_state(guild_id)
    state.text_channel = message.channel
    if state.current is None and (voice_client.is_playing() or voice_client.is_paused()):
        log_music_action("enqueue_rejected", guild_id, voice_channel_id(voice_client), requester_id, reason="already_playing")
        await message.channel.send("現在再生中です。")
        return True

    try:
        track = await asyncio.to_thread(extract_track_info, url, requester_id)
    except Exception as exc:
        print("[WARN] voice music extract failed: guild_id={0} requester_id={1} error={2}".format(guild_id, requester_id, exc))
        if is_youtube_cookie_required_error(exc):
            await message.channel.send("YouTube側の確認要求により取得できませんでした。Cookie設定が必要な可能性があります。")
        else:
            await message.channel.send("URL情報を取得できませんでした。URLや対応サイトを確認してください。")
        return True

    should_start = state.current is None and not (voice_client.is_playing() or voice_client.is_paused())
    state.queue.append(track)
    log_music_action("enqueue", guild_id, voice_channel_id(voice_client), requester_id, track.title)
    if should_start:
        await message.channel.send("再生します: {0}".format(track.title))
        await play_next_track(voice_client, guild_id)
    else:
        await message.channel.send("キューに追加しました: {0}".format(track.title))
    return True


async def skip_music(message: discord.Message) -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    voice_client = get_guild_voice_client(guild)
    state = get_music_state(str(guild.id))
    if voice_client is None or state.current is None:
        await message.channel.send("現在再生中の曲はありません。")
        return True
    log_music_action("skip", str(guild.id), voice_channel_id(voice_client), str(getattr(message.author, "id", "") or ""), state.current.title)
    voice_client.stop()
    await message.channel.send("スキップしました。")
    return True


async def stop_music(message: discord.Message) -> bool:
    guild = message.guild
    if guild is None:
        return False
    guild_id = str(guild.id)
    state = get_music_state(guild_id)
    voice_client = get_guild_voice_client(guild)
    has_music = state.current is not None or bool(state.queue)
    if not has_music:
        return False
    state.queue.clear()
    state.stopping = True
    current_title = state.current.title if state.current else ""
    state.current = None
    if voice_client is not None and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
    log_music_action("stop", guild_id, voice_channel_id(voice_client), str(getattr(message.author, "id", "") or ""), current_title)
    await message.channel.send("再生を停止し、キューをクリアしました。")
    return True


async def pause_music(message: discord.Message) -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    voice_client = get_guild_voice_client(guild)
    if voice_client is None or not voice_client.is_playing():
        await message.channel.send("一時停止できる再生中の曲はありません。")
        return True
    voice_client.pause()
    log_music_action("pause", str(guild.id), voice_channel_id(voice_client), str(getattr(message.author, "id", "") or ""))
    await message.channel.send("一時停止しました。")
    return True


async def resume_music(message: discord.Message) -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    voice_client = get_guild_voice_client(guild)
    if voice_client is None or not voice_client.is_paused():
        await message.channel.send("再開できる一時停止中の曲はありません。")
        return True
    voice_client.resume()
    log_music_action("resume", str(guild.id), voice_channel_id(voice_client), str(getattr(message.author, "id", "") or ""))
    await message.channel.send("再開しました。")
    return True


async def send_music_queue(message: discord.Message) -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    await message.channel.send(format_queue(get_music_state(str(guild.id))))
    return True


async def send_now_playing(message: discord.Message) -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    await message.channel.send(format_now_playing(get_music_state(str(guild.id))))
    return True
