import asyncio
import sys
from pathlib import Path
from typing import Any, Dict


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot import config
from bot.services import deck_search
from bot.services import runtime_db
from bot.services.deck_search import parse_deck_search_command, search_decks
from bot.services.qr_detector import detect_qr_codes, opencv_available
from bot.services.x_search import XMedia, XPost, parse_search_response


class Check:
    def __init__(self) -> None:
        self.results = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append({"name": name, "ok": ok, "detail": detail})

    def print_results(self) -> None:
        for result in self.results:
            label = "OK" if result["ok"] else "NG"
            detail = " - {0}".format(result["detail"]) if result["detail"] else ""
            print("[{0}] {1}{2}".format(label, result["name"], detail))
        passed = len([result for result in self.results if result["ok"]])
        print("summary: {0}/{1} OK".format(passed, len(self.results)))

    def ok(self) -> bool:
        return all(result["ok"] for result in self.results)


class FakeChannel:
    def __init__(self, channel_id: str = "123") -> None:
        self.id = channel_id
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


class FakeAuthor:
    bot = False
    id = 111
    display_name = "DeckUser"
    name = "DeckUser"
    mention = "<@111>"


class FakeGuild:
    id = "guild"


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.channel = FakeChannel()
        self.author = FakeAuthor()
        self.guild = FakeGuild()
        self.mentions = []

    async def add_reaction(self, emoji) -> None:
        pass


class FakeMentionReactionRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_reactions(self, guild_id, enabled=None, reaction_kind=None, include_system=True):
        if reaction_kind == "random_draw":
            return []
        if reaction_kind == "search":
            return [
                {
                    "id": 10,
                    "guild_id": guild_id,
                    "reaction_key": "deck_search",
                    "keyword": "デッキ",
                    "match_type": "prefix",
                    "reaction_kind": "search",
                    "name": "デッキ検索",
                    "enabled": True,
                    "config_json": base_config(),
                    "created_at": "2026-06-20T00:00:00Z",
                }
            ]
        return []


def base_config() -> Dict[str, Any]:
    return {
        "search_type": "deck_search",
        "allowed_channel_ids": ["123"],
        "max_results": 3,
        "deny_message": "このチャンネルではデッキ検索は使えません。",
        "missing_format_behavior": "ask_format",
        "cache_ttl_seconds": 0,
        "request_timeout_seconds": 1,
        "image_scan_limit": 3,
    }


async def check_runtime_path(check: Check) -> None:
    disabled_before = config.X_SEARCH_ENABLED
    token_before = config.X_BEARER_TOKEN
    repository_before = runtime_db.MentionReactionRepository
    feature_before = runtime_db.feature_enabled
    limited_before = runtime_db.list_limited_effects
    command_before = runtime_db.get_mention_command_text
    try:
        config.X_SEARCH_ENABLED = False
        config.X_BEARER_TOKEN = ""
        runtime_db.MentionReactionRepository = FakeMentionReactionRepository
        runtime_db.feature_enabled = lambda connection, guild_id, feature_key: True
        runtime_db.list_limited_effects = lambda connection, guild_id, message: []
        runtime_db.get_mention_command_text = lambda message: "デッキ エルフ"
        message = FakeMessage("@bot デッキ エルフ")
        action = await runtime_db.process_db_mention(message, "guild", object())
        check.add(
            "DB runtime deck search responds without limited effects",
            action.handled is True and message.channel.sent == ["デッキ検索はまだ無効"],
            str(message.channel.sent),
        )
    finally:
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before
        runtime_db.MentionReactionRepository = repository_before
        runtime_db.feature_enabled = feature_before
        runtime_db.list_limited_effects = limited_before
        runtime_db.get_mention_command_text = command_before


async def check_search_flow(check: Check) -> None:
    parsed = parse_deck_search_command("デッキ elf", "ask_format")
    check.add("class alias elf", parsed is not None and parsed.class_key == "elf")
    check.add("missing class asks format", parse_deck_search_command("デッキ", "ask_format") is None)

    disabled_before = config.X_SEARCH_ENABLED
    token_before = config.X_BEARER_TOKEN
    config.X_SEARCH_ENABLED = False
    config.X_BEARER_TOKEN = ""
    disabled = await search_decks("g", "123", "デッキ エルフ", base_config())
    check.add("disabled search returns safe message", disabled == "デッキ検索はまだ無効", disabled)

    denied = await search_decks("g", "999", "デッキ エルフ", base_config())
    check.add("channel deny message", denied == "このチャンネルではデッキ検索は使えません。", denied)

    async def fake_search_recent_posts(query, max_results, timeout_seconds):
        return [
            XPost(
                post_id="111",
                text="エルフ デッキ QRあり",
                created_at="2026-06-20T00:00:00Z",
                media=[XMedia(media_key="m1", url="https://example.test/image.jpg", type="photo")],
            )
        ]

    async def fake_scan_post_images(post, class_label, limit, timeout_seconds):
        return deck_search.DeckSearchResult(
            post=post,
            image_url=post.media[0].url,
            detected_class=class_label,
            qr_score=100,
            created_at=post.created_at,
        )

    original_search = deck_search.search_recent_posts
    original_scan = deck_search.scan_post_images
    original_opencv = deck_search.opencv_available
    try:
        deck_search.search_recent_posts = fake_search_recent_posts
        deck_search.scan_post_images = fake_scan_post_images
        deck_search.opencv_available = lambda: True
        config.X_SEARCH_ENABLED = True
        config.X_BEARER_TOKEN = "dummy"
        response = await search_decks("g", "123", "デッキ エルフ", base_config())
        check.add("mock search formats result", "エルフのデッキ候補" in response and "https://x.com/i/web/status/111" in response, response)
    finally:
        deck_search.search_recent_posts = original_search
        deck_search.scan_post_images = original_scan
        deck_search.opencv_available = original_opencv
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before


def check_x_payload(check: Check) -> None:
    payload = {
        "data": [
            {"id": "1", "text": "deck", "created_at": "2026-06-20T00:00:00Z", "attachments": {"media_keys": ["m1"]}}
        ],
        "includes": {"media": [{"media_key": "m1", "type": "photo", "url": "https://example.test/a.jpg"}]},
    }
    posts = parse_search_response(payload)
    check.add("x payload media parsed", len(posts) == 1 and len(posts[0].media) == 1)


def check_qr_optional(check: Check) -> None:
    if not opencv_available():
        check.add("opencv optional", True, "not installed")
        return
    detections = detect_qr_codes(b"")
    check.add("opencv empty image is safe", detections == [])


def main() -> None:
    check = Check()
    asyncio.run(check_search_flow(check))
    asyncio.run(check_runtime_path(check))
    check_x_payload(check)
    check_qr_optional(check)
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
