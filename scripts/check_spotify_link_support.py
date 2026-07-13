import asyncio
import os
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services.spotify_client import (
    SpotifyAlbumMetadata,
    SpotifyClient,
    SpotifyCredentialsMissing,
    SpotifyRateLimitedError,
    SpotifyTrackMetadata,
    max_album_tracks,
)
from bot.services.spotify_link import parse_spotify_link
from bot.services.spotify_resolver import (
    ResolvedYouTubeTrack,
    SpotifyLowScoreError,
    YouTubeCandidate,
    build_search_queries,
    get_album_lock,
    resolve_cache_ttl_seconds,
    resolve_concurrency,
    resolve_spotify_track_to_youtube,
    score_candidate,
    select_best_candidate,
)
import bot.services.spotify_resolver as spotify_resolver
import bot.services.voice_music as voice_music
from bot.services.voice_music import MusicTrack, parse_music_command, resolve_spotify_track_to_music_track, spotify_unsupported_message


TRACK_ID = "1Q2W3E4R5T6Y7U8I9O0P1A"
ALBUM_ID = "2Q2W3E4R5T6Y7U8I9O0P1B"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def sample_track(track_id: str = TRACK_ID, name: str = "熱帯夜") -> SpotifyTrackMetadata:
    return SpotifyTrackMetadata(
        track_id=track_id,
        name=name,
        artists=["RIP SLYME"],
        album_name="熱帯夜",
        duration_ms=240000,
        isrc="JPXXX0000001",
        explicit=False,
        spotify_url="https://open.spotify.com/track/{0}".format(track_id),
        disc_number=1,
        track_number=1,
    )


class FakeResponse:
    def __init__(self, status_code, data=None, headers=None):
        self.status_code = status_code
        self._data = data or {}
        self.headers = headers or {}

    def json(self):
        return self._data


class FakeAsyncClient:
    token_calls = 0
    get_calls = 0
    next_get_status = []

    def __init__(self, timeout=10.0):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, data=None, auth=None, headers=None):
        FakeAsyncClient.token_calls += 1
        return FakeResponse(200, {"access_token": "fake-token", "expires_in": 3600})

    async def get(self, url, params=None, headers=None):
        FakeAsyncClient.get_calls += 1
        if FakeAsyncClient.next_get_status:
            status = FakeAsyncClient.next_get_status.pop(0)
            if status == 429:
                return FakeResponse(429, {}, {"Retry-After": "7"})
            if status == 401:
                return FakeResponse(401, {})
        return FakeResponse(
            200,
            {
                "id": TRACK_ID,
                "name": "熱帯夜",
                "artists": [{"name": "RIP SLYME"}],
                "album": {"name": "熱帯夜"},
                "duration_ms": 240000,
                "external_ids": {"isrc": "JPXXX0000001"},
                "explicit": False,
                "external_urls": {"spotify": "https://open.spotify.com/track/{0}".format(TRACK_ID)},
            },
        )


def run_url_checks(results):
    cases = {
        "https://open.spotify.com/track/{0}".format(TRACK_ID): "track",
        "https://open.spotify.com/album/{0}".format(ALBUM_ID): "album",
        "https://open.spotify.com/intl-ja/track/{0}?si=abc".format(TRACK_ID): "track",
        "spotify:track:{0}".format(TRACK_ID): "track",
        "spotify:album:{0}".format(ALBUM_ID): "album",
        "https://open.spotify.com/playlist/{0}?si=abc".format(TRACK_ID): "playlist",
        "https://open.spotify.com/episode/{0}".format(TRACK_ID): "episode",
    }
    for value, expected in cases.items():
        parsed = parse_spotify_link(value)
        results.append(check("spotify parse {0}".format(expected), parsed is not None and parsed.kind == expected, str(parsed)))
    results.append(check("invalid spotify id is rejected", parse_spotify_link("https://open.spotify.com/track/short").kind == "invalid"))
    results.append(check("similar domain is ignored", parse_spotify_link("https://open.spotify.example.com/track/{0}".format(TRACK_ID)) is None))
    playlist = parse_spotify_link("https://open.spotify.com/playlist/{0}".format(TRACK_ID))
    results.append(check("playlist has helpful unsupported message", "プレイリスト" in spotify_unsupported_message(playlist)))
    results.append(check("music command accepts spotify url position", parse_music_command("歌え https://open.spotify.com/track/{0}".format(TRACK_ID))[0] == "music_play"))


