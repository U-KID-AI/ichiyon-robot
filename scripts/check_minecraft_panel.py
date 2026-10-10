"""Offline Discord interactions through the real parser/permissions/queue handler."""
import asyncio
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
with patch("dotenv.load_dotenv", return_value=False):
    from bot import message_routing as routing
    from bot.message_router import MessageRouter
    from bot.services import interaction_panel, minecraft_bridge as bridge, minecraft_panel as panel
import discord
from discord.ui.view import ViewStore


class Response:
    def __init__(self):
        self.done = False
        self.defer = AsyncMock(side_effect=self.ack)
        self.send_message = AsyncMock(side_effect=self.ack)
        self.send_modal = AsyncMock(side_effect=self.ack)

    async def ack(self, *args, **kwargs):
        if self.done:
            raise AssertionError("interaction acknowledged twice")
        self.done = True

    def is_done(self):
        return self.done


def interaction(user=123, *, ephemeral=True):
    return SimpleNamespace(id=987, user=SimpleNamespace(id=user, bot=False),
        guild=SimpleNamespace(id=456), guild_id=456, channel=SimpleNamespace(id=789),
        message=SimpleNamespace(flags=SimpleNamespace(ephemeral=ephemeral)),
        response=Response(), followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())


def button(view, label):
    return next(child for child in view.children if isinstance(child, discord.ui.Button) and child.label == label)


async def click(view, label, event):
    if await view.interaction_check(event):
        await button(view, label).callback(event)


def shown_view(event):
    return event.edit_original_response.call_args.kwargs["view"]


class PanelChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(bridge.config, "BOT_INSTANCE_ID", "ichiyon"))
        self.stack.enter_context(patch.object(bridge.config, "MINECRAFT_RESTART_ALLOWED_USER_IDS", set()))
        self.stack.enter_context(patch.object(bridge.config, "DEVELOPER_USER_ID", ""))
        self.connection = MagicMock()
        self.stack.enter_context(patch.object(bridge, "get_connection", return_value=self.connection))
        self.repo = MagicMock()
        self.repo.enqueue_command.return_value = {"request_id": "request-1"}
        self.stack.enter_context(patch.object(bridge, "MinecraftBridgeRepository", return_value=self.repo))
        self.result = self.stack.enter_context(patch.object(bridge, "wait_for_minecraft_result", AsyncMock(
            return_value={"status": "succeeded", "result_message": "Bridge result"})))
        permissions = MagicMock()
        permissions.has_global_admin.return_value = False
        permissions.list_manageable_guilds_for_bot.return_value = []
        self.permissions = self.stack.enter_context(patch.object(bridge, "PermissionRepository", return_value=permissions))
        self.fetch = self.stack.enter_context(patch.object(
            bridge, "fetch_online_players",
            AsyncMock(return_value=["Sourui3", "Yuki351"]),
        ))
        self.configured = self.stack.enter_context(patch.object(bridge, "control_api_configured", return_value=True))
        self.restart = self.stack.enter_context(patch.object(bridge, "request_control_restart", AsyncMock(return_value={})))
        panel._RUNNING_USERS.clear()

    async def test_production_routing_bare_minecraft_and_all_legacy_commands(self):
        bot_user = SimpleNamespace(id=88)
        with patch.object(routing.messages, "_bot", SimpleNamespace(user=bot_user)):
            routes = [route if route.name in ("context_panel", "minecraft") else
                      replace(route, handler=AsyncMock(return_value=False)) for route in routing.build_message_router().routes]
            router = MessageRouter(routes)
            message = SimpleNamespace(id=12, author=SimpleNamespace(id=123, bot=False),
                guild=SimpleNamespace(id=456), channel=SimpleNamespace(id=789, send=AsyncMock()),
                content="<@88> マイクラ", mentions=[bot_user])
            self.assertTrue(await routing.dispatch_message(message, router))
            self.assertIsInstance(message.channel.send.call_args.kwargs["view"], panel.MinecraftPanelView)
            self.repo.enqueue_command.assert_not_called()
            for command in bridge.minecraft_panel_commands():
                text = "マイクラ " + command.text + (" Sourui3" if command.needs_player else "")
                self.assertIsNone(interaction_panel.panel_command_kind(text))
                message.content = "<@88> " + text
                message.channel.send.reset_mock()
                self.assertTrue(await routing.dispatch_message(message, router), text)
                self.assertNotIn("view", message.channel.send.call_args.kwargs)
            # Malformed owned text must still receive the original usage response.
            message.content = "<@88> マイクラ モルカー召喚"
            await routing.dispatch_message(message, router)
            self.assertEqual(message.channel.send.call_args.args[0], bridge.MINECRAFT_COMMAND_USAGE)

    async def test_main_entry_and_root_persistent_registration(self):
        event = interaction()
        await click(interaction_panel.MainPanelView(), "Minecraft", event)
        sent = event.response.send_message.call_args.kwargs
        self.assertIsInstance(sent["view"], panel.MinecraftPanelView)
        self.assertTrue(sent["ephemeral"])
        self.assertEqual(sent["allowed_mentions"].to_dict(), discord.AllowedMentions.none().to_dict())
        for bot_id in ("ichiyon", "irsia", "x" * 48):
            with patch.object(bridge.config, "BOT_INSTANCE_ID", bot_id):
                bot = MagicMock()
                interaction_panel.register_persistent_views(bot)
                roots = [call.args[0] for call in bot.add_view.call_args_list if isinstance(call.args[0], panel.MinecraftPanelView)]
                self.assertEqual(len(roots), 1)
                root = roots[0]
                self.assertTrue(root.is_persistent())
                self.assertFalse(hasattr(root, "player"))
                self.assertTrue(all(item.custom_id.startswith(f"ichiyon_panel:{bot_id}:mc:") for item in root.children))
                self.assertTrue(all(len(item.custom_id) <= 100 for item in root.children))

    async def test_online_select_defers_before_network_and_opens_private_target(self):
        event = interaction(ephemeral=False)
        async def fetch():
            event.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            return {"bridge": {"player_names": ["Sourui3", "Yuki351", "Sourui3", "bad name", None]}}
        self.fetch.side_effect = fetch
        await click(panel.MinecraftPanelView(), "プレイヤー操作", event)
        view = shown_view(event)
        select = next(c for c in view.children if isinstance(c, panel.PlayerSelect))
        self.assertEqual([o.value for o in select.options], ["Sourui3", "Yuki351"])
        select._values = ["Yuki351"]
        chosen = interaction()
        await select.callback(chosen)
        target = shown_view(chosen)
        self.assertEqual(target.player, "Yuki351")
        self.assertIn("対象: Yuki351", chosen.edit_original_response.call_args.kwargs["content"])
        self.assertEqual(target.timeout, panel.SESSION_TIMEOUT)
        self.assertFalse(target.is_persistent())
        self.assertTrue(view.is_finished())

    async def test_empty_roster_and_bridge_failure_are_distinct_with_manual_fallback(self):
        self.fetch.return_value = []
        self.fetch.side_effect = None

        empty = interaction()
        await click(panel.MinecraftPanelView(), "???????", empty)
        empty_view = shown_view(empty)
        self.assertIn(
            "??????????????????",
            empty.edit_original_response.call_args.kwargs["content"],
        )
        self.assertFalse(any(isinstance(c, discord.ui.Select) for c in empty_view.children))

        manual = interaction()
        await click(empty_view, "Minecraft????", manual)
        self.assertIsInstance(
            manual.response.send_modal.call_args.args[0],
            panel.PlayerNameModal,
        )

        self.fetch.side_effect = bridge.OnlinePlayersError("bridge_unavailable")

        failed = interaction()
        await click(panel.MinecraftPanelView(), "???????", failed)
        failed_view = shown_view(failed)
        self.assertIn(
            "???????????????????????",
            failed.edit_original_response.call_args.kwargs["content"],
        )
        self.assertFalse(any(isinstance(c, discord.ui.Select) for c in failed_view.children))

        manual = interaction()
        await click(failed_view, "Minecraft????", manual)
        self.assertIsInstance(
            manual.response.send_modal.call_args.args[0],
            panel.PlayerNameModal,
        )

    def test_online_players_result_parser_validates_machine_contract(self):
        good = {
            "status": "succeeded",
            "result_message": json.dumps({
                "schema": bridge.ONLINE_PLAYERS_SCHEMA,
                "timestamp_ms": 123456789,
                "count": 3,
                "players": ["Yuki351", "Sourui3", "Yuki351"],
            }),
        }

        self.assertEqual(
            bridge.parse_online_players_result(good),
            ["Sourui3", "Yuki351"],
        )

        bad_results = (
            None,
            {},
            {"status": "failed", "result_message": ""},
            {"status": "succeeded", "result_message": "not-json"},
            {
                "status": "succeeded",
                "result_message": json.dumps({
                    "schema": "wrong.schema",
                    "timestamp_ms": 1,
                    "count": 0,
                    "players": [],
                }),
            },
            {
                "status": "succeeded",
                "result_message": json.dumps({
                    "schema": bridge.ONLINE_PLAYERS_SCHEMA,
                    "timestamp_ms": 1,
                    "count": 2,
                    "players": ["Sourui3"],
                }),
            },
            {
                "status": "succeeded",
                "result_message": json.dumps({
                    "schema": bridge.ONLINE_PLAYERS_SCHEMA,
                    "timestamp_ms": 1,
                    "count": 1,
                    "players": ["bad name"],
                }),
            },
        )

        for value in bad_results:
            with self.assertRaises(bridge.OnlinePlayersError):
                bridge.parse_online_players_result(value)

    def test_panel_roster_source_is_bridge_online_players_not_control_status(self):
        source = (
            Path(__file__).resolve().parent.parent
            / "bot"
            / "services"
            / "minecraft_panel.py"
        ).read_text(encoding="utf-8")

        self.assertIn("fetch_online_players", source)
        self.assertNotIn("fetch_control_status", source)
        self.assertNotIn('["player_names"]', source)

    async def test_modal_validation_and_owner(self):
        for name in ("", "bad name", "@everyone", "x" * 17, "日本語", "abc\n/give"):
            modal = panel.PlayerNameModal(123)
            modal.player._value = name
            event = interaction()
            await modal.on_submit(event)
            event.edit_original_response.assert_not_awaited()
            self.assertIn("Minecraft名", event.followup.send.call_args.args[0])
        modal = panel.PlayerNameModal(123)
        modal.player._value = "Player_01"
        other = interaction(user=999)
        await modal.on_submit(other)
        other.edit_original_response.assert_not_awaited()
        event = interaction()
        await modal.on_submit(event)
        self.assertEqual(shown_view(event).player, "Player_01")
        self.repo.enqueue_command.assert_not_called()

    async def test_every_player_command_uses_original_parser_and_queue(self):
        commands = [c for c in bridge.minecraft_panel_commands() if c.needs_player]
        self.assertEqual({c.text for c in commands}, set(bridge._COMMAND_TYPES_BY_TEXT))
        representatives = {"成田カーペット": "narita_carpet", "ライオポスター": "poster_raio",
            "モルカー召喚": "molcar_spawn_near_player", "キアナ召喚": "avatar_kiana_spawn_near_player",
            "手持ち確認": "held_item_inspect", "同期診断": "sync_diagnostics"}
        for command in commands:
            event = interaction()
            view = panel.PlayerPanelView(123, "Sourui3", command.category)
            await click(view, command.text, event)
            args = self.repo.enqueue_command.call_args.kwargs
            self.assertEqual(args["command_type"], representatives.get(command.text, command.command_type))
            self.assertEqual(args["minecraft_player_name"], "Sourui3")
            self.assertEqual(args["requester_discord_user_id"], "123")
            self.assertEqual(args["guild_id"], "456")
            self.assertEqual(args["discord_channel_id"], "789")
            self.assertEqual(args["discord_message_id"], "987")
            event.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            for sent in event.followup.send.call_args_list:
                self.assertTrue(sent.kwargs["ephemeral"])
                self.assertEqual(sent.kwargs["allowed_mentions"].to_dict(), discord.AllowedMentions.none().to_dict())
        self.assertEqual(self.repo.enqueue_command.call_count, len(commands))

    async def test_existing_timeout_failure_and_success_messages(self):
        view = panel.PlayerPanelView(123, "Sourui3", "items")
        for result, expected in (
            (None, "Minecraftサーバーとの通信がタイムアウトしました。"),
            ({"status": "failed", "result_reason": "inventory_full"}, "Sourui3 のインベントリに空きがありません。"),
            ({"status": "failed", "result_reason": "player_offline"}, bridge._result_error_message("Sourui3", "player_offline")),
            ({"status": "succeeded"}, "Sourui3 に成田カーペットを送り付けました。"),
        ):
            self.result.return_value = result
            event = interaction()
            await click(view, "成田カーペット", event)
            self.assertEqual(event.followup.send.call_args.args[0], expected)

    async def test_playerless_status_and_join_history(self):
        root = panel.MinecraftPanelView()
        for label, expected_type, placeholder in (("サーバー状態", "server_status", "ServerStatus"),
                                                 ("join履歴", "join_history", "JoinHistory")):
            event = interaction()
            await click(root, label, event)
            self.assertEqual(self.repo.enqueue_command.call_args.kwargs["command_type"], expected_type)
            self.assertEqual(self.repo.enqueue_command.call_args.kwargs["minecraft_player_name"], placeholder)
            self.assertTrue(event.followup.send.called)

    async def test_restart_confirmation_cancellation_and_original_permission(self):
        root = panel.MinecraftPanelView()
        opening = interaction()
        await click(root, "サーバー再起動", opening)
        confirm = shown_view(opening)
        self.restart.assert_not_awaited()
        self.assertEqual(opening.edit_original_response.call_args.kwargs["content"], "Minecraftサーバーを再起動しますか？")
        denied = interaction()
        await click(confirm, "再起動する", denied)
        self.permissions.assert_called_once()
        self.assertEqual(denied.followup.send.call_args.args[0], "Minecraftサーバー再起動の権限がありません。")
        self.restart.assert_not_awaited()
        opening = interaction()
        await click(root, "サーバー再起動", opening)
        cancelled = interaction()
        await click(shown_view(opening), "キャンセル", cancelled)
        self.assertIsNone(shown_view(cancelled))
        self.restart.assert_not_awaited()

    async def test_restart_authorization_is_checked_at_confirmation_and_single_use(self):
        with patch.object(bridge.config, "MINECRAFT_RESTART_ALLOWED_USER_IDS", {"123"}):
            opening = interaction()
            await click(panel.MinecraftPanelView(), "サーバー再起動", opening)
        denied = interaction()
        await click(shown_view(opening), "再起動する", denied)
        self.restart.assert_not_awaited()
        opening = interaction()
        await click(panel.MinecraftPanelView(), "サーバー再起動", opening)
        confirm = shown_view(opening)
        other = interaction(user=999)
        await click(confirm, "再起動する", other)
        self.restart.assert_not_awaited()
        with patch.object(bridge.config, "MINECRAFT_RESTART_ALLOWED_USER_IDS", {"123"}):
            await click(confirm, "再起動する", interaction())
            await click(confirm, "再起動する", interaction())
        self.restart.assert_awaited_once()

    async def test_navigation_target_change_back_and_close(self):
        view = panel.PlayerPanelView(123, "Sourui3")
        event = interaction()
        await click(view, "ポスター", event)
        category = shown_view(event)
        self.assertEqual(category.player, "Sourui3")
        self.assertTrue(view.is_finished())
        back = interaction()
        await click(category, "戻る", back)
        self.assertIsNone(shown_view(back).category)
        change = interaction()
        await click(shown_view(back), "対象変更", change)
        self.assertIsInstance(shown_view(change), panel.PlayerSelectView)
        close = interaction()
        await click(shown_view(change), "閉じる", close)
        self.assertIsNone(shown_view(close))
        denied = interaction(user=999)
        await click(panel.PlayerPanelView(123, "Sourui3", "items"), "成田カーペット", denied)
        self.repo.enqueue_command.assert_not_called()

    async def test_busy_guard_across_views_and_cleanup_after_exception(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def waiting(*args):
            entered.set()
            await release.wait()
            return {"status": "succeeded"}
        self.result.side_effect = waiting
        first_view = panel.PlayerPanelView(123, "Sourui3", "items")
        task = asyncio.create_task(click(first_view, "成田カーペット", interaction()))
        await entered.wait()
        try:
            second = interaction()
            await click(panel.PlayerPanelView(123, "Yuki351", "mobs"), "ゴン太召喚", second)
            self.assertIn("処理中", second.response.send_message.call_args.args[0])
            self.assertEqual(self.repo.enqueue_command.call_count, 1)
        finally:
            release.set()
            await task
        self.assertFalse(panel._RUNNING_USERS)
        self.result.side_effect = RuntimeError("offline fixture error")
        with self.assertRaises(RuntimeError):
            await click(first_view, "成田カーペット", interaction())
        self.assertFalse(panel._RUNNING_USERS)

    async def test_navigation_keeps_replacement_callbacks_registered(self):
        store = ViewStore(state=MagicMock())
        current = panel.PlayerPanelView(123, "Sourui3")
        store.add_view(current, message_id=321)
        for label in ("アイテム・ブロック", "戻る", "対象変更", "戻る"):
            event = interaction()
            async def edit(**kwargs):
                store.add_view(kwargs["view"], message_id=321)
            event.edit_original_response.side_effect = edit
            await click(current, label, event)
            current = shown_view(event)
            for child in current.children:
                self.assertIs(store._views[321][(child.type.value, child.custom_id)], child)
        current.stop()

    async def test_limits_and_pagination(self):
        def within_limits(view):
            data = view.to_components()
            self.assertLessEqual(len(data), 5)
            self.assertLessEqual(len(view.children), 25)
            identifiers = [c.custom_id for c in view.children]
            self.assertEqual(len(set(identifiers)), len(identifiers))
            for row in data:
                self.assertLessEqual(len(row["components"]), 5)
                for component in row["components"]:
                    self.assertLessEqual(len(component["custom_id"]), 100)
                    self.assertLessEqual(len(component.get("label", "")), 80)
                    self.assertLessEqual(len(component.get("options", [])), 25)
        within_limits(interaction_panel.MainPanelView())
        within_limits(panel.MinecraftPanelView())
        within_limits(panel.PlayerPanelView(123, "Sourui3"))
        with patch.object(bridge.config, "BOT_INSTANCE_ID", "x" * 48):
            command_ids = []
            for category in panel.CATEGORIES:
                view = panel.PlayerPanelView(123, "Sourui3", category)
                within_limits(view)
                command_ids.extend(c.custom_id for c in view.children if c.row != 4)
            self.assertEqual(len(command_ids), len(set(command_ids)))
        players = panel.PlayerSelectView(123, [f"Player{i}" for i in range(52)])
        within_limits(players)
        next_page = interaction()
        await click(players, "次へ", next_page)
        within_limits(shown_view(next_page))
        self.assertEqual(shown_view(next_page).children[0].options[0].value, "Player25")
        within_limits(panel.RestartConfirmView(123, next(c for c in bridge.minecraft_panel_commands() if c.command_type == "server_restart")))
        commands = tuple(bridge.MinecraftPanelCommand(f"Item{i}", f"item_{i}", "items") for i in range(46))
        with patch.object(bridge, "minecraft_panel_commands", return_value=commands):
            view = panel.PlayerPanelView(123, "Sourui3", "items", page=1)
            within_limits(view)
            self.assertEqual(len(view.children), 25)
            turn = interaction()
            await click(view, "次へ", turn)
            self.assertIn("Item40", [c.label for c in shown_view(turn).children])
            within_limits(shown_view(turn))


if __name__ == "__main__":
    unittest.main()
