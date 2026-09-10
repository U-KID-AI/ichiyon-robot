import argparse
import sys
from pathlib import Path
from typing import List


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.db import get_connection
from bot.repositories.persona_draws import PersonaDrawRepository


DEFAULT_BOT_ID = "ichiyon"
DEFAULT_NAME = "ジョーカー"
DEFAULT_TRIGGER = "正体を見せろ！"
DEFAULT_REROLL_PROBABILITY_PERCENT = 10
DEFAULT_MAX_REROLLS = 1
DEFAULT_CANDIDATES = [
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


def seed(bot_id: str) -> int:
    seeded = 0
    with get_connection() as connection:
        repo = PersonaDrawRepository(connection, bot_id=bot_id)
        for guild_id in list_active_guild_ids(connection, bot_id):
            draw = repo.upsert_draw(
                guild_id,
                {
                    "name": DEFAULT_NAME,
                    "trigger_text": DEFAULT_TRIGGER,
                    "reroll_enabled": True,
                    "reroll_probability_percent": DEFAULT_REROLL_PROBABILITY_PERCENT,
                    "max_rerolls": DEFAULT_MAX_REROLLS,
                    "enabled": True,
                },
            )
            repo.replace_candidates(guild_id, int(draw["id"]), DEFAULT_CANDIDATES)
            repo.replace_reroll_lines(guild_id, int(draw["id"]), DEFAULT_REROLL_LINES)
            seeded += 1
            print("guild_id={0} persona_draw_id={1} seeded".format(guild_id, draw["id"]))
        connection.commit()
    return seeded


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the default Joker persona draw.")
    parser.add_argument("--bot-id", default=DEFAULT_BOT_ID)
    args = parser.parse_args()
    count = seed(str(args.bot_id).strip())
    print("seed_joker_persona_draw completed guilds={0}".format(count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
