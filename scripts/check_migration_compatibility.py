"""Preserve newer effect rows when applying a missing historical migration."""
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql


def main():
    url = os.environ["DATABASE_URL"]
    info = psycopg.conninfo.conninfo_to_dict(url)
    if (info.get("host"), info.get("port"), info.get("dbname")) != (
        "127.0.0.1", "5432", "routing_test"
    ):
        raise ValueError("Only fixed local disposable CI database is allowed")
    schema = "migration_fixture_" + uuid4().hex
    migration = (Path(__file__).resolve().parent.parent / "migrations/039_add_audio_assets.sql").read_text()
    with psycopg.connect(url) as connection:
        try:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            connection.execute("""CREATE TABLE special_effect_tags (
                id serial PRIMARY KEY, effect_type text NOT NULL,
                CONSTRAINT special_effect_tags_effect_type_check
                CHECK (effect_type IN ('message', 'probability_user_message'))
            )""")
            connection.execute("INSERT INTO special_effect_tags (effect_type) VALUES ('message'), ('probability_user_message')")
            connection.execute(migration)
            assert connection.execute("SELECT effect_type FROM special_effect_tags ORDER BY id").fetchall() == [
                ("message",), ("probability_user_message",)
            ]
            assert connection.execute("SELECT to_regclass('audio_assets') IS NOT NULL").fetchone() == (True,)
            connection.execute("INSERT INTO special_effect_tags (effect_type) VALUES ('audio_asset')")
            try:
                with connection.transaction():
                    connection.execute("INSERT INTO special_effect_tags (effect_type) VALUES ('invalid_fixture_effect')")
            except psycopg.errors.CheckViolation:
                pass
            else:
                raise AssertionError("Invalid effect accepted")
            print("PASS historical audio migration preserves newer effects and rejects invalid effects")
        finally:
            connection.rollback()


if __name__ == "__main__":
    main()
