import random
import re
import time
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Set

import discord

from bot import config
from bot.repositories import RandomReactionRepository


_RANDOM_REACTION_COOLDOWNS: Dict[str, float] = {}


def safe_emoji_for_log(emoji: str) -> str:
    return str(emoji or "").encode("unicode_escape", errors="backslashreplace").decode("ascii")


def normalize_channel_id(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "").strip())
    mention_match = re.fullmatch(r"<#([0-9]+)>", text)
    if mention_match:
        text = mention_match.group(1)
    return text if re.fullmatch(r"[0-9]+", text) else ""


def split_channel_ids(value: object) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items: Iterable[object] = value
    else:
        raw_items = re.split(r"[\s,、]+", str(value or ""))
    seen: Set[str] = set()
    result: List[str] = []
    for item in raw_items:
        channel_id = normalize_channel_id(item)
        if channel_id and channel_id not in seen:
            seen.add(channel_id)
            result.append(channel_id)
    return result


def join_channel_ids(channel_ids: Iterable[str]) -> str:
    return "\n".join(split_channel_ids(list(channel_ids)))


def random_reaction_cooldown_key(guild_id: str, emoji: str) -> str:
    return "{0}:{1}:{2}".format(config.BOT_INSTANCE_ID, guild_id, emoji)


def random_reaction_channel_allowed(settings: Dict[str, Any], channel_id: str) -> bool:
    excluded = set(split_channel_ids(settings.get("excluded_channel_ids")))
    if channel_id in excluded:
        return False
    targets = set(split_channel_ids(settings.get("target_channel_ids")))
    return not targets or channel_id in targets


def is_human_text_message(message: discord.Message) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    if getattr(message, "webhook_id", None):
        return False
    if not str(getattr(message, "content", "") or "").strip():
        return False
    message_type = getattr(message, "type", None)
    type_name = str(getattr(message_type, "name", message_type) or "default")
    return type_name in {"default", "reply"}


def random_reaction_probability_hit(probability_percent: object) -> bool:
    try:
        percent = float(probability_percent)
    except (TypeError, ValueError):
        return False
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    return random.random() * 100 < percent


def random_reaction_cooldown_allows(settings: Dict[str, Any], guild_id: str, now: Optional[float] = None) -> bool:
    try:
        cooldown_seconds = max(0, int(settings.get("cooldown_seconds") or 0))
    except (TypeError, ValueError):
        cooldown_seconds = 0
    if cooldown_seconds <= 0:
        return True
    key = random_reaction_cooldown_key(guild_id, str(settings.get("emoji") or ""))
    current = time.time() if now is None else now
    last_at = _RANDOM_REACTION_COOLDOWNS.get(key)
    return last_at is None or current - last_at >= cooldown_seconds


def mark_random_reaction_cooldown(settings: Dict[str, Any], guild_id: str, now: Optional[float] = None) -> None:
    key = random_reaction_cooldown_key(guild_id, str(settings.get("emoji") or ""))
    _RANDOM_REACTION_COOLDOWNS[key] = time.time() if now is None else now


def should_add_random_reaction(message: discord.Message, settings: Dict[str, Any], guild_id: str) -> bool:
    if not bool(settings.get("enabled")):
        return False
    emoji = str(settings.get("emoji") or "").strip()
    if not emoji:
        return False
    if not is_human_text_message(message):
        return False
    channel_id = str(getattr(getattr(message, "channel", None), "id", "") or "")
    if not random_reaction_channel_allowed(settings, channel_id):
        return False
    if not random_reaction_cooldown_allows(settings, guild_id):
        return False
    return random_reaction_probability_hit(settings.get("probability_percent"))


async def maybe_add_random_emoji_reaction(message: discord.Message, guild_id: str, connection) -> bool:
    try:
        settings = RandomReactionRepository(connection).get(guild_id)
    except Exception as exc:
        print("[WARN] random reaction settings unavailable: guild_id={0} error={1}".format(guild_id, type(exc).__name__))
        return False

    if not should_add_random_reaction(message, settings, guild_id):
        return False

    emoji = str(settings.get("emoji") or "").strip()
    try:
        await message.add_reaction(emoji)
        mark_random_reaction_cooldown(settings, guild_id)
        return True
    except Exception as exc:
        mark_random_reaction_cooldown(settings, guild_id)
        print(
            "[WARN] random reaction add failed: bot_instance_id={0} guild_id={1} channel_id={2} emoji={3} error={4}".format(
                config.BOT_INSTANCE_ID,
                guild_id,
                str(getattr(getattr(message, "channel", None), "id", "") or ""),
                safe_emoji_for_log(emoji),
                type(exc).__name__,
            )
        )
        return False
