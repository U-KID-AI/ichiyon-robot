import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional


JST = timezone(timedelta(hours=9))
DEFAULT_MAX_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class DeckFetchSinceCommand:
    action: str
    fetch_since_date: Optional[date] = None


def today_jst(now: Optional[datetime] = None) -> date:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(JST).date()


def parse_fetch_since_date(value: str, now: Optional[datetime] = None) -> date:
    text = (value or "").strip()
    text = text.replace("から", "").strip()
    matched = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if matched:
        year = int(matched.group(1))
        month = int(matched.group(2))
        day = int(matched.group(3))
    else:
        matched = re.search(r"(\d{1,2})[/-](\d{1,2})", text)
        if not matched:
            raise ValueError("date not found")
        year = today_jst(now).year
        month = int(matched.group(1))
        day = int(matched.group(2))

    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError("invalid date") from exc


def validate_fetch_since_date(
    fetch_since_date: date,
    max_lookback_days: int,
    now: Optional[datetime] = None,
) -> Optional[str]:
    today = today_jst(now)
    oldest = today - timedelta(days=max(1, int(max_lookback_days)))
    if fetch_since_date > today:
        return "未来の日付は使えません。"
    if fetch_since_date < oldest:
        return "{0}日前より古い日付は使えません。".format(max_lookback_days)
    return None


def parse_deck_fetch_since_command(command_text: str, now: Optional[datetime] = None) -> Optional[DeckFetchSinceCommand]:
    text = " ".join((command_text or "").replace("\u3000", " ").split())
    if not text:
        return None
    lowered = text.lower()
    if not (text.startswith("デッキ") or lowered.startswith("deck")):
        return None

    rest = re.sub(r"^(デッキ検索|デッキ|deck)\s*", "", text, flags=re.IGNORECASE).strip()
    if rest == "取得日確認":
        return DeckFetchSinceCommand("show")
    if rest == "取得日リセット":
        return DeckFetchSinceCommand("reset")
    if rest.startswith("取得日更新"):
        raw_date = rest[len("取得日更新") :].strip()
        return DeckFetchSinceCommand("update", parse_fetch_since_date(raw_date, now))
    return None


def settings_max_lookback_days(settings: Optional[Dict[str, Any]]) -> int:
    if not settings:
        return DEFAULT_MAX_LOOKBACK_DAYS
    try:
        return max(1, int(settings.get("max_lookback_days") or DEFAULT_MAX_LOOKBACK_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_LOOKBACK_DAYS


def settings_fetch_since_date(settings: Optional[Dict[str, Any]]) -> Optional[date]:
    if not settings:
        return None
    value = settings.get("fetch_since_date")
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return parse_fetch_since_date(str(value))
    except ValueError:
        return None


def apply_deck_search_settings(config_json: Dict[str, Any], settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(config_json or {})
    fetch_since_date = settings_fetch_since_date(settings)
    if fetch_since_date is not None:
        merged["fetch_since_date"] = fetch_since_date.isoformat()
    merged["max_lookback_days"] = settings_max_lookback_days(settings)
    return merged


def fetch_since_start_time(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        fetch_date = parse_fetch_since_date(value)
    except ValueError:
        return None
    start = datetime(fetch_date.year, fetch_date.month, fetch_date.day, 0, 0, 0, tzinfo=JST)
    return start.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_fetch_since_date(value: Optional[date]) -> str:
    if value is None:
        return "未設定"
    return value.isoformat()
