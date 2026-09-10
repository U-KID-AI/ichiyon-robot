import random
from typing import Any, Dict, List, Optional

import discord

from bot import config, messages
from bot.db import get_connection
from bot.repositories.feature_flags import FeatureFlagRepository
from bot.repositories.persona_draws import PersonaDrawRepository, normalize_persona_trigger
from bot.services.runtime_db import get_message_guild_id

FEATURE_PERSONA_DRAWS = "persona_draws"


def persona_draw_command_text(message: discord.Message, command_text: Optional[str]) -> str:
    if command_text is not None:
        return str(command_text)
    return str(getattr(message, "content", "") or "")


def should_reroll(draw: Dict[str, Any], rng: random.Random = random) -> bool:
    if not bool(draw.get("reroll_enabled")):
        return False
    try:
        probability = int(draw.get("reroll_probability_percent") or 0)
    except (TypeError, ValueError):
        probability = 0
    probability = max(0, min(100, probability))
    return rng.randrange(100) < probability


def choose_candidate(candidates: List[Dict[str, Any]], exclude_body: Optional[str] = None, rng: random.Random = random) -> Optional[Dict[str, Any]]:
    available = [candidate for candidate in candidates if str(candidate.get("body") or "").strip()]
    if exclude_body is not None and len(available) > 1:
        filtered = [candidate for candidate in available if str(candidate.get("body") or "").strip() != exclude_body]
        if filtered:
            available = filtered
    if not available:
        return None
    return rng.choice(available)


def build_persona_draw_messages(
    draw: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    reroll_lines: List[Dict[str, Any]],
    rng: random.Random = random,
) -> List[str]:
    first = choose_candidate(candidates, rng=rng)
    if first is None:
        return []

    first_body = str(first.get("body") or "").strip()
    messages_to_send = [first_body]
    try:
        max_rerolls = int(draw.get("max_rerolls") or 0)
    except (TypeError, ValueError):
        max_rerolls = 0
    max_rerolls = max(0, min(10, max_rerolls))
    if max_rerolls <= 0 or not should_reroll(draw, rng):
        return messages_to_send

    reroll_line = choose_candidate(reroll_lines, rng=rng)
    if reroll_line is not None:
        messages_to_send.append(str(reroll_line.get("body") or "").strip())
    second = choose_candidate(candidates, exclude_body=first_body, rng=rng)
    if second is not None:
        messages_to_send.append(str(second.get("body") or "").strip())
    return [line for line in messages_to_send if line]


async def handle_persona_draw_message(message: discord.Message, command_text: Optional[str]) -> bool:
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False
    if getattr(getattr(message, "author", None), "bot", False):
        return False

    source_text = persona_draw_command_text(message, command_text)
    trigger_key = normalize_persona_trigger(source_text)
    if not trigger_key:
        return False

    try:
        with get_connection() as connection:
            if not FeatureFlagRepository(connection, bot_id=config.BOT_INSTANCE_ID).is_enabled(guild_id, FEATURE_PERSONA_DRAWS, default=True):
                return False
            repo = PersonaDrawRepository(connection, bot_id=config.BOT_INSTANCE_ID)
            draw = repo.find_by_trigger(guild_id, source_text)
            if draw is None:
                return False
            candidates = repo.list_candidates(guild_id, int(draw["id"]), enabled=True)
            reroll_lines = repo.list_reroll_lines(guild_id, int(draw["id"]), enabled=True)
    except Exception as exc:
        print(
            "[WARN] persona draw lookup failed: bot_instance_id={0} guild_id={1} trigger_key={2} error_type={3}".format(
                config.BOT_INSTANCE_ID,
                guild_id,
                trigger_key,
                type(exc).__name__,
            )
        )
        return False

    draw_messages = build_persona_draw_messages(draw, candidates, reroll_lines)
    if not draw_messages:
        print(
            "[WARN] persona draw has no enabled candidates: bot_instance_id={0} guild_id={1} draw_id={2}".format(
                config.BOT_INSTANCE_ID,
                guild_id,
                draw.get("id"),
            )
        )
        return True

    for line in draw_messages:
        await messages.send_text_or_image(message.channel, line, "")
    print(
        "[INFO] persona draw handled: bot_instance_id={0} guild_id={1} draw_id={2} messages={3}".format(
            config.BOT_INSTANCE_ID,
            guild_id,
            draw.get("id"),
            len(draw_messages),
        )
    )
    return True
