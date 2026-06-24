import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from bot import config
from bot.services.qr_detector import detect_qr_codes, opencv_available
from bot.services.x_search import XPost, XSearchDisabled, XSearchError, search_posts


DEFAULT_DENY_MESSAGE = "このチャンネルではデッキ検索は使えません。"
DEFAULT_DISABLED_MESSAGE = "デッキ検索はまだ無効"
DEFAULT_ERROR_MESSAGE = "検索でエラー"
DEFAULT_NOT_FOUND_MESSAGE = "おい ないんだが"
DEFAULT_ASK_FORMAT_MESSAGE = "クラス名も入れて"
DEFAULT_FULL_ARCHIVE_UNAVAILABLE_MESSAGE = "過去検索が使えません"
DEFAULT_X_QUERY_TEMPLATE = "{class_search_query} {required_context_query} has:media"
DEFAULT_TIMEOUT_MESSAGE = "検索に時間がかかりすぎたため中断しました"
LEGACY_X_QUERY_TEMPLATES = [
    "({class_label} OR {class_en}) (シャドバ OR Shadowverse OR シャドウバース OR SV) (デッキ OR deck OR QR OR コード) has:images",
    "({class_label} OR {class_en}) (シャドバ OR Shadowverse OR シャドウバース OR SV) (デッキ OR deck OR QR OR コード) has:media",
    "({class_label} OR {class_en}) (デッキ OR deck OR QR OR コード OR レシピ OR 構築) has:images",
    "({class_label} OR {class_en}) (デッキ OR deck OR QR OR コード OR レシピ OR 構築) has:media",
    "({class_label} OR {class_en}) {required_context_query} has:media",
]
DEFAULT_REQUIRED_CONTEXT_TERMS = ["ビヨンド", "beyond"]
DEFAULT_EXCLUDED_KEYWORDS = ["ドラゴンボール", "レジェンズ", "探索コード", "フレンドコード"]
DECK_TRIGGER_ALIASES = ["デッキ検索", "デッキ", "deck"]
HIGH_ACCURACY_WORDS = ["高精度"]
MAX_EXTRA_TERMS = 8
MAX_EXTRA_TERM_LENGTH = 40
MAX_EXTRA_QUERY_LENGTH = 120
MAX_X_QUERY_LENGTH = 480
MAX_IMAGE_BYTES = 3 * 1024 * 1024
ALLOWED_IMAGE_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")
DEFAULT_TOTAL_TIMEOUT_SECONDS = 30
IMAGE_SCAN_CACHE_TTL_SECONDS = 300
HIGH_SCORE_TERMS = ["デッキ", "deck", "レシピ", "構築", "QR", "コード", "BEYOND", "WB", "シャドバ", "Shadowverse"]
LOW_SCORE_TERMS = ["キャンペーン", "100万円", "配信", "YouTube", "大会", "勝ったら", "探索コード", "フレンドコード", "ドラゴンボール", "レジェンズ"]

CLASS_ALIASES = {
    "elf": ("エルフ", ["エルフ", "elf", "えるふ"]),
    "royal": ("ロイヤル", ["ロイヤル", "royal", "ロイ"]),
    "witch": ("ウィッチ", ["ウィッチ", "witch", "ウイッチ", "土", "スペル"]),
    "dragon": ("ドラゴン", ["ドラゴン", "dragon", "ドラ"]),
    "nightmare": ("ナイトメア", ["ナイトメア", "nightmare", "Nightmare", "ナイト", "メア", "Nm", "Ｎｍ", "nm"]),
    "bishop": ("ビショップ", ["ビショップ", "bishop", "ビショ"]),
    "nemesis": ("ネメシス", ["ネメシス", "nemesis", "ネメ"]),
    "neutral": ("ニュートラル", ["ニュートラル", "neutral", "ニュート"]),
}

CLASS_EN_LABELS = {
    "nightmare": "Nightmare",
}

