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
        self.guild = FakeGuild()

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


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
            "name": "しこっちモード",
            "enabled": True,
            "behavior_type": "offline",
            "enter_message": "しこっちきた",
            "exit_message": "ended",
            "mode_icon_path": "assets/avatar_shikocchi.png",
            "appearance_config_json": {"nickname": "しこっち"},
        },
    }

    def __init__(self, connection) -> None:
        self.connection = connection

    def get_mode_state(self, guild_id: str) -> Optional[Dict[str, Any]]:
        return self.state or None

    def list_enabled_modes(self, guild_id: str) -> List[Dict[str, Any]]:
        return [self.modes[self.enabled_mode_id]]

    def list_trigger_conditions(self, guild_id: str, mode_id: int, enabled: bool = True) -> List[Dict[str, Any]]:
        if mode_id in (1, 3):
            return [
                {
                    "id": mode_id,
                    "mode_id": mode_id,
                    "condition_type": "counter_threshold",
                    "condition_config_json": {
                        "counter_key": "shikocchi_count" if mode_id == 3 else "mode_count",
                        "operator": ">=",
                        "threshold": 1,
                    },
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
        FakeModeRepository.enabled_mode_id = 1
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
        check.add(
            "reply mode applies mode nickname",
            FakeIdentity.nickname_updates == ["reply"],
            str(FakeIdentity.nickname_updates),
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

        FakeModeRepository.state = {}
        FakeModeRepository.entered = []
        FakeModeRepository.enabled_mode_id = 3
        FakeIdentity.nickname_updates = []
        FakeIdentity.avatar_updates = []
        FakeIdentity.status_updates = []
        shikocchi_enter_message = FakeMessage("enter shikocchi")
        shikocchi_entered = await runtime_db.enter_mode_if_needed(shikocchi_enter_message, "guild", connection)
        check.add(
            "shikocchi mode enters with single message",
            shikocchi_entered is True and shikocchi_enter_message.channel.sent == ["しこっちきた"],
            str(shikocchi_enter_message.channel.sent),
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
