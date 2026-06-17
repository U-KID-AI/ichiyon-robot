from typing import Dict, List

import discord

from bot.data_store import load_json_file
from bot.messages import send_text_or_image


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
            normalized_reactions.append(reaction)

    return normalized_reactions


async def handle_word_response(message: discord.Message) -> bool:
    for reaction in load_reactions():
        if reaction["trigger"] in message.content:
            return await send_text_or_image(
                message.channel,
                reaction.get("response", ""),
                reaction.get("image_path", ""),
            )

    return False
