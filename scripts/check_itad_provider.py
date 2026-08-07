import asyncio
import os
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
    old_key = os.environ.pop("ITAD_API_KEY", None)
    try:
        skipped = await game_provider.fetch_itad_price_quote("1414850", "Nickelodeon")
        results.append(check("missing ITAD key is skipped", skipped.status == "skipped" and skipped.error_code == "not_configured"))
    finally:
        if old_key is not None:
            os.environ["ITAD_API_KEY"] = old_key

    os.environ["ITAD_API_KEY"] = "dummy"
    original_fetch = game_provider.fetch_json
    original_post = game_provider._post_json

    async def fake_fetch_json(url, policy=None, headers=None):
        results.append(check("ITAD key is passed as authorization header", headers and "Authorization" in headers))
        return {"found": True, "game": {"id": "itad-game-id", "title": "Nickelodeon", "url": "https://example.com/itad"}}

    async def fake_post_json(url, body, headers):
        results.append(check("ITAD POST body uses game id list", body == ["itad-game-id"], body))
        if "overview" in url:
            return [{"id": "itad-game-id", "deals": [{"price": {"amount": 1234, "currency": "JPY", "formatted": "1,234円"}}]}]
        return [{"id": "itad-game-id", "low": {"price": {"amount": 999, "currency": "JPY", "formatted": "999円"}}}]

    game_provider._PRICE_CACHE.clear()
    game_provider.fetch_json = fake_fetch_json
    game_provider._post_json = fake_post_json
    try:
        quote = await game_provider.fetch_itad_price_quote("1414850", "Nickelodeon")
    finally:
        game_provider.fetch_json = original_fetch
        game_provider._post_json = original_post
        if old_key is None:
            os.environ.pop("ITAD_API_KEY", None)
        else:
            os.environ["ITAD_API_KEY"] = old_key
    results.append(check("ITAD current price parsed", quote.current_price == 1234 and quote.formatted_current_price == "1,234円", quote))
    results.append(check("ITAD historical low parsed", quote.historical_low == 999 and quote.formatted_historical_low == "999円", quote))
    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
