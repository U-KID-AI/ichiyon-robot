import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services import runtime_db
from bot.services.runtime_db import build_effective_weighted_rows
from bot.services.runtime_db import execute_effects
from bot.services.runtime_db import get_next_action_extra_repeats
from bot.services.runtime_db import get_probability_multiplier_for_target
from bot.services.runtime_db import probability_hit_with_multiplier


class FakeChannel:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


class FakeMessage:
    def __init__(self) -> None:
        self.channel = FakeChannel()
        self.reactions = []

    async def add_reaction(self, emoji) -> None:
        self.reactions.append(str(emoji))


class FakeCounterRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def ensure_counter(self, guild_id: str, count_key: str, name: str, *args, **kwargs) -> Dict[str, Any]:
        counters = self.connection.setdefault("counters", {})
        counters.setdefault(count_key, {"id": count_key, "count_key": count_key, "initial_value": 0})
        self.connection.setdefault("values", {}).setdefault(count_key, counters[count_key]["initial_value"])
        return counters[count_key]

    def get_value(self, guild_id: str, count_key: str, default: int = 0) -> int:
        return int(self.connection.setdefault("values", {}).get(count_key, default))

    def increment(self, guild_id: str, count_key: str, amount: int = 1, *args, **kwargs) -> Dict[str, Any]:
        values = self.connection.setdefault("values", {})
        values[count_key] = int(values.get(count_key, 0)) + amount
        return {"current_value": values[count_key]}

    def set_value(self, guild_id: str, count_key: str, value: int, *args, **kwargs) -> Dict[str, Any]:
        values = self.connection.setdefault("values", {})
        values[count_key] = int(value)
        return {"current_value": values[count_key]}


class Check:
    def __init__(self) -> None:
        self.results = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append({"name": name, "ok": ok, "detail": detail})

    def print_results(self) -> None:
        for result in self.results:
            label = "OK" if result["ok"] else "NG"
            safe_detail = str(result["detail"]).encode("ascii", "backslashreplace").decode("ascii")
            detail = " - {0}".format(safe_detail) if result["detail"] else ""
            print("[{0}] {1}{2}".format(label, result["name"], detail))
        passed = len([result for result in self.results if result["ok"]])
        print("summary: {0}/{1} OK".format(passed, len(self.results)))

    def ok(self) -> bool:
        return all(result["ok"] for result in self.results)


def effect(effect_type: str, config: Dict[str, Any], additional_text: str = "") -> Dict[str, Any]:
    return {
        "id": len(str(config)) + len(effect_type),
        "effect_type": effect_type,
        "effect_config_json": config,
        "additional_text": additional_text,
        "additional_post_timing": "effect_success",
        "target_type": "mention_reaction_choice",
    }


async def check_execute_effects(check: Check) -> None:
    message = FakeMessage()
    values = {
        "match_1": "しこっち",
        "user_name": "User",
        "user_mention": "<@1>",
        "message_text": "hello",
    }
    effects: List[Dict[str, Any]] = [
        effect("message", {}, "追加 {match_1:hankaku}"),
        effect("reaction", {"emoji": "🍒"}),
        effect("next_action_count", {"count": 2, "target_action": "same"}),
    ]
    result = await execute_effects(None, "guild", effects, message, values)
    check.add("message effect sends template text", message.channel.sent == ["追加 ｼｺｯﾁ"], str(message.channel.sent))
    check.add("reaction effect adds emoji", message.reactions == ["🍒"], str(message.reactions))
    check.add(
        "next_action_count is queued for next action",
        result.repeat_count == 0 and len(result.pending_effects) == 1,
        "repeat={0} pending={1}".format(result.repeat_count, len(result.pending_effects)),
    )
    check.add(
        "queued next_action_count repeats next action once",
        get_next_action_extra_repeats(result.pending_effects, "mention_reaction_choice") == 1,
    )

    capped = await execute_effects(
        None,
        "guild",
        [effect("next_action_count", {"count": 99, "target_action": "same"})],
        FakeMessage(),
        values,
    )
    check.add(
        "next_action_count total is capped",
        get_next_action_extra_repeats(capped.pending_effects, "mention_reaction_choice") == 4,
        "pending={0}".format(len(capped.pending_effects)),
    )

    destroy_message = FakeMessage()
    destroy_result = await execute_effects(
        None,
        "guild",
        [
            effect("destroy", {"action": "log_only", "reason": "dry-run"}),
            effect("destroy", {"action": "send_message", "message": "破壊 {match_1:hankaku}"}),
            effect("destroy", {"action": "counter_reset", "counter_key": "narita_count", "value": 0}),
        ],
        destroy_message,
        values,
    )
    check.add(
        "destroy send_message sends template text",
        destroy_message.channel.sent == ["破壊 ｼｺｯﾁ"],
        str(destroy_message.channel.sent),
    )
    check.add(
        "destroy dry-run counter_reset does not crash",
        destroy_result.count_changed is False,
        "count_changed={0}".format(destroy_result.count_changed),
    )