def run_scoring_checks(results):
    track = sample_track()
    official = YouTubeCandidate("RIP SLYME - 熱帯夜 Official Audio", "https://youtube.example/1", 241, "RIP SLYME - Topic")
    cover = YouTubeCandidate("熱帯夜 cover karaoke", "https://youtube.example/2", 240, "someone")
    short = YouTubeCandidate("熱帯夜 shorts", "https://youtube.example/3", 20, "shorts")
    results.append(check("search query includes official audio", "official audio" in build_search_queries(track)[0].lower()))
    results.append(check("official candidate scores higher than cover", score_candidate(track, official) > score_candidate(track, cover), str((score_candidate(track, official), score_candidate(track, cover)))))
    results.append(check("short candidate is penalized", score_candidate(track, short) < score_candidate(track, official), str((score_candidate(track, short), score_candidate(track, official)))))
    best, score = select_best_candidate(track, [cover, official])
    results.append(check("best youtube candidate is selected", best.webpage_url == official.webpage_url and score >= 55, str(score)))
    try:
        select_best_candidate(track, [YouTubeCandidate("unrelated tutorial", "https://youtube.example/4", 20, "uploader")])
        low_score_failed = False
    except SpotifyLowScoreError:
        low_score_failed = True
    results.append(check("low score candidate is rejected", low_score_failed))


async def run_client_checks(results):
    import bot.services.spotify_client as spotify_client

    original_async_client = spotify_client.httpx.AsyncClient
    original_id = os.environ.get("SPOTIFY_CLIENT_ID")
    original_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    try:
        os.environ.pop("SPOTIFY_CLIENT_ID", None)
        os.environ.pop("SPOTIFY_CLIENT_SECRET", None)
        missing = False
        try:
            await SpotifyClient().get_token()
        except SpotifyCredentialsMissing:
            missing = True
        results.append(check("missing spotify credentials does not crash import", missing))

        os.environ["SPOTIFY_CLIENT_ID"] = "client-id"
        os.environ["SPOTIFY_CLIENT_SECRET"] = "client-secret"
        spotify_client.httpx.AsyncClient = FakeAsyncClient
        FakeAsyncClient.token_calls = 0
        FakeAsyncClient.get_calls = 0
        FakeAsyncClient.next_get_status = []
        client = SpotifyClient()
        token1 = await client.get_token()
        token2 = await client.get_token()
        results.append(check("spotify token is cached", token1 == token2 and FakeAsyncClient.token_calls == 1, str(FakeAsyncClient.token_calls)))

        client._token_expires_at = time.time() - 1
        await client.get_token()
        results.append(check("spotify token refreshes before expiry", FakeAsyncClient.token_calls == 2, str(FakeAsyncClient.token_calls)))

        FakeAsyncClient.next_get_status = [401]
        await client.get_track(TRACK_ID)
        results.append(check("spotify api 401 retries once", FakeAsyncClient.token_calls == 3 and FakeAsyncClient.get_calls >= 2, str((FakeAsyncClient.token_calls, FakeAsyncClient.get_calls))))

        FakeAsyncClient.next_get_status = [429]
        rate_limited = False
        try:
            await client.get_track(TRACK_ID)
        except SpotifyRateLimitedError as exc:
            rate_limited = exc.retry_after == 7
        results.append(check("spotify api 429 exposes retry-after without retry loop", rate_limited))
    finally:
        spotify_client.httpx.AsyncClient = original_async_client
        if original_id is None:
            os.environ.pop("SPOTIFY_CLIENT_ID", None)
        else:
            os.environ["SPOTIFY_CLIENT_ID"] = original_id
        if original_secret is None:
            os.environ.pop("SPOTIFY_CLIENT_SECRET", None)
        else:
            os.environ["SPOTIFY_CLIENT_SECRET"] = original_secret


async def run_resolver_checks(results):
    original_search = spotify_resolver.search_youtube_candidates
    original_cache = dict(spotify_resolver._RESOLVE_CACHE)
    try:
        calls = {"count": 0}

        def fake_search(query, guild_id=None, limit=5):
            calls["count"] += 1
            return [YouTubeCandidate("RIP SLYME - 熱帯夜 Official Audio", "https://youtube.example/watch?v=ok", 240, "RIP SLYME - Topic")]

        spotify_resolver._RESOLVE_CACHE.clear()
        spotify_resolver.search_youtube_candidates = fake_search
        track = sample_track()
        resolved1 = await resolve_spotify_track_to_youtube(track, "guild-a")
        resolved2 = await resolve_spotify_track_to_youtube(track, "guild-a")
        results.append(check("spotify resolver stores youtube webpage url", resolved1.youtube_url == "https://youtube.example/watch?v=ok", str(resolved1)))
        results.append(check("spotify resolver uses memory cache", resolved1 == resolved2 and calls["count"] == 1, str(calls)))
    finally:
        spotify_resolver.search_youtube_candidates = original_search
        spotify_resolver._RESOLVE_CACHE.clear()
        spotify_resolver._RESOLVE_CACHE.update(original_cache)


