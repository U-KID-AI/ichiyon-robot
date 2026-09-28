"""Real DB routing checks on the fixed disposable CI database, never production."""
import asyncio
import contextlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg
with patch('dotenv.load_dotenv', return_value=False):
    from bot import message_routing as routing
    from bot.repositories.feature_flags import FeatureFlagRepository

DATABASE_URL = 'postgresql://routing_test:routing_test@127.0.0.1:5432/routing_test'


class DatabaseRoutingChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, DATABASE_URL=DATABASE_URL))
        self.stack.enter_context(patch.object(routing.config, 'DATA_BACKEND', 'db'))
        self.stack.enter_context(patch.object(routing.config, 'BOT_INSTANCE_ID', 'ichiyon'))
        self.guild_id = str(uuid.uuid4().int % 10**18)
        self.user = SimpleNamespace(id=88)
        self.stack.enter_context(patch.object(routing.messages, '_bot', SimpleNamespace(user=self.user)))
        self.legacy = self.stack.enter_context(patch.object(routing, 'handle_mention_message', AsyncMock(return_value=True)))
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute('INSERT INTO guilds (guild_id,name) VALUES (%s,%s)', (self.guild_id,'routing fixture'))
            for bot_id in ('ichiyon','irsia'):
                FeatureFlagRepository(conn, bot_id=bot_id).set_flag(self.guild_id,'mention_reactions',True,'fixture')
                reaction_id = conn.execute('''INSERT INTO mention_reactions
                    (bot_id,guild_id,reaction_key,keyword,match_type,reaction_kind,name)
                    VALUES (%s,%s,'routing_probe','routing_probe','exact','random','routing probe') RETURNING id''',
                    (bot_id,self.guild_id)).fetchone()[0]
                conn.execute('''INSERT INTO mention_reaction_choices
                    (bot_id,guild_id,mention_reaction_id,name,body) VALUES (%s,%s,%s,'probe',%s)''',
                    (bot_id,self.guild_id,reaction_id,bot_id+' response'))
        self.addCleanup(self.remove_fixture)

    def remove_fixture(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute('DELETE FROM guilds WHERE guild_id = %s',(self.guild_id,))

    def message(self, command):
        return SimpleNamespace(id=123, content='<@88> '+command, mentions=[self.user],
            author=SimpleNamespace(id=456,bot=False,name='fixture',display_name='fixture',mention='<@456>',roles=[]),
            guild=SimpleNamespace(id=int(self.guild_id)),
            channel=SimpleNamespace(id=789,send=AsyncMock()))

    async def test_real_registry_preserves_bot_scoped_db_responses_and_misses(self):
        for bot_id in ('ichiyon','irsia'):
            routing.config.BOT_INSTANCE_ID = bot_id
            message = self.message('routing_probe')
            log = __import__('io').StringIO()
            with contextlib.redirect_stdout(log):
                self.assertTrue(await routing.dispatch_message(message,routing.build_message_router()))
            self.assertNotIn('[WARN]',log.getvalue(),log.getvalue())
            self.assertEqual(message.channel.send.await_args.args[0],bot_id+' response')
            message = self.message('unmatched_probe')
            self.assertTrue(await routing.dispatch_message(message,routing.build_message_router()))
            message.channel.send.assert_not_awaited()
        self.legacy.assert_not_awaited()


if __name__ == '__main__':
    unittest.main(verbosity=2)
