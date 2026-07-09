import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import timezone
from typing import Dict, List, Optional, Tuple

from bot.repositories.schedule_templates import ScheduleTemplateRepository


MAX_SCHEDULE_DAYS = 14
JST = timezone(timedelta(hours=9))
REACTION_EMOJIS = ("⭕", "❌")
NUMBER_MARKS = {
    1: "①",
    2: "②",
    3: "③",
    4: "④",
    5: "⑤",
    6: "⑥",
    7: "⑦",
    8: "⑧",
    9: "⑨",
    10: "⑩",
    11: "⑪",
    12: "⑫",
    13: "⑬",
    14: "⑭",
}
WEEKDAY_LABELS = ("月", "火", "水", "木", "金", "土", "日")
USAGE_MESSAGE = (
    "使い方:\n"
    "@Bot スケジュール 7/6から1W レイド\n"
    "@Bot スケジュール 7/6から2W レイド\n"
    "@Bot スケジュール 7/6から3D レイド\n"
    "@Bot スケジュール 7/6から3D\n\n"
    "期間は1W、2W、または1D〜14Dです。最大14日までです。"
)
COMMAND_PATTERN = re.compile(
    r"^スケジュール\s+"
    r"(?:(?P<year>\d{4})[/-])?"
    r"(?P<month>\d{1,2})[/-](?P<day>\d{1,2})"
    r"\s*から\s*(?P<amount>\d{1,2})\s*(?P<unit>[WwＷｗDdＤｄ])"
    r"(?:\s+(?P<title>.+))?$"
)


@dataclass
class ScheduleCommand:
    start_date: date
    days: int
    title: str


@dataclass
class ScheduleBuildResult:
    messages: List[str]
    template_name: Optional[str] = None
    error: str = ""


def is_schedule_command(command_text: str) -> bool:
    return command_text.strip().startswith("スケジュール")


def parse_schedule_command(command_text: str, now: Optional[datetime] = None) -> Tuple[Optional[ScheduleCommand], str]:
    text = command_text.strip()
    match = COMMAND_PATTERN.fullmatch(text)
    if match is None:
        return None, USAGE_MESSAGE

    current = now.astimezone(JST) if now is not None and now.tzinfo is not None else (now or datetime.now(JST))
    year = int(match.group("year") or current.year)
    month = int(match.group("month"))
    day = int(match.group("day"))
    amount = int(match.group("amount"))
    unit = match.group("unit")
    title = (match.group("title") or "").strip()
    try:
        start_date = date(year, month, day)
    except ValueError:
        return None, "日付が正しくありません。\n\n{0}".format(USAGE_MESSAGE)

    if unit in ("W", "w", "Ｗ", "ｗ"):
        if amount not in (1, 2):
            return None, USAGE_MESSAGE
        days = amount * 7
    else:
        days = amount
    if days < 1 or days > MAX_SCHEDULE_DAYS:
        return None, USAGE_MESSAGE
    return ScheduleCommand(start_date=start_date, days=days, title=title), ""


def format_schedule_line(day_number: int, target_date: date, content: str) -> str:
    mark = NUMBER_MARKS[day_number]
    weekday = WEEKDAY_LABELS[target_date.weekday()]
    suffix = " {0}".format(content.strip()) if content.strip() else ""
    return "{0}{1}/{2}({3}){4}".format(mark, target_date.month, target_date.day, weekday, suffix)


def normalize_template_items(items: List[Dict]) -> Dict[int, str]:
    normalized = {}
    for item in items:
        try:
            day_index = int(item.get("day_index"))
        except (TypeError, ValueError):
            continue
        content = str(item.get("content") or "").strip()
        if 1 <= day_index <= MAX_SCHEDULE_DAYS and content:
            normalized[day_index] = content
    return normalized


def build_schedule_messages(command: ScheduleCommand, template_items: Optional[Dict[int, str]] = None) -> List[str]:
    messages = []
    for index in range(1, command.days + 1):
        target_date = command.start_date + timedelta(days=index - 1)
        content = (template_items or {}).get(index, command.title)
        messages.append(format_schedule_line(index, target_date, content))
    return messages


def build_schedule_from_repository(
    connection,
    bot_id: str,
    guild_id: str,
    command: ScheduleCommand,
) -> ScheduleBuildResult:
    repository = ScheduleTemplateRepository(connection, bot_id=bot_id)
    if not command.title:
        return ScheduleBuildResult(messages=build_schedule_messages(command))
    template = repository.get_by_name(guild_id, command.title, enabled=True)
    if template is None:
        return ScheduleBuildResult(messages=build_schedule_messages(command))

    items = normalize_template_items(repository.list_items(int(template["id"])))
    missing_days = [day for day in range(1, command.days + 1) if day not in items]
    if missing_days:
        return ScheduleBuildResult(
            messages=[],
            template_name=str(template["name"]),
            error="テンプレート「{0}」の {1}日目 が未設定です。".format(
                template["name"],
                ", ".join(str(day) for day in missing_days),
            ),
        )
    return ScheduleBuildResult(
        messages=build_schedule_messages(command, items),
        template_name=str(template["name"]),
    )
