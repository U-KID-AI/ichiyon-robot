import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import discord

from bot.db import get_connection
from bot.messages import get_mention_command_text, send_text_or_image
from bot.ng_words import normalize_ng_match_text
from bot.repositories import (
    AutoReactionRepository,
    FeatureFlagRepository,
    MentionReactionRepository,
    NgWordRepository,
    SpecialEffectRepository,
)


FEATURE_MENTION_REACTIONS = "mention_reactions"
FEATURE_AUTO_REACTIONS = "reactions"
FEATURE_NG_WORDS = "ng_words"

MATCH_TYPE_RANK = {
    "exact": 3,
    "prefix": 2,
    "regex": 1,
}


@dataclass
class MatchResult:
    row: Dict[str, Any]
    groups: Dict[str, str]


def get_message_guild_id(message: discord.Message) -> Optional[str]:
    guild = getattr(message, "guild", None)
    if guild is None:
        return None
    guild_id = getattr(guild, "id", None)
    if guild_id is None:
        return None
    return str(guild_id)


def feature_enabled(connection, guild_id: str, feature_key: str) -> bool:
    repository = FeatureFlagRepository(connection)
    return repository.is_enabled(guild_id, feature_key, default=True)


def build_template_values(message: discord.Message, message_text: str, groups: Dict[str, str]) -> Dict[str, str]:
    values = {
        "user_name": getattr(message.author, "display_name", None) or getattr(message.author, "name", ""),
        "user_mention": getattr(message.author, "mention", ""),
        "message_text": message_text,
    }
    values.update(groups)
    return values


def render_template(text: Optional[str], values: Dict[str, str]) -> str:
    rendered = text or ""
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def regex_groups(match: re.Match) -> Dict[str, str]:
    groups = {}
    for index, value in enumerate(match.groups(), start=1):
        groups["match_{0}".format(index)] = value or ""
    return groups


def match_pattern(pattern: str, match_type: str, content: str) -> Optional[Dict[str, str]]:
    if match_type == "exact":
        if content == pattern:
            return {}
        return None
    if match_type == "prefix":
        if content.startswith(pattern):
            return {}
        return None
    if match_type == "contains":
        if pattern in content:
            return {}
        return None
    if match_type == "regex":
        try:
            matched = re.search(pattern, content)
        except re.error as exc:
            print("[WARN] Invalid DB regex pattern {0!r}: {1}".format(pattern, exc))
            return None
        if matched is None:
            return None
        return regex_groups(matched)
    return None


def sort_mention_matches(matches: List[MatchResult]) -> List[MatchResult]:
    return sorted(
        matches,
        key=lambda item: (
            -len(item.row.get("keyword") or ""),
            -MATCH_TYPE_RANK.get(item.row.get("match_type"), 0),
            item.row.get("created_at"),
        ),
    )


def sort_auto_matches(matches: List[MatchResult]) -> List[MatchResult]:
    return sorted(
        matches,
        key=lambda item: (
            -int(item.row.get("priority") or 0),
            -len(item.row.get("trigger_text") or ""),
            item.row.get("created_at"),
        ),
    )


