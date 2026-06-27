import json
from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one, json_dumps


def json_dumps_list(value: List[str]) -> str:
    return json.dumps(value, ensure_ascii=False)


class ModeRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_modes(
        self,
        guild_id: str,
        query: Optional[str] = None,
        enabled: Optional[bool] = None,
        behavior_type: Optional[str] = None,
        admin_only: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

        if query:
            like_query = "%{0}%".format(query)
            where.append("(name ILIKE %s OR mode_key ILIKE %s OR COALESCE(description, '') ILIKE %s)")
            params.extend([like_query, like_query, like_query])

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        if behavior_type is not None:
            where.append("behavior_type = %s")
            params.append(behavior_type)

        if admin_only is not None:
            where.append("admin_only = %s")
            params.append(admin_only)

        sql = """
            SELECT *
            FROM modes
            WHERE {where}
            ORDER BY name ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def get_by_id(self, guild_id: str, mode_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM modes
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, mode_id),
            )
            return fetch_one(cursor)

    def mode_key_exists(
        self,
        guild_id: str,
        mode_key: str,
        exclude_mode_id: Optional[int] = None,
    ) -> bool:
        params = [guild_id, mode_key]
        where = ["guild_id = %s", "mode_key = %s"]
        if exclude_mode_id is not None:
            where.append("id <> %s")
            params.append(exclude_mode_id)
        sql = """
            SELECT 1
            FROM modes
            WHERE {where}
            LIMIT 1
        """.format(where=" AND ".join(where))
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone() is not None

    def create_mode(
        self,
        guild_id: str,
        mode_key: str,
        name: str,
        description: str,
        behavior_type: str,
        mode_icon_path: str,
        enter_message: str,
        exit_message: str,
        enter_gif_path: str,
        exit_gif_path: str,
        enter_notify_channel_id: str,
        exit_notify_channel_id: str,
        reaction_channel_ids: List[str],
        ignore_channel_ids: List[str],
        cooldown_config: Dict[str, Any],
        enabled: bool,
        admin_only: bool,
        is_deletable: bool,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO modes (
                    guild_id, mode_key, name, description, behavior_type,
                    mode_icon_path, enter_message, exit_message, enter_gif_path, exit_gif_path,
                    enter_notify_channel_id, exit_notify_channel_id, reaction_channel_ids,
                    ignore_channel_ids, cooldown_config_json, enabled, admin_only, is_deletable
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB, %s::JSONB, %s::JSONB, %s, %s, %s)
                RETURNING *
                """,
                (
                    guild_id,
                    mode_key,
                    name,
                    description,
                    behavior_type,
                    mode_icon_path,
                    enter_message,
                    exit_message,
                    enter_gif_path,
                    exit_gif_path,
                    enter_notify_channel_id,
                    exit_notify_channel_id,
                    json_dumps_list(reaction_channel_ids),
                    json_dumps_list(ignore_channel_ids),
                    json_dumps(cooldown_config),
                    enabled,
                    admin_only,
                    is_deletable,
                ),
            )
            return fetch_one(cursor)

    def update_mode(
        self,
        guild_id: str,
        mode_id: int,
        mode_key: str,
        name: str,
        description: str,
        behavior_type: str,
        mode_icon_path: str,
        enter_message: str,
        exit_message: str,
        enter_gif_path: str,
        exit_gif_path: str,
        enter_notify_channel_id: str,
        exit_notify_channel_id: str,
        reaction_channel_ids: List[str],
        ignore_channel_ids: List[str],
        cooldown_config: Dict[str, Any],
        enabled: bool,
        admin_only: bool,
        is_deletable: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE modes
                SET mode_key = %s,
                    name = %s,
                    description = %s,
                    behavior_type = %s,
                    mode_icon_path = %s,
                    enter_message = %s,
                    exit_message = %s,
                    enter_gif_path = %s,
                    exit_gif_path = %s,
                    enter_notify_channel_id = %s,
                    exit_notify_channel_id = %s,
                    reaction_channel_ids = %s::JSONB,
                    ignore_channel_ids = %s::JSONB,
                    cooldown_config_json = %s::JSONB,
                    enabled = %s,
                    admin_only = %s,
                    is_deletable = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    mode_key,
                    name,
                    description,
                    behavior_type,
                    mode_icon_path,
                    enter_message,
                    exit_message,
                    enter_gif_path,
                    exit_gif_path,
                    enter_notify_channel_id,
                    exit_notify_channel_id,
                    json_dumps_list(reaction_channel_ids),
                    json_dumps_list(ignore_channel_ids),
                    json_dumps(cooldown_config),
                    enabled,
                    admin_only,
                    is_deletable,
                    guild_id,
                    mode_id,
                ),
            )
            return fetch_one(cursor)

    def set_enabled(self, guild_id: str, mode_id: int, enabled: bool) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE modes
                SET enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, guild_id, mode_id),
            )
            return fetch_one(cursor)

    def toggle_enabled(self, guild_id: str, mode_id: int) -> Optional[Dict[str, Any]]:
        mode = self.get_by_id(guild_id, mode_id)
        if mode is None:
            return None
        return self.set_enabled(guild_id, mode_id, not bool(mode["enabled"]))

    def delete_mode(self, guild_id: str, mode_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mode_states
                SET current_mode_id = NULL,
                    active_until = NULL,
                    pseudo_offline_until = NULL,
                    updated_at = NOW()
                WHERE guild_id = %s AND current_mode_id = %s
                """,
                (guild_id, mode_id),
            )
            cursor.execute(
                """
                DELETE FROM modes
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, mode_id),
            )
            return cursor.rowcount > 0

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

    def enter_mode(
        self,
        guild_id: str,
        mode_id: int,
        active_until: Optional[str],
        state_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.upsert_mode_state(
            guild_id,
            mode_id,
            active_until,
            None,
            0,
            {},
            state_json or {},
        )

    def clear_mode_state(
        self,
        guild_id: str,
        state_json: Optional[Dict[str, Any]] = None,
    ) -> None:
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
                VALUES (%s, NULL, NULL, NULL, 0, '{}'::jsonb, %s::jsonb)
                ON CONFLICT (guild_id) DO UPDATE
                SET current_mode_id = NULL,
                    active_until = NULL,
                    pseudo_offline_until = NULL,
                    shikocchi_count = 0,
                    state_json = EXCLUDED.state_json,
                    updated_at = NOW()
                """,
                (guild_id, json_dumps(state_json or {})),
            )

    def list_expired_mode_states(self) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mode_states
                WHERE current_mode_id IS NOT NULL
                  AND active_until IS NOT NULL
                  AND active_until <= NOW()
                ORDER BY active_until ASC
                """
            )
            return fetch_all(cursor)

    def get_trigger_history(
        self,
        guild_id: str,
        mode_id: int,
        period_key: str,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mode_trigger_history
                WHERE guild_id = %s AND mode_id = %s AND period_key = %s
                """,
                (guild_id, mode_id, period_key),
            )
            return fetch_one(cursor)

    def record_trigger_history(
        self,
        guild_id: str,
        mode_id: int,
        period_key: str,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metadata = metadata_json or {}
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_trigger_history (
                    guild_id,
                    mode_id,
                    period_key,
                    triggered_at,
                    metadata_json
                )
                VALUES (%s, %s, %s, NOW(), %s::jsonb)
                ON CONFLICT (guild_id, mode_id, period_key) DO UPDATE
                SET triggered_at = EXCLUDED.triggered_at,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = NOW()
                RETURNING *
                """,
                (guild_id, mode_id, period_key, json_dumps(metadata)),
            )
            return fetch_one(cursor)

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

    def create_reply_choice(
        self,
        guild_id: str,
        mode_id: int,
        name: str,
        body: Optional[str],
        image_path: Optional[str],
        appearance_rate: int,
        enabled: bool,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_reply_choices (
                    guild_id, mode_id, name, body, image_path, appearance_rate, enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (guild_id, mode_id, name, body, image_path, appearance_rate, enabled),
            )
            return fetch_one(cursor)

    def update_reply_choice(
        self,
        guild_id: str,
        choice_id: int,
        name: str,
        body: Optional[str],
        image_path: Optional[str],
        appearance_rate: int,
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mode_reply_choices
                SET name = %s,
                    body = %s,
                    image_path = %s,
                    appearance_rate = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (name, body, image_path, appearance_rate, enabled, guild_id, choice_id),
            )
            return fetch_one(cursor)

    def delete_reply_choice(self, guild_id: str, choice_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM mode_reply_choices
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, choice_id),
            )
            return cursor.rowcount > 0

    def get_reply_choice(self, guild_id: str, choice_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM mode_reply_choices WHERE guild_id = %s AND id = %s",
                (guild_id, choice_id),
            )
            return fetch_one(cursor)

    def create_trigger_condition(
        self,
        guild_id: str,
        mode_id: int,
        condition_type: str,
        condition_config: Dict[str, Any],
        group_operator: str,
        enabled: bool,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_trigger_conditions (
                    guild_id, mode_id, condition_type, condition_config_json, group_operator, enabled
                )
                VALUES (%s, %s, %s, %s::JSONB, %s, %s)
                RETURNING *
                """,
                (guild_id, mode_id, condition_type, json_dumps(condition_config), group_operator, enabled),
            )
            return fetch_one(cursor)

    def update_trigger_condition(
        self,
        guild_id: str,
        condition_id: int,
        condition_type: str,
        condition_config: Dict[str, Any],
        group_operator: str,
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mode_trigger_conditions
                SET condition_type = %s,
                    condition_config_json = %s::JSONB,
                    group_operator = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (condition_type, json_dumps(condition_config), group_operator, enabled, guild_id, condition_id),
            )
            return fetch_one(cursor)

    def get_trigger_condition(self, guild_id: str, condition_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM mode_trigger_conditions WHERE guild_id = %s AND id = %s",
                (guild_id, condition_id),
            )
            return fetch_one(cursor)

    def create_exit_condition(
        self,
        guild_id: str,
        mode_id: int,
        condition_type: str,
        condition_config: Dict[str, Any],
        enabled: bool,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_exit_conditions (
                    guild_id, mode_id, condition_type, condition_config_json, enabled
                )
                VALUES (%s, %s, %s, %s::JSONB, %s)
                RETURNING *
                """,
                (guild_id, mode_id, condition_type, json_dumps(condition_config), enabled),
            )
            return fetch_one(cursor)

    def update_exit_condition(
        self,
        guild_id: str,
        condition_id: int,
        condition_type: str,
        condition_config: Dict[str, Any],
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mode_exit_conditions
                SET condition_type = %s,
                    condition_config_json = %s::JSONB,
                    enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (condition_type, json_dumps(condition_config), enabled, guild_id, condition_id),
            )
            return fetch_one(cursor)

    def get_exit_condition(self, guild_id: str, condition_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM mode_exit_conditions WHERE guild_id = %s AND id = %s",
                (guild_id, condition_id),
            )
            return fetch_one(cursor)
