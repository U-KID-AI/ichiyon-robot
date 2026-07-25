import asyncio
from typing import Dict

from bot import config
from bot.services.voice.models import MusicState, VoiceSessionState


_MUSIC_STATES: Dict[str, MusicState] = {}
_VOICE_CONNECT_LOCKS: Dict[str, asyncio.Lock] = {}
_VOICE_SESSION_STATES: Dict[str, VoiceSessionState] = {}


def voice_state_key(guild_id: str) -> str:
    return "{0}:{1}".format(config.BOT_INSTANCE_ID, guild_id)


def music_state_key(guild_id: str) -> str:
    return voice_state_key(guild_id)


def get_music_state(guild_id: str) -> MusicState:
    key = music_state_key(guild_id)
    if key not in _MUSIC_STATES:
        _MUSIC_STATES[key] = MusicState()
    return _MUSIC_STATES[key]


def get_voice_session_state(guild_id: str) -> VoiceSessionState:
    key = voice_state_key(guild_id)
    if key not in _VOICE_SESSION_STATES:
        _VOICE_SESSION_STATES[key] = VoiceSessionState()
    return _VOICE_SESSION_STATES[key]


def get_voice_connect_lock(guild_id: str) -> asyncio.Lock:
    key = voice_state_key(guild_id)
    if key not in _VOICE_CONNECT_LOCKS:
        _VOICE_CONNECT_LOCKS[key] = asyncio.Lock()
    return _VOICE_CONNECT_LOCKS[key]


def clear_music_state(guild_id: str) -> None:
    _MUSIC_STATES.pop(music_state_key(guild_id), None)


def clear_voice_session_state(guild_id: str) -> None:
    _VOICE_SESSION_STATES.pop(voice_state_key(guild_id), None)


def clear_voice_runtime_state(guild_id: str) -> None:
    clear_music_state(guild_id)
    clear_voice_session_state(guild_id)

