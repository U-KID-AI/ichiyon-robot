import asyncio
from typing import Any, Optional, Tuple

import discord

from bot import config
from bot.services.voice_audio import (
    AUDIO_ROOT,
    cleanup_stale_voice_client,
    format_audio_file_list,
    get_guild_voice_client,
    get_raw_guild_voice_client,
    is_voice_client_connected,
    list_audio_files,
    play_audio_on_voice_client,
    resolve_audio_file,
)
from bot.services.voice_music import (
    enqueue_music_url,
    parse_music_command,
    pause_music,
    resume_music,
    send_music_queue,
    send_now_playing,
    skip_music,
    stop_music,
)

VOICE_JOIN_COMMANDS = {
    "もしもししよ",
}
VOICE_LEAVE_COMMANDS = {
    "二度と来るな",
}
VOICE_LIST_COMMANDS = {
    "音声一覧",
    "ボイス一覧",
    "soundlist",
}
VOICE_STOP_COMMANDS = {
    "止めて",
    "停止",
    "stop",
}
VOICE_PLAY_PREFIXES = ("鳴らして", "再生", "ボイス", "sound")
DISCORD_CONNECTION_CLOSED = getattr(discord, "ConnectionClosed", discord.ClientException)


def normalize_voice_command(command_text: Optional[str]) -> str:
    return "".join(str(command_text or "").strip().lower().split())


def classify_voice_command(command_text: Optional[str]) -> Optional[str]:
    action, _ = parse_music_command(command_text)
    if action is not None:
        return action
    action, _ = parse_voice_command(command_text)
    return action


def parse_voice_command(command_text: Optional[str]) -> Tuple[Optional[str], str]:
    raw = str(command_text or "").strip()
    normalized = normalize_voice_command(raw)
    if normalized in VOICE_JOIN_COMMANDS:
        return "join", ""
    if normalized in VOICE_LEAVE_COMMANDS:
        return "leave", ""
    if normalized in VOICE_LIST_COMMANDS:
        return "list", ""
    if normalized in VOICE_STOP_COMMANDS:
        return "stop", ""

    raw_lower = raw.lower()
    for prefix in VOICE_PLAY_PREFIXES:
        prefix_lower = prefix.lower()
        if raw_lower == prefix_lower:
            return "play", ""
        if raw_lower.startswith(prefix_lower + " "):
            return "play", raw[len(prefix) :].strip()
    return None, ""


def get_author_voice_channel(message: discord.Message) -> Optional[Any]:
    voice_state = getattr(message.author, "voice", None)
    channel = getattr(voice_state, "channel", None)
    if channel is None:
        return None
    return channel


