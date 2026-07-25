from typing import Any, Dict, Optional

from bot import config
from bot.repositories.base import fetch_one


DEFAULT_TTS_ENABLED = True
DEFAULT_TTS_AUTO_JOIN_ENABLED = True
DEFAULT_TTS_SPEAKER_ID = 3
DEFAULT_TTS_SPEAKER_NAME = "ずんだもん ノーマル"
DEFAULT_TTS_VOLUME_PERCENT = 50
DEFAULT_TTS_SPEED_SCALE = 1.0
DEFAULT_TTS_USER_PITCH_ENABLED = True
DEFAULT_TTS_PITCH_VARIATION = 0.06
DEFAULT_TTS_MAX_TEXT_LENGTH = 300
DEFAULT_TTS_QUEUE_LIMIT = 50
DEFAULT_DUCKING_ENABLED = False
DEFAULT_DUCKING_MUSIC_GAIN = 0.5
DEFAULT_DUCKING_ATTACK_MS = 100
DEFAULT_DUCKING_RELEASE_MS = 300
DEFAULT_TTS_CREDIT_TEXT = "VOICEVOX: ずんだもん"


def _value_or_default(value, default):
    return default if value is None else value


def default_tts_settings(bot_id: str, guild_id: str) -> Dict[str, Any]:
    return {
        "bot_id": bot_id,
        "guild_id": guild_id,
        "enabled": DEFAULT_TTS_ENABLED,
        "auto_join_enabled": DEFAULT_TTS_AUTO_JOIN_ENABLED,
        "speaker_id": DEFAULT_TTS_SPEAKER_ID,
        "speaker_name": DEFAULT_TTS_SPEAKER_NAME,
        "tts_volume_percent": DEFAULT_TTS_VOLUME_PERCENT,
        "speed_scale": DEFAULT_TTS_SPEED_SCALE,
        "user_pitch_enabled": DEFAULT_TTS_USER_PITCH_ENABLED,
        "pitch_variation": DEFAULT_TTS_PITCH_VARIATION,
        "max_text_length": DEFAULT_TTS_MAX_TEXT_LENGTH,
        "queue_limit": DEFAULT_TTS_QUEUE_LIMIT,
        "ducking_enabled": DEFAULT_DUCKING_ENABLED,
        "ducking_music_gain": DEFAULT_DUCKING_MUSIC_GAIN,
        "ducking_attack_ms": DEFAULT_DUCKING_ATTACK_MS,
        "ducking_release_ms": DEFAULT_DUCKING_RELEASE_MS,
        "credit_text": DEFAULT_TTS_CREDIT_TEXT,
    }


class TTSSettingsRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def get(self, guild_id: str) -> Dict[str, Any]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM bot_tts_settings
                    WHERE bot_id = %s AND guild_id = %s
                    """,
                    (self.bot_id, guild_id),
                )
                row = fetch_one(cursor)
        except Exception:
            return default_tts_settings(self.bot_id, guild_id)
        if row is None:
            return default_tts_settings(self.bot_id, guild_id)
        merged = default_tts_settings(self.bot_id, guild_id)
        merged.update(row)
        return merged

    def upsert(self, guild_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        current = self.get(guild_id)
        merged = dict(current)
        merged.update(values)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bot_tts_settings (
                    bot_id,
                    guild_id,
                    enabled,
                    auto_join_enabled,
                    speaker_id,
                    speaker_name,
                    tts_volume_percent,
                    speed_scale,
                    user_pitch_enabled,
                    pitch_variation,
                    max_text_length,
                    queue_limit,
                    ducking_enabled,
                    ducking_music_gain,
                    ducking_attack_ms,
                    ducking_release_ms,
                    credit_text
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bot_id, guild_id) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    auto_join_enabled = EXCLUDED.auto_join_enabled,
                    speaker_id = EXCLUDED.speaker_id,
                    speaker_name = EXCLUDED.speaker_name,
                    tts_volume_percent = EXCLUDED.tts_volume_percent,
                    speed_scale = EXCLUDED.speed_scale,
                    user_pitch_enabled = EXCLUDED.user_pitch_enabled,
                    pitch_variation = EXCLUDED.pitch_variation,
                    max_text_length = EXCLUDED.max_text_length,
                    queue_limit = EXCLUDED.queue_limit,
                    ducking_enabled = EXCLUDED.ducking_enabled,
                    ducking_music_gain = EXCLUDED.ducking_music_gain,
                    ducking_attack_ms = EXCLUDED.ducking_attack_ms,
                    ducking_release_ms = EXCLUDED.ducking_release_ms,
                    credit_text = EXCLUDED.credit_text,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    self.bot_id,
                    guild_id,
                    bool(merged.get("enabled")),
                    bool(merged.get("auto_join_enabled")),
                    int(_value_or_default(merged.get("speaker_id"), DEFAULT_TTS_SPEAKER_ID)),
                    str(merged.get("speaker_name") or ""),
                    int(_value_or_default(merged.get("tts_volume_percent"), DEFAULT_TTS_VOLUME_PERCENT)),
                    float(_value_or_default(merged.get("speed_scale"), DEFAULT_TTS_SPEED_SCALE)),
                    bool(merged.get("user_pitch_enabled")),
                    float(_value_or_default(merged.get("pitch_variation"), DEFAULT_TTS_PITCH_VARIATION)),
                    int(_value_or_default(merged.get("max_text_length"), DEFAULT_TTS_MAX_TEXT_LENGTH)),
                    int(_value_or_default(merged.get("queue_limit"), DEFAULT_TTS_QUEUE_LIMIT)),
                    bool(merged.get("ducking_enabled")),
                    float(_value_or_default(merged.get("ducking_music_gain"), DEFAULT_DUCKING_MUSIC_GAIN)),
                    int(_value_or_default(merged.get("ducking_attack_ms"), DEFAULT_DUCKING_ATTACK_MS)),
                    int(_value_or_default(merged.get("ducking_release_ms"), DEFAULT_DUCKING_RELEASE_MS)),
                    str(merged.get("credit_text") or DEFAULT_TTS_CREDIT_TEXT),
                ),
            )
            return fetch_one(cursor)

