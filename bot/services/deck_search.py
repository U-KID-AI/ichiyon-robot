import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from bot import config
from bot.services.qr_detector import detect_qr_codes, opencv_available
from bot.services.x_search import XPost, XSearchDisabled, XSearchError, search_recent_posts


DEFAULT_DENY_MESSAGE = "このチャンネルではデッキ検索は使えません。"
DEFAULT_DISABLED_MESSAGE = "デッキ検索はまだ無効"
DEFAULT_ERROR_MESSAGE = "検索でエラー"
DEFAULT_NOT_FOUND_MESSAGE = "見つからなかった"
DEFAULT_ASK_FORMAT_MESSAGE = "クラス名も入れて"
DEFAULT_X_QUERY_TEMPLATE = "({class_label} OR {class_en}) (デッキ OR deck OR QR OR コード) has:images"
MAX_IMAGE_BYTES = 8 * 1024 * 1024

CLASS_ALIASES = {
    "elf": ("エルフ", ["エルフ", "elf", "えるふ"]),
    "royal": ("ロイヤル", ["ロイヤル", "royal", "ロイ"]),
    "witch": ("ウィッチ", ["ウィッチ", "witch", "ウイッチ", "土", "スペル"]),
    "dragon": ("ドラゴン", ["ドラゴン", "dragon", "ドラ"]),
    "nightmare": ("ナイトメア", ["ナイトメア", "nightmare", "ナイト", "nm"]),
    "bishop": ("ビショップ", ["ビショップ", "bishop", "ビショ"]),
    "nemesis": ("ネメシス", ["ネメシス", "nemesis", "ネメ"]),
    "neutral": ("ニュートラル", ["ニュートラル", "neutral", "ニュート"]),
}

_CACHE = {}


@dataclass
class DeckSearchRequest:
    query: str
    class_key: str
    class_label: str
    class_en: str


@dataclass
class DeckSearchResult:
    post: XPost
    image_url: str
    detected_class: str
    qr_score: int
    created_at: str


@dataclass
class DeckSearchStats:
    x_results: int = 0
    media_posts: int = 0
    image_downloaded: int = 0
    qr_detected: int = 0
    candidates: int = 0
    skipped_no_media: int = 0
    skipped_non_photo: int = 0
    skipped_image_fetch: int = 0
    skipped_no_qr: int = 0
    skipped_qr_error: int = 0

    def to_log(self) -> str:
        return (
            "X results={0}, media={1}, downloaded={2}, qr={3}, candidates={4}, "
            "skip_no_media={5}, skip_non_photo={6}, skip_image_fetch={7}, skip_no_qr={8}, skip_qr_error={9}"
        ).format(
            self.x_results,
            self.media_posts,
            self.image_downloaded,
            self.qr_detected,
            self.candidates,
            self.skipped_no_media,
            self.skipped_non_photo,
            self.skipped_image_fetch,
            self.skipped_no_qr,
            self.skipped_qr_error,
        )


def normalize_text(value: str) -> str:
    return value.strip().lower()


def detect_class(text: str) -> Optional[Tuple[str, str]]:
    normalized = normalize_text(text)
    for key, (label, aliases) in CLASS_ALIASES.items():
        for alias in aliases:
            if normalize_text(alias) in normalized:
                return key, label
    return None


def parse_deck_search_command(command_text: str, missing_behavior: str = "ask_format") -> Optional[DeckSearchRequest]:
    text = command_text.strip()
    text = re.sub(r"^(デッキ検索|デッキ|deck)\s*", "", text, flags=re.IGNORECASE).strip()
    found = detect_class(text)
    if found is None:
        if missing_behavior == "latest":
            return DeckSearchRequest(query=text or "デッキ", class_key="", class_label="指定なし", class_en="")
        return None
    class_key, class_label = found
    return DeckSearchRequest(query=text or class_label, class_key=class_key, class_label=class_label, class_en=class_key)


