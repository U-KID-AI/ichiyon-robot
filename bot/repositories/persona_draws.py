import unicodedata
from typing import Any, Dict, List, Optional

from bot import config
from bot.repositories.base import fetch_all, fetch_one


def normalize_persona_trigger(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(text.strip().split())


class PersonaDrawRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def list_draws(self, guild_id: str, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        params: List[Any] = [self.bot_id, guild_id]
        where = ["bot_id = %s", "guild_id = %s"]
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.*,
                       (SELECT COUNT(*) FROM persona_draw_candidates c
                        WHERE c.bot_id = d.bot_id AND c.guild_id = d.guild_id
                          AND c.persona_draw_id = d.id AND c.enabled = TRUE) AS candidate_count,
                       (SELECT COUNT(*) FROM persona_draw_reroll_lines r
                        WHERE r.bot_id = d.bot_id AND r.guild_id = d.guild_id
                          AND r.persona_draw_id = d.id AND r.enabled = TRUE) AS reroll_line_count
                FROM persona_draws d
                WHERE {where}
                ORDER BY enabled DESC, name ASC, id ASC
                """.format(where=" AND ".join(where)),
                params,
            )
            return fetch_all(cursor)

    def get_draw(self, guild_id: str, draw_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM persona_draws
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, draw_id),
            )
            return fetch_one(cursor)

    def find_by_trigger(self, guild_id: str, trigger_text: str) -> Optional[Dict[str, Any]]:
        trigger_key = normalize_persona_trigger(trigger_text)
        if not trigger_key:
            return None
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM persona_draws
                WHERE bot_id = %s
                  AND guild_id = %s
                  AND trigger_key = %s
                  AND enabled = TRUE
                """,
                (self.bot_id, guild_id, trigger_key),
            )
            return fetch_one(cursor)

    def upsert_draw(self, guild_id: str, values: Dict[str, Any], draw_id: Optional[int] = None) -> Dict[str, Any]:
        trigger_text = str(values.get("trigger_text") or "").strip()
        trigger_key = normalize_persona_trigger(trigger_text)
        params = (
            self.bot_id,
            guild_id,
            str(values.get("name") or "").strip(),
            trigger_text,
            trigger_key,
            bool(values.get("reroll_enabled")),
            int(values.get("reroll_probability_percent") or 0),
            int(values.get("max_rerolls") or 0),
            bool(values.get("enabled")),
        )
        if draw_id is None:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO persona_draws (
                        bot_id, guild_id, name, trigger_text, trigger_key,
                        reroll_enabled, reroll_probability_percent, max_rerolls, enabled
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (bot_id, guild_id, trigger_key) DO UPDATE
                    SET name = EXCLUDED.name,
                        trigger_text = EXCLUDED.trigger_text,
                        reroll_enabled = EXCLUDED.reroll_enabled,
                        reroll_probability_percent = EXCLUDED.reroll_probability_percent,
                        max_rerolls = EXCLUDED.max_rerolls,
                        enabled = EXCLUDED.enabled,
                        updated_at = NOW()
                    RETURNING *
                    """,
                    params,
                )
                return fetch_one(cursor)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE persona_draws
                SET name = %s,
                    trigger_text = %s,
                    trigger_key = %s,
                    reroll_enabled = %s,
                    reroll_probability_percent = %s,
                    max_rerolls = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    params[2],
                    params[3],
                    params[4],
                    params[5],
                    params[6],
                    params[7],
                    params[8],
                    self.bot_id,
                    guild_id,
                    draw_id,
                ),
            )
            return fetch_one(cursor)

    def set_enabled(self, guild_id: str, draw_id: int, enabled: bool) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE persona_draws
                SET enabled = %s, updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, self.bot_id, guild_id, draw_id),
            )
            return fetch_one(cursor)

    def delete_draw(self, guild_id: str, draw_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM persona_draws
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, draw_id),
            )
            return cursor.rowcount > 0

    def list_candidates(self, guild_id: str, draw_id: int, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        return self._list_children("persona_draw_candidates", guild_id, draw_id, enabled)

    def list_reroll_lines(self, guild_id: str, draw_id: int, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        return self._list_children("persona_draw_reroll_lines", guild_id, draw_id, enabled)

    def replace_candidates(self, guild_id: str, draw_id: int, bodies: List[str]) -> None:
        self._replace_children("persona_draw_candidates", guild_id, draw_id, bodies)

    def replace_reroll_lines(self, guild_id: str, draw_id: int, bodies: List[str]) -> None:
        self._replace_children("persona_draw_reroll_lines", guild_id, draw_id, bodies)

    def _list_children(self, table: str, guild_id: str, draw_id: int, enabled: Optional[bool]) -> List[Dict[str, Any]]:
        params: List[Any] = [self.bot_id, guild_id, draw_id]
        where = ["bot_id = %s", "guild_id = %s", "persona_draw_id = %s"]
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM {table}
                WHERE {where}
                ORDER BY sort_order ASC, id ASC
                """.format(table=table, where=" AND ".join(where)),
                params,
            )
            return fetch_all(cursor)

    def _replace_children(self, table: str, guild_id: str, draw_id: int, bodies: List[str]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM {table} WHERE bot_id = %s AND guild_id = %s AND persona_draw_id = %s".format(table=table),
                (self.bot_id, guild_id, draw_id),
            )
            for index, body in enumerate(bodies, start=1):
                cleaned = str(body or "").strip()
                if not cleaned:
                    continue
                cursor.execute(
                    """
                    INSERT INTO {table} (
                        bot_id, guild_id, persona_draw_id, body, sort_order, enabled
                    )
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    """.format(table=table),
                    (self.bot_id, guild_id, draw_id, cleaned, index * 10),
                )
