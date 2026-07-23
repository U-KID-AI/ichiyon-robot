import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.db import get_connection
from bot.repositories import AutoPostRepository
from bot.repositories.base import json_dumps
from bot.services.auto_posts import get_due_run


JST = timezone(timedelta(hours=9))


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


def make_post(schedule_type: str, schedule: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": 1,
        "guild_id": "111",
        "name": "runtime check",
        "body": "サ終やめませんか？",
        "image_path": "",
        "channel_id": "123",
        "schedule_type": schedule_type,
        "schedule_value": json_dumps(schedule),
    }


def check_schedule() -> Check:
    check = Check()

    yearly = make_post("yearly", {"type": "yearly", "month": 6, "day": 30, "time": "09:00", "timezone": "Asia/Tokyo"})
    yearly_due = get_due_run(yearly, datetime(2026, 6, 30, 9, 0, tzinfo=JST))
    check.add("yearly 6/30 is due", yearly_due is not None and yearly_due.due_key == "yearly:2026-06-30T09:00+09:00")

    yearly_before = get_due_run(yearly, datetime(2026, 6, 30, 8, 59, tzinfo=JST))
    check.add("yearly before time is not due", yearly_before is None)

    monthly = make_post("monthly", {"type": "monthly", "day": 20, "time": "10:00", "timezone": "Asia/Tokyo"})
    monthly_due = get_due_run(monthly, datetime(2026, 6, 20, 10, 0, tzinfo=JST))
    check.add("monthly is due", monthly_due is not None and monthly_due.due_key == "monthly:2026-06-20T10:00+09:00")
    monthly_next = get_due_run(monthly, datetime(2026, 7, 20, 10, 0, tzinfo=JST))
    check.add("monthly next occurrence has distinct key", monthly_next is not None and monthly_next.due_key == "monthly:2026-07-20T10:00+09:00")

    daily = make_post("daily", {"type": "daily", "time": "10:00", "timezone": "Asia/Tokyo"})
    daily_due = get_due_run(daily, datetime(2026, 6, 20, 10, 1, tzinfo=JST))
    check.add("daily is due after time", daily_due is not None and daily_due.due_key == "daily:2026-06-20T10:00+09:00")
    check.add("daily key is not legacy date-only key", daily_due is not None and daily_due.due_key != "daily:2026-06-20")
    daily_changed_time = make_post("daily", {"type": "daily", "time": "10:05", "timezone": "Asia/Tokyo"})
    daily_changed_due = get_due_run(daily_changed_time, datetime(2026, 6, 20, 10, 5, tzinfo=JST))
    check.add("daily changed time has distinct key", daily_changed_due is not None and daily_changed_due.due_key == "daily:2026-06-20T10:05+09:00")

    weekly = make_post("weekly", {"type": "weekly", "weekday": "saturday", "time": "10:00", "timezone": "Asia/Tokyo"})
    weekly_due = get_due_run(weekly, datetime(2026, 6, 20, 10, 0, tzinfo=JST))
    check.add("weekly is due on matching weekday", weekly_due is not None and weekly_due.due_key == "weekly:2026-06-20T10:00+09:00")
    weekly_next = get_due_run(weekly, datetime(2026, 6, 27, 10, 0, tzinfo=JST))
    check.add("weekly next occurrence has distinct key", weekly_next is not None and weekly_next.due_key == "weekly:2026-06-27T10:00+09:00")

    interval_one = make_post("interval", {"type": "interval", "interval_minutes": 1, "timezone": "Asia/Tokyo"})
    interval_first = get_due_run(interval_one, datetime(2026, 6, 20, 10, 1, 30, tzinfo=JST))
    interval_second = get_due_run(interval_one, datetime(2026, 6, 20, 10, 2, 5, tzinfo=JST))
    check.add("interval one minute is due for current minute", interval_first is not None and interval_first.due_key == "interval:2026-06-20T10:01+09:00")
    check.add("interval next minute has distinct key", interval_second is not None and interval_second.due_key == "interval:2026-06-20T10:02+09:00")

    interval_five = make_post("interval", {"type": "interval", "interval_minutes": 5, "timezone": "Asia/Tokyo"})
    interval_five_due = get_due_run(interval_five, datetime(2026, 6, 20, 10, 7, 30, tzinfo=JST))
    check.add("interval five minutes snaps to boundary", interval_five_due is not None and interval_five_due.due_key == "interval:2026-06-20T10:05+09:00")

    interval_invalid_zero = get_due_run(make_post("interval", {"type": "interval", "interval_minutes": 0}), datetime(2026, 6, 20, 10, 0, tzinfo=JST))
    interval_invalid_text = get_due_run(make_post("interval", {"type": "interval", "interval_minutes": "abc"}), datetime(2026, 6, 20, 10, 0, tzinfo=JST))
    interval_invalid_large = get_due_run(make_post("interval", {"type": "interval", "interval_minutes": 1441}), datetime(2026, 6, 20, 10, 0, tzinfo=JST))
    check.add("interval zero is rejected", interval_invalid_zero is None)
    check.add("interval non-integer is rejected", interval_invalid_text is None)
    check.add("interval above max is rejected", interval_invalid_large is None)

    utc_now = datetime(2026, 6, 20, 1, 1, tzinfo=timezone.utc)
    utc_due = get_due_run(daily, utc_now)
    check.add("UTC input converts to Asia/Tokyo due key", utc_due is not None and utc_due.due_key == "daily:2026-06-20T10:00+09:00")

    repository_source = (ROOT_DIR / "bot" / "repositories" / "auto_posts.py").read_text(encoding="utf-8")
    check.add("delivery lock uses advisory lock", "pg_try_advisory_xact_lock" in repository_source)

    return check


def check_db_history(database_url: str, guild_id: str, check: Check) -> None:
    with get_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO guilds (guild_id, name)
                VALUES (%s, 'auto post runtime check')
                ON CONFLICT (guild_id) DO NOTHING
                """,
                (guild_id,),
            )
            cursor.execute(
                """
                DELETE FROM auto_posts
                WHERE guild_id = %s AND name = 'integration_check_auto_post_runtime'
                """,
                (guild_id,),
            )
        repository = AutoPostRepository(connection)
        post = repository.create_post(
            guild_id,
            "integration_check_auto_post_runtime",
            "サ終やめませんか？",
            None,
            "0",
            "yearly",
            json_dumps({"type": "yearly", "month": 6, "day": 30, "time": "09:00", "timezone": "Asia/Tokyo"}),
            None,
            True,
        )
        due_key = "yearly:2026-06-30T09:00+09:00"
        before = repository.was_delivered(int(post["id"]), due_key)
        repository.record_delivery(guild_id, int(post["id"]), due_key, post.get("channel_id"))
        after = repository.was_delivered(int(post["id"]), due_key)
        duplicate = repository.record_delivery(guild_id, int(post["id"]), due_key, post.get("channel_id"))
        check.add("delivery history starts empty", before is False)
        check.add("delivery history records sent key", after is True)
        check.add("delivery history prevents duplicate key", duplicate is None)
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM auto_posts WHERE id = %s", (post["id"],))
        connection.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check DB auto post runtime scheduling.")
    parser.add_argument("--database-url", help="Optional DATABASE_URL for delivery history check.")
    parser.add_argument("--guild-id", default="integration_check_auto_posts", help="Guild ID for optional DB check.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    check = check_schedule()
    if args.database_url:
        check_db_history(args.database_url, args.guild_id, check)
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
