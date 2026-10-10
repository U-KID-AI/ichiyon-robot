"""Shared interaction transport for existing message-based command handlers."""
import discord

from bot import config


PANEL_CUSTOM_ID_PREFIX = "ichiyon_panel"


def custom_id(*parts: str) -> str:
    return ":".join([PANEL_CUSTOM_ID_PREFIX, config.BOT_INSTANCE_ID] + [str(part) for part in parts])


class InteractionSendChannel:
    def __init__(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.id = getattr(getattr(interaction, "channel", None), "id", None)

    async def send(self, content=None, **kwargs):
        allowed = kwargs.pop("allowed_mentions", None)
        if allowed is None:
            allowed = discord.AllowedMentions.none()
        if not self.interaction.response.is_done():
            await self.interaction.response.send_message(content, allowed_mentions=allowed, ephemeral=True, **kwargs)
            return
        await self.interaction.followup.send(content, allowed_mentions=allowed, ephemeral=True, **kwargs)


class InteractionMessageAdapter:
    def __init__(self, interaction: discord.Interaction, content: str = "") -> None:
        self.interaction = interaction
        self.guild = interaction.guild
        self.author = interaction.user
        self.channel = InteractionSendChannel(interaction)
        self.content = content
