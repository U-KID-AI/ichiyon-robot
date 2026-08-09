import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import interaction_panel
from bot.services import voice_music


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakeResponse:
    def __init__(self):
        self.deferred = 0
        self.sent = []

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


def candidate(video_id="abc123def45", title="Life Will Change - Persona 5 OST"):
    return {
        "video_id": video_id,
        "webpage_url": "https://www.youtube.com/watch?v={0}".format(video_id),
        "title": title,
        "uploader": "Official Channel",
        "duration": 245,
    }


async def check_search_modal_flow() -> bool:
    original_search = interaction_panel.search_youtube_music_candidates
    seen_queries = []

    async def fake_search(query, guild_id, requester_id="", limit=5):
        seen_queries.append((query, guild_id, requester_id, limit))
        return [candidate(), candidate("xyz987uvw65", "Life Will Change Live")]

    try:
        interaction_panel.search_youtube_music_candidates = fake_search
        modal = interaction_panel.MusicSearchModal()
        modal.query._value = "Life Will Change Persona 5"
        interaction = FakeInteraction()
        await modal.on_submit(interaction)
        view = interaction.followup.sent[0][1].get("view") if interaction.followup.sent else None
        select = view.children[0] if view and view.children else None
        return (
            interaction.response.deferred == 1
            and seen_queries == [("Life Will Change Persona 5", "guild-a", "user-a", 5)]
            and isinstance(view, interaction_panel.MusicSearchResultView)
            and isinstance(select, interaction_panel.MusicSearchResultSelect)
            and len(select.options) == 2
            and "Official Channel" in (select.options[0].description or "")
        )
    finally:
        interaction_panel.search_youtube_music_candidates = original_search


async def check_search_select_enqueues_existing_url_path() -> bool:
    original_enqueue = interaction_panel.enqueue_music_url
    seen = []

    async def fake_enqueue(message, url):
        seen.append((message.guild.id, message.author.id, url))
        return True

    try:
        interaction_panel.enqueue_music_url = fake_enqueue
        select = interaction_panel.MusicSearchResultSelect([candidate()])
        select._values = ["abc123def45"]
        interaction = FakeInteraction()
        await select.callback(interaction)
        await select.callback(interaction)
        return (
            interaction.response.deferred == 1
            and seen == [("guild-a", "user-a", "https://www.youtube.com/watch?v=abc123def45")]
            and bool(interaction.response.sent)
        )
    finally:
        interaction_panel.enqueue_music_url = original_enqueue


def check_search_candidate_normalization() -> bool:
    entry = {
        "id": "abc123def45",
        "title": "Life Will Change",
        "uploader": "Atlus",
        "duration": 245,
    }
    normalized = voice_music.normalize_youtube_search_entry(entry)
    return bool(normalized and normalized["webpage_url"] == "https://www.youtube.com/watch?v=abc123def45" and normalized["duration"] == 245)


def check_search_options_are_flat() -> bool:
    options_seen = []

    class FakeYDL:
        def __init__(self, options):
            options_seen.append(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            return {"entries": [candidate()]}

    original_ytdlp = voice_music.yt_dlp
    try:
        voice_music.yt_dlp = SimpleNamespace(YoutubeDL=FakeYDL)
        results = voice_music.extract_youtube_search_candidates("test", "guild-a")
        return (
            len(results) == 1
            and options_seen
            and options_seen[0].get("extract_flat") is True
            and options_seen[0].get("skip_download") is True
            and options_seen[0].get("noplaylist") is True
        )
    finally:
        voice_music.yt_dlp = original_ytdlp


async def run_checks():
    results = []
    results.append(check("search modal returns candidate select", await check_search_modal_flow()))
    results.append(check("candidate select enqueues existing URL path once", await check_search_select_enqueues_existing_url_path()))
    results.append(check("search candidate builds canonical URL", check_search_candidate_normalization()))
    results.append(check("ytsearch uses flat extraction options", check_search_options_are_flat()))
    return all(results)


def main() -> int:
    return 0 if asyncio.run(run_checks()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
