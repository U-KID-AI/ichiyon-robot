import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin import special_effects
from admin.ux import EFFECT_TYPE_LABELS
from bot.services import runtime_db
from bot.services.runtime_db import MatchResult, RandomDrawPullParse, execute_random_draw_reaction


MIGRATION_PATH = PROJECT_ROOT / "migrations" / "043_admin_permission_and_kuji_effects.sql"
SEED_PATH = PROJECT_ROOT / "scripts" / "seed_kuji_additional_effects.py"
SPECIAL_EFFECT_TEMPLATE_PATH = PROJECT_ROOT / "admin" / "templates" / "special_effect_form.html"


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


class FakeChannel:
    def __init__(self) -> None:
        self.sent: List[str] = []
        self.kwargs: List[Any] = []

    async def send(self, content=None, **kwargs):
        self.sent.append(str(content or ""))
        self.kwargs.append(kwargs)
        return SimpleNamespace(id=len(self.sent))


class FakeMessage:
    def __init__(self) -> None:
        self.channel = FakeChannel()
        self.author = SimpleNamespace(display_name="User", name="User", mention="<@123>", id=123, bot=False)
        self.guild = SimpleNamespace(id=456)
        self.content = "<@999> おみくじ"
        self.reactions: List[str] = []

    async def add_reaction(self, emoji) -> None:
        self.reactions.append(str(emoji))


def effect_config(result_behavior: str = "", numerator: int = 1, denominator: int = 1) -> List[dict]:
    config = {
        "target_user_id": "748965361486921831",
        "message": "テメェがやれ",
        "probability": {"numerator": numerator, "denominator": denominator},
    }
    if result_behavior:
        config["result_behavior"] = result_behavior
    return [
        {
            "id": 901,
            "effect_type": "probability_user_message",
            "effect_config_json": config,
            "additional_text": "",
            "additional_post_timing": "none",
            "target_type": "mention_reaction_choice",
        }
    ]


async def run_draw_with_effects(effects: List[dict]) -> FakeMessage:
    async def fake_play_configured_reaction_audio(*args, **kwargs) -> bool:
        return False

    original_list_effects = runtime_db.list_effects
    original_audio = runtime_db.play_configured_reaction_audio
    runtime_db.list_effects = lambda connection, guild_id, target_type, target_id: effects
    runtime_db.play_configured_reaction_audio = fake_play_configured_reaction_audio
    try:
        message = FakeMessage()
        await execute_random_draw_reaction(
            None,
            "456",
            message,
            MatchResult(row={"id": 1, "name": "おみくじ", "reaction_key": "omikuji"}, groups={}),
            RandomDrawPullParse(1, "おみくじ"),
            [{"id": 101, "body": "通常くじ結果", "result_label": "", "appearance_rate": 1, "enabled": True}],
            [],
            [],
        )
        return message
    finally:
        runtime_db.list_effects = original_list_effects
        runtime_db.play_configured_reaction_audio = original_audio


def main() -> int:
    results: List[Tuple[str, bool, Any]] = []
    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    seed = SEED_PATH.read_text(encoding="utf-8")
    template = SPECIAL_EFFECT_TEMPLATE_PATH.read_text(encoding="utf-8")
    runtime_source = (PROJECT_ROOT / "bot" / "services" / "runtime_db.py").read_text(encoding="utf-8")

    record(
        results,
        "effect type is available in admin form",
        "probability_user_message" in special_effects.EFFECT_TYPES
        and EFFECT_TYPE_LABELS.get("probability_user_message") == "確率で指定ユーザーへ投稿",
        EFFECT_TYPE_LABELS.get("probability_user_message"),
    )
    record(
        results,
        "effect form exposes fixed user message fields",
        'name="target_user_id"' in template
        and 'name="target_user_message"' in template
        and 'name="target_user_probability_numerator"' in template
        and 'name="target_user_probability_denominator"' in template
        and 'name="target_user_result_behavior"' in template,
        "target user message fields",
    )
    form, errors = special_effects.build_form(
        "くじ追加メンション",
        "",
        "#F59E0B",
        "on",
        None,
        "0",
        "mention_reaction_choice",
        "choice_selected",
        "probability_user_message",
        "{}",
        "",
        "none",
        "permanent",
        "",
        "0",
        "none",
        "",
        target_user_id="748965361486921831",
        target_user_message="テメェがやれ",
        target_user_probability_numerator="1",
        target_user_probability_denominator="10",
        target_user_result_behavior="replace",
    )
    record(
        results,
        "admin form builds 1/10 fixed user message config",
        not errors
        and form["effect_config"] == {
            "message": "テメェがやれ",
            "probability": {"denominator": 10, "numerator": 1},
            "result_behavior": "replace",
            "target_user_id": "748965361486921831",
        },
        "{0} errors={1}".format(form.get("effect_config"), errors),
    )
    restored = special_effects.build_form_from_tag({"effect_type": "probability_user_message", "effect_config_json": form["effect_config"]})
    record(
        results,
        "admin form restores append/replace behavior",
        restored["target_user_result_behavior"] == "replace"
        and restored["target_user_result_behavior_label"] == "置換",
        restored,
    )
    record(
        results,
        "runtime sends probability_user_message with allowed user mention",
        "async def send_probability_user_message" in runtime_source
        and "discord.AllowedMentions(users=True, roles=False, everyone=False, replied_user=False)" in runtime_source
        and 'elif effect_type == "probability_user_message"' in runtime_source,
        "runtime effect branch",
    )
    replace_hit = asyncio.run(run_draw_with_effects(effect_config("replace", 1, 1)))
    record(
        results,
        "replace hit sends only fixed user message",
        replace_hit.channel.sent == ["<@748965361486921831> テメェがやれ"],
        replace_hit.channel.sent,
    )
    replace_miss = asyncio.run(run_draw_with_effects(effect_config("replace", 0, 1)))
    record(
        results,
        "replace miss sends normal kuji result",
        replace_miss.channel.sent == ["通常くじ結果"],
        replace_miss.channel.sent,
    )
    append_hit = asyncio.run(run_draw_with_effects(effect_config("append", 1, 1)))
    record(
        results,
        "append hit sends normal result and fixed user message",
        append_hit.channel.sent == ["通常くじ結果", "<@748965361486921831> テメェがやれ"],
        append_hit.channel.sent,
    )
    legacy_append = asyncio.run(run_draw_with_effects(effect_config("", 1, 1)))
    record(
        results,
        "missing result_behavior keeps append compatibility",
        legacy_append.channel.sent == ["通常くじ結果", "<@748965361486921831> テメェがやれ"],
        legacy_append.channel.sent,
    )
    record(
        results,
        "migration only extends check constraint and preserves data",
        "probability_user_message" in migration
        and "DROP TABLE" not in migration.upper()
        and "TRUNCATE" not in migration.upper()
        and "DELETE FROM" not in migration.upper(),
        "migration 043",
    )
    record(
        results,
        "seed targets active ichiyon guild kuji choices idempotently",
        "list_active_guild_ids" in seed
        and "ON CONFLICT (bot_id, guild_id, name) DO UPDATE" in seed
        and "ON CONFLICT (bot_id, special_effect_tag_id, target_type, target_id) DO UPDATE" in seed
        and '"result_behavior": "replace"' in seed
        and "mention_reaction_choice" in seed
        and "おみくじ" in seed
        and "くじ" in seed,
        "seed assignment",
    )

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
