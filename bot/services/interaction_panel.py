import re
import unicodedata
from typing import Optional

import discord

from bot import config
from bot.db import get_connection
from bot.repositories.audio_assets import AudioAssetRepository
from bot.repositories.feature_flags import FeatureFlagRepository
from bot.repositories.games import GameRepository
from bot.repositories.youtube_n_pull import YouTubeNPullRepository
from bot.services import game_provider
from bot.services.voice_audio import (
    get_guild_voice_client,
    play_audio_asset_row,
    stop_foreground_audio,
)
from bot.services.voice_control import join_author_voice_channel
from bot.services.voice_music import (
    enqueue_music_url,
    format_duration,
    pause_music,
    resume_music,
    search_youtube_music_candidates,
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
from bot.services.youtube_n_pull import handle_youtube_n_pull_command, is_youtube_n_pull_schema_missing


PANEL_CUSTOM_ID_PREFIX = "ichiyon_panel"
MENTION_ONLY_MESSAGE = "何をしますか？"
MAX_SELECT_OPTIONS = 25
SOUNDBOARD_ASSETS_PER_PAGE = 20
DISCORD_BUTTON_LABEL_MAX_LENGTH = 80
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


def load_enabled_audio_assets(guild_id: str):
    with get_connection() as connection:
        return AudioAssetRepository(connection).list_assets(str(guild_id), enabled=True)


def truncate_button_label(value: object, fallback: str) -> str:
    label = str(value or fallback or "").strip() or str(fallback)
    return label[:DISCORD_BUTTON_LABEL_MAX_LENGTH]


def build_audio_soundboard_content(assets) -> str:
    if assets:
        return "\u97f3\u58f0\u30fbSE"
    return "\u97f3\u58f0\u30fbSE\n\u767b\u9332\u3055\u308c\u3066\u3044\u308b\u97f3\u58f0\u304c\u3042\u308a\u307e\u305b\u3093\u3002"


async def send_main_panel(message: discord.Message) -> bool:
    await message.channel.send(MENTION_ONLY_MESSAGE, view=build_main_view(), allowed_mentions=discord.AllowedMentions.none())
    return True


def normalize_panel_command(command_text: Optional[str]) -> str:
    text = unicodedata.normalize("NFKC", str(command_text or "")).casefold()
    return "".join(text.strip().split())


def panel_command_kind(command_text: Optional[str]) -> Optional[str]:
    normalized = normalize_panel_command(command_text)
    if normalized in {"パネル", "panel"}:
        return "root"
    if normalized in {"ゲーム", "game"}:
        return "game"
    if normalized in {"音声", "se", "sound", "ボイス"}:
        return "audio"
    if normalized in {"音楽", "music"}:
        return "music"
    return None


async def handle_context_panel_command(message: discord.Message, command_text: Optional[str]) -> bool:
    if getattr(getattr(message, "author", None), "bot", False):
        return False
    guild = getattr(message, "guild", None)
    if guild is None:
        return False

    source_text = command_text if command_text is not None else getattr(message, "content", "")
    kind = panel_command_kind(source_text)
    if kind is None:
        return False

    if kind == "root":
        await send_main_panel(message)
        return True

    if kind == "game":
        if not panel_feature_enabled(str(guild.id), FEATURE_GAMES):
            await message.channel.send("ゲーム機能はOFFです。", allowed_mentions=discord.AllowedMentions.none())
            return True
        await message.channel.send("ゲーム操作", view=GamePanelView(), allowed_mentions=discord.AllowedMentions.none())
        return True

    if kind == "audio":
        if not panel_feature_enabled(str(guild.id), FEATURE_AUDIO_ASSETS):
            await message.channel.send("音声・SE機能はOFFです。", allowed_mentions=discord.AllowedMentions.none())
            return True
        assets = load_enabled_audio_assets(str(guild.id))
        await message.channel.send(build_audio_soundboard_content(assets), view=AudioSoundboardView(assets), allowed_mentions=discord.AllowedMentions.none())
        return True

    await message.channel.send("音楽操作", view=MusicPanelView(), allowed_mentions=discord.AllowedMentions.none())
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

    @discord.ui.button(label="SE", style=discord.ButtonStyle.secondary, custom_id=custom_id("main", "audio"))
    async def audio(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.guild is not None and not panel_feature_enabled(str(interaction.guild.id), FEATURE_AUDIO_ASSETS):
            await interaction.response.send_message("音声・SE機能はOFFです。", ephemeral=True)
            return
        assets = []
        if interaction.guild is not None:
            assets = load_enabled_audio_assets(str(interaction.guild.id))
        await interaction.response.send_message(build_audio_soundboard_content(assets), view=AudioSoundboardView(assets), ephemeral=True)

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


class MusicSearchModal(discord.ui.Modal, title="検索して追加"):
    query = discord.ui.TextInput(label="検索キーワード", max_length=200)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("サーバー内で使ってください。", ephemeral=True)
            return
        keyword = str(self.query or "").strip()
        if not keyword:
            await interaction.followup.send("検索キーワードを入力してください。", ephemeral=True)
            return
        try:
            candidates = await search_youtube_music_candidates(keyword, str(guild.id), str(getattr(interaction.user, "id", "") or ""))
        except Exception:
            await interaction.followup.send("検索に失敗しました。時間をおいて試してください。", ephemeral=True)
            return
        if not candidates:
            await interaction.followup.send("候補が見つかりませんでした。", ephemeral=True)
            return
        await interaction.followup.send("候補を選んでください。", view=MusicSearchResultView(candidates), ephemeral=True)


class YouTubeNPullCountModal(discord.ui.Modal, title="N連を実行"):
    def __init__(self, preset) -> None:
        super().__init__()
        self.preset = dict(preset or {})
        default_count = max(1, min(100, int(self.preset.get("max_pulls") or 1)))
        self.count = discord.ui.TextInput(
            label="何連しますか？",
            default=str(default_count),
            placeholder=str(default_count),
            max_length=3,
        )
        self.add_item(self.count)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        command_name = str(self.preset.get("command_name") or "").strip()
        if not command_name:
            await interaction.followup.send("N連プリセットのコマンド名が未設定です。", ephemeral=True)
            return
        count_text = str(self.count or "").strip()
        if not count_text or not count_text.isdigit():
            await interaction.followup.send("N連の件数は1〜100で指定してください。", ephemeral=True)
            return
        command_text = "{0} {1}連".format(command_name, int(count_text))
        handled = await handle_youtube_n_pull_command(InteractionMessageAdapter(interaction, command_text), command_text)
        if not handled:
            await interaction.followup.send("N連プリセットを実行できませんでした。", ephemeral=True)


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

    @discord.ui.button(label="検索して追加", style=discord.ButtonStyle.primary, custom_id=custom_id("music", "search_add"))
    async def search_add(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(MusicSearchModal())

    @discord.ui.button(label="URLで追加", style=discord.ButtonStyle.primary, custom_id=custom_id("music", "add"))
    async def add(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(MusicUrlModal())

    @discord.ui.button(label="N連プリセット", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "n_pull"))
    async def n_pull(self, interaction: discord.Interaction, _button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("サーバー内で使ってください。", ephemeral=True)
            return
        try:
            with get_connection() as connection:
                presets = YouTubeNPullRepository(connection).list_presets(str(guild.id), enabled=True)
        except Exception as exc:
            if is_youtube_n_pull_schema_missing(exc):
                await interaction.response.send_message("N連プリセットはまだ利用できません。", ephemeral=True)
                return
            raise
        if not presets:
            await interaction.response.send_message("利用できるN連プリセットがありません。", ephemeral=True)
            return
        await interaction.response.send_message("N連プリセットを選んでください。", view=YouTubeNPullPresetView(presets), ephemeral=True)

    @discord.ui.button(label="戻る", style=discord.ButtonStyle.secondary, custom_id=custom_id("music", "back"))
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(MENTION_ONLY_MESSAGE, view=MainPanelView(), ephemeral=True)


class MusicSearchResultSelect(discord.ui.Select):
    def __init__(self, candidates):
        self.candidates = {}
        self.processed_interactions = set()
        options = []
        for index, candidate in enumerate(candidates[:25], start=1):
            video_id = str(candidate.get("video_id") or "")
            if not video_id:
                continue
            self.candidates[video_id] = candidate
            title = str(candidate.get("title") or video_id)
            uploader = str(candidate.get("uploader") or "")
            duration = format_duration(candidate.get("duration"))
            description_parts = [part for part in (uploader, duration) if part]
            options.append(
                discord.SelectOption(
                    label="{0}. {1}".format(index, title)[:100],
                    value=video_id[:100],
                    description=" / ".join(description_parts)[:100] if description_parts else None,
                )
            )
        if not options:
            options = [discord.SelectOption(label="候補なし", value="__none__")]
        super().__init__(placeholder="追加する曲を選んでください", min_values=1, max_values=1, options=options, custom_id=custom_id("music", "search_select"))

    async def callback(self, interaction: discord.Interaction) -> None:
        interaction_key = str(getattr(interaction, "id", "") or id(interaction))
        if interaction_key in self.processed_interactions:
            await interaction.response.send_message("この操作は処理済みです。", ephemeral=True)
            return
        self.processed_interactions.add(interaction_key)
        video_id = str(self.values[0])
        if video_id == "__none__":
            await interaction.response.send_message("候補が見つかりませんでした。", ephemeral=True)
            return
        candidate = self.candidates.get(video_id)
        if not candidate:
            await interaction.response.send_message("候補が見つかりませんでした。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await enqueue_music_url(InteractionMessageAdapter(interaction, str(candidate.get("webpage_url") or "")), str(candidate.get("webpage_url") or ""))


class MusicSearchResultView(discord.ui.View):
    def __init__(self, candidates) -> None:
        super().__init__(timeout=300)
        self.add_item(MusicSearchResultSelect(candidates))


class YouTubeNPullPresetSelect(discord.ui.Select):
    def __init__(self, presets):
        self.presets = {str(preset.get("id")): preset for preset in presets[:MAX_SELECT_OPTIONS]}
        options = []
        for preset in presets[:MAX_SELECT_OPTIONS]:
            preset_id = str(preset.get("id"))
            name = str(preset.get("display_name") or preset.get("command_name") or preset_id)
            command_name = str(preset.get("command_name") or "")
            max_pulls = int(preset.get("max_pulls") or 1)
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=preset_id[:100],
                    description="{0} / 最大{1}連".format(command_name, max_pulls)[:100],
                )
            )
        super().__init__(placeholder="N連プリセット", min_values=1, max_values=1, options=options, custom_id=custom_id("music", "n_pull_select"))

    async def callback(self, interaction: discord.Interaction) -> None:
        preset = self.presets.get(str(self.values[0]))
        if not preset:
            await interaction.response.send_message("N連プリセットが見つかりません。", ephemeral=True)
            return
        command_name = str(preset.get("command_name") or "").strip()
        if not command_name:
            await interaction.response.send_message("N連プリセットのコマンド名が未設定です。", ephemeral=True)
            return
        await interaction.response.send_modal(YouTubeNPullCountModal(preset))


class YouTubeNPullPresetView(discord.ui.View):
    def __init__(self, presets) -> None:
        super().__init__(timeout=300)
        self.add_item(YouTubeNPullPresetSelect(presets))


class AudioAssetButton(discord.ui.Button):
    def __init__(self, asset) -> None:
        self.asset_id = int(asset.get("id"))
        label = truncate_button_label(asset.get("display_name"), str(self.asset_id))
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=custom_id("audio", "asset", self.asset_id),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("サーバー内で使ってください。", ephemeral=True)
            return
        try:
            with get_connection() as connection:
                asset = AudioAssetRepository(connection).get_asset(str(guild.id), self.asset_id, enabled=True)
        except Exception:
            await interaction.followup.send("音声の取得に失敗しました。時間をおいて試してください。", ephemeral=True)
            return
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
            try:
                await target_channel.connect()
            except Exception:
                await interaction.followup.send("VCへの接続に失敗しました。", ephemeral=True)
                return
        played, reason = await play_audio_asset_row(guild, asset)
        if not played:
            await interaction.followup.send("再生できませんでした: {0}".format(reason), ephemeral=True)


class AudioSoundboardPageButton(discord.ui.Button):
    def __init__(self, page: int, label: str, direction: str, disabled: bool = False) -> None:
        self.target_page = page
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=custom_id("audio", "page", direction, page),
            disabled=disabled,
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("サーバー内で使ってください。", ephemeral=True)
            return
        assets = load_enabled_audio_assets(str(guild.id))
        await interaction.response.edit_message(content=build_audio_soundboard_content(assets), view=AudioSoundboardView(assets, self.target_page))


class AudioSoundboardBackButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="戻る", style=discord.ButtonStyle.secondary, custom_id=custom_id("audio", "soundboard_back"), row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(MENTION_ONLY_MESSAGE, view=MainPanelView(), ephemeral=True)


class AudioSoundboardView(discord.ui.View):
    def __init__(self, assets=None, page: int = 0) -> None:
        super().__init__(timeout=300)
        self.assets = list(assets or [])
        self.page_count = max(1, (len(self.assets) + SOUNDBOARD_ASSETS_PER_PAGE - 1) // SOUNDBOARD_ASSETS_PER_PAGE)
        self.page = max(0, min(int(page or 0), self.page_count - 1))
        start = self.page * SOUNDBOARD_ASSETS_PER_PAGE
        page_assets = self.assets[start : start + SOUNDBOARD_ASSETS_PER_PAGE]
        for asset in page_assets:
            self.add_item(AudioAssetButton(asset))
        if self.page_count > 1:
            self.add_item(AudioSoundboardPageButton(max(0, self.page - 1), "←", "prev", disabled=self.page <= 0))
            self.add_item(discord.ui.Button(label="{0}/{1}".format(self.page + 1, self.page_count), style=discord.ButtonStyle.secondary, custom_id=custom_id("audio", "page_label", self.page), disabled=True, row=4))
            self.add_item(AudioSoundboardPageButton(min(self.page_count - 1, self.page + 1), "→", "next", disabled=self.page >= self.page_count - 1))
        self.add_item(AudioSoundboardBackButton())


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
    metadata = game.get("metadata_json") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    price = game_provider.format_price(
        game.get("last_known_price"),
        game.get("currency") or "JPY",
        str(metadata.get("formatted_price") or ""),
    )
    regular = game_provider.format_price(
        game.get("last_known_regular_price"),
        game.get("currency") or "JPY",
        str(metadata.get("formatted_regular_price") or ""),
    )
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
