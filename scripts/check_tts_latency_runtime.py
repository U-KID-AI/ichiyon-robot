import io
import math
import os
import sys
import wave
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


import bot.services.voice.tts as tts


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload = payload or {}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return dict(self._payload)


class FakeClient:
    def __init__(self, wav_bytes):
        self.wav_bytes = wav_bytes
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path == "/audio_query":
            return FakeResponse({"speedScale": 1.0, "pitchScale": 0.0, "volumeScale": 1.0})
        if path == "/synthesis":
            return FakeResponse(content=self.wav_bytes)
        raise AssertionError(path)


def wav_bytes(sample_rate=24000, channels=1, sample_width=2, frames=2400, amplitude=8000):
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        payload = bytearray()
        for index in range(frames):
            value = int(math.sin(index / 12.0) * amplitude)
            payload.extend(value.to_bytes(2, "little", signed=True))
        wav.writeframes(bytes(payload))
    return out.getvalue()


def main() -> int:
    results = []
    fake = FakeClient(wav_bytes())
    original_client = tts._HTTP_CLIENT
    original_base = tts._HTTP_CLIENT_BASE_URL
    original_timeout = tts._HTTP_CLIENT_TIMEOUT
    original_get_client = tts.get_voicevox_client
    try:
        tts.get_voicevox_client = lambda: fake
        settings = {
            "speaker_id": 3,
            "speed_scale": 1.0,
        }
        speech = tts.synthesize_voicevox_to_pcm("hello", settings, 0.0)
        results.append(check("audio_query called once", [call[0] for call in fake.calls].count("/audio_query") == 1, str(fake.calls)))
        results.append(check("synthesis called once", [call[0] for call in fake.calls].count("/synthesis") == 1, str(fake.calls)))
        results.append(check("voicevox volumeScale stays baseline", fake.calls[1][1]["json"]["volumeScale"] == 1.0, str(fake.calls[1][1]["json"])))
        results.append(check("pcm is 48k stereo frame aligned enough", len(speech.pcm) > 0 and len(speech.pcm) % 4 == 0, str(len(speech.pcm))))
        results.append(check("pcm metrics are available", speech.peak > 0 and speech.rms > 0, "peak={0} rms={1}".format(speech.peak, speech.rms)))
        results.append(check("timings are measured", speech.audio_query_ms >= 0 and speech.synthesis_ms >= 0 and speech.pcm_convert_ms >= 0))

        source = tts.PCMBytesAudioSource(speech.pcm)
        first = source.read()
        results.append(check("pcm source emits discord frame size", len(first) == 3840, str(len(first))))
        source.cleanup()
        results.append(check("cleanup clears pcm", source.read() == b""))

        source_text = Path("bot/services/voice/tts.py").read_text(encoding="utf-8")
        results.append(check("tts path no longer uses temporary wav file", "NamedTemporaryFile" not in source_text and "cleanup_tts_file" not in source_text))
        results.append(check("tts path no longer starts ffmpeg", "FFmpegPCMAudio" not in source_text))
        results.append(check("voicevox default timeout allows slow synthesis", tts.VOICEVOX_DEFAULT_TIMEOUT_SECONDS >= 30, str(tts.VOICEVOX_DEFAULT_TIMEOUT_SECONDS)))

        os.environ["VOICEVOX_ENGINE_URL"] = "http://voicevox-engine:50021"
        os.environ["VOICEVOX_TIMEOUT_SECONDS"] = "10"
        tts.get_voicevox_client = original_get_client
        tts._HTTP_CLIENT = None
        client1 = tts.get_voicevox_client()
        client2 = tts.get_voicevox_client()
        results.append(check("http client is reused", client1 is client2))
        results.append(check("voicevox client ignores environment proxy", "trust_env=False" in source_text))
    finally:
        if tts._HTTP_CLIENT is not None:
            try:
                tts._HTTP_CLIENT.close()
            except Exception:
                pass
        tts._HTTP_CLIENT = original_client
        tts._HTTP_CLIENT_BASE_URL = original_base
        tts._HTTP_CLIENT_TIMEOUT = original_timeout
        tts.get_voicevox_client = original_get_client

    print("tts latency checks: {0}/{1}".format(sum(1 for value in results if value), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
