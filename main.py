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


def load_responses() -> dict:
    with open("data/responses.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_kuji() -> dict:
    with open("data/kuji.json", "r", encoding="utf-8") as f:
        return json.load(f)


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


@bot.command(name="くじ")
async def kuji_jp(ctx: commands.Context):
    kuji_data = load_kuji()
    result = random.choice(kuji_data["results"])

    await ctx.send(
        f"🎴 **{result['name']}**\n{result['message']}"
    )


@bot.command(name="kuji")
async def kuji_en(ctx: commands.Context):
    kuji_data = load_kuji()
    result = random.choice(kuji_data["results"])

    await ctx.send(
        f"🎴 **{result['name']}**\n{result['message']}"
    )


bot.run(TOKEN)