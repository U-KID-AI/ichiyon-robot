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
DEFAULT_RESOLVE_CONCURRENCY = 1
DEFAULT_CACHE_TTL_SECONDS = 86400
DEFAULT_MATCH_MIN_SCORE = 55
DEFAULT_YOUTUBE_CANDIDATES = 5
MAX_QUERY_LENGTH = 180
VARIANT_WORDS = {"cover", "karaoke", "instrumental", "live", "remix", "sped up", "slowed", "nightcore", "reaction", "tutorial"}
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


def get_album_lock(guild_key: str) -> asyncio.Lock:
    if guild_key not in _ALBUM_LOCKS:
        _ALBUM_LOCKS[guild_key] = asyncio.Lock()
    return _ALBUM_LOCKS[guild_key]


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"\b(feat|featuring|ft)\.?\b", " ", text)
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


def score_candidate(track: SpotifyTrackMetadata, candidate: YouTubeCandidate) -> int:
    track_title = normalize_text(track.name)
    candidate_title = normalize_text(candidate.title)
    artist_tokens = [normalize_text(artist) for artist in track.artists if normalize_text(artist)]
    score = 0

    if track_title and track_title in candidate_title:
        score += 35
    elif track_title and all(token in candidate_title for token in track_title.split()[:3]):
        score += 20

    if artist_tokens and any(token in candidate_title or token in normalize_text(candidate.uploader) for token in artist_tokens):
        score += 25

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

    track_has_variant = _contains_any(track.name, VARIANT_WORDS)
    if not track_has_variant:
        for word in VARIANT_WORDS:
            if word in lowered_raw:
                score -= 12

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
    scored = [(score_candidate(track, candidate), candidate) for candidate in candidates]
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_candidate = scored[0]
    if best_score < match_min_score():
        raise SpotifyLowScoreError("best score={0}".format(best_score))
    return best_candidate, best_score


async def resolve_spotify_track_to_youtube(track: SpotifyTrackMetadata, guild_id: str) -> ResolvedYouTubeTrack:
    cached = _RESOLVE_CACHE.get(track.track_id)
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
            _RESOLVE_CACHE[track.track_id] = resolved
            return resolved
        except SpotifyResolveError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise SpotifyNoCandidateError()
