"""Disk-backed retrieval of an operator-supplied export. No Discord API access."""
import collections
import hashlib
import json
import math
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from bot.services.llm_persona import PERSON_ID, intent, terms

HEADER = re.compile(
    r"^={50}\n日時: (?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) JST\n"
    r"サーバー: (?P<server>[^\n]+)\nチャンネル: (?P<channel>[^\n]+)\n"
    r"メッセージID: (?P<id>\d+)\n(?P<extra>(?:(?!={50}\n)[^\n]*\n)*)={50}\n", re.M)
GAME = re.compile(r"原神|シャドバ|シャドウバース|デッキ|ネメシス|ロイヤル|ガチャ|マイクラ|ポケモン|ゲーム|対戦|ランク|エルフ|ドラゴン|ヴァンプ|ウィッチ|ネクロ|ビショップ|AF|アグロ")
ANSWER = re.compile(r"と思う|はず|たぶん|多分|かも|でいい|だから|なら|による|だよ|そうね|そうだね")
STOP = {"して", "した", "する", "です", "ます", "って", "った", "こと", "これ", "それ", "ある", "から", "ので", "んな", "ない", "たい", "ょう", "そう", "どう"}
TOPICS = {
    "原神": r"原神|螺旋|ナヒーダ|パイモン|天理",
    "シャドバ": r"シャドバ|シャドウバース|ネメ|ロイヤル|ウィッチ|エルフ|ヴァンプ|ネクロ|ビショップ|デッキ|ルムマ",
    "パズドラ": r"パズドラ",
    "スタレ": r"スターレイル|スタレ",
    "オレカ": r"オレカ",
    "マイクラ": r"マイクラ|minecraft",
    "ポケモン": r"ポケモン",
    "ウルトラランブル": r"ウルトラランブル|ヒロアカ",
    "アイマス": r"アイマス|学マス",
    "モンハン": r"モンハン|ワイルズ",
}


def topic_for(text, channel=""):
    for value in (text, channel):
        for name, pattern in TOPICS.items():
            if re.search(pattern, value, re.I):
                return name
    return ""


def parse_export(text):
    """Keep raw body/header as well as parsed fields for lossless verification."""
    matches = list(HEADER.finditer(text))
    if not matches or text[:matches[0].start()].strip("\ufeff\n"):
        raise ValueError("unrecognized export header")
    seen = set()
    previous = None
    for i, match in enumerate(matches):
        date = datetime.strptime(match["date"], "%Y-%m-%d %H:%M:%S")
        mid = int(match["id"])
        if mid in seen or not 0 < mid < 2**63:
            raise ValueError("duplicate or invalid message id")
        if previous and date < previous:
            raise ValueError("export is not chronological")
        seen.add(mid)
        previous = date
        extra = {}
        for line in match["extra"].splitlines():
            key, separator, value = line.partition(": ")
            if not separator:
                raise ValueError("unrecognized metadata")
            if key in ("著者ID", "ユーザーID", "発言者ID") and value != PERSON_ID:
                raise ValueError("author mismatch")
            extra.setdefault(key, []).append(value)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw_body = text[match.end():end]
        # A header-like fragment in a body is ambiguous and must be inspected.
        if re.search(r"^={50}\n日時:", raw_body, re.M):
            raise ValueError("malformed or ambiguous record boundary")
        body = raw_body[:-2] if raw_body.endswith("\n\n") else raw_body
        reply_to = extra.get("返信先ID", [None])[0]
        yield {"id": mid, "date": match["date"] + "+09:00", "server": match["server"],
               "channel": match["channel"], "body": body, "raw_body": raw_body,
               "header": match.group(), "extra": extra,
               "reply_to": int(reply_to) if reply_to else None}


