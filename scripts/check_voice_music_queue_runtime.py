import asyncio
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import bot.services.voice_music as voice_music
from bot.services.voice_music import (
    MUSIC_LOOP_OFF,
    MUSIC_LOOP_ONE,
    MUSIC_LOOP_QUEUE,
    MusicState,
    MusicTrack,
    YTDLP_COOKIES_FILE_ENV,
    apply_music_volume_to_voice_client,
    build_ytdl_options,
    clear_music_state,
    extract_music_links_from_text,
    format_now_playing,
    format_queue,
    get_music_state,
    get_ytdlp_cookie_tmp_path,
    handle_mention_music_links,
    loop_status_text,
    make_loop_track,
    parse_volume_percent,
    is_youtube_cookie_required_error,
    is_http_url,
    parse_music_command,
    refresh_track_for_playback,
    save_music_volume_percent,
    volume_factor,
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakeChannel:
    def __init__(self):
        self.messages = []

    async def send(self, content):
        self.messages.append(str(content))


class FakeAuthor:
    def __init__(self, user_id="1001", bot=False, voice_channel=None):
        self.id = user_id
        self.bot = bot
        self.voice = type("FakeVoiceState", (), {"channel": voice_channel})() if voice_channel is not None else None


class FakeGuild:
    def __init__(self, guild_id="guild-link"):
        self.id = guild_id
        self.voice_client = None


class FakeVoiceChannel:
    def __init__(self, guild, channel_id="voice-link"):
        self.guild = guild
        self.id = channel_id
        self.connect_calls = 0

    async def connect(self):
        self.connect_calls += 1
        voice_client = FakeVoiceClient()
        voice_client.channel = self
        self.guild.voice_client = voice_client
        return voice_client


class FakeVoiceClient:
    def __init__(self, playing=False, paused=False, connected=True):
        self._playing = playing
        self._paused = paused
        self._connected = connected
        self.channel = type("FakeVoiceChannel", (), {"id": "voice-link"})()

    def is_connected(self):
        return self._connected

    def is_playing(self):
        return self._playing

    def is_paused(self):
        return self._paused


class FakeMessage:
    def __init__(self, guild_id="guild-link", user_id="1001", bot=False, voice_channel=None):
        self.guild = FakeGuild(guild_id)
        if voice_channel == "author":
            voice_channel = FakeVoiceChannel(self.guild)
        self.author = FakeAuthor(user_id, bot=bot, voice_channel=voice_channel)
        self.channel = FakeChannel()


async def run_mention_link_checks():
    results = []
    guild_id = "guild-link"
    clear_music_state(guild_id)
    fake_voice = FakeVoiceClient()
    extract_calls = []
    play_next_calls = []
    spotify_calls = []

    original_get_voice = voice_music.get_guild_voice_client
    original_extract = voice_music.extract_track_info_with_cookie_fallback
    original_play_next = voice_music.play_next_track
    original_enqueue_spotify = voice_music.enqueue_spotify_link
    original_ensure_voice = voice_music.ensure_mention_music_voice_client
    try:
        async def _fake_ensure_voice(message):
            return fake_voice

        voice_music.ensure_mention_music_voice_client = _fake_ensure_voice

        async def _fake_extract(url, requester_id, guild_id_arg, voice_client):
            extract_calls.append((url, requester_id, guild_id_arg))
            return MusicTrack("link track", url, "https://stream.example.com/link", requester_id, 120, url)

        async def _fake_play_next(voice_client, guild_id_arg):
            play_next_calls.append(guild_id_arg)
            return True

        async def _fake_enqueue_spotify(message, link, voice_client):
            spotify_calls.append((link.kind, link.original_url))
            return True

        voice_music.extract_track_info_with_cookie_fallback = _fake_extract
        voice_music.play_next_track = _fake_play_next
        voice_music.enqueue_spotify_link = _fake_enqueue_spotify

        message = FakeMessage(guild_id)
        results.append(check("mention youtube link is handled", await handle_mention_music_links(message, "https://youtu.be/abc123") is True))
        results.append(check("youtube link is passed once", len(extract_calls) == 1 and extract_calls[-1][0] == "https://youtu.be/abc123", str(extract_calls)))
        results.append(check("youtube link starts playback when idle", play_next_calls == [guild_id], str(play_next_calls)))

        before_extracts = len(extract_calls)
        results.append(check("legacy play command with link is intercepted once", await handle_mention_music_links(FakeMessage(guild_id), "豁後∴ https://youtu.be/legacy") is True))
        results.append(check("legacy play command enqueues only once", len(extract_calls) == before_extracts + 1, str(extract_calls)))

        results.append(check("full-width space before youtube link is handled", await handle_mention_music_links(FakeMessage(guild_id), "　https://youtu.be/fullwidth") is True))
        results.append(check("arbitrary text before youtube link is handled", await handle_mention_music_links(FakeMessage(guild_id), "これ流して https://www.youtube.com/watch?v=text") is True))

        results.append(check("spotify track link is handled", await handle_mention_music_links(FakeMessage(guild_id), "https://open.spotify.com/track/1234567890123456789012?si=test&utm_source=copy-link") is True))
        results.append(check("spotify track is routed to spotify enqueue", spotify_calls[-1][0] == "track", str(spotify_calls)))
        results.append(check("spotify album link is handled", await handle_mention_music_links(FakeMessage(guild_id), "https://open.spotify.com/album/1234567890123456789012?rowId=1") is True))
        results.append(check("spotify album is routed to spotify enqueue", spotify_calls[-1][0] == "album", str(spotify_calls)))
        results.append(check("spotify URI is handled", await handle_mention_music_links(FakeMessage(guild_id), "spotify:track:1234567890123456789012") is True))
        results.append(check("spotify URI is routed to spotify enqueue", spotify_calls[-1][0] == "track", str(spotify_calls)))

        results.append(check("no mention command text does not trigger", await handle_mention_music_links(FakeMessage(guild_id), None) is False))
        results.append(check("bot author's message does not trigger", await handle_mention_music_links(FakeMessage(guild_id, bot=True), "https://youtu.be/bot") is False))
        results.append(check("unsupported URL does not trigger", await handle_mention_music_links(FakeMessage(guild_id), "https://example.com/not-music") is False))

        before_extracts = len(extract_calls)
        voice_music.ensure_mention_music_voice_client = original_ensure_voice
        no_vc_message = FakeMessage(guild_id)
        results.append(check("vc disconnected mention link is handled with author guidance", await handle_mention_music_links(no_vc_message, "https://youtu.be/no-vc") is True))
        results.append(check("author-not-in-vc mention link does not extract URL", len(extract_calls) == before_extracts, str(extract_calls)))
        results.append(check("author-not-in-vc mention link asks user to join vc", no_vc_message.channel.messages == ["VCに参加してからURLを送ってください。"], str(no_vc_message.channel.messages)))

        auto_join_message = FakeMessage(guild_id, voice_channel="author")
        results.append(check("vc disconnected mention link auto joins author vc", await handle_mention_music_links(auto_join_message, "https://youtu.be/auto-join") is True))
        results.append(check("auto join connects once", getattr(auto_join_message.author.voice.channel, "connect_calls", 0) == 1, str(getattr(auto_join_message.author.voice.channel, "connect_calls", 0))))
        results.append(check("auto join extracts URL", len(extract_calls) == before_extracts + 1, str(extract_calls)))

        voice_music.ensure_mention_music_voice_client = _fake_ensure_voice
        clear_music_state(guild_id)
        state = get_music_state(guild_id)
        state.current = MusicTrack("current", "https://youtu.be/current", "https://stream.example.com/current", "1001", 100, "https://youtu.be/current")
        play_next_calls.clear()
        before_extracts = len(extract_calls)
        playing_message = FakeMessage(guild_id)
        results.append(check("playing mention link queues without stopping current", await handle_mention_music_links(playing_message, "https://youtu.be/queued") is True))
        results.append(check("playing mention link extracts queued track", len(extract_calls) == before_extracts + 1, str(extract_calls)))
        results.append(check("playing mention link does not start play_next", play_next_calls == [], str(play_next_calls)))
        results.append(check("playing mention link preserves current and appends queue", state.current.title == "current" and len(state.queue) == 1, str(state)))

        extracted_links = extract_music_links_from_text("https://youtu.be/a https://youtu.be/a https://open.spotify.com/track/1234567890123456789012")
        results.append(check("duplicate music links are deduplicated in order", extracted_links == ["https://youtu.be/a", "https://open.spotify.com/track/1234567890123456789012"], str(extracted_links)))
    finally:
        voice_music.get_guild_voice_client = original_get_voice
        voice_music.extract_track_info_with_cookie_fallback = original_extract
        voice_music.play_next_track = original_play_next
        voice_music.enqueue_spotify_link = original_enqueue_spotify
        voice_music.ensure_mention_music_voice_client = original_ensure_voice
        clear_music_state(guild_id)
    return results


def main() -> int:
    results = []
    play_examples = {
        "歌え https://example.com/a": ("music_play", "https://example.com/a"),
        "流して https://example.com/b": ("music_play", "https://example.com/b"),
        "音楽 https://example.com/c": ("music_play", "https://example.com/c"),
        "play https://example.com/d": ("music_play", "https://example.com/d"),
    }
    for command, expected in play_examples.items():
        results.append(check("play command parses: {0}".format(command), parse_music_command(command) == expected, str(parse_music_command(command))))

    control_examples = {
        "スキップ": "music_skip",
        "skip": "music_skip",
        "次": "music_skip",
        "次の曲": "music_skip",
        "一時停止": "music_pause",
        "pause": "music_pause",
        "再開": "music_resume",
        "resume": "music_resume",
        "キュー": "music_queue",
        "queue": "music_queue",
        "再生予定": "music_queue",
        "今何": "music_now",
        "now": "music_now",
        "nowplaying": "music_now",
        "ループ": "music_loop_status",
        "1曲ループ": "music_loop_one",
        "キューループ": "music_loop_queue",
        "ループ解除": "music_loop_off",
        "シャッフル": "music_shuffle",
    }
    for command, expected_action in control_examples.items():
        action, argument = parse_music_command(command)
        results.append(check("control command parses: {0}".format(command), action == expected_action and argument == "", str((action, argument))))

    loop_range_examples = {
        "キューループ 5": ("music_loop_queue", "5"),
        "キューループ　5": ("music_loop_queue", "5"),
        "5曲ループ": ("music_loop_queue", "5"),
    }
    for command, expected in loop_range_examples.items():
        results.append(check("loop range command parses: {0}".format(command), parse_music_command(command) == expected, str(parse_music_command(command))))
    for command in ("5曲ループしてる曲", "この曲ループして", "ループ5回", "5回ループ", "ループ曲5"):
        results.append(check("loop range command does not overmatch: {0}".format(command), parse_music_command(command)[0] is None, str(parse_music_command(command))))

    results.append(check("http url is accepted", is_http_url("https://example.com/watch?v=1")))
    results.append(check("non-url is rejected", not is_http_url("not-a-url")))
    results.append(check("javascript url is rejected", not is_http_url("javascript:alert(1)")))
    results.extend(asyncio.run(run_mention_link_checks()))
    results.append(check("volume command without value parses", parse_music_command("音量") == ("music_volume", ""), str(parse_music_command("音量"))))
    results.append(check("volume command with value parses", parse_music_command("音量 40") == ("music_volume", "40"), str(parse_music_command("音量 40"))))
    results.append(check("volume accepts 0", parse_volume_percent("0") == (0, "")))
    results.append(check("volume accepts 40", parse_volume_percent("40") == (40, "")))
    results.append(check("volume accepts 100", parse_volume_percent("100") == (100, "")))
    results.append(check("volume rejects -1", parse_volume_percent("-1")[0] is None))
    results.append(check("volume rejects 101", parse_volume_percent("101")[0] is None))
    results.append(check("volume rejects text", parse_volume_percent("big")[0] is None))
    results.append(check("music volume default factor is 40 percent", volume_factor(40) == 0.4))
    fake_source = type("FakeSource", (), {"volume": 1.0})()
    fake_voice = type("FakeVoice", (), {"source": fake_source})()
    results.append(check("playing volume can be updated", apply_music_volume_to_voice_client(fake_voice, 25) and fake_source.volume == 0.25))
    original_get_connection = voice_music.get_connection
    try:
        def _failing_connection():
            raise RuntimeError("db unavailable")

        voice_music.get_connection = _failing_connection
        temporary_volume, persisted = save_music_volume_percent("guild-check", 33, MusicState())
        results.append(check("volume save failure still returns temporary value", temporary_volume == 33 and persisted is False, str((temporary_volume, persisted))))
    finally:
        voice_music.get_connection = original_get_connection
    results.append(check("loop off text", loop_status_text(MUSIC_LOOP_OFF) == "ループは無効です。"))
    results.append(check("loop one text", loop_status_text(MUSIC_LOOP_ONE) == "1曲ループ中です。"))
    results.append(check("loop queue text", loop_status_text(MUSIC_LOOP_QUEUE) == "キュー全体をループ中です。"))

    original_cookies_file = os.environ.get(YTDLP_COOKIES_FILE_ENV)
    try:
        os.environ.pop(YTDLP_COOKIES_FILE_ENV, None)
        options_without_cookies = build_ytdl_options()
        results.append(check("yt-dlp keeps playlist disabled", options_without_cookies.get("noplaylist") is True, str(options_without_cookies)))
        results.append(check("yt-dlp uses deno JS runtime config dict", options_without_cookies.get("js_runtimes") == {"deno": {}}, str(options_without_cookies)))
        results.append(check("yt-dlp enables ejs remote component", options_without_cookies.get("remote_components") == ["ejs:github"], str(options_without_cookies)))
        results.append(check("yt-dlp omits cookiefile when env is empty", "cookiefile" not in options_without_cookies, str(options_without_cookies)))

        os.environ[YTDLP_COOKIES_FILE_ENV] = "/app/secrets/youtube-cookies.txt"
        options_with_cookies = build_ytdl_options("12345", copy_cookies=False)
        expected_cookiefile = str(get_ytdlp_cookie_tmp_path("12345"))
        results.append(
            check(
                "yt-dlp copies cookiefile to tmp path",
                options_with_cookies.get("cookiefile") == expected_cookiefile,
                str(options_with_cookies),
            )
        )
        results.append(check("yt-dlp does not use read-only secrets path directly", options_with_cookies.get("cookiefile") != "/app/secrets/youtube-cookies.txt"))
    finally:
        if original_cookies_file is None:
            os.environ.pop(YTDLP_COOKIES_FILE_ENV, None)
        else:
            os.environ[YTDLP_COOKIES_FILE_ENV] = original_cookies_file

    bot_check_error = RuntimeError("Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication.")
    results.append(check("youtube bot check error is detected", is_youtube_cookie_required_error(bot_check_error)))

    state = MusicState()
    first = MusicTrack("一曲目", "https://example.com/1", "https://stream.example.com/1", "111", 125)
    second = MusicTrack("二曲目", "https://example.com/2", "https://stream.example.com/2", "222", None)
    source_track = MusicTrack("ループ曲", "https://example.com/watch", "https://old-stream.example.com/1", "111", 125, "https://example.com/watch")
    loop_track = make_loop_track(source_track)
    results.append(check("loop track requires stream refresh", loop_track.refresh_required and loop_track.stream_url == "", str(loop_track)))

    original_extract = voice_music.extract_track_info
    try:
        def _fake_extract(url, requester_id, guild_id=None, use_cookies=True):
            return MusicTrack("fresh", url, "https://fresh-stream.example.com/1", requester_id, 99, url)

        voice_music.extract_track_info = _fake_extract
        refreshed = asyncio.run(refresh_track_for_playback(loop_track, "guild-check"))
        results.append(
            check(
                "loop refresh updates stream URL and preserves display data",
                refreshed is not None
                and refreshed.stream_url == "https://fresh-stream.example.com/1"
                and refreshed.title == "ループ曲"
                and refreshed.requester_id == "111"
                and refreshed.refresh_required is False,
                str(refreshed),
            )
        )

        def _failing_extract(url, requester_id, guild_id=None, use_cookies=True):
            raise RuntimeError("video unavailable")

        voice_music.extract_track_info = _failing_extract
        failed_refresh = asyncio.run(refresh_track_for_playback(loop_track, "guild-check"))
        results.append(check("loop refresh failure skips only that track", failed_refresh is None, str(failed_refresh)))
    finally:
        voice_music.extract_track_info = original_extract

    state.current = first
    state.queue.append(second)
    queue_text = format_queue(state)
    now_text = format_now_playing(state)
    results.append(check("queue shows current track", "再生中: 一曲目" in queue_text, queue_text))
    results.append(check("queue shows waiting track", "1. 二曲目" in queue_text, queue_text))
    results.append(check("now playing shows requester", "リクエスト: <@111>" in now_text, now_text))
    results.append(check("empty queue message", format_queue(MusicState()) == "キューは空です。"))
    results.append(check("empty now playing message", format_now_playing(MusicState()) == "現在再生中の曲はありません。"))

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