async def run_album_and_queue_checks(results):
    original_resolve = voice_music.resolve_spotify_track_to_youtube
    original_extract = voice_music.extract_track_info
    try:
        async def fake_resolve(track, guild_id):
            return ResolvedYouTubeTrack(track.track_id, "https://youtube.example/{0}".format(track.track_id), "yt {0}".format(track.name), track.duration_seconds, 90, time.time())

        def fake_extract(url, requester_id, guild_id=None, use_cookies=True):
            return MusicTrack("YouTube title", url, "https://stream.example/audio", requester_id, 240, url)

        voice_music.resolve_spotify_track_to_youtube = fake_resolve
        voice_music.extract_track_info = fake_extract
        converted = await resolve_spotify_track_to_music_track(sample_track(), "requester", "guild-a", None, "spotify:track:{0}".format(TRACK_ID))
        results.append(check("spotify track converts to normal MusicTrack", isinstance(converted, MusicTrack) and converted.source_url.startswith("https://youtube.example/"), str(converted)))
        results.append(check("spotify metadata is retained on MusicTrack", converted.source_type == "spotify" and converted.original_spotify_url.startswith("spotify:track:")))

        album = SpotifyAlbumMetadata(
            album_id=ALBUM_ID,
            name="Album",
            artists=["Artist"],
            spotify_url="https://open.spotify.com/album/{0}".format(ALBUM_ID),
            tracks=[sample_track(TRACK_ID, "曲1"), sample_track("1Q2W3E4R5T6Y7U8I9O0P1C", "曲2")],
            skipped_tracks=1,
        )
        lock = get_album_lock("ichiyon:guild-a")
        results.append(check("album lock is guild scoped", lock is get_album_lock("ichiyon:guild-a") and lock is not get_album_lock("irsia:guild-a")))
        results.append(check("album metadata carries skipped tracks", album.skipped_tracks == 1 and len(album.tracks) == 2))

        class FakeAlbumClient(SpotifyClient):
            async def _get_json(self, path, params=None, retry_auth=True):
                if path.startswith("/albums/") and "/tracks" not in path:
                    return {
                        "id": ALBUM_ID,
                        "name": "Paged Album",
                        "artists": [{"name": "Album Artist"}],
                        "external_urls": {"spotify": "https://open.spotify.com/album/{0}".format(ALBUM_ID)},
                        "tracks": {
                            "total": 2,
                            "next": "https://api.spotify.com/v1/albums/{0}/tracks?offset=1&limit=1".format(ALBUM_ID),
                            "items": [
                                {
                                    "id": TRACK_ID,
                                    "name": "一曲目",
                                    "artists": [{"name": "Artist"}],
                                    "duration_ms": 100000,
                                    "external_urls": {"spotify": "https://open.spotify.com/track/{0}".format(TRACK_ID)},
                                }
                            ],
                        },
                    }
                return {
                    "next": None,
                    "items": [
                        {
                            "id": "1Q2W3E4R5T6Y7U8I9O0P1D",
                            "name": "二曲目",
                            "artists": [{"name": "Artist"}],
                            "duration_ms": 100000,
                            "external_urls": {"spotify": "https://open.spotify.com/track/1Q2W3E4R5T6Y7U8I9O0P1D"},
                        }
                    ],
                }

        paged_album = await FakeAlbumClient(client_id="id", client_secret="secret").get_album(ALBUM_ID)
        results.append(check("album pagination collects all tracks", len(paged_album.tracks) == 2, str([track.name for track in paged_album.tracks])))
    finally:
        voice_music.resolve_spotify_track_to_youtube = original_resolve
        voice_music.extract_track_info = original_extract


def run_env_checks(results):
    original_values = {key: os.environ.get(key) for key in ("SPOTIFY_MAX_ALBUM_TRACKS", "SPOTIFY_RESOLVE_CONCURRENCY", "SPOTIFY_RESOLVE_CACHE_TTL_SECONDS")}
    try:
        os.environ["SPOTIFY_MAX_ALBUM_TRACKS"] = "999"
        os.environ["SPOTIFY_RESOLVE_CONCURRENCY"] = "99"
        os.environ["SPOTIFY_RESOLVE_CACHE_TTL_SECONDS"] = "1"
        results.append(check("album max tracks is clamped", max_album_tracks() == 200, str(max_album_tracks())))
        results.append(check("resolve concurrency is clamped", resolve_concurrency() == 4, str(resolve_concurrency())))
        results.append(check("resolve cache ttl has safe minimum", resolve_cache_ttl_seconds() == 60, str(resolve_cache_ttl_seconds())))
    finally:
        for key, value in original_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def main_async() -> int:
    results = []
    run_url_checks(results)
    run_scoring_checks(results)
    await run_client_checks(results)
    await run_resolver_checks(results)
    await run_album_and_queue_checks(results)
    run_env_checks(results)

    env_text = (ROOT_DIR / ".env.example").read_text(encoding="utf-8")
    doc_text = (ROOT_DIR / "docs" / "voice-vc-commands.md").read_text(encoding="utf-8")
    results.append(check("env example documents spotify client id", "SPOTIFY_CLIENT_ID=" in env_text))
    results.append(check("env example keeps spotify secret empty", "SPOTIFY_CLIENT_SECRET=" in env_text))
    results.append(check("docs mention spotify does not directly play audio", "Spotify上の音源やプレビュー音源を直接再生することはありません" in doc_text))

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
