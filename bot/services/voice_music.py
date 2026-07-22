import asyncio
import os
import random
import re
import shutil
import tempfile
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

import discord

from bot import config
from bot.db import get_connection
from bot.messages import get_bot
from bot.repositories.music_settings import (
    DEFAULT_MUSIC_VOLUME_PERCENT,
    MusicSettingsRepository,
)
from bot.services.spotify_client import (
    SpotifyApiError,
    SpotifyCredentialsMissing,
    SpotifyError,
    SpotifyRateLimitedError,
    SpotifyTrackMetadata,
    get_spotify_client,
)
from bot.services.spotify_link import SpotifyLink, parse_spotify_link
from bot.services.spotify_resolver import (
    SpotifyResolveError,
    get_album_lock,
    invalidate_resolve_cache,
    remove_album_lock,
    resolve_concurrency,
    resolve_spotify_track_to_youtube,
)
from bot.services.youtube_cookie_monitor import (
    AUTH_FAILURE_STATUSES,
    COOKIE_STATUS_VIDEO_UNAVAILABLE,
    classify_ytdlp_error,
    format_cookie_monitor_status,
    handle_transient_auth_failure,
)
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
MUSIC_SKIP_COUNT_MIN = 1
MUSIC_SKIP_COUNT_MAX = 100
MUSIC_SKIP_INVALID_COUNT_MESSAGE = "スキップできる曲数は1～100曲です。"
MUSIC_SKIP_INVALID_FORMAT_MESSAGE = "スキップする曲数を1～100で指定してください。"
MUSIC_LOOP_RANGE_MIN = 1
MUSIC_LOOP_RANGE_MAX = 100
MUSIC_LOOP_INVALID_RANGE_MESSAGE = "ループする曲数は1～100曲で指定してください。"
MUSIC_PAUSE_COMMANDS = {"一時停止", "pause"}
MUSIC_RESUME_COMMANDS = {"再開", "resume"}
MUSIC_QUEUE_COMMANDS = {"キュー", "queue", "再生予定"}
MUSIC_NOW_COMMANDS = {"今何", "now", "nowplaying"}
MUSIC_LOOP_STATUS_COMMANDS = {"ループ"}
MUSIC_LOOP_ONE_COMMANDS = {"1曲ループ"}
MUSIC_LOOP_QUEUE_COMMANDS = {"キューループ"}
MUSIC_LOOP_OFF_COMMANDS = {"ループ解除"}
MUSIC_SHUFFLE_COMMANDS = {"シャッフル"}
MUSIC_VOLUME_COMMAND = "音量"
MUSIC_YOUTUBE_STATUS_COMMAND = "youtube状態"
MUSIC_LOOP_OFF = "off"
MUSIC_LOOP_ONE = "one"
MUSIC_LOOP_QUEUE = "queue"
MENTION_MUSIC_LINK_LIMIT = 3
MUSIC_LINK_TRAILING_CHARS = ".,!?、。)]）＞>"
HTTP_URL_PATTERN = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
SPOTIFY_URI_PATTERN = re.compile(r"spotify:(?:track|album|playlist|episode|show|artist):[A-Za-z0-9]+", re.IGNORECASE)
DISCORD_CONNECTION_CLOSED = getattr(discord, "ConnectionClosed", discord.ClientException)
STREAM_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
STREAM_OPTIONS = "-vn"
YTDLP_COOKIES_FILE_ENV = "YTDLP_COOKIES_FILE"
YTDLP_COOKIES_TMP_DIR = Path(tempfile.gettempdir())
DEFAULT_YTDLP_JS_RUNTIME = "deno"
SUPPORTED_YTDLP_JS_RUNTIMES = {"deno", "node"}
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "js_runtimes": {DEFAULT_YTDLP_JS_RUNTIME: {}},
}
YTDLP_STAGE_PATTERNS = [
    ("challenge", ("solving js challenges", "challenge solver", "n challenge", "[jsc:", "ejs")),
    ("player_js", ("downloading player", "player_", "base.js", "player javascript")),
    ("player_api", ("player api json", "youtubei", "api json")),
    ("manifest", ("downloading m3u8", "m3u8 information", "dash manifest", "mpd manifest", "manifest")),
    ("webpage", ("downloading webpage", "web client config", "initial data")),
    ("format", ("downloading 1 format", "format(s)", "format sorting")),
    ("cache", ("cache", "cached")),
]


@dataclass
class MusicTrack:
    title: str
    webpage_url: str
    stream_url: str
    requester_id: str
    duration: Optional[int] = None
    source_url: Optional[str] = None
    refresh_required: bool = False
    source_type: str = "youtube"
    original_spotify_url: str = ""
    spotify_title: str = ""
    spotify_artists: str = ""
    enqueued_at_monotonic: float = 0.0


@dataclass
class MusicState:
    queue: Deque[MusicTrack] = field(default_factory=deque)
    loop_queue: Deque[MusicTrack] = field(default_factory=deque)
    current: Optional[MusicTrack] = None
    text_channel: Optional[discord.abc.Messageable] = None
    stopping: bool = False
    skip_requested: bool = False
    loop_mode: str = MUSIC_LOOP_OFF
    loop_range_size: Optional[int] = None
    music_volume_percent: Optional[int] = None


_MUSIC_STATES: Dict[str, MusicState] = {}
_VOICE_CONNECT_LOCKS: Dict[str, asyncio.Lock] = {}


def music_state_key(guild_id: str) -> str:
    return "{0}:{1}".format(config.BOT_INSTANCE_ID, guild_id)


def get_music_state(guild_id: str) -> MusicState:
    key = music_state_key(guild_id)
    if key not in _MUSIC_STATES:
        _MUSIC_STATES[key] = MusicState()
    return _MUSIC_STATES[key]


def get_voice_connect_lock(guild_id: str) -> asyncio.Lock:
    key = music_state_key(guild_id)
    if key not in _VOICE_CONNECT_LOCKS:
        _VOICE_CONNECT_LOCKS[key] = asyncio.Lock()
    return _VOICE_CONNECT_LOCKS[key]


def clear_music_state(guild_id: str) -> None:
    _MUSIC_STATES.pop(music_state_key(guild_id), None)


def perf_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def mark_track_enqueued(track: MusicTrack) -> MusicTrack:
    track.enqueued_at_monotonic = time.perf_counter()
    return track


def queue_wait_ms(track: MusicTrack) -> Optional[int]:
    if not track.enqueued_at_monotonic:
        return None
    return max(0, int((time.perf_counter() - track.enqueued_at_monotonic) * 1000))


def format_music_timing_fields(fields: Dict[str, object]) -> str:
    safe_items = []
    for key in sorted(fields):
        value = fields[key]
        if value is None:
            continue
        safe_items.append("{0}={1}".format(key, value))
    return " ".join(safe_items)


def log_music_timing(
    action: str,
    guild_id: str,
    channel_id: Optional[str] = None,
    requester_id: Optional[str] = None,
    **fields: object,
) -> None:
    print(
        "[INFO] voice music timing {0}: bot_instance_id={1} guild_id={2} channel_id={3} requester_id={4} {5}".format(
            action,
            config.BOT_INSTANCE_ID,
            guild_id,
            channel_id or "",
            requester_id or "",
            format_music_timing_fields(fields),
        ).rstrip()
    )


def extract_youtube_video_id(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.lower()
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0]
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        query_id = parse_qs(parsed.query).get("v", [""])[0]
        if query_id:
            return query_id
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
            return parts[1]
    return ""


