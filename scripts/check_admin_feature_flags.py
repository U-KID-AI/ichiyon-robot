import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from admin import servers
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
    results.append(check(labels.get("mention_random_draw") == "ランダム抽選", "random draw label is independent", labels))
    results.append(check(labels.get("mention_search") == "検索", "search label is independent", labels))
    results.append(check(labels.get("mention_limited") == "限定機能", "limited label is independent", labels))
    results.append(check(all("メンション:" not in label for label in labels.values()), "mention prefix is removed", labels))
    results.append(check(len(keys) == len(set(keys)), "mention feature keys are unique", keys))
    results.append(check(all("flag_key" not in feature for feature in features), "mention features do not share flag_key", features))
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


def main() -> int:
    total = 6 + 6 + 3
    passed = check_display_definitions() + check_build_feature_rows() + check_runtime_flags()
    print("summary: {0}/{1} OK".format(passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
