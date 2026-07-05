import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from admin import servers
from admin import mention_reactions
from bot import guild_context
from bot.repositories.mention_reactions import MentionReactionRepository
from bot.services import runtime_db


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeFeatureFlagRepository:
    flags = []

    def __init__(self, connection) -> None:
        self.connection = connection

    def list_flags(self, guild_id: str) -> List[Dict[str, Any]]:
        return list(self.flags)

    def is_enabled(self, guild_id: str, feature_key: str, default: bool = False) -> bool:
        for flag in self.flags:
            if flag["feature_key"] == feature_key:
                return bool(flag["enabled"])
        return default


class FakeMentionReactionRepository:
    reactions = []

    def __init__(self, connection) -> None:
        self.connection = connection

    def list_reactions_for_admin(self, guild_id, query=None, reaction_kind=None, enabled=None, is_system=None):
        rows = []
        for reaction in self.reactions:
            if reaction_kind is not None and reaction["reaction_kind"] != reaction_kind:
                continue
            rows.append(dict(reaction))
        return rows


def ok(name: str, detail: Any = "") -> bool:
    print("[OK] {0}{1}".format(name, " - {0}".format(detail) if detail else ""))
    return True


def ng(name: str, detail: Any = "") -> bool:
    print("[NG] {0}{1}".format(name, " - {0}".format(detail) if detail else ""))
    return False


def check(condition: bool, name: str, detail: Any = "") -> bool:
    return ok(name, detail) if condition else ng(name, detail)


def mention_features() -> List[Dict[str, Any]]:
    wanted = {"mention_random_draw", "mention_search", "mention_limited"}
    return [feature for feature in servers.DISPLAY_FEATURES if feature["key"] in wanted]


def check_display_definitions() -> int:
    results = []
    features = mention_features()
    labels = {feature["key"]: feature["label"] for feature in features}
    keys = [feature["key"] for feature in features]
    all_keys = [feature["key"] for feature in servers.DISPLAY_FEATURES]
    all_edit_paths = [feature["edit_path"] for feature in servers.DISPLAY_FEATURES]
    results.append(check(labels.get("mention_random_draw") == "ランダム抽選", "random draw label is independent", labels))
    results.append(check(labels.get("mention_search") == "検索", "search label is independent", labels))
    results.append(check(labels.get("mention_limited") == "限定機能", "limited label is independent", labels))
    results.append(check(all("メンション:" not in label for label in labels.values()), "mention prefix is removed", labels))
    results.append(check(len(keys) == len(set(keys)), "mention feature keys are unique", keys))
    results.append(check(all("flag_key" not in feature for feature in features), "mention features do not share flag_key", features))
    results.append(check("mention_reactions" not in all_keys, "old mention parent is not displayed", all_keys))
    results.append(check(servers.get_feature_definition("mention_reactions") is None, "old mention parent is not toggle target"))
    results.append(check(all(path != "mention-reactions" for path in all_edit_paths), "old mention parent edit path is absent", all_edit_paths))
    return sum(1 for result in results if result)


def check_build_feature_rows() -> int:
    original_get_connection = servers.get_connection
    original_repository = servers.FeatureFlagRepository
    try:
        servers.get_connection = lambda: FakeConnection()
        servers.FeatureFlagRepository = FakeFeatureFlagRepository
        FakeFeatureFlagRepository.flags = [
            {"feature_key": "mention_random_draw", "enabled": False},
            {"feature_key": "mention_search", "enabled": True},
            {"feature_key": "mention_limited", "enabled": True},
        ]
        rows = {
            row["key"]: row
            for row in servers.build_feature_rows("guild", "guild_admin")
            if row["key"] in {"mention_random_draw", "mention_search", "mention_limited"}
        }
    finally:
        servers.get_connection = original_get_connection
        servers.FeatureFlagRepository = original_repository

    results = []
    results.append(check(rows["mention_random_draw"]["enabled"] is False, "random draw state is independent", rows))
    results.append(check(rows["mention_search"]["enabled"] is True, "search state is independent", rows))
    results.append(check(rows["mention_limited"]["enabled"] is True, "limited state is independent", rows))
    results.append(check(rows["mention_random_draw"]["toggle_url"].endswith("/features/mention_random_draw/toggle"), "random draw toggle target", rows["mention_random_draw"]["toggle_url"]))
    results.append(check(rows["mention_search"]["toggle_url"].endswith("/features/mention_search/toggle"), "search toggle target", rows["mention_search"]["toggle_url"]))
    results.append(check(rows["mention_limited"]["toggle_url"].endswith("/features/mention_limited/toggle"), "limited toggle target", rows["mention_limited"]["toggle_url"]))
    results.append(check(len({row["toggle_url"] for row in rows.values()}) == 3, "mention feature toggle urls are unique", rows))
    return sum(1 for result in results if result)


