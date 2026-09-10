import sys
from pathlib import Path
from typing import Any, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin import special_effects
from admin.ux import EFFECT_TYPE_LABELS
from bot.services import runtime_db


MIGRATION_PATH = PROJECT_ROOT / "migrations" / "043_admin_permission_and_kuji_effects.sql"
SEED_PATH = PROJECT_ROOT / "scripts" / "seed_kuji_additional_effects.py"
SPECIAL_EFFECT_TEMPLATE_PATH = PROJECT_ROOT / "admin" / "templates" / "special_effect_form.html"


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


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
        and 'name="target_user_probability_denominator"' in template,
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
    )
    record(
        results,
        "admin form builds 1/10 fixed user message config",
        not errors
        and form["effect_config"] == {
            "message": "テメェがやれ",
            "probability": {"denominator": 10, "numerator": 1},
            "target_user_id": "748965361486921831",
        },
        "{0} errors={1}".format(form.get("effect_config"), errors),
    )
    record(
        results,
        "runtime sends probability_user_message with allowed user mention",
        "async def send_probability_user_message" in runtime_source
        and "discord.AllowedMentions(users=True, roles=False, everyone=False, replied_user=False)" in runtime_source
        and 'elif effect_type == "probability_user_message"' in runtime_source,
        "runtime effect branch",
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
