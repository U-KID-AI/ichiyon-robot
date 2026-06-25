import discord
from discord.ext import commands, tasks

from bot import config, hayusu, messages, scheduler
from bot.dev_guard import handle_developer_command
from bot.kuji import draw_kuji_message
from bot.ng_words import contains_ng_word
from bot.quotes import draw_quote_message
from bot.reactions import handle_word_response
from bot.services.auto_posts import run_db_auto_posts_once
from bot.services.reaction_thresholds import handle_db_reaction_threshold
from bot.services.runtime_db import get_message_guild_id, handle_db_runtime_message


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
messages.configure(bot)
hayusu.configure(bot)
scheduler.configure(bot)


async def handle_mention_message(message: discord.Message) -> bool:
    if bot.user is None or bot.user not in message.mentions:
        return False

    if "くじ" in message.content:
        kuji_result = draw_kuji_message()
        await messages.send_text_or_image(
            message.channel,
            kuji_result.get("text", ""),
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


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"APP_ENV={config.APP_ENV} ENABLE_DEV_COMMANDS={config.ENABLE_DEV_COMMANDS}")

    await messages.sync_bot_identity_for_all_guilds()

    if config.DATA_BACKEND == "db":
        if not db_auto_post_task.is_running():
            db_auto_post_task.start()
    elif not annual_message_task.is_running():
        annual_message_task.start()

    await hayusu.restore_hayusu_auto_exit()


@bot.event
async def on_guild_join(guild: discord.Guild):
    await messages.sync_bot_identity_for_guild(guild)
    channel = messages.get_guild_startup_channel(guild)
    if channel is not None:
        await messages.send_startup_message(channel)


@tasks.loop(hours=1)
async def annual_message_task():
    try:
        await scheduler.maybe_send_annual_message()
    except Exception as e:
        print(f"[WARN] annual_message_task failed: {e}")


@annual_message_task.before_loop
async def before_annual_message_task():
    await bot.wait_until_ready()


@tasks.loop(minutes=1)
async def db_auto_post_task():
    try:
        await run_db_auto_posts_once(bot)
    except Exception as e:
        print(f"[WARN] db_auto_post_task failed: {e}")


@db_auto_post_task.before_loop
async def before_db_auto_post_task():
    await bot.wait_until_ready()


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if config.DATA_BACKEND != "db":
        return
    await handle_db_reaction_threshold(payload, bot)


@bot.event
async def on_message(message: discord.Message):
    print(f"[DEBUG] on_message: author={message.author} content={message.content!r}")

    if message.author.bot:
        print("[DEBUG] ignored bot message")
        return

    command_text = messages.get_mention_command_text(message)
    if await handle_developer_command(message, command_text):
        return

    if config.DATA_BACKEND == "db" and get_message_guild_id(message) is not None:
        await handle_db_runtime_message(message)
        return

    if contains_ng_word(message.content):
        print("[DEBUG] ignored by ng word")
        return

    if await hayusu.handle_mode_message(message):
        return

    if await hayusu.maybe_start_hayusu_mode(message):
        return

    if await handle_mention_message(message):
        return

    if await handle_word_response(message):
        return


bot.run(config.TOKEN)
