import asyncio
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin import mention_reactions, persona_draws
from bot import messages
from bot.services import runtime_db


MIGRATION_PATH = PROJECT_ROOT / "migrations" / "045_migrate_persona_draws_to_random_draw.sql"
SEED_PATH = PROJECT_ROOT / "scripts" / "seed_joker_random_draw.py"
TEMPLATE_PATH = PROJECT_ROOT / "admin" / "templates" / "mention_reaction_form.html"
ADMIN_ROUTE_PATH = PROJECT_ROOT / "admin" / "mention_reactions.py"
MAIN_PATH = PROJECT_ROOT / "main.py"


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


class FakeChannel:
    def __init__(self) -> None:
        self.sent: List[str] = []

    async def send(self, content=None, **kwargs):
        self.sent.append(str(content or ""))
        return SimpleNamespace(id=len(self.sent))


class FakeUser:
    id = 999
    bot = True

    def __eq__(self, other) -> bool:
        return str(getattr(other, "id", "")) == str(self.id)


class FakeMessage:
    def __init__(self, content: str, mention: bool = False, author_bot: bool = False, guild_id: str = "guild-a") -> None:
        bot_user = FakeUser()
        messages._bot = SimpleNamespace(user=bot_user)
        self.content = "<@999> {0}".format(content) if mention else content
        self.mentions = [bot_user] if mention else []
        self.guild = SimpleNamespace(id=guild_id)
        self.author = SimpleNamespace(id=1234, bot=author_bot, display_name="user", mention="<@1234>")
        self.channel = FakeChannel()


class FakeConnection:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeFeatureFlagRepository:
    enabled = True

    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.bot_id = bot_id

    def is_enabled(self, guild_id: str, feature_key: str, default: bool = False) -> bool:
        return self.enabled


class FakeMentionReactionRepository:
    reactions_by_guild: Dict[str, List[Dict[str, Any]]] = {}
    choices_by_reaction: Dict[int, List[Dict[str, Any]]] = {}

    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.bot_id = bot_id or "ichiyon"

    def list_reactions(self, guild_id: str, enabled: Optional[bool] = None, reaction_kind: Optional[str] = None, include_system: bool = True):
        rows = list(self.reactions_by_guild.get(guild_id, []))
        if enabled is not None:
            rows = [row for row in rows if bool(row.get("enabled")) is enabled]
        if reaction_kind == "random_draw":
            rows = [row for row in rows if row.get("reaction_kind") in ("random", "random_draw")]
        elif reaction_kind is not None:
            rows = [row for row in rows if row.get("reaction_kind") == reaction_kind]
        return rows

    def list_choices(self, guild_id: str, mention_reaction_id: int, enabled: Optional[bool] = None):
        rows = list(self.choices_by_reaction.get(int(mention_reaction_id), []))
        if enabled is not None:
            rows = [row for row in rows if bool(row.get("enabled")) is enabled]
        return rows


def joker_reaction(**overrides) -> Dict[str, Any]:
    config_json = {
        "allow_standalone_trigger": True,
        "allow_mention_trigger": True,
        "consume_mention": True,
        "reroll_enabled": True,
        "reroll_probability_percent": 10,
        "max_rerolls": 1,
        "reroll_lines": ["違うな…", "これじゃない…"],
    }
    config_json.update(overrides.pop("config_json", {}))
    row = {
        "id": 1,
        "reaction_key": "joker_persona_random_draw",
        "keyword": "正体を見せろ！",
        "match_type": "exact",
        "reaction_kind": "random",
        "name": "ジョーカー",
        "enabled": True,
        "config_json": config_json,
        "created_at": None,
    }
    row.update(overrides)
    return row


def choices(count: int = 2) -> List[Dict[str, Any]]:
    bodies = [
        "呪文を詠唱するヒヒ！",
        "池袋の犯人！",
        "セクハラゲイ野郎！",
        "児童ポルノの妖怪！",
        "イェール大学の准教授！",
    ]
    return [
        {
            "id": index + 1,
            "name": body,
            "body": body,
            "image_path": "",
            "appearance_rate": 1,
            "enabled": True,
            "result_label": "",
            "emoji_internal": "",
        }
        for index, body in enumerate(bodies[:count])
    ]


async def run_runtime(content: str, mention: bool = False, guild_id: str = "guild-a") -> Tuple[bool, List[str]]:
    message = FakeMessage(content, mention=mention, guild_id=guild_id)
    handled = await runtime_db.handle_db_runtime_message_locked(message, guild_id)
    return handled, message.channel.sent


