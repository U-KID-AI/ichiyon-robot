import discord
from discord.ext import commands, tasks

from bot import config, hayusu, messages, scheduler
from bot.message_routing import build_message_router, dispatch_message
from bot.services.auto_posts import run_db_auto_posts_once
from bot.services.ai_tasks import notify_ai_task_terminal_updates_once
from bot.services.storage_monitor import notify_storage_once
from bot.services.reaction_thresholds import handle_db_reaction_threshold
from bot.services.interaction_panel import register_persistent_views
from bot.services.runtime_db import expire_db_modes_once
from bot.services.x_update_notifications import run_x_update_notifications_once
from bot.services.youtube_cookie_monitor import maybe_run_scheduled_cookie_check


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
messages.configure(bot)
hayusu.configure(bot)
scheduler.configure(bot)
_PERSISTENT_VIEWS_REGISTERED = False
message_router = build_message_router()


@bot.event
async def on_ready():
    global _PERSISTENT_VIEWS_REGISTERED
    print(f"Logged in as {bot.user}")
    print(
        "APP_ENV={0} ENABLE_DEV_COMMANDS={1} bot_instance_id={2} bot_instance_name={3} token_env_key={4}".format(
            config.APP_ENV,
            config.ENABLE_DEV_COMMANDS,
            config.BOT_INSTANCE_ID,
            config.BOT_INSTANCE.display_name,
            config.TOKEN_ENV_KEY,
        )
    )

    await messages.sync_bot_identity_for_all_guilds()
    if not _PERSISTENT_VIEWS_REGISTERED:
        register_persistent_views(bot)
        _PERSISTENT_VIEWS_REGISTERED = True

    if config.DATA_BACKEND == "db":
        if config.BOT_INSTANCE_ID == "ichiyon" and not ai_task_notification_task.is_running():
            ai_task_notification_task.start()
        if not db_auto_post_task.is_running():
            db_auto_post_task.start()
    elif not annual_message_task.is_running():
        annual_message_task.start()

    await hayusu.restore_hayusu_auto_exit()

    if config.BOT_INSTANCE_ID == "ichiyon" and not storage_notification_task.is_running():
        storage_notification_task.start()


@bot.event
async def on_guild_join(guild: discord.Guild):
    await messages.sync_bot_identity_for_guild(guild)
    channel = messages.get_guild_startup_channel(guild)
    if channel is not None:
        await messages.send_startup_message(channel)


@tasks.loop(seconds=5)
async def ai_task_notification_task():
    try:
        await notify_ai_task_terminal_updates_once(bot)
    except Exception as exc:
        print("[WARN] AI terminal notification failed: " + type(exc).__name__)


@ai_task_notification_task.before_loop
async def before_ai_task_notification_task():
    await bot.wait_until_ready()


@tasks.loop(hours=1)
async def annual_message_task():
    try:
        await scheduler.maybe_send_annual_message()
    except Exception as e:
        print(f"[WARN] annual_message_task failed: {e}")


@tasks.loop(minutes=1)
async def storage_notification_task():
    # The service boundary also catches errors; keep the loop isolated if its
    # implementation fails before entering that boundary.
    try:
        await notify_storage_once(bot)
    except Exception as exc:
        print("[WARN] Storage notification failed: " + type(exc).__name__)


@storage_notification_task.before_loop
async def before_storage_notification_task():
    await bot.wait_until_ready()


@annual_message_task.before_loop
async def before_annual_message_task():
    await bot.wait_until_ready()


@tasks.loop(minutes=1)
async def db_auto_post_task():
    for task_name, task in (
        ("expire_db_modes", expire_db_modes_once(bot)),
        ("auto_posts", run_db_auto_posts_once(bot)),
        ("x_update_notifications", run_x_update_notifications_once(bot)),
        ("youtube_cookie_check", maybe_run_scheduled_cookie_check(bot)),
    ):
        try:
            await task
        except Exception as e:
            print(f"[WARN] db_auto_post_task {task_name} failed: {e}")


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
    await dispatch_message(message, message_router)


if not config.TOKEN:
    raise RuntimeError(
        "Discord token is not set for BOT_INSTANCE_ID={0}. Set one of: {1}".format(
            config.BOT_INSTANCE_ID,
            ", ".join(config.TOKEN_ENV_KEYS),
        )
    )

bot.run(config.TOKEN)
