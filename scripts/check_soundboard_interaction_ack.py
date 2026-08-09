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


class FakeResponse:
    def __init__(self, events, already_done=False):
        self.events = events
        self.deferred = 0
        self.already_done = already_done

    async def defer(self, **kwargs):
        self.events.append(("defer", kwargs.get("ephemeral")))
        self.deferred += 1

    def is_done(self):
        return self.already_done or self.deferred > 0


class FakeFollowup:
    def __init__(self, events):
        self.events = events
        self.sent = []

    async def send(self, *args, **kwargs):
        self.events.append(("followup", bool(kwargs.get("ephemeral"))))
        self.sent.append((args, kwargs))


class FakeInteraction:
    def __init__(self, events=None, already_done=False, connected=True, guild_id="guild-a"):
        self.events = events if events is not None else []
        self.guild = SimpleNamespace(id=guild_id, voice_client=SimpleNamespace(is_connected=lambda: connected))
        self.guild_id = guild_id
        self.user = SimpleNamespace(id="user-a")
        self.channel = SimpleNamespace(id="channel-a")
        self.response = FakeResponse(self.events, already_done=already_done)
        self.followup = FakeFollowup(self.events)


class FakeConnection:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append(("db_enter", None))
        return self

    def __exit__(self, exc_type, exc, tb):
        self.events.append(("db_exit", None))
        return False


class FakeAudioAssetRepository:
    def __init__(self, connection, bot_id):
        self.connection = connection
        self.bot_id = bot_id

    def get_asset(self, guild_id, asset_id, enabled=True):
        self.connection.events.append(("asset_lookup", asset_id))
        return {
            "id": asset_id,
            "guild_id": guild_id,
            "display_name": "SE",
            "enabled": True,
            "storage_path": "dummy.wav",
        }


async def check_ack_before_lookup_and_enqueue() -> bool:
    events = []
    original_get_connection = interaction_panel.get_connection
    original_repo = interaction_panel.AudioAssetRepository
    original_play = interaction_panel.play_audio_asset_row

    async def fake_play(guild, asset, volume_percent=None, reaction_type=None, reaction_key=None):
        events.append(("enqueue", asset["id"]))
        return True, ""

    try:
        interaction_panel.get_connection = lambda: FakeConnection(events)
        interaction_panel.AudioAssetRepository = FakeAudioAssetRepository
        interaction_panel.play_audio_asset_row = fake_play
        await interaction_panel.AudioAssetDynamicButton(1, label="SE1").callback(FakeInteraction(events))
        names = [event[0] for event in events]
        return names.index("defer") < names.index("db_enter") < names.index("asset_lookup") < names.index("enqueue")
    finally:
        interaction_panel.get_connection = original_get_connection
        interaction_panel.AudioAssetRepository = original_repo
        interaction_panel.play_audio_asset_row = original_play


async def check_three_clicks_ack_and_enqueue() -> bool:
    events = []
    original_get_connection = interaction_panel.get_connection
    original_repo = interaction_panel.AudioAssetRepository
    original_play = interaction_panel.play_audio_asset_row

    async def fake_play(guild, asset, volume_percent=None, reaction_type=None, reaction_key=None):
        events.append(("enqueue", asset["id"]))
        return True, ""

    try:
        interaction_panel.get_connection = lambda: FakeConnection(events)
        interaction_panel.AudioAssetRepository = FakeAudioAssetRepository
        interaction_panel.play_audio_asset_row = fake_play
        button = interaction_panel.AudioAssetDynamicButton(1, label="SE1")
        interactions = [FakeInteraction(events) for _ in range(3)]
        for interaction in interactions:
            await button.callback(interaction)
        return (
            sum(1 for event in events if event[0] == "defer") == 3
            and sum(1 for event in events if event[0] == "enqueue") == 3
            and all(interaction.response.deferred == 1 for interaction in interactions)
        )
    finally:
        interaction_panel.get_connection = original_get_connection
        interaction_panel.AudioAssetRepository = original_repo
        interaction_panel.play_audio_asset_row = original_play


async def check_already_acked_does_not_double_defer() -> bool:
    events = []
    original_get_connection = interaction_panel.get_connection
    original_repo = interaction_panel.AudioAssetRepository
    original_play = interaction_panel.play_audio_asset_row

    async def fake_play(guild, asset, volume_percent=None, reaction_type=None, reaction_key=None):
        events.append(("enqueue", asset["id"]))
        return True, ""

    try:
        interaction_panel.get_connection = lambda: FakeConnection(events)
        interaction_panel.AudioAssetRepository = FakeAudioAssetRepository
        interaction_panel.play_audio_asset_row = fake_play
        interaction = FakeInteraction(events, already_done=True)
        await interaction_panel.AudioAssetDynamicButton(1, label="SE1").callback(interaction)
        return interaction.response.deferred == 0 and any(event[0] == "enqueue" for event in events)
    finally:
        interaction_panel.get_connection = original_get_connection
        interaction_panel.AudioAssetRepository = original_repo
        interaction_panel.play_audio_asset_row = original_play


async def check_missing_asset_ack_then_error() -> bool:
    events = []
    original_get_connection = interaction_panel.get_connection
    original_repo = interaction_panel.AudioAssetRepository

    class MissingRepository(FakeAudioAssetRepository):
        def get_asset(self, guild_id, asset_id, enabled=True):
            self.connection.events.append(("asset_lookup", asset_id))
            return None

    try:
        interaction_panel.get_connection = lambda: FakeConnection(events)
        interaction_panel.AudioAssetRepository = MissingRepository
        interaction = FakeInteraction(events)
        await interaction_panel.AudioAssetDynamicButton(1, label="SE1").callback(interaction)
        names = [event[0] for event in events]
        return names.index("defer") < names.index("asset_lookup") < names.index("followup")
    finally:
        interaction_panel.get_connection = original_get_connection
        interaction_panel.AudioAssetRepository = original_repo


async def check_from_custom_id_is_lightweight() -> bool:
    match = interaction_panel.AudioAssetDynamicButton.__discord_ui_compiled_template__.fullmatch(
        "ichiyon_panel:ichiyon:audio:asset:123"
    )
    if match is None:
        return False
    item = discord.ui.Button(label="SE", custom_id="ichiyon_panel:ichiyon:audio:asset:123")
    dynamic = await interaction_panel.AudioAssetDynamicButton.from_custom_id(FakeInteraction([]), item, match)
    return dynamic.bot_id == "ichiyon" and dynamic.asset_id == 123 and dynamic.label == "SE"


async def run_checks() -> bool:
    results = []
    results.append(check("callback defers before db lookup and enqueue", await check_ack_before_lookup_and_enqueue()))
    results.append(check("three clicks create three ACKs and enqueues", await check_three_clicks_ack_and_enqueue()))
    results.append(check("already acknowledged interaction is not double-deferred", await check_already_acked_does_not_double_defer()))
    results.append(check("missing asset is ACKed before ephemeral error", await check_missing_asset_ack_then_error()))
    results.append(check("DynamicItem from_custom_id stays lightweight", await check_from_custom_id_is_lightweight()))
    return all(results)


def main() -> int:
    return 0 if asyncio.run(run_checks()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
