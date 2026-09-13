import asyncio
import re
from typing import Optional

import discord

from bot import config
from bot import messages
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
_NARITA_COMMAND_RE = re.compile(
    r"^\s*マイクラ[\s\u3000]+成田カーペット[\s\u3000]+<@!?(\d{15,25})>\s*$"
)


def parse_narita_carpet_target_id(command_text: Optional[str], mentions, bot_user) -> Optional[str]:
    text = str(command_text or "")
    match = _NARITA_COMMAND_RE.fullmatch(text)
    if match is None:
        return None
    target_id = match.group(1)
    bot_id = str(getattr(bot_user, "id", "") or "")
    if target_id == bot_id:
        return None
    mentioned_user_ids = {
        str(getattr(user, "id", "") or "")
        for user in mentions
        if str(getattr(user, "id", "") or "") != bot_id
    }
    if target_id not in mentioned_user_ids:
        return None
    return target_id


async def handle_minecraft_command(message: discord.Message, command_text: Optional[str]) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False
    target_discord_user_id = parse_narita_carpet_target_id(command_text, getattr(message, "mentions", []), messages.get_bot().user)
    if target_discord_user_id is None:
        return False

    target_user = _find_mentioned_user(message, target_discord_user_id)
    if target_user is None:
        return False

    try:
        with get_connection() as connection:
            repository = MinecraftBridgeRepository(connection, bot_id=config.BOT_INSTANCE_ID)
            link = repository.get_player_link(guild_id, target_discord_user_id)
            if link is None:
                await message.channel.send(
                    "{0} のMinecraftユーザー名が登録されていません。".format(target_user.mention),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
                return True
            minecraft_player_name = str(link.get("minecraft_player_name") or "")
            if not is_valid_minecraft_player_name(minecraft_player_name):
                await message.channel.send(
                    "{0} のMinecraftユーザー名が不正です。".format(target_user.mention),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
                return True
            row = repository.enqueue_narita_carpet(
                guild_id=guild_id,
                discord_channel_id=str(getattr(message.channel, "id", "") or ""),
                discord_message_id=str(getattr(message, "id", "") or ""),
                requester_discord_user_id=str(getattr(message.author, "id", "") or ""),
                target_discord_user_id=target_discord_user_id,
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
        "[INFO] minecraft command queued: bot_instance_id={0} guild_id={1} request_id={2} type=narita_carpet target_discord_user_id={3}".format(
            config.BOT_INSTANCE_ID,
            guild_id,
            row.get("request_id"),
            target_discord_user_id,
        )
    )
    result = await wait_for_minecraft_result(str(row["request_id"]), config.MINECRAFT_COMMAND_TIMEOUT_SECONDS)
    if result is None:
        await message.channel.send("Minecraftサーバーとの通信がタイムアウトしました。")
        return True
    if result.get("status") == "succeeded":
        await message.channel.send(
            "{0} に成田カーペットを送り付けました。".format(target_user.mention),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        return True
    await message.channel.send(
        _result_error_message(target_user, str(result.get("result_reason") or "")),
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
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


def _result_error_message(target_user, reason: str) -> str:
    if reason == "player_offline":
        return "{0} は現在Minecraftにいません。".format(target_user.mention)
    if reason in ("unknown_command_type", "invalid_player_name"):
        return "Minecraftコマンドの内容が不正です。"
    if reason in ("command_failed", "structure_load_failed"):
        return "成田カーペットの生成に失敗しました。"
    return "Minecraftサーバーとの通信に失敗しました。"


def _find_mentioned_user(message, user_id: str):
    for user in getattr(message, "mentions", []):
        if str(getattr(user, "id", "") or "") == str(user_id):
            return user
    return None