def classify(body, channel="", reply_to=None):
    text = body.strip()
    tags = []
    if not text:
        return "empty", 0, ["empty"]
    if re.fullmatch(r"(?:https?://\S+\s*)+", text):
        return "url", 0, ["url"]
    if text.startswith(("!", "/")):
        return "command", 5, ["command"]
    if text.startswith((">", "```")) or len(text) > 600:
        return "quote", 10, ["quote_or_paste"]
    visible = re.sub(r"<a?:\w+:\d+>", "", text).strip()
    if not visible or not re.search(r"[ぁ-んァ-ヶ一-龯ｦ-ﾟa-zA-Z0-9]", visible):
        return "symbol", 20, ["symbol"]
    kind = intent(text)
    if kind != "chat":
        tags.append(kind)
    if GAME.search(text + " " + channel):
        tags.append("game")
    if reply_to:
        tags.append("reply")
    if ANSWER.search(text) and kind != "question":
        tags.append("answer_cue")
    if re.search(r"<@|さん|くん|先生|ちゃん", text):
        tags.append("address")
    if len(text) <= 5:
        tags.append("short")
    category = kind if kind != "chat" else ("reply" if "reply" in tags or "answer_cue" in tags else "chat")
    quality = 90 if 6 <= len(text) <= 120 else 65
    return category, quality, tags or ["chat"]


SCHEMA = """
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE messages(
    id INTEGER PRIMARY KEY, author_id TEXT NOT NULL, timestamp TEXT NOT NULL,
    server TEXT NOT NULL, channel TEXT NOT NULL, body TEXT NOT NULL,
    raw_body TEXT NOT NULL, header TEXT NOT NULL, extra TEXT NOT NULL,
    reply_to INTEGER, category TEXT NOT NULL, quality INTEGER NOT NULL, tags TEXT NOT NULL,
    topic TEXT NOT NULL
);
CREATE INDEX messages_category ON messages(category, quality DESC, id DESC);
CREATE INDEX messages_quality ON messages(quality DESC, id DESC);
CREATE INDEX messages_topic ON messages(topic, quality DESC, id DESC);
CREATE INDEX messages_short ON messages(quality DESC,id DESC) WHERE length(body)<=5 AND quality>=20;
CREATE VIRTUAL TABLE corpus_fts USING fts5(grams, topic, content='', tokenize='unicode61');
CREATE VIRTUAL TABLE corpus_vocab USING fts5vocab(corpus_fts, 'row');
"""


