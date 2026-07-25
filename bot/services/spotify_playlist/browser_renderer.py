import asyncio
import os
import time
from typing import Callable, Optional

try:
    import resource
except ImportError:  # pragma: no cover - Windows local checks.
    resource = None

from bot.services.spotify_client import SpotifyPlaylistMetadata
from bot.services.spotify_playlist.errors import SpotifyPlaylistProviderUnavailable


BROWSER_PROVIDER = "browser_renderer"
SPOTIFY_PLAYLIST_BROWSER_ENABLED_ENV = "SPOTIFY_PLAYLIST_BROWSER_ENABLED"
SPOTIFY_PLAYLIST_BROWSER_TIMEOUT_SECONDS_ENV = "SPOTIFY_PLAYLIST_BROWSER_TIMEOUT_SECONDS"
_BROWSER_SEMAPHORE = asyncio.Semaphore(1)


def browser_enabled() -> bool:
    return str(os.getenv(SPOTIFY_PLAYLIST_BROWSER_ENABLED_ENV, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def browser_timeout_seconds() -> int:
    raw = str(os.getenv(SPOTIFY_PLAYLIST_BROWSER_TIMEOUT_SECONDS_ENV, "30") or "30").strip()
    try:
        return max(5, min(120, int(raw)))
    except ValueError:
        return 30


def memory_snapshot_mb() -> int:
    if resource is None:
        return 0
    try:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except Exception:
        return 0


async def fetch_with_browser_renderer(
    playlist_id: str,
    renderer: Optional[Callable[[str], SpotifyPlaylistMetadata]] = None,
) -> SpotifyPlaylistMetadata:
    if not browser_enabled() and renderer is None:
        raise SpotifyPlaylistProviderUnavailable("browser renderer disabled")
    async with _BROWSER_SEMAPHORE:
        started = time.perf_counter()
        try:
            if renderer is not None:
                return await asyncio.wait_for(asyncio.to_thread(renderer, playlist_id), timeout=browser_timeout_seconds())
            raise SpotifyPlaylistProviderUnavailable("playwright renderer is not installed")
        finally:
            _ = int((time.perf_counter() - started) * 1000)
