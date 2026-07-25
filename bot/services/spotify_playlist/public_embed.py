import html
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx

from bot.services.spotify_client import SpotifyPlaylistMetadata, SpotifyTrackMetadata
from bot.services.spotify_playlist.errors import SpotifyPlaylistNoTracks, SpotifyPlaylistParseError
from bot.services.spotify_playlist.models import build_public_playlist


PUBLIC_EMBED_PROVIDER = "public_embed"
SPOTIFY_PUBLIC_PLAYLIST_CACHE_TTL_SECONDS_ENV = "SPOTIFY_PUBLIC_PLAYLIST_CACHE_TTL_SECONDS"
SPOTIFY_PUBLIC_PLAYLIST_CACHE_MAX_ENTRIES_ENV = "SPOTIFY_PUBLIC_PLAYLIST_CACHE_MAX_ENTRIES"
DEFAULT_PUBLIC_PLAYLIST_CACHE_TTL_SECONDS = 1800
DEFAULT_PUBLIC_PLAYLIST_CACHE_MAX_ENTRIES = 20
SPOTIFY_OPEN_BASE = "https://open.spotify.com"
TRACK_ID_RE = re.compile(r"spotify:track:([A-Za-z0-9]{22})")
TRACK_URL_RE = re.compile(r"https://open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([A-Za-z0-9]{22})")
NEXT_DATA_RE = re.compile(
    r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class _CacheEntry:
    playlist: SpotifyPlaylistMetadata
    stored_at: float


_CACHE: Dict[str, _CacheEntry] = {}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def public_playlist_cache_ttl_seconds() -> int:
    return _env_int(
        SPOTIFY_PUBLIC_PLAYLIST_CACHE_TTL_SECONDS_ENV,
        DEFAULT_PUBLIC_PLAYLIST_CACHE_TTL_SECONDS,
        60,
        24 * 60 * 60,
    )


def public_playlist_cache_max_entries() -> int:
    return _env_int(
        SPOTIFY_PUBLIC_PLAYLIST_CACHE_MAX_ENTRIES_ENV,
        DEFAULT_PUBLIC_PLAYLIST_CACHE_MAX_ENTRIES,
        1,
        200,
    )


def clear_public_playlist_cache() -> None:
    _CACHE.clear()


def _cache_get(playlist_id: str) -> Optional[SpotifyPlaylistMetadata]:
    entry = _CACHE.get(playlist_id)
    if entry is None:
        return None
    if time.time() - entry.stored_at > public_playlist_cache_ttl_seconds():
        _CACHE.pop(playlist_id, None)
        return None
    return entry.playlist


def _cache_put(playlist_id: str, playlist: SpotifyPlaylistMetadata) -> None:
    _CACHE[playlist_id] = _CacheEntry(playlist=playlist, stored_at=time.time())
    while len(_CACHE) > public_playlist_cache_max_entries():
        oldest_key = min(_CACHE, key=lambda key: _CACHE[key].stored_at)
        _CACHE.pop(oldest_key, None)


def spotify_embed_playlist_url(playlist_id: str) -> str:
    return "{0}/embed/playlist/{1}".format(SPOTIFY_OPEN_BASE, playlist_id)


def spotify_playlist_url(playlist_id: str) -> str:
    return "{0}/playlist/{1}".format(SPOTIFY_OPEN_BASE, playlist_id)


def _safe_text(value: Any) -> str:
    return html.unescape(str(value or "")).strip()


def _duration_to_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        if number > 1000:
            return number
        return number * 1000
    text = _safe_text(value)
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        return number if number > 1000 else number * 1000
    parts = text.split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds * 1000


def _artist_names(value: Any) -> List[str]:
    if isinstance(value, list):
        names = []
        for item in value:
            if isinstance(item, dict):
                name = _safe_text(item.get("name") or item.get("title"))
            else:
                name = _safe_text(item)
            if name:
                names.append(name)
        return names
    if isinstance(value, dict):
        name = _safe_text(value.get("name") or value.get("title"))
        return [name] if name else []
    text = _safe_text(value)
    if not text:
        return []
    separators = [",", "、", " • ", " - "]
    for sep in separators:
        if sep in text:
            return [part.strip() for part in text.split(sep) if part.strip()]
    return [text]


def _track_id_from_dict(item: Dict[str, Any]) -> str:
    for key in ("uri", "trackUri", "spotifyUri"):
        match = TRACK_ID_RE.search(_safe_text(item.get(key)))
        if match:
            return match.group(1)
    for key in ("url", "href", "spotify_url", "external_url"):
        match = TRACK_URL_RE.search(_safe_text(item.get(key)))
        if match:
            return match.group(1)
    raw_id = _safe_text(item.get("id") or item.get("track_id"))
    return raw_id if re.fullmatch(r"[A-Za-z0-9]{22}", raw_id) else ""


def _track_from_public_dict(item: Dict[str, Any], index: int) -> Optional[SpotifyTrackMetadata]:
    track_id = _track_id_from_dict(item)
    name = _safe_text(item.get("name") or item.get("title") or item.get("trackName"))
    artists = _artist_names(item.get("artists") or item.get("artist") or item.get("subtitle") or item.get("byArtist"))
    duration_ms = _duration_to_ms(
        item.get("duration_ms")
        or item.get("durationMs")
        or item.get("duration")
        or item.get("length")
        or item.get("time")
    )
    if not name or not artists or duration_ms is None:
        return None
    synthetic_id = "PUBLICPLAYLIST{0:08d}".format(index)[-22:]
    spotify_url = "https://open.spotify.com/track/{0}".format(track_id) if track_id else ""
    return SpotifyTrackMetadata(
        track_id=track_id or synthetic_id,
        name=name,
        artists=artists,
        album_name="",
        duration_ms=duration_ms,
        isrc="",
        explicit=False,
        spotify_url=spotify_url,
        disc_number=1,
        track_number=index,
    )


def _iter_dicts(value: Any, max_depth: int = 16) -> Iterable[Dict[str, Any]]:
    stack: List[Tuple[Any, int]] = [(value, 0)]
    seen: Set[int] = set()
    while stack:
        item, depth = stack.pop()
        if depth > max_depth:
            continue
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(item, dict):
            yield item
            for child in item.values():
                if isinstance(child, (dict, list)):
                    stack.append((child, depth + 1))
        elif isinstance(item, list):
            for child in reversed(item):
                if isinstance(child, (dict, list)):
                    stack.append((child, depth + 1))


def _dedupe_tracks(tracks: Iterable[SpotifyTrackMetadata]) -> List[SpotifyTrackMetadata]:
    deduped: List[SpotifyTrackMetadata] = []
    seen: Set[str] = set()
    for track in tracks:
        key = track.spotify_url or "{0}|{1}|{2}".format(track.name.casefold(), track.display_artist.casefold(), track.duration_ms or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(track)
    return deduped


def _extract_next_data(page_html: str) -> Optional[Dict[str, Any]]:
    match = NEXT_DATA_RE.search(page_html)
    if not match:
        return None
    try:
        return json.loads(html.unescape(match.group(1)))
    except (TypeError, ValueError) as exc:
        raise SpotifyPlaylistParseError("invalid next data") from exc


def _playlist_name_from_payload(payload: Any, fallback: str = "") -> str:
    for item in _iter_dicts(payload, max_depth=10):
        for key in ("playlistName", "playlist_name", "name", "title"):
            value = _safe_text(item.get(key))
            if value and not value.lower().startswith("spotify"):
                return value
    return fallback


def _image_from_payload(payload: Any) -> str:
    for item in _iter_dicts(payload, max_depth=10):
        for key in ("coverArt", "cover", "image", "images"):
            value = item.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                url = _safe_text(value.get("url") or value.get("src"))
                if url.startswith("http"):
                    return url
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        url = _safe_text(entry.get("url") or entry.get("src"))
                        if url.startswith("http"):
                            return url
    return ""


def parse_public_embed_html(playlist_id: str, page_html: str) -> SpotifyPlaylistMetadata:
    payload = _extract_next_data(page_html)
    if payload is None:
        raise SpotifyPlaylistParseError("missing next data")

    tracks = []
    for item in _iter_dicts(payload):
        track = _track_from_public_dict(item, len(tracks) + 1)
        if track is not None:
            tracks.append(track)
    tracks = _dedupe_tracks(tracks)
    if not tracks:
        raise SpotifyPlaylistNoTracks()

    playlist_name = _playlist_name_from_payload(payload, "Spotify Playlist")
    image_url = _image_from_payload(payload)
    return build_public_playlist(
        playlist_id=playlist_id,
        name=playlist_name,
        spotify_url=spotify_playlist_url(playlist_id),
        image_url=image_url,
        tracks=tracks,
        source_provider=PUBLIC_EMBED_PROVIDER,
    )


async def fetch_public_embed_playlist(playlist_id: str, use_cache: bool = True) -> SpotifyPlaylistMetadata:
    if use_cache:
        cached = _cache_get(playlist_id)
        if cached is not None:
            return cached
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.8",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(spotify_embed_playlist_url(playlist_id))
    if response.status_code < 200 or response.status_code >= 300:
        raise SpotifyPlaylistParseError("public embed http status {0}".format(response.status_code))
    playlist = parse_public_embed_html(playlist_id, response.text)
    _cache_put(playlist_id, playlist)
    return playlist
