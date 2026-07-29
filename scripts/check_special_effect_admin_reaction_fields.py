from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from admin import special_effects


class Check:
    def __init__(self) -> None:
        self.ok = 0
        self.ng = 0

    def add(self, condition: bool, name: str, detail: object = "") -> None:
        if condition:
            self.ok += 1
            return
        self.ng += 1
        safe_detail = str(detail).encode("unicode_escape").decode("ascii")
        print("[NG] {0}: {1}".format(name, safe_detail))

    def finish(self) -> int:
        total = self.ok + self.ng
        print("special effect admin reaction fields check: {0}/{1} OK".format(self.ok, total))
        return 0 if self.ng == 0 else 1


def build_reaction(**overrides):
    values = {
        "name": "bread reaction",
        "description": "",
        "color": "#6B7280",
        "enabled": "on",
        "admin_only": None,
        "priority": "0",
        "target_type": "auto_reaction",
        "trigger_timing": "auto_reaction_triggered",
        "effect_type": "reaction",
        "effect_config_json": '{"keep": "yes"}',
        "additional_text": "ignored",
        "additional_post_timing": "effect_success",
        "expires_type": "permanent",
        "expires_value": "",
        "cooldown_seconds": "600",
        "cooldown_scope": "guild",
        "max_multiplier": "9",
        "reaction_emoji": "bread",
        "reaction_target": "source_message",
        "reaction_probability_numerator": "1",
        "reaction_probability_denominator": "1000",
    }
    values.update(overrides)
    return special_effects.build_form(**values)


def main() -> int:
    check = Check()

    form, errors = build_reaction()
    check.add(errors == [], "valid reaction form has no errors", errors)
    check.add(form["effect_config"]["emoji"] == "bread", "reaction emoji is saved")
    check.add(form["effect_config"]["target"] == "source_message", "reaction target is source message")
    check.add(form["effect_config"]["probability"] == {"numerator": 1, "denominator": 1000}, "fraction probability is saved", form["effect_config"])
    check.add(form["effect_config"]["keep"] == "yes", "unknown json key is preserved", form["effect_config"])
    check.add(form["additional_text"] == "" and form["additional_post_timing"] == "none", "reaction ignores additional post fields")
    check.add(form["max_multiplier"] is None, "reaction ignores max multiplier")
    check.add(form["reaction_probability_label"] == "1/1000", "fraction label is shown", form["reaction_probability_label"])

    hidden_invalid, hidden_invalid_errors = build_reaction(max_multiplier="not-a-number")
    check.add(hidden_invalid_errors == [], "reaction ignores hidden invalid max multiplier", hidden_invalid_errors)
    check.add(hidden_invalid["max_multiplier"] is None, "hidden invalid max multiplier is cleared")

    always, always_errors = build_reaction(
        reaction_probability_numerator="1",
        reaction_probability_denominator="1",
    )
    check.add(always_errors == [], "1/1 probability is valid", always_errors)
    check.add(always["effect_config"]["probability"] == {"numerator": 1, "denominator": 1}, "1/1 is stored exactly")

    never, never_errors = build_reaction(
        reaction_probability_numerator="0",
        reaction_probability_denominator="1",
    )
    check.add(never_errors == [], "0/1 probability is valid", never_errors)
    check.add(never["effect_config"]["probability"] == {"numerator": 0, "denominator": 1}, "0/1 is stored exactly")

    empty_probability, empty_errors = build_reaction(
        reaction_probability_numerator="",
        reaction_probability_denominator="",
    )
    check.add(empty_errors == [], "empty probability means always", empty_errors)
    check.add("probability" not in empty_probability["effect_config"], "empty probability omits config", empty_probability["effect_config"])

    _, one_sided_errors = build_reaction(
        reaction_probability_numerator="1",
        reaction_probability_denominator="",
    )
    check.add(bool(one_sided_errors), "one-sided probability is rejected")

    _, bad_denominator_errors = build_reaction(
        reaction_probability_numerator="1",
        reaction_probability_denominator="0",
    )
    check.add(bool(bad_denominator_errors), "zero denominator is rejected")

    _, empty_emoji_errors = build_reaction(reaction_emoji="")
    check.add(bool(empty_emoji_errors), "empty reaction emoji is rejected")

    restored = special_effects.build_form_from_tag(
        {
            "id": 1,
            "name": "bread",
            "effect_type": "reaction",
            "effect_config_json": {"emoji": "bread", "target": "source_message", "probability": {"numerator": 1, "denominator": 1}},
            "enabled": True,
        }
    )
    check.add(restored["reaction_emoji"] == "bread", "stored reaction emoji is restored")
    check.add(restored["reaction_probability_label"] == "1/1", "stored 1/1 label is restored", restored["reaction_probability_label"])
    check.add("絵文字" in restored["effect_summary"], "reaction row summary is readable", restored["effect_summary"])

    template = (ROOT / "admin" / "templates" / "special_effect_form.html").read_text(encoding="utf-8")
    check.add('data-effect-section="reaction"' in template, "template has reaction section")
    check.add('name="reaction_emoji"' in template, "template has reaction emoji input")
    check.add('name="reaction_probability_numerator"' in template, "template has probability numerator input")
    check.add("updateEffectSections" in template, "template switches effect sections")

    return check.finish()


if __name__ == "__main__":
    raise SystemExit(main())
