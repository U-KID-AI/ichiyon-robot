"""Quota windows, provider errors and exact Discord response; no live API calls."""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
from bot.services import llm_chat as chat, llm_limits as limits

NOW = datetime(2026, 11, 9, 9, tzinfo=timezone.utc)


def event(mid=1, channel=1568969564769747006, bot=False):
    return SimpleNamespace(id=mid, content="やあ", channel=SimpleNamespace(id=channel),
        guild=SimpleNamespace(id=1515983621461245972),
        author=SimpleNamespace(id=42, bot=bot), reply=AsyncMock(return_value=SimpleNamespace(id=mid+100)))


class WindowTests(unittest.TestCase):
    def test_minute_and_tokens_recover(self):
        s = chat.Settings(requests_per_minute=2, tokens_per_minute=100)
        rows = [(NOW-timedelta(seconds=40), 60), (NOW-timedelta(seconds=10), 30)]
        self.assertTrue(limits.decide(NOW, rows[:1], 1, s, 40).allowed)
        blocked = limits.decide(NOW, rows[:1], 1, s, 41)
        self.assertEqual(blocked.until, NOW+timedelta(seconds=20))
        self.assertFalse(limits.decide(NOW, rows, 2, s).allowed)
        self.assertTrue(limits.decide(NOW+timedelta(seconds=21), rows, 2, s, 60).allowed)

    def test_pacific_day_and_dst(self):
        s = chat.Settings(requests_per_day=100)
        blocked = limits.decide(NOW, [], 100, s)
        self.assertEqual(blocked.until, datetime(2026, 11, 10, 8, tzinfo=timezone.utc))
        self.assertTrue(limits.decide(blocked.until, [], 0, s).allowed)
        for day, hours in ((datetime(2026,3,8,12,tzinfo=timezone.utc),23),
                           (datetime(2026,11,1,12,tzinfo=timezone.utc),25)):
            start, end = limits.day_bounds(day)
            self.assertEqual((end-start).total_seconds()/3600, hours)

    def test_observed_caps_and_zero(self):
        s = chat.Settings(requests_per_day=1000)
        self.assertTrue(limits.decide(NOW, [], 474, s, observed={"requests_per_day":500}).allowed)
        self.assertFalse(limits.decide(NOW, [], 475, s, observed={"requests_per_day":500}).allowed)
        self.assertFalse(limits.decide(NOW, [], 0, s, observed={"requests_per_minute":0}).allowed)

    def test_provider_daily_minute_tokens_unknown_and_nonquota(self):
        for quota, field, reason in (
            ("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "requests_per_day", "provider_day"),
            ("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "requests_per_minute", "provider_minute"),
            ("GenerateContentInputTokensPerModelPerMinute-FreeTier", "tokens_per_minute", "provider_minute")):
            data = {"error":{"status":"RESOURCE_EXHAUSTED", "details":[
                {"@type":"type.googleapis.com/google.rpc.QuotaFailure", "violations":[{"quotaId":quota,"quotaValue":"500"}]},
                {"@type":"type.googleapis.com/google.rpc.RetryInfo", "retryDelay":"31.5s"}]}}
            e = limits.parse_quota_error(429, data, now=NOW)
            self.assertEqual(e.reason, reason)
            self.assertEqual(e.observed, {field:500})
            self.assertEqual(e.until, limits.day_bounds(NOW)[1] if reason.endswith("day") else NOW+timedelta(seconds=31.5))
        self.assertIsNone(limits.parse_quota_error(503, {"error":{"status":"UNAVAILABLE"}}, now=NOW))
        self.assertEqual(limits.parse_quota_error(429, {}, {"retry-after":"90"}, NOW).until, NOW+timedelta(seconds=90))
        self.assertEqual(limits.parse_quota_error(429, {}, now=NOW).reason, "provider_unknown")
        self.assertIsNotNone(limits.parse_quota_error(400, {"error":{"status":"RESOURCE_EXHAUSTED"}}, now=NOW))


class ReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_added_channel_and_cross_guild_isolation(self):
        s=chat.Settings(enabled=True,extra_channels={"1417484793674731622":1392174489609179327})
        m=event(channel=1417484793674731622)
        self.assertFalse(s.allows(m))
        m.guild.id=1392174489609179327
        self.assertTrue(s.allows(m))
        self.assertTrue(s.allows(event()))
        m.channel.id=123
        self.assertFalse(s.allows(m))

    async def test_inflight_provider_limit_gets_exact_reply(self):
        from bot import messages
        c=chat.Chat();s=chat.Settings(enabled=True);m=event()
        @asynccontextmanager
        async def typing():
            yield
        m.channel.typing=typing;m.author.display_name='test';m.reference=None
        persona=SimpleNamespace(guide='guide',retrieve=lambda *args:[],last_search_ms=0)
        with patch.object(c,'reload',AsyncMock(return_value=s)), \
             patch.object(chat,'claim',AsyncMock(return_value=True)), \
             patch.object(chat,'finish',AsyncMock()), \
             patch.object(limits,'check',AsyncMock(return_value=limits.Decision(True))), \
             patch.object(messages,'get_bot',return_value=SimpleNamespace(user=SimpleNamespace(id=999),latency=0)), \
             patch.object(chat,'conversation',AsyncMock(return_value=([],None))), \
             patch.object(chat.Persona,'load',return_value=persona), \
             patch.object(chat,'generate',AsyncMock(side_effect=chat.QuotaError('provider_day'))) as generate:
            await c.respond(m,s)
            generate.assert_awaited_once()
            self.assertEqual(m.reply.await_args.args,('ゴソゴソ\nアイスがないよ',))

    async def test_slow_search_keeps_real_fallback_examples(self):
        from bot import messages
        c=chat.Chat();s=chat.Settings(enabled=True);m=event()
        @asynccontextmanager
        async def typing():
            yield
        m.channel.typing=typing;m.author.display_name='test';m.reference=None
        examples=[{'message_id':'1','reply':'そうね','context':'fixture'}]
        persona=SimpleNamespace(guide='guide',retrieve=lambda *args:time.sleep(.85),
                                fallback=lambda *args:examples,last_search_ms=0)
        with patch.object(c,'reload',AsyncMock(return_value=s)), \
             patch.object(chat,'claim',AsyncMock(return_value=True)), patch.object(chat,'finish',AsyncMock()), \
             patch.object(limits,'check',AsyncMock(return_value=limits.Decision(True))), \
             patch.object(messages,'get_bot',return_value=SimpleNamespace(user=SimpleNamespace(id=999),latency=0)), \
             patch.object(chat,'conversation',AsyncMock(return_value=([],None))), \
             patch.object(chat.Persona,'load',return_value=persona), \
             patch.object(chat,'generate',AsyncMock(return_value=('fixture response',{},.1))) as generate:
            await c.respond(m,s)
            self.assertEqual(json.loads(generate.await_args.args[1])['examples'],examples)

    async def test_exact_every_message_no_api_and_dedup(self):
        c = chat.Chat();s = chat.Settings(enabled=True)
        c.recent_users[42] = float('inf')  # Fixed replies bypass the ordinary user cooldown.
        messages = [event(1), event(2), event(2)]
        with patch.object(c,"reload",AsyncMock(return_value=s)), \
             patch.object(limits,"check",AsyncMock(return_value=limits.Decision(False,"day"))), \
             patch.object(chat,"claim",AsyncMock(side_effect=[True,True,False])), \
             patch.object(chat,"finish",AsyncMock()), patch.object(chat,"generate",AsyncMock()) as gen:
            for m in messages:
                await c.handle(m)
            for m in messages[:2]:
                self.assertEqual(m.reply.await_args.args, ("ゴソゴソ\nアイスがないよ",))
                self.assertFalse(m.reply.await_args.kwargs["mention_author"])
                self.assertFalse(m.reply.await_args.kwargs["allowed_mentions"].everyone)
            messages[2].reply.assert_not_awaited()
            gen.assert_not_awaited()
            self.assertEqual(c.queue.qsize(),0)

    async def test_boundary_and_recovery(self):
        c = chat.Chat();s=chat.Settings(enabled=True)
        c.worker=asyncio.create_task(asyncio.sleep(60))
        try:
            with patch.object(c,"reload",AsyncMock(return_value=s)), \
                 patch.object(limits,"check",AsyncMock(side_effect=[limits.Decision(False,"minute"),limits.Decision(True)])) as check, \
                 patch.object(chat,"claim",AsyncMock(return_value=True)), patch.object(chat,"finish",AsyncMock()):
                self.assertFalse(await c.handle(event(channel=123)))
                self.assertTrue(await c.handle(event(bot=True)))
                check.assert_not_awaited()
                first,second=event(1),event(2)
                await c.handle(first); await c.handle(second)
                first.reply.assert_awaited_once()
                second.reply.assert_not_awaited()
                self.assertEqual(c.queue.qsize(),1)
        finally:
            c.worker.cancel();await asyncio.gather(c.worker,return_exceptions=True)

    async def test_429_no_retry_503_still_retries_and_no_raw_error(self):
        real=httpx.AsyncClient
        with tempfile.TemporaryDirectory() as tmp:
            key=Path(tmp)/'key';key.write_text('fixture')
            s=chat.Settings(key_path=str(key))
            for status,count in ((429,1),(503,2)):
                calls=[]
                def handler(request):
                    calls.append(request)
                    return httpx.Response(status,json={"error":{"message":"do not reveal","status":"RESOURCE_EXHAUSTED" if status==429 else "UNAVAILABLE"}})
                with patch.object(chat.httpx,"AsyncClient",lambda **kwargs:real(transport=httpx.MockTransport(handler),**kwargs)), \
                     patch.object(chat,"reserve",AsyncMock(return_value=True)), patch.object(limits,"remember",AsyncMock()) as remember:
                    with self.assertRaises(chat.GeminiError) as caught:
                        await chat.generate(s,'hello',1)
                    self.assertEqual(len(calls),count)
                    self.assertNotIn('do not reveal',str(caught.exception))
                    self.assertEqual(isinstance(caught.exception,chat.QuotaError),status==429)
                    self.assertEqual(remember.await_count,1 if status==429 else 0)


@unittest.skipUnless(os.getenv("LLM_TEST_DATABASE_URL"), "requires disposable llm_test database")
class PersistentTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocks_tokens_recovery_and_retention(self):
        url=os.environ['LLM_TEST_DATABASE_URL'];self.assertTrue(url.endswith('/llm_test'))
        with patch.dict(os.environ,DATABASE_URL=url):
            async with await limits.connection() as conn:
                for name in ('072_add_llm_requests.sql','073_add_llm_quota_windows.sql'):
                    await conn.execute((Path(__file__).resolve().parent.parent/'migrations'/name).read_text())
                await conn.execute('TRUNCATE llm_requests CASCADE');await conn.execute('TRUNCATE llm_quota_blocks')
            s=chat.Settings(tokens_per_minute=100)
            await chat.claim(1,s);self.assertTrue((await limits.check(s,80,1)).allowed)
            self.assertFalse((await limits.check(s,21)).allowed)
            await limits.record_usage(1,{'promptTokenCount':10})
            self.assertTrue((await limits.check(s,21)).allowed)
            e=limits.parse_quota_error(429,{},now=datetime.now(timezone.utc))
            await limits.remember(s,e)
            self.assertFalse((await limits.check(s)).allowed)
            # A new instance reads the same persisted block, then automatically resumes.
            self.assertFalse((await limits.check(replace(s))).allowed)
            async with await limits.connection() as conn:
                await conn.execute("UPDATE llm_quota_blocks SET blocked_until=now()-interval '1 second'")
                await conn.execute("UPDATE llm_api_attempts SET attempted_at=now()-interval '61 seconds'")
            self.assertTrue((await limits.check(s,100)).allowed)
            await chat.claim(2,s)
            async with await limits.connection() as conn:
                await conn.execute("UPDATE llm_requests SET created_at=now()-interval '8 days',last_attempt_at=now()-interval '8 days' WHERE message_id=1")
            self.assertTrue((await limits.check(s,1,2)).allowed)
            async with await limits.connection() as conn:
                self.assertEqual((await (await conn.execute('SELECT count(*) FROM llm_requests WHERE message_id=1')).fetchone())[0],0)


if __name__ == '__main__':
    unittest.main()
