import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx


SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"
SPOTIFY_CLIENT_ID_ENV = "SPOTIFY_CLIENT_ID"
SPOTIFY_CLIENT_SECRET_ENV = "SPOTIFY_CLIENT_SECRET"
SPOTIFY_MARKET_ENV = "SPOTIFY_MARKET"
SPOTIFY_MAX_ALBUM_TRACKS_ENV = "SPOTIFY_MAX_ALBUM_TRACKS"
DEFAULT_SPOTIFY_MARKET = "JP"
DEFAULT_MAX_ALBUM_TRACKS = 100
DEFAULT_PLAYLIST_ITEMS_PAGE_LIMIT = 50
_SHARED_SPOTIFY_CLIENT: Optional["SpotifyClient"] = None


class SpotifyError(Exception):
    user_message = "Spotify情報の取得に失敗しました。"


class SpotifyCredentialsMissing(SpotifyError):
    user_message = "Spotifyリンク対応が設定されていません。管理者へ連絡してください。"


class SpotifyAuthError(SpotifyError):
    user_message = "Spotify認証に失敗しました。管理者へ連絡してください。"

    def __init__(self, status_code: int = 0, error_code: str = ""):
        super().__init__("Spotify auth failed: status={0} error={1}".format(status_code, error_code or "unknown"))
        self.status_code = status_code
        self.error_code = error_code or "unknown"


class SpotifyNotFoundError(SpotifyError):
    user_message = "Spotifyの曲またはアルバムが見つかりませんでした。"


class SpotifyRateLimitedError(SpotifyError):
    def __init__(self, retry_after: Optional[int] = None):
        super().__init__("Spotify API rate limited")
        self.retry_after = retry_after
        self.status_code = 429
        self.user_message = "Spotify APIの制限に達しました。少し時間を置いて再試行してください。"


class SpotifyApiError(SpotifyError):
    def __init__(self, status_code: int):
        super().__init__("Spotify API failed: status={0}".format(status_code))
        self.status_code = status_code
        if status_code == 403:
            self.user_message = "Spotify APIの権限またはアクセス制限により取得できませんでした。\nSpotify API status: 403"
        elif status_code == 404:
            self.user_message = SpotifyNotFoundError.user_message
        elif status_code:
            self.user_message = "Spotify情報を取得できませんでした。\nSpotify API status: {0}".format(status_code)


class SpotifyTimeoutError(SpotifyError):
    user_message = "Spotify APIがタイムアウトしました。時間を置いて再試行してください。"


class SpotifyJsonError(SpotifyApiError):
    def __init__(self):
        super().__init__(400)
        self.user_message = "Spotify APIのレスポンスを解析できませんでした。時間を置いて再試行してください。"


@dataclass(frozen=True)
class SpotifyTrackMetadata:
    track_id: str
    name: str
    artists: List[str]
    album_name: str
    duration_ms: Optional[int]
    isrc: str
    explicit: bool
    spotify_url: str
    disc_number: Optional[int] = None
    track_number: Optional[int] = None

    @property
    def duration_seconds(self) -> Optional[int]:
        if self.duration_ms is None:
            return None
        return max(0, int(round(self.duration_ms / 1000)))

    @property
    def display_artist(self) -> str:
        return ", ".join(self.artists)


@dataclass(frozen=True)
class SpotifyAlbumMetadata:
    album_id: str
    name: str
    artists: List[str]
    spotify_url: str
    tracks: List[SpotifyTrackMetadata]
    skipped_tracks: int = 0
    truncated: bool = False

    @property
    def display_artist(self) -> str:
        return ", ".join(self.artists)


@dataclass(frozen=True)
class SpotifyPlaylistMetadata:
    playlist_id: str
    name: str
    spotify_url: str
    image_url: str
    tracks: List[SpotifyTrackMetadata]
    skipped_tracks: int = 0
    total_tracks: int = 0
    item_field_tracks: int = 0
    track_field_tracks: int = 0
    local_tracks: int = 0
    episode_tracks: int = 0
    missing_metadata_tracks: int = 0
    source_provider: str = "official_api"

    @property
    def total_duration_seconds(self) -> int:
        return sum(track.duration_seconds or 0 for track in self.tracks)


def spotify_market() -> str:
    return str(os.getenv(SPOTIFY_MARKET_ENV, DEFAULT_SPOTIFY_MARKET) or DEFAULT_SPOTIFY_MARKET).strip() or DEFAULT_SPOTIFY_MARKET


