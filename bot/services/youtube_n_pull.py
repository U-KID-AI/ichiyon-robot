import asyncio
import random
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import discord

from bot import config
from bot.db import get_connection
from bot.repositories.youtube_n_pull import YouTubeNPullRepository, cache_is_fresh, normalize_command_name
from bot.services.voice_audio import get_guild_voice_client
from bot.services.voice_music import MusicTrack, get_music_state, play_next_track, voice_channel_id

try:
    import yt_dlp
except ImportError:  # pragma: no cover - dependency availability is checked separately.
    yt_dlp = None


MAX_N_PULL_COUNT = 100
N_PULL_PATTERN = re.compile(r"^(?P<name>.+?)\s*(?P<count>[0-9０-９]+)?\s*連\s*$")
_CACHE_REFRESH_LOCKS: Dict[str, asyncio.Lock] = {}


def parse_n_pull_command(command_text: Optional[str]) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    raw = str(command_text or "").strip()
    if not raw:
        return None, None, None
    normalized = unicodedata.normalize("NFKC", raw)
    if not normalized.endswith("連"):
        return None, None, None
    match = N_PULL_PATTERN.match(normalized)
    if not match:
        return None, None, "N連は `プリセット名 10連` の形で指定してください。"
    name = " ".join(str(match.group("name") or "").strip().split())
    count_text = match.group("count")
    if not name or not count_text:
        return None, None, "N連は `プリセット名 10連` の形で指定してください。"
    count = int(count_text)
    if count < 1 or count > MAX_N_PULL_COUNT:
        return name, None, "N連の件数は1〜100で指定してください。"
    return name, count, None


def is_youtube_source_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.lower()
    if parsed.scheme not in ("http", "https"):
        return False
    return host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be")


def video_id_from_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.lower()
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0]
    if parsed.path == "/watch":
        return (parse_qs(parsed.query).get("v") or [""])[0]
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0] in ("shorts", "embed", "live") and len(parts) >= 2:
        return parts[1]
    return ""


def canonical_video_url(video_id: str) -> str:
    return "https://www.youtube.com/watch?v={0}".format(video_id)


def split_terms(value: str) -> List[str]:
    return [unicodedata.normalize("NFKC", line).casefold() for line in str(value or "").splitlines() if line.strip()]


def video_passes_filters(video: Dict[str, Any], preset: Dict[str, Any]) -> bool:
    title = unicodedata.normalize("NFKC", str(video.get("title") or "")).casefold()
    if not title:
        return False
    url = str(video.get("canonical_url") or "")
    entry_url = str(video.get("entry_url") or "")
    if not preset.get("include_shorts") and "/shorts/" in (url + " " + entry_url).lower():
        return False
    live_status = str(video.get("live_status") or "").lower()
    if live_status in ("is_live", "is_upcoming") and not preset.get("include_live"):
        return False
    if live_status == "was_live" and not preset.get("include_archived_live"):
        return False
    duration = video.get("duration_seconds")
    min_duration = preset.get("min_duration_seconds")
    max_duration = preset.get("max_duration_seconds")
    if duration is not None and min_duration is not None and int(duration) < int(min_duration):
        return False
    if duration is not None and max_duration is not None and int(duration) > int(max_duration):
        return False
    include_terms = split_terms(preset.get("include_title_terms") or "")
    if include_terms and not any(term in title for term in include_terms):
        return False
    exclude_terms = split_terms(preset.get("exclude_title_terms") or "")
    if exclude_terms and any(term in title for term in exclude_terms):
        return False
    return True