def install_runtime_fakes():
    originals = {
        "MentionReactionRepository": runtime_db.MentionReactionRepository,
        "FeatureFlagRepository": runtime_db.FeatureFlagRepository,
        "get_connection": runtime_db.get_connection,
        "list_limited_effects": runtime_db.list_limited_effects,
        "apply_mention_suffix_guards": runtime_db.apply_mention_suffix_guards,
        "apply_consuming_mention_effects": runtime_db.apply_consuming_mention_effects,
        "list_effects": runtime_db.list_effects,
        "enter_mode_if_needed": runtime_db.enter_mode_if_needed,
        "expire_mode_if_needed": runtime_db.expire_mode_if_needed,
        "handle_active_mode": runtime_db.handle_active_mode,
        "find_ng_word_match": runtime_db.find_ng_word_match,
        "process_db_auto_reaction": runtime_db.process_db_auto_reaction,
    }
    runtime_db.MentionReactionRepository = FakeMentionReactionRepository
    runtime_db.FeatureFlagRepository = FakeFeatureFlagRepository
    runtime_db.get_connection = lambda: FakeConnection()
    runtime_db.list_limited_effects = lambda connection, guild_id, message: []
    runtime_db.apply_mention_suffix_guards = async_none
    runtime_db.apply_consuming_mention_effects = async_none
    runtime_db.list_effects = lambda connection, guild_id, target_type, target_id: []
    runtime_db.enter_mode_if_needed = async_false
    runtime_db.expire_mode_if_needed = async_false
    runtime_db.handle_active_mode = async_false
    runtime_db.find_ng_word_match = lambda connection, guild_id, content: None
    runtime_db.process_db_auto_reaction = async_runtime_false
    return originals


def restore_runtime_fakes(originals) -> None:
    for key, value in originals.items():
        setattr(runtime_db, key, value)


async def async_none(*args, **kwargs):
    return None


async def async_false(*args, **kwargs):
    return False


async def async_runtime_false(*args, **kwargs):
    return runtime_db.RuntimeAction(False)


async def runtime_checks(results: List[Tuple[str, bool, Any]]) -> None:
    originals = install_runtime_fakes()
    original_random = runtime_db.random
    try:
        FakeFeatureFlagRepository.enabled = True
        FakeMentionReactionRepository.reactions_by_guild = {"guild-a": [joker_reaction()]}
        FakeMentionReactionRepository.choices_by_reaction = {1: choices(2)}

        runtime_db.random = random.Random(3)
        handled, sent = await run_runtime("正体を見せろ！", mention=False)
        record(results, "plain Joker trigger is handled by generic random draw", handled and len(sent) == 1 and sent[0] in {item["body"] for item in choices(2)}, sent)

        runtime_db.random = random.Random(4)
        handled, sent = await run_runtime("正体を見せろ！", mention=True)
        record(results, "mention Joker trigger is consumed by generic random draw", handled and len(sent) == 1, sent)

        runtime_db.random = random.Random(5)
        FakeMentionReactionRepository.reactions_by_guild = {"guild-a": [joker_reaction(config_json={"reroll_probability_percent": 0})]}
        handled, sent = await run_runtime("正体を見せろ！", mention=False)
        record(results, "reroll zero percent sends one result", handled and len(sent) == 1, sent)

        runtime_db.random = random.Random(1)
        FakeMentionReactionRepository.reactions_by_guild = {"guild-a": [joker_reaction(config_json={"reroll_probability_percent": 100})]}
        handled, sent = await run_runtime("正体を見せろ！", mention=False)
        record(results, "reroll one hundred percent sends result line result", handled and len(sent) == 3 and sent[0] != sent[2] and sent[1] in {"違うな…", "これじゃない…"}, sent)

        FakeMentionReactionRepository.choices_by_reaction = {1: choices(1)}
        runtime_db.random = random.Random(2)
        handled, sent = await run_runtime("正体を見せろ！", mention=False)
        record(results, "single candidate reroll is safe", handled and len(sent) == 3 and sent[0] == sent[2], sent)

        FakeMentionReactionRepository.choices_by_reaction = {1: []}
        handled, sent = await run_runtime("正体を見せろ！", mention=True)
        record(results, "zero candidates with consume is safe", handled and sent == [], sent)

        FakeMentionReactionRepository.choices_by_reaction = {1: choices(1)}
        FakeMentionReactionRepository.reactions_by_guild = {
            "guild-a": [joker_reaction(id=1)],
            "guild-b": [joker_reaction(id=2, keyword="別トリガー")],
        }
        handled_a, sent_a = await run_runtime("正体を見せろ！", guild_id="guild-a")
        handled_b, sent_b = await run_runtime("正体を見せろ！", guild_id="guild-b")
        record(results, "bot and guild scope stay separated", handled_a and sent_a and not handled_b and sent_b == [], {"a": sent_a, "b": sent_b})

        legacy = joker_reaction(config_json={})
        legacy["config_json"] = {}
        record(
            results,
            "legacy random draw defaults keep mention on and standalone off",
            runtime_db.random_draw_allows_mention_trigger(legacy) is True
            and runtime_db.random_draw_allows_standalone_trigger(legacy) is False
            and runtime_db.random_draw_consumes_mention(legacy) is False
            and runtime_db.random_draw_reroll_enabled(legacy) is False,
            legacy["config_json"],
        )
    finally:
        runtime_db.random = original_random
        restore_runtime_fakes(originals)


