import audioop
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot.repositories.tts_settings import TTSSettingsRepository
from bot.services.voice.mixer import PCM_FRAME_BYTES, get_mixer
from bot.services.voice.session import clear_voice_runtime_state


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakePCMSource:
    def __init__(self, frames):
        self.frames = list(frames)

    def is_opus(self):
        return False

    def read(self):
        if not self.frames:
            return b""
        return self.frames.pop(0)

    def cleanup(self):
        pass


class FakeCursor:
    def __init__(self):
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, _sql, params):
        self.params = params

    def fetchone(self):
        return None


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj


def frame(value: int) -> bytes:
    sample = int(value).to_bytes(2, "little", signed=True)
    return sample * (PCM_FRAME_BYTES // 2)


def peak(pcm: bytes) -> int:
    return audioop.max(pcm, 2)


def rms(pcm: bytes) -> int:
    return audioop.rms(pcm, 2)


def main() -> int:
    results = []
    guild_id = "guild-tts-volume"
    clear_voice_runtime_state(guild_id)
    mixer = get_mixer(guild_id)
    mixer.configure_ducking(__import__("bot.services.voice.ducking", fromlist=["DuckingConfig"]).DuckingConfig(enabled=False))

    mixer.set_tts_volume(0.0)
    mixer.set_tts_source(FakePCMSource([frame(12000)]), lambda error: None)
    zero = mixer.read()
    results.append(check("tts volume 0 is silent", peak(zero) == 0 and rms(zero) == 0, "peak={0} rms={1}".format(peak(zero), rms(zero))))

    clear_voice_runtime_state(guild_id)
    mixer = get_mixer(guild_id)
    mixer.set_tts_volume(0.5)
    mixer.set_tts_source(FakePCMSource([frame(12000)]), lambda error: None)
    half = mixer.read()
    clear_voice_runtime_state(guild_id)
    mixer = get_mixer(guild_id)
    mixer.set_tts_volume(1.0)
    mixer.set_tts_source(FakePCMSource([frame(12000)]), lambda error: None)
    full = mixer.read()
    results.append(check("tts volume 50 lower than 100", peak(half) < peak(full), "half={0} full={1}".format(peak(half), peak(full))))
    results.append(check("tts volume 100 keeps full gain", peak(full) == 12000, "peak={0}".format(peak(full))))
    results.append(check("tts volume 100 is not 0.01 gain", peak(full) > 10000, "peak={0}".format(peak(full))))

    clear_voice_runtime_state(guild_id)
    mixer = get_mixer(guild_id)
    mixer.set_music_volume(0.4)
    mixer.set_tts_volume(1.0)
    mixer.configure_ducking(__import__("bot.services.voice.ducking", fromlist=["DuckingConfig"]).DuckingConfig(enabled=False))
    mixer.set_music_source(FakePCMSource([frame(10000)]), lambda error: None)
    mixer.set_tts_source(FakePCMSource([frame(12000)]), lambda error: None)
    mixed = mixer.read()
    results.append(check("music plus tts does not halve tts", peak(mixed) == 16000, "peak={0}".format(peak(mixed))))
    results.append(check("music and tts settings are independent", peak(mixed) != 12000 // 2, "peak={0}".format(peak(mixed))))

    clear_voice_runtime_state(guild_id)
    mixer = get_mixer(guild_id)
    mixer.set_music_volume(1.0)
    mixer.set_tts_volume(1.0)
    mixer.set_music_source(FakePCMSource([frame(30000)]), lambda error: None)
    mixer.set_tts_source(FakePCMSource([frame(30000)]), lambda error: None)
    clipped = mixer.read()
    results.append(check("int16 overflow is clamped", int.from_bytes(clipped[:2], "little", signed=True) == 32767))

    clear_voice_runtime_state(guild_id)
    mixer = get_mixer(guild_id)
    mixer.set_tts_volume(1.0)
    mixer.set_tts_source(FakePCMSource([frame(1234)]), lambda error: None)
    unclipped = mixer.read()
    results.append(check("unclipped sample amplitude is preserved", peak(unclipped) == 1234, "peak={0}".format(peak(unclipped))))

    conn = FakeConnection()
    TTSSettingsRepository(conn).upsert(guild_id, {"tts_volume_percent": 0})
    params = conn.cursor_obj.params
    results.append(check("repository persists zero volume", params is not None and params[6] == 0, "param={0}".format(None if params is None else params[6])))
    TTSSettingsRepository(conn).upsert(guild_id, {"tts_volume_percent": 100})
    params = conn.cursor_obj.params
    results.append(check("repository persists max volume", params is not None and params[6] == 100, "param={0}".format(None if params is None else params[6])))

    print("tts volume checks: {0}/{1}".format(sum(1 for value in results if value), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
