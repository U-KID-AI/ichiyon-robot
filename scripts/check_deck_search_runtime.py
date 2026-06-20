import asyncio
import sys
from pathlib import Path
from typing import Any, Dict


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot import config
from bot.services import deck_search
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
    check_x_payload(check)
    check_qr_optional(check)
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
