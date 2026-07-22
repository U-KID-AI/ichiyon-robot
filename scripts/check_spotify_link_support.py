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
    get_spotify_client,
    max_album_tracks,
    reset_spotify_client_cache,
)
from bot.services.spotify_link import parse_spotify_link
from bot.services.spotify_resolver import (
    ResolvedYouTubeTrack,
    SpotifyLowScoreError,
    YouTubeCandidate,
    build_search_queries,
    clear_resolve_cache,
    deduplicate_candidates,
    get_album_lock,
    invalidate_resolve_cache,
    match_min_margin,
    resolve_cache_ttl_seconds,
    resolve_cache_max_entries,
    resolve_concurrency,
    resolve_spotify_track_to_youtube,
    score_candidate,
    select_best_candidate,
    youtube_candidates_per_query,
)
import bot.services.spotify_resolver as spotify_resolver
import bot.services.voice_music as voice_music
from bot.services.voice_music import (
    MusicTrack,
    parse_music_command,
    resolve_spotify_album_tracks,
    resolve_spotify_track_to_music_track,
    should_retry_spotify_resolution,
    spotify_unsupported_message,
)


TRACK_ID = "1Q2W3E4R5T6Y7U8I9O0P1A"
ALBUM_ID = "2Q2W3E4R5T6Y7U8I9O0P1B"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def sample_track(track_id: str = TRACK_ID, name: str = "熱帯夜", artists=None) -> SpotifyTrackMetadata:
    return SpotifyTrackMetadata(
        track_id=track_id,
        name=name,
        artists=artists or ["RIP SLYME"],
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
        "https://open.spotify.com/track/2KD6Qx09NNMsv1HQOh8zVv?si=U5S8MYi-TzyVqOds8p7dUQ&utm_source=copy-link&rowId=d5e11eed648993c4": "track",
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
    topic = YouTubeCandidate("熱帯夜", "https://youtube.example/topic", 240, "RIP SLYME - Topic")
    vevo = YouTubeCandidate("RIP SLYME - 熱帯夜", "https://youtube.example/vevo", 240, "RIPSLYMEVEVO")
    different_artist = YouTubeCandidate("熱帯夜 Official Audio", "https://youtube.example/other", 240, "Other Artist - Topic")
    cover = YouTubeCandidate("RIP SLYME - 熱帯夜 cover karaoke", "https://youtube.example/2", 240, "someone")
    karaoke = YouTubeCandidate("RIP SLYME - 熱帯夜 karaoke", "https://youtube.example/karaoke", 240, "karaoke channel")
    instrumental = YouTubeCandidate("RIP SLYME - 熱帯夜 instrumental", "https://youtube.example/instrumental", 240, "instrumental channel")
    short = YouTubeCandidate("熱帯夜 shorts", "https://youtube.example/3", 20, "shorts")
    results.append(check("search query includes official audio", any("official audio" in query.lower() for query in build_search_queries(track)), str(build_search_queries(track))))
    multi_artist_track = sample_track(name="Beneath the Mask -rain-", artists=["Lyn", "ATLUS GAME MUSIC"])
    multi_queries = build_search_queries(multi_artist_track)
    results.append(check("search queries include secondary artists", any("ATLUS GAME MUSIC" in query for query in multi_queries), str(multi_queries)))
    results.append(check("search queries include title only fallback", any(query == "Beneath the Mask -rain-" for query in multi_queries), str(multi_queries)))
    results.append(check("official candidate scores higher than cover", score_candidate(track, official) > score_candidate(track, cover), str((score_candidate(track, official), score_candidate(track, cover)))))
    results.append(check("topic candidate can be selected", score_candidate(track, topic) >= 70, str(score_candidate(track, topic))))
    results.append(check("vevo candidate can be selected", score_candidate(track, vevo) >= 70, str(score_candidate(track, vevo))))
    results.append(check("same title by different artist is rejected", score_candidate(track, different_artist) < 70, str(score_candidate(track, different_artist))))
    results.append(check("cover candidate is rejected", score_candidate(track, cover) < 70, str(score_candidate(track, cover))))
    results.append(check("karaoke candidate is rejected", score_candidate(track, karaoke) < 70, str(score_candidate(track, karaoke))))
    results.append(check("instrumental candidate is rejected", score_candidate(track, instrumental) < 70, str(score_candidate(track, instrumental))))
    results.append(check("short candidate is penalized", score_candidate(track, short) < score_candidate(track, official), str((score_candidate(track, short), score_candidate(track, official)))))
    best, score = select_best_candidate(track, [cover, official])
    results.append(check("best youtube candidate is selected", best.webpage_url == official.webpage_url and score >= 70, str(score)))
    try:
        select_best_candidate(track, [YouTubeCandidate("unrelated tutorial", "https://youtube.example/4", 20, "uploader")])
        low_score_failed = False
    except SpotifyLowScoreError:
        low_score_failed = True
    results.append(check("low score candidate is rejected", low_score_failed))
    live_track = sample_track(name="熱帯夜 Live")
    live_candidate = YouTubeCandidate("RIP SLYME - 熱帯夜 Live", "https://youtube.example/live", 240, "RIP SLYME")
    normal_for_live = YouTubeCandidate("RIP SLYME - 熱帯夜 Official Audio", "https://youtube.example/original", 240, "RIP SLYME - Topic")
    results.append(check("live spotify track accepts live candidate", score_candidate(live_track, live_candidate) >= 70, str(score_candidate(live_track, live_candidate))))
    results.append(check("live spotify track rejects original candidate", score_candidate(live_track, normal_for_live) < 70, str(score_candidate(live_track, normal_for_live))))
    remix_track = sample_track(name="熱帯夜 Remix")
    remix_candidate = YouTubeCandidate("RIP SLYME - 熱帯夜 Remix", "https://youtube.example/remix", 240, "RIP SLYME")
    original_for_remix = YouTubeCandidate("RIP SLYME - 熱帯夜 Official Audio", "https://youtube.example/original2", 240, "RIP SLYME - Topic")
    results.append(check("remix spotify track accepts remix candidate", score_candidate(remix_track, remix_candidate) >= 70, str(score_candidate(remix_track, remix_candidate))))
    results.append(check("remix spotify track rejects original candidate", score_candidate(remix_track, original_for_remix) < 70, str(score_candidate(remix_track, original_for_remix))))
    japanese_artist_track = sample_track(name="星空", artists=["山田太郎"])
    japanese_candidate = YouTubeCandidate("山田太郎 - 星空 Official Audio", "https://youtube.example/jp", 180, "山田太郎 - Topic")
    results.append(check("japanese title and artist are scored", score_candidate(japanese_artist_track, japanese_candidate) >= 70, str(score_candidate(japanese_artist_track, japanese_candidate))))
    ambiguous_a = YouTubeCandidate("RIP SLYME - 熱帯夜 Official Audio", "https://youtube.example/a", 240, "RIP SLYME")
    ambiguous_b = YouTubeCandidate("RIP SLYME - 熱帯夜 Official Video", "https://youtube.example/b", 241, "RIP SLYME")
    try:
        ambiguous_best, ambiguous_score = select_best_candidate(track, [ambiguous_a, ambiguous_b])
        margin_accepted = ambiguous_best.webpage_url in {ambiguous_a.webpage_url, ambiguous_b.webpage_url} and ambiguous_score >= 70
    except SpotifyLowScoreError:
        margin_accepted = False
    results.append(check("small score margin allows same-song candidates", margin_accepted))

    provant = SpotifyTrackMetadata(
        track_id="PROVANTTRACK0000000001",
        name="PROVANT",
        artists=["SawanoHiroyuki[nZk]", "Jean-Ken Johnny", "TAKUMA"],
        album_name="PROVANT",
        duration_ms=171000,
        isrc="",
        explicit=False,
        spotify_url="https://open.spotify.com/track/PROVANTTRACK0000000001",
    )
    provant_official_collab = YouTubeCandidate(
        "SawanoHiroyuki[nZk]:Jean-Ken Johnny:TAKUMA - PROVANT",
        "https://youtube.example/provant-collab",
        171,
        "SawanoHiroyuki[nZk]",
    )
    provant_official_mv = YouTubeCandidate(
        "SawanoHiroyuki[nZk] - PROVANT feat. Jean-Ken Johnny & TAKUMA Official Music Video",
        "https://youtube.example/provant-mv",
        171,
        "SawanoHiroyuki[nZk] Official YouTube Channel",
    )
    provant_lyrics = YouTubeCandidate(
        "SawanoHiroyuki[nZk] - PROVANT Lyrics",
        "https://youtube.example/provant-lyrics",
        171,
        "Lyrics Channel",
    )
    provant_best, provant_score = select_best_candidate(
        provant,
        [provant_official_collab, provant_official_mv, provant_lyrics],
    )
    results.append(check("PROVANT equal-score candidates do not fail margin", provant_score >= 70, str(provant_score)))
    results.append(check("PROVANT official source is preferred", provant_best.webpage_url == provant_official_mv.webpage_url, provant_best.webpage_url))

    beneath = SpotifyTrackMetadata(
        track_id="BENEATHTHEMASK0000001",
        name="Beneath the Mask -rain-",
        artists=["Lyn"],
        album_name="Beneath the Mask -rain-",
        duration_ms=279000,
        isrc="",
        explicit=False,
        spotify_url="https://open.spotify.com/track/BENEATHTHEMASK0000001",
    )
    beneath_sung_cover = YouTubeCandidate(
        "Beneath the Mask -rain- - Lyn / REKA【歌ってみた】",
        "https://youtube.example/beneath-cover",
        279,
        "REKA",
    )
    beneath_chiptune = YouTubeCandidate(
        "Lyn - Beneath The Mask -rain- -chiptune-",
        "https://youtube.example/beneath-chiptune",
        279,
        "Chiptune Channel",
    )
    beneath_drum_cover = YouTubeCandidate(
        "Lyn - Beneath The Mask -rain- drum cover",
        "https://youtube.example/beneath-drum-cover",
        279,
        "Drum Cover Channel",
    )
    results.append(check("Japanese sung cover candidate is rejected", score_candidate(beneath, beneath_sung_cover) < 70, str(score_candidate(beneath, beneath_sung_cover))))
    results.append(check("chiptune candidate is rejected", score_candidate(beneath, beneath_chiptune) < 70, str(score_candidate(beneath, beneath_chiptune))))
    results.append(check("drum cover candidate is rejected", score_candidate(beneath, beneath_drum_cover) < 70, str(score_candidate(beneath, beneath_drum_cover))))
    try:
        select_best_candidate(beneath, [beneath_sung_cover, beneath_chiptune, beneath_drum_cover])
        beneath_failed = False
    except SpotifyLowScoreError:
        beneath_failed = True
    results.append(check("Beneath the Mask derived-only candidates fail safely", beneath_failed))
    beneath_soundtrack = SpotifyTrackMetadata(
        track_id="2KD6Qx09NNMsv1HQOh8zVv",
        name="Beneath the Mask -rain-",
        artists=["Lyn", "ATLUS GAME MUSIC"],
        album_name="PERSONA5 ORIGINAL SOUNDTRACK",
        duration_ms=279000,
        isrc="",
        explicit=False,
        spotify_url="https://open.spotify.com/track/2KD6Qx09NNMsv1HQOh8zVv",
    )
    soundtrack_candidate = YouTubeCandidate(
        "P5 - Beneath the Mask -rainy day- | Synchronized Lyrics",
        "https://youtube.example/beneath-rainy",
        272,
        "SkAlgorythmik",
    )
    results.append(check("soundtrack fallback accepts high-confidence title duration match", score_candidate(beneath_soundtrack, soundtrack_candidate) >= 70, str(score_candidate(beneath_soundtrack, soundtrack_candidate))))
    best_beneath, beneath_score = select_best_candidate(beneath_soundtrack, [beneath_sung_cover, beneath_chiptune, beneath_drum_cover, soundtrack_candidate])
    results.append(check("Beneath the Mask selects soundtrack fallback over derived candidates", best_beneath.webpage_url == soundtrack_candidate.webpage_url and beneath_score >= 70, str((best_beneath, beneath_score))))


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

        reset_spotify_client_cache()
        FakeAsyncClient.token_calls = 0
        FakeAsyncClient.get_calls = 0
        shared1 = get_spotify_client()
        shared2 = get_spotify_client()
        await shared1.get_track(TRACK_ID)
        await shared2.get_track(TRACK_ID)
        results.append(check("shared spotify client reuses token", shared1 is shared2 and FakeAsyncClient.token_calls == 1, str(FakeAsyncClient.token_calls)))

        FakeAsyncClient.next_get_status = [401]
        await shared1.get_track(TRACK_ID)
        results.append(check("shared spotify client refreshes token once after 401", FakeAsyncClient.token_calls == 2, str(FakeAsyncClient.token_calls)))
    finally:
        reset_spotify_client_cache()
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
    original_resolve = voice_music.resolve_spotify_track_to_youtube
    original_extract = voice_music.extract_track_info
    try:
        calls = {"count": 0}

        def fake_search(query, guild_id=None, limit=5):
            calls["count"] += 1
            return [YouTubeCandidate("RIP SLYME - 熱帯夜 Official Audio", "https://youtube.example/watch?v=ok", 240, "RIP SLYME - Topic")]

        spotify_resolver._RESOLVE_CACHE.clear()
        spotify_resolver.search_youtube_candidates = fake_search
        track = sample_track()
        resolved1 = await resolve_spotify_track_to_youtube(track, "guild-a")
        first_resolve_calls = calls["count"]
        resolved2 = await resolve_spotify_track_to_youtube(track, "guild-a")
        results.append(check("spotify resolver stores youtube webpage url", resolved1.youtube_url == "https://youtube.example/watch?v=ok", str(resolved1)))
        results.append(check("spotify resolver uses memory cache", resolved1 == resolved2 and calls["count"] == first_resolve_calls, str(calls)))

        resolved3 = await resolve_spotify_track_to_youtube(track, "guild-a", bypass_cache=True)
        results.append(check("spotify resolver can bypass cache", calls["count"] == first_resolve_calls * 2 and resolved3.youtube_url == resolved1.youtube_url, str(calls)))
        invalidate_resolve_cache(track.track_id)
        results.append(check("spotify resolver cache invalidates by track id", track.track_id not in spotify_resolver._RESOLVE_CACHE))

        clear_resolve_cache()
        now = time.time()
        for index in range(0, 105):
            item = ResolvedYouTubeTrack(str(index).zfill(22), "https://youtube.example/{0}".format(index), "title", 100, 90, now + index)
            spotify_resolver._store_resolved_track(item)
        original_max = os.environ.get("SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES")
        os.environ["SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES"] = "100"
        spotify_resolver.prune_resolve_cache(now + 200)
        results.append(check("spotify resolver cache max entries is enforced", len(spotify_resolver._RESOLVE_CACHE) <= 100, str(len(spotify_resolver._RESOLVE_CACHE))))
        if original_max is None:
            os.environ.pop("SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES", None)
        else:
            os.environ["SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES"] = original_max

        retry_calls = {"resolve": 0, "extract": 0}

        async def fake_resolve_retry(item, guild_id, bypass_cache=False):
            retry_calls["resolve"] += 1
            url = "https://youtube.example/dead" if not bypass_cache else "https://youtube.example/fresh"
            return ResolvedYouTubeTrack(item.track_id, url, "yt", item.duration_seconds, 90, time.time())

        def fake_extract_retry(url, requester_id, guild_id=None, use_cookies=True, js_runtime=None):
            retry_calls["extract"] += 1
            if "dead" in url:
                raise RuntimeError("video unavailable")
            return MusicTrack("fresh", url, "https://stream.example/fresh", requester_id, 240, url)

        voice_music.resolve_spotify_track_to_youtube = fake_resolve_retry
        voice_music.extract_track_info = fake_extract_retry
        converted = await resolve_spotify_track_to_music_track(sample_track(), "requester", "guild-a", None, "spotify:track:{0}".format(TRACK_ID))
        results.append(check("dead cached youtube url triggers one re-resolve", converted.source_url.endswith("/fresh") and retry_calls == {"resolve": 2, "extract": 2}, str(retry_calls)))

        def fake_extract_network(url, requester_id, guild_id=None, use_cookies=True, js_runtime=None):
            raise RuntimeError("network timeout")

        retry_calls["resolve"] = 0
        voice_music.extract_track_info = fake_extract_network
        try:
            await resolve_spotify_track_to_music_track(sample_track(), "requester", "guild-a", None, "spotify:track:{0}".format(TRACK_ID))
            network_retry_failed = False
        except RuntimeError:
            network_retry_failed = retry_calls["resolve"] == 1
        results.append(check("network errors do not invalidate spotify cache", network_retry_failed, str(retry_calls)))

        spotify_resolver._RESOLVE_CACHE.clear()
        aggregate_calls = []
        soundtrack_track = SpotifyTrackMetadata(
            track_id="2KD6Qx09NNMsv1HQOh8zVv",
            name="Beneath the Mask -rain-",
            artists=["Lyn", "ATLUS GAME MUSIC"],
            album_name="PERSONA5 ORIGINAL SOUNDTRACK",
            duration_ms=279000,
            isrc="",
            explicit=False,
            spotify_url="https://open.spotify.com/track/2KD6Qx09NNMsv1HQOh8zVv",
        )

        def fake_multi_query_search(query, guild_id=None, limit=5, use_cookies=True):
            aggregate_calls.append((query, limit))
            if "ATLUS GAME MUSIC" in query or query == "Beneath the Mask -rain-":
                return [
                    YouTubeCandidate("P5 - Beneath the Mask -rainy day- | Synchronized Lyrics", "https://youtube.example/watch?v=rainy", 272, "SkAlgorythmik"),
                    YouTubeCandidate("P5 - Beneath the Mask -rainy day- | Synchronized Lyrics", "https://youtu.be/rainy", 272, "SkAlgorythmik"),
                ]
            return [
                YouTubeCandidate("Lyn - Beneath The Mask -chiptune- (Silver Mix)", "https://youtube.example/watch?v=chip", 266, "Chiptune Channel"),
                YouTubeCandidate("Beneath the Mask - Lyn / REKA【歌ってみた】", "https://youtube.example/watch?v=cover", 277, "REKA"),
            ]

        spotify_resolver.search_youtube_candidates = fake_multi_query_search
        resolved_soundtrack = await resolve_spotify_track_to_youtube(soundtrack_track, "guild-a", bypass_cache=True)
        results.append(check("multi-query resolver can find soundtrack fallback", resolved_soundtrack.youtube_url.endswith("rainy"), str(resolved_soundtrack)))
        results.append(check("resolver passes configured candidate limit per query", all(limit == youtube_candidates_per_query() for _query, limit in aggregate_calls), str(aggregate_calls)))
        results.append(check("resolver tries multiple search queries", len(aggregate_calls) >= 3, str(aggregate_calls)))
        deduped = deduplicate_candidates(
            [
                YouTubeCandidate("same", "https://www.youtube.com/watch?v=abc", 10, ""),
                YouTubeCandidate("same duplicate", "https://youtu.be/abc", 10, ""),
                YouTubeCandidate("other", "https://youtube.example/watch?v=def", 10, ""),
            ]
        )
        results.append(check("duplicate youtube candidates are removed", len(deduped) == 2, str(deduped)))
    finally:
        spotify_resolver.search_youtube_candidates = original_search
        spotify_resolver._RESOLVE_CACHE.clear()
        spotify_resolver._RESOLVE_CACHE.update(original_cache)
        voice_music.resolve_spotify_track_to_youtube = original_resolve
        voice_music.extract_track_info = original_extract


async def run_album_and_queue_checks(results):
    original_resolve = voice_music.resolve_spotify_track_to_youtube
    original_extract = voice_music.extract_track_info
    try:
        async def fake_resolve(track, guild_id, bypass_cache=False):
            return ResolvedYouTubeTrack(track.track_id, "https://youtube.example/{0}".format(track.track_id), "yt {0}".format(track.name), track.duration_seconds, 90, time.time())

        def fake_extract(url, requester_id, guild_id=None, use_cookies=True, js_runtime=None):
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

        ordered_tracks = [
            sample_track(TRACK_ID, "曲1"),
            sample_track("1Q2W3E4R5T6Y7U8I9O0P1C", "曲2"),
            sample_track("1Q2W3E4R5T6Y7U8I9O0P1D", "曲3"),
        ]
        active = {"count": 0, "max": 0}

        async def fake_ordered_resolve(item, guild_id, bypass_cache=False):
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
            await asyncio.sleep(0.01)
            active["count"] -= 1
            if item.name == "曲2":
                raise RuntimeError("not found")
            return ResolvedYouTubeTrack(item.track_id, "https://youtube.example/{0}".format(item.track_id), "yt {0}".format(item.name), item.duration_seconds, 90, time.time())

        voice_music.resolve_spotify_track_to_youtube = fake_ordered_resolve
        original_concurrency = os.environ.get("SPOTIFY_RESOLVE_CONCURRENCY")
        os.environ["SPOTIFY_RESOLVE_CONCURRENCY"] = "2"
        resolved_tracks, failed_count = await resolve_spotify_album_tracks(ordered_tracks, "requester", "guild-a", None, "spotify:album:{0}".format(ALBUM_ID))
        results.append(check("album worker preserves track order after failures", [track.spotify_title for track in resolved_tracks] == ["曲1", "曲3"], str([track.spotify_title for track in resolved_tracks])))
        results.append(check("album worker counts partial failures", failed_count == 1, str(failed_count)))
        results.append(check("album worker respects concurrency limit", active["max"] <= 2, str(active["max"])))
        if original_concurrency is None:
            os.environ.pop("SPOTIFY_RESOLVE_CONCURRENCY", None)
        else:
            os.environ["SPOTIFY_RESOLVE_CONCURRENCY"] = original_concurrency

        transient_lock = get_album_lock("ichiyon:guild-to-remove")
        async with transient_lock:
            pass
        spotify_resolver.remove_album_lock("ichiyon:guild-to-remove", transient_lock)
        results.append(check("album lock can be removed after use", "ichiyon:guild-to-remove" not in spotify_resolver._ALBUM_LOCKS))
    finally:
        voice_music.resolve_spotify_track_to_youtube = original_resolve
        voice_music.extract_track_info = original_extract


def run_env_checks(results):
    original_values = {
        key: os.environ.get(key)
        for key in (
            "SPOTIFY_MAX_ALBUM_TRACKS",
            "SPOTIFY_RESOLVE_CONCURRENCY",
            "SPOTIFY_RESOLVE_CACHE_TTL_SECONDS",
            "SPOTIFY_MATCH_MIN_MARGIN",
            "SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES",
            "SPOTIFY_YOUTUBE_CANDIDATES_PER_QUERY",
        )
    }
    try:
        os.environ["SPOTIFY_MAX_ALBUM_TRACKS"] = "999"
        os.environ["SPOTIFY_RESOLVE_CONCURRENCY"] = "99"
        os.environ["SPOTIFY_RESOLVE_CACHE_TTL_SECONDS"] = "1"
        os.environ["SPOTIFY_MATCH_MIN_MARGIN"] = "999"
        os.environ["SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES"] = "1"
        os.environ["SPOTIFY_YOUTUBE_CANDIDATES_PER_QUERY"] = "99"
        results.append(check("album max tracks is clamped", max_album_tracks() == 200, str(max_album_tracks())))
        results.append(check("resolve concurrency is clamped", resolve_concurrency() == 4, str(resolve_concurrency())))
        results.append(check("resolve cache ttl has safe minimum", resolve_cache_ttl_seconds() == 60, str(resolve_cache_ttl_seconds())))
        results.append(check("match score margin is clamped", match_min_margin() == 100, str(match_min_margin())))
        results.append(check("resolve cache max entries has safe minimum", resolve_cache_max_entries() == 100, str(resolve_cache_max_entries())))
        results.append(check("youtube candidates per query is clamped", youtube_candidates_per_query() == 15, str(youtube_candidates_per_query())))
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
    results.append(check("env example documents spotify score margin", "SPOTIFY_MATCH_MIN_MARGIN=10" in env_text))
    results.append(check("env example documents spotify cache max entries", "SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES=1000" in env_text))
    results.append(check("env example documents spotify youtube candidates", "SPOTIFY_YOUTUBE_CANDIDATES_PER_QUERY=10" in env_text))
    results.append(check("docs mention spotify does not directly play audio", "Spotify上の音源やプレビュー音源を直接再生することはありません" in doc_text))
    results.append(check("docs mention spotify cache max entries", "SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES" in doc_text))

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
