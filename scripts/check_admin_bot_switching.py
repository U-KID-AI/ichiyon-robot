import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin import servers


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakePermissionRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_manageable_guilds_for_bot(self, bot_id: str, discord_user_id: str) -> List[Dict[str, Any]]:
        if bot_id == "irsia":
            return [
                {
                    "guild_id": "928619302213533736",
                    "name": "神聖イルシア皇国",
                    "icon_url": None,
                    "enabled": True,
                    "role": "guild_admin",
                }
            ]
        return []

    def can_access_bot_guild(self, bot_id: str, guild_id: str, discord_user_id: str) -> bool:
        return bot_id == "irsia" and guild_id == "928619302213533736"


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


def main() -> int:
    original_get_connection = servers.get_connection
    original_permission_repository = servers.PermissionRepository
    results: List[Tuple[str, bool, Any]] = []
    try:
        servers.get_connection = lambda: FakeConnection()
        servers.PermissionRepository = FakePermissionRepository
        irsia_guilds = servers.list_manageable_servers("user", "irsia")
        ichiyon_guilds = servers.list_manageable_servers("user", "ichiyon")
        record(results, "authorized bot guild is visible", len(irsia_guilds) == 1, irsia_guilds)
        record(results, "unauthorized bot guilds are hidden", ichiyon_guilds == [], ichiyon_guilds)
        record(
            results,
            "authorized bot guild access is true",
            servers.can_access_guild("928619302213533736", "user", "irsia") is True,
            "irsia",
        )
        record(
            results,
            "unauthorized bot guild access is false",
            servers.can_access_guild("928619302213533736", "user", "ichiyon") is False,
            "ichiyon",
        )
    finally:
        servers.get_connection = original_get_connection
        servers.PermissionRepository = original_permission_repository

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
