from typing import Optional

import discord

from bot import config
from bot.data_store import (
    get_end_of_service_message,
    get_local_now,
    load_state,
    save_state,
)

_bot = None


def configure(bot) -> None:
    global _bot
    _bot = bot


def get_bot():
    if _bot is None:
        raise RuntimeError("scheduler.configure(bot) must be called first")
    return _bot


def get_schedule_channel() -> Optional[discord.abc.Messageable]:
    if config.SCHEDULE_CHANNEL_ID == 0:
        print("[WARN] SCHEDULE_CHANNEL_ID is not set")
        return None

    bot = get_bot()
    channel = bot.get_channel(config.SCHEDULE_CHANNEL_ID)
    if channel is None:
        print("[WARN] SCHEDULE_CHANNEL_ID channel was not found")
        return None

    if not hasattr(channel, "send"):
        print("[WARN] SCHEDULE_CHANNEL_ID channel cannot send messages")
        return None

    return channel


async def send_annual_message(channel: discord.abc.Messageable) -> None:
    await channel.send(get_end_of_service_message())


async def maybe_send_annual_message() -> None:
    now = get_local_now()
    if now.month != 6 or now.day != 30:
        return

    state = load_state()
    sent_years = state.get("annual_message_sent_years", [])
    current_year = now.year
    if current_year in sent_years:
        return

    channel = get_schedule_channel()
    if channel is None:
        return

    try:
        await send_annual_message(channel)
    except discord.DiscordException as e:
        print(f"[WARN] Failed to send annual message: {e}")
        return

    sent_years.append(current_year)
    state["annual_message_sent_years"] = sent_years
    save_state(state)
