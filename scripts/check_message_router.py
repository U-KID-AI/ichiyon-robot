"""Offline behavioral tests of the production registry and message boundary."""
import asyncio
import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import message_routing as routing
from bot.message_router import MessageRouter, Phase, Route


class RoutingChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(routing.config, 'DATA_BACKEND', 'json'))
        self.stack.enter_context(patch.object(routing.config, 'BOT_INSTANCE_ID', 'ichiyon'))
        self.user = SimpleNamespace(id=88)
        self.stack.enter_context(patch.object(routing.messages, '_bot', SimpleNamespace(user=self.user)))
        self.message = SimpleNamespace(content='<@88> unknown', mentions=[self.user],
            author=SimpleNamespace(id=123, bot=False), guild=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=456, send=AsyncMock()))
        self.mocks = {}
        for name in ('handle_ai_task_channel_message', 'handle_context_panel_command',
                     'handle_mention_music_links', 'handle_youtube_n_pull_command',
                     'handle_voice_command', 'handle_developer_command', 'handle_minecraft_command',
                     'handle_mention_shortcut_command', 'handle_horoscope_command',
                     'maybe_enqueue_tts', 'handle_db_runtime_message', 'handle_word_response'):
            self.mocks[name] = self.stack.enter_context(patch.object(routing, name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(routing, 'contains_ng_word', return_value=False))
        self.mode = self.stack.enter_context(patch.object(routing.hayusu, 'handle_mode_message', AsyncMock(return_value=False)))
        self.start = self.stack.enter_context(patch.object(routing.hayusu, 'maybe_start_hayusu_mode', AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(routing, 'draw_quote_message', return_value={'text':'quote'}))

    async def dispatch(self):
        return await routing.dispatch_message(self.message, routing.build_message_router())

    async def test_registry_order_independent_of_declaration(self):
        routes = routing.build_message_router().routes
        expected = ['ai_task','empty_mention','context_panel','music_links','youtube_n_pull','voice',
                    'developer','minecraft','mention_shortcut','horoscope','tts','db_runtime',
                    'ng_word','mode','mode_start','mention','word_response']
        self.assertEqual([r.name for r in MessageRouter(reversed(routes)).routes], expected)
        for index, stopping in enumerate(routes):
            trace = []
            def callback(name):
                async def handler(message, command_text):
                    trace.append(name)
                    return name == stopping.name
                return handler
            router = MessageRouter(Route(r.name,r.phase,r.priority,callback(r.name)) for r in reversed(routes))
            self.assertTrue(await router.dispatch(self.message, None))
            self.assertEqual(trace, expected[:index+1])
        miss = AsyncMock(return_value=False)
        self.assertFalse(await MessageRouter([Route('miss',Phase.COMMAND,10,miss)]).dispatch(self.message,None))

    async def test_registration_and_result_contract(self):
        route = Route('a', Phase.COMMAND, 10, AsyncMock(return_value=None))
        with self.assertRaises(ValueError):
            MessageRouter([route,route])
        with self.assertRaises(ValueError):
            MessageRouter([route,Route('b',Phase.COMMAND,10,route.handler)])
        with self.assertRaises(TypeError):
            await MessageRouter([route]).dispatch(self.message,None)
        route = Route('failure',Phase.COMMAND,10,AsyncMock(side_effect=RuntimeError('failed')))
        later = AsyncMock(return_value=True)
        with self.assertRaises(RuntimeError):
            await MessageRouter([route,Route('later',Phase.COMMAND,20,later)]).dispatch(self.message,None)
        later.assert_not_awaited()

    async def test_ai_channel_owns_even_explicit_commands_and_misses(self):
        # Existing main has NO explicit command exception before AI Task.
        self.message.guild.id = routing.config.AI_TASK_DISCORD_GUILD_ID
        self.message.channel.id = routing.config.AI_TASK_DISCORD_CHANNEL_ID
        for text in ('private prompt', '音楽', '<@88> マイクラ 状態', '<@88> 入って', '<@88> 占い'):
            self.message.content = text
            log = io.StringIO()
            with contextlib.redirect_stdout(log):
                self.assertTrue(await self.dispatch())
            self.assertNotIn(text, log.getvalue())
        for name,mock in self.mocks.items():
            if name != 'handle_ai_task_channel_message':
                mock.assert_not_awaited()
        self.mocks['handle_ai_task_channel_message'].reset_mock()
        routing.config.BOT_INSTANCE_ID = 'irsia'
        self.assertTrue(await self.dispatch())
        self.mocks['handle_ai_task_channel_message'].assert_not_awaited()

    async def test_bot_messages_are_ignored(self):
        self.message.author.bot = True
        self.assertTrue(await self.dispatch())
        for mock in self.mocks.values():
            mock.assert_not_awaited()

    async def test_commands_consume_before_backend_tts_and_generic_mention(self):
        for name in ('handle_context_panel_command','handle_mention_music_links','handle_youtube_n_pull_command',
                     'handle_voice_command','handle_developer_command','handle_minecraft_command',
                     'handle_mention_shortcut_command','handle_horoscope_command'):
            mock = self.mocks[name]
            mock.return_value = True
            self.assertTrue(await self.dispatch())
            mock.assert_awaited()
            self.mocks['maybe_enqueue_tts'].assert_not_awaited()
            self.mocks['handle_db_runtime_message'].assert_not_awaited()
            self.message.channel.send.assert_not_awaited()
            mock.return_value = False

    async def test_db_miss_is_terminal_tts_true_is_not(self):
        routing.config.DATA_BACKEND = 'db'
        self.mocks['maybe_enqueue_tts'].return_value = True
        self.assertTrue(await self.dispatch())
        self.mocks['handle_db_runtime_message'].assert_awaited_once()
        self.mode.assert_not_awaited()
        self.message.channel.send.assert_not_awaited()

    async def test_empty_mention_db_hit_preempts_panels_and_miss_falls_through(self):
        routing.config.DATA_BACKEND = 'db'
        self.message.content = '<@88>'
        self.mocks['handle_db_runtime_message'].return_value = True
        await self.dispatch()
        self.mocks['handle_context_panel_command'].assert_not_awaited()
        self.mocks['handle_db_runtime_message'].reset_mock()
        self.mocks['handle_db_runtime_message'].return_value = False
        await self.dispatch()
        self.assertEqual(self.mocks['handle_db_runtime_message'].await_count,2)
        self.mocks['handle_context_panel_command'].assert_awaited_once()

    async def test_legacy_generic_mention_for_both_instances_and_dm(self):
        for instance in ('ichiyon','irsia'):
            routing.config.BOT_INSTANCE_ID = instance
            for guild in (SimpleNamespace(id=123),None):
                self.message.guild = guild
                self.assertTrue(await self.dispatch())
        self.assertEqual(self.message.channel.send.await_count,4)
        self.mocks['handle_word_response'].assert_not_awaited()

    async def test_legacy_kuji_preserves_count(self):
        self.message.content = '<@88> おみくじ 3連'
        with patch.object(routing,'draw_kuji_message',return_value={'text':'fortune'}):
            self.assertTrue(await self.dispatch())
        self.assertEqual([call.args[0] for call in self.message.channel.send.await_args_list],
                         ['1/3\nfortune', '2/3\nfortune', '3/3\nfortune'])

    async def test_ng_mode_and_word_fallback(self):
        with patch.object(routing,'contains_ng_word',return_value=True):
            await self.dispatch()
        self.mode.assert_not_awaited()
        self.mode.return_value = True
        await self.dispatch()
        self.start.assert_not_awaited()
        self.mode.return_value = False
        self.start.return_value = True
        await self.dispatch()
        self.message.channel.send.assert_not_awaited()
        self.start.return_value = False
        self.message.mentions = []
        self.message.content = 'plain'
        self.assertFalse(await self.dispatch())
        self.mocks['handle_word_response'].assert_awaited_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