def check_multiplier(check: Check) -> None:
    effects = [
        {
            "id": 1,
            "effect_type": "probability_multiplier",
            "target_type": "mention_reaction_choice",
            "effect_config_json": {"multiplier": 9, "target": {"type": "mention_reaction_choice", "id": 10}},
        }
    ]
    multiplier = get_probability_multiplier_for_target(effects, "mention_reaction_choice", 10)
    check.add("probability_multiplier reads target multiplier", multiplier == 9.0, "multiplier={0}".format(multiplier))

    mismatch = get_probability_multiplier_for_target(effects, "mention_reaction_choice", 11)
    check.add("probability_multiplier ignores mismatch", mismatch == 1.0, "multiplier={0}".format(mismatch))

    broad_next = [
        {
            "id": 2,
            "effect_type": "probability_multiplier",
            "target_type": "auto_reaction",
            "effect_config_json": {"multiplier": 32, "label": "raio"},
        }
    ]
    broad_multiplier = get_probability_multiplier_for_target(broad_next, "special_effect_tag", 99)
    check.add(
        "probability_multiplier without target applies to next probability",
        broad_multiplier == 32.0,
        "multiplier={0}".format(broad_multiplier),
    )
    check.add(
        "raio 32x makes one-in-32 probability certain",
        probability_hit_with_multiplier({"probability": {"numerator": 1, "denominator": 32}}, broad_multiplier) is True,
    )

    choices = [
        {"id": 10, "appearance_rate": 1},
        {"id": 11, "appearance_rate": 1},
    ]
    single = build_effective_weighted_rows(choices, "mention_reaction_choice", effects)
    check.add(
        "probability_multiplier increases target choice weight",
        [weight for _, weight in single] == [9, 1],
        str(single),
    )
    stacked_effects = effects + [
        {
            "id": 3,
            "effect_type": "probability_multiplier",
            "target_type": "mention_reaction_choice",
            "effect_config_json": {"multiplier": 9, "target": {"type": "mention_reaction_choice", "id": 10}},
        }
    ]
    stacked = build_effective_weighted_rows(choices, "mention_reaction_choice", stacked_effects)
    check.add(
        "probability_multiplier stacks by multiplication",
        [weight for _, weight in stacked] == [81, 1],
        str(stacked),
    )
    mismatch_weight = build_effective_weighted_rows(choices, "auto_reaction", stacked_effects)
    check.add(
        "probability_multiplier does not affect unrelated target type",
        [weight for _, weight in mismatch_weight] == [1, 1],
        str(mismatch_weight),
    )


async def check_probability_multiplier_effect_execution(check: Check) -> None:
    message = FakeMessage()
    values = {"match_1": "しこっち"}
    raio = effect("probability_multiplier", {"multiplier": 32, "label": "raio"})
    mini = effect(
        "probability_message",
        {"probability": {"numerator": 1, "denominator": 32}},
        ":yukkuri_itiyon: ｲﾔ〜{match_1:hankaku}ﾈ〜",
    )
    result = await execute_effects(None, "guild", [mini], message, values, [raio])
    check.add(
        "raio 32x triggers mini-style probability_message",
        message.channel.sent == [":yukkuri_itiyon: ｲﾔ〜ｼｺｯﾁﾈ〜"] and result.count_changed is False,
        str(message.channel.sent),
    )


