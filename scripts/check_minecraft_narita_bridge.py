import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot import messages
from bot.repositories.minecraft_bridge import is_valid_minecraft_player_name
from bot.services import minecraft_bridge
from admin import minecraft_internal


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakeUser:
    def __init__(self, user_id, *, bot=False):
        self.id = int(user_id)
        self.bot = bot
        self.mention = "<@{0}>".format(self.id)

    def __eq__(self, other):
        return int(getattr(other, "id", -1)) == self.id


class FakeChannel:
    def __init__(self):
        self.id = 444
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class FakeMessage:
    def __init__(self, command_text, mentions, *, author_bot=False, guild=True):
        self.id = 555
        self.content = "<@999999999999999999> {0}".format(command_text).strip()
        self.mentions = mentions
        self.author = SimpleNamespace(id=111, bot=author_bot)
        self.channel = FakeChannel()
        self.guild = SimpleNamespace(id="guild-a") if guild else None


class DummyConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        pass


class FakeBridgeRepo:
    enqueued = []
    expired = 0
    result = {"status": "succeeded", "result_reason": "ok"}
    claimed = None
    marked = None

    def __init__(self, connection, bot_id="ichiyon"):
        self.bot_id = bot_id

    def enqueue_narita_carpet(self, **kwargs):
        row = dict(kwargs)
        row["request_id"] = "request-1"
        row["command_type"] = "narita_carpet"
        FakeBridgeRepo.enqueued.append(row)
        return row

    def fail_expired(self):
        FakeBridgeRepo.expired += 1
        return 0

    def get_command_by_request_id(self, request_id):
        return FakeBridgeRepo.result

    def claim_next_pending(self, *, bot_id, guild_id):
        FakeBridgeRepo.claimed = {"bot_id": bot_id, "guild_id": guild_id}
        return {
            "request_id": "request-1",
            "command_type": "narita_carpet",
            "minecraft_player_name": "Player45165996",
        }

    def mark_result(self, *, request_id, status, reason, message=""):
        FakeBridgeRepo.marked = {
            "request_id": request_id,
            "status": status,
            "reason": reason,
            "message": message,
        }
        return {"request_id": request_id, "status": status}


