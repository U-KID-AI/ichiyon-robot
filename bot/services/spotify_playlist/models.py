from dataclasses import dataclass
from typing import List

from bot.services.spotify_client import SpotifyPlaylistMetadata, SpotifyTrackMetadata


@dataclass(frozen=True)
class SpotifyPlaylistProviderResult:
    playlist: SpotifyPlaylistMetadata
    provider: str
    elapsed_ms: int
    memory_before_mb: int = 0
    memory_after_mb: int = 0
    browser_used: bool = False


def build_public_playlist(
    playlist_id: str,
    name: str,
    spotify_url: str,
    image_url: str,
    tracks: List[SpotifyTrackMetadata],
    source_provider: str,
    total_tracks: int = 0,
) -> SpotifyPlaylistMetadata:
    return SpotifyPlaylistMetadata(
        playlist_id=playlist_id,
        name=name,
        spotify_url=spotify_url,
        image_url=image_url,
        tracks=tracks,
        skipped_tracks=max(0, int(total_tracks or len(tracks)) - len(tracks)),
        total_tracks=int(total_tracks or len(tracks)),
        item_field_tracks=len(tracks),
        source_provider=source_provider,
    )
