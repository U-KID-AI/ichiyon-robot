import asyncio
import audioop
import io
import os
import time
import wave
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional

import discord
import httpx

from bot import config
from bot.db import get_connection
from bot.repositories.tts_settings import (
    DEFAULT_TTS_SPEAKER_ID,
    DEFAULT_TTS_VOLUME_PERCENT,
    TTSSettingsRepository,
    default_tts_settings,
)
from bot.services.voice.ducking import DuckingConfig
from bot.services.voice.mixer import clear_mixer, ensure_mixer_playing, get_mixer
from bot.services.voice.mixer import PCM_FRAME_BYTES, SAMPLE_WIDTH
from bot.services.voice.models import TTSItem
from bot.services.voice.session import (
    clear_voice_session_state,
    get_voice_session_state,
    voice_state_key,
)
from bot.services.voice.text_normalizer import (
    is_code_block_only,
    is_url_only_text,
    normalize_tts_text,
    stable_pitch_for_user,
)


VOICEVOX_URL_ENV = "VOICEVOX_ENGINE_URL"
VOICEVOX_TIMEOUT_ENV = "VOICEVOX_TIMEOUT_SECONDS"
VOICEVOX_DEFAULT_URL = "http://voicevox-engine:50021"
VOICEVOX_DEFAULT_TIMEOUT_SECONDS = 10
TTS_START_COMMAND = "読み上げ開始"
TTS_STOP_COMMAND = "読み上げ停止"
TTS_SAMPLE_RATE = 48000
TTS_CHANNELS = 2


@dataclass
class SynthesizedSpeech:
    pcm: bytes
    duration_ms: int
    audio_query_ms: int
    synthesis_ms: int
    pcm_convert_ms: int
    peak: int
    rms: int


class PCMBytesAudioSource(discord.AudioSource):
    def __init__(self, pcm: bytes, on_first_frame: Optional[Callable[[], None]] = None) -> None:
        self._pcm = bytes(pcm)
        self._offset = 0
        self._on_first_frame = on_first_frame
        self._first_frame_reported = False

    def is_opus(self) -> bool:
        return False

    def read(self) -> bytes:
        if self._offset >= len(self._pcm):
            return b""
        end = min(self._offset + PCM_FRAME_BYTES, len(self._pcm))
        chunk = self._pcm[self._offset:end]
        self._offset = end
        if len(chunk) < PCM_FRAME_BYTES:
            chunk += b"\x00" * (PCM_FRAME_BYTES - len(chunk))
        if not self._first_frame_reported:
            self._first_frame_reported = True
            if self._on_first_frame is not None:
                try:
                    self._on_first_frame()
                except Exception:
                    pass
        return chunk

    def cleanup(self) -> None:
        self._pcm = b""
        self._offset = 0


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def voicevox_base_url() -> str:
    return os.getenv(VOICEVOX_URL_ENV, VOICEVOX_DEFAULT_URL).rstrip("/")


def voicevox_timeout_seconds() -> int:
    return _env_int(VOICEVOX_TIMEOUT_ENV, VOICEVOX_DEFAULT_TIMEOUT_SECONDS, 1)


_HTTP_CLIENT: Optional[httpx.Client] = None
_HTTP_CLIENT_BASE_URL = ""
_HTTP_CLIENT_TIMEOUT = 0


def get_voicevox_client() -> httpx.Client:
    global _HTTP_CLIENT, _HTTP_CLIENT_BASE_URL, _HTTP_CLIENT_TIMEOUT
    base_url = voicevox_base_url()
    timeout = voicevox_timeout_seconds()
    if _HTTP_CLIENT is None or _HTTP_CLIENT_BASE_URL != base_url or _HTTP_CLIENT_TIMEOUT != timeout:
        if _HTTP_CLIENT is not None:
            try:
                _HTTP_CLIENT.close()
            except Exception:
                pass
        _HTTP_CLIENT = httpx.Client(base_url=base_url, timeout=timeout, trust_env=False)
        _HTTP_CLIENT_BASE_URL = base_url
        _HTTP_CLIENT_TIMEOUT = timeout
    return _HTTP_CLIENT


def normalize_tts_command(command_text: Optional[str]) -> str:
    return "".join(str(command_text or "").strip().split())


def classify_tts_command(command_text: Optional[str]) -> Optional[str]:
    normalized = normalize_tts_command(command_text)
    if normalized == TTS_START_COMMAND:
        return "start"
    if normalized == TTS_STOP_COMMAND:
        return "stop"
    return None


