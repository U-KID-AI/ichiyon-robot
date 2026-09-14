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
from bot.repositories.permissions import PermissionRepository
from bot.services.minecraft_control import (
    MinecraftControlError,
    control_api_configured,
    fetch_control_status,
    format_control_status,
    format_restart_result,
    request_control_restart,
)
from bot.services.runtime_db import get_message_guild_id


MINECRAFT_COMMAND_PREFIX = "マイクラ"
NARITA_CARPET_COMMAND = "成田カーペット"
NARITA_CARPET_STRUCTURE_ID = "mystructure:narita_map_item"
STRUCTURE_BLOCK_COMMAND = "ストラクチャーブロック"
COMMAND_BLOCK_COMMAND = "コマンドブロック"
TAKETUMI_EGG_COMMAND = "タケツミエッグ"
E_SEIMONJI_COMMAND = "Eの聖文字"
HELD_ITEM_INSPECT_COMMAND = "手持ち確認"
SERVER_STATUS_COMMAND = "状態"
SERVER_RESTART_COMMAND = "再起動"
SERVER_STATUS_PLAYER_PLACEHOLDER = "ServerStatus"
_MINECRAFT_ITEM_COMMANDS = {
    STRUCTURE_BLOCK_COMMAND: {
        "type": "structure_block",
        "label": "ストラクチャーブロック",
    },
    COMMAND_BLOCK_COMMAND: {
        "type": "command_block",
        "label": "コマンドブロック",
    },
    "バリアブロック": {
        "type": "barrier_block",
        "label": "バリアブロック",
    },
    "ライトブロック": {
        "type": "light_block",
        "label": "ライトブロック",
    },
    "ジグソーブロック": {
        "type": "jigsaw_block",
        "label": "ジグソーブロック",
    },
    "ストラクチャーヴォイド": {
        "type": "structure_void",
        "label": "ストラクチャーヴォイド",
    },
    "リピートコマンドブロック": {
        "type": "repeating_command_block",
        "label": "リピートコマンドブロック",
    },
    "チェーンコマンドブロック": {
        "type": "chain_command_block",
        "label": "チェーンコマンドブロック",
    },
    TAKETUMI_EGG_COMMAND: {
        "type": "taketumi_spawn_egg",
        "label": "タケツミエッグ",
    },
}
_UNAVAILABLE_COMMAND_MESSAGES = {
    E_SEIMONJI_COMMAND: "Eの聖文字のMinecraft item identifierを特定できないため実行できません。",
}
_COMMAND_TYPES_BY_TEXT = {
    NARITA_CARPET_COMMAND: "narita_carpet",
    HELD_ITEM_INSPECT_COMMAND: "held_item_inspect",
    **{text: spec["type"] for text, spec in _MINECRAFT_ITEM_COMMANDS.items()},
}
_PLAYERLESS_COMMAND_TYPES_BY_TEXT = {
    SERVER_STATUS_COMMAND: "server_status",
    SERVER_RESTART_COMMAND: "server_restart",
}
_SUCCESS_MESSAGES = {
    "narita_carpet": "{player} に成田カーペットを送り付けました。",
    **{
        spec["type"]: "{player} に" + spec["label"] + "を送り付けました。"
        for spec in _MINECRAFT_ITEM_COMMANDS.values()
    },
}
_SUPPORTED_SUBCOMMAND_PATTERN = "|".join(
    re.escape(name) for name in [*_COMMAND_TYPES_BY_TEXT, *_PLAYERLESS_COMMAND_TYPES_BY_TEXT]
)
_UNAVAILABLE_SUBCOMMAND_PATTERN = "|".join(re.escape(name) for name in _UNAVAILABLE_COMMAND_MESSAGES)
MINECRAFT_COMMAND_USAGE = (
    "使い方: @いちよんロボ マイクラ 成田カーペット <Minecraft名> / "
    "ストラクチャーブロック <Minecraft名> / コマンドブロック <Minecraft名> / "
    "バリアブロック <Minecraft名> / ライトブロック <Minecraft名> / "
    "ジグソーブロック <Minecraft名> / ストラクチャーヴォイド <Minecraft名> / "
    "リピートコマンドブロック <Minecraft名> / チェーンコマンドブロック <Minecraft名> / "
    "タケツミエッグ <Minecraft名> / 手持ち確認 <Minecraft名> / 状態 / 再起動"
)
_COMMAND_RE = re.compile(
    r"^\s*マイクラ[\s\u3000]+(?P<subcommand>"
    + _SUPPORTED_SUBCOMMAND_PATTERN
    + (r"|" + _UNAVAILABLE_SUBCOMMAND_PATTERN if _UNAVAILABLE_SUBCOMMAND_PATTERN else "")
    + r")(?:[\s\u3000]+(?P<arg>\S+))?\s*$"
)
_OWNED_COMMAND_RE = re.compile(
    r"^\s*マイクラ[\s\u3000]+(?P<subcommand>"
    + _SUPPORTED_SUBCOMMAND_PATTERN
    + (r"|" + _UNAVAILABLE_SUBCOMMAND_PATTERN if _UNAVAILABLE_SUBCOMMAND_PATTERN else "")
    + r")(?:[\s\u3000]|$)"
)


