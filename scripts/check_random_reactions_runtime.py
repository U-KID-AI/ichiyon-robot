from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Check:
    def __init__(self) -> None:
        self.ok = 0
        self.ng = 0

    def add(self, condition: bool, name: str, detail: object = "") -> None:
        if condition:
            self.ok += 1
            print("[OK] {0}".format(name))
            return
        self.ng += 1
        safe = str(detail).encode("unicode_escape").decode("ascii")
        print("[NG] {0} - {1}".format(name, safe))

    def finish(self) -> int:
        total = self.ok + self.ng
        print("random reaction removal check: {0}/{1} OK".format(self.ok, total))
        return 0 if self.ng == 0 else 1


def read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    check = Check()
    runtime_source = read("bot/services/runtime_db.py")
    admin_main_source = read("admin/main.py")
    servers_source = read("admin/servers.py")
    repository_init_source = read("bot/repositories/__init__.py")
    migration_source = read("migrations/037_add_random_reaction_settings.sql")
    migrate_source = read("scripts/migrate_random_reaction_to_special_effect.py")

    check.add(not (PROJECT_ROOT / "bot" / "services" / "random_reactions.py").exists(), "old random reaction service is removed")
    check.add(not (PROJECT_ROOT / "bot" / "repositories" / "random_reactions.py").exists(), "old random reaction repository is removed")
    check.add(not (PROJECT_ROOT / "admin" / "random_reactions.py").exists(), "old random reaction admin API is removed")
    check.add(not (PROJECT_ROOT / "admin" / "templates" / "random_reactions.html").exists(), "old random reaction template is removed")

    check.add("maybe_add_random_emoji_reaction" not in runtime_source, "runtime does not call old random reaction path")
    check.add("random_reactions" not in admin_main_source, "admin main does not register old random reaction router")
    check.add("random_reactions" not in servers_source and "random-reactions" not in servers_source, "feature list has no old random reaction entry")
    check.add("RandomReactionRepository" not in repository_init_source, "repository package does not export old random reaction repository")

    check.add("CREATE TABLE IF NOT EXISTS random_reaction_settings" in migration_source, "migration 037 keeps old table definition")
    check.add("DROP" not in migration_source and "TRUNCATE" not in migration_source, "migration 037 remains non-destructive")
    check.add("UPDATE random_reaction_settings" in migrate_source, "migration helper disables old setting without repository dependency")
    check.add("DELETE" not in migrate_source and "DROP" not in migrate_source and "TRUNCATE" not in migrate_source, "migration helper does not delete old data")
    check.add('"reaction"' in migrate_source and "non_consuming" in migrate_source, "migration helper creates non-consuming reaction effect")
    check.add("RandomReactionRepository" not in migrate_source, "migration helper does not depend on old repository")

    return check.finish()


if __name__ == "__main__":
    raise SystemExit(main())
