import asyncio
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import bot.services.youtube_n_pull as n_pull
from bot.repositories.youtube_n_pull import cache_is_fresh, normalize_command_name
from bot.services.voice_music import MusicTrack


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = "[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else "")
    print(line.encode("cp932", errors="backslashreplace").decode("cp932"))
    return ok


class FakeChannel:
    def __init__(self):
        self.messages = []

    async def send(self, content):
        self.messages.append(str(content))


class FakeAuthor:
    def __init__(self, bot=False):
        self.id = "requester"
        self.bot = bot


class FakeGuild:
    id = "guild-a"


class FakeVoiceClient:
    channel = type("Channel", (), {"id": "voice-a"})()

    def __init__(self, playing=False, paused=False):
        self.playing = playing
        self.paused = paused

    def is_playing(self):
        return self.playing

    def is_paused(self):
        return self.paused


class FakeMessage:
    def __init__(self, bot=False):
        self.guild = FakeGuild()
        self.author = FakeAuthor(bot=bot)
        self.channel = FakeChannel()


class FakeRepository:
    def __init__(self, connection=None):
        self.preset = {
            "id": 1,
            "bot_id": "ichiyon",
            "guild_id": "guild-a",
            "display_name": "しゃろう",
            "command_name": "しゃろう",
            "command_key": normalize_command_name("しゃろう"),
            "aliases": "Sharou\nP5",
            "enabled": True,
            "max_pulls": 100,
            "cache_ttl_seconds": 86400,
            "include_shorts": False,
            "include_live": False,
            "include_archived_live": False,
            "min_duration_seconds": None,
            "max_duration_seconds": 7200,
            "include_title_terms": "",
            "exclude_title_terms": "cover",
            "last_cache_refresh_at": datetime.now(timezone.utc),
            "last_cache_error": "",
        }
        self.videos = [
            {"video_id": "a", "canonical_url": "https://www.youtube.com/watch?v=a", "title": "2:23 AM", "duration_seconds": 120, "live_status": ""},
            {"video_id": "b", "canonical_url": "https://www.youtube.com/watch?v=b", "title": "3:03 PM", "duration_seconds": 180, "live_status": ""},
            {"video_id": "c", "canonical_url": "https://www.youtube.com/watch?v=c", "title": "SUMMER TRIANGLE", "duration_seconds": 240, "live_status": ""},
        ]
        self.sources = [{"id": 1, "source_type": "channel", "source_url": "https://www.youtube.com/channel/UCfjca6Z_wpyinTqHdIYJ49Q", "enabled": True}]
        self.replaced_cache = []
        self.marked = []

    def list_presets(self, guild_id, enabled=None):
        return [self.preset] if enabled is True else [self.preset]

    def find_preset_by_command(self, guild_id, command_name):
        key = normalize_command_name(command_name)
        if key in (self.preset["command_key"], normalize_command_name("Sharou"), normalize_command_name("P5")):
            return self.preset
        return None

    def get_preset(self, guild_id, preset_id):
        return self.preset

    def list_cached_videos(self, preset_id):
        return list(self.videos)

    def list_sources(self, preset_id, enabled=None):
        return list(self.sources)

    def replace_cache_videos(self, preset_id, videos):
        self.replaced_cache = list(videos)
        self.videos = list(videos)

    def mark_cache_refresh(self, preset_id, error=""):
        self.marked.append(error)


class FakeFeatureFlagRepository:
    enabled = True

    def __init__(self, connection=None):
        pass

    def is_enabled(self, guild_id, feature_key, default=True):
        return self.enabled


