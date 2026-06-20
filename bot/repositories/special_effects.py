from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one, json_dumps, normalize_effect_target_type


class SpecialEffectRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_tags(
        self,
        guild_id: str,
        query: Optional[str] = None,
        effect_type: Optional[str] = None,
        target_type: Optional[str] = None,
        enabled: Optional[bool] = None,
        admin_only: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

        if query:
            like_query = "%{0}%".format(query)
            where.append("(name ILIKE %s OR COALESCE(description, '') ILIKE %s)")
            params.extend([like_query, like_query])

        if effect_type is not None:
            where.append("effect_type = %s")
            params.append(effect_type)

        if target_type is not None:
            where.append("target_type = %s")
            params.append(target_type)

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        if admin_only is not None:
            where.append("admin_only = %s")
            params.append(admin_only)

        sql = """
            SELECT *
            FROM special_effect_tags
            WHERE {where}
            ORDER BY priority DESC, name ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def get_by_id(self, guild_id: str, tag_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM special_effect_tags
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, tag_id),
            )
            return fetch_one(cursor)

    def create_tag(
        self,
        guild_id: str,
        name: str,
        description: str,
        color: str,
        admin_only: bool,
        enabled: bool,
        priority: int,
        target_type: str,
        trigger_timing: str,
        effect_type: str,
        effect_config: Dict[str, Any],
        additional_text: str,
        additional_post_timing: str,
        expires_type: str,
        expires_value: Optional[int],
        cooldown_seconds: int,
        cooldown_scope: str,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO special_effect_tags (
                    guild_id,
                    name,
                    description,
                    color,
                    admin_only,
                    enabled,
                    priority,
                    target_type,
                    trigger_timing,
                    effect_type,
                    effect_config_json,
                    additional_text,
                    additional_post_timing,
                    expires_type,
                    expires_value,
                    cooldown_seconds,
                    cooldown_scope
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    guild_id,
                    name,
                    description,
                    color,
                    admin_only,
                    enabled,
                    priority,
                    target_type,
                    trigger_timing,
                    effect_type,
                    json_dumps(effect_config),
                    additional_text,
                    additional_post_timing,
                    expires_type,
                    expires_value,
                    cooldown_seconds,
                    cooldown_scope,
                ),
            )
            return fetch_one(cursor)

    def update_tag(
        self,
        guild_id: str,
        tag_id: int,
        name: str,
        description: str,
        color: str,
        admin_only: bool,
        enabled: bool,
        priority: int,
        target_type: str,
        trigger_timing: str,
        effect_type: str,
        effect_config: Dict[str, Any],
        additional_text: str,
        additional_post_timing: str,
        expires_type: str,
        expires_value: Optional[int],
        cooldown_seconds: int,
        cooldown_scope: str,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE special_effect_tags
                SET name = %s,
                    description = %s,
                    color = %s,
                    admin_only = %s,
                    enabled = %s,
                    priority = %s,
                    target_type = %s,
                    trigger_timing = %s,
                    effect_type = %s,
                    effect_config_json = %s::JSONB,
                    additional_text = %s,
                    additional_post_timing = %s,
                    expires_type = %s,
                    expires_value = %s,
                    cooldown_seconds = %s,
                    cooldown_scope = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    name,
                    description,
                    color,
                    admin_only,
                    enabled,
                    priority,
                    target_type,
                    trigger_timing,
                    effect_type,
                    json_dumps(effect_config),
                    additional_text,
                    additional_post_timing,
                    expires_type,
                    expires_value,
                    cooldown_seconds,
                    cooldown_scope,
                    guild_id,
                    tag_id,
                ),
            )
            return fetch_one(cursor)

    def set_enabled(
        self,
        guild_id: str,
        tag_id: int,
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE special_effect_tags
                SET enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, guild_id, tag_id),
            )
            return fetch_one(cursor)

    def toggle_enabled(
        self,
        guild_id: str,
        tag_id: int,
    ) -> Optional[Dict[str, Any]]:
        tag = self.get_by_id(guild_id, tag_id)
        if tag is None:
            return None
        return self.set_enabled(guild_id, tag_id, not bool(tag["enabled"]))

    def delete_tag(self, guild_id: str, tag_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM special_effect_assignments
                WHERE guild_id = %s AND special_effect_tag_id = %s
                """,
                (guild_id, tag_id),
            )
            cursor.execute(
                """
                DELETE FROM special_effect_tags
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, tag_id),
            )
            return cursor.rowcount > 0

    def list_assignments(
        self,
        guild_id: str,
        target_type: Optional[str] = None,
        target_id: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["a.guild_id = %s"]

        if target_type is not None:
            where.append("a.target_type = %s")
            params.append(normalize_effect_target_type(target_type))

        if target_id is not None:
            where.append("a.target_id = %s")
            params.append(target_id)

        if enabled is not None:
            where.append("a.enabled = %s")
            where.append("t.enabled = %s")
            params.extend([enabled, enabled])

        sql = """
            SELECT
                a.id AS assignment_id,
                a.target_type,
                a.target_id,
                a.enabled AS assignment_enabled,
                t.*
            FROM special_effect_assignments a
            JOIN special_effect_tags t ON t.id = a.special_effect_tag_id
            WHERE {where}
            ORDER BY t.priority DESC, t.name ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def list_for_target(
        self,
        guild_id: str,
        target_type: str,
        target_id: int,
        enabled: Optional[bool] = True,
    ) -> List[Dict[str, Any]]:
        return self.list_assignments(guild_id, target_type, target_id, enabled)

    def assign_tag(
        self,
        guild_id: str,
        special_effect_tag_id: int,
        target_type: str,
        target_id: int,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO special_effect_assignments (
                    guild_id,
                    special_effect_tag_id,
                    target_type,
                    target_id,
                    enabled
                )
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (special_effect_tag_id, target_type, target_id) DO UPDATE
                SET enabled = TRUE,
                    updated_at = NOW()
                RETURNING *
                """,
                (guild_id, special_effect_tag_id, target_type, target_id),
            )
            return fetch_one(cursor)

    def unassign_tag(
        self,
        guild_id: str,
        special_effect_tag_id: int,
        target_type: str,
        target_id: int,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE special_effect_assignments
                SET enabled = FALSE,
                    updated_at = NOW()
                WHERE guild_id = %s
                    AND special_effect_tag_id = %s
                    AND target_type = %s
                    AND target_id = %s
                RETURNING *
                """,
                (guild_id, special_effect_tag_id, target_type, target_id),
            )
            return fetch_one(cursor)
