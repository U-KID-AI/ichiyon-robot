"""Offline boundary, API and persona regression checks. No real API calls."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
from bot.services import llm_chat as llm
from bot.services.llm_persona import Persona, PERSON_ID


def message(mid=10, channel=1569571831894114354, bot=False, author=42):
    return SimpleNamespace(id=mid, content="今日は何してた？", reference=None,
        guild=SimpleNamespace(id=1392174489609179327),
        channel=SimpleNamespace(id=channel),
        author=SimpleNamespace(id=author, bot=bot, display_name="テスト"), reply=AsyncMock())


class PersonaTests(unittest.TestCase):
    def test_private_author_and_dedup(self):
        data = {"author_id": PERSON_ID, "source": "direct_provision", "examples": [
            {"context": "何してた？", "reply": "ゲームしてた"},
            {"context": "何してた？", "reply": "ゲームしてた"},
            {"reply": "https://example.com"}, {"reply": "!play"}]}
        p = Persona(data)
        self.assertEqual(len(p.examples), 1)
        self.assertEqual(p.retrieve("最近どう？", 3)[0]["reply"], "ゲームしてた")
        data["examples"].append({"author_id": "123", "reply": "別人"})
        with self.assertRaises(ValueError):
            Persona(data)
        with self.assertRaises(ValueError):
            Persona({"author_id": PERSON_ID, "source": "discord_history"})

    def test_empty_and_short_penalty(self):
        self.assertEqual(Persona().retrieve("やあ", 4), [])
        p = Persona({"author_id": PERSON_ID, "source": "direct_provision", "examples": [
            {"context": "何？", "reply": "w"}, {"context": "どう？", "reply": "ちょっと待って"}]})
        self.assertEqual(p.retrieve("質問？", 1)[0]["reply"], "ちょっと待って")


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import discord
        async def deliver(settings, message, text):
            return await message.reply(text, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        self.transport = patch.object(llm, 'webhook_send', side_effect=deliver)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    async def test_channel_boundary_disabled_and_bot(self):
        chat = llm.Chat()
        with patch.object(chat, "reload", AsyncMock(return_value=llm.Settings())):
            self.assertFalse(await chat.handle(message(channel=123)))
            self.assertTrue(await chat.handle(message()))
            self.assertTrue(await chat.handle(message(bot=True)))
            self.assertEqual(chat.queue.qsize(), 0)

    async def test_queue_and_rate_limit(self):
        chat = llm.Chat()
        chat.worker = asyncio.create_task(asyncio.sleep(60))
        try:
            with patch.object(chat, "reload", AsyncMock(return_value=llm.Settings(enabled=True))), \
                    patch.object(llm.limits, "check", AsyncMock(return_value=llm.limits.Decision(True))):
                for i in range(10):
                    await chat.handle(message(mid=i, author=i))
                self.assertEqual(chat.queue.qsize(), 5)
                await chat.handle(message(mid=99, author=0))
                self.assertEqual(chat.queue.qsize(), 5)
        finally:
            chat.worker.cancel()
            await asyncio.gather(chat.worker, return_exceptions=True)

    async def test_duplicate_claim_blocks_api(self):
        with patch.object(llm, "claim", AsyncMock(return_value=False)), patch.object(llm, "generate", AsyncMock()) as generate:
            await llm.Chat().respond(message(), llm.Settings(enabled=True))
            generate.assert_not_called()

    async def test_history_orders_speakers_and_reply(self):
        m = message(10)
        old = message(8, bot=True, author=99)
        newer = message(11, author=43)
        last_reply = message(12, bot=True, author=99)
        async def history(**kwargs):
            for item in (last_reply, newer, m, old):
                yield item
        m.channel.history = history
        m.channel.fetch_message = AsyncMock(return_value=old)
        m.reference = SimpleNamespace(message_id=8, channel_id=m.channel.id)
        old.webhook_id = 99; last_reply.webhook_id = 99
        rows, ref = await llm.conversation(m, llm.Settings(webhook_id="99"), "99")
        self.assertEqual([row["message_id"] for row in rows], ["8", "10", "12"])
        self.assertEqual(ref["speaker_id"], "99")
        self.assertEqual(rows[1]["reply_to"], "8")

    async def test_api_success_error_retry_and_local_quota(self):
        real_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"
            key.write_text("test-only-key")
            settings = llm.Settings(key_path=str(key), reply_chars=40)
            for status in (200, 429, 503):
                calls = []
                def handler(request):
                    calls.append(request)
                    self.assertNotIn("test-only-key", str(request.url))
                    return httpx.Response(status, json={"modelVersion":"gemini-3.1-flash-lite", "candidates": [{"content": {"parts": [{"text": "あ" * 20}]}}]})
                def client(**kwargs):
                    return real_client(transport=httpx.MockTransport(handler), **kwargs)
                with patch.object(llm.httpx, "AsyncClient", client), patch.object(llm, "reserve", AsyncMock(return_value=True)), \
                        patch.object(llm.limits, "remember", AsyncMock()), patch.object(llm.limits, "record_usage", AsyncMock()):
                    if status == 200:
                        result, _, _ = await llm.generate(settings, "test", 1)
                        self.assertEqual(result, "あ" * 20)
                    else:
                        with self.assertRaises(llm.GeminiError):
                            await llm.generate(settings, "test", 1)
                self.assertEqual(len(calls), 2 if status == 503 else 1)
            with patch.object(llm, "reserve", AsyncMock(return_value=False)):
                with self.assertRaises(llm.GeminiError) as exc:
                    await llm.generate(settings, "test", 1)
                self.assertEqual(exc.exception.status, "local_quota")

    async def test_bad_config_fails_closed(self):
        with patch.object(llm.Settings, "load", side_effect=ValueError("bad")):
            self.assertFalse((await llm.Chat().reload(force=True)).enabled)


@unittest.skipUnless(os.getenv("LLM_TEST_DATABASE_URL"), "requires disposable llm_test database")
class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_claim_and_durable_limits(self):
        url = os.environ["LLM_TEST_DATABASE_URL"]
        self.assertTrue(url.endswith("/llm_test"))
        with patch.dict(os.environ, DATABASE_URL=url):
            async with await llm.database() as conn:
                await conn.execute((Path(__file__).resolve().parent.parent / "migrations/072_add_llm_requests.sql").read_text())
                await conn.execute((Path(__file__).resolve().parent.parent / "migrations/073_add_llm_quota_windows.sql").read_text())
                await conn.execute("TRUNCATE llm_requests CASCADE")
                await conn.execute("TRUNCATE llm_quota_blocks")
            settings = llm.Settings(requests_per_minute=2, requests_per_day=2)
            claims = await asyncio.gather(*(llm.claim(1, settings) for _ in range(5)))
            self.assertEqual(sum(claims), 1)
            reservations = await asyncio.gather(*(llm.reserve(1, settings) for _ in range(5)), return_exceptions=True)
            self.assertEqual(sum(result is True for result in reservations), 2)
            self.assertEqual(sum(isinstance(result, llm.QuotaError) for result in reservations), 3)
            with self.assertRaises(llm.QuotaError):
                await llm.reserve(1, settings)
            await llm.finish(1, "sent", reply_id=2, usage={"promptTokenCount": 10, "candidatesTokenCount": 5})
            async with await llm.database() as conn:
                row = await (await conn.execute("SELECT status, attempts, reply_id, input_tokens FROM llm_requests WHERE message_id=1")).fetchone()
                self.assertEqual(row, ("sent", 2, 2, 10))
                await conn.execute("UPDATE llm_api_attempts SET attempted_at=now()-interval '2 days' WHERE message_id=1")
            self.assertTrue(await llm.reserve(1, settings))


if __name__ == "__main__":
    unittest.main()
