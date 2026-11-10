"""Rachel migration boundaries and single-attempt transport; no live calls."""
import json
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import discord
import httpx
from bot.services import llm_chat as chat, llm_webhook as webhook
from bot.services.llm_persona import Persona


def message(mid=100):
    return NS(id=mid, guild=NS(id=1392174489609179327), channel=NS(id=1569571831894114354),
              author=NS(id=42, bot=False, display_name='participant'), webhook_id=None,
              reference=None, content='hello')


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    def test_config_and_persona_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.json'
            with patch.object(chat, 'CONFIG_PATH', path):
                self.assertFalse(chat.Settings.load().enabled)
                for changes in ({'channel_id':'1568969564769747006'}, {'model':'gemini-3.6-flash'},
                                {'guild_id':1392174489609179327}, {'enabled':True}):
                    path.write_text(json.dumps(asdict(replace(chat.Settings(), **changes))))
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        chat.Settings.load()
        with self.assertRaises(ValueError):
            Persona({'author_id':'748965361486921831', 'source':'direct_provision'})

    async def test_routes_and_loop_prevention(self):
        settings=chat.Settings(enabled=True)
        for cid in (1568969564769747006, 1569403401446359040, 1417484793674731622):
            m=message();m.channel.id=cid
            self.assertFalse(settings.allows(m))
        m=message();m.guild=None
        self.assertFalse(settings.allows(m))
        m=message();m.channel=unittest.mock.MagicMock(spec=discord.Thread);m.channel.id=1569571831894114354
        self.assertFalse(settings.allows(m))
        worker=chat.Chat()
        with patch.object(worker,'reload',AsyncMock(return_value=settings)), patch.object(chat.limits,'check',AsyncMock()) as quota:
            for bot, wid in ((True,None),(True,99),(False,99)):
                m=message();m.author.bot=bot;m.webhook_id=wid
                await worker.handle(m)
            quota.assert_not_awaited()
            self.assertEqual(worker.queue.qsize(),0)

    async def test_history_cutoff_webhook_and_reply(self):
        current=message(100)
        own=message(98);own.author.bot=True;own.webhook_id=99
        other=message(97);other.webhook_id=22
        old=message(80)
        async def history(**kwargs):
            for item in (current, own, own, other, old):yield item
        current.channel.history=history
        current.reference=NS(message_id=98,channel_id=current.channel.id)
        current.channel.fetch_message=AsyncMock(return_value=own)
        rows, ref=await chat.conversation(current,chat.Settings(history_after='90',webhook_id='99'),'99')
        self.assertEqual([r['message_id'] for r in rows],['98','100'])
        self.assertTrue(ref['robot'])
        self.assertEqual(ref['speaker_id'],'42')

    async def test_webhook_identity_mentions_and_no_retry(self):
        real_client=httpx.AsyncClient
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'webhook.json'
            path.write_text(json.dumps({'id':'99','channel_id':'1569571831894114354',
                'name':'レイチェル竈門にぼ','avatar':'hash','token':'private-test-token'}),encoding='utf-8')
            settings=chat.Settings(enabled=True,webhook_id='99',webhook_path=str(path))
            for failure in ('success','timeout','403','wrong_identity'):
                calls=[]
                def handler(request):
                    calls.append(request)
                    body=json.loads(request.content)
                    self.assertEqual(body['allowed_mentions'],{'parse':[],'replied_user':False})
                    self.assertNotIn('username',body)
                    self.assertNotIn('avatar_url',body)
                    self.assertEqual(request.url.params['wait'],'true')
                    if failure=='timeout':raise httpx.ReadTimeout(str(request.url))
                    if failure=='403':return httpx.Response(403)
                    return httpx.Response(200,json={'id':'123','webhook_id':'22' if failure=='wrong_identity' else '99',
                        'channel_id':'1569571831894114354'})
                def client(**kwargs):return real_client(transport=httpx.MockTransport(handler),**kwargs)
                with patch.object(webhook.httpx,'AsyncClient',client):
                    if failure=='success':
                        self.assertEqual((await webhook.send(settings,message(),'@everyone test')).id,'123')
                    else:
                        with self.assertRaises(chat.GeminiError) as error:
                            await webhook.send(settings,message(),'test')
                        self.assertNotIn('private-test-token',str(error.exception))
                self.assertEqual(len(calls),1)

    async def test_model_request_and_provider_resolution(self):
        real_client=httpx.AsyncClient
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'key';path.write_text('test-key')
            for version in ('gemini-3.1-flash-lite','gemini-3.8-flash'):
                calls=[]
                def handler(request):
                    calls.append(request)
                    self.assertEqual(request.url.path,'/v1beta/models/gemini-3.1-flash-lite:generateContent')
                    generation=json.loads(request.content)['generationConfig']
                    self.assertEqual(generation,{'thinkingConfig':{'thinkingLevel':'low'},'maxOutputTokens':4096})
                    return httpx.Response(200,json={'modelVersion':version,'candidates':[{'content':{'parts':[{'text':'ok'}]}}]})
                def client(**kwargs):return real_client(transport=httpx.MockTransport(handler),**kwargs)
                with patch.object(chat.httpx,'AsyncClient',client),patch.object(chat,'reserve',AsyncMock(return_value=True)), \
                     patch.object(chat.limits,'record_usage',AsyncMock()):
                    if version=='gemini-3.1-flash-lite':
                        self.assertEqual((await chat.generate(chat.Settings(key_path=str(path)),'test',1))[0],'ok')
                    else:
                        with self.assertRaises(chat.GeminiError):
                            await chat.generate(chat.Settings(key_path=str(path)),'test',1)
                self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
