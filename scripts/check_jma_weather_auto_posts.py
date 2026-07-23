import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import admin.auto_posts as admin_auto_posts
import bot.services.auto_posts as auto_posts_runtime
from bot.repositories.base import json_dumps
from bot.services import jma_weather


JST = timezone(timedelta(hours=9))


class Check:
    def __init__(self) -> None:
        self.results: List[Dict[str, Any]] = []

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


AREA_MASTER = {
    "offices": {
        "430000": {"name": "熊本県", "children": ["430010", "430020", "430030", "430040"]},
        "016000": {"name": "北海道地方", "children": ["011000"]},
        "130000": {"name": "東京都", "children": ["130010", "130020"]},
        "460100": {"name": "鹿児島県", "children": ["460010"]},
        "471000": {"name": "沖縄本島地方", "children": ["471010"]},
    },
    "class10s": {
        "430010": {"name": "熊本地方"},
        "430020": {"name": "阿蘇地方"},
        "430030": {"name": "天草・芦北地方"},
        "430040": {"name": "球磨地方"},
        "011000": {"name": "石狩地方"},
        "130010": {"name": "東京地方"},
        "130020": {"name": "伊豆諸島北部"},
        "460010": {"name": "薩摩地方"},
        "471010": {"name": "本島中南部"},
    },
}


FORECAST = [
    {
        "reportDatetime": "2026-07-24T05:00:00+09:00",
        "timeSeries": [
            {
                "timeDefines": ["2026-07-24T05:00:00+09:00", "2026-07-25T00:00:00+09:00"],
                "areas": [
                    {"area": {"name": "熊本地方", "code": "430010"}, "weathers": ["晴れ時々曇り", "曇り"]},
                    {"area": {"name": "阿蘇地方", "code": "430020"}, "weathers": ["曇り時々雨", "雨"]},
                    {"area": {"name": "球磨地方", "code": "430040"}, "weathers": ["晴れ", "晴れ"]},
                ],
            },
            {
                "timeDefines": [
                    "2026-07-24T00:00:00+09:00",
                    "2026-07-24T06:00:00+09:00",
                    "2026-07-24T12:00:00+09:00",
                    "2026-07-24T18:00:00+09:00",
                ],
                "areas": [
                    {"area": {"name": "熊本地方", "code": "430010"}, "pops": ["", "10", "20", "30"]},
                    {"area": {"name": "阿蘇地方", "code": "430020"}, "pops": ["", "20", "30", "40"]},
                ],
            },
            {
                "timeDefines": ["2026-07-24T09:00:00+09:00", "2026-07-24T15:00:00+09:00"],
                "areas": [
                    {"area": {"name": "熊本", "code": "8610"}, "temps": ["26", "35"]},
                    {"area": {"name": "阿蘇乙姫", "code": "8611"}, "temps": ["21", "30"]},
                    {"area": {"name": "人吉", "code": "8612"}, "temps": ["23", "34"]},
                ],
            },
        ],
    }
]


def check_area_master(check: Check) -> None:
    offices = jma_weather.list_forecast_offices(AREA_MASTER)
    areas = jma_weather.list_class10_areas(AREA_MASTER, "430000")
    check.add("area master lists forecast offices", any(row["code"] == "430000" for row in offices))
    check.add("kumamoto office has four class10 areas", [row["code"] for row in areas] == ["430010", "430020", "430030", "430040"])
    check.add("hokkaido-like hierarchy is selectable", jma_weather.list_class10_areas(AREA_MASTER, "016000")[0]["code"] == "011000")
    check.add("tokyo-like hierarchy is selectable", len(jma_weather.list_class10_areas(AREA_MASTER, "130000")) == 2)
    check.add("kagoshima-like hierarchy is selectable", jma_weather.list_class10_areas(AREA_MASTER, "460100")[0]["name"] == "薩摩地方")
    check.add("okinawa-like hierarchy is selectable", jma_weather.list_class10_areas(AREA_MASTER, "471000")[0]["name"] == "本島中南部")
    check.add("invalid office is rejected", bool(jma_weather.validate_weather_config({"office_code": "999999", "area_codes": ["430010"]}, AREA_MASTER)))
    check.add("out-of-office area is rejected", bool(jma_weather.validate_weather_config({"office_code": "430000", "area_codes": ["130010"]}, AREA_MASTER)))
    check.add("single string area code normalizes to list", jma_weather.normalize_area_codes("430010") == ["430010"])
    check.add("integer area code normalizes to string", jma_weather.normalize_area_codes(430010) == ["430010"])
    check.add("duplicate area codes are removed", jma_weather.normalize_area_codes([" 430010 ", "430010", "", None]) == ["430010"])
    check.add("primary subdivision alias is accepted", jma_weather.get_config_area_codes({"primary_subdivision_codes": ["430010"]}) == ["430010"])


