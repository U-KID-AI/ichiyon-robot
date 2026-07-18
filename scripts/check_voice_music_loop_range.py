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
    play_next_track,
    set_music_loop,
    shuffle_music_queue,
    skip_music,
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
        self.channel = type("FakeVoiceChannel", (), {"id": "voice-loop"})()
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


class FakeMessage:
    def __init__(self, guild_id):
        self.guild = FakeGuild(guild_id)
        self.author = FakeAuthor()
        self.channel = FakeChannel()


def track(title: str) -> MusicTrack:
    return MusicTrack(
        title=title,
        webpage_url="https://example.com/{0}".format(title),
        stream_url="https://stream.example.com/{0}".format(title),
        requester_id="requester",
        duration=100,
        source_url="https://example.com/{0}".format(title),
    )


def setup_state(guild_id: str, current=True, queue_titles=None, loop_mode=MUSIC_LOOP_OFF):
    clear_music_state(guild_id)
    state = get_music_state(guild_id)
    state.current = track("A") if current else None
    for title in queue_titles or []:
        state.queue.append(track(title))
    state.loop_mode = loop_mode
    return state


async def finish_current(guild_id: str, voice_client: FakeVoiceClient, started):
    original_play_next = voice_music.play_next_track

    async def fake_play_next(inner_voice_client, inner_guild_id):
        state = get_music_state(inner_guild_id)
        queue = state.loop_queue if state.loop_mode == MUSIC_LOOP_QUEUE and state.loop_range_size is not None and state.loop_queue else state.queue
        if queue:
            next_track = queue.popleft()
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


async def run_skip(guild_id: str, argument: str, voice_client: FakeVoiceClient):
    message = FakeMessage(guild_id)
    original_get_voice = voice_music.get_guild_voice_client
    try:
        voice_music.get_guild_voice_client = lambda guild: voice_client
        handled = await skip_music(message, argument)
        return handled, message
    finally:
        voice_music.get_guild_voice_client = original_get_voice


async def run_set_loop(guild_id: str, mode: str, argument: str = ""):
    message = FakeMessage(guild_id)
    handled = await set_music_loop(message, mode, argument)
    return handled, message


async def run_shuffle(guild_id: str):
    message = FakeMessage(guild_id)
    handled = await shuffle_music_queue(message)
    return handled, message


def titles(items):
    return [item.title for item in items]