def log_voice_action(
    action: str,
    guild_id: str,
    channel_id: Optional[str],
    filename: Optional[str] = None,
) -> None:
    print(
        "[INFO] voice {0}: guild_id={1} channel_id={2} bot_instance_id={3} filename={4}".format(
            action,
            guild_id,
            channel_id or "",
            config.BOT_INSTANCE_ID,
            filename or "",
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
    voice_client = get_raw_guild_voice_client(guild)
    try:
        if voice_client is not None and not is_voice_client_connected(voice_client):
            log_voice_action("join_stale_cleanup", guild_id, target_channel_id)
            await cleanup_stale_voice_client(voice_client)
            voice_client = None

        if voice_client is None:
            await target_channel.connect()
            log_voice_action("join", guild_id, target_channel_id)
            await message.channel.send("VCに入りました。")
            return

        current_channel = getattr(voice_client, "channel", None)
        if is_voice_client_connected(voice_client) and getattr(current_channel, "id", None) == getattr(target_channel, "id", None):
            log_voice_action("join_already_connected", guild_id, target_channel_id)
            await message.channel.send("もう同じVCにいます。")
            return

        await voice_client.move_to(target_channel)
        log_voice_action("move", guild_id, target_channel_id)
        await message.channel.send("VCを移動しました。")
    except (
        RuntimeError,
        asyncio.TimeoutError,
        discord.ClientException,
        discord.Forbidden,
        discord.HTTPException,
        DISCORD_CONNECTION_CLOSED,
    ) as exc:
        log_voice_action("join_failed", guild_id, target_channel_id)
        print("[WARN] voice join failed: guild_id={0} channel_id={1} error={2}".format(guild_id, target_channel_id, exc))
        await cleanup_stale_voice_client(get_raw_guild_voice_client(guild))
        await message.channel.send("VCへの接続に失敗しました。権限や接続状態を確認してください。")
    except Exception as exc:
        log_voice_action("join_failed", guild_id, target_channel_id)
        print("[WARN] unexpected voice join failed: guild_id={0} channel_id={1} error={2}".format(guild_id, target_channel_id, exc))
        await cleanup_stale_voice_client(get_raw_guild_voice_client(guild))
        await message.channel.send("VCへの接続に失敗しました。")


async def leave_voice_channel(message: discord.Message) -> None:
    guild = message.guild
    if guild is None:
        await message.channel.send("VCコマンドはサーバー内で使ってください。")
        return

    guild_id = str(guild.id)
    voice_client = get_raw_guild_voice_client(guild)
    if voice_client is None:
        log_voice_action("leave_not_connected", guild_id, None)
        await message.channel.send("いまVCには入っていません。")
        return
    if not is_voice_client_connected(voice_client):
        await cleanup_stale_voice_client(voice_client)
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


async def send_audio_list(message: discord.Message) -> None:
    await message.channel.send(format_audio_file_list(list_audio_files()))


async def play_audio_file(message: discord.Message, name: str) -> None:
    guild = message.guild
    if guild is None:
        await message.channel.send("音声コマンドはサーバー内で使ってください。")
        return

    if not name:
        await message.channel.send("再生する音声ファイル名を指定してください。")
        return

    voice_client = get_guild_voice_client(guild)
    if voice_client is None:
        await message.channel.send("先にVCへ呼んでください。")
        return
    if voice_client.is_playing() or voice_client.is_paused():
        await message.channel.send("現在再生中です。")
        return

    audio_path = resolve_audio_file(name)
    if audio_path is None:
        await message.channel.send("指定された音声ファイルが見つかりません。")
        return

    guild_id = str(guild.id)
    current_channel = getattr(voice_client, "channel", None)
    channel_id = str(getattr(current_channel, "id", "") or "")
    filename = audio_path.name

    played, reason = play_audio_on_voice_client(voice_client, audio_path, guild_id, channel_id)
    if played:
        log_voice_action("play_start", guild_id, channel_id, filename)
        await message.channel.send("再生します: {0}".format(filename))
        return
    log_voice_action("play_failed", guild_id, channel_id, filename)
    if reason == "playback_error":
        await message.channel.send("音声の再生開始に失敗しました。ffmpegや音声ファイルを確認してください。")


async def stop_audio(message: discord.Message) -> None:
    guild = message.guild
    if guild is None:
        await message.channel.send("音声コマンドはサーバー内で使ってください。")
        return

    voice_client = get_guild_voice_client(guild)
    if voice_client is None or not (voice_client.is_playing() or voice_client.is_paused()):
        await message.channel.send("現在再生していません。")
        return

    current_channel = getattr(voice_client, "channel", None)
    channel_id = str(getattr(current_channel, "id", "") or "")
    voice_client.stop()
    log_voice_action("stop", str(guild.id), channel_id)
    await message.channel.send("再生を停止しました。")


async def handle_voice_command(message: discord.Message, command_text: Optional[str]) -> bool:
    music_command, music_argument = parse_music_command(command_text)
    if music_command == "music_play":
        return await enqueue_music_url(message, music_argument)
    if music_command == "music_skip":
        return await skip_music(message)
    if music_command == "music_pause":
        return await pause_music(message)
    if music_command == "music_resume":
        return await resume_music(message)
    if music_command == "music_queue":
        return await send_music_queue(message)
    if music_command == "music_now":
        return await send_now_playing(message)

    command, argument = parse_voice_command(command_text)
    if command is None:
        return False
    if command == "join":
        await join_author_voice_channel(message)
        return True
    if command == "leave":
        await leave_voice_channel(message)
        return True
    if command == "list":
        await send_audio_list(message)
        return True
    if command == "play":
        await play_audio_file(message, argument)
        return True
    if command == "stop":
        if await stop_music(message):
            return True
        await stop_audio(message)
        return True
    return False