def parse_minecraft_command(command_text: Optional[str]):
    text = str(command_text or "")
    match = _COMMAND_RE.fullmatch(text)
    if match is None:
        return None, None, bool(_OWNED_COMMAND_RE.match(text))
    subcommand = match.group("subcommand")
    if subcommand in _UNAVAILABLE_COMMAND_MESSAGES:
        return "unavailable:" + subcommand, None, True
    if subcommand in _PLAYERLESS_COMMAND_TYPES_BY_TEXT:
        if str(match.group("arg") or "").strip():
            return _PLAYERLESS_COMMAND_TYPES_BY_TEXT[subcommand], None, True
        return _PLAYERLESS_COMMAND_TYPES_BY_TEXT[subcommand], SERVER_STATUS_PLAYER_PLACEHOLDER, True
    command_type = _COMMAND_TYPES_BY_TEXT[subcommand]
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
    if command_type and command_type.startswith("unavailable:"):
        subcommand = command_type.split(":", 1)[1]
        await message.channel.send(_UNAVAILABLE_COMMAND_MESSAGES[subcommand])
        return True
    if command_type is None or minecraft_player_name is None:
        await message.channel.send(MINECRAFT_COMMAND_USAGE)
        return True
    if command_type == "server_status":
        if control_api_configured():
            try:
                status_message = format_control_status(await fetch_control_status())
                bridge_result = await _enqueue_bridge_command_result(
                    message,
                    guild_id,
                    command_type,
                    minecraft_player_name,
                    min(config.MINECRAFT_COMMAND_TIMEOUT_SECONDS, 5),
                )
                if bridge_result and bridge_result.get("status") == "succeeded" and bridge_result.get("result_message"):
                    status_message = "{0}\n\nNaritaBridge:\n{1}".format(
                        status_message,
                        str(bridge_result.get("result_message") or "").strip(),
                    )
                else:
                    status_message = "{0}\nNaritaBridge: 応答なし".format(status_message)
                await message.channel.send(status_message[:1900])
                return True
            except MinecraftControlError as exc:
                print(
                    "[WARN] minecraft control status failed: bot_instance_id={0} guild_id={1} error={2}".format(
                        config.BOT_INSTANCE_ID,
                        guild_id,
                        str(exc),
                    )
                )
                await message.channel.send("Minecraft管理APIから状態を取得できませんでした。")
                return True
        return await _handle_bridge_queue_command(message, guild_id, command_type, minecraft_player_name)
    if command_type == "server_restart":
        if not _can_restart_minecraft(message):
            await message.channel.send("Minecraftサーバー再起動の権限がありません。")
            return True
        if not control_api_configured():
            await message.channel.send("Minecraft管理APIが未設定です。")
            return True
        try:
            await message.channel.send("Minecraftサーバーの再起動を開始します。")
            await message.channel.send(format_restart_result(await request_control_restart()))
            return True
        except MinecraftControlError as exc:
            print(
                "[WARN] minecraft control restart failed: bot_instance_id={0} guild_id={1} error={2}".format(
                    config.BOT_INSTANCE_ID,
                    guild_id,
                    str(exc),
                )
            )
            await message.channel.send("Minecraftサーバー再起動に失敗しました。")
            return True

    return await _handle_bridge_queue_command(message, guild_id, command_type, minecraft_player_name)


async def _handle_bridge_queue_command(
    message: discord.Message,
    guild_id: str,
    command_type: str,
    minecraft_player_name: str,
) -> bool:
    result = await _enqueue_bridge_command_result(
        message,
        guild_id,
        command_type,
        minecraft_player_name,
        config.MINECRAFT_COMMAND_TIMEOUT_SECONDS,
    )
    if result is None:
        await message.channel.send("Minecraftサーバーとの通信がタイムアウトしました。")
        return True
    if result.get("status") == "succeeded":
        result_message = str(result.get("result_message") or "").strip()
        if command_type in ("held_item_inspect", "server_status") and result_message:
            await message.channel.send(result_message[:1900])
            return True
        await message.channel.send(_SUCCESS_MESSAGES[command_type].format(player=minecraft_player_name))
        return True
    await message.channel.send(_result_error_message(minecraft_player_name, str(result.get("result_reason") or "")))
    return True


async def _enqueue_bridge_command_result(
    message: discord.Message,
    guild_id: str,
    command_type: str,
    minecraft_player_name: str,
    timeout_seconds: int,
):
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
                timeout_seconds=timeout_seconds,
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
        return None

    print(
        "[INFO] minecraft command queued: bot_instance_id={0} guild_id={1} request_id={2} type={3} minecraft_player={4}".format(
            config.BOT_INSTANCE_ID,
            guild_id,
            row.get("request_id"),
            command_type,
            minecraft_player_name,
        )
    )
    return await wait_for_minecraft_result(str(row["request_id"]), timeout_seconds)


def _can_restart_minecraft(message: discord.Message) -> bool:
    user_id = str(getattr(getattr(message, "author", None), "id", "") or "")
    if not user_id:
        return False
    if user_id in config.MINECRAFT_RESTART_ALLOWED_USER_IDS:
        return True
    if config.DEVELOPER_USER_ID and user_id == str(config.DEVELOPER_USER_ID):
        return True
    try:
        with get_connection() as connection:
            return PermissionRepository(connection).has_global_admin(user_id)
    except Exception as exc:
        print("[WARN] minecraft restart permission check failed: user_id={0} error={1}".format(user_id, type(exc).__name__))
        return False


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
    if reason == "empty_hand":
        return "{0} は現在なにも手に持っていません。".format(minecraft_player_name)
    if reason in ("command_failed", "structure_load_failed"):
        return "成田カーペットの生成に失敗しました。"
    return "Minecraftサーバーとの通信に失敗しました。"
