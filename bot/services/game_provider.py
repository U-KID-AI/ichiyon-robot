import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from bot.services.external_http import ExternalHttpError, ExternalHttpPolicy, fetch_json


STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/?term={query}&cc=JP&l=japanese"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={app_id}&cc=JP&l=japanese"
STEAM_STORE_URL = "https://store.steampowered.com/app/{app_id}"
ITAD_BASE_URL = "https://api.isthereanydeal.com"
ITAD_LOOKUP_URL = ITAD_BASE_URL + "/games/lookup/v1?appid={app_id}"
ITAD_OVERVIEW_URL = ITAD_BASE_URL + "/games/overview/v2?country=JP"
ITAD_HISTORY_LOW_URL = ITAD_BASE_URL + "/games/historylow/v1?country=JP"
NTPRICES_BASE_URL = "https://ntprices.com/api/v2"
NTPRICES_GAME_URL = NTPRICES_BASE_URL + "/games/{product_id}?region={region}&include_related=1"
NTPRICES_BY_NSUID_URL = NTPRICES_BASE_URL + "/games/by-nsuid/{product_id}?region={region}&include_related=1"
NTPRICES_SEARCH_URL = NTPRICES_BASE_URL + "/games/search?q={query}&region={region}&include_dlc=0"
DEFAULT_GAME_PROVIDER_TIMEOUT_SECONDS = 8.0
DEFAULT_GAME_SEARCH_LIMIT = 5
DEFAULT_GAME_PRICE_CACHE_TTL_SECONDS = 900


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
    metadata: Dict[str, Any] = field(default_factory=dict)

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


@dataclass
class GamePriceQuote:
    provider: str
    store_name: str
    provider_product_id: str
    title: str
    current_price: Optional[int] = None
    regular_price: Optional[int] = None
    discount_percent: Optional[int] = None
    currency: str = "JPY"
    formatted_current_price: str = ""
    formatted_regular_price: str = ""
    historical_low: Optional[int] = None
    formatted_historical_low: str = ""
    store_url: str = ""
    fetched_at: float = 0.0
    status: str = "ok"
    error_code: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


_PRICE_CACHE: Dict[Tuple[str, str, str], Tuple[float, GamePriceQuote]] = {}
_PROVIDER_STATUS: Dict[str, Dict[str, Any]] = {}


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


def price_cache_ttl_seconds() -> int:
    try:
        return max(0, int(os.getenv("GAME_PRICE_CACHE_TTL_SECONDS") or DEFAULT_GAME_PRICE_CACHE_TTL_SECONDS))
    except ValueError:
        return DEFAULT_GAME_PRICE_CACHE_TTL_SECONDS


def provider_status() -> Dict[str, Dict[str, Any]]:
    return dict(_PROVIDER_STATUS)


def _set_provider_status(provider: str, status: str, error_code: str = "") -> None:
    _PROVIDER_STATUS[provider] = {
        "status": status,
        "error_code": error_code,
        "updated_at": int(time.time()),
    }


def _cached(provider: str, lookup_type: str, product_id: str) -> Optional[GamePriceQuote]:
    ttl = price_cache_ttl_seconds()
    if ttl <= 0:
        return None
    key = (provider, lookup_type, product_id)
    item = _PRICE_CACHE.get(key)
    if not item:
        return None
    cached_at, quote = item
    if time.time() - cached_at > ttl:
        _PRICE_CACHE.pop(key, None)
        return None
    clone = GamePriceQuote(**{**quote.__dict__})
    clone.metadata = dict(quote.metadata)
    clone.metadata["cache"] = "hit"
    return clone


def _store_cache(provider: str, lookup_type: str, product_id: str, quote: GamePriceQuote) -> GamePriceQuote:
    _PRICE_CACHE[(provider, lookup_type, product_id)] = (time.time(), quote)
    quote.metadata["cache"] = "miss"
    return quote


