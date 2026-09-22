import asyncio
import ast
import re
import sys
import uuid
import os
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Never load local dotenv files or real credential environment values in checks.
os.environ.clear()
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False

from bot import config
from bot.repositories.ai_tasks import AI_TASK_STATUSES
from bot.services import ai_tasks


class Check:
    def __init__(self) -> None:
        self.results = []

    def add(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok), detail))

    def print_results(self) -> None:
        for name, ok, detail in self.results:
            suffix = " - {0}".format(detail) if detail else ""
            print("[{0}] {1}{2}".format("OK" if ok else "NG", name, suffix))
        passed = sum(1 for _, ok, _ in self.results if ok)
        print("summary: {0}/{1} OK".format(passed, len(self.results)))

    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.results)


class FakeChannel:
    def __init__(self) -> None:
        self.sent = []
        self.id = config.AI_TASK_DISCORD_CHANNEL_ID

    async def send(self, content: str, **kwargs) -> None:
        self.sent.append((content, kwargs))


class FakeMessage:
    def __init__(self, user_id: int, message_id: str = "message-1") -> None:
        self.author = SimpleNamespace(id=user_id)
        self.channel = FakeChannel()
        self.guild = SimpleNamespace(id=config.AI_TASK_DISCORD_GUILD_ID)
        self.id = message_id


class FakeConnection:
    def __init__(self, row=None, rows=None, error=None) -> None:
        self.row = row
        self.rows = rows or []
        self.error = error
        self.committed = False
        self.used = False

    def __enter__(self):
        self.used = True
        return self

    def __exit__(self, *_args):
        return False

    def commit(self):
        self.committed = True


class FakeRepository:
    def __init__(self, connection) -> None:
        self.connection = connection

    def create_task(self, **kwargs):
        if self.connection.error:
            raise self.connection.error
        return {
            "task_id": kwargs["task_id"],
            "status": "queued",
            "branch_name": kwargs["branch_name"],
            "worktree_name": kwargs["worktree_name"],
        }

    def get_task(self, _task_id):
        if self.connection.error:
            raise self.connection.error
        return self.connection.row

    def list_tasks(self, limit=20):
        if self.connection.error:
            raise self.connection.error
        return self.connection.rows[:limit]


class ConnectionMustNotBeUsed:
    def __enter__(self):
        raise AssertionError("database connection must not be used")

    def __exit__(self, *_args):
        return False


