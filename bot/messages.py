import os
from typing import Optional, Set

import discord

from bot import config
from bot.data_store import get_startup_message

_bot = None


def configure(bot) -> None:
    global _bot
    _bot = bot


def get_bot():
    if _bot is None:
        raise RuntimeError("messages.configure(bot) must be called first")
    return _bot


async def send_optional_gif(channel: discord.abc.Messageable, path: str) -> None:
    if os.path.exists(path):
        await channel.send(file=discord.File(path))


async def send_startup_message(channel: discord.abc.Messageable) -> None:
    startup_message = get_startup_message()
    if startup_message is not None:
        await channel.send(startup_message)


def can_send_to_channel(
    guild: discord.Guild,
    channel: Optional[discord.TextChannel],
) -> bool:
    if channel is None or guild.me is None:
        return False
    return channel.permissions_for(guild.me).send_messages


def get_guild_startup_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    if can_send_to_channel(guild, guild.system_channel):
        return guild.system_channel

    for text_channel in guild.text_channels:
        if can_send_to_channel(guild, text_channel):
            return text_channel

    return None


def get_channel_guild(channel: discord.abc.Messageable) -> Optional[discord.Guild]:
    guild = getattr(channel, "guild", None)
    if isinstance(guild, discord.Guild):
        return guild
    return None


def get_mention_command_text(message: discord.Message) -> Optional[str]:
    bot = get_bot()
    print(f"[DEBUG] mentions={message.mentions}")
    if bot.user is None or bot.user not in message.mentions:
        return None

    print("[DEBUG] bot mentioned")

    bot_id = bot.user.id
    content = message.content
    content = content.replace(f"<@{bot_id}>", "")
    content = content.replace(f"<@!{bot_id}>", "")
    command_text = content.strip()
    print(f"[DEBUG] command_text={command_text!r}")
    return command_text


async def update_bot_nickname(
    channel: discord.abc.Messageable,
    nickname: str,
) -> None:
    guild = get_channel_guild(channel)
    if guild is None:
        print("[WARN] Cannot change bot nickname outside a guild")
        return

    await update_bot_nickname_in_guild(guild, nickname)


async def update_bot_nickname_in_guild(
    guild: discord.Guild,
    nickname: str,
) -> None:
    bot = get_bot()
    member = guild.me
    if member is None and bot.user is not None:
        member = guild.get_member(bot.user.id)

    if member is None:
        print("[WARN] Bot member was not found for nickname change")
        return

    try:
        await member.edit(nick=nickname)
    except discord.DiscordException as e:
        print(f"[WARN] Failed to change bot nickname: {e}")


def can_manage_role(guild: discord.Guild, role: discord.Role) -> bool:
    if role == guild.default_role or role.managed:
        return False

    member = guild.me
    if member is None:
        return False

    if not member.guild_permissions.manage_roles:
        return False

    return role < member.top_role


async def rename_bot_role_if_needed(
    guild: discord.Guild,
    role_name_candidates: Set[str],
) -> None:
    bot = get_bot()
    member = guild.me
    if member is None and bot.user is not None:
        member = guild.get_member(bot.user.id)

    if member is None:
        print("[WARN] Bot member was not found for role rename")
        return

    for role in member.roles:
        if role.name == config.BOT_ROLE_NAME:
            continue
        if role.name not in role_name_candidates:
            continue
        if not can_manage_role(guild, role):
            continue

        try:
            old_role_name = role.name
            await role.edit(name=config.BOT_ROLE_NAME)
            print(
                f"[INFO] Renamed bot role in {guild.name}: "
                f"{old_role_name} -> {config.BOT_ROLE_NAME}"
            )
        except discord.DiscordException as e:
            print(f"[WARN] Failed to rename bot role in {guild.name}: {e}")


async def sync_bot_identity_for_guild(guild: discord.Guild) -> None:
    bot = get_bot()
    member = guild.me
    if member is None and bot.user is not None:
        member = guild.get_member(bot.user.id)

    role_name_candidates = {
        config.NORMAL_BOT_NICKNAME,
        config.HAYUSU_BOT_NICKNAME,
    }
    if member is not None:
        role_name_candidates.add(member.display_name)

    await update_bot_nickname_in_guild(guild, config.NORMAL_BOT_NICKNAME)
    await rename_bot_role_if_needed(guild, role_name_candidates)


async def sync_bot_identity_for_all_guilds() -> None:
    bot = get_bot()
    for guild in bot.guilds:
        await sync_bot_identity_for_guild(guild)


async def update_bot_avatar(path: str) -> None:
    bot = get_bot()
    if not os.path.exists(path):
        print(f"[WARN] Avatar image not found: {path}")
        return

    if bot.user is None:
        print("[WARN] Cannot change bot avatar before bot user is ready")
        return

    try:
        with open(path, "rb") as f:
            avatar = f.read()
        await bot.user.edit(avatar=avatar)
    except OSError as e:
        print(f"[WARN] Failed to read avatar image {path}: {e}")
    except discord.DiscordException as e:
        print(f"[WARN] Failed to change bot avatar: {e}")
