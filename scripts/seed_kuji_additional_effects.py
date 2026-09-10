import argparse
import json
import sys
from pathlib import Path
from typing import List


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.db import get_connection


DEFAULT_BOT_ID = "ichiyon"
DEFAULT_TARGET_USER_ID = "748965361486921831"
DEFAULT_MESSAGE = "テメェがやれ"
DEFAULT_TAG_NAME = "くじ追加メンション: テメェがやれ"
DEFAULT_PROBABILITY_NUMERATOR = 1
DEFAULT_PROBABILITY_DENOMINATOR = 10
KUJI_REACTION_KEYS = ("omikuji", "kuji")
KUJI_KEYWORDS = ("おみくじ", "くじ")


def json_dumps(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def list_active_guild_ids(connection, bot_id: str) -> List[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT bg.guild_id
            FROM bot_guilds bg
            JOIN guilds g ON g.guild_id = bg.guild_id
            WHERE bg.bot_id = %s
              AND bg.enabled = TRUE
              AND COALESCE(g.enabled, TRUE) = TRUE
            ORDER BY bg.guild_id ASC
            """,
            (bot_id,),
        )
        return [str(row[0]) for row in cursor.fetchall()]


def upsert_effect_tag(connection, bot_id: str, guild_id: str, target_user_id: str, message: str) -> int:
    config = {
        "target_user_id": target_user_id,
        "message": message,
        "probability": {
            "numerator": DEFAULT_PROBABILITY_NUMERATOR,
            "denominator": DEFAULT_PROBABILITY_DENOMINATOR,
        },
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO special_effect_tags (
                bot_id,
                guild_id,
                name,
                description,
                color,
                admin_only,
                enabled,
                is_deletable,
                priority,
                target_type,
                trigger_timing,
                effect_type,
                effect_config_json,
                additional_text,
                additional_post_timing,
                expires_type,
                cooldown_seconds,
                cooldown_scope
            )
            VALUES (%s, %s, %s, %s, %s, FALSE, TRUE, TRUE, 0, %s, %s, %s, %s::JSONB, '', 'none', 'permanent', 0, 'none')
            ON CONFLICT (bot_id, guild_id, name) DO UPDATE
            SET description = EXCLUDED.description,
                color = EXCLUDED.color,
                enabled = TRUE,
                is_deletable = TRUE,
                target_type = EXCLUDED.target_type,
                trigger_timing = EXCLUDED.trigger_timing,
                effect_type = EXCLUDED.effect_type,
                effect_config_json = EXCLUDED.effect_config_json,
                additional_text = '',
                additional_post_timing = 'none',
                expires_type = 'permanent',
                cooldown_seconds = 0,
                cooldown_scope = 'none',
                updated_at = NOW()
            RETURNING id
            """,
            (
                bot_id,
                guild_id,
                DEFAULT_TAG_NAME,
                "くじを引いた時に指定確率で固定ユーザーへメンション投稿する汎用特殊効果。",
                "#F59E0B",
                "mention_reaction_choice",
                "choice_selected",
                "probability_user_message",
                json_dumps(config),
            ),
        )
        return int(cursor.fetchone()[0])


def list_kuji_choice_ids(connection, bot_id: str, guild_id: str) -> List[int]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.id
            FROM mention_reactions r
            JOIN mention_reaction_choices c
              ON c.mention_reaction_id = r.id
             AND c.bot_id = r.bot_id
             AND c.guild_id = r.guild_id
            WHERE r.bot_id = %s
              AND r.guild_id = %s
              AND r.enabled = TRUE
              AND c.enabled = TRUE
              AND r.reaction_kind IN ('random', 'random_draw')
              AND (
                  r.reaction_key = ANY(%s)
                  OR r.keyword = ANY(%s)
                  OR r.name = ANY(%s)
              )
            ORDER BY r.id ASC, c.id ASC
            """,
            (bot_id, guild_id, list(KUJI_REACTION_KEYS), list(KUJI_KEYWORDS), list(KUJI_KEYWORDS)),
        )
        return [int(row[0]) for row in cursor.fetchall()]


def assign_tag_to_choice(connection, bot_id: str, guild_id: str, tag_id: int, choice_id: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO special_effect_assignments (
                bot_id,
                guild_id,
                special_effect_tag_id,
                target_type,
                target_id,
                enabled
            )
            VALUES (%s, %s, %s, 'mention_reaction_choice', %s, TRUE)
            ON CONFLICT (bot_id, special_effect_tag_id, target_type, target_id) DO UPDATE
            SET enabled = TRUE,
                updated_at = NOW()
            """,
            (bot_id, guild_id, tag_id, choice_id),
        )


def seed(bot_id: str, target_user_id: str, message: str) -> int:
    total_assignments = 0
    with get_connection() as connection:
        for guild_id in list_active_guild_ids(connection, bot_id):
            tag_id = upsert_effect_tag(connection, bot_id, guild_id, target_user_id, message)
            choice_ids = list_kuji_choice_ids(connection, bot_id, guild_id)
            for choice_id in choice_ids:
                assign_tag_to_choice(connection, bot_id, guild_id, tag_id, choice_id)
            total_assignments += len(choice_ids)
            print("guild_id={0} tag_id={1} assigned_choices={2}".format(guild_id, tag_id, len(choice_ids)))
        connection.commit()
    return total_assignments


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed generic additional effects for kuji random draws.")
    parser.add_argument("--bot-id", default=DEFAULT_BOT_ID)
    parser.add_argument("--target-user-id", default=DEFAULT_TARGET_USER_ID)
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    args = parser.parse_args()

    total = seed(args.bot_id.strip(), args.target_user_id.strip(), args.message.strip())
    print("seed_kuji_additional_effects completed assigned_choices={0}".format(total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
