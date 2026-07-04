import json
from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one, json_dumps


class ReactionThresholdRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_rules(
        self,
        guild_id: str,
        enabled: Optional[bool] = True,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM reaction_threshold_rules
                WHERE {where}
                ORDER BY enabled DESC, name ASC, id ASC
                """.format(where=" AND ".join(where)),
                params,
            )
            return fetch_all(cursor)

    def get_by_id(self, guild_id: str, rule_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM reaction_threshold_rules
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, rule_id),
            )
            return fetch_one(cursor)

    def create_rule(
        self,
        guild_id: str,
        name: str,
        enabled: bool,
        config_json: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO reaction_threshold_rules (
                    guild_id,
                    name,
                    enabled,
                    config_json
                )
                VALUES (%s, %s, %s, %s::JSONB)
                RETURNING *
                """,
                (guild_id, name, enabled, json_dumps(config_json)),
            )
            return fetch_one(cursor)

    def update_rule(
        self,
        guild_id: str,
        rule_id: int,
        name: str,
        enabled: bool,
        config_json: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reaction_threshold_rules
                SET name = %s,
                    enabled = %s,
                    config_json = %s::JSONB,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (name, enabled, json_dumps(config_json), guild_id, rule_id),
            )
            return fetch_one(cursor)

    def set_enabled(self, guild_id: str, rule_id: int, enabled: bool) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reaction_threshold_rules
                SET enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, guild_id, rule_id),
            )
            return fetch_one(cursor)

    def bulk_set_enabled(self, guild_id: str, rule_ids: List[int], enabled: bool) -> int:
        if not rule_ids:
            return 0
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reaction_threshold_rules
                SET enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s
                  AND id = ANY(%s)
                """,
                (enabled, guild_id, rule_ids),
            )
            return cursor.rowcount

    def copy_rule(self, guild_id: str, rule_id: int) -> Optional[Dict[str, Any]]:
        source = self.get_by_id(guild_id, rule_id)
        if source is None:
            return None
        config = source.get("config_json") or {}
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except ValueError:
                config = {}
        if not isinstance(config, dict):
            config = {}
        return self.create_rule(
            guild_id,
            "{0} コピー".format(str(source.get("name") or "リアクション返信").strip()),
            False,
            config,
        )

    def delete_rule(self, guild_id: str, rule_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM reaction_threshold_rules
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, rule_id),
            )
            return cursor.rowcount > 0

    def event_exists(
        self,
        guild_id: str,
        rule_id: int,
        message_id: str,
        emoji_key: str,
        threshold: int,
    ) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM reaction_threshold_events
                WHERE guild_id = %s
                  AND rule_id = %s
                  AND message_id = %s
                  AND emoji_key = %s
                  AND threshold = %s
                """,
                (guild_id, rule_id, message_id, emoji_key, threshold),
            )
            return fetch_one(cursor) is not None

    def record_event(
        self,
        guild_id: str,
        rule_id: int,
        message_id: str,
        channel_id: str,
        emoji_key: str,
        threshold: int,
    ) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO reaction_threshold_events (
                    guild_id,
                    rule_id,
                    message_id,
                    channel_id,
                    emoji_key,
                    threshold
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (guild_id, rule_id, message_id, emoji_key, threshold) DO NOTHING
                """,
                (guild_id, rule_id, message_id, channel_id, emoji_key, threshold),
            )
            return cursor.rowcount > 0