def load_tts_settings(guild_id: str) -> Dict[str, Any]:
    try:
        with get_connection() as connection:
            return TTSSettingsRepository(connection).get(guild_id)
    except Exception:
        return default_tts_settings(config.BOT_INSTANCE_ID, guild_id)


def tts_feature_enabled(guild_id: str) -> bool:
    return bool(load_tts_settings(guild_id).get("enabled"))


def tts_auto_join_enabled(guild_id: str) -> bool:
    settings = load_tts_settings(guild_id)
    return bool(settings.get("enabled")) and bool(settings.get("auto_join_enabled"))


def activate_tts_session(guild_id: str, text_channel_id: str, force_enabled: Optional[bool] = None) -> bool:
    settings = load_tts_settings(guild_id)
    enabled = bool(settings.get("enabled"))
    if force_enabled is not None:
        enabled = enabled and bool(force_enabled)
    else:
        enabled = enabled and bool(settings.get("auto_join_enabled"))
    session = get_voice_session_state(guild_id)
    session.bound_text_channel_id = str(text_channel_id or "")
    session.tts_enabled = enabled
    session.generation_id += 1
    print(
        "[INFO] tts session bind: bot_instance_id={0} guild_id={1} channel_id={2} enabled={3}".format(
            config.BOT_INSTANCE_ID,
            guild_id,
            session.bound_text_channel_id,
            enabled,
        )
    )
    return enabled


async def stop_tts_session(guild_id: str) -> None:
    session = get_voice_session_state(guild_id)
    session.tts_enabled = False
    session.tts_queue.clear()
    session.current_tts = None
    session.generation_id += 1
    task = session.tts_worker_task
    if task is not None and hasattr(task, "cancel"):
        task.cancel()
    get_mixer(guild_id).clear_tts(call_after=False)
    print("[INFO] tts stopped: bot_instance_id={0} guild_id={1}".format(config.BOT_INSTANCE_ID, guild_id))


async def reset_voice_session(guild_id: str) -> None:
    await stop_tts_session(guild_id)
    clear_mixer(guild_id)
    clear_voice_session_state(guild_id)


def _attachment_types(message: discord.Message) -> Iterable[str]:
    for attachment in getattr(message, "attachments", []) or []:
        yield str(getattr(attachment, "content_type", "") or "")


def should_tts_read_message(message: discord.Message, command_text: Optional[str]) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    if getattr(message, "webhook_id", None):
        return False
    if getattr(message, "guild", None) is None:
        return False
    if command_text is not None:
        return False
    content = str(getattr(message, "content", "") or "")
    if not content.strip() and not (getattr(message, "attachments", []) or []):
        return False
    if is_code_block_only(content):
        return False
    if is_url_only_text(content):
        return False
    return True


