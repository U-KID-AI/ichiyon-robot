import argparse
import asyncio
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot.services.spotify_link import parse_spotify_link
from bot.services.spotify_public import get_spotify_public_resolver
from bot.services.voice_music import spotify_track_to_lazy_music_track


DEFAULT_ARTIST_URL = "https://open.spotify.com/artist/4tZwfgrHOc3mvqYlEYSvVi"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


async def run(url: str) -> int:
    results = []
    parsed = parse_spotify_link(url)
    results.append(check("artist url parses", parsed is not None and parsed.kind == "artist", str(parsed)))
    if parsed is None or parsed.kind != "artist":
        return 1
    started = time.perf_counter()
    artist = await get_spotify_public_resolver().get_artist(parsed.spotify_id)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    lazy_tracks = [
        spotify_track_to_lazy_music_track(
            track,
            "check-requester",
            parsed.original_url,
            playlist_index=index,
            source_type="spotify_artist",
            collection_id=artist.artist_id,
            collection_name=artist.name,
        )
        for index, track in enumerate(artist.tracks, start=1)
    ]
    results.append(check("artist name exists", bool(artist.name)))
    results.append(check("artist tracks exist", len(artist.tracks) > 0, str(len(artist.tracks))))
    results.append(check("artist track metadata exists", all(track.name and track.artists and track.duration_seconds is not None for track in artist.tracks)))
    results.append(check("artist order is stable", [track.spotify_playlist_index for track in lazy_tracks] == list(range(1, len(lazy_tracks) + 1))))
    results.append(check("artist lazy conversion avoids youtube pre-resolve", all(not track.stream_url and not track.source_url for track in lazy_tracks)))
    print("spotify_public_artist_summary provider=public_embed artist_id={0} track_count={1} elapsed_ms={2}".format(artist.artist_id, len(artist.tracks), elapsed_ms))
    return 0 if all(results) else 1


def parse_args():
    parser = argparse.ArgumentParser(description="Check public Spotify artist popular tracks without Web API.")
    parser.add_argument("--url", default=DEFAULT_ARTIST_URL)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(asyncio.run(run(args.url)))
