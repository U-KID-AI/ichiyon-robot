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
    detail = {
        "success": True,
        "data": {
            "name": "Nickelodeon All-Star Brawl",
            "type": "game",
            "price_overview": {
                "currency": "JPY",
                "initial": 550000,
                "final": 550000,
                "initial_formatted": "¥ 5,500",
                "final_formatted": "¥ 5,500",
                "discount_percent": 0,
            },
            "platforms": {"windows": True},
            "release_date": {"date": "2021"},
        },
    }
    candidate = game_provider._candidate_from_detail("1414850", detail)
    results.append(check("steam raw JPY is scaled to yen", candidate.last_known_price == 5500, candidate.last_known_price))
    results.append(check("steam regular raw JPY is scaled to yen", candidate.last_known_regular_price == 5500, candidate.last_known_regular_price))
    results.append(check("steam formatted current price is preserved", candidate.metadata["formatted_price"] == "5,500", candidate.metadata["formatted_price"]))
    results.append(check("format_price prefers formatted", game_provider.format_price(5500, "JPY", "¥ 5,500") == "¥ 5,500"))
    results.append(check("raw without formatted is not scaled blindly", game_provider._normalize_steam_raw_price(1200, "JPY") == 1200))
    results.append(check("non-JPY raw is not forced as JPY", game_provider._normalize_steam_raw_price(1999, "USD") == 1999))
    sale_detail = {
        "success": True,
        "data": {
            "name": "Sale Game",
            "price_overview": {
                "currency": "JPY",
                "initial": 800000,
                "final": 400000,
                "initial_formatted": "¥ 8,000",
                "final_formatted": "¥ 4,000",
                "discount_percent": 50,
            },
        },
    }
    sale = game_provider._candidate_from_detail("1", sale_detail)
    results.append(check("sale initial/final are scaled independently", sale.last_known_price == 4000 and sale.last_known_regular_price == 8000, (sale.last_known_price, sale.last_known_regular_price)))
    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
