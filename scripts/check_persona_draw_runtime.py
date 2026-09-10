import asyncio
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin import persona_draws as admin_persona_draws
from bot.repositories.persona_draws import normalize_persona_trigger
from bot.services import persona_draw


MIGRATION_PATH = PROJECT_ROOT / "migrations" / "044_add_persona_draws.sql"
SEED_PATH = PROJECT_ROOT / "scripts" / "seed_joker_persona_draw.py"
ADMIN_TEMPLATE_PATH = PROJECT_ROOT / "admin" / "templates" / "persona_draw_form.html"
ADMIN_ROUTE_PATH = PROJECT_ROOT / "admin" / "persona_draws.py"
MAIN_PATH = PROJECT_ROOT / "main.py"


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


class FakeChannel:
    def __init__(self) -> None:
        self.sent: List[str] = []

    async def send(self, content=None, **kwargs):
        self.sent.append(str(content or ""))
        return SimpleNamespace(id=len(self.sent))


class FakeMessage:
    def __init__(self, content: str, guild_id: str = "guild-a", author_bot: bool = False) -> None:
        self.content = content
        self.guild = SimpleNamespace(id=guild_id)
        self.author = SimpleNamespace(bot=author_bot, id=123)
        self.channel = FakeChannel()


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeRepo:
    draws: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
    candidates: List[Dict[str, Any]] = []
    reroll_lines: List[Dict[str, Any]] = []
    bot_ids: List[str] = []

    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.bot_id = bot_id or "ichiyon"
        self.bot_ids.append(self.bot_id)

    def find_by_trigger(self, guild_id: str, trigger_text: str):
        return self.draws.get((guild_id, normalize_persona_trigger(trigger_text)))

    def list_candidates(self, guild_id: str, draw_id: int, enabled: Optional[bool] = None):
        return list(self.candidates)

    def list_reroll_lines(self, guild_id: str, draw_id: int, enabled: Optional[bool] = None):
        return list(self.reroll_lines)


class FakeFeatureFlagRepository:
    enabled = True

    def __init__(self, connection, bot_id: Optional[str] = None) -> None:
        self.bot_id = bot_id or "ichiyon"

    def is_enabled(self, guild_id: str, feature_key: str, default: bool = False) -> bool:
        return self.enabled


async def run_handler(content: str, command_text: Optional[str], author_bot: bool = False) -> Tuple[bool, List[str]]:
    original_connection = persona_draw.get_connection
    original_repo = persona_draw.PersonaDrawRepository
    original_flags = persona_draw.FeatureFlagRepository
    try:
        persona_draw.get_connection = lambda: FakeConnection()
        persona_draw.PersonaDrawRepository = FakeRepo
        persona_draw.FeatureFlagRepository = FakeFeatureFlagRepository
        message = FakeMessage(content, author_bot=author_bot)
        handled = await persona_draw.handle_persona_draw_message(message, command_text)
        return handled, message.channel.sent
    finally:
        persona_draw.get_connection = original_connection
        persona_draw.PersonaDrawRepository = original_repo
        persona_draw.FeatureFlagRepository = original_flags


def seeded_draw(probability: int = 0, enabled: bool = True) -> Dict[str, Any]:
    return {
        "id": 1,
        "name": "ジョーカー",
        "trigger_text": "正体を見せろ！",
        "trigger_key": normalize_persona_trigger("正体を見せろ！"),
        "reroll_enabled": enabled,
        "reroll_probability_percent": probability,
        "max_rerolls": 1,
        "enabled": True,
    }


