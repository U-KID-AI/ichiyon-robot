import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services.voice_control import classify_voice_command, normalize_voice_command


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main() -> int:
    results = []
    join_examples = ["入って", "来て", "参加", "vc入って", "VC 入って", "ボイス入って"]
    leave_examples = ["出て", "抜けて", "退出", "vc出て", "VC 出て", "ボイス出て"]

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
    results.append(check("unknown command is ignored", classify_voice_command("スケジュール 7/6から1W") is None))
    results.append(check("empty command is ignored", classify_voice_command("") is None))
    results.append(check("normalizer removes spaces and lowercases", normalize_voice_command(" VC 入って ") == "vc入って"))

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
