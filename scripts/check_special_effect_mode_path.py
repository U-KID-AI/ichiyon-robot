import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


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
    runtime_source = (ROOT_DIR / "bot" / "services" / "runtime_db.py").read_text(encoding="utf-8")
    check.add("counter_set effect updates counters", 'effect_type == "counter_set"' in runtime_source and "repository.set_value" in runtime_source)
    check.add("counter_delta effect updates counters", 'effect_type == "counter_delta"' in runtime_source and "repository.increment" in runtime_source)
    check.add("mode entry uses enter_mode_if_needed", "enter_mode_if_needed" in runtime_source and "mode_trigger_conditions" not in runtime_source)
    check.add("shikocchi counter key remains supported", "shikocchi_count" in runtime_source)
    forbidden_writes = ["SET current_mode_id", "current_mode_id = %s", "current_mode_id=%s"]
    check.add(
        "special effects do not write current_mode_id directly",
        all(token not in runtime_source for token in forbidden_writes),
    )

    docs = (ROOT_DIR / "docs" / "operations" / "special-effects-production.md").read_text(encoding="utf-8")
    check.add("production docs record counter mode path", "counter_set" in docs and "enter_mode_if_needed()" in docs)
    check.add("production docs record no direct current_mode_id writes", "直接変更する経路はありません" in docs)

    check.print_results()
    return 0 if check.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