def build_corpus(source, output, expected_count):
    source, output = Path(source), Path(output)
    if output.exists() or source.resolve() == output.resolve():
        raise ValueError("output must be a new file")
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    # Normalization is only for parsing; the original hash covers exact bytes.
    normalized = text.replace("\r\n", "\n")
    conn = sqlite3.connect(output)
    output.chmod(0o600)
    counts = collections.Counter()
    tag_counts = collections.Counter()
    topic_counts = collections.Counter()
    style_counts = collections.Counter()
    channels = collections.Counter()
    servers = collections.Counter()
    repetitions = collections.Counter()
    endings = collections.Counter()
    pronouns = collections.Counter()
    lengths = []
    samples = collections.defaultdict(list)
    first = last = None
    reconstructed = hashlib.sha256()
    try:
        conn.executescript(SCHEMA)
        for row in parse_export(normalized):
            category, quality, tags = classify(row["body"], row["channel"], row["reply_to"])
            topic = topic_for(row["body"], row["channel"])
            conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                row["id"], PERSON_ID, row["date"], row["server"], row["channel"], row["body"],
                row["raw_body"], row["header"], json.dumps(row["extra"], ensure_ascii=False),
                row["reply_to"], category, quality, json.dumps(tags), topic))
            grams = terms(row["body"])
            conn.execute("INSERT INTO corpus_fts(rowid,grams,topic) VALUES (?,?,?)", (row["id"], " ".join(sorted(grams)), topic))
            reconstructed.update((row["header"] + row["raw_body"]).encode())
            counts[category] += 1
            tag_counts.update(tags)
            topic_counts[topic] += 1
            channels[row["server"] + " / " + row["channel"]] += 1
            servers[row["server"]] += 1
            first = first or row["date"]
            last = row["date"]
            body = row["body"].strip()
            for name, pattern in {"period": "。", "comma": "、", "question_mark": "[?？]",
                                  "exclamation": "[!！‼]", "ellipsis": "…|\\.\\.\\.",
                                  "polite_ending": r"(?:です|ます|ました|ません|ください)[。!！?？]*$",
                                  "custom_emoji": r"<a?:\w+:\d+>"}.items():
                if re.search(pattern, body):
                    style_counts[name] += 1
            if body:
                lengths.append(len(body))
                repetitions[body] += 1
                endings[body[-3:]] += 1
            for pronoun in ("俺", "僕", "私", "自分", "わたし", "ぼく", "ワイ"):
                if re.search(r"(?<![ァ-ヶ一-龯])" + re.escape(pronoun) + r"(?![ァ-ヶ一-龯]|くん|さん|ちゃん)", body):
                    pronouns[pronoun] += 1
                    key = "pronoun_" + pronoun
                    if len(samples[key]) < 12 and len(body) <= 150:
                        samples[key].append({"id": row["id"], "text": body})
            if len(samples[category]) < 20 and len(body) <= 180:
                samples[category].append({"id": row["id"], "text": body})
        count = sum(counts.values())
        if count != expected_count:
            raise ValueError(f"record count mismatch: expected {expected_count}, found {count}")
        if reconstructed.hexdigest() != hashlib.sha256(normalized.lstrip("\ufeff").encode()).hexdigest():
            raise ValueError("lossless reconstruction check failed")
        lengths.sort()
        report = {"count": count, "nonempty": len(lengths), "source_sha256": hashlib.sha256(raw).hexdigest(),
                  "bytes": len(raw), "first": first, "last": last, "servers": dict(servers),
                  "channels": dict(channels), "categories": dict(counts),
                  "tags": dict(tag_counts), "topics": dict(topic_counts), "style_counts": dict(style_counts),
                  "mean_chars": round(sum(lengths) / max(1, len(lengths)), 2),
                  "median_chars": lengths[len(lengths) // 2] if lengths else 0,
                  "p90_chars": lengths[int(len(lengths) * .9)] if lengths else 0,
                  "pronoun_messages": dict(pronouns), "common_replies": repetitions.most_common(40),
                  "common_endings": endings.most_common(30), "samples": dict(samples),
                  "author_verification": "operator_attribution_no_author_field",
                  "replacement_characters": normalized.count("\ufffd"), "lossless_reconstruction": True}
        metadata = {"version": "2", "author_id": PERSON_ID, "source": "user_supplied_export",
                    "count": str(count), "source_sha256": report["source_sha256"], "guide": ""}
        conn.executemany("INSERT INTO metadata VALUES (?,?)", metadata.items())
        conn.execute("INSERT INTO corpus_fts(corpus_fts) VALUES ('optimize')")
        conn.commit()
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        return report
    finally:
        conn.close()


def open_index(path, deadline):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=.2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA cache_size=-2048")
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
    return conn


class IndexedPersona:
    def __init__(self, path):
        self.path = Path(path)
        conn = open_index(self.path, time.monotonic() + .5)
        try:
            meta = dict(conn.execute("SELECT key,value FROM metadata").fetchall())
        finally:
            conn.close()
        if meta.get("author_id") != PERSON_ID or meta.get("source") != "user_supplied_export" or meta.get("version") != "2":
            raise ValueError("invalid corpus identity")
        self.guide = meta["guide"]
        if len(self.guide) > 6000:
            raise ValueError("guide too long")
        self.count = int(meta["count"])
        self.last_search_ms = 0

    def retrieve(self, text, limit, context="", avoid=()):
        started = time.monotonic()
        conn = open_index(self.path, started + .5)
        candidates = {}
        weights = {}
        try:
            wanted = intent(text)
            if re.search(r"何使|どれ|どこ|おすすめ|選べば|すべき|したらいい", text):
                wanted = "question"
            topic = topic_for(text) or topic_for(context)
            search_text = re.sub(r"原神|シャドウバース|シャドバ|パズドラ|スターレイル|スタレ|オレカ|マイクラ|ポケモン|ウルトラランブル|アイマス|モンハン", "", text) if topic else text
            tokens = sorted(terms(search_text) - STOP, key=lambda t: (-len(t), t))[:80]
            if tokens and wanted != "greeting":
                placeholders = ",".join("?" for _ in tokens)
                frequent = conn.execute(f"SELECT term,doc FROM corpus_vocab WHERE term IN ({placeholders}) ORDER BY doc LIMIT 12", tokens).fetchall()
                weights = {row["term"]: math.log(1 + self.count / (1 + row["doc"])) for row in frequent}
                query = " OR ".join('"' + row["term"].replace('"', '""') + '"' for row in frequent)
                if query:
                    query = "grams:(" + query + ")"
                    if topic:
                        query = 'topic:"' + topic + '" AND (' + query + ')'
                    ids = conn.execute("SELECT rowid FROM corpus_fts WHERE corpus_fts MATCH ? ORDER BY rank LIMIT 48", (query,)).fetchall()
                    if ids:
                        placeholders = ",".join("?" for _ in ids)
                        for item in conn.execute(f"SELECT * FROM messages WHERE id IN ({placeholders})", [row[0] for row in ids]):
                            candidates[item["id"]] = item
            category = "reply" if wanted == "question" else wanted
            if classify(text)[0] == "symbol":
                category = "symbol"
            if len(text) <= 5:
                for row in conn.execute("SELECT * FROM messages WHERE length(body)<=5 AND quality>=20 ORDER BY quality DESC,id DESC LIMIT 24"):
                    candidates[row["id"]] = row
            if topic:
                for row in conn.execute("SELECT * FROM messages WHERE topic=? ORDER BY quality DESC,id DESC LIMIT 24", (topic,)):
                    candidates[row["id"]] = row
            for row in conn.execute("SELECT * FROM messages WHERE category=? ORDER BY quality DESC,id DESC LIMIT 24", (category,)):
                candidates[row["id"]] = row
            for row in conn.execute("SELECT * FROM messages ORDER BY quality DESC,id DESC LIMIT 12"):
                candidates.setdefault(row["id"], row)
            current_terms, context_terms = terms(text), terms(context)
            scored = []
            for row in candidates.values():
                if time.monotonic() > started + .5:
                    break
                body = row["body"].strip()
                if row["quality"] < 20 or not body or body in avoid:
                    continue
                if wanted == "greeting" and row["category"] != "greeting":
                    continue
                tags = json.loads(row["tags"])
                body_terms = terms(body)
                score = sum(weights.get(t, .1) for t in current_terms & body_terms)
                score += min(3, len(context_terms & body_terms)) * .3 + row["quality"] / 100
                if topic and row["topic"] != topic:
                    score -= 8
                if wanted == "greeting" and row["category"] != "greeting":
                    score -= 12
                if row["category"] == category:
                    score += 10 if wanted == "question" else 2
                if wanted == "question" and "answer_cue" in tags:
                    score += 1
                if wanted == "question" and row["category"] == "question":
                    score -= 3
                if bool(GAME.search(text + context)) == ("game" in tags):
                    score += .5
                if len(text) <= 12 and len(body) <= 20:
                    score += 1
                if len(text) <= 5 and len(body) <= 5:
                    score += 5
                if len(body) > 160:
                    score -= 2
                scored.append((score, row))
            selected, seen = [], set()
            for score, row in sorted(scored, key=lambda item: (-item[0], -item[1]["id"])):
                body = re.sub(r"https?://\S+|<@!?\d+>", "", row["body"]).strip()[:300]
                if not body or body in seen:
                    continue
                seen.add(body)
                selected.append({"message_id": str(row["id"]),
                                 "context": "本人の発言。相手の発言本文は未収録。分類=" + row["category"] + " 話題=" + row["topic"],
                                 "reply": body})
                if len(selected) >= limit:
                    break
            return selected
        except sqlite3.OperationalError as exc:
            if "interrupted" not in str(exc):
                raise
            return []
        finally:
            self.last_search_ms = round((time.monotonic() - started) * 1000, 2)
            conn.close()
