import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import bot.services.reaction_thresholds as runtime


class Check:
    def __init__(self) -> None:
        self.ok = 0
        self.ng = 0

    def add(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.ok += 1
            print("[OK] {0}".format(name))
            return
        self.ng += 1
        print("[NG] {0} - {1}".format(name, detail))

    def finish(self) -> int:
        total = self.ok + self.ng
        print("reaction threshold runtime check: {0}/{1} OK".format(self.ok, total))
        return 0 if self.ng == 0 else 1


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self) -> None:
        pass


class FakeFeatureFlagRepository:
    def __init__(self, connection) -> None:
        pass

    def is_enabled(self, guild_id: str, feature_key: str, default: bool = True) -> bool:
        return True


class FakeReactionThresholdRepository:
    events = set()
    rules: List[Dict[str, Any]] = []

    def __init__(self, connection) -> None:
        pass

    def list_rules(self, guild_id: str, enabled: Optional[bool] = True):
        return list(self.rules)

    def record_event(self, guild_id: str, rule_id: int, message_id: str, channel_id: str, emoji_key: str, threshold: int) -> bool:
        key = (guild_id, rule_id, message_id, emoji_key, threshold)
        if key in self.events:
            return False
        self.events.add(key)
        return True


class FakeMentionReactionRepository:
    reactions: Dict[str, Dict[str, Any]] = {}
    choices: Dict[int, List[Dict[str, Any]]] = {}

    def __init__(self, connection) -> None:
        pass

    def get_by_key(self, guild_id: str, reaction_key: str):
        return self.reactions.get(reaction_key)

    def list_choices(self, guild_id: str, mention_reaction_id: int, enabled: Optional[bool] = None):
        rows = list(self.choices.get(mention_reaction_id, []))
        if enabled is None:
            return rows
        return [row for row in rows if bool(row.get("enabled", True)) == enabled]


class FakeMessage:
    def __init__(self, count: int, emoji: str = "🍒", author_bot: bool = False) -> None:
        self.content = "hello"
        self.author = SimpleNamespace(id=123, bot=author_bot, display_name="user", name="user")
        self.reactions = [SimpleNamespace(emoji=emoji, count=count)]
        self.replies = []

    async def reply(self, content=None, **kwargs):
        self.replies.append(content)


class FakeChannel:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message

    async def fetch_message(self, message_id: int):
        return self.message


class FakeBot:
    def __init__(self, message: FakeMessage) -> None:
        self.user = SimpleNamespace(id=999)
        self.channel = FakeChannel(message)

    def get_channel(self, channel_id: int):
        return self.channel


def payload(emoji: str = "🍒", channel_id: int = 10, message_id: int = 20):
    return SimpleNamespace(guild_id=111, channel_id=channel_id, message_id=message_id, emoji=emoji, user_id=123)


def base_rule(config: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": 1, "name": "rule", "enabled": True, "config_json": config}


async def run_case(message: FakeMessage, rule_config: Dict[str, Any], emoji: str = "🍒", channel_id: int = 10, message_id: int = 20) -> bool:
    FakeReactionThresholdRepository.rules = [base_rule(rule_config)]
    return await runtime.handle_db_reaction_threshold(payload(emoji, channel_id, message_id), FakeBot(message))


async def run_checks() -> int:
    check = Check()
    old = {
        "get_connection": runtime.get_connection,
        "repo": runtime.ReactionThresholdRepository,
        "mention_repo": runtime.MentionReactionRepository,
        "flags": runtime.FeatureFlagRepository,
    }
    runtime.get_connection = lambda: FakeConnection()
    runtime.ReactionThresholdRepository = FakeReactionThresholdRepository
    runtime.MentionReactionRepository = FakeMentionReactionRepository
    runtime.FeatureFlagRepository = FakeFeatureFlagRepository
    try:
        FakeReactionThresholdRepository.events = set()
        FakeMentionReactionRepository.reactions = {
            "quotes": {"id": 100, "reaction_key": "quotes", "enabled": True},
            "disabled": {"id": 101, "reaction_key": "disabled", "enabled": False},
        }
        FakeMentionReactionRepository.choices = {
            100: [
                {"id": 1000, "body": "quote reply", "appearance_rate": 1, "enabled": True},
            ],
            101: [
                {"id": 1001, "body": "disabled reply", "appearance_rate": 1, "enabled": True},
            ],
        }
        message = FakeMessage(4)
        handled = await run_case(message, {"threshold": 5, "reply_message": "5つ"})
        check.add("less than threshold does not reply", handled is False and message.replies == [], str(message.replies))

        message = FakeMessage(5)
        handled = await run_case(message, {"threshold": 5, "reply_message": "5つ"})
        check.add("threshold replies", handled is True and message.replies == ["5つ"], str(message.replies))

        message = FakeMessage(6)
        handled = await run_case(message, {"threshold": 5, "reply_message": "5つ"})
        check.add("same message emoji does not reply twice", handled is False and message.replies == [], str(message.replies))

        message = FakeMessage(5, "🍋")
        handled = await run_case(message, {"threshold": 5, "reply_message": "{emoji}"}, "🍋")
        check.add("different emoji is separate", handled is True and message.replies == ["🍋"], str(message.replies))

        message = FakeMessage(5)
        handled = await run_case(message, {"threshold": 5, "reply_message": "allowed", "allowed_channel_ids": ["99"]}, channel_id=10, message_id=30)
        check.add("allowed channel blocks other channels", handled is False and message.replies == [], str(message.replies))

        message = FakeMessage(5)
        handled = await run_case(message, {"threshold": 5, "reply_message": "ignored", "ignored_channel_ids": ["10"]}, channel_id=10, message_id=31)
        check.add("ignored channel blocks channel", handled is False and message.replies == [], str(message.replies))

        message = FakeMessage(2)
        handled = await run_case(
            message,
            {
                "threshold": 2,
                "reply_source_type": "mention_reaction",
                "reply_reaction_key": "quote",
                "reply_message": "fallback",
            },
            message_id=40,
        )
        check.add("mention reaction source replies from quote dataset", handled is True and message.replies == ["quote reply"], str(message.replies))

        message = FakeMessage(2)
        handled = await run_case(
            message,
            {
                "threshold": 2,
                "reply_source_type": "mention_reaction",
                "reply_reaction_key": "missing",
                "reply_message": "fallback",
            },
            message_id=41,
        )
        check.add("missing mention reaction source falls back to fixed reply", handled is True and message.replies == ["fallback"], str(message.replies))

        message = FakeMessage(2)
        handled = await run_case(
            message,
            {
                "threshold": 2,
                "reply_source_type": "mention_reaction",
                "reply_reaction_key": "missing",
                "reply_message": "",
            },
            message_id=42,
        )
        check.add("missing source without fallback is safe no-op", handled is False and message.replies == [], str(message.replies))
    finally:
        runtime.get_connection = old["get_connection"]
        runtime.ReactionThresholdRepository = old["repo"]
        runtime.MentionReactionRepository = old["mention_repo"]
        runtime.FeatureFlagRepository = old["flags"]
    return check.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_checks()))
