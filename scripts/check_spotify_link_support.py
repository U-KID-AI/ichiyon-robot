import asyncio
import os
import re
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot.services.spotify_client import SpotifyAlbumMetadata, SpotifyTrackMetadata, max_album_tracks
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
    resolve_cache_max_entries,
    resolve_cache_ttl_seconds,
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
    resolve_spotify_track_to_music_track,
)


TRACK_ID = "1Q2W3E4R5T6Y7U8I9O0P1A"
ALBUM_ID = "2Q2W3E4R5T6Y7U8I9O0P1B"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def sample_track(track_id: str = TRACK_ID, name: str = "Heat Night", artists=None) -> SpotifyTrackMetadata:
    return SpotifyTrackMetadata(
        track_id=track_id,
        name=name,
        artists=artists or ["RIP SLYME"],
        album_name=name,
        duration_ms=240000,
        isrc="JPXXX0000001",
        explicit=False,
        spotify_url="https://open.spotify.com/track/{0}".format(track_id),
        disc_number=1,
        track_number=1,
    )


def run_url_checks(results):
    cases = {
        "https://open.spotify.com/track/{0}".format(TRACK_ID): "track",
        "https://open.spotify.com/album/{0}".format(ALBUM_ID): "album",
        "https://open.spotify.com/artist/{0}".format(TRACK_ID): "artist",
        "https://open.spotify.com/playlist/{0}?si=abc".format(TRACK_ID): "playlist",
        "https://open.spotify.com/intl-ja/track/{0}?si=abc".format(TRACK_ID): "track",
        "spotify:track:{0}".format(TRACK_ID): "track",
        "spotify:album:{0}".format(ALBUM_ID): "album",
        "spotify:artist:{0}".format(TRACK_ID): "artist",
        "spotify:playlist:{0}".format(TRACK_ID): "playlist",
        "https://open.spotify.com/episode/{0}".format(TRACK_ID): "episode",
    }
    for value, expected in cases.items():
        parsed = parse_spotify_link(value)
        results.append(check("spotify parse {0}".format(expected), parsed is not None and parsed.kind == expected, str(parsed)))
    results.append(check("invalid spotify id is rejected", parse_spotify_link("https://open.spotify.com/track/short").kind == "invalid"))
    results.append(check("similar domain is ignored", parse_spotify_link("https://open.spotify.example.com/track/{0}".format(TRACK_ID)) is None))
    results.append(check("playlist is supported", parse_spotify_link("https://open.spotify.com/playlist/{0}".format(TRACK_ID)).is_supported))
    results.append(check("music command accepts spotify url position", parse_music_command("play https://open.spotify.com/track/{0}".format(TRACK_ID))[0] == "music_play"))