CLASS_SEARCH_TERMS = {
    "elf": ["エルフ", "Elf", "elf", "エル"],
    "royal": ["ロイヤル", "Royal", "royal", "ロイ"],
    "witch": ["ウィッチ", "Witch", "witch", "ウィ"],
    "dragon": ["ドラゴン", "Dragon", "dragon", "ドラ"],
    "nightmare": [
        "ナイトメア",
        "Nightmare",
        "nightmare",
        "メア",
        "Nm",
        "nm",
        "Ｎｍ",
        "ナイトメアビヨンド",
        "メアビヨンド",
        "NightmareBeyond",
    ],
    "bishop": ["ビショップ", "Bishop", "bishop", "ビショ", "ビショプ"],
    "nemesis": ["ネメシス", "Nemesis", "nemesis", "ネメ"],
    "neutral": ["ニュートラル", "Neutral", "neutral", "ニュート"],
}

FORMAT_ALIASES = {
    "rotation": ("ローテーション", ["ローテーション", "ローテ", "rotation", "rotate"]),
    "unlimited": ("アンリミテッド", ["アンリミテッド", "アンリミ", "unlimited", "unlim"]),
    "2pick": ("2Pick", ["2pick", "2ピック", "ツーピック", "pick"]),
}

_CACHE = {}
_IMAGE_SCAN_CACHE = {}


@dataclass
class DeckSearchRequest:
    query: str
    class_key: str
    class_label: str
    class_en: str
    high_accuracy: bool = False
    format_key: str = ""
    format_label: str = ""
    extra_terms: Optional[List[str]] = None


@dataclass
class DeckSearchResult:
    post: XPost
    image_url: str
    detected_class: str
    qr_score: int
    created_at: str


@dataclass
class DeckSearchStats:
    search_mode: str = "recent"
    endpoint_type: str = "recent"
    lookback_days: int = 14
    http_status: Optional[int] = None
    total_ms: int = 0
    x_api_ms: int = 0
    image_scan_ms: int = 0
    x_search_max_results: int = 50
    image_scan_limit: int = 30
    image_scan_concurrency: int = 5
    stop_after_candidates: bool = True
    high_accuracy: bool = False
    stopped_after_candidates: bool = False
    x_results: int = 0
    media_posts: int = 0
    image_url_count: int = 0
    scanned_image_count: int = 0
    image_downloaded: int = 0
    qr_detected: int = 0
    candidates: int = 0
    skipped_no_media: int = 0
    skipped_non_photo: int = 0
    skipped_by_content_type: int = 0
    skipped_by_content_length: int = 0
    skipped_image_fetch: int = 0
    skipped_no_qr: int = 0
    skipped_qr_error: int = 0
    timed_out: bool = False

    def to_log(self) -> str:
        elapsed_seconds = round(float(self.total_ms) / 1000.0, 3)
        return (
            "mode={0}, endpoint={1}, lookback_days={2}, http_status={3}, "
            "total_ms={4}, x_api_ms={5}, image_scan_ms={6}, x_search_max_results={7}, "
            "image_scan_limit={8}, image_scan_concurrency={9}, stop_after_candidates={10}, "
            "high_accuracy={11}, precision_mode={11}, stopped_after_candidates={12}, "
            "search_count={13}, X results={13}, media={14}, image_url_count={15}, scanned_image_count={16}, "
            "downloaded={17}, qr_detected_count={18}, qr={18}, candidates={19}, "
            "skipped_by_content_type={20}, skipped_by_content_length={21}, "
            "skip_no_media={22}, skip_non_photo={23}, skip_image_fetch={24}, skip_no_qr={25}, skip_qr_error={26}, "
            "elapsed_seconds={27}, timeout={28}"
        ).format(
            self.search_mode,
            self.endpoint_type,
            self.lookback_days,
            self.http_status if self.http_status is not None else "-",
            self.total_ms,
            self.x_api_ms,
            self.image_scan_ms,
            self.x_search_max_results,
            self.image_scan_limit,
            self.image_scan_concurrency,
            self.stop_after_candidates,
            self.high_accuracy,
            self.stopped_after_candidates,
            self.x_results,
            self.media_posts,
            self.image_url_count,
            self.scanned_image_count,
            self.image_downloaded,
            self.qr_detected,
            self.candidates,
            self.skipped_by_content_type,
            self.skipped_by_content_length,
            self.skipped_no_media,
            self.skipped_non_photo,
            self.skipped_image_fetch,
            self.skipped_no_qr,
            self.skipped_qr_error,
            elapsed_seconds,
            self.timed_out,
        )


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().lower()


