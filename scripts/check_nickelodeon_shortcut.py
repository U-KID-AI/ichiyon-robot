import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.repositories.mention_shortcuts import normalize_shortcut_trigger
from bot.services import game_provider


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def main():
    results = []
    migration = (ROOT_DIR / "migrations" / "041_add_mention_shortcuts.sql").read_text(encoding="utf-8")
    results.append(check("nickelodeon trigger normalizes", normalize_shortcut_trigger("ニコロデオン") == "ニコロデオン"))
    results.append(check("nickelodeon shortcut is seeded", "'ニコロデオン'" in migration))
    results.append(check("first Nickelodeon Steam app id is seeded", "'1414850'" in migration))
    results.append(check("sequel Steam app id is not seeded", "'2017080'" not in migration))
    results.append(check("ITAD target uses Steam app id", "'itad', '1414850', 'steam_app_id'" in migration))
    seed_block = migration.split("DO $$", 1)[1]
    results.append(check("uncertain Nintendo target is not guessed", "(v_shortcut_id, 'ntprices'" not in seed_block.lower()))
    results.append(check("first title ranks above sequel", game_provider._title_score("Nickelodeon All-Star Brawl", "Nickelodeon All-Star Brawl") > game_provider._title_score("Nickelodeon All-Star Brawl", "Nickelodeon All-Star Brawl 2")))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
