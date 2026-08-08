import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot import messages
from bot.services.interaction_panel import mention_text_is_empty
from bot.services.runtime_db import get_message_guild_id


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FakeBot:
    def event(self, func):
        return func


class FakeUser:
    def __init__(self, user_id="999"):
        self.id = int(user_id)
        self.bot = True

    def __eq__(self, other):
        return int(getattr(other, "id", -1)) == self.id


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class FakeMessage:
    def __init__(self, command_text, bot_user, author_bot=False, mention=True):
        mention_text = "<@{0}>".format(bot_user.id)
        if mention:
            self.content = mention_text if command_text is None else "{0} {1}".format(mention_text, command_text)
            self.mentions = [bot_user]
        else:
            self.content = str(command_text or "")
            self.mentions = []
        self.author = SimpleNamespace(id=1234, bot=author_bot)
        self.guild = SimpleNamespace(id="guild-a")
        self.channel = FakeChannel()


def load_main_routing_functions():
    source = (ROOT_DIR / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {"handle_empty_mention_message", "on_message"}:
            selected.append(ast.get_source_segment(source, node))
    namespace = {
        "bot": FakeBot(),
        "discord": SimpleNamespace(Message=object),
        "messages": messages,
        "mention_text_is_empty": mention_text_is_empty,
        "get_message_guild_id": get_message_guild_id,
        "config": SimpleNamespace(DATA_BACKEND="db"),
    }
    exec("\n\n".join(selected), namespace)
    return namespace


async def main_async():
    results = []
    bot_user = FakeUser()
    messages._bot = SimpleNamespace(user=bot_user)
    namespace = load_main_routing_functions()
    events = []

    def command_from_message(message):
        return messages.get_mention_command_text(message) or ""

    async def fake_music_links(message, command_text):
        events.append(("music_links", command_text))
        return False

    async def fake_youtube_n_pull(message, command_text):
        events.append(("youtube_n_pull", command_text))
        return False

    async def fake_voice(message, command_text):
        events.append(("voice", command_text))
        return command_text in {"入って", "もしもししよ"}

    async def fake_developer(message, command_text):
        events.append(("developer", command_text))
        return False

    async def fake_shortcut(message, command_text):
        events.append(("shortcut", command_text))
        return command_text == "ニコロデオン"

    async def fake_panel(message, command_text):
        source_text = command_text if command_text is not None else getattr(message, "content", "")
        events.append(("panel", source_text))
        return source_text in {"音楽", "ゲーム", "音声", "SE", "パネル"}

    async def fake_tts(message, command_text):
        events.append(("tts", command_text))

    async def fake_db_runtime(message):
        command_text = command_from_message(message)
        events.append(("db_runtime", command_text))
        if command_text == "":
            await message.channel.send("森羅万象って終わってなかったっけ")
            return True
        return False

    async def fake_false_message(message):
        events.append(("mention_fallback", command_from_message(message)))
        return False

    async def fake_false_word(message):
        events.append(("word_response", command_from_message(message)))
        return False

    async def fake_false_mode(message):
        events.append(("mode", command_from_message(message)))
        return False

    namespace.update(
        {
            "handle_mention_music_links": fake_music_links,
            "handle_youtube_n_pull_command": fake_youtube_n_pull,
            "handle_voice_command": fake_voice,
            "handle_developer_command": fake_developer,
            "handle_mention_shortcut_command": fake_shortcut,
            "handle_context_panel_command": fake_panel,
            "maybe_enqueue_tts": fake_tts,
            "handle_db_runtime_message": fake_db_runtime,
            "contains_ng_word": lambda content: False,
            "handle_mention_message": fake_false_message,
            "handle_word_response": fake_false_word,
            "handle_db_reaction_threshold": lambda reaction: None,
            "hayusu": SimpleNamespace(handle_mode_message=fake_false_mode, maybe_start_hayusu_mode=fake_false_mode),
        }
    )

    async def run(command_text):
        events.clear()
        message = FakeMessage(command_text, bot_user)
        await namespace["on_message"](message)
        return message, list(events)

    async def run_standalone(content):
        events.clear()
        message = FakeMessage(content, bot_user, mention=False)
        await namespace["on_message"](message)
        return message, list(events)

    message, trace = await run(None)
    results.append(check("empty mention uses DB runtime", trace == [("db_runtime", "")], trace))
    results.append(check("empty mention sends existing DB response only", len(message.channel.sent) == 1 and "森羅万象" in message.channel.sent[0][0][0]))

    _, trace = await run("入って")
    results.append(check("non-empty voice text reaches voice handler", ("voice", "入って") in trace and ("db_runtime", "入って") not in trace, trace))

    _, trace = await run("音楽")
    results.append(check("music text reaches explicit panel first", trace == [("panel", "音楽")], trace))

    _, trace = await run("ゲーム")
    results.append(check("game text reaches explicit panel first", trace == [("panel", "ゲーム")], trace))

    _, trace = await run("音声")
    results.append(check("audio text reaches explicit panel first", trace == [("panel", "音声")], trace))

    _, trace = await run("SE")
    results.append(check("SE text reaches explicit audio panel first", trace == [("panel", "SE")], trace))

    _, trace = await run("パネル")
    results.append(check("root panel mention reaches explicit panel first", trace == [("panel", "パネル")], trace))

    _, trace = await run_standalone("音楽")
    results.append(check("standalone music opens panel", trace == [("panel", "音楽")], trace))

    _, trace = await run_standalone("ゲーム")
    results.append(check("standalone game opens panel", trace == [("panel", "ゲーム")], trace))

    _, trace = await run_standalone("SE")
    results.append(check("standalone SE opens panel", trace == [("panel", "SE")], trace))

    _, trace = await run_standalone("パネル")
    results.append(check("standalone root opens panel", trace == [("panel", "パネル")], trace))

    _, trace = await run_standalone("音楽っぽい")
    results.append(check("standalone partial panel command falls through", ("panel", "音楽っぽい") in trace and any(event[0] == "db_runtime" for event in trace), trace))

    _, trace = await run("ニコロデオン")
    results.append(check("shortcut text reaches mention shortcut after panel miss", ("panel", "ニコロデオン") in trace and ("shortcut", "ニコロデオン") in trace, trace))

    _, trace = await run("デッキ エルフ")
    results.append(check("deck command falls through to DB runtime", ("db_runtime", "デッキ エルフ") in trace, trace))

    _, trace = await run("もしもししょ")
    results.append(check("unknown non-empty mention reaches fallback DB runtime", ("db_runtime", "もしもししょ") in trace, trace))

    _, trace = await run("ニコロデオンって何？")
    results.append(check("shortcut suffix misses exact shortcut and falls back", ("shortcut", "ニコロデオンって何？") in trace and ("db_runtime", "ニコロデオンって何？") in trace, trace))

    return all(results)


def main():
    return 0 if asyncio.run(main_async()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
