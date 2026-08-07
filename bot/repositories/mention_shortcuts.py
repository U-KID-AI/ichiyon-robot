import unicodedata
from typing import Any, Dict, List, Optional

from bot import config
from bot.repositories.base import fetch_all, fetch_one


def normalize_shortcut_trigger(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(text.strip().split())


class MentionShortcutRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def list_shortcuts(self, guild_id: str, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        params: List[Any] = [self.bot_id, guild_id]
        where = ["bot_id = %s", "guild_id = %s"]
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM mention_shortcut_price_targets t WHERE t.shortcut_id = s.id) AS price_target_count,
                       (SELECT COUNT(*) FROM mention_shortcut_audio_actions a WHERE a.shortcut_id = s.id AND a.enabled = TRUE) AS audio_action_count
                FROM mention_shortcuts s
                WHERE {where}
                ORDER BY enabled DESC, name ASC, id ASC
                """.format(where=" AND ".join(where)),
                params,
            )
            return fetch_all(cursor)

    def get_shortcut(self, guild_id: str, shortcut_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mention_shortcuts
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, shortcut_id),
            )
            return fetch_one(cursor)

    def find_by_trigger(self, guild_id: str, trigger_text: str) -> Optional[Dict[str, Any]]:
        key = normalize_shortcut_trigger(trigger_text)
        if not key:
            return None
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mention_shortcuts
                WHERE bot_id = %s AND guild_id = %s AND trigger_key = %s AND enabled = TRUE
                """,
                (self.bot_id, guild_id, key),
            )
            return fetch_one(cursor)

    def upsert_shortcut(self, guild_id: str, values: Dict[str, Any], shortcut_id: Optional[int] = None) -> Dict[str, Any]:
        trigger_text = str(values.get("trigger_text") or "").strip()
        trigger_key = normalize_shortcut_trigger(trigger_text)
        if shortcut_id is None:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO mention_shortcuts (
                        bot_id, guild_id, name, trigger_text, trigger_key, match_type, enabled
                    )
                    VALUES (%s, %s, %s, %s, %s, 'exact', %s)
                    ON CONFLICT (bot_id, guild_id, trigger_key) DO UPDATE
                    SET name = EXCLUDED.name,
                        trigger_text = EXCLUDED.trigger_text,
                        match_type = 'exact',
                        enabled = EXCLUDED.enabled,
                        updated_at = NOW()
                    RETURNING *
                    """,
                    (
                        self.bot_id,
                        guild_id,
                        values["name"],
                        trigger_text,
                        trigger_key,
                        values.get("enabled", True),
                    ),
                )
                return fetch_one(cursor)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mention_shortcuts
                SET name = %s,
                    trigger_text = %s,
                    trigger_key = %s,
                    match_type = 'exact',
                    enabled = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    values["name"],
                    trigger_text,
                    trigger_key,
                    values.get("enabled", True),
                    self.bot_id,
                    guild_id,
                    shortcut_id,
                ),
            )
            return fetch_one(cursor)

    def set_enabled(self, guild_id: str, shortcut_id: int, enabled: bool) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mention_shortcuts
                SET enabled = %s, updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, self.bot_id, guild_id, shortcut_id),
            )
            return fetch_one(cursor)

    def delete_shortcut(self, guild_id: str, shortcut_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM mention_shortcuts
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, shortcut_id),
            )
            return cursor.rowcount > 0

    def list_price_targets(self, shortcut_id: int, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        params: List[Any] = [shortcut_id]
        where = ["shortcut_id = %s"]
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mention_shortcut_price_targets
                WHERE {where}
                ORDER BY sort_order ASC, id ASC
                """.format(where=" AND ".join(where)),
                params,
            )
            return fetch_all(cursor)

    def replace_price_targets(self, shortcut_id: int, targets: List[Dict[str, Any]]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM mention_shortcut_price_targets WHERE shortcut_id = %s", (shortcut_id,))
            for index, target in enumerate(targets, start=1):
                cursor.execute(
                    """
                    INSERT INTO mention_shortcut_price_targets (
                        shortcut_id, provider, provider_product_id, lookup_type, display_name,
                        sort_order, include_historical_low, enabled
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        shortcut_id,
                        target["provider"],
                        target["provider_product_id"],
                        target.get("lookup_type", ""),
                        target.get("display_name", ""),
                        target.get("sort_order", index * 10),
                        target.get("include_historical_low", True),
                        target.get("enabled", True),
                    ),
                )

    def list_audio_actions(self, shortcut_id: int, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        params: List[Any] = [shortcut_id]
        where = ["a.shortcut_id = %s"]
        if enabled is not None:
            where.append("a.enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.*, aa.display_name AS audio_asset_name, aa.enabled AS audio_asset_enabled
                FROM mention_shortcut_audio_actions a
                LEFT JOIN audio_assets aa ON aa.id = a.audio_asset_id
                WHERE {where}
                ORDER BY a.id ASC
                """.format(where=" AND ".join(where)),
                params,
            )
            return fetch_all(cursor)

    def replace_audio_actions(self, shortcut_id: int, actions: List[Dict[str, Any]]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM mention_shortcut_audio_actions WHERE shortcut_id = %s", (shortcut_id,))
            for action in actions:
                cursor.execute(
                    """
                    INSERT INTO mention_shortcut_audio_actions (
                        shortcut_id, audio_asset_id, play_condition, volume_override, enabled
                    )
                    VALUES (%s, %s, 'bot_in_vc', %s, %s)
                    """,
                    (
                        shortcut_id,
                        action.get("audio_asset_id"),
                        action.get("volume_override"),
                        action.get("enabled", True),
                    ),
                )
