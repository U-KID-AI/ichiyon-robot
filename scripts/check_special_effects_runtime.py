import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services.runtime_db import execute_effects
from bot.services.runtime_db import get_probability_multiplier_for_target


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
    check.add("next_action_count returns repeat count", result.repeat_count == 2, "repeat={0}".format(result.repeat_count))

    capped = await execute_effects(
        None,
        "guild",
        [effect("next_action_count", {"count": 99, "target_action": "same"})],
        FakeMessage(),
        values,
    )
    check.add("next_action_count is capped", capped.repeat_count == 5, "repeat={0}".format(capped.repeat_count))

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


def main() -> None:
    check = Check()
    asyncio.run(check_execute_effects(check))
    check_multiplier(check)
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