def normalize_command_text(value: str) -> str:
    return " ".join((value or "").replace("\u3000", " ").split())


def detect_class(text: str) -> Optional[Tuple[str, str]]:
    normalized = normalize_text(text)
    for key, (label, aliases) in CLASS_ALIASES.items():
        for alias in aliases:
            if normalize_text(alias) in normalized:
                return key, label
    return None


def detect_class_token(token: str) -> Optional[Tuple[str, str]]:
    normalized = normalize_text(token)
    for key, (label, aliases) in CLASS_ALIASES.items():
        for alias in aliases:
            if normalized == normalize_text(alias):
                return key, label
    return None


def detect_format_token(token: str) -> Optional[Tuple[str, str]]:
    normalized = normalize_text(token)
    for key, (label, aliases) in FORMAT_ALIASES.items():
        for alias in aliases:
            if normalized == normalize_text(alias):
                return key, label
    return None


def is_deck_trigger_token(token: str) -> bool:
    normalized = normalize_text(token)
    return normalized in [normalize_text(alias) for alias in DECK_TRIGGER_ALIASES]


def is_high_accuracy_token(token: str) -> bool:
    normalized = normalize_text(token)
    return normalized in [normalize_text(word) for word in HIGH_ACCURACY_WORDS]


def sanitize_extra_term(token: str) -> str:
    value = (token or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://", "www.")):
        return ""
    if value.startswith("@") or value.startswith("<@"):
        return ""
    value = re.sub(r"[\x00-\x1f\x7f]", "", value)
    value = value.replace('"', "").replace("'", "")
    value = value.replace("(", "").replace(")", "")
    value = value.strip()
    if not value:
        return ""
    return value[:MAX_EXTRA_TERM_LENGTH]


def limit_extra_terms(terms: List[str]) -> List[str]:
    limited = []
    total_length = 0
    for term in terms:
        if len(limited) >= MAX_EXTRA_TERMS:
            break
        next_length = total_length + len(term) + (1 if limited else 0)
        if next_length > MAX_EXTRA_QUERY_LENGTH:
            remaining = MAX_EXTRA_QUERY_LENGTH - total_length - (1 if limited else 0)
            if remaining > 0:
                limited.append(term[:remaining])
            break
        limited.append(term)
        total_length = next_length
    return limited


def parse_deck_search_tokens(text: str) -> Dict[str, Any]:
    class_match = None
    format_match = None
    high_accuracy = False
    extra_terms = []

    for token in text.split():
        if is_deck_trigger_token(token):
            continue
        if is_high_accuracy_token(token):
            high_accuracy = True
            continue

        token_class = detect_class_token(token)
        if token_class is not None:
            if class_match is None:
                class_match = token_class
            continue

        token_format = detect_format_token(token)
        if token_format is not None:
            if format_match is None:
                format_match = token_format
            continue

        term = sanitize_extra_term(token)
        if term:
            extra_terms.append(term)

    return {
        "class_match": class_match,
        "format_match": format_match,
        "high_accuracy": high_accuracy,
        "extra_terms": limit_extra_terms(extra_terms),
    }


def parse_deck_search_command(command_text: str, missing_behavior: str = "ask_format") -> Optional[DeckSearchRequest]:
    text = normalize_command_text(command_text)
    text = re.sub(r"^(デッキ検索|デッキ|deck)\s*", "", text, flags=re.IGNORECASE).strip()
    parsed = parse_deck_search_tokens(text)
    high_accuracy = bool(parsed["high_accuracy"])
    found = parsed["class_match"] or detect_class(text)
    if found is None:
        if missing_behavior == "latest":
            return DeckSearchRequest(
                query=text or "デッキ",
                class_key="",
                class_label="指定なし",
                class_en="",
                high_accuracy=high_accuracy,
                extra_terms=parsed["extra_terms"],
            )
        return None
    class_key, class_label = found
    format_match = parsed["format_match"]
    format_key = format_match[0] if format_match is not None else ""
    format_label = format_match[1] if format_match is not None else ""
    return DeckSearchRequest(
        query=text or class_label,
        class_key=class_key,
        class_label=class_label,
        class_en=CLASS_EN_LABELS.get(class_key, class_key),
        high_accuracy=high_accuracy,
        format_key=format_key,
        format_label=format_label,
        extra_terms=parsed["extra_terms"],
    )


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


def get_config_str(config_json: Dict[str, Any], key: str, default: str) -> str:
    value = config_json.get(key)
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


def normalize_media_filter(value: str) -> str:
    normalized = (value or "media").strip().lower()
    if normalized in ("image", "images", "has:images"):
        return "images"
    return "media"


def normalize_search_mode(value: str) -> str:
    normalized = (value or "recent").strip().lower()
    if normalized == "full_archive":
        return "full_archive"
    return "recent"


def get_excluded_keywords(config_json: Dict[str, Any]) -> List[str]:
    raw = config_json.get("excluded_keywords")
    if raw is None:
        return list(DEFAULT_EXCLUDED_KEYWORDS)
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw)
    keywords = []
    for line in text.replace(",", "\n").splitlines():
        item = line.strip()
        if item:
            keywords.append(item)
    return keywords