async def exercise_service():
    results = []
    bot_user = FakeUser(999999999999999999, bot=True)
    target_user = FakeUser(222222222222222222)
    messages._bot = SimpleNamespace(user=bot_user)

    original_get_connection = minecraft_bridge.get_connection
    original_repo = minecraft_bridge.MinecraftBridgeRepository
    original_wait = minecraft_bridge.wait_for_minecraft_result
    original_timeout = minecraft_bridge.config.MINECRAFT_COMMAND_TIMEOUT_SECONDS
    minecraft_bridge.get_connection = lambda: DummyConnection()
    minecraft_bridge.MinecraftBridgeRepository = FakeBridgeRepo
    minecraft_bridge.config.MINECRAFT_COMMAND_TIMEOUT_SECONDS = 3

    async def fake_wait(request_id, timeout_seconds):
        return {"status": "succeeded", "result_reason": "ok"}

    minecraft_bridge.wait_for_minecraft_result = fake_wait
    try:
        FakeBridgeRepo.enqueued = []
        message = FakeMessage("マイクラ 成田カーペット Player45165996", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(message, "マイクラ 成田カーペット Player45165996")
        results.append(check("valid narita command is handled", handled is True))
        results.append(check("minecraft player name is parsed directly", FakeBridgeRepo.enqueued[-1]["minecraft_player_name"] == "Player45165996"))
        results.append(check("queue does not require Discord target mapping", FakeBridgeRepo.enqueued[-1]["target_discord_user_id"] == ""))
        results.append(check("success waits for Minecraft result", message.channel.sent[-1][0][0] == "Player45165996 に成田カーペットを送り付けました。"))

        invalid = FakeMessage("マイクラ 成田カーペット @a", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(invalid, "マイクラ 成田カーペット @a")
        results.append(check("invalid Minecraft player name returns usage", handled is True and invalid.channel.sent[-1][0][0] == minecraft_bridge.NARITA_CARPET_USAGE))

        invalid_space = FakeMessage("マイクラ 成田カーペット Player 45165996", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(invalid_space, "マイクラ 成田カーペット Player 45165996")
        results.append(check("spaced Minecraft player name returns usage", handled is True and invalid_space.channel.sent[-1][0][0] == minecraft_bridge.NARITA_CARPET_USAGE))

        missing_arg = FakeMessage("マイクラ 成田カーペット", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(missing_arg, "マイクラ 成田カーペット")
        results.append(check("missing Minecraft player name returns usage", handled is True and missing_arg.channel.sent[-1][0][0] == minecraft_bridge.NARITA_CARPET_USAGE))

        no_bot = FakeMessage("マイクラ 成田カーペット Player45165996", [target_user])
        handled = await minecraft_bridge.handle_minecraft_command(no_bot, None)
        results.append(check("bot mention is required", handled is False and no_bot.channel.sent == []))

        typo = FakeMessage("マイクラ 成田 Player45165996", [bot_user, target_user])
        handled = await minecraft_bridge.handle_minecraft_command(typo, "マイクラ 成田 Player45165996")
        results.append(check("syntax mismatch is ignored", handled is False and typo.channel.sent == []))

        bot_message = FakeMessage("マイクラ 成田カーペット Player45165996", [bot_user, target_user], author_bot=True)
        handled = await minecraft_bridge.handle_minecraft_command(bot_message, "マイクラ 成田カーペット Player45165996")
        results.append(check("bot author is ignored", handled is False))
    finally:
        minecraft_bridge.get_connection = original_get_connection
        minecraft_bridge.MinecraftBridgeRepository = original_repo
        minecraft_bridge.wait_for_minecraft_result = original_wait
        minecraft_bridge.config.MINECRAFT_COMMAND_TIMEOUT_SECONDS = original_timeout
    return results


async def exercise_internal_api():
    results = []
    original_secret = minecraft_internal.config.MINECRAFT_BRIDGE_SECRET
    original_get_connection = minecraft_internal.get_connection
    original_repo = minecraft_internal.MinecraftBridgeRepository
    minecraft_internal.config.MINECRAFT_BRIDGE_SECRET = "test-secret"
    minecraft_internal.get_connection = lambda: DummyConnection()
    minecraft_internal.MinecraftBridgeRepository = FakeBridgeRepo
    try:
        minecraft_internal.config.MINECRAFT_BRIDGE_SECRET = ""
        try:
            await minecraft_internal.next_minecraft_command(bot_id="ichiyon", guild_id="guild-a", x_minecraft_bridge_secret="test-secret")
            results.append(check("unset bridge secret disables internal API", False))
        except HTTPException as exc:
            results.append(check("unset bridge secret disables internal API", exc.status_code == 503))
        minecraft_internal.config.MINECRAFT_BRIDGE_SECRET = "test-secret"

        try:
            await minecraft_internal.next_minecraft_command(bot_id="ichiyon", guild_id="guild-a", x_minecraft_bridge_secret=None)
            results.append(check("missing secret is rejected", False))
        except HTTPException as exc:
            results.append(check("missing secret is rejected", exc.status_code == 401))

        try:
            await minecraft_internal.next_minecraft_command(bot_id="ichiyon", guild_id="guild-a", x_minecraft_bridge_secret="bad")
            results.append(check("bad secret is rejected", False))
        except HTTPException as exc:
            results.append(check("bad secret is rejected", exc.status_code == 401))

        payload = await minecraft_internal.next_minecraft_command(
            bot_id="ichiyon",
            guild_id="guild-a",
            x_minecraft_bridge_secret="test-secret",
        )
        command = payload["command"]
        results.append(check("next command returns structured type only", command["type"] == "narita_carpet" and "command" not in command))
        results.append(check("next command returns minecraft player", command["minecraft_player"] == "Player45165996"))

        result = minecraft_internal.MinecraftCommandResult(status="succeeded", reason="ok")
        payload = await minecraft_internal.post_minecraft_command_result(
            "request-1",
            result,
            x_minecraft_bridge_secret="test-secret",
        )
        results.append(check("result callback stores terminal status", payload == {"ok": True} and FakeBridgeRepo.marked["status"] == "succeeded"))
    finally:
        minecraft_internal.config.MINECRAFT_BRIDGE_SECRET = original_secret
        minecraft_internal.get_connection = original_get_connection
        minecraft_internal.MinecraftBridgeRepository = original_repo
    return results


def static_checks():
    results = []
    migration = (ROOT_DIR / "migrations" / "047_add_minecraft_bridge.sql").read_text(encoding="utf-8")
    script = (ROOT_DIR / "minecraft" / "behavior_packs" / "import_structures" / "scripts" / "main.js").read_text(encoding="utf-8")
    manifest = (ROOT_DIR / "minecraft" / "behavior_packs" / "import_structures" / "manifest.json").read_text(encoding="utf-8")
    permissions = (ROOT_DIR / "minecraft" / "config" / "2fbc1c02-0c4d-4e98-a851-c1e41337c7a8" / "permissions.json").read_text(encoding="utf-8")

    results.append(check("minecraft player validation accepts safe name", is_valid_minecraft_player_name("Player45165996")))
    for value in ("Player 45165996", "../bad", "@a", "too_long_player_name_123"):
        results.append(check("minecraft player validation rejects {0}".format(value), not is_valid_minecraft_player_name(value)))

    results.append(check("migration keeps optional player link table without requiring it", "CREATE TABLE IF NOT EXISTS minecraft_player_links" in migration))
    results.append(check("queue claim uses skip locked", "FOR UPDATE SKIP LOCKED" in migration or "FOR UPDATE SKIP LOCKED" in (ROOT_DIR / "bot" / "repositories" / "minecraft_bridge.py").read_text(encoding="utf-8")))
    results.append(check("queue stores structured narita type", "CHECK (command_type IN ('narita_carpet'))" in migration))
    results.append(check("script rejects unknown command type", "unknown_command_type" in script and "command.type !== \"narita_carpet\"" in script))
    results.append(check("script uses fixed structure id", "mystructure:narita_map_item" in script and "command.structure" not in script))
    results.append(check("script runs fixed execute structure command", "dimension.runCommand" in script and "execute at ${playerName} run structure load ${STRUCTURE_ID} ~ ~ ~" in script))
    results.append(check("script polls every 2 seconds not every tick", "POLL_INTERVAL_TICKS = 40" in script))
    results.append(check("script uses server-net", "@minecraft/server-net" in manifest and "@minecraft/server-net" in permissions))
    results.append(check("module specific permissions limit private bot API", "http://10.0.0.94:8000/internal/minecraft/" in permissions))
    results.append(check("permissions force_https false for private HTTP", '"force_https": false' in permissions))
    results.append(check("real secret is not committed", "replace-with-long-random-secret" in (ROOT_DIR / "minecraft" / "config" / "2fbc1c02-0c4d-4e98-a851-c1e41337c7a8" / "secrets.json.example").read_text(encoding="utf-8")))
    return results


async def main_async():
    results = []
    results.extend(static_checks())
    results.extend(await exercise_service())
    results.extend(await exercise_internal_api())
    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