def main() -> int:
    results: List[Tuple[str, bool, Any]] = []
    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    seed = SEED_PATH.read_text(encoding="utf-8")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    admin_route = ADMIN_ROUTE_PATH.read_text(encoding="utf-8")
    main_source = MAIN_PATH.read_text(encoding="utf-8")
    persona_admin = (PROJECT_ROOT / "admin" / "persona_draws.py").read_text(encoding="utf-8")

    form = mention_reactions.build_reaction_form(
        "ジョーカー",
        "",
        "正体を見せろ！",
        "exact",
        "on",
        None,
        "on",
        "on",
        "on",
        "on",
        "10",
        "1",
        "違うな…\nこれじゃない…",
    )
    record(
        results,
        "random draw form builds Joker advanced config",
        form["config_json"]["allow_standalone_trigger"] is True
        and form["config_json"]["allow_mention_trigger"] is True
        and form["config_json"]["consume_mention"] is True
        and form["config_json"]["reroll_enabled"] is True
        and form["config_json"]["reroll_probability_percent"] == 10
        and form["config_json"]["max_rerolls"] == 1
        and form["config_json"]["reroll_lines"] == ["違うな…", "これじゃない…"],
        form["config_json"],
    )
    restored = mention_reactions.build_reaction_view({"reaction_kind": "random", "match_type": "exact", "config_json": form["config_json"]})
    record(
        results,
        "random draw form restores advanced config",
        restored["reroll_lines_text"] == "違うな…\nこれじゃない…" and restored["consume_mention"] is True,
        restored,
    )
    record(
        results,
        "admin template exposes generic advanced fields",
        "高度な抽選設定" in template
        and "通常メッセージでも使用" in template
        and "メンションでも使用" in template
        and "メンション成立時は通常返信へ流さない" in template
        and "自動引き直し" in template,
        "mention_reaction_form.html",
    )
    record(
        results,
        "admin route preserves random draw config on save",
        "merge_random_draw_config(reaction.get(\"config_json\"), form[\"config_json\"])" in admin_route,
        "merge_random_draw_config",
    )
    record(
        results,
        "legacy persona admin redirects to random draw",
        "RedirectResponse" in persona_admin
        and "mention-reactions?kind=random_draw" in persona_admin
        and "PersonaDrawRepository" not in persona_admin,
        "admin/persona_draws.py",
    )
    record(
        results,
        "persona menu is removed from feature list",
        "persona_draws" not in (PROJECT_ROOT / "admin" / "servers.py").read_text(encoding="utf-8"),
        "admin/servers.py",
    )
    record(
        results,
        "main no longer calls persona dedicated runtime",
        "handle_persona_draw_message" not in main_source,
        "main.py",
    )
    record(
        results,
        "migration keeps old persona tables and copies to random draw",
        "DROP TABLE" not in migration.upper()
        and "DELETE FROM" not in migration.upper()
        and "TRUNCATE" not in migration.upper()
        and "persona_draws" in migration
        and "mention_reactions" in migration
        and "mention_reaction_choices" in migration
        and "migrated_from" in migration,
        "migration 045",
    )
    record(
        results,
        "migration is idempotent for choices",
        "NOT EXISTS" in migration and "reaction_key = 'persona_draw_'" in migration,
        "migration 045",
    )
    record(
        results,
        "seed writes generic random draw",
        "MentionReactionRepository" in seed
        and "DEFAULT_NAME = \"ジョーカー\"" in seed
        and "allow_standalone_trigger" in seed
        and "consume_mention" in seed
        and "replace_choices" in seed,
        "seed_joker_random_draw.py",
    )
    record(
        results,
        "old seed delegates to generic seed",
        "seed_joker_random_draw" in (PROJECT_ROOT / "scripts" / "seed_joker_persona_draw.py").read_text(encoding="utf-8"),
        "seed_joker_persona_draw.py",
    )
    asyncio.run(runtime_checks(results))

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