def run_scoring_checks(results):
    track = sample_track()
    official = YouTubeCandidate("RIP SLYME - Heat Night Official Audio", "https://youtube.example/1", 241, "RIP SLYME - Topic")
    topic = YouTubeCandidate("Heat Night", "https://youtube.example/topic", 240, "RIP SLYME - Topic")
    vevo = YouTubeCandidate("RIP SLYME - Heat Night", "https://youtube.example/vevo", 240, "RIPSLYMEVEVO")
    different_artist = YouTubeCandidate("Heat Night Official Audio", "https://youtube.example/other", 240, "Other Artist - Topic")
    cover = YouTubeCandidate("RIP SLYME - Heat Night cover karaoke", "https://youtube.example/2", 240, "someone")
    karaoke = YouTubeCandidate("RIP SLYME - Heat Night karaoke", "https://youtube.example/karaoke", 240, "karaoke channel")
    instrumental = YouTubeCandidate("RIP SLYME - Heat Night instrumental", "https://youtube.example/instrumental", 240, "instrumental channel")
    short = YouTubeCandidate("Heat Night shorts", "https://youtube.example/3", 20, "shorts")
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
    live_track = sample_track(name="Heat Night Live")
    live_candidate = YouTubeCandidate("RIP SLYME - Heat Night Live", "https://youtube.example/live", 240, "RIP SLYME")
    normal_for_live = YouTubeCandidate("RIP SLYME - Heat Night Official Audio", "https://youtube.example/original", 240, "RIP SLYME - Topic")
    results.append(check("live spotify track accepts live candidate", score_candidate(live_track, live_candidate) >= 70, str(score_candidate(live_track, live_candidate))))
    results.append(check("live spotify track rejects original candidate", score_candidate(live_track, normal_for_live) < 70, str(score_candidate(live_track, normal_for_live))))
    remix_track = sample_track(name="Heat Night Remix")
    remix_candidate = YouTubeCandidate("RIP SLYME - Heat Night Remix", "https://youtube.example/remix", 240, "RIP SLYME")
    original_for_remix = YouTubeCandidate("RIP SLYME - Heat Night Official Audio", "https://youtube.example/original2", 240, "RIP SLYME - Topic")
    results.append(check("remix spotify track accepts remix candidate", score_candidate(remix_track, remix_candidate) >= 70, str(score_candidate(remix_track, remix_candidate))))
    results.append(check("remix spotify track rejects original candidate", score_candidate(remix_track, original_for_remix) < 70, str(score_candidate(remix_track, original_for_remix))))

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
    provant_official_collab = YouTubeCandidate("SawanoHiroyuki[nZk]:Jean-Ken Johnny:TAKUMA - PROVANT", "https://youtube.example/provant-collab", 171, "SawanoHiroyuki[nZk]")
    provant_official_mv = YouTubeCandidate("SawanoHiroyuki[nZk] - PROVANT feat. Jean-Ken Johnny & TAKUMA Official Music Video", "https://youtube.example/provant-mv", 171, "SawanoHiroyuki[nZk] Official YouTube Channel")
    provant_lyrics = YouTubeCandidate("SawanoHiroyuki[nZk] - PROVANT Lyrics", "https://youtube.example/provant-lyrics", 171, "Lyrics Channel")
    provant_best, provant_score = select_best_candidate(provant, [provant_official_collab, provant_official_mv, provant_lyrics])
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
    derived = [
        YouTubeCandidate("Beneath the Mask -rain- - Lyn / REKA 歌ってみた", "https://youtube.example/beneath-cover", 279, "REKA"),
        YouTubeCandidate("Lyn - Beneath The Mask -rain- -chiptune-", "https://youtube.example/beneath-chiptune", 279, "Chiptune Channel"),
        YouTubeCandidate("Lyn - Beneath The Mask -rain- drum cover", "https://youtube.example/beneath-drum-cover", 279, "Drum Cover Channel"),
    ]
    results.append(check("Beneath derived candidates are rejected", all(score_candidate(beneath, candidate) < 70 for candidate in derived), str([score_candidate(beneath, candidate) for candidate in derived])))
    try:
        select_best_candidate(beneath, derived)
        beneath_failed = False
    except SpotifyLowScoreError:
        beneath_failed = True
    results.append(check("Beneath derived-only candidates fail safely", beneath_failed))
    deduped = deduplicate_candidates(
        [
            YouTubeCandidate("same", "https://www.youtube.com/watch?v=abc", 10, ""),
            YouTubeCandidate("same duplicate", "https://youtu.be/abc", 10, ""),
            YouTubeCandidate("other", "https://youtube.example/watch?v=def", 10, ""),
        ]
    )
    results.append(check("duplicate youtube candidates are removed", len(deduped) == 2, str(deduped)))


