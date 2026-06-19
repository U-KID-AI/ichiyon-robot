import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.db import get_connection
from bot.repositories.base import fetch_one, json_dumps


@dataclass
class Stats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    assigned: int = 0

    def add(self, action: str) -> None:
        if action == "inserted":
            self.inserted += 1
        elif action == "updated":
            self.updated += 1
        elif action == "assigned":
            self.assigned += 1
        else:
            self.skipped += 1


class PresetSeeder:
    def __init__(self, connection, guild_id: str, guild_name: str, dry_run: bool, force: bool) -> None:
        self.connection = connection
        self.guild_id = guild_id
        self.guild_name = guild_name
        self.dry_run = dry_run
        self.force = force
        self.stats = {
            "guilds": Stats(),
            "counters": Stats(),
            "special_effect_tags": Stats(),
            "special_effect_assignments": Stats(),
            "modes": Stats(),
            "mode_trigger_conditions": Stats(),
            "mode_exit_conditions": Stats(),
            "mode_reply_choices": Stats(),
            "mention_reactions": Stats(),
            "mention_reaction_choices": Stats(),
            "auto_reactions": Stats(),
            "ng_words": Stats(),
        }

    def run(self) -> Dict[str, Stats]:
        self.ensure_guild()
        self.seed_counters()
        tags = self.seed_special_effect_tags()
        modes = self.seed_modes()
        reactions = self.seed_mention_reactions(tags)
        self.seed_auto_reactions(tags)
        self.seed_ng_words(tags)
        self.print_material_summary(tags, modes, reactions)
        return self.stats

    def add(self, key: str, action: str) -> None:
        self.stats[key].add(action)

    def fetch_one(self, sql: str, params: Tuple[Any, ...]) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return fetch_one(cursor)

    def ensure_guild(self) -> None:
        existing = self.fetch_one("SELECT * FROM guilds WHERE guild_id = %s", (self.guild_id,))
        if existing is not None and not self.force:
            self.add("guilds", "skipped")
            return

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO guilds (guild_id, name)
                VALUES (%s, %s)
                ON CONFLICT (guild_id) DO UPDATE
                SET name = EXCLUDED.name,
                    updated_at = NOW()
                RETURNING *
                """,
                (self.guild_id, self.guild_name),
            )
            cursor.fetchone()
        self.add("guilds", "updated" if existing is not None else "inserted")

    def seed_counters(self) -> None:
        self.ensure_counter(
            "narita_count",
            "成田カウント",
            "成田モード突入に使うカウント。毎月22日相当でリセットする想定。",
            0,
            "monthly_day",
            22,
        )
        self.ensure_counter(
            "shikocchi_count",
            "しこっちカウント",
            "しこっちモード突入に使うカウント。モード突入時にruntime側でリセットする想定。",
            0,
            "manual",
            None,
        )

    def ensure_counter(
        self,
        count_key: str,
        name: str,
        description: str,
        initial_value: int,
        reset_type: str,
        reset_day: Optional[int],
    ) -> int:
        existing = self.fetch_one(
            "SELECT * FROM counters WHERE guild_id = %s AND count_key = %s",
            (self.guild_id, count_key),
        )
        if existing is not None and not self.force:
            self.add("counters", "skipped")
            return int(existing["id"])

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO counters (
                    guild_id, count_key, name, description, initial_value, reset_type, reset_day
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (guild_id, count_key) DO UPDATE
                SET name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    initial_value = EXCLUDED.initial_value,
                    reset_type = EXCLUDED.reset_type,
                    reset_day = EXCLUDED.reset_day,
                    updated_at = NOW()
                RETURNING id
                """,
                (self.guild_id, count_key, name, description, initial_value, reset_type, reset_day),
            )
            counter_id = int(cursor.fetchone()[0])
        self.add("counters", "updated" if existing is not None else "inserted")
        return counter_id

    def seed_special_effect_tags(self) -> Dict[str, int]:
        tags = {
            "mini_ichiyon": self.ensure_effect_tag(
                name="ミニいちよん",
                description="抽選候補が選ばれた時、1/32で追加テキストを投稿する。",
                color="#33AAFF",
                priority=100,
                target_type="mention_reaction_choice",
                trigger_timing="choice_selected",
                effect_type="probability_message",
                effect_config={"probability": {"numerator": 1, "denominator": 32}},
                additional_text=":yukkuri_itiyon: ｲﾔ〜{match_1:mini_ichiyon}ﾈ〜",
                additional_post_timing="effect_success",
                expires_type="immediate",
                cooldown_seconds=0,
                cooldown_scope="none",
            ),
            "narita_auto": self.ensure_effect_tag(
                name="成田カウント加算（自動反応）",
                description="自動反応が発火した時、narita_countを1増やす。",
                color="#49A66A",
                priority=80,
                target_type="auto_reaction",
                trigger_timing="auto_reaction_triggered",
                effect_type="counter_delta",
                effect_config={"counter_key": "narita_count", "delta": 1},
                additional_text="",
                additional_post_timing="none",
                expires_type="immediate",
                cooldown_seconds=0,
                cooldown_scope="none",
            ),
            "narita_ng": self.ensure_effect_tag(
                name="成田カウント加算（NGワード）",
                description="NGワード検知時、narita_countを1増やす。",
                color="#49A66A",
                priority=80,
                target_type="ng_word",
                trigger_timing="ng_word_detected",
                effect_type="counter_delta",
                effect_config={"counter_key": "narita_count", "delta": 1},
                additional_text="",
                additional_post_timing="none",
                expires_type="immediate",
                cooldown_seconds=0,
                cooldown_scope="none",
            ),
            "shikocchi_roll": self.ensure_effect_tag(
                name="しこっち抽選",
                description="自動反応が発火した時、1/444でshikocchi_countを1にする。",
                color="#D85A55",
                priority=90,
                target_type="auto_reaction",
                trigger_timing="auto_reaction_triggered",
                effect_type="counter_set",
                effect_config={
                    "counter_key": "shikocchi_count",
                    "value": 1,
                    "probability": {"numerator": 1, "denominator": 444},
                },
                additional_text="",
                additional_post_timing="none",
                expires_type="immediate",
                cooldown_seconds=0,
                cooldown_scope="none",
            ),
            "raio_multiplier": self.ensure_effect_tag(
                name="ライオ9倍",
                description="後続Phaseで確率倍率として実行するための設定プリセット。",
                color="#F0B429",
                priority=50,
                target_type="auto_reaction",
                trigger_timing="auto_reaction_triggered",
                effect_type="probability_multiplier",
                effect_config={"multiplier": 9, "label": "raio"},
                additional_text="",
                additional_post_timing="none",
                expires_type="immediate",
                cooldown_seconds=0,
                cooldown_scope="none",
            ),
            "cherry_next_count": self.ensure_effect_tag(
                name="さくらんぼ2回",
                description="後続Phaseで次アクション回数として実行するための設定プリセット。",
                color="#C5305A",
                priority=50,
                target_type="auto_reaction",
                trigger_timing="auto_reaction_triggered",
                effect_type="next_action_count",
                effect_config={"count": 2, "label": "cherry"},
                additional_text="",
                additional_post_timing="none",
                expires_type="immediate",
                cooldown_seconds=0,
                cooldown_scope="none",
            ),
        }
        return tags

    def ensure_effect_tag(
        self,
        name: str,
        description: str,
        color: str,
        priority: int,
        target_type: str,
        trigger_timing: str,
        effect_type: str,
        effect_config: Dict[str, Any],
        additional_text: str,
        additional_post_timing: str,
        expires_type: str,
        cooldown_seconds: int,
        cooldown_scope: str,
    ) -> int:
        existing = self.fetch_one(
            "SELECT * FROM special_effect_tags WHERE guild_id = %s AND name = %s",
            (self.guild_id, name),
        )
        if existing is not None and not self.force:
            self.add("special_effect_tags", "skipped")
            return int(existing["id"])

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO special_effect_tags (
                    guild_id, name, description, color, admin_only, enabled, is_deletable,
                    priority, target_type, trigger_timing, effect_type, effect_config_json,
                    additional_text, additional_post_timing, expires_type, expires_value,
                    cooldown_seconds, cooldown_scope
                )
                VALUES (
                    %s, %s, %s, %s, FALSE, TRUE, TRUE,
                    %s, %s, %s, %s, %s::JSONB,
                    %s, %s, %s, NULL, %s, %s
                )
                ON CONFLICT (guild_id, name) DO UPDATE
                SET description = EXCLUDED.description,
                    color = EXCLUDED.color,
                    priority = EXCLUDED.priority,
                    target_type = EXCLUDED.target_type,
                    trigger_timing = EXCLUDED.trigger_timing,
                    effect_type = EXCLUDED.effect_type,
                    effect_config_json = EXCLUDED.effect_config_json,
                    additional_text = EXCLUDED.additional_text,
                    additional_post_timing = EXCLUDED.additional_post_timing,
                    expires_type = EXCLUDED.expires_type,
                    cooldown_seconds = EXCLUDED.cooldown_seconds,
                    cooldown_scope = EXCLUDED.cooldown_scope,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    self.guild_id,
                    name,
                    description,
                    color,
                    priority,
                    target_type,
                    trigger_timing,
                    effect_type,
                    json_dumps(effect_config),
                    additional_text,
                    additional_post_timing,
                    expires_type,
                    cooldown_seconds,
                    cooldown_scope,
                ),
            )
            tag_id = int(cursor.fetchone()[0])
        self.add("special_effect_tags", "updated" if existing is not None else "inserted")
        return tag_id

    def seed_modes(self) -> Dict[str, int]:
        modes = {
            "hayusu": self.ensure_mode(
                mode_key="hayusu",
                name="はゆすモード",
                description="1/112抽選で突入する返答モード。",
                behavior_type="reply",
                enter_message="",
                exit_message="",
                cooldown_config={"type": "once_per_period", "period": "monthly", "reset": "month_start"},
                enabled=True,
            ),
            "narita": self.ensure_mode(
                mode_key="narita",
                name="成田モード",
                description="narita_countが22以上になった時に突入する返答モード。",
                behavior_type="reply",
                enter_message="",
                exit_message="",
                cooldown_config={"type": "once_per_period", "period": "monthly", "reset": {"day": 22}},
                enabled=True,
            ),
            "shikocchi": self.ensure_mode(
                mode_key="shikocchi",
                name="しこっちモード",
                description="shikocchi_countが1以上になった時に突入するオフラインモード。",
                behavior_type="offline",
                enter_message="しこっち、きた。",
                exit_message="",
                cooldown_config={"type": "none"},
                enabled=True,
            ),
        }

        self.ensure_trigger_condition(
            modes["hayusu"],
            "probability",
            {"probability": {"numerator": 1, "denominator": 112}},
            "AND",
        )
        self.ensure_trigger_condition(
            modes["hayusu"],
            "period_not_triggered",
            {"period": "monthly", "reset": "month_start"},
            "AND",
        )
        self.ensure_exit_condition(modes["hayusu"], "duration", {"seconds": 180})
        self.ensure_reply_choice(modes["hayusu"], "はゆす返答", "チェルさんこれギャバいっすよ", "", 1)

        self.ensure_trigger_condition(
            modes["narita"],
            "counter_threshold",
            {"counter_key": "narita_count", "operator": ">=", "value": 22},
            "AND",
        )
        for index, text in enumerate(
            [
                "お金の代わりにデータを持つ時代が到来する",
                "稼ぐより踊れ",
                "まねきねこアルゴリズム",
                "泥だんご",
                "アートークン",
            ],
            start=1,
        ):
            self.ensure_reply_choice(modes["narita"], "成田返答{0}".format(index), text, "", 1)

        self.ensure_trigger_condition(
            modes["shikocchi"],
            "counter_threshold",
            {"counter_key": "shikocchi_count", "operator": ">=", "value": 1},
            "AND",
        )
        self.ensure_exit_condition(modes["shikocchi"], "duration", {"seconds": 14 * 60})
        return modes

    def ensure_mode(
        self,
        mode_key: str,
        name: str,
        description: str,
        behavior_type: str,
        enter_message: str,
        exit_message: str,
        cooldown_config: Dict[str, Any],
        enabled: bool,
    ) -> int:
        existing = self.fetch_one(
            "SELECT * FROM modes WHERE guild_id = %s AND mode_key = %s",
            (self.guild_id, mode_key),
        )
        if existing is not None and not self.force:
            self.add("modes", "skipped")
            return int(existing["id"])

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO modes (
                    guild_id, mode_key, name, description, behavior_type,
                    mode_icon_path, enter_message, exit_message, enter_gif_path, exit_gif_path,
                    enter_notify_channel_id, exit_notify_channel_id, reaction_channel_ids,
                    ignore_channel_ids, cooldown_config_json, enabled, admin_only, is_deletable
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    '', %s, %s, '', '',
                    '', '', '[]'::JSONB,
                    '[]'::JSONB, %s::JSONB, %s, FALSE, TRUE
                )
                ON CONFLICT (guild_id, mode_key) DO UPDATE
                SET name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    behavior_type = EXCLUDED.behavior_type,
                    enter_message = EXCLUDED.enter_message,
                    exit_message = EXCLUDED.exit_message,
                    cooldown_config_json = EXCLUDED.cooldown_config_json,
                    enabled = EXCLUDED.enabled,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    self.guild_id,
                    mode_key,
                    name,
                    description,
                    behavior_type,
                    enter_message,
                    exit_message,
                    json_dumps(cooldown_config),
                    enabled,
                ),
            )
            mode_id = int(cursor.fetchone()[0])
        self.add("modes", "updated" if existing is not None else "inserted")
        return mode_id

    def ensure_trigger_condition(
        self,
        mode_id: int,
        condition_type: str,
        condition_config: Dict[str, Any],
        group_operator: str,
    ) -> int:
        existing = self.fetch_one(
            """
            SELECT *
            FROM mode_trigger_conditions
            WHERE guild_id = %s AND mode_id = %s AND condition_type = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (self.guild_id, mode_id, condition_type),
        )
        if existing is not None and not self.force:
            self.add("mode_trigger_conditions", "skipped")
            return int(existing["id"])

        if existing is not None:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE mode_trigger_conditions
                    SET condition_config_json = %s::JSONB,
                        group_operator = %s,
                        enabled = TRUE,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (json_dumps(condition_config), group_operator, existing["id"]),
                )
                condition_id = int(cursor.fetchone()[0])
            self.add("mode_trigger_conditions", "updated")
            return condition_id

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_trigger_conditions (
                    guild_id, mode_id, condition_type, condition_config_json, group_operator, enabled
                )
                VALUES (%s, %s, %s, %s::JSONB, %s, TRUE)
                RETURNING id
                """,
                (self.guild_id, mode_id, condition_type, json_dumps(condition_config), group_operator),
            )
            condition_id = int(cursor.fetchone()[0])
        self.add("mode_trigger_conditions", "inserted")
        return condition_id

    def ensure_exit_condition(self, mode_id: int, condition_type: str, condition_config: Dict[str, Any]) -> int:
        existing = self.fetch_one(
            """
            SELECT *
            FROM mode_exit_conditions
            WHERE guild_id = %s AND mode_id = %s AND condition_type = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (self.guild_id, mode_id, condition_type),
        )
        if existing is not None and not self.force:
            self.add("mode_exit_conditions", "skipped")
            return int(existing["id"])

        if existing is not None:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE mode_exit_conditions
                    SET condition_config_json = %s::JSONB,
                        enabled = TRUE,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (json_dumps(condition_config), existing["id"]),
                )
                condition_id = int(cursor.fetchone()[0])
            self.add("mode_exit_conditions", "updated")
            return condition_id

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_exit_conditions (
                    guild_id, mode_id, condition_type, condition_config_json, enabled
                )
                VALUES (%s, %s, %s, %s::JSONB, TRUE)
                RETURNING id
                """,
                (self.guild_id, mode_id, condition_type, json_dumps(condition_config)),
            )
            condition_id = int(cursor.fetchone()[0])
        self.add("mode_exit_conditions", "inserted")
        return condition_id

    def ensure_reply_choice(
        self,
        mode_id: int,
        name: str,
        body: str,
        image_path: str,
        appearance_rate: int,
    ) -> int:
        existing = self.fetch_one(
            """
            SELECT *
            FROM mode_reply_choices
            WHERE guild_id = %s AND mode_id = %s AND name = %s
            """,
            (self.guild_id, mode_id, name),
        )
        if existing is not None and not self.force:
            self.add("mode_reply_choices", "skipped")
            return int(existing["id"])

        if existing is not None:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE mode_reply_choices
                    SET body = %s,
                        image_path = %s,
                        appearance_rate = %s,
                        enabled = TRUE,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (body, image_path, appearance_rate, existing["id"]),
                )
                choice_id = int(cursor.fetchone()[0])
            self.add("mode_reply_choices", "updated")
            return choice_id

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mode_reply_choices (
                    guild_id, mode_id, name, body, image_path, appearance_rate, enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                RETURNING id
                """,
                (self.guild_id, mode_id, name, body, image_path, appearance_rate),
            )
            choice_id = int(cursor.fetchone()[0])
        self.add("mode_reply_choices", "inserted")
        return choice_id

    def seed_mention_reactions(self, tags: Dict[str, int]) -> Dict[str, int]:
        quote_id = self.ensure_mention_reaction(
            reaction_key="quotes",
            keyword="",
            match_type="exact",
            reaction_kind="random",
            name="名言",
            description="名言用の固定メンション反応枠。候補はJSON移行スクリプトで投入する。",
            admin_only=False,
            is_system=True,
            is_deletable=False,
            enabled=True,
            config_json={},
        )
        kuji_id = self.ensure_mention_reaction(
            reaction_key="kuji",
            keyword="くじ",
            match_type="exact",
            reaction_kind="random",
            name="おみくじ",
            description="おみくじ用のランダム抽選メンション反応。",
            admin_only=False,
            is_system=False,
            is_deletable=True,
            enabled=True,
            config_json={},
        )
        omae_id = self.ensure_mention_reaction(
            reaction_key="omae_mo_yona",
            keyword="お前も(.+?)よな？",
            match_type="regex",
            reaction_kind="random",
            name="お前も〇〇よな？",
            description="正規表現で拾った語句を返答に差し込むランダム抽選メンション反応。",
            admin_only=False,
            is_system=False,
            is_deletable=True,
            enabled=True,
            config_json={},
        )
        omae_choice_id = self.ensure_mention_choice(
            omae_id,
            "お前も〇〇よな？",
            "いや〜{match_1}ね〜",
            "",
            1,
        )
        self.ensure_assignment(tags["mini_ichiyon"], "mention_reaction_choice", omae_choice_id)

        deck_id = self.ensure_mention_reaction(
            reaction_key="deck_search",
            keyword="デッキ検索",
            match_type="prefix",
            reaction_kind="search",
            name="デッキ検索",
            description="検索型の固定メンション反応。検索ロジックは後続Phaseで接続する。",
            admin_only=False,
            is_system=True,
            is_deletable=False,
            enabled=False,
            config_json={
                "search_type": "deck_search",
                "allowed_channel_ids": [],
                "max_results": 3,
                "deny_message": "このチャンネルではデッキ検索は使えません。",
                "missing_format_behavior": "ask_format",
            },
        )
        return {"quotes": quote_id, "kuji": kuji_id, "omae_mo_yona": omae_id, "deck_search": deck_id}

    def ensure_mention_reaction(
        self,
        reaction_key: str,
        keyword: str,
        match_type: str,
        reaction_kind: str,
        name: str,
        description: str,
        admin_only: bool,
        is_system: bool,
        is_deletable: bool,
        enabled: bool,
        config_json: Dict[str, Any],
    ) -> int:
        existing = self.fetch_one(
            "SELECT * FROM mention_reactions WHERE guild_id = %s AND reaction_key = %s",
            (self.guild_id, reaction_key),
        )
        if existing is not None and not self.force:
            self.add("mention_reactions", "skipped")
            return int(existing["id"])

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mention_reactions (
                    guild_id, reaction_key, keyword, match_type, reaction_kind, name,
                    description, admin_only, is_system, is_deletable, config_json, enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB, %s)
                ON CONFLICT (guild_id, reaction_key) DO UPDATE
                SET keyword = EXCLUDED.keyword,
                    match_type = EXCLUDED.match_type,
                    reaction_kind = EXCLUDED.reaction_kind,
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    admin_only = EXCLUDED.admin_only,
                    is_system = EXCLUDED.is_system,
                    is_deletable = EXCLUDED.is_deletable,
                    config_json = EXCLUDED.config_json,
                    enabled = EXCLUDED.enabled,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    self.guild_id,
                    reaction_key,
                    keyword,
                    match_type,
                    reaction_kind,
                    name,
                    description,
                    admin_only,
                    is_system,
                    is_deletable,
                    json_dumps(config_json),
                    enabled,
                ),
            )
            reaction_id = int(cursor.fetchone()[0])
        self.add("mention_reactions", "updated" if existing is not None else "inserted")
        return reaction_id

    def ensure_mention_choice(
        self,
        mention_reaction_id: int,
        name: str,
        body: str,
        image_path: str,
        appearance_rate: int,
    ) -> int:
        existing = self.fetch_one(
            """
            SELECT *
            FROM mention_reaction_choices
            WHERE guild_id = %s AND mention_reaction_id = %s AND name = %s
            """,
            (self.guild_id, mention_reaction_id, name),
        )
        if existing is not None and not self.force:
            self.add("mention_reaction_choices", "skipped")
            return int(existing["id"])

        if existing is not None:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE mention_reaction_choices
                    SET body = %s,
                        image_path = %s,
                        appearance_rate = %s,
                        enabled = TRUE,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (body, image_path, appearance_rate, existing["id"]),
                )
                choice_id = int(cursor.fetchone()[0])
            self.add("mention_reaction_choices", "updated")
            return choice_id

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mention_reaction_choices (
                    guild_id, mention_reaction_id, name, body, image_path, appearance_rate, enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                RETURNING id
                """,
                (self.guild_id, mention_reaction_id, name, body, image_path, appearance_rate),
            )
            choice_id = int(cursor.fetchone()[0])
        self.add("mention_reaction_choices", "inserted")
        return choice_id

    def seed_auto_reactions(self, tags: Dict[str, int]) -> None:
        shikocchi_id = self.ensure_auto_reaction(
            "しこっち",
            "しこっちきたぁぁぁ",
            "",
            "",
            "contains",
            100,
            True,
        )
        self.ensure_assignment(tags["shikocchi_roll"], "auto_reaction", shikocchi_id)

    def ensure_auto_reaction(
        self,
        trigger_text: str,
        response_text: str,
        image_path: str,
        emoji_internal: str,
        match_type: str,
        priority: int,
        enabled: bool,
    ) -> int:
        existing = self.fetch_one(
            """
            SELECT *
            FROM reactions
            WHERE guild_id = %s AND trigger_text = %s AND match_type = %s
            """,
            (self.guild_id, trigger_text, match_type),
        )
        if existing is not None and not self.force:
            self.add("auto_reactions", "skipped")
            return int(existing["id"])

        if existing is not None:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE reactions
                    SET response_text = %s,
                        image_path = %s,
                        emoji_internal = %s,
                        priority = %s,
                        enabled = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (response_text, image_path, emoji_internal, priority, enabled, existing["id"]),
                )
                reaction_id = int(cursor.fetchone()[0])
            self.add("auto_reactions", "updated")
            return reaction_id

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO reactions (
                    guild_id, trigger_text, response_text, image_path,
                    emoji_internal, match_type, priority, enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.guild_id,
                    trigger_text,
                    response_text,
                    image_path,
                    emoji_internal,
                    match_type,
                    priority,
                    enabled,
                ),
            )
            reaction_id = int(cursor.fetchone()[0])
        self.add("auto_reactions", "inserted")
        return reaction_id

    def seed_ng_words(self, tags: Dict[str, int]) -> None:
        for word in ("お金", "データ"):
            word_id = self.ensure_ng_word(word, True)
            self.ensure_assignment(tags["narita_ng"], "ng_word", word_id)

    def ensure_ng_word(self, word: str, enabled: bool) -> int:
        existing = self.fetch_one(
            "SELECT * FROM ng_words WHERE guild_id = %s AND word = %s",
            (self.guild_id, word),
        )
        if existing is not None and not self.force:
            self.add("ng_words", "skipped")
            return int(existing["id"])

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ng_words (guild_id, word, enabled)
                VALUES (%s, %s, %s)
                ON CONFLICT (guild_id, word) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    updated_at = NOW()
                RETURNING id
                """,
                (self.guild_id, word, enabled),
            )
            word_id = int(cursor.fetchone()[0])
        self.add("ng_words", "updated" if existing is not None else "inserted")
        return word_id

    def ensure_assignment(self, tag_id: int, target_type: str, target_id: int) -> int:
        existing = self.fetch_one(
            """
            SELECT *
            FROM special_effect_assignments
            WHERE special_effect_tag_id = %s AND target_type = %s AND target_id = %s
            """,
            (tag_id, target_type, target_id),
        )
        if existing is not None and not self.force:
            self.add("special_effect_assignments", "skipped")
            return int(existing["id"])

        with self.connection.cursor() as cursor:
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
            assignment_id = int(cursor.fetchone()[0])
        self.add("special_effect_assignments", "updated" if existing is not None else "assigned")
        return assignment_id

    def print_material_summary(
        self,
        tags: Dict[str, int],
        modes: Dict[str, int],
        reactions: Dict[str, int],
    ) -> None:
        print("preset material ids:")
        print("  mini tag id: {0}".format(tags["mini_ichiyon"]))
        print("  shikocchi roll tag id: {0}".format(tags["shikocchi_roll"]))
        print("  narita ng tag id: {0}".format(tags["narita_ng"]))
        print("  hayusu mode id: {0}".format(modes["hayusu"]))
        print("  narita mode id: {0}".format(modes["narita"]))
        print("  shikocchi mode id: {0}".format(modes["shikocchi"]))
        print("  omae reaction id: {0}".format(reactions["omae_mo_yona"]))
        print("  deck search reaction id: {0}".format(reactions["deck_search"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed ver2.0 DB backend preset data.")
    parser.add_argument("--database-url", help="Override DATABASE_URL for this run.")
    parser.add_argument("--guild-id", required=True, help="Discord guild id to seed.")
    parser.add_argument("--guild-name", default="いちよんプリセット", help="Guild display name.")
    parser.add_argument("--dry-run", action="store_true", help="Run inside a rollback-only transaction.")
    parser.add_argument("--force", action="store_true", help="Update existing preset rows instead of skipping them.")
    return parser.parse_args()


def print_stats(stats: Dict[str, Stats], dry_run: bool) -> None:
    print("v2 preset seed {0}".format("dry-run completed" if dry_run else "completed"))
    for key in sorted(stats):
        item = stats[key]
        print(
            "{0}: inserted={1} updated={2} assigned={3} skipped={4}".format(
                key,
                item.inserted,
                item.updated,
                item.assigned,
                item.skipped,
            )
        )


def main() -> None:
    args = parse_args()
    with get_connection(args.database_url) as connection:
        seeder = PresetSeeder(
            connection,
            args.guild_id,
            args.guild_name,
            args.dry_run,
            args.force,
        )
        stats = seeder.run()
        if args.dry_run:
            connection.rollback()
        else:
            connection.commit()

    print_stats(stats, args.dry_run)


if __name__ == "__main__":
    main()
