import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional


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
        print("random draw n-pull check: {0}/{1} OK".format(self.ok, total))
        return 0 if self.ng == 0 else 1


class FakeChannel:
    def __init__(self, fail_on_send: bool = False) -> None:
        self.sent: List[str] = []
        self.fail_on_send = fail_on_send

    async def send(self, content=None, **kwargs):
        if self.fail_on_send:
            raise RuntimeError("send failed")
        self.sent.append(str(content or ""))
        return SimpleNamespace(id=len(self.sent))


class FakeMessage:
    def __init__(
        self,
        command_text: str,
        *,
        bot_author: bool = False,
        fail_on_send: bool = False,
    ) -> None:
        self.command_text = command_text
        self.content = "<@999> {0}".format(command_text)
        self.channel = FakeChannel(fail_on_send=fail_on_send)
        self.guild = SimpleNamespace(id=111)
        self.author = SimpleNamespace(
            id=999 if bot_author else 123,
            bot=bot_author,
            display_name="user",
            name="user",
            mention="<@123>",
        )
        self.reactions: List[str] = []

    async def add_reaction(self, emoji):
        self.reactions.append(str(emoji))


class FakeConnection:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class FakeMentionReactionRepository:
    reactions: List[Dict[str, Any]] = []
    choices_by_reaction_id: Dict[int, List[Dict[str, Any]]] = {}

    def __init__(self, connection) -> None:
        pass

    def list_reactions(self, guild_id: str, enabled=None, reaction_kind=None):
        result = []
        for reaction in self.reactions:
            if enabled is True and not reaction.get("enabled", True):
                continue
            if reaction_kind and reaction.get("reaction_kind") != reaction_kind:
                continue
            result.append(dict(reaction))
        return result

    def list_choices(self, guild_id: str, mention_reaction_id: int, enabled=None):
        choices = self.choices_by_reaction_id.get(mention_reaction_id, [])
        result = []
        for choice in choices:
            if enabled is True and not choice.get("enabled", True):
                continue
            result.append(dict(choice))
        return result


def reaction(
    reaction_id: int,
    keyword: str,
    *,
    name: Optional[str] = None,
    enabled: bool = True,
    match_type: str = "exact",
) -> Dict[str, Any]:
    return {
        "id": reaction_id,
        "reaction_key": "random_{0}".format(reaction_id),
        "keyword": keyword,
        "name": name or keyword,
        "match_type": match_type,
        "reaction_kind": "random_draw",
        "enabled": enabled,
        "created_at": datetime(2026, 1, reaction_id, tzinfo=timezone.utc),
    }


def choice(
    choice_id: int,
    body: str,
    *,
    weight: int = 1,
    enabled: bool = True,
    image_path: str = "",
    emoji: str = "",
) -> Dict[str, Any]:
    return {
        "id": choice_id,
        "name": "choice_{0}".format(choice_id),
        "body": body,
        "image_path": image_path,
        "emoji_internal": emoji,
        "appearance_rate": weight,
        "enabled": enabled,
    }


async def run_process(command_text: str) -> runtime_db.RuntimeAction:
    return await runtime_db.process_db_mention(FakeMessage(command_text), "111", FakeConnection())


