import html
import json
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import httpx

from bot.services.spotify_client import (
    SpotifyAlbumMetadata,
    SpotifyArtistMetadata,
    SpotifyPlaylistMetadata,
    SpotifyTrackMetadata,
    max_album_tracks,
)
from bot.services.spotify_playlist.errors import (
    SpotifyPlaylistNoTracks,
    SpotifyPlaylistParseError,
    SpotifyPlaylistResolveError,
)
from bot.services.spotify_playlist.models import build_public_playlist


SPOTIFY_OPEN_BASE = "https://open.spotify.com"
PUBLIC_PROVIDER = "public_embed"
PAGE_PROVIDER = "public_page"
TRACK_ID_RE = re.compile(r"spotify:track:([A-Za-z0-9]{22})")
ALBUM_ID_RE = re.compile(r"spotify:album:([A-Za-z0-9]{22})")
ARTIST_ID_RE = re.compile(r"spotify:artist:([A-Za-z0-9]{22})")
TRACK_URL_RE = re.compile(r"https://open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([A-Za-z0-9]{22})")
NEXT_DATA_RE = re.compile(
    r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


class SpotifyPublicResolveError(SpotifyPlaylistResolveError):
    user_message = "このSpotifyリンクの公開情報を取得できませんでした。"


def spotify_embed_url(kind: str, spotify_id: str) -> str:
    return "{0}/embed/{1}/{2}".format(SPOTIFY_OPEN_BASE, kind, spotify_id)


def spotify_public_url(kind: str, spotify_id: str) -> str:
    return "{0}/{1}/{2}".format(SPOTIFY_OPEN_BASE, kind, spotify_id)


def _safe_text(value: Any) -> str:
    return html.unescape(str(value or "")).strip()


def _duration_to_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number if number > 1000 else number * 1000
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
        names: List[str] = []
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
    for sep in (",", "、", "・", " • ", " - "):
        if sep in text:
            return [part.strip() for part in text.split(sep) if part.strip()]
    return [text]


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


def _extract_next_data(page_html: str) -> Dict[str, Any]:
    match = NEXT_DATA_RE.search(page_html)
    if not match:
        raise SpotifyPlaylistParseError("missing next data")
    try:
        return json.loads(html.unescape(match.group(1)))
    except (TypeError, ValueError) as exc:
        raise SpotifyPlaylistParseError("invalid next data") from exc


def _image_from_payload(payload: Any) -> str:
    for item in _iter_dicts(payload, max_depth=10):
        for key in ("visualIdentity", "coverArt", "cover", "image", "images"):
            value = item.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                for image_key in ("url", "src"):
                    url = _safe_text(value.get(image_key))
                    if url.startswith("http"):
                        return url
                for child in value.values():
                    if isinstance(child, dict):
                        url = _safe_text(child.get("url") or child.get("src"))
                        if url.startswith("http"):
                            return url
                    if isinstance(child, list):
                        for entry in child:
                            if isinstance(entry, dict):
                                url = _safe_text(entry.get("url") or entry.get("src"))
                                if url.startswith("http"):
                                    return url
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        url = _safe_text(entry.get("url") or entry.get("src"))
                        if url.startswith("http"):
                            return url
    return ""


def _track_id_from_dict(item: Dict[str, Any]) -> str:
    for key in ("uri", "trackUri", "spotifyUri"):
        match = TRACK_ID_RE.search(_safe_text(item.get(key)))
        if match:
            return match.group(1)
    for key in ("url", "href", "spotify_url", "external_url"):
        match = TRACK_URL_RE.search(_safe_text(item.get(key)))
        if match:
            return match.group(1)
    raw_id = _safe_text(item.get("id") or item.get("uid") or item.get("track_id"))
    return raw_id if re.fullmatch(r"[A-Za-z0-9]{22}", raw_id) else ""


def _track_from_public_dict(item: Dict[str, Any], index: int, album_name: str = "", default_artist: str = "") -> Optional[SpotifyTrackMetadata]:
    uri = _safe_text(item.get("uri") or item.get("trackUri") or item.get("spotifyUri"))
    if uri and not uri.startswith("spotify:track:"):
        return None
    entity_type = _safe_text(item.get("type") or item.get("entityType")).lower()
    if entity_type and entity_type not in ("track", "song"):
        return None
    track_id = _track_id_from_dict(item)
    name = _safe_text(item.get("name") or item.get("title") or item.get("trackName"))
    artists = _artist_names(item.get("artists") or item.get("artist") or item.get("subtitle") or item.get("byArtist"))
    if not artists and default_artist:
        artists = [default_artist]
    duration_ms = _duration_to_ms(
        item.get("duration_ms")
        or item.get("durationMs")
        or item.get("duration")
        or item.get("length")
        or item.get("time")
    )
    if not name or not artists or duration_ms is None:
        return None
    synthetic_id = "PUBLICSPOTIFY{0:09d}".format(index)[-22:]
    spotify_url = "{0}/track/{1}".format(SPOTIFY_OPEN_BASE, track_id) if track_id else ""
    return SpotifyTrackMetadata(
        track_id=track_id or synthetic_id,
        name=name,
        artists=artists,
        album_name=album_name,
        duration_ms=duration_ms,
        isrc="",
        explicit=bool(item.get("isExplicit")),
        spotify_url=spotify_url,
        disc_number=item.get("disc_number") or item.get("discNumber") or 1,
        track_number=item.get("track_number") or item.get("trackNumber") or index,
    )


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


def _name_from_payload(payload: Any, fallback: str = "") -> str:
    for item in _iter_dicts(payload, max_depth=10):
        for key in ("name", "title", "playlistName", "albumName", "artistName"):
            value = _safe_text(item.get(key))
            if value and not value.lower().startswith("spotify"):
                return value
    return fallback


def _entity_dict(payload: Any, spotify_id: str, kind: str) -> Optional[Dict[str, Any]]:
    expected_uri = "spotify:{0}:{1}".format(kind, spotify_id)
    for item in _iter_dicts(payload, max_depth=12):
        if _safe_text(item.get("uri")) == expected_uri:
            return item
    for item in _iter_dicts(payload, max_depth=12):
        if _safe_text(item.get("id")) == spotify_id and _safe_text(item.get("type")).lower() in ("", kind):
            return item
    return None


def parse_public_track_html(track_id: str, page_html: str, provider: str = PUBLIC_PROVIDER) -> SpotifyTrackMetadata:
    payload = _extract_next_data(page_html)
    entity = _entity_dict(payload, track_id, "track")
    if entity is None:
        raise SpotifyPublicResolveError()
    track = _track_from_public_dict(entity, 1)
    if track is None:
        raise SpotifyPublicResolveError()
    album_name = ""
    for item in _iter_dicts(entity, max_depth=4):
        if _safe_text(item.get("uri")).startswith("spotify:album:"):
            album_name = _safe_text(item.get("name") or item.get("title"))
            break
    if not album_name:
        album_name = _safe_text(entity.get("albumName") or entity.get("relatedEntityName"))
    return SpotifyTrackMetadata(
        track_id=track.track_id,
        name=track.name,
        artists=track.artists,
        album_name=album_name,
        duration_ms=track.duration_ms,
        isrc=track.isrc,
        explicit=track.explicit,
        spotify_url=track.spotify_url or spotify_public_url("track", track_id),
        disc_number=track.disc_number,
        track_number=track.track_number,
    )


def _tracks_from_payload(payload: Any, album_name: str = "", default_artist: str = "") -> List[SpotifyTrackMetadata]:
    tracks: List[SpotifyTrackMetadata] = []
    for item in _iter_dicts(payload):
        track = _track_from_public_dict(item, len(tracks) + 1, album_name=album_name, default_artist=default_artist)
        if track is not None:
            tracks.append(track)
    return _dedupe_tracks(tracks)


def parse_public_playlist_html(playlist_id: str, page_html: str, provider: str = PUBLIC_PROVIDER) -> SpotifyPlaylistMetadata:
    payload = _extract_next_data(page_html)
    tracks = _tracks_from_payload(payload)
    if not tracks:
        raise SpotifyPlaylistNoTracks()
    return build_public_playlist(
        playlist_id=playlist_id,
        name=_name_from_payload(payload, "Spotify Playlist"),
        spotify_url=spotify_public_url("playlist", playlist_id),
        image_url=_image_from_payload(payload),
        tracks=tracks,
        source_provider=provider,
    )


def parse_public_album_html(album_id: str, page_html: str, provider: str = PUBLIC_PROVIDER) -> SpotifyAlbumMetadata:
    payload = _extract_next_data(page_html)
    entity = _entity_dict(payload, album_id, "album") or {}
    album_name = _safe_text(entity.get("name") or entity.get("title")) or _name_from_payload(payload, "Spotify Album")
    album_artists = _artist_names(entity.get("artists") or entity.get("subtitle") or entity.get("byArtist"))
    tracks = _tracks_from_payload(entity or payload, album_name=album_name, default_artist=", ".join(album_artists))
    tracks = sorted(tracks[: max_album_tracks()], key=lambda track: (track.disc_number or 1, track.track_number or 0))
    if not tracks:
        raise SpotifyPublicResolveError()
    return SpotifyAlbumMetadata(
        album_id=album_id,
        name=album_name,
        artists=album_artists,
        spotify_url=spotify_public_url("album", album_id),
        tracks=tracks,
        skipped_tracks=0,
        truncated=False,
    )


def parse_public_artist_html(artist_id: str, page_html: str, provider: str = PUBLIC_PROVIDER) -> SpotifyArtistMetadata:
    payload = _extract_next_data(page_html)
    entity = _entity_dict(payload, artist_id, "artist") or {}
    artist_name = _safe_text(entity.get("name") or entity.get("title")) or _name_from_payload(payload, "Spotify Artist")
    tracks = _tracks_from_payload(entity or payload, default_artist=artist_name)
    if not tracks:
        raise SpotifyPublicResolveError()
    return SpotifyArtistMetadata(
        artist_id=artist_id,
        name=artist_name,
        spotify_url=spotify_public_url("artist", artist_id),
        image_url=_image_from_payload(entity or payload),
        tracks=tracks,
        source_provider=provider,
    )


async def _fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.8",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
    if response.status_code < 200 or response.status_code >= 300:
        raise SpotifyPublicResolveError()
    return response.text


async def _resolve_with_static_pages(kind: str, spotify_id: str, parser):
    errors: List[str] = []
    for provider, url in ((PUBLIC_PROVIDER, spotify_embed_url(kind, spotify_id)), (PAGE_PROVIDER, spotify_public_url(kind, spotify_id))):
        try:
            html_text = await _fetch_html(url)
            result = parser(spotify_id, html_text, provider)
            return result
        except Exception as exc:
            errors.append(type(exc).__name__)
            continue
    raise SpotifyPublicResolveError(",".join(errors))


class SpotifyPublicResolver:
    async def get_track(self, track_id: str) -> SpotifyTrackMetadata:
        started = time.perf_counter()
        track = await _resolve_with_static_pages("track", track_id, parse_public_track_html)
        print("[INFO] spotify_public_track_resolved provider={0} track_id={1} elapsed_ms={2}".format(PUBLIC_PROVIDER, track_id, int((time.perf_counter() - started) * 1000)))
        return track

    async def get_playlist(self, playlist_id: str) -> SpotifyPlaylistMetadata:
        started = time.perf_counter()
        playlist = await _resolve_with_static_pages("playlist", playlist_id, parse_public_playlist_html)
        print("[INFO] spotify_playlist_resolved provider={0} playlist_id={1} track_count={2} elapsed_ms={3}".format(playlist.source_provider, playlist_id, len(playlist.tracks), int((time.perf_counter() - started) * 1000)))
        return playlist

    async def get_album(self, album_id: str) -> SpotifyAlbumMetadata:
        started = time.perf_counter()
        album = await _resolve_with_static_pages("album", album_id, parse_public_album_html)
        print("[INFO] spotify_public_album_resolved provider={0} album_id={1} track_count={2} elapsed_ms={3}".format(PUBLIC_PROVIDER, album_id, len(album.tracks), int((time.perf_counter() - started) * 1000)))
        return album

    async def get_artist(self, artist_id: str) -> SpotifyArtistMetadata:
        started = time.perf_counter()
        artist = await _resolve_with_static_pages("artist", artist_id, parse_public_artist_html)
        print("[INFO] spotify_public_artist_resolved provider={0} artist_id={1} track_count={2} elapsed_ms={3}".format(PUBLIC_PROVIDER, artist_id, len(artist.tracks), int((time.perf_counter() - started) * 1000)))
        return artist


_SHARED_PUBLIC_RESOLVER: Optional[SpotifyPublicResolver] = None


def get_spotify_public_resolver() -> SpotifyPublicResolver:
    global _SHARED_PUBLIC_RESOLVER
    if _SHARED_PUBLIC_RESOLVER is None:
        _SHARED_PUBLIC_RESOLVER = SpotifyPublicResolver()
    return _SHARED_PUBLIC_RESOLVER