def get_required_context_terms(config_json: Dict[str, Any]) -> List[str]:
    raw = config_json.get("required_context_terms")
    if raw is None:
        return list(DEFAULT_REQUIRED_CONTEXT_TERMS)
    if isinstance(raw, list):
        return [sanitize_extra_term(str(item)) for item in raw if sanitize_extra_term(str(item))]
    text = str(raw)
    terms = []
    for item in text.replace(",", "\n").splitlines():
        term = sanitize_extra_term(item)
        if term:
            terms.append(term)
    return terms


def build_or_query(terms: List[str]) -> str:
    safe_terms = [sanitize_extra_term(term) for term in terms]
    safe_terms = [term for term in safe_terms if term]
    if not safe_terms:
        return ""
    if len(safe_terms) == 1:
        return safe_terms[0]
    return "({0})".format(" OR ".join(safe_terms))


def get_class_search_terms(request: DeckSearchRequest) -> List[str]:
    configured = CLASS_SEARCH_TERMS.get(request.class_key)
    if configured:
        return list(configured)
    terms = [request.class_label, request.class_en]
    return [term for term in terms if term]


def get_extra_terms(request: DeckSearchRequest) -> List[str]:
    return list(request.extra_terms or [])


def get_query_terms(request: DeckSearchRequest) -> List[str]:
    terms = []
    if request.format_label:
        terms.append(request.format_label)
    terms.extend(get_extra_terms(request))
    return terms


def insert_query_terms(query: str, terms: List[str]) -> str:
    safe_terms = limit_extra_terms([sanitize_extra_term(term) for term in terms])
    safe_terms = [term for term in safe_terms if term]
    if not safe_terms:
        return query[:MAX_X_QUERY_LENGTH].rstrip()

    extra_query = " ".join(safe_terms)
    match = re.search(r"\s+has:(images|media)\b", query, flags=re.IGNORECASE)
    if match:
        query = "{0} {1}{2}".format(query[: match.start()], extra_query, query[match.start() :])
    else:
        query = "{0} {1}".format(query, extra_query)
    return query[:MAX_X_QUERY_LENGTH].rstrip()


