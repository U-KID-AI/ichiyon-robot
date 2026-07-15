import asyncio
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bot.services.spotify_client import SpotifyTrackMetadata

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None


SPOTIFY_RESOLVE_CONCURRENCY_ENV = "SPOTIFY_RESOLVE_CONCURRENCY"
SPOTIFY_RESOLVE_CACHE_TTL_SECONDS_ENV = "SPOTIFY_RESOLVE_CACHE_TTL_SECONDS"
SPOTIFY_MATCH_MIN_SCORE_ENV = "SPOTIFY_MATCH_MIN_SCORE"
SPOTIFY_MATCH_MIN_MARGIN_ENV = "SPOTIFY_MATCH_MIN_MARGIN"
SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES_ENV = "SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES"
DEFAULT_RESOLVE_CONCURRENCY = 1
DEFAULT_CACHE_TTL_SECONDS = 86400
DEFAULT_MATCH_MIN_SCORE = 70
DEFAULT_MATCH_MIN_MARGIN = 10
DEFAULT_CACHE_MAX_ENTRIES = 1000
DEFAULT_YOUTUBE_CANDIDATES = 5
MAX_QUERY_LENGTH = 180
VARIANT_PATTERNS = {
    "cover": ("cover", "covered by", "カバー", "歌ってみた", "弾いてみた", "演奏してみた", "drum cover"),
    "karaoke": ("karaoke", "カラオケ"),
    "instrumental": ("instrumental", "インスト"),
    "chiptune": ("chiptune", "chip tune", "8-bit", "8bit"),
    "live": ("live", "ライブ"),
    "acoustic": ("acoustic", "アコースティック"),
    "remix": ("remix", "リミックス"),
    "sped up": ("sped up", "speed up"),
    "slowed": ("slowed", "slowed reverb"),
    "nightcore": ("nightcore",),
    "reaction": ("reaction",),
    "tutorial": ("tutorial",),
    "lyric translation": ("lyric translation",),
    "8d": ("8d", "eight dimensional"),
}
VARIANT_WORDS = {alias for aliases in VARIANT_PATTERNS.values() for alias in aliases}
OFFICIAL_WORDS = {"official audio", "official video", "topic", "vevo", "provided to youtube"}


class SpotifyResolveError(Exception):
    user_message = "Spotify曲に一致する音源が見つかりませんでした。"


class SpotifyNoCandidateError(SpotifyResolveError):
    pass


class SpotifyLowScoreError(SpotifyResolveError):
    pass


@dataclass(frozen=True)
class YouTubeCandidate:
    title: str
    webpage_url: str
    duration: Optional[int] = None
    uploader: str = ""


@dataclass(frozen=True)
class ResolvedYouTubeTrack:
    spotify_track_id: str
    youtube_url: str
    youtube_title: str
    duration: Optional[int]
    score: int
    resolved_at: float


_RESOLVE_CACHE: Dict[str, ResolvedYouTubeTrack] = {}
_ALBUM_LOCKS: Dict[str, asyncio.Lock] = {}


