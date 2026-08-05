from typing import Any, Dict, List, Optional

from bot import config
from bot.repositories.base import fetch_all, fetch_one


class GameRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def upsert_game(self, values: Dict[str, Any]) -> Dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO games (
                    provider, provider_game_id, title, store_url, release_date, platforms,
                    last_known_price, last_known_regular_price, last_known_discount_percent,
                    currency, historical_low, metadata_json, fetched_at
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                ON CONFLICT (provider, provider_game_id) DO UPDATE
                SET title = EXCLUDED.title,
                    store_url = EXCLUDED.store_url,
                    release_date = EXCLUDED.release_date,
                    platforms = EXCLUDED.platforms,
                    last_known_price = EXCLUDED.last_known_price,
                    last_known_regular_price = EXCLUDED.last_known_regular_price,
                    last_known_discount_percent = EXCLUDED.last_known_discount_percent,
                    currency = EXCLUDED.currency,
                    historical_low = EXCLUDED.historical_low,
                    metadata_json = EXCLUDED.metadata_json,
                    fetched_at = NOW(),
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    values["provider"],
                    values["provider_game_id"],
                    values["title"],
                    values.get("store_url", ""),
                    values.get("release_date", ""),
                    values.get("platforms_json", "{}"),
                    values.get("last_known_price"),
                    values.get("last_known_regular_price"),
                    values.get("last_known_discount_percent"),
                    values.get("currency", ""),
                    values.get("historical_low"),
                    values.get("metadata_json", "{}"),
                ),
            )
            return fetch_one(cursor)

    def get_game(self, game_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM games WHERE id = %s", (game_id,))
            return fetch_one(cursor)

    def list_games(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT g.*,
                       (SELECT COUNT(*) FROM user_game_entries e WHERE e.game_id = g.id) AS user_count
                FROM games g
                ORDER BY fetched_at DESC NULLS LAST, updated_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return fetch_all(cursor)

    def upsert_user_entry(
        self,
        guild_id: str,
        discord_user_id: str,
        game_id: int,
        owned: Optional[bool] = None,
        wishlist: Optional[bool] = None,
        backlog: Optional[bool] = None,
    ) -> Dict[str, Any]:
        current = self.get_user_entry(guild_id, discord_user_id, game_id) or {}
        values = {
            "owned": bool(current.get("owned", False) if owned is None else owned),
            "wishlist": bool(current.get("wishlist", False) if wishlist is None else wishlist),
            "backlog": bool(current.get("backlog", False) if backlog is None else backlog),
        }
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_game_entries (
                    bot_id, guild_id, discord_user_id, game_id, owned, wishlist, backlog
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bot_id, guild_id, discord_user_id, game_id) DO UPDATE
                SET owned = EXCLUDED.owned,
                    wishlist = EXCLUDED.wishlist,
                    backlog = EXCLUDED.backlog,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    self.bot_id,
                    guild_id,
                    discord_user_id,
                    game_id,
                    values["owned"],
                    values["wishlist"],
                    values["backlog"],
                ),
            )
            return fetch_one(cursor)

    def get_user_entry(self, guild_id: str, discord_user_id: str, game_id: int) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM user_game_entries
                WHERE bot_id = %s AND guild_id = %s AND discord_user_id = %s AND game_id = %s
                """,
                (self.bot_id, guild_id, discord_user_id, game_id),
            )
            return fetch_one(cursor)

    def list_user_entries(self, guild_id: str, discord_user_id: str, flag: str, limit: int = 10) -> List[Dict[str, Any]]:
        if flag not in ("owned", "wishlist", "backlog"):
            raise ValueError("invalid game entry flag")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT e.*, g.title, g.store_url, g.last_known_price, g.currency
                FROM user_game_entries e
                JOIN games g ON g.id = e.game_id
                WHERE e.bot_id = %s AND e.guild_id = %s AND e.discord_user_id = %s AND e.{flag} = TRUE
                ORDER BY e.updated_at DESC, e.id DESC
                LIMIT %s
                """.format(flag=flag),
                (self.bot_id, guild_id, discord_user_id, limit),
            )
            return fetch_all(cursor)

    def add_search_history(self, guild_id: str, discord_user_id: str, query: str, game_id: Optional[int]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO game_search_history (bot_id, guild_id, discord_user_id, query, game_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (self.bot_id, guild_id, discord_user_id, query, game_id),
            )

    def list_recent_searches(self, guild_id: str, discord_user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT h.*, g.title, g.store_url
                FROM game_search_history h
                LEFT JOIN games g ON g.id = h.game_id
                WHERE h.bot_id = %s AND h.guild_id = %s AND h.discord_user_id = %s
                ORDER BY h.searched_at DESC
                LIMIT %s
                """,
                (self.bot_id, guild_id, discord_user_id, limit),
            )
            return fetch_all(cursor)
