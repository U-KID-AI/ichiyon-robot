"""Private, explicitly supplied examples. Never collects Discord history."""
import collections
import json
import re
from functools import lru_cache
from pathlib import Path

PERSON_ID = "748965361486921831"


def terms(text):
    text = re.sub(r"https?://\S+|<[^>]+>", "", text.lower())[:1600]
    words = re.findall(r"[a-z0-9]{2,}|[ぁ-んァ-ヶ一-龯ー]+", text)
    return set(words) | {word[i:i + 2] for word in words for i in range(len(word) - 1)}


def intent(text):
    if re.search(r"[?？]|どう|なぜ|何|いつ|教えて", text):
        return "question"
    if re.search(r"笑|[wｗ]{2,}|草|冗談", text):
        return "joke"
    if re.search(r"おはよ|こんにちは|こんばんは|おやすみ", text):
        return "greeting"
    if re.search(r"嬉し|悲し|つら|やった|疲れ", text):
        return "emotion"
    return "chat"


class Persona:
    def __init__(self, data=None):
        data = data or {}
        if data and (str(data.get("author_id")) != PERSON_ID or data.get("source") != "direct_provision"):
            raise ValueError("persona must be directly provided by the configured person")
        self.guide = data.get("guide", "")
        if not isinstance(self.guide, str) or len(self.guide) > 6000:
            raise ValueError("guide must be text of at most 6000 characters")
        rows = data.get("examples", [])
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError("at most 2000 examples per persona file")
        self.examples = []
        self.index = collections.defaultdict(set)
        self.intents = collections.defaultdict(list)
        seen = set()
        for row in rows:
            if str(row.get("author_id", PERSON_ID)) != PERSON_ID:
                raise ValueError("example author mismatch")
            reply, context = row.get("reply", ""), row.get("context", "")
            if not isinstance(reply, str) or not isinstance(context, str) or not 1 <= len(reply) <= 1000 or len(context) > 1000:
                raise ValueError("invalid example text")
            if (context, reply) in seen:
                continue
            seen.add((context, reply))
            # Commands, URL-only messages and copied quotations are poor style examples.
            if reply.startswith(("!", "/", ">")) or re.fullmatch(r"https?://\S+", reply):
                continue
            i = len(self.examples)
            tokens = terms(context + " " + reply)
            self.examples.append({"context": context, "reply": reply, "terms": tokens})
            for token in tokens:
                self.index[token].add(i)
            self.intents[intent(context)].append(i)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        if path.stat().st_size > 4_000_000:
            raise ValueError("persona file exceeds 4 MB")
        return _cached_persona(str(path), path.stat().st_mtime_ns, path.stat().st_size)

    def retrieve(self, text, limit):
        tokens = terms(text)
        scores = collections.Counter()
        for token in tokens:
            for i in sorted(self.index.get(token, ()))[:64]:
                scores[i] += 1 / max(1, len(self.index[token])) ** 0.5
        similar = self.intents[intent(text)][:64]
        for i in similar:
            scores[i] += 1.5
        for i in range(min(16, len(self.examples))):
            scores[i] += 0.1
        for i in scores:
            if len(self.examples[i]["reply"]) < 4:
                scores[i] *= 0.25
        return [{"context": self.examples[i]["context"], "reply": self.examples[i]["reply"]}
                for i, _ in scores.most_common(limit)]


@lru_cache(maxsize=2)
def _cached_persona(path, modified_ns, size):
    return Persona(json.loads(Path(path).read_text(encoding="utf-8")))
