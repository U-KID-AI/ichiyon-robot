import asyncio
import sys
from datetime import date, datetime, timezone
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
from bot.services.deck_search_settings import (
    apply_deck_search_settings,
    fetch_since_start_time,
    parse_deck_fetch_since_command,
    parse_fetch_since_date,
    validate_fetch_since_date,
)
from bot.services.qr_detector import detect_qr_codes, opencv_available
from bot.services.x_search import (
    XMedia,
    XPost,
    XSearchError,
    build_search_params,
    build_search_time_range,
    get_search_endpoint,
    parse_search_response,
)


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


class FakeMentionPriorityRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def list_reactions(self, guild_id, enabled=None, reaction_kind=None, include_system=True):
        if reaction_kind == "random_draw":
            return [
                {
                    "id": 21,
                    "guild_id": guild_id,
                    "reaction_key": "kuji",
                    "keyword": "おみくじ",
                    "match_type": "exact",
                    "reaction_kind": "random_draw",
                    "name": "おみくじ",
                    "enabled": True,
                    "created_at": "2026-06-20T00:00:00Z",
                },
                {
                    "id": 20,
                    "guild_id": guild_id,
                    "reaction_key": "quotes",
                    "keyword": "名言",
                    "match_type": "exact",
                    "reaction_kind": "random_draw",
                    "name": "名言",
                    "enabled": True,
                    "created_at": "2026-06-20T00:00:00Z",
                }
            ]
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

    def list_choices(self, guild_id, mention_reaction_id, enabled=None):
        if mention_reaction_id == 21:
            return [
                {
                    "id": 31,
                    "guild_id": guild_id,
                    "mention_reaction_id": mention_reaction_id,
                    "name": "kuji",
                    "body": "kuji result",
                    "image_path": "",
                    "appearance_rate": 1,
                    "enabled": True,
                }
            ]
        return [
            {
                "id": 30,
                "guild_id": guild_id,
                "mention_reaction_id": mention_reaction_id,
                "name": "fallback",
                "body": "fallback quote",
                "image_path": "",
                "appearance_rate": 1,
                "enabled": True,
            }
        ]


