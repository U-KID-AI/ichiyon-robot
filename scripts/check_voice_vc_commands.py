import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import voice_audio
from bot.services.voice_control import (
    classify_voice_command,
    normalize_voice_command,
    parse_voice_command,
)
from bot.services.voice_audio import (
    AUDIO_ROOT,
    cleanup_stale_voice_client,
    format_audio_file_list,
    get_guild_voice_client,
    is_voice_client_connected,
    resolve_audio_file,
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakeVoiceClient:
    def __init__(self, connected: bool) -> None:
        self.connected = connected
        self.disconnected = False
        self.channel = SimpleNamespace(id=123)

    def is_connected(self) -> bool:
        return self.connected

    async def disconnect(self, force: bool = False) -> None:
        self.disconnected = True


async def check_stale_cleanup() -> bool:
    stale = FakeVoiceClient(False)
    await cleanup_stale_voice_client(stale)
    return check("stale voice client is cleaned up", stale.disconnected is True)


def main() -> int:
    results = []
    join_examples = ["もしもししよ"]
    leave_examples = ["二度と来るな"]
    legacy_join_examples = ["入って", "来て", "参加", "vc入って", "VC 入って", "ボイス入って"]
    legacy_leave_examples = ["出て", "抜けて", "退出", "vc出て", "VC 出て", "ボイス出て"]
    list_examples = ["音声一覧", "ボイス一覧", "sound list"]
    play_examples = {
        "鳴らして test": ("play", "test"),
        "再生 test.mp3": ("play", "test.mp3"),
        "ボイス sample": ("play", "sample"),
        "sound test": ("play", "test"),
    }
    stop_examples = ["止めて", "停止", "stop"]

    results.append(
        check(
            "join commands are recognized",
            all(classify_voice_command(command) == "join" for command in join_examples),
            str(join_examples),
        )
    )
    results.append(
        check(
            "leave commands are recognized",
            all(classify_voice_command(command) == "leave" for command in leave_examples),
            str(leave_examples),
        )
    )
    results.append(
        check(
            "legacy join commands are ignored",
            all(classify_voice_command(command) != "join" for command in legacy_join_examples),
            str({command: classify_voice_command(command) for command in legacy_join_examples}),
        )
    )
    results.append(
        check(
            "legacy leave commands are ignored",
            all(classify_voice_command(command) != "leave" for command in legacy_leave_examples),
            str({command: classify_voice_command(command) for command in legacy_leave_examples}),
        )
    )
    results.append(
        check(
            "list commands are recognized",
            all(classify_voice_command(command) == "list" for command in list_examples),
            str(list_examples),
        )
    )
    results.append(
        check(
            "play commands parse name",
            all(parse_voice_command(command) == expected for command, expected in play_examples.items()),
            str({command: parse_voice_command(command) for command in play_examples}),
        )
    )
    results.append(
        check(
            "stop commands are recognized",
            all(classify_voice_command(command) == "stop" for command in stop_examples),
            str(stop_examples),
        )
    )
    results.append(check("unknown command is ignored", classify_voice_command("スケジュール 7/6から1W") is None))
    results.append(check("empty command is ignored", classify_voice_command("") is None))
    results.append(check("normalizer removes spaces", normalize_voice_command(" もしもし しよ ") == "もしもししよ"))
    results.append(check("audio list empty message is clear", format_audio_file_list([]) == "登録されている音声ファイルがありません。"))

    original_root = voice_audio.AUDIO_ROOT
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            voice_audio.AUDIO_ROOT = Path(tmp_dir).resolve()
            dummy_path = voice_audio.AUDIO_ROOT / "dummy_check.wav"
            dummy_path.write_bytes(b"not real audio")
            results.append(check("audio file resolves without extension", resolve_audio_file("dummy_check") == dummy_path.resolve()))
            results.append(check("audio file resolves with extension", resolve_audio_file("dummy_check.wav") == dummy_path.resolve()))
            results.append(check("unsupported extension is rejected", resolve_audio_file("dummy_check.txt") is None))
            results.append(check("path traversal is rejected", resolve_audio_file("../dummy_check.wav") is None))
    finally:
        voice_audio.AUDIO_ROOT = original_root

    results.append(check("standard audio root is assets/audio", AUDIO_ROOT.name == "audio"))
    stale = FakeVoiceClient(False)
    connected = FakeVoiceClient(True)
    results.append(check("stale voice client is not treated as connected", is_voice_client_connected(stale) is False))
    results.append(check("connected voice client is recognized", is_voice_client_connected(connected) is True))
    results.append(check("get_guild_voice_client hides stale client", get_guild_voice_client(SimpleNamespace(voice_client=stale)) is None))
    results.append(check("get_guild_voice_client returns connected client", get_guild_voice_client(SimpleNamespace(voice_client=connected)) is connected))
    results.append(asyncio.run(check_stale_cleanup()))

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
