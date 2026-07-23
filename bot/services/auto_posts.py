import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import discord

from bot.db import get_connection
from bot.messages import send_text_or_image
from bot.repositories import AutoPostRepository, FeatureFlagRepository
from bot.services.jma_weather import JmaWeatherError, build_weather_messages


FEATURE_AUTO_POSTS = "auto_posts"
JST = timezone(timedelta(hours=9))
WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass
class AutoPostDue:
    due_key: str
    scheduled_at: datetime


def parse_schedule_value(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def parse_time(value: Any) -> Optional[Dict[str, int]]:
    text = str(value or "09:00").strip()
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return {"hour": hour, "minute": minute}


def parse_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_local_now(now: Optional[datetime] = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(JST)


def build_scheduled_at(local_now: datetime, month: int, day: int, hour: int, minute: int) -> Optional[datetime]:
    try:
        return datetime(local_now.year, month, day, hour, minute, tzinfo=JST)
    except ValueError:
        return None


def get_due_run(post: Dict[str, Any], now: Optional[datetime] = None) -> Optional[AutoPostDue]:
    local_now = get_local_now(now)
    schedule_type = post.get("schedule_type") or "yearly"
    config = parse_schedule_value(post.get("schedule_value"))
    time_parts = parse_time(config.get("time"))
    if time_parts is None:
        print("[WARN] auto_posts invalid time: id={0}".format(post.get("id")))
        return None

    hour = time_parts["hour"]
    minute = time_parts["minute"]
    scheduled_at = None

    if schedule_type == "daily":
        scheduled_at = datetime(local_now.year, local_now.month, local_now.day, hour, minute, tzinfo=JST)
        due_key = "daily:{0}".format(scheduled_at.date().isoformat())
    elif schedule_type == "weekly":
        weekday = str(config.get("weekday") or "").strip().lower()
        if WEEKDAY_INDEX.get(weekday) != local_now.weekday():
            return None
        scheduled_at = datetime(local_now.year, local_now.month, local_now.day, hour, minute, tzinfo=JST)
        due_key = "weekly:{0}".format(scheduled_at.date().isoformat())
    elif schedule_type == "monthly":
        day = parse_int(config.get("day"))
        if day is None or day != local_now.day:
            return None
        scheduled_at = datetime(local_now.year, local_now.month, local_now.day, hour, minute, tzinfo=JST)
        due_key = "monthly:{0}".format(scheduled_at.date().isoformat())
    elif schedule_type in ("once", "yearly"):
        month = parse_int(config.get("month"))
        day = parse_int(config.get("day"))
        if month is None or day is None:
            return None
        if month != local_now.month or day != local_now.day:
            return None
        scheduled_at = build_scheduled_at(local_now, month, day, hour, minute)
        if scheduled_at is None:
            return None
        if schedule_type == "once":
            due_key = "once:{0}:{1}".format(post.get("id"), scheduled_at.date().isoformat())
        else:
            due_key = "yearly:{0}".format(scheduled_at.date().isoformat())
    else:
        print("[WARN] auto_posts unknown schedule_type: id={0} type={1}".format(post.get("id"), schedule_type))
        return None

    if scheduled_at is None or local_now < scheduled_at:
        return None
    return AutoPostDue(due_key=due_key, scheduled_at=scheduled_at)


async def get_post_channel(bot, post: Dict[str, Any]) -> Optional[discord.abc.Messageable]:
    raw_channel_id = str(post.get("channel_id") or "").strip()
    if not raw_channel_id:
        print("[WARN] auto_posts channel_id is not set: id={0}".format(post.get("id")))
        return None
    try:
        channel_id = int(raw_channel_id)
    except ValueError:
        print("[WARN] auto_posts channel_id is invalid: id={0} channel_id={1}".format(post.get("id"), raw_channel_id))
        return None

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.DiscordException as exc:
            print("[WARN] auto_posts channel was not found: id={0} error={1}".format(post.get("id"), exc))
            return None

    if not hasattr(channel, "send"):
        print("[WARN] auto_posts channel cannot send messages: id={0}".format(post.get("id")))
        return None
    return channel


async def send_auto_post_content(
    channel: discord.abc.Messageable,
    post: Dict[str, Any],
    forecast_cache: Dict[str, Any],
    now: Optional[datetime] = None,
) -> bool:
    content_type = str(post.get("content_type") or "static").strip() or "static"
    if content_type == "jma_weather":
        messages = await build_weather_messages(
            post.get("content_config_json"),
            forecast_cache=forecast_cache,
            now=now,
        )
        if not messages:
            return False
        for message in messages:
            await channel.send(message)
        return True

    return await send_text_or_image(channel, post.get("body"), post.get("image_path"))


async def run_db_auto_posts_once(bot, now: Optional[datetime] = None) -> int:
    sent_count = 0
    with get_connection() as connection:
        post_repository = AutoPostRepository(connection)
        flag_repository = FeatureFlagRepository(connection)
        forecast_cache: Dict[str, Any] = {}
        for post in post_repository.list_enabled_posts():
            try:
                guild_id = str(post.get("guild_id") or "")
                if not flag_repository.is_enabled(guild_id, FEATURE_AUTO_POSTS, default=True):
                    continue
                due = get_due_run(post, now)
                if due is None:
                    continue
                post_id = int(post["id"])
                if post_repository.was_delivered(post_id, due.due_key):
                    continue

                channel = await get_post_channel(bot, post)
                if channel is None:
                    continue

                try:
                    sent = await send_auto_post_content(channel, post, forecast_cache, now=now)
                except JmaWeatherError as exc:
                    print("[WARN] auto_posts weather generation failed: id={0} error={1}".format(post_id, exc))
                    continue
                except discord.DiscordException as exc:
                    print("[WARN] auto_posts send failed: id={0} error={1}".format(post_id, exc))
                    continue
                if not sent:
                    print("[WARN] auto_posts has no sendable content: id={0}".format(post_id))
                    continue

                post_repository.record_delivery(guild_id, post_id, due.due_key, post.get("channel_id"))
                post_repository.update_last_posted_at(guild_id, post_id)
                connection.commit()
                sent_count += 1
            except Exception as exc:
                print("[WARN] auto_posts runtime skipped post id={0}: {1}".format(post.get("id"), exc))
                try:
                    connection.rollback()
                except Exception:
                    pass
                continue
    return sent_count