def base_config() -> Dict[str, Any]:
    return {
        "search_type": "deck_search",
        "allowed_channel_ids": ["123"],
        "max_results": 3,
        "x_search_max_results": 50,
        "deny_message": "このチャンネルではデッキ検索は使えません。",
        "not_found_message": "おい ないんだが",
        "missing_format_behavior": "ask_format",
        "cache_ttl_seconds": 0,
        "request_timeout_seconds": 1,
        "image_scan_limit": 30,
        "image_scan_concurrency": 2,
        "stop_after_candidates": True,
        "image_fetch_timeout_seconds": 5,
        "high_accuracy_enabled": True,
        "high_accuracy_x_search_max_results": 100,
        "high_accuracy_image_scan_limit": 100,
        "high_accuracy_image_scan_concurrency": 2,
        "high_accuracy_stop_after_candidates": False,
        "excluded_keywords": ["ドラゴンボール", "レジェンズ", "探索コード", "フレンドコード"],
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


async def check_mention_priority(check: Check) -> None:
    disabled_before = config.X_SEARCH_ENABLED
    token_before = config.X_BEARER_TOKEN
    repository_before = runtime_db.MentionReactionRepository
    feature_before = runtime_db.feature_enabled
    limited_before = runtime_db.list_limited_effects
    effects_before = runtime_db.list_effects
    command_before = runtime_db.get_mention_command_text
    try:
        config.X_SEARCH_ENABLED = False
        config.X_BEARER_TOKEN = ""
        runtime_db.MentionReactionRepository = FakeMentionPriorityRepository
        runtime_db.feature_enabled = lambda connection, guild_id, feature_key: True
        runtime_db.list_limited_effects = lambda connection, guild_id, message: []
        runtime_db.list_effects = lambda connection, guild_id, target_type, target_id: []

        runtime_db.get_mention_command_text = lambda message: "デッキ エルフ"
        deck_message = FakeMessage("@bot デッキ エルフ")
        deck_action = await runtime_db.process_db_mention(deck_message, "guild", object())
        check.add(
            "deck mention uses search before fallback",
            deck_action.handled is True and deck_message.channel.sent == ["デッキ検索はまだ無効"],
            str(deck_message.channel.sent),
        )

        runtime_db.get_mention_command_text = lambda message: "デッキ　エルフ"
        wide_space_message = FakeMessage("@bot デッキ　エルフ")
        wide_space_action = await runtime_db.process_db_mention(wide_space_message, "guild", object())
        check.add(
            "deck mention accepts full-width space",
            wide_space_action.handled is True and wide_space_message.channel.sent == ["デッキ検索はまだ無効"],
            str(wide_space_message.channel.sent),
        )

        runtime_db.get_mention_command_text = lambda message: "デッキ エルフ 高精度"
        high_accuracy_message = FakeMessage("@bot デッキ エルフ 高精度")
        high_accuracy_action = await runtime_db.process_db_mention(high_accuracy_message, "guild", object())
        check.add(
            "high accuracy deck mention uses search before fallback",
            high_accuracy_action.handled is True and high_accuracy_message.channel.sent == ["デッキ検索はまだ無効"],
            str(high_accuracy_message.channel.sent),
        )

        runtime_db.get_mention_command_text = lambda message: "なんでもない文章"
        message = FakeMessage("@bot なんでもない文章")
        action = await runtime_db.process_db_mention(message, "guild", object())
        check.add(
            "unknown mention falls back to single mention reaction",
            action.handled is True and message.channel.sent == ["fallback quote"],
            str(message.channel.sent),
        )

        runtime_db.get_mention_command_text = lambda message: "おみくじ"
        kuji_message = FakeMessage("@bot おみくじ")
        kuji_action = await runtime_db.process_db_mention(kuji_message, "guild", object())
        check.add(
            "kuji mention uses keyword reaction before fallback",
            kuji_action.handled is True and kuji_message.channel.sent == ["kuji result"],
            str(kuji_message.channel.sent),
        )
    finally:
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before
        runtime_db.MentionReactionRepository = repository_before
        runtime_db.feature_enabled = feature_before
        runtime_db.list_limited_effects = limited_before
        runtime_db.list_effects = effects_before
        runtime_db.get_mention_command_text = command_before


async def check_search_flow(check: Check) -> None:
    parsed = parse_deck_search_command("デッキ elf", "ask_format")
    check.add("class alias elf", parsed is not None and parsed.class_key == "elf")
    high_tail = parse_deck_search_command("デッキ エルフ 高精度", "ask_format")
    check.add(
        "high accuracy suffix is parsed",
        high_tail is not None and high_tail.class_key == "elf" and high_tail.high_accuracy is True,
    )
    high_middle = parse_deck_search_command("デッキ 高精度 エルフ", "ask_format")
    check.add(
        "high accuracy middle is parsed",
        high_middle is not None and high_middle.class_key == "elf" and high_middle.high_accuracy is True,
    )
    high_wide_space = parse_deck_search_command("デッキ　エルフ　高精度", "ask_format")
    check.add(
        "high accuracy accepts full-width spaces",
        high_wide_space is not None and high_wide_space.class_key == "elf" and high_wide_space.high_accuracy is True,
    )
    bishop_extra = parse_deck_search_command("デッキ ビショップ アンリミテッド ロデオ", "ask_format")
    bishop_query = build_x_query(bishop_extra, {}) if bishop_extra is not None else ""
    check.add(
        "extra term parses after class and format",
        bishop_extra is not None
        and bishop_extra.class_key == "bishop"
        and bishop_extra.format_label == "アンリミテッド"
        and bishop_extra.extra_terms == ["ロデオ"],
        str(bishop_extra),
    )
    check.add(
        "format and extra term appear in final query",
        "アンリミテッド" in bishop_query and "ロデオ" in bishop_query,
        bishop_query,
    )
    elf_extra = parse_deck_search_command("デッキ エルフ リノ セッカ", "ask_format")
    elf_query = build_x_query(elf_extra, {}) if elf_extra is not None else ""
    check.add(
        "multiple extra terms are kept",
        elf_extra is not None and elf_extra.class_key == "elf" and elf_extra.extra_terms == ["リノ", "セッカ"],
        str(elf_extra),
    )
    check.add("multiple extra terms appear in final query", "リノ セッカ" in elf_query, elf_query)
    royal_high_extra = parse_deck_search_command("デッキ 高精度 ロイヤル 連携", "ask_format")
    royal_high_query = build_x_query(royal_high_extra, {}) if royal_high_extra is not None else ""
    check.add(
        "high accuracy is not treated as extra term",
        royal_high_extra is not None
        and royal_high_extra.class_key == "royal"
        and royal_high_extra.high_accuracy is True
        and royal_high_extra.extra_terms == ["連携"],
        str(royal_high_extra),
    )
    check.add(
        "high accuracy is not included in final query",
        "高精度" not in royal_high_query and "連携" in royal_high_query,
        royal_high_query,
    )
    nightmare = parse_deck_search_command("デッキ ナイトメア", "ask_format")
    nightmare_query = build_x_query(nightmare, {}) if nightmare is not None else ""
    check.add(
        "nightmare class is parsed",
        nightmare is not None
        and nightmare.class_key == "nightmare"
        and nightmare.class_label == "ナイトメア"
        and nightmare.class_en == "Nightmare",
        str(nightmare),
    )
    check.add("nightmare appears in final query", "ナイトメア" in nightmare_query and "Nightmare" in nightmare_query, nightmare_query)
    check.add(
        "nightmare query includes class search aliases",
        "メア" in nightmare_query
        and "ネメ" not in nightmare_query
        and "ナイトメアビヨンド" in nightmare_query,
        nightmare_query,
    )
    nemesis_alias = parse_deck_search_command("デッキ ネメ", "ask_format")
    nemesis_query = build_x_query(nemesis_alias, {}) if nemesis_alias is not None else ""
    check.add(
        "neme alias belongs to nemesis",
        nemesis_alias is not None and nemesis_alias.class_key == "nemesis" and "ネメ" in nemesis_query,
        str(nemesis_alias),
    )
    check.add(
        "nightmare query uses beyond context",
        "(ビヨンド OR beyond)" in nightmare_query and "has:media" in nightmare_query,
        nightmare_query,
    )
    check.add(
        "nightmare query does not require deck words",
        "(デッキ OR deck" not in nightmare_query.split(" -is:")[0]
        and " QR " not in nightmare_query.split(" -is:")[0]
        and "コード" not in nightmare_query.split(" -is:")[0]
        and "レシピ" not in nightmare_query.split(" -is:")[0]
        and "構築" not in nightmare_query.split(" -is:")[0],
        nightmare_query,
    )
    nightmare_extra = parse_deck_search_command("デッキ ナイトメア ロデオ", "ask_format")
    nightmare_extra_query = build_x_query(nightmare_extra, {}) if nightmare_extra is not None else ""
    check.add(
        "nightmare extra term is kept",
        nightmare_extra is not None
        and nightmare_extra.class_key == "nightmare"
        and nightmare_extra.extra_terms == ["ロデオ"],
        str(nightmare_extra),
    )
    check.add("nightmare extra term appears in final query", "ロデオ" in nightmare_extra_query, nightmare_extra_query)
    nightmare_high = parse_deck_search_command("デッキ ナイトメア 高精度", "ask_format")
    check.add(
        "nightmare high accuracy is parsed",
        nightmare_high is not None and nightmare_high.class_key == "nightmare" and nightmare_high.high_accuracy is True,
        str(nightmare_high),
    )
    check.add("high accuracy without class asks format", parse_deck_search_command("デッキ 高精度", "ask_format") is None)
    query = build_x_query(parsed, {}) if parsed is not None else ""
    check.add("default query uses media filter", "has:media" in query and "has:images" not in query, query)
    check.add("default query includes beyond terms", "ビヨンド" in query and "beyond" in query, query)
    legacy_query = build_x_query(
        parsed,
        {"x_query_template": "({class_label} OR {class_en}) (デッキ OR deck OR QR OR コード OR レシピ OR 構築) has:media"},
    ) if parsed is not None else ""
    check.add(
        "legacy default query template is normalized",
        "ビヨンド" in legacy_query and "デッキ OR deck" not in legacy_query and "エル" in legacy_query,
        legacy_query,
    )
    old_context_query = build_x_query(
        parsed,
        {"required_context_terms": ["デッキ", "deck", "QR", "コード", "レシピ", "構築"]},
    ) if parsed is not None else ""
    check.add(
        "required context terms can restore old condition",
        "(デッキ OR deck OR QR OR コード OR レシピ OR 構築)" in old_context_query,
        old_context_query,
    )
    check.add(
        "default query includes exclusions",
        "-ドラゴンボール" in query and "-レジェンズ" in query and "-探索コード" in query and "-フレンドコード" in query,
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

    empty_config = base_config()
    config.X_SEARCH_ENABLED = True
    config.X_BEARER_TOKEN = "dummy"

    async def fake_empty_search(query, max_results, timeout_seconds, search_mode, lookback_days):
        return []

    original_empty_search = deck_search.search_posts
    original_empty_opencv = deck_search.opencv_available
    try:
        deck_search.search_posts = fake_empty_search
        deck_search.opencv_available = lambda: True
        not_found = await search_decks("g", "123", "デッキ エルフ", empty_config)
        check.add("deck search not found message", not_found == "おい ないんだが", not_found)
    finally:
        deck_search.search_posts = original_empty_search
        deck_search.opencv_available = original_empty_opencv

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

    async def fake_scan_media_image(post, media, class_label, timeout_seconds, stats):
        stats.image_downloaded += 1
        stats.qr_detected += 1
        return deck_search.DeckSearchResult(
            post=post,
            image_url=media.url,
            detected_class=class_label,
            qr_score=100,
            created_at=post.created_at,
        )

    original_search = deck_search.search_posts
    original_scan = deck_search.scan_media_image
    original_opencv = deck_search.opencv_available
    try:
        deck_search.search_posts = fake_search_posts
        deck_search.scan_media_image = fake_scan_media_image
        deck_search.opencv_available = lambda: True
        config.X_SEARCH_ENABLED = True
        config.X_BEARER_TOKEN = "dummy"
        recent_config = base_config()
        recent_config["search_mode"] = "recent"
        response = await search_decks("g", "123", "デッキ エルフ", recent_config)
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
        deck_search.scan_media_image = original_scan
        deck_search.opencv_available = original_opencv
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before


async def check_full_archive_error(check: Check) -> None:
    disabled_before = config.X_SEARCH_ENABLED
    token_before = config.X_BEARER_TOKEN
    original_search = deck_search.search_posts
    original_opencv = deck_search.opencv_available
    called_modes = []

    async def fake_forbidden_search(query, max_results, timeout_seconds, search_mode, lookback_days):
        called_modes.append(search_mode)
        if search_mode == "recent":
            return [XPost("111", "繧ｨ繝ｫ繝・繝・ャ繧ｭ QR", "2026-06-01T00:00:00Z", [XMedia("m1", "https://example.com/qr.png", "photo")])]
        raise XSearchError("api status 403", status_code=403, endpoint_type=search_mode)

    try:
        deck_search.search_posts = fake_forbidden_search
        deck_search.opencv_available = lambda: True
        config.X_SEARCH_ENABLED = True
        config.X_BEARER_TOKEN = "dummy"
        full_archive_config = base_config()
        full_archive_config["search_mode"] = "full_archive"
        response = await search_decks("g", "123", "デッキ エルフ", full_archive_config)
        check.add("full archive permission error falls back to recent", called_modes == ["full_archive", "recent"], str(called_modes))
        check.add("full archive fallback keeps deck search usable", "過去検索が使えません" not in response, response)
    finally:
        deck_search.search_posts = original_search
        deck_search.opencv_available = original_opencv
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before


async def check_x_api_error_messages(check: Check) -> None:
    disabled_before = config.X_SEARCH_ENABLED
    token_before = config.X_BEARER_TOKEN
    original_search = deck_search.search_posts
    original_opencv = deck_search.opencv_available

    async def fake_error_search(query, max_results, timeout_seconds, search_mode, lookback_days):
        status_code = fake_error_search.status_code
        raise XSearchError("api status {0}".format(status_code), status_code=status_code, endpoint_type=search_mode)

    try:
        deck_search.search_posts = fake_error_search
        deck_search.opencv_available = lambda: True
        config.X_SEARCH_ENABLED = True
        config.X_BEARER_TOKEN = "dummy"

        full_archive_config = base_config()
        full_archive_config["search_mode"] = "full_archive"

        fake_error_search.status_code = 401
        response = await search_decks("g", "123", "デッキ エルフ", full_archive_config)
        check.add("recent 401 after full archive fallback shows auth error", "認証" in response and "過去検索" not in response, response)

        fake_error_search.status_code = 403
        response = await search_decks("g", "123", "デッキ エルフ", full_archive_config)
        check.add(
            "recent 403 after full archive fallback shows access error",
            ("権限" in response or "プラン" in response) and "過去検索" not in response,
            response,
        )
    finally:
        deck_search.search_posts = original_search
        deck_search.opencv_available = original_opencv
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before


async def check_high_accuracy_mode(check: Check) -> None:
    disabled_before = config.X_SEARCH_ENABLED
    token_before = config.X_BEARER_TOKEN
    original_search = deck_search.search_posts
    original_scan_posts = deck_search.scan_posts_concurrently
    original_opencv = deck_search.opencv_available
    captured = {}

    async def fake_search_posts(query, max_results, timeout_seconds, search_mode, lookback_days):
        captured["x_search_max_results"] = max_results
        return [
            XPost(
                post_id="ha1",
                text="エルフ デッキ QR",
                created_at="2026-06-20T00:00:00Z",
                media=[XMedia(media_key="ha1", url="https://example.test/ha.jpg", type="photo")],
            )
        ]

    async def fake_scan_posts_concurrently(
        posts,
        request,
        max_results,
        image_scan_limit,
        image_fetch_timeout_seconds,
        image_scan_concurrency,
        stop_after_candidates,
        stats,
    ):
        captured["request_high_accuracy"] = request.high_accuracy
        captured["image_scan_limit"] = image_scan_limit
        captured["image_scan_concurrency"] = image_scan_concurrency
        captured["stop_after_candidates"] = stop_after_candidates
        captured["stats_high_accuracy"] = stats.high_accuracy
        return []

    try:
        deck_search.search_posts = fake_search_posts
        deck_search.scan_posts_concurrently = fake_scan_posts_concurrently
        deck_search.opencv_available = lambda: True
        config.X_SEARCH_ENABLED = True
        config.X_BEARER_TOKEN = "dummy"
        response = await search_decks("g", "123", "デッキ 高精度 エルフ", base_config())
        check.add("high accuracy still enters deck search", response == "おい ないんだが", response)
        check.add("high accuracy flag reaches request", captured.get("request_high_accuracy") is True, str(captured))
        check.add("high accuracy disables early stop", captured.get("stop_after_candidates") is False, str(captured))
        check.add("high accuracy uses configured x result limit", captured.get("x_search_max_results") == 100, str(captured))
        check.add("high accuracy uses configured scan limit", captured.get("image_scan_limit") == 100, str(captured))
        check.add("high accuracy uses configured concurrency", captured.get("image_scan_concurrency") == 2, str(captured))
        check.add("high accuracy appears in stats", captured.get("stats_high_accuracy") is True, str(captured))
    finally:
        deck_search.search_posts = original_search
        deck_search.scan_posts_concurrently = original_scan_posts
        deck_search.opencv_available = original_opencv
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before


async def check_lightweight_normal_search_settings(check: Check) -> None:
    captured = {}
    disabled_before = config.X_SEARCH_ENABLED
    token_before = config.X_BEARER_TOKEN
    original_search = deck_search.search_posts
    original_scan_posts = deck_search.scan_posts_concurrently
    original_opencv = deck_search.opencv_available

    async def fake_search_posts(query, max_results, timeout_seconds, search_mode, lookback_days):
        captured["x_search_max_results"] = max_results
        return [
            XPost(
                post_id="normal1",
                text="deck search QR",
                created_at="2026-06-20T00:00:00Z",
                media=[XMedia(media_key="normal1", url="https://example.test/normal.jpg", type="photo")],
            )
        ]

    async def fake_scan_posts_concurrently(
        posts,
        request,
        max_results,
        image_scan_limit,
        image_fetch_timeout_seconds,
        image_scan_concurrency,
        stop_after_candidates,
        stats,
    ):
        captured["request_high_accuracy"] = request.high_accuracy
        captured["image_scan_limit"] = image_scan_limit
        captured["image_scan_concurrency"] = image_scan_concurrency
        captured["stop_after_candidates"] = stop_after_candidates
        return []

    try:
        deck_search.search_posts = fake_search_posts
        deck_search.scan_posts_concurrently = fake_scan_posts_concurrently
        deck_search.opencv_available = lambda: True
        config.X_SEARCH_ENABLED = True
        config.X_BEARER_TOKEN = "dummy"
        await search_decks("g", "123", "\u30c7\u30c3\u30ad \u30a8\u30eb\u30d5", base_config())
        check.add("normal search uses lightweight x result limit", captured.get("x_search_max_results") == 50, str(captured))
        check.add("normal search uses lightweight image scan limit", captured.get("image_scan_limit") == 30, str(captured))
        check.add("normal search uses lightweight concurrency", captured.get("image_scan_concurrency") == 2, str(captured))
        check.add("normal search stops after candidates", captured.get("stop_after_candidates") is True, str(captured))
        check.add("normal search is not high accuracy", captured.get("request_high_accuracy") is False, str(captured))
    finally:
        deck_search.search_posts = original_search
        deck_search.scan_posts_concurrently = original_scan_posts
        deck_search.opencv_available = original_opencv
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before


async def check_fetch_since_search_flow(check: Check) -> None:
    captured = {}
    disabled_before = config.X_SEARCH_ENABLED
    token_before = config.X_BEARER_TOKEN
    original_search = deck_search.search_posts
    original_opencv = deck_search.opencv_available

    async def fake_search_posts(query, max_results, timeout_seconds, search_mode, lookback_days, start_time=None):
        captured["start_time"] = start_time
        return []

    try:
        deck_search.search_posts = fake_search_posts
        deck_search.opencv_available = lambda: True
        config.X_SEARCH_ENABLED = True
        config.X_BEARER_TOKEN = "dummy"
        deck_config = base_config()
        deck_config["fetch_since_date"] = "2026-06-27"
        await search_decks("g", "123", "\u30c7\u30c3\u30ad \u30a8\u30eb\u30d5", deck_config)
        check.add(
            "fetch since reaches deck search API call",
            captured.get("start_time") == "2026-06-26T15:00:00Z",
            str(captured),
        )
    finally:
        deck_search.search_posts = original_search
        deck_search.opencv_available = original_opencv
        config.X_SEARCH_ENABLED = disabled_before
        config.X_BEARER_TOKEN = token_before


async def check_parallel_scan(check: Check) -> None:
    active = {"current": 0, "max": 0, "started": 0}

    async def fake_scan_media_image(post, media, class_label, timeout_seconds, stats):
        active["current"] += 1
        active["started"] += 1
        active["max"] = max(active["max"], active["current"])
        try:
            await asyncio.sleep(0.01)
            stats.image_downloaded += 1
            stats.qr_detected += 1
            return deck_search.DeckSearchResult(
                post=post,
                image_url=media.url,
                detected_class=class_label,
                qr_score=100,
                created_at=post.created_at,
            )
        finally:
            active["current"] -= 1

    posts = [
        XPost(
            post_id=str(index),
            text="エルフ デッキ QR",
            created_at="2026-06-20T00:00:00Z",
            media=[XMedia(media_key=str(index), url="https://example.test/{0}.jpg".format(index), type="photo")],
        )
        for index in range(10)
    ]
    request = deck_search.DeckSearchRequest(query="エルフ", class_key="elf", class_label="エルフ", class_en="elf")
    stats = DeckSearchStats(image_scan_concurrency=2)
    original_scan = deck_search.scan_media_image
    try:
        deck_search.scan_media_image = fake_scan_media_image
        results = await deck_search.scan_posts_concurrently(posts, request, 3, 10, 5, 2, True, stats)
    finally:
        deck_search.scan_media_image = original_scan

    check.add("parallel scan honors concurrency", active["max"] <= 2, str(active))
    check.add("parallel scan stops after candidates", len(results) == 3 and stats.stopped_after_candidates, str(active))


async def check_image_fetch_failure(check: Check) -> None:
    media = XMedia(media_key="m", url="https://example.test/missing.jpg", type="photo")
    post = XPost(post_id="1", text="エルフ デッキ", created_at="2026-06-20T00:00:00Z", media=[media])
    stats = DeckSearchStats()
    original_fetch = deck_search.fetch_image_bytes

    async def fake_fetch_image_bytes(url, timeout_seconds):
        return None

    try:
        deck_search.fetch_image_bytes = fake_fetch_image_bytes
        result = await deck_search.scan_media_image(post, media, "エルフ", 1, stats)
    finally:
        deck_search.fetch_image_bytes = original_fetch
    check.add("image fetch failure is safe", result is None and stats.skipped_image_fetch == 1)


def check_post_scoring(check: Check) -> None:
    request = deck_search.DeckSearchRequest(query="エルフ", class_key="elf", class_label="エルフ", class_en="elf")
    low = XPost("1", "ドラゴンボール レジェンズ キャンペーン", "2026-06-20T00:00:00Z", [])
    high = XPost("2", "エルフ デッキ QR Shadowverse", "2026-06-20T00:00:00Z", [])
    sorted_posts = deck_search.sort_posts_for_scan([low, high], request)
    check.add("posts are sorted by deck score", sorted_posts[0].post_id == "2")


def check_deck_class_spam_filter(check: Check) -> None:
    request = deck_search.DeckSearchRequest(query="エルフ", class_key="elf", class_label="エルフ", class_en="elf")

    target_only = XPost("target", "エルフ デッキ QR", "2026-06-20T00:00:00Z", [])
    two_classes = XPost("two", "エルフ ロイヤル 対面メモ デッキ", "2026-06-20T00:00:00Z", [])
    three_classes = XPost("three", "エルフ/ロイヤル/ウィッチ 比較 デッキ", "2026-06-20T00:00:00Z", [])
    unknown = XPost("unknown", "Shadowverse デッキ QR", "2026-06-20T00:00:00Z", [])
    all_classes = XPost("all", "エルフ ロイヤル ウィッチ ドラゴン ナイトメア ビショップ ネメシス デッキ", "2026-06-20T00:00:00Z", [])
    broad = XPost("broad", "全クラス対応 デッキ QR", "2026-06-20T00:00:00Z", [])
    compact_broad = XPost("compact", "全 7 クラス 対応可 デッキ QR", "2026-06-20T00:00:00Z", [])
    matchup = XPost("matchup", "エルフ vs ロイヤル 全対面メモ", "2026-06-20T00:00:00Z", [])

    check.add(
        "detects all seven deck classes",
        len(deck_search.detect_deck_classes_in_text(all_classes.text)) == 7,
        str(deck_search.detect_deck_classes_in_text(all_classes.text)),
    )
    check.add("broad all-class term is detected", deck_search.has_broad_class_listing_term(broad.text))
    check.add("spaced broad all-class term is detected", deck_search.has_broad_class_listing_term(compact_broad.text))
    check.add("all matchups is not treated as all classes", not deck_search.has_broad_class_listing_term(matchup.text))

    reason, classes = deck_search.deck_post_class_filter_reason(all_classes, request)
    check.add("four or more classes are filtered", reason == "many_classes" and len(classes) >= 4, str((reason, classes)))
    reason, classes = deck_search.deck_post_class_filter_reason(broad, request)
    check.add("broad all-class post is filtered", reason == "broad_class_listing", str((reason, classes)))
    reason, classes = deck_search.deck_post_class_filter_reason(three_classes, request)
    check.add("three-class comparison is kept", reason is None and len(classes) == 3, str((reason, classes)))
    reason, classes = deck_search.deck_post_class_filter_reason(matchup, request)
    check.add("two-class matchup is kept", reason is None and len(classes) == 2, str((reason, classes)))

    ranked = deck_search.sort_posts_for_scan([two_classes, unknown, target_only, three_classes], request)
    check.add(
        "target-only deck posts are ranked first",
        [post.post_id for post in ranked[:4]] == ["target", "two", "three", "unknown"],
        str([post.post_id for post in ranked]),
    )
    filtered = deck_search.filter_and_rank_posts_for_scan([all_classes, broad, target_only], request)
    check.add("filtered posts are excluded before scan", [post.post_id for post in filtered] == ["target"], str([post.post_id for post in filtered]))


def check_search_params(check: Check) -> None:
    recent_endpoint = get_search_endpoint("recent")
    full_endpoint = get_search_endpoint("full_archive")
    recent_params = build_search_params("query", 10, "recent", 14)
    archive_params = build_search_params("query", 10, "full_archive", 14)
    fetch_since_start = fetch_since_start_time("2026-06-27")
    fetch_since_params = build_search_params("query", 10, "full_archive", 14, fetch_since_start)
    time_range = build_search_time_range(14, datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc))
    check.add("recent endpoint path", recent_endpoint.endswith("/2/tweets/search/recent"), recent_endpoint)
    check.add("full archive endpoint path", full_endpoint.endswith("/2/tweets/search/all"), full_endpoint)
    check.add(
        "full archive has time range",
        "start_time" in archive_params and "end_time" in archive_params and "start_time" not in recent_params,
        str(archive_params),
    )
    check.add(
        "full archive end_time has five minute safety margin",
        time_range["end_time"] == "2026-06-20T11:55:00Z" and time_range["start_time"] == "2026-06-06T11:55:00Z",
        str(time_range),
    )
    check.add(
        "x search params match stg request shape",
        archive_params.get("tweet.fields") == "created_at"
        and archive_params.get("expansions") == "attachments.media_keys"
        and archive_params.get("media.fields") == "url,preview_image_url,type"
        and "sort_order" not in archive_params,
        str(archive_params),
    )
    check.add(
        "fetch since date sets start_time",
        fetch_since_params.get("start_time") == "2026-06-26T15:00:00Z",
        str(fetch_since_params),
    )


def check_fetch_since_helpers(check: Check) -> None:
    fixed_now = datetime(2026, 7, 4, 3, 0, 0, tzinfo=timezone.utc)
    short_date = parse_fetch_since_date("6/27から", fixed_now)
    slash_date = parse_fetch_since_date("2026/6/27", fixed_now)
    dash_date = parse_fetch_since_date("2026-06-27", fixed_now)
    check.add("fetch since short date uses current year", short_date == date(2026, 6, 27), str(short_date))
    check.add("fetch since slash date parses", slash_date == date(2026, 6, 27), str(slash_date))
    check.add("fetch since dash date parses", dash_date == date(2026, 6, 27), str(dash_date))
    try:
        parse_fetch_since_date("99/99", fixed_now)
        invalid_ok = False
    except ValueError:
        invalid_ok = True
    check.add("fetch since invalid date is rejected", invalid_ok)
    check.add(
        "fetch since old date is rejected",
        validate_fetch_since_date(date(2026, 5, 1), 30, fixed_now) is not None,
    )
    update_command = parse_deck_fetch_since_command("デッキ 取得日更新 6/27から", fixed_now)
    show_command = parse_deck_fetch_since_command("デッキ 取得日確認", fixed_now)
    reset_command = parse_deck_fetch_since_command("デッキ 取得日リセット", fixed_now)
    check.add(
        "fetch since update command parses",
        update_command is not None
        and update_command.action == "update"
        and update_command.fetch_since_date == date(2026, 6, 27),
        str(update_command),
    )
    check.add("fetch since show command parses", show_command is not None and show_command.action == "show", str(show_command))
    check.add("fetch since reset command parses", reset_command is not None and reset_command.action == "reset", str(reset_command))
    merged = apply_deck_search_settings(base_config(), {"fetch_since_date": date(2026, 6, 27), "max_lookback_days": 30})
    check.add("fetch since settings merge into deck config", merged.get("fetch_since_date") == "2026-06-27", str(merged))


def check_x_payload(check: Check) -> None:
    payload = {
        "data": [
            {"id": "1", "text": "deck", "created_at": "2026-06-20T00:00:00Z", "attachments": {"media_keys": ["m1"]}},
            {"id": "2", "text": "deck video", "created_at": "2026-06-20T00:00:00Z", "attachments": {"media_keys": ["m2"]}},
        ],
        "includes": {
            "media": [
                {"media_key": "m1", "type": "photo", "url": "https://example.test/a.jpg"},
                {"media_key": "m2", "type": "video", "preview_image_url": "https://example.test/preview.jpg"},
            ]
        },
    }
    posts = parse_search_response(payload)
    check.add("x payload media parsed", len(posts) == 2 and len(posts[0].media) == 1)
    check.add("x video preview media parsed", posts[1].media[0].url == "https://example.test/preview.jpg")


def check_stats(check: Check) -> None:
    stats = DeckSearchStats(
        total_ms=1200,
        x_api_ms=300,
        image_scan_ms=800,
        image_scan_concurrency=5,
        stopped_after_candidates=True,
        x_results=20,
        media_posts=8,
        image_downloaded=5,
        qr_detected=0,
        candidates=0,
    )
    log_text = stats.to_log()
    check.add(
        "stats log contains safe counters",
        "total_ms=1200" in log_text
        and "x_api_ms=300" in log_text
        and "image_scan_ms=800" in log_text
        and "image_scan_concurrency=5" in log_text
        and "stopped_after_candidates=True" in log_text
        and "X results=20" in log_text
        and "media=8" in log_text
        and "downloaded=5" in log_text
        and "qr=0" in log_text,
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
    asyncio.run(check_x_api_error_messages(check))
    asyncio.run(check_high_accuracy_mode(check))
    asyncio.run(check_lightweight_normal_search_settings(check))
    asyncio.run(check_fetch_since_search_flow(check))
    asyncio.run(check_parallel_scan(check))
    asyncio.run(check_image_fetch_failure(check))
    asyncio.run(check_runtime_path(check))
    asyncio.run(check_mention_priority(check))
    check_search_params(check)
    check_fetch_since_helpers(check)
    check_post_scoring(check)
    check_deck_class_spam_filter(check)
    check_x_payload(check)
    check_stats(check)
    check_qr_optional(check)
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
