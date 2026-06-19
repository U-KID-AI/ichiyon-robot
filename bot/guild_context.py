from typing import Iterable, Optional

from bot.repositories.guilds import GuildRepository


DEFAULT_FEATURE_KEYS = (
    "mention_reactions",
    "reactions",
    "ng_words",
    "modes",
    "auto_posts",
    "special_effect_tags",
    "destroy",
)


def get_guild_id_from_message(message) -> Optional[str]:
    guild = getattr(message, "guild", None)
    if guild is None:
        return None

    guild_id = getattr(guild, "id", None)
    if guild_id is None:
        return None

    return str(guild_id)


def get_guild_id_from_guild(guild) -> Optional[str]:
    if guild is None:
        return None

    guild_id = getattr(guild, "id", None)
    if guild_id is None:
        return None

    return str(guild_id)


def initialize_guild_from_discord(
    connection,
    guild,
    feature_keys: Optional[Iterable[str]] = DEFAULT_FEATURE_KEYS,
    feature_enabled: bool = False,
):
    repository = GuildRepository(connection)
    return repository.ensure_from_discord_guild(
        guild,
        feature_keys=feature_keys,
        feature_enabled=feature_enabled,
    )
