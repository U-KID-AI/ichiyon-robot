from datetime import datetime, timezone
from typing import Any, Dict, Optional
import re
import uuid

from bot import config
from bot.repositories.base import fetch_one


MINECRAFT_PLAYER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,16}$")


def is_valid_minecraft_player_name(value: str) -> bool:
    return bool(MINECRAFT_PLAYER_NAME_PATTERN.fullmatch(str(value or "")))


class MinecraftBridgeRepository:
    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.connection = connection
        self.bot_id = bot_id or config.BOT_INSTANCE_ID

    def get_player_link(self, guild_id: str, discord_user_id: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM minecraft_player_links
                WHERE bot_id = %s AND guild_id = %s AND discord_user_id = %s
                """,
                (self.bot_id, guild_id, discord_user_id),
            )
            return fetch_one(cursor)

    def upsert_player_link(self, guild_id: str, discord_user_id: str, minecraft_player_name: str) -> Dict[str, Any]:
        if not is_valid_minecraft_player_name(minecraft_player_name):
            raise ValueError("invalid minecraft player name")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO minecraft_player_links (
                    bot_id, guild_id, discord_user_id, minecraft_player_name
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (bot_id, guild_id, discord_user_id) DO UPDATE
                SET minecraft_player_name = EXCLUDED.minecraft_player_name,
                    updated_at = NOW()
                RETURNING *
                """,
                (self.bot_id, guild_id, discord_user_id, minecraft_player_name),
            )
            return fetch_one(cursor)

    def enqueue_narita_carpet(
        self,
        *,
        guild_id: str,
        discord_channel_id: str,
        discord_message_id: str,
        requester_discord_user_id: str,
        target_discord_user_id: str,
        minecraft_player_name: str,
        timeout_seconds: int,
    ) -> Dict[str, Any]:
        if not is_valid_minecraft_player_name(minecraft_player_name):
            raise ValueError("invalid minecraft player name")
        request_id = uuid.uuid4()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO minecraft_command_queue (
                    request_id, bot_id, guild_id, discord_channel_id, discord_message_id,
                    requester_discord_user_id, target_discord_user_id,
                    command_type, minecraft_player_name, expires_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    'narita_carpet', %s, NOW() + (%s || ' seconds')::INTERVAL
                )
                RETURNING *
                """,
                (
                    request_id,
                    self.bot_id,
                    guild_id,
                    discord_channel_id,
                    discord_message_id,
                    requester_discord_user_id,
                    target_discord_user_id,
                    minecraft_player_name,
                    int(timeout_seconds),
                ),
            )
            return fetch_one(cursor)

    def claim_next_pending(self, *, bot_id: str, guild_id: str) -> Optional[Dict[str, Any]]:
        claim_token = str(uuid.uuid4())
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                WITH next_command AS (
                    SELECT id
                    FROM minecraft_command_queue
                    WHERE bot_id = %s
                      AND guild_id = %s
                      AND status = 'pending'
                      AND expires_at > NOW()
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE minecraft_command_queue q
                SET status = 'claimed',
                    claim_token = %s,
                    attempt_count = attempt_count + 1,
                    claimed_at = NOW(),
                    updated_at = NOW()
                FROM next_command
                WHERE q.id = next_command.id
                RETURNING q.*
                """,
                (bot_id, guild_id, claim_token),
            )
            return fetch_one(cursor)

    def mark_result(
        self,
        *,
        request_id: str,
        status: str,
        reason: str,
        message: str = "",
    ) -> Optional[Dict[str, Any]]:
        if status not in ("succeeded", "failed"):
            raise ValueError("invalid minecraft command result status")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE minecraft_command_queue
                SET status = %s,
                    result_reason = %s,
                    result_message = %s,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE request_id = %s
                  AND status IN ('pending', 'claimed')
                RETURNING *
                """,
                (status, reason[:120], message[:300], request_id),
            )
            return fetch_one(cursor)

    def get_command_by_request_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM minecraft_command_queue
                WHERE request_id = %s
                """,
                (request_id,),
            )
            return fetch_one(cursor)

    def fail_expired(self) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE minecraft_command_queue
                SET status = 'failed',
                    result_reason = 'timeout',
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE status IN ('pending', 'claimed')
                  AND expires_at <= NOW()
                """,
            )
            return int(cursor.rowcount or 0)


def command_completed(row: Optional[Dict[str, Any]]) -> bool:
    return bool(row and row.get("status") in ("succeeded", "failed"))
