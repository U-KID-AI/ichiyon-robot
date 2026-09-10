import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from admin.servers import role_allows
from bot.services import mezamashi_horoscope as horoscope


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


ZODIAC_NAMES = [
    "おひつじ座",
    "おうし座",
    "ふたご座",
    "かに座",
    "しし座",
    "おとめ座",
    "てんびん座",
    "さそり座",
    "いて座",
    "やぎ座",
    "みずがめ座",
    "うお座",
]


def fixture_payload():
    return {
        "date": "2026/09/11",
        "ranking": [
            {
                "name": name,
                "rank": index + 1,
                "text": "{0}のコメント<br>2行目".format(name),
                "point": "ラッキーポイント",
                "color": "ラッキーカラー",
                "advice": "アドバイス",
                "person": "人",
                "menu": "",
            }
            for index, name in enumerate(ZODIAC_NAMES)
        ],
    }


class FakeChannel:
    def __init__(self):
        self.messages = []

    async def send(self, text):
        self.messages.append(text)


class FakeMessage:
    def __init__(self, content, command_text=None, guild_id="guild-a", bot=False):
        self.content = content
        self.command_text = command_text
        self.guild = SimpleNamespace(id=guild_id)
        self.author = SimpleNamespace(bot=bot)
        self.channel = FakeChannel()


async def run_handler_checks(results):
    original_backend = horoscope.config.DATA_BACKEND
    original_get_bundle = horoscope.get_horoscope_bundle
    original_settings = horoscope.settings_enabled
    bundle = horoscope.parse_payload(fixture_payload())

    async def fake_bundle(force_refresh=False):
        return bundle

    try:
        horoscope.config.DATA_BACKEND = "db"
        horoscope.get_horoscope_bundle = fake_bundle
        horoscope.settings_enabled = lambda guild_id, kind: True

        ranking_message = FakeMessage("占い")
        results.append(check("standalone ranking command is handled", await horoscope.handle_horoscope_command(ranking_message, None) is True))
        results.append(check("ranking sends 12 signs", "12位" in ranking_message.channel.messages[0] and "出典:" in ranking_message.channel.messages[0]))

        zodiac_message = FakeMessage("<@1> さそり座 占い", command_text="さそり座 占い")
        results.append(check("mention zodiac command is handled", await horoscope.handle_horoscope_command(zodiac_message, "さそり座 占い") is True))
        results.append(check("zodiac response is scoped", "さそり座" in zodiac_message.channel.messages[0] and "今日の順位: 8位" in zodiac_message.channel.messages[0], zodiac_message.channel.messages))

        disabled_message = FakeMessage("占い")
        horoscope.settings_enabled = lambda guild_id, kind: False
        results.append(check("disabled feature consumes horoscope command without normal fallback", await horoscope.handle_horoscope_command(disabled_message, None) is True))
        results.append(check("disabled feature sends no normal mention response", disabled_message.channel.messages == [], disabled_message.channel.messages))

        async def failing_bundle(force_refresh=False):
            raise horoscope.MezamashiHoroscopeError("fixture timeout")

        horoscope.settings_enabled = lambda guild_id, kind: True
        horoscope.get_horoscope_bundle = failing_bundle
        failed_message = FakeMessage("占い")
        results.append(check("HTTP timeout or 4xx fails safe", await horoscope.handle_horoscope_command(failed_message, None) is True))
        results.append(check("failure sends safe message", failed_message.channel.messages == ["占い情報を取得できませんでした。少し時間をおいて試してください。"], failed_message.channel.messages))
    finally:
        horoscope.config.DATA_BACKEND = original_backend
        horoscope.get_horoscope_bundle = original_get_bundle
        horoscope.settings_enabled = original_settings


