import sys
from pathlib import Path
from typing import Any, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin.bots import parse_permission_value
from bot.repositories.permissions import role_allows


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


def main() -> int:
    results: List[Tuple[str, bool, Any]] = []

    record(
        results,
        "bot level permission parses",
        parse_permission_value("irsia:guild_admin") == {"bot_id": "irsia", "guild_id": None, "role": "guild_admin"},
        parse_permission_value("irsia:guild_admin"),
    )
    record(
        results,
        "guild level permission parses",
        parse_permission_value("irsia:928619302213533736:editor")
        == {"bot_id": "irsia", "guild_id": "928619302213533736", "role": "editor"},
        parse_permission_value("irsia:928619302213533736:editor"),
    )
    record(
        results,
        "invalid role is ignored",
        parse_permission_value("irsia:owner") is None,
        parse_permission_value("irsia:owner"),
    )
    record(
        results,
        "role hierarchy allows guild admin to edit",
        role_allows("guild_admin", "editor"),
        "guild_admin >= editor",
    )
    record(
        results,
        "role hierarchy blocks viewer from edit",
        not role_allows("viewer", "editor"),
        "viewer < editor",
    )

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
