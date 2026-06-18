import argparse
import sys
from pathlib import Path
from typing import Iterable, Optional, Set


ROOT_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT_DIR / "migrations"

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.db import get_connection


def iter_migration_files() -> Iterable[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def ensure_schema_migrations(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    connection.commit()


def get_applied_versions(connection) -> Set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT version FROM schema_migrations")
        rows = cursor.fetchall()

    return {row[0] for row in rows}


def apply_migration(connection, path: Path) -> None:
    version = path.stem
    sql = path.read_text(encoding="utf-8")

    with connection.cursor() as cursor:
        cursor.execute(sql)
        cursor.execute(
            "INSERT INTO schema_migrations (version) VALUES (%s)",
            (version,),
        )

    connection.commit()
    print("applied {0}".format(path.name))


def run_migrations(database_url: Optional[str] = None) -> None:
    migration_files = list(iter_migration_files())
    if not migration_files:
        print("no migration files found")
        return

    with get_connection(database_url) as connection:
        ensure_schema_migrations(connection)
        applied_versions = get_applied_versions(connection)

        for path in migration_files:
            if path.stem in applied_versions:
                print("skipped {0}".format(path.name))
                continue

            apply_migration(connection, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply PostgreSQL migrations.")
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL for this run.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_migrations(args.database_url)


if __name__ == "__main__":
    main()
