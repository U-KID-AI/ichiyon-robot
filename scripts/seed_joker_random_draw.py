import argparse
import sys
from pathlib import Path
from typing import List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot import config
from bot.db import get_connection
from bot.repositories import MentionReactionRepository


DEFAULT_NAME = "ジョーカー"
DEFAULT_TRIGGER = "正体を見せろ！"
DEFAULT_CHOICES = [
    "呪文を詠唱するヒヒ！",
    "池袋の犯人！",
    "セクハラゲイ野郎！",
    "児童ポルノの妖怪！",
    "イェール大学の准教授！",
]
DEFAULT_REROLL_LINES = [
    "違うな…",
    "これじゃない…",
]
DEFAULT_REROLL_PROBABILITY_PERCENT = 10
DEFAULT_MAX_REROLLS = 1


def table_has_column(connection, table_name: str, column_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                  AND column_name = %s
            )
            """,
            (table_name, column_name),
        )
        row = cursor.fetchone()
        return bool(row and row[0])


def list_active_guild_ids(connection, bot_id: str) -> List[str]:
    has_bot_id = table_has_column(connection, "guilds", "bot_id")
    has_enabled = table_has_column(connection, "guilds", "enabled")
    where = []
    params = []
    if has_bot_id:
        where.append("bot_id = %s")
        params.append(bot_id)
    if has_enabled:
        where.append("enabled = TRUE")
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT guild_id
            FROM guilds
            {where_sql}
            ORDER BY guild_id
            """.format(where_sql=where_sql),
            tuple(params),
        )
        return [str(row[0]) for row in cursor.fetchall()]


def find_random_draw_by_keyword(connection, bot_id: str, guild_id: str, keyword: str) -> Optional[dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM mention_reactions
            WHERE bot_id = %s
              AND guild_id = %s
              AND keyword = %s
              AND reaction_kind IN ('random', 'random_draw')
            ORDER BY id ASC
            LIMIT 1
            """,
            (bot_id, guild_id, keyword),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        columns = [column.name for column in cursor.description]
        return dict(zip(columns, row))


def replace_choices(connection, bot_id: str, guild_id: str, reaction_id: int, choices: List[str]) -> None:
    with connection.cursor() as cursor:
        for index, body in enumerate(choices):
            cursor.execute(
                """
                SELECT id
                FROM mention_reaction_choices
                WHERE bot_id = %s
                  AND guild_id = %s
                  AND mention_reaction_id = %s
                  AND body = %s
                LIMIT 1
                """,
                (bot_id, guild_id, reaction_id, body),
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    """
                    UPDATE mention_reaction_choices
                    SET name = %s,
                        appearance_rate = 1,
                        enabled = TRUE,
                        sort_order = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (body, index, existing[0]),
                )
                continue
            cursor.execute(
                """
                INSERT INTO mention_reaction_choices (
                    bot_id,
                    guild_id,
                    mention_reaction_id,
                    name,
                    body,
                    image_path,
                    appearance_rate,
                    enabled,
                    result_label,
                    emoji_internal,
                    sort_order
                )
                VALUES (%s, %s, %s, %s, %s, '', 1, TRUE, '', '', %s)
                """,
                (bot_id, guild_id, reaction_id, body, body, index),
            )


def seed_guild(connection, bot_id: str, guild_id: str) -> int:
    repo = MentionReactionRepository(connection, bot_id=bot_id)
    config_json = {
        "allow_standalone_trigger": False,
        "allow_mention_trigger": True,
        "consume_mention": True,
        "reroll_enabled": True,
        "reroll_probability_percent": DEFAULT_REROLL_PROBABILITY_PERCENT,
        "max_rerolls": DEFAULT_MAX_REROLLS,
        "reroll_lines": DEFAULT_REROLL_LINES,
    }
    existing = repo.get_by_key(guild_id, "joker_persona_random_draw") or find_random_draw_by_keyword(
        connection,
        bot_id,
        guild_id,
        DEFAULT_TRIGGER,
    )
    if existing is None:
        reaction = repo.create_reaction(
            guild_id,
            "joker_persona_random_draw",
            DEFAULT_TRIGGER,
            "exact",
            "random_draw",
            DEFAULT_NAME,
            "固定フレーズからジョーカー候補を抽選します。",
            False,
            False,
            True,
            True,
            config_json,
        )
    else:
        reaction = repo.update_reaction(
            guild_id,
            int(existing["id"]),
            DEFAULT_TRIGGER,
            "exact",
            DEFAULT_NAME,
            str(existing.get("description") or "固定フレーズからジョーカー候補を抽選します。"),
            bool(existing.get("admin_only", False)),
            True,
            {**(existing.get("config_json") or {}), **config_json},
        )
    replace_choices(connection, bot_id, guild_id, int(reaction["id"]), DEFAULT_CHOICES)
    return int(reaction["id"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot-id", default=config.BOT_INSTANCE_ID or "ichiyon")
    parser.add_argument("--guild-id", action="append", default=[])
    args = parser.parse_args()

    with get_connection() as connection:
        guild_ids = args.guild_id or list_active_guild_ids(connection, args.bot_id)
        for guild_id in guild_ids:
            reaction_id = seed_guild(connection, args.bot_id, str(guild_id))
            print("guild_id={0} random_draw_id={1} seeded".format(guild_id, reaction_id))
        connection.commit()
    print("seed_joker_random_draw completed guilds={0}".format(len(guild_ids)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
