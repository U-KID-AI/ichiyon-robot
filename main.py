import json
import os
import random

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
STARTUP_CHANNEL_ID = int(os.getenv("STARTUP_CHANNEL_ID", "0"))
STATE_FILE = "data/state.json"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

has_sent_startup_message = False

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


def load_responses() -> dict:
    responses = load_json_file("data/responses.json", {})
    if not isinstance(responses, dict):
        return {}
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


async def handle_mode_message(message: discord.Message) -> bool:
    state = load_state()
    current_mode = state.get("current_mode", "normal")
    if current_mode == "normal":
        return False

    return True


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


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if await handle_mode_message(message):
        return

    if await handle_mention_message(message):
        return

    if await handle_word_response(message):
        return


bot.run(TOKEN)
