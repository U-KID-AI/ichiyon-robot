from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, normalize_effect_target_type


class SpecialEffectRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_tags(
        self,
        guild_id: str,
        enabled: Optional[bool] = None,
        admin_only: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

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
