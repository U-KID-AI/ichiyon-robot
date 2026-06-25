import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot import messages
from bot.services import runtime_db


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
        print("mention guard runtime check: {0}/{1} OK".format(self.ok, total))
        return 0 if self.ng == 0 else 1


class FakeChannel:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


class FakeConnection:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class FakeCounterRepository:
    values: Dict[str, int] = {}

    def __init__(self, connection) -> None:
        pass

    def ensure_counter(self, guild_id: str, count_key: str, name: str, *args, **kwargs):
        self.values.setdefault(count_key, 0)
        return {"count_key": count_key}

    def increment(self, guild_id: str, count_key: str, amount: int = 1, period_key=None):
        self.values[count_key] = self.values.get(count_key, 0) + amount
        return {"current_value": self.values[count_key]}


class FakeMentionReactionRepository:
    def __init__(self, connection) -> None:
        pass

    def list_reactions(self, guild_id: str, enabled=None, reaction_kind=None):
        if reaction_kind == "search":
            return []
        return [
            {
                "id": 1,
                "reaction_key": "quote",
                "keyword": "",
                "match_type": "prefix",
                "reaction_kind": "random_draw",
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        ]

    def list_choices(self, guild_id: str, mention_reaction_id: int, enabled=None):
        return [
            {
                "id": 10,
                "name": "quote",
                "body": "通常反応",
                "image_path": "",
                "appearance_rate": 1,
                "enabled": True,
            }
        ]


class FakeMessage:
    def __init__(self, content: str, author_id: int) -> None:
        bot_user = messages.get_bot().user
        self.content = content
        self.channel = FakeChannel()
        self.author = SimpleNamespace(id=author_id, display_name="user", name="user", mention="<@{0}>".format(author_id))
        self.guild = SimpleNamespace(id=111)
        self.mentions = [bot_user]
        self.reactions = []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)


def guard_effect() -> Dict[str, Any]:
    return {
        "id": 99,
        "effect_type": "mention_suffix_guard",
        "effect_config_json": {
            "target_user_ids": ["1290338867685363764"],
            "required_suffix": "さん",
            "warn_every": 3,
            "warning_message": "さんを付けろよ",
            "enabled": True,
        },
    }


async def run_checks() -> int:
    check = Check()
    old = {
        "counter": runtime_db.CounterRepository,
        "mention_repo": runtime_db.MentionReactionRepository,
        "feature_enabled": runtime_db.feature_enabled,
        "list_limited_effects": runtime_db.list_limited_effects,
        "list_effects": runtime_db.list_effects,
    }
    messages.configure(SimpleNamespace(user=SimpleNamespace(id=999)))
    runtime_db.CounterRepository = FakeCounterRepository
    runtime_db.MentionReactionRepository = FakeMentionReactionRepository
    runtime_db.feature_enabled = lambda connection, guild_id, feature_key: True
    runtime_db.list_effects = lambda connection, guild_id, target_type, target_id: []
    try:
        runtime_db.list_limited_effects = lambda connection, guild_id, message: [guard_effect()]
        FakeCounterRepository.values = {}
        for index in range(1, 4):
            message = FakeMessage("<@999> 名言", 1290338867685363764)
            action = await runtime_db.process_db_mention(message, "111", FakeConnection())
            if index < 3:
                check.add("guard blocks mention without suffix {0}".format(index), action.handled and message.channel.sent == [], str(message.channel.sent))
            else:
                check.add("guard warns every third mention", action.handled and message.channel.sent == ["さんを付けろよ"], str(message.channel.sent))

        message = FakeMessage("<@999> さん 名言", 1290338867685363764)
        action = await runtime_db.process_db_mention(message, "111", FakeConnection())
        check.add("guard allows mention with suffix", action.handled and message.channel.sent == ["通常反応"], str(message.channel.sent))

        message = FakeMessage("<@999> 名言", 222)
        action = await runtime_db.process_db_mention(message, "111", FakeConnection())
        check.add("guard does not affect other users", action.handled and message.channel.sent == ["通常反応"], str(message.channel.sent))

        runtime_db.list_limited_effects = lambda connection, guild_id, message: []
        message = FakeMessage("<@999> テスト 名言", 1290338867685363764)
        action = await runtime_db.process_db_mention(message, "111", FakeConnection())
        check.add("test text no longer suppresses mention", action.handled and message.channel.sent == ["通常反応"], str(message.channel.sent))
    finally:
        runtime_db.CounterRepository = old["counter"]
        runtime_db.MentionReactionRepository = old["mention_repo"]
        runtime_db.feature_enabled = old["feature_enabled"]
        runtime_db.list_limited_effects = old["list_limited_effects"]
        runtime_db.list_effects = old["list_effects"]
        runtime_db._PENDING_NEXT_EFFECTS.clear()
    return check.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_checks()))
