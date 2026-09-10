import asyncio
import argparse
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
    message = "[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else "")
    print(message.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"))
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
                "menu": "メニュー",
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

        ranking_message = FakeMessage("<@1> 占い", command_text="占い")
        results.append(check("mention ranking command is handled", await horoscope.handle_horoscope_command(ranking_message, "占い") is True))
        ranking_text = ranking_message.channel.messages[0]
        results.append(check("ranking sends 12 detailed signs", "12位" in ranking_text and "ラッキーカラー:" in ranking_text and "ラッキーポイント:" in ranking_text, ranking_text))
        results.append(check("ranking starts from first rank without title", ranking_text.startswith("🥇 1位") and "今日のめざまし占い" not in ranking_text and "直近のめざまし占い" not in ranking_text, ranking_text))
        results.append(check("ranking omits source url and update date", "公式ページ:" not in ranking_text and "更新:" not in ranking_text and "出典:" not in ranking_text, ranking_text))

        standalone_message = FakeMessage("占い")
        results.append(check("standalone ranking command is ignored", await horoscope.handle_horoscope_command(standalone_message, None) is False))
        results.append(check("standalone ranking sends nothing", standalone_message.channel.messages == [], standalone_message.channel.messages))

        zodiac_message = FakeMessage("<@1> さそり座 占い", command_text="さそり座 占い")
        results.append(check("mention zodiac command is handled", await horoscope.handle_horoscope_command(zodiac_message, "さそり座 占い") is True))
        zodiac_text = zodiac_message.channel.messages[0]
        results.append(check("zodiac response is scoped", "さそり座" in zodiac_text and "8位" in zodiac_text, zodiac_message.channel.messages))
        results.append(check("zodiac response shows full extras", "アドバイス:" in zodiac_text and "ラッキーパーソン:" in zodiac_text and "ラッキーメニュー:" in zodiac_text, zodiac_text))
        results.append(check("zodiac response omits source url and update date", "公式ページ:" not in zodiac_text and "更新:" not in zodiac_text, zodiac_text))

        empty_payload = fixture_payload()
        empty_payload["ranking"][7]["advice"] = ""
        empty_payload["ranking"][7]["person"] = ""
        empty_payload["ranking"][7]["menu"] = ""
        empty_bundle = horoscope.parse_payload(empty_payload)

        async def empty_extras_bundle(force_refresh=False):
            return empty_bundle

        horoscope.get_horoscope_bundle = empty_extras_bundle
        empty_extras_message = FakeMessage("<@1> さそり座 占い", command_text="さそり座 占い")
        results.append(check("empty extras omit blank labels", await horoscope.handle_horoscope_command(empty_extras_message, "さそり座 占い") is True))
        empty_extras_text = empty_extras_message.channel.messages[0]
        results.append(check("empty advice person menu labels are omitted", "アドバイス:" not in empty_extras_text and "ラッキーパーソン:" not in empty_extras_text and "ラッキーメニュー:" not in empty_extras_text, empty_extras_text))
        horoscope.get_horoscope_bundle = fake_bundle

        disabled_message = FakeMessage("<@1> 占い", command_text="占い")
        horoscope.settings_enabled = lambda guild_id, kind: False
        results.append(check("disabled feature consumes horoscope command without normal fallback", await horoscope.handle_horoscope_command(disabled_message, "占い") is True))
        results.append(check("disabled feature sends no normal mention response", disabled_message.channel.messages == [], disabled_message.channel.messages))

        async def failing_bundle(force_refresh=False):
            raise horoscope.MezamashiHoroscopeError("fixture timeout")

        horoscope.settings_enabled = lambda guild_id, kind: True
        horoscope.get_horoscope_bundle = failing_bundle
        failed_message = FakeMessage("<@1> 占い", command_text="占い")
        results.append(check("HTTP timeout or 4xx fails safe", await horoscope.handle_horoscope_command(failed_message, "占い") is True))
        results.append(check("cacheless failure sends short safe message", failed_message.channel.messages == ["占いデータを取得できませんでした。"], failed_message.channel.messages))

        stale_bundle = horoscope.parse_payload(fixture_payload())
        stale_bundle.stale = True

        async def stale_bundle_func(force_refresh=False):
            return stale_bundle

        horoscope.get_horoscope_bundle = stale_bundle_func
        stale_message = FakeMessage("<@1> 占い", command_text="占い")
        results.append(check("fetch failure with latest cache renders cached ranking", await horoscope.handle_horoscope_command(stale_message, "占い") is True))
        stale_text = stale_message.channel.messages[0]
        results.append(check("stale cache ranking starts from first rank", stale_text.startswith("🥇 1位") and "12位" in stale_text, stale_text))
        results.append(check("stale cache ranking omits title", "今日のめざまし占い" not in stale_text and "直近のめざまし占い" not in stale_text and "直近のめざましうらない" not in stale_text, stale_text))
        results.append(check("stale cache omits long stale explanation", "本日のデータを取得できないため" not in stale_text and "公式ページ:" not in stale_text and "更新:" not in stale_text and "出典:" not in stale_text, stale_text))

        stale_zodiac_message = FakeMessage("<@1> さそり座 占い", command_text="さそり座 占い")
        results.append(check("zodiac command uses latest cache when stale", await horoscope.handle_horoscope_command(stale_zodiac_message, "さそり座 占い") is True))
        results.append(check("stale zodiac keeps full details", "さそり座" in stale_zodiac_message.channel.messages[0] and "ラッキーメニュー:" in stale_zodiac_message.channel.messages[0], stale_zodiac_message.channel.messages))
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
        results.append(check("today cache uses today's data", first.target_date == "2026-09-11" and first.stale is False, first))
        results.append(check("cache miss fetches and saves memory cache", calls == ["fetch"], calls))
        results.append(check("cache hit avoids HTTP fetch", second.target_date == first.target_date and calls == ["fetch"], calls))

        stale = horoscope.parse_payload(fixture_payload())
        stale.target_date = "2026-09-10"
        stale.stale = True
        horoscope._memory_cache.clear()
        horoscope._latest_memory_cache = stale
        horoscope._latest_memory_cached_at = datetime.now(horoscope.JST) - timedelta(minutes=1)

        async def failing_fetch():
            calls.append("failed-fetch")
            raise horoscope.MezamashiHoroscopeError("fixture timeout")

        horoscope.fetch_official_horoscope = failing_fetch
        fallback = await horoscope.get_horoscope_bundle()
        results.append(check("fetch failure falls back to latest memory cache", fallback.stale is True and fallback.target_date == "2026-09-10", fallback))
        results.append(check("stale memory cache does not block today's fetch attempt", calls[-1:] == ["failed-fetch"], calls))
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


async def live_async():
    bundle = await horoscope.fetch_official_horoscope()
    print(
        "live target_date={0} count={1} first={2}:{3} last={4}:{5}".format(
            bundle.target_date,
            len(bundle.entries),
            bundle.entries[0].rank,
            bundle.entries[0].name,
            bundle.entries[-1].rank,
            bundle.entries[-1].name,
        )
    )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Fetch the current Fujitv horoscope JSON once.")
    args = parser.parse_args()
    if args.live:
        return 0 if asyncio.run(live_async()) else 1
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
