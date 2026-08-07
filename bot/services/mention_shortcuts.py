from typing import Any, Dict, List, Optional

import discord

from bot import config
from bot.db import get_connection
from bot.repositories.mention_shortcuts import MentionShortcutRepository
from bot.repositories.feature_flags import FeatureFlagRepository
from bot.services import game_provider
from bot.services.runtime_db import get_message_guild_id
from bot.services.voice_audio import play_audio_asset_by_id


FEATURE_MENTION_SHORTCUTS = "mention_shortcuts"


def _safe_command_text(command_text: Optional[str]) -> str:
    return str(command_text or "").strip()


async def handle_mention_shortcut_command(message: discord.Message, command_text: Optional[str]) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False
    text = _safe_command_text(command_text)
    if not text:
        return False

    try:
        with get_connection() as connection:
            if not FeatureFlagRepository(connection, bot_id=config.BOT_INSTANCE_ID).is_enabled(
                guild_id,
                FEATURE_MENTION_SHORTCUTS,
                True,
            ):
                return False
            repo = MentionShortcutRepository(connection, bot_id=config.BOT_INSTANCE_ID)
            shortcut = repo.find_by_trigger(guild_id, text)
            if shortcut is None:
                return False
            price_targets = repo.list_price_targets(int(shortcut["id"]), enabled=True)
            audio_actions = repo.list_audio_actions(int(shortcut["id"]), enabled=True)
    except Exception as exc:
        print(
            "[WARN] mention shortcut lookup failed: bot_instance_id={0} guild_id={1} error={2}".format(
                config.BOT_INSTANCE_ID,
                guild_id,
                type(exc).__name__,
            )
        )
        return False

    quotes = await fetch_shortcut_price_quotes(price_targets)
    embeds = build_shortcut_embeds(shortcut, quotes)
    if embeds:
        await message.channel.send(embeds=embeds[:10], allowed_mentions=discord.AllowedMentions.none())
    else:
        await message.channel.send("表示できる価格情報がありません。", allowed_mentions=discord.AllowedMentions.none())

    await run_shortcut_audio_actions(message, audio_actions)
    return True


async def fetch_shortcut_price_quotes(targets: List[Dict[str, Any]]) -> List[game_provider.GamePriceQuote]:
    quotes: List[game_provider.GamePriceQuote] = []
    for target in targets:
        provider = str(target.get("provider") or "").strip().lower()
        product_id = str(target.get("provider_product_id") or "").strip()
        if not provider or not product_id:
            continue
        quote = await game_provider.fetch_price_quote(
            provider,
            product_id,
            str(target.get("lookup_type") or ""),
            str(target.get("display_name") or ""),
        )
        if not bool(target.get("include_historical_low", True)):
            quote.historical_low = None
            quote.formatted_historical_low = ""
        quotes.append(quote)
    return quotes


def build_shortcut_embeds(shortcut: Dict[str, Any], quotes: List[game_provider.GamePriceQuote]) -> List[discord.Embed]:
    title = str(shortcut.get("name") or shortcut.get("trigger_text") or "ショートカット")
    if not quotes:
        return []
    embed = discord.Embed(title=title)
    ok_count = 0
    for quote in quotes:
        if not quote.ok:
            embed.add_field(name=quote.store_name or quote.provider, value=_quote_error_label(quote), inline=False)
            continue
        ok_count += 1
        if ok_count == 1 and quote.store_url:
            embed.url = quote.store_url
        embed.add_field(name=quote.store_name or quote.provider, value=_quote_value(quote), inline=False)
    embed.set_footer(text="最終取得: providerごとの取得時刻")
    return [embed]


def _quote_value(quote: game_provider.GamePriceQuote) -> str:
    lines = [
        "現在価格: {0}".format(game_provider.format_price(quote.current_price, quote.currency, quote.formatted_current_price)),
        "通常価格: {0}".format(game_provider.format_price(quote.regular_price, quote.currency, quote.formatted_regular_price)),
        "割引: {0}%".format(quote.discount_percent or 0),
    ]
    if quote.historical_low is not None or quote.formatted_historical_low:
        lines.append("過去最安: {0}".format(game_provider.format_price(quote.historical_low, quote.currency, quote.formatted_historical_low)))
    if quote.store_url:
        lines.append(quote.store_url)
    return "\n".join(lines)


def _quote_error_label(quote: game_provider.GamePriceQuote) -> str:
    if quote.error_code == "not_configured":
        return "API未設定"
    if quote.error_code == "region_not_in_plan":
        return "JPリージョン未許可"
    if quote.error_code == "not_found":
        return "未取得"
    return "取得失敗: {0}".format(quote.error_code or quote.status)


async def run_shortcut_audio_actions(message: discord.Message, actions: List[Dict[str, Any]]) -> None:
    for action in actions:
        asset_id = action.get("audio_asset_id")
        if not asset_id:
            continue
        try:
            played, reason = await play_audio_asset_by_id(
                message,
                int(asset_id),
                action.get("volume_override"),
                reaction_type="mention_shortcut",
                reaction_key=str(action.get("id") or asset_id),
            )
            print(
                "[INFO] mention shortcut audio: bot_instance_id={0} guild_id={1} asset_id={2} played={3} reason={4}".format(
                    config.BOT_INSTANCE_ID,
                    get_message_guild_id(message) or "",
                    asset_id,
                    played,
                    reason,
                )
            )
        except Exception as exc:
            print(
                "[WARN] mention shortcut audio failed: bot_instance_id={0} guild_id={1} asset_id={2} error={3}".format(
                    config.BOT_INSTANCE_ID,
                    get_message_guild_id(message) or "",
                    asset_id,
                    type(exc).__name__,
                )
            )
