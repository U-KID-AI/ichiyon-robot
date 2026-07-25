import asyncio
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from bot import config
from bot.repositories.tts_settings import TTSSettingsRepository, default_tts_settings
from bot.services.voice.session import (
    clear_voice_runtime_state,
    get_voice_session_state,
    voice_state_key,
)
from bot.services.voice.text_normalizer import (
    normalize_tts_text,
    stable_pitch_for_user,
    truncate_for_tts,
)
import bot.services.voice.tts as tts_module
from bot.services.voice.tts import (
    activate_tts_session,
    classify_tts_command,
    maybe_enqueue_tts,
    should_tts_read_message,
    stop_tts_session,
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


class FailingConnection:
    def cursor(self):
        raise RuntimeError("table missing")


class FakeAuthor:
    def __init__(self, user_id="1001", bot=False):
        self.id = user_id
        self.bot = bot


class FakeGuild:
    def __init__(self, guild_id="guild-tts"):
        self.id = guild_id
        self.voice_client = None


class FakeChannel:
    def __init__(self, channel_id="channel-tts"):
        self.id = channel_id
        self.messages = []

    async def send(self, content):
        self.messages.append(str(content))


class FakeAttachment:
    def __init__(self, content_type):
        self.content_type = content_type


class FakeMessage:
    def __init__(self, content="hello", guild_id="guild-tts", channel_id="channel-tts", author=None, attachments=None, webhook_id=None):
        self.content = content
        self.guild = FakeGuild(guild_id)
        self.channel = FakeChannel(channel_id)
        self.author = author or FakeAuthor()
        self.attachments = attachments or []
        self.webhook_id = webhook_id


async def main() -> int:
    results = []
    guild_id = "guild-tts"
    other_guild_id = "guild-tts-other"
    clear_voice_runtime_state(guild_id)
    clear_voice_runtime_state(other_guild_id)

    results.append(check("tts command start", classify_tts_command("読み上げ開始") == "start"))
    results.append(check("tts command stop", classify_tts_command(" 読み上げ 停止 ") == "stop"))
    results.append(check("tts command unknown", classify_tts_command("音楽") is None))

    results.append(check("default settings bot/guild", default_tts_settings("ichiyon", guild_id)["guild_id"] == guild_id))
    fallback = TTSSettingsRepository(FailingConnection(), bot_id="irsia").get(guild_id)
    results.append(check("repository get fallback", fallback["bot_id"] == "irsia" and fallback["enabled"] is True))

    activate_tts_session(guild_id, "channel-a")
    session = get_voice_session_state(guild_id)
    results.append(check("activate binds channel", session.bound_text_channel_id == "channel-a" and session.tts_enabled))
    activate_tts_session(guild_id, "channel-b", force_enabled=True)
    results.append(check("explicit start rebinds", get_voice_session_state(guild_id).bound_text_channel_id == "channel-b"))
    results.append(check("state key scoped", voice_state_key(guild_id) != voice_state_key(other_guild_id)))

    normal = FakeMessage("こんにちは https://example.com <@123> **太字**", guild_id=guild_id, channel_id="channel-b")
    results.append(check("should read normal", should_tts_read_message(normal, None)))
    results.append(check("ignore bot", not should_tts_read_message(FakeMessage(author=FakeAuthor(bot=True)), None)))
    results.append(check("ignore webhook", not should_tts_read_message(FakeMessage(webhook_id="webhook"), None)))
    results.append(check("ignore mention command", not should_tts_read_message(normal, "読み上げ開始")))
    results.append(check("ignore code-only", not should_tts_read_message(FakeMessage("```python\nprint(1)\n```"), None)))
    results.append(check("ignore url-only", not should_tts_read_message(FakeMessage("https://youtu.be/example"), None)))

    normalized = normalize_tts_text(normal.content, [], 200)
    results.append(check("normalize url", "URL" in normalized and "https://" not in normalized))
    results.append(check("normalize mention", "メンション" in normalized and "<@" not in normalized))
    results.append(check("normalize markdown", "*" not in normalized))
    results.append(check("attachment image", normalize_tts_text("", ["image/png"], 100) == "画像"))
    results.append(check("attachment file", normalize_tts_text("", ["application/pdf"], 100) == "ファイル"))
    results.append(check("truncate suffix", truncate_for_tts("あ" * 20, 8).endswith("以下省略")))

    p1 = stable_pitch_for_user("1234567890", 0.06)
    p2 = stable_pitch_for_user("1234567890", 0.06)
    p3 = stable_pitch_for_user("1234567891", 0.06)
    results.append(check("stable pitch same user", p1 == p2))
    results.append(check("stable pitch bucket range", -0.2 <= p1 <= 0.2 and -0.2 <= p3 <= 0.2))

    original_worker = tts_module._tts_worker

    async def fake_worker(message, guild_id_arg, generation_id):
        return None

    tts_module._tts_worker = fake_worker
    await maybe_enqueue_tts(normal, None)
    results.append(check("enqueue bound channel", len(get_voice_session_state(guild_id).tts_queue) == 1))
    await maybe_enqueue_tts(FakeMessage("別チャンネル", guild_id=guild_id, channel_id="channel-x"), None)
    results.append(check("ignore unbound channel", len(get_voice_session_state(guild_id).tts_queue) == 1))
    await stop_tts_session(guild_id)
    results.append(check("stop clears queue", not get_voice_session_state(guild_id).tts_enabled and len(get_voice_session_state(guild_id).tts_queue) == 0))
    await maybe_enqueue_tts(normal, None)
    results.append(check("stopped does not enqueue", len(get_voice_session_state(guild_id).tts_queue) == 0))
    tts_module._tts_worker = original_worker

    migration = Path("migrations/038_add_bot_tts_settings.sql").read_text(encoding="utf-8")
    destructive = any(word in migration.upper() for word in ("DROP ", "DELETE ", "TRUNCATE "))
    results.append(check("migration is additive", "CREATE TABLE IF NOT EXISTS bot_tts_settings" in migration and not destructive))
    results.append(check("migration primary key scope", "PRIMARY KEY (bot_id, guild_id)" in migration))
    results.append(check("migration ducking defaults", "ducking_enabled BOOLEAN NOT NULL DEFAULT FALSE" in migration))
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")
    results.append(check("compose has voicevox profile", "voicevox-engine:" in compose and "- voicevox" in compose))
    results.append(check("compose does not publish voicevox port", "50021:50021" not in compose))
    results.append(check("compose pins voicevox image", "voicevox/voicevox_engine:cpu-ubuntu24.04-0.25.0" in compose))
    results.append(check("bot receives voicevox env", compose.count("VOICEVOX_ENGINE_URL") >= 2 and compose.count("VOICEVOX_TIMEOUT_SECONDS") >= 2))
    results.append(check("env documents voicevox", "VOICEVOX_ENGINE_IMAGE=" in env_example and "VOICEVOX_ENGINE_URL=" in env_example))

    print("tts runtime checks: {0}/{1}".format(sum(1 for value in results if value), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
