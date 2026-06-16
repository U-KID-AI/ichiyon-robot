import json
import os
import random
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()


def get_env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        print(f"[WARN] {name} must be an integer")
        return default


TOKEN = os.getenv("DISCORD_TOKEN")
STARTUP_CHANNEL_ID = get_env_int("STARTUP_CHANNEL_ID")
SCHEDULE_CHANNEL_ID = get_env_int("SCHEDULE_CHANNEL_ID")
STATE_FILE = "data/state.json"
HAYUSU_ENTER_GIF = "assets/transitions/hayusu_enter.gif"
HAYUSU_EXIT_GIF = "assets/transitions/hayusu_exit.gif"
HAYUSU_MODE_SECONDS = 180
HAYUSU_TRIGGER_RATE = 122
HAYUSU_RESPONSE = "チェルさんこれギャバいっすよ"
HAYUSU_ENTER_MESSAGE = "# はゆすモード\n\n# 突入"
HAYUSU_EXIT_MESSAGE = "# はゆすモード\n\n# 終了"
END_OF_SERVICE_MESSAGE = "サ終やめませんか？"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

has_sent_startup_message = False
is_mode_transitioning = False

WORD_RESPONSES = (
    ("紫", "B01010"),
    ("ナツル", "ナツルちゃんかわゆい"),
    ("はいロボ", "プ"),
)

DEFAULT_STATE = {
    "current_mode": "normal",
    "mode_until": None,
    "last_hayusu_trigger_month": None,
    "annual_message_sent_years": [],
}

DEFAULT_RESPONSES = {
    "end_of_service_message": END_OF_SERVICE_MESSAGE,
}


