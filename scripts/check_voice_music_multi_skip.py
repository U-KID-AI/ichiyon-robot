import asyncio
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
    MusicTrack,
    clear_music_state,
    get_music_state,
    parse_music_command,
    parse_skip_count,
    skip_music,
)


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
    def __init__(self):
        self.id = "requester"
        self.bot = False


class FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id


class FakeVoiceClient:
    def __init__(self, playing=True, paused=False, connected=True):
        self._playing = playing
        self._paused = paused
        self._connected = connected
        self.stop_calls = 0
        self.disconnect_calls = 0
        self.channel = type("FakeVoiceChannel", (), {"id": "voice-a"})()
        self.client = type("FakeClient", (), {"loop": None})()

    def is_playing(self):
        return self._playing

    def is_paused(self):
        return self._paused

    def is_connected(self):
        return self._connected

    def stop(self):
        self.stop_calls += 1
        self._playing = False
        self._paused = False

    async def disconnect(self, force=False):
        self.disconnect_calls += 1
        self._connected = False


class FakeMessage:
    def __init__(self, guild_id):
        self.guild = FakeGuild(guild_id)
        self.author = FakeAuthor()
        self.channel = FakeChannel()


def track(title: str, source_type: str = "youtube") -> MusicTrack:
    return MusicTrack(
        title=title,
        webpage_url="https://example.com/{0}".format(title),
        stream_url="https://stream.example.com/{0}".format(title),
        requester_id="requester",
        duration=100,
        source_url="https://example.com/{0}".format(title),
        source_type=source_type,
    )


def setup_state(guild_id: str, current=True, queue_titles=None, loop_mode=MUSIC_LOOP_OFF):
    clear_music_state(guild_id)
    state = get_music_state(guild_id)
    state.current = track("A") if current else None
    for title in queue_titles or []:
        state.queue.append(track(title))
    state.loop_mode = loop_mode
    return state


async def run_skip(guild_id: str, argument: str, voice_client: FakeVoiceClient):
    message = FakeMessage(guild_id)
    original_get_voice = voice_music.get_guild_voice_client
    try:
        voice_music.get_guild_voice_client = lambda guild: voice_client
        handled = await skip_music(message, argument)
        return handled, message
    finally:
        voice_music.get_guild_voice_client = original_get_voice


async def finish_current(guild_id: str, voice_client: FakeVoiceClient, started):
    original_play_next = voice_music.play_next_track

    async def fake_play_next(inner_voice_client, inner_guild_id):
        state = get_music_state(inner_guild_id)
        if state.queue:
            next_track = state.queue.popleft()
            state.current = next_track
            started.append(next_track.title)
            return True
        state.current = None
        started.append("")
        return False

    try:
        voice_music.play_next_track = fake_play_next
        await voice_music._handle_track_finished(voice_client, guild_id, None)
    finally:
        voice_music.play_next_track = original_play_next


