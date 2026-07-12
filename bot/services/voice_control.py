from typing import Any, Optional

import discord

from bot import config


VOICE_JOIN_COMMANDS = {
    "入って",
    "来て",
    "参加",
    "vc入って",
    "ボイス入って",
}
VOICE_LEAVE_COMMANDS = {
    "出て",
    "抜けて",
    "退出",
    "vc出て",
    "ボイス出て",
}


def normalize_voice_command(command_text: Optional[str]) -> str:
    return "".join(str(command_text or "").strip().lower().split())


def classify_voice_command(command_text: Optional[str]) -> Optional[str]:
    normalized = normalize_voice_command(command_text)
    if normalized in VOICE_JOIN_COMMANDS:
        return "join"
    if normalized in VOICE_LEAVE_COMMANDS:
        return "leave"
    return None


def get_author_voice_channel(message: discord.Message) -> Optional[Any]:
    voice_state = getattr(message.author, "voice", None)
    channel = getattr(voice_state, "channel", None)
    if channel is None:
        return None
    return channel


def log_voice_action(action: str, guild_id: str, channel_id: Optional[str]) -> None:
    print(
        "[INFO] voice {0}: guild_id={1} channel_id={2} bot_instance_id={3}".format(
            action,
            guild_id,
            channel_id or "",
            config.BOT_INSTANCE_ID,
        )
    )


async def join_author_voice_channel(message: discord.Message) -> None:
    guild = message.guild
    if guild is None:
        await message.channel.send("VCコマンドはサーバー内で使ってください。")
        return

    target_channel = get_author_voice_channel(message)
    if target_channel is None:
        await message.channel.send("先にVCに入ってから呼んでください。")
        return

    guild_id = str(guild.id)
    target_channel_id = str(getattr(target_channel, "id", "") or "")
    voice_client = guild.voice_client
    try:
        if voice_client is None:
            await target_channel.connect()
            log_voice_action("join", guild_id, target_channel_id)
            await message.channel.send("VCに入りました。")
            return

        current_channel = getattr(voice_client, "channel", None)
        if getattr(current_channel, "id", None) == getattr(target_channel, "id", None):
            log_voice_action("join_already_connected", guild_id, target_channel_id)
            await message.channel.send("もう同じVCにいます。")
            return

        await voice_client.move_to(target_channel)
        log_voice_action("move", guild_id, target_channel_id)
        await message.channel.send("VCを移動しました。")
    except (discord.ClientException, discord.Forbidden, discord.HTTPException) as exc:
        log_voice_action("join_failed", guild_id, target_channel_id)
        print("[WARN] voice join failed: guild_id={0} channel_id={1} error={2}".format(guild_id, target_channel_id, exc))
        await message.channel.send("VCへの接続に失敗しました。権限を確認してください。")


async def leave_voice_channel(message: discord.Message) -> None:
    guild = message.guild
    if guild is None:
        await message.channel.send("VCコマンドはサーバー内で使ってください。")
        return

    guild_id = str(guild.id)
    voice_client = guild.voice_client
    if voice_client is None:
        log_voice_action("leave_not_connected", guild_id, None)
        await message.channel.send("いまVCには入っていません。")
        return

    current_channel = getattr(voice_client, "channel", None)
    channel_id = str(getattr(current_channel, "id", "") or "")
    try:
        await voice_client.disconnect()
        log_voice_action("leave", guild_id, channel_id)
        await message.channel.send("VCから退出しました。")
    except (discord.ClientException, discord.HTTPException) as exc:
        log_voice_action("leave_failed", guild_id, channel_id)
        print("[WARN] voice leave failed: guild_id={0} channel_id={1} error={2}".format(guild_id, channel_id, exc))
        await message.channel.send("VCからの退出に失敗しました。")


async def handle_voice_command(message: discord.Message, command_text: Optional[str]) -> bool:
    command = classify_voice_command(command_text)
    if command is None:
        return False
    if command == "join":
        await join_author_voice_channel(message)
        return True
    await leave_voice_channel(message)
    return True
