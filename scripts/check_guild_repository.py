import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.db import get_connection
from bot.guild_context import get_guild_id_from_message
from bot.repositories import GuildRepository


@dataclass
class FakeIcon:
    url: str


@dataclass
class FakeGuild:
    id: int
    name: str
    icon: Optional[FakeIcon]


@dataclass
class FakeMessage:
    guild: Optional[FakeGuild]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check GuildRepository upsert/get.")
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL for this run.",
    )
    parser.add_argument(
        "--guild-id",
        default="123456789012345678",
        help="Guild ID to upsert.",
    )
    parser.add_argument(
        "--guild-name",
        default="GuildRepository確認用",
        help="Guild name to upsert.",
    )
    parser.add_argument(
        "--icon-url",
        default="https://example.com/icon.png",
        help="Icon URL to upsert.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    guild = FakeGuild(
        id=int(args.guild_id),
        name=args.guild_name,
        icon=FakeIcon(args.icon_url),
    )
    message = FakeMessage(guild=guild)

    with get_connection(args.database_url) as connection:
        repository = GuildRepository(connection)
        upserted = repository.upsert_from_discord_guild(guild)
        fetched = repository.get(str(guild.id))
        connection.commit()

    print("message_guild_id={0}".format(get_guild_id_from_message(message)))
    print("upserted_guild_id={0}".format(upserted["guild_id"]))
    print("fetched_name={0}".format(fetched["name"] if fetched else ""))
    print("fetched_icon_url={0}".format(fetched["icon_url"] if fetched else ""))


if __name__ == "__main__":
    main()
