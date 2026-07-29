import argparse
from decimal import Decimal
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Optional

from bot.db import get_connection
from bot.repositories import AutoReactionRepository, RandomReactionRepository, SpecialEffectRepository


TRIGGER_TEXT = r".+"
MATCH_TYPE = "regex"
REACTION_PRIORITY = -100
TAG_NAME = "ランダム絵文字リアクション（特殊効果）"
REACTION_RESPONSE_TEXT = None
REACTION_IMAGE_PATH = None
REACTION_EMOJI = None


def probability_percent_to_fraction(value: object) -> Dict[str, int]:
    if isinstance(value, Decimal):
        source = Fraction(value)
    else:
        source = Fraction(str(value or "0"))
    probability = source / 100
    probability = probability.limit_denominator(1000000)
    return {"numerator": probability.numerator, "denominator": probability.denominator}


def list_enabled_random_reaction_settings(connection, bot_id: Optional[str], guild_id: Optional[str]) -> List[Dict[str, Any]]:
    params: List[Any] = []
    where = ["enabled = TRUE"]
    if bot_id:
        where.append("bot_id = %s")
        params.append(bot_id)
    if guild_id:
        where.append("guild_id = %s")
        params.append(guild_id)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM random_reaction_settings
            WHERE {where}
            ORDER BY bot_id, guild_id
            """.format(where=" AND ".join(where)),
            params,
        )
        columns = [column.name for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def find_existing_reaction(repository: AutoReactionRepository, guild_id: str) -> Optional[Dict[str, Any]]:
    for row in repository.list_reactions(guild_id, enabled=None):
        if row.get("trigger_text") == TRIGGER_TEXT and row.get("match_type") == MATCH_TYPE:
            return row
    return None


def find_existing_tag(repository: SpecialEffectRepository, guild_id: str) -> Optional[Dict[str, Any]]:
    for row in repository.list_tags(guild_id, query=TAG_NAME, target_type="auto_reaction", enabled=None):
        if row.get("name") == TAG_NAME:
            return row
    return None


def ensure_special_effect_path(connection, setting: Dict[str, Any], apply: bool) -> Dict[str, Any]:
    bot_id = str(setting["bot_id"])
    guild_id = str(setting["guild_id"])
    emoji = str(setting.get("emoji") or "").strip()
    if not emoji:
        return {"status": "skipped", "reason": "missing emoji", "bot_id": bot_id, "guild_id": guild_id}
    if str(setting.get("target_channel_ids") or "").strip() or str(setting.get("excluded_channel_ids") or "").strip():
        return {"status": "skipped", "reason": "channel filters are not yet supported by auto-reaction trigger", "bot_id": bot_id, "guild_id": guild_id}

    probability = probability_percent_to_fraction(setting.get("probability_percent"))
    cooldown_seconds = int(setting.get("cooldown_seconds") or 0)
    reaction_repository = AutoReactionRepository(connection, bot_id=bot_id)
    effect_repository = SpecialEffectRepository(connection, bot_id=bot_id)

    existing_reaction = find_existing_reaction(reaction_repository, guild_id)
    existing_tag = find_existing_tag(effect_repository, guild_id)
    if not apply:
        return {
            "status": "dry-run",
            "bot_id": bot_id,
            "guild_id": guild_id,
            "emoji": emoji,
            "probability": probability,
            "cooldown_seconds": cooldown_seconds,
            "reaction_exists": existing_reaction is not None,
            "tag_exists": existing_tag is not None,
        }

    if existing_reaction is None:
        reaction = reaction_repository.create_reaction(
            guild_id,
            TRIGGER_TEXT,
            REACTION_RESPONSE_TEXT,
            REACTION_IMAGE_PATH,
            REACTION_EMOJI,
            MATCH_TYPE,
            REACTION_PRIORITY,
            True,
        )
    else:
        reaction = reaction_repository.update_reaction(
            guild_id,
            int(existing_reaction["id"]),
            TRIGGER_TEXT,
            REACTION_RESPONSE_TEXT,
            REACTION_IMAGE_PATH,
            REACTION_EMOJI,
            MATCH_TYPE,
            REACTION_PRIORITY,
            True,
        )

    effect_config = {"emoji": emoji, "probability": probability, "target": "source_message"}
    if existing_tag is None:
        tag = effect_repository.create_tag(
            guild_id,
            TAG_NAME,
            "旧ランダム絵文字リアクションを特殊効果として表現するタグ。",
            "#F59E0B",
            False,
            True,
            REACTION_PRIORITY,
            "auto_reaction",
            "auto_reaction_triggered",
            "reaction",
            effect_config,
            "",
            "none",
            "permanent",
            None,
            cooldown_seconds,
            "guild" if cooldown_seconds > 0 else "none",
        )
    else:
        tag = effect_repository.update_tag(
            guild_id,
            int(existing_tag["id"]),
            TAG_NAME,
            "旧ランダム絵文字リアクションを特殊効果として表現するタグ。",
            "#F59E0B",
            False,
            True,
            REACTION_PRIORITY,
            "auto_reaction",
            "auto_reaction_triggered",
            "reaction",
            effect_config,
            "",
            "none",
            "permanent",
            None,
            cooldown_seconds,
            "guild" if cooldown_seconds > 0 else "none",
        )
    effect_repository.assign_tag(guild_id, int(tag["id"]), "auto_reaction", int(reaction["id"]))
    RandomReactionRepository(connection, bot_id=bot_id).set_enabled(guild_id, False, "migration:special_effect")
    return {"status": "updated", "bot_id": bot_id, "guild_id": guild_id, "reaction_id": reaction["id"], "tag_id": tag["id"]}


def migrate(bot_id: Optional[str], guild_id: Optional[str], apply: bool) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        settings = list_enabled_random_reaction_settings(connection, bot_id, guild_id)
        results = [ensure_special_effect_path(connection, setting, apply) for setting in settings]
        if apply:
            connection.commit()
        else:
            connection.rollback()
        return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate random emoji reaction settings to generic special effects.")
    parser.add_argument("--bot-id", default=None)
    parser.add_argument("--guild-id", default=None)
    parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry-run.")
    args = parser.parse_args()
    results = migrate(args.bot_id, args.guild_id, args.apply)
    for result in results:
        print(result)
    print("random reaction migration candidates: {0}".format(len(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
