from typing import Any, Dict, List, Optional

from bot.repositories.base import fetch_all, fetch_one, json_dumps, normalize_reaction_kind


class MentionReactionRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_reactions(
        self,
        guild_id: str,
        enabled: Optional[bool] = None,
        reaction_kind: Optional[str] = None,
        include_system: bool = True,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        normalized_kind = normalize_reaction_kind(reaction_kind)
        if normalized_kind is not None:
            where.append("reaction_kind = %s")
            params.append(normalized_kind)

        if not include_system:
            where.append("is_system = FALSE")

        sql = """
            SELECT *
            FROM mention_reactions
            WHERE {where}
            ORDER BY sort_order ASC, LENGTH(keyword) DESC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def list_reactions_for_admin(
        self,
        guild_id: str,
        query: Optional[str] = None,
        enabled: Optional[bool] = None,
        reaction_kind: Optional[str] = None,
        is_system: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["r.guild_id = %s"]

        if query:
            like_query = "%{0}%".format(query)
            where.append(
                "(r.name ILIKE %s OR r.keyword ILIKE %s OR COALESCE(r.description, '') ILIKE %s)"
            )
            params.extend([like_query, like_query, like_query])

        if enabled is not None:
            where.append("r.enabled = %s")
            params.append(enabled)

        normalized_kind = normalize_reaction_kind(reaction_kind)
        if normalized_kind is not None:
            where.append("r.reaction_kind = %s")
            params.append(normalized_kind)

        if is_system is not None:
            where.append("r.is_system = %s")
            params.append(is_system)

        sql = """
            SELECT
                r.*,
                COUNT(c.id) AS choice_count
            FROM mention_reactions r
            LEFT JOIN mention_reaction_choices c
                ON c.guild_id = r.guild_id
                AND c.mention_reaction_id = r.id
            WHERE {where}
            GROUP BY r.id
            ORDER BY r.sort_order ASC, LENGTH(r.keyword) DESC, r.created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def get_by_id(self, guild_id: str, reaction_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mention_reactions
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, reaction_id),
            )
            return fetch_one(cursor)

    def keyword_exists(
        self,
        guild_id: str,
        keyword: str,
        exclude_reaction_id: Optional[int] = None,
    ) -> bool:
        params = [guild_id, keyword]
        where = ["guild_id = %s", "keyword = %s"]
        if exclude_reaction_id is not None:
            where.append("id <> %s")
            params.append(exclude_reaction_id)

        sql = """
            SELECT 1
            FROM mention_reactions
            WHERE {where}
            LIMIT 1
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone() is not None

    def create_reaction(
        self,
        guild_id: str,
        reaction_key: str,
        keyword: str,
        match_type: str,
        reaction_kind: str,
        name: str,
        description: str,
        admin_only: bool,
        is_system: bool,
        is_deletable: bool,
        enabled: bool,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mention_reactions (
                    guild_id,
                    reaction_key,
                    keyword,
                    match_type,
                    reaction_kind,
                    name,
                    description,
                    admin_only,
                    is_system,
                    is_deletable,
                    config_json,
                    enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::JSONB, %s)
                RETURNING *
                """,
                (
                    guild_id,
                    reaction_key,
                    keyword,
                    match_type,
                    normalize_reaction_kind(reaction_kind),
                    name,
                    description,
                    admin_only,
                    is_system,
                    is_deletable,
                    enabled,
                ),
            )
            return fetch_one(cursor)

    def update_reaction(
        self,
        guild_id: str,
        reaction_id: int,
        keyword: str,
        match_type: str,
        name: str,
        description: str,
        admin_only: bool,
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mention_reactions
                SET keyword = %s,
                    match_type = %s,
                    name = %s,
                    description = %s,
                    admin_only = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    keyword,
                    match_type,
                    name,
                    description,
                    admin_only,
                    enabled,
                    guild_id,
                    reaction_id,
                ),
            )
            return fetch_one(cursor)

    def update_search_settings(
        self,
        guild_id: str,
        reaction_id: int,
        keyword: str,
        match_type: str,
        description: str,
        enabled: bool,
        config_json: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mention_reactions
                SET keyword = %s,
                    match_type = %s,
                    description = %s,
                    enabled = %s,
                    config_json = %s::JSONB,
                    updated_at = NOW()
                WHERE guild_id = %s
                  AND id = %s
                  AND reaction_kind = 'search'
                RETURNING *
                """,
                (
                    keyword,
                    match_type,
                    description,
                    enabled,
                    json_dumps(config_json),
                    guild_id,
                    reaction_id,
                ),
            )
            return fetch_one(cursor)

    def ensure_deck_search_reaction(
        self,
        guild_id: str,
        enabled: bool = False,
    ) -> Dict[str, Any]:
        existing = self.get_by_key(guild_id, "deck_search")
        if existing is not None:
            return existing

        config = {
            "search_type": "deck_search",
            "allowed_channel_ids": [],
            "max_results": 3,
            "x_search_max_results": 100,
            "deny_message": "このチャンネルではデッキ検索は使えません。",
            "not_found_message": "おい ないんだが",
            "missing_format_behavior": "ask_format",
            "x_query_template": "({class_label} OR {class_en}) (シャドバ OR Shadowverse OR シャドウバース OR SV) (デッキ OR deck OR QR OR コード) has:images",
            "search_mode": "full_archive",
            "lookback_days": 14,
            "excluded_keywords": ["ドラゴンボール", "レジェンズ", "探索コード", "フレンドコード"],
            "include_retweets": False,
            "include_replies": False,
            "image_scan_limit": 80,
            "image_scan_concurrency": 5,
            "stop_after_candidates": True,
            "image_fetch_timeout_seconds": 5,
            "high_accuracy_enabled": True,
            "high_accuracy_image_scan_limit": 100,
            "high_accuracy_image_scan_concurrency": 1,
            "high_accuracy_stop_after_candidates": False,
            "request_timeout_seconds": 10,
            "cache_ttl_seconds": 300,
            "result_format": "default",
            "class_filter_required": True,
        }
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mention_reactions (
                    guild_id,
                    reaction_key,
                    keyword,
                    match_type,
                    reaction_kind,
                    name,
                    description,
                    admin_only,
                    is_system,
                    is_deletable,
                    config_json,
                    enabled
                )
                VALUES (%s, 'deck_search', 'デッキ検索', 'prefix', 'search', 'デッキ検索', %s, FALSE, TRUE, FALSE, %s::JSONB, %s)
                RETURNING *
                """,
                (
                    guild_id,
                    "デッキ検索の固定検索型メンション反応です。",
                    json_dumps(config),
                    enabled,
                ),
            )
            return fetch_one(cursor)

    def set_enabled(
        self,
        guild_id: str,
        reaction_id: int,
        enabled: bool,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mention_reactions
                SET enabled = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (enabled, guild_id, reaction_id),
            )
            return fetch_one(cursor)

    def toggle_enabled(
        self,
        guild_id: str,
        reaction_id: int,
    ) -> Optional[Dict[str, Any]]:
        reaction = self.get_by_id(guild_id, reaction_id)
        if reaction is None:
            return None
        return self.set_enabled(guild_id, reaction_id, not bool(reaction["enabled"]))

    def get_by_key(self, guild_id: str, reaction_key: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mention_reactions
                WHERE guild_id = %s AND reaction_key = %s
                """,
                (guild_id, reaction_key),
            )
            return fetch_one(cursor)

    def find_keyword_matches(
        self,
        guild_id: str,
        content: str,
        enabled: Optional[bool] = True,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, content, content, content, content]
        where = [
            "guild_id = %s",
            "((match_type = 'exact' AND %s = keyword) "
            "OR (match_type = 'prefix' AND POSITION(keyword IN %s) = 1) "
            "OR (match_type = 'contains' AND POSITION(keyword IN %s) > 0) "
            "OR (match_type = 'regex' AND %s ~ keyword))",
        ]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        sql = """
            SELECT *
            FROM mention_reactions
            WHERE {where}
            ORDER BY LENGTH(keyword) DESC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def search_by_keyword(
        self,
        guild_id: str,
        keyword: str,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, "%{0}%".format(keyword)]
        where = ["guild_id = %s", "keyword ILIKE %s"]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        sql = """
            SELECT *
            FROM mention_reactions
            WHERE {where}
            ORDER BY LENGTH(keyword) DESC, created_at ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def list_choices(
        self,
        guild_id: str,
        mention_reaction_id: int,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id, mention_reaction_id]
        where = ["guild_id = %s", "mention_reaction_id = %s"]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        sql = """
            SELECT *
            FROM mention_reaction_choices
            WHERE {where}
            ORDER BY sort_order ASC, id ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def get_choice(self, guild_id: str, choice_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM mention_reaction_choices
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, choice_id),
            )
            return fetch_one(cursor)

    def create_choice(
        self,
        guild_id: str,
        mention_reaction_id: int,
        name: str,
        body: Optional[str],
        image_path: Optional[str],
        appearance_rate: int,
        enabled: bool,
        result_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mention_reaction_choices (
                    guild_id,
                    mention_reaction_id,
                    name,
                    body,
                    image_path,
                    appearance_rate,
                    enabled,
                    result_label
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    guild_id,
                    mention_reaction_id,
                    name,
                    body,
                    image_path,
                    appearance_rate,
                    enabled,
                    result_label,
                ),
            )
            return fetch_one(cursor)

    def update_choice(
        self,
        guild_id: str,
        choice_id: int,
        name: str,
        body: Optional[str],
        image_path: Optional[str],
        appearance_rate: int,
        enabled: bool,
        result_label: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE mention_reaction_choices
                SET name = %s,
                    body = %s,
                    image_path = %s,
                    appearance_rate = %s,
                    enabled = %s,
                    result_label = %s,
                    updated_at = NOW()
                WHERE guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    name,
                    body,
                    image_path,
                    appearance_rate,
                    enabled,
                    result_label,
                    guild_id,
                    choice_id,
                ),
            )
            return fetch_one(cursor)

    def delete_reaction(self, guild_id: str, reaction_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM special_effect_assignments
                WHERE guild_id = %s
                  AND target_type = 'mention_reaction_choice'
                  AND target_id IN (
                      SELECT id
                      FROM mention_reaction_choices
                      WHERE guild_id = %s AND mention_reaction_id = %s
                  )
                """,
                (guild_id, guild_id, reaction_id),
            )
            cursor.execute(
                """
                DELETE FROM mention_reactions
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, reaction_id),
            )
            return cursor.rowcount > 0

    def delete_choice(self, guild_id: str, choice_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM special_effect_assignments
                WHERE guild_id = %s
                  AND target_type = 'mention_reaction_choice'
                  AND target_id = %s
                """,
                (guild_id, choice_id),
            )
            cursor.execute(
                """
                DELETE FROM mention_reaction_choices
                WHERE guild_id = %s AND id = %s
                """,
                (guild_id, choice_id),
            )
            return cursor.rowcount > 0

    def list_search_handlers(
        self,
        guild_id: str,
        enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        params = [guild_id]
        where = ["guild_id = %s"]

        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)

        sql = """
            SELECT *
            FROM mention_search_handlers
            WHERE {where}
            ORDER BY handler_key ASC
        """.format(where=" AND ".join(where))

        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)