def _convert_wav_to_discord_pcm(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    if sample_width != SAMPLE_WIDTH:
        frames = audioop.lin2lin(frames, sample_width, SAMPLE_WIDTH)
        sample_width = SAMPLE_WIDTH
    if channels == 1:
        frames = audioop.tostereo(frames, sample_width, 1.0, 1.0)
        channels = 2
    elif channels != TTS_CHANNELS:
        frames = audioop.tomono(frames, sample_width, 1.0 / max(1, channels), 1.0 / max(1, channels))
        frames = audioop.tostereo(frames, sample_width, 1.0, 1.0)
        channels = 2
    if sample_rate != TTS_SAMPLE_RATE:
        frames, _ = audioop.ratecv(frames, sample_width, channels, sample_rate, TTS_SAMPLE_RATE, None)
    return frames


def synthesize_voicevox_to_pcm(text: str, settings: Dict[str, Any], pitch_scale: float) -> SynthesizedSpeech:
    started = time.perf_counter()
    speaker_id = int(settings.get("speaker_id") or DEFAULT_TTS_SPEAKER_ID)
    client = get_voicevox_client()
    query_started = time.perf_counter()
    query_response = client.post("/audio_query", params={"text": text, "speaker": speaker_id})
    query_response.raise_for_status()
    query = query_response.json()
    audio_query_ms = max(0, int((time.perf_counter() - query_started) * 1000))
    query["speedScale"] = float(settings.get("speed_scale") or 1.0)
    query["pitchScale"] = float(query.get("pitchScale") or 0.0) + float(pitch_scale)
    query["volumeScale"] = 1.0
    synth_started = time.perf_counter()
    synth_response = client.post("/synthesis", params={"speaker": speaker_id}, json=query)
    synth_response.raise_for_status()
    wav_bytes = synth_response.content
    synthesis_ms = max(0, int((time.perf_counter() - synth_started) * 1000))
    convert_started = time.perf_counter()
    pcm = _convert_wav_to_discord_pcm(wav_bytes)
    pcm_convert_ms = max(0, int((time.perf_counter() - convert_started) * 1000))
    peak = audioop.max(pcm, SAMPLE_WIDTH) if pcm else 0
    rms = audioop.rms(pcm, SAMPLE_WIDTH) if pcm else 0
    return SynthesizedSpeech(
        pcm=pcm,
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        audio_query_ms=audio_query_ms,
        synthesis_ms=synthesis_ms,
        pcm_convert_ms=pcm_convert_ms,
        peak=peak,
        rms=rms,
    )


async def _play_tts_item(message: discord.Message, item: TTSItem, settings: Dict[str, Any]) -> None:
    play_started = time.perf_counter()
    guild = message.guild
    if guild is None or str(getattr(guild, "id", "") or "") != item.guild_id:
        return
    voice_client = getattr(guild, "voice_client", None)
    is_connected = getattr(voice_client, "is_connected", None)
    if voice_client is None or not callable(is_connected) or not is_connected():
        return
    session = get_voice_session_state(item.guild_id)
    if item.generation_id != session.generation_id or not session.tts_enabled:
        return
    pitch = stable_pitch_for_user(item.author_id, float(settings.get("pitch_variation") or 0.0)) if settings.get("user_pitch_enabled") else 0.0
    try:
        synth_started = time.perf_counter()
        speech = await asyncio.to_thread(synthesize_voicevox_to_pcm, item.text, settings, pitch)
        synth_total_ms = max(0, int((time.perf_counter() - synth_started) * 1000))
    except (httpx.HTTPError, TimeoutError, OSError, ValueError, wave.Error) as exc:
        print(
            "[WARN] tts synth skipped: bot_instance_id={0} guild_id={1} channel_id={2} error={3}".format(
                config.BOT_INSTANCE_ID,
                item.guild_id,
                item.channel_id,
                type(exc).__name__,
            )
        )
        return
    if item.generation_id != session.generation_id or not session.tts_enabled:
        return
    done = asyncio.Event()
    first_frame = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_first_frame() -> None:
        loop.call_soon_threadsafe(first_frame.set)

    def _after(error: Optional[Exception]) -> None:
        if error is not None:
            print("[WARN] tts playback error: bot_instance_id={0} guild_id={1} error={2}".format(config.BOT_INSTANCE_ID, item.guild_id, type(error).__name__))
        loop.call_soon_threadsafe(done.set)

    try:
        source = PCMBytesAudioSource(speech.pcm, on_first_frame=_on_first_frame)
        mixer = get_mixer(item.guild_id)
        tts_volume_percent = int(settings.get("tts_volume_percent") if settings.get("tts_volume_percent") is not None else DEFAULT_TTS_VOLUME_PERCENT)
        mixer.set_tts_volume(max(0.0, min(1.0, tts_volume_percent / 100.0)))
        mixer.configure_ducking(
            DuckingConfig(
                enabled=bool(settings.get("ducking_enabled")),
                music_gain=float(settings.get("ducking_music_gain") or 0.5),
                attack_ms=int(settings.get("ducking_attack_ms") or 100),
                release_ms=int(settings.get("ducking_release_ms") or 300),
            )
        )
        mixer.set_tts_source(source, _after)
        ensure_mixer_playing(voice_client, item.guild_id)
        try:
            await asyncio.wait_for(first_frame.wait(), timeout=2.0)
            print(
                "[INFO] tts_timing: stage=first_frame bot_instance_id={0} guild_id={1} channel_id={2} queue_wait_ms={3} audio_query_ms={4} synthesis_ms={5} pcm_convert_ms={6} mixer_wait_ms={7} total_to_first_frame_ms={8} pcm_bytes={9} peak={10} rms={11} volume_percent={12}".format(
                    config.BOT_INSTANCE_ID,
                    item.guild_id,
                    item.channel_id,
                    max(0, int((synth_started - item.accepted_monotonic) * 1000)) if item.accepted_monotonic else 0,
                    speech.audio_query_ms,
                    speech.synthesis_ms,
                    speech.pcm_convert_ms,
                    max(0, int((time.perf_counter() - play_started) * 1000) - synth_total_ms),
                    max(0, int((time.perf_counter() - item.accepted_monotonic) * 1000)) if item.accepted_monotonic else speech.duration_ms,
                    len(speech.pcm),
                    speech.peak,
                    speech.rms,
                    tts_volume_percent,
                )
            )
        except asyncio.TimeoutError:
            print(
                "[WARN] tts_timing: stage=first_frame_timeout bot_instance_id={0} guild_id={1} channel_id={2} audio_query_ms={3} synthesis_ms={4} pcm_convert_ms={5} total_wait_ms={6}".format(
                    config.BOT_INSTANCE_ID,
                    item.guild_id,
                    item.channel_id,
                    speech.audio_query_ms,
                    speech.synthesis_ms,
                    speech.pcm_convert_ms,
                    max(0, int((time.perf_counter() - item.accepted_monotonic) * 1000)) if item.accepted_monotonic else 0,
                )
            )
        print(
            "[INFO] tts play_start: bot_instance_id={0} guild_id={1} channel_id={2} synth_ms={3} audio_query_ms={4} synthesis_ms={5} pcm_convert_ms={6} pcm_bytes={7} volume_percent={8}".format(
                config.BOT_INSTANCE_ID,
                item.guild_id,
                item.channel_id,
                speech.duration_ms,
                speech.audio_query_ms,
                speech.synthesis_ms,
                speech.pcm_convert_ms,
                len(speech.pcm),
                tts_volume_percent,
            )
        )
        await done.wait()
    except Exception as exc:
        print("[WARN] tts playback skipped: bot_instance_id={0} guild_id={1} error={2}".format(config.BOT_INSTANCE_ID, item.guild_id, type(exc).__name__))


async def _tts_worker(message: discord.Message, guild_id: str, generation_id: int) -> None:
    session = get_voice_session_state(guild_id)
    while session.tts_enabled and session.generation_id == generation_id:
        if not session.tts_queue:
            session.tts_worker_task = None
            return
        item = session.tts_queue.popleft()
        if item.generation_id != generation_id:
            continue
        session.current_tts = item
        settings = load_tts_settings(guild_id)
        await _play_tts_item(message, item, settings)
        session.current_tts = None
    session.tts_worker_task = None


async def maybe_enqueue_tts(message: discord.Message, command_text: Optional[str]) -> bool:
    accepted_at = time.perf_counter()
    if not should_tts_read_message(message, command_text):
        return False
    guild_id = str(getattr(message.guild, "id", "") or "")
    channel_id = str(getattr(message.channel, "id", "") or "")
    session = get_voice_session_state(guild_id)
    if not session.tts_enabled or session.bound_text_channel_id != channel_id:
        return False
    settings = load_tts_settings(guild_id)
    if not settings.get("enabled"):
        return False
    normalize_started = time.perf_counter()
    text = normalize_tts_text(str(getattr(message, "content", "") or ""), _attachment_types(message), int(settings.get("max_text_length") or 300))
    normalize_ms = max(0, int((time.perf_counter() - normalize_started) * 1000))
    if not text:
        return False
    queue_limit = int(settings.get("queue_limit") or 50)
    if len(session.tts_queue) >= queue_limit:
        print("[WARN] tts queue full: bot_instance_id={0} guild_id={1} channel_id={2} limit={3}".format(config.BOT_INSTANCE_ID, guild_id, channel_id, queue_limit))
        return False
    item = TTSItem(
        text=text,
        author_id=str(getattr(message.author, "id", "") or ""),
        guild_id=guild_id,
        channel_id=channel_id,
        generation_id=session.generation_id,
        accepted_monotonic=accepted_at,
        normalize_ms=normalize_ms,
    )
    session.tts_queue.append(item)
    print(
        "[INFO] tts_message_accepted: bot_instance_id={0} guild_id={1} channel_id={2} text_length={3} queue_size={4} normalize_ms={5}".format(
            config.BOT_INSTANCE_ID,
            guild_id,
            channel_id,
            len(text),
            len(session.tts_queue),
            normalize_ms,
        )
    )
    if session.tts_worker_task is None or getattr(session.tts_worker_task, "done", lambda: True)():
        session.tts_worker_task = asyncio.create_task(_tts_worker(message, guild_id, session.generation_id))
    return True


async def handle_tts_command(message: discord.Message, command_text: Optional[str]) -> bool:
    command = classify_tts_command(command_text)
    if command is None:
        return False
    guild = getattr(message, "guild", None)
    if guild is None:
        await message.channel.send("読み上げコマンドはサーバー内で使ってください。")
        return True
    guild_id = str(guild.id)
    if command == "stop":
        await stop_tts_session(guild_id)
        await message.channel.send("読み上げを停止しました。")
        return True
    activate_tts_session(guild_id, str(getattr(message.channel, "id", "") or ""), force_enabled=True)
    await message.channel.send("このチャンネルの読み上げを開始しました。")
    return True
