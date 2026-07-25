import re
import unicodedata
from typing import Iterable


URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
MENTION_RE = re.compile(r"<@!?\d+>|<@&\d+>|<#\d+>")
CUSTOM_EMOJI_RE = re.compile(r"<a?:[A-Za-z0-9_]+:\d+>")
MARKDOWN_RE = re.compile(r"[*_~`>|]+")
SPACE_RE = re.compile(r"\s+")
REPEATED_SYMBOL_RE = re.compile(r"([!?！？wｗ笑])\1{4,}")
CODE_BLOCK_RE = re.compile(r"^\s*```.*```\s*$", re.DOTALL)


def stable_pitch_for_user(user_id: str, variation: float) -> float:
    digits = "".join(ch for ch in str(user_id or "") if ch.isdigit())
    value = int(digits[-8:] or "0")
    buckets = [-2, -1, 0, 1, 2]
    bucket = buckets[value % len(buckets)]
    return max(-0.2, min(0.2, bucket * float(variation or 0.0)))


def is_code_block_only(text: str) -> bool:
    return bool(CODE_BLOCK_RE.match(str(text or "")))


def is_url_only_text(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    if not normalized:
        return False
    return bool(URL_RE.sub("", normalized).strip() == "")


def truncate_for_tts(text: str, limit: int) -> str:
    text = str(text or "").strip()
    limit = max(1, int(limit or 1))
    if len(text) <= limit:
        return text
    boundary = max(text.rfind("。", 0, limit), text.rfind("、", 0, limit), text.rfind(" ", 0, limit))
    if boundary < max(20, limit // 2):
        boundary = limit
    return text[:boundary].rstrip() + "。以下省略"


def normalize_tts_text(content: str, attachment_content_types: Iterable[str], max_length: int) -> str:
    text = unicodedata.normalize("NFKC", str(content or ""))
    text = URL_RE.sub(" URL ", text)
    text = MENTION_RE.sub(" メンション ", text)
    text = CUSTOM_EMOJI_RE.sub(" 絵文字 ", text)
    text = MARKDOWN_RE.sub(" ", text)
    text = REPEATED_SYMBOL_RE.sub(lambda match: match.group(1) * 3, text)
    text = SPACE_RE.sub(" ", text).strip()
    if not text:
        types = [str(value or "").lower() for value in attachment_content_types]
        if any(value.startswith("image/") for value in types):
            text = "画像"
        elif types:
            text = "ファイル"
    return truncate_for_tts(text, max_length)
