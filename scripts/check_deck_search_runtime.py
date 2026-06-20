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
from bot.services.deck_search import DeckSearchStats, build_x_query
from bot.services.deck_search import parse_deck_search_command, search_decks
from bot.services.qr_detector import detect_qr_codes, opencv_available
from bot.services.x_search import XMedia, XPost, XSearchError, build_search_params, get_search_endpoint, parse_search_response


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
        "excluded_keywords": ["ドラゴンボール", "レジェンズ"],
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
    query = build_x_query(parsed, {}) if parsed is not None else ""
    check.add(
        "default query includes shadowverse terms",
        "シャドバ" in query and "Shadowverse" in query and "シャドウバース" in query and "SV" in query,
        query,
    )
    check.add(
        "default query includes exclusions",
        "-ドラゴンボール" in query and "-レジェンズ" in query,
        query,
    )
    check.add("missing class asks format", parse_deck_search_command("デッキ", "ask_format") is None)

    disabled_before = config.X_SEARCH_ENABLED
    token_before = config.X_BEARER_TOKEN
    config.X_SEARCH_ENABLED = False
    config.X_BEARER_TOKEN = ""
    disabled = await search_decks("g", "123", "デッキ エルフ", base_config())
    check.add("disabled search returns safe message", disabled == "デッキ検索はまだ無効", disabled)

    denied = await search_decks("g", "999", "デッキ エルフ", base_config())
    check.add("channel deny message", denied == "このチャンネルではデッキ検索は使えません。", denied)

    called_modes = []

    async def fake_search_posts(query, max_results, timeout_seconds, search_mode, lookback_days):
        called_modes.append({"mode": search_mode, "lookback_days": lookback_days, "query": query})
        return [
            XPost(
                post_id="111",
                text="エルフ デッキ QRあり",
                created_at="2026-06-20T00:00:00Z",
                media=[XMedia(media_key="m1", url="https://example.test/image.jpg", type="photo")],
            )
        ]

    async def fake_scan_post_images(post, class_label, limit, timeout_seconds, stats=None):
        if stats is not None:
            stats.image_downloaded += 1
            stats.qr_detected += 1
        return deck_search.DeckSearchResult(
            post=post,
            image_url=post.media[0].url,
            detected_class=class_label,
            qr_score=100,
            created_at=post.created_at,
        )

    original_search = deck_search.search_posts
    original_scan = deck_search.scan_post_images
    original_opencv = deck_search.opencv_available
    try:
        deck_search.search_posts = fake_search_posts
        deck_search.scan_post_images = fake_scan_post_images
        deck_search.opencv_available = lambda: True
        config.X_SEARCH_ENABLED = True
        config.X_BEARER_TOKEN = "dummy"
        response = await search_decks("g", "123", "デッキ エルフ", base_config())
        check.add("mock search formats result", "エルフのデッキ候補" in response and "https://x.com/i/web/status/111" in response, response)
        check.add("recent endpoint selected", called_modes[-1]["mode"] == "recent", str(called_modes[-1]))
        full_archive_config = base_config()
        full_archive_config["search_mode"] = "full_archive"
        full_archive_config["lookback_days"] = 14
        await search_decks("g", "123", "デッキ エルフ", full_archive_config)
        check.add(
            "full archive endpoint selected",
            called_modes[-1]["mode"] == "full_archive" and called_modes[-1]["lookback_days"] == 14,
            str(called_modes[-1]),
        )
    finally:
        deck_search.search_posts = original_search
        deck_search.scan_post_images = original_scan
        deck_search.opencv_available = original_opencv
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before


async def check_full_archive_error(check: Check) -> None:
    disabled_before = config.X_SEARCH_ENABLED
    token_before = config.X_BEARER_TOKEN
    original_search = deck_search.search_posts
    original_opencv = deck_search.opencv_available

    async def fake_forbidden_search(query, max_results, timeout_seconds, search_mode, lookback_days):
        raise XSearchError("api status 403", status_code=403, endpoint_type=search_mode)

    try:
        deck_search.search_posts = fake_forbidden_search
        deck_search.opencv_available = lambda: True
        config.X_SEARCH_ENABLED = True
        config.X_BEARER_TOKEN = "dummy"
        full_archive_config = base_config()
        full_archive_config["search_mode"] = "full_archive"
        response = await search_decks("g", "123", "デッキ エルフ", full_archive_config)
        check.add("full archive permission error is safe", response == "過去検索が使えません", response)
    finally:
        deck_search.search_posts = original_search
        deck_search.opencv_available = original_opencv
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before


def check_search_params(check: Check) -> None:
    recent_endpoint = get_search_endpoint("recent")
    full_endpoint = get_search_endpoint("full_archive")
    recent_params = build_search_params("query", 10, "recent", 14)
    archive_params = build_search_params("query", 10, "full_archive", 14)
    check.add("recent endpoint path", recent_endpoint.endswith("/2/tweets/search/recent"), recent_endpoint)
    check.add("full archive endpoint path", full_endpoint.endswith("/2/tweets/search/all"), full_endpoint)
    check.add(
        "full archive has time range",
        "start_time" in archive_params and "end_time" in archive_params and "start_time" not in recent_params,
        str(archive_params),
    )


def check_x_payload(check: Check) -> None:
    payload = {
        "data": [
            {"id": "1", "text": "deck", "created_at": "2026-06-20T00:00:00Z", "attachments": {"media_keys": ["m1"]}}
        ],
        "includes": {"media": [{"media_key": "m1", "type": "photo", "url": "https://example.test/a.jpg"}]},
    }
    posts = parse_search_response(payload)
    check.add("x payload media parsed", len(posts) == 1 and len(posts[0].media) == 1)


def check_stats(check: Check) -> None:
    stats = DeckSearchStats(x_results=20, media_posts=8, image_downloaded=5, qr_detected=0, candidates=0)
    log_text = stats.to_log()
    check.add(
        "stats log contains safe counters",
        "X results=20" in log_text and "media=8" in log_text and "downloaded=5" in log_text and "qr=0" in log_text,
        log_text,
    )


def check_qr_optional(check: Check) -> None:
    if not opencv_available():
        check.add("opencv optional", True, "not installed")
        return
    detections = detect_qr_codes(b"")
    check.add("opencv empty image is safe", detections == [])


def main() -> None:
    check = Check()
    asyncio.run(check_search_flow(check))
    asyncio.run(check_full_archive_error(check))
    asyncio.run(check_runtime_path(check))
    check_search_params(check)
    check_x_payload(check)
    check_stats(check)
    check_qr_optional(check)
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