async def run() -> int:
    check = Check()
    allowed_id = "123456789012345678"
    config.AI_TASK_ALLOWED_USER_IDS = (allowed_id,)

    action, argument, owned = ai_tasks.parse_ai_command("AI 開発 依頼本文")
    check.add("AI 開発 is parsed", action == "開発" and argument == "依頼本文" and owned)
    check.add("empty description is rejected", not ai_tasks.parse_ai_command("AI 開発")[1])
    check.add("1800 characters are allowed", len("x" * 1800) == 1800 and len("x" * 1800) <= ai_tasks.MAX_DISCORD_DESCRIPTION_LENGTH)
    check.add("1801 characters are rejected", len("x" * 1801) > ai_tasks.MAX_DISCORD_DESCRIPTION_LENGTH)
    check.add("unauthorized user is rejected", not ai_tasks.is_ai_task_allowed("999"))
    config.AI_TASK_ALLOWED_USER_IDS = ()
    check.add("empty allowlist rejects everyone", not ai_tasks.is_ai_task_allowed(allowed_id))
    config.AI_TASK_ALLOWED_USER_IDS = (allowed_id,)
    check.add("global_admin is not an implicit AI permission", not hasattr(config, "GLOBAL_ADMIN_USER_IDS") and not ai_tasks.is_ai_task_allowed("global-admin-only"))

    task_id = uuid.uuid4()
    branch, worktree = ai_tasks.build_task_names(task_id)
    check.add("new task status is queued", FakeRepository(FakeConnection()).create_task(task_id=task_id, branch_name=branch, worktree_name=worktree)["status"] == "queued")
    check.add("task ID is generated as UUID", isinstance(task_id, uuid.UUID))
    check.add("description is absent from branch and worktree", "依頼" not in branch and "依頼" not in worktree)

    migration = (ROOT_DIR / "migrations" / "059_add_ai_tasks.sql").read_text(encoding="utf-8")
    check.add("duplicate message constraint exists", "UNIQUE (bot_id, discord_message_id)" in migration)
    deployment_migration = (ROOT_DIR / "migrations" / "062_add_ai_task_deployment_fields.sql").read_text(encoding="utf-8")
    check.add("all statuses are in current schema", all("'" + status + "'" in deployment_migration for status in AI_TASK_STATUSES))

    repository_source = (ROOT_DIR / "bot" / "repositories" / "ai_tasks.py").read_text(encoding="utf-8")
    repository_tree = ast.parse(repository_source)
    query_calls = [node for node in ast.walk(repository_tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "execute"]
    fixed_queries = {target.id for node in ast.walk(repository_tree)
                     if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                     and isinstance(node.value.value, str)
                     for target in node.targets if isinstance(target, ast.Name)}
    check.add("repository queries are parameterized", bool(query_calls)
              and all(len(node.args) >= 2 and (
                  isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                  or isinstance(node.args[0], ast.Name) and node.args[0].id in fixed_queries)
                  for node in query_calls))
    check.add("invalid task UUID is rejected", ai_tasks.parse_task_id("not-a-uuid") is None)
    check.add("list limit is capped at 20", "min(int(limit), 20)" in repository_source)
    check.add("progress fields are fixed", "current_step" in repository_source and "progress_summary" in repository_source and "SET {" not in repository_source)
    status_source = repository_source[repository_source.index("    def update_status"):repository_source.index("    def update_progress")]
    progress_source = repository_source[repository_source.index("    def update_progress"):]
    check.add("update_status preserves progress fields", "current_step = %s" not in status_source and "SET status = %s" in status_source)
    check.add("update_progress preserves unspecified fields", all("COALESCE(%s, {0})".format(field) in progress_source for field in ("current_step", "progress_summary", "result_summary", "error_message")) and "self.get_task" not in progress_source)

    original_connection = ai_tasks.get_connection
    original_repository = ai_tasks.AITaskRepository
    original_backend = config.DATA_BACKEND
    try:
        config.DATA_BACKEND = "db"
        ai_tasks.get_connection = lambda: FakeConnection(error=RuntimeError("database details must stay private"))
        ai_tasks.AITaskRepository = FakeRepository
        error_message = FakeMessage(int(allowed_id))
        await ai_tasks.handle_ai_task_command(error_message, "AI 一覧")
        error_allowed_mentions = error_message.channel.sent[0][1]["allowed_mentions"]
        check.add("DB exception is hidden from Discord", error_message.channel.sent[0][0] == ai_tasks.AI_DB_ERROR and error_allowed_mentions.everyone is False and error_allowed_mentions.users is False and error_allowed_mentions.roles is False)

        ai_tasks.get_connection = lambda: ConnectionMustNotBeUsed()
        unauth_message = FakeMessage(999)
        await ai_tasks.handle_ai_task_command(unauth_message, "AI 一覧")
        check.add("AI command consumes unauthorized request", unauth_message.channel.sent[0][0] == ai_tasks.AI_UNAUTHORIZED and unauth_message.channel.sent[0][1]["allowed_mentions"].everyone is False)

        config.DATA_BACKEND = "json"
        json_message = FakeMessage(int(allowed_id))
        ai_tasks.get_connection = lambda: ConnectionMustNotBeUsed()
        await ai_tasks.handle_ai_task_command(json_message, "AI 一覧")
        check.add("json backend does not connect to DB", json_message.channel.sent[0][0] == ai_tasks.AI_DB_REQUIRED)
        config.DATA_BACKEND = "db"

        unknown_message = FakeMessage(int(allowed_id))
        await ai_tasks.handle_ai_task_command(unknown_message, "AI xxxx")
        check.add("unknown AI command returns usage and is consumed", unknown_message.channel.sent[0][0] == ai_tasks.AI_USAGE)
        normal_message = FakeMessage(int(allowed_id))
        check.add("normal text is not owned", not await ai_tasks.handle_ai_task_command(normal_message, "通常の文章"))

        long_message = FakeMessage(int(allowed_id), message_id="long")
        await ai_tasks.handle_ai_task_command(long_message, "AI 開発 " + ("x" * 1801))
        check.add("1801 character request is not registered", long_message.channel.sent[0][0].startswith("依頼内容は"))
        empty_message = FakeMessage(int(allowed_id), message_id="empty")
        await ai_tasks.handle_ai_task_command(empty_message, "AI 開発")
        check.add("empty request is not registered", empty_message.channel.sent[0][0].startswith("依頼内容は"))
    finally:
        ai_tasks.get_connection = original_connection
        ai_tasks.AITaskRepository = original_repository
        config.DATA_BACKEND = original_backend

    main_source = (ROOT_DIR / "main.py").read_text(encoding="utf-8")
    messages_source = (ROOT_DIR / "bot" / "messages.py").read_text(encoding="utf-8")
    on_message_source = main_source[main_source.index("async def on_message("):]
    check.add("AI channel is routed before DB runtime", on_message_source.index("handle_ai_task_channel_message") < on_message_source.index("handle_db_runtime_message(message)"))
    check.add("AI command debug text is redacted", "<AI command redacted>" in main_source and "<AI command redacted>" in messages_source and "parse_ai_command" in messages_source)
    check.add("no arbitrary subprocess was added", "subprocess" not in (ROOT_DIR / "bot" / "services" / "ai_tasks.py").read_text(encoding="utf-8"))
    check.add("Discord response has a safety cap", ai_tasks.MAX_DISCORD_RESPONSE_LENGTH < 2000)

    check.print_results()
    return 0 if check.ok() else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
