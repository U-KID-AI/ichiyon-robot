import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.repositories.mention_shortcuts import normalize_shortcut_trigger
from bot.services import mention_shortcuts
from bot.services.game_provider import GamePriceQuote


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class FakeMessage:
    def __init__(self, command="ニコロデオン", bot=False):
        self.guild = SimpleNamespace(id=123)
        self.author = SimpleNamespace(id=456, bot=bot)
        self.channel = FakeChannel()
        self.content = "@Bot " + command


async def main_async():
    results = []
    results.append(check("shortcut trigger normalizes full-width spaces", normalize_shortcut_trigger(" ニコロデオン　 ") == "ニコロデオン"))
    results.append(check("shortcut trigger is casefolded", normalize_shortcut_trigger("ABC") == "abc"))
    migration = (ROOT_DIR / "migrations" / "041_add_mention_shortcuts.sql").read_text(encoding="utf-8")
    results.append(check("migration creates mention_shortcuts", "CREATE TABLE IF NOT EXISTS mention_shortcuts" in migration))
    results.append(check("migration unique scope is bot guild trigger", "mention_shortcuts_scope_trigger_unique UNIQUE (bot_id, guild_id, trigger_key)" in migration))
    results.append(check("migration avoids trigger-only unique", "UNIQUE (trigger_text)" not in migration and "UNIQUE (trigger_key)" not in migration))
    results.append(check("migration seeds steam app id 1414850", "'1414850'" in migration))
    results.append(check("price target seed has unique conflict target", "mention_shortcut_price_target_unique UNIQUE" in migration and "ON CONFLICT DO NOTHING" in migration))
    seed_block = migration.split("DO $$", 1)[1]
    results.append(check("migration does not seed uncertain NTPrices target", "(v_shortcut_id, 'ntprices'" not in seed_block.lower()))

    original_connection = mention_shortcuts.get_connection
    original_repo = mention_shortcuts.MentionShortcutRepository
    original_flag_repo = mention_shortcuts.FeatureFlagRepository
    original_fetch = mention_shortcuts.fetch_shortcut_price_quotes
    original_audio = mention_shortcuts.run_shortcut_audio_actions
    original_provider_fetch = mention_shortcuts.game_provider.fetch_price_quote
    calls = {"audio": 0, "fetch": 0}

    class DummyConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeFlagRepo:
        def __init__(self, connection, bot_id=None):
            pass

        def is_enabled(self, guild_id, feature_key, default=True):
            return True

    class FakeRepo:
        def __init__(self, connection, bot_id=None):
            pass

        def find_by_trigger(self, guild_id, text):
            if normalize_shortcut_trigger(text) == "ニコロデオン":
                return {"id": 1, "name": "ニコロデオン", "trigger_text": "ニコロデオン"}
            return None

        def list_price_targets(self, shortcut_id, enabled=True):
            return [{"provider": "steam", "provider_product_id": "1414850", "lookup_type": "app_id", "display_name": "Steam"}]

        def list_audio_actions(self, shortcut_id, enabled=True):
            return [{"id": 10, "audio_asset_id": 99, "volume_override": 50}]

    async def fake_fetch(targets):
        calls["fetch"] += 1
        return [
            GamePriceQuote(
                provider="steam",
                store_name="Steam",
                provider_product_id="1414850",
                title="Nickelodeon All-Star Brawl",
                current_price=5500,
                regular_price=5500,
                discount_percent=0,
                currency="JPY",
                formatted_current_price="5,500円",
                formatted_regular_price="5,500円",
                fetched_at=1786208400.0,
                status="ok",
            )
        ]

    async def fake_audio(message, actions):
        calls["audio"] += 1
        raise RuntimeError("audio failure should not escape")

    # Use a non-raising audio wrapper to verify service itself keeps price send independent.
    async def fake_audio_safe(message, actions):
        calls["audio"] += 1

    mention_shortcuts.get_connection = lambda: DummyConnection()
    mention_shortcuts.MentionShortcutRepository = FakeRepo
    mention_shortcuts.FeatureFlagRepository = FakeFlagRepo
    mention_shortcuts.fetch_shortcut_price_quotes = fake_fetch
    mention_shortcuts.run_shortcut_audio_actions = fake_audio_safe
    try:
        message = FakeMessage("ニコロデオン")
        handled = await mention_shortcuts.handle_mention_shortcut_command(message, "ニコロデオン")
        results.append(check("exact shortcut is handled", handled))
        results.append(check("price embed is sent once", len(message.channel.sent) == 1, message.channel.sent))
        sent_embed = message.channel.sent[0][1]["embeds"][0]
        field_value = sent_embed.fields[0].value
        footer_text = sent_embed.footer.text
        results.append(check("shortcut price current is normalized for Discord", "現在価格: 5,500円" in field_value, field_value))
        results.append(check("shortcut price regular is normalized for Discord", "通常価格: 5,500円" in field_value, field_value))
        results.append(check("shortcut footer shows provider timestamp", "Steam:" in footer_text and "providerごとの取得時刻" not in footer_text, footer_text))
        results.append(check("audio action runs independently", calls["audio"] == 1, calls))
        spaced = FakeMessage(" ニコロデオン ")
        results.append(check("shortcut trims surrounding spaces", await mention_shortcuts.handle_mention_shortcut_command(spaced, " ニコロデオン ") is True))
        miss = FakeMessage("ニコロデオン 価格")
        results.append(check("partial text is not handled", await mention_shortcuts.handle_mention_shortcut_command(miss, "ニコロデオン 価格") is False))
        suffix = FakeMessage("ニコロデオンって何？")
        results.append(check("exact shortcut rejects suffix text", await mention_shortcuts.handle_mention_shortcut_command(suffix, "ニコロデオンって何？") is False))
        empty = FakeMessage("")
        results.append(check("empty mention is not shortcut", await mention_shortcuts.handle_mention_shortcut_command(empty, "") is False))
        voice_command = FakeMessage("もしもししよ")
        results.append(check("voice command is not shortcut", await mention_shortcuts.handle_mention_shortcut_command(voice_command, "もしもししよ") is False))
        deck_command = FakeMessage("デッキ エルフ")
        results.append(check("deck command is not shortcut", await mention_shortcuts.handle_mention_shortcut_command(deck_command, "デッキ エルフ") is False))
        bot_message = FakeMessage("ニコロデオン", bot=True)
        results.append(check("bot author is ignored", await mention_shortcuts.handle_mention_shortcut_command(bot_message, "ニコロデオン") is False))
    finally:
        mention_shortcuts.get_connection = original_connection
        mention_shortcuts.MentionShortcutRepository = original_repo
        mention_shortcuts.FeatureFlagRepository = original_flag_repo
        mention_shortcuts.fetch_shortcut_price_quotes = original_fetch
        mention_shortcuts.run_shortcut_audio_actions = original_audio
        mention_shortcuts.game_provider.fetch_price_quote = original_provider_fetch

    async def fake_provider_fetch(provider, product_id, lookup_type="", display_name=""):
        if provider == "steam":
            raise RuntimeError("steam unavailable")
        return GamePriceQuote(
            provider=provider,
            store_name=display_name or provider,
            provider_product_id=product_id,
            title=display_name or product_id,
            status="error",
            error_code="not_configured",
        )

    mention_shortcuts.game_provider.fetch_price_quote = fake_provider_fetch
    try:
        quotes = await mention_shortcuts.fetch_shortcut_price_quotes(
            [
                {"provider": "steam", "provider_product_id": "1414850", "lookup_type": "app_id", "display_name": "Steam"},
                {"provider": "itad", "provider_product_id": "1414850", "lookup_type": "app_id", "display_name": "ITAD"},
            ]
        )
        results.append(check("provider exception does not abort shortcut prices", len(quotes) == 2, quotes))
        results.append(check("steam failure is represented as error quote", quotes[0].provider == "steam" and quotes[0].status == "error"))
        results.append(check("unset optional provider still returns status quote", quotes[1].provider == "itad" and quotes[1].error_code == "not_configured"))
    finally:
        mention_shortcuts.game_provider.fetch_price_quote = original_provider_fetch

    async def fake_partial_provider_fetch(provider, product_id, lookup_type="", display_name=""):
        if provider == "itad":
            raise RuntimeError("itad unavailable")
        return GamePriceQuote(
            provider=provider,
            store_name=display_name or provider,
            provider_product_id=product_id,
            title=display_name or product_id,
            current_price=5500,
            regular_price=5500,
            discount_percent=0,
            formatted_current_price="5,500円",
            formatted_regular_price="5,500円",
            fetched_at=1786208400.0,
            status="ok",
        )

    mention_shortcuts.game_provider.fetch_price_quote = fake_partial_provider_fetch
    try:
        quotes = await mention_shortcuts.fetch_shortcut_price_quotes(
            [
                {"provider": "steam", "provider_product_id": "1414850", "lookup_type": "app_id", "display_name": "Steam"},
                {"provider": "itad", "provider_product_id": "1414850", "lookup_type": "app_id", "display_name": "ITAD"},
            ]
        )
        results.append(check("optional provider failure does not suppress steam", quotes[0].ok and quotes[1].status == "error"))
        embeds = mention_shortcuts.build_shortcut_embeds({"name": "ニコロデオン"}, quotes)
        results.append(check("partial failure still builds price embed", len(embeds) == 1 and len(embeds[0].fields) == 2))
        results.append(check("shortcut footer marks failed provider as not fetched", "ITAD: 未取得" in embeds[0].footer.text, embeds[0].footer.text))
    finally:
        mention_shortcuts.game_provider.fetch_price_quote = original_provider_fetch

    itad_quote = GamePriceQuote(
        provider="itad",
        store_name="PC過去最安(ITAD)",
        provider_product_id="itad-game-id",
        title="Nickelodeon All-Star Brawl",
        current_price=3785,
        historical_low=206,
        currency="JPY",
        formatted_current_price="3,785円",
        formatted_historical_low="206円",
        current_store_name="GameBillet",
        historical_low_store_name="Fanatical",
        fetched_at=1786208400.0,
        status="ok",
        metadata={"itad_current_shop": "GameBillet", "itad_low_shop": "Fanatical"},
    )
    itad_embeds = mention_shortcuts.build_shortcut_embeds({"name": "ニコロデオン"}, [itad_quote])
    itad_fields = {field.name: field.value for field in itad_embeds[0].fields} if itad_embeds else {}
    current_field = itad_fields.get("PC現在最安(ITAD)", "")
    low_field = itad_fields.get("PC過去最安(ITAD)", "")
    results.append(check("ITAD current best is displayed separately", "現在最安: 3,785円" in current_field and "販売店: GameBillet" in current_field, current_field))
    results.append(check("ITAD history low is displayed separately", "過去最安: 206円" in low_field and "販売店: Fanatical" in low_field, low_field))
    results.append(check("ITAD embed does not show regular price", all("通常価格" not in value and "割引" not in value for value in itad_fields.values()), itad_fields))
    legacy_itad_quote = GamePriceQuote(
        provider="itad",
        store_name="PC過去最安(ITAD)",
        provider_product_id="itad-game-id",
        title="legacy cache",
        current_price=3785,
        historical_low=206,
        currency="JPY",
        formatted_current_price="3,785円",
        formatted_historical_low="206円",
        fetched_at=1786208400.0,
        status="ok",
        metadata={"itad_current_shop": "GameBillet", "itad_low_shop": "Fanatical"},
    )
    legacy_fields = {field.name: field.value for field in mention_shortcuts.build_shortcut_embeds({"name": "ニコロデオン"}, [legacy_itad_quote])[0].fields}
    results.append(check("legacy ITAD cache metadata keeps current shop", "販売店: GameBillet" in legacy_fields.get("PC現在最安(ITAD)", ""), legacy_fields))
    overview_failed = GamePriceQuote(
        provider="itad",
        store_name="PC過去最安(ITAD)",
        provider_product_id="itad-game-id",
        title="overview failed",
        historical_low=206,
        currency="JPY",
        formatted_historical_low="206円",
        historical_low_store_name="Fanatical",
        fetched_at=1786208400.0,
        status="ok",
        metadata={"itad_overview_error": "http_500"},
    )
    overview_failed_fields = {field.name: field.value for field in mention_shortcuts.build_shortcut_embeds({"name": "ニコロデオン"}, [overview_failed])[0].fields}
    results.append(check("ITAD overview failure does not hide history low", "過去最安: 206円" in overview_failed_fields.get("PC過去最安(ITAD)", ""), overview_failed_fields))
    history_failed = GamePriceQuote(
        provider="itad",
        store_name="PC過去最安(ITAD)",
        provider_product_id="itad-game-id",
        title="history failed",
        current_price=3785,
        currency="JPY",
        formatted_current_price="3,785円",
        current_store_name="GameBillet",
        fetched_at=1786208400.0,
        status="ok",
        metadata={"itad_historylow_error": "http_503"},
    )
    history_failed_fields = {field.name: field.value for field in mention_shortcuts.build_shortcut_embeds({"name": "ニコロデオン"}, [history_failed])[0].fields}
    results.append(check("ITAD history failure does not hide current best", "現在最安: 3,785円" in history_failed_fields.get("PC現在最安(ITAD)", ""), history_failed_fields))

    original_fetch_json = mention_shortcuts.game_provider.fetch_json
    mention_shortcuts.game_provider._PRICE_CACHE.clear()

    async def fake_steam_fetch_json(url, policy=None):
        return {
            "1414850": {
                "success": True,
                "data": {
                    "name": "Nickelodeon All-Star Brawl",
                    "type": "game",
                    "price_overview": {
                        "currency": "JPY",
                        "initial": 515000,
                        "final": 515000,
                        "final_formatted": "¥ 5,150",
                        "discount_percent": 0,
                    },
                },
            }
        }

    mention_shortcuts.game_provider.fetch_json = fake_steam_fetch_json
    try:
        quotes = await mention_shortcuts.fetch_shortcut_price_quotes(
            [
                {"provider": "steam", "provider_product_id": "1414850", "lookup_type": "app_id", "display_name": "Steam"},
            ]
        )
        embeds = mention_shortcuts.build_shortcut_embeds({"name": "ニコロデオン"}, quotes)
        value = embeds[0].fields[0].value if embeds else ""
        results.append(check("shortcut provider cache quote current is normalized", "現在価格: 5,150円" in value, value))
        results.append(check("shortcut provider cache quote regular is normalized", "通常価格: 5,150円" in value, value))
        cached_quotes = await mention_shortcuts.fetch_shortcut_price_quotes(
            [
                {"provider": "steam", "provider_product_id": "1414850", "lookup_type": "app_id", "display_name": "Steam"},
            ]
        )
        cached_embeds = mention_shortcuts.build_shortcut_embeds({"name": "ニコロデオン"}, cached_quotes)
        cached_value = cached_embeds[0].fields[0].value if cached_embeds else ""
        results.append(check("shortcut cached quote regular remains normalized", "通常価格: 5,150円" in cached_value and "515,000円" not in cached_value, cached_value))
    finally:
        mention_shortcuts.game_provider.fetch_json = original_fetch_json
        mention_shortcuts.game_provider._PRICE_CACHE.clear()
    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