def choose_weighted_choice(choices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    weighted = []
    total = 0
    for choice in choices:
        weight = int(choice.get("appearance_rate") or 1)
        if weight < 1:
            continue
        total += weight
        weighted.append((choice, total))
    if total <= 0:
        return None

    selected = random.randint(1, total)
    for choice, threshold in weighted:
        if selected <= threshold:
            return choice
    return None


def list_effects(connection, guild_id: str, target_type: str, target_id: int) -> List[Dict[str, Any]]:
    repository = SpecialEffectRepository(connection)
    try:
        effects = repository.list_for_target(guild_id, target_type, target_id, enabled=True)
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        print(
            "[WARN] Failed to load special effect tags for {0}:{1}: {2}".format(
                target_type,
                target_id,
                exc,
            )
        )
        return []
    if effects:
        print(
            "[INFO] Loaded {0} special effect tag(s) for {1}:{2}".format(
                len(effects),
                target_type,
                target_id,
            )
        )
    return effects


def message_has_ng_word(connection, guild_id: str, content: str) -> bool:
    if not feature_enabled(connection, guild_id, FEATURE_NG_WORDS):
        return False

    repository = NgWordRepository(connection)
    normalized_content = normalize_ng_match_text(content)
    for row in repository.list_words(guild_id, enabled=True):
        word = row.get("word") or ""
        if not word:
            continue
        if normalize_ng_match_text(word) in normalized_content:
            list_effects(connection, guild_id, "ng_word", int(row["id"]))
            return True
    return False


async def handle_db_mention(message: discord.Message, guild_id: str, connection) -> bool:
    if not feature_enabled(connection, guild_id, FEATURE_MENTION_REACTIONS):
        return False

    command_text = get_mention_command_text(message)
    if command_text is None:
        return False

    repository = MentionReactionRepository(connection)
    reactions = repository.list_reactions(guild_id, enabled=True, reaction_kind="random_draw")
    matches = []
    for reaction in reactions:
        groups = match_pattern(reaction.get("keyword") or "", reaction.get("match_type") or "exact", command_text)
        if groups is not None:
            matches.append(MatchResult(reaction, groups))
    if not matches:
        return False

    selected = sort_mention_matches(matches)[0]
    choices = repository.list_choices(guild_id, int(selected.row["id"]), enabled=True)
    choice = choose_weighted_choice(choices)
    if choice is None:
        return False

    list_effects(connection, guild_id, "mention_reaction_choice", int(choice["id"]))
    values = build_template_values(message, command_text, selected.groups)
    text = render_template(choice.get("body"), values)
    image_path = choice.get("image_path") or ""
    return await send_text_or_image(message.channel, text, image_path)


async def handle_db_auto_reaction(message: discord.Message, guild_id: str, connection) -> bool:
    if not feature_enabled(connection, guild_id, FEATURE_AUTO_REACTIONS):
        return False

    repository = AutoReactionRepository(connection)
    reactions = repository.list_reactions(guild_id, enabled=True)
    matches = []
    for reaction in reactions:
        groups = match_pattern(
            reaction.get("trigger_text") or "",
            reaction.get("match_type") or "contains",
            message.content,
        )
        if groups is not None:
            matches.append(MatchResult(reaction, groups))
    if not matches:
        return False

    selected = sort_auto_matches(matches)[0]
    list_effects(connection, guild_id, "auto_reaction", int(selected.row["id"]))
    values = build_template_values(message, message.content, selected.groups)
    text = render_template(selected.row.get("response_text"), values)
    image_path = selected.row.get("image_path") or ""
    sent = await send_text_or_image(message.channel, text, image_path)

    emoji = selected.row.get("emoji_internal") or ""
    if emoji:
        try:
            await message.add_reaction(emoji)
            sent = True
        except discord.DiscordException as exc:
            print("[WARN] Failed to add DB auto reaction emoji {0!r}: {1}".format(emoji, exc))

    return sent


async def handle_db_message(message: discord.Message) -> bool:
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False

    try:
        with get_connection() as connection:
            if message_has_ng_word(connection, guild_id, message.content):
                print("[DEBUG] ignored by DB ng word")
                return True

            if await handle_db_mention(message, guild_id, connection):
                return True

            if await handle_db_auto_reaction(message, guild_id, connection):
                return True
    except Exception as exc:
        print("[WARN] DB runtime backend failed: {0}".format(exc))

    return False


async def handle_db_ng_word(message: discord.Message) -> bool:
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False

    try:
        with get_connection() as connection:
            if message_has_ng_word(connection, guild_id, message.content):
                print("[DEBUG] ignored by DB ng word")
                return True
    except Exception as exc:
        print("[WARN] DB ng word backend failed: {0}".format(exc))

    return False


async def handle_db_reactions(message: discord.Message) -> bool:
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False

    try:
        with get_connection() as connection:
            if get_mention_command_text(message) is not None:
                return await handle_db_mention(message, guild_id, connection)
            if await handle_db_mention(message, guild_id, connection):
                return True
            if await handle_db_auto_reaction(message, guild_id, connection):
                return True
    except Exception as exc:
        print("[WARN] DB reaction backend failed: {0}".format(exc))

    return False