def max_album_tracks() -> int:
    raw = str(os.getenv(SPOTIFY_MAX_ALBUM_TRACKS_ENV, str(DEFAULT_MAX_ALBUM_TRACKS)) or str(DEFAULT_MAX_ALBUM_TRACKS)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_MAX_ALBUM_TRACKS
    return max(1, min(200, value))


def _artist_names(items: Any) -> List[str]:
    names = []
    for item in items or []:
        name = str((item or {}).get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _external_url(item: Dict[str, Any]) -> str:
    urls = item.get("external_urls") or {}
    return str(urls.get("spotify") or "").strip()


def _image_url(item: Dict[str, Any]) -> str:
    images = item.get("images") or []
    if not isinstance(images, list):
        return ""
    for image in images:
        url = str((image or {}).get("url") or "").strip()
        if url:
            return url
    return ""


def _track_from_payload(payload: Dict[str, Any], album_name: str = "") -> Optional[SpotifyTrackMetadata]:
    if not payload or payload.get("is_local") or payload.get("type") not in (None, "track"):
        return None
    track_id = str(payload.get("id") or "").strip()
    name = str(payload.get("name") or "").strip()
    artists = _artist_names(payload.get("artists"))
    if not track_id or not name or not artists:
        return None
    external_ids = payload.get("external_ids") or {}
    album_payload = payload.get("album") or {}
    return SpotifyTrackMetadata(
        track_id=track_id,
        name=name,
        artists=artists,
        album_name=str(album_payload.get("name") or album_name or "").strip(),
        duration_ms=payload.get("duration_ms"),
        isrc=str(external_ids.get("isrc") or "").strip(),
        explicit=bool(payload.get("explicit")),
        spotify_url=_external_url(payload),
        disc_number=payload.get("disc_number"),
        track_number=payload.get("track_number"),
    )


@dataclass
class SpotifyPlaylistFetchStats:
    playlist_id: str
    market: str
    page: int = 0
    items_count: int = 0
    item_field_count: int = 0
    track_field_count: int = 0
    local_count: int = 0
    episode_count: int = 0
    missing_metadata_count: int = 0
    accepted_count: int = 0

    @property
    def skipped_count(self) -> int:
        return self.local_count + self.episode_count + self.missing_metadata_count


def _playlist_item_payload(entry: Dict[str, Any], stats: SpotifyPlaylistFetchStats) -> Optional[Dict[str, Any]]:
    if not entry:
        stats.missing_metadata_count += 1
        return None
    has_item = "item" in entry
    has_track = "track" in entry
    if has_item:
        stats.item_field_count += 1
        payload = entry.get("item")
    else:
        payload = None
    if payload is None and has_track:
        stats.track_field_count += 1
        payload = entry.get("track")
    if not isinstance(payload, dict):
        stats.missing_metadata_count += 1
        return None
    return payload


def _track_from_playlist_entry(entry: Dict[str, Any], stats: SpotifyPlaylistFetchStats) -> Optional[SpotifyTrackMetadata]:
    payload = _playlist_item_payload(entry, stats)
    if payload is None:
        return None
    if payload.get("is_local"):
        stats.local_count += 1
        return None
    payload_type = payload.get("type")
    if payload_type not in (None, "track"):
        stats.episode_count += 1
        return None
    track = _track_from_payload(payload)
    if track is None:
        stats.missing_metadata_count += 1
        return None
    stats.accepted_count += 1
    return track


def _log_spotify_playlist_fetch(stats: SpotifyPlaylistFetchStats, http_status: int) -> None:
    print(
        "[INFO] spotify_playlist_fetch playlist_id={0} http_status={1} page={2} market={3} "
        "items_count={4} item_field_count={5} track_field_count={6} local_count={7} "
        "episode_count={8} missing_metadata_count={9} accepted_count={10}".format(
            stats.playlist_id,
            http_status,
            stats.page,
            stats.market or "",
            stats.items_count,
            stats.item_field_count,
            stats.track_field_count,
            stats.local_count,
            stats.episode_count,
            stats.missing_metadata_count,
            stats.accepted_count,
        )
    )


class SpotifyClient:
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        market: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ):
        self.client_id = (client_id if client_id is not None else os.getenv(SPOTIFY_CLIENT_ID_ENV) or "").strip()
        self.client_secret = (client_secret if client_secret is not None else os.getenv(SPOTIFY_CLIENT_SECRET_ENV) or "").strip()
        self.market = (market or spotify_market()).strip()
        self.timeout_seconds = timeout_seconds
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def _fetch_token(self) -> str:
        if not self.configured:
            raise SpotifyCredentialsMissing()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    SPOTIFY_TOKEN_URL,
                    data={"grant_type": "client_credentials"},
                    auth=(self.client_id, self.client_secret),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.TimeoutException as exc:
            raise SpotifyTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise SpotifyAuthError() from exc

        if response.status_code != 200:
            error_code = "unknown"
            try:
                data = response.json()
                error_code = str(data.get("error") or "unknown").strip() or "unknown"
            except ValueError:
                pass
            print("[WARN] spotify_token_error status={0} error={1}".format(response.status_code, error_code))
            raise SpotifyAuthError(response.status_code, error_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise SpotifyAuthError(response.status_code, "invalid_json") from exc
        token = str(data.get("access_token") or "").strip()
        expires_in = int(data.get("expires_in") or 3600)
        if not token:
            raise SpotifyAuthError(response.status_code, "missing_access_token")
        self._token = token
        self._token_expires_at = time.time() + max(60, expires_in - 60)
        return token

    async def get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            return await self._fetch_token()

    def clear_token(self) -> None:
        self._token = ""
        self._token_expires_at = 0.0

    @property
    def cache_key(self) -> tuple:
        return (self.client_id, self.client_secret, self.market, self.timeout_seconds)

    async def _get_json_with_status(self, path: str, params: Optional[Dict[str, Any]] = None, retry_auth: bool = True) -> Tuple[Dict[str, Any], int]:
        token = await self.get_token()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    SPOTIFY_API_BASE_URL + path,
                    params=params or {},
                    headers={"Authorization": "Bearer {0}".format(token)},
                )
        except httpx.TimeoutException as exc:
            raise SpotifyTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise SpotifyApiError(0) from exc

        if response.status_code == 401 and retry_auth:
            self.clear_token()
            return await self._get_json_with_status(path, params=params, retry_auth=False)
        if response.status_code == 401:
            raise SpotifyAuthError(401)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after_value = int(retry_after) if retry_after is not None else None
            except ValueError:
                retry_after_value = None
            raise SpotifyRateLimitedError(retry_after_value)
        if response.status_code == 404:
            raise SpotifyNotFoundError()
        if response.status_code < 200 or response.status_code >= 300:
            raise SpotifyApiError(response.status_code)
        try:
            return response.json(), response.status_code
        except ValueError as exc:
            raise SpotifyJsonError() from exc

    async def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None, retry_auth: bool = True) -> Dict[str, Any]:
        data, _status = await self._get_json_with_status(path, params=params, retry_auth=retry_auth)
        return data

    async def _get_next_page_with_status(self, next_url: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], int]:
        parsed = urlparse(str(next_url or ""))
        if parsed.scheme and parsed.netloc:
            if parsed.netloc != "api.spotify.com":
                raise SpotifyApiError(400)
            path = parsed.path.replace("/v1", "", 1)
            next_params = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
            next_params.update(params or {})
            return await self._get_json_with_status(path, next_params)
        return await self._get_json_with_status(str(next_url).replace(SPOTIFY_API_BASE_URL, ""), params or {})

    async def _get_next_page(self, next_url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        parsed = urlparse(str(next_url or ""))
        if parsed.scheme and parsed.netloc:
            if parsed.netloc != "api.spotify.com":
                raise SpotifyApiError(400)
            path = parsed.path.replace("/v1", "", 1)
            next_params = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
            next_params.update(params or {})
            return await self._get_json(path, next_params)
        return await self._get_json(str(next_url).replace(SPOTIFY_API_BASE_URL, ""), params or {})

    async def get_track(self, track_id: str) -> SpotifyTrackMetadata:
        data = await self._get_json("/tracks/{0}".format(track_id), {"market": self.market})
        track = _track_from_payload(data)
        if track is None:
            raise SpotifyNotFoundError()
        return track

    async def get_album(self, album_id: str) -> SpotifyAlbumMetadata:
        album = await self._get_json("/albums/{0}".format(album_id), {"market": self.market})
        album_name = str(album.get("name") or "").strip()
        album_artists = _artist_names(album.get("artists"))
        album_url = _external_url(album)
        tracks_payload = album.get("tracks") or {}
        tracks: List[SpotifyTrackMetadata] = []
        skipped = 0
        limit = max_album_tracks()

        def _append_items(items: Any) -> None:
            nonlocal skipped
            for item in items or []:
                if len(tracks) >= limit:
                    return
                track = _track_from_payload(item or {}, album_name=album_name)
                if track is None:
                    skipped += 1
                    continue
                tracks.append(track)

        _append_items(tracks_payload.get("items") or [])
        next_url = tracks_payload.get("next")
        while next_url and len(tracks) < limit:
            data = await self._get_next_page(str(next_url), {"market": self.market})
            _append_items(data.get("items") or [])
            next_url = data.get("next")

        total = int(tracks_payload.get("total") or len(tracks))
        truncated = total > len(tracks) + skipped or len(tracks) >= limit and total > limit
        return SpotifyAlbumMetadata(
            album_id=album_id,
            name=album_name,
            artists=album_artists,
            spotify_url=album_url,
            tracks=tracks,
            skipped_tracks=skipped,
            truncated=truncated,
        )

    async def get_playlist(self, playlist_id: str) -> SpotifyPlaylistMetadata:
        stats = SpotifyPlaylistFetchStats(playlist_id=playlist_id, market=self.market)
        try:
            playlist, _metadata_status = await self._get_json_with_status(
                "/playlists/{0}".format(playlist_id),
                {"market": self.market},
            )
        except SpotifyError as exc:
            _log_spotify_playlist_fetch(stats, getattr(exc, "status_code", 0))
            raise
        playlist_name = str(playlist.get("name") or "").strip()
        playlist_url = _external_url(playlist)
        image_url = _image_url(playlist)
        tracks_payload = playlist.get("tracks") or {}
        tracks: List[SpotifyTrackMetadata] = []

        def _append_items(items: Any) -> None:
            for item in items or []:
                stats.items_count += 1
                track = _track_from_playlist_entry(item or {}, stats)
                if track is None:
                    continue
                tracks.append(track)

        seen_next = set()
        item_params = {
            "market": self.market,
            "limit": DEFAULT_PLAYLIST_ITEMS_PAGE_LIMIT,
            "additional_types": "track,episode",
        }
        item_error: Optional[SpotifyError] = None
        try:
            items_page, items_status = await self._get_json_with_status(
                "/playlists/{0}/items".format(playlist_id),
                item_params,
            )
            stats.page = 1
            _append_items(items_page.get("items") or [])
            _log_spotify_playlist_fetch(stats, items_status)
            next_url = items_page.get("next")
            while next_url:
                next_text = str(next_url)
                if next_text in seen_next:
                    raise SpotifyApiError(508)
                seen_next.add(next_text)
                data, status = await self._get_next_page_with_status(next_text, {"market": self.market})
                stats.page += 1
                _append_items(data.get("items") or [])
                _log_spotify_playlist_fetch(stats, status)
                next_url = data.get("next")
        except SpotifyError as exc:
            _log_spotify_playlist_fetch(stats, getattr(exc, "status_code", 0))
            item_error = exc

        if item_error is not None:
            embedded_items = tracks_payload.get("items") or []
            if not embedded_items:
                raise item_error
            stats.page = max(stats.page, 1)
            _append_items(embedded_items)
            _log_spotify_playlist_fetch(stats, _metadata_status)
            next_url = tracks_payload.get("next")
            while next_url:
                next_text = str(next_url)
                if next_text in seen_next:
                    raise SpotifyApiError(508)
                seen_next.add(next_text)
                data, status = await self._get_next_page_with_status(next_text, {"market": self.market})
                stats.page += 1
                _append_items(data.get("items") or [])
                _log_spotify_playlist_fetch(stats, status)
                next_url = data.get("next")

        total = int(tracks_payload.get("total") or len(tracks) + stats.skipped_count)
        return SpotifyPlaylistMetadata(
            playlist_id=playlist_id,
            name=playlist_name,
            spotify_url=playlist_url,
            image_url=image_url,
            tracks=tracks,
            skipped_tracks=stats.skipped_count,
            total_tracks=total,
            item_field_tracks=stats.item_field_count,
            track_field_tracks=stats.track_field_count,
            local_tracks=stats.local_count,
            episode_tracks=stats.episode_count,
            missing_metadata_tracks=stats.missing_metadata_count,
        )


def get_spotify_client() -> SpotifyClient:
    global _SHARED_SPOTIFY_CLIENT
    candidate = SpotifyClient()
    if _SHARED_SPOTIFY_CLIENT is None or _SHARED_SPOTIFY_CLIENT.cache_key != candidate.cache_key:
        _SHARED_SPOTIFY_CLIENT = candidate
    return _SHARED_SPOTIFY_CLIENT


def reset_spotify_client_cache() -> None:
    global _SHARED_SPOTIFY_CLIENT
    _SHARED_SPOTIFY_CLIENT = None
