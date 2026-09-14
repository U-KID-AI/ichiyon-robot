import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot import messages
from bot.repositories.minecraft_bridge import MINECRAFT_COMMAND_TYPES, is_valid_minecraft_player_name
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

    def enqueue_command(self, **kwargs):
        row = dict(kwargs)
        row["request_id"] = "request-1"
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


EXPECTED_ITEM_COMMANDS = [
    ("ストラクチャーブロック", "structure_block", "ストラクチャーブロック", "inventory"),
    ("コマンドブロック", "command_block", "コマンドブロック", "inventory"),
    ("バリアブロック", "barrier_block", "バリアブロック", "inventory"),
    ("ライトブロック", "light_block", "ライトブロック", "inventory"),
    ("ジグソーブロック", "jigsaw_block", "ジグソーブロック", "inventory"),
    ("ストラクチャーヴォイド", "structure_void", "ストラクチャーヴォイド", "inventory"),
    ("リピートコマンドブロック", "repeating_command_block", "リピートコマンドブロック", "inventory"),
    ("チェーンコマンドブロック", "chain_command_block", "チェーンコマンドブロック", "inventory"),
    ("タケツミエッグ", "taketumi_spawn_egg", "タケツミエッグ", "inventory"),
]


async def exercise_service():
    results = []
    bot_user = FakeUser(999999999999999999, bot=True)
    target_user = FakeUser(222222222222222222)
    messages._bot = SimpleNamespace(user=bot_user)

    original_get_connection = minecraft_bridge.get_connection
    original_repo = minecraft_bridge.MinecraftBridgeRepository
    original_permission_repo = minecraft_bridge.PermissionRepository
    original_wait = minecraft_bridge.wait_for_minecraft_result
    original_timeout = minecraft_bridge.config.MINECRAFT_COMMAND_TIMEOUT_SECONDS
    original_control_base = minecraft_bridge.config.MINECRAFT_CONTROL_API_BASE
    original_control_secret = minecraft_bridge.config.MINECRAFT_CONTROL_API_SECRET
    original_allowed_restart = minecraft_bridge.config.MINECRAFT_RESTART_ALLOWED_USER_IDS
    original_fetch_control_status = minecraft_bridge.fetch_control_status
    original_request_control_restart = minecraft_bridge.request_control_restart
    minecraft_bridge.get_connection = lambda: DummyConnection()
    minecraft_bridge.MinecraftBridgeRepository = FakeBridgeRepo

    class FakePermissionRepository:
        def __init__(self, connection):
            self.connection = connection

        def has_global_admin(self, discord_user_id):
            return False

    minecraft_bridge.PermissionRepository = FakePermissionRepository
    minecraft_bridge.config.MINECRAFT_COMMAND_TIMEOUT_SECONDS = 3
    minecraft_bridge.config.MINECRAFT_CONTROL_API_BASE = ""
    minecraft_bridge.config.MINECRAFT_CONTROL_API_SECRET = ""
    minecraft_bridge.config.MINECRAFT_RESTART_ALLOWED_USER_IDS = ()

    async def fake_wait(request_id, timeout_seconds):
        return {"status": "succeeded", "result_reason": "ok"}

    minecraft_bridge.wait_for_minecraft_result = fake_wait
    try:
        FakeBridgeRepo.enqueued = []
        message = FakeMessage("マイクラ 成田カーペット Player45165996", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(message, "マイクラ 成田カーペット Player45165996")
        results.append(check("valid narita command is handled", handled is True))
        results.append(check("narita command queues narita type", FakeBridgeRepo.enqueued[-1]["command_type"] == "narita_carpet"))
        results.append(check("minecraft player name is parsed directly", FakeBridgeRepo.enqueued[-1]["minecraft_player_name"] == "Player45165996"))
        results.append(check("queue does not require Discord target mapping", FakeBridgeRepo.enqueued[-1]["target_discord_user_id"] == ""))
        results.append(check("success waits for Minecraft result", message.channel.sent[-1][0][0] == "Player45165996 に成田カーペットを送り付けました。"))

        for command_text, command_type, label, _path in EXPECTED_ITEM_COMMANDS:
            message = FakeMessage("マイクラ {0} Player45165996".format(command_text), [bot_user])
            handled = await minecraft_bridge.handle_minecraft_command(
                message,
                "マイクラ {0} Player45165996".format(command_text),
            )
            results.append(check("{0} command is handled".format(command_text), handled is True))
            results.append(
                check(
                    "{0} queues structured type".format(command_text),
                    FakeBridgeRepo.enqueued[-1]["command_type"] == command_type,
                    FakeBridgeRepo.enqueued[-1]["command_type"],
                )
            )
            results.append(
                check(
                    "{0} success message".format(command_text),
                    message.channel.sent[-1][0][0] == "Player45165996 に{0}を送り付けました。".format(label),
                )
            )

        async def fake_message_wait(request_id, timeout_seconds):
            return {"status": "succeeded", "result_message": "diagnostic payload"}

        original_wait_fn = minecraft_bridge.wait_for_minecraft_result
        minecraft_bridge.wait_for_minecraft_result = fake_message_wait

        inspect_message = FakeMessage("マイクラ 手持ち確認 Player45165996", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(
            inspect_message,
            "マイクラ 手持ち確認 Player45165996",
        )
        results.append(check("held item inspect command is handled", handled is True))
        results.append(check("held item inspect queues structured type", FakeBridgeRepo.enqueued[-1]["command_type"] == "held_item_inspect"))
        results.append(check("held item inspect returns result payload", inspect_message.channel.sent[-1][0][0] == "diagnostic payload"))

        status_message = FakeMessage("マイクラ 状態", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(status_message, "マイクラ 状態")
        results.append(check("minecraft status command is handled", handled is True))
        results.append(check("minecraft status queues structured type", FakeBridgeRepo.enqueued[-1]["command_type"] == "server_status"))
        results.append(check("minecraft status uses internal safe placeholder", FakeBridgeRepo.enqueued[-1]["minecraft_player_name"] == minecraft_bridge.SERVER_STATUS_PLAYER_PLACEHOLDER))
        results.append(check("minecraft status returns result payload", status_message.channel.sent[-1][0][0] == "diagnostic payload"))

        status_with_arg = FakeMessage("マイクラ 状態 Player45165996", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(status_with_arg, "マイクラ 状態 Player45165996")
        results.append(check("minecraft status rejects extra argument", handled is True and status_with_arg.channel.sent[-1][0][0] == minecraft_bridge.MINECRAFT_COMMAND_USAGE))
        minecraft_bridge.wait_for_minecraft_result = fake_wait

        async def fake_control_status():
            return {
                "server_status": "ONLINE",
                "container": {
                    "state": "running",
                    "health": "healthy",
                    "restart_count": 0,
                    "started_at": "2026-09-13T17:29:26Z",
                    "uptime_seconds": 7200,
                    "cpu_percent": "12.3%",
                    "memory": "1GiB / 2GiB",
                },
                "host": {"cpu_percent": "20.0%", "memory": "3.0GiB / 12.0GiB"},
                "bds": {"version": "1.26.45.1"},
                "bridge": {"responding": True, "player_count": 1, "player_names": []},
            }

        minecraft_bridge.config.MINECRAFT_CONTROL_API_BASE = "http://minecraft-control:8099"
        minecraft_bridge.config.MINECRAFT_CONTROL_API_SECRET = "control-secret"
        minecraft_bridge.fetch_control_status = fake_control_status
        minecraft_bridge.wait_for_minecraft_result = fake_message_wait
        control_status = FakeMessage("マイクラ 状態", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(control_status, "マイクラ 状態")
        results.append(check("minecraft status uses control API when configured", handled is True and "Docker: running" in control_status.channel.sent[-1][0][0]))
        results.append(check("minecraft status still probes NaritaBridge for players", "NaritaBridge:" in control_status.channel.sent[-1][0][0] and "diagnostic payload" in control_status.channel.sent[-1][0][0]))

        restart_denied = FakeMessage("マイクラ 再起動", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(restart_denied, "マイクラ 再起動")
        results.append(check("minecraft restart requires explicit permission", handled is True and restart_denied.channel.sent[-1][0][0] == "Minecraftサーバー再起動の権限がありません。"))

        async def fake_control_restart():
            return {
                "server_status": "ONLINE",
                "container": {"state": "running", "health": "healthy"},
                "backup_file": "/home/ubuntu/minecraft-bedrock-creative/backups/test.tar.gz",
                "pack_sync": {"status": "unchanged", "changed_packs": []},
            }

        minecraft_bridge.config.MINECRAFT_RESTART_ALLOWED_USER_IDS = ("111",)
        minecraft_bridge.request_control_restart = fake_control_restart
        restart_allowed = FakeMessage("マイクラ 再起動", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(restart_allowed, "マイクラ 再起動")
        results.append(check("minecraft restart calls fixed control API for allowed user", handled is True and len(restart_allowed.channel.sent) == 2 and "再起動しました" in restart_allowed.channel.sent[-1][0][0]))
        minecraft_bridge.config.MINECRAFT_CONTROL_API_BASE = ""
        minecraft_bridge.config.MINECRAFT_CONTROL_API_SECRET = ""
        minecraft_bridge.config.MINECRAFT_RESTART_ALLOWED_USER_IDS = ()
        minecraft_bridge.fetch_control_status = original_fetch_control_status
        minecraft_bridge.request_control_restart = original_request_control_restart
        minecraft_bridge.wait_for_minecraft_result = fake_wait

        invalid = FakeMessage("マイクラ 成田カーペット @a", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(invalid, "マイクラ 成田カーペット @a")
        results.append(check("invalid Minecraft player name returns usage", handled is True and invalid.channel.sent[-1][0][0] == minecraft_bridge.MINECRAFT_COMMAND_USAGE))

        invalid_space = FakeMessage("マイクラ 成田カーペット Player 45165996", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(invalid_space, "マイクラ 成田カーペット Player 45165996")
        results.append(check("spaced Minecraft player name returns usage", handled is True and invalid_space.channel.sent[-1][0][0] == minecraft_bridge.MINECRAFT_COMMAND_USAGE))

        missing_arg = FakeMessage("マイクラ 成田カーペット", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(missing_arg, "マイクラ 成田カーペット")
        results.append(check("missing Minecraft player name returns usage", handled is True and missing_arg.channel.sent[-1][0][0] == minecraft_bridge.MINECRAFT_COMMAND_USAGE))

        arbitrary_item = FakeMessage("マイクラ ダイヤモンド Player45165996", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(arbitrary_item, "マイクラ ダイヤモンド Player45165996")
        results.append(check("arbitrary item id is not accepted", handled is False and arbitrary_item.channel.sent == []))

        injected = FakeMessage("マイクラ コマンドブロック Player45165996;kill", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(injected, "マイクラ コマンドブロック Player45165996;kill")
        results.append(check("raw command injection is rejected", handled is True and injected.channel.sent[-1][0][0] == minecraft_bridge.MINECRAFT_COMMAND_USAGE))

        async def fake_offline_wait(request_id, timeout_seconds):
            return {"status": "failed", "result_reason": "player_offline"}
        minecraft_bridge.wait_for_minecraft_result = fake_offline_wait
        offline = FakeMessage("マイクラ バリアブロック Player45165996", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(offline, "マイクラ バリアブロック Player45165996")
        results.append(check("offline result is reported", handled is True and offline.channel.sent[-1][0][0] == "Player45165996 は現在Minecraftにいません。"))

        async def fake_inventory_full_wait(request_id, timeout_seconds):
            return {"status": "failed", "result_reason": "inventory_full"}
        minecraft_bridge.wait_for_minecraft_result = fake_inventory_full_wait
        full = FakeMessage("マイクラ ジグソーブロック Player45165996", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(full, "マイクラ ジグソーブロック Player45165996")
        results.append(check("inventory full result is reported", handled is True and full.channel.sent[-1][0][0] == "Player45165996 のインベントリに空きがありません。"))
        minecraft_bridge.wait_for_minecraft_result = original_wait_fn

        e_seimonji = FakeMessage("マイクラ Eの聖文字 Player45165996", [bot_user])
        handled = await minecraft_bridge.handle_minecraft_command(e_seimonji, "マイクラ Eの聖文字 Player45165996")
        results.append(check("E sacred letter is explicitly unavailable without identifier", handled is True and e_seimonji.channel.sent[-1][0][0] == minecraft_bridge._UNAVAILABLE_COMMAND_MESSAGES[minecraft_bridge.E_SEIMONJI_COMMAND]))
        results.append(check("E sacred letter does not enqueue guessed item", FakeBridgeRepo.enqueued[-1]["command_type"] != "e_seimonji"))

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
        minecraft_bridge.PermissionRepository = original_permission_repo
        minecraft_bridge.wait_for_minecraft_result = original_wait
        minecraft_bridge.config.MINECRAFT_COMMAND_TIMEOUT_SECONDS = original_timeout
        minecraft_bridge.config.MINECRAFT_CONTROL_API_BASE = original_control_base
        minecraft_bridge.config.MINECRAFT_CONTROL_API_SECRET = original_control_secret
        minecraft_bridge.config.MINECRAFT_RESTART_ALLOWED_USER_IDS = original_allowed_restart
        minecraft_bridge.fetch_control_status = original_fetch_control_status
        minecraft_bridge.request_control_restart = original_request_control_restart
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
    migration_048 = (ROOT_DIR / "migrations" / "048_extend_minecraft_command_types.sql").read_text(encoding="utf-8")
    migration_049 = (ROOT_DIR / "migrations" / "049_extend_minecraft_utility_command_types.sql").read_text(encoding="utf-8")
    script = (ROOT_DIR / "minecraft" / "behavior_packs" / "import_structures" / "scripts" / "main.js").read_text(encoding="utf-8")
    control_api = (ROOT_DIR / "scripts" / "minecraft" / "minecraft_control_api.py").read_text(encoding="utf-8")
    control_env = (ROOT_DIR / "scripts" / "minecraft" / "minecraft-control-api.env.example").read_text(encoding="utf-8")
    control_service = (ROOT_DIR / "scripts" / "minecraft" / "minecraft-control-api.service.example").read_text(encoding="utf-8")
    compose = (ROOT_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (ROOT_DIR / ".env.example").read_text(encoding="utf-8")
    manifest = (ROOT_DIR / "minecraft" / "behavior_packs" / "import_structures" / "manifest.json").read_text(encoding="utf-8")
    permissions = (ROOT_DIR / "minecraft" / "config" / "2fbc1c02-0c4d-4e98-a851-c1e41337c7a8" / "permissions.json").read_text(encoding="utf-8")
    avatar_bp_item = (ROOT_DIR / "minecraft" / "behavior_packs" / "ichiyon_avatar_bp" / "items" / "taketumi_spawn_egg.json").read_text(encoding="utf-8")
    avatar_bp_entity = (ROOT_DIR / "minecraft" / "behavior_packs" / "ichiyon_avatar_bp" / "entities" / "taketumi.json").read_text(encoding="utf-8")
    item_texture = (ROOT_DIR / "minecraft" / "resource_packs" / "ichiyon_avatar_rp" / "textures" / "item_texture.json").read_text(encoding="utf-8")
    lang = (ROOT_DIR / "minecraft" / "resource_packs" / "ichiyon_avatar_rp" / "texts" / "en_US.lang").read_text(encoding="utf-8")

    results.append(check("minecraft player validation accepts safe name", is_valid_minecraft_player_name("Player45165996")))
    for value in ("Player 45165996", "../bad", "@a", "too_long_player_name_123"):
        results.append(check("minecraft player validation rejects {0}".format(value), not is_valid_minecraft_player_name(value)))

    results.append(check("migration keeps optional player link table without requiring it", "CREATE TABLE IF NOT EXISTS minecraft_player_links" in migration))
    results.append(check("queue claim uses skip locked", "FOR UPDATE SKIP LOCKED" in migration or "FOR UPDATE SKIP LOCKED" in (ROOT_DIR / "bot" / "repositories" / "minecraft_bridge.py").read_text(encoding="utf-8")))
    for _command_text, command_type, _label, _path in EXPECTED_ITEM_COMMANDS:
        results.append(
            check(
                "queue allows {0}".format(command_type),
                command_type in migration_049
                and command_type in MINECRAFT_COMMAND_TYPES
                and command_type in minecraft_bridge._COMMAND_TYPES_BY_TEXT.values(),
            )
        )
    results.append(check("existing queue migration still has original block commands", "structure_block" in migration_048 and "command_block" in migration_048))
    results.append(check("queue allows held item inspect", "held_item_inspect" in migration_049 and "held_item_inspect" in MINECRAFT_COMMAND_TYPES))
    results.append(check("queue allows minecraft status", "server_status" in migration_049 and "server_status" in MINECRAFT_COMMAND_TYPES))
    results.append(check("script rejects unknown command type", "unknown_command_type" in script))
    results.append(check("script uses fixed structure id", "mystructure:narita_map_item" in script and "command.structure" not in script))
    results.append(check("script transfers fixed Narita map item", "structure load ${STRUCTURE_ID} ${x} ${y} ${z}" in script and "minecraft:filled_map" in script))
    for _command_text, command_type, _label, path in EXPECTED_ITEM_COMMANDS:
        if path == "inventory":
            results.append(check("script has inventory item for {0}".format(command_type), command_type in script))
        else:
            results.append(check("script has fixed give path for {0}".format(command_type), command_type in script))
    results.append(check("script grants only allow-listed items", "command.item" not in script and "ダイヤモンド" not in script))
    results.append(check("light block uses inventory light block 15 item", "minecraft:light_block_15" in script and "FIXED_GIVE" not in script and "data: 15" not in script))
    results.append(check("script reports inventory full", "inventory_full" in script and "addItem" in script and "ItemStack" in script))
    results.append(check("taketumi egg uses allow-listed item", "taketumi_spawn_egg" in script and "ichiyon:taketumi_spawn_egg" in script))
    results.append(check("taketumi entity remains spawnable and summonable", '"is_spawnable": true' in avatar_bp_entity and '"is_summonable": true' in avatar_bp_entity))
    results.append(check("taketumi egg item uses entity placer", '"minecraft:entity_placer"' in avatar_bp_item and '"entity": "ichiyon:taketumi"' in avatar_bp_item))
    results.append(check("taketumi egg item has icon", '"minecraft:icon": "ichiyon:taketumi_spawn_egg"' in avatar_bp_item and "textures/items/taketumi_spawn_egg" in item_texture))
    results.append(check("taketumi egg has display name", "item.ichiyon:taketumi_spawn_egg.name=タケツミエッグ" in lang))
    results.append(check("script can inspect held item", "handleHeldItemInspect" in script and "getDynamicPropertyIds" in script and "internal_nbt: Script APIでは取得不可" in script))
    results.append(check("script can report minecraft status", "handleServerStatus" in script and "world.getAllPlayers()" in script and "Minecraft Server: ONLINE" in script))
    results.append(check("script does not expose host shell status", "child_process" not in script and "docker ps" not in script))
    results.append(check("bot compose passes minecraft control settings", "MINECRAFT_CONTROL_API_BASE" in compose and "MINECRAFT_CONTROL_API_SECRET" in compose))
    results.append(check("env example does not contain real minecraft control secret", "MINECRAFT_CONTROL_API_SECRET=" in env_example and "MINECRAFT_CONTROL_API_SECRET=replace" not in env_example))
    results.append(check("control API has fixed status and restart endpoints", '@app.get("/status")' in control_api and '@app.post("/restart")' in control_api))
    results.append(check("control API executes fixed docker compose restart only", '["docker", "compose", "stop", "-t", "60", COMPOSE_SERVICE]' in control_api and '["docker", "compose", "up", "-d", COMPOSE_SERVICE]' in control_api))
    results.append(check("control API does not accept arbitrary shell command input", "shell=True" not in control_api and "request.command" not in control_api and "docker command" not in control_api))
    results.append(check("control API backs up world before restart", "creative-before-restart" in control_api and "tarfile.open" in control_api))
    results.append(check("control API syncs packs only from fixed specs", "PACK_SPECS" in control_api and "changed_packs" in control_api and "pack_source_missing" in control_api))
    results.append(check("control API prunes restart backups", "MINECRAFT_CONTROL_BACKUP_RETENTION" in control_api and "prune_old_backups" in control_api))
    results.append(check("control API waits for post-restart readiness", "wait_for_ready" in control_api and "MINECRAFT_CONTROL_RESTART_WAIT_SECONDS" in control_api))
    results.append(check("control API defaults save hold off", "MINECRAFT_CONTROL_USE_SAVE_HOLD=false" in control_env and "USE_SAVE_HOLD" in control_api))
    results.append(check("control API systemd service binds private IP", "--host 10.0.0.62 --port 8099" in control_service))
    results.append(check("control API service is independent from BDS container", "docker exec" not in control_service and "ExecStart=/opt/ichiyon-minecraft-control/.venv/bin/uvicorn" in control_service))
    results.append(check("restart has longer bot-side timeout", "MINECRAFT_RESTART_TIMEOUT_SECONDS=240" in env_example and "MINECRAFT_RESTART_TIMEOUT_SECONDS" in compose))
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
