from typing import Any, Dict, List, Optional

import discord

from bot.db import get_connection
from bot.repositories import FeatureFlagRepository, MentionReactionRepository, ReactionThresholdRepository
from bot.services.runtime_db import choose_weighted_choice, normalize_json, render_template


DEFAULT_THRESHOLD = 5
DEFAULT_REPLY_MESSAGE = "同じリアクションが{threshold}個ついた"
FEATURE_REACTION_THRESHOLDS = "reaction_thresholds"


def list_text(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value)
    return [item.strip() for item in text.replace(",", "\n").splitlines() if item.strip()]


def get_config_bool(config: Dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def get_config_int(config: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def emoji_to_key(emoji: Any) -> str:
    emoji_id = getattr(emoji, "id", None)
    emoji_name = getattr(emoji, "name", None)
    animated = getattr(emoji, "animated", False)
    if emoji_id is not None and emoji_name:
        prefix = "a" if animated else ""
        return "<{0}:{1}:{2}>".format(prefix, emoji_name, emoji_id) if prefix else "<:{0}:{1}>".format(emoji_name, emoji_id)
    return str(emoji)


def channel_allowed(config: Dict[str, Any], channel_id: str) -> bool:
    allowed = list_text(config.get("allowed_channel_ids"))
    ignored = list_text(config.get("ignored_channel_ids"))
    if ignored and channel_id in ignored:
        return False
    if allowed and channel_id not in allowed:
        return False
    return True


def emoji_allowed(config: Dict[str, Any], emoji_key: str) -> bool:
    targets = list_text(config.get("target_emojis"))
    ignored = list_text(config.get("ignored_emojis"))
    if ignored and emoji_key in ignored:
        return False
    if targets and emoji_key not in targets:
        return False
    return True


def rule_enabled(config: Dict[str, Any]) -> bool:
    return get_config_bool(config, "enabled", True)


async def fetch_reaction_count(message: discord.Message, emoji_key: str) -> int:
    for reaction in getattr(message, "reactions", []) or []:
        if emoji_to_key(getattr(reaction, "emoji", "")) != emoji_key:
            continue
        try:
            return int(getattr(reaction, "count", 0) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def get_fixed_reply_text(config: Dict[str, Any], values: Dict[str, str]) -> Optional[str]:
    text = str(config.get("reply_message") or "").strip()
    if not text:
        return None
    return render_template(text, values)


def select_mention_reaction_reply(connection, guild_id: str, config: Dict[str, Any]) -> Optional[str]:
    reaction_key = str(config.get("reply_reaction_key") or "").strip()
    if not reaction_key:
        return None
    repository = MentionReactionRepository(connection)
    candidate_keys = [reaction_key]
    if reaction_key == "quote":
        candidate_keys.append("quotes")
    elif reaction_key == "quotes":
        candidate_keys.append("quote")
    reaction = None
    for candidate_key in candidate_keys:
        reaction = repository.get_by_key(guild_id, candidate_key)
        if reaction is not None:
            break
    if reaction is None or not bool(reaction.get("enabled", True)):
        return None
    choices = repository.list_choices(guild_id, int(reaction["id"]), enabled=True)
    if not choices:
        return None
    choice = choose_weighted_choice(choices)
    if choice is None:
        return None
    return str(choice.get("body") or choice.get("response_text") or "").strip() or None


def resolve_reply_text(connection, guild_id: str, config: Dict[str, Any], values: Dict[str, str]) -> Optional[str]:
    source_type = str(config.get("reply_source_type") or "fixed").strip()
    if source_type == "mention_reaction":
        selected = select_mention_reaction_reply(connection, guild_id, config)
        if selected:
            return render_template(selected, values)
        return get_fixed_reply_text(config, values)
    return get_fixed_reply_text(config, values)


async def handle_db_reaction_threshold(payload: discord.RawReactionActionEvent, bot: discord.Client) -> bool:
    guild_id_value = getattr(payload, "guild_id", None)
    if guild_id_value is None:
        return False
    user_id = getattr(payload, "user_id", None)
    if user_id is not None and getattr(getattr(bot, "user", None), "id", None) == user_id:
        return False

    guild_id = str(guild_id_value)
    channel_id = str(getattr(payload, "channel_id", ""))
    message_id = str(getattr(payload, "message_id", ""))
    emoji_key = emoji_to_key(getattr(payload, "emoji", ""))

    try:
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        if getattr(getattr(message, "author", None), "bot", False):
            return False
        count = await fetch_reaction_count(message, emoji_key)

        with get_connection() as connection:
            if not FeatureFlagRepository(connection).is_enabled(guild_id, FEATURE_REACTION_THRESHOLDS, default=True):
                return False
            repository = ReactionThresholdRepository(connection)
            for rule in repository.list_rules(guild_id, enabled=True):
                config = normalize_json(rule.get("config_json"))
                if not rule_enabled(config):
                    continue
                threshold = max(1, get_config_int(config, "threshold", DEFAULT_THRESHOLD))
                if count < threshold:
                    continue
                if not channel_allowed(config, channel_id):
                    continue
                if not emoji_allowed(config, emoji_key):
                    continue
                values = {
                    "threshold": str(threshold),
                    "emoji": emoji_key,
                    "count": str(count),
                    "user_mention": "<@{0}>".format(getattr(message.author, "id", "")),
                    "user_name": getattr(message.author, "display_name", None) or getattr(message.author, "name", ""),
                    "message_text": getattr(message, "content", ""),
                }
                text = resolve_reply_text(connection, guild_id, config, values)
                if not text:
                    print("[INFO] reaction threshold skipped without reply text: rule_id={0}".format(rule.get("id")))
                    continue
                if get_config_bool(config, "once_per_message_emoji", True):
                    created = repository.record_event(
                        guild_id,
                        int(rule["id"]),
                        message_id,
                        channel_id,
                        emoji_key,
                        threshold,
                    )
                    if not created:
                        continue
                await message.reply(text, mention_author=False)
                connection.commit()
                return True
    except Exception as exc:
        print("[WARN] reaction threshold runtime failed: {0}".format(exc))
    return False
