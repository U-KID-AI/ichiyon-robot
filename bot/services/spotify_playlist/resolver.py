import time
from typing import Optional

from bot.services.spotify_client import (
    SpotifyApiError,
    SpotifyAuthError,
    SpotifyClient,
    SpotifyCredentialsMissing,
    SpotifyPlaylistMetadata,
    get_spotify_client,
)
import bot.services.spotify_playlist.browser_renderer as browser_renderer
from bot.services.spotify_playlist.errors import SpotifyPlaylistResolveError
from bot.services.spotify_playlist.official_api import fetch_official_playlist
from bot.services.spotify_playlist.public_embed import fetch_public_embed_playlist


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def should_try_public_playlist_fallback(error: Exception) -> bool:
    if isinstance(error, SpotifyCredentialsMissing):
        return True
    if isinstance(error, SpotifyAuthError):
        return getattr(error, "status_code", 0) in (0, 401, 403)
    if isinstance(error, SpotifyApiError):
        return getattr(error, "status_code", 0) in (401, 403)
    return False


def _log_resolved(provider: str, playlist: SpotifyPlaylistMetadata, elapsed_ms: int) -> None:
    print(
        "[INFO] spotify_playlist_resolved provider={0} playlist_id={1} track_count={2} elapsed_ms={3}".format(
            provider,
            playlist.playlist_id,
            len(playlist.tracks),
            elapsed_ms,
        )
    )


class SpotifyPlaylistResolver:
    def __init__(self, client: Optional[SpotifyClient] = None):
        self.client = client or get_spotify_client()

    async def resolve(self, playlist_id: str) -> SpotifyPlaylistMetadata:
        started = time.perf_counter()
        fallback_error: Optional[Exception] = None
        try:
            playlist = await fetch_official_playlist(self.client, playlist_id)
            _log_resolved("official_api", playlist, _elapsed_ms(started))
            return playlist
        except Exception as exc:
            if not should_try_public_playlist_fallback(exc):
                raise
            fallback_error = exc

        for provider_name, fetcher in (
            ("public_embed", fetch_public_embed_playlist),
            ("browser_renderer", browser_renderer.fetch_with_browser_renderer),
        ):
            try:
                playlist = await fetcher(playlist_id)
            except Exception as exc:
                fallback_error = exc
                continue
            if playlist.tracks:
                _log_resolved(provider_name, playlist, _elapsed_ms(started))
                return playlist

        if fallback_error is not None:
            raise SpotifyPlaylistResolveError() from fallback_error
        raise SpotifyPlaylistResolveError()


def get_spotify_playlist_resolver(client: Optional[SpotifyClient] = None) -> SpotifyPlaylistResolver:
    return SpotifyPlaylistResolver(client)
