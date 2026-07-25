import audioop
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot.services.voice.ducking import DuckingConfig
from bot.services.voice.mixer import PCM_FRAME_BYTES, ensure_mixer_playing, get_mixer
from bot.services.voice.session import clear_voice_runtime_state
from bot.services.voice_music import stop_music_source


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakePCMSource:
    def __init__(self, frames):
        self.frames = list(frames)
        self.cleanup_calls = 0

    def is_opus(self):
        return False

    def read(self):
        if not self.frames:
            return b""
        return self.frames.pop(0)

    def cleanup(self):
        self.cleanup_calls += 1


class FakeVoiceClient:
    def __init__(self):
        self.source = None
        self.play_calls = 0
        self.stop_calls = 0
        self._playing = False
        self._paused = False

    def is_playing(self):
        return self._playing

    def is_paused(self):
        return self._paused

    def play(self, source):
        self.source = source
        self._playing = True
        self.play_calls += 1

    def stop(self):
        self._playing = False
        self.stop_calls += 1


def frame(value: int) -> bytes:
    sample = int(value).to_bytes(2, "little", signed=True)
    return sample * (PCM_FRAME_BYTES // 2)


def sample(pcm: bytes) -> int:
    return int.from_bytes(pcm[:2], "little", signed=True)


def main() -> int:
    results = []
    guild_id = "guild-simultaneous"
    clear_voice_runtime_state(guild_id)
    voice_client = FakeVoiceClient()
    mixer = get_mixer(guild_id)
    mixer.set_music_volume(0.4)
    mixer.set_tts_volume(1.0)
    mixer.configure_ducking(DuckingConfig(enabled=False))
    music_after = []
    tts_after = []
    mixer.set_music_source(FakePCMSource([frame(10000), frame(10000), frame(10000)]), lambda error: music_after.append(error))
    ensure_mixer_playing(voice_client, guild_id)
    results.append(check("voice client receives only mixer source", voice_client.source is mixer and voice_client.play_calls == 1))

    first_music = voice_client.source.read()
    results.append(check("music only plays through mixer", sample(first_music) == 4000, str(sample(first_music))))

    mixer.set_tts_source(FakePCMSource([frame(12000)]), lambda error: tts_after.append(error))
    ensure_mixer_playing(voice_client, guild_id)
    mixed = voice_client.source.read()
    results.append(check("music and tts are mixed in final pcm", sample(mixed) == 16000, str(sample(mixed))))
    results.append(check("tts start does not replace voice source", voice_client.source is mixer and voice_client.play_calls == 1))

    after_tts = voice_client.source.read()
    results.append(check("music continues after tts eof", sample(after_tts) == 4000, str(sample(after_tts))))
    results.append(check("tts eof callback fired", tts_after == [None], str(tts_after)))
    results.append(check("music did not end with tts", music_after == [], str(music_after)))

    mixer.set_tts_source(FakePCMSource([frame(12000), frame(12000)]), lambda error: tts_after.append(error))
    stopped = stop_music_source(voice_client, guild_id, call_after=True)
    results.append(check("music skip clears only music bus", stopped and mixer.music_source is None and mixer.tts_source is not None))
    tts_only = voice_client.source.read()
    results.append(check("tts remains after music skip", sample(tts_only) == 12000, str(sample(tts_only))))

    clear_voice_runtime_state(guild_id)
    mixer = get_mixer(guild_id)
    mixer.set_music_volume(0.4)
    mixer.set_tts_volume(1.0)
    mixer.configure_ducking(DuckingConfig(enabled=True, music_gain=0.25, attack_ms=0, release_ms=0))
    mixer.set_music_source(FakePCMSource([frame(10000)]), lambda error: None)
    mixer.set_tts_source(FakePCMSource([frame(12000)]), lambda error: None)
    ducked = mixer.read()
    results.append(check("ducking affects music but not tts", sample(ducked) == 13000, str(sample(ducked))))

    print("music tts simultaneous checks: {0}/{1}".format(sum(1 for value in results if value), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
