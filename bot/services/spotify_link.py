from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


SUPPORTED_SPOTIFY_KINDS = {"track", "album"}
KNOWN_SPOTIFY_KINDS = {"track", "album", "playlist", "episode", "show", "artist"}
SPOTIFY_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
SPOTIFY_ID_LENGTH = 22


@dataclass(frozen=True)
class SpotifyLink:
    kind: str
    spotify_id: str
    original_url: str

    @property
    def is_supported(self) -> bool:
        return self.kind in SUPPORTED_SPOTIFY_KINDS


def _valid_spotify_id(value: str) -> bool:
    return len(value) == SPOTIFY_ID_LENGTH and all(char in SPOTIFY_ID_CHARS for char in value)


def parse_spotify_link(value: str) -> Optional[SpotifyLink]:
    text = str(value or "").strip()
    if not text:
        return None

    if text.lower().startswith("spotify:"):
        parts = text.split(":")
        if len(parts) != 3:
            return SpotifyLink("unsupported", "", text)
        _, kind, spotify_id = parts
        kind = kind.lower().strip()
        spotify_id = spotify_id.strip()
        if kind not in KNOWN_SPOTIFY_KINDS:
            return SpotifyLink("unsupported", spotify_id, text)
        if not _valid_spotify_id(spotify_id):
            return SpotifyLink("invalid", spotify_id, text)
        return SpotifyLink(kind, spotify_id, text)

    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    host = parsed.netloc.lower()
    if host != "open.spotify.com":
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        return SpotifyLink("unsupported", "", text)

    if path_parts[0].lower().startswith("intl-"):
        path_parts = path_parts[1:]

    if len(path_parts) < 2:
        return SpotifyLink("unsupported", "", text)

    kind = path_parts[0].lower()
    spotify_id = path_parts[1].strip()
    if kind not in KNOWN_SPOTIFY_KINDS:
        return SpotifyLink("unsupported", spotify_id, text)
    if not _valid_spotify_id(spotify_id):
        return SpotifyLink("invalid", spotify_id, text)
    return SpotifyLink(kind, spotify_id, text)

