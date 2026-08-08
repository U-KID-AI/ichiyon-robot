import asyncio
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import game_provider


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def steam_detail(app_id, name):
    return {
        str(app_id): {
            "success": True,
            "data": {
                "name": name,
                "type": "game",
                "platforms": {"windows": True, "mac": False, "linux": False},
                "price_overview": {
                    "currency": "JPY",
                    "final": 877800,
                    "final_formatted": "¥ 8,778",
                    "initial": 877800,
                    "initial_formatted": "¥ 8,778",
                    "discount_percent": 0,
                },
            },
        }
    }


async def main_async():
    results = []
    calls = []

    async def fake_fetch_json(url, policy=None, headers=None):
        calls.append(url)
        parsed = urlparse(url)
        if "storesearch" in parsed.path:
            params = parse_qs(parsed.query)
            term = params.get("term", [""])[0]
            language = params.get("l", [""])[0]
            country = params.get("cc", [""])[0]
            if term == "サイバーパンク2077" and country == "JP" and language == "japanese":
                return {
                    "items": [
                        {"id": 1091500, "name": "サイバーパンク2077"},
                        {"id": 1091500, "name": "Cyberpunk 2077"},
                        {"id": 2138330, "name": "サイバーパンク2077: DLC"},
                    ]
                }
            if term == "モンスターハンターワイルズ" and country == "JP" and language == "japanese":
                return {"items": [{"id": 2246340, "name": "モンスターハンターワイルズ"}]}
            if term == "fallback-only" and country == "JP" and language == "japanese":
                return {"items": []}
            if term == "fallback-only" and country == "JP" and language == "english":
                return {"items": [{"id": 999001, "name": "Fallback Only"}]}
            return {"items": []}
        if "1091500" in url:
            return steam_detail("1091500", "サイバーパンク2077")
        if "2138330" in url:
            return steam_detail("2138330", "サイバーパンク2077: DLC")
        if "2246340" in url:
            return steam_detail("2246340", "モンスターハンターワイルズ")
        if "999001" in url:
            return steam_detail("999001", "Fallback Only")
        return {}

    original = game_provider.fetch_json
    game_provider.fetch_json = fake_fetch_json
    try:
        cyberpunk = await game_provider.search_steam_games("サイバーパンク2077", limit=3)
        monster_hunter = await game_provider.search_steam_games("モンスターハンターワイルズ", limit=1)
        fallback = await game_provider.search_steam_games("fallback-only", limit=1)
    finally:
        game_provider.fetch_json = original

    results.append(check("Japanese Cyberpunk search returns localized exact first", cyberpunk and cyberpunk[0].provider_game_id == "1091500", [c.provider_game_id for c in cyberpunk]))
    results.append(check("duplicate App ID is removed", [c.provider_game_id for c in cyberpunk].count("1091500") == 1, [c.provider_game_id for c in cyberpunk]))
    results.append(check("localized exact beats DLC candidate", cyberpunk and cyberpunk[0].title == "サイバーパンク2077", cyberpunk[0].title if cyberpunk else "none"))
    results.append(check("Japanese Monster Hunter Wilds search works", monster_hunter and monster_hunter[0].provider_game_id == "2246340", monster_hunter[0].provider_game_id if monster_hunter else "none"))
    results.append(check("English fallback is used only after zero Japanese results", fallback and fallback[0].provider_game_id == "999001", fallback[0].provider_game_id if fallback else "none"))

    search_calls = [url for url in calls if "storesearch" in url]
    results.append(check("primary Steam search uses JP japanese", any("cc=JP" in url and "l=japanese" in url for url in search_calls), search_calls))
    cyberpunk_searches = [url for url in search_calls if "%E3%82%B5%E3%82%A4%E3%83%90%E3%83%BC" in url]
    results.append(check("English fallback is not called when Japanese results exist", len(cyberpunk_searches) == 1, cyberpunk_searches))
    fallback_searches = [url for url in search_calls if "fallback-only" in url]
    results.append(check("fallback search calls Japanese then English", len(fallback_searches) == 2 and "l=japanese" in fallback_searches[0] and "l=english" in fallback_searches[1], fallback_searches))
    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
