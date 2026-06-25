import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import discord

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
        print("reaction runtime check: {0}/{1} OK".format(self.ok, total))
        return 0 if self.ng == 0 else 1


class FakeChannel:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, content=None, file=None):
        self.sent.append({"content": content, "file": file})


class FakeMessage:
    def __init__(self, content: str, fail_reaction: bool = False) -> None:
        self.content = content
        self.channel = FakeChannel()
        self.author = SimpleNamespace(id=222, display_name="tester", name="tester", mention="<@222>")
        self.guild = SimpleNamespace(id=111)
        self.mentions = []
        self.reactions = []
        self.fail_reaction = fail_reaction

    async def add_reaction(self, emoji):
        if self.fail_reaction:
            raise discord.DiscordException("reaction denied")
        self.reactions.append(emoji)


class FakeMentionReactionRepository:
    choices = []

    def __init__(self, connection) -> None:
        pass

    def list_reactions(self, guild_id: str, enabled=None, reaction_kind=None):
        if reaction_kind == "search":
            return []
        return [
            {
                "id": 10,
                "guild_id": guild_id,
                "reaction_key": "check_quote",
                "keyword": "名言",
                "match_type": "exact",
                "reaction_kind": "random_draw",
                "name": "名言",
                "enabled": True,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        ]

    def list_choices(self, guild_id: str, mention_reaction_id: int, enabled=None):
        return list(self.choices)


class FakeAutoReactionRepository:
    rows = []

    def __init__(self, connection) -> None:
        pass

    def list_reactions(self, guild_id: str, enabled=None):
        return list(self.rows)


async def fake_send_text_or_image(channel_or_message, text, image_path):
    content = (text or "").strip()
    image = (image_path or "").strip()
    if not content and not image:
        return False
    channel = getattr(channel_or_message, "channel", channel_or_message)
    await channel.send(content or None, file=image or None)
    return True


def configure_runtime() -> Dict[str, Any]:
    old = {
        "mention_repo": runtime_db.MentionReactionRepository,
        "auto_repo": runtime_db.AutoReactionRepository,
        "feature_enabled": runtime_db.feature_enabled,
        "list_effects": runtime_db.list_effects,
        "list_limited_effects": runtime_db.list_limited_effects,
        "send_text_or_image": runtime_db.send_text_or_image,
    }
    bot_user = SimpleNamespace(id=999)
    messages.configure(SimpleNamespace(user=bot_user))
    runtime_db.MentionReactionRepository = FakeMentionReactionRepository
    runtime_db.AutoReactionRepository = FakeAutoReactionRepository
    runtime_db.feature_enabled = lambda connection, guild_id, feature_key: True
    runtime_db.list_effects = lambda connection, guild_id, target_type, target_id: []
    runtime_db.list_limited_effects = lambda connection, guild_id, message: []
    runtime_db.send_text_or_image = fake_send_text_or_image
    return old


def restore_runtime(old: Dict[str, Any]) -> None:
    runtime_db.MentionReactionRepository = old["mention_repo"]
    runtime_db.AutoReactionRepository = old["auto_repo"]
    runtime_db.feature_enabled = old["feature_enabled"]
    runtime_db.list_effects = old["list_effects"]
    runtime_db.list_limited_effects = old["list_limited_effects"]
    runtime_db.send_text_or_image = old["send_text_or_image"]
    runtime_db._PENDING_NEXT_EFFECTS.clear()


def make_auto_row(response_text: str, emoji_internal: str) -> Dict[str, Any]:
    return {
        "id": 20,
        "trigger_text": "ping",
        "response_text": response_text,
        "image_path": None,
        "emoji_internal": emoji_internal,
        "match_type": "contains",
        "priority": 0,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


def make_choice(body: str, emoji_internal: str) -> Dict[str, Any]:
    return {
        "id": 30,
        "name": "check_choice",
        "body": body,
        "image_path": None,
        "emoji_internal": emoji_internal,
        "appearance_rate": 1,
        "enabled": True,
    }


async def run_checks() -> int:
    check = Check()
    old = configure_runtime()
    try:
        FakeAutoReactionRepository.rows = [make_auto_row("", "🍒")]
        message = FakeMessage("ping")
        action = await runtime_db.process_db_auto_reaction(message, "111", None)
        check.add("auto reaction emoji only handled", action.handled and message.reactions == ["🍒"], str(message.reactions))
        check.add("auto reaction emoji only sends no message", len(message.channel.sent) == 0, str(message.channel.sent))

        FakeAutoReactionRepository.rows = [make_auto_row("pong", "<:cherry:123456789012345678>")]
        message = FakeMessage("ping")
        action = await runtime_db.process_db_auto_reaction(message, "111", None)
        check.add("auto reaction text plus custom emoji handled", action.handled, "")
        check.add("auto reaction text sent", message.channel.sent[0]["content"] == "pong", str(message.channel.sent))
        check.add("auto reaction custom emoji added", message.reactions == ["<:cherry:123456789012345678>"], str(message.reactions))

        FakeMentionReactionRepository.choices = [make_choice("", "🍒")]
        message = FakeMessage("<@999> 名言")
        message.mentions = [messages.get_bot().user]
        action = await runtime_db.process_db_mention(message, "111", None)
        check.add("mention choice emoji only handled", action.handled and message.reactions == ["🍒"], str(message.reactions))
        check.add("mention choice emoji only sends no message", len(message.channel.sent) == 0, str(message.channel.sent))

        FakeMentionReactionRepository.choices = [make_choice("名言だよ", "<a:spin:123456789012345678>")]
        message = FakeMessage("<@999> 名言")
        message.mentions = [messages.get_bot().user]
        action = await runtime_db.process_db_mention(message, "111", None)
        check.add("mention choice text plus custom emoji handled", action.handled, "")
        check.add("mention choice text sent", message.channel.sent[0]["content"] == "名言だよ", str(message.channel.sent))
        check.add("mention choice custom emoji added", message.reactions == ["<a:spin:123456789012345678>"], str(message.reactions))

        FakeAutoReactionRepository.rows = [make_auto_row("pong", "🍒")]
        message = FakeMessage("ping", fail_reaction=True)
        action = await runtime_db.process_db_auto_reaction(message, "111", None)
        check.add("reaction permission error does not stop text action", action.handled and message.channel.sent[0]["content"] == "pong", "")

        FakeMentionReactionRepository.choices = [make_choice("", "🍒")]
        message = FakeMessage("<@999> 名言", fail_reaction=True)
        message.mentions = [messages.get_bot().user]
        try:
            action = await runtime_db.process_db_mention(message, "111", None)
            no_exception = True
        except Exception:
            no_exception = False
        check.add("reaction permission error does not crash mention action", no_exception, "")
        check.add("mention reaction-only permission error safely unhandled", no_exception and not action.handled, "")
    finally:
        restore_runtime(old)
    return check.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_checks()))
