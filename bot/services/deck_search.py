import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from bot import config
from bot.services.deck_search_settings import fetch_since_start_time
from bot.services.qr_detector import detect_qr_codes, opencv_available
from bot.services.x_search import XPost, XSearchDisabled, XSearchError, search_posts


DEFAULT_DENY_MESSAGE = "このチャンネルではデッキ検索は使えません。"
DEFAULT_DISABLED_MESSAGE = "デッキ検索はまだ無効"
DEFAULT_ERROR_MESSAGE = "検索でエラー"
DEFAULT_NOT_FOUND_MESSAGE = "おい ないんだが"
DEFAULT_ASK_FORMAT_MESSAGE = "クラス名も入れて"
DEFAULT_FULL_ARCHIVE_UNAVAILABLE_MESSAGE = "過去検索が使えません"
DEFAULT_X_AUTH_ERROR_MESSAGE = "X API認証に失敗しています。設定を確認してください。"
DEFAULT_X_ACCESS_ERROR_MESSAGE = "X APIの権限またはプラン制限で検索できません。"
DEFAULT_X_QUERY_TEMPLATE = "{class_search_query} {required_context_query} has:media"
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
MAX_IMAGE_BYTES = 8 * 1024 * 1024
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
    qr_detected: bool = True


@dataclass
class DeckSearchStats:
    search_mode: str = "recent"
    endpoint_type: str = "recent"
    lookback_days: int = 14
    http_status: Optional[int] = None
    total_ms: int = 0
    x_api_ms: int = 0
    image_scan_ms: int = 0
    image_scan_concurrency: int = 5
    high_accuracy: bool = False
    stopped_after_candidates: bool = False
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
    skipped_class_spam: int = 0

    def to_log(self) -> str:
        return (
            "mode={0}, endpoint={1}, lookback_days={2}, http_status={3}, "
            "total_ms={4}, x_api_ms={5}, image_scan_ms={6}, image_scan_concurrency={7}, "
            "high_accuracy={8}, precision_mode={8}, stopped_after_candidates={9}, "
            "X results={10}, media={11}, downloaded={12}, qr={13}, candidates={14}, "
            "skip_no_media={15}, skip_non_photo={16}, skip_image_fetch={17}, skip_no_qr={18}, skip_qr_error={19}, "
            "skip_class_spam={20}"
        ).format(
            self.search_mode,
            self.endpoint_type,
            self.lookback_days,
            self.http_status if self.http_status is not None else "-",
            self.total_ms,
            self.x_api_ms,
            self.image_scan_ms,
            self.image_scan_concurrency,
            self.high_accuracy,
            self.stopped_after_candidates,
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
            self.skipped_class_spam,
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


def x_search_error_message(exc: XSearchError, attempted_full_archive_fallback: bool = False) -> str:
    if exc.status_code == 401:
        return DEFAULT_X_AUTH_ERROR_MESSAGE
    if exc.status_code == 403:
        return DEFAULT_X_ACCESS_ERROR_MESSAGE
    if exc.status_code == 404 and not attempted_full_archive_fallback:
        return DEFAULT_FULL_ARCHIVE_UNAVAILABLE_MESSAGE
    return DEFAULT_ERROR_MESSAGE


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
    fetch_since_date = get_config_str(config_json, "fetch_since_date", "")
    return "{0}:{1}:{2}:{3}:{4}:{5}:{6}:{7}:{8}:{9}:{10}:{11}:{12}:{13}".format(
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
        fetch_since_date,
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


CLASS_SPAM_IGNORED_CLASS_KEYS = {"neutral"}
CLASS_SPAM_BROAD_TERMS = [
    "\u5168\u30af\u30e9\u30b9",
    "\u51687\u30af\u30e9\u30b9",
    "\u5168\u30af\u30e9\u30b9\u5bfe\u5fdc",
    "\u5168\u30af\u30e9\u30b9\u5bfe\u5fdc\u53ef",
    "\u5404\u30af\u30e9\u30b9",
    "\u5168\u8077",
    "\u5168\u8077\u696d",
]
CLASS_SPAM_SEPARATORS_RE = re.compile(r"[\s\u3000/／・,，|｜、。:：;；\[\]（）(){}<>＜＞]+")
CLASS_SPAM_COMPACT_RE = re.compile(r"[\s\u3000/／・,，|｜、。:：;；\[\]（）(){}<>＜＞_-]+")


def normalize_deck_class_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").lower()
    normalized = CLASS_SPAM_SEPARATORS_RE.sub(" ", normalized)
    return " ".join(normalized.split())


def compact_deck_class_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").lower()
    return CLASS_SPAM_COMPACT_RE.sub("", normalized)


def deck_class_alias_items() -> List[Tuple[str, str]]:
    items = []
    for class_key, (label, aliases) in CLASS_ALIASES.items():
        if class_key in CLASS_SPAM_IGNORED_CLASS_KEYS:
            continue
        for alias in [label] + list(aliases):
            normalized = normalize_text(alias)
            if len(normalized) < 2 and normalized not in ("nm",):
                continue
            items.append((class_key, normalized))
    items.sort(key=lambda item: len(item[1]), reverse=True)
    return items


def deck_class_alias_matches(normalized_text: str, compact_text: str, alias: str) -> bool:
    compact_alias = compact_deck_class_text(alias)
    if not compact_alias:
        return False
    if compact_alias.isascii() and compact_alias.isalnum():
        pattern = r"(?<![a-z0-9]){0}(?![a-z0-9])".format(re.escape(compact_alias))
        return re.search(pattern, normalized_text) is not None
    return compact_alias in compact_text


def detect_deck_classes_in_text(text: str) -> List[str]:
    normalized = normalize_deck_class_text(text)
    compact = compact_deck_class_text(text)
    found = set()
    for class_key, alias in deck_class_alias_items():
        if deck_class_alias_matches(normalized, compact, alias):
            found.add(class_key)
    return sorted(found)


def has_broad_class_listing_term(text: str) -> bool:
    compact = compact_deck_class_text(text)
    return any(compact_deck_class_text(term) in compact for term in CLASS_SPAM_BROAD_TERMS)


def deck_post_class_filter_reason(post: XPost, request: DeckSearchRequest) -> Tuple[Optional[str], List[str]]:
    classes = detect_deck_classes_in_text(post.text)
    if has_broad_class_listing_term(post.text):
        return "broad_class_listing", classes
    if len(classes) >= 4:
        return "many_classes", classes
    return None, classes


def deck_post_class_rank(post: XPost, request: DeckSearchRequest) -> int:
    classes = detect_deck_classes_in_text(post.text)
    if request.class_key and classes == [request.class_key]:
        return 0
    if request.class_key and request.class_key in classes and 2 <= len(classes) <= 3:
        return 1
    return 2


def filter_and_rank_posts_for_scan(
    posts: List[XPost],
    request: DeckSearchRequest,
    log_filtered: bool = False,
    stats: Optional[DeckSearchStats] = None,
) -> List[XPost]:
    indexed_posts = []
    for index, post in enumerate(posts):
        reason, classes = deck_post_class_filter_reason(post, request)
        if reason is not None:
            if stats is not None:
                stats.skipped_class_spam += 1
            if log_filtered:
                print(
                    "[INFO] deck_search_filtered tweet_id={0} reason={1} class_count={2} target_class={3}".format(
                        post.post_id,
                        reason,
                        len(classes),
                        request.class_key or "-",
                    )
                )
            continue
        indexed_posts.append((index, post))

    indexed_posts.sort(
        key=lambda item: (
            deck_post_class_rank(item[1], request),
            -score_post(item[1], request),
            item[0],
        )
    )
    return [post for _, post in indexed_posts]


def sort_posts_for_scan(posts: List[XPost], request: DeckSearchRequest) -> List[XPost]:
    return filter_and_rank_posts_for_scan(posts, request)



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


async def detect_qr_codes_async(image_bytes: bytes):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, detect_qr_codes, image_bytes)

def build_fallback_result(post: XPost, media, class_label: str) -> DeckSearchResult:
    return DeckSearchResult(
        post=post,
        image_url=media.url,
        detected_class=class_label,
        qr_score=0,
        created_at=post.created_at,
        qr_detected=False,
    )

async def scan_media_image(
    post: XPost,
    media,
    class_label: str,
    timeout_seconds: int,
    stats: DeckSearchStats,
) -> Optional[DeckSearchResult]:
    image_bytes = await fetch_image_bytes(media.url, timeout_seconds)
    if image_bytes is None:
        stats.skipped_image_fetch += 1
        return None
    stats.image_downloaded += 1
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
        return None
    best = sorted(detections, key=lambda item: item.score, reverse=True)[0]
    stats.qr_detected += 1
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
    fallback_results = []
    fallback_post_ids = set()

    for post in filter_and_rank_posts_for_scan(posts, request, log_filtered=True, stats=stats):
        if not post.media:
            stats.skipped_no_media += 1
            continue
        stats.media_posts += 1
        for media in post.media:
            if len(scan_items) >= image_scan_limit:
                break
            scan_items.append((post, media))
            if post.post_id not in fallback_post_ids:
                fallback_results.append(build_fallback_result(post, media, request.class_label))
                fallback_post_ids.add(post.post_id)
        if len(scan_items) >= image_scan_limit:
            break

    async def run_one(post, media):
        async with semaphore:
            return await scan_media_image(post, media, request.class_label, image_fetch_timeout_seconds, stats)

    tasks = [asyncio.ensure_future(run_one(post, media)) for post, media in scan_items]
    qr_results = []
    qr_post_ids = set()
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

                if found is not None and found.post.post_id not in qr_post_ids:
                    qr_results.append(found)
                    qr_post_ids.add(found.post.post_id)
                    stats.candidates = len(qr_results)

                    if stop_after_candidates and len(qr_results) >= max_results:
                        stats.stopped_after_candidates = True
                        for pending_task in pending:
                            pending_task.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                        return qr_results

    finally:
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    results = list(qr_results)
    for fallback in fallback_results:
        if len(results) >= max_results:
            break
        if fallback.post.post_id in qr_post_ids:
            continue
        results.append(fallback)

    stats.candidates = len(results)
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
    cache_ttl_seconds = get_config_int(config_json, "cache_ttl_seconds", 60, 0, 3600)
    image_scan_limit = get_config_int(config_json, "image_scan_limit", 30, 1, 200)
    image_scan_concurrency = get_config_int(config_json, "image_scan_concurrency", 2, 1, 10)
    image_fetch_timeout_seconds = get_config_int(config_json, "image_fetch_timeout_seconds", 5, 1, 30)
    stop_after_candidates = get_config_bool(config_json, "stop_after_candidates", True)
    search_limit = get_config_int(config_json, "x_search_max_results", config.X_SEARCH_MAX_RESULTS, 10, 100)
    search_mode = normalize_search_mode(get_config_str(config_json, "search_mode", config.X_SEARCH_MODE))
    lookback_days = get_config_int(config_json, "lookback_days", config.X_SEARCH_LOOKBACK_DAYS, 1, 30)
    fetch_since_date = get_config_str(config_json, "fetch_since_date", "")
    search_start_time = fetch_since_start_time(fetch_since_date)
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
        "[INFO] deck search query: class_label={0} class_en={1} format={2} extra_terms={3} required_context_terms={4} media_filter={5} fetch_since_date={6} final_query={7}".format(
            request.class_label,
            request.class_en,
            request.format_label or "-",
            get_extra_terms(request),
            get_required_context_terms(config_json),
            media_filter,
            fetch_since_date or "-",
            query,
        )
    )
    stats = DeckSearchStats(
        search_mode=search_mode,
        endpoint_type=search_mode,
        lookback_days=lookback_days,
        image_scan_concurrency=image_scan_concurrency,
        high_accuracy=high_accuracy,
    )
    async def run_x_search(selected_mode: str) -> List[XPost]:
        if search_start_time:
            return await search_posts(
                query,
                search_limit,
                timeout_seconds,
                selected_mode,
                lookback_days,
                search_start_time,
            )
        return await search_posts(query, search_limit, timeout_seconds, selected_mode, lookback_days)

    try:
        x_started_ms = monotonic_ms()
        posts = await run_x_search(search_mode)
        stats.x_api_ms = elapsed_ms(x_started_ms)
    except XSearchDisabled:
        return DEFAULT_DISABLED_MESSAGE
    except XSearchError as exc:
        if search_mode == "full_archive" and exc.status_code in (401, 403, 404):
            print(
                "[WARN] full_archive deck search unavailable; fallback to recent: status={0}".format(
                    exc.status_code
                )
            )
            try:
                x_started_ms = monotonic_ms()
                search_mode = "recent"
                stats.search_mode = search_mode
                stats.endpoint_type = search_mode
                posts = await run_x_search(search_mode)
                stats.http_status = None
                stats.x_api_ms = elapsed_ms(x_started_ms)
            except XSearchDisabled:
                return DEFAULT_DISABLED_MESSAGE
            except XSearchError as fallback_exc:
                stats.http_status = fallback_exc.status_code
                stats.total_ms = elapsed_ms(total_started_ms)
                print("[INFO] deck search stats: {0}".format(stats.to_log()))
                return x_search_error_message(fallback_exc, attempted_full_archive_fallback=True)
        else:
            stats.http_status = exc.status_code
            stats.total_ms = elapsed_ms(total_started_ms)
            print("[INFO] deck search stats: {0}".format(stats.to_log()))
            return x_search_error_message(exc)

    stats.x_results = len(posts)
    image_started_ms = monotonic_ms()
    results = await scan_posts_concurrently(
        posts,
        request,
        max_results,
        image_scan_limit,
        image_fetch_timeout_seconds,
        image_scan_concurrency,
        stop_after_candidates,
        stats,
    )
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
        status_label = "QR検出済み" if result.qr_detected else "画像候補（QR未検出）"
        lines.append(
            "{0}. {1}\n{2}\n{3} / {4}".format(
                index,
                summarize_text(result.post.text),
                result.post.url,
                result.detected_class,
                status_label,
            )
        )
    return "\n".join(lines)