def classify_ytdlp_stage(message: str) -> Optional[str]:
    text = str(message or "").lower()
    for stage, markers in YTDLP_STAGE_PATTERNS:
        if any(marker in text for marker in markers):
            return stage
    return None


class YoutubeExtractStageRecorder:
    def __init__(self, video_id: str = "") -> None:
        self.video_id = video_id or "-"
        self.extract_started: Optional[float] = None
        self.current_stage: Optional[str] = None
        self.current_stage_started: Optional[float] = None
        self.stage_elapsed_ms: Dict[str, int] = {}
        self.option_build_ms: Optional[int] = None
        self.cookie_prep_ms: Optional[int] = None
        self.result_processing_ms: Optional[int] = None
        self.extract_info_ms: Optional[int] = None
        self.messages_seen = 0

    def debug(self, message: str) -> None:
        self.record_message(message)

    def warning(self, message: str) -> None:
        self.record_message(message)

    def error(self, message: str) -> None:
        self.record_message(message)

    def record_message(self, message: str) -> None:
        self.messages_seen += 1
        stage = classify_ytdlp_stage(message)
        if stage is None:
            return
        now = time.perf_counter()
        if self.extract_started is None:
            self.extract_started = now
        if self.current_stage == stage:
            return
        self._close_current_stage(now)
        self.current_stage = stage
        self.current_stage_started = now

    def _close_current_stage(self, now: Optional[float] = None) -> None:
        if self.current_stage is None or self.current_stage_started is None:
            return
        ended = now if now is not None else time.perf_counter()
        elapsed = max(0, int((ended - self.current_stage_started) * 1000))
        self.stage_elapsed_ms[self.current_stage] = self.stage_elapsed_ms.get(self.current_stage, 0) + elapsed
        self.current_stage = None
        self.current_stage_started = None

    def set_option_build_ms(self, elapsed_ms: int) -> None:
        self.option_build_ms = max(0, int(elapsed_ms))

    def add_cookie_prep_ms(self, elapsed_ms: int) -> None:
        self.cookie_prep_ms = (self.cookie_prep_ms or 0) + max(0, int(elapsed_ms))

    def finish_extract_info(self, elapsed_ms: int) -> None:
        self._close_current_stage()
        self.extract_info_ms = max(0, int(elapsed_ms))

    def set_result_processing_ms(self, elapsed_ms: int) -> None:
        self.result_processing_ms = max(0, int(elapsed_ms))

    def unknown_extract_ms(self) -> Optional[int]:
        if self.extract_info_ms is None:
            return None
        known = sum(self.stage_elapsed_ms.values())
        return max(0, self.extract_info_ms - known)

    def iter_stage_timings(self) -> List[Tuple[str, int]]:
        stages: List[Tuple[str, int]] = []
        if self.option_build_ms is not None:
            stages.append(("options", self.option_build_ms))
        if self.cookie_prep_ms is not None:
            stages.append(("cookie_prep", self.cookie_prep_ms))
        for stage in ("webpage", "player_api", "player_js", "challenge", "manifest", "format", "cache"):
            if stage in self.stage_elapsed_ms:
                stages.append((stage, self.stage_elapsed_ms[stage]))
        if self.result_processing_ms is not None:
            stages.append(("result_processing", self.result_processing_ms))
        unknown = self.unknown_extract_ms()
        if unknown is not None:
            stages.append(("unknown_extract", unknown))
        return stages

    def emit(self, guild_id: str, requester_id: str, channel_id: Optional[str] = None) -> None:
        for stage, elapsed_ms in self.iter_stage_timings():
            log_music_timing(
                "youtube_extract_stage",
                guild_id,
                channel_id,
                requester_id,
                stage=stage,
                elapsed_ms=elapsed_ms,
                video_id=self.video_id,
            )


def normalize_music_command(command_text: Optional[str]) -> str:
    return "".join(str(command_text or "").strip().lower().split())


