import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.services.schedule_recruitment import (
    build_schedule_from_repository,
    build_schedule_messages,
    parse_schedule_command,
)


class Check:
    def __init__(self) -> None:
        self.results = []

    def add(self, name: str, ok: bool, detail: Any = "") -> None:
        self.results.append((name, ok, detail))
        print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))

    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.results)


class FakeCursor:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.description = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def execute(self, sql: str, params=None) -> None:
        params = params or []
        if "FROM schedule_templates" in sql and "lower(name) = lower(%s)" in sql:
            bot_id, guild_id, name = params[:3]
            self.description = [Column("id"), Column("bot_id"), Column("guild_id"), Column("name"), Column("is_enabled")]
            row = self.connection.templates.get((bot_id, guild_id, name.lower()))
            self.rows = [row] if row else []
            return
        if "FROM schedule_template_items" in sql:
            template_id = int(params[0])
            self.description = [Column("day_index"), Column("content")]
            self.rows = self.connection.items.get(template_id, [])
            return
        self.rows = []

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Column:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeConnection:
    def __init__(self) -> None:
        self.templates = {
            ("ichiyon", "guild", "レイド"): (1, "ichiyon", "guild", "レイド", True),
            ("irsia", "guild", "レイド"): (2, "irsia", "guild", "レイド", True),
        }
        self.items = {
            1: [(1, "21:30~随時:花NM"), (2, "21:30~随時:消化")],
            2: [(1, "イルシア1日目"), (2, "イルシア2日目")],
        }

    def cursor(self):
        return FakeCursor(self)


def main() -> int:
    check = Check()
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)

    one_week, error = parse_schedule_command("スケジュール 7/6から1W レイド", now)
    check.add("1W command parses", one_week is not None and one_week.days == 7 and not error, one_week)
    if one_week:
        lines = build_schedule_messages(one_week)
        check.add("1W creates 7 messages", len(lines) == 7, lines)
        check.add("schedule line has circled number and weekday", lines[0] == "①7/6(月) レイド", lines[0])

    two_week, error = parse_schedule_command("スケジュール 2026-7-6から2W レイド", now)
    check.add("2W command parses", two_week is not None and two_week.days == 14 and not error, two_week)
    if two_week:
        lines = build_schedule_messages(two_week)
        check.add("2W creates 14 messages", len(lines) == 14 and lines[-1].startswith("⑭"), lines[-1])

    invalid, error = parse_schedule_command("スケジュール 7/6から3W レイド", now)
    check.add("invalid week is rejected", invalid is None and "最大14日" in error, error)

    no_template = build_schedule_from_repository(FakeConnection(), "ichiyon", "guild", one_week)
    check.add(
        "template with missing days errors safely",
        bool(no_template.error) and "未設定" in no_template.error,
        no_template.error,
    )

    short_command, _ = parse_schedule_command("スケジュール 7/6から1W 通常", now)
    normal = build_schedule_from_repository(FakeConnection(), "ichiyon", "guild", short_command)
    check.add("missing template falls back to title", normal.messages[0] == "①7/6(月) 通常", normal.messages[:1])

    two_day_command, _ = parse_schedule_command("スケジュール 7/6から1W レイド", now)
    two_day_command.days = 2
    ichiyon = build_schedule_from_repository(FakeConnection(), "ichiyon", "guild", two_day_command)
    irsia = build_schedule_from_repository(FakeConnection(), "irsia", "guild", two_day_command)
    check.add("template lookup is bot scoped", ichiyon.messages[0] != irsia.messages[0], (ichiyon.messages, irsia.messages))

    return 0 if check.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
