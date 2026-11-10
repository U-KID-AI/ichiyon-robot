"""Offline multi-turn exposure checks, not simulated model-quality scores."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
from bot.services.llm_context import make_prompt, prepare_context, retrieval_context, response_mode, context_excerpt, format_reply
from bot.services import llm_chat as llm

# Synthetic participants for routing/context tests; never personality training data.
TURNS = ['おはよう','今日はのんびりしたい','冷蔵庫がラスボスだったw','俺が給食大臣だ',
 'ゲームで負けた','あの作品の最終回よかった','さっきのは間違い、明日だった','俺は好きなキャラで遊びたい',
 '失敗して落ち込んでる','そんなw','お年玉ちょうだい','ゲームの方が謝るべきだろw',
 'それで次どうする','今日は休もう','面白いね','いや普通に楽しかった',
 'ありがとう','もう全部敵のモード入ってるじゃん','お嬢様言葉で会話してみよう','冷笑について教えてください',
 'そんな設定だったのかw','好みは違うけどどっちもいい','まあそれでいいか','おやすみ']


def row(mid, text, bot=False):
    return {'message_id':str(mid),'speaker_id':'webhook' if bot else str(mid%3),
            'speaker_name':'fixture','robot':bot,'text':text,'reply_to':None}


class ContextTests(unittest.TestCase):
    def test_chat_punctuation_does_not_cut_content_or_change_quotes(self):
        self.assertEqual(format_reply('「例です。」これは普通の文。次の文。'), '「例です。」これは普通の文\n次の文')
        self.assertEqual(format_reply('```\n文。\n```'), '```\n文。\n```')

    def test_24_turns_do_not_amplify_generated_style_or_search_topic(self):
        history=[];exposures=[]
        for turn,text in enumerate(TURNS):
            latest=row(turn*2+1,text);history.append(latest)
            prompt=json.loads(make_prompt(latest,history[-16:],None,[{'reply':'fixture example'}]))
            context=prompt['conversation_context'];bots=[r for r in context if r['robot']]
            exposures.append(sum(len(r['text']) for r in bots))
            self.assertLessEqual(len(bots),3)
            self.assertLessEqual(exposures[-1],480)
            self.assertNotIn('BOT_ONLY_TOPIC',retrieval_context(history[-16:]))
            self.assertEqual(prompt['latest_message'],latest)
            self.assertTrue(all(r['source']=='generated_context_not_style' for r in bots))
            # Deliberately increasing synthetic lectures; this does not call Gemini.
            history.append(row(turn*2+2,('BOT_ONLY_TOPIC 自分の判断を反省すべきだ。'*(turn+10)),True))
        self.assertEqual(max(exposures[8:16]),max(exposures[16:24]))

    def test_modes_are_hints_not_low_output_token_limits(self):
        for text in ('何で？','ゲームに謝ってほしいw','そんな','ちょっと疲れた'):
            self.assertEqual(response_mode(text),'ordinary')
        self.assertEqual(response_mode('事故る理由を3点に分けて詳しく説明して'),'explanation')
        self.assertEqual(response_mode('お嬢様言葉で会話してみよう'),'style_play')
        self.assertEqual(llm.Settings().max_output_tokens,4096)

    def test_reference_and_small_budgets_are_explicit_excerpts(self):
        for limit in range(1,25):
            text,cut=context_excerpt('長い文脈'*100,limit)
            self.assertLessEqual(len(text),limit);self.assertTrue(cut)
        original=row(1,'過去の生成文'*100,True)
        result=json.loads(make_prompt(row(2,'その話の続き'),[original],original,[]))
        self.assertLessEqual(len(result['referenced_message']['text']),240)
        self.assertTrue(result['referenced_message']['excerpted'])
        self.assertEqual(original['text'],'過去の生成文'*100)


class OutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_or_oversize_generation_is_not_cut_or_retried(self):
        real_client=httpx.AsyncClient
        with tempfile.TemporaryDirectory() as directory:
            key=Path(directory)/'key';key.write_text('fixture')
            for body,reason,error in [('文'*80,'STOP','reply_too_long'),('途中','MAX_TOKENS','incomplete_response')]:
                calls=[]
                def handler(request):
                    calls.append(request)
                    return httpx.Response(200,json={'modelVersion':'gemini-3.1-flash-lite',
                        'candidates':[{'finishReason':reason,'content':{'parts':[{'text':body}]}}]})
                def client(**kwargs):return real_client(transport=httpx.MockTransport(handler),**kwargs)
                with patch.object(llm.httpx,'AsyncClient',client),patch.object(llm,'reserve',AsyncMock(return_value=True)), \
                     patch.object(llm.limits,'record_usage',AsyncMock()):
                    with self.assertRaises(llm.GeminiError) as caught:
                        await llm.generate(llm.Settings(key_path=str(key),reply_chars=40),'test',1)
                    self.assertEqual(caught.exception.status,error)
                self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