def main() -> int:
    results = []

    parse_cases = {
        "キューループ": ("music_loop_queue", ""),
        "キューループ 5": ("music_loop_queue", "5"),
        "キューループ　5": ("music_loop_queue", "5"),
        "5曲ループ": ("music_loop_queue", "5"),
    }
    for command, expected in parse_cases.items():
        results.append(check("parse {0}".format(command), parse_music_command(command) == expected, str(parse_music_command(command))))

    for command in ("5曲ループしてる曲", "この曲ループして", "ループ5回", "5回ループ", "ループ曲5"):
        results.append(check("does not misparse {0}".format(command), parse_music_command(command)[0] is None, str(parse_music_command(command))))

    guild_id = "guild-loop-range"
    state = setup_state(guild_id, current=True, queue_titles=["B", "C", "D", "E", "F", "G"])
    handled, message = asyncio.run(run_set_loop(guild_id, MUSIC_LOOP_QUEUE, "5"))
    results.append(check("fixed range loop command handles", handled is True))
    results.append(check("fixed range includes current plus four waiting tracks", state.loop_range_size == 5 and titles(state.loop_queue) == ["B", "C", "D", "E"], str((state.loop_range_size, titles(state.loop_queue)))))
    results.append(check("fixed range leaves outside queue untouched", titles(state.queue) == ["F", "G"], str(titles(state.queue))))

    started = []
    asyncio.run(finish_current(guild_id, FakeVoiceClient(), started))
    results.append(check("natural finish in fixed loop starts next loop track", started == ["B"] and state.current.title == "B", str((started, state.current))))
    results.append(check("natural finish rotates finished current into loop target", titles(state.loop_queue) == ["C", "D", "E", "A"], str(titles(state.loop_queue))))
    results.append(check("natural finish keeps outside queue outside", titles(state.queue) == ["F", "G"], str(titles(state.queue))))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C", "D", "E", "F", "G"])
    asyncio.run(run_set_loop(guild_id, MUSIC_LOOP_QUEUE, "5"))
    voice_client = FakeVoiceClient()
    handled, message = asyncio.run(run_skip(guild_id, "3", voice_client))
    started = []
    asyncio.run(finish_current(guild_id, voice_client, started))
    results.append(check("fixed queue loop skip advances within target", started == ["D"], str(started)))
    results.append(check("fixed queue loop skip rotates skipped tracks to target tail", titles(state.loop_queue) == ["E", "A", "B", "C"], str(titles(state.loop_queue))))
    results.append(check("fixed queue loop skip keeps outside queue", titles(state.queue) == ["F", "G"], str(titles(state.queue))))
    results.append(check("fixed queue loop skip response is not empty queue", any("キューループ内で3曲先へ進みました。" in text for text in message.channel.messages), str(message.channel.messages)))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C", "D", "E"], loop_mode=MUSIC_LOOP_QUEUE)
    voice_client = FakeVoiceClient()
    handled, message = asyncio.run(run_skip(guild_id, "5", voice_client))
    started = []
    asyncio.run(finish_current(guild_id, voice_client, started))
    results.append(check("full queue loop skip equal size returns to current", started == ["A"], str(started)))
    results.append(check("full queue loop skip keeps all loop tracks", titles(state.queue) == ["B", "C", "D", "E"], str(titles(state.queue))))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C"])
    handled, message = asyncio.run(run_set_loop(guild_id, MUSIC_LOOP_QUEUE, "5"))
    results.append(check("loop range clamps to available count", state.loop_range_size == 3 and any("3曲" in text for text in message.channel.messages), str((state.loop_range_size, message.channel.messages))))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C"])
    before = (state.loop_mode, titles(state.queue), titles(state.loop_queue))
    handled, message = asyncio.run(run_set_loop(guild_id, MUSIC_LOOP_QUEUE, "0"))
    after = (state.loop_mode, titles(state.queue), titles(state.loop_queue))
    results.append(check("invalid loop count does not mutate state", before == after, str((before, after))))
    results.append(check("invalid loop count reports range", message.channel.messages == ["ループする曲数は1～100曲で指定してください。"], str(message.channel.messages)))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C", "D"])
    asyncio.run(run_set_loop(guild_id, MUSIC_LOOP_QUEUE, "3"))
    asyncio.run(run_set_loop(guild_id, MUSIC_LOOP_OFF))
    results.append(check("loop off merges fixed target before outside queue", state.loop_mode == MUSIC_LOOP_OFF and titles(state.queue) == ["B", "C", "D"], str((state.loop_mode, titles(state.queue)))))
    results.append(check("loop off clears range state", not state.loop_queue and state.loop_range_size is None, str((state.loop_queue, state.loop_range_size))))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C", "D", "E"])
    asyncio.run(run_set_loop(guild_id, MUSIC_LOOP_QUEUE, "3"))
    original_shuffle = voice_music.random.shuffle
    try:
        voice_music.random.shuffle = lambda values: values.reverse()
        asyncio.run(run_shuffle(guild_id))
    finally:
        voice_music.random.shuffle = original_shuffle
    results.append(check("shuffle in fixed loop only shuffles loop target", titles(state.loop_queue) == ["C", "B"] and titles(state.queue) == ["D", "E"], str((titles(state.loop_queue), titles(state.queue)))))

    state = setup_state(guild_id, current=True, queue_titles=["B", "C"])
    asyncio.run(run_set_loop(guild_id, MUSIC_LOOP_QUEUE, "1"))
    results.append(check("queue loop 1 is one-track loop equivalent", state.loop_mode == MUSIC_LOOP_ONE and not state.loop_queue and titles(state.queue) == ["B", "C"], str((state.loop_mode, titles(state.queue)))))

    state = setup_state(guild_id, current=False, queue_titles=["F", "G"], loop_mode=MUSIC_LOOP_QUEUE)
    state.loop_range_size = 3
    played = asyncio.run(play_next_track(FakeVoiceClient(playing=False), guild_id))
    results.append(check("fixed loop does not auto-play outside queue when target is empty", played is False and state.current is None and titles(state.queue) == ["F", "G"], str((played, state.current, titles(state.queue)))))

    ok_count = sum(1 for result in results if result)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
