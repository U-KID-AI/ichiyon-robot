import asyncio
import re
from typing import Optional

import discord

from bot import config
from bot.db import get_connection
from bot.repositories.minecraft_bridge import (
    MinecraftBridgeRepository,
    command_completed,
    is_valid_minecraft_player_name,
)
from bot.services.runtime_db import get_message_guild_id


MINECRAFT_COMMAND_PREFIX = "マイクラ"
NARITA_CARPET_COMMAND = "成田カーペット"
NARITA_CARPET_STRUCTURE_ID = "mystructure:narita_map_item"
NARITA_CARPET_USAGE = "使い方: @いちよんロボ マイクラ 成田カーペット <Minecraft名>"
_NARITA_PREFIX_RE = re.compile(r"^\s*マイクラ[\s\u3000]+成田カーペット(?:[\s\u3000]+(?P<arg>\S+))?\s*$")
_NARITA_COMMAND_OWNED_RE = re.compile(r"^\s*マイクラ[\s\u3000]+成田カーペット(?:[\s\u3000]|$)")


def parse_narita_carpet_player_name(command_text: Optional[str]):
    text = str(command_text or "")
    match = _NARITA_PREFIX_RE.fullmatch(text)
    if match is None:
        return None, bool(_NARITA_COMMAND_OWNED_RE.match(text))
    player_name = str(match.group("arg") or "").strip()
    if not player_name or not is_valid_minecraft_player_name(player_name):
        return None, True
    return player_name, True


async def handle_minecraft_command(message: discord.Message, command_text: Optional[str]) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False
    minecraft_player_name, is_narita_command = parse_narita_carpet_player_name(command_text)
    if not is_narita_command:
        return False
    if minecraft_player_name is None:
        await message.channel.send(NARITA_CARPET_USAGE)
        return True

    try:
        with get_connection() as connection:
            repository = MinecraftBridgeRepository(connection, bot_id=config.BOT_INSTANCE_ID)
            row = repository.enqueue_narita_carpet(
                guild_id=guild_id,
                discord_channel_id=str(getattr(message.channel, "id", "") or ""),
                discord_message_id=str(getattr(message, "id", "") or ""),
                requester_discord_user_id=str(getattr(message.author, "id", "") or ""),
                target_discord_user_id="",
                minecraft_player_name=minecraft_player_name,
                timeout_seconds=config.MINECRAFT_COMMAND_TIMEOUT_SECONDS,
            )
            connection.commit()
    except Exception as exc:
        print(
            "[WARN] minecraft command enqueue failed: bot_instance_id={0} guild_id={1} error={2}".format(
                config.BOT_INSTANCE_ID,
                guild_id,
                type(exc).__name__,
            )
        )
        await message.channel.send("Minecraftサーバーとの通信準備に失敗しました。")
        return True

    print(
        "[INFO] minecraft command queued: bot_instance_id={0} guild_id={1} request_id={2} type=narita_carpet minecraft_player={3}".format(
            config.BOT_INSTANCE_ID,
            guild_id,
            row.get("request_id"),
            minecraft_player_name,
        )
    )
    result = await wait_for_minecraft_result(str(row["request_id"]), config.MINECRAFT_COMMAND_TIMEOUT_SECONDS)
    if result is None:
        await message.channel.send("Minecraftサーバーとの通信がタイムアウトしました。")
        return True
    if result.get("status") == "succeeded":
        await message.channel.send("{0} に成田カーペットを送り付けました。".format(minecraft_player_name))
        return True
    await message.channel.send(_result_error_message(minecraft_player_name, str(result.get("result_reason") or "")))
    return True


async def wait_for_minecraft_result(request_id: str, timeout_seconds: int):
    deadline = asyncio.get_running_loop().time() + max(1, int(timeout_seconds))
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.5)
        try:
            with get_connection() as connection:
                repository = MinecraftBridgeRepository(connection, bot_id=config.BOT_INSTANCE_ID)
                repository.fail_expired()
                row = repository.get_command_by_request_id(request_id)
                connection.commit()
                if command_completed(row):
                    return row
        except Exception as exc:
            print(
                "[WARN] minecraft command result polling failed: request_id={0} error={1}".format(
                    request_id,
                    type(exc).__name__,
                )
            )
            return None
    return None


def _result_error_message(minecraft_player_name: str, reason: str) -> str:
    if reason == "player_offline":
        return "{0} は現在Minecraftにいません。".format(minecraft_player_name)
    if reason in ("unknown_command_type", "invalid_player_name"):
        return "Minecraftコマンドの内容が不正です。"
    if reason in ("command_failed", "structure_load_failed"):
        return "成田カーペットの生成に失敗しました。"
    return "Minecraftサーバーとの通信に失敗しました。"
