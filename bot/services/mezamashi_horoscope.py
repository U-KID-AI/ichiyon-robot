import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bot import config
from bot.db import get_connection
from bot.repositories.feature_flags import FeatureFlagRepository
from bot.repositories.horoscope import HoroscopeRepository
from bot.services.external_http import ExternalHttpError, ExternalHttpPolicy, fetch_json as fetch_external_json


FEATURE_HOROSCOPE = "mezamashi_horoscope"
SOURCE_URL = "https://www.fujitv.co.jp/meza/uranai/index.html"
DATA_URL = "https://www.fujitv.co.jp/meza/uranai/data/uranai.json"
JST = timezone(timedelta(hours=9))
HTTP_POLICY = ExternalHttpPolicy(
    connect_timeout=5.0,
    read_timeout=10.0,
    retries=1,
    trust_env=False,
    user_agent="ichiyon-robot-mezamashi-horoscope/1.0",
)
RANKING_COMMANDS = {"占い", "今日の占い", "めざまし占い"}
MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}


@dataclass(frozen=True)
class Zodiac:
    key: str
    name: str
    aliases: Tuple[str, ...]


ZODIACS: Tuple[Zodiac, ...] = (
    Zodiac("aries", "おひつじ座", ("おひつじ", "牡羊座", "牡羊")),
    Zodiac("taurus", "おうし座", ("おうし", "牡牛座", "牡牛")),
    Zodiac("gemini", "ふたご座", ("ふたご", "双子座", "双子")),
    Zodiac("cancer", "かに座", ("かに", "蟹座", "蟹")),
    Zodiac("leo", "しし座", ("しし", "獅子座", "獅子")),
    Zodiac("virgo", "おとめ座", ("おとめ", "乙女座", "乙女")),
    Zodiac("libra", "てんびん座", ("てんびん", "天秤座", "天秤")),
    Zodiac("scorpio", "さそり座", ("さそり", "蠍座", "蠍")),
    Zodiac("sagittarius", "いて座", ("いて", "射手座", "射手")),
    Zodiac("capricorn", "やぎ座", ("やぎ", "山羊座", "山羊")),
    Zodiac("aquarius", "みずがめ座", ("みずがめ", "水瓶座", "水瓶")),
    Zodiac("pisces", "うお座", ("うお", "魚座", "魚")),
)
ZODIAC_BY_NAME = {zodiac.name: zodiac for zodiac in ZODIACS}

_alias_to_key: Dict[str, str] = {}
for zodiac in ZODIACS:
    for value in (zodiac.name, zodiac.key) + zodiac.aliases:
        _alias_to_key[unicodedata.normalize("NFKC", value).lower()] = zodiac.key


@dataclass
class HoroscopeEntry:
    key: str
    name: str
    rank: int
    text: str = ""
    point: str = ""
    color: str = ""
    advice: str = ""
    person: str = ""
    menu: str = ""


@dataclass
class HoroscopeBundle:
    target_date: str
    entries: List[HoroscopeEntry]
    source_url: str = SOURCE_URL
    fetched_at: Optional[str] = None
    stale: bool = False


class MezamashiHoroscopeError(RuntimeError):
    pass


_memory_cache: Dict[str, HoroscopeBundle] = {}
_latest_memory_cache: Optional[HoroscopeBundle] = None
_latest_memory_cached_at: Optional[datetime] = None
_fetch_lock = asyncio.Lock()


def normalize_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def normalize_zodiac(value: Any) -> Optional[str]:
    text = normalize_text(value).lower()
    return _alias_to_key.get(text)


