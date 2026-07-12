from pathlib import Path
from typing import Any, List, Optional, Tuple

import discord

from bot import config


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AUDIO_ROOT = (PROJECT_ROOT / "assets" / "audio").resolve()
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg"}

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


def normalize_voice_command(command_text: Optional[str]) -> str:
    return "".join(str(command_text or "").strip().lower().split())


def classify_voice_command(command_text: Optional[str]) -> Optional[str]:
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


def list_audio_files() -> List[Path]:
    if not AUDIO_ROOT.exists() or not AUDIO_ROOT.is_dir():
        return []
    files = [
        path
        for path in AUDIO_ROOT.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
    ]
    return sorted(files, key=lambda path: path.name.lower())


def format_audio_file_list(files: List[Path]) -> str:
    if not files:
        return "登録されている音声ファイルがありません。"
    lines = ["登録されている音声ファイル:"]
    lines.extend("- {0} ({1})".format(path.stem, path.name) for path in files)
    return "\n".join(lines)


def resolve_audio_file(name: str) -> Optional[Path]:
    requested = str(name or "").strip()
    if not requested:
        return None
    raw_path = Path(requested)
    if raw_path.name != requested or raw_path.is_absolute():
        return None

    candidates: List[Path]
    suffix = raw_path.suffix.lower()
    if suffix:
        if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
            return None
        candidates = [AUDIO_ROOT / raw_path.name]
    else:
        candidates = [AUDIO_ROOT / "{0}{1}".format(requested, ext) for ext in sorted(SUPPORTED_AUDIO_EXTENSIONS)]

    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(AUDIO_ROOT)
        except ValueError:
            continue
        if resolved.is_file() and resolved.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
            return resolved
    return None


def get_guild_voice_client(message: discord.Message) -> Optional[discord.VoiceClient]:
    guild = message.guild
    if guild is None:
        return None
    voice_client = guild.voice_client
    if isinstance(voice_client, discord.VoiceClient):
        return voice_client
    return voice_client


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

    voice_client = get_guild_voice_client(message)
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

    def after_playback(error: Optional[Exception]) -> None:
        if error is not None:
            print(
                "[WARN] voice playback error: guild_id={0} channel_id={1} bot_instance_id={2} filename={3} error={4}".format(
                    guild_id,
                    channel_id,
                    config.BOT_INSTANCE_ID,
                    filename,
                    error,
                )
            )
            return
        log_voice_action("play_complete", guild_id, channel_id, filename)

    try:
        source = discord.FFmpegPCMAudio(str(audio_path))
        voice_client.play(source, after=after_playback)
        log_voice_action("play_start", guild_id, channel_id, filename)
        await message.channel.send("再生します: {0}".format(filename))
    except (discord.ClientException, discord.OpusNotLoaded, OSError) as exc:
        log_voice_action("play_failed", guild_id, channel_id, filename)
        print(
            "[WARN] voice playback start failed: guild_id={0} channel_id={1} filename={2} error={3}".format(
                guild_id,
                channel_id,
                filename,
                exc,
            )
        )
        await message.channel.send("音声の再生開始に失敗しました。ffmpegや音声ファイルを確認してください。")


async def stop_audio(message: discord.Message) -> None:
    guild = message.guild
    if guild is None:
        await message.channel.send("音声コマンドはサーバー内で使ってください。")
        return

    voice_client = get_guild_voice_client(message)
    if voice_client is None or not (voice_client.is_playing() or voice_client.is_paused()):
        await message.channel.send("現在再生していません。")
        return

    current_channel = getattr(voice_client, "channel", None)
    channel_id = str(getattr(current_channel, "id", "") or "")
    voice_client.stop()
    log_voice_action("stop", str(guild.id), channel_id)
    await message.channel.send("再生を停止しました。")


async def handle_voice_command(message: discord.Message, command_text: Optional[str]) -> bool:
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
        await stop_audio(message)
        return True
    return False