async def check_additional_text_placeholders(check: Check) -> None:
    original_repository = runtime_db.CounterRepository
    runtime_db.CounterRepository = FakeCounterRepository
    try:
        connection = {"values": {"narita_count": 7}}
        values = {
            "match_1": "縺励％縺｣縺｡",
            "user_name": "User",
            "user_mention": "<@1>",
            "message_text": "hello",
        }

        counter_message = FakeMessage()
        await execute_effects(
            connection,
            "guild",
            [
                effect(
                    "message",
                    {},
                    "narita={counter:narita_count} missing={counter:missing_count} match={match_1:hankaku}",
                )
            ],
            counter_message,
            values,
        )
        sent_text = counter_message.channel.sent[0] if counter_message.channel.sent else ""
        check.add(
            "additional_text resolves counter placeholders",
            "narita=7" in sent_text and "missing=0" in sent_text,
            sent_text,
        )
        check.add(
            "additional_text keeps existing match transforms",
            "match=" in sent_text and "{match_1" not in sent_text,
            sent_text,
        )

        raio_message = FakeMessage()
        await execute_effects(
            connection,
            "guild",
            [
                effect(
                    "probability_multiplier",
                    {"multiplier": 9, "label": "raio"},
                    "label={effect_label} one={effect_multiplier} total={effective_multiplier}",
                )
            ],
            raio_message,
            values,
        )
        raio_text = raio_message.channel.sent[0] if raio_message.channel.sent else ""
        check.add(
            "probability_multiplier additional_text shows multiplier",
            "one=9" in raio_text and "total=9" in raio_text and "label=raio" in raio_text,
            raio_text,
        )

        pending = []
        totals = []
        for _ in range(4):
            step_message = FakeMessage()
            step_result = await execute_effects(
                connection,
                "guild",
                [
                    effect(
                        "probability_multiplier",
                        {"multiplier": 9, "label": "raio"},
                        "total={effective_multiplier}",
                    )
                ],
                step_message,
                values,
                pending,
            )
            pending = step_result.pending_effects
            totals.append(step_message.channel.sent[0] if step_message.channel.sent else "")
        check.add(
            "probability_multiplier stacks through four activations",
            totals == ["total=9", "total=81", "total=729", "total=6561"],
            str(totals),
        )
        check.add(
            "probability_multiplier pending keeps duplicate labels",
            len(pending) == 4 and get_probability_multiplier_for_target(pending, "special_effect_tag", 999) == 6561.0,
            "pending={0} multiplier={1}".format(
                len(pending),
                get_probability_multiplier_for_target(pending, "special_effect_tag", 999),
            ),
        )

        stacked_pending = [
            effect("probability_multiplier", {"multiplier": 9, "label": "raio"}),
            effect("probability_multiplier", {"multiplier": 9, "label": "raio"}),
            effect("probability_multiplier", {"multiplier": 9, "label": "raio"}),
        ]
        shikocchi_message = FakeMessage()
        result = await execute_effects(
            connection,
            "guild",
            [
                effect(
                    "counter_set",
                    {
                        "counter_key": "shikocchi_count",
                        "value": 1,
                        "probability": {"numerator": 1, "denominator": 444},
                    },
                    "base={base_probability} now={effective_probability} percent={probability_percent} total={effective_multiplier}",
                )
            ],
            shikocchi_message,
            values,
            stacked_pending,
        )
        shikocchi_text = shikocchi_message.channel.sent[0] if shikocchi_message.channel.sent else ""
        check.add(
            "stacked multiplier appears in probability placeholders",
            "base=1/444" in shikocchi_text
            and "now=1/1" in shikocchi_text
            and "percent=100%" in shikocchi_text
            and "total=729" in shikocchi_text,
            shikocchi_text,
        )
        check.add(
            "raio stacked three times makes shikocchi certain",
            result.count_changed is True and connection["values"].get("shikocchi_count") == 1,
            "count_changed={0} value={1}".format(result.count_changed, connection["values"].get("shikocchi_count")),
        )
    finally:
        runtime_db.CounterRepository = original_repository


def main() -> None:
    check = Check()
    asyncio.run(check_execute_effects(check))
    check_multiplier(check)
    asyncio.run(check_probability_multiplier_effect_execution(check))
    asyncio.run(check_additional_text_placeholders(check))
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
