import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import voice_audio
from bot.services.voice import mixer as mixer_module
from bot.services.voice.mixer import PCM_FRAME_BYTES, clear_mixer, get_mixer
from bot.services.voice.session import voice_state_key


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def frame(value=1):
    return bytes([value % 256]) * PCM_FRAME_BYTES


class FakePCMSource:
    def __init__(self, frames=None, error_on_eof=False):
        self.frames = list(frames or [])
        self.error_on_eof = error_on_eof
        self.cleaned = 0

    def read(self):
        if self.frames:
            return self.frames.pop(0)
        if self.error_on_eof:
            raise RuntimeError("fake playback error")
        return b""

    def cleanup(self):
        self.cleaned += 1


class FakeVoiceClient:
    def __init__(self):
        self.source = None
        self.play_calls = 0
        self.stopped = 0
        self.channel = type("Channel", (), {"id": "voice-a"})()

    def is_connected(self):
        return True

    def is_playing(self):
        return self.source is not None and not getattr(self.source, "closed", False)

    def is_paused(self):
        return False

    def play(self, source, after=None):
        self.source = source
        self.play_calls += 1

    def stop(self):
        self.stopped += 1
        if self.source is not None:
            cleanup = getattr(self.source, "cleanup", None)
            if callable(cleanup):
                cleanup()
        self.source = None


def reset(guild_id):
    key = voice_state_key(guild_id)
    voice_audio._FOREGROUND_QUEUES.pop(key, None)
    voice_audio._FOREGROUND_ACTIVE.pop(key, None)
    clear_mixer(guild_id)


def drain_mixer(guild_id, limit=50):
    mix = get_mixer(guild_id)
    reads = []
    for _ in range(limit):
        data = mix.read()
        reads.append(data)
        if data == b"":
            break
    return reads


def make_audio_file():
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(b"fake")
    tmp.close()
    return Path(tmp.name)


def patch_ffmpeg(frames_factory):
    original = voice_audio.discord.FFmpegPCMAudio
    voice_audio.discord.FFmpegPCMAudio = lambda _path: FakePCMSource(frames_factory())
    return original


def patch_logs():
    original = voice_audio.log_voice_audio
    events = []

    def fake_log(action, guild_id, channel_id, filename=None, reaction_type=None, reaction_key=None, skipped_reason=None):
        events.append((action, guild_id, reaction_key, skipped_reason))

    voice_audio.log_voice_audio = fake_log
    return original, events


def enqueue(fake_voice, guild_id, path, key):
    return voice_audio.enqueue_foreground_audio(fake_voice, path, guild_id, "voice-a", 50, "soundboard", str(key))


def starts(events):
    return [event for event in events if event[0] == "play_start"]


def completes(events):
    return [event for event in events if event[0] == "play_complete"]


def active(guild_id):
    return bool(voice_audio._FOREGROUND_ACTIVE.get(voice_state_key(guild_id)))


def queue_len(guild_id):
    return len(voice_audio._FOREGROUND_QUEUES.get(voice_state_key(guild_id), []))


def check_single_se():
    guild_id = "foreground-single"
    reset(guild_id)
    fake_voice = FakeVoiceClient()
    path = make_audio_file()
    original_ffmpeg = patch_ffmpeg(lambda: [frame(1)])
    original_log, events = patch_logs()
    try:
        enqueue(fake_voice, guild_id, path, 1)
        drain_mixer(guild_id)
        return len(starts(events)) == 1 and len(completes(events)) == 1 and not active(guild_id) and queue_len(guild_id) == 0
    finally:
        voice_audio.discord.FFmpegPCMAudio = original_ffmpeg
        voice_audio.log_voice_audio = original_log
        reset(guild_id)
        path.unlink(missing_ok=True)


def check_chained_se(count=3):
    guild_id = "foreground-chain-{0}".format(count)
    reset(guild_id)
    fake_voice = FakeVoiceClient()
    path = make_audio_file()
    original_ffmpeg = patch_ffmpeg(lambda: [frame(1)])
    original_log, events = patch_logs()
    try:
        for index in range(1, count + 1):
            enqueue(fake_voice, guild_id, path, index)
        drain_mixer(guild_id, limit=80)
        started_keys = [event[2] for event in starts(events)]
        completed_keys = [event[2] for event in completes(events)]
        return (
            started_keys == [str(index) for index in range(1, count + 1)]
            and completed_keys == [str(index) for index in range(1, count + 1)]
            and not active(guild_id)
            and queue_len(guild_id) == 0
            and not get_mixer(guild_id).closed
        )
    finally:
        voice_audio.discord.FFmpegPCMAudio = original_ffmpeg
        voice_audio.log_voice_audio = original_log
        reset(guild_id)
        path.unlink(missing_ok=True)


