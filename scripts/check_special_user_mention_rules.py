import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from admin import servers
from bot.services import runtime_db


class Check:
    def __init__(self) -> None:
        self.results = []

    def add(self, name: str, ok: bool, detail: object = "") -> None:
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


def main() -> int:
    check = Check()
    feature = next(item for item in servers.DISPLAY_FEATURES if item["key"] == "mention_limited")
    check.add("limited feature label is renamed", feature["label"] == "特殊ユーザーメンションルール", feature)
    check.add("limited feature URL remains compatible", feature["edit_path"] == "mention-reactions/limited", feature["edit_path"])
    check.add("limited feature description explains mention trigger", "Botへのメンション" in feature["overview"] or "メンション" in feature["overview"], feature["overview"])

    list_template = (ROOT_DIR / "admin" / "templates" / "mention_limited_effects.html").read_text(encoding="utf-8")
    form_template = (ROOT_DIR / "admin" / "templates" / "mention_limited_effect_form.html").read_text(encoding="utf-8")
    check.add("list template uses new label", "特殊ユーザーメンションルール" in list_template)
    check.add("form template uses new label", "特殊ユーザーメンションルール" in form_template)
    check.add("old visible limited label is removed from templates", "限定機能" not in list_template and "限定機能" not in form_template)

    runtime_source = (ROOT_DIR / "bot" / "services" / "runtime_db.py").read_text(encoding="utf-8")
    check.add("mention suffix guard remains implemented", "mention_suffix_guard" in runtime_source and "apply_mention_suffix_guards" in runtime_source)
    check.add("destroy effect remains implemented", "execute_destroy_effect" in runtime_source and 'effect_type == "destroy"' in runtime_source)
    check.add("suffix guard can suppress normal mention response", "return RuntimeAction(True, True)" in runtime_source)
    check.add("runtime still loads limited effects", "list_limited_effects" in runtime_source and "limited_effects" in runtime_source)
    check.add("feature flag key remains stable", runtime_db.FEATURE_MENTION_LIMITED == "mention_limited", runtime_db.FEATURE_MENTION_LIMITED)

    check.print_results()
    return 0 if check.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
