from typing import Optional

import discord

from bot import config
from bot.data_store import get_end_of_service_message
from bot.hayusu import enter_hayusu_mode, exit_hayusu_mode


def is_developer_command_text(command_text: str) -> bool:
    normalized = command_text.lower()
    return any(keyword.lower() in normalized for keyword in config.DEV_COMMAND_KEYWORDS)


def is_dev_command_allowed(message: discord.Message) -> bool:
    if config.APP_ENV == "production":
        return False

    if not config.ENABLE_DEV_COMMANDS:
        return False

    if config.DEVELOPER_USER_ID == 0:
        return False

    return message.author.id == config.DEVELOPER_USER_ID


async def handle_developer_command(
    message: discord.Message,
    command_text: Optional[str],
) -> bool:
    if command_text is None or not is_developer_command_text(command_text):
        return False

    if not is_dev_command_allowed(message):
        print("dev command blocked")
        return True

    if "はゆす終了テスト" in command_text:
        print("[DEBUG] hayusu exit test command detected")
        await exit_hayusu_mode(message.channel)
        return True

    if "はゆすテスト" in command_text:
        print("[DEBUG] hayusu test command detected")
        await enter_hayusu_mode(message.channel, ignore_monthly_limit=True)
        return True

    if "年次テスト" in command_text or "6/30テスト" in command_text:
        await message.channel.send(get_end_of_service_message())
        return True

    return True
