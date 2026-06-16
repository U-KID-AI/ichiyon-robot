import json
import os
import random

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
STARTUP_CHANNEL_ID = int(os.getenv("STARTUP_CHANNEL_ID", "0"))

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


def load_responses() -> dict:
    with open("data/responses.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_kuji() -> dict:
    with open("data/kuji.json", "r", encoding="utf-8") as f:
        return json.load(f)


def draw_kuji_message() -> str:
    kuji_data = load_kuji()
    result = random.choice(kuji_data["results"])

    return f"🎴 **{result['name']}**\n{result['message']}"


async def handle_mode_message(message: discord.Message) -> bool:
    return False


async def handle_mention_message(message: discord.Message) -> bool:
    if bot.user is None or bot.user not in message.mentions:
        return False

    if "くじ" not in message.content:
        return False

    await message.channel.send(draw_kuji_message())
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

    await channel.send(responses["startup_message"])


@bot.event
async def on_guild_join(guild: discord.Guild):
    responses = load_responses()

    channel = guild.system_channel

    if channel is None:
        for text_channel in guild.text_channels:
            if text_channel.permissions_for(guild.me).send_messages:
                channel = text_channel
                break

    if channel is not None:
        await channel.send(responses["startup_message"])


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
