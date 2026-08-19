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


def price_payload(title_id="70010000057297", sale=True):
    row = {
        "title_id": int(title_id),
        "sales_status": "onsale",
        "regular_price": {"amount": "6,578円", "currency": "JPY", "raw_value": "6578"},
    }
    if sale:
        row["discount_price"] = {
            "amount": "290円",
            "currency": "JPY",
            "raw_value": "290",
            "start_datetime": "2026-08-05T15:00:00Z",
            "end_datetime": "2026-08-27T14:59:59Z",
        }
    return {"personalized": False, "country": "JP", "prices": [row]}


async def main_async():
    results = []
    game_provider._PRICE_CACHE.clear()
    original_fetch_json = game_provider.fetch_json
    original_fetch_text = game_provider._fetch_text

    parsed = game_provider._parse_nintendo_price_payload(price_payload(sale=False), "70010000057297", "Nickelodeon")
    results.append(check("Nintendo normal price parses", parsed is not None and parsed.current_price == 6578 and parsed.regular_price == 6578, parsed))
    results.append(check("Nintendo normal formatted price parses", parsed and parsed.formatted_current_price == "6,578円", parsed.formatted_current_price if parsed else ""))

    sale = game_provider._parse_nintendo_price_payload(price_payload(), "70010000057297", "Nickelodeon")
    results.append(check("Nintendo sale price parses", sale is not None and sale.current_price == 290 and sale.regular_price == 6578, sale))
    results.append(check("Nintendo sale discount percent calculates", sale and sale.discount_percent == 96, sale.discount_percent if sale else ""))
    results.append(check("Nintendo sale end is preserved", sale and sale.metadata.get("sale_end") == "2026-08-27T14:59:59Z", sale.metadata if sale else ""))

    missing = game_provider._parse_nintendo_price_payload({"prices": []}, "70010000057297")
    results.append(check("Nintendo missing product returns none", missing is None, missing))
    malformed = game_provider._parse_nintendo_price_payload({"unexpected": []}, "70010000057297")
    results.append(check("Nintendo malformed response returns none", malformed is None, malformed))
    results.append(check("Nintendo invalid id is rejected", game_provider._safe_nintendo_title_id("../secret") == ""))

    async def fake_fetch_json(url, policy=None, headers=None):
        results.append(check("Nintendo price endpoint is official JP price API", url.startswith(game_provider.NINTENDO_PRICE_URL.split("?")[0]) and "country=JP" in url and "lang=jp" in url, url))
        return price_payload()

    game_provider.fetch_json = fake_fetch_json
    try:
        quote = await game_provider.fetch_nintendo_price_quote("70010000057297", "Nickelodeon")
        results.append(check("Nintendo quote is ok", quote.ok and quote.provider == "nintendo", quote))
        results.append(check("Nintendo quote uses Store URL", "store-jp.nintendo.com" in quote.store_url and "70010000057297" in quote.store_url, quote.store_url))
        cached = await game_provider.fetch_nintendo_price_quote("70010000057297", "Nickelodeon")
        results.append(check("Nintendo quote cache hit works", cached.metadata.get("cache") == "hit", cached.metadata))
    finally:
        game_provider.fetch_json = original_fetch_json
        game_provider._PRICE_CACHE.clear()

    async def failing_fetch_json(url, policy=None, headers=None):
        raise game_provider.ExternalHttpError("not found", status_code=404)

    game_provider.fetch_json = failing_fetch_json
    try:
        failed = await game_provider.fetch_nintendo_price_quote("70010000057297", "Nickelodeon")
        results.append(check("Nintendo API 404 is isolated", failed.status == "error" and failed.error_code == "http_404", failed))
    finally:
        game_provider.fetch_json = original_fetch_json

    catalog_xml = """<?xml version="1.0" encoding="UTF-8"?>
<TitleInfoList>
  <TitleInfo><TitleName>ニコロデオン オールスター大乱闘 アルティメットエディション</TitleName><MakerName>3goo</MakerName><Price>6,578円(税込)</Price><SalesDate>2022.11.24</SalesDate><LinkURL>/titles/70010000057297</LinkURL></TitleInfo>
  <TitleInfo><TitleName>ニコロデオン オールスター大乱闘2</TitleName><MakerName>GameMill</MakerName><Price>6,578円(税込)</Price><SalesDate>2023.11.7</SalesDate><LinkURL>/titles/70010000080000</LinkURL></TitleInfo>
  <TitleInfo><TitleName>Nickelodeon Kart Racers 2</TitleName><MakerName>Ripples</MakerName><Price>4,780円(税込)</Price><SalesDate>2020.11.19</SalesDate><LinkURL>/titles/70010000034496</LinkURL></TitleInfo>
</TitleInfoList>"""
    candidates = game_provider._parse_nintendo_catalog_xml(catalog_xml)
    results.append(check("Nintendo catalog parses title id", candidates and candidates[0].provider_game_id == "70010000057297", candidates))
    results.append(check("Nintendo catalog builds Store URL", candidates and candidates[0].store_url.endswith("70010000057297.html"), candidates[0].store_url if candidates else ""))

    async def fake_fetch_text(url):
        results.append(check("Nintendo catalog endpoint is official JP XML", url == game_provider.NINTENDO_JP_CATALOG_URL, url))
        return catalog_xml

    game_provider._NINTENDO_CATALOG_CACHE = (0.0, [])
    game_provider._fetch_text = fake_fetch_text
    try:
        ranked = await game_provider.search_nintendo_games("Nickelodeon All-Star Brawl", limit=2)
        results.append(check("Nintendo search returns candidates", len(ranked) == 2, ranked))
        first = await game_provider.search_nintendo_games("ニコロデオン オールスター大乱闘", limit=1)
        results.append(check("Nintendo Japanese search prefers first title over sequel", first and first[0].provider_game_id == "70010000057297", first))
    finally:
        game_provider._fetch_text = original_fetch_text
        game_provider._NINTENDO_CATALOG_CACHE = (0.0, [])

    async def fake_ntprices_fetch(url, policy=None, headers=None):
        raise AssertionError("NTPrices API should not be called for nsuid official lookup")

    game_provider.fetch_json = fake_fetch_json
    try:
        quote = await game_provider.fetch_ntprices_price_quote("70010000057297", "nsuid", "Nintendo Switch")
        results.append(check("NTPrices nsuid lookup uses Nintendo official provider", quote.provider == "nintendo" and quote.ok, quote))
    finally:
        game_provider.fetch_json = original_fetch_json
        game_provider._PRICE_CACHE.clear()
    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