def check_legacy_redirects() -> int:
    results = []
    results.append(check(
        servers.legacy_mention_feature_redirect_url("guild") == "/guilds/guild",
        "old mention parent redirects to guild top",
        servers.legacy_mention_feature_redirect_url("guild"),
    ))
    results.append(check(
        servers.legacy_mention_feature_redirect_url("guild", "random_draw") == "/guilds/guild/mention-reactions?kind=random_draw",
        "old random kind redirects to random draw page",
        servers.legacy_mention_feature_redirect_url("guild", "random_draw"),
    ))
    results.append(check(
        servers.legacy_mention_feature_redirect_url("guild", "search") == "/guilds/guild/mention-reactions?kind=search",
        "old search kind redirects to search page",
        servers.legacy_mention_feature_redirect_url("guild", "search"),
    ))
    results.append(check(
        servers.legacy_mention_feature_redirect_url("guild", "limited") == "/guilds/guild/mention-reactions/limited",
        "old limited kind redirects to limited page",
        servers.legacy_mention_feature_redirect_url("guild", "limited"),
    ))
    results.append(check(
        mention_reactions.mention_reaction_kind_list_url("guild", "random_draw") == "/guilds/guild/mention-reactions?kind=random_draw",
        "random draw operation returns to random draw page",
        mention_reactions.mention_reaction_kind_list_url("guild", "random_draw"),
    ))
    results.append(check(
        mention_reactions.mention_reaction_kind_list_url("guild", "search") == "/guilds/guild/mention-reactions?kind=search",
        "search operation returns to search page",
        mention_reactions.mention_reaction_kind_list_url("guild", "search"),
    ))
    return sum(1 for result in results if result)


def check_mention_reaction_action_urls() -> int:
    original_get_connection = mention_reactions.get_connection
    original_repository = mention_reactions.MentionReactionRepository
    try:
        mention_reactions.get_connection = lambda: FakeConnection()
        mention_reactions.MentionReactionRepository = FakeMentionReactionRepository
        FakeMentionReactionRepository.reactions = [
            {
                "id": 10,
                "reaction_key": "omikuji",
                "keyword": "おみくじ",
                "match_type": "exact",
                "reaction_kind": "random_draw",
                "name": "おみくじ",
                "description": "",
                "admin_only": False,
                "is_system": True,
                "is_deletable": False,
                "enabled": True,
                "choice_count": 3,
            },
            {
                "id": 20,
                "reaction_key": "deck_search",
                "keyword": "デッキ",
                "match_type": "prefix",
                "reaction_kind": "search",
                "name": "デッキ検索",
                "description": "",
                "admin_only": False,
                "is_system": True,
                "is_deletable": False,
                "enabled": True,
                "choice_count": 0,
            },
        ]
        random_rows = mention_reactions.list_reaction_rows(
            "guild",
            "guild_admin",
            {"q": "", "kind": "random_draw", "enabled": "all", "system": "all", "show_test_data": True},
        )
        search_rows = mention_reactions.list_reaction_rows(
            "guild",
            "guild_admin",
            {"q": "", "kind": "search", "enabled": "all", "system": "all", "show_test_data": True},
        )
    finally:
        mention_reactions.get_connection = original_get_connection
        mention_reactions.MentionReactionRepository = original_repository

    random_row = random_rows[0]
    search_row = search_rows[0]
    results = []
    results.append(check(random_row["toggle_url"].endswith("/mention-reactions/10/toggle"), "random draw row has toggle url", random_row["toggle_url"]))
    results.append(check(random_row["copy_url"].endswith("/mention-reactions/10/copy"), "random draw row has copy url", random_row["copy_url"]))
    results.append(check(search_row["toggle_url"].endswith("/mention-reactions/20/toggle"), "deck search row has toggle url", search_row["toggle_url"]))
    results.append(check(search_row["copy_url"].endswith("/mention-reactions/20/copy"), "deck search row has copy url", search_row["copy_url"]))
    results.append(check("/features/mention_reactions" not in random_row["toggle_url"], "random row toggle avoids old parent", random_row["toggle_url"]))
    results.append(check("/features/mention_reactions" not in search_row["toggle_url"], "deck row toggle avoids old parent", search_row["toggle_url"]))
    return sum(1 for result in results if result)