def run_parse_checks(results):
    bundle = horoscope.parse_payload(fixture_payload())
    results.append(check("normal HTML-backed JSON parses 12 zodiacs", len(bundle.entries) == 12))
    results.append(check("rank 1-12 validation passes", [entry.rank for entry in bundle.entries] == list(range(1, 13))))
    results.append(check("zodiac aliases normalize", horoscope.normalize_zodiac("蠍座") == "scorpio" and horoscope.normalize_zodiac("さそり") == "scorpio"))
    results.append(check("ranking command parses", horoscope.parse_command("今日の占い") == ("ranking", None)))
    results.append(check("zodiac command parses", horoscope.parse_command("蠍座 占い") == ("zodiac", "scorpio")))
    results.append(check("ambiguous partial text does not parse", horoscope.parse_command("占いっぽい") == (None, None)))

    duplicate_zodiac = fixture_payload()
    duplicate_zodiac["ranking"][1]["name"] = "おひつじ座"
    try:
        horoscope.parse_payload(duplicate_zodiac)
        results.append(check("duplicate zodiac is invalid", False))
    except horoscope.MezamashiHoroscopeError:
        results.append(check("duplicate zodiac is invalid", True))

    duplicate_rank = fixture_payload()
    duplicate_rank["ranking"][1]["rank"] = 1
    try:
        horoscope.parse_payload(duplicate_rank)
        results.append(check("duplicate rank is invalid", False))
    except horoscope.MezamashiHoroscopeError:
        results.append(check("duplicate rank is invalid", True))

    short_payload = fixture_payload()
    short_payload["ranking"] = short_payload["ranking"][:11]
    try:
        horoscope.parse_payload(short_payload)
        results.append(check("11 zodiacs is invalid", False))
    except horoscope.MezamashiHoroscopeError:
        results.append(check("11 zodiacs is invalid", True))


async def run_cache_checks(results):
    original_fetch = horoscope.fetch_official_horoscope
    original_backend = horoscope.config.DATA_BACKEND
    horoscope._memory_cache.clear()
    horoscope._latest_memory_cache = None
    horoscope._latest_memory_cached_at = None
    calls = []
    bundle = horoscope.parse_payload(fixture_payload())

    async def fake_fetch():
        calls.append("fetch")
        return bundle

    try:
        horoscope.config.DATA_BACKEND = "json"
        horoscope.fetch_official_horoscope = fake_fetch
        first = await horoscope.get_horoscope_bundle(force_refresh=True)
        second = await horoscope.get_horoscope_bundle()
        results.append(check("cache miss fetches and saves memory cache", first.target_date == "2026-09-11" and calls == ["fetch"], calls))
        results.append(check("cache hit avoids HTTP fetch", second.target_date == first.target_date and calls == ["fetch"], calls))

        stale = horoscope.parse_payload(fixture_payload())
        stale.target_date = "2026-09-10"
        stale.stale = True
        horoscope._memory_cache.clear()
        horoscope._latest_memory_cache = stale
        horoscope._latest_memory_cached_at = datetime.now(horoscope.JST) - timedelta(minutes=1)
        results.append(check("recent stale cache is not shown as today", (await horoscope.get_horoscope_bundle()).stale is True))
    finally:
        horoscope.fetch_official_horoscope = original_fetch
        horoscope.config.DATA_BACKEND = original_backend


def run_admin_checks(results):
    results.append(check("editor can edit horoscope settings", role_allows("editor", "editor")))
    results.append(check("viewer cannot edit horoscope settings", not role_allows("viewer", "editor")))
    source = (ROOT_DIR / "admin" / "servers.py").read_text(encoding="utf-8")
    template = (ROOT_DIR / "admin" / "templates" / "horoscope_settings.html").read_text(encoding="utf-8")
    auto_template = (ROOT_DIR / "admin" / "templates" / "auto_post_form.html").read_text(encoding="utf-8")
    results.append(check("feature list contains horoscope", '"key": "mezamashi_horoscope"' in source))
    results.append(check("admin setting labels are present", "12星座一覧コマンド" in template and "個別星座検索" in template))
    results.append(check("auto post form supports horoscope", "mezamashi_horoscope" in auto_template))


def run_migration_checks(results):
    sql = (ROOT_DIR / "migrations" / "046_add_mezamashi_horoscope.sql").read_text(encoding="utf-8")
    statements = [part.strip().upper() for part in sql.split(";")]
    destructive = any(statement.startswith(("DROP ", "DELETE ", "TRUNCATE ", "UPDATE ")) for statement in statements)
    results.append(check("migration is non-destructive", not destructive))
    results.append(check("cache table exists", "CREATE TABLE IF NOT EXISTS mezamashi_horoscope_cache" in sql))
    results.append(check("settings table is bot/guild scoped", "UNIQUE (bot_id, guild_id)" in sql))


async def main_async():
    results = []
    run_parse_checks(results)
    await run_cache_checks(results)
    await run_handler_checks(results)
    run_admin_checks(results)
    run_migration_checks(results)
    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
