import argparse
import sys
from pathlib import Path
from typing import Optional, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.db import get_connection


GLOBAL_ROLES = ("global_admin",)
GUILD_ROLES = ("guild_admin", "editor", "viewer")
ALL_ROLES = GLOBAL_ROLES + GUILD_ROLES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grant admin UI access for local or verification use.")
    parser.add_argument("--database-url", required=True, help="PostgreSQL database URL.")
    parser.add_argument("--discord-user-id", required=True, help="Discord user id to grant.")
    parser.add_argument("--guild-id", required=True, help="Target guild id.")
    parser.add_argument("--role", required=True, choices=ALL_ROLES, help="Role to grant.")
    parser.add_argument("--dry-run", action="store_true", help="Show the planned change without committing.")
    return parser.parse_args()


def get_guild_name(connection, guild_id: str) -> Optional[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM guilds WHERE guild_id = %s", (guild_id,))
        row = cursor.fetchone()
    if row is None:
        return None
    return row[0]


def grant_global_admin(connection, discord_user_id: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT role FROM admin_users WHERE discord_user_id = %s",
            (discord_user_id,),
        )
        row = cursor.fetchone()
        if row is not None and row[0] == "global_admin":
            return "skipped"

        cursor.execute(
            """
            INSERT INTO admin_users (discord_user_id, role)
            VALUES (%s, 'global_admin')
            ON CONFLICT (discord_user_id) DO UPDATE
            SET role = EXCLUDED.role,
                updated_at = NOW()
            """,
            (discord_user_id,),
        )
    return "updated" if row is not None else "inserted"


def grant_guild_role(connection, guild_id: str, discord_user_id: str, role: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT role
            FROM guild_permissions
            WHERE guild_id = %s AND discord_user_id = %s
            """,
            (guild_id, discord_user_id),
        )
        row = cursor.fetchone()
        if row is not None and row[0] == role:
            return "skipped"

        cursor.execute(
            """
            INSERT INTO guild_permissions (guild_id, discord_user_id, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (guild_id, discord_user_id) DO UPDATE
            SET role = EXCLUDED.role,
                updated_at = NOW()
            """,
            (guild_id, discord_user_id, role),
        )
    return "updated" if row is not None else "inserted"


def grant_access(
    database_url: str,
    discord_user_id: str,
    guild_id: str,
    role: str,
    dry_run: bool,
) -> Tuple[str, str]:
    with get_connection(database_url) as connection:
        guild_name = get_guild_name(connection, guild_id)
        if guild_name is None:
            connection.rollback()
            raise RuntimeError(
                "guild_id {0} was not found. Run migrations and seed/register the guild first.".format(guild_id)
            )

        if role in GLOBAL_ROLES:
            action = grant_global_admin(connection, discord_user_id)
            detail = "admin_users global_admin; /servers will show all guilds, including {0}".format(guild_name)
        else:
            action = grant_guild_role(connection, guild_id, discord_user_id, role)
            detail = "guild_permissions {0} for {1}".format(role, guild_name)

        if dry_run:
            connection.rollback()
        else:
            connection.commit()

    return action, detail


def main() -> None:
    args = parse_args()
    try:
        action, detail = grant_access(
            args.database_url,
            args.discord_user_id,
            args.guild_id,
            args.role,
            args.dry_run,
        )
    except RuntimeError as exc:
        print("ERROR: {0}".format(exc))
        raise SystemExit(1)

    prefix = "dry-run" if args.dry_run else "completed"
    print(
        "grant admin access {0}: action={1} discord_user_id={2} guild_id={3} role={4}".format(
            prefix,
            action,
            args.discord_user_id,
            args.guild_id,
            args.role,
        )
    )
    print(detail)


if __name__ == "__main__":
    main()