def apply_media_filter(query: str, media_filter: str) -> str:
    tag = "has:images" if normalize_media_filter(media_filter) == "images" else "has:media"
    if re.search(r"\bhas:(images|media)\b", query, flags=re.IGNORECASE):
        return re.sub(r"\bhas:(images|media)\b", tag, query, flags=re.IGNORECASE)
    return "{0} {1}".format(query, tag)


def normalize_query_template(template: str) -> str:
    value = (template or "").strip()
    if not value:
        return DEFAULT_X_QUERY_TEMPLATE
    if value in LEGACY_X_QUERY_TEMPLATES:
        return DEFAULT_X_QUERY_TEMPLATE
    return value


def build_x_query(request: DeckSearchRequest, config_json: Dict[str, Any]) -> str:
    template = normalize_query_template(config_json.get("x_query_template") or DEFAULT_X_QUERY_TEMPLATE)
    required_context_terms = get_required_context_terms(config_json)
    class_search_terms = get_class_search_terms(request)
    class_search_query = build_or_query(class_search_terms)
    required_context_query = build_or_query(required_context_terms)
    query_terms = get_query_terms(request)
    extra_query = " ".join(limit_extra_terms([sanitize_extra_term(term) for term in query_terms]))
    query = template.format(
        class_key=request.class_key,
        class_label=request.class_label,
        class_en=request.class_en,
        class_search_query=class_search_query,
        query=request.query,
        required_context_query=required_context_query,
        format_key=request.format_key,
        format_label=request.format_label,
        extra_terms=" ".join(get_extra_terms(request)),
        extra_query=extra_query,
    )
    query = apply_media_filter(query, get_config_str(config_json, "media_filter", "media"))
    query = insert_query_terms(query, query_terms)
    if not get_config_bool(config_json, "include_retweets", False):
        query += " -is:retweet"
    if not get_config_bool(config_json, "include_replies", False):
        query += " -is:reply"
    for keyword in get_excluded_keywords(config_json):
        query += " -{0}".format(keyword)
    return query[:MAX_X_QUERY_LENGTH].rstrip()


def allowed_in_channel(config_json: Dict[str, Any], channel_id: str) -> bool:
    allowed = config_json.get("allowed_channel_ids") or []
    if not allowed:
        return True
    return str(channel_id) in [str(item) for item in allowed]


def cache_key(guild_id: str, channel_id: str, request: DeckSearchRequest, config_json: Dict[str, Any]) -> str:
    mode = normalize_search_mode(get_config_str(config_json, "search_mode", config.X_SEARCH_MODE))
    lookback_days = get_config_int(config_json, "lookback_days", config.X_SEARCH_LOOKBACK_DAYS, 1, 30)
    query_template = normalize_query_template(config_json.get("x_query_template") or DEFAULT_X_QUERY_TEMPLATE)
    excluded_keywords = ",".join(get_excluded_keywords(config_json))
    required_context_terms = ",".join(get_required_context_terms(config_json))
    media_filter = normalize_media_filter(get_config_str(config_json, "media_filter", "media"))
    extra_terms = ",".join(get_extra_terms(request))
    return "{0}:{1}:{2}:{3}:{4}:{5}:{6}:{7}:{8}:{9}:{10}:{11}:{12}".format(
        guild_id,
        channel_id,
        request.class_key,
        request.query,
        "high" if request.high_accuracy else "normal",
        request.format_key,
        extra_terms,
        required_context_terms,
        media_filter,
        mode,
        lookback_days,
        query_template,
        excluded_keywords,
    )


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


def get_image_scan_cached(image_url: str) -> Tuple[bool, Optional[int]]:
    item = _IMAGE_SCAN_CACHE.get(image_url)
    if item is None:
        return False, None
    cached_at, score = item
    if time.time() - cached_at > IMAGE_SCAN_CACHE_TTL_SECONDS:
        _IMAGE_SCAN_CACHE.pop(image_url, None)
        return False, None
    return True, score


