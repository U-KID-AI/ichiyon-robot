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


DEFAULT_ALBUM_URL = "https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


async def run(url: str) -> int:
    results = []
    parsed = parse_spotify_link(url)
    results.append(check("album url parses", parsed is not None and parsed.kind == "album", str(parsed)))
    if parsed is None or parsed.kind != "album":
        return 1
    started = time.perf_counter()
    album = await get_spotify_public_resolver().get_album(parsed.spotify_id)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    lazy_tracks = [
        spotify_track_to_lazy_music_track(
            track,
            "check-requester",
            parsed.original_url,
            playlist_index=index,
            source_type="spotify_album",
            collection_id=album.album_id,
            collection_name=album.name,
        )
        for index, track in enumerate(album.tracks, start=1)
    ]
    results.append(check("album name exists", bool(album.name)))
    results.append(check("album artists exist", bool(album.artists)))
    results.append(check("album tracks exist", len(album.tracks) > 0, str(len(album.tracks))))
    results.append(check("album track metadata exists", all(track.name and track.artists and track.duration_seconds is not None for track in album.tracks)))
    results.append(check("album order is stable", [track.spotify_playlist_index for track in lazy_tracks] == list(range(1, len(lazy_tracks) + 1))))
    results.append(check("album lazy conversion avoids youtube pre-resolve", all(not track.stream_url and not track.source_url for track in lazy_tracks)))
    print("spotify_public_album_summary provider=public_embed album_id={0} track_count={1} elapsed_ms={2}".format(album.album_id, len(album.tracks), elapsed_ms))
    return 0 if all(results) else 1


def parse_args():
    parser = argparse.ArgumentParser(description="Check public Spotify album metadata resolution without Web API.")
    parser.add_argument("--url", default=DEFAULT_ALBUM_URL)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(asyncio.run(run(args.url)))