async def runtime_checks(results: List[Tuple[str, bool, Any]]) -> None:
    FakeRepo.draws = {("guild-a", normalize_persona_trigger("正体を見せろ！")): seeded_draw(0)}
    FakeFeatureFlagRepository.enabled = True
    FakeRepo.candidates = [{"body": "呪文を詠唱するヒヒ！"}, {"body": "池袋の犯人！"}]
    FakeRepo.reroll_lines = [{"body": "違うな…"}]

    handled, sent = await run_handler("正体を見せろ！", None)
    record(results, "plain trigger is handled", handled and len(sent) == 1 and sent[0] in {"呪文を詠唱するヒヒ！", "池袋の犯人！"}, sent)

    handled, sent = await run_handler("<@999> 正体を見せろ！", "正体を見せろ！")
    record(results, "mention trigger is consumed before normal mention", handled and len(sent) == 1, sent)

    handled, sent = await run_handler("<@999> 音楽", "音楽")
    record(results, "explicit music command is not persona", not handled and sent == [], sent)

    handled, sent = await run_handler("正体を見せろ！", None, author_bot=True)
    record(results, "bot author never triggers persona draw", not handled and sent == [], sent)

    handled, sent = await run_handler("正体を見せろ！って何", None)
    record(results, "partial sentence does not trigger persona draw", not handled and sent == [], sent)

    FakeFeatureFlagRepository.enabled = False
    handled, sent = await run_handler("正体を見せろ！", None)
    record(results, "feature flag off returns to normal routing", not handled and sent == [], sent)
    FakeFeatureFlagRepository.enabled = True

    FakeRepo.draws = {("guild-a", normalize_persona_trigger("正体を見せろ！")): seeded_draw(100)}
    rng = random.Random(1)
    messages = persona_draw.build_persona_draw_messages(
        seeded_draw(100),
        [{"body": "A"}, {"body": "B"}],
        [{"body": "違うな…"}, {"body": "これじゃない…"}],
        rng,
    )
    record(results, "reroll sends first result, reroll line, second result", len(messages) == 3 and messages[0] != messages[2], messages)

    messages = persona_draw.build_persona_draw_messages(seeded_draw(100), [{"body": "A"}], [{"body": "違うな…"}], random.Random(2))
    record(results, "single candidate reroll is safe", messages.count("A") >= 1 and len(messages) >= 2, messages)

    messages = persona_draw.build_persona_draw_messages(seeded_draw(100), [], [{"body": "違うな…"}], random.Random(3))
    record(results, "zero candidates is safe no-op", messages == [], messages)


def main() -> int:
    results: List[Tuple[str, bool, Any]] = []
    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    seed = SEED_PATH.read_text(encoding="utf-8")
    template = ADMIN_TEMPLATE_PATH.read_text(encoding="utf-8")
    admin_route = ADMIN_ROUTE_PATH.read_text(encoding="utf-8")
    main_source = MAIN_PATH.read_text(encoding="utf-8")

    record(
        results,
        "trigger normalization handles full-width and spacing",
        normalize_persona_trigger(" 正体を見せろ！ ") == "正体を見せろ!",
        normalize_persona_trigger(" 正体を見せろ！ "),
    )
    record(
        results,
        "migration creates only new persona tables",
        "CREATE TABLE IF NOT EXISTS persona_draws" in migration
        and "CREATE TABLE IF NOT EXISTS persona_draw_candidates" in migration
        and "CREATE TABLE IF NOT EXISTS persona_draw_reroll_lines" in migration
        and "DROP TABLE" not in migration.upper()
        and "TRUNCATE" not in migration.upper()
        and "DELETE FROM" not in migration.upper()
        and "UPDATE " not in migration.upper(),
        "migration 044",
    )
    record(
        results,
        "migration scopes trigger uniqueness by bot and guild",
        "ON persona_draws (bot_id, guild_id, trigger_key)" in migration,
        "trigger unique index",
    )
    record(
        results,
        "admin uses bot/guild editor permission, not user-management permission",
        "can_access_guild" in admin_route
        and "role_allows(server[\"role\"], \"editor\")" in admin_route
        and "can_manage_users" not in admin_route,
        "admin permissions",
    )
    form = admin_persona_draws.collect_form("ジョーカー", "正体を見せろ！", "on", "10", "1", "on", "A\nB", "違うな…")
    record(
        results,
        "admin form collects reroll settings",
        not admin_persona_draws.validate_form(form)
        and form["reroll_enabled"] is True
        and form["reroll_probability_percent"] == 10
        and form["max_rerolls"] == 1,
        form,
    )
    record(
        results,
        "admin template exposes Japanese fields",
        "呼び出しフレーズ" in template
        and "抽選候補" in template
        and "再抽選確率" in template
        and "再抽選セリフ" in template,
        "template fields",
    )
    record(
        results,
        "seed is idempotent and contains Joker preset",
        "DEFAULT_NAME = \"ジョーカー\"" in seed
        and "正体を見せろ！" in seed
        and "呪文を詠唱するヒヒ！" in seed
        and "DEFAULT_REROLL_PROBABILITY_PERCENT = 10" in seed
        and "replace_candidates" in seed
        and "replace_reroll_lines" in seed,
        "seed_joker_persona_draw",
    )
    persona_line = main_source.find("if await handle_persona_draw_message(message, command_text):")
    panel_line = main_source.find("handle_context_panel_command(message, command_text)")
    db_line = main_source.find('if config.DATA_BACKEND == "db" and get_message_guild_id(message) is not None:', persona_line)
    record(
        results,
        "main routes persona before normal DB runtime",
        persona_line >= 0 and panel_line >= 0 and db_line >= 0 and persona_line < panel_line < db_line,
        (persona_line, panel_line, db_line),
    )
    asyncio.run(runtime_checks(results))

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