async def run_resolver_checks(results):
    original_search = spotify_resolver.search_youtube_candidates
    original_cache = dict(spotify_resolver._RESOLVE_CACHE)
    original_resolve = voice_music.resolve_spotify_track_to_youtube
    original_extract = voice_music.extract_track_info_with_cookie_fallback
    original_home_vpn_enabled = os.environ.get("YOUTUBE_HOME_VPN_ENABLED")
    try:
        os.environ["YOUTUBE_HOME_VPN_ENABLED"] = "false"
        calls = {"count": 0}

        def fake_search(query, guild_id=None, limit=5):
            calls["count"] += 1
            return [YouTubeCandidate("RIP SLYME - Heat Night Official Audio", "https://youtube.example/watch?v=ok", 240, "RIP SLYME - Topic")]

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

        async def fake_extract_retry(url, requester_id, guild_id=None, voice_client=None):
            retry_calls["extract"] += 1
            if "dead" in url:
                raise RuntimeError("video unavailable")
            return MusicTrack("fresh", url, "https://stream.example/fresh", requester_id, 240, url)

        voice_music.resolve_spotify_track_to_youtube = fake_resolve_retry
        voice_music.extract_track_info_with_cookie_fallback = fake_extract_retry
        converted = await resolve_spotify_track_to_music_track(sample_track(), "requester", "guild-a", None, "spotify:track:{0}".format(TRACK_ID))
        results.append(check("dead cached youtube url triggers one re-resolve", converted.source_url.endswith("/fresh") and retry_calls == {"resolve": 2, "extract": 2}, str(retry_calls)))
    finally:
        spotify_resolver.search_youtube_candidates = original_search
        spotify_resolver._RESOLVE_CACHE.clear()
        spotify_resolver._RESOLVE_CACHE.update(original_cache)
        voice_music.resolve_spotify_track_to_youtube = original_resolve
        voice_music.extract_track_info_with_cookie_fallback = original_extract
        if original_home_vpn_enabled is None:
            os.environ.pop("YOUTUBE_HOME_VPN_ENABLED", None)
        else:
            os.environ["YOUTUBE_HOME_VPN_ENABLED"] = original_home_vpn_enabled


async def run_album_and_queue_checks(results):
    album = SpotifyAlbumMetadata(
        album_id=ALBUM_ID,
        name="Album",
        artists=["Artist"],
        spotify_url="https://open.spotify.com/album/{0}".format(ALBUM_ID),
        tracks=[sample_track(TRACK_ID, "Song 1"), sample_track("1Q2W3E4R5T6Y7U8I9O0P1C", "Song 2")],
        skipped_tracks=1,
    )
    lock = get_album_lock("ichiyon:guild-a")
    results.append(check("album lock is guild scoped", lock is get_album_lock("ichiyon:guild-a") and lock is not get_album_lock("irsia:guild-a")))
    results.append(check("album metadata carries skipped tracks", album.skipped_tracks == 1 and len(album.tracks) == 2))


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
        results.append(check("resolve concurrency is clamped", spotify_resolver.resolve_concurrency() == 4, str(spotify_resolver.resolve_concurrency())))
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


def run_compose_and_doc_checks(results):
    compose_text = (ROOT_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    env_text = (ROOT_DIR / ".env.example").read_text(encoding="utf-8")
    doc_text = (ROOT_DIR / "docs" / "voice-vc-commands.md").read_text(encoding="utf-8")
    for service_name in ("bot", "bot-irsia"):
        marker = "  {0}:".format(service_name)
        service_matches = list(re.finditer(r"(?m)^  [A-Za-z0-9_-]+:\s*$", compose_text))
        start = compose_text.find(marker)
        next_start = len(compose_text)
        for match in service_matches:
            if match.start() > start:
                next_start = match.start()
                break
        section = compose_text[start:next_start] if start >= 0 else ""
        results.append(check("{0} does not pass spotify client id env".format(service_name), "SPOTIFY_CLIENT_ID" not in section))
        results.append(check("{0} does not pass spotify client secret env".format(service_name), "SPOTIFY_CLIENT_SECRET" not in section))
    results.append(check("env example omits spotify client id", "SPOTIFY_CLIENT_ID" not in env_text))
    results.append(check("env example omits spotify client secret", "SPOTIFY_CLIENT_SECRET" not in env_text))
    results.append(check("env example documents spotify score margin", "SPOTIFY_MATCH_MIN_MARGIN=10" in env_text))
    results.append(check("env example documents spotify cache max entries", "SPOTIFY_RESOLVE_CACHE_MAX_ENTRIES=1000" in env_text))
    results.append(check("env example documents spotify youtube candidates", "SPOTIFY_YOUTUBE_CANDIDATES_PER_QUERY=10" in env_text))
    results.append(check("docs mention public spotify metadata", "public Spotify pages or public embed HTML" in doc_text))
    results.append(check("docs omit spotify client credentials", "SPOTIFY_CLIENT_ID" not in doc_text and "SPOTIFY_CLIENT_SECRET" not in doc_text))


async def main_async() -> int:
    results = []
    run_url_checks(results)
    run_scoring_checks(results)
    await run_resolver_checks(results)
    await run_album_and_queue_checks(results)
    run_env_checks(results)
    run_compose_and_doc_checks(results)

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