def check_forecast_parsing(check: Check) -> None:
    config = {"office_code": "430000", "area_codes": ["430010", "430020"]}
    bundle = jma_weather.parse_forecast(AREA_MASTER, FORECAST, config, now=datetime(2026, 7, 24, 7, 0, tzinfo=JST))
    messages = jma_weather.format_forecast_message(bundle)
    message = "\n".join(messages)
    check.add("forecast keeps report datetime", "7/24 05:00" in message)
    check.add("forecast includes selected class10 only", "熊本地方" in message and "阿蘇地方" in message and "球磨地方" not in message)
    check.add("weather text is included", "晴れ時々曇り" in message and "曇り時々雨" in message)
    check.add("precipitation aligns with timeDefines", "6-12時 10%" in message and "12-18時 20%" in message)
    check.add("past precipitation window is omitted", "0-6時" not in message)
    check.add("representative temperatures are separate", "代表地点の気温" in message and "熊本: 最低 26℃ / 最高 35℃" in message)
    check.add("missing values are not converted to zero", "最低 0℃" not in message and "最高 0℃" not in message)


def check_subdivision_filtering(check: Check) -> None:
    now = datetime(2026, 7, 24, 7, 0, tzinfo=JST)
    single_bundle = jma_weather.parse_forecast(
        AREA_MASTER,
        FORECAST,
        {"office_code": "430000", "area_codes": ["430010"]},
        now=now,
    )
    multi_bundle = jma_weather.parse_forecast(
        AREA_MASTER,
        FORECAST,
        {"office_code": "430000", "area_codes": ["430010", "430040"]},
        now=now,
    )
    alias_bundle = jma_weather.parse_forecast(
        AREA_MASTER,
        FORECAST,
        {"office_code": "430000", "primary_subdivision_codes": ["430010"]},
        now=now,
    )
    all_bundle = jma_weather.parse_forecast(
        AREA_MASTER,
        FORECAST,
        {"office_code": "430000", "area_codes": ["430010", "430020", "430030", "430040"]},
        now=now,
    )

    check.add("single selected subdivision generates one area", len(single_bundle.area_lines) == 1, str(single_bundle.area_lines))
    check.add("multiple selected subdivisions generate selected areas", len(multi_bundle.area_lines) == 2, str(multi_bundle.area_lines))
    check.add("primary subdivision alias generates selected area", len(alias_bundle.area_lines) == 1, str(alias_bundle.area_lines))
    check.add("all selected subdivisions keep available areas", len(all_bundle.area_lines) == 3, str(all_bundle.area_lines))

    try:
        jma_weather.parse_forecast(
            AREA_MASTER,
            FORECAST,
            {"office_code": "430000", "area_codes": ["439999"]},
            now=now,
        )
        check.add("empty subdivision filter raises diagnostic error", False, "no error")
    except jma_weather.JmaWeatherError as exc:
        detail = str(exc)
        check.add(
            "empty subdivision filter raises diagnostic error",
            "requested_codes=439999" in detail and "available_codes=" in detail,
            detail,
        )


