"""Production message registry and adapters preserving existing service ownership."""
import discord

from bot import config, hayusu, messages
from bot.dev_guard import handle_developer_command
from bot.kuji import draw_kuji_message
from bot.ng_words import contains_ng_word
from bot.quotes import draw_quote_message
from bot.reactions import handle_word_response
from bot.services.ai_tasks import (
    handle_ai_task_channel_message, is_ai_task_channel,
    parse_ai_command,
)
from bot.services.interaction_panel import handle_context_panel_command, mention_text_is_empty
from bot.services.mention_shortcuts import handle_mention_shortcut_command
from bot.services.mezamashi_horoscope import handle_horoscope_command
from bot.services.minecraft_bridge import handle_minecraft_command
from bot.services.runtime_db import (
    RANDOM_DRAW_PULL_BLOCKED,
    RANDOM_DRAW_PULL_INVALID_MESSAGE,
    get_message_guild_id,
    handle_db_runtime_message,
    parse_random_draw_pull_for_keyword,
)
from bot.services.voice_control import handle_voice_command
from bot.services.voice.tts import maybe_enqueue_tts
from bot.services.voice_music import handle_mention_music_links
from bot.services.youtube_n_pull import handle_youtube_n_pull_command

from bot.message_router import MessageRouter, Route, Phase


async def handle_mention_message(message: discord.Message) -> bool:
    if messages.get_bot().user is None or messages.get_bot().user not in message.mentions:
        return False

    command_text = messages.get_mention_command_text(message) or ""
    for legacy_keyword in ("おみくじ", "くじ"):
        parsed, error = parse_random_draw_pull_for_keyword(command_text, legacy_keyword)
        if error == RANDOM_DRAW_PULL_BLOCKED:
            return False
        if error:
            await message.channel.send(RANDOM_DRAW_PULL_INVALID_MESSAGE)
            return True
        if parsed is None:
            continue
        for index in range(parsed.count):
            kuji_result = draw_kuji_message()
            text = kuji_result.get("text", "")
            if parsed.count > 1:
                text = "{0}/{1}\n{2}".format(index + 1, parsed.count, text).strip()
            await messages.send_text_or_image(
                message.channel,
                text,
                kuji_result.get("image_path", ""),
            )
        return True

    quote = draw_quote_message()
    if quote is not None:
        await messages.send_text_or_image(
            message.channel,
            quote.get("text", ""),
            quote.get("image_path", ""),
        )
    return True


async def handle_empty_mention_message(message: discord.Message, command_text: str | None) -> bool:
    if getattr(message, "guild", None) is None:
        return False
    if not mention_text_is_empty(command_text):
        return False

    if config.DATA_BACKEND == "db" and get_message_guild_id(message) is not None:
        if await handle_db_runtime_message(message):
            return True

    return False


async def _tts(message, command_text) -> bool:
    # Enqueuing speech is an observer, never ownership of the text message.
    await maybe_enqueue_tts(message, command_text)
    return False


async def _db_runtime(message, command_text) -> bool:
    if config.DATA_BACKEND != "db" or get_message_guild_id(message) is None:
        return False
    await handle_db_runtime_message(message)
    # DB owns this branch even on a miss/error; do not invoke JSON fallback.
    return True


async def _ng_word(message, command_text) -> bool:
    if contains_ng_word(message.content):
        print("[DEBUG] ignored by ng word")
        return True
    return False


async def _mode(message, command_text) -> bool:
    return await hayusu.handle_mode_message(message)


async def _mode_start(message, command_text) -> bool:
    return await hayusu.maybe_start_hayusu_mode(message)


async def _mention(message, command_text) -> bool:
    return await handle_mention_message(message)


async def _word_response(message, command_text) -> bool:
    return await handle_word_response(message)


async def _ai_channel(message, command_text) -> bool:
    handled = await handle_ai_task_channel_message(message, command_text)
    # Preserve channel ownership even if a future AI handler returns False.
    return handled or is_ai_task_channel(message)


def build_message_router() -> MessageRouter:
    """The single registration point; ordering is (phase, priority), not source order."""
    return MessageRouter((
        Route("ai_task", Phase.CHANNEL, 10, _ai_channel),
        Route("empty_mention", Phase.COMMAND, 10, handle_empty_mention_message),
        Route("context_panel", Phase.COMMAND, 20, handle_context_panel_command),
        Route("music_links", Phase.COMMAND, 30, handle_mention_music_links),
        Route("youtube_n_pull", Phase.COMMAND, 40, handle_youtube_n_pull_command),
        Route("voice", Phase.COMMAND, 50, handle_voice_command),
        Route("developer", Phase.COMMAND, 60, handle_developer_command),
        Route("minecraft", Phase.COMMAND, 70, handle_minecraft_command),
        Route("mention_shortcut", Phase.COMMAND, 80, handle_mention_shortcut_command),
        Route("horoscope", Phase.COMMAND, 90, handle_horoscope_command),
        Route("tts", Phase.OBSERVER, 10, _tts),
        Route("db_runtime", Phase.BACKEND, 10, _db_runtime),
        Route("ng_word", Phase.LEGACY, 10, _ng_word),
        Route("mode", Phase.LEGACY, 20, _mode),
        Route("mode_start", Phase.LEGACY, 30, _mode_start),
        Route("mention", Phase.LEGACY, 40, _mention),
        Route("word_response", Phase.LEGACY, 50, _word_response),
    ))


async def dispatch_message(message: discord.Message, router: MessageRouter) -> bool:
    if message.author.bot:
        print("[DEBUG] ignored bot message")
        return True
    command_text = messages.get_mention_command_text(message)
    _action, _argument, is_ai_command = parse_ai_command(command_text)
    debug_content = "<AI command redacted>" if is_ai_command else message.content
    if is_ai_task_channel(message):
        debug_content = "<AI development prompt redacted>"
    print(f"[DEBUG] on_message: author={message.author} content={debug_content!r}")
    if is_ai_task_channel(message) and config.BOT_INSTANCE_ID != "ichiyon":
        print("[DEBUG] ignored AI development channel for non-Ichiyon bot")
        return True
    return await router.dispatch(message, command_text)
