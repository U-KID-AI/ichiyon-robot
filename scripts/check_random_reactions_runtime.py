import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import admin.random_reactions as admin_random_reactions
import bot.services.random_reactions as random_reactions
from bot.repositories.random_reactions import default_random_reaction_settings


def check(name: str, ok: bool, detail: str = "") -> bool:
    safe_detail = str(detail).encode("unicode_escape", errors="backslashreplace").decode("ascii") if detail else ""
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(safe_detail) if safe_detail else ""))
    return ok


class FakeAuthor:
    def __init__(self, bot=False):
        self.bot = bot


class FakeChannel:
    def __init__(self, channel_id="100"):
        self.id = channel_id


class FakeMessage:
    def __init__(self, content="hello", channel_id="100", bot=False, webhook_id=None, message_type=None, fail_reaction=False):
        self.content = content
        self.channel = FakeChannel(channel_id)
        self.author = FakeAuthor(bot=bot)
        self.webhook_id = webhook_id
        self.type = message_type
        self.fail_reaction = fail_reaction
        self.reactions = []

    async def add_reaction(self, emoji):
        if self.fail_reaction:
            raise RuntimeError("missing permissions")
        self.reactions.append(emoji)


class FakeRepository:
    settings = {}

    def __init__(self, connection):
        self.connection = connection

    def get(self, guild_id):
        return dict(self.settings)


def settings(**overrides):
    base = default_random_reaction_settings("ichiyon", "guild-1")
    base.update({"enabled": True, "probability_percent": 100, "cooldown_seconds": 0})
    base.update(overrides)
    return base


async def run_service_checks():
    results = []
    original_repository = random_reactions.RandomReactionRepository
    try:
        random_reactions.RandomReactionRepository = FakeRepository

        FakeRepository.settings = settings(emoji="🍞")
        message = FakeMessage("normal text")
        results.append(check("enabled setting adds bread reaction", await random_reactions.maybe_add_random_emoji_reaction(message, "guild-1", object()) is True and message.reactions == ["🍞"], str(message.reactions)))

        FakeRepository.settings = settings(emoji="🍞")
        failing = FakeMessage("normal text", fail_reaction=True)
        results.append(check("add_reaction failure is warning only", await random_reactions.maybe_add_random_emoji_reaction(failing, "guild-1", object()) is False and failing.reactions == [], str(failing.reactions)))

        FakeRepository.settings = settings(enabled=False)
        off_message = FakeMessage("normal text")
        results.append(check("disabled setting does not react", await random_reactions.maybe_add_random_emoji_reaction(off_message, "guild-1", object()) is False and off_message.reactions == [], str(off_message.reactions)))
    finally:
        random_reactions.RandomReactionRepository = original_repository
        random_reactions._RANDOM_REACTION_COOLDOWNS.clear()
    return results