class FakeConnection:
    def __init__(self):
        self.repository = FakeRepository()
        self.committed = False
        self.rolled_back = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def main() -> int:
    results = []
    import_probe = """
import importlib.abc
import sys

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("bot.services.voice_music", "bot.services.youtube_cookie_monitor"):
            raise ImportError("blocked " + fullname)
        return None

sys.meta_path.insert(0, Blocker())
import admin.youtube_n_pull as module
assert "bot.services.voice_music" not in sys.modules
assert "bot.services.youtube_cookie_monitor" not in sys.modules
assert callable(module.is_youtube_source_url)
assert callable(module.fetch_source_videos)
print("admin import ok")
"""
    probe_result = subprocess.run(
        [sys.executable, "-c", import_probe],
        cwd=str(ROOT_DIR),
        text=True,
        capture_output=True,
    )
    results.append(
        check(
            "admin import does not require voice music or cookie monitor",
            probe_result.returncode == 0,
            (probe_result.stdout + probe_result.stderr).strip(),
        )
    )
    results.append(check("command parses 10連", n_pull.parse_n_pull_command("油粘土マン 10連")[:2] == ("油粘土マン", 10), str(n_pull.parse_n_pull_command("油粘土マン 10連"))))
    results.append(check("command parses 100連", n_pull.parse_n_pull_command("しゃろう 100連")[:2] == ("しゃろう", 100)))
    results.append(check("command parses alias P5", n_pull.parse_n_pull_command("P5 10連")[:2] == ("P5", 10)))
    results.append(check("full-width digits parse", n_pull.parse_n_pull_command("しゃろう １０連")[:2] == ("しゃろう", 10)))
    results.append(check("space before 連 parses", n_pull.parse_n_pull_command("しゃろう 10 連")[:2] == ("しゃろう", 10)))
    results.append(check("0連 is rejected", n_pull.parse_n_pull_command("しゃろう 0連")[2] is not None))
    results.append(check("101連 is rejected", n_pull.parse_n_pull_command("しゃろう 101連")[2] is not None))
    results.append(check("missing number is rejected", n_pull.parse_n_pull_command("しゃろう 連")[2] is not None))
    results.append(check("normalizer absorbs case width spaces", normalize_command_name(" Persona　5 ") == normalize_command_name("persona 5")))
    migration_sql = (ROOT_DIR / "migrations" / "035_add_youtube_n_pull_presets.sql").read_text(encoding="utf-8")
    results.append(check("migration uses bot guild command key unique scope", "ON youtube_n_pull_presets(bot_id, guild_id, command_key)" in migration_sql))
    results.append(check("migration does not use command_name-only unique", "UNIQUE (command_name)" not in migration_sql and "ON youtube_n_pull_presets(command_name)" not in migration_sql))
    results.append(check("migration seeds unconfirmed presets disabled", "'油粘土マン',\n            '油粘土マン'," in migration_sql and "FALSE,\n            100" in migration_sql))
    repository_source = (ROOT_DIR / "bot" / "repositories" / "youtube_n_pull.py").read_text(encoding="utf-8")
    results.append(check("cache refresh replaces old cache rows", "DELETE FROM youtube_n_pull_cache_videos WHERE preset_id = %s" in repository_source))

    videos = [
        {"video_id": "a", "canonical_url": "https://www.youtube.com/watch?v=a", "title": "normal", "duration_seconds": 100, "live_status": ""},
        {"video_id": "a", "canonical_url": "https://www.youtube.com/watch?v=a", "title": "normal duplicate", "duration_seconds": 100, "live_status": ""},
        {"video_id": "b", "canonical_url": "https://www.youtube.com/watch?v=b", "title": "cover song", "duration_seconds": 100, "live_status": ""},
        {"video_id": "c", "canonical_url": "https://www.youtube.com/shorts/c", "title": "short", "duration_seconds": 30, "live_status": ""},
        {"video_id": "cs", "canonical_url": "https://www.youtube.com/watch?v=cs", "entry_url": "https://www.youtube.com/shorts/cs", "title": "short entry", "duration_seconds": 30, "live_status": ""},
        {"video_id": "d", "canonical_url": "https://www.youtube.com/watch?v=d", "title": "live", "duration_seconds": 100, "live_status": "is_live"},
        {"video_id": "e", "canonical_url": "https://www.youtube.com/watch?v=e", "title": "long", "duration_seconds": 9000, "live_status": ""},
    ]
    preset = FakeRepository().preset
    filtered = [video for video in videos if n_pull.video_passes_filters(video, preset)]
    results.append(check("filters remove duplicate later via picker", len(n_pull.pick_videos(filtered, 10)) == 1, str(filtered)))
    results.append(check("shorts are excluded", all(video["video_id"] != "c" for video in filtered), str(filtered)))
    results.append(check("shorts entry URL is excluded", all(video["video_id"] != "cs" for video in filtered), str(filtered)))
    results.append(check("live is excluded", all(video["video_id"] != "d" for video in filtered), str(filtered)))
    results.append(check("title exclude terms work", all(video["video_id"] != "b" for video in filtered), str(filtered)))
    results.append(check("duration filter works", all(video["video_id"] != "e" for video in filtered), str(filtered)))

    picked = n_pull.pick_videos(FakeRepository().videos, 2)
    results.append(check("pick has no duplicates", len({video["video_id"] for video in picked}) == len(picked), str(picked)))
    picked_all = n_pull.pick_videos(FakeRepository().videos, 100)
    results.append(check("underfilled request returns all available", len(picked_all) == 3, str(picked_all)))
    second_pick = n_pull.pick_videos(FakeRepository().videos, 3)
    results.append(check("separate run may select same videos", {v["video_id"] for v in picked_all} == {v["video_id"] for v in second_pick}))

    old = dict(FakeRepository().preset)
    old["last_cache_refresh_at"] = datetime.now(timezone.utc) - timedelta(hours=25)
    results.append(check("fresh cache is detected", cache_is_fresh(FakeRepository().preset)))
    results.append(check("stale cache is detected", not cache_is_fresh(old)))

    track = n_pull.make_track_from_cached_video(FakeRepository().videos[0], "requester")
    results.append(check("queued track defers stream extraction", isinstance(track, MusicTrack) and track.refresh_required and track.stream_url == "", str(track)))
    results.append(check("runtime track creation uses music dependency lazily", track.source_type == "youtube_n_pull" and track.source_url == "https://www.youtube.com/watch?v=a", str(track)))

    results.append(check("youtube source URL validates channel", n_pull.is_youtube_source_url("https://www.youtube.com/channel/UCfjca6Z_wpyinTqHdIYJ49Q")))
    results.append(check("invalid source URL is rejected", not n_pull.is_youtube_source_url("https://example.com/channel")))
    results.append(check("channel root URL normalizes to videos tab", n_pull.normalize_youtube_source_url("https://www.youtube.com/channel/UCabc") == "https://www.youtube.com/channel/UCabc/videos"))
    results.append(check("handle root URL normalizes to videos tab", n_pull.normalize_youtube_source_url("https://www.youtube.com/@sample") == "https://www.youtube.com/@sample/videos"))
    results.append(check("existing videos tab is not duplicated", n_pull.normalize_youtube_source_url("https://www.youtube.com/@sample/videos") == "https://www.youtube.com/@sample/videos"))
    results.append(check("playlist URL is not normalized as channel", n_pull.normalize_youtube_source_url("https://www.youtube.com/playlist?list=PLabc") == "https://www.youtube.com/playlist?list=PLabc"))

    playlist_fixture = {
        "_type": "playlist",
        "title": "oil clay man - Videos",
        "id": "UCchannelid",
        "entries": [
            {"_type": "url", "id": "VIDEO_ID_01", "url": "https://www.youtube.com/watch?v=VIDEO_ID_01", "title": "actual video 1"},
            {"_type": "url", "id": "VIDEO_ID_02", "url": "https://www.youtube.com/watch?v=VIDEO_ID_02", "title": "actual video 2"},
        ],
    }
    extracted_entries = n_pull.iter_source_video_entries(playlist_fixture)
    extracted_videos = [n_pull.extract_video_from_entry(entry) for entry in extracted_entries]
    results.append(check("top-level playlist dict is not treated as video", len(extracted_entries) == 2, str(extracted_entries)))
    results.append(check("playlist entries produce real videos", [video["video_id"] for video in extracted_videos if video] == ["VIDEO_ID_01", "VIDEO_ID_02"], str(extracted_videos)))
    results.append(check("canonical watch URL is generated", extracted_videos[0]["canonical_url"] == "https://www.youtube.com/watch?v=VIDEO_ID_01", str(extracted_videos[0])))
    results.append(check("entry with nested entries is not cached as video", n_pull.extract_video_from_entry(playlist_fixture) is None))
    results.append(check("entry without video id is rejected", n_pull.extract_video_from_entry({"title": "no id", "url": "https://www.youtube.com/@sample/videos"}) is None))
    results.append(check("channel id is not used as video id", n_pull.extract_video_from_entry({"id": "UCfjca6Z_wpyinTqHdIYJ49Q", "title": "channel only"}) is None))
    nested_fixture = {
        "_type": "playlist",
        "title": "root",
        "entries": [
            {"_type": "playlist", "title": "tab", "entries": [{"_type": "url", "id": "NESTED00001", "url": "https://www.youtube.com/watch?v=NESTED00001", "title": "nested video"}]},
        ],
    }
    nested_videos = [n_pull.extract_video_from_entry(entry) for entry in n_pull.iter_source_video_entries(nested_fixture)]
    results.append(check("nested entries are expanded", [video["video_id"] for video in nested_videos if video] == ["NESTED00001"], str(nested_videos)))

    class _FakeYoutubeDL:
        seen_urls = []
        seen_options = []

        def __init__(self, options):
            self.options = options
            _FakeYoutubeDL.seen_options.append(dict(options))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            _FakeYoutubeDL.seen_urls.append((url, download))
            return playlist_fixture

    class _FakeYtDlp:
        YoutubeDL = _FakeYoutubeDL

    original_ytdlp = n_pull.yt_dlp
    try:
        n_pull.yt_dlp = _FakeYtDlp
        fetched = n_pull.fetch_source_videos(
            {"id": 99, "source_url": "https://www.youtube.com/channel/UCabc"},
            "guild-a",
            FakeRepository().preset,
        )
        results.append(check("fetch normalizes channel before yt-dlp", _FakeYoutubeDL.seen_urls[0][0] == "https://www.youtube.com/channel/UCabc/videos", str(_FakeYoutubeDL.seen_urls)))
        results.append(check("fetch expands playlist entries", [video["video_id"] for video in fetched] == ["VIDEO_ID_01", "VIDEO_ID_02"], str(fetched)))
        results.append(check("fetch keeps flat extraction options", _FakeYoutubeDL.seen_options[0].get("extract_flat") is True and _FakeYoutubeDL.seen_options[0].get("skip_download") is True, str(_FakeYoutubeDL.seen_options[0])))
        results.append(check("fetch does not pre-resolve stream URLs", all("stream_url" not in video for video in fetched), str(fetched)))
    finally:
        n_pull.yt_dlp = original_ytdlp

    original_get_connection = n_pull.get_connection
    original_repo = n_pull.YouTubeNPullRepository
    original_flags = n_pull.FeatureFlagRepository
    original_voice = n_pull.get_guild_voice_client
    original_play_next = n_pull.play_next_track
    fake_connection = FakeConnection()
    play_calls = []
    try:
        class _ConnectionContext:
            def __enter__(self):
                return fake_connection

            def __exit__(self, exc_type, exc, tb):
                fake_connection.close()

        n_pull.get_connection = lambda: _ConnectionContext()
        n_pull.YouTubeNPullRepository = lambda connection: fake_connection.repository
        n_pull.FeatureFlagRepository = FakeFeatureFlagRepository
        n_pull.get_guild_voice_client = lambda guild: FakeVoiceClient()

        async def _fake_play_next(voice_client, guild_id):
            play_calls.append(guild_id)
            return True

        n_pull.play_next_track = _fake_play_next
        message = FakeMessage()
        handled = asyncio.run(n_pull.handle_youtube_n_pull_command(message, "しゃろう 2連"))
        results.append(check("mention command handles known preset", handled is True))
        results.append(check("queue output mentions added count", any("2件" in text for text in message.channel.messages), str(message.channel.messages)))
        results.append(check("idle playback starts once", play_calls == ["guild-a"], str(play_calls)))

        n_pull.get_guild_voice_client = lambda guild: None
        no_vc_message = FakeMessage()
        handled = asyncio.run(n_pull.handle_youtube_n_pull_command(no_vc_message, "しゃろう 1連"))
        results.append(check("VC disconnected is handled without auto join", handled is True and play_calls == ["guild-a"]))
        results.append(check("VC disconnected sends guidance", any("VC" in text for text in no_vc_message.channel.messages), str(no_vc_message.channel.messages)))

        FakeFeatureFlagRepository.enabled = False
        n_pull.get_guild_voice_client = lambda guild: FakeVoiceClient()
        feature_off_message = FakeMessage()
        handled = asyncio.run(n_pull.handle_youtube_n_pull_command(feature_off_message, "しゃろう 1連"))
        results.append(check("feature flag OFF blocks N pull", handled is True and any("OFF" in text for text in feature_off_message.channel.messages), str(feature_off_message.channel.messages)))
        FakeFeatureFlagRepository.enabled = True

        results.append(check("bot author does not trigger", asyncio.run(n_pull.handle_youtube_n_pull_command(FakeMessage(bot=True), "しゃろう 1連")) is False))
        results.append(check("non N-pull command does not trigger", asyncio.run(n_pull.handle_youtube_n_pull_command(FakeMessage(), "https://youtu.be/abc")) is False))

        fake_connection.repository.preset["last_cache_refresh_at"] = datetime.now(timezone.utc) - timedelta(days=2)
        refresh_calls = []

        def _fake_fetch_source(source, guild_id, preset):
            refresh_calls.append(source["source_url"])
            return fake_connection.repository.videos

        original_fetch = n_pull.fetch_source_videos
        n_pull.fetch_source_videos = _fake_fetch_source
        try:
            refreshed, status = asyncio.run(n_pull.refresh_cache_if_needed(fake_connection.repository, "guild-a", fake_connection.repository.preset))
            results.append(check("stale cache refreshes", refreshed and status == "refresh", str((refreshed, status))))
            results.append(check("refresh uses source once", len(refresh_calls) == 1, str(refresh_calls)))
        finally:
            n_pull.fetch_source_videos = original_fetch

        fake_connection.repository.preset["last_cache_refresh_at"] = datetime.now(timezone.utc) - timedelta(days=2)
        fake_connection.repository.videos = [{"video_id": "old", "canonical_url": "https://www.youtube.com/watch?v=old", "title": "old cache", "duration_seconds": 100, "live_status": ""}]
        before_empty_refresh = list(fake_connection.repository.videos)

        def _fake_empty_fetch(source, guild_id, preset):
            return []

        n_pull.fetch_source_videos = _fake_empty_fetch
        try:
            refreshed, status = asyncio.run(n_pull.refresh_cache_if_needed(fake_connection.repository, "guild-a", fake_connection.repository.preset))
            results.append(check("Videos-tab-only refresh is not success", refreshed is False and status == "stale", str((refreshed, status))))
            results.append(check("failed refresh keeps old cache", fake_connection.repository.videos == before_empty_refresh, str(fake_connection.repository.videos)))
        finally:
            n_pull.fetch_source_videos = original_fetch
    finally:
        n_pull.get_connection = original_get_connection
        n_pull.YouTubeNPullRepository = original_repo
        n_pull.FeatureFlagRepository = original_flags
        n_pull.get_guild_voice_client = original_voice
        n_pull.play_next_track = original_play_next

    result_text = "\n".join(n_pull.build_result_messages(FakeRepository().preset, 100, FakeRepository().videos * 40, "hit"))
    results.append(check("100 pull output is bounded and split-safe", len(result_text) < 8000, str(len(result_text))))

    ok_count = sum(1 for result in results if result)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
