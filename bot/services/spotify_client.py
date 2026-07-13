import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
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


class SpotifyError(Exception):
    user_message = "Spotify情報の取得に失敗しました。"


class SpotifyCredentialsMissing(SpotifyError):
    user_message = "Spotifyリンク対応が設定されていません。管理者へ連絡してください。"


class SpotifyAuthError(SpotifyError):
    user_message = "Spotify認証に失敗しました。管理者へ連絡してください。"


class SpotifyNotFoundError(SpotifyError):
    user_message = "Spotifyの曲またはアルバムが見つかりませんでした。"


class SpotifyRateLimitedError(SpotifyError):
    def __init__(self, retry_after: Optional[int] = None):
        super().__init__("Spotify API rate limited")
        self.retry_after = retry_after
        self.user_message = "Spotify APIの制限に達しました。少し時間を置いて再試行してください。"


class SpotifyApiError(SpotifyError):
    def __init__(self, status_code: int):
        super().__init__("Spotify API failed: status={0}".format(status_code))
        self.status_code = status_code
        if status_code == 403:
            self.user_message = "Spotify APIの権限またはアクセス制限により取得できませんでした。"
        elif status_code == 404:
            self.user_message = SpotifyNotFoundError.user_message


class SpotifyTimeoutError(SpotifyError):
    user_message = "Spotify APIがタイムアウトしました。時間を置いて再試行してください。"


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


def _track_from_payload(payload: Dict[str, Any], album_name: str = "") -> Optional[SpotifyTrackMetadata]:
    if not payload or payload.get("is_local"):
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
            raise SpotifyAuthError()
        data = response.json()
        token = str(data.get("access_token") or "").strip()
        expires_in = int(data.get("expires_in") or 3600)
        if not token:
            raise SpotifyAuthError()
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

    async def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None, retry_auth: bool = True) -> Dict[str, Any]:
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
            return await self._get_json(path, params=params, retry_auth=False)
        if response.status_code == 401:
            raise SpotifyAuthError()
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
        return response.json()

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
            parsed = urlparse(str(next_url))
            if parsed.scheme and parsed.netloc:
                path = parsed.path.replace("/v1", "", 1)
                params = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
                params["market"] = self.market
            else:
                path = str(next_url).replace(SPOTIFY_API_BASE_URL, "")
                params = {"market": self.market}
            data = await self._get_json(path, params)
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
