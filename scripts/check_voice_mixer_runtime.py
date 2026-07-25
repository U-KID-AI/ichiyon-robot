import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot.services.voice.ducking import DuckingConfig
from bot.services.voice.mixer import PCM_FRAME_BYTES, clear_mixer, get_mixer
from bot.services.voice.session import clear_voice_runtime_state


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


def frame(value: int) -> bytes:
    sample = int(value).to_bytes(2, "little", signed=True)
    return sample * (PCM_FRAME_BYTES // 2)


def main() -> int:
    results = []
    guild = "guild-mixer"
    other_guild = "guild-mixer-other"
    clear_voice_runtime_state(guild)
    clear_voice_runtime_state(other_guild)
    clear_mixer(guild)
    clear_mixer(other_guild)

    music_after = []
    tts_after = []
    mixer = get_mixer(guild)
    mixer.set_music_volume(1.0)
    mixer.set_tts_volume(1.0)
    mixer.set_music_source(FakePCMSource([frame(1000)]), lambda error: music_after.append(error))
    data = mixer.read()
    results.append(check("music-only frame length", len(data) == PCM_FRAME_BYTES))
    results.append(check("music after on eof", not music_after))
    results.append(check("music eof callback", mixer.read() == b"" and music_after == [None]))

    mixer = get_mixer(guild)
    mixer.set_tts_source(FakePCMSource([frame(1000)]), lambda error: tts_after.append(error))
    data = mixer.read()
    results.append(check("tts-only frame length", len(data) == PCM_FRAME_BYTES))
    results.append(check("tts eof callback", mixer.read() == b"" and tts_after == [None]))

    mixer = get_mixer(guild)
    mixer.set_music_volume(1.0)
    mixer.set_tts_volume(1.0)
    mixer.configure_ducking(DuckingConfig(enabled=False))
    mixer.set_music_source(FakePCMSource([frame(1000)]), lambda error: None)
    mixer.set_tts_source(FakePCMSource([frame(1000)]), lambda error: None)
    mixed = mixer.read()
    expected = frame(2000)
    results.append(check("mix adds buses", mixed[:20] == expected[:20]))

    music_after = []
    tts_after = []
    mixer = get_mixer(guild)
    mixer.set_music_source(FakePCMSource([frame(500), frame(500)]), lambda error: music_after.append(error))
    mixer.set_tts_source(FakePCMSource([frame(500), frame(500)]), lambda error: tts_after.append(error))
    mixer.clear_tts(call_after=False)
    results.append(check("clear tts keeps music", mixer.tts_source is None and mixer.music_source is not None and not tts_after))
    mixer.clear_music(call_after=True)
    results.append(check("clear music callback", mixer.music_source is None and music_after == [None]))

    other = get_mixer(other_guild)
    results.append(check("mixer guild separation", mixer is not other))

    mixer = get_mixer(guild)
    mixer.set_music_source(FakePCMSource([frame(30000)]), lambda error: None)
    mixer.set_tts_source(FakePCMSource([frame(30000)]), lambda error: None)
    clipped = mixer.read()
    first_sample = int.from_bytes(clipped[:2], "little", signed=True)
    results.append(check("mix clamps pcm", first_sample == 32767))

    print("voice mixer checks: {0}/{1}".format(sum(1 for value in results if value), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
