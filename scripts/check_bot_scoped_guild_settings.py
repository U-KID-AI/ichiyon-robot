import sys
from pathlib import Path
from typing import Any, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


MIGRATION_PATH = PROJECT_ROOT / "migrations" / "031_scope_shared_guild_settings_by_bot.sql"


def read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def record(results: List[Tuple[str, bool, Any]], name: str, ok: bool, detail: Any = "") -> None:
    results.append((name, ok, detail))
    print("[{0}] {1} - {2}".format("OK" if ok else "NG", name, detail))


def contains_all(source: str, snippets: List[str]) -> bool:
    return all(snippet in source for snippet in snippets)


def main() -> int:
    results: List[Tuple[str, bool, Any]] = []

    repository_requirements = [
        (
            "bot/repositories/feature_flags.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "WHERE bot_id = %s AND guild_id = %s",
                "ON CONFLICT (bot_id, guild_id, feature_key)",
            ],
        ),
        (
            "bot/repositories/mention_reactions.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "where = [\"bot_id = %s\", \"guild_id = %s\"]",
                "ON CONFLICT (bot_id, special_effect_tag_id, target_type, target_id)",
            ],
        ),
        (
            "bot/repositories/auto_reactions.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "where = [\"bot_id = %s\", \"guild_id = %s\"]",
                "WHERE bot_id = %s AND guild_id = %s AND id = %s",
            ],
        ),
        (
            "bot/repositories/auto_posts.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "where = [\"bot_id = %s\", \"guild_id = %s\"]",
                "AND bot_id = %s",
            ],
        ),
        (
            "bot/repositories/ng_words.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "where = [\"bot_id = %s\", \"guild_id = %s\"]",
                "WHERE bot_id = %s AND guild_id = %s AND id = %s",
            ],
        ),
        (
            "bot/repositories/special_effects.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "where = [\"bot_id = %s\", \"guild_id = %s\"]",
                "ON CONFLICT (bot_id, special_effect_tag_id, target_type, target_id)",
            ],
        ),
        (
            "bot/repositories/mention_limited_effects.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "where = [\"l.bot_id = %s\", \"l.guild_id = %s\"",
                "ON CONFLICT (bot_id, guild_id, discord_user_id, effect_tag_id)",
            ],
        ),
        (
            "bot/repositories/modes.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "where = [\"bot_id = %s\", \"guild_id = %s\"]",
                "ON CONFLICT (bot_id, guild_id)",
                "ON CONFLICT (bot_id, guild_id, mode_id, period_key)",
            ],
        ),
        (
            "bot/repositories/counters.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "ON CONFLICT (bot_id, guild_id, count_key)",
                "ON CONFLICT (bot_id, guild_id, counter_id)",
            ],
        ),
        (
            "bot/repositories/reaction_thresholds.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "where = [\"bot_id = %s\", \"guild_id = %s\"]",
                "ON CONFLICT (bot_id, guild_id, rule_id, message_id, emoji_key, threshold)",
            ],
        ),
        (
            "bot/repositories/schedule_templates.py",
            [
                "self.bot_id = bot_id or config.BOT_INSTANCE_ID",
                "WHERE bot_id = %s AND guild_id = %s",
                "lower(name) = lower(%s)",
            ],
        ),
    ]

    for path, snippets in repository_requirements:
        source = read(path)
        record(results, "{0} is bot scoped".format(path), contains_all(source, snippets), path)

    admin_requirements = [
        "admin/auto_posts.py",
        "admin/auto_reactions.py",
        "admin/mention_reactions.py",
        "admin/mention_limited_effects.py",
        "admin/modes.py",
        "admin/ng_words_db.py",
        "admin/reaction_thresholds.py",
        "admin/special_effects.py",
        "admin/schedule_templates.py",
    ]
    for path in admin_requirements:
        source = read(path)
        record(
            results,
            "{0} uses selected bot for access and repositories".format(path),
            "selected_bot_id(request)" in source and "current_selected_bot_id()" in source,
            path,
        )

    x_updates = read("admin/x_updates.py")
    record(
        results,
        "x update list rows receives explicit bot_id",
        "def list_watch_rows(bot_id: str, guild_id: str" in x_updates
        and "selected_bot_id(request)" not in x_updates.split("def list_watch_rows", 1)[1].split("def row_is_hidden_test_data", 1)[0],
        "admin/x_updates.py",
    )

    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    migration_snippets = [
        "idx_feature_flags_bot_guild_feature_unique",
        "idx_mention_reactions_bot_guild_reaction_key_unique",
        "idx_ng_words_bot_guild_word_unique",
        "idx_special_effect_tags_bot_guild_name_unique",
        "idx_special_effect_assignments_bot_tag_target_unique",
        "idx_counters_bot_guild_count_key_unique",
        "idx_counter_states_bot_guild_counter_unique",
        "idx_modes_bot_guild_mode_key_unique",
        "idx_mode_states_bot_guild_unique",
        "idx_mode_trigger_history_bot_guild_mode_period_unique",
        "idx_mention_limited_effects_bot_guild_user_tag_unique",
        "idx_reaction_threshold_events_bot_guild_rule_message_emoji_unique",
    ]
    record(
        results,
        "migration 031 declares bot scoped uniqueness",
        contains_all(migration, migration_snippets),
        MIGRATION_PATH.name,
    )
    forbidden_data_ops = ["DELETE FROM", "UPDATE ", "TRUNCATE", "DROP TABLE", "DROP COLUMN"]
    record(
        results,
        "migration 031 does not delete or rewrite table data",
        not any(token in migration.upper() for token in forbidden_data_ops),
        forbidden_data_ops,
    )

    voice_lines = read("bot/repositories/voice_lines.py")
    record(
        results,
        "voice lines remain bot and guild scoped",
        "WHERE bot_id = %s" in voice_lines
        and "AND guild_id = %s" in voice_lines
        and "ON CONFLICT (bot_id, guild_id)" in voice_lines,
        "voice_lines",
    )

    ok_count = sum(1 for _, ok, _ in results if ok)
    print("{0}/{1} OK".format(ok_count, len(results)))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
