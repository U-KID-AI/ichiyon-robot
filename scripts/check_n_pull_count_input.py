import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import interaction_panel


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakeResponse:
    def __init__(self):
        self.modals = []
        self.deferred = 0
        self.sent = []

    async def send_modal(self, modal):
        self.modals.append(modal)

    async def defer(self, **kwargs):
        self.deferred += 1

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))

    def is_done(self):
        return self.deferred > 0 or bool(self.sent)


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class FakeInteraction:
    def __init__(self):
        self.id = id(self)
        self.guild = SimpleNamespace(id="guild-a")
        self.user = SimpleNamespace(id="user-a")
        self.channel = SimpleNamespace(id="channel-a")
        self.response = FakeResponse()
        self.followup = FakeFollowup()


async def check_select_opens_count_modal() -> bool:
    preset = {"id": 1, "display_name": "しゃろう", "command_name": "しゃろう", "max_pulls": 100}
    select = interaction_panel.YouTubeNPullPresetSelect([preset])
    select._values = ["1"]
    interaction = FakeInteraction()
    await select.callback(interaction)
    modal = interaction.response.modals[0] if interaction.response.modals else None
    return (
        isinstance(modal, interaction_panel.YouTubeNPullCountModal)
        and str(modal.count) == "1"
        and modal.preset.get("max_pulls") == 100
    )


async def check_count_modal_uses_runtime_count(count_text: str, expected_command: str) -> bool:
    original_handler = interaction_panel.handle_youtube_n_pull_command
    seen = []

    async def fake_handler(message, command_text):
        seen.append(command_text)
        return True

    try:
        interaction_panel.handle_youtube_n_pull_command = fake_handler
        modal = interaction_panel.YouTubeNPullCountModal({"id": 1, "display_name": "しゃろう", "command_name": "しゃろう", "max_pulls": 100})
        modal.count._value = count_text
        await modal.on_submit(FakeInteraction())
        return seen == [expected_command]
    finally:
        interaction_panel.handle_youtube_n_pull_command = original_handler


async def check_ascii_count_modal_uses_runtime_count(count_text: str, expected_count: int) -> bool:
    original_handler = interaction_panel.handle_youtube_n_pull_command
    seen = []

    async def fake_handler(message, command_text):
        seen.append(command_text)
        return True

    try:
        interaction_panel.handle_youtube_n_pull_command = fake_handler
        modal = interaction_panel.YouTubeNPullCountModal({"id": 1, "display_name": "preset", "command_name": "preset", "max_pulls": 100})
        modal.count._value = count_text
        await modal.on_submit(FakeInteraction())
        return (
            len(seen) == 1
            and seen[0].startswith("preset ")
            and str(expected_count) in seen[0]
            and modal.preset.get("max_pulls") == 100
        )
    finally:
        interaction_panel.handle_youtube_n_pull_command = original_handler


async def check_invalid_count_is_safe(count_text: str) -> bool:
    original_handler = interaction_panel.handle_youtube_n_pull_command

    async def fake_handler(message, command_text):
        raise AssertionError("runtime should not be called")

    try:
        interaction_panel.handle_youtube_n_pull_command = fake_handler
        modal = interaction_panel.YouTubeNPullCountModal({"id": 1, "display_name": "しゃろう", "command_name": "しゃろう", "max_pulls": 100})
        modal.count._value = count_text
        interaction = FakeInteraction()
        await modal.on_submit(interaction)
        return interaction.response.deferred == 1 and bool(interaction.followup.sent)
    finally:
        interaction_panel.handle_youtube_n_pull_command = original_handler


async def run_checks():
    results = []
    results.append(check("1 input reaches existing runtime", await check_ascii_count_modal_uses_runtime_count("1", 1)))
    results.append(check("100 input keeps max_pulls as limit only", await check_ascii_count_modal_uses_runtime_count("100", 100)))
    results.append(check("preset select opens N input modal", await check_select_opens_count_modal()))
    results.append(check("manual 100 input reaches existing runtime", await check_count_modal_uses_runtime_count("100", "しゃろう 100連")))
    results.append(check("10 input reaches existing runtime", await check_count_modal_uses_runtime_count("10", "しゃろう 10連")))
    results.append(check("30 input reaches existing runtime", await check_count_modal_uses_runtime_count("30", "しゃろう 30連")))
    results.append(check("invalid blank count is safe", await check_invalid_count_is_safe("")))
    results.append(check("invalid non-integer count is safe", await check_invalid_count_is_safe("abc")))
    return all(results)


def main() -> int:
    return 0 if asyncio.run(run_checks()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
