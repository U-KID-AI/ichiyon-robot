import os
from contextlib import contextmanager
from typing import Iterator, Optional

from dotenv import load_dotenv


load_dotenv()


def get_database_url() -> Optional[str]:
    value = os.getenv("DATABASE_URL")
    if value is None:
        return None

    value = value.strip()
    if not value:
        return None

    return value


def require_database_url() -> str:
    database_url = get_database_url()
    if database_url is None:
        raise RuntimeError("DATABASE_URL is not set")

    return database_url


def connect(database_url: Optional[str] = None):
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is not installed. Run pip install -r requirements.txt."
        ) from exc

    return psycopg.connect(database_url or require_database_url())


@contextmanager
def get_connection(database_url: Optional[str] = None) -> Iterator[object]:
    connection = connect(database_url)
    try:
        yield connection
    finally:
        connection.close()


def ping(database_url: Optional[str] = None) -> bool:
    with get_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()

    return row == (1,)