def main() -> int:
    results = []

    default_settings = default_random_reaction_settings("ichiyon", "guild-1")
    results.append(check("default is off", default_settings["enabled"] is False, str(default_settings)))
    results.append(check("default emoji is bread", default_settings["emoji"] == "🍞", str(default_settings)))
    results.append(check("default probability is 1 percent", float(default_settings["probability_percent"]) == 1.0, str(default_settings)))
    results.append(check("default cooldown is 600 seconds", int(default_settings["cooldown_seconds"]) == 600, str(default_settings)))

    results.append(check("channel id normalizes mentions", random_reactions.split_channel_ids("<#123>\n456, ４５６") == ["123", "456"], str(random_reactions.split_channel_ids("<#123>\n456, ４５６"))))
    results.append(check("excluded channel has priority", random_reactions.random_reaction_channel_allowed(settings(target_channel_ids="100\n200", excluded_channel_ids="100"), "100") is False))
    results.append(check("target channel allows listed channel", random_reactions.random_reaction_channel_allowed(settings(target_channel_ids="100\n200"), "100") is True))
    results.append(check("target channel rejects unlisted channel", random_reactions.random_reaction_channel_allowed(settings(target_channel_ids="100\n200"), "300") is False))
    results.append(check("empty target allows all non-excluded channels", random_reactions.random_reaction_channel_allowed(settings(target_channel_ids="", excluded_channel_ids="200"), "100") is True))

    results.append(check("probability zero never hits", random_reactions.random_reaction_probability_hit(0) is False))
    results.append(check("probability hundred always hits", random_reactions.random_reaction_probability_hit(100) is True))

    random_reactions._RANDOM_REACTION_COOLDOWNS.clear()
    cd_settings = settings(cooldown_seconds=600, emoji="🍞")
    results.append(check("cooldown initially allows", random_reactions.random_reaction_cooldown_allows(cd_settings, "guild-1", now=1000) is True))
    random_reactions.mark_random_reaction_cooldown(cd_settings, "guild-1", now=1000)
    results.append(check("cooldown blocks within window", random_reactions.random_reaction_cooldown_allows(cd_settings, "guild-1", now=1200) is False))
    results.append(check("cooldown allows after window", random_reactions.random_reaction_cooldown_allows(cd_settings, "guild-1", now=1600) is True))
    results.append(check("cooldown key is guild scoped", random_reactions.random_reaction_cooldown_allows(cd_settings, "guild-2", now=1200) is True))

    results.append(check("human text message is eligible", random_reactions.is_human_text_message(FakeMessage("hello")) is True))
    results.append(check("url in normal text remains eligible", random_reactions.is_human_text_message(FakeMessage("https://example.com")) is True))
    results.append(check("bot message is excluded", random_reactions.is_human_text_message(FakeMessage("hello", bot=True)) is False))
    results.append(check("webhook message is excluded", random_reactions.is_human_text_message(FakeMessage("hello", webhook_id="wh")) is False))
    results.append(check("empty message is excluded", random_reactions.is_human_text_message(FakeMessage("   ")) is False))

    eligible = random_reactions.should_add_random_reaction(FakeMessage("hello", channel_id="100"), settings(target_channel_ids="100", probability_percent=100), "guild-1")
    excluded = random_reactions.should_add_random_reaction(FakeMessage("hello", channel_id="200"), settings(target_channel_ids="100", probability_percent=100), "guild-1")
    results.append(check("should_add accepts eligible configured message", eligible is True))
    results.append(check("should_add rejects channel mismatch", excluded is False))

    form = admin_random_reactions.build_random_reaction_form("on", "🍞", "1.5", "600", "<#100>\n200", "300")
    errors, probability, cooldown = admin_random_reactions.validate_random_reaction_form(form)
    results.append(check("admin form normalizes channel ids", form["target_channel_ids"] == "100\n200" and form["excluded_channel_ids"] == "300", str(form)))
    results.append(check("admin form accepts valid values", errors == [] and probability == 1.5 and cooldown == 600, str((errors, probability, cooldown))))
    bad_form = admin_random_reactions.build_random_reaction_form("on", "", "101", "-1", "", "")
    bad_errors, _, _ = admin_random_reactions.validate_random_reaction_form(bad_form)
    results.append(check("admin form rejects empty emoji and invalid ranges", len(bad_errors) >= 3, str(bad_errors)))
    zero_form = admin_random_reactions.form_from_settings(settings(probability_percent=0, cooldown_seconds=0))
    results.append(check("admin form preserves zero probability and cooldown", zero_form["probability_percent"] == "0" and zero_form["cooldown_seconds"] == "0", str(zero_form)))

    migration = (PROJECT_ROOT / "migrations" / "037_add_random_reaction_settings.sql").read_text(encoding="utf-8")
    migration_detail = "migrations/037_add_random_reaction_settings.sql"
    results.append(check("migration creates bot guild scoped primary key", "PRIMARY KEY (bot_id, guild_id)" in migration, migration_detail))
    results.append(check("migration default is disabled", "enabled BOOLEAN NOT NULL DEFAULT FALSE" in migration, migration_detail))
    results.append(check("migration has probability and cooldown constraints", "probability_percent >= 0" in migration and "cooldown_seconds >= 0" in migration, migration_detail))

    repository_source = (PROJECT_ROOT / "bot" / "repositories" / "random_reactions.py").read_text(encoding="utf-8")
    results.append(check("repository is bot scoped", "self.bot_id = bot_id or config.BOT_INSTANCE_ID" in repository_source and "WHERE bot_id = %s AND guild_id = %s" in repository_source, "bot/repositories/random_reactions.py"))
    results.append(check("repository upsert is bot guild scoped", "ON CONFLICT (bot_id, guild_id)" in repository_source, "bot/repositories/random_reactions.py"))

    runtime_source = (PROJECT_ROOT / "bot" / "services" / "runtime_db.py").read_text(encoding="utf-8")
    results.append(check("runtime runs random reaction after unhandled normal DB message", "not action.handled and not entered" in runtime_source and "maybe_add_random_emoji_reaction" in runtime_source, "bot/services/runtime_db.py"))

    results.extend(asyncio.run(run_service_checks()))

    ok_count = sum(1 for item in results if item)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