def clean_html_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def compact_text(value: str, *, limit: int = 54) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\n", " ")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def parse_target_date(value: Any) -> str:
    text = str(value or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise MezamashiHoroscopeError("horoscope date is invalid")


def today_jst() -> str:
    return datetime.now(JST).date().isoformat()


def parse_payload(payload: Any) -> HoroscopeBundle:
    if not isinstance(payload, dict):
        raise MezamashiHoroscopeError("horoscope payload is not an object")
    target_date = parse_target_date(payload.get("date"))
    ranking = payload.get("ranking")
    if not isinstance(ranking, list):
        raise MezamashiHoroscopeError("horoscope ranking is missing")

    entries: List[HoroscopeEntry] = []
    for item in ranking:
        if not isinstance(item, dict):
            continue
        key = normalize_zodiac(item.get("name"))
        if key is None:
            raise MezamashiHoroscopeError("horoscope zodiac is unknown")
        try:
            rank = int(item.get("rank"))
        except (TypeError, ValueError):
            raise MezamashiHoroscopeError("horoscope rank is invalid")
        zodiac = next(z for z in ZODIACS if z.key == key)
        entries.append(
            HoroscopeEntry(
                key=key,
                name=zodiac.name,
                rank=rank,
                text=clean_html_text(item.get("text")),
                point=clean_html_text(item.get("point")),
                color=clean_html_text(item.get("color")),
                advice=clean_html_text(item.get("advice")),
                person=clean_html_text(item.get("person")),
                menu=clean_html_text(item.get("menu")),
            )
        )

    validate_entries(entries)
    return HoroscopeBundle(target_date=target_date, entries=sorted(entries, key=lambda item: item.rank))


def validate_entries(entries: List[HoroscopeEntry]) -> None:
    if len(entries) != 12:
        raise MezamashiHoroscopeError("horoscope ranking must contain 12 entries")
    ranks = [entry.rank for entry in entries]
    if sorted(ranks) != list(range(1, 13)):
        raise MezamashiHoroscopeError("horoscope ranks must be 1-12 without duplicates")
    keys = [entry.key for entry in entries]
    if sorted(keys) != sorted(zodiac.key for zodiac in ZODIACS):
        raise MezamashiHoroscopeError("horoscope zodiacs must be complete")


def bundle_to_payload(bundle: HoroscopeBundle) -> Dict[str, Any]:
    return {
        "date": bundle.target_date,
        "ranking": [
            {
                "key": entry.key,
                "name": entry.name,
                "rank": entry.rank,
                "text": entry.text,
                "point": entry.point,
                "color": entry.color,
                "advice": entry.advice,
                "person": entry.person,
                "menu": entry.menu,
            }
            for entry in bundle.entries
        ],
    }


def bundle_from_cache_row(row: Dict[str, Any], *, stale: bool = False) -> HoroscopeBundle:
    payload = row.get("payload_json")
    if isinstance(payload, str):
        payload = json.loads(payload)
    bundle = parse_payload(payload)
    bundle.fetched_at = str(row.get("fetched_at") or "")
    bundle.stale = stale
    return bundle


async def fetch_official_horoscope() -> HoroscopeBundle:
    try:
        payload = await fetch_external_json(DATA_URL, policy=HTTP_POLICY)
    except ExternalHttpError as exc:
        if exc.status_code is not None:
            raise MezamashiHoroscopeError("official horoscope returned status {0}".format(exc.status_code)) from exc
        raise MezamashiHoroscopeError("official horoscope fetch failed") from exc
    bundle = parse_payload(payload)
    print(
        "mezamashi_horoscope_fetch target_date={0} entries={1}".format(
            bundle.target_date,
            len(bundle.entries),
        )
    )
    return bundle


async def get_horoscope_bundle(force_refresh: bool = False) -> HoroscopeBundle:
    global _latest_memory_cache, _latest_memory_cached_at
    today = today_jst()
    if not force_refresh and today in _memory_cache:
        return _memory_cache[today]
    if (
        not force_refresh
        and _latest_memory_cache is not None
        and _latest_memory_cache.target_date == today
        and _latest_memory_cached_at is not None
        and datetime.now(JST) - _latest_memory_cached_at < timedelta(minutes=30)
    ):
        return _latest_memory_cache

    async with _fetch_lock:
        if not force_refresh and today in _memory_cache:
            return _memory_cache[today]
        if config.DATA_BACKEND == "db":
            try:
                with get_connection() as connection:
                    repository = HoroscopeRepository(connection, bot_id=config.BOT_INSTANCE_ID)
                    cached = repository.get_cache_by_date(today)
                    if cached is not None and not force_refresh:
                        bundle = bundle_from_cache_row(cached)
                        _memory_cache[today] = bundle
                        return bundle
            except Exception as exc:
                print("[WARN] mezamashi_horoscope cache read failed: {0}".format(exc))

        try:
            bundle = await fetch_official_horoscope()
        except MezamashiHoroscopeError:
            if config.DATA_BACKEND == "db":
                try:
                    with get_connection() as connection:
                        latest = HoroscopeRepository(connection, bot_id=config.BOT_INSTANCE_ID).get_latest_cache()
                        if latest is not None:
                            return bundle_from_cache_row(latest, stale=True)
                except Exception as exc:
                    print("[WARN] mezamashi_horoscope fallback cache read failed: {0}".format(exc))
            if _latest_memory_cache is not None:
                bundle = _latest_memory_cache
                bundle.stale = True
                return bundle
            raise

        bundle.stale = bundle.target_date != today
        if config.DATA_BACKEND == "db":
            try:
                with get_connection() as connection:
                    repository = HoroscopeRepository(connection, bot_id=config.BOT_INSTANCE_ID)
                    repository.upsert_cache(bundle.target_date, bundle_to_payload(bundle), SOURCE_URL)
                    connection.commit()
            except Exception as exc:
                print("[WARN] mezamashi_horoscope cache write failed: {0}".format(exc))
        _memory_cache[bundle.target_date] = bundle
        _latest_memory_cache = bundle
        _latest_memory_cached_at = datetime.now(JST)
        return bundle


def settings_enabled(guild_id: str, command_kind: str) -> bool:
    if config.DATA_BACKEND != "db":
        return True
    try:
        with get_connection() as connection:
            if not FeatureFlagRepository(connection, bot_id=config.BOT_INSTANCE_ID).is_enabled(
                guild_id,
                FEATURE_HOROSCOPE,
                default=True,
            ):
                return False
            settings = HoroscopeRepository(connection, bot_id=config.BOT_INSTANCE_ID).get_settings(guild_id)
    except Exception as exc:
        print("[WARN] mezamashi_horoscope settings read failed: guild_id={0} error={1}".format(guild_id, exc))
        return True
    if not bool(settings.get("enabled")):
        return False
    if command_kind == "ranking":
        return bool(settings.get("ranking_command_enabled"))
    if command_kind == "zodiac":
        return bool(settings.get("zodiac_command_enabled"))
    return True


def parse_command(command_text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    text = normalize_text(command_text)
    if text in RANKING_COMMANDS:
        return "ranking", None
    match = re.fullmatch(r"(.+?)\s*占い", text)
    if match:
        key = normalize_zodiac(match.group(1))
        if key is not None:
            return "zodiac", key
    return None, None


def get_message_guild_id(message: Any) -> Optional[str]:
    guild = getattr(message, "guild", None)
    guild_id = getattr(guild, "id", None)
    if guild_id is None:
        return None
    return str(guild_id)


def date_label(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value
    return "{0:04d}/{1:02d}/{2:02d}".format(parsed.year, parsed.month, parsed.day)


def format_ranking(bundle: HoroscopeBundle) -> str:
    title = "直近のめざまし占い" if bundle.stale else "今日のめざまし占い"
    lines = [title, ""]
    for entry in bundle.entries:
        prefix = MEDAL.get(entry.rank, "  ")
        lines.append("{0} {1}位　{2}".format(prefix, entry.rank, entry.name).strip())
        if entry.text:
            lines.append("　{0}".format(compact_text(entry.text)))
        lucky = []
        if entry.color:
            lucky.append("ラッキーカラー: {0}".format(entry.color))
        if entry.point:
            lucky.append("ラッキーポイント: {0}".format(entry.point))
        if lucky:
            lines.append("　" + " / ".join(lucky))
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def format_zodiac(bundle: HoroscopeBundle, zodiac_key: str) -> str:
    entry = next((item for item in bundle.entries if item.key == zodiac_key), None)
    if entry is None:
        raise MezamashiHoroscopeError("zodiac was not found")
    lines = ["🔮 {0}　{1}位".format(entry.name, entry.rank)]
    if entry.text:
        lines.extend(["", entry.text])
    extras = []
    if entry.color:
        extras.append("ラッキーカラー: {0}".format(entry.color))
    if entry.point:
        extras.append("ラッキーポイント: {0}".format(entry.point))
    if entry.advice:
        extras.append("アドバイス: {0}".format(entry.advice))
    if entry.person:
        extras.append("ラッキーパーソン: {0}".format(entry.person))
    if entry.menu:
        extras.append("ラッキーメニュー: {0}".format(entry.menu))
    if extras:
        lines.extend([""] + extras)
    return "\n".join(lines)


async def build_horoscope_messages(content_config: Any = None, *, force_refresh: bool = False) -> List[str]:
    if isinstance(content_config, dict):
        config_map = content_config
    elif content_config:
        try:
            parsed = json.loads(str(content_config))
        except (TypeError, ValueError):
            parsed = {}
        config_map = parsed if isinstance(parsed, dict) else {}
    else:
        config_map = {}
    zodiac_key = normalize_zodiac(config_map.get("zodiac")) if config_map.get("zodiac") else None
    bundle = await get_horoscope_bundle(force_refresh=force_refresh)
    if zodiac_key:
        return [format_zodiac(bundle, zodiac_key)]
    return [format_ranking(bundle)]


async def handle_horoscope_command(message, command_text: Optional[str]) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    if command_text is None:
        return False
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False
    kind, zodiac_key = parse_command(command_text)
    if kind is None:
        return False
    if not settings_enabled(guild_id, kind):
        print(
            "mezamashi_horoscope_skipped bot_instance_id={0} guild_id={1} command_kind={2} reason=disabled".format(
                config.BOT_INSTANCE_ID,
                guild_id,
                kind,
            )
        )
        return True
    try:
        bundle = await get_horoscope_bundle()
        text = format_zodiac(bundle, zodiac_key) if kind == "zodiac" and zodiac_key else format_ranking(bundle)
    except MezamashiHoroscopeError as exc:
        print(
            "[WARN] mezamashi_horoscope command failed: bot_instance_id={0} guild_id={1} error={2}".format(
                config.BOT_INSTANCE_ID,
                guild_id,
                exc,
            )
        )
        await message.channel.send("占いデータを取得できませんでした。")
        return True
    await message.channel.send(text)
    return True
