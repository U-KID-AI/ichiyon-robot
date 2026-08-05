import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from bot.services.external_http import ExternalHttpPolicy, fetch_json


STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/?term={query}&cc=JP&l=japanese"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={app_id}&cc=JP&l=japanese"
STEAM_STORE_URL = "https://store.steampowered.com/app/{app_id}"
DEFAULT_GAME_PROVIDER_TIMEOUT_SECONDS = 8.0
DEFAULT_GAME_SEARCH_LIMIT = 5


@dataclass
class GameSearchCandidate:
    provider: str
    provider_game_id: str
    title: str
    store_url: str
    platforms: Dict[str, bool]
    last_known_price: Optional[int] = None
    last_known_regular_price: Optional[int] = None
    last_known_discount_percent: Optional[int] = None
    currency: str = "JPY"
    release_date: str = ""
    historical_low: Optional[int] = None
    metadata: Dict[str, Any] = None

    def to_repository_values(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_game_id": self.provider_game_id,
            "title": self.title,
            "store_url": self.store_url,
            "release_date": self.release_date,
            "platforms_json": json.dumps(self.platforms or {}, ensure_ascii=False),
            "last_known_price": self.last_known_price,
            "last_known_regular_price": self.last_known_regular_price,
            "last_known_discount_percent": self.last_known_discount_percent,
            "currency": self.currency,
            "historical_low": self.historical_low,
            "metadata_json": json.dumps(self.metadata or {}, ensure_ascii=False),
        }


def game_http_policy() -> ExternalHttpPolicy:
    try:
        timeout = float(os.getenv("GAME_PROVIDER_TIMEOUT_SECONDS") or DEFAULT_GAME_PROVIDER_TIMEOUT_SECONDS)
    except ValueError:
        timeout = DEFAULT_GAME_PROVIDER_TIMEOUT_SECONDS
    safe_timeout = max(1.0, timeout)
    return ExternalHttpPolicy(
        connect_timeout=min(5.0, safe_timeout),
        read_timeout=safe_timeout,
        write_timeout=safe_timeout,
        pool_timeout=min(5.0, safe_timeout),
        retries=1,
        backoff_base_seconds=0.4,
    )


def _parse_price_overview(data: Dict[str, Any]) -> Dict[str, Optional[int]]:
    overview = data.get("price_overview")
    if not isinstance(overview, dict):
        if data.get("is_free"):
            return {"price": 0, "regular_price": 0, "discount": 0, "currency": "JPY"}
        return {"price": None, "regular_price": None, "discount": None, "currency": "JPY"}
    return {
        "price": overview.get("final"),
        "regular_price": overview.get("initial"),
        "discount": overview.get("discount_percent"),
        "currency": str(overview.get("currency") or "JPY"),
    }


def _platforms(data: Dict[str, Any]) -> Dict[str, bool]:
    platforms = data.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}
    return {
        "windows": bool(platforms.get("windows")),
        "mac": bool(platforms.get("mac")),
        "linux": bool(platforms.get("linux")),
    }


def _candidate_from_detail(app_id: str, detail: Dict[str, Any], fallback_title: str = "") -> Optional[GameSearchCandidate]:
    if not detail.get("success"):
        return None
    data = detail.get("data")
    if not isinstance(data, dict):
        return None
    title = str(data.get("name") or fallback_title or "").strip()
    if not title:
        return None
    price = _parse_price_overview(data)
    release = data.get("release_date") if isinstance(data.get("release_date"), dict) else {}
    return GameSearchCandidate(
        provider="steam",
        provider_game_id=str(app_id),
        title=title,
        store_url=STEAM_STORE_URL.format(app_id=app_id),
        platforms=_platforms(data),
        last_known_price=price["price"],
        last_known_regular_price=price["regular_price"],
        last_known_discount_percent=price["discount"],
        currency=str(price["currency"] or "JPY"),
        release_date=str(release.get("date") or ""),
        historical_low=None,
        metadata={
            "steam_type": data.get("type"),
            "short_description": data.get("short_description") or "",
            "header_image": data.get("header_image") or "",
            "price_history_provider": "unavailable",
        },
    )


async def fetch_steam_app_detail(app_id: str, fallback_title: str = "") -> Optional[GameSearchCandidate]:
    safe_app_id = "".join(ch for ch in str(app_id or "") if ch.isdigit())
    if not safe_app_id:
        return None
    payload = await fetch_json(STEAM_APPDETAILS_URL.format(app_id=safe_app_id), policy=game_http_policy())
    detail = payload.get(safe_app_id) if isinstance(payload, dict) else None
    if not isinstance(detail, dict):
        return None
    return _candidate_from_detail(safe_app_id, detail, fallback_title)


async def search_steam_games(query: str, limit: int = DEFAULT_GAME_SEARCH_LIMIT) -> List[GameSearchCandidate]:
    text = str(query or "").strip()
    if not text:
        return []
    payload = await fetch_json(STEAM_SEARCH_URL.format(query=quote(text)), policy=game_http_policy())
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    candidates: List[GameSearchCandidate] = []
    for item in items[: max(1, min(limit, 10))]:
        if not isinstance(item, dict):
            continue
        app_id = str(item.get("id") or "").strip()
        title = str(item.get("name") or "").strip()
        detail = await fetch_steam_app_detail(app_id, title)
        if detail is not None:
            candidates.append(detail)
    return candidates


def format_price(price: Optional[int], currency: str = "JPY") -> str:
    if price is None:
        return "未取得"
    if price == 0:
        return "無料"
    if currency == "JPY":
        return "{0:,}円".format(int(price))
    return "{0} {1}".format(currency or "", price)