def get_config_int(config_json: Dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(config_json.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def get_config_bool(config_json: Dict[str, Any], key: str, default: bool) -> bool:
    value = config_json.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def build_x_query(request: DeckSearchRequest, config_json: Dict[str, Any]) -> str:
    template = config_json.get("x_query_template") or DEFAULT_X_QUERY_TEMPLATE
    query = template.format(
        class_key=request.class_key,
        class_label=request.class_label,
        class_en=request.class_en,
        query=request.query,
    )
    if not get_config_bool(config_json, "include_retweets", False):
        query += " -is:retweet"
    if not get_config_bool(config_json, "include_replies", False):
        query += " -is:reply"
    return query


def allowed_in_channel(config_json: Dict[str, Any], channel_id: str) -> bool:
    allowed = config_json.get("allowed_channel_ids") or []
    if not allowed:
        return True
    return str(channel_id) in [str(item) for item in allowed]


def cache_key(guild_id: str, channel_id: str, request: DeckSearchRequest) -> str:
    return "{0}:{1}:{2}:{3}".format(guild_id, channel_id, request.class_key, request.query)


def get_cached(key: str, ttl_seconds: int) -> Optional[List[DeckSearchResult]]:
    if ttl_seconds <= 0:
        return None
    item = _CACHE.get(key)
    if item is None:
        return None
    created_at, value = item
    if time.time() - created_at > ttl_seconds:
        _CACHE.pop(key, None)
        return None
    return value


def set_cached(key: str, value: List[DeckSearchResult]) -> None:
    _CACHE[key] = (time.time(), value)


async def fetch_image_bytes(url: str, timeout_seconds: int) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        print("[WARN] deck image fetch failed: {0}".format(exc.__class__.__name__))
        return None
    content_type = response.headers.get("content-type", "")
    if response.status_code >= 400 or not content_type.startswith("image/"):
        return None
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_IMAGE_BYTES:
                return None
        except ValueError:
            pass
    body = response.content
    if len(body) > MAX_IMAGE_BYTES:
        return None
    return body


async def scan_post_images(
    post: XPost,
    class_label: str,
    limit: int,
    timeout_seconds: int,
    stats: Optional[DeckSearchStats] = None,
) -> Optional[DeckSearchResult]:
    scanned = 0
    for media in post.media:
        if media.type and media.type != "photo":
            if stats is not None:
                stats.skipped_non_photo += 1
            continue
        if scanned >= limit:
            break
        scanned += 1
        image_bytes = await fetch_image_bytes(media.url, timeout_seconds)
        if image_bytes is None:
            if stats is not None:
                stats.skipped_image_fetch += 1
            continue
        if stats is not None:
            stats.image_downloaded += 1
        try:
            detections = detect_qr_codes(image_bytes)
        except RuntimeError:
            raise
        except Exception as exc:
            print("[WARN] deck QR detection failed: {0}".format(exc.__class__.__name__))
            if stats is not None:
                stats.skipped_qr_error += 1
            continue
        if not detections:
            if stats is not None:
                stats.skipped_no_qr += 1
            continue
        best = sorted(detections, key=lambda item: item.score, reverse=True)[0]
        if stats is not None:
            stats.qr_detected += 1
        return DeckSearchResult(
            post=post,
            image_url=media.url,
            detected_class=class_label,
            qr_score=best.score,
            created_at=post.created_at,
        )
    return None


async def search_decks(guild_id: str, channel_id: str, command_text: str, config_json: Dict[str, Any]) -> str:
    if not allowed_in_channel(config_json, channel_id):
        return config_json.get("deny_message") or DEFAULT_DENY_MESSAGE

    missing_behavior = config_json.get("missing_format_behavior") or "ask_format"
    if config_json.get("class_filter_required") is False:
        missing_behavior = "latest"
    request = parse_deck_search_command(command_text, missing_behavior)
    if request is None:
        return DEFAULT_ASK_FORMAT_MESSAGE if missing_behavior == "ask_format" else DEFAULT_NOT_FOUND_MESSAGE

    if not config.X_SEARCH_ENABLED:
        return DEFAULT_DISABLED_MESSAGE
    if not config.X_BEARER_TOKEN.strip():
        return DEFAULT_DISABLED_MESSAGE
    if not opencv_available():
        return "画像判定が使えません"

    max_results = get_config_int(config_json, "max_results", 3, 1, 10)
    timeout_seconds = get_config_int(config_json, "request_timeout_seconds", 10, 1, 30)
    cache_ttl_seconds = get_config_int(config_json, "cache_ttl_seconds", 60, 0, 3600)
    image_scan_limit = get_config_int(config_json, "image_scan_limit", 8, 1, 50)
    search_limit = get_config_int(config_json, "x_search_max_results", config.X_SEARCH_MAX_RESULTS, 10, 100)
    key = cache_key(guild_id, channel_id, request)
    cached = get_cached(key, cache_ttl_seconds)
    if cached is not None:
        return format_results(request, cached, max_results)

    query = build_x_query(request, config_json)
    print("[INFO] deck search query: {0}".format(query))
    stats = DeckSearchStats()
    try:
        posts = await search_recent_posts(query, search_limit, timeout_seconds)
    except XSearchDisabled:
        return DEFAULT_DISABLED_MESSAGE
    except XSearchError:
        return DEFAULT_ERROR_MESSAGE

    stats.x_results = len(posts)
    results = []
    for post in posts:
        if not post.media:
            stats.skipped_no_media += 1
            continue
        stats.media_posts += 1
        found = await scan_post_images(post, request.class_label, image_scan_limit, timeout_seconds, stats)
        if found is not None:
            results.append(found)
            stats.candidates = len(results)
        if len(results) >= max_results:
            break
    set_cached(key, results)
    stats.candidates = len(results)
    print("[INFO] deck search stats: {0}".format(stats.to_log()))
    return format_results(request, results, max_results)


def summarize_text(text: str, limit: int = 80) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."


def format_results(request: DeckSearchRequest, results: List[DeckSearchResult], max_results: int) -> str:
    if not results:
        return DEFAULT_NOT_FOUND_MESSAGE
    lines = ["{0}のデッキ候補".format(request.class_label)]
    for index, result in enumerate(results[:max_results], start=1):
        lines.append(
            "{0}. {1}\n{2}\n{3} / QR検出済み".format(
                index,
                summarize_text(result.post.text),
                result.post.url,
                result.detected_class,
            )
        )
    return "\n".join(lines)