def set_image_scan_cached(image_url: str, score: Optional[int]) -> None:
    _IMAGE_SCAN_CACHE[image_url] = (time.time(), score)


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def elapsed_ms(start_ms: int) -> int:
    return max(0, monotonic_ms() - start_ms)


def score_post(post: XPost, request: DeckSearchRequest) -> int:
    text = normalize_text(post.text)
    score = 0
    for term in HIGH_SCORE_TERMS:
        if normalize_text(term) in text:
            score += 3
    for term in LOW_SCORE_TERMS:
        if normalize_text(term) in text:
            score -= 4
    if request.class_label and normalize_text(request.class_label) in text:
        score += 5
    if request.class_en and normalize_text(request.class_en) in text:
        score += 5
    if post.media:
        score += 1
    return score


def sort_posts_for_scan(posts: List[XPost], request: DeckSearchRequest) -> List[XPost]:
    indexed = list(enumerate(posts))
    indexed.sort(key=lambda item: (-score_post(item[1], request), item[0]))
    return [post for _, post in indexed]


def allowed_image_content_type(content_type: str) -> bool:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    return normalized in ALLOWED_IMAGE_CONTENT_TYPES


def content_length_too_large(content_length: str) -> bool:
    if not content_length:
        return False
    try:
        return int(content_length) > MAX_IMAGE_BYTES
    except ValueError:
        return False


async def fetch_image_bytes(url: str, timeout_seconds: int, stats: Optional[DeckSearchStats] = None) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            try:
                head_response = await client.head(url)
            except httpx.HTTPError:
                head_response = None
            if head_response is not None and head_response.status_code < 400:
                if not allowed_image_content_type(head_response.headers.get("content-type", "")):
                    if stats is not None:
                        stats.skipped_by_content_type += 1
                    return None
                if content_length_too_large(head_response.headers.get("content-length", "")):
                    if stats is not None:
                        stats.skipped_by_content_length += 1
                    return None

            async with client.stream("GET", url) as response:
                content_type = response.headers.get("content-type", "")
                if response.status_code >= 400:
                    return None
                if not allowed_image_content_type(content_type):
                    if stats is not None:
                        stats.skipped_by_content_type += 1
                    return None
                if content_length_too_large(response.headers.get("content-length", "")):
                    if stats is not None:
                        stats.skipped_by_content_length += 1
                    return None

                chunks = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        if stats is not None:
                            stats.skipped_by_content_length += 1
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
    except httpx.HTTPError as exc:
        print("[WARN] deck image fetch failed: {0}".format(exc.__class__.__name__))
        return None


async def detect_qr_codes_async(image_bytes: bytes):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, detect_qr_codes, image_bytes)


async def scan_media_image(
    post: XPost,
    media,
    class_label: str,
    timeout_seconds: int,
    stats: DeckSearchStats,
) -> Optional[DeckSearchResult]:
    cached, cached_score = get_image_scan_cached(media.url)
    if cached:
        if cached_score is None:
            stats.skipped_no_qr += 1
            return None
        stats.qr_detected += 1
        return DeckSearchResult(
            post=post,
            image_url=media.url,
            detected_class=class_label,
            qr_score=cached_score,
            created_at=post.created_at,
        )

    image_bytes = await fetch_image_bytes(media.url, timeout_seconds, stats)
    if image_bytes is None:
        stats.skipped_image_fetch += 1
        set_image_scan_cached(media.url, None)
        return None
    stats.image_downloaded += 1
    stats.scanned_image_count += 1
    try:
        detections = await detect_qr_codes_async(image_bytes)
    except RuntimeError:
        raise
    except Exception as exc:
        print("[WARN] deck QR detection failed: {0}".format(exc.__class__.__name__))
        stats.skipped_qr_error += 1
        return None
    if not detections:
        stats.skipped_no_qr += 1
        set_image_scan_cached(media.url, None)
        return None
    best = sorted(detections, key=lambda item: item.score, reverse=True)[0]
    stats.qr_detected += 1
    set_image_scan_cached(media.url, best.score)
    return DeckSearchResult(
        post=post,
        image_url=media.url,
        detected_class=class_label,
        qr_score=best.score,
        created_at=post.created_at,
    )


