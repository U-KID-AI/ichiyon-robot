from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import unicodedata

from bot import config
from bot.repositories.base import fetch_all, fetch_one


class YouTubeNPullRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def list_presets(self, guild_id: str, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        params: List[Any] = [self.bot_id, guild_id]
        where = ["bot_id = %s", "guild_id = %s"]
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.*,
                       (SELECT COUNT(*) FROM youtube_n_pull_sources s WHERE s.preset_id = p.id) AS source_count,
                       (SELECT COUNT(*) FROM youtube_n_pull_cache_videos c WHERE c.preset_id = p.id) AS cache_count
                FROM youtube_n_pull_presets p
                WHERE {where}
                ORDER BY enabled DESC, category ASC, display_name ASC, id ASC
                """.format(where=" AND ".join(where)),
                params,
            )
            return fetch_all(cursor)

    def get_preset(self, guild_id: str, preset_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM youtube_n_pull_presets
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, preset_id),
            )
            return fetch_one(cursor)

    def find_preset_by_command(self, guild_id: str, command_name: str) -> Optional[Dict[str, Any]]:
        command_key = normalize_command_name(command_name)
        for preset in self.list_presets(guild_id, enabled=True):
            if preset.get("command_key") == command_key:
                return preset
            aliases = split_lines(preset.get("aliases") or "")
            if any(normalize_command_name(alias) == command_key for alias in aliases):
                return preset
        return None

    def create_preset(self, guild_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO youtube_n_pull_presets (
                    bot_id, guild_id, display_name, command_name, command_key, aliases, category,
                    enabled, max_pulls, cache_ttl_seconds, include_shorts, include_live,
                    include_archived_live, min_duration_seconds, max_duration_seconds,
                    include_title_terms, exclude_title_terms
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                RETURNING *
                """,
                (
                    self.bot_id,
                    guild_id,
                    values["display_name"],
                    values["command_name"],
                    normalize_command_name(values["command_name"]),
                    values["aliases"],
                    values["category"],
                    values["enabled"],
                    values["max_pulls"],
                    values["cache_ttl_seconds"],
                    values["include_shorts"],
                    values["include_live"],
                    values["include_archived_live"],
                    values["min_duration_seconds"],
                    values["max_duration_seconds"],
                    values["include_title_terms"],
                    values["exclude_title_terms"],
                ),
            )
            return fetch_one(cursor)

    def update_preset(self, guild_id: str, preset_id: int, values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE youtube_n_pull_presets
                SET display_name = %s,
                    command_name = %s,
                    command_key = %s,
                    aliases = %s,
                    category = %s,
                    enabled = %s,
                    max_pulls = %s,
                    cache_ttl_seconds = %s,
                    include_shorts = %s,
                    include_live = %s,
                    include_archived_live = %s,
                    min_duration_seconds = %s,
                    max_duration_seconds = %s,
                    include_title_terms = %s,
                    exclude_title_terms = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (
                    values["display_name"],
                    values["command_name"],
                    normalize_command_name(values["command_name"]),
                    values["aliases"],
                    values["category"],
                    values["enabled"],
                    values["max_pulls"],
                    values["cache_ttl_seconds"],
                    values["include_shorts"],
                    values["include_live"],
                    values["include_archived_live"],
                    values["min_duration_seconds"],
                    values["max_duration_seconds"],
                    values["include_title_terms"],
                    values["exclude_title_terms"],
                    self.bot_id,
                    guild_id,
                    preset_id,
                ),
            )
            return fetch_one(cursor)

    def toggle_preset(self, guild_id: str, preset_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE youtube_n_pull_presets
                SET enabled = NOT enabled,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                RETURNING *
                """,
                (self.bot_id, guild_id, preset_id),
            )
            return fetch_one(cursor)

    def delete_preset(self, guild_id: str, preset_id: int) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM youtube_n_pull_presets
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (self.bot_id, guild_id, preset_id),
            )
            return cursor.rowcount > 0

    def list_sources(self, preset_id: int, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        params: List[Any] = [preset_id]
        where = ["preset_id = %s"]
        if enabled is not None:
            where.append("enabled = %s")
            params.append(enabled)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM youtube_n_pull_sources
                WHERE {where}
                ORDER BY enabled DESC, priority ASC, id ASC
                """.format(where=" AND ".join(where)),
                params,
            )
            return fetch_all(cursor)

    def replace_sources(self, preset_id: int, sources: List[Dict[str, Any]]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM youtube_n_pull_sources WHERE preset_id = %s", (preset_id,))
            for source in sources:
                cursor.execute(
                    """
                    INSERT INTO youtube_n_pull_sources (
                        preset_id, source_type, source_url, priority, enabled
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        preset_id,
                        source["source_type"],
                        source["source_url"],
                        source["priority"],
                        source["enabled"],
                    ),
                )

    def list_cached_videos(self, preset_id: int) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM youtube_n_pull_cache_videos
                WHERE preset_id = %s
                ORDER BY cached_at DESC, id ASC
                """,
                (preset_id,),
            )
            return fetch_all(cursor)

    def list_cache_preview(self, preset_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM youtube_n_pull_cache_videos
                WHERE preset_id = %s
                ORDER BY cached_at DESC, title ASC
                LIMIT %s
                """,
                (preset_id, limit),
            )
            return fetch_all(cursor)

    def replace_cache_videos(self, preset_id: int, videos: List[Dict[str, Any]]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM youtube_n_pull_cache_videos WHERE preset_id = %s", (preset_id,))
            for video in videos:
                cursor.execute(
                    """
                    INSERT INTO youtube_n_pull_cache_videos (
                        preset_id, source_id, video_id, canonical_url, title,
                        duration_seconds, live_status, published_at, cached_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (preset_id, video_id) DO UPDATE
                    SET source_id = EXCLUDED.source_id,
                        canonical_url = EXCLUDED.canonical_url,
                        title = EXCLUDED.title,
                        duration_seconds = EXCLUDED.duration_seconds,
                        live_status = EXCLUDED.live_status,
                        published_at = EXCLUDED.published_at,
                        cached_at = NOW(),
                        updated_at = NOW()
                    """,
                    (
                        preset_id,
                        video.get("source_id"),
                        video["video_id"],
                        video["canonical_url"],
                        video["title"],
                        video.get("duration_seconds"),
                        video.get("live_status") or "",
                        video.get("published_at"),
                    ),
                )

    def mark_cache_refresh(self, preset_id: int, error: str = "") -> None:
        if error:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE youtube_n_pull_presets
                    SET last_cache_error = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (error[:1000], preset_id),
                )
            return
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE youtube_n_pull_presets
                SET last_cache_refresh_at = NOW(),
                    last_cache_error = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (error[:1000], preset_id),
            )


def normalize_command_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.strip().casefold().split())


def split_lines(value: str) -> List[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def cache_is_fresh(preset: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    refreshed_at = preset.get("last_cache_refresh_at")
    ttl = int(preset.get("cache_ttl_seconds") or 86400)
    if refreshed_at is None:
        return False
    if refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - refreshed_at).total_seconds() < ttl
