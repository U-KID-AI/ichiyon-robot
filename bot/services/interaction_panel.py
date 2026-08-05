import re
from typing import Optional

import discord

from bot import config
from bot.db import get_connection
from bot.repositories.audio_assets import AudioAssetRepository
from bot.repositories.feature_flags import FeatureFlagRepository
from bot.repositories.games import GameRepository
from bot.services import game_provider
from bot.services.voice_audio import (
    get_guild_voice_client,
    play_audio_asset_row,
    stop_foreground_audio,
)
from bot.services.voice_control import join_author_voice_channel
from bot.services.voice_music import (
    enqueue_music_url,
    pause_music,
    resume_music,
    send_music_queue,
    send_now_playing,
    set_music_loop,
    shuffle_music_queue,
    skip_music,
    stop_music,
    MUSIC_LOOP_OFF,
    MUSIC_LOOP_ONE,
    MUSIC_LOOP_QUEUE,
)


PANEL_CUSTOM_ID_PREFIX = "ichiyon_panel"
MENTION_ONLY_MESSAGE = "何をしますか？"
MAX_SELECT_OPTIONS = 25
FEATURE_AUDIO_ASSETS = "audio_assets"
FEATURE_GAMES = "games"


def custom_id(*parts: str) -> str:
    return ":".join([PANEL_CUSTOM_ID_PREFIX, config.BOT_INSTANCE_ID] + [str(part) for part in parts])


def mention_text_is_empty(command_text: Optional[str]) -> bool:
    return command_text is not None and not str(command_text or "").strip()


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


def build_main_view() -> discord.ui.View:
    return MainPanelView()


def panel_feature_enabled(guild_id: str, feature_key: str) -> bool:
    try:
        with get_connection() as connection:
            return FeatureFlagRepository(connection).is_enabled(guild_id, feature_key, True)
    except Exception:
        return True


async def send_main_panel(message: discord.Message) -> bool:
    await message.channel.send(MENTION_ONLY_MESSAGE, view=build_main_view(), allowed_mentions=discord.AllowedMentions.none())
    return True


async def handle_interaction_panel_mention(message: discord.Message, command_text: Optional[str]) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    if getattr(message, "guild", None) is None:
        return False
    if not mention_text_is_empty(command_text):
        return False
    return await send_main_panel(message)


def register_persistent_views(bot: discord.Client) -> None:
    bot.add_view(MainPanelView())
    bot.add_view(MusicPanelView())
    bot.add_view(AudioCategoryView())
    bot.add_view(GamePanelView())


class MainPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="音楽", style=discord.ButtonStyle.primary, custom_id=custom_id("main", "music"))
    async def music(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message("音楽操作", view=MusicPanelView(), ephemeral=True)

    @discord.ui.button(label="音声・SE", style=discord.ButtonStyle.secondary, custom_id=custom_id("main", "audio"))
    async def audio(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.guild is not None and not panel_feature_enabled(str(interaction.guild.id), FEATURE_AUDIO_ASSETS):
            await interaction.response.send_message("音声・SE機能はOFFです。", ephemeral=True)
            return
        categories = []
        if interaction.guild is not None:
            with get_connection() as connection:
                categories = AudioAssetRepository(connection).list_categories(str(interaction.guild.id), enabled=True)
        await interaction.response.send_message("音声・SE", view=AudioCategoryView(categories), ephemeral=True)

    @discord.ui.button(label="ゲーム", style=discord.ButtonStyle.secondary, custom_id=custom_id("main", "game"))
    async def game(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.guild is not None and not panel_feature_enabled(str(interaction.guild.id), FEATURE_GAMES):
            await interaction.response.send_message("ゲーム機能はOFFです。", ephemeral=True)
            return
        await interaction.response.send_message("ゲーム", view=GamePanelView(), ephemeral=True)

    @discord.ui.button(label="状態確認", style=discord.ButtonStyle.secondary, custom_id=custom_id("main", "status"))
    async def status(self, interaction: discord.Interaction, _button: discord.ui.Button):
        adapter = InteractionMessageAdapter(interaction)
        await send_now_playing(adapter)

    @discord.ui.button(label="閉じる", style=discord.ButtonStyle.danger, custom_id=custom_id("main", "close"))
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message("閉じました。", ephemeral=True)


class MusicUrlModal(discord.ui.Modal, title="曲を追加"):
    url = discord.ui.TextInput(label="YouTubeまたはSpotify URL", max_length=1000)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        adapter = InteractionMessageAdapter(interaction, str(self.url))
        await enqueue_music_url(adapter, str(self.url))


class VolumeModal(discord.ui.Modal, title="音量変更"):
    volume = discord.ui.TextInput(label="音量 0-100", max_length=3)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        from bot.services.voice_music import send_or_update_music_volume

        adapter = InteractionMessageAdapter(interaction, str(self.volume))
        await send_or_update_music_volume(adapter, str(self.volume))


class MusicPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="VCに入る", style=discord.ButtonStyle.primary, custom_id=custom_id("music", "join"))
    async def join(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await join_author_voice_channel(InteractionMessageAdapter(interaction))

    @discord.ui.button(label="一時停止", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "pause"))
    async def pause(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await pause_music(InteractionMessageAdapter(interaction))

    @discord.ui.button(label="再開", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "resume"))
    async def resume(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await resume_music(InteractionMessageAdapter(interaction))

    @discord.ui.button(label="次の曲", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "skip"))
    async def skip(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await skip_music(InteractionMessageAdapter(interaction))

    @discord.ui.button(label="停止", style=discord.ButtonStyle.danger, custom_id=custom_id("music", "stop"))
    async def stop(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await stop_music(InteractionMessageAdapter(interaction))

    @discord.ui.button(label="現在の曲", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "now"))
    async def now(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await send_now_playing(InteractionMessageAdapter(interaction))

    @discord.ui.button(label="キュー表示", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "queue"))
    async def queue(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await send_music_queue(InteractionMessageAdapter(interaction))

    @discord.ui.button(label="ループ切替", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "loop"))
    async def loop(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        state = getattr(interaction.guild, "id", None)
        from bot.services.voice.session import get_music_state

        mode = get_music_state(str(state)).loop_mode if state else MUSIC_LOOP_OFF
        next_mode = MUSIC_LOOP_ONE if mode == MUSIC_LOOP_OFF else MUSIC_LOOP_QUEUE if mode == MUSIC_LOOP_ONE else MUSIC_LOOP_OFF
        await set_music_loop(InteractionMessageAdapter(interaction), next_mode)

    @discord.ui.button(label="シャッフル切替", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "shuffle"))
    async def shuffle(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await shuffle_music_queue(InteractionMessageAdapter(interaction))

    @discord.ui.button(label="音量変更", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "volume"))
    async def volume(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(VolumeModal())

    @discord.ui.button(label="曲を追加", style=discord.ButtonStyle.primary, custom_id=custom_id("music", "add"))
    async def add(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(MusicUrlModal())

    @discord.ui.button(label="戻る", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "back"))
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(MENTION_ONLY_MESSAGE, view=MainPanelView(), ephemeral=True)


class AudioCategorySelect(discord.ui.Select):
    def __init__(self, categories):
        options = [discord.SelectOption(label=category[:100], value=category[:100]) for category in categories[:MAX_SELECT_OPTIONS]]
        if not options:
            options = [discord.SelectOption(label="未登録", value="__none__")]
        super().__init__(placeholder="カテゴリ", min_values=1, max_values=1, options=options, custom_id=custom_id("audio", "category"))

    async def callback(self, interaction: discord.Interaction) -> None:
        category = self.values[0]
        if category == "__none__":
            await interaction.response.send_message("登録されている音声がありません。", ephemeral=True)
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("サーバー内で使ってください。", ephemeral=True)
            return
        db_category = "" if category == "未分類" else category
        with get_connection() as connection:
            assets = AudioAssetRepository(connection).list_assets(str(guild.id), enabled=True, category=db_category)
        await interaction.response.send_message("音声を選んでください。", view=AudioAssetView(category, assets), ephemeral=True)


class AudioCategoryView(discord.ui.View):
    def __init__(self, categories=None) -> None:
        super().__init__(timeout=None)
        self.add_item(AudioCategorySelect(categories or []))

    @discord.ui.button(label="戻る", style=discord.ButtonStyle.secondary, custom_id=custom_id("audio", "back"))
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(MENTION_ONLY_MESSAGE, view=MainPanelView(), ephemeral=True)


class AudioAssetSelect(discord.ui.Select):
    def __init__(self, category: str, assets):
        options = [
            discord.SelectOption(label=str(asset.get("display_name") or asset["id"])[:100], value=str(asset["id"]))
            for asset in assets[:MAX_SELECT_OPTIONS]
        ]
        if not options:
            options = [discord.SelectOption(label="未登録", value="__none__")]
        super().__init__(placeholder="音声", min_values=1, max_values=1, options=options, custom_id=custom_id("audio", "asset"))
        self.category = category

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "__none__":
            await interaction.response.send_message("このカテゴリには音声がありません。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("サーバー内で使ってください。", ephemeral=True)
            return
        with get_connection() as connection:
            asset = AudioAssetRepository(connection).get_asset(str(guild.id), int(self.values[0]), enabled=True)
        if asset is None:
            await interaction.followup.send("音声が見つかりません。", ephemeral=True)
            return
        voice_client = get_guild_voice_client(guild)
        if voice_client is None:
            voice_state = getattr(interaction.user, "voice", None)
            target_channel = getattr(voice_state, "channel", None)
            if target_channel is None:
                await interaction.followup.send("再生先のVCがありません。先にVCへ入ってください。", ephemeral=True)
                return
            await target_channel.connect()
        played, reason = await play_audio_asset_row(guild, asset)
        await interaction.followup.send("再生します。" if played else "再生できませんでした: {0}".format(reason), ephemeral=True)


class AudioAssetView(discord.ui.View):
    def __init__(self, category: str, assets=None) -> None:
        super().__init__(timeout=300)
        self.category = category
        self.add_item(AudioAssetSelect(category, assets or []))

    @discord.ui.button(label="停止", style=discord.ButtonStyle.danger, custom_id=custom_id("audio", "stop"))
    async def stop(self, interaction: discord.Interaction, _button: discord.ui.Button):
        guild = interaction.guild
        if guild is not None:
            stop_foreground_audio(str(guild.id))
        await interaction.response.send_message("音声・SEを停止しました。", ephemeral=True)


class GameSearchModal(discord.ui.Modal, title="ゲーム検索"):
    query = discord.ui.TextInput(label="ゲーム名", max_length=120)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("サーバー内で使ってください。", ephemeral=True)
            return
        try:
            candidates = await game_provider.search_steam_games(str(self.query), limit=1)
        except Exception:
            await interaction.followup.send("ゲーム検索に失敗しました。時間をおいて試してください。", ephemeral=True)
            return
        if not candidates:
            await interaction.followup.send("候補が見つかりませんでした。", ephemeral=True)
            return
        candidate = candidates[0]
        with get_connection() as connection:
            repo = GameRepository(connection)
            game = repo.upsert_game(candidate.to_repository_values())
            repo.add_search_history(str(guild.id), str(interaction.user.id), str(self.query), game["id"])
            connection.commit()
        await interaction.followup.send(embed=build_game_embed(game), view=GameResultView(int(game["id"])), ephemeral=True)


def build_game_embed(game) -> discord.Embed:
    price = game_provider.format_price(game.get("last_known_price"), game.get("currency") or "JPY")
    regular = game_provider.format_price(game.get("last_known_regular_price"), game.get("currency") or "JPY")
    embed = discord.Embed(title=str(game.get("title") or "Steam game"), url=str(game.get("store_url") or ""))
    embed.add_field(name="現在価格", value=price, inline=True)
    embed.add_field(name="通常価格", value=regular, inline=True)
    embed.add_field(name="割引", value="{0}%".format(game.get("last_known_discount_percent") or 0), inline=True)
    embed.add_field(name="発売日", value=str(game.get("release_date") or "未取得"), inline=True)
    embed.add_field(name="過去最安", value=game_provider.format_price(game.get("historical_low"), game.get("currency") or "JPY"), inline=True)
    return embed


class GameResultView(discord.ui.View):
    def __init__(self, game_id: int) -> None:
        super().__init__(timeout=300)
        self.game_id = game_id

    async def _set_flag(self, interaction: discord.Interaction, flag: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("サーバー内で使ってください。", ephemeral=True)
            return
        with get_connection() as connection:
            repo = GameRepository(connection)
            current = repo.get_user_entry(str(guild.id), str(interaction.user.id), self.game_id) or {}
            next_value = not bool(current.get(flag))
            repo.upsert_user_entry(str(guild.id), str(interaction.user.id), self.game_id, **{flag: next_value})
            connection.commit()
        await interaction.response.send_message("{0}を{1}にしました。".format(flag, "ON" if next_value else "OFF"), ephemeral=True)

    @discord.ui.button(label="所持", style=discord.ButtonStyle.primary, custom_id=custom_id("game", "owned"))
    async def owned(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._set_flag(interaction, "owned")

    @discord.ui.button(label="ほしい", style=discord.ButtonStyle.secondary, custom_id=custom_id("game", "wishlist"))
    async def wishlist(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._set_flag(interaction, "wishlist")

    @discord.ui.button(label="積み", style=discord.ButtonStyle.secondary, custom_id=custom_id("game", "backlog"))
    async def backlog(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._set_flag(interaction, "backlog")


class GamePanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="検索", style=discord.ButtonStyle.primary, custom_id=custom_id("game", "search"))
    async def search(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(GameSearchModal())

    @discord.ui.button(label="所持リスト", style=discord.ButtonStyle.secondary, custom_id=custom_id("game", "owned_list"))
    async def owned_list(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._send_list(interaction, "owned", "所持")

    @discord.ui.button(label="ほしいもの", style=discord.ButtonStyle.secondary, custom_id=custom_id("game", "wishlist_list"))
    async def wishlist_list(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._send_list(interaction, "wishlist", "ほしいもの")

    @discord.ui.button(label="積み", style=discord.ButtonStyle.secondary, custom_id=custom_id("game", "backlog_list"))
    async def backlog_list(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._send_list(interaction, "backlog", "積み")

    @discord.ui.button(label="最近の検索", style=discord.ButtonStyle.secondary, custom_id=custom_id("game", "recent"))
    async def recent(self, interaction: discord.Interaction, _button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("サーバー内で使ってください。", ephemeral=True)
            return
        with get_connection() as connection:
            rows = GameRepository(connection).list_recent_searches(str(guild.id), str(interaction.user.id))
        lines = [str(row.get("title") or row.get("query")) for row in rows]
        await interaction.response.send_message("\n".join(lines) if lines else "最近の検索はありません。", ephemeral=True)

    @discord.ui.button(label="戻る", style=discord.ButtonStyle.secondary, custom_id=custom_id("game", "back"))
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(MENTION_ONLY_MESSAGE, view=MainPanelView(), ephemeral=True)

    async def _send_list(self, interaction: discord.Interaction, flag: str, label: str) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("サーバー内で使ってください。", ephemeral=True)
            return
        with get_connection() as connection:
            rows = GameRepository(connection).list_user_entries(str(guild.id), str(interaction.user.id), flag)
        lines = ["- {0}".format(row.get("title")) for row in rows]
        await interaction.response.send_message("{0}:\n{1}".format(label, "\n".join(lines)) if lines else "{0}は空です。".format(label), ephemeral=True)


def extract_supported_music_url(text: str) -> Optional[str]:
    match = re.search(r"https?://\S+", text or "")
    return match.group(0).strip("<>") if match else None
