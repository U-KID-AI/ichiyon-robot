import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import voice_audio
from bot.services.voice_audio import (
    extract_audio_file_from_config,
    extract_reaction_audio_file,
    play_reaction_audio,
    resolve_audio_file,
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


async def check_not_connected_skip() -> bool:
    message = SimpleNamespace(guild=SimpleNamespace(id=12345, voice_client=None))
    played, reason = await play_reaction_audio(message, "dummy_check.wav", "auto_reaction", "1")
    return check("not connected skips without playback", played is False and reason == "not_connected", reason)


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

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
