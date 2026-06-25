import random
from typing import Dict, List, Optional

import discord

from bot.data_store import load_json_file
from bot.messages import send_text_or_image


def normalize_priority(value) -> int:
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return 1
        if parsed >= 1:
            return parsed
    return 1


def load_reactions() -> List[Dict]:
    data = load_json_file("data/reactions.json", {"reactions": []})
    if not isinstance(data, dict):
        return []

    reactions = data.get("reactions", [])
    if not isinstance(reactions, list):
        return []

    normalized_reactions = []
    for reaction in reactions:
        if not isinstance(reaction, dict):
            continue

        trigger = reaction.get("trigger")
        response = reaction.get("response", "")
        image_path = reaction.get("image_path", "")
        priority = normalize_priority(reaction.get("priority", 1))
        match_type = reaction.get("match_type")
        enabled = reaction.get("enabled")
        if (
            isinstance(trigger, str)
            and isinstance(response, str)
            and isinstance(image_path, str)
            and match_type == "contains"
            and enabled is True
            and (response or image_path)
        ):
            normalized_reactions.append(
                {
                    "id": reaction.get("id", ""),
                    "trigger": trigger,
                    "response": response,
                    "image_path": image_path,
                    "match_type": match_type,
                    "priority": priority,
                    "enabled": enabled,
                }
            )

    return normalized_reactions


def select_reaction_for_content(content: str) -> Optional[Dict]:
    matched_reactions = [
        reaction
        for reaction in load_reactions()
        if reaction["trigger"] in content
    ]
    if not matched_reactions:
        return None

    max_priority = max(reaction.get("priority", 1) for reaction in matched_reactions)
    candidates = [
        reaction
        for reaction in matched_reactions
        if reaction.get("priority", 1) == max_priority
    ]
    return random.choice(candidates)


async def handle_word_response(message: discord.Message) -> bool:
    reaction = select_reaction_for_content(message.content)
    if reaction is None:
        return False

    return await send_text_or_image(
        message.channel,
        reaction.get("response", ""),
        reaction.get("image_path", ""),
    )