def check_mention_reaction_copy_keywords() -> int:
    repository = MentionReactionRepository(None)
    existing = {"デッキ コピー", "おみくじ コピー", "おみくじ コピー_2"}
    repository.keyword_exists = lambda guild_id, keyword, exclude_id=None: keyword in existing
    deck_keyword = repository.build_unique_copy_keyword("guild", "デッキ", "deck_search")
    omikuji_keyword = repository.build_unique_copy_keyword("guild", "おみくじ", "omikuji")
    empty_keyword = repository.build_unique_copy_keyword("guild", "", "custom_key")
    results = []
    results.append(check(deck_keyword == "デッキ コピー_2", "deck search copy keyword is unique", deck_keyword))
    results.append(check(omikuji_keyword == "おみくじ コピー_3", "random draw copy keyword is unique", omikuji_keyword))
    results.append(check(empty_keyword == "custom_key_copy", "empty copy keyword is safe", empty_keyword))
    return sum(1 for result in results if result)


def check_default_feature_keys() -> int:
    keys = list(guild_context.DEFAULT_FEATURE_KEYS)
    results = []
    results.append(check("mention_reactions" not in keys, "old mention parent is not default feature key", keys))
    results.append(check("mention_random_draw" in keys, "random draw default feature key exists", keys))
    results.append(check("mention_search" in keys, "search default feature key exists", keys))
    results.append(check("mention_limited" in keys, "limited default feature key exists", keys))
    return sum(1 for result in results if result)


def check_runtime_flags() -> int:
    original_repository = runtime_db.FeatureFlagRepository
    try:
        runtime_db.FeatureFlagRepository = FakeFeatureFlagRepository
        FakeFeatureFlagRepository.flags = [
            {"feature_key": "mention_reactions", "enabled": False},
            {"feature_key": "mention_random_draw", "enabled": True},
            {"feature_key": "mention_search", "enabled": False},
            {"feature_key": "mention_limited", "enabled": True},
        ]
        random_enabled = runtime_db.mention_feature_enabled(FakeConnection(), "guild", runtime_db.FEATURE_MENTION_RANDOM_DRAW)
        search_enabled = runtime_db.mention_feature_enabled(FakeConnection(), "guild", runtime_db.FEATURE_MENTION_SEARCH)
        limited_enabled = runtime_db.mention_feature_enabled(FakeConnection(), "guild", runtime_db.FEATURE_MENTION_LIMITED)
    finally:
        runtime_db.FeatureFlagRepository = original_repository

    results = []
    results.append(check(random_enabled is True, "runtime random draw ignores old parent flag", random_enabled))
    results.append(check(search_enabled is False, "runtime search reads its own flag", search_enabled))
    results.append(check(limited_enabled is True, "runtime limited reads its own flag", limited_enabled))
    return sum(1 for result in results if result)


def check_admin_templates_parse() -> int:
    template_dir = ROOT_DIR / "admin" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    results = []
    for path in sorted(template_dir.glob("*.html")):
        try:
            env.parse(path.read_text(encoding="utf-8"))
            results.append(check(True, "template parses: {0}".format(path.name)))
        except Exception as exc:
            results.append(check(False, "template parses: {0}".format(path.name), "{0}: {1}".format(type(exc).__name__, exc)))
    return sum(1 for result in results if result)


