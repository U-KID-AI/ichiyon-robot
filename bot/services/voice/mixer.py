import audioop
import threading
from typing import Callable, Dict, Optional

import discord

from bot.services.voice.ducking import DuckingConfig, DuckingEnvelope
from bot.services.voice.session import voice_state_key


PCM_FRAME_BYTES = 3840
SAMPLE_WIDTH = 2
_MIXERS: Dict[str, "VoiceMixerAudioSource"] = {}


def _silence() -> bytes:
    return b"\x00" * PCM_FRAME_BYTES


class VoiceMixerAudioSource(discord.AudioSource):
    def __init__(self, key: str) -> None:
        self.key = key
        self._lock = threading.RLock()
        self.music_source: Optional[discord.AudioSource] = None
        self.tts_source: Optional[discord.AudioSource] = None
        self.music_after: Optional[Callable[[Optional[Exception]], None]] = None
        self.tts_after: Optional[Callable[[Optional[Exception]], None]] = None
        self.music_volume = 0.4
        self.tts_volume = 0.5
        self.closed = False
        self.ducking = DuckingEnvelope(DuckingConfig())

    def is_opus(self) -> bool:
        return False

    def configure_ducking(self, config: DuckingConfig) -> None:
        with self._lock:
            self.ducking = DuckingEnvelope(config)

    def set_music_volume(self, volume: float) -> None:
        with self._lock:
            self.music_volume = max(0.0, min(1.0, float(volume)))

    def set_tts_volume(self, volume: float) -> None:
        with self._lock:
            self.tts_volume = max(0.0, min(1.0, float(volume)))

    def set_music_source(self, source: discord.AudioSource, after: Callable[[Optional[Exception]], None]) -> None:
        with self._lock:
            self._cleanup_source(self.music_source)
            self.music_source = source
            self.music_after = after
            self.closed = False

    def set_tts_source(self, source: discord.AudioSource, after: Callable[[Optional[Exception]], None]) -> None:
        with self._lock:
            self._cleanup_source(self.tts_source)
            self.tts_source = source
            self.tts_after = after
            self.closed = False
            self.ducking.set_tts_active(True)

    def clear_music(self, call_after: bool = False) -> None:
        after = None
        with self._lock:
            self._cleanup_source(self.music_source)
            self.music_source = None
            after = self.music_after if call_after else None
            self.music_after = None
        if after:
            after(None)

    def clear_tts(self, call_after: bool = False) -> None:
        after = None
        with self._lock:
            self._cleanup_source(self.tts_source)
            self.tts_source = None
            after = self.tts_after if call_after else None
            self.tts_after = None
            self.ducking.set_tts_active(False)
        if after:
            after(None)

    def read(self) -> bytes:
        with self._lock:
            music = self._read_source("music")
            tts = self._read_source("tts")
            if music is None and tts is None:
                if self.music_source is not None or self.tts_source is not None:
                    self.closed = False
                    return _silence()
                self.closed = True
                return b""
            if music is None:
                music = _silence()
            if tts is None:
                tts = _silence()
            music_gain = self.music_volume * self.ducking.step()
            tts_gain = self.tts_volume
            music = audioop.mul(music, SAMPLE_WIDTH, music_gain)
            tts = audioop.mul(tts, SAMPLE_WIDTH, tts_gain)
            return audioop.add(music, tts, SAMPLE_WIDTH)

    def cleanup(self) -> None:
        with self._lock:
            self._cleanup_source(self.music_source)
            self._cleanup_source(self.tts_source)
            self.music_source = None
            self.tts_source = None
            self.music_after = None
            self.tts_after = None
            self.ducking.reset()

    def _read_source(self, kind: str) -> Optional[bytes]:
        source = self.music_source if kind == "music" else self.tts_source
        if source is None:
            return None
        try:
            data = source.read()
        except Exception as exc:
            self._finish_source(kind, exc)
            return None
        if not data:
            self._finish_source(kind, None)
            return None
        if len(data) < PCM_FRAME_BYTES:
            data += b"\x00" * (PCM_FRAME_BYTES - len(data))
        return data[:PCM_FRAME_BYTES]

    def _finish_source(self, kind: str, error: Optional[Exception]) -> None:
        if kind == "music":
            source = self.music_source
            after = self.music_after
            self.music_source = None
            self.music_after = None
        else:
            source = self.tts_source
            after = self.tts_after
            self.tts_source = None
            self.tts_after = None
            self.ducking.set_tts_active(False)
        self._cleanup_source(source)
        if after:
            after(error)

    def _cleanup_source(self, source: Optional[discord.AudioSource]) -> None:
        if source is None:
            return
        cleanup = getattr(source, "cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass


def get_mixer(guild_id: str) -> VoiceMixerAudioSource:
    key = voice_state_key(guild_id)
    mixer = _MIXERS.get(key)
    if mixer is None or mixer.closed:
        mixer = VoiceMixerAudioSource(key)
        _MIXERS[key] = mixer
    return mixer


def clear_mixer(guild_id: str) -> None:
    mixer = _MIXERS.pop(voice_state_key(guild_id), None)
    if mixer is not None:
        mixer.cleanup()


def mixer_is_active(guild_id: str) -> bool:
    mixer = _MIXERS.get(voice_state_key(guild_id))
    return bool(mixer and not mixer.closed)


def mixer_has_tts_source(guild_id: str) -> bool:
    mixer = _MIXERS.get(voice_state_key(guild_id))
    return bool(mixer and not mixer.closed and mixer.tts_source is not None)


def ensure_mixer_playing(voice_client: discord.VoiceClient, guild_id: str) -> VoiceMixerAudioSource:
    mixer = get_mixer(guild_id)
    current_source = getattr(voice_client, "source", None)
    if current_source is mixer and (voice_client.is_playing() or voice_client.is_paused()):
        return mixer
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()
    voice_client.play(mixer)
    return mixer

