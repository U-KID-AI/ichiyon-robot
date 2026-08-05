import asyncio
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.repositories.games import GameRepository
from bot.services import game_provider
from bot.services.interaction_panel import GamePanelView


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


async def fake_fetch_json(url, policy=None, headers=None):
    if "storesearch" in url:
        return {"items": [{"id": 10, "name": "Fake Game"}]}
    return {
        "10": {
            "success": True,
            "data": {
                "name": "Fake Game",
                "type": "game",
                "is_free": False,
                "price_overview": {
                    "final": 1200,
                    "initial": 2400,
                    "discount_percent": 50,
                    "currency": "JPY",
                },
                "release_date": {"date": "2026年1月1日"},
                "platforms": {"windows": True, "mac": False, "linux": True},
                "short_description": "safe",
            },
        }
    }


async def run_provider_check(results):
    original = game_provider.fetch_json
    game_provider.fetch_json = fake_fetch_json
    try:
        candidates = await game_provider.search_steam_games("fake")
    finally:
        game_provider.fetch_json = original
    results.append(check("steam search candidate", len(candidates) == 1))
    candidate = candidates[0]
    results.append(check("price parsed", candidate.last_known_price == 1200))
    results.append(check("regular price parsed", candidate.last_known_regular_price == 2400))
    results.append(check("discount parsed", candidate.last_known_discount_percent == 50))
    results.append(check("historical low safe unavailable", candidate.historical_low is None))


def main() -> int:
    results = []
    view = GamePanelView()
    ids = [item.custom_id for item in view.children if hasattr(item, "custom_id")]
    for required in ("search", "owned_list", "wishlist_list", "backlog_list", "recent", "back"):
        results.append(check("game button {0}".format(required), any("game:{0}".format(required) in item for item in ids)))
    repo_source = Path("bot/repositories/games.py").read_text(encoding="utf-8")
    results.append(check("user game entries scoped by bot guild user", "bot_id = %s AND guild_id = %s AND discord_user_id = %s" in repo_source))
    migration = Path("migrations/040_add_games.sql").read_text(encoding="utf-8")
    results.append(check("games table migration", "CREATE TABLE IF NOT EXISTS games" in migration))
    results.append(check("user_game_entries unique scope", "user_game_entries_scope_unique" in migration))
    asyncio.run(run_provider_check(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
