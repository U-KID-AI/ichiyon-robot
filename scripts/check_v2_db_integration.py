import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.db import get_connection
from bot.repositories import CounterRepository, FeatureFlagRepository, ModeRepository
from bot.repositories.base import fetch_all, fetch_one, json_dumps


BOT_USER_ID = "999999999999999999"
CHECK_PREFIX = "integration_check"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


class FakeUser:
    def __init__(self, user_id: str, name: str) -> None:
        self.id = int(user_id)
        self.name = name
        self.display_name = name
        self.mention = "<@{0}>".format(user_id)


class FakeGuild:
    def __init__(self, guild_id: str) -> None:
        self.id = guild_id


class FakeChannel:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


class FakeMessage:
    def __init__(
        self,
        guild_id: str,
        content: str,
        channel: Optional[FakeChannel] = None,
        mentions: Optional[List[FakeUser]] = None,
    ) -> None:
        self.guild = FakeGuild(guild_id)
        self.content = content
        self.channel = channel or FakeChannel()
        self.author = FakeUser("111111111111111111", "IntegrationUser")
        self.mentions = mentions or []
        self.reactions = []

    async def add_reaction(self, emoji) -> None:
        self.reactions.append(str(emoji))


class IntegrationChecker:
    def __init__(self, database_url: str, guild_id: str, upsert_guild: bool, guild_name: str) -> None:
        self.database_url = database_url
        self.guild_id = guild_id
        self.upsert_guild = upsert_guild
        self.guild_name = guild_name
        self.results = []
        self.saved_feature_flags = {}
        self.saved_mode_enabled = []
        self.saved_mode_state = None

    def add_result(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(CheckResult(name, ok, detail))

    def run(self) -> bool:
        with get_connection(self.database_url) as connection:
            try:
                self.save_mutable_state(connection)
                self.check_guild(connection)
                self.check_presets(connection)
                self.ensure_runtime_check_data(connection)
                connection.commit()
                self.check_feature_flags(connection)
                connection.commit()
                self.check_period_conditions(connection)
                connection.commit()
                self.check_runtime(connection)
                connection.commit()
            finally:
                self.restore_mutable_state(connection)
                connection.commit()

        self.print_results()
        return all(result.ok for result in self.results)

    def fetch_one(self, connection, sql: str, params: Tuple[Any, ...]) -> Optional[Dict[str, Any]]:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_one(cursor)

    def fetch_all(self, connection, sql: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_all(cursor)

    def save_mutable_state(self, connection) -> None:
        self.saved_feature_flags = {}
        for key in ("mention_reactions", "reactions", "ng_words"):
            self.saved_feature_flags[key] = self.fetch_one(
                connection,
                "SELECT * FROM feature_flags WHERE guild_id = %s AND feature_key = %s",
                (self.guild_id, key),
            )
        self.saved_mode_enabled = self.fetch_all(
            connection,
            "SELECT id, enabled FROM modes WHERE guild_id = %s",
            (self.guild_id,),
        )
        self.saved_mode_state = self.fetch_one(
            connection,
            "SELECT * FROM mode_states WHERE guild_id = %s",
            (self.guild_id,),
        )

    def restore_mutable_state(self, connection) -> None:
        with connection.cursor() as cursor:
            for key, row in self.saved_feature_flags.items():
                if row is None:
                    cursor.execute(
                        "DELETE FROM feature_flags WHERE guild_id = %s AND feature_key = %s",
                        (self.guild_id, key),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO feature_flags (
                            guild_id, feature_key, enabled, updated_by_discord_user_id
                        )
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (guild_id, feature_key) DO UPDATE
                        SET enabled = EXCLUDED.enabled,
                            updated_by_discord_user_id = EXCLUDED.updated_by_discord_user_id,
                            updated_at = NOW()
                        """,
                        (
                            self.guild_id,
                            key,
                            row["enabled"],
                            row.get("updated_by_discord_user_id"),
                        ),
                    )

            for row in self.saved_mode_enabled:
                cursor.execute(
                    "UPDATE modes SET enabled = %s, updated_at = NOW() WHERE id = %s",
                    (row["enabled"], row["id"]),
                )

            if self.saved_mode_state is None:
                cursor.execute("DELETE FROM mode_states WHERE guild_id = %s", (self.guild_id,))
            else:
                cursor.execute(
                    """
                    INSERT INTO mode_states (
                        guild_id, current_mode_id, active_until, pseudo_offline_until,
                        shikocchi_count, period_states_json, state_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::JSONB, %s::JSONB)
                    ON CONFLICT (guild_id) DO UPDATE
                    SET current_mode_id = EXCLUDED.current_mode_id,
                        active_until = EXCLUDED.active_until,
                        pseudo_offline_until = EXCLUDED.pseudo_offline_until,
                        shikocchi_count = EXCLUDED.shikocchi_count,
                        period_states_json = EXCLUDED.period_states_json,
                        state_json = EXCLUDED.state_json,
                        updated_at = NOW()
                    """,
                    (
                        self.guild_id,
                        self.saved_mode_state.get("current_mode_id"),
                        self.saved_mode_state.get("active_until"),
                        self.saved_mode_state.get("pseudo_offline_until"),
                        self.saved_mode_state.get("shikocchi_count") or 0,
                        json_dumps(self.saved_mode_state.get("period_states_json") or {}),
                        json_dumps(self.saved_mode_state.get("state_json") or {}),
                    ),
                )

    def check_guild(self, connection) -> None:
        row = self.fetch_one(
            connection,
            "SELECT * FROM guilds WHERE guild_id = %s",
            (self.guild_id,),
        )
        if row is not None:
            self.add_result("guild exists", True, row.get("name") or "")
            return

        if not self.upsert_guild:
            self.add_result("guild exists", False, "missing; rerun with --upsert-guild for a test guild")
            return

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO guilds (guild_id, name)
                VALUES (%s, %s)
                ON CONFLICT (guild_id) DO UPDATE
                SET name = EXCLUDED.name,
                    updated_at = NOW()
                """,
                (self.guild_id, self.guild_name),
            )
        self.add_result("guild exists", True, "upserted test guild")

    def check_presets(self, connection) -> None:
        checks = [
            (
                "hayusu mode",
                "SELECT 1 FROM modes WHERE guild_id = %s AND mode_key = 'hayusu'",
                (self.guild_id,),
            ),
            (
                "narita mode",
                "SELECT 1 FROM modes WHERE guild_id = %s AND mode_key = 'narita'",
                (self.guild_id,),
            ),
            (
                "hayusu period condition",
                """
                SELECT 1
                FROM mode_trigger_conditions c
                JOIN modes m ON m.id = c.mode_id
                WHERE c.guild_id = %s
                  AND m.mode_key = 'hayusu'
                  AND c.condition_type = 'period_not_triggered'
                """,
                (self.guild_id,),
            ),
            (
                "narita period condition",
                """
                SELECT 1
                FROM mode_trigger_conditions c
                JOIN modes m ON m.id = c.mode_id
                WHERE c.guild_id = %s
                  AND m.mode_key = 'narita'
                  AND c.condition_type = 'period_not_triggered'
                """,
                (self.guild_id,),
            ),
            (
                "shikocchi mode",
                "SELECT 1 FROM modes WHERE guild_id = %s AND mode_key = 'shikocchi'",
                (self.guild_id,),
            ),
            (
                "mini ichiyon tag",
                """
                SELECT 1 FROM special_effect_tags
                WHERE guild_id = %s
                  AND target_type = 'mention_reaction_choice'
                  AND effect_type = 'probability_message'
                  AND additional_text LIKE %s
                """,
                (self.guild_id, "%{match_1}%"),
            ),
            (
                "narita counter delta tag",
                """
                SELECT 1 FROM special_effect_tags
                WHERE guild_id = %s
                  AND effect_type = 'counter_delta'
                  AND effect_config_json->>'counter_key' = 'narita_count'
                """,
                (self.guild_id,),
            ),
            (
                "shikocchi counter set tag",
                """
                SELECT 1 FROM special_effect_tags
                WHERE guild_id = %s
                  AND target_type = 'auto_reaction'
                  AND effect_type = 'counter_set'
                  AND effect_config_json->>'counter_key' = 'shikocchi_count'
                """,
                (self.guild_id,),
            ),
            (
                "destroy special effect tag",
                """
                SELECT 1 FROM special_effect_tags
                WHERE guild_id = %s
                  AND name = '破壊'
                  AND target_type = 'mention_reaction_choice'
                  AND effect_type = 'custom'
                """,
                (self.guild_id,),
            ),
            (
                "omae choice has mini tag",
                """
                SELECT 1
                FROM mention_reactions r
                JOIN mention_reaction_choices c ON c.mention_reaction_id = r.id
                JOIN special_effect_assignments a
                    ON a.target_type = 'mention_reaction_choice'
                    AND a.target_id = c.id
                    AND a.enabled = TRUE
                JOIN special_effect_tags t
                    ON t.id = a.special_effect_tag_id
                    AND t.effect_type = 'probability_message'
                WHERE r.guild_id = %s
                  AND r.reaction_key = 'omae_mo_yona'
                """,
                (self.guild_id,),
            ),
            (
                "shikocchi auto has roll tag",
                """
                SELECT 1
                FROM reactions r
                JOIN special_effect_assignments a
                    ON a.target_type = 'auto_reaction'
                    AND a.target_id = r.id
                    AND a.enabled = TRUE
                JOIN special_effect_tags t
                    ON t.id = a.special_effect_tag_id
                    AND t.effect_type = 'counter_set'
                    AND t.effect_config_json->>'counter_key' = 'shikocchi_count'
                WHERE r.guild_id = %s
                  AND r.trigger_text = %s
                """,
                (self.guild_id, "しこっち"),
            ),
        ]
        for name, sql, params in checks:
            row = self.fetch_one(connection, sql, params)
            self.add_result("preset: {0}".format(name), row is not None)

    def ensure_runtime_check_data(self, connection) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO counters (guild_id, count_key, name, initial_value, reset_type)
                VALUES
                    (%s, %s, %s, 0, 'manual'),
                    (%s, %s, %s, 0, 'manual'),
                    (%s, %s, %s, 0, 'manual'),
                    (%s, %s, %s, 0, 'manual'),
                    (%s, %s, %s, 0, 'manual')
                ON CONFLICT (guild_id, count_key) DO NOTHING
                """,
                (
                    self.guild_id,
                    CHECK_PREFIX + "_delta_count",
                    "integration delta count",
                    self.guild_id,
                    CHECK_PREFIX + "_set_count",
                    "integration set count",
                    self.guild_id,
                    CHECK_PREFIX + "_ng_count",
                    "integration ng count",
                    self.guild_id,
                    CHECK_PREFIX + "_limited_count",
                    "integration limited count",
                    self.guild_id,
                    CHECK_PREFIX + "_mode_count",
                    "integration mode count",
                ),
            )

        mention_id = self.ensure_mention_reaction(connection)
        choice_id = self.ensure_mention_choice(connection, mention_id)
        auto_delta_id = self.ensure_auto_reaction(
            connection,
            CHECK_PREFIX + "_auto_delta",
            "auto delta ok",
            "contains",
            200,
        )
        auto_set_id = self.ensure_auto_reaction(
            connection,
            CHECK_PREFIX + "_auto_set",
            "auto set ok",
            "contains",
            210,
        )
        ng_id = self.ensure_ng_word(connection, CHECK_PREFIX + "_ng")
        delta_tag_id = self.ensure_effect_tag(
            connection,
            CHECK_PREFIX + "_delta_tag",
            "auto_reaction",
            "auto_reaction_triggered",
            "counter_delta",
            {"counter_key": CHECK_PREFIX + "_delta_count", "delta": 1},
        )
        set_tag_id = self.ensure_effect_tag(
            connection,
            CHECK_PREFIX + "_set_tag",
            "auto_reaction",
            "auto_reaction_triggered",
            "counter_set",
            {
                "counter_key": CHECK_PREFIX + "_set_count",
                "value": 1,
                "chance_denominator": 1,
            },
        )
        ng_tag_id = self.ensure_effect_tag(
            connection,
            CHECK_PREFIX + "_ng_tag",
            "ng_word",
            "ng_word_detected",
            "counter_delta",
            {"counter_key": CHECK_PREFIX + "_ng_count", "delta": 1},
        )
        message_tag_id = self.ensure_effect_tag(
            connection,
            CHECK_PREFIX + "_message_tag",
            "mention_reaction_choice",
            "choice_selected",
            "probability_message",
            {"chance_denominator": 1},
            "extra {match_1}",
        )
        limited_tag_id = self.ensure_effect_tag(
            connection,
            CHECK_PREFIX + "_limited_tag",
            "mention_reaction_choice",
            "choice_selected",
            "counter_delta",
            {"counter_key": CHECK_PREFIX + "_limited_count", "delta": 1},
        )
        self.ensure_assignment(connection, delta_tag_id, "auto_reaction", auto_delta_id)
        self.ensure_assignment(connection, set_tag_id, "auto_reaction", auto_set_id)
        self.ensure_assignment(connection, ng_tag_id, "ng_word", ng_id)
        self.ensure_assignment(connection, message_tag_id, "mention_reaction_choice", choice_id)
        self.ensure_limited_effect(connection, limited_tag_id)
        self.ensure_reply_mode(connection)
        self.ensure_offline_mode(connection)

    def ensure_limited_effect(self, connection, tag_id: int) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mention_limited_effects (
                    guild_id, discord_user_id, display_name, effect_tag_id, description, enabled
                )
                VALUES (%s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (guild_id, discord_user_id, effect_tag_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    description = EXCLUDED.description,
                    enabled = TRUE,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    self.guild_id,
                    "111111111111111111",
                    "IntegrationUser",
                    tag_id,
                    "integration check limited effect",
                ),
            )
            return int(cursor.fetchone()[0])

    def ensure_mention_reaction(self, connection) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mention_reactions (
                    guild_id, reaction_key, keyword, match_type, reaction_kind, name,
                    description, admin_only, is_system, is_deletable, config_json, enabled
                )
                VALUES (%s, %s, %s, 'regex', 'random', %s, %s, FALSE, FALSE, TRUE, '{}'::JSONB, TRUE)
                ON CONFLICT (guild_id, reaction_key) DO UPDATE
                SET keyword = EXCLUDED.keyword,
                    match_type = EXCLUDED.match_type,
                    enabled = TRUE,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    self.guild_id,
                    CHECK_PREFIX + "_mention",
                    CHECK_PREFIX + "_mention (.+)",
                    "integration mention",
                    "integration check mention reaction",
                ),
            )
            return int(cursor.fetchone()[0])

    def ensure_mention_choice(self, connection, reaction_id: int) -> int:
        existing = self.fetch_one(
            connection,
            """
            SELECT * FROM mention_reaction_choices
            WHERE guild_id = %s AND mention_reaction_id = %s AND name = %s
            """,
            (self.guild_id, reaction_id, CHECK_PREFIX + "_choice"),
        )
        if existing is not None:
            return int(existing["id"])
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mention_reaction_choices (
                    guild_id, mention_reaction_id, name, body, image_path, appearance_rate, enabled
                )
                VALUES (%s, %s, %s, %s, '', 1, TRUE)
                RETURNING id
                """,
                (
                    self.guild_id,
                    reaction_id,
                    CHECK_PREFIX + "_choice",
                    "mention ok {match_1}",
                ),
            )
            return int(cursor.fetchone()[0])

    def ensure_auto_reaction(
        self,
        connection,
        trigger_text: str,
        response_text: str,
        match_type: str,
        priority: int,
    ) -> int:
        existing = self.fetch_one(
            connection,
            """
            SELECT * FROM reactions
            WHERE guild_id = %s AND trigger_text = %s AND match_type = %s
            """,
            (self.guild_id, trigger_text, match_type),
        )
        if existing is not None:
            return int(existing["id"])
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO reactions (
                    guild_id, trigger_text, response_text, image_path,
                    emoji_internal, match_type, priority, enabled
                )
                VALUES (%s, %s, %s, '', '', %s, %s, TRUE)
                RETURNING id
                """,
                (self.guild_id, trigger_text, response_text, match_type, priority),
            )
            return int(cursor.fetchone()[0])

    def ensure_ng_word(self, connection, word: str) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ng_words (guild_id, word, enabled)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (guild_id, word) DO UPDATE
                SET enabled = TRUE,
                    updated_at = NOW()
                RETURNING id
                """,
                (self.guild_id, word),
            )
            return int(cursor.fetchone()[0])

    def ensure_effect_tag(
        self,
        connection,
        name: str,
        target_type: str,
        trigger_timing: str,
        effect_type: str,
        effect_config: Dict[str, Any],
        additional_text: str = "",
    ) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO special_effect_tags (
                    guild_id, name, description, color, admin_only, enabled, is_deletable,
                    priority, target_type, trigger_timing, effect_type, effect_config_json,
                    additional_text, additional_post_timing, expires_type, cooldown_seconds, cooldown_scope
                )
                VALUES (
                    %s, %s, 'integration check', '#5588CC', FALSE, TRUE, TRUE,
                    500, %s, %s, %s, %s::JSONB,
                    %s, 'effect_success', 'immediate', 0, 'none'
                )
                ON CONFLICT (guild_id, name) DO UPDATE
                SET target_type = EXCLUDED.target_type,
                    trigger_timing = EXCLUDED.trigger_timing,
                    effect_type = EXCLUDED.effect_type,
                    effect_config_json = EXCLUDED.effect_config_json,
                    additional_text = EXCLUDED.additional_text,
                    enabled = TRUE,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    self.guild_id,
                    name,
                    target_type,
                    trigger_timing,
                    effect_type,
                    json_dumps(effect_config),
                    additional_text,
                ),
            )
            return int(cursor.fetchone()[0])

    def ensure_assignment(self, connection, tag_id: int, target_type: str, target_id: int) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO special_effect_assignments (
                    guild_id, special_effect_tag_id, target_type, target_id, enabled
                )
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (special_effect_tag_id, target_type, target_id) DO UPDATE
                SET enabled = TRUE,
                    updated_at = NOW()
                RETURNING id
                """,
                (self.guild_id, tag_id, target_type, target_id),
            )
            return int(cursor.fetchone()[0])

    def ensure_reply_mode(self, connection) -> int:
        mode_id = self.ensure_mode(
            connection,
            CHECK_PREFIX + "_reply_mode",
            "000 integration reply mode",
            "reply",
        )
        self.ensure_mode_trigger(
            connection,
            mode_id,
            "counter_threshold",
            {"counter_key": CHECK_PREFIX + "_mode_count", "operator": ">=", "value": 1},
        )
        self.ensure_mode_exit(connection, mode_id, {"seconds": 120})
        self.ensure_mode_reply_choice(connection, mode_id, "integration reply choice", "reply mode ok")
        return mode_id

    def ensure_offline_mode(self, connection) -> int:
        mode_id = self.ensure_mode(
            connection,
            CHECK_PREFIX + "_offline_mode",
            "000 integration offline mode",
            "offline",
        )
        self.ensure_mode_exit(connection, mode_id, {"seconds": 120})
        return mode_id

    def ensure_mode(self, connection, mode_key: str, name: str, behavior_type: str) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO modes (
                    guild_id, mode_key, name, description, behavior_type,
                    mode_icon_path, enter_message, exit_message, enter_gif_path, exit_gif_path,
                    enter_notify_channel_id, exit_notify_channel_id, reaction_channel_ids,
                    ignore_channel_ids, cooldown_config_json, enabled, admin_only, is_deletable
                )
                VALUES (
                    %s, %s, %s, 'integration check mode', %s,
                    '', '', '', '', '',
                    '', '', '[]'::JSONB,
                    '[]'::JSONB, '{}'::JSONB, FALSE, FALSE, TRUE
                )
                ON CONFLICT (guild_id, mode_key) DO UPDATE
                SET name = EXCLUDED.name,
                    behavior_type = EXCLUDED.behavior_type,
                    updated_at = NOW()
                RETURNING id
                """,
                (self.guild_id, mode_key, name, behavior_type),
            )
            return int(cursor.fetchone()[0])

    def ensure_mode_trigger(self, connection, mode_id: int, condition_type: str, config: Dict[str, Any]) -> int:
        existing = self.fetch_one(
            connection,
            """
            SELECT * FROM mode_trigger_conditions
            WHERE guild_id = %s AND mode_id = %s AND condition_type = %s
            LIMIT 1
            """,
            (self.guild_id, mode_id, condition_type),
        )
        if existing is not None:
            return int(existing["id"])
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_trigger_conditions (
                    guild_id, mode_id, condition_type, condition_config_json, group_operator, enabled
                )
                VALUES (%s, %s, %s, %s::JSONB, 'AND', TRUE)
                RETURNING id
                """,
                (self.guild_id, mode_id, condition_type, json_dumps(config)),
            )
            return int(cursor.fetchone()[0])

    def ensure_mode_exit(self, connection, mode_id: int, config: Dict[str, Any]) -> int:
        existing = self.fetch_one(
            connection,
            """
            SELECT * FROM mode_exit_conditions
            WHERE guild_id = %s AND mode_id = %s AND condition_type = 'duration'
            LIMIT 1
            """,
            (self.guild_id, mode_id),
        )
        if existing is not None:
            return int(existing["id"])
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_exit_conditions (
                    guild_id, mode_id, condition_type, condition_config_json, enabled
                )
                VALUES (%s, %s, 'duration', %s::JSONB, TRUE)
                RETURNING id
                """,
                (self.guild_id, mode_id, json_dumps(config)),
            )
            return int(cursor.fetchone()[0])

    def ensure_mode_reply_choice(self, connection, mode_id: int, name: str, body: str) -> int:
        existing = self.fetch_one(
            connection,
            """
            SELECT * FROM mode_reply_choices
            WHERE guild_id = %s AND mode_id = %s AND name = %s
            """,
            (self.guild_id, mode_id, name),
        )
        if existing is not None:
            return int(existing["id"])
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_reply_choices (
                    guild_id, mode_id, name, body, image_path, appearance_rate, enabled
                )
                VALUES (%s, %s, %s, %s, '', 1, TRUE)
                RETURNING id
                """,
                (self.guild_id, mode_id, name, body),
            )
            return int(cursor.fetchone()[0])

    def check_feature_flags(self, connection) -> None:
        repository = FeatureFlagRepository(connection)
        repository.set_flag(self.guild_id, "mention_reactions", False, "integration_check")
        off = self.runtime_feature_enabled(connection, "mention_reactions")
        repository.set_flag(self.guild_id, "mention_reactions", True, "integration_check")
        on = self.runtime_feature_enabled(connection, "mention_reactions")
        self.add_result("feature_flags OFF blocks feature", off is False)
        self.add_result("feature_flags ON enables feature", on is True)

    def runtime_feature_enabled(self, connection, feature_key: str) -> bool:
        from bot.services.runtime_db import feature_enabled

        return feature_enabled(connection, self.guild_id, feature_key)

    def check_period_conditions(self, connection) -> None:
        from bot.services.runtime_db import build_mode_period_info
        from bot.services.runtime_db import period_not_triggered_met
        from bot.services.runtime_db import record_mode_period_trigger

        mode_id = self.ensure_mode(connection, CHECK_PREFIX + "_period_mode", "000 integration period mode", "reply")
        self.ensure_mode_trigger(
            connection,
            mode_id,
            "period_not_triggered",
            {"period": "monthly", "reset": "month_start"},
        )
        mode = self.fetch_one(
            connection,
            "SELECT * FROM modes WHERE guild_id = %s AND id = %s",
            (self.guild_id, mode_id),
        )
        condition = self.fetch_one(
            connection,
            """
            SELECT *
            FROM mode_trigger_conditions
            WHERE guild_id = %s AND mode_id = %s AND condition_type = 'period_not_triggered'
            LIMIT 1
            """,
            (self.guild_id, mode_id),
        )
        if mode is None or condition is None:
            self.add_result("period_not_triggered: check data", False, "mode or condition missing")
            return

        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM mode_trigger_history WHERE guild_id = %s AND mode_id = %s",
                (self.guild_id, mode_id),
            )

        before = period_not_triggered_met(connection, self.guild_id, mode, condition)
        record_mode_period_trigger(connection, self.guild_id, mode)
        after = period_not_triggered_met(connection, self.guild_id, mode, condition)
        self.add_result("period_not_triggered: 未発動期間はtrue", before is True)
        self.add_result("period_not_triggered: 発動後は同じ期間でfalse", after is False)

        month_info = build_mode_period_info(
            {"period": "monthly", "reset": "month_start"},
            now=datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc),
        )
        day_before_info = build_mode_period_info(
            {"period": "monthly", "reset": {"type": "day", "day": 22}},
            now=datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc),
        )
        day_after_info = build_mode_period_info(
            {"period": "monthly", "day": 22},
            now=datetime(2026, 6, 22, 0, 0, tzinfo=timezone(timedelta(hours=9))),
        )
        self.add_result(
            "period: 月初基準",
            month_info["period_key"] == "monthly:2026-06-01",
            str(month_info["period_key"]),
        )
        self.add_result(
            "period: 22日前は前月22日基準",
            day_before_info["period_key"] == "monthly-day-22:2026-05-22",
            str(day_before_info["period_key"]),
        )
        self.add_result(
            "period: 22日以降は当月22日基準",
            day_after_info["period_key"] == "monthly-day-22:2026-06-22",
            str(day_after_info["period_key"]),
        )

    def check_runtime(self, connection) -> None:
        self.disable_all_modes(connection)
        self.clear_mode_state(connection)
        FeatureFlagRepository(connection).set_flag(self.guild_id, "mention_reactions", False, "integration_check")
        connection.commit()
        off_channel = FakeChannel()
        handled = asyncio.run(
            self.handle_message("<@{0}> {1}_mention alpha".format(BOT_USER_ID, CHECK_PREFIX), off_channel, True)
        )
        self.add_result(
            "runtime: feature flag OFF prevents mention",
            handled is False and off_channel.sent == [],
            str(off_channel.sent),
        )

        FeatureFlagRepository(connection).set_flag(self.guild_id, "mention_reactions", True, "integration_check")
        FeatureFlagRepository(connection).set_flag(self.guild_id, "reactions", True, "integration_check")
        FeatureFlagRepository(connection).set_flag(self.guild_id, "ng_words", True, "integration_check")
        connection.commit()

        self.set_counter(connection, CHECK_PREFIX + "_limited_count", 0)
        mention_channel = FakeChannel()
        handled = asyncio.run(
            self.handle_message("<@{0}> {1}_mention alpha".format(BOT_USER_ID, CHECK_PREFIX), mention_channel, True)
        )
        limited_value = self.get_counter_value(connection, CHECK_PREFIX + "_limited_count")
        self.add_result(
            "runtime: mention reaction",
            handled is True and mention_channel.sent == ["mention ok alpha", "extra alpha"],
            str(mention_channel.sent),
        )
        self.add_result(
            "runtime: mention limited effect",
            limited_value == 1,
            "limited_count={0}".format(limited_value),
        )

        self.set_counter(connection, CHECK_PREFIX + "_delta_count", 0)
        auto_channel = FakeChannel()
        handled = asyncio.run(self.handle_message(CHECK_PREFIX + "_auto_delta", auto_channel, False))
        delta_value = self.get_counter_value(connection, CHECK_PREFIX + "_delta_count")
        self.add_result(
            "runtime: auto reaction + counter_delta",
            handled is True and auto_channel.sent == ["auto delta ok"] and delta_value == 1,
            "sent={0} count={1}".format(auto_channel.sent, delta_value),
        )

        self.set_counter(connection, CHECK_PREFIX + "_set_count", 0)
        set_channel = FakeChannel()
        handled = asyncio.run(self.handle_message(CHECK_PREFIX + "_auto_set", set_channel, False))
        set_value = self.get_counter_value(connection, CHECK_PREFIX + "_set_count")
        self.add_result(
            "runtime: counter_set",
            handled is True and set_channel.sent == ["auto set ok"] and set_value == 1,
            "sent={0} count={1}".format(set_channel.sent, set_value),
        )

        self.set_counter(connection, CHECK_PREFIX + "_ng_count", 0)
        self.set_counter(connection, CHECK_PREFIX + "_delta_count", 0)
        ng_channel = FakeChannel()
        handled = asyncio.run(
            self.handle_message(CHECK_PREFIX + "_ng " + CHECK_PREFIX + "_auto_delta", ng_channel, False)
        )
        ng_value = self.get_counter_value(connection, CHECK_PREFIX + "_ng_count")
        delta_after_ng = self.get_counter_value(connection, CHECK_PREFIX + "_delta_count")
        self.add_result(
            "runtime: NG word stops normal reaction",
            handled is True and ng_channel.sent == [] and ng_value == 1 and delta_after_ng == 0,
            "sent={0} ng={1} auto_delta={2}".format(ng_channel.sent, ng_value, delta_after_ng),
        )

        self.clear_mode_state(connection)
        self.disable_all_modes(connection)
        reply_mode_id = self.get_mode_id(connection, CHECK_PREFIX + "_reply_mode")
        self.set_mode_enabled(connection, reply_mode_id, True)
        self.set_counter(connection, CHECK_PREFIX + "_mode_count", 1)
        connection.commit()
        mode_enter_channel = FakeChannel()
        handled = asyncio.run(self.handle_message("mode threshold", mode_enter_channel, False))
        current_mode = self.get_current_mode_key(connection)
        self.add_result(
            "runtime: counter_threshold enters mode",
            handled is True and current_mode == CHECK_PREFIX + "_reply_mode",
            "current_mode={0} sent={1}".format(current_mode, mode_enter_channel.sent),
        )

        reply_channel = FakeChannel()
        handled = asyncio.run(self.handle_message("mode reply", reply_channel, False))
        self.add_result(
            "runtime: reply mode",
            handled is True and reply_channel.sent == ["reply mode ok"],
            str(reply_channel.sent),
        )

        self.clear_mode_state(connection)
        offline_mode_id = self.get_mode_id(connection, CHECK_PREFIX + "_offline_mode")
        self.set_mode_enabled(connection, offline_mode_id, True)
        ModeRepository(connection).enter_mode(
            self.guild_id,
            offline_mode_id,
            datetime.now(timezone.utc) + timedelta(seconds=120),
            {"entered_by": "integration_check"},
        )
        connection.commit()
        offline_channel = FakeChannel()
        handled = asyncio.run(self.handle_message(CHECK_PREFIX + "_auto_delta", offline_channel, False))
        self.add_result(
            "runtime: offline mode",
            handled is True and offline_channel.sent == [],
            str(offline_channel.sent),
        )

    async def handle_message(self, content: str, channel: FakeChannel, mentioned: bool) -> bool:
        import bot.messages as messages
        from bot.services.runtime_db import handle_db_runtime_message

        bot_user = FakeUser(BOT_USER_ID, "IntegrationBot")
        messages._bot = type("Bot", (), {"user": bot_user})()
        mentions = [bot_user] if mentioned else []
        message = FakeMessage(self.guild_id, content, channel, mentions)
        return await handle_db_runtime_message(message)

    def disable_all_modes(self, connection) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE modes SET enabled = FALSE, updated_at = NOW() WHERE guild_id = %s",
                (self.guild_id,),
            )

    def clear_mode_state(self, connection) -> None:
        ModeRepository(connection).clear_mode_state(
            self.guild_id,
            {"ended_by": "integration_check"},
        )

    def set_mode_enabled(self, connection, mode_id: int, enabled: bool) -> None:
        ModeRepository(connection).set_enabled(self.guild_id, mode_id, enabled)

    def get_mode_id(self, connection, mode_key: str) -> int:
        row = self.fetch_one(
            connection,
            "SELECT id FROM modes WHERE guild_id = %s AND mode_key = %s",
            (self.guild_id, mode_key),
        )
        if row is None:
            raise RuntimeError("mode not found: {0}".format(mode_key))
        return int(row["id"])

    def get_current_mode_key(self, connection) -> Optional[str]:
        row = self.fetch_one(
            connection,
            """
            SELECT m.mode_key
            FROM mode_states s
            JOIN modes m ON m.id = s.current_mode_id
            WHERE s.guild_id = %s
            """,
            (self.guild_id,),
        )
        if row is None:
            return None
        return row.get("mode_key")

    def set_counter(self, connection, count_key: str, value: int) -> None:
        CounterRepository(connection).ensure_counter(self.guild_id, count_key, count_key, initial_value=0, reset_type="manual")
        CounterRepository(connection).set_value(self.guild_id, count_key, value)
        connection.commit()

    def get_counter_value(self, connection, count_key: str) -> int:
        return CounterRepository(connection).get_value(self.guild_id, count_key, 0)

    def print_results(self) -> None:
        for result in self.results:
            label = "OK" if result.ok else "NG"
            suffix = " - {0}".format(result.detail) if result.detail else ""
            print("[{0}] {1}{2}".format(label, result.name, suffix))
        ok_count = len([result for result in self.results if result.ok])
        print("summary: {0}/{1} OK".format(ok_count, len(self.results)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check ver2.0 DB backend integration.")
    parser.add_argument("--database-url", required=True, help="PostgreSQL database URL.")
    parser.add_argument("--guild-id", required=True, help="Guild id to check.")
    parser.add_argument(
        "--upsert-guild",
        action="store_true",
        help="Create/update a test guild row if it does not exist.",
    )
    parser.add_argument(
        "--guild-name",
        default="DB統合確認ギルド",
        help="Guild name used with --upsert-guild.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ["DATABASE_URL"] = args.database_url
    checker = IntegrationChecker(
        args.database_url,
        args.guild_id,
        args.upsert_guild,
        args.guild_name,
    )
    if not checker.run():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
