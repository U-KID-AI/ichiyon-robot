"""Private corpus parsing, identity, ranking and bounded SQLite retrieval checks."""
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.services.llm_corpus import IndexedPersona, build_corpus, classify, parse_export
from bot.services.llm_persona import Persona, intent


def record(mid, text, channel="雑談", extra=""):
    return ("=" * 50 + "\n日時: 2026-11-08 12:00:00 JST\nサーバー: 試験\n"
            + "チャンネル: " + channel + "\nメッセージID: " + str(mid) + "\n" + extra
            + "=" * 50 + "\n" + text + "\n\n")


class CorpusTests(unittest.TestCase):
    def test_situation_and_diverse_rejections(self):
        from bot.services.llm_style import situation, diverse
        self.assertEqual(situation('真剣な相談、魔王の話ではない'), 'serious')
        self.assertEqual(situation('じゃあ任せた', '今日は魔王になる設定'), 'playful')
        self.assertEqual(situation('オレカの魔王の入手方法を教えて'), 'open')
        rows=[{'reply':x} for x in ('いや、無理','知らない','いいね','そうなんだ')]
        self.assertEqual([r['reply'] for r in diverse(rows,3)], ['いや、無理','いいね','そうなんだ'])
        self.assertEqual(diverse(rows[:2], 4), rows[:2])  # Real expressions are not banned.
        self.assertEqual(diverse(rows, 4, avoid=('いいね',)), [rows[0], rows[3], rows[1]])

    def test_fallback_retains_longer_real_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.txt'
            output = Path(directory) / 'corpus.sqlite3'
            longer = '手持ちを確認してから組めるデッキを考えるといいと思う。' * 3
            source.write_text(record(100, 'そうね') + record(101, longer), encoding='utf-8')
            build_corpus(source, output, 2)
            persona = Persona.load(output)
            self.assertIn(longer, [row['reply'] for row in persona.fallback_examples])
            self.assertLessEqual(len(persona.fallback_examples), 24)
            result = persona.retrieve('デッキの選び方を詳しく説明して', 4)
            self.assertIn(longer, [row['reply'] for row in result])
            self.assertTrue(all('未収録' in row['context'] for row in result))
    def test_multiline_unicode_empty_and_metadata(self):
        text = record(100, "一行目\n\n二行目🙂\n", extra="返信先ID: 99\n添付: example.png\n") + record(101, "")
        rows = list(parse_export(text))
        self.assertEqual(rows[0]["body"], "一行目\n\n二行目🙂\n")
        self.assertEqual(rows[0]["reply_to"], "99")
        self.assertEqual(rows[1]["body"], "")
        self.assertEqual("".join(row["header"] + row["raw_body"] for row in rows), text)

    def test_reject_duplicate_bad_header_and_other_author(self):
        for text in (record(100, "a") * 2, record(100, "a", extra="著者ID: 123\n"),
                     record(100, "a") + "=" * 50 + "\n日時: broken\n"):
            with self.assertRaises(ValueError):
                list(parse_export(text))

    def test_context_labels(self):
        self.assertEqual(intent("草元素のキャラが来た"), "chat")
        self.assertNotEqual(intent("おはようDMの話"), "greeting")
        self.assertEqual(intent("うす"), "greeting")
        self.assertEqual(classify("<:fixture:12345>")[0], "symbol")
        self.assertEqual(classify("https://example.com")[0], "url")
        self.assertEqual(classify("そう")[1], 65)

    def test_import_search_preservation_and_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.txt"
            output = Path(directory) / "corpus.sqlite3"
            text = (record(100, "原神のガチャは天井まで引くか迷う", "原神")
                    + record(101, "ガチャからMP枠ばっかり出る", "パズドラ")
                    + record(102, "うす") + record(103, "")
                    + record(104, "https://example.com") + record(105, "そうね")
                    + record(106, "ネメのデッキでいいと思う", "デッキ"))
            source.write_text(text, encoding="utf-8")
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            report = build_corpus(source, output, 7)
            self.assertEqual(report["count"], 7)
            self.assertTrue(report["lossless_reconstruction"])
            self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())
            persona = Persona.load(output)
            self.assertIsInstance(persona, IndexedPersona)
            self.assertEqual(persona.count, 7)
            self.assertTrue(persona.fallback_examples)
            with patch('bot.services.llm_corpus.open_index') as connect:
                connect.return_value.execute.side_effect = sqlite3.OperationalError('interrupted')
                fallback = persona.retrieve('今日はどう？', 4)
                self.assertTrue(fallback)
                self.assertTrue(all(row['message_id'] in {'100','101','102','105','106'} for row in fallback))
                self.assertNotIn('https://example.com', [row['reply'] for row in fallback])
            self.assertEqual(persona.retrieve("原神のガチャ", 1)[0]["message_id"], "100")
            self.assertEqual(persona.retrieve("おはよう", 1)[0]["reply"], "うす")
            self.assertNotIn("うす", [r["reply"] for r in persona.retrieve("おはよう", 2, avoid=("うす",))])
            with sqlite3.connect(output) as c:
                self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 7)
                plan = c.execute("EXPLAIN QUERY PLAN SELECT * FROM messages WHERE category='chat' ORDER BY quality DESC,id DESC LIMIT 24").fetchall()
                self.assertIn("messages_category", str(plan))
                c.execute("UPDATE metadata SET value='123' WHERE key='author_id'")
            c.close()
            with self.assertRaises(ValueError):
                Persona.load(output)
            with self.assertRaises(ValueError):
                build_corpus(source, output, 7)

    def test_reject_wrong_count(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.txt"
            source.write_text(record(100, "fixture"), encoding="utf-8")
            with self.assertRaises(ValueError):
                build_corpus(source, Path(directory) / "bad.sqlite3", 2)


if __name__ == "__main__":
    unittest.main()
