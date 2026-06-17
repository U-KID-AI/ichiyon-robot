import asyncio
import random
from datetime import timedelta
from typing import Optional

import discord

from bot import config
from bot.data_store import (
    get_current_month,
    get_now,
    load_state,
    parse_iso_datetime,
    save_state,
)
from bot.messages import (
    send_optional_gif,
    send_startup_message,
    update_bot_avatar,
    update_bot_nickname,
)

_bot = None
is_mode_transitioning = False
hayusu_auto_exit_task = None


def configure(bot) -> None:
    global _bot
    _bot = bot


def get_bot():
    if _bot is None:
        raise RuntimeError("hayusu.configure(bot) must be called first")
    return _bot


async def apply_hayusu_identity(channel: discord.abc.Messageable) -> None:
    await update_bot_nickname(channel, config.HAYUSU_BOT_NICKNAME)
    await update_bot_avatar(config.HAYUSU_AVATAR)


async def apply_normal_identity(channel: discord.abc.Messageable) -> None:
    await update_bot_nickname(channel, config.NORMAL_BOT_NICKNAME)
    await update_bot_avatar(config.NORMAL_AVATAR)


def cancel_hayusu_auto_exit_task() -> None:
    global hayusu_auto_exit_task

    if hayusu_auto_exit_task is not None and not hayusu_auto_exit_task.done():
        hayusu_auto_exit_task.cancel()
        print("[DEBUG] cancelled hayusu auto exit task")

    hayusu_auto_exit_task = None


async def hayusu_auto_exit_after(
    channel: discord.abc.Messageable,
    delay_seconds: float,
) -> None:
    try:
        await asyncio.sleep(delay_seconds)
    except asyncio.CancelledError:
        return

    print("[DEBUG] hayusu auto exit triggered")
    state = load_state()
    if state.get("current_mode") == "hayusu":
        await exit_hayusu_mode(channel, cancel_auto_task=False)


def schedule_hayusu_auto_exit(
    channel: discord.abc.Messageable,
    delay_seconds: float,
) -> None:
    global hayusu_auto_exit_task

    if hayusu_auto_exit_task is not None and not hayusu_auto_exit_task.done():
        return

    bot = get_bot()
    delay_seconds = max(0, delay_seconds)
    hayusu_auto_exit_task = bot.loop.create_task(
        hayusu_auto_exit_after(channel, delay_seconds)
    )
    print(f"[DEBUG] scheduled hayusu auto exit in {delay_seconds:.0f} seconds")


def get_channel_by_id(channel_id: Optional[int]) -> Optional[discord.abc.Messageable]:
    if not channel_id:
        return None

    bot = get_bot()
    channel = bot.get_channel(channel_id)
    if channel is None or not hasattr(channel, "send"):
        return None

    return channel


async def restore_hayusu_auto_exit() -> None:
    state = load_state()
    if state.get("current_mode") != "hayusu":
        return

    mode_until = parse_iso_datetime(state.get("mode_until"))
    if mode_until is None or get_now() >= mode_until:
        state["current_mode"] = "normal"
        state["mode_until"] = None
        state.pop("hayusu_channel_id", None)
        save_state(state)
        return

    channel_id = state.get("hayusu_channel_id")
    if not isinstance(channel_id, int):
        channel_id = config.STARTUP_CHANNEL_ID

    channel = get_channel_by_id(channel_id)
    if channel is None and channel_id != config.STARTUP_CHANNEL_ID:
        channel = get_channel_by_id(config.STARTUP_CHANNEL_ID)

    if channel is None:
        print("[WARN] Hayusu auto exit channel was not found")
        return

    remaining_seconds = (mode_until - get_now()).total_seconds()
    schedule_hayusu_auto_exit(channel, remaining_seconds)


async def enter_hayusu_mode(
    channel: discord.abc.Messageable,
    ignore_monthly_limit: bool = False,
) -> bool:
    global is_mode_transitioning

    if is_mode_transitioning:
        return False

    state = load_state()
    current_month = get_current_month()
    if (
        not ignore_monthly_limit
        and state.get("last_hayusu_trigger_month") == current_month
    ):
        return False

    is_mode_transitioning = True
    try:
        await channel.send(config.HAYUSU_ENTER_MESSAGE)
        await send_optional_gif(channel, config.HAYUSU_ENTER_GIF)
        await apply_hayusu_identity(channel)

        state["current_mode"] = "hayusu"
        state["mode_until"] = (
            get_now() + timedelta(seconds=config.HAYUSU_MODE_SECONDS)
        ).isoformat()
        channel_id = getattr(channel, "id", None)
        if channel_id is not None:
            state["hayusu_channel_id"] = channel_id
        if not ignore_monthly_limit:
            state["last_hayusu_trigger_month"] = current_month
        save_state(state)
        schedule_hayusu_auto_exit(channel, config.HAYUSU_MODE_SECONDS)
    finally:
        is_mode_transitioning = False

    return True


async def exit_hayusu_mode(
    channel: discord.abc.Messageable,
    cancel_auto_task: bool = True,
) -> None:
    global is_mode_transitioning

    if is_mode_transitioning:
        return

    if cancel_auto_task:
        cancel_hayusu_auto_exit_task()

    is_mode_transitioning = True
    try:
        await channel.send(config.HAYUSU_EXIT_MESSAGE)
        await send_optional_gif(channel, config.HAYUSU_EXIT_GIF)
        await apply_normal_identity(channel)

        state = load_state()
        state["current_mode"] = "normal"
        state["mode_until"] = None
        state.pop("hayusu_channel_id", None)
        save_state(state)
        await send_startup_message(channel)
    finally:
        is_mode_transitioning = False


async def handle_mode_message(message: discord.Message) -> bool:
    if is_mode_transitioning:
        return True

    state = load_state()
    current_mode = state.get("current_mode", "normal")
    if current_mode == "normal":
        return False

    if current_mode == "hayusu":
        mode_until = parse_iso_datetime(state.get("mode_until"))
        if mode_until is None or get_now() >= mode_until:
            await exit_hayusu_mode(message.channel)
            return True

        await message.channel.send(config.HAYUSU_RESPONSE)
        return True

    return True


async def maybe_start_hayusu_mode(message: discord.Message) -> bool:
    bot = get_bot()
    if bot.user is not None and bot.user in message.mentions:
        return False

    state = load_state()
    if state.get("current_mode", "normal") != "normal":
        return False

    if state.get("last_hayusu_trigger_month") == get_current_month():
        return False

    if random.randrange(config.HAYUSU_TRIGGER_RATE) != 0:
        return False

    return await enter_hayusu_mode(message.channel)
