from typing import Any, Dict, Optional

from fastapi import Request

from bot import config as bot_config
from bot.db import get_connection
from bot.repositories import PermissionRepository
from bot.repositories.bot_instances import BotInstanceRepository


SESSION_BOT_ID_KEY = "selected_bot_id"


def selected_bot_id(request: Request) -> str:
    value = request.session.get(SESSION_BOT_ID_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return bot_config.BOT_INSTANCE_ID


def set_selected_bot_id(request: Request, bot_id: str) -> None:
    request.session[SESSION_BOT_ID_KEY] = bot_id


def current_bot_instance_for_request(request: Request) -> Dict[str, Any]:
    bot_id = selected_bot_id(request)
    with get_connection() as connection:
        row = BotInstanceRepository(connection).get(bot_id)
    if row:
        return row
    return {
        "bot_id": bot_config.BOT_INSTANCE.bot_id,
        "display_name": bot_config.BOT_INSTANCE.display_name,
        "description": bot_config.BOT_INSTANCE.description,
        "enabled": True,
    }


def can_manage_users(discord_user_id: str) -> bool:
    with get_connection() as connection:
        return PermissionRepository(connection).can_manage_users(discord_user_id)


def require_bot_access(bot_id: str, discord_user_id: str) -> bool:
    with get_connection() as connection:
        return PermissionRepository(connection).can_access_bot(bot_id, discord_user_id)


def require_bot_guild_access(bot_id: str, guild_id: str, discord_user_id: str) -> bool:
    with get_connection() as connection:
        return PermissionRepository(connection).can_access_bot_guild(bot_id, guild_id, discord_user_id)
