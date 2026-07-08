import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import bot.services.runtime_db as runtime_db
from bot.services.runtime_db import SHIKOCCHI_RECOVERY_MESSAGE


class FakeChannel:
    def __init__(self) -> None:
        self.id = 12345
        self.sent = []
        self.send_kwargs = []
        self.guild = FakeGuild()

    async def send(self, content=None, **kwargs):
        self.sent.append(content)
        self.send_kwargs.append(kwargs)


class FakeBotUser:
    id = 999

    async def edit(self, avatar=None) -> None:
        FakeIdentity.avatar_updates.append(avatar)


class FakeBot:
    def __init__(self) -> None:
        self.user = FakeBotUser()
        self.statuses = []
        self.channels: Dict[int, FakeChannel] = {}
        self.guilds: Dict[int, Any] = {}

    async def change_presence(self, status=None) -> None:
        self.statuses.append(str(status))
        FakeIdentity.status_updates.append(str(status))

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    def get_guild(self, guild_id: int):
        return self.guilds.get(guild_id)


class FakeMember:
    async def edit(self, nick=None) -> None:
        FakeIdentity.nickname_updates.append(nick)


class FakeGuild:
    def __init__(self) -> None:
        self.me = FakeMember()

    def get_member(self, user_id: int):
        return self.me


class FakeIdentity:
    nickname_updates: List[str] = []
    avatar_updates: List[Any] = []
    status_updates: List[str] = []


