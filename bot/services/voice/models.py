from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional

import discord


@dataclass
class MusicTrack:
    title: str
    webpage_url: str
    stream_url: str
    requester_id: str
    duration: Optional[int] = None
    source_url: Optional[str] = None
    refresh_required: bool = False
    source_type: str = "youtube"
    original_spotify_url: str = ""
    spotify_track_id: str = ""
    spotify_title: str = ""
    spotify_artists: str = ""
    spotify_album_name: str = ""
    spotify_playlist_id: str = ""
    spotify_playlist_name: str = ""
    spotify_playlist_index: Optional[int] = None
    spotify_resolve_status: str = ""
    enqueued_at_monotonic: float = 0.0
    youtube_route: str = "direct_cookie"
    ffmpeg_proxy_url: str = ""
    playback_http_403: bool = False
    playback_retry_count: int = 0


@dataclass
class TTSItem:
    text: str
    author_id: str
    guild_id: str
    channel_id: str
    generation_id: int
    accepted_monotonic: float = 0.0
    normalize_ms: int = 0


@dataclass
class MusicState:
    queue: Deque[MusicTrack] = field(default_factory=deque)
    loop_queue: Deque[MusicTrack] = field(default_factory=deque)
    current: Optional[MusicTrack] = None
    text_channel: Optional[discord.abc.Messageable] = None
    stopping: bool = False
    skip_requested: bool = False
    loop_mode: str = "off"
    loop_range_size: Optional[int] = None
    music_volume_percent: Optional[int] = None


@dataclass
class VoiceSessionState:
    bound_text_channel_id: Optional[str] = None
    tts_enabled: bool = False
    tts_queue: Deque[TTSItem] = field(default_factory=deque)
    current_tts: Optional[TTSItem] = None
    generation_id: int = 0
    tts_worker_task: Optional[object] = None
    ducking_gain: float = 1.0