def normalize_game_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[\s_\-:：]+", " ", text)
    text = re.sub(r"[^\w\s]+", "", text)
    return " ".join(text.split())


def _title_score(query: str, title: str) -> Tuple[int, int]:
    q = normalize_game_title(query)
    t = normalize_game_title(title)
    if not q or not t:
        return (0, 0)
    if q == t:
        return (1000, -len(t))
    if t.startswith(q + " "):
        return (800, -len(t))
    q_words = set(q.split())
    t_words = set(t.split())
    if q_words and q_words.issubset(t_words):
        return (600, -len(t_words - q_words))
    if q in t:
        return (400, -len(t))
    overlap = len(q_words & t_words)
    return (overlap * 50, -abs(len(t) - len(q)))


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_from_minor(value: Optional[int], currency: str) -> str:
    if value is None:
        return ""
    if value == 0:
        return "無料"
    if currency == "JPY":
        return "{0:,}円".format(int(value))
    return "{0} {1}".format(currency or "", value)


def _normalize_steam_raw_price(value: Any, currency: str) -> Optional[int]:
    raw = _as_int(value)
    return raw


def _steam_price_value(overview: Dict[str, Any], key: str, formatted_key: str, currency: str) -> Tuple[Optional[int], str]:
    formatted = str(overview.get(formatted_key) or "").strip()
    raw = _normalize_steam_raw_price(overview.get(key), currency)
    if formatted:
        if currency == "JPY":
            digits = re.sub(r"[^0-9]+", "", formatted)
            if digits:
                raw = int(digits)
            return raw, formatted.replace("¥", "").replace("￥", "").strip()
        return raw, formatted
    return raw, _format_from_minor(raw, currency)