def parse_music_skip_command(raw: str, normalized: str) -> Optional[str]:
    if normalized in MUSIC_SKIP_COMMANDS:
        return ""
    for prefix in ("スキップ", "skip"):
        pattern = r"^{0}[\s\u3000]+(.+)$".format(re.escape(prefix))
        match = re.match(pattern, raw, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    match = re.match(r"^(.+)曲スキップ$", normalized, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def parse_music_loop_queue_command(raw: str, normalized: str) -> Optional[str]:
    if normalized in MUSIC_LOOP_ONE_COMMANDS:
        return None
    if normalized in MUSIC_LOOP_QUEUE_COMMANDS:
        return ""
    match = re.fullmatch(r"キューループ[\s\u3000]+(.+)", str(raw or "").strip(), flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    normalized_raw = unicodedata.normalize("NFKC", str(raw or "").strip())
    match = re.fullmatch(r"([0-9]+)曲ループ", normalized_raw, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def parse_music_command(command_text: Optional[str]) -> Tuple[Optional[str], str]:
    raw = str(command_text or "").strip()
    normalized = normalize_music_command(raw)
    skip_argument = parse_music_skip_command(raw, normalized)
    if skip_argument is not None:
        return "music_skip", skip_argument
    loop_queue_argument = parse_music_loop_queue_command(raw, normalized)
    if loop_queue_argument is not None:
        return "music_loop_queue", loop_queue_argument
    if normalized in MUSIC_PAUSE_COMMANDS:
        return "music_pause", ""
    if normalized in MUSIC_RESUME_COMMANDS:
        return "music_resume", ""
    if normalized in MUSIC_QUEUE_COMMANDS:
        return "music_queue", ""
    if normalized in MUSIC_NOW_COMMANDS:
        return "music_now", ""
    if normalized in MUSIC_LOOP_STATUS_COMMANDS:
        return "music_loop_status", ""
    if normalized in MUSIC_LOOP_ONE_COMMANDS:
        return "music_loop_one", ""
    if normalized in MUSIC_LOOP_OFF_COMMANDS:
        return "music_loop_off", ""
    if normalized in MUSIC_SHUFFLE_COMMANDS:
        return "music_shuffle", ""

    if raw == MUSIC_VOLUME_COMMAND:
        return "music_volume", ""
    if raw.startswith(MUSIC_VOLUME_COMMAND + " "):
        return "music_volume", raw[len(MUSIC_VOLUME_COMMAND) :].strip()
    if normalized == MUSIC_YOUTUBE_STATUS_COMMAND:
        return "youtube_status", ""

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


def is_youtube_music_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = parsed.netloc.lower()
    if parsed.scheme not in ("http", "https"):
        return False
    if host == "youtu.be":
        return bool(parsed.path.strip("/"))
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        path = parsed.path.rstrip("/") or "/"
        return path == "/watch" or path.startswith(("/shorts/", "/live/", "/embed/"))
    return False


def music_link_type(value: str) -> Optional[str]:
    spotify_link = parse_spotify_link(value)
    if spotify_link is not None:
        return "spotify"
    if is_youtube_music_url(value):
        return "youtube"
    return None


def _strip_music_link_candidate(value: str) -> str:
    return str(value or "").strip().rstrip(MUSIC_LINK_TRAILING_CHARS)


def extract_music_links_from_text(text: Optional[str], limit: int = MENTION_MUSIC_LINK_LIMIT) -> List[str]:
    if not text:
        return []
    found: List[str] = []
    seen: Set[str] = set()
    for pattern in (HTTP_URL_PATTERN, SPOTIFY_URI_PATTERN):
        for match in pattern.finditer(str(text)):
            candidate = _strip_music_link_candidate(match.group(0))
            if not candidate or candidate in seen:
                continue
            if music_link_type(candidate) is None:
                continue
            seen.add(candidate)
            found.append(candidate)
    found.sort(key=lambda item: str(text).find(item))
    return found[: max(0, limit)]


def spotify_unsupported_message(link: SpotifyLink) -> str:
    if link.kind == "playlist":
        return "現在のSpotify API仕様では、一般のプレイリストから曲一覧を取得できないため、このリンクにはまだ対応していません。曲またはアルバムのリンクを送ってください。"
    if link.kind == "invalid":
        return "Spotifyリンクの形式が正しくありません。曲またはアルバムのリンクを送ってください。"
    if link.kind in ("episode", "show", "artist"):
        return "このSpotifyリンク種別にはまだ対応していません。曲またはアルバムのリンクを送ってください。"
    return "このSpotifyリンクにはまだ対応していません。曲またはアルバムのリンクを送ってください。"


def _safe_cookie_suffix(guild_id: Optional[str]) -> str:
    raw = str(guild_id or "global")
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in raw)
    return safe or "global"


def get_ytdlp_cookie_tmp_path(guild_id: Optional[str] = None) -> Path:
    return YTDLP_COOKIES_TMP_DIR / "ichiyon-ytdlp-cookies-{0}.txt".format(_safe_cookie_suffix(guild_id))


def prepare_ytdlp_cookie_file(cookies_file: str, guild_id: Optional[str] = None) -> str:
    source_path = Path(cookies_file)
    target_path = get_ytdlp_cookie_tmp_path(guild_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target_path)
    return str(target_path)


def build_ytdl_options(
    guild_id: Optional[str] = None,
    copy_cookies: bool = True,
    use_cookies: bool = True,
    stage_recorder: Optional[YoutubeExtractStageRecorder] = None,
    js_runtime: Optional[str] = None,
) -> Dict[str, object]:
    options: Dict[str, object] = dict(YTDL_OPTIONS)
    runtime = (js_runtime or DEFAULT_YTDLP_JS_RUNTIME).strip().lower()
    if runtime not in SUPPORTED_YTDLP_JS_RUNTIMES:
        raise ValueError("unsupported yt-dlp JS runtime: {0}".format(runtime))
    options["js_runtimes"] = {runtime: {}}
    if stage_recorder is not None:
        options["logger"] = stage_recorder
    cookies_file = str(os.getenv(YTDLP_COOKIES_FILE_ENV) or "").strip()
    if use_cookies and cookies_file:
        cookie_started = time.perf_counter()
        options["cookiefile"] = prepare_ytdlp_cookie_file(cookies_file, guild_id) if copy_cookies else str(get_ytdlp_cookie_tmp_path(guild_id))
        if stage_recorder is not None:
            stage_recorder.add_cookie_prep_ms(perf_ms(cookie_started))
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
    if state.loop_queue:
        lines.append("ループ対象 待機中:")
        for index, track in enumerate(list(state.loop_queue)[:limit], start=1):
            duration = format_duration(track.duration)
            suffix = " ({0})".format(duration) if duration else ""
            lines.append("{0}. {1}{2}".format(index, track.title, suffix))
        remaining = len(state.loop_queue) - limit
        if remaining > 0:
            lines.append("...ほか {0} 件".format(remaining))
    if state.queue:
        heading = "ループ対象外 待機中:" if state.loop_queue else "待機中:"
        lines.append(heading)
        for index, track in enumerate(list(state.queue)[:limit], start=1):
            duration = format_duration(track.duration)
            suffix = " ({0})".format(duration) if duration else ""
            lines.append("{0}. {1}{2}".format(index, track.title, suffix))
        remaining = len(state.queue) - limit
        if remaining > 0:
            lines.append("...ほか {0} 件".format(remaining))
    if state.loop_mode == MUSIC_LOOP_QUEUE and state.loop_range_size is not None:
        lines.append("ループ: 現在曲を含む{0}曲".format(state.loop_range_size))
    if not lines:
        return "キューは空です。"
    return "\n".join(lines)[:1900]


def format_now_playing(state: MusicState) -> str:
    if state.current is None:
        return "現在再生中の曲はありません。"
    return "現在再生中:\n{0}\nリクエスト: <@{1}>".format(format_track(state.current), state.current.requester_id)


def clamp_volume_percent(value: int) -> int:
    return max(0, min(100, int(value)))


def volume_factor(percent: int) -> float:
    return clamp_volume_percent(percent) / 100.0


def parse_volume_percent(argument: str) -> Tuple[Optional[int], str]:
    text = str(argument or "").strip()
    if not text:
        return None, ""
    try:
        value = int(text)
    except ValueError:
        return None, "音量は0〜100の数値で指定してください。"
    if value < 0 or value > 100:
        return None, "音量は0〜100の範囲で指定してください。"
    return value, ""


def parse_skip_count(argument: str) -> Tuple[Optional[int], str]:
    text = unicodedata.normalize("NFKC", str(argument or "")).strip()
    if not text:
        return 1, ""
    if not re.fullmatch(r"[+-]?\d+", text):
        return None, MUSIC_SKIP_INVALID_FORMAT_MESSAGE
    value = int(text)
    if value < MUSIC_SKIP_COUNT_MIN or value > MUSIC_SKIP_COUNT_MAX:
        return None, MUSIC_SKIP_INVALID_COUNT_MESSAGE
    return value, ""


def parse_loop_range_count(argument: str) -> Tuple[Optional[int], str]:
    text = unicodedata.normalize("NFKC", str(argument or "")).strip()
    if not text:
        return None, ""
    if not re.fullmatch(r"[+-]?\d+", text):
        return None, MUSIC_LOOP_INVALID_RANGE_MESSAGE
    value = int(text)
    if value < MUSIC_LOOP_RANGE_MIN or value > MUSIC_LOOP_RANGE_MAX:
        return None, MUSIC_LOOP_INVALID_RANGE_MESSAGE
    return value, ""


def load_music_volume_percent(guild_id: str, state: Optional[MusicState] = None) -> int:
    current_state = state or get_music_state(guild_id)
    if current_state.music_volume_percent is not None:
        return current_state.music_volume_percent
    try:
        with get_connection() as connection:
            settings = MusicSettingsRepository(connection).get(guild_id)
            current_state.music_volume_percent = int(settings.get("music_volume_percent") or DEFAULT_MUSIC_VOLUME_PERCENT)
    except Exception as exc:
        print("[WARN] music volume settings unavailable: guild_id={0} error={1}".format(guild_id, exc))
        current_state.music_volume_percent = DEFAULT_MUSIC_VOLUME_PERCENT
    return current_state.music_volume_percent


def save_music_volume_percent(guild_id: str, percent: int, state: Optional[MusicState] = None) -> Tuple[int, bool]:
    value = clamp_volume_percent(percent)
    current_state = state or get_music_state(guild_id)
    saved = True
    try:
        with get_connection() as connection:
            MusicSettingsRepository(connection).upsert(guild_id, music_volume_percent=value)
            connection.commit()
    except Exception as exc:
        print("[WARN] music volume settings save failed: guild_id={0} error={1}".format(guild_id, exc))
        saved = False
    current_state.music_volume_percent = value
    return value, saved


def apply_music_volume_to_voice_client(voice_client: Optional[discord.VoiceClient], percent: int) -> bool:
    source = getattr(voice_client, "source", None)
    if source is None or not hasattr(source, "volume"):
        return False
    try:
        source.volume = volume_factor(percent)
        return True
    except Exception:
        return False


def loop_status_text(loop_mode: str, state: Optional[MusicState] = None) -> str:
    if loop_mode == MUSIC_LOOP_ONE:
        return "1曲ループ中です。"
    if loop_mode == MUSIC_LOOP_QUEUE:
        if state is not None and state.loop_range_size is not None:
            outside_count = len(state.queue)
            suffix = " ループ対象外の待機曲: {0}曲".format(outside_count) if outside_count else ""
            return "現在曲を含む{0}曲をループ中です。{1}".format(state.loop_range_size, suffix).strip()
        return "キュー全体をループ中です。"
    return "ループは無効です。"


def make_loop_track(track: MusicTrack) -> MusicTrack:
    if track.source_url:
        return replace(track, stream_url="", refresh_required=True, enqueued_at_monotonic=time.perf_counter())
    return replace(track, enqueued_at_monotonic=time.perf_counter())


def _merge_loop_queue_into_queue(state: MusicState) -> None:
    if state.loop_queue:
        state.queue = deque(list(state.loop_queue) + list(state.queue))
        state.loop_queue.clear()
    state.loop_range_size = None


def _has_waiting_tracks(state: MusicState) -> bool:
    return bool(state.loop_queue) or bool(state.queue)


def _has_music_tracks(state: MusicState) -> bool:
    return state.current is not None or _has_waiting_tracks(state)


def _active_loop_waiting_queue(state: MusicState) -> Deque[MusicTrack]:
    if state.loop_mode == MUSIC_LOOP_QUEUE and state.loop_range_size is not None:
        return state.loop_queue
    return state.queue


def _next_playback_queue(state: MusicState) -> Deque[MusicTrack]:
    if state.loop_mode == MUSIC_LOOP_QUEUE and state.loop_range_size is not None:
        return state.loop_queue
    return state.queue


def _total_loop_target_count(state: MusicState) -> int:
    if state.loop_mode != MUSIC_LOOP_QUEUE:
        return 0
    if state.loop_range_size is not None:
        return (1 if state.current is not None else 0) + len(state.loop_queue)
    return (1 if state.current is not None else 0) + len(state.queue)


def _format_loop_skip_result(requested_count: int) -> str:
    return "キューループ内で{0}曲先へ進みました。".format(requested_count)


def _rotate_queue_loop_for_skip(state: MusicState, requested_count: int) -> bool:
    waiting = list(_active_loop_waiting_queue(state))
    loop_items = ([make_loop_track(state.current)] if state.current is not None else []) + waiting
    if not loop_items:
        return False
    advance = requested_count % len(loop_items)
    rotated = loop_items[advance:] + loop_items[:advance]
    if state.loop_range_size is not None:
        state.loop_queue = deque(rotated)
    else:
        state.queue = deque(rotated)
    return True


def extract_track_info(
    url: str,
    requester_id: str,
    guild_id: Optional[str] = None,
    use_cookies: bool = True,
    js_runtime: Optional[str] = None,
) -> MusicTrack:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    safe_guild_id = str(guild_id or "")
    recorder = YoutubeExtractStageRecorder(extract_youtube_video_id(url))
    options_started = time.perf_counter()
    ytdl_options = build_ytdl_options(guild_id, use_cookies=use_cookies, stage_recorder=recorder, js_runtime=js_runtime)
    recorder.set_option_build_ms(perf_ms(options_started))
    extract_started = time.perf_counter()
    try:
        with yt_dlp.YoutubeDL(ytdl_options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        recorder.finish_extract_info(perf_ms(extract_started))
        recorder.emit(safe_guild_id, requester_id)
        raise
    recorder.finish_extract_info(perf_ms(extract_started))
    processing_started = time.perf_counter()
    if info is None:
        recorder.set_result_processing_ms(perf_ms(processing_started))
        recorder.emit(safe_guild_id, requester_id)
        raise RuntimeError("URL情報を取得できませんでした")
    if "entries" in info:
        entries = [entry for entry in info.get("entries") or [] if entry]
        if not entries:
            recorder.set_result_processing_ms(perf_ms(processing_started))
            recorder.emit(safe_guild_id, requester_id)
            raise RuntimeError("再生できる項目がありません")
        info = entries[0]
    stream_url = str(info.get("url") or "").strip()
    if not stream_url:
        recorder.set_result_processing_ms(perf_ms(processing_started))
        recorder.emit(safe_guild_id, requester_id)
        raise RuntimeError("ストリームURLを取得できませんでした")
    title = str(info.get("title") or "無題").strip()
    webpage_url = str(info.get("webpage_url") or url).strip()
    duration = info.get("duration")
    try:
        duration_value = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_value = None
    track = MusicTrack(
        title=title,
        webpage_url=webpage_url,
        stream_url=stream_url,
        requester_id=requester_id,
        duration=duration_value,
        source_url=url,
    )
    recorder.set_result_processing_ms(perf_ms(processing_started))
    recorder.emit(safe_guild_id, requester_id)
    return track


async def extract_track_info_with_cookie_fallback(
    url: str,
    requester_id: str,
    guild_id: str,
    voice_client: Optional[discord.VoiceClient] = None,
) -> MusicTrack:
    started = time.perf_counter()
    attempts = 1
    try:
        track = await asyncio.to_thread(extract_track_info, url, requester_id, guild_id)
        log_music_timing(
            "youtube_extract",
            guild_id,
            voice_channel_id(voice_client),
            requester_id,
            source_type="youtube",
            attempts=attempts,
            total_ms=perf_ms(started),
        )
        return track
    except Exception as exc:
        error_status = classify_ytdlp_error(exc)
        if error_status in AUTH_FAILURE_STATUSES:
            await handle_transient_auth_failure(get_bot(), error_status)
            try:
                attempts += 1
                track = await asyncio.to_thread(extract_track_info, url, requester_id, guild_id, False)
                log_music_action(
                    "extract_cookie_fallback",
                    guild_id,
                    voice_channel_id(voice_client),
                    requester_id,
                    track.title,
                    "cookie_less",
                )
                log_music_timing(
                    "youtube_extract",
                    guild_id,
                    voice_channel_id(voice_client),
                    requester_id,
                    source_type="youtube",
                    attempts=attempts,
                    fallback="no_cookie",
                    total_ms=perf_ms(started),
                )
                return track
            except Exception as fallback_exc:
                print("[WARN] voice music cookie-less fallback failed: guild_id={0} requester_id={1} error={2}".format(guild_id, requester_id, fallback_exc))
                log_music_timing(
                    "youtube_extract_failed",
                    guild_id,
                    voice_channel_id(voice_client),
                    requester_id,
                    source_type="youtube",
                    attempts=attempts,
                    status=classify_ytdlp_error(fallback_exc),
                    total_ms=perf_ms(started),
                )
                raise fallback_exc
        log_music_timing(
            "youtube_extract_failed",
            guild_id,
            voice_channel_id(voice_client),
            requester_id,
            source_type="youtube",
            attempts=attempts,
            status=error_status,
            total_ms=perf_ms(started),
        )
        raise exc


def spotify_error_message(error: Exception) -> str:
    if isinstance(error, SpotifyCredentialsMissing):
        return error.user_message
    if isinstance(error, SpotifyRateLimitedError):
        return error.user_message
    if isinstance(error, SpotifyApiError):
        return error.user_message
    if isinstance(error, SpotifyError):
        return error.user_message
    if isinstance(error, SpotifyResolveError):
        return "Spotify曲に一致するYouTube音源が見つかりませんでした。"
    if is_youtube_cookie_required_error(error):
        return "YouTube側の確認要求により取得できませんでした。Cookie設定が必要な可能性があります。"
    return "Spotifyリンクから再生できる音源を特定できませんでした。"


def should_retry_spotify_resolution(error: Exception) -> bool:
    status = classify_ytdlp_error(error)
    if status == COOKIE_STATUS_VIDEO_UNAVAILABLE:
        return True
    if status in AUTH_FAILURE_STATUSES:
        return False
    message = str(error or "").lower()
    retry_markers = (
        "private video",
        "video unavailable",
        "removed",
        "deleted",
        "region",
        "not available",
        "requested format is not available",
        "only images are available",
    )
    return any(marker in message for marker in retry_markers)


async def resolve_spotify_track_to_music_track(
    spotify_track: SpotifyTrackMetadata,
    requester_id: str,
    guild_id: str,
    voice_client: discord.VoiceClient,
    original_spotify_url: str,
) -> MusicTrack:
    resolved = await resolve_spotify_track_to_youtube(spotify_track, guild_id)
    try:
        track = await extract_track_info_with_cookie_fallback(resolved.youtube_url, requester_id, guild_id, voice_client)
    except Exception as exc:
        if not should_retry_spotify_resolution(exc):
            raise
        invalidate_resolve_cache(spotify_track.track_id)
        retry_resolved = await resolve_spotify_track_to_youtube(spotify_track, guild_id, bypass_cache=True)
        if retry_resolved.youtube_url == resolved.youtube_url:
            raise exc
        try:
            track = await extract_track_info_with_cookie_fallback(retry_resolved.youtube_url, requester_id, guild_id, voice_client)
            resolved = retry_resolved
        except Exception:
            invalidate_resolve_cache(spotify_track.track_id)
            raise
    track.source_type = "spotify"
    track.original_spotify_url = original_spotify_url or spotify_track.spotify_url
    track.spotify_title = spotify_track.name
    track.spotify_artists = spotify_track.display_artist
    track.source_url = resolved.youtube_url
    track.webpage_url = resolved.youtube_url
    return track


async def resolve_spotify_album_tracks(
    album_tracks: List[SpotifyTrackMetadata],
    requester_id: str,
    guild_id: str,
    voice_client: discord.VoiceClient,
    original_spotify_url: str,
) -> Tuple[List[MusicTrack], int]:
    concurrency = min(resolve_concurrency(), max(1, len(album_tracks)))
    results: List[Optional[MusicTrack]] = [None] * len(album_tracks)
    failed_count = 0

    if concurrency <= 1:
        for index, item in enumerate(album_tracks):
            try:
                results[index] = await resolve_spotify_track_to_music_track(item, requester_id, guild_id, voice_client, original_spotify_url)
            except Exception:
                failed_count += 1
        return [track for track in results if track is not None], failed_count

    queue: asyncio.Queue = asyncio.Queue()
    for index, item in enumerate(album_tracks):
        queue.put_nowait((index, item))

    async def _worker() -> None:
        nonlocal failed_count
        while True:
            try:
                index, item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                results[index] = await resolve_spotify_track_to_music_track(item, requester_id, guild_id, voice_client, original_spotify_url)
            except Exception:
                failed_count += 1
            finally:
                queue.task_done()

    workers = [asyncio.create_task(_worker()) for _ in range(concurrency)]
    try:
        await queue.join()
    finally:
        for worker in workers:
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    return [track for track in results if track is not None], failed_count


async def enqueue_spotify_link(
    message: discord.Message,
    link: SpotifyLink,
    voice_client: discord.VoiceClient,
) -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True

    guild_id = str(guild.id)
    requester_id = str(getattr(message.author, "id", "") or "")
    state = get_music_state(guild_id)
    client = get_spotify_client()

    if link.kind == "track":
        started = time.perf_counter()
        try:
            spotify_track = await client.get_track(link.spotify_id)
            metadata_ms = perf_ms(started)
            resolve_started = time.perf_counter()
            track = await resolve_spotify_track_to_music_track(spotify_track, requester_id, guild_id, voice_client, link.original_url)
            resolve_ms = perf_ms(resolve_started)
        except Exception as exc:
            print("[WARN] spotify track enqueue failed: bot_instance_id={0} guild_id={1} requester_id={2} kind=track error={3}".format(config.BOT_INSTANCE_ID, guild_id, requester_id, type(exc).__name__))
            log_music_timing("spotify_track_failed", guild_id, voice_channel_id(voice_client), requester_id, total_ms=perf_ms(started), error=type(exc).__name__)
            await message.channel.send(spotify_error_message(exc))
            return True

        should_start = state.current is None and not (voice_client.is_playing() or voice_client.is_paused())
        state.queue.append(mark_track_enqueued(track))
        log_music_action("enqueue_spotify", guild_id, voice_channel_id(voice_client), requester_id, track.title)
        log_music_timing("spotify_track", guild_id, voice_channel_id(voice_client), requester_id, metadata_ms=metadata_ms, resolve_ms=resolve_ms, total_ms=perf_ms(started))
        await message.channel.send("Spotifyから『{0} / {1}』を検索し、キューへ追加しました。".format(spotify_track.name, spotify_track.display_artist))
        if should_start:
            await play_next_track(voice_client, guild_id)
        return True

    lock_key = "{0}:{1}".format(config.BOT_INSTANCE_ID, guild_id)
    lock = get_album_lock(lock_key)
    if lock.locked():
        await message.channel.send("このサーバーでは別のSpotifyアルバムを処理中です。完了後にもう一度試してください。")
        return True

    try:
        async with lock:
            started = time.perf_counter()
            try:
                album = await client.get_album(link.spotify_id)
                metadata_ms = perf_ms(started)
            except Exception as exc:
                print("[WARN] spotify album fetch failed: bot_instance_id={0} guild_id={1} requester_id={2} error={3}".format(config.BOT_INSTANCE_ID, guild_id, requester_id, type(exc).__name__))
                log_music_timing("spotify_album_failed", guild_id, voice_channel_id(voice_client), requester_id, total_ms=perf_ms(started), error=type(exc).__name__)
                await message.channel.send(spotify_error_message(exc))
                return True

            if not album.tracks:
                await message.channel.send("Spotifyアルバムから追加できる曲が見つかりませんでした。")
                return True

            await message.channel.send("Spotifyアルバム『{0}』を処理中です。曲数によって少し時間がかかります。".format(album.name))
            resolve_started = time.perf_counter()
            tracks, failed_count = await resolve_spotify_album_tracks(album.tracks, requester_id, guild_id, voice_client, link.original_url)
            resolve_ms = perf_ms(resolve_started)
            skipped_count = failed_count + album.skipped_tracks
            if not tracks:
                await message.channel.send("Spotifyアルバム『{0}』から一致するYouTube音源を見つけられませんでした。".format(album.name))
                return True

            should_start = state.current is None and not (voice_client.is_playing() or voice_client.is_paused())
            for track in tracks:
                state.queue.append(mark_track_enqueued(track))
            log_music_action("enqueue_spotify_album", guild_id, voice_channel_id(voice_client), requester_id, album.name, "tracks={0} skipped={1}".format(len(tracks), skipped_count))
            log_music_timing(
                "spotify_album",
                guild_id,
                voice_channel_id(voice_client),
                requester_id,
                metadata_ms=metadata_ms,
                resolve_ms=resolve_ms,
                total_ms=perf_ms(started),
                tracks=len(tracks),
                skipped=skipped_count,
            )
            suffix = " 上限により一部の曲は処理していません。" if album.truncated else ""
            await message.channel.send(
                "Spotifyアルバム『{0}』から{1}曲をキューへ追加しました。{2}曲は音源を特定できなかったためスキップしました。{3}".format(
                    album.name,
                    len(tracks),
                    skipped_count,
                    suffix,
                ).strip()
            )
            if should_start:
                await play_next_track(voice_client, guild_id)
            return True
    finally:
        remove_album_lock(lock_key, lock)


async def refresh_track_for_playback(track: MusicTrack, guild_id: str) -> Optional[MusicTrack]:
    if not track.refresh_required:
        return track
    if not track.source_url:
        return replace(track, refresh_required=False)

    try:
        refreshed = await asyncio.to_thread(extract_track_info, track.source_url, track.requester_id, guild_id)
    except Exception as exc:
        error_status = classify_ytdlp_error(exc)
        if error_status in AUTH_FAILURE_STATUSES:
            await handle_transient_auth_failure(get_bot(), error_status)
            try:
                refreshed = await asyncio.to_thread(extract_track_info, track.source_url, track.requester_id, guild_id, False)
            except Exception as fallback_exc:
                print(
                    "[WARN] voice music loop refresh cookie-less fallback failed: guild_id={0} title={1} error={2}".format(
                        guild_id,
                        track.title,
                        fallback_exc,
                    )
                )
                log_music_action("loop_refresh_failed", guild_id, requester_id=track.requester_id, title=track.title, reason=classify_ytdlp_error(fallback_exc))
                return None
        else:
            print("[WARN] voice music loop refresh failed: guild_id={0} title={1} error={2}".format(guild_id, track.title, exc))
            log_music_action("loop_refresh_failed", guild_id, requester_id=track.requester_id, title=track.title, reason=error_status)
            return None

    refreshed.title = track.title or refreshed.title
    refreshed.webpage_url = track.webpage_url or refreshed.webpage_url
    refreshed.requester_id = track.requester_id
    refreshed.duration = track.duration if track.duration is not None else refreshed.duration
    refreshed.source_url = track.source_url
    refreshed.refresh_required = False
    log_music_action("loop_refresh", guild_id, requester_id=track.requester_id, title=refreshed.title)
    return refreshed


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


async def ensure_mention_music_voice_client(message: discord.Message) -> Optional[discord.VoiceClient]:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return None

    guild_id = str(guild.id)
    requester_id = str(getattr(message.author, "id", "") or "")
    voice_client = get_raw_guild_voice_client(guild)
    if voice_client is not None and is_voice_client_connected(voice_client):
        return voice_client

    target_channel = get_author_voice_channel(message)
    if target_channel is None:
        log_music_action("mention_link_autojoin_skipped", guild_id, requester_id=requester_id, reason="author_not_in_vc")
        await message.channel.send("VCに参加してからURLを送ってください。")
        return None

    target_channel_id = str(getattr(target_channel, "id", "") or "")
    async with get_voice_connect_lock(guild_id):
        voice_client = get_raw_guild_voice_client(guild)
        try:
            if voice_client is not None and not is_voice_client_connected(voice_client):
                log_music_action("mention_link_stale_cleanup", guild_id, target_channel_id, requester_id)
                await cleanup_stale_voice_client(voice_client)
                voice_client = None

            if voice_client is not None and is_voice_client_connected(voice_client):
                return voice_client

            voice_client = await target_channel.connect()
            log_music_action("mention_link_autojoin", guild_id, target_channel_id, requester_id)
            return voice_client
        except (
            RuntimeError,
            asyncio.TimeoutError,
            discord.ClientException,
            discord.Forbidden,
            discord.HTTPException,
            DISCORD_CONNECTION_CLOSED,
        ) as exc:
            print("[WARN] mention music voice connect failed: guild_id={0} channel_id={1} error={2}".format(guild_id, target_channel_id, exc))
            await cleanup_stale_voice_client(get_raw_guild_voice_client(guild))
            await message.channel.send("VCへの接続に失敗しました。権限や接続状態を確認してください。")
            return None
        except Exception as exc:
            print("[WARN] unexpected mention music voice connect failed: guild_id={0} channel_id={1} error={2}".format(guild_id, target_channel_id, exc))
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
    finished_track = state.current
    skip_requested = state.skip_requested
    state.skip_requested = False
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
    if finished_track is not None and state.loop_mode == MUSIC_LOOP_ONE and not skip_requested:
        state.queue.appendleft(make_loop_track(finished_track))
    elif finished_track is not None and state.loop_mode == MUSIC_LOOP_QUEUE and not skip_requested:
        if state.loop_range_size is not None:
            state.loop_queue.append(make_loop_track(finished_track))
        else:
            state.queue.append(make_loop_track(finished_track))
    await play_next_track(voice_client, guild_id)


async def play_next_track(voice_client: discord.VoiceClient, guild_id: str) -> bool:
    setup_started = time.perf_counter()
    state = get_music_state(guild_id)
    if not is_voice_client_connected(voice_client):
        state.current = None
        log_music_action("queue_empty", guild_id, voice_channel_id(voice_client), reason="not_connected")
        return False
    channel_id = voice_channel_id(voice_client)
    while _has_waiting_tracks(state):
        playback_queue = _next_playback_queue(state)
        if not playback_queue:
            break
        track = playback_queue.popleft()
        wait_ms = queue_wait_ms(track)
        refresh_started = time.perf_counter()
        refreshed_track = await refresh_track_for_playback(track, guild_id)
        refresh_ms = perf_ms(refresh_started)
        if refreshed_track is None:
            log_music_timing(
                "play_setup_skipped",
                guild_id,
                channel_id,
                track.requester_id,
                queue_wait_ms=wait_ms,
                refresh_ms=refresh_ms,
                total_ms=perf_ms(setup_started),
                reason="refresh_failed",
            )
            continue

        state.current = refreshed_track
        try:
            ffmpeg_started = time.perf_counter()
            raw_source = discord.FFmpegPCMAudio(
                refreshed_track.stream_url,
                before_options=STREAM_BEFORE_OPTIONS,
                options=STREAM_OPTIONS,
            )
            source = discord.PCMVolumeTransformer(raw_source, volume=volume_factor(load_music_volume_percent(guild_id, state)))
            ffmpeg_ms = perf_ms(ffmpeg_started)
            play_call_started = time.perf_counter()
            voice_client.play(source, after=lambda error: _schedule_after_callback(voice_client, guild_id, error))
            play_call_ms = perf_ms(play_call_started)
            log_music_action("play_start", guild_id, channel_id, refreshed_track.requester_id, refreshed_track.title)
            log_music_timing(
                "play_setup",
                guild_id,
                channel_id,
                refreshed_track.requester_id,
                source_type=refreshed_track.source_type,
                queue_wait_ms=wait_ms,
                refresh_ms=refresh_ms,
                ffmpeg_ms=ffmpeg_ms,
                play_call_ms=play_call_ms,
                total_ms=perf_ms(setup_started),
            )
            return True
        except (discord.ClientException, discord.OpusNotLoaded, OSError) as exc:
            print("[WARN] voice music play start failed: guild_id={0} title={1} error={2}".format(guild_id, refreshed_track.title, exc))
            log_music_action("playback_error", guild_id, channel_id, refreshed_track.requester_id, refreshed_track.title, str(exc))
            state.current = None

    state.current = None
    log_music_action("queue_empty", guild_id, channel_id)
    return False


async def enqueue_music_url(message: discord.Message, url: str) -> bool:
    received_started = time.perf_counter()
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    spotify_link = parse_spotify_link(url)
    if spotify_link is not None and not spotify_link.is_supported:
        await message.channel.send(spotify_unsupported_message(spotify_link))
        return True
    if spotify_link is None and not is_http_url(url):
        await message.channel.send("再生するURLを指定してください。")
        return True

    voice_client = await ensure_music_voice_client(message)
    if voice_client is None:
        return True

    guild_id = str(guild.id)
    requester_id = str(getattr(message.author, "id", "") or "")
    voice_ready_ms = perf_ms(received_started)
    state = get_music_state(guild_id)
    state.text_channel = message.channel
    if state.current is None and (voice_client.is_playing() or voice_client.is_paused()):
        log_music_action("enqueue_rejected", guild_id, voice_channel_id(voice_client), requester_id, reason="already_playing")
        await message.channel.send("現在再生中です。")
        return True

    if spotify_link is not None:
        return await enqueue_spotify_link(message, spotify_link, voice_client)

    try:
        extract_started = time.perf_counter()
        track = await extract_track_info_with_cookie_fallback(url, requester_id, guild_id, voice_client)
        extract_ms = perf_ms(extract_started)
    except Exception as exc:
        print("[WARN] voice music extract failed: guild_id={0} requester_id={1} error={2}".format(guild_id, requester_id, exc))
        if classify_ytdlp_error(exc) in AUTH_FAILURE_STATUSES or is_youtube_cookie_required_error(exc):
            await message.channel.send("YouTube側の確認要求により取得できませんでした。Cookie設定が必要な可能性があります。")
            return True
        await message.channel.send("URL情報を取得できませんでした。URLや対応サイトを確認してください。")
        return True

    should_start = state.current is None and not (voice_client.is_playing() or voice_client.is_paused())
    state.queue.append(mark_track_enqueued(track))
    log_music_action("enqueue", guild_id, voice_channel_id(voice_client), requester_id, track.title)
    log_music_timing(
        "enqueue_command",
        guild_id,
        voice_channel_id(voice_client),
        requester_id,
        source_type="youtube",
        voice_ready_ms=voice_ready_ms,
        extract_ms=extract_ms,
        total_ms=perf_ms(received_started),
        starts_playback=should_start,
    )
    if should_start:
        await message.channel.send("再生します: {0}".format(track.title))
        await play_next_track(voice_client, guild_id)
    else:
        await message.channel.send("キューに追加しました: {0}".format(track.title))
    return True


async def enqueue_music_url_if_voice_connected(message: discord.Message, url: str) -> bool:
    received_started = time.perf_counter()
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True

    spotify_link = parse_spotify_link(url)
    if spotify_link is not None and not spotify_link.is_supported:
        await message.channel.send(spotify_unsupported_message(spotify_link))
        return True
    if spotify_link is None and not is_http_url(url):
        await message.channel.send("再生するURLを指定してください。")
        return True

    guild_id = str(guild.id)
    requester_id = str(getattr(message.author, "id", "") or "")
    link_type = music_link_type(url) or "unknown"
    voice_client = await ensure_mention_music_voice_client(message)
    if voice_client is None:
        log_music_action("mention_link_skipped", guild_id, requester_id=requester_id, reason="type={0} not_connected".format(link_type))
        return True
    voice_ready_ms = perf_ms(received_started)

    state = get_music_state(guild_id)
    state.text_channel = message.channel
    if state.current is None and (voice_client.is_playing() or voice_client.is_paused()):
        log_music_action("mention_link_rejected", guild_id, voice_channel_id(voice_client), requester_id, reason="already_playing")
        await message.channel.send("現在再生中です。")
        return True

    if spotify_link is not None:
        log_music_action("mention_link_enqueue", guild_id, voice_channel_id(voice_client), requester_id, reason="type=spotify")
        return await enqueue_spotify_link(message, spotify_link, voice_client)

    try:
        extract_started = time.perf_counter()
        track = await extract_track_info_with_cookie_fallback(url, requester_id, guild_id, voice_client)
        extract_ms = perf_ms(extract_started)
    except Exception as exc:
        status = classify_ytdlp_error(exc)
        print("[WARN] mention music link extract failed: bot_instance_id={0} guild_id={1} requester_id={2} type=youtube status={3} error={4}".format(config.BOT_INSTANCE_ID, guild_id, requester_id, status, type(exc).__name__))
        log_music_action("mention_link_failed", guild_id, voice_channel_id(voice_client), requester_id, reason="type=youtube status={0}".format(status))
        if status in AUTH_FAILURE_STATUSES or is_youtube_cookie_required_error(exc):
            await message.channel.send("YouTube側の確認要求により取得できませんでした。Cookie設定が必要な可能性があります。")
            return True
        await message.channel.send("URL情報を取得できませんでした。URLや対応サイトを確認してください。")
        return True

    should_start = state.current is None and not (voice_client.is_playing() or voice_client.is_paused())
    state.queue.append(mark_track_enqueued(track))
    log_music_action("mention_link_enqueue", guild_id, voice_channel_id(voice_client), requester_id, track.title, "type=youtube")
    log_music_timing(
        "mention_link_enqueue",
        guild_id,
        voice_channel_id(voice_client),
        requester_id,
        source_type="youtube",
        voice_ready_ms=voice_ready_ms,
        extract_ms=extract_ms,
        total_ms=perf_ms(received_started),
        starts_playback=should_start,
    )
    if should_start:
        await message.channel.send("再生します: {0}".format(track.title))
        await play_next_track(voice_client, guild_id)
    else:
        await message.channel.send("キューに追加しました: {0}".format(track.title))
    return True


async def handle_mention_music_links(message: discord.Message, command_text: Optional[str]) -> bool:
    if command_text is None:
        return False
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    if getattr(message, "guild", None) is None:
        return False

    links = extract_music_links_from_text(command_text)
    if not links:
        return False

    handled = False
    for url in links:
        handled = await enqueue_music_url_if_voice_connected(message, url) or handled
        if get_guild_voice_client(message.guild) is None:
            break
    return handled


def _pop_skipped_waiting_tracks(state: MusicState, count: int) -> int:
    removed = 0
    while removed < count and state.queue:
        state.queue.popleft()
        removed += 1
    return removed


def format_skip_result(skipped_count: int, has_next: bool) -> str:
    if skipped_count <= 1:
        return "スキップしました。"
    if has_next:
        return "{0}曲をスキップしました。次の曲を再生します。".format(skipped_count)
    return "{0}曲をスキップしました。音楽キューは空です。".format(skipped_count)


async def skip_music(message: discord.Message, argument: str = "") -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    skip_count, error = parse_skip_count(argument)
    if error:
        await message.channel.send(error)
        return True
    voice_client = get_guild_voice_client(guild)
    guild_id = str(guild.id)
    state = get_music_state(guild_id)
    if voice_client is None or not _has_music_tracks(state):
        await message.channel.send("現在再生中の曲はありません。")
        return True
    requested_count = int(skip_count or 1)
    requester_id = str(getattr(message.author, "id", "") or "")
    channel_id = voice_channel_id(voice_client)
    if state.loop_mode == MUSIC_LOOP_QUEUE:
        loop_target_count = _total_loop_target_count(state)
        if not _rotate_queue_loop_for_skip(state, requested_count):
            await message.channel.send("現在再生中の曲はありません。")
            return True
        log_music_action(
            "skip",
            guild_id,
            channel_id,
            requester_id,
            state.current.title if state.current else "",
            "requested={0} loop_target={1} queue_loop=true".format(requested_count, loop_target_count),
        )
        if state.current is not None and (voice_client.is_playing() or voice_client.is_paused()):
            state.skip_requested = True
            voice_client.stop()
        else:
            state.current = None
            state.skip_requested = False
            if _has_waiting_tracks(state) and is_voice_client_connected(voice_client):
                await play_next_track(voice_client, guild_id)
        await message.channel.send(_format_loop_skip_result(requested_count))
        return True

    skipped_count = 0
    current_title = state.current.title if state.current else ""
    if state.current is not None:
        skipped_count = 1
        skipped_count += _pop_skipped_waiting_tracks(state, requested_count - 1)
        log_music_action(
            "skip",
            guild_id,
            channel_id,
            requester_id,
            current_title,
            "requested={0} skipped={1}".format(requested_count, skipped_count),
        )
        if voice_client.is_playing() or voice_client.is_paused():
            state.skip_requested = True
            voice_client.stop()
        else:
            state.current = None
            state.skip_requested = False
            if _has_waiting_tracks(state) and is_voice_client_connected(voice_client):
                await play_next_track(voice_client, guild_id)
        await message.channel.send(format_skip_result(skipped_count, _has_waiting_tracks(state)))
        return True

    skipped_count = _pop_skipped_waiting_tracks(state, requested_count)
    log_music_action(
        "skip",
        guild_id,
        channel_id,
        requester_id,
        reason="requested={0} skipped={1} current=none".format(requested_count, skipped_count),
    )
    if _has_waiting_tracks(state) and is_voice_client_connected(voice_client):
        await play_next_track(voice_client, guild_id)
    await message.channel.send(format_skip_result(skipped_count, _has_waiting_tracks(state)))
    return True


async def stop_music(message: discord.Message) -> bool:
    guild = message.guild
    if guild is None:
        return False
    guild_id = str(guild.id)
    state = get_music_state(guild_id)
    voice_client = get_guild_voice_client(guild)
    has_music = _has_music_tracks(state) or state.loop_mode != MUSIC_LOOP_OFF
    if not has_music:
        return False
    state.queue.clear()
    state.loop_queue.clear()
    state.stopping = True
    state.skip_requested = False
    state.loop_mode = MUSIC_LOOP_OFF
    state.loop_range_size = None
    current_title = state.current.title if state.current else ""
    state.current = None
    if voice_client is not None and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
    log_music_action("stop", guild_id, voice_channel_id(voice_client), str(getattr(message.author, "id", "") or ""), current_title)
    await message.channel.send("再生を停止し、キューをクリアしました。")
    return True


async def send_or_update_music_volume(message: discord.Message, argument: str) -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音量コマンドはサーバー内で使ってください。")
        return True
    guild_id = str(guild.id)
    state = get_music_state(guild_id)
    if not str(argument or "").strip():
        await message.channel.send("現在の音楽音量は {0}% です。".format(load_music_volume_percent(guild_id, state)))
        return True
    value, error = parse_volume_percent(argument)
    if error:
        await message.channel.send(error)
        return True
    saved, persisted = save_music_volume_percent(guild_id, int(value), state)
    voice_client = get_guild_voice_client(guild)
    applied = apply_music_volume_to_voice_client(voice_client, saved)
    suffix = " 現在再生中の音量にも反映しました。" if applied else ""
    if persisted:
        await message.channel.send("音楽音量を {0}% に変更しました。{1}".format(saved, suffix).strip())
    else:
        await message.channel.send(
            "音楽音量を {0}% に一時的に変更しましたが、設定を保存できませんでした。Botを再起動すると元に戻る可能性があります。{1}".format(
                saved,
                suffix,
            ).strip()
        )
    return True


async def send_music_loop_status(message: discord.Message) -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    state = get_music_state(str(guild.id))
    await message.channel.send(loop_status_text(state.loop_mode, state))
    return True


async def set_music_loop(message: discord.Message, loop_mode: str, argument: str = "") -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    state = get_music_state(str(guild.id))
    range_count, error = parse_loop_range_count(argument) if loop_mode == MUSIC_LOOP_QUEUE else (None, "")
    if error:
        await message.channel.send(error)
        return True

    if loop_mode == MUSIC_LOOP_OFF:
        _merge_loop_queue_into_queue(state)
        state.loop_mode = MUSIC_LOOP_OFF
        await message.channel.send(loop_status_text(loop_mode, state))
        return True

    if loop_mode == MUSIC_LOOP_ONE:
        _merge_loop_queue_into_queue(state)
        state.loop_mode = MUSIC_LOOP_ONE
        await message.channel.send(loop_status_text(loop_mode, state))
        return True

    if loop_mode == MUSIC_LOOP_QUEUE and range_count is None:
        _merge_loop_queue_into_queue(state)
        state.loop_mode = MUSIC_LOOP_QUEUE
        await message.channel.send(loop_status_text(loop_mode, state))
        return True

    if loop_mode == MUSIC_LOOP_QUEUE and range_count is not None:
        ordered_waiting = list(state.loop_queue) + list(state.queue)
        total_available = (1 if state.current is not None else 0) + len(ordered_waiting)
        if total_available <= 0:
            await message.channel.send("現在再生中の曲はありません。")
            return True
        actual_count = min(int(range_count), total_available)
        if actual_count == 1 and state.current is not None:
            state.loop_queue.clear()
            state.queue = deque(ordered_waiting)
            state.loop_range_size = None
            state.loop_mode = MUSIC_LOOP_ONE
            await message.channel.send("現在曲1曲をループします。")
            return True

        if state.current is not None:
            loop_waiting_count = max(0, actual_count - 1)
            state.loop_queue = deque(ordered_waiting[:loop_waiting_count])
            state.queue = deque(ordered_waiting[loop_waiting_count:])
        else:
            state.loop_queue = deque(ordered_waiting[:actual_count])
            state.queue = deque(ordered_waiting[actual_count:])
        state.loop_mode = MUSIC_LOOP_QUEUE
        state.loop_range_size = actual_count
        if actual_count < int(range_count):
            await message.channel.send("現在のキューは{0}曲のため、{0}曲をループします。".format(actual_count))
        else:
            await message.channel.send("現在曲を含む{0}曲をループします。".format(actual_count))
        return True

    state.loop_mode = loop_mode
    await message.channel.send(loop_status_text(loop_mode, state))
    return True


async def shuffle_music_queue(message: discord.Message) -> bool:
    guild = message.guild
    if guild is None:
        await message.channel.send("音楽コマンドはサーバー内で使ってください。")
        return True
    state = get_music_state(str(guild.id))
    target_queue = state.loop_queue if state.loop_mode == MUSIC_LOOP_QUEUE and state.loop_range_size is not None else state.queue
    waiting = list(target_queue)
    random.shuffle(waiting)
    if state.loop_mode == MUSIC_LOOP_QUEUE and state.loop_range_size is not None:
        state.loop_queue = deque(waiting)
    else:
        state.queue = deque(waiting)
    await message.channel.send("待機中のキューをシャッフルしました。対象: {0}件".format(len(waiting)))
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


async def send_youtube_status(message: discord.Message) -> bool:
    await message.channel.send(format_cookie_monitor_status())
    return True
