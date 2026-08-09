import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import voice_audio
from bot.services import runtime_db
from bot.services.voice_audio import (
    enqueue_foreground_audio,
    extract_audio_file_from_config,
    extract_audio_asset_id_from_config,
    extract_reaction_audio_file,
    extract_reaction_audio_asset_id,
    extract_reaction_audio_volume_percent,
    play_reaction_audio,
    resolve_audio_file,
)
from bot.services.voice.session import voice_state_key


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


async def check_not_connected_skip() -> bool:
    message = SimpleNamespace(guild=SimpleNamespace(id=12345, voice_client=None))
    played, reason = await play_reaction_audio(message, "dummy_check.wav", "auto_reaction", "1")
    return check("not connected skips without playback", played is False and reason == "not_connected", reason)


async def check_configured_reaction_audio_asset_route() -> bool:
    calls = []
    original = runtime_db.play_audio_asset_by_id

    async def fake_play_audio_asset_by_id(message, asset_id, volume_percent=None, reaction_type="audio_asset", reaction_key=""):
        calls.append(
            {
                "asset_id": asset_id,
                "volume_percent": volume_percent,
                "reaction_type": reaction_type,
                "reaction_key": reaction_key,
            }
        )
        return True, "queued"

    try:
        runtime_db.play_audio_asset_by_id = fake_play_audio_asset_by_id
        played = await runtime_db.play_configured_reaction_audio(
            SimpleNamespace(guild=SimpleNamespace(id=12345, voice_client=None)),
            {"id": 77, "audio_config_json": {"audio_asset_id": 42, "volume_percent": 65}},
            "auto_reaction",
            "fallback",
        )
    finally:
        runtime_db.play_audio_asset_by_id = original

    return check(
        "configured reaction audio_asset_id uses audio asset playback",
        played
        and calls
        and calls[0]["asset_id"] == 42
        and calls[0]["volume_percent"] == 65
        and calls[0]["reaction_type"] == "auto_reaction"
        and calls[0]["reaction_key"] == "77",
        str(calls),
    )


def check_foreground_audio_accepts_repeated_requests() -> bool:
    guild_id = "guild-repeat-se"
    key = voice_state_key(guild_id)
    voice_audio._FOREGROUND_QUEUES.pop(key, None)
    voice_audio._FOREGROUND_ACTIVE[key] = True
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "repeat.wav"
            path.write_bytes(b"not real audio")
            fake_voice = SimpleNamespace(channel=SimpleNamespace(id="voice-repeat"), is_connected=lambda: True)
            first = enqueue_foreground_audio(fake_voice, path, guild_id, "voice-repeat", 50, "panel_se", "1")
            second = enqueue_foreground_audio(fake_voice, path, guild_id, "voice-repeat", 50, "panel_se", "1")
            queue = voice_audio._FOREGROUND_QUEUES.get(key)
            return check(
                "foreground audio queues repeated SE requests while active",
                first == (True, "queued") and second == (True, "queued") and queue is not None and len(queue) == 2,
                "first={0} second={1} queue_len={2}".format(first, second, len(queue or [])),
            )
    finally:
        voice_audio._FOREGROUND_QUEUES.pop(key, None)
        voice_audio._FOREGROUND_ACTIVE.pop(key, None)


def main() -> int:
    results = []
    results.append(check("top-level audio_file is accepted", extract_audio_file_from_config({"audio_file": "test.mp3"}) == "test.mp3"))
    results.append(
        check(
            "nested voice.audio_file is accepted",
            extract_audio_file_from_config({"voice": {"audio_file": "test.wav"}}) == "test.wav",
        )
    )
    results.append(check("json string audio config is accepted", extract_audio_file_from_config('{"audio_file":"json.mp3"}') == "json.mp3"))
    results.append(check("top-level audio_asset_id is accepted", extract_audio_asset_id_from_config({"audio_asset_id": "123"}) == 123))
    results.append(
        check(
            "nested voice.audio_asset_id is accepted",
            extract_audio_asset_id_from_config({"voice": {"audio_asset_id": 456}}) == 456,
        )
    )
    results.append(check("invalid audio_asset_id is ignored", extract_audio_asset_id_from_config({"audio_asset_id": "../bad"}) is None))
    results.append(check("blank audio_file is ignored", extract_audio_file_from_config({"audio_file": "   "}) == ""))
    results.append(check("missing audio config is ignored", extract_audio_file_from_config({"voice": {}}) == ""))
    results.append(
        check(
            "auto reaction audio_config_json is read",
            extract_reaction_audio_file({"audio_config_json": {"voice": {"audio_file": "auto.ogg"}}}) == "auto.ogg",
        )
    )
    results.append(
        check(
            "mention reaction config_json is read",
            extract_reaction_audio_file({"config_json": {"audio_file": "mention.mp3"}}) == "mention.mp3",
        )
    )
    results.append(
        check(
            "audio_config_json has priority over config_json",
            extract_reaction_audio_file(
                {
                    "audio_config_json": {"audio_file": "auto.mp3"},
                    "config_json": {"audio_file": "mention.mp3"},
                }
            )
            == "auto.mp3",
        )
    )
    results.append(
        check(
            "auto reaction audio_asset_id is read",
            extract_reaction_audio_asset_id({"audio_config_json": {"voice": {"audio_asset_id": "321"}}}) == 321,
        )
    )
    results.append(
        check(
            "auto reaction audio volume is clamped",
            extract_reaction_audio_volume_percent({"audio_config_json": {"volume_percent": "130"}}) == 100,
        )
    )

    original_root = voice_audio.AUDIO_ROOT
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            voice_audio.AUDIO_ROOT = Path(tmp_dir).resolve()
            dummy_path = voice_audio.AUDIO_ROOT / "dummy_reaction_check.ogg"
            dummy_path.write_bytes(b"not real audio")
            results.append(check("reaction audio resolves without extension", resolve_audio_file("dummy_reaction_check") == dummy_path.resolve()))
            results.append(check("reaction audio rejects traversal", resolve_audio_file("../dummy_reaction_check.ogg") is None))
            results.append(check("reaction audio rejects unsupported extension", resolve_audio_file("dummy_reaction_check.txt") is None))
    finally:
        voice_audio.AUDIO_ROOT = original_root

    results.append(asyncio.run(check_not_connected_skip()))
    results.append(asyncio.run(check_configured_reaction_audio_asset_route()))
    results.append(check_foreground_audio_accepts_repeated_requests())

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
