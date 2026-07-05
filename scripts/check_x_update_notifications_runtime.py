import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import x_search
from bot.services import x_update_notifications as updates


class Check:
    def __init__(self) -> None:
        self.results = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))

    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.results)

    def print_results(self) -> None:
        for name, ok, detail in self.results:
            status = "OK" if ok else "NG"
            if detail:
                print("[{0}] {1} - {2}".format(status, name, detail))
            else:
                print("[{0}] {1}".format(status, name))
        passed = len([item for item in self.results if item[1]])
        print("summary: {0}/{1} OK".format(passed, len(self.results)))


class FakeMessage:
    def __init__(self, message_id: str) -> None:
        self.id = message_id


class FakeChannel:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, text: str):
        self.sent.append(text)
        return FakeMessage("message-{0}".format(len(self.sent)))


class FakeBot:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    def get_channel(self, channel_id: int):
        return self.channel

    async def fetch_channel(self, channel_id: int):
        return self.channel


class FakeRepository:
    def __init__(self) -> None:
        self.identity_updates = []
        self.success_updates = []
        self.error_updates = []
        self.histories = []
        self.posted = []
        self.duplicates = set()

    def update_user_identity(
        self,
        watch_id: int,
        x_user_id: str,
        display_name: Optional[str],
        x_username: Optional[str] = None,
    ) -> None:
        self.identity_updates.append((watch_id, x_user_id, display_name, x_username))

    def mark_checked_success(
        self,
        watch_id: int,
        last_seen_post_id: Optional[str],
        last_posted_post_id: Optional[str],
    ) -> None:
        self.success_updates.append((watch_id, last_seen_post_id, last_posted_post_id))

    def mark_checked_error(self, watch_id: int, error: str) -> None:
        self.error_updates.append((watch_id, error))

    def record_history(
        self,
        watch_id: int,
        post_id: str,
        post_url: str,
        post_text: Optional[str],
        posted_channel_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if (watch_id, post_id) in self.duplicates:
            return None
        history = {"id": len(self.histories) + 1, "watch_id": watch_id, "post_id": post_id}
        self.histories.append(history)
        return history

    def mark_history_posted(self, history_id: int, message_id: Optional[str]) -> None:
        self.posted.append((history_id, message_id))


def make_watch(**overrides) -> Dict[str, Any]:
    watch = {
        "id": 1,
        "guild_id": "g",
        "channel_id": "123",
        "x_username": "example",
        "x_user_id": "u1",
        "display_name": "Example",
        "enabled": True,
        "include_replies": False,
        "include_reposts": False,
        "include_quotes": False,
        "check_interval_seconds": 900,
        "last_seen_post_id": "100",
        "last_posted_post_id": None,
        "post_template": updates.DEFAULT_POST_TEMPLATE,
        "include_keywords": "",
        "exclude_keywords": "",
    }
    watch.update(overrides)
    return watch


def make_post(post_id: str, text: str = "hello", ref_types: Optional[List[str]] = None) -> x_search.XPost:
    return x_search.XPost(
        post_id=post_id,
        text=text,
        created_at="2026-07-04T00:00:00Z",
        media=[],
        referenced_types=ref_types or [],
    )


def check_helpers(check: Check) -> None:
    post = make_post("101", "本文")
    watch = make_watch(post_template="{account_name} / @{username} / {post_text} / {post_url} / {created_at}")
    rendered = updates.render_post_template(watch, post)
    check.add("template renders account and url", "Example / @example / 本文 / https://x.com/i/web/status/101" in rendered, rendered)
    check.add("normal post is allowed", updates.should_post_update(make_post("102"), watch) is True)
    check.add("reply is skipped by default", updates.should_post_update(make_post("103", ref_types=["replied_to"]), watch) is False)
    check.add("repost is skipped by default", updates.should_post_update(make_post("104", ref_types=["retweeted"]), watch) is False)
    check.add("quote is skipped by default", updates.should_post_update(make_post("105", ref_types=["quoted"]), watch) is False)
    check.add("reply can be enabled", updates.should_post_update(make_post("106", ref_types=["replied_to"]), make_watch(include_replies=True)) is True)
    keywords = updates.parse_keyword_list(" シャドバ,Shadowverse\nシャドバ\n  ")
    check.add("keyword parser trims and deduplicates", keywords == ["シャドバ", "Shadowverse"], str(keywords))
    include_watch = make_watch(include_keywords="シャドバ\nShadowverse")
    check.add("include keyword allows matching japanese text", updates.should_post_update(make_post("107", "今日はシャドバ"), include_watch) is True)
    check.add("include keyword is case insensitive", updates.should_post_update(make_post("108", "new shadowverse deck"), include_watch) is True)
    check.add("include keyword blocks nonmatching text", updates.should_post_update(make_post("109", "雑談"), include_watch) is False)
    exclude_watch = make_watch(include_keywords="シャドバ", exclude_keywords="PR\nキャンペーン")
    check.add("exclude keyword wins over include", updates.should_post_update(make_post("110", "シャドバ PR"), exclude_watch) is False)
    query = updates.build_x_update_search_query("example", ["シャドバ", "Shadowverse", "https://bad.example"], ["PR", "@bad"])
    check.add("keyword query includes safe include terms", "(シャドバ OR Shadowverse)" in query and "https://" not in query, query)
    check.add("keyword query includes safe exclude terms", "-PR" in query and "@bad" not in query, query)


async def check_initial_sync(check: Check) -> None:
    original_lookup = x_search.lookup_user_by_username
    original_posts = x_search.get_user_posts
    repository = FakeRepository()
    channel = FakeChannel()
    bot = FakeBot(channel)

    async def fake_lookup(username: str, timeout_seconds: int = 10):
        return x_search.XUser(user_id="u-initial", username=username, name="Initial User")

    async def fake_posts(user_id: str, since_id: Optional[str], max_results: int, timeout_seconds: int = 10):
        return [make_post("101"), make_post("102")]

    try:
        x_search.lookup_user_by_username = fake_lookup
        x_search.get_user_posts = fake_posts
        sent = await updates.process_x_update_watch(bot, repository, make_watch(x_user_id="", last_seen_post_id=None))
        check.add("initial sync sends nothing", sent == 0 and channel.sent == [], str(channel.sent))
        check.add("initial sync stores newest post id", repository.success_updates[-1][1] == "102", str(repository.success_updates))
        check.add("initial sync resolves user id once", repository.identity_updates[-1][1] == "u-initial", str(repository.identity_updates))
    finally:
        x_search.lookup_user_by_username = original_lookup
        x_search.get_user_posts = original_posts


async def check_normal_posting(check: Check) -> None:
    original_posts = x_search.get_user_posts
    repository = FakeRepository()
    channel = FakeChannel()
    bot = FakeBot(channel)

    async def fake_posts(user_id: str, since_id: Optional[str], max_results: int, timeout_seconds: int = 10):
        return [
            make_post("101", "old"),
            make_post("102", "reply", ["replied_to"]),
            make_post("103", "new"),
        ]

    try:
        x_search.get_user_posts = fake_posts
        sent = await updates.process_x_update_watch(bot, repository, make_watch())
        check.add("normal check posts only allowed updates", sent == 2 and len(channel.sent) == 2, str(channel.sent))
        check.add("normal check stores newest seen id", repository.success_updates[-1][1] == "103", str(repository.success_updates))
        check.add("normal check stores last posted id", repository.success_updates[-1][2] == "103", str(repository.success_updates))
    finally:
        x_search.get_user_posts = original_posts


async def check_duplicate_history(check: Check) -> None:
    original_posts = x_search.get_user_posts
    repository = FakeRepository()
    repository.duplicates.add((1, "101"))
    channel = FakeChannel()
    bot = FakeBot(channel)

    async def fake_posts(user_id: str, since_id: Optional[str], max_results: int, timeout_seconds: int = 10):
        return [make_post("101"), make_post("102")]

    try:
        x_search.get_user_posts = fake_posts
        sent = await updates.process_x_update_watch(bot, repository, make_watch())
        check.add("duplicate history is not posted twice", sent == 1 and len(channel.sent) == 1, str(channel.sent))
        check.add("different post is still recorded", repository.histories[-1]["post_id"] == "102", str(repository.histories))
    finally:
        x_search.get_user_posts = original_posts


async def check_keyword_search_query(check: Check) -> None:
    original_recent = x_search.search_recent_posts
    original_timeline = x_search.get_user_posts
    repository = FakeRepository()
    channel = FakeChannel()
    bot = FakeBot(channel)
    calls = []

    async def fake_recent(query: str, max_results: int, timeout_seconds: int, since_id: Optional[str] = None):
        calls.append(("recent", query, since_id))
        return [
            make_post("101", "シャドバ デッキ"),
            make_post("102", "シャドバ PR"),
            make_post("103", "雑談"),
        ]

    async def fake_timeline(user_id: str, since_id: Optional[str], max_results: int, timeout_seconds: int = 10):
        calls.append(("timeline", user_id, since_id))
        return [make_post("104", "シャドバ デッキ")]

    try:
        x_search.search_recent_posts = fake_recent
        x_search.get_user_posts = fake_timeline
        sent = await updates.process_x_update_watch(
            bot,
            repository,
            make_watch(include_keywords="シャドバ", exclude_keywords="PR"),
        )
        check.add("keyword filter uses recent search query", calls and calls[0][0] == "recent", str(calls))
        check.add("keyword query keeps since id", calls and calls[0][2] == "100", str(calls))
        check.add("keyword filter posts only matching non-excluded updates", sent == 1 and len(channel.sent) == 1, str(channel.sent))
        calls.clear()
        channel.sent.clear()
        await updates.process_x_update_watch(
            bot,
            repository,
            make_watch(include_keywords="https://bad.example"),
        )
        check.add("unsafe keyword falls back to user timeline", calls and calls[0][0] == "timeline", str(calls))
    finally:
        x_search.search_recent_posts = original_recent
        x_search.get_user_posts = original_timeline


def main() -> None:
    check = Check()
    check_helpers(check)
    asyncio.run(check_initial_sync(check))
    asyncio.run(check_normal_posting(check))
    asyncio.run(check_duplicate_history(check))
    asyncio.run(check_keyword_search_query(check))
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
