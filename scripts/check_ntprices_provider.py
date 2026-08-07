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
    old_key = os.environ.pop("NTPRICES_API_KEY", None)
    old_region = os.environ.get("NTPRICES_REGION")
    os.environ["NTPRICES_REGION"] = "JP"
    try:
        skipped = await game_provider.fetch_ntprices_price_quote("123", "ppid")
        results.append(check("missing NTPrices key is skipped", skipped.status == "skipped" and skipped.error_code == "not_configured"))
    finally:
        if old_key is not None:
            os.environ["NTPRICES_API_KEY"] = old_key

    os.environ["NTPRICES_API_KEY"] = "dummy"
    original = game_provider.fetch_json

    async def fake_fetch_json(url, policy=None, headers=None):
        results.append(check("NTPrices key is passed as X-API-Key", headers and "X-API-Key" in headers))
        results.append(check("NTPrices region is JP", "region=jp" in url, url))
        return {
            "data": {
                "PPID": 111,
                "NSUID": "70010000000001",
                "ProductName": "Nickelodeon All-Star Brawl",
                "BasePrice": 5500,
                "SalePrice": 3300,
                "DiscPerc": 40,
                "LowestEverPrice": 2500,
                "formattedBasePrice": "5,500円",
                "formattedSalePrice": "3,300円",
                "formattedLowestEverPrice": "2,500円",
                "NTPricesURL": "https://example.com/ntprices",
                "IsSwitch": True,
                "IsSwitch2": False,
            }
        }

    game_provider._PRICE_CACHE.clear()
    game_provider.fetch_json = fake_fetch_json
    try:
        quote = await game_provider.fetch_ntprices_price_quote("111", "ppid")
    finally:
        game_provider.fetch_json = original
        if old_key is None:
            os.environ.pop("NTPRICES_API_KEY", None)
        else:
            os.environ["NTPRICES_API_KEY"] = old_key
        if old_region is None:
            os.environ.pop("NTPRICES_REGION", None)
        else:
            os.environ["NTPRICES_REGION"] = old_region
    results.append(check("NTPrices sale price parsed", quote.current_price == 3300 and quote.formatted_current_price == "3,300円", quote))
    results.append(check("NTPrices base price parsed", quote.regular_price == 5500 and quote.formatted_regular_price == "5,500円", quote))
    results.append(check("NTPrices lowest ever parsed", quote.historical_low == 2500 and quote.formatted_historical_low == "2,500円", quote))
    results.append(check("NTPrices uses v2 base URL", game_provider.NTPRICES_BASE_URL.endswith("/api/v2"), game_provider.NTPRICES_BASE_URL))
    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
