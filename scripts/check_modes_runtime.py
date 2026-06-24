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
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


class FakeAuthor:
    id = 100
    name = "Tester"
    display_name = "Tester"

    @property
    def mention(self) -> str:
        return "<@100>"


class FakeMessage:
    def __init__(self, content: str = "hello") -> None:
        self.content = content
        self.channel = FakeChannel()
        self.author = FakeAuthor()


class FakeConnection:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


class FakeModeRepository:
    state: Dict[str, Any] = {}
    entered: List[int] = []
    cleared: bool = False
    modes: Dict[int, Dict[str, Any]] = {
        1: {
            "id": 1,
            "mode_key": "reply_test",
            "name": "reply",
            "enabled": True,
            "behavior_type": "reply",
            "enter_message": "entered",
        },
        2: {
            "id": 2,
            "mode_key": "offline_test",
            "name": "offline",
            "enabled": True,
            "behavior_type": "offline",
        },
        3: {
            "id": 3,
            "mode_key": "shikocchi",
            "name": "shikocchi",
            "enabled": True,
            "behavior_type": "offline",
            "exit_message": "ended",
        },
    }

    def __init__(self, connection) -> None:
        self.connection = connection

    def get_mode_state(self, guild_id: str) -> Optional[Dict[str, Any]]:
        return self.state or None

    def list_enabled_modes(self, guild_id: str) -> List[Dict[str, Any]]:
        return [self.modes[1]]

    def list_trigger_conditions(self, guild_id: str, mode_id: int, enabled: bool = True) -> List[Dict[str, Any]]:
        if mode_id == 1:
            return [
                {
                    "id": 1,
                    "mode_id": 1,
                    "condition_type": "counter_threshold",
                    "condition_config_json": {"counter_key": "mode_count", "operator": ">=", "threshold": 1},
                    "group_operator": "AND",
                }
            ]
        return []

    def list_exit_conditions(self, guild_id: str, mode_id: int, enabled: bool = True) -> List[Dict[str, Any]]:
        if mode_id == 1:
            return [{"condition_type": "duration", "condition_config_json": {"seconds": 60}}]
        return []

    def enter_mode(
        self,
        guild_id: str,
        mode_id: int,
        active_until: Optional[datetime],
        state_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        type(self).entered.append(mode_id)
        type(self).state = {"guild_id": guild_id, "current_mode_id": mode_id, "active_until": active_until}

    def get_by_id(self, guild_id: str, mode_id: int) -> Optional[Dict[str, Any]]:
        return self.modes.get(mode_id)

    def list_reply_choices(self, guild_id: str, mode_id: int, enabled: bool = True) -> List[Dict[str, Any]]:
        return [{"id": 1, "body": "reply ok", "image_path": "", "weight": 1, "enabled": True}]

    def clear_mode_state(self, guild_id: str, state_json: Optional[Dict[str, Any]] = None) -> None:
        type(self).cleared = True
        type(self).state = {}

    def get_trigger_history(self, guild_id: str, mode_id: int, period_key: str) -> Optional[Dict[str, Any]]:
        return None

    def record_trigger_history(self, guild_id: str, mode_id: int, period_key: str, state_json: Dict[str, Any]) -> None:
        pass


class FakeCounterRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def get_value(self, guild_id: str, counter_key: str, default: int = 0) -> int:
        return 1

    def reset(self, guild_id: str, counter_key: str) -> None:
        pass


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
    try:
        runtime_db.ModeRepository = FakeModeRepository
        runtime_db.CounterRepository = FakeCounterRepository
        FakeModeRepository.state = {}
        FakeModeRepository.entered = []
        FakeModeRepository.cleared = False
        connection = FakeConnection()

        enter_message = FakeMessage("enter")
        entered = await runtime_db.enter_mode_if_needed(enter_message, "guild", connection)
        check.add(
            "counter threshold enters mode",
            entered is True and FakeModeRepository.entered == [1] and enter_message.channel.sent == ["entered"],
            "entered={0} sent={1}".format(FakeModeRepository.entered, enter_message.channel.sent),
        )

        reply_message = FakeMessage("reply")
        handled = await runtime_db.handle_active_mode(reply_message, "guild", connection)
        check.add(
            "reply mode stops other features and replies",
            handled is True and reply_message.channel.sent == ["reply ok"],
            str(reply_message.channel.sent),
        )

        FakeModeRepository.state = {"guild_id": "guild", "current_mode_id": 2, "active_until": None}
        offline_message = FakeMessage("offline")
        handled = await runtime_db.handle_active_mode(offline_message, "guild", connection)
        check.add(
            "offline mode stops all replies",
            handled is True and offline_message.channel.sent == [],
            str(offline_message.channel.sent),
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
    finally:
        runtime_db.ModeRepository = original_mode_repository
        runtime_db.CounterRepository = original_counter_repository


def main() -> None:
    check = Check()
    asyncio.run(run_checks(check))
    check.print_results()
    if not check.ok():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
