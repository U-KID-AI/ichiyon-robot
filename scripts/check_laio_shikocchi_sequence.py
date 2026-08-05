import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import List


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot import config
from bot.services import runtime_db


class Check:
    def __init__(self) -> None:
        self.results = []

    def add(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append({"name": name, "ok": bool(ok), "detail": detail})

    def print_results(self) -> None:
        for result in self.results:
            label = "OK" if result["ok"] else "NG"
            detail = ""
            if result["detail"] != "":
                safe = str(result["detail"]).encode("ascii", "backslashreplace").decode("ascii")
                detail = " - {0}".format(safe)
            print("[{0}] {1}{2}".format(label, result["name"], detail))
        passed = sum(1 for result in self.results if result["ok"])
        print("summary: {0}/{1} OK".format(passed, len(self.results)))

    def ok(self) -> bool:
        return all(result["ok"] for result in self.results)


class FakeConnection:
    def __init__(self, log: List[str]) -> None:
        self.log = log

    def __enter__(self):
        self.log.append("connection_enter")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.log.append("connection_exit")
        return False

    def commit(self) -> None:
        self.log.append("commit")

    def rollback(self) -> None:
        self.log.append("rollback")


class FakeMessage:
    def __init__(self, content: str, guild_id: str = "guild-a", author_id: int = 1) -> None:
        self.content = content
        self.guild = SimpleNamespace(id=guild_id)
        self.author = SimpleNamespace(id=author_id, bot=False)
        self.channel = SimpleNamespace(id="channel-a")
        self.webhook_id = None
        self.type = SimpleNamespace(name="default")


class PatchRuntime:
    def __init__(self, log: List[str], release_first: asyncio.Event, first_started: asyncio.Event):
        self.log = log
        self.release_first = release_first
        self.first_started = first_started
        self.old = {}

    def __enter__(self):
        self.old = {
            "get_connection": runtime_db.get_connection,
            "expire_mode_if_needed": runtime_db.expire_mode_if_needed,
            "handle_active_mode": runtime_db.handle_active_mode,
            "find_ng_word_match": runtime_db.find_ng_word_match,
            "get_mention_command_text": runtime_db.get_mention_command_text,
            "process_db_auto_reaction": runtime_db.process_db_auto_reaction,
            "enter_mode_if_needed": runtime_db.enter_mode_if_needed,
        }
        runtime_db.get_connection = lambda: FakeConnection(self.log)
        runtime_db.expire_mode_if_needed = self._expire_mode_if_needed
        runtime_db.handle_active_mode = self._handle_active_mode
        runtime_db.find_ng_word_match = self._find_ng_word_match
        runtime_db.get_mention_command_text = lambda message: None
        runtime_db.process_db_auto_reaction = self._process_db_auto_reaction
        runtime_db.enter_mode_if_needed = self._enter_mode_if_needed
        runtime_db._RUNTIME_MESSAGE_LOCKS.clear()
        runtime_db._PENDING_NEXT_EFFECTS.clear()
        return self

    def __exit__(self, exc_type, exc, tb):
        for name, value in self.old.items():
            setattr(runtime_db, name, value)
        runtime_db._RUNTIME_MESSAGE_LOCKS.clear()
        runtime_db._PENDING_NEXT_EFFECTS.clear()
        return False

    async def _expire_mode_if_needed(self, message, guild_id, connection) -> bool:
        return False

    async def _handle_active_mode(self, message, guild_id, connection) -> bool:
        return False

    def _find_ng_word_match(self, connection, guild_id, content):
        return None

    async def _process_db_auto_reaction(self, message, guild_id, connection):
        self.log.append("{0}:start:{1}".format(guild_id, message.content))
        if message.content == "ライオ":
            self.first_started.set()
            await self.release_first.wait()
            runtime_db.store_pending_next_effects(guild_id, message, [{"effect_type": "probability_multiplier", "id": 9}])
            self.log.append("{0}:stored_laio".format(guild_id))
            return runtime_db.RuntimeAction(True, pending_effects=[{"effect_type": "probability_multiplier", "id": 9}])
        if message.content == "しこっち":
            pending = runtime_db.pop_pending_next_effects(guild_id, message)
            self.log.append("{0}:shikocchi_pending={1}".format(guild_id, len(pending)))
            return runtime_db.RuntimeAction(True)
        if message.content == "raise":
            self.log.append("{0}:raise".format(guild_id))
            raise RuntimeError("fixture")
        return runtime_db.RuntimeAction(False)

    async def _enter_mode_if_needed(self, message, guild_id, connection, pending_effects=None) -> bool:
        self.log.append("{0}:enter_mode:{1}".format(guild_id, message.content))
        return False

async def check_same_guild_sequence(check: Check) -> None:
    log: List[str] = []
    release_first = asyncio.Event()
    first_started = asyncio.Event()
    with PatchRuntime(log, release_first, first_started):
        first = asyncio.create_task(runtime_db.handle_db_runtime_message(FakeMessage("ライオ", "guild-a")))
        await asyncio.wait_for(first_started.wait(), timeout=1)
        second = asyncio.create_task(runtime_db.handle_db_runtime_message(FakeMessage("しこっち", "guild-a")))
        await asyncio.sleep(0)
        check.add("same guild second message waits for laio completion", not second.done(), log)
        release_first.set()
        first_result, second_result = await asyncio.gather(first, second)
        check.add("laio and shikocchi are both handled", first_result is True and second_result is True, log)
        check.add("first shikocchi sees pending laio effect", "guild-a:shikocchi_pending=1" in log, log)
        check.add(
            "laio stores effect before shikocchi starts",
            log.index("guild-a:stored_laio") < log.index("guild-a:start:しこっち"),
            log,
        )
        check.add(
            "same guild processing commits in order",
            log.index("commit") < log.index("guild-a:start:しこっち"),
            log,
        )


async def check_different_guilds_do_not_block(check: Check) -> None:
    log: List[str] = []
    release_first = asyncio.Event()
    first_started = asyncio.Event()
    with PatchRuntime(log, release_first, first_started):
        first = asyncio.create_task(runtime_db.handle_db_runtime_message(FakeMessage("ライオ", "guild-a")))
        await asyncio.wait_for(first_started.wait(), timeout=1)
        other = asyncio.create_task(runtime_db.handle_db_runtime_message(FakeMessage("しこっち", "guild-b")))
        await asyncio.wait_for(other, timeout=1)
        check.add("different guild can proceed while laio is waiting", "guild-b:start:しこっち" in log and not first.done(), log)
        release_first.set()
        await first


async def check_exception_releases_lock(check: Check) -> None:
    log: List[str] = []
    release_first = asyncio.Event()
    first_started = asyncio.Event()
    with PatchRuntime(log, release_first, first_started):
        failed = await runtime_db.handle_db_runtime_message(FakeMessage("raise", "guild-a"))
        ok = await asyncio.wait_for(runtime_db.handle_db_runtime_message(FakeMessage("しこっち", "guild-a")), timeout=1)
        check.add("exception path returns false", failed is False, log)
        check.add("same guild lock is released after exception", ok is True and "guild-a:start:しこっち" in log, log)


def check_bot_scoped_key(check: Check) -> None:
    original = config.BOT_INSTANCE_ID
    try:
        config.BOT_INSTANCE_ID = "ichiyon"
        ichiyon_key = runtime_db.runtime_message_lock_key("guild-a")
        config.BOT_INSTANCE_ID = "irsia"
        irsia_key = runtime_db.runtime_message_lock_key("guild-a")
        check.add("lock key is bot and guild scoped", ichiyon_key != irsia_key and ichiyon_key.endswith(":guild-a"), (ichiyon_key, irsia_key))
    finally:
        config.BOT_INSTANCE_ID = original


async def main() -> int:
    check = Check()
    await check_same_guild_sequence(check)
    await check_different_guilds_do_not_block(check)
    await check_exception_releases_lock(check)
    check_bot_scoped_key(check)
    check.print_results()
    return 0 if check.ok() else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
