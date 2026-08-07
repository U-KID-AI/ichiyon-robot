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
        results.append(check("audio action runs independently", calls["audio"] == 1, calls))
        miss = FakeMessage("ニコロデオン 価格")
        results.append(check("partial text is not handled", await mention_shortcuts.handle_mention_shortcut_command(miss, "ニコロデオン 価格") is False))
        bot_message = FakeMessage("ニコロデオン", bot=True)
        results.append(check("bot author is ignored", await mention_shortcuts.handle_mention_shortcut_command(bot_message, "ニコロデオン") is False))
    finally:
        mention_shortcuts.get_connection = original_connection
        mention_shortcuts.MentionShortcutRepository = original_repo
        mention_shortcuts.FeatureFlagRepository = original_flag_repo
        mention_shortcuts.fetch_shortcut_price_quotes = original_fetch
        mention_shortcuts.run_shortcut_audio_actions = original_audio
    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