async def scan_posts_concurrently(
    posts: List[XPost],
    request: DeckSearchRequest,
    max_results: int,
    image_scan_limit: int,
    image_fetch_timeout_seconds: int,
    image_scan_concurrency: int,
    stop_after_candidates: bool,
    stats: DeckSearchStats,
) -> List[DeckSearchResult]:
    semaphore = asyncio.Semaphore(image_scan_concurrency)
    scan_items = []
    image_url_count = 0
    for post in sort_posts_for_scan(posts, request):
        if not post.media:
            stats.skipped_no_media += 1
            continue
        stats.media_posts += 1
        post_media = []
        for media in post.media:
            if image_url_count >= image_scan_limit:
                break
            post_media.append(media)
            image_url_count += 1
        if post_media:
            scan_items.append((post, post_media))
        if image_url_count >= image_scan_limit:
            break
    stats.image_url_count = image_url_count

    async def run_one(post, media_items):
        async with semaphore:
            for media in media_items:
                found = await scan_media_image(post, media, request.class_label, image_fetch_timeout_seconds, stats)
                if found is not None:
                    return found
            return None

    tasks = [asyncio.ensure_future(run_one(post, media_items)) for post, media_items in scan_items]
    results = []
    pending = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    found = task.result()
                except asyncio.CancelledError:
                    continue
                except RuntimeError:
                    raise
                except Exception as exc:
                    print("[WARN] deck image scan task failed: {0}".format(exc.__class__.__name__))
                    continue
                if found is not None:
                    results.append(found)
                    stats.candidates = len(results)
                    if stop_after_candidates and len(results) >= max_results:
                        stats.stopped_after_candidates = True
                        for pending_task in pending:
                            pending_task.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                        return results
    finally:
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    return results


