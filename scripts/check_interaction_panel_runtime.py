import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import discord


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import interaction_panel


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class FakeMessage:
    def __init__(self):
        self.author = SimpleNamespace(bot=False)
        self.guild = SimpleNamespace(id="guild-a")
        self.channel = FakeChannel()


class FakeResponse:
    def __init__(self):
        self.deferred = 0
        self.sent = []
        self.edited = []

    async def defer(self, **kwargs):
        self.deferred += 1

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))

    async def edit_message(self, *args, **kwargs):
        self.edited.append((args, kwargs))

    def is_done(self):
        return self.deferred > 0 or bool(self.sent or self.edited)


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class FakeInteraction:
    def __init__(self, connected=True, user_in_voice=True):
        voice_client = SimpleNamespace(is_connected=lambda: connected)
        self.guild = SimpleNamespace(id="guild-a", voice_client=voice_client)
        channel = SimpleNamespace(connect=self._connect)
        self.user = SimpleNamespace(id="user-a", voice=SimpleNamespace(channel=channel) if user_in_voice else None)
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.channel = SimpleNamespace(id="channel-a")
        self.connected_calls = 0

    async def _connect(self):
        self.connected_calls += 1


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeAudioAssetRepository:
    assets = []

    def __init__(self, connection):
        self.connection = connection

    def get_asset(self, guild_id, asset_id, enabled=None):
        for asset in self.assets:
            if int(asset["id"]) == int(asset_id) and (enabled is None or bool(asset.get("enabled", True)) == enabled):
                return asset
        return None

    def list_assets(self, guild_id, enabled=None, category=None, limit=None):
        rows = [asset for asset in self.assets if enabled is None or bool(asset.get("enabled", True)) == enabled]
        return rows[:limit] if limit is not None else rows


def audio_assets(count=3):
    return [
        {
            "id": index,
            "display_name": "SE{0}".format(index),
            "enabled": True,
            "storage_path": "dummy{0}.wav".format(index),
        }
        for index in range(1, count + 1)
    ]


async def check_music_panel_without_voice() -> bool:
    original_get_voice = interaction_panel.get_guild_voice_client
    try:
        interaction_panel.get_guild_voice_client = lambda guild: None
        message = FakeMessage()
        handled = await interaction_panel.handle_context_panel_command(message, "音楽")
        return handled is True and len(message.channel.sent) == 1
    finally:
        interaction_panel.get_guild_voice_client = original_get_voice


async def check_audio_command_opens_soundboard() -> bool:
    original_get_connection = interaction_panel.get_connection
    original_repo = interaction_panel.AudioAssetRepository
    try:
        FakeAudioAssetRepository.assets = audio_assets(2)
        interaction_panel.get_connection = lambda: FakeConnection()
        interaction_panel.AudioAssetRepository = FakeAudioAssetRepository
        message = FakeMessage()
        handled = await interaction_panel.handle_context_panel_command(message, "SE")
        if handled is not True or len(message.channel.sent) != 1:
            return False
        view = message.channel.sent[0][1].get("view")
        return isinstance(view, interaction_panel.AudioSoundboardView) and not any(isinstance(item, discord.ui.Select) for item in view.children)
    finally:
        interaction_panel.get_connection = original_get_connection
        interaction_panel.AudioAssetRepository = original_repo


async def check_repeated_soundboard_clicks() -> bool:
    original_get_connection = interaction_panel.get_connection
    original_repo = interaction_panel.AudioAssetRepository
    original_play = interaction_panel.play_audio_asset_row
    calls = []

    async def fake_play(guild, asset):
        calls.append((guild.id, asset["id"]))
        return True, ""

    try:
        FakeAudioAssetRepository.assets = audio_assets(1)
        interaction_panel.get_connection = lambda: FakeConnection()
        interaction_panel.AudioAssetRepository = FakeAudioAssetRepository
        interaction_panel.play_audio_asset_row = fake_play
        view = interaction_panel.AudioSoundboardView(FakeAudioAssetRepository.assets)
        button = next(item for item in view.children if isinstance(item, interaction_panel.AudioAssetButton))
        interactions = [FakeInteraction() for _ in range(3)]
        for interaction in interactions:
            await button.callback(interaction)
        return (
            len(calls) == 3
            and all(interaction.response.deferred == 1 for interaction in interactions)
            and all(not interaction.followup.sent for interaction in interactions)
            and button.disabled is False
            and not view.is_finished()
        )
    finally:
        interaction_panel.get_connection = original_get_connection
        interaction_panel.AudioAssetRepository = original_repo
        interaction_panel.play_audio_asset_row = original_play


async def check_soundboard_vc_disconnected_safe() -> bool:
    original_get_connection = interaction_panel.get_connection
    original_repo = interaction_panel.AudioAssetRepository
    original_play = interaction_panel.play_audio_asset_row
    try:
        FakeAudioAssetRepository.assets = audio_assets(1)
        interaction_panel.get_connection = lambda: FakeConnection()
        interaction_panel.AudioAssetRepository = FakeAudioAssetRepository
        interaction_panel.play_audio_asset_row = lambda guild, asset: (_ for _ in ()).throw(AssertionError("should not play"))
        view = interaction_panel.AudioSoundboardView(FakeAudioAssetRepository.assets)
        button = next(item for item in view.children if isinstance(item, interaction_panel.AudioAssetButton))
        interaction = FakeInteraction(connected=False, user_in_voice=False)
        await button.callback(interaction)
        return interaction.response.deferred == 1 and bool(interaction.followup.sent) and button.disabled is False and not view.is_finished()
    finally:
        interaction_panel.get_connection = original_get_connection
        interaction_panel.AudioAssetRepository = original_repo
        interaction_panel.play_audio_asset_row = original_play


