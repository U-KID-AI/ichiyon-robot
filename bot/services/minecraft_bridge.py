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
STRUCTURE_BLOCK_COMMAND = "ストラクチャーブロック"
COMMAND_BLOCK_COMMAND = "コマンドブロック"
MINECRAFT_COMMAND_USAGE = (
    "使い方: @いちよんロボ マイクラ 成田カーペット <Minecraft名> / "
    "ストラクチャーブロック <Minecraft名> / コマンドブロック <Minecraft名>"
)
_COMMAND_TYPES_BY_TEXT = {
    NARITA_CARPET_COMMAND: "narita_carpet",
    STRUCTURE_BLOCK_COMMAND: "structure_block",
    COMMAND_BLOCK_COMMAND: "command_block",
}
_COMMAND_RE = re.compile(
    r"^\s*マイクラ[\s\u3000]+(?P<subcommand>成田カーペット|ストラクチャーブロック|コマンドブロック)(?:[\s\u3000]+(?P<arg>\S+))?\s*$"
)
_OWNED_COMMAND_RE = re.compile(
    r"^\s*マイクラ[\s\u3000]+(?P<subcommand>成田カーペット|ストラクチャーブロック|コマンドブロック)(?:[\s\u3000]|$)"
)
_SUCCESS_MESSAGES = {
    "narita_carpet": "{player} に成田カーペットを送り付けました。",
    "structure_block": "{player} にストラクチャーブロックを送り付けました。",
    "command_block": "{player} にコマンドブロックを送り付けました。",
}


def parse_minecraft_command(command_text: Optional[str]):
    text = str(command_text or "")
    match = _COMMAND_RE.fullmatch(text)
    if match is None:
        return None, None, bool(_OWNED_COMMAND_RE.match(text))
    command_type = _COMMAND_TYPES_BY_TEXT[match.group("subcommand")]
    player_name = str(match.group("arg") or "").strip()
    if not player_name or not is_valid_minecraft_player_name(player_name):
        return command_type, None, True
    return command_type, player_name, True


async def handle_minecraft_command(message: discord.Message, command_text: Optional[str]) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False
    command_type, minecraft_player_name, is_minecraft_command = parse_minecraft_command(command_text)
    if not is_minecraft_command:
        return False
    if command_type is None or minecraft_player_name is None:
        await message.channel.send(MINECRAFT_COMMAND_USAGE)
        return True

    try:
        with get_connection() as connection:
            repository = MinecraftBridgeRepository(connection, bot_id=config.BOT_INSTANCE_ID)
            row = repository.enqueue_command(
                guild_id=guild_id,
                discord_channel_id=str(getattr(message.channel, "id", "") or ""),
                discord_message_id=str(getattr(message, "id", "") or ""),
                requester_discord_user_id=str(getattr(message.author, "id", "") or ""),
                target_discord_user_id="",
                command_type=command_type,
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
        "[INFO] minecraft command queued: bot_instance_id={0} guild_id={1} request_id={2} type={3} minecraft_player={4}".format(
            config.BOT_INSTANCE_ID,
            guild_id,
            row.get("request_id"),
            command_type,
            minecraft_player_name,
        )
    )
    result = await wait_for_minecraft_result(str(row["request_id"]), config.MINECRAFT_COMMAND_TIMEOUT_SECONDS)
    if result is None:
        await message.channel.send("Minecraftサーバーとの通信がタイムアウトしました。")
        return True
    if result.get("status") == "succeeded":
        await message.channel.send(_SUCCESS_MESSAGES[command_type].format(player=minecraft_player_name))
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
    if reason == "inventory_full":
        return "{0} のインベントリに空きがありません。".format(minecraft_player_name)
    if reason in ("unknown_command_type", "invalid_player_name"):
        return "Minecraftコマンドの内容が不正です。"
    if reason in ("inventory_unavailable", "item_add_failed"):
        return "{0} へアイテムを渡せませんでした。".format(minecraft_player_name)
    if reason in ("command_failed", "structure_load_failed"):
        return "成田カーペットの生成に失敗しました。"
    return "Minecraftサーバーとの通信に失敗しました。"
