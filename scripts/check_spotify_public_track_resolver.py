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
from bot.services.spotify_resolver import build_search_queries
from bot.services.voice_music import spotify_track_to_lazy_music_track


DEFAULT_TRACK_URLS = [
    "https://open.spotify.com/track/4oqUgyBXtbbJkeppHt8t2D",
    "https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV",
    "https://open.spotify.com/track/2lVfBpBhHN7YaZrUpzyaZd",
]


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


async def run(urls) -> int:
    results = []
    resolver = get_spotify_public_resolver()
    for index, url in enumerate(urls, start=1):
        parsed = parse_spotify_link(url)
        results.append(check("track url parses {0}".format(index), parsed is not None and parsed.kind == "track", str(parsed)))
        if parsed is None or parsed.kind != "track":
            continue
        started = time.perf_counter()
        track = await resolver.get_track(parsed.spotify_id)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        queries = build_search_queries(track)
        lazy = spotify_track_to_lazy_music_track(track, "check-requester", parsed.original_url)
        results.append(check("track name exists {0}".format(index), bool(track.name)))
        results.append(check("track artists exist {0}".format(index), bool(track.artists)))
        results.append(check("track album exists {0}".format(index), bool(track.album_name)))
        results.append(check("track duration exists {0}".format(index), track.duration_seconds is not None, str(track.duration_seconds)))
        results.append(check("track id preserved {0}".format(index), track.track_id == parsed.spotify_id, track.track_id))
        results.append(check("search queries generated {0}".format(index), bool(queries), str(len(queries))))
        results.append(check("lazy conversion avoids youtube pre-resolve {0}".format(index), not lazy.stream_url and not lazy.source_url))
        print("spotify_public_track_summary index={0} provider=public_embed track_id={1} elapsed_ms={2} query_count={3}".format(index, parsed.spotify_id, elapsed_ms, len(queries)))
    return 0 if all(results) else 1


def parse_args():
    parser = argparse.ArgumentParser(description="Check public Spotify track metadata resolution without Web API.")
    parser.add_argument("--url", action="append", dest="urls")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(asyncio.run(run(args.urls or DEFAULT_TRACK_URLS)))
