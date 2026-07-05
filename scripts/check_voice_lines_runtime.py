import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot import messages
from bot.repositories.voice_lines import DEFAULT_REVIVE_LINE, resolve_voice_line


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeVoiceLineRepository:
    rows: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}

    def __init__(self, connection) -> None:
        self.connection = connection

    def get(self, bot_id: str, guild_id: str) -> Optional[Dict[str, Any]]:
        return self.rows.get((bot_id, guild_id))


def resolve_startup_for_test(
    bot_id: str,
    rows: Dict[Tuple[str, str], Optional[Dict[str, Any]]],
    default_message: Optional[str],
) -> Optional[str]:
    old_bot_id = messages.config.BOT_INSTANCE_ID
    old_backend = messages.config.DATA_BACKEND
    old_get_connection = messages.get_connection
    old_repository = messages.VoiceLineRepository
    old_get_startup_message = messages.get_startup_message
    try:
        messages.config.BOT_INSTANCE_ID = bot_id
        messages.config.DATA_BACKEND = "db"
        messages.get_connection = lambda: FakeConnection()
        FakeVoiceLineRepository.rows = rows
        messages.VoiceLineRepository = FakeVoiceLineRepository
        messages.get_startup_message = lambda: default_message
        return messages.get_startup_voice_line("guild")
    finally:
        messages.config.BOT_INSTANCE_ID = old_bot_id
        messages.config.DATA_BACKEND = old_backend
        messages.get_connection = old_get_connection
        messages.VoiceLineRepository = old_repository
        messages.get_startup_message = old_get_startup_message


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
        resolve_voice_line({"enabled": True, "revive_line": "  "}, "revive_line", DEFAULT_REVIVE_LINE)
        == DEFAULT_REVIVE_LINE,
        "blank",
    )
    record(
        results,
        "join line can be blank without default",
        resolve_voice_line({"enabled": True, "join_line": ""}, "join_line", "") == "",
        "join",
    )
    record(
        results,
        "irsia startup uses bot guild voice line",
        resolve_startup_for_test(
            "irsia",
            {("irsia", "guild"): {"enabled": True, "join_line": "私が神です"}},
            DEFAULT_REVIVE_LINE,
        )
        == "私が神です",
        "irsia join",
    )
    record(
        results,
        "irsia startup does not fall back to ichiyon fixed line",
        resolve_startup_for_test("irsia", {}, DEFAULT_REVIVE_LINE) == "",
        "irsia blank",
    )
    record(
        results,
        "ichiyon startup keeps existing default fallback",
        resolve_startup_for_test("ichiyon", {}, "既存入室") == "既存入室",
        "ichiyon fallback",
    )
    record(
        results,
        "disabled startup setting suppresses line",
        resolve_startup_for_test(
            "irsia",
            {("irsia", "guild"): {"enabled": False, "join_line": "私が神です"}},
            DEFAULT_REVIVE_LINE,
        )
        is None,
        "disabled",
    )

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