def resolve_concurrency() -> int:
    raw = str(os.getenv(SPOTIFY_RESOLVE_CONCURRENCY_ENV, str(DEFAULT_RESOLVE_CONCURRENCY)) or str(DEFAULT_RESOLVE_CONCURRENCY)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_RESOLVE_CONCURRENCY
    return max(1, min(4, value))


def resolve_cache_ttl_seconds() -> int:
    raw = str(os.getenv(SPOTIFY_RESOLVE_CACHE_TTL_SECONDS_ENV, str(DEFAULT_CACHE_TTL_SECONDS)) or str(DEFAULT_CACHE_TTL_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_CACHE_TTL_SECONDS
    return max(60, min(604800, value))


def match_min_score() -> int:
    raw = str(os.getenv(SPOTIFY_MATCH_MIN_SCORE_ENV, str(DEFAULT_MATCH_MIN_SCORE)) or str(DEFAULT_MATCH_MIN_SCORE)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_MATCH_MIN_SCORE
    return max(0, min(100, value))


def match_min_margin() -> int:
    raw = str(os.getenv(SPOTIFY_MATCH_MIN_MARGIN_ENV, str(DEFAULT_MATCH_MIN_MARGIN)) or str(DEFAULT_MATCH_MIN_MARGIN)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_MATCH_MIN_MARGIN
    return max(0, min(100, value))


def resolve_cache_max_entries() -> int:
    raw = str(os.getenv(SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES_ENV, str(DEFAULT_CACHE_MAX_ENTRIES)) or str(DEFAULT_CACHE_MAX_ENTRIES)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_CACHE_MAX_ENTRIES
    return max(100, min(10000, value))


def get_album_lock(guild_key: str) -> asyncio.Lock:
    if guild_key not in _ALBUM_LOCKS:
        _ALBUM_LOCKS[guild_key] = asyncio.Lock()
    return _ALBUM_LOCKS[guild_key]


def remove_album_lock(guild_key: str, lock: asyncio.Lock) -> None:
    if not lock.locked() and _ALBUM_LOCKS.get(guild_key) is lock:
        _ALBUM_LOCKS.pop(guild_key, None)


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"\b(feat|featuring|ft|with)\.?\b", " ", text)
    text = re.sub(r"\b(official\s+(audio|video|music\s+video)|audio|video|lyrics?)\b", " ", text)
    text = re.sub(r"[\[\]\(\)\{\}]", " ", text)
    text = re.sub(r"[^0-9a-zぁ-んァ-ン一-龥ー]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_search_queries(track: SpotifyTrackMetadata) -> List[str]:
    artist = track.artists[0] if track.artists else ""
    title = track.name
    primary = "{0} - {1} official audio".format(artist, title).strip()
    secondary = "{0} {1}".format(artist, title).strip()
    return [primary[:MAX_QUERY_LENGTH], secondary[:MAX_QUERY_LENGTH]]


def _contains_any(text: str, words: Iterable[str]) -> bool:
    normalized = normalize_text(text)
    return any(word in normalized for word in words)


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", normalize_text(value))


def _variant_flags(value: str) -> set:
    normalized = normalize_text(value)
    compact = _compact_text(value)
    raw_normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    raw_compact = re.sub(r"\s+", "", raw_normalized)
    flags = set()
    for canonical, aliases in VARIANT_PATTERNS.items():
        for word in aliases:
            word_normalized = normalize_text(word)
            word_raw = unicodedata.normalize("NFKC", word).lower()
            word_raw_compact = re.sub(r"\s+", "", word_raw)
            if word_normalized and (word_normalized in normalized or word_normalized.replace(" ", "") in compact):
                flags.add(canonical)
                break
            if word_raw and (word_raw in raw_normalized or word_raw_compact in raw_compact):
                flags.add(canonical)
                break
    return flags


def _artist_match_score(track: SpotifyTrackMetadata, candidate: YouTubeCandidate) -> int:
    candidate_text = "{0} {1}".format(candidate.title, candidate.uploader)
    normalized_candidate = normalize_text(candidate_text)
    compact_candidate = _compact_text(candidate_text)
    matched_scores = []
    for index, artist in enumerate(track.artists or []):
        normalized_artist = normalize_text(artist)
        compact_artist = _compact_text(artist)
        if not normalized_artist:
            continue
        if normalized_artist in normalized_candidate or compact_artist in compact_candidate:
            matched_scores.append(35 if index == 0 else 20)
            continue
        if "{0} topic".format(normalized_artist) in normalized_candidate:
            matched_scores.append(35 if index == 0 else 20)
            continue
        if compact_artist and "{0}vevo".format(compact_artist) in compact_candidate:
            matched_scores.append(35 if index == 0 else 20)
    if not matched_scores:
        return 0
    return max(matched_scores)


def _source_quality_score(track: SpotifyTrackMetadata, candidate: YouTubeCandidate) -> int:
    title_raw = unicodedata.normalize("NFKC", candidate.title or "").lower()
    uploader_raw = unicodedata.normalize("NFKC", candidate.uploader or "").lower()
    combined_raw = "{0} {1}".format(title_raw, uploader_raw)
    uploader_normalized = normalize_text(candidate.uploader)
    uploader_compact = _compact_text(candidate.uploader)
    combined_normalized = normalize_text("{0} {1}".format(candidate.title, candidate.uploader))
    combined_compact = _compact_text("{0} {1}".format(candidate.title, candidate.uploader))

    artist_in_source = False
    artist_in_uploader = False
    primary_artist_compact = ""
    for artist in track.artists or []:
        artist_normalized = normalize_text(artist)
        artist_compact = _compact_text(artist)
        if not artist_normalized:
            continue
        if not primary_artist_compact:
            primary_artist_compact = artist_compact
        if artist_normalized in uploader_normalized or artist_compact in uploader_compact:
            artist_in_uploader = True
        if artist_normalized in combined_normalized or artist_compact in combined_compact:
            artist_in_source = True

    if not artist_in_source:
        return 0

    quality = 0
    if primary_artist_compact and "{0}vevo".format(primary_artist_compact) in uploader_compact:
        quality = max(quality, 45)
    if " - topic" in uploader_raw or uploader_raw.endswith("topic"):
        quality = max(quality, 40)
    if artist_in_uploader and "official music video" in title_raw:
        quality = max(quality, 35)
    elif artist_in_uploader and "official video" in title_raw:
        quality = max(quality, 32)
    elif artist_in_uploader and "official audio" in title_raw:
        quality = max(quality, 30)
    if artist_in_uploader and "provided to youtube" in combined_raw:
        quality = max(quality, 28)
    return quality


def score_candidate(track: SpotifyTrackMetadata, candidate: YouTubeCandidate) -> int:
    track_title = normalize_text(track.name)
    candidate_title = normalize_text(candidate.title)
    artist_score = _artist_match_score(track, candidate)
    if artist_score <= 0:
        return 0

    score = 0

    if track_title and track_title in candidate_title:
        score += 35
    elif track_title and all(token in candidate_title for token in track_title.split()[:3]):
        score += 20
    else:
        return 0

    score += artist_score

    if track.duration_seconds and candidate.duration:
        diff = abs(int(candidate.duration) - int(track.duration_seconds))
        if diff <= 10:
            score += 20
        elif diff <= 20:
            score += 12
        elif diff > max(60, int(track.duration_seconds * 0.25)):
            score -= 18

    lowered_raw = "{0} {1}".format(candidate.title, candidate.uploader).lower()
    if any(word in lowered_raw for word in OFFICIAL_WORDS):
        score += 10

    track_variants = _variant_flags(track.name)
    candidate_variants = _variant_flags("{0} {1}".format(candidate.title, candidate.uploader))
    extra_variants = candidate_variants - track_variants
    missing_variants = track_variants - candidate_variants
    if extra_variants or missing_variants:
        return 0
    score -= 35 * len(extra_variants)
    score -= 25 * len(missing_variants)

    if candidate.duration is not None and candidate.duration < 45 and (track.duration_seconds or 0) > 90:
        score -= 25

    return max(0, min(100, score))


def _candidate_from_entry(entry: Dict[str, Any]) -> Optional[YouTubeCandidate]:
    if not entry:
        return None
    url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
    if url and url.startswith("http") is False:
        url = "https://www.youtube.com/watch?v={0}".format(url)
    title = str(entry.get("title") or "").strip()
    if not url or not title:
        return None
    duration = entry.get("duration")
    try:
        duration_value = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_value = None
    return YouTubeCandidate(
        title=title,
        webpage_url=url,
        duration=duration_value,
        uploader=str(entry.get("uploader") or entry.get("channel") or "").strip(),
    )


def search_youtube_candidates(
    query: str,
    guild_id: Optional[str] = None,
    limit: int = DEFAULT_YOUTUBE_CANDIDATES,
    use_cookies: bool = True,
) -> List[YouTubeCandidate]:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    from bot.services.voice_music import build_ytdl_options

    options = build_ytdl_options(guild_id, use_cookies=use_cookies)
    options["extract_flat"] = True
    with yt_dlp.YoutubeDL(options) as ydl:
        result = ydl.extract_info("ytsearch{0}:{1}".format(limit, query), download=False)
    entries = (result or {}).get("entries") or []
    candidates = []
    for entry in entries:
        candidate = _candidate_from_entry(entry or {})
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def select_best_candidate(track: SpotifyTrackMetadata, candidates: List[YouTubeCandidate]) -> Tuple[YouTubeCandidate, int]:
    if not candidates:
        raise SpotifyNoCandidateError()
    scored = [
        (score_candidate(track, candidate), _source_quality_score(track, candidate), candidate)
        for candidate in candidates
    ]
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, best_quality, best_candidate = scored[0]
    if best_score < match_min_score():
        raise SpotifyLowScoreError("best score={0}".format(best_score))
    eligible = [item for item in scored if item[0] >= match_min_score()]
    if len(eligible) > 1:
        second_score, second_quality, _second_candidate = eligible[1]
        margin = best_score - second_score
        if best_quality == 0 and second_quality == 0 and 0 < margin < match_min_margin():
            raise SpotifyLowScoreError("score margin too small: best={0} second={1}".format(best_score, second_score))
    return best_candidate, best_score


def clear_resolve_cache() -> None:
    _RESOLVE_CACHE.clear()


def invalidate_resolve_cache(track_id: str) -> None:
    _RESOLVE_CACHE.pop(track_id, None)


def prune_resolve_cache(now: Optional[float] = None) -> None:
    current_time = time.time() if now is None else now
    ttl = resolve_cache_ttl_seconds()
    expired = [track_id for track_id, item in _RESOLVE_CACHE.items() if current_time - item.resolved_at > ttl]
    for track_id in expired:
        _RESOLVE_CACHE.pop(track_id, None)

    max_entries = resolve_cache_max_entries()
    overflow = len(_RESOLVE_CACHE) - max_entries
    if overflow <= 0:
        return
    oldest = sorted(_RESOLVE_CACHE.items(), key=lambda item: item[1].resolved_at)[:overflow]
    for track_id, _item in oldest:
        _RESOLVE_CACHE.pop(track_id, None)


def _store_resolved_track(resolved: ResolvedYouTubeTrack) -> None:
    prune_resolve_cache(resolved.resolved_at)
    _RESOLVE_CACHE[resolved.spotify_track_id] = resolved
    prune_resolve_cache(resolved.resolved_at)


async def resolve_spotify_track_to_youtube(track: SpotifyTrackMetadata, guild_id: str, bypass_cache: bool = False) -> ResolvedYouTubeTrack:
    prune_resolve_cache()
    cached = None if bypass_cache else _RESOLVE_CACHE.get(track.track_id)
    now = time.time()
    if cached and now - cached.resolved_at <= resolve_cache_ttl_seconds():
        return cached

    last_error: Optional[Exception] = None
    for query in build_search_queries(track):
        try:
            candidates = await asyncio.to_thread(search_youtube_candidates, query, guild_id, DEFAULT_YOUTUBE_CANDIDATES)
        except Exception as exc:
            from bot.messages import get_bot
            from bot.services.youtube_cookie_monitor import AUTH_FAILURE_STATUSES, classify_ytdlp_error, handle_transient_auth_failure

            error_status = classify_ytdlp_error(exc)
            if error_status not in AUTH_FAILURE_STATUSES:
                last_error = exc
                continue
            await handle_transient_auth_failure(get_bot(), error_status)
            try:
                candidates = await asyncio.to_thread(search_youtube_candidates, query, guild_id, DEFAULT_YOUTUBE_CANDIDATES, False)
            except Exception as fallback_exc:
                last_error = fallback_exc
                continue
        try:
            best, score = select_best_candidate(track, candidates)
            resolved = ResolvedYouTubeTrack(
                spotify_track_id=track.track_id,
                youtube_url=best.webpage_url,
                youtube_title=best.title,
                duration=best.duration,
                score=score,
                resolved_at=now,
            )
            _store_resolved_track(resolved)
            return resolved
        except SpotifyResolveError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise SpotifyNoCandidateError()
