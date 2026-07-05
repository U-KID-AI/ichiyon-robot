import sys
from pathlib import Path
from typing import Any, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.repositories.voice_lines import DEFAULT_REVIVE_LINE, resolve_voice_line


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


def main() -> int:
    results: List[Tuple[str, bool, Any]] = []

    record(
        results,
        "unset revive line keeps ichiyon default",
        resolve_voice_line(None, "revive_line", DEFAULT_REVIVE_LINE) == DEFAULT_REVIVE_LINE,
        DEFAULT_REVIVE_LINE,
    )
    record(
        results,
        "custom revive line is used",
        resolve_voice_line({"enabled": True, "revive_line": "復活"}, "revive_line", DEFAULT_REVIVE_LINE) == "復活",
        "復活",
    )
    record(
        results,
        "disabled setting suppresses line",
        resolve_voice_line({"enabled": False, "revive_line": "復活"}, "revive_line", DEFAULT_REVIVE_LINE) is None,
        "disabled",
    )
    record(
        results,
        "blank custom line falls back",
        resolve_voice_line({"enabled": True, "revive_line": "  "}, "revive_line", DEFAULT_REVIVE_LINE) == DEFAULT_REVIVE_LINE,
        "blank",
    )
    record(
        results,
        "join line can be blank without default",
        resolve_voice_line({"enabled": True, "join_line": ""}, "join_line", "") == "",
        "join",
    )

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
