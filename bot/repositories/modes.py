from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one, json_dumps


class ModeRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_modes(
        self,
        guild_id: str,
        enabled: Optional[bool] = None,
        behavior_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        if behavior_type is not None:
            where.append("behavior_type = %s")
            params.append(behavior_type)

        sql = """
            SELECT *
            FROM modes
            WHERE {where}
            ORDER BY name ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def list_enabled_modes(self, guild_id: str) -> List[Dict[str, Any]]:
        return self.list_modes(guild_id, enabled=True)

    def get_mode_state(self, guild_id: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mode_states
                WHERE guild_id = %s
                """,
                (guild_id,),
            )
            return fetch_one(cursor)

    def upsert_mode_state(
        self,
        guild_id: str,
        current_mode_id: Optional[int],
        active_until: Optional[str],
        pseudo_offline_until: Optional[str],
        shikocchi_count: int,
        period_states_json: Optional[Dict[str, Any]] = None,
        state_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        period_states = period_states_json or {}
        state = state_json or {}
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_states (
                    guild_id,
                    current_mode_id,
                    active_until,
                    pseudo_offline_until,
                    shikocchi_count,
                    period_states_json,
                    state_json
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (guild_id) DO UPDATE
                SET current_mode_id = EXCLUDED.current_mode_id,
                    active_until = EXCLUDED.active_until,
                    pseudo_offline_until = EXCLUDED.pseudo_offline_until,
                    shikocchi_count = EXCLUDED.shikocchi_count,
                    period_states_json = EXCLUDED.period_states_json,
                    state_json = EXCLUDED.state_json,
                    updated_at = NOW()
                """,
                (
                    guild_id,
                    current_mode_id,
                    active_until,
                    pseudo_offline_until,
                    shikocchi_count,
                    json_dumps(period_states),
                    json_dumps(state),
                ),
            )

    def list_trigger_conditions(
        self,
        guild_id: str,
        mode_id: int,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        return self._list_mode_child(
            "mode_trigger_conditions",
            guild_id,
            mode_id,
            enabled,
            "condition_group_key ASC, sort_order ASC, id ASC",
        )

    def list_exit_conditions(
        self,
        guild_id: str,
        mode_id: int,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        return self._list_mode_child(
            "mode_exit_conditions",
            guild_id,
            mode_id,
            enabled,
            "sort_order ASC, id ASC",
        )

    def list_reply_choices(
        self,
        guild_id: str,
        mode_id: int,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        return self._list_mode_child(
            "mode_reply_choices",
            guild_id,
            mode_id,
            enabled,
            "sort_order ASC, id ASC",
        )

    def _list_mode_child(
        self,
        table_name: str,
        guild_id: str,
        mode_id: int,
        enabled: Optional[bool],
        order_by: str,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, mode_id]
        where = ["guild_id = %s", "mode_id = %s"]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        sql = """
            SELECT *
            FROM {table_name}
            WHERE {where}
            ORDER BY {order_by}
        """.format(
            table_name=table_name,
            where=" AND ".join(where),
            order_by=order_by,
        )

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)
