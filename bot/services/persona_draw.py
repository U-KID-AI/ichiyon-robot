from typing import Optional

import discord

from bot.services.runtime_db import handle_db_runtime_message


async def handle_persona_draw_message(message: discord.Message, command_text: Optional[str] = None) -> bool:
    return await handle_db_runtime_message(message)
