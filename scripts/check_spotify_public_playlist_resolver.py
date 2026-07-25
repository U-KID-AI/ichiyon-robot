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


DEFAULT_PLAYLIST_URL = "https://open.spotify.com/playlist/6wtgpQbVF1aJ4irWRKE0Rq"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


async def run(url: str, expected_count: int) -> int:
    results = []
    parsed = parse_spotify_link(url)
    results.append(check("playlist url parses", parsed is not None and parsed.kind == "playlist", str(parsed)))
    if parsed is None or parsed.kind != "playlist":
        return 1

    started = time.perf_counter()
    playlist = await get_spotify_public_resolver().get_playlist(parsed.spotify_id)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    results.append(check("playlist track count", len(playlist.tracks) == expected_count, str(len(playlist.tracks))))
    results.append(check("playlist order is stable", [track.track_number for track in playlist.tracks] == list(range(1, len(playlist.tracks) + 1)), str([track.track_number for track in playlist.tracks[:5]])))
    results.append(check("track names exist", all(track.name for track in playlist.tracks)))
    results.append(check("track artists exist", all(track.artists for track in playlist.tracks)))
    results.append(check("duration is available", all(track.duration_seconds is not None for track in playlist.tracks)))
    lazy_tracks = [
        spotify_track_to_lazy_music_track(track, "check-requester", parsed.original_url, playlist=playlist, playlist_index=index)
        for index, track in enumerate(playlist.tracks, start=1)
    ]
    results.append(check("queue conversion keeps all tracks", len(lazy_tracks) == expected_count, str(len(lazy_tracks))))
    results.append(check("queue conversion does not pre-resolve youtube", all(not track.stream_url and not track.source_url for track in lazy_tracks)))
    results.append(check("queue conversion keeps playlist order", [track.spotify_playlist_index for track in lazy_tracks] == list(range(1, len(lazy_tracks) + 1))))
    print(
        "spotify_public_playlist_summary provider={0} playlist_id={1} track_count={2} elapsed_ms={3}".format(
            playlist.source_provider,
            playlist.playlist_id,
            len(playlist.tracks),
            elapsed_ms,
        )
    )
    return 0 if all(results) else 1


def parse_args():
    parser = argparse.ArgumentParser(description="Check public Spotify playlist metadata resolution without Discord playback.")
    parser.add_argument("--url", default=DEFAULT_PLAYLIST_URL)
    parser.add_argument("--expected-count", type=int, default=17)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(asyncio.run(run(args.url, args.expected_count)))
