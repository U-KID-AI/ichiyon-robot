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
from bot.repositories.permissions import PermissionRepository, role_allows
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
TAKETUMI_SPAWN_COMMAND = "タケツミ召喚"
TAKETUMI_REMOVE_COMMAND = "タケツミ削除"
GONTA_SPAWN_COMMAND = "ゴン太召喚"
GONTA_REMOVE_COMMAND = "ゴン太削除"
MOLCAR_SPAWN_COMMAND = "モルカー召喚"
MOLCAR_REMOVE_COMMAND = "モルカー削除"
MOLCAR3_SPAWN_COMMAND = "モルカー3召喚"
MOLCAR3_REMOVE_COMMAND = "モルカー3削除"
E_SEIMONJI_COMMAND = "Eの聖文字"
IRSIA_POSTER_COMMAND = "イルシアポスター"
RAIO_POSTER_COMMAND = "ライオポスター"
TRENT_POSTER_COMMAND = "トレントポスター"
AURELIA_POSTER_COMMAND = "オーレリアポスター"
KILLZAEL_POSTER_COMMAND = "キルザエルポスター"
CARAVAN_MAMMOTH_POSTER_COMMAND = "キャラバンマンモスポスター"
ITSUTAKE_POSTER_COMMAND = "イツタケポスター"
CAT_TUNER_POSTER_COMMAND = "キャットチューナーポスター"
WILBERT_POSTER_COMMAND = "ウィルバートポスター"
MILTIO_POSTER_COMMAND = "ミルティオポスター"
ACE_POSTER_COMMAND = "エースポスター"
EYES_EDEN_POSTER_COMMAND = "アイズエデンポスター"
AKUKI_POSTER_COMMAND = "悪鬼ポスター"
SOFTSHELL_CRAB_COMMAND = "ソフトシェルクラブ"
HELD_ITEM_INSPECT_COMMAND = "手持ち確認"
SYNC_DIAGNOSTICS_COMMAND = "同期診断"
JOIN_HISTORY_COMMAND = "join履歴"
SERVER_STATUS_COMMAND = "状態"
SERVER_RESTART_COMMAND = "再起動"
SERVER_STATUS_PLAYER_PLACEHOLDER = "ServerStatus"
JOIN_HISTORY_PLAYER_PLACEHOLDER = "JoinHistory"
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
    E_SEIMONJI_COMMAND: {
        "type": "e_schrift_item",
        "label": "Eの聖文字",
    },
    IRSIA_POSTER_COMMAND: {
        "type": "poster_irsia",
        "label": "イルシアポスター",
    },
    RAIO_POSTER_COMMAND: {
        "type": "poster_raio",
        "label": "ライオポスター",
    },
    TRENT_POSTER_COMMAND: {
        "type": "poster_trent",
        "label": "トレントポスター",
    },
    AURELIA_POSTER_COMMAND: {
        "type": "poster_aurelia",
        "label": "オーレリアポスター",
    },
    KILLZAEL_POSTER_COMMAND: {
        "type": "poster_killzael",
        "label": "キルザエルポスター",
    },
    CARAVAN_MAMMOTH_POSTER_COMMAND: {
        "type": "poster_caravan_mammoth",
        "label": "キャラバンマンモスポスター",
    },
    ITSUTAKE_POSTER_COMMAND: {
        "type": "poster_itsutake",
        "label": "イツタケポスター",
    },
    CAT_TUNER_POSTER_COMMAND: {
        "type": "poster_cat_tuner",
        "label": "キャットチューナーポスター",
    },
    WILBERT_POSTER_COMMAND: {
        "type": "poster_wilbert",
        "label": "ウィルバートポスター",
    },
    MILTIO_POSTER_COMMAND: {
        "type": "poster_miltio",
        "label": "ミルティオポスター",
    },
    ACE_POSTER_COMMAND: {
        "type": "poster_ace",
        "label": "エースポスター",
    },
    EYES_EDEN_POSTER_COMMAND: {
        "type": "poster_eyes_eden",
        "label": "アイズエデンポスター",
    },
    AKUKI_POSTER_COMMAND: {
        "type": "poster_akuki",
        "label": "悪鬼ポスター",
    },
    SOFTSHELL_CRAB_COMMAND: {
        "type": "softshell_crab",
        "label": "ソフトシェルクラブ",
    },
}
_UNAVAILABLE_COMMAND_MESSAGES = {}
_COMMAND_TYPES_BY_TEXT = {
    NARITA_CARPET_COMMAND: "narita_carpet",
    TAKETUMI_SPAWN_COMMAND: "taketumi_spawn_near_player",
    TAKETUMI_REMOVE_COMMAND: "taketumi_remove_near_player",
    GONTA_SPAWN_COMMAND: "gonta_spawn_near_player",
    GONTA_REMOVE_COMMAND: "gonta_remove_near_player",
    MOLCAR_SPAWN_COMMAND: "molcar_spawn_near_player",
    MOLCAR_REMOVE_COMMAND: "molcar_remove_near_player",
    MOLCAR3_SPAWN_COMMAND: "molcar3_spawn_near_player",
    MOLCAR3_REMOVE_COMMAND: "molcar3_remove_near_player",
    HELD_ITEM_INSPECT_COMMAND: "held_item_inspect",
    SYNC_DIAGNOSTICS_COMMAND: "sync_diagnostics",
    **{text: spec["type"] for text, spec in _MINECRAFT_ITEM_COMMANDS.items()},
}
_PLAYERLESS_COMMAND_TYPES_BY_TEXT = {
    JOIN_HISTORY_COMMAND: "join_history",
    SERVER_STATUS_COMMAND: "server_status",
    SERVER_RESTART_COMMAND: "server_restart",
}
_SUCCESS_MESSAGES = {
    "narita_carpet": "{player} に成田カーペットを送り付けました。",
    "taketumi_spawn_near_player": "{player} の近くにタケツミを召喚しました。",
    "taketumi_remove_near_player": "{player} の近くのタケツミ削除を完了しました。",
    "gonta_spawn_near_player": "{player} の近くにゴン太を召喚しました。",
    "gonta_remove_near_player": "{player} の近くのゴン太削除を完了しました。",
    "molcar_spawn_near_player": "{player} の近くにモルカーを召喚しました。",
    "molcar_remove_near_player": "{player} の近くのモルカー削除を完了しました。",
    "molcar3_spawn_near_player": "{player} の近くにモルカー3を召喚しました。",
    "molcar3_remove_near_player": "{player} の近くのモルカー3削除を完了しました。",
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
    "タケツミエッグ <Minecraft名> / Eの聖文字 <Minecraft名> / "
    "イルシアポスター <Minecraft名> / ライオポスター <Minecraft名> / "
    "トレントポスター <Minecraft名> / オーレリアポスター <Minecraft名> / "
    "キルザエルポスター <Minecraft名> / キャラバンマンモスポスター <Minecraft名> / "
    "イツタケポスター <Minecraft名> / キャットチューナーポスター <Minecraft名> / "
    "ウィルバートポスター <Minecraft名> / ミルティオポスター <Minecraft名> / "
    "エースポスター <Minecraft名> / アイズエデンポスター <Minecraft名> / "
    "悪鬼ポスター <Minecraft名> / ソフトシェルクラブ <Minecraft名> / "
    "タケツミ召喚 <Minecraft名> / タケツミ削除 <Minecraft名> / "
    "ゴン太召喚 <Minecraft名> / ゴン太削除 <Minecraft名> / "
    "モルカー召喚 <Minecraft名> / モルカー削除 <Minecraft名> / "
    "モルカー3召喚 <Minecraft名> / モルカー3削除 <Minecraft名> / "
    "手持ち確認 <Minecraft名> / 同期診断 <Minecraft名> / join履歴 / 状態 / 再起動"
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
        if subcommand == JOIN_HISTORY_COMMAND:
            return _PLAYERLESS_COMMAND_TYPES_BY_TEXT[subcommand], JOIN_HISTORY_PLAYER_PLACEHOLDER, True
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
        if command_type in (
            "held_item_inspect",
            "sync_diagnostics",
            "join_history",
            "server_status",
            "taketumi_remove_near_player",
            "gonta_remove_near_player",
            "molcar_remove_near_player",
            "molcar3_remove_near_player",
        ) and result_message:
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

    guild_id = get_message_guild_id(message)

    try:
        with get_connection() as connection:
            permissions = PermissionRepository(connection)

            if permissions.has_global_admin(user_id):
                return True

            if guild_id is None:
                return False

            for guild in permissions.list_manageable_guilds_for_bot(
                config.BOT_INSTANCE_ID,
                user_id,
            ):
                if (
                    str(guild.get("guild_id") or "") == str(guild_id)
                    and role_allows(guild.get("role"), "guild_admin")
                ):
                    return True

            return False
    except Exception as exc:
        print(
            "[WARN] minecraft restart permission check failed: "
            "user_id={0} guild_id={1} error={2}".format(
                user_id,
                guild_id,
                type(exc).__name__,
            )
        )
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
    if reason == "taketumi_spawn_failed":
        return "{0} の近くにタケツミを召喚できませんでした。".format(minecraft_player_name)
    if reason == "taketumi_name_failed":
        return "タケツミの名前設定に失敗しました。"
    if reason == "taketumi_remove_failed":
        return "タケツミの削除に失敗しました。"
    if reason == "gonta_spawn_failed":
        return "{0} の近くにゴン太を召喚できませんでした。".format(minecraft_player_name)
    if reason == "gonta_remove_failed":
        return "ゴン太の削除に失敗しました。"
    if reason == "molcar_spawn_failed":
        return "{0} の近くにモルカーを召喚できませんでした。".format(minecraft_player_name)
    if reason == "molcar_remove_failed":
        return "モルカーの削除に失敗しました。"
    if reason == "molcar3_spawn_failed":
        return "{0} の近くにモルカー3を召喚できませんでした。".format(minecraft_player_name)
    if reason == "molcar3_remove_failed":
        return "モルカー3の削除に失敗しました。"
    if reason == "empty_hand":
        return "{0} は現在なにも手に持っていません。".format(minecraft_player_name)
    if reason in (
        "e_schrift_source_block_missing",
        "e_schrift_source_inventory_missing",
        "e_schrift_item_missing",
        "e_schrift_transfer_failed",
    ):
        return "Eの聖文字の生成に失敗しました。"
    if reason in ("command_failed", "structure_load_failed"):
        return "成田カーペットの生成に失敗しました。"
    return "Minecraftサーバーとの通信に失敗しました。"