async def check_admin_form(check: Check) -> None:
    static_form, static_errors, _ = admin_auto_posts.build_form(
        {
            "name": "static",
            "body": "hello",
            "image_path": "",
            "channel_id": "123",
            "schedule_type": "daily",
            "time": "07:00",
            "timezone": "Asia/Tokyo",
            "enabled": "on",
            "content_type": "static",
        }
    )
    weather_form, weather_errors, _ = admin_auto_posts.build_form(
        {
            "name": "weather",
            "body": "",
            "image_path": "",
            "channel_id": "123",
            "schedule_type": "daily",
            "time": "07:00",
            "timezone": "Asia/Tokyo",
            "enabled": "on",
            "content_type": "jma_weather",
            "office_code": "430000",
            "area_codes": ["430010", "430020"],
        }
    )
    check.add("static form remains static", not static_errors and static_form["content_type"] == "static")
    check.add("weather form allows empty body", not weather_errors and weather_form["content_type"] == "jma_weather")
    check.add("weather form stores selected office and areas", '"office_code": "430000"' in weather_form["content_config_json"] and "430020" in weather_form["content_config_json"])

    single_weather_form, single_weather_errors, _ = admin_auto_posts.build_form(
        {
            "name": "weather",
            "body": "",
            "image_path": "",
            "channel_id": "123",
            "schedule_type": "daily",
            "time": "07:00",
            "timezone": "Asia/Tokyo",
            "enabled": "on",
            "content_type": "jma_weather",
            "office_code": "430000",
            "area_codes": "430010",
        }
    )
    restored_form = admin_auto_posts.build_form_from_post(
        {
            "content_type": "jma_weather",
            "content_config_json": single_weather_form["content_config_json"],
            "enabled": True,
        }
    )
    check.add("admin weather form stores single area as list", not single_weather_errors and '"area_codes": ["430010"]' in single_weather_form["content_config_json"])
    check.add("admin edit restores selected single area", restored_form["area_codes"] == ["430010"], str(restored_form["area_codes"]))

    original_get_area_master = admin_auto_posts.get_area_master

    async def fake_get_area_master() -> Dict[str, Any]:
        return AREA_MASTER

    try:
        admin_auto_posts.get_area_master = fake_get_area_master
        valid_errors = await admin_auto_posts.validate_jma_weather_form(weather_form)
        invalid_form = dict(weather_form)
        invalid_form["area_codes"] = ["130010"]
        invalid_errors = await admin_auto_posts.validate_jma_weather_form(invalid_form)
        context = await admin_auto_posts.build_weather_context(weather_form)
    finally:
        admin_auto_posts.get_area_master = original_get_area_master

    check.add("admin weather validation accepts selected class10", not valid_errors)
    check.add("admin weather validation rejects unrelated class10", bool(invalid_errors))
    check.add("admin weather context is master-derived", len(context["weather_offices"]) >= 5 and len(context["weather_areas"]) >= 9)


class FakeConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeRepo:
    posts: List[Dict[str, Any]] = []
    delivered = 0
    updated = 0

    def __init__(self, connection) -> None:
        self.connection = connection

    def list_enabled_posts(self) -> List[Dict[str, Any]]:
        return list(self.posts)

    def was_delivered(self, post_id: int, due_key: str) -> bool:
        return False

    def record_delivery(self, guild_id: str, post_id: int, due_key: str, channel_id: Optional[str]) -> None:
        self.__class__.delivered += 1

    def update_last_posted_at(self, guild_id: str, post_id: int) -> None:
        self.__class__.updated += 1


class FakeFlagRepo:
    def __init__(self, connection) -> None:
        self.connection = connection

    def is_enabled(self, guild_id: str, feature_key: str, default: bool = True) -> bool:
        return True


class FakeChannel:
    def __init__(self) -> None:
        self.sent: List[str] = []

    async def send(self, content: str) -> None:
        self.sent.append(content)


def make_due_post(content_type: str = "jma_weather") -> Dict[str, Any]:
    return {
        "id": 10,
        "guild_id": "111",
        "name": "weather",
        "body": "static body",
        "image_path": "",
        "channel_id": "222",
        "schedule_type": "daily",
        "schedule_value": json_dumps({"type": "daily", "time": "07:00", "timezone": "Asia/Tokyo"}),
        "content_type": content_type,
        "content_config_json": json_dumps({"office_code": "430000", "area_codes": ["430010"]}),
        "enabled": True,
    }