async def run_checks() -> int:
    check = Check()
    old = {
        "mention_repo": runtime_db.MentionReactionRepository,
        "feature_enabled": runtime_db.feature_enabled,
        "mention_feature_enabled": runtime_db.mention_feature_enabled,
        "get_mention_command_text": runtime_db.get_mention_command_text,
        "list_limited_effects": runtime_db.list_limited_effects,
        "list_effects": runtime_db.list_effects,
        "execute_effects": runtime_db.execute_effects,
        "play_audio": runtime_db.play_configured_reaction_audio,
        "send_text_or_image": runtime_db.send_text_or_image,
        "randint": runtime_db.random.randint,
    }
    effect_log: List[int] = []
    send_log: List[Dict[str, str]] = []
    random_values: List[int] = []
    feature_flags = {
        runtime_db.FEATURE_MENTION_RANDOM_DRAW: True,
        runtime_db.FEATURE_MENTION_SEARCH: False,
        runtime_db.FEATURE_MENTION_LIMITED: False,
    }

    def next_randint(low: int, high: int) -> int:
        if random_values:
            return random_values.pop(0)
        return low

    async def fake_send_text_or_image(channel, text: str, image_path: str) -> bool:
        if getattr(channel, "fail_on_send", False):
            raise RuntimeError("send failed")
        send_log.append({"text": str(text or ""), "image_path": str(image_path or "")})
        await channel.send(text)
        return True

    async def fake_play_audio(*args, **kwargs) -> bool:
        return False

    async def fake_execute_effects(connection, guild_id, effects, message, values, pending_effects):
        for effect in effects:
            effect_log.append(int(effect["choice_id"]))
        return runtime_db.EffectExecutionResult(
            count_changed=bool(effects),
            repeat_count=0,
            pending_effects=[],
        )

    def fake_list_effects(connection, guild_id, target_type, target_id):
        if target_type == "mention_reaction_choice":
            return [{"id": target_id, "choice_id": target_id, "effect_type": "counter_increment"}]
        return []

    def set_repo(reactions: List[Dict[str, Any]], choices: Dict[int, List[Dict[str, Any]]]) -> None:
        FakeMentionReactionRepository.reactions = reactions
        FakeMentionReactionRepository.choices_by_reaction_id = choices
        effect_log.clear()
        send_log.clear()
        runtime_db._PENDING_NEXT_EFFECTS.clear()

    messages.configure(SimpleNamespace(user=SimpleNamespace(id=999)))
    runtime_db.MentionReactionRepository = FakeMentionReactionRepository
    runtime_db.feature_enabled = lambda connection, guild_id, feature_key: feature_flags.get(feature_key, True)
    runtime_db.mention_feature_enabled = lambda connection, guild_id, feature_key: feature_flags.get(feature_key, True)
    runtime_db.get_mention_command_text = lambda message: getattr(message, "command_text", "")
    runtime_db.list_limited_effects = lambda connection, guild_id, message: []
    runtime_db.list_effects = fake_list_effects
    runtime_db.execute_effects = fake_execute_effects
    runtime_db.play_configured_reaction_audio = fake_play_audio
    runtime_db.send_text_or_image = fake_send_text_or_image
    runtime_db.random.randint = next_randint

    try:
        parsed, error = runtime_db.parse_random_draw_pull_for_keyword("おみくじ", "おみくじ")
        check.add("no count defaults to one", parsed is not None and parsed.count == 1 and error is None)
        parsed, error = runtime_db.parse_random_draw_pull_for_keyword("おみくじ 10連", "おみくじ")
        check.add("space separated count parses", parsed is not None and parsed.count == 10 and error is None)
        parsed, error = runtime_db.parse_random_draw_pull_for_keyword("おみくじ10連", "おみくじ")
        check.add("attached count parses", parsed is not None and parsed.count == 10 and error is None)
        parsed, error = runtime_db.parse_random_draw_pull_for_keyword("おみくじ　１０連", "おみくじ")
        check.add("full width space and number parse", parsed is not None and parsed.count == 10 and error is None)
        for value in ("おみくじ 0連", "おみくじ -1連", "おみくじ 101連", "おみくじ abc連", "おみくじ 10回", "おみくじ 連"):
            parsed, error = runtime_db.parse_random_draw_pull_for_keyword(value, "おみくじ")
            check.add("invalid count rejects {0}".format(value), parsed is None and error == runtime_db.RANDOM_DRAW_PULL_INVALID_MESSAGE, str(error))
        for value in ("ペルソナ5 10連", "油粘土マン 5連", "5曲ループ", "5曲スキップ", "おみくじ10連した結果", "おみくじを10回"):
            parsed, error = runtime_db.parse_random_draw_pull_for_keyword(value, "おみくじ")
            check.add("non random command is not consumed {0}".format(value), parsed is None and error in (None, runtime_db.RANDOM_DRAW_PULL_BLOCKED), str(error))

        set_repo(
            [reaction(1, "おみくじ")],
            {
                1: [
                    choice(10, "大吉", weight=1, emoji="⭕"),
                    choice(11, "吉", weight=5),
                    choice(12, "凶", weight=2),
                    choice(13, "無効", weight=100, enabled=False),
                ]
            },
        )
        random_values[:] = [1, 2, 7]
        message = FakeMessage("おみくじ 3連")
        action = await runtime_db.process_db_mention(message, "111", FakeConnection())
        sent = "\n".join(message.channel.sent)
        check.add("three pulls are handled", action.handled and len(effect_log) == 3, "sent={0} effects={1}".format(message.channel.sent, effect_log))
        check.add("weighted draw uses replacement and preserves order", "1. 大吉" in sent and "2. 吉" in sent and "3. 凶" in sent, sent)
        check.add("disabled choices are excluded", "無効" not in sent and 13 not in effect_log, sent)

        random_values[:] = [1, 1]
        set_repo([reaction(1, "おみくじ")], {1: [choice(10, "大吉", weight=1)]})
        message = FakeMessage("おみくじ 2連")
        await runtime_db.process_db_mention(message, "111", FakeConnection())
        check.add("same result can appear repeatedly", "\n".join(message.channel.sent).count("大吉") == 2, str(message.channel.sent))

        set_repo([reaction(1, "おみくじ")], {1: [choice(10, "大吉\n今日はよい日", weight=1)]})
        message = FakeMessage("おみくじ")
        await runtime_db.process_db_mention(message, "111", FakeConnection())
        check.add("single draw keeps legacy format", message.channel.sent == ["大吉\n今日はよい日"], str(message.channel.sent))

        set_repo([reaction(1, "おみくじ")], {1: [choice(10, "画像結果", image_path="assets/result.png")]})
        message = FakeMessage("おみくじ 2連")
        await runtime_db.process_db_mention(message, "111", FakeConnection())
        check.add("image pulls use per-result send", len(send_log) == 2 and all(row["image_path"] for row in send_log), str(send_log))
        check.add("image pulls include per-result prefix", send_log[0]["text"].startswith("1/2") and send_log[1]["text"].startswith("2/2"), str(send_log))

        long_body = "長い結果" * 300
        set_repo([reaction(1, "おみくじ")], {1: [choice(10, long_body, weight=1)]})
        message = FakeMessage("おみくじ 8連")
        await runtime_db.process_db_mention(message, "111", FakeConnection())
        check.add("long text is split under discord safe limit", len(message.channel.sent) > 1 and all(len(row) <= runtime_db.DISCORD_SAFE_MESSAGE_LIMIT for row in message.channel.sent), [len(row) for row in message.channel.sent])

        set_repo([reaction(1, "名言")], {1: [choice(10, "名言結果", weight=1)]})
        message = FakeMessage("名言 5連")
        await runtime_db.process_db_mention(message, "111", FakeConnection())
        check.add("arbitrary db random draw keyword supports n-pull", "5連結果" in "\n".join(message.channel.sent) and len(effect_log) == 5, str(message.channel.sent))

        for invalid in ("おみくじ 0連", "おみくじ 101連", "おみくじ abc連", "おみくじ 10回", "おみくじ 連"):
            set_repo([reaction(1, "おみくじ")], {1: [choice(10, "大吉", weight=1)]})
            message = FakeMessage(invalid)
            action = await runtime_db.process_db_mention(message, "111", FakeConnection())
            check.add("invalid input sends only validation error {0}".format(invalid), action.handled and message.channel.sent == [runtime_db.RANDOM_DRAW_PULL_INVALID_MESSAGE] and effect_log == [], str(message.channel.sent))

        for blocked in ("ペルソナ5 10連", "油粘土マン 5連", "5曲ループ", "5曲スキップ", "おみくじ10連した結果", "おみくじを10回"):
            set_repo([reaction(1, "おみくじ")], {1: [choice(10, "大吉", weight=1)]})
            message = FakeMessage(blocked)
            action = await runtime_db.process_db_mention(message, "111", FakeConnection())
            check.add("non random command does not draw {0}".format(blocked), not action.handled and message.channel.sent == [] and effect_log == [], str(message.channel.sent))

        feature_flags[runtime_db.FEATURE_MENTION_RANDOM_DRAW] = False
        set_repo([reaction(1, "おみくじ")], {1: [choice(10, "大吉", weight=1)]})
        message = FakeMessage("おみくじ 2連")
        action = await runtime_db.process_db_mention(message, "111", FakeConnection())
        check.add("feature flag still gates n-pull", not action.handled and message.channel.sent == [], str(message.channel.sent))
        feature_flags[runtime_db.FEATURE_MENTION_RANDOM_DRAW] = True

        set_repo([reaction(1, "おみくじ", enabled=False)], {1: [choice(10, "大吉", weight=1)]})
        message = FakeMessage("おみくじ 2連")
        action = await runtime_db.process_db_mention(message, "111", FakeConnection())
        check.add("disabled random draw is not used", not action.handled and message.channel.sent == [], str(message.channel.sent))

        set_repo([reaction(1, "おみくじ")], {1: [choice(10, "大吉", weight=1)]})
        message = FakeMessage("おみくじ 2連", fail_on_send=True)
        try:
            await runtime_db.process_db_mention(message, "111", FakeConnection())
            failed_safely = False
        except RuntimeError:
            failed_safely = effect_log == []
        check.add("effects are not applied before send succeeds", failed_safely, str(effect_log))

        main_text = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        check.add(
            "legacy kuji path uses common n-pull parser",
            "parse_random_draw_pull_for_keyword(command_text, legacy_keyword)" in main_text
            and "for index in range(parsed.count)" in main_text,
        )
    finally:
        runtime_db.MentionReactionRepository = old["mention_repo"]
        runtime_db.feature_enabled = old["feature_enabled"]
        runtime_db.mention_feature_enabled = old["mention_feature_enabled"]
        runtime_db.get_mention_command_text = old["get_mention_command_text"]
        runtime_db.list_limited_effects = old["list_limited_effects"]
        runtime_db.list_effects = old["list_effects"]
        runtime_db.execute_effects = old["execute_effects"]
        runtime_db.play_configured_reaction_audio = old["play_audio"]
        runtime_db.send_text_or_image = old["send_text_or_image"]
        runtime_db.random.randint = old["randint"]
        runtime_db._PENDING_NEXT_EFFECTS.clear()
    return check.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_checks()))
