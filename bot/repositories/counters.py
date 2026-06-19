from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one


class CounterRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def get_counter(self, guild_id: str, count_key: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM counters
                WHERE guild_id = %s AND count_key = %s
                """,
                (guild_id, count_key),
            )
            return fetch_one(cursor)

    def get_by_key(self, guild_id: str, count_key: str) -> Optional[Dict[str, Any]]:
        return self.get_counter(guild_id, count_key)

    def list_counters(self, guild_id: str) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM counters
                WHERE guild_id = %s
                ORDER BY name ASC, count_key ASC
                """,
                (guild_id,),
            )
            return fetch_all(cursor)

    def create_counter(
        self,
        guild_id: str,
        count_key: str,
        name: str,
        description: Optional[str] = None,
        initial_value: int = 0,
        reset_type: str = "none",
        reset_day: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO counters (
                    guild_id,
                    count_key,
                    name,
                    description,
                    initial_value,
                    reset_type,
                    reset_day
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (guild_id, count_key) DO UPDATE
                SET name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    initial_value = EXCLUDED.initial_value,
                    reset_type = EXCLUDED.reset_type,
                    reset_day = EXCLUDED.reset_day,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    guild_id,
                    count_key,
                    name,
                    description,
                    initial_value,
                    reset_type,
                    reset_day,
                ),
            )
            return fetch_one(cursor)

    def get_state(self, guild_id: str, count_key: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.*
                FROM counter_states s
                JOIN counters c ON c.id = s.counter_id
                WHERE s.guild_id = %s AND c.count_key = %s
                """,
                (guild_id, count_key),
            )
            return fetch_one(cursor)

    def set_value(
        self,
        guild_id: str,
        count_key: str,
        value: int,
        period_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        counter = self.get_counter(guild_id, count_key)
        if counter is None:
            raise ValueError("counter does not exist: {0}".format(count_key))

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO counter_states (
                    guild_id,
                    counter_id,
                    current_value,
                    period_key
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (guild_id, counter_id) DO UPDATE
                SET current_value = EXCLUDED.current_value,
                    period_key = EXCLUDED.period_key,
                    updated_at = NOW()
                RETURNING *
                """,
                (guild_id, counter["id"], value, period_key),
            )
            return fetch_one(cursor)

    def increment(
        self,
        guild_id: str,
        count_key: str,
        amount: int = 1,
        period_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        counter = self.get_counter(guild_id, count_key)
        if counter is None:
            raise ValueError("counter does not exist: {0}".format(count_key))

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO counter_states (
                    guild_id,
                    counter_id,
                    current_value,
                    period_key
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (guild_id, counter_id) DO UPDATE
                SET current_value = counter_states.current_value + EXCLUDED.current_value,
                    period_key = COALESCE(EXCLUDED.period_key, counter_states.period_key),
                    updated_at = NOW()
                RETURNING *
                """,
                (guild_id, counter["id"], amount, period_key),
            )
            return fetch_one(cursor)

    def reset(self, guild_id: str, count_key: str) -> Dict[str, Any]:
        counter = self.get_counter(guild_id, count_key)
        if counter is None:
            raise ValueError("counter does not exist: {0}".format(count_key))

        return self.set_value(guild_id, count_key, counter["initial_value"])