def _parse_price_overview(data: Dict[str, Any]) -> Dict[str, Any]:
    overview = data.get("price_overview")
    if not isinstance(overview, dict):
        if data.get("is_free"):
            return {
                "price": 0,
                "regular_price": 0,
                "discount": 0,
                "currency": "JPY",
                "formatted_price": "無料",
                "formatted_regular_price": "無料",
            }
        return {
            "price": None,
            "regular_price": None,
            "discount": None,
            "currency": "JPY",
            "formatted_price": "",
            "formatted_regular_price": "",
        }
    currency = str(overview.get("currency") or "JPY").upper()
    final, final_formatted = _steam_price_value(overview, "final", "final_formatted", currency)
    initial, initial_formatted = _steam_price_value(overview, "initial", "initial_formatted", currency)
    return {
        "price": final,
        "regular_price": initial,
        "discount": _as_int(overview.get("discount_percent")),
        "currency": currency,
        "formatted_price": final_formatted,
        "formatted_regular_price": initial_formatted,
        "raw_final": overview.get("final"),
        "raw_initial": overview.get("initial"),
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
            "formatted_price": price["formatted_price"],
            "formatted_regular_price": price["formatted_regular_price"],
            "raw_final": price.get("raw_final"),
            "raw_initial": price.get("raw_initial"),
            "price_history_provider": "itad_optional",
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
    scored = sorted(
        [item for item in items if isinstance(item, dict)],
        key=lambda item: _title_score(text, str(item.get("name") or "")),
        reverse=True,
    )
    candidates: List[GameSearchCandidate] = []
    for item in scored[: max(1, min(limit, 10))]:
        app_id = str(item.get("id") or "").strip()
        title = str(item.get("name") or "").strip()
        detail = await fetch_steam_app_detail(app_id, title)
        if detail is not None:
            candidates.append(detail)
    return candidates


async def fetch_steam_price_quote(app_id: str, display_name: str = "") -> GamePriceQuote:
    cached = _cached("steam", "app_id", str(app_id))
    if cached:
        return cached
    try:
        candidate = await fetch_steam_app_detail(app_id, display_name)
    except ExternalHttpError as exc:
        code = "http_{0}".format(exc.status_code) if exc.status_code else "request_failed"
        _set_provider_status("steam", "error", code)
        return GamePriceQuote("steam", "Steam", str(app_id), display_name or str(app_id), status="error", error_code=code)
    except Exception:
        _set_provider_status("steam", "error", "request_failed")
        return GamePriceQuote("steam", "Steam", str(app_id), display_name or str(app_id), status="error", error_code="request_failed")
    if candidate is None:
        _set_provider_status("steam", "error", "not_found")
        return GamePriceQuote("steam", "Steam", str(app_id), display_name or str(app_id), status="error", error_code="not_found")
    _set_provider_status("steam", "ok")
    quote = GamePriceQuote(
        provider="steam",
        store_name="Steam",
        provider_product_id=candidate.provider_game_id,
        title=candidate.title,
        current_price=candidate.last_known_price,
        regular_price=candidate.last_known_regular_price,
        discount_percent=candidate.last_known_discount_percent,
        currency=candidate.currency,
        formatted_current_price=str(candidate.metadata.get("formatted_price") or ""),
        formatted_regular_price=str(candidate.metadata.get("formatted_regular_price") or ""),
        historical_low=candidate.historical_low,
        formatted_historical_low="",
        store_url=candidate.store_url,
        fetched_at=time.time(),
        metadata=dict(candidate.metadata),
    )
    return _store_cache("steam", "app_id", str(app_id), quote)


def itad_api_key() -> str:
    return str(os.getenv("ITAD_API_KEY") or "").strip()


def ntprices_api_key() -> str:
    return str(os.getenv("NTPRICES_API_KEY") or "").strip()


def ntprices_region() -> str:
    return str(os.getenv("NTPRICES_REGION") or "JP").strip().upper() or "JP"


async def _post_json(url: str, body: Any, headers: Dict[str, str]) -> Any:
    policy = game_http_policy()
    async with httpx.AsyncClient(timeout=policy.read_timeout, follow_redirects=True, trust_env=False) as client:
        response = await client.post(url, json=body, headers=headers)
        if response.status_code >= 400:
            raise ExternalHttpError("external request failed with status {0}".format(response.status_code), status_code=response.status_code, retryable=response.status_code in (429, 500, 502, 503, 504))
        return response.json()


async def fetch_itad_price_quote(steam_app_id: str, display_name: str = "") -> GamePriceQuote:
    key = itad_api_key()
    if not key:
        _set_provider_status("itad", "not_configured")
        return GamePriceQuote("itad", "PC過去最安(ITAD)", str(steam_app_id), display_name or str(steam_app_id), status="skipped", error_code="not_configured")
    cached = _cached("itad", "steam_app_id", str(steam_app_id))
    if cached:
        return cached
    headers = {"Authorization": "Bearer {0}".format(key)}
    try:
        lookup = await fetch_json(ITAD_LOOKUP_URL.format(app_id=quote(str(steam_app_id))), policy=game_http_policy(), headers=headers)
        game = lookup.get("game") if isinstance(lookup, dict) and lookup.get("found") else None
        itad_id = str(game.get("id") or "") if isinstance(game, dict) else ""
        if not itad_id:
            _set_provider_status("itad", "error", "not_found")
            return GamePriceQuote("itad", "PC過去最安(ITAD)", str(steam_app_id), display_name or str(steam_app_id), status="error", error_code="not_found")
        overview = await _post_json(ITAD_OVERVIEW_URL, [itad_id], headers)
        history = await _post_json(ITAD_HISTORY_LOW_URL, [itad_id], headers)
    except ExternalHttpError as exc:
        code = "http_{0}".format(exc.status_code) if exc.status_code else "request_failed"
        _set_provider_status("itad", "error", code)
        return GamePriceQuote("itad", "PC過去最安(ITAD)", str(steam_app_id), display_name or str(steam_app_id), status="error", error_code=code)
    except Exception:
        _set_provider_status("itad", "error", "request_failed")
        return GamePriceQuote("itad", "PC過去最安(ITAD)", str(steam_app_id), display_name or str(steam_app_id), status="error", error_code="request_failed")

    current = _parse_itad_overview(overview, itad_id)
    low = _parse_itad_history_low(history, itad_id)
    title = str((game or {}).get("title") or display_name or steam_app_id)
    _set_provider_status("itad", "ok")
    quote_obj = GamePriceQuote(
        provider="itad",
        store_name="PC過去最安(ITAD)",
        provider_product_id=itad_id,
        title=title,
        current_price=current[0],
        formatted_current_price=current[1],
        historical_low=low[0],
        formatted_historical_low=low[1],
        currency=current[2] or low[2] or "",
        store_url=str((game or {}).get("url") or ""),
        fetched_at=time.time(),
        metadata={"steam_app_id": str(steam_app_id), "cache": "miss"},
    )
    return _store_cache("itad", "steam_app_id", str(steam_app_id), quote_obj)


def _parse_price_object(price: Any) -> Tuple[Optional[int], str, str]:
    if not isinstance(price, dict):
        return None, "", ""
    amount = price.get("amount")
    currency = str(price.get("currency") or "").upper()
    formatted = str(price.get("formatted") or price.get("formattedAmount") or "").strip()
    value = _as_int(amount)
    if formatted:
        return value, formatted, currency
    return value, _format_from_minor(value, currency), currency


def _parse_itad_overview(payload: Any, itad_id: str) -> Tuple[Optional[int], str, str]:
    items = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return None, "", ""
    for item in items:
        if not isinstance(item, dict) or str(item.get("id") or "") != itad_id:
            continue
        deals = item.get("deals")
        if isinstance(deals, list) and deals:
            return _parse_price_object(deals[0].get("price") if isinstance(deals[0], dict) else None)
        price = item.get("price") or item.get("current")
        return _parse_price_object(price)
    return None, "", ""


def _parse_itad_history_low(payload: Any, itad_id: str) -> Tuple[Optional[int], str, str]:
    items = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return None, "", ""
    for item in items:
        if not isinstance(item, dict) or str(item.get("id") or "") != itad_id:
            continue
        low = item.get("low") if isinstance(item.get("low"), dict) else item
        return _parse_price_object(low.get("price") if isinstance(low, dict) else None)
    return None, "", ""


async def fetch_ntprices_price_quote(product_id: str, lookup_type: str = "ppid", display_name: str = "") -> GamePriceQuote:
    key = ntprices_api_key()
    region = ntprices_region()
    if not key:
        _set_provider_status("ntprices", "not_configured")
        return GamePriceQuote("ntprices", "Nintendo Switch", str(product_id), display_name or str(product_id), status="skipped", error_code="not_configured")
    cached = _cached("ntprices", lookup_type, str(product_id))
    if cached:
        return cached
    headers = {"X-API-Key": key}
    endpoint = NTPRICES_BY_NSUID_URL if lookup_type == "nsuid" else NTPRICES_GAME_URL
    try:
        payload = await fetch_json(endpoint.format(product_id=quote(str(product_id)), region=quote(region.lower())), policy=game_http_policy(), headers=headers)
    except ExternalHttpError as exc:
        code = "region_not_in_plan" if exc.status_code == 403 else "http_{0}".format(exc.status_code) if exc.status_code else "request_failed"
        _set_provider_status("ntprices", "error", code)
        return GamePriceQuote("ntprices", "Nintendo Switch", str(product_id), display_name or str(product_id), status="error", error_code=code)
    except Exception:
        _set_provider_status("ntprices", "error", "request_failed")
        return GamePriceQuote("ntprices", "Nintendo Switch", str(product_id), display_name or str(product_id), status="error", error_code="request_failed")
    game = _unwrap_ntprices_game(payload)
    if not game:
        _set_provider_status("ntprices", "error", "not_found")
        return GamePriceQuote("ntprices", "Nintendo Switch", str(product_id), display_name or str(product_id), status="error", error_code="not_found")
    _set_provider_status("ntprices", "ok")
    quote_obj = _ntprices_quote_from_game(game, product_id, lookup_type, region)
    return _store_cache("ntprices", lookup_type, str(product_id), quote_obj)


async def search_ntprices_games(query: str, limit: int = 5) -> List[GamePriceQuote]:
    key = ntprices_api_key()
    region = ntprices_region()
    if not key:
        _set_provider_status("ntprices", "not_configured")
        return []
    try:
        payload = await fetch_json(
            NTPRICES_SEARCH_URL.format(query=quote(str(query or "")), region=quote(region.lower())),
            policy=game_http_policy(),
            headers={"X-API-Key": key},
        )
    except Exception:
        _set_provider_status("ntprices", "error", "search_failed")
        return []
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    ranked = sorted(items, key=lambda row: _title_score(query, str(row.get("ProductName") or "")), reverse=True)
    return [_ntprices_quote_from_game(row, str(row.get("PPID") or ""), "ppid", region) for row in ranked[: max(1, min(limit, 10))] if isinstance(row, dict)]


def _unwrap_ntprices_game(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if "ProductName" in payload:
            return payload
    return None


def _ntprices_quote_from_game(game: Dict[str, Any], product_id: str, lookup_type: str, region: str) -> GamePriceQuote:
    sale = _as_int(game.get("SalePrice"))
    base = _as_int(game.get("BasePrice"))
    current = sale if sale is not None else base
    formatted_sale = str(game.get("formattedSalePrice") or "").strip()
    formatted_base = str(game.get("formattedBasePrice") or "").strip()
    formatted_current = formatted_sale or formatted_base
    low = _as_int(game.get("LowestEverPrice"))
    return GamePriceQuote(
        provider="ntprices",
        store_name="Nintendo Switch",
        provider_product_id=str(game.get("PPID") or product_id),
        title=str(game.get("ProductName") or product_id),
        current_price=current,
        regular_price=base,
        discount_percent=_as_int(game.get("DiscPerc")) or 0,
        currency=region,
        formatted_current_price=formatted_current,
        formatted_regular_price=formatted_base,
        historical_low=low,
        formatted_historical_low=str(game.get("formattedLowestEverPrice") or "").strip(),
        store_url=str(game.get("NTPricesURL") or ""),
        fetched_at=time.time(),
        metadata={
            "lookup_type": lookup_type,
            "region": region,
            "nsuid": game.get("NSUID"),
            "is_switch": bool(game.get("IsSwitch")),
            "is_switch2": bool(game.get("IsSwitch2")),
            "related_count": len(game.get("related") or []) if isinstance(game.get("related"), list) else 0,
            "cache": "miss",
        },
    )


async def fetch_price_quote(provider: str, product_id: str, lookup_type: str = "", display_name: str = "") -> GamePriceQuote:
    key = str(provider or "").strip().lower()
    if key == "steam":
        return await fetch_steam_price_quote(product_id, display_name)
    if key == "itad":
        return await fetch_itad_price_quote(product_id, display_name)
    if key == "ntprices":
        return await fetch_ntprices_price_quote(product_id, lookup_type or "ppid", display_name)
    return GamePriceQuote(key, key, str(product_id), display_name or str(product_id), status="error", error_code="unknown_provider")


def format_price(price: Optional[int], currency: str = "JPY", formatted: str = "") -> str:
    if formatted:
        return formatted
    if price is None:
        return "未取得"
    if price == 0:
        return "無料"
    if str(currency or "").upper() == "JPY":
        return "{0:,}円".format(int(price))
    return "{0} {1}".format(currency or "", price)


def provider_status_label(provider: str) -> str:
    status = _PROVIDER_STATUS.get(provider) or {}
    state = status.get("status") or "unknown"
    error = status.get("error_code") or ""
    if state == "ok":
        return "利用可能"
    if state == "not_configured":
        return "API key未設定"
    if error:
        return "エラー: {0}".format(error)
    return "未確認"
