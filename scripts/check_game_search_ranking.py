import asyncio
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import game_provider


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


async def main_async():
    results = []
    calls = []

    async def fake_fetch_json(url, policy=None, headers=None):
        if "storesearch" in url:
            if "%E3%82%B5%E3%82%A4%E3%83%90%E3%83%BC" in url:
                return {
                    "items": [
                        {"id": 200001, "name": "サイバーパンク2077 デラックスエディション"},
                        {"id": 1091500, "name": "サイバーパンク2077"},
                    ]
                }
            return {
                "items": [
                    {"id": 2017080, "name": "Nickelodeon All-Star Brawl 2"},
                    {"id": 1414850, "name": "Nickelodeon All-Star Brawl"},
                ]
            }
        if "1091500" in url:
            app_id = "1091500"
            name = "サイバーパンク2077"
        elif "200001" in url:
            app_id = "200001"
            name = "サイバーパンク2077 デラックスエディション"
        else:
            app_id = "2017080" if "2017080" in url else "1414850"
            name = "Nickelodeon All-Star Brawl 2" if app_id == "2017080" else "Nickelodeon All-Star Brawl"
        calls.append(app_id)
        return {app_id: {"success": True, "data": {"name": name, "platforms": {"windows": True}}}}

    original = game_provider.fetch_json
    game_provider.fetch_json = fake_fetch_json
    try:
        first = await game_provider.search_steam_games("Nickelodeon All-Star Brawl", limit=1)
        second = await game_provider.search_steam_games("Nickelodeon All-Star Brawl 2", limit=1)
        japanese = await game_provider.search_steam_games("サイバーパンク2077", limit=1)
    finally:
        game_provider.fetch_json = original

    results.append(check("exact title prefers first game over sequel", first and first[0].provider_game_id == "1414850", first[0].provider_game_id if first else "none"))
    results.append(check("exact sequel title prefers sequel", second and second[0].provider_game_id == "2017080", second[0].provider_game_id if second else "none"))
    results.append(check("Japanese exact title prefers base game over deluxe", japanese and japanese[0].provider_game_id == "1091500", japanese[0].provider_game_id if japanese else "none"))
    results.append(check("normalization ignores case and spacing", game_provider.normalize_game_title("  Nickelodeon　All-Star Brawl ") == "nickelodeon all star brawl"))
    results.append(check("exact score beats prefix score", game_provider._title_score("abc", "abc")[0] > game_provider._title_score("abc", "abc 2")[0]))
    results.append(check("variant penalty applies when query is base title", game_provider._variant_penalty("サイバーパンク2077", "サイバーパンク2077 デラックスエディション") > 0))
    results.append(check("variant penalty does not apply when query contains variant", game_provider._variant_penalty("サイバーパンク2077 デラックスエディション", "サイバーパンク2077 デラックスエディション") == 0))
    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