def extract_video_from_entry(entry: Dict[str, Any], source_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    if not entry:
        return None
    entry_url = str(entry.get("url") or entry.get("webpage_url") or "")
    video_id = str(entry.get("id") or "").strip()
    if not video_id and entry_url:
        video_id = video_id_from_url(entry_url)
    if not video_id:
        return None
    title = str(entry.get("title") or "").strip()
    if not title:
        return None
    canonical_url = canonical_video_url(video_id)
    duration = entry.get("duration")
    try:
        duration_value = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_value = None
    return {
        "source_id": source_id,
        "video_id": video_id,
        "canonical_url": canonical_url,
        "entry_url": entry_url,
        "title": title,
        "duration_seconds": duration_value,
        "live_status": str(entry.get("live_status") or ""),
        "published_at": None,
    }


def build_flat_ytdl_options(guild_id: str) -> Dict[str, Any]:
    from bot.services.voice_music import build_ytdl_options

    options = build_ytdl_options(guild_id)
    options.update(
        {
            "extract_flat": True,
            "skip_download": True,
            "ignoreerrors": True,
            "playlistend": 500,
        }
    )
    return options


def fetch_source_videos(source: Dict[str, Any], guild_id: str, preset: Dict[str, Any]) -> List[Dict[str, Any]]:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    url = str(source.get("source_url") or "").strip()
    if not is_youtube_source_url(url):
        raise ValueError("unsupported youtube source url")
    with yt_dlp.YoutubeDL(build_flat_ytdl_options(guild_id)) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = (info or {}).get("entries") or []
    videos: List[Dict[str, Any]] = []
    seen = set()
    for entry in entries:
        video = extract_video_from_entry(entry or {}, int(source.get("id")) if source.get("id") is not None else None)
        if video is None or video["video_id"] in seen:
            continue
        if not video_passes_filters(video, preset):
            continue
        seen.add(video["video_id"])
        videos.append(video)
    return videos


async def refresh_cache_if_needed(repository: YouTubeNPullRepository, guild_id: str, preset: Dict[str, Any]) -> Tuple[bool, str]:
    preset_id = int(preset["id"])
    cached = repository.list_cached_videos(preset_id)
    if cached and cache_is_fresh(preset):
        return False, "hit"
    lock_key = "{0}:{1}:{2}".format(config.BOT_INSTANCE_ID, guild_id, preset_id)
    lock = _CACHE_REFRESH_LOCKS.setdefault(lock_key, asyncio.Lock())
    async with lock:
        latest_preset = repository.get_preset(guild_id, preset_id) or preset
        cached = repository.list_cached_videos(preset_id)
        if cached and cache_is_fresh(latest_preset):
            return False, "hit"
        sources = repository.list_sources(preset_id, enabled=True)
        if not sources:
            return False, "no_sources"
        refreshed: List[Dict[str, Any]] = []
        try:
            for source in sources:
                source_videos = await asyncio.to_thread(fetch_source_videos, source, guild_id, latest_preset)
                refreshed.extend(source_videos)
            unique: Dict[str, Dict[str, Any]] = {}
            for video in refreshed:
                unique.setdefault(video["video_id"], video)
            repository.replace_cache_videos(preset_id, list(unique.values()))
            repository.mark_cache_refresh(preset_id, "")
            return True, "refresh"
        except Exception as exc:
            repository.mark_cache_refresh(preset_id, type(exc).__name__)
            if cached:
                return False, "stale"
            raise


def pick_videos(videos: List[Dict[str, Any]], count: int) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for video in videos:
        unique.setdefault(str(video.get("video_id")), video)
    values = list(unique.values())
    if count >= len(values):
        return values
    return random.sample(values, count)


def make_track_from_cached_video(video: Dict[str, Any], requester_id: str) -> MusicTrack:
    url = str(video.get("canonical_url") or canonical_video_url(str(video.get("video_id") or "")))
    return MusicTrack(
        title=str(video.get("title") or url),
        webpage_url=url,
        stream_url="",
        requester_id=requester_id,
        duration=video.get("duration_seconds"),
        source_url=url,
        refresh_required=True,
        source_type="youtube_n_pull",
    )


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "?:??"
    seconds = max(0, int(seconds))
    minutes, rest = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "{0}:{1:02d}:{2:02d}".format(hours, minutes, rest)
    return "{0}:{1:02d}".format(minutes, rest)


def build_result_messages(preset: Dict[str, Any], requested: int, selected: List[Dict[str, Any]], cache_status: str) -> List[str]:
    display_name = preset.get("display_name") or preset.get("command_name") or "YouTube N連"
    total_duration = 0
    duration_complete = True
    lines = ["🎲 {0} {1}連".format(display_name, requested)]
    if len(selected) < requested:
        lines.append("{0}件中、利用可能な{1}件を音楽キューへ追加しました。".format(requested, len(selected)))
    else:
        lines.append("{0}件を音楽キューへ追加しました。".format(len(selected)))
    for video in selected:
        duration = video.get("duration_seconds")
        if duration is None:
            duration_complete = False
        else:
            total_duration += int(duration)
    if selected:
        if duration_complete:
            lines.append("推定合計時間: {0}".format(format_duration(total_duration)))
        else:
            lines.append("推定合計時間: 一部不明")
    lines.append("キャッシュ: {0}".format(cache_status))
    messages: List[str] = []
    current = "\n".join(lines)
    for index, video in enumerate(selected, start=1):
        item = "{0}. {1}".format(index, truncate_text(str(video.get("title") or ""), 80))
        if len(current) + len(item) + 1 > 1800:
            messages.append(current)
            current = item
        else:
            current += "\n" + item
    messages.append(current)
    return messages


def truncate_text(value: str, limit: int) -> str:
    value = str(value or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def log_n_pull(action: str, guild_id: str, requester_id: str = "", preset: Optional[Dict[str, Any]] = None, **extra) -> None:
    preset_id = str((preset or {}).get("id") or "")
    preset_name = str((preset or {}).get("display_name") or "")
    detail = " ".join("{0}={1}".format(key, value) for key, value in sorted(extra.items()))
    print(
        "[INFO] youtube_n_pull {0}: bot_instance_id={1} guild_id={2} requester_id={3} preset_id={4} preset_name={5} {6}".format(
            action,
            config.BOT_INSTANCE_ID,
            guild_id,
            requester_id,
            preset_id,
            preset_name,
            detail,
        ).strip()
    )


async def handle_youtube_n_pull_command(message: discord.Message, command_text: Optional[str]) -> bool:
    if command_text is None:
        return False
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    guild = getattr(message, "guild", None)
    if guild is None:
        return False

    preset_name, count, error = parse_n_pull_command(command_text)
    if error:
        await message.channel.send(error)
        return True
    if preset_name is None or count is None:
        return False

    guild_id = str(guild.id)
    requester_id = str(getattr(message.author, "id", "") or "")
    voice_client = get_guild_voice_client(guild)
    if voice_client is None:
        await message.channel.send("先にVCへ呼んでください。")
        log_n_pull("skipped", guild_id, requester_id, requested_count=count, reason="not_connected")
        return True

    with get_connection() as connection:
        repository = YouTubeNPullRepository(connection)
        preset = repository.find_preset_by_command(guild_id, preset_name)
        if preset is None:
            return False
        if count > int(preset.get("max_pulls") or MAX_N_PULL_COUNT):
            await message.channel.send("{0}連はこのプリセットの上限を超えています。".format(count))
            return True
        try:
            _, cache_status = await refresh_cache_if_needed(repository, guild_id, preset)
            videos = repository.list_cached_videos(int(preset["id"]))
            selected = pick_videos(videos, count)
            if not selected:
                await message.channel.send("利用可能な動画がありません。管理画面でソースを確認してください。")
                connection.commit()
                log_n_pull("empty", guild_id, requester_id, preset, requested_count=count, available_count=0, cache=cache_status)
                return True
            state = get_music_state(guild_id)
            state.text_channel = message.channel
            should_start = state.current is None and not (voice_client.is_playing() or voice_client.is_paused())
            for video in selected:
                state.queue.append(make_track_from_cached_video(video, requester_id))
            connection.commit()
        except Exception as exc:
            connection.rollback()
            print("[WARN] youtube_n_pull failed: bot_instance_id={0} guild_id={1} requester_id={2} error={3}".format(config.BOT_INSTANCE_ID, guild_id, requester_id, type(exc).__name__))
            await message.channel.send("YouTube一覧の取得または抽選に失敗しました。時間を置いて再試行してください。")
            return True

    for response in build_result_messages(preset, count, selected, cache_status):
        await message.channel.send(response)
    log_n_pull("queued", guild_id, requester_id, preset, requested_count=count, available_count=len(videos), queued_count=len(selected), cache=cache_status)
    if should_start:
        await play_next_track(voice_client, guild_id)
    return True
