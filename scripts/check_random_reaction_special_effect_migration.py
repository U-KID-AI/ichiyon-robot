import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import scripts.migrate_random_reaction_to_special_effect as migration


class Check:
    def __init__(self) -> None:
        self.results = []

    def add(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append({"name": name, "ok": ok, "detail": detail})

    def print_results(self) -> None:
        for result in self.results:
            label = "OK" if result["ok"] else "NG"
            detail = " - {0}".format(result["detail"]) if result["detail"] else ""
            print("[{0}] {1}{2}".format(label, result["name"], detail))
        passed = len([result for result in self.results if result["ok"]])
        print("summary: {0}/{1} OK".format(passed, len(self.results)))

    def ok(self) -> bool:
        return all(result["ok"] for result in self.results)


def main() -> int:
    check = Check()
    check.add("trigger uses low-priority regex all-post match", migration.TRIGGER_TEXT == r".+" and migration.MATCH_TYPE == "regex")
    check.add("trigger priority is lower than normal auto reactions", migration.REACTION_PRIORITY < 0, migration.REACTION_PRIORITY)
    check.add("default reaction has no text/image/emoji response", migration.REACTION_RESPONSE_TEXT is None and migration.REACTION_IMAGE_PATH is None and migration.REACTION_EMOJI is None)
    check.add(
        "0.100 percent converts to one-in-1000",
        migration.probability_percent_to_fraction("0.100") == {"numerator": 1, "denominator": 1000},
        migration.probability_percent_to_fraction("0.100"),
    )
    check.add(
        "one percent converts to one-in-100",
        migration.probability_percent_to_fraction("1.0") == {"numerator": 1, "denominator": 100},
        migration.probability_percent_to_fraction("1.0"),
    )
    source = Path(migration.__file__).read_text(encoding="utf-8")
    check.add("script does not delete random reaction table data", "DELETE" not in source and "DROP" not in source and "TRUNCATE" not in source)
    check.add("old path is disabled only through scoped repository", "RandomReactionRepository(connection, bot_id=bot_id).set_enabled(guild_id, False" in source)
    check.add("special effect remains generic reaction type", '"reaction"' in source and "probability_reaction" not in source)
    check.print_results()
    return 0 if check.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