class FakeAuthor:
    def __init__(self, user_id: int = 100, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot
        self.name = "Tester"
        self.display_name = "Tester"

    @property
    def mention(self) -> str:
        return "<@{0}>".format(self.id)


class FakeMessage:
    def __init__(self, content: str = "hello", author: Optional[FakeAuthor] = None) -> None:
        self.content = content
        self.channel = FakeChannel()
        self.author = author or FakeAuthor()


class FakeConnection:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


class FakeConnectionContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> FakeConnection:
        return self.connection

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class FakeModeRepository:
    state: Dict[str, Any] = {}
    entered: List[int] = []
    cleared: bool = False
    enabled_mode_id: int = 1
    trigger_history: Dict[str, Dict[str, Any]] = {}
    modes: Dict[int, Dict[str, Any]] = {
        1: {
            "id": 1,
            "mode_key": "reply_test",
            "name": "reply",
            "enabled": True,
            "behavior_type": "reply",
            "enter_message": "entered",
            "duration_seconds": 60,
        },
        2: {
            "id": 2,
            "mode_key": "offline_test",
            "name": "offline",
            "enabled": True,
            "behavior_type": "offline",
            "duration_seconds": None,
        },
        3: {
            "id": 3,
            "mode_key": "shikocchi",
            "name": "しこっちモード",
            "enabled": True,
            "behavior_type": "offline",
            "enter_message": "しこっち、きた",
            "exit_message": "ended",
            "mode_icon_path": "assets/avatar_shikocchi.png",
            "appearance_config_json": {"nickname": "しこっち"},
            "duration_seconds": 60,
        },
        4: {
            "id": 4,
            "mode_key": "fallback_duration",
            "name": "fallback",
            "enabled": True,
            "behavior_type": "reply",
            "enter_message": "fallback entered",
            "duration_seconds": None,
        },
        5: {
            "id": 5,
            "mode_key": "nickname_override",
            "name": "management name",
            "enabled": True,
            "behavior_type": "reply",
            "enter_message": "nickname entered",
            "appearance_config_json": {"nickname": "mode bot name"},
            "duration_seconds": 60,
        },
        6: {
            "id": 6,
            "mode_key": "shikocchi_monthly",
            "name": "shikocchi monthly",
            "enabled": True,
            "behavior_type": "offline",
            "enter_message": "monthly entered",
            "duration_seconds": 60,
            "cooldown_config_json": {"type": "once_per_period", "period": "monthly", "reset": "day", "day": 4},
        },
        7: {
            "id": 7,
            "mode_key": "narita",
            "name": "narita",
            "enabled": True,
            "behavior_type": "reply",
            "enter_message": "narita entered",
            "duration_seconds": 60,
            "cooldown_config_json": {"type": "once_per_period", "period": "monthly", "reset": {"day": 22}},
        },
        8: {
            "id": 8,
            "mode_key": "ichiyon_almost",
            "name": "ichiyon almost",
            "enabled": True,
            "behavior_type": "reply",
            "duration_seconds": 60,
            "appearance_config_json": {
                "reply_type": "echo_user_message",
                "target_user_ids": ["1414"],
            },
        },
    }

    def __init__(self, connection) -> None:
        self.connection = connection

    def get_mode_state(self, guild_id: str) -> Optional[Dict[str, Any]]:
        return self.state or None

    def list_enabled_modes(self, guild_id: str) -> List[Dict[str, Any]]:
        return [self.modes[self.enabled_mode_id]]

    def list_trigger_conditions(self, guild_id: str, mode_id: int, enabled: bool = True) -> List[Dict[str, Any]]:
        if mode_id in (1, 3, 4, 5, 6, 7):
            return [
                {
                    "id": mode_id,
                    "mode_id": mode_id,
                    "condition_type": "counter_threshold",
                    "condition_config_json": {
                        "counter_key": (
                            "shikocchi_count"
                            if mode_id == 3
                            else "narita_count"
                            if mode_id == 7
                            else "mode_count"
                        ),
                        "operator": ">=",
                        "threshold": 22 if mode_id == 7 else 1,
                    },
                    "group_operator": "AND",
                }
            ]
        return []

    def list_exit_conditions(self, guild_id: str, mode_id: int, enabled: bool = True) -> List[Dict[str, Any]]:
        if mode_id == 1:
            return [{"condition_type": "duration", "condition_config_json": {"seconds": 840}}]
        if mode_id == 4:
            return [{"condition_type": "duration", "condition_config_json": {"seconds": 75}}]
        return []

    def enter_mode(
        self,
        guild_id: str,
        mode_id: int,
        active_until: Optional[datetime],
        state_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        type(self).entered.append(mode_id)
        type(self).state = {
            "guild_id": guild_id,
            "current_mode_id": mode_id,
            "active_until": active_until,
            "state_json": state_json or {},
        }

    def get_by_id(self, guild_id: str, mode_id: int) -> Optional[Dict[str, Any]]:
        return self.modes.get(mode_id)

    def list_reply_choices(self, guild_id: str, mode_id: int, enabled: bool = True) -> List[Dict[str, Any]]:
        return [{"id": 1, "body": "reply ok", "image_path": "", "weight": 1, "enabled": True}]

    def clear_mode_state(self, guild_id: str, state_json: Optional[Dict[str, Any]] = None) -> None:
        type(self).cleared = True
        type(self).state = {}

    def list_expired_mode_states(self) -> List[Dict[str, Any]]:
        state = self.state or {}
        active_until = state.get("active_until")
        if state.get("current_mode_id") and active_until is not None and active_until <= datetime.now(timezone.utc):
            return [state]
        return []

    def get_trigger_history(self, guild_id: str, mode_id: int, period_key: str) -> Optional[Dict[str, Any]]:
        return self.trigger_history.get("{0}:{1}:{2}".format(guild_id, mode_id, period_key))

    def record_trigger_history(self, guild_id: str, mode_id: int, period_key: str, state_json: Dict[str, Any]) -> None:
        self.trigger_history["{0}:{1}:{2}".format(guild_id, mode_id, period_key)] = state_json


class FakeCounterRepository:
    states: Dict[str, Dict[str, Any]] = {}
    values: Dict[str, int] = {}

    def __init__(self, connection) -> None:
        self.connection = connection

    @classmethod
    def key(cls, guild_id: str, counter_key: str) -> str:
        return "{0}:{1}".format(guild_id, counter_key)

    def get_state(self, guild_id: str, counter_key: str) -> Optional[Dict[str, Any]]:
        return self.states.get(self.key(guild_id, counter_key))

    def get_value(self, guild_id: str, counter_key: str, default: int = 0) -> int:
        key = self.key(guild_id, counter_key)
        if key in self.values:
            return self.values[key]
        state = self.states.get(key)
        if state is not None:
            return int(state.get("current_value") or 0)
        return 1

    def set_value(
        self,
        guild_id: str,
        counter_key: str,
        value: int,
        period_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = self.key(guild_id, counter_key)
        self.values[key] = value
        self.states[key] = {
            "guild_id": guild_id,
            "count_key": counter_key,
            "current_value": value,
            "period_key": period_key,
        }
        return self.states[key]

    def reset(self, guild_id: str, counter_key: str) -> None:
        self.set_value(guild_id, counter_key, 0)


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


async def run_checks(check: Check) -> None:
    original_mode_repository = runtime_db.ModeRepository
    original_counter_repository = runtime_db.CounterRepository
    original_update_bot_nickname = runtime_db.update_bot_nickname
    original_update_bot_avatar = runtime_db.update_bot_avatar
    original_get_bot = runtime_db.get_bot
    original_get_connection = runtime_db.get_connection
    try:
        runtime_db.ModeRepository = FakeModeRepository
        runtime_db.CounterRepository = FakeCounterRepository
        fake_bot = FakeBot()
        runtime_db.get_bot = lambda: fake_bot

        async def fake_update_bot_nickname(channel, nickname: str) -> None:
            FakeIdentity.nickname_updates.append(nickname)

        async def fake_update_bot_avatar(path: str) -> None:
            FakeIdentity.avatar_updates.append(path)

        runtime_db.update_bot_nickname = fake_update_bot_nickname
        runtime_db.update_bot_avatar = fake_update_bot_avatar
        FakeModeRepository.state = {}
        FakeModeRepository.entered = []
        FakeModeRepository.cleared = False
        FakeModeRepository.trigger_history = {}
        FakeModeRepository.enabled_mode_id = 1
        FakeCounterRepository.states = {}
        FakeCounterRepository.values = {}
        FakeIdentity.nickname_updates = []
        FakeIdentity.avatar_updates = []
        FakeIdentity.status_updates = []
        connection = FakeConnection()
        runtime_db.get_connection = lambda: FakeConnectionContext(connection)

        enter_message = FakeMessage("enter")
        entered = await runtime_db.enter_mode_if_needed(enter_message, "guild", connection)
        check.add(
            "counter threshold enters mode",
            entered is True and FakeModeRepository.entered == [1] and enter_message.channel.sent == ["entered"],
            "entered={0} sent={1}".format(FakeModeRepository.entered, enter_message.channel.sent),
        )
        active_until = FakeModeRepository.state.get("active_until")
        delta_seconds = (active_until - datetime.now(timezone.utc)).total_seconds() if active_until else 0
        check.add(
            "mode duration_seconds overrides exit condition duration",
            45 <= delta_seconds <= 75,
            "delta_seconds={0}".format(delta_seconds),
        )
        check.add(
            "reply mode applies mode nickname",
            FakeIdentity.nickname_updates == ["reply"],
            str(FakeIdentity.nickname_updates),
        )

        FakeModeRepository.state = {}
        FakeModeRepository.entered = []
        FakeModeRepository.enabled_mode_id = 5
        FakeCounterRepository.states = {}
        FakeCounterRepository.values = {}
        FakeIdentity.nickname_updates = []
        nickname_message = FakeMessage("enter nickname")
        nickname_entered = await runtime_db.enter_mode_if_needed(nickname_message, "guild", connection)
        check.add(
            "mode appearance nickname overrides management name",
            nickname_entered is True and FakeIdentity.nickname_updates == ["mode bot name"],
            "entered={0} nick={1}".format(nickname_entered, FakeIdentity.nickname_updates),
        )
        check.add(
            "mode nickname is separate from enter message",
            nickname_message.channel.sent == ["nickname entered"],
            str(nickname_message.channel.sent),
        )

        reply_message = FakeMessage("reply")
        handled = await runtime_db.handle_active_mode(reply_message, "guild", connection)
        check.add(
            "reply mode stops other features and replies",
            handled is True and reply_message.channel.sent == ["reply ok"],
            str(reply_message.channel.sent),
        )

        FakeModeRepository.state = {"guild_id": "guild", "current_mode_id": 8, "active_until": None}
        echo_message = FakeMessage("@everyone @here hello", FakeAuthor(1414))
        handled = await runtime_db.handle_active_mode(echo_message, "guild", connection)
        check.add(
            "echo user mode repeats target user text safely",
            handled is True
            and echo_message.channel.sent == ["@\u200beveryone @\u200bhere hello"]
            and echo_message.channel.send_kwargs
            and echo_message.channel.send_kwargs[0].get("allowed_mentions") is not None,
            "sent={0} kwargs={1}".format(echo_message.channel.sent, echo_message.channel.send_kwargs),
        )

        non_target_message = FakeMessage("do not echo", FakeAuthor(9999))
        handled = await runtime_db.handle_active_mode(non_target_message, "guild", connection)
        check.add(
            "echo user mode ignores non-target user",
            handled is True and non_target_message.channel.sent == [],
            str(non_target_message.channel.sent),
        )

        bot_message = FakeMessage("do not echo bot", FakeAuthor(1414, bot=True))
        handled = await runtime_db.handle_active_mode(bot_message, "guild", connection)
        check.add(
            "echo user mode ignores bot messages",
            handled is True and bot_message.channel.sent == [],
            str(bot_message.channel.sent),
        )

        FakeModeRepository.state = {"guild_id": "guild", "current_mode_id": 2, "active_until": None}
        offline_message = FakeMessage("offline")
        handled = await runtime_db.handle_active_mode(offline_message, "guild", connection)
        check.add(
            "offline mode stops all replies",
            handled is True and offline_message.channel.sent == [],
            str(offline_message.channel.sent),
        )

        FakeModeRepository.state = {}
        FakeModeRepository.entered = []
        FakeModeRepository.enabled_mode_id = 3
        FakeCounterRepository.states = {}
        FakeCounterRepository.values = {}
        FakeIdentity.nickname_updates = []
        FakeIdentity.avatar_updates = []
        FakeIdentity.status_updates = []
        shikocchi_enter_message = FakeMessage("enter shikocchi")
        shikocchi_entered = await runtime_db.enter_mode_if_needed(shikocchi_enter_message, "guild", connection)
        check.add(
            "shikocchi mode enters with single message",
            shikocchi_entered is True and shikocchi_enter_message.channel.sent == ["しこっち、きた"],
            str(shikocchi_enter_message.channel.sent),
        )
        shikocchi_active_until = FakeModeRepository.state.get("active_until")
        shikocchi_delta = (
            (shikocchi_active_until - datetime.now(timezone.utc)).total_seconds()
            if shikocchi_active_until
            else 0
        )
        check.add(
            "shikocchi duration_seconds controls active_until",
            45 <= shikocchi_delta <= 75,
            "delta_seconds={0}".format(shikocchi_delta),
        )
        check.add(
            "shikocchi mode does not send legacy text",
            "しこっちきたぁぁぁ" not in shikocchi_enter_message.channel.sent,
            str(shikocchi_enter_message.channel.sent),
        )
        check.add(
            "shikocchi mode applies nickname avatar and offline status",
            FakeIdentity.nickname_updates == ["しこっち"]
            and FakeIdentity.avatar_updates == ["assets/avatar_shikocchi.png"]
            and "invisible" in "".join(FakeIdentity.status_updates),
            "nick={0} avatar={1} status={2}".format(
                FakeIdentity.nickname_updates,
                FakeIdentity.avatar_updates,
                FakeIdentity.status_updates,
            ),
        )

        FakeModeRepository.state = {}
        FakeModeRepository.entered = []
        FakeModeRepository.enabled_mode_id = 4
        FakeCounterRepository.states = {}
        FakeCounterRepository.values = {}
        fallback_message = FakeMessage("enter fallback")
        fallback_entered = await runtime_db.enter_mode_if_needed(fallback_message, "guild", connection)
        fallback_active_until = FakeModeRepository.state.get("active_until")
        fallback_delta = (
            (fallback_active_until - datetime.now(timezone.utc)).total_seconds()
            if fallback_active_until
            else 0
        )
        check.add(
            "exit condition duration is fallback when duration_seconds is empty",
            fallback_entered is True and 60 <= fallback_delta <= 90,
            "entered={0} delta_seconds={1}".format(fallback_entered, fallback_delta),
        )

        period_before_reset = runtime_db.get_mode_once_per_period_info(
            FakeModeRepository.modes[6],
            datetime(2026, 6, 3, 14, 59, tzinfo=timezone.utc),
        )
        period_after_reset = runtime_db.get_mode_once_per_period_info(
            FakeModeRepository.modes[6],
            datetime(2026, 6, 3, 15, 0, tzinfo=timezone.utc),
        )
        check.add(
            "monthly day cooldown treats days before reset as previous period",
            period_before_reset is not None
            and period_before_reset["period_key"] == "cooldown:monthly-day-4:2026-05-04",
            str(period_before_reset),
        )
        check.add(
            "monthly day cooldown switches period on reset day",
            period_after_reset is not None
            and period_after_reset["period_key"] == "cooldown:monthly-day-4:2026-06-04",
            str(period_after_reset),
        )

        FakeModeRepository.state = {}
        FakeModeRepository.entered = []
        FakeModeRepository.enabled_mode_id = 6
        FakeCounterRepository.states = {}
        FakeCounterRepository.values = {}
        monthly_conditions = FakeModeRepository(None).list_trigger_conditions("guild", 6, enabled=True)
        monthly_counter_period = runtime_db.get_mode_counter_period_info(
            FakeModeRepository.modes[6],
            monthly_conditions,
        )
        monthly_counter_period_key = str(monthly_counter_period["period_key"]) if monthly_counter_period else ""
        FakeCounterRepository.states = {
            "guild:mode_count": {
                "guild_id": "guild",
                "count_key": "mode_count",
                "current_value": 1,
                "period_key": monthly_counter_period_key,
            }
        }
        FakeCounterRepository.values = {"guild:mode_count": 1}
        monthly_message = FakeMessage("enter monthly")
        monthly_entered = await runtime_db.enter_mode_if_needed(monthly_message, "guild", connection)
        check.add(
            "mode once_per_period cooldown allows first entry",
            monthly_entered is True and FakeModeRepository.entered == [6],
            "entered={0} history={1}".format(FakeModeRepository.entered, FakeModeRepository.trigger_history),
        )
        FakeModeRepository.state = {}
        second_monthly_message = FakeMessage("enter monthly again")
        second_monthly_entered = await runtime_db.enter_mode_if_needed(second_monthly_message, "guild", connection)
        check.add(
            "mode once_per_period cooldown blocks second entry in same period",
            second_monthly_entered is False and FakeModeRepository.entered == [6],
            "entered={0} sent={1}".format(FakeModeRepository.entered, second_monthly_message.channel.sent),
        )

        narita_conditions = FakeModeRepository(None).list_trigger_conditions("guild", 7, enabled=True)
        narita_period_before_reset = runtime_db.get_mode_counter_period_info(
            FakeModeRepository.modes[7],
            narita_conditions,
            datetime(2026, 6, 21, 14, 59, tzinfo=timezone.utc),
        )
        narita_period_after_reset = runtime_db.get_mode_counter_period_info(
            FakeModeRepository.modes[7],
            narita_conditions,
            datetime(2026, 6, 21, 15, 0, tzinfo=timezone.utc),
        )
        check.add(
            "narita counter period uses day 22 reset before boundary",
            narita_period_before_reset is not None
            and narita_period_before_reset["period_key"] == "counter:monthly-day-22:2026-05-22",
            str(narita_period_before_reset),
        )
        check.add(
            "narita counter period switches on day 22",
            narita_period_after_reset is not None
            and narita_period_after_reset["period_key"] == "counter:monthly-day-22:2026-06-22",
            str(narita_period_after_reset),
        )
        current_narita_period = runtime_db.get_mode_counter_period_info(
            FakeModeRepository.modes[7],
            narita_conditions,
        )
        current_narita_period_key = str(current_narita_period["period_key"]) if current_narita_period else ""
        stale_narita_period_key = "counter:stale-period"
        FakeCounterRepository.states = {
            "guild:narita_count": {
                "guild_id": "guild",
                "count_key": "narita_count",
                "current_value": 25,
                "period_key": stale_narita_period_key,
            }
        }
        FakeCounterRepository.values = {"guild:narita_count": 25}
        runtime_db.reset_counter_thresholds_on_period_change(
            connection,
            "guild",
            FakeModeRepository.modes[7],
            narita_conditions,
            datetime(2026, 6, 21, 15, 0, tzinfo=timezone.utc),
        )
        narita_state = FakeCounterRepository.states.get("guild:narita_count") or {}
        check.add(
            "narita count resets when monthly period changes",
            narita_state.get("current_value") == 0
            and narita_state.get("period_key") == "counter:monthly-day-22:2026-06-22",
            str(narita_state),
        )
        before_same_period = dict(narita_state)
        FakeCounterRepository.values["guild:narita_count"] = 13
        FakeCounterRepository.states["guild:narita_count"]["current_value"] = 13
        runtime_db.reset_counter_thresholds_on_period_change(
            connection,
            "guild",
            FakeModeRepository.modes[7],
            narita_conditions,
            datetime(2026, 6, 22, 1, 0, tzinfo=timezone.utc),
        )
        same_period_state = FakeCounterRepository.states.get("guild:narita_count") or {}
        check.add(
            "narita count is not reset twice in same period",
            same_period_state.get("current_value") == 13
            and same_period_state.get("period_key") == before_same_period.get("period_key"),
            str(same_period_state),
        )
        FakeCounterRepository.states = {
            "guild:narita_count": {
                "guild_id": "guild",
                "count_key": "narita_count",
                "current_value": 25,
                "period_key": stale_narita_period_key,
            }
        }
        FakeCounterRepository.values = {"guild:narita_count": 25}
        narita_trigger_after_period_change = runtime_db.mode_triggers_met(
            connection,
            "guild",
            FakeModeRepository.modes[7],
        )
        narita_reset_state = FakeCounterRepository.states.get("guild:narita_count") or {}
        check.add(
            "narita stale count does not trigger after period reset",
            narita_trigger_after_period_change is False
            and narita_reset_state.get("current_value") == 0
            and narita_reset_state.get("period_key") == current_narita_period_key,
            "trigger={0} state={1}".format(narita_trigger_after_period_change, narita_reset_state),
        )
        FakeCounterRepository.states = {
            "guild:narita_count": {
                "guild_id": "guild",
                "count_key": "narita_count",
                "current_value": 25,
                "period_key": current_narita_period_key,
            }
        }
        FakeCounterRepository.values = {"guild:narita_count": 25}
        narita_trigger_current_period = runtime_db.mode_triggers_met(
            connection,
            "guild",
            FakeModeRepository.modes[7],
        )
        check.add(
            "narita current period count can trigger",
            narita_trigger_current_period is True,
            "trigger={0}".format(narita_trigger_current_period),
        )

        FakeModeRepository.state = {
            "guild_id": "guild",
            "current_mode_id": 3,
            "active_until": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        expire_message = FakeMessage("expire")
        expired = await runtime_db.expire_mode_if_needed(expire_message, "guild", connection)
        check.add(
            "duration expiry clears shikocchi mode",
            expired is True
            and FakeModeRepository.cleared is True
            and expire_message.channel.sent == ["ended", SHIKOCCHI_RECOVERY_MESSAGE],
            str(expire_message.channel.sent),
        )
        check.add(
            "mode expiry restores normal identity",
            runtime_db.config.NORMAL_BOT_NICKNAME in FakeIdentity.nickname_updates
            and runtime_db.config.NORMAL_AVATAR in FakeIdentity.avatar_updates
            and "online" in "".join(FakeIdentity.status_updates),
            "nick={0} avatar={1} status={2}".format(
                FakeIdentity.nickname_updates,
                FakeIdentity.avatar_updates,
                FakeIdentity.status_updates,
            ),
        )

        FakeModeRepository.state = {
            "guild_id": "guild",
            "current_mode_id": 3,
            "active_until": datetime.now(timezone.utc) + timedelta(seconds=60),
            "state_json": {"channel_id": "12345"},
        }
        FakeModeRepository.cleared = False
        not_expired_channel = FakeChannel()
        fake_bot.channels[12345] = not_expired_channel
        not_expired_count = await runtime_db.expire_db_modes_once(fake_bot)
        check.add(
            "periodic expiry keeps active mode before deadline",
            not_expired_count == 0 and FakeModeRepository.cleared is False and not_expired_channel.sent == [],
            "count={0} sent={1}".format(not_expired_count, not_expired_channel.sent),
        )

        FakeModeRepository.state = {
            "guild_id": "guild",
            "current_mode_id": 3,
            "active_until": datetime.now(timezone.utc) - timedelta(seconds=1),
            "state_json": {"channel_id": "12345"},
        }
        FakeModeRepository.cleared = False
        FakeIdentity.nickname_updates = []
        FakeIdentity.avatar_updates = []
        FakeIdentity.status_updates = []
        expired_channel = FakeChannel()
        fake_bot.channels[12345] = expired_channel
        expired_count = await runtime_db.expire_db_modes_once(fake_bot)
        check.add(
            "periodic expiry clears mode without mention",
            expired_count == 1
            and FakeModeRepository.cleared is True
            and expired_channel.sent == ["ended", SHIKOCCHI_RECOVERY_MESSAGE],
            "count={0} sent={1}".format(expired_count, expired_channel.sent),
        )
        check.add(
            "periodic expiry restores normal identity",
            runtime_db.config.NORMAL_BOT_NICKNAME in FakeIdentity.nickname_updates
            and runtime_db.config.NORMAL_AVATAR in FakeIdentity.avatar_updates
            and "online" in "".join(FakeIdentity.status_updates),
            "nick={0} avatar={1} status={2}".format(
                FakeIdentity.nickname_updates,
                FakeIdentity.avatar_updates,
                FakeIdentity.status_updates,
            ),
        )

        second_count = await runtime_db.expire_db_modes_once(fake_bot)
        check.add(
            "periodic expiry does not run twice",
            second_count == 0 and expired_channel.sent == ["ended", SHIKOCCHI_RECOVERY_MESSAGE],
            "count={0} sent={1}".format(second_count, expired_channel.sent),
        )
    finally:
        runtime_db.ModeRepository = original_mode_repository
        runtime_db.CounterRepository = original_counter_repository
        runtime_db.update_bot_nickname = original_update_bot_nickname
        runtime_db.update_bot_avatar = original_update_bot_avatar
        runtime_db.get_bot = original_get_bot
        runtime_db.get_connection = original_get_connection


def main() -> None:
    check = Check()
    asyncio.run(run_checks(check))
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