def main() -> int:
    results = []
    parse_cases = {
        "スキップ": ("music_skip", ""),
        "skip": ("music_skip", ""),
        "次": ("music_skip", ""),
        "次の曲": ("music_skip", ""),
        "スキップ 5": ("music_skip", "5"),
        "スキップ　5": ("music_skip", "5"),
        "skip 5": ("music_skip", "5"),
        "SKIP 5": ("music_skip", "5"),
        "5曲スキップ": ("music_skip", "5"),
    }
    for command, expected in parse_cases.items():
        results.append(check("parse {0}".format(command), parse_music_command(command) == expected, str(parse_music_command(command))))

    results.append(check("skip count blank defaults to 1", parse_skip_count("") == (1, "")))
    results.append(check("skip count accepts 1", parse_skip_count("1") == (1, "")))
    results.append(check("skip count accepts 100", parse_skip_count("100") == (100, "")))
    results.append(check("skip count rejects 0", parse_skip_count("0")[0] is None))
    results.append(check("skip count rejects negative", parse_skip_count("-1")[0] is None))
    results.append(check("skip count rejects 101", parse_skip_count("101")[0] is None))
    results.append(check("skip count rejects text", parse_skip_count("abc")[0] is None))
    results.append(check("invalid skip abc is still parsed for error", parse_music_command("スキップ abc") == ("music_skip", "abc")))
    results.append(check("normal conversation is not skip", parse_music_command("スキップしたい気分") == (None, "")))

    guild_id = "guild-multi-skip"
    state = setup_state(guild_id, current=True, queue_titles=["B", "C", "D", "E", "F", "G"])
    voice_client = FakeVoiceClient()
    handled, message = asyncio.run(run_skip(guild_id, "5", voice_client))
    results.append(check("multi skip command handles", handled is True))
    results.append(check("current is counted as first skipped track", voice_client.stop_calls == 1 and state.skip_requested is True, str((voice_client.stop_calls, state.skip_requested))))
    results.append(check("N-1 waiting tracks are removed", [item.title for item in state.queue] == ["F", "G"], str([item.title for item in state.queue])))
    results.append(check("multi skip response reports count", any("5曲" in text for text in message.channel.messages), str(message.channel.messages)))
    started = []
    asyncio.run(finish_current(guild_id, voice_client, started))
    results.append(check("N+1 track starts after callback", started == ["F"] and get_music_state(guild_id).current.title == "F", str((started, get_music_state(guild_id).current))))
    results.append(check("remaining queue order is preserved", [item.title for item in get_music_state(guild_id).queue] == ["G"], str([item.title for item in get_music_state(guild_id).queue])))
    results.append(check("skip flag is cleared after callback", get_music_state(guild_id).skip_requested is False))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C"])
    voice_client = FakeVoiceClient()
    handled, message = asyncio.run(run_skip(guild_id, "10", voice_client))
    started = []
    asyncio.run(finish_current(guild_id, voice_client, started))
    results.append(check("over-count skip removes all available tracks", state.current is None and not state.queue, str(state)))
    results.append(check("over-count skip stays in VC", voice_client.disconnect_calls == 0))
    results.append(check("over-count response reports actual count", any("3曲" in text and "空" in text for text in message.channel.messages), str(message.channel.messages)))

    state = setup_state(guild_id, current=False, queue_titles=[])
    voice_client = FakeVoiceClient(playing=False)
    handled, message = asyncio.run(run_skip(guild_id, "3", voice_client))
    results.append(check("empty queue response is clear", any("現在再生中の曲はありません" in text for text in message.channel.messages), str(message.channel.messages)))

    state = setup_state(guild_id, current=False, queue_titles=["A", "B", "C", "D"])
    voice_client = FakeVoiceClient(playing=False)
    play_calls = []
    original_play_next = voice_music.play_next_track

    async def fake_play_next(inner_voice_client, inner_guild_id):
        play_calls.append(inner_guild_id)
        next_track = get_music_state(inner_guild_id).queue.popleft()
        get_music_state(inner_guild_id).current = next_track
        return True

    try:
        voice_music.play_next_track = fake_play_next
        handled, message = asyncio.run(run_skip(guild_id, "2", voice_client))
    finally:
        voice_music.play_next_track = original_play_next
    results.append(check("queue-only temporary state skips from queue head", get_music_state(guild_id).current.title == "C", str(get_music_state(guild_id).current)))
    results.append(check("queue-only temporary state starts next once", play_calls == [guild_id], str(play_calls)))
    results.append(check("queue-only remaining order is preserved", [item.title for item in get_music_state(guild_id).queue] == ["D"], str([item.title for item in get_music_state(guild_id).queue])))

    state = setup_state(guild_id, current=True, queue_titles=["B"], loop_mode=MUSIC_LOOP_ONE)
    voice_client = FakeVoiceClient()
    handled, message = asyncio.run(run_skip(guild_id, "", voice_client))
    started = []
    asyncio.run(finish_current(guild_id, voice_client, started))
    results.append(check("one-track loop does not replay skipped current", started == ["B"] and all(item.title != "A" for item in get_music_state(guild_id).queue), str((started, get_music_state(guild_id).queue))))
    results.append(check("one-track loop setting remains unchanged", get_music_state(guild_id).loop_mode == MUSIC_LOOP_ONE))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C"], loop_mode=MUSIC_LOOP_QUEUE)
    voice_client = FakeVoiceClient()
    handled, message = asyncio.run(run_skip(guild_id, "2", voice_client))
    started = []
    asyncio.run(finish_current(guild_id, voice_client, started))
    titles = [item.title for item in get_music_state(guild_id).queue]
    results.append(check("queue loop rotates skipped current to loop tail", titles == ["A", "B"], str(titles)))
    results.append(check("queue loop skip does not empty queue", not any("音楽キューは空" in text for text in message.channel.messages), str(message.channel.messages)))
    results.append(check("queue loop continues for advanced track", started == ["C"], str(started)))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C"])
    before = (state.current.title, [item.title for item in state.queue])
    voice_client = FakeVoiceClient()
    handled, message = asyncio.run(run_skip(guild_id, "abc", voice_client))
    after = (state.current.title, [item.title for item in state.queue])
    results.append(check("invalid input does not mutate queue", before == after and voice_client.stop_calls == 0, str((before, after))))
    results.append(check("invalid input sends format guidance", any("1～100" in text for text in message.channel.messages), str(message.channel.messages)))

    state = setup_state(guild_id, current=True, queue_titles=["S", "N"], loop_mode=MUSIC_LOOP_OFF)
    state.queue[0] = track("S", source_type="spotify")
    state.queue[1] = track("N", source_type="youtube_n_pull")
    voice_client = FakeVoiceClient()
    handled, message = asyncio.run(run_skip(guild_id, "2", voice_client))
    started = []
    asyncio.run(finish_current(guild_id, voice_client, started))
    results.append(check("spotify-origin queue item is skipped like music", started == ["N"], str(started)))
    results.append(check("youtube-n-pull-origin queue item can become next", get_music_state(guild_id).current.source_type == "youtube_n_pull", str(get_music_state(guild_id).current)))

    voice_audio_source = (ROOT_DIR / "bot" / "services" / "voice_audio.py").read_text(encoding="utf-8")
    results.append(check("foreground audio service is not modified by multi skip", "play_reaction_audio" in voice_audio_source and "_MUSIC_STATES" not in voice_audio_source))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C"])
    voice_client = FakeVoiceClient()
    handled, message = asyncio.run(run_skip(guild_id, "", voice_client))
    results.append(check("existing one-skip response remains compatible", message.channel.messages == ["スキップしました。"], str(message.channel.messages)))

    ok_count = sum(1 for result in results if result)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
