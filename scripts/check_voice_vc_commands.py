import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import voice_control
from bot.services.voice_control import (
    AUDIO_ROOT,
    classify_voice_command,
    format_audio_file_list,
    normalize_voice_command,
    parse_voice_command,
    resolve_audio_file,
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main() -> int:
    results = []
    join_examples = ["入って", "来て", "参加", "vc入って", "VC 入って", "ボイス入って"]
    leave_examples = ["出て", "抜けて", "退出", "vc出て", "VC 出て", "ボイス出て"]
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
    results.append(check("normalizer removes spaces and lowercases", normalize_voice_command(" VC 入って ") == "vc入って"))
    results.append(check("audio list empty message is clear", format_audio_file_list([]) == "登録されている音声ファイルがありません。"))

    original_root = voice_control.AUDIO_ROOT
    try:
        voice_control.AUDIO_ROOT = (ROOT_DIR / "assets" / "audio").resolve()
        voice_control.AUDIO_ROOT.mkdir(parents=True, exist_ok=True)
        dummy_path = voice_control.AUDIO_ROOT / "dummy_check.wav"
        dummy_path.write_bytes(b"not real audio")
        try:
            results.append(check("audio file resolves without extension", resolve_audio_file("dummy_check") == dummy_path.resolve()))
            results.append(check("audio file resolves with extension", resolve_audio_file("dummy_check.wav") == dummy_path.resolve()))
            results.append(check("unsupported extension is rejected", resolve_audio_file("dummy_check.txt") is None))
            results.append(check("path traversal is rejected", resolve_audio_file("../dummy_check.wav") is None))
        finally:
            dummy_path.unlink(missing_ok=True)
    finally:
        voice_control.AUDIO_ROOT = original_root

    results.append(check("standard audio root is assets/audio", AUDIO_ROOT.name == "audio"))

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