async def check_runtime_success(check: Check) -> None:
    connection = FakeConnection()
    channel = FakeChannel()
    FakeRepo.posts = [make_due_post()]
    FakeRepo.delivered = 0
    FakeRepo.updated = 0

    original_connection = auto_posts_runtime.get_connection
    original_repo = auto_posts_runtime.AutoPostRepository
    original_flag_repo = auto_posts_runtime.FeatureFlagRepository
    original_channel = auto_posts_runtime.get_post_channel
    original_build = auto_posts_runtime.build_weather_messages

    async def fake_channel(bot, post):
        return channel

    async def fake_build(config, forecast_cache, now=None):
        forecast_cache.setdefault("430000", FORECAST)
        return ["weather message"]

    try:
        auto_posts_runtime.get_connection = lambda: connection
        auto_posts_runtime.AutoPostRepository = FakeRepo
        auto_posts_runtime.FeatureFlagRepository = FakeFlagRepo
        auto_posts_runtime.get_post_channel = fake_channel
        auto_posts_runtime.build_weather_messages = fake_build
        count = await auto_posts_runtime.run_db_auto_posts_once(None, datetime(2026, 7, 24, 7, 0, tzinfo=JST))
    finally:
        auto_posts_runtime.get_connection = original_connection
        auto_posts_runtime.AutoPostRepository = original_repo
        auto_posts_runtime.FeatureFlagRepository = original_flag_repo
        auto_posts_runtime.get_post_channel = original_channel
        auto_posts_runtime.build_weather_messages = original_build

    check.add("weather auto post sends generated body", count == 1 and channel.sent == ["weather message"])
    check.add("weather auto post records history after success", FakeRepo.delivered == 1 and FakeRepo.updated == 1 and connection.commits == 1)


async def check_runtime_failure(check: Check) -> None:
    connection = FakeConnection()
    channel = FakeChannel()
    FakeRepo.posts = [make_due_post()]
    FakeRepo.delivered = 0
    FakeRepo.updated = 0

    original_connection = auto_posts_runtime.get_connection
    original_repo = auto_posts_runtime.AutoPostRepository
    original_flag_repo = auto_posts_runtime.FeatureFlagRepository
    original_channel = auto_posts_runtime.get_post_channel
    original_build = auto_posts_runtime.build_weather_messages

    async def fake_channel(bot, post):
        return channel

    async def failing_build(config, forecast_cache, now=None):
        raise jma_weather.JmaWeatherError("fixture failure")

    try:
        auto_posts_runtime.get_connection = lambda: connection
        auto_posts_runtime.AutoPostRepository = FakeRepo
        auto_posts_runtime.FeatureFlagRepository = FakeFlagRepo
        auto_posts_runtime.get_post_channel = fake_channel
        auto_posts_runtime.build_weather_messages = failing_build
        count = await auto_posts_runtime.run_db_auto_posts_once(None, datetime(2026, 7, 24, 7, 0, tzinfo=JST))
    finally:
        auto_posts_runtime.get_connection = original_connection
        auto_posts_runtime.AutoPostRepository = original_repo
        auto_posts_runtime.FeatureFlagRepository = original_flag_repo
        auto_posts_runtime.get_post_channel = original_channel
        auto_posts_runtime.build_weather_messages = original_build

    check.add("weather failure does not send blank fallback", count == 0 and not channel.sent)
    check.add("weather failure does not record delivery", FakeRepo.delivered == 0 and FakeRepo.updated == 0 and connection.commits == 0)


async def main_async() -> Check:
    check = Check()
    check_area_master(check)
    check_forecast_parsing(check)
    check_subdivision_filtering(check)
    await check_admin_form(check)
    await check_runtime_success(check)
    await check_runtime_failure(check)
    return check


def main() -> None:
    check = asyncio.run(main_async())
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
