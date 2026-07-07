import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin import servers
from admin.role_labels import role_description


REPOSITORY_PATH = PROJECT_ROOT / "bot" / "repositories" / "permissions.py"
MIGRATION_028_PATH = PROJECT_ROOT / "migrations" / "028_register_irsia_production_guilds.sql"
MIGRATION_029_PATH = PROJECT_ROOT / "migrations" / "029_fix_irsia_bot_guilds_and_voice_lines.sql"
MIGRATION_030_PATH = PROJECT_ROOT / "migrations" / "030_correct_irsia_ichiyon_lab_guild_id.sql"


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
        repository_source = REPOSITORY_PATH.read_text(encoding="utf-8")
        migration_source = MIGRATION_028_PATH.read_text(encoding="utf-8")
        migration_029_source = MIGRATION_029_PATH.read_text(encoding="utf-8")
        migration_030_source = MIGRATION_030_PATH.read_text(encoding="utf-8")
        record(
            results,
            "bot guild mapping table is declared",
            "CREATE TABLE IF NOT EXISTS bot_guilds" in migration_source
            and "CREATE TABLE IF NOT EXISTS bot_guilds" in migration_029_source,
            "bot_guilds",
        )
        record(
            results,
            "irsia target guilds are mapped to bot",
            "('irsia', '1520964851046944900', TRUE)" in migration_source
            and "('irsia', '928619302213533736', TRUE)" in migration_source,
            "irsia guilds",
        )
        record(
            results,
            "irsia lab guild is mapped to bot",
            "('irsia', '1515983621461245972', TRUE)" in migration_030_source,
            "irsia lab",
        )
        record(
            results,
            "wrong irsia lab guild is removed from bot mapping",
            "DELETE FROM bot_guilds" in migration_030_source
            and "bot_id = 'irsia'" in migration_030_source
            and "guild_id = '1392174489609179327'" in migration_030_source,
            "irsia wrong guild removed from bot_guilds",
        )
        record(
            results,
            "correct irsia lab guild exists in guilds",
            "('1515983621461245972', 'いちよんラボ', TRUE)" in migration_030_source,
            "guilds correct lab",
        )
        record(
            results,
            "wrong guild is not deleted from guilds",
            "DELETE FROM guilds" not in migration_030_source,
            "guilds wrong id retained",
        )
        record(
            results,
            "irsia voice line uses corrected lab guild",
            "('irsia', '1515983621461245972', '', '', TRUE)" in migration_030_source
            and "DELETE FROM bot_voice_lines" in migration_030_source,
            "voice line corrected lab",
        )
        record(
            results,
            "existing guilds are mapped to ichiyon except irsia only guilds",
            "SELECT 'ichiyon', g.guild_id, TRUE" in migration_029_source
            and "'1520964851046944900', '928619302213533736'" in migration_029_source,
            "ichiyon mapping",
        )
        record(
            results,
            "bot guild mapping filters guild list",
            "list_configured_guilds_for_bot" in repository_source
            and "FROM bot_guilds bg" in repository_source,
            "repository",
        )
        record(
            results,
            "bot list deduplicates by bot_id",
            "bots_by_id" in repository_source
            and "ROLE_LEVELS.get(row.get(\"role\") or \"\", 0)" in repository_source
            and "return sorted(" in repository_source,
            "highest role per bot_id",
        )
        record(
            results,
            "bot list no longer uses distinct role rows",
            "SELECT DISTINCT\n                    b.*,\n                    p.role" not in repository_source,
            "role rows are collapsed in repository",
        )
        record(
            results,
            "role descriptions explain editor and guild admin difference",
            "通常設定を変更できる" in role_description("editor")
            and "管理者向け設定まで変更できる" in role_description("guild_admin"),
            "editor={0} guild_admin={1}".format(role_description("editor"), role_description("guild_admin")),
        )
    finally:
        servers.get_connection = original_get_connection
        servers.PermissionRepository = original_permission_repository

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