def load_json_file(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Failed to load {path}: {e}")
        return default


def get_default_state() -> dict:
    state = DEFAULT_STATE.copy()
    state["annual_message_sent_years"] = []
    return state


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:
        print(f"Failed to save {STATE_FILE}: {e}")


def save_json_file(path: str, data) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:
        print(f"Failed to save {path}: {e}")


def load_state() -> dict:
    should_save = not os.path.exists(STATE_FILE)

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Failed to load {STATE_FILE}: {e}")
        state = get_default_state()
        should_save = True

    if not isinstance(state, dict):
        state = get_default_state()
        should_save = True

    normalized_state = get_default_state()
    normalized_state.update(state)

    if not isinstance(normalized_state.get("annual_message_sent_years"), list):
        normalized_state["annual_message_sent_years"] = []
        should_save = True

    if state != normalized_state:
        should_save = True

    if should_save:
        save_state(normalized_state)

    return normalized_state


def get_now() -> datetime:
    return datetime.now(timezone.utc)


def get_current_month() -> str:
    return get_now().strftime("%Y-%m")


def get_local_now() -> datetime:
    return datetime.now().astimezone()


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_responses() -> dict:
    responses = load_json_file("data/responses.json", {})
    if not isinstance(responses, dict):
        return {}

    should_save = False
    for key, value in DEFAULT_RESPONSES.items():
        if key not in responses:
            responses[key] = value
            should_save = True

    if should_save:
        save_json_file("data/responses.json", responses)

    return responses


def load_kuji() -> dict:
    kuji_data = load_json_file("data/kuji.json", {"results": []})
    if not isinstance(kuji_data, dict):
        return {"results": []}
    return kuji_data


def load_quotes() -> list[str]:
    quotes = load_json_file("data/quotes.json", [])
    if not isinstance(quotes, list):
        return []
    return [quote for quote in quotes if isinstance(quote, str)]


def load_ng_words() -> list[str]:
    ng_words = load_json_file("data/ng_words.json", [])
    if not isinstance(ng_words, list):
        return []
    return [ng_word for ng_word in ng_words if isinstance(ng_word, str)]


def draw_kuji_message() -> str:
    kuji_data = load_kuji()
    results = kuji_data.get("results", [])
    if not results:
        return "くじデータが読み込めませんでした"

    result = random.choice(results)
    if not isinstance(result, dict):
        return "くじデータが読み込めませんでした"

    name = result.get("name", "結果不明")
    result_message = result.get("message", "")

    return f"🎴 **{name}**\n{result_message}"


def draw_quote_message() -> str | None:
    quotes = load_quotes()
    if not quotes:
        return None

    return random.choice(quotes)


def get_end_of_service_message() -> str:
    responses = load_responses()
    message = responses.get("end_of_service_message")
    if isinstance(message, str) and message:
        return message
    return END_OF_SERVICE_MESSAGE


def get_schedule_channel() -> discord.abc.Messageable | None:
    if SCHEDULE_CHANNEL_ID == 0:
        print("[WARN] SCHEDULE_CHANNEL_ID is not set")
        return None

    channel = bot.get_channel(SCHEDULE_CHANNEL_ID)
    if channel is None:
        print("[WARN] SCHEDULE_CHANNEL_ID channel was not found")
        return None

    if not hasattr(channel, "send"):
        print("[WARN] SCHEDULE_CHANNEL_ID channel cannot send messages")
        return None

    return channel


async def send_annual_message(channel: discord.abc.Messageable) -> None:
    await channel.send(get_end_of_service_message())


async def maybe_send_annual_message() -> None:
    now = get_local_now()
    if now.month != 6 or now.day != 30:
        return

    state = load_state()
    sent_years = state.get("annual_message_sent_years", [])
    current_year = now.year
    if current_year in sent_years:
        return

    channel = get_schedule_channel()
    if channel is None:
        return

    try:
        await send_annual_message(channel)
    except discord.DiscordException as e:
        print(f"[WARN] Failed to send annual message: {e}")
        return

    sent_years.append(current_year)
    state["annual_message_sent_years"] = sent_years
    save_state(state)


async def send_optional_gif(channel: discord.abc.Messageable, path: str) -> None:
    if os.path.exists(path):
        await channel.send(file=discord.File(path))


async def enter_hayusu_mode(
    channel: discord.abc.Messageable,
    ignore_monthly_limit: bool = False,
) -> bool:
    global is_mode_transitioning

    if is_mode_transitioning:
        return False

    state = load_state()
    current_month = get_current_month()
    if (
        not ignore_monthly_limit
        and state.get("last_hayusu_trigger_month") == current_month
    ):
        return False

    is_mode_transitioning = True
    try:
        await channel.send(HAYUSU_ENTER_MESSAGE)
        await send_optional_gif(channel, HAYUSU_ENTER_GIF)

        state["current_mode"] = "hayusu"
        state["mode_until"] = (
            get_now() + timedelta(seconds=HAYUSU_MODE_SECONDS)
        ).isoformat()
        if not ignore_monthly_limit:
            state["last_hayusu_trigger_month"] = current_month
        save_state(state)
    finally:
        is_mode_transitioning = False

    return True


async def exit_hayusu_mode(channel: discord.abc.Messageable) -> None:
    global is_mode_transitioning

    if is_mode_transitioning:
        return

    is_mode_transitioning = True
    try:
        await channel.send(HAYUSU_EXIT_MESSAGE)
        await send_optional_gif(channel, HAYUSU_EXIT_GIF)

        state = load_state()
        state["current_mode"] = "normal"
        state["mode_until"] = None
        save_state(state)
    finally:
        is_mode_transitioning = False


def get_mention_command_text(message: discord.Message) -> str | None:
    print(f"[DEBUG] mentions={message.mentions}")
    if bot.user is None or bot.user not in message.mentions:
        return None

    print("[DEBUG] bot mentioned")

    bot_id = bot.user.id
    content = message.content
    content = content.replace(f"<@{bot_id}>", "")
    content = content.replace(f"<@!{bot_id}>", "")
    command_text = content.strip()
    print(f"[DEBUG] command_text={command_text!r}")
    return command_text


async def handle_mode_message(message: discord.Message) -> bool:
    if is_mode_transitioning:
        return True

    state = load_state()
    current_mode = state.get("current_mode", "normal")
    if current_mode == "normal":
        return False

    if current_mode == "hayusu":
        command_text = get_mention_command_text(message)
        if command_text is not None and "はゆす終了テスト" in command_text:
            print("[DEBUG] hayusu test command detected")
            await exit_hayusu_mode(message.channel)
            return True

        mode_until = parse_iso_datetime(state.get("mode_until"))
        if mode_until is None or get_now() >= mode_until:
            await exit_hayusu_mode(message.channel)
            return True

        await message.channel.send(HAYUSU_RESPONSE)
        return True

    return True


async def handle_hayusu_test_commands(message: discord.Message) -> bool:
    command_text = get_mention_command_text(message)
    if command_text is None:
        return False

    if "はゆす終了テスト" in command_text:
        print("[DEBUG] hayusu test command detected")
        state = load_state()
        if state.get("current_mode") != "normal":
            await exit_hayusu_mode(message.channel)
        return True

    if "はゆすテスト" in command_text:
        print("[DEBUG] hayusu test command detected")
        await enter_hayusu_mode(message.channel, ignore_monthly_limit=True)
        return True

    return False


async def handle_annual_test_command(message: discord.Message) -> bool:
    command_text = get_mention_command_text(message)
    if command_text is None:
        return False

    if "年次テスト" not in command_text:
        return False

    await message.channel.send(get_end_of_service_message())
    return True


async def maybe_start_hayusu_mode(message: discord.Message) -> bool:
    if bot.user is not None and bot.user in message.mentions:
        return False

    state = load_state()
    if state.get("current_mode", "normal") != "normal":
        return False

    if state.get("last_hayusu_trigger_month") == get_current_month():
        return False

    if random.randrange(HAYUSU_TRIGGER_RATE) != 0:
        return False

    return await enter_hayusu_mode(message.channel)


async def handle_mention_message(message: discord.Message) -> bool:
    if bot.user is None or bot.user not in message.mentions:
        return False

    ng_words = load_ng_words()
    if any(ng_word in message.content for ng_word in ng_words):
        return True

    if "くじ" in message.content:
        await message.channel.send(draw_kuji_message())
        return True

    quote = draw_quote_message()
    if quote is not None:
        await message.channel.send(quote)
    return True


async def handle_word_response(message: discord.Message) -> bool:
    for keyword, response in WORD_RESPONSES:
        if keyword in message.content:
            await message.channel.send(response)
            return True

    return False


@bot.event
async def on_ready():
    global has_sent_startup_message

    print(f"Logged in as {bot.user}")

    if not annual_message_task.is_running():
        annual_message_task.start()

    if has_sent_startup_message:
        return

    has_sent_startup_message = True

    responses = load_responses()

    channel = bot.get_channel(STARTUP_CHANNEL_ID)
    if channel is None:
        print("STARTUP_CHANNEL_ID のチャンネルが見つかりません")
        return

    startup_message = responses.get("startup_message")
    if startup_message is not None:
        await channel.send(startup_message)


@bot.event
async def on_guild_join(guild: discord.Guild):
    responses = load_responses()

    channel = guild.system_channel

    if channel is None:
        for text_channel in guild.text_channels:
            if text_channel.permissions_for(guild.me).send_messages:
                channel = text_channel
                break

    startup_message = responses.get("startup_message")
    if channel is not None and startup_message is not None:
        await channel.send(startup_message)


@tasks.loop(hours=1)
async def annual_message_task():
    try:
        await maybe_send_annual_message()
    except Exception as e:
        print(f"[WARN] annual_message_task failed: {e}")


@annual_message_task.before_loop
async def before_annual_message_task():
    await bot.wait_until_ready()


@bot.event
async def on_message(message: discord.Message):
    print(f"[DEBUG] on_message: author={message.author} content={message.content!r}")

    if message.author.bot:
        print("[DEBUG] ignored bot message")
        return

    if await handle_mode_message(message):
        return

    if await handle_hayusu_test_commands(message):
        return

    if await handle_annual_test_command(message):
        return

    if await maybe_start_hayusu_mode(message):
        return

    if await handle_mention_message(message):
        return

    if await handle_word_response(message):
        return


bot.run(TOKEN)
