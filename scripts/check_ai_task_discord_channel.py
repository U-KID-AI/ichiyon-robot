"""Offline Discord entry/exit checks. No bot startup, credentials or network."""
import ast
import contextlib
import io
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

# This helper clears the environment and disables dotenv before bot imports.
from check_ai_tasks import ROOT_DIR, FakeConnection, config, ai_tasks
from bot import messages
from bot.repositories.ai_tasks import AITaskRepository


GUILD = 1515983621461245972
CHANNEL = 1551004878808285377


class Channel:
    id = CHANNEL
    guild = SimpleNamespace(id=GUILD)

    def __init__(self):
        self.sent = []
        self.fail = False

    async def send(self, text, **kwargs):
        if self.fail:
            raise RuntimeError("private failure detail")
        self.sent.append((text, kwargs))
        return SimpleNamespace(id=9001)


class Repository:
    def __init__(self, connection):
        self.connection = connection

    def create_task(self, **kwargs):
        self.connection.created.append(kwargs)
        return dict(kwargs, status="queued")

    def get_task(self, task_id):
        return dict(task_id=task_id, status="queued")

    def list_tasks(self, limit):
        return []

    def list_unnotified_terminal_tasks(self, **kwargs):
        self.connection.queries.append(kwargs)
        return [r for r in self.connection.rows if r["task_id"] not in self.connection.marks]

    def mark_terminal_notified(self, **kwargs):
        assert self.connection.channel.sent, "must send before mark"
        self.connection.marks[kwargs["task_id"]] = kwargs
        return kwargs


class ChannelChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = Channel()
        self.connection = FakeConnection()
        self.connection.created = []
        self.connection.queries = []
        self.connection.marks = {}
        self.connection.channel = self.channel
        self.bot = SimpleNamespace(get_channel=lambda channel_id: self.channel)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (("BOT_INSTANCE_ID", "ichiyon"), ("DATA_BACKEND", "db"),
                            ("AI_TASK_ALLOWED_USER_IDS", ("123",))):
            self.stack.enter_context(patch.object(config, name, value))
        self.get_connection = self.stack.enter_context(patch.object(ai_tasks, "get_connection", return_value=self.connection))
        self.stack.enter_context(patch.object(ai_tasks, "AITaskRepository", Repository))

    def message(self, text):
        return SimpleNamespace(content=text, author=SimpleNamespace(id=123, bot=False),
                               guild=SimpleNamespace(id=GUILD), channel=self.channel,
                               id=456, mentions=[])

    async def test_defaults_and_location(self):
        self.assertEqual(config.AI_TASK_DISCORD_GUILD_ID, GUILD)
        self.assertEqual(config.AI_TASK_DISCORD_CHANNEL_ID, CHANNEL)
        self.assertTrue(ai_tasks.is_ai_task_channel(self.message("text")))
        for guild, channel in ((None, CHANNEL), (GUILD + 1, CHANNEL), (GUILD, CHANNEL + 1)):
            message = self.message("AI 開発 text")
            message.guild = None if guild is None else SimpleNamespace(id=guild)
            message.channel = SimpleNamespace(id=channel)
            self.assertFalse(ai_tasks.is_ai_task_channel(message))
            self.assertFalse(await ai_tasks.handle_ai_task_channel_message(message))
            self.assertFalse(await ai_tasks.handle_ai_task_command(message, message.content))
        config.BOT_INSTANCE_ID = "irsia"
        self.assertFalse(await ai_tasks.handle_ai_task_channel_message(self.message("text")))
        self.assertFalse(await ai_tasks.handle_ai_task_command(self.message("text"), "AI 開発 text"))
        self.get_connection.assert_not_called()

    async def test_permission_before_db_and_backend(self):
        for allowlist in ((), ("999",)):
            config.AI_TASK_ALLOWED_USER_IDS = allowlist
            self.assertTrue(await ai_tasks.handle_ai_task_channel_message(self.message("text")))
            self.assertEqual(self.channel.sent[-1][0], ai_tasks.AI_UNAUTHORIZED)
        config.AI_TASK_ALLOWED_USER_IDS = ("123",)
        config.DATA_BACKEND = "json"
        await ai_tasks.handle_ai_task_channel_message(self.message("text"))
        self.assertEqual(self.channel.sent[-1][0], ai_tasks.AI_DB_REQUIRED)
        self.get_connection.assert_not_called()

    async def test_plain_prompts_preserve_body_and_ids(self):
        for text in ("天気投稿に降水確率も追加して", "AIの返答をもっと短くして", "AI unknown", "  keep\n spaces <@88>  "):
            self.assertTrue(await ai_tasks.handle_ai_task_channel_message(self.message(text)))
            row = self.connection.created[-1]
            self.assertEqual(row["description"], text)
            self.assertEqual((row["guild_id"], row["discord_channel_id"], row["discord_message_id"], row["requester_discord_user_id"]),
                             (str(GUILD), str(CHANNEL), "456", "123"))
            self.assertIsInstance(row["task_id"], uuid.UUID)
            self.assertEqual((row["branch_name"], row["worktree_name"]), ai_tasks.build_task_names(row["task_id"]))
            self.assertIn("queued", self.channel.sent[-1][0])
        self.assertEqual(len(self.connection.created), 4)
        self.assertTrue(self.connection.committed)

    async def test_legacy_management_and_length(self):
        await ai_tasks.handle_ai_task_channel_message(self.message("AI 開発 本文"))
        self.assertEqual(self.connection.created[-1]["description"], "本文")
        await ai_tasks.handle_ai_task_channel_message(self.message("AI 状態 " + str(uuid.uuid4())))
        self.assertIn("AI task状態", self.channel.sent[-1][0])
        await ai_tasks.handle_ai_task_channel_message(self.message("AI 一覧"))
        self.assertEqual(self.channel.sent[-1][0], "AI taskはありません。")
        for text in ("x" * 1801, "", "   "):
            await ai_tasks.handle_ai_task_channel_message(self.message(text))
            self.assertTrue(self.channel.sent[-1][0].startswith("依頼内容は"))
        self.assertEqual(len(self.connection.created), 1)
        await ai_tasks.handle_ai_task_channel_message(self.message("x" * 1800))
        self.assertEqual(len(self.connection.created), 2)

    async def test_mentions_and_log_redaction(self):
        with patch.object(messages, "_bot", SimpleNamespace(user=SimpleNamespace(id=88))):
            for prefix in ("<@88>", "<@!88>"):
                message = self.message(prefix + " private prompt <@88>  ")
                log = io.StringIO()
                with contextlib.redirect_stdout(log):
                    command = messages.get_mention_command_text(message)
                self.assertNotIn("private prompt", log.getvalue())
                self.assertIn("<AI development prompt redacted>", log.getvalue())
                await ai_tasks.handle_ai_task_channel_message(message, command)
                self.assertEqual(self.connection.created[-1]["description"], "private prompt <@88>  ")
            message = self.message("keep <@88> private prompt")
            self.assertIsNone(messages.get_mention_command_text(message))

    async def test_role_mentioned_management_does_not_create_tasks(self):
        task_id = "703f4750-9e05-4eeb-a9e8-004813baaeef"
        with patch.object(messages, "_bot", SimpleNamespace(user=SimpleNamespace(id=88))):
            for prefix in ("<@&1538852669488107583> ", "  <@&77>\u3000<@&99> ",
                           "<@88> <@&77> ", "<@!88> <@&77> "):
                for command, expected in (("AI 状態 " + task_id, task_id),
                                          ("AI 一覧", "AI taskはありません。"),
                                          ("AI 状態 invalid", "task IDが不正です。"),
                                          ("AI 一覧 extra", ai_tasks.AI_USAGE)):
                    message = self.message(prefix + command)
                    text = messages.get_mention_command_text(message)
                    self.assertTrue(await ai_tasks.handle_ai_task_channel_message(message, text))
                    self.assertIn(expected, self.channel.sent[-1][0])
                    self.assert_mentions_disabled(self.channel.sent[-1][1])
        self.assertEqual(self.connection.created, [])

    async def test_role_mentions_preserve_development_prompts(self):
        for text in ("<@&77> この機能を修正して", "<@&77> AI 開発 本文",
                     "<@&77> AI unknown", "本文 <@&77> AI 状態",
                     "<@77> AI 一覧", "<@&invalid> AI 一覧"):
            await ai_tasks.handle_ai_task_channel_message(self.message(text))
            self.assertEqual(self.connection.created[-1]["description"], text)

    async def test_role_mentioned_status_requires_authorization_and_location(self):
        message = self.message("<@&77> AI 状態 " + str(uuid.uuid4()))
        with patch.object(config, "AI_TASK_ALLOWED_USER_IDS", ()):
            self.assertTrue(await ai_tasks.handle_ai_task_channel_message(message))
            self.assertEqual(self.channel.sent[-1][0], ai_tasks.AI_UNAUTHORIZED)
        with patch.object(config, "BOT_INSTANCE_ID", "irsia"):
            self.assertFalse(await ai_tasks.handle_ai_task_channel_message(message))
        message.guild.id = GUILD + 1
        self.assertFalse(await ai_tasks.handle_ai_task_channel_message(message))
        self.get_connection.assert_not_called()
        self.assertEqual(self.connection.created, [])

    async def test_main_dispatch_and_redaction_without_starting_bot(self):
        source = (ROOT_DIR / "main.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_message")
        node.decorator_list = []
        namespace = dict(discord=SimpleNamespace(Message=object), messages=messages,
                         config=config,
                         parse_ai_command=ai_tasks.parse_ai_command,
                         is_ai_task_channel=ai_tasks.is_ai_task_channel,
                         handle_ai_task_channel_message=ai_tasks.handle_ai_task_channel_message)
        # Only this event function is compiled: no main imports, bot.run or other features.
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<offline on_message>", "exec"), namespace)
        with patch.object(messages, "_bot", SimpleNamespace(user=SimpleNamespace(id=88))):
            log = io.StringIO()
            with contextlib.redirect_stdout(log):
                await namespace["on_message"](self.message("private development prompt"))
                bot_message = self.message("private bot text")
                bot_message.author.bot = True
                await namespace["on_message"](bot_message)
                with patch.object(config, "BOT_INSTANCE_ID", "irsia"):
                    await namespace["on_message"](self.message("private irsia prompt"))
            self.assertNotIn("private", log.getvalue())
            self.assertIn("<AI development prompt redacted>", log.getvalue())
            self.assertIn(
                "ignored AI development channel for non-Ichiyon bot",
                log.getvalue(),
            )
            self.assertEqual(len(self.connection.created), 1)

    async def test_notification_dedupe_scope_and_mentions(self):
        task_id = uuid.uuid4()
        self.connection.rows = [dict(task_id=task_id, status="completed", result_summary="@everyone @here",
                                     description="never echo this", deployed_commit_sha="a" * 40)]
        await ai_tasks.notify_ai_task_terminal_updates_once(self.bot)
        self.assertEqual(self.connection.queries[0], dict(guild_id=str(GUILD), discord_channel_id=str(CHANNEL), limit=10))
        self.assertEqual(self.connection.marks[task_id], dict(task_id=task_id, status="completed", message_id="9001"))
        self.assertTrue(self.connection.committed)
        # A fresh invocation reads persisted state, with no process-local dedupe cache.
        await ai_tasks.notify_ai_task_terminal_updates_once(self.bot)
        self.assertEqual(len(self.channel.sent), 1)
        text, kwargs = self.channel.sent[0]
        self.assertNotIn("never echo", text)
        self.assertNotIn("@everyone", text)
        self.assertNotIn("@here", text)
        self.assertIn("a" * 40, text)
        self.assert_mentions_disabled(kwargs)

    async def test_notification_failure_and_guards(self):
        self.connection.rows = [dict(task_id=uuid.uuid4(), status="failed")]
        self.channel.fail = True
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            await ai_tasks.notify_ai_task_terminal_updates_once(self.bot)
        self.assertNotIn("private failure detail", log.getvalue())
        self.assertFalse(self.connection.marks)
        self.channel.fail = False
        await ai_tasks.notify_ai_task_terminal_updates_once(self.bot)
        self.assertEqual(len(self.connection.marks), 1)
        self.get_connection.reset_mock()
        config.BOT_INSTANCE_ID = "irsia"
        await ai_tasks.notify_ai_task_terminal_updates_once(self.bot)
        config.BOT_INSTANCE_ID = "ichiyon"
        config.DATA_BACKEND = "json"
        await ai_tasks.notify_ai_task_terminal_updates_once(self.bot)
        config.DATA_BACKEND = "db"
        self.channel.guild = SimpleNamespace(id=1)
        await ai_tasks.notify_ai_task_terminal_updates_once(self.bot)
        self.bot.get_channel = lambda _: None
        await ai_tasks.notify_ai_task_terminal_updates_once(self.bot)
        self.get_connection.assert_not_called()

    async def test_long_error_is_attached_with_only_secrets_redacted(self):
        detail = "src/example.py:42: actual failure\n" + "line detail\n" * 300 + "PASSWORD=do-not-display"
        self.connection.rows = [dict(task_id=uuid.uuid4(), status="failed", error_message=detail)]
        await ai_tasks.notify_ai_task_terminal_updates_once(self.bot)
        text, kwargs = self.channel.sent[-1]
        self.assertLess(len(text), 2000)
        attachment = kwargs["file"].fp.getvalue().decode("utf-8")
        self.assertIn("src/example.py:42: actual failure", attachment)
        self.assertIn("line detail\n" * 300, attachment)
        self.assertNotIn("do-not-display", text + attachment)

    def assert_mentions_disabled(self, kwargs):
        mentions = kwargs["allowed_mentions"]
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)

    async def test_publication_failure_redacts_exception_details(self):
        from ai_task_publish import PublishSafetyError
        from ai_task_runner import failure_reason
        # The protected publisher/runner currently expose only a generic reason.
        # Keep this disclosure regression check independent of the deferred codes.
        private_detail = "PRIVATE_PATH_TOKEN_STDERR"
        error = failure_reason(PublishSafetyError(private_detail))
        self.assertNotIn(private_detail, error)
        self.assertTrue(error)
        self.connection.rows = [dict(task_id=uuid.uuid4(), status="failed", error_message=error)]
        await ai_tasks.notify_ai_task_terminal_updates_once(self.bot)
        self.assertEqual(len(self.channel.sent), 1)
        text, kwargs = self.channel.sent[0]
        self.assertIn(error, text)
        self.assertNotIn(private_detail, text)
        self.assert_mentions_disabled(kwargs)

    async def test_all_response_mentions_and_terminal_formats(self):
        await ai_tasks.handle_ai_task_channel_message(self.message("text"))
        self.assert_mentions_disabled(self.channel.sent[-1][1])
        for status in ("completed", "failed", "needs_human", "cancelled"):
            text = ai_tasks.format_task_terminal(dict(task_id=uuid.uuid4(), status=status, result_summary="x" * 4000,
                                                     error_message="@here", deployment_summary="done"))
            self.assertIn(status, text)
            self.assertLess(len(text), 2000)
            self.assertNotIn("@here", text)
        with self.assertRaises(ValueError):
            ai_tasks.format_task_terminal(dict(status="running"))


