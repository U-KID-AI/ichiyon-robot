from typing import Any, Dict, List, Optional

from bot import config
from bot.repositories.base import fetch_all, fetch_one


class ScheduleTemplateRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def list_templates(self, guild_id: str, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        params = [self.bot_id, guild_id]
        where = ["bot_id = %s", "guild_id = %s"]
        if enabled is not None:
            where.append("is_enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM schedule_templates
                WHERE {where}
                ORDER BY is_enabled DESC, name ASC, id ASC
                """.format(where=" AND ".join(where)),
                params,
            )
            return fetch_all(cursor)

    def get_by_id(self, guild_id: str, template_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM schedule_templates
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, template_id),
            )
            return fetch_one(cursor)

    def get_by_name(self, guild_id: str, name: str, enabled: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        params = [self.bot_id, guild_id, name]
        enabled_sql = ""
        if enabled is not None:
            enabled_sql = "AND is_enabled = %s"
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM schedule_templates
                WHERE bot_id = %s
                  AND guild_id = %s
                  AND lower(name) = lower(%s)
                  {enabled_sql}
                ORDER BY id ASC
                LIMIT 1
                """.format(enabled_sql=enabled_sql),
                params,
            )
            return fetch_one(cursor)

    def list_items(self, template_id: int) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM schedule_template_items
                WHERE template_id = %s
                ORDER BY day_index ASC
                """,
                (template_id,),
            )
            return fetch_all(cursor)

    def create_template(
        self,
        guild_id: str,
        name: str,
        description: str,
        is_enabled: bool,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO schedule_templates (
                    bot_id, guild_id, name, description, is_enabled
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (self.bot_id, guild_id, name, description, is_enabled),
            )
            return fetch_one(cursor)

    def update_template(
        self,
        guild_id: str,
        template_id: int,
        name: str,
        description: str,
        is_enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE schedule_templates
                SET name = %s,
                    description = %s,
                    is_enabled = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (name, description, is_enabled, self.bot_id, guild_id, template_id),
            )
            return fetch_one(cursor)

    def upsert_item(self, template_id: int, day_index: int, content: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO schedule_template_items (template_id, day_index, content)
                VALUES (%s, %s, %s)
                ON CONFLICT (template_id, day_index) DO UPDATE
                SET content = EXCLUDED.content,
                    updated_at = NOW()
                """,
                (template_id, day_index, content),
            )

    def replace_items(self, template_id: int, day_contents: Dict[int, str]) -> None:
        for day_index in range(1, 15):
            self.upsert_item(template_id, day_index, day_contents.get(day_index, ""))

    def toggle_enabled(self, guild_id: str, template_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE schedule_templates
                SET is_enabled = NOT is_enabled,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (self.bot_id, guild_id, template_id),
            )
            return fetch_one(cursor)

    def delete_template(self, guild_id: str, template_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM schedule_templates
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, template_id),
            )
            return cursor.rowcount > 0