def check_mode_templates_render() -> int:
    template_dir = ROOT_DIR / "admin" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    env.globals["url_for"] = lambda name, **kwargs: "/static/style.css"
    env.globals["current_bot_instance"] = SimpleNamespace(display_name="いちよんロボ", bot_id="ichiyon")
    env.globals["current_bot_instance_id"] = "ichiyon"
    request = SimpleNamespace(session={"discord_user": {"username": "tester"}})
    base_context = {
        "request": request,
        "user": {"name": "tester"},
        "server": {"name": "server", "icon_url": None, "role": "guild_admin"},
        "guild_id": "guild",
    }
    mode_row = {
        "id": 1,
        "enabled": True,
        "can_toggle": True,
        "can_delete": True,
        "name": "しこっち",
        "mode_nickname": "しこっち",
        "mode_key": "shikocchi",
        "behavior_type_label": "返信",
        "admin_only": False,
        "cooldown_summary": "none",
        "trigger_count": 1,
        "reply_count": 1,
        "exit_count": 1,
        "description": "",
        "enter_message": "しこっち、きた",
        "edit_url": "/guilds/guild/modes/1",
        "toggle_url": "/guilds/guild/modes/1/toggle",
        "copy_url": "/guilds/guild/modes/1/copy",
        "delete_url": "/guilds/guild/modes/1/delete",
    }
    mode_form = {
        "name": "しこっち",
        "mode_key": "shikocchi",
        "mode_nickname": "しこっち",
        "description": "",
        "behavior_type": "reply",
        "mode_icon_path": "",
        "enabled": True,
        "admin_only": False,
        "is_deletable": True,
        "enter_message": "しこっち、きた",
        "exit_message": "",
        "enter_gif_path": "",
        "exit_gif_path": "",
        "enter_notify_channel_id": "",
        "exit_notify_channel_id": "",
        "reaction_channel_ids": "",
        "ignore_channel_ids": "",
        "cooldown_type": "none",
        "cooldown_seconds": 0,
        "cooldown_period": "none",
        "cooldown_reset": "none",
        "cooldown_day": "",
        "state": None,
        "reply_choices": [],
        "trigger_conditions": [],
        "exit_conditions": [],
    }
    results = []
    try:
        env.get_template("modes.html").render(
            **base_context,
            filters={"q": "", "enabled": "all", "behavior_type": "all", "admin_only": "all", "show_test_data": False},
            modes=[mode_row],
            can_create=True,
            message="",
            error="",
        )
        results.append(check(True, "modes list template renders"))
    except Exception as exc:
        results.append(check(False, "modes list template renders", "{0}: {1}".format(type(exc).__name__, exc)))
    try:
        env.get_template("mode_form.html").render(
            **base_context,
            mode="edit",
            mode_id=1,
            mode_data=mode_form,
            errors=[],
            can_edit=True,
            can_set_admin_only=True,
            behavior_types=("reply", "offline"),
            behavior_labels={"reply": "返信", "offline": "反応しない"},
            cooldown_types=("none", "duration", "once_per_period"),
            cooldown_type_labels={},
            cooldown_periods=("none", "monthly"),
            cooldown_period_labels={},
            cooldown_resets=("none", "month_start", "day"),
            cooldown_reset_labels={},
            trigger_types=("probability", "counter_threshold", "period_not_triggered", "manual", "schedule"),
            condition_type_labels={},
            exit_types=("duration", "manual"),
            reset_types=("none", "daily", "monthly", "monthly_day", "manual"),
            reset_type_labels={},
            counters=[],
        )
        results.append(check(True, "mode edit template renders"))
    except Exception as exc:
        results.append(check(False, "mode edit template renders", "{0}: {1}".format(type(exc).__name__, exc)))
    return sum(1 for result in results if result)


def main() -> int:
    template_count = len(list((ROOT_DIR / "admin" / "templates").glob("*.html")))
    total = 9 + 7 + 6 + 6 + 3 + 4 + 3 + template_count + 2
    passed = (
        check_display_definitions()
        + check_build_feature_rows()
        + check_legacy_redirects()
        + check_mention_reaction_action_urls()
        + check_mention_reaction_copy_keywords()
        + check_default_feature_keys()
        + check_runtime_flags()
        + check_admin_templates_parse()
        + check_mode_templates_render()
    )
    print("summary: {0}/{1} OK".format(passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
