import asyncio
import os
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import bot.services.voice_music as voice_music
from bot.services.voice_music import MusicTrack, clear_music_state, get_music_state


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def expiring_stream_url(seconds: int = 600) -> str:
    return "https://stream.example.test/audio?expire={0}".format(int(time.time()) + seconds)


async def check_cache_and_singleflight() -> list:
    results = []
    original_extract = voice_music.extract_track_info
    voice_music.clear_youtube_extract_runtime_state()
    os.environ["YOUTUBE_EXTRACT_CACHE_MAX_ENTRIES"] = "2"
    os.environ["YOUTUBE_EXTRACT_CACHE_MAX_TTL_SECONDS"] = "300"
    calls = []

    def fake_extract(url, requester_id, guild_id=None, use_cookies=True, js_runtime=None):
        calls.append((url, requester_id, guild_id, use_cookies, js_runtime))
        time.sleep(0.05)
        return MusicTrack(
            title="cached track",
            webpage_url="https://www.youtube.com/watch?v=cacheTest01",
            stream_url=expiring_stream_url(),
            requester_id=requester_id,
            duration=120,
            source_url=url,
        )

    try:
        voice_music.extract_track_info = fake_extract
        first = await voice_music.extract_track_info_with_cookie_fallback("https://youtu.be/cacheTest01", "user-a", "guild-cache")
        second = await voice_music.extract_track_info_with_cookie_fallback("https://youtu.be/cacheTest01", "user-b", "guild-cache")
        results.append(check("cache hit avoids second extraction", len(calls) == 1, str(calls)))
        results.append(check("cache clone updates requester", second.requester_id == "user-b", second.requester_id))
        results.append(check("cache hit is marked", second.from_extract_cache is True, str(second.from_extract_cache)))
        results.append(check("leader result is not marked as cache", first.from_extract_cache is False, str(first.from_extract_cache)))

        voice_music.clear_youtube_extract_runtime_state()
        calls.clear()
        concurrent = await asyncio.gather(
            voice_music.extract_track_info_with_cookie_fallback("https://youtu.be/singleFlight", "user-1", "guild-cache"),
            voice_music.extract_track_info_with_cookie_fallback("https://youtu.be/singleFlight", "user-2", "guild-cache"),
        )
        results.append(check("single-flight shares one extraction", len(calls) == 1, str(calls)))
        results.append(check("single-flight waiter keeps requester", {track.requester_id for track in concurrent} == {"user-1", "user-2"}, str([track.requester_id for track in concurrent])))
        results.append(check("inflight registry is cleared", not voice_music._YOUTUBE_EXTRACT_INFLIGHT, str(voice_music._YOUTUBE_EXTRACT_INFLIGHT)))

        key, video_id = voice_music.youtube_extract_cache_key("https://youtu.be/singleFlight")
        entry = voice_music._YOUTUBE_EXTRACT_CACHE[key]
        entry.expires_at_monotonic = time.monotonic() - 1
        calls_before = len(calls)
        await voice_music.extract_track_info_with_cookie_fallback("https://youtu.be/singleFlight", "user-3", "guild-cache")
        results.append(check("expired cache re-extracts", len(calls) == calls_before + 1, str(calls)))

        voice_music.clear_youtube_extract_runtime_state()
        for index in range(3):
            cache_key, vid = voice_music.youtube_extract_cache_key("https://youtu.be/cachePrune{0}".format(index))
            voice_music.put_youtube_extract_cache(
                cache_key,
                vid,
                MusicTrack(
                    title="track {0}".format(index),
                    webpage_url="https://www.youtube.com/watch?v=cachePrune{0}".format(index),
                    stream_url=expiring_stream_url(),
                    requester_id="user",
                    source_url="https://youtu.be/cachePrune{0}".format(index),
                ),
            )
        results.append(check("cache max entries is enforced", len(voice_music._YOUTUBE_EXTRACT_CACHE) <= 2, str(len(voice_music._YOUTUBE_EXTRACT_CACHE))))
    finally:
        voice_music.extract_track_info = original_extract
        voice_music.clear_youtube_extract_runtime_state()
    return results


async def check_prefetch() -> list:
    results = []
    guild_id = "guild-prefetch"
    original_extract_with_fallback = voice_music.extract_track_info_with_cookie_fallback
    voice_music.clear_youtube_extract_runtime_state()
    clear_music_state(guild_id)
    os.environ["YOUTUBE_EXTRACT_PREFETCH_COUNT"] = "2"
    os.environ["YOUTUBE_EXTRACT_PREFETCH_CONCURRENCY"] = "1"
    calls = []
    active = 0
    max_active = 0

    async def fake_extract_with_fallback(url, requester_id, guild_id_arg, voice_client=None, js_runtime=None, bypass_cache=False):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append((url, requester_id, guild_id_arg))
        await asyncio.sleep(0.01)
        active -= 1
        return MusicTrack("prefetched", url, expiring_stream_url(), requester_id, 60, url)

    try:
        voice_music.extract_track_info_with_cookie_fallback = fake_extract_with_fallback
        state = get_music_state(guild_id)
        state.queue.append(MusicTrack("one", "https://youtu.be/prefetch01", "", "user", 60, "https://youtu.be/prefetch01", refresh_required=True))
        state.queue.append(MusicTrack("two", "https://youtu.be/prefetch02", "", "user", 60, "https://youtu.be/prefetch02", refresh_required=True))
        state.queue.append(MusicTrack("three", "https://youtu.be/prefetch03", "", "user", 60, "https://youtu.be/prefetch03", refresh_required=True))
        voice_music.schedule_prefetch_for_queue(guild_id)
        tasks = list(state.prefetch_tasks.values())
        await asyncio.gather(*tasks)
        voice_music.cleanup_prefetch_tasks(guild_id)
        results.append(check("prefetch count limits queued tracks", len(calls) == 2, str(calls)))
        results.append(check("prefetch keeps queue order intact", [track.title for track in state.queue] == ["one", "two", "three"], str([track.title for track in state.queue])))
        results.append(check("prefetch concurrency is limited", max_active == 1, str(max_active)))
        results.append(check("prefetch completed tasks are cleaned", not state.prefetch_tasks, str(state.prefetch_tasks)))

        calls.clear()
        state.queue.clear()
        state.queue.append(MusicTrack("cancel", "https://youtu.be/cancel01", "", "user", 60, "https://youtu.be/cancel01", refresh_required=True))

        async def slow_extract(url, requester_id, guild_id_arg, voice_client=None, js_runtime=None, bypass_cache=False):
            calls.append((url, requester_id, guild_id_arg))
            await asyncio.sleep(10)
            return MusicTrack("slow", url, expiring_stream_url(), requester_id, 60, url)

        voice_music.extract_track_info_with_cookie_fallback = slow_extract
        voice_music.schedule_prefetch_for_queue(guild_id)
        tasks = list(state.prefetch_tasks.values())
        clear_music_state(guild_id)
        await asyncio.gather(*tasks, return_exceptions=True)
        results.append(check("clear music state cancels prefetch", all(task.cancelled() for task in tasks), str([task.cancelled() for task in tasks])))
    finally:
        voice_music.extract_track_info_with_cookie_fallback = original_extract_with_fallback
        clear_music_state(guild_id)
        voice_music.clear_youtube_extract_runtime_state()
    return results


async def run_checks() -> list:
    results = []
    results.extend(await check_cache_and_singleflight())
    results.extend(await check_prefetch())
    return results


def main() -> int:
    results = asyncio.run(run_checks())
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