def check_callback_sets_next_source_does_not_eof():
    guild_id = "mixer-callback-next"
    reset(guild_id)
    mix = get_mixer(guild_id)
    after_calls = []

    def after(_error):
        after_calls.append("first")
        mix.set_tts_source(FakePCMSource([frame(9)]), lambda error: after_calls.append("second"))

    mix.set_tts_source(FakePCMSource([]), after)
    first_read = mix.read()
    second_read = mix.read()
    third_read = mix.read()
    ok = first_read == b"\x00" * PCM_FRAME_BYTES and second_read != b"" and third_read == b"" and after_calls == ["first", "second"]
    reset(guild_id)
    return ok


def check_real_eof_when_no_sources():
    guild_id = "mixer-real-eof"
    reset(guild_id)
    mix = get_mixer(guild_id)
    data = mix.read()
    ok = data == b"" and mix.closed is True
    reset(guild_id)
    return ok


def check_active_recovery_after_missing_tts_source():
    guild_id = "foreground-active-recovery"
    reset(guild_id)
    fake_voice = FakeVoiceClient()
    path = make_audio_file()
    key = voice_state_key(guild_id)
    voice_audio._FOREGROUND_ACTIVE[key] = True
    original_ffmpeg = patch_ffmpeg(lambda: [frame(1)])
    original_log, events = patch_logs()
    try:
        enqueue(fake_voice, guild_id, path, 1)
        drain_mixer(guild_id)
        return len(starts(events)) == 1 and len(completes(events)) == 1 and not active(guild_id)
    finally:
        voice_audio.discord.FFmpegPCMAudio = original_ffmpeg
        voice_audio.log_voice_audio = original_log
        reset(guild_id)
        path.unlink(missing_ok=True)


def check_error_source_advances_queue():
    guild_id = "foreground-error-advance"
    reset(guild_id)
    fake_voice = FakeVoiceClient()
    path = make_audio_file()
    factories = [
        lambda: FakePCMSource([], error_on_eof=True),
        lambda: FakePCMSource([frame(2)]),
    ]

    def next_source(_path):
        factory = factories.pop(0) if factories else (lambda: FakePCMSource([frame(3)]))
        return factory()

    original_ffmpeg = voice_audio.discord.FFmpegPCMAudio
    voice_audio.discord.FFmpegPCMAudio = next_source
    original_log, events = patch_logs()
    try:
        enqueue(fake_voice, guild_id, path, 1)
        enqueue(fake_voice, guild_id, path, 2)
        drain_mixer(guild_id)
        return len(starts(events)) == 2 and [event[2] for event in completes(events)] == ["2"] and not active(guild_id) and queue_len(guild_id) == 0
    finally:
        voice_audio.discord.FFmpegPCMAudio = original_ffmpeg
        voice_audio.log_voice_audio = original_log
        reset(guild_id)
        path.unlink(missing_ok=True)


def check_music_continues_with_foreground_queue():
    guild_id = "foreground-with-music"
    reset(guild_id)
    fake_voice = FakeVoiceClient()
    path = make_audio_file()
    mix = get_mixer(guild_id)
    music_after = []
    mix.set_music_source(FakePCMSource([frame(2)] * 10), lambda error: music_after.append(error))
    mixer_module.ensure_mixer_playing(fake_voice, guild_id)
    original_ffmpeg = patch_ffmpeg(lambda: [frame(1)])
    original_log, events = patch_logs()
    try:
        enqueue(fake_voice, guild_id, path, 1)
        enqueue(fake_voice, guild_id, path, 2)
        for _ in range(6):
            mix.read()
        return len(starts(events)) == 2 and len(completes(events)) == 2 and mix.music_source is not None and not music_after
    finally:
        voice_audio.discord.FFmpegPCMAudio = original_ffmpeg
        voice_audio.log_voice_audio = original_log
        reset(guild_id)
        path.unlink(missing_ok=True)


def main():
    results = []
    results.append(check("single SE starts completes and clears active", check_single_se()))
    results.append(check("two SE chain starts and completes in FIFO order", check_chained_se(2)))
    results.append(check("three SE chain starts and completes in FIFO order", check_chained_se(3)))
    results.append(check("same asset can be queued three times", check_chained_se(3)))
    results.append(check("callback-installed next source does not close mixer", check_callback_sets_next_source_does_not_eof()))
    results.append(check("mixer returns EOF only when no sources remain", check_real_eof_when_no_sources()))
    results.append(check("active stuck state recovers on next enqueue", check_active_recovery_after_missing_tts_source()))
    results.append(check("foreground source error advances to next queue item", check_error_source_advances_queue()))
    results.append(check("music continues while foreground queue drains", check_music_continues_with_foreground_queue()))
    print("summary: {0}/{1} OK".format(sum(1 for value in results if value), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
