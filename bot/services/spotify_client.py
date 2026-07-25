import os
from dataclasses import dataclass
from typing import List, Optional


SPOTIFY_MAX_ALBUM_TRACKS_ENV = "SPOTIFY_MAX_ALBUM_TRACKS"
DEFAULT_MAX_ALBUM_TRACKS = 100


class SpotifyError(Exception):
    user_message = "Spotifyリンクの公開情報を取得できませんでした。"


class SpotifyNotFoundError(SpotifyError):
    user_message = "Spotifyの公開情報が見つかりませんでした。"


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
    image_url: str = ""

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
    source_provider: str = "public_embed"

    @property
    def total_duration_seconds(self) -> int:
        return sum(track.duration_seconds or 0 for track in self.tracks)


@dataclass(frozen=True)
class SpotifyArtistMetadata:
    artist_id: str
    name: str
    spotify_url: str
    image_url: str
    tracks: List[SpotifyTrackMetadata]
    source_provider: str = "public_embed"


def max_album_tracks() -> int:
    raw = str(os.getenv(SPOTIFY_MAX_ALBUM_TRACKS_ENV, str(DEFAULT_MAX_ALBUM_TRACKS)) or str(DEFAULT_MAX_ALBUM_TRACKS)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_MAX_ALBUM_TRACKS
    return max(1, min(200, value))