def custom_ids(view):
    return [item.custom_id for item in view.children if hasattr(item, "custom_id") and item.custom_id]


def main() -> int:
    results = []
    results.append(check("mention-only empty command is detected", interaction_panel.mention_text_is_empty("")))
    results.append(check("mention-only whitespace is detected", interaction_panel.mention_text_is_empty(" \t")))
    results.append(check("non-empty mention is not empty", not interaction_panel.mention_text_is_empty("歌え https://youtu.be/x")))
    results.append(check("empty mention is not panel command", interaction_panel.panel_command_kind("") is None))
    results.append(check("game command opens game panel", interaction_panel.panel_command_kind("ゲーム") == "game"))
    results.append(check("audio command opens audio panel", interaction_panel.panel_command_kind("SE") == "audio"))
    results.append(check("voice command opens audio panel", interaction_panel.panel_command_kind("音声") == "audio"))
    results.append(check("music command opens music panel", interaction_panel.panel_command_kind("音楽") == "music"))
    results.append(check("root panel command opens root panel", interaction_panel.panel_command_kind("パネル") == "root"))
    results.append(check("explicit music panel does not require VC connection", asyncio.run(check_music_panel_without_voice())))
    results.append(check("explicit audio panel opens soundboard", asyncio.run(check_audio_command_opens_soundboard())))
    results.append(check("shortcut text is not panel command", interaction_panel.panel_command_kind("ニコロデオン") is None))

    view = interaction_panel.MainPanelView()
    button_ids = custom_ids(view)
    results.append(check("main panel has music button", any("main:music" in item for item in button_ids)))
    results.append(check("main panel has audio button", any("main:audio" in item for item in button_ids)))
    results.append(check("main panel has game button", any("main:game" in item for item in button_ids)))
    results.append(check("main panel has status button", any("main:status" in item for item in button_ids)))
    results.append(check("main panel has close button", any("main:close" in item for item in button_ids)))
    results.append(check("custom ids are persistent scoped", all(item.startswith("ichiyon_panel:") for item in button_ids)))

    soundboard = interaction_panel.AudioSoundboardView(audio_assets(3))
    soundboard_ids = custom_ids(soundboard)
    results.append(check("soundboard has direct asset buttons", sum(isinstance(item, interaction_panel.AudioAssetButton) for item in soundboard.children) == 3))
    results.append(check("soundboard has no select", not any(isinstance(item, discord.ui.Select) for item in soundboard.children)))
    results.append(check("soundboard has no stop button", not any("audio:stop" in item for item in soundboard_ids), str(soundboard_ids)))
    results.append(check("soundboard has back button", any("audio:soundboard_back" in item for item in soundboard_ids), str(soundboard_ids)))
    results.append(check("soundboard asset labels use display name", any(getattr(item, "label", "") == "SE1" for item in soundboard.children)))
    results.append(check("soundboard asset button stores id", any(getattr(item, "asset_id", None) == 1 for item in soundboard.children)))
    results.append(check("soundboard buttons stay repeatable", asyncio.run(check_repeated_soundboard_clicks())))
    results.append(check("soundboard handles missing VC safely", asyncio.run(check_soundboard_vc_disconnected_safe())))

    paged = interaction_panel.AudioSoundboardView(audio_assets(21))
    paged_ids = custom_ids(paged)
    results.append(check("soundboard paginates over component limit", paged.page_count == 2 and sum(isinstance(item, interaction_panel.AudioAssetButton) for item in paged.children) == 20))
    results.append(check("soundboard has next page button", any("audio:page" in item for item in paged_ids), str(paged_ids)))

    music_view = interaction_panel.MusicPanelView()
    music_ids = custom_ids(music_view)
    for required in ("join", "pause", "resume", "skip", "stop", "now", "queue", "loop", "shuffle", "volume", "search_add", "add", "n_pull", "back"):
        results.append(check("music button {0}".format(required), any("music:{0}".format(required) in item for item in music_ids)))

    game_ids = custom_ids(interaction_panel.GamePanelView())
    results.append(check("game panel has search button", any("game:search" in item for item in game_ids)))
    results.append(check("game panel has recent button", any("game:recent" in item for item in game_ids)))
    results.append(check("game panel has back button", any("game:back" in item for item in game_ids)))
    results.append(check("game panel removed owned list button", not any("game:owned_list" in item for item in game_ids), str(game_ids)))
    results.append(check("game panel removed wishlist list button", not any("game:wishlist_list" in item for item in game_ids), str(game_ids)))
    results.append(check("game panel removed backlog list button", not any("game:backlog_list" in item for item in game_ids), str(game_ids)))

    presets = [{"id": 1, "display_name": "しゃろう", "command_name": "しゃろう", "max_pulls": 10}]
    select_view = interaction_panel.YouTubeNPullPresetView(presets)
    results.append(check("youtube n-pull preset view has select", any(item.custom_id.endswith("music:n_pull_select") for item in select_view.children if hasattr(item, "custom_id"))))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