class StaticAndSQLChecks(unittest.TestCase):
    def test_repository_parameter_binding(self):
        class Cursor:
            description = []
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, sql, params): self.sql, self.params = sql, params
            def fetchall(self): return []
            def fetchone(self): return None
        cursor = Cursor()
        repo = AITaskRepository(SimpleNamespace(cursor=lambda: cursor), bot_id="ichiyon")
        repo.list_unnotified_terminal_tasks(guild_id=str(GUILD), discord_channel_id=str(CHANNEL), limit=999)
        self.assertEqual(cursor.params, ("ichiyon", str(GUILD), str(CHANNEL), 20))
        for clause in ("bot_id = %s", "guild_id = %s", "discord_channel_id = %s", "FOR UPDATE SKIP LOCKED", "discord_terminal_notified_status IS NULL"):
            self.assertIn(clause, cursor.sql)
        task_id = uuid.uuid4()
        repo.mark_terminal_notified(task_id=task_id, status="completed", message_id="9001")
        self.assertEqual(cursor.params, ("completed", "9001", task_id, "completed", "ichiyon"))
        for clause in ("task_id = %s", "status = %s", "bot_id = %s", "discord_terminal_notified_at = NOW()", "discord_terminal_notified_status IS NULL"):
            self.assertIn(clause, cursor.sql)
        for status in ("queued", "running", "testing", "deploying", "ready_for_review"):
            with self.assertRaises(ValueError):
                repo.mark_terminal_notified(task_id=task_id, status=status, message_id=None)

    def test_migration_and_routing(self):
        migration = (ROOT_DIR / "migrations/063_add_ai_task_discord_notifications.sql").read_text(encoding="utf-8")
        for column in ("discord_terminal_notified_status", "discord_terminal_notified_at", "discord_terminal_notification_message_id"):
            self.assertIn("ADD COLUMN " + column, migration)
        self.assertIn("TIMESTAMPTZ", migration)
        source = (ROOT_DIR / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_message")
        text = ast.get_source_segment(source, handler)
        self.assertLess(text.index("if message.author.bot:"), text.index("get_mention_command_text"))
        self.assertLess(text.index("<AI development prompt redacted>"), text.index('content={debug_content'))
        irsia_guard = text.index('config.BOT_INSTANCE_ID != "ichiyon"')
        route = text.index("await handle_ai_task_channel_message")
        self.assertLess(irsia_guard, route)
        for feature in ("handle_empty_mention_message", "handle_context_panel_command", "handle_mention_music_links",
                        "handle_voice_command", "handle_minecraft_command", "handle_horoscope_command", "handle_db_runtime_message",
                        "hayusu.", "handle_mention_message", "handle_word_response", "maybe_enqueue_tts"):
            self.assertLess(route, text.index(feature))
        self.assertIn("@tasks.loop(seconds=5)", source)
        self.assertIn('config.BOT_INSTANCE_ID == "ichiyon" and not ai_task_notification_task.is_running()', source)
        self.assertIn("type(exc).__name__", source)
        self.assertNotIn("content={message.content", text)

if __name__ == "__main__":
    unittest.main(verbosity=2)
