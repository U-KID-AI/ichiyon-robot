import sys
from pathlib import Path
from typing import Any, List, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.seed_core_ichiyon_modes import (
    COCONUTS_USER_ID,
    ICHIYON_USER_ID,
    build_mode_specs,
)


SCRIPT_PATH = ROOT_DIR / "scripts" / "seed_core_ichiyon_modes.py"


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))


def main() -> int:
    results: List[Tuple[str, bool, Any]] = []
    specs = {spec["mode_key"]: spec for spec in build_mode_specs(ICHIYON_USER_ID, COCONUTS_USER_ID)}
    raw_source = SCRIPT_PATH.read_text(encoding="utf-8")
    source = raw_source.upper()

    record(
        results,
        "four core modes are defined",
        set(specs) == {"ichiyon_almost", "coconuts_almost", "ryugasaki_hiiro", "taketsumi_robot"},
        sorted(specs),
    )
    record(
        results,
        "ichiyon almost is author scoped",
        specs["ichiyon_almost"]["probability_config"].get("author_user_ids") == [ICHIYON_USER_ID],
        specs["ichiyon_almost"]["probability_config"],
    )
    record(
        results,
        "coconuts almost is author scoped",
        specs["coconuts_almost"]["probability_config"].get("author_user_ids") == [COCONUTS_USER_ID],
        specs["coconuts_almost"]["probability_config"],
    )
    record(
        results,
        "hiiro is keyword and probability scoped",
        specs["ryugasaki_hiiro"]["probability_config"].get("keywords") == ["シャドバ", "スマホ"]
        and specs["ryugasaki_hiiro"]["probability_config"].get("probability") == {"numerator": 1, "denominator": 40},
        specs["ryugasaki_hiiro"]["probability_config"],
    )
    record(
        results,
        "all modes are deletable in seed",
        "is_deletable = %s" in raw_source
        and "True,\n            False,\n            True," in raw_source
        and "True,\n                False,\n                True," in raw_source,
    )
    record(
        results,
        "taketsumi uses counter threshold trigger",
        specs["taketsumi_robot"].get("trigger_condition_type") == "counter_threshold"
        and specs["taketsumi_robot"]["probability_config"] == {
            "counter_key": "taketsumi_count",
            "operator": ">=",
            "value": 1,
        },
        specs["taketsumi_robot"]["probability_config"],
    )
    trigger_effect = specs["taketsumi_robot"].get("trigger_effect") or {}
    auto_reaction = trigger_effect.get("auto_reaction") or {}
    tag = trigger_effect.get("tag") or {}
    record(
        results,
        "taketsumi auto reaction is seeded",
        auto_reaction.get("trigger_text") == "記憶パ"
        and auto_reaction.get("match_type") == "contains"
        and auto_reaction.get("enabled") is True,
        auto_reaction,
    )
    record(
        results,
        "taketsumi counter_set special effect is seeded",
        tag.get("target_type") == "auto_reaction"
        and tag.get("trigger_timing") == "auto_reaction_triggered"
        and tag.get("effect_type") == "counter_set"
        and tag.get("effect_config_json") == {"counter_key": "taketsumi_count", "value": 1},
        tag,
    )
    record(
        results,
        "taketsumi uses special effect assignment",
        "upsert_special_effect_assignment" in raw_source
        and '"auto_reaction"' in raw_source,
    )
    record(
        results,
        "all modes use month-start monthly cooldown",
        all(spec["cooldown_config_json"] == {"type": "once_per_period", "period": "monthly", "reset": "month_start"} for spec in specs.values()),
    )
    record(
        results,
        "all modes last three minutes",
        all(spec["duration_seconds"] == 180 for spec in specs.values()),
    )
    record(
        results,
        "taketsumi has nineteen replies",
        len(specs["taketsumi_robot"]["reply_choices"]) == 19,
        len(specs["taketsumi_robot"]["reply_choices"]),
    )
    forbidden = ["DELETE FROM", "DROP ", "TRUNCATE"]
    record(
        results,
        "seed script does not use destructive SQL",
        not any(token in source for token in forbidden),
        forbidden,
    )

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("summary: {0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