async def scan_post_images(
    post: XPost,
    class_label: str,
    limit: int,
    timeout_seconds: int,
    stats: Optional[DeckSearchStats] = None,
) -> Optional[DeckSearchResult]:
    scanned = 0
    for media in post.media:
        if scanned >= limit:
            break
        scanned += 1
        image_bytes = await fetch_image_bytes(media.url, timeout_seconds, stats)
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
    total_started_ms = monotonic_ms()
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
    total_timeout_seconds = get_config_int(config_json, "total_timeout_seconds", DEFAULT_TOTAL_TIMEOUT_SECONDS, 5, 120)
    cache_ttl_seconds = get_config_int(config_json, "cache_ttl_seconds", 60, 0, 3600)
    image_scan_limit = get_config_int(config_json, "image_scan_limit", 30, 1, 200)
    image_scan_concurrency = get_config_int(config_json, "image_scan_concurrency", 2, 1, 10)
    image_fetch_timeout_seconds = get_config_int(config_json, "image_fetch_timeout_seconds", 5, 1, 30)
    stop_after_candidates = get_config_bool(config_json, "stop_after_candidates", True)
    search_limit = get_config_int(config_json, "x_search_max_results", 50, 10, 100)
    search_mode = normalize_search_mode(get_config_str(config_json, "search_mode", config.X_SEARCH_MODE))
    lookback_days = get_config_int(config_json, "lookback_days", config.X_SEARCH_LOOKBACK_DAYS, 1, 30)
    high_accuracy_enabled = get_config_bool(config_json, "high_accuracy_enabled", True)
    high_accuracy = bool(request.high_accuracy and high_accuracy_enabled)
    if high_accuracy:
        search_limit = get_config_int(config_json, "high_accuracy_x_search_max_results", 100, 10, 100)
        image_scan_limit = get_config_int(config_json, "high_accuracy_image_scan_limit", 100, 1, 200)
        image_scan_concurrency = get_config_int(config_json, "high_accuracy_image_scan_concurrency", 2, 1, 10)
        stop_after_candidates = get_config_bool(config_json, "high_accuracy_stop_after_candidates", False)
    key = cache_key(guild_id, channel_id, request, config_json)
    cached = get_cached(key, cache_ttl_seconds)
    if cached is not None:
        return format_results(request, cached, max_results, config_json)

    query = build_x_query(request, config_json)
    media_filter = normalize_media_filter(get_config_str(config_json, "media_filter", "media"))
    print(
        "[INFO] deck search query: class_label={0} class_en={1} format={2} extra_terms={3} required_context_terms={4} media_filter={5} final_query={6}".format(
            request.class_label,
            request.class_en,
            request.format_label or "-",
            get_extra_terms(request),
            get_required_context_terms(config_json),
            media_filter,
            query,
        )
    )
    stats = DeckSearchStats(
        search_mode=search_mode,
        endpoint_type=search_mode,
        lookback_days=lookback_days,
        x_search_max_results=search_limit,
        image_scan_limit=image_scan_limit,
        image_scan_concurrency=image_scan_concurrency,
        stop_after_candidates=stop_after_candidates,
        high_accuracy=high_accuracy,
    )
    try:
        x_started_ms = monotonic_ms()
        posts = await asyncio.wait_for(
            search_posts(query, search_limit, timeout_seconds, search_mode, lookback_days),
            timeout=total_timeout_seconds,
        )
        stats.x_api_ms = elapsed_ms(x_started_ms)
    except asyncio.TimeoutError:
        stats.timed_out = True
        stats.total_ms = elapsed_ms(total_started_ms)
        print("[INFO] deck search stats: {0}".format(stats.to_log()))
        return get_config_str(config_json, "timeout_message", DEFAULT_TIMEOUT_MESSAGE)
    except XSearchDisabled:
        return DEFAULT_DISABLED_MESSAGE
    except XSearchError as exc:
        stats.http_status = exc.status_code
        stats.total_ms = elapsed_ms(total_started_ms)
        print("[INFO] deck search stats: {0}".format(stats.to_log()))
        if search_mode == "full_archive" and exc.status_code in (401, 403, 404):
            return DEFAULT_FULL_ARCHIVE_UNAVAILABLE_MESSAGE
        return DEFAULT_ERROR_MESSAGE

    stats.x_results = len(posts)
    image_started_ms = monotonic_ms()
    try:
        remaining_seconds = max(1.0, float(total_timeout_seconds) - (float(elapsed_ms(total_started_ms)) / 1000.0))
        results = await asyncio.wait_for(
            scan_posts_concurrently(
                posts,
                request,
                max_results,
                image_scan_limit,
                image_fetch_timeout_seconds,
                image_scan_concurrency,
                stop_after_candidates,
                stats,
            ),
            timeout=remaining_seconds,
        )
    except asyncio.TimeoutError:
        stats.timed_out = True
        stats.image_scan_ms = elapsed_ms(image_started_ms)
        stats.total_ms = elapsed_ms(total_started_ms)
        print("[INFO] deck search stats: {0}".format(stats.to_log()))
        return get_config_str(config_json, "timeout_message", DEFAULT_TIMEOUT_MESSAGE)
    stats.image_scan_ms = elapsed_ms(image_started_ms)
    set_cached(key, results)
    stats.candidates = len(results)
    stats.total_ms = elapsed_ms(total_started_ms)
    print("[INFO] deck search stats: {0}".format(stats.to_log()))
    return format_results(request, results, max_results, config_json)


def summarize_text(text: str, limit: int = 80) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."


def format_results(
    request: DeckSearchRequest,
    results: List[DeckSearchResult],
    max_results: int,
    config_json: Optional[Dict[str, Any]] = None,
) -> str:
    if not results:
        if config_json:
            return get_config_str(config_json, "not_found_message", DEFAULT_NOT_FOUND_MESSAGE)
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
