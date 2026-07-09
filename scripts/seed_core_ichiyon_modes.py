import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.db import get_connection


BOT_ID = "ichiyon"
ICHIYON_USER_ID = "748965361486921831"
COCONUTS_USER_ID = "874915774672818227"


def json_dumps(value: Dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def list_ichiyon_guild_ids(connection) -> List[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT bg.guild_id
            FROM bot_guilds bg
            JOIN guilds g ON g.guild_id = bg.guild_id
            WHERE bg.bot_id = %s
              AND bg.enabled = TRUE
              AND COALESCE(g.enabled, TRUE) = TRUE
            ORDER BY bg.guild_id ASC
            """,
            (BOT_ID,),
        )
        return [str(row[0]) for row in cursor.fetchall()]


def upsert_mode(connection, guild_id: str, spec: Dict[str, Any]) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM modes
            WHERE bot_id = %s AND guild_id = %s AND mode_key = %s
            """,
            (BOT_ID, guild_id, spec["mode_key"]),
        )
        row = cursor.fetchone()
        params = (
            spec["name"],
            spec.get("description", ""),
            spec["behavior_type"],
            spec.get("enter_message", ""),
            spec.get("exit_message", ""),
            spec.get("duration_seconds"),
            json_dumps(spec.get("cooldown_config_json", {})),
            json_dumps(spec.get("appearance_config_json", {})),
            True,
            False,
            True,
        )
        if row:
            mode_id = int(row[0])
            cursor.execute(
                """
                UPDATE modes
                SET name = %s,
                    description = %s,
                    behavior_type = %s,
                    enter_message = %s,
                    exit_message = %s,
                    duration_seconds = %s,
                    cooldown_config_json = %s::JSONB,
                    appearance_config_json = %s::JSONB,
                    enabled = %s,
                    admin_only = %s,
                    is_deletable = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                params + (BOT_ID, guild_id, mode_id),
            )
            return mode_id

        cursor.execute(
            """
            INSERT INTO modes (
                bot_id, guild_id, mode_key, name, description, behavior_type,
                enter_message, exit_message, duration_seconds,
                cooldown_config_json, appearance_config_json,
                enabled, admin_only, is_deletable
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB, %s::JSONB, %s, %s, %s)
            RETURNING id
            """,
            (
                BOT_ID,
                guild_id,
                spec["mode_key"],
                spec["name"],
                spec.get("description", ""),
                spec["behavior_type"],
                spec.get("enter_message", ""),
                spec.get("exit_message", ""),
                spec.get("duration_seconds"),
                json_dumps(spec.get("cooldown_config_json", {})),
                json_dumps(spec.get("appearance_config_json", {})),
                True,
                False,
                True,
            ),
        )
        return int(cursor.fetchone()[0])


def upsert_trigger_condition(
    connection,
    guild_id: str,
    mode_id: int,
    group_key: str,
    condition_type: str,
    config: Dict[str, Any],
    group_operator: str = "AND",
    enabled: bool = True,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM mode_trigger_conditions
            WHERE bot_id = %s AND guild_id = %s AND mode_id = %s AND condition_group_key = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (BOT_ID, guild_id, mode_id, group_key),
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                """
                UPDATE mode_trigger_conditions
                SET condition_type = %s,
                    condition_config_json = %s::JSONB,
                    group_operator = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (condition_type, json_dumps(config), group_operator, enabled, BOT_ID, guild_id, int(row[0])),
            )
            return
        cursor.execute(
            """
            INSERT INTO mode_trigger_conditions (
                bot_id, guild_id, mode_id, condition_group_key,
                condition_type, condition_config_json, group_operator, enabled
            )
            VALUES (%s, %s, %s, %s, %s, %s::JSONB, %s, %s)
            """,
            (BOT_ID, guild_id, mode_id, group_key, condition_type, json_dumps(config), group_operator, enabled),
        )


def upsert_auto_reaction(connection, guild_id: str, spec: Dict[str, Any]) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM reactions
            WHERE bot_id = %s
              AND guild_id = %s
              AND trigger_text = %s
              AND match_type = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (BOT_ID, guild_id, spec["trigger_text"], spec["match_type"]),
        )
        row = cursor.fetchone()
        params = (
            spec.get("response_text"),
            spec.get("image_path"),
            spec.get("emoji_internal"),
            spec.get("priority", 0),
            spec.get("enabled", True),
        )
        if row:
            reaction_id = int(row[0])
            cursor.execute(
                """
                UPDATE reactions
                SET response_text = %s,
                    image_path = %s,
                    emoji_internal = %s,
                    priority = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                params + (BOT_ID, guild_id, reaction_id),
            )
            return reaction_id

        cursor.execute(
            """
            INSERT INTO reactions (
                bot_id, guild_id, trigger_text, response_text, image_path,
                emoji_internal, match_type, priority, enabled
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                BOT_ID,
                guild_id,
                spec["trigger_text"],
                spec.get("response_text"),
                spec.get("image_path"),
                spec.get("emoji_internal"),
                spec["match_type"],
                spec.get("priority", 0),
                spec.get("enabled", True),
            ),
        )
        return int(cursor.fetchone()[0])


def upsert_special_effect_tag(connection, guild_id: str, spec: Dict[str, Any]) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM special_effect_tags
            WHERE bot_id = %s AND guild_id = %s AND name = %s
            """,
            (BOT_ID, guild_id, spec["name"]),
        )
        row = cursor.fetchone()
        params = (
            spec.get("description", ""),
            spec.get("color", "#6B7280"),
            spec.get("admin_only", False),
            spec.get("enabled", True),
            spec.get("is_deletable", True),
            spec.get("priority", 0),
            spec["target_type"],
            spec["trigger_timing"],
            spec["effect_type"],
            json_dumps(spec.get("effect_config_json", {})),
            spec.get("additional_text", ""),
            spec.get("additional_post_timing", "none"),
            spec.get("expires_type", "immediate"),
            spec.get("expires_value"),
            spec.get("cooldown_seconds", 0),
            spec.get("cooldown_scope", "none"),
        )
        if row:
            tag_id = int(row[0])
            cursor.execute(
                """
                UPDATE special_effect_tags
                SET description = %s,
                    color = %s,
                    admin_only = %s,
                    enabled = %s,
                    is_deletable = %s,
                    priority = %s,
                    target_type = %s,
                    trigger_timing = %s,
                    effect_type = %s,
                    effect_config_json = %s::JSONB,
                    additional_text = %s,
                    additional_post_timing = %s,
                    expires_type = %s,
                    expires_value = %s,
                    cooldown_seconds = %s,
                    cooldown_scope = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                params + (BOT_ID, guild_id, tag_id),
            )
            return tag_id

        cursor.execute(
            """
            INSERT INTO special_effect_tags (
                bot_id, guild_id, name, description, color, admin_only, enabled,
                is_deletable, priority, target_type, trigger_timing, effect_type,
                effect_config_json, additional_text, additional_post_timing,
                expires_type, expires_value, cooldown_seconds, cooldown_scope
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                BOT_ID,
                guild_id,
                spec["name"],
                spec.get("description", ""),
                spec.get("color", "#6B7280"),
                spec.get("admin_only", False),
                spec.get("enabled", True),
                spec.get("is_deletable", True),
                spec.get("priority", 0),
                spec["target_type"],
                spec["trigger_timing"],
                spec["effect_type"],
                json_dumps(spec.get("effect_config_json", {})),
                spec.get("additional_text", ""),
                spec.get("additional_post_timing", "none"),
                spec.get("expires_type", "immediate"),
                spec.get("expires_value"),
                spec.get("cooldown_seconds", 0),
                spec.get("cooldown_scope", "none"),
            ),
        )
        return int(cursor.fetchone()[0])


def upsert_special_effect_assignment(connection, guild_id: str, tag_id: int, target_type: str, target_id: int) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO special_effect_assignments (
                bot_id, guild_id, special_effect_tag_id, target_type, target_id, enabled
            )
            VALUES (%s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (bot_id, special_effect_tag_id, target_type, target_id) DO UPDATE
            SET enabled = TRUE,
                updated_at = NOW()
            RETURNING id
            """,
            (BOT_ID, guild_id, tag_id, target_type, target_id),
        )
        return int(cursor.fetchone()[0])


def upsert_exit_condition(
    connection,
    guild_id: str,
    mode_id: int,
    group_key: str,
    condition_type: str,
    config: Dict[str, Any],
    enabled: bool = True,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM mode_exit_conditions
            WHERE bot_id = %s AND guild_id = %s AND mode_id = %s AND condition_type = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (BOT_ID, guild_id, mode_id, condition_type),
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                """
                UPDATE mode_exit_conditions
                SET condition_config_json = %s::JSONB,
                    enabled = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (json_dumps(config), enabled, BOT_ID, guild_id, int(row[0])),
            )
            return
        cursor.execute(
            """
            INSERT INTO mode_exit_conditions (
                bot_id, guild_id, mode_id, condition_type, condition_config_json, enabled
            )
            VALUES (%s, %s, %s, %s, %s::JSONB, %s)
            """,
            (BOT_ID, guild_id, mode_id, condition_type, json_dumps(config), enabled),
        )


def upsert_reply_choice(
    connection,
    guild_id: str,
    mode_id: int,
    name: str,
    body: str,
    appearance_rate: int = 1,
    enabled: bool = True,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM mode_reply_choices
            WHERE bot_id = %s AND guild_id = %s AND mode_id = %s AND name = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (BOT_ID, guild_id, mode_id, name),
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                """
                UPDATE mode_reply_choices
                SET body = %s,
                    image_path = NULL,
                    appearance_rate = %s,
                    enabled = %s,
                    updated_at = NOW()
                WHERE bot_id = %s AND guild_id = %s AND id = %s
                """,
                (body, appearance_rate, enabled, BOT_ID, guild_id, int(row[0])),
            )
            return
        cursor.execute(
            """
            INSERT INTO mode_reply_choices (
                bot_id, guild_id, mode_id, name, body, image_path, appearance_rate, enabled
            )
            VALUES (%s, %s, %s, %s, %s, NULL, %s, %s)
            """,
            (BOT_ID, guild_id, mode_id, name, body, appearance_rate, enabled),
        )


def disable_unlisted_reply_choices(connection, guild_id: str, mode_id: int, allowed_names: Iterable[str]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE mode_reply_choices
            SET enabled = FALSE,
                updated_at = NOW()
            WHERE bot_id = %s
              AND guild_id = %s
              AND mode_id = %s
              AND NOT (name = ANY(%s))
            """,
            (BOT_ID, guild_id, mode_id, list(allowed_names)),
        )


def build_mode_specs(ichiyon_user_id: str, coconuts_user_id: str) -> List[Dict[str, Any]]:
    monthly = {"type": "once_per_period", "period": "monthly", "reset": "month_start"}
    return [
        {
            "mode_key": "ichiyon_almost",
            "name": "いちよんほぼ",
            "description": "いちよん本人の発言時に低確率で突入し、発言を真似します。",
            "behavior_type": "reply",
            "enter_message": "# まずは\n# 女子供から\n# 殺す",
            "duration_seconds": 180,
            "cooldown_config_json": monthly,
            "appearance_config_json": {
                "reply_type": "echo_user_message",
                "target_user_ids": [ichiyon_user_id],
            },
            "probability_config": {
                "probability": {"numerator": 14, "denominator": 141414},
                "author_user_ids": [ichiyon_user_id],
            },
            "reply_choices": [],
        },
        {
            "mode_key": "coconuts_almost",
            "name": "ここなっつほぼ",
            "description": "ここなっつ本人の発言時に低確率で突入し、発言を真似します。",
            "behavior_type": "reply",
            "enter_message": "# 敵\n\n# 敵\n\n# 敵\n\n# 敵",
            "duration_seconds": 180,
            "cooldown_config_json": monthly,
            "appearance_config_json": {
                "reply_type": "echo_user_message",
                "target_user_ids": [coconuts_user_id],
            },
            "probability_config": {
                "probability": {"numerator": 1, "denominator": 5572},
                "author_user_ids": [coconuts_user_id],
            },
            "reply_choices": [],
        },
        {
            "mode_key": "ryugasaki_hiiro",
            "name": "竜ヶ崎ヒイロ",
            "description": "シャドバまたはスマホの話題で低確率突入し、固定文で返答します。",
            "behavior_type": "reply",
            "enter_message": "# シャドバ\n\n# すっげー\n\n# 楽しい！！！",
            "duration_seconds": 180,
            "cooldown_config_json": monthly,
            "appearance_config_json": {"reply_type": "choice"},
            "probability_config": {
                "probability": {"numerator": 1, "denominator": 40},
                "keywords": ["シャドバ", "スマホ"],
            },
            "reply_choices": [
                ("ヒイロ固定返答", "# シャドバ\n\n# すっげー\n\n# 楽しい！！！", 1),
            ],
        },
        {
            "mode_key": "taketsumi_robot",
            "name": "タケツミロボ",
            "description": "記憶パの話題で突入し、タケツミ台詞からランダムに返答します。",
            "behavior_type": "reply",
            "enter_message": "# 無粋はよせよせ！\n# 宴としようや！",
            "duration_seconds": 180,
            "cooldown_config_json": monthly,
            "appearance_config_json": {"reply_type": "choice"},
            "probability_config": {
                "counter_key": "taketsumi_count",
                "operator": ">=",
                "value": 1,
            },
            "trigger_condition_type": "counter_threshold",
            "trigger_effect": {
                "auto_reaction": {
                    "trigger_text": "記憶パ",
                    "response_text": "",
                    "image_path": None,
                    "emoji_internal": None,
                    "match_type": "contains",
                    "priority": 100,
                    "enabled": True,
                },
                "tag": {
                    "name": "タケツミロボ突入カウンター",
                    "description": "自動反応「記憶パ」発火時にtaketsumi_countを1にして、タケツミロボの突入条件を満たします。",
                    "color": "#B7791F",
                    "admin_only": False,
                    "enabled": True,
                    "is_deletable": True,
                    "priority": 100,
                    "target_type": "auto_reaction",
                    "trigger_timing": "auto_reaction_triggered",
                    "effect_type": "counter_set",
                    "effect_config_json": {"counter_key": "taketsumi_count", "value": 1},
                    "additional_text": "",
                    "additional_post_timing": "none",
                    "expires_type": "immediate",
                    "expires_value": None,
                    "cooldown_seconds": 0,
                    "cooldown_scope": "none",
                },
            },
            "reply_choices": [
                ("タケツミ01", "おっ、新入りか？", 1),
                ("タケツミ02", "ありがとよ。", 1),
                ("タケツミ03", "すまねぇな。", 1),
                ("タケツミ04", "っか、おもしれぇ！", 1),
                ("タケツミ05", "おいおいおい！？", 1),
                ("タケツミ06", "どうすっかねぇ...", 1),
                ("タケツミ07", "# 此処で決めるが\n# 大将だろうよ！", 1),
                ("タケツミ08", "頼まれちゃあ、断れねぇなぁ！", 1),
                ("タケツミ09", "華と咲かせば、世は極楽。", 1),
                ("タケツミ10", "もてなしてやるよ、存分に！", 1),
                ("タケツミ11", "一丁やるか。", 1),
                ("タケツミ12", "盛り上げるぜ！", 1),
                ("タケツミ13", "そら、大詰めだ！", 1),
                ("タケツミ14", "さぁて、宴の続きだ！", 1),
                ("タケツミ15", "御見逸れしたぜ。", 1),
                ("タケツミ16", "なんて華だ。", 1),
                ("タケツミ17", "もてなしだぁ！", 1),
                ("タケツミ18", "楽しいだろ！手前てめぇもよぉ！", 1),
                ("タケツミ19", "何だと…！？", 1),
            ],
        },
    ]


def seed_guild(connection, guild_id: str, specs: List[Dict[str, Any]]) -> None:
    for spec in specs:
        mode_id = upsert_mode(connection, guild_id, spec)
        trigger_effect = spec.get("trigger_effect")
        if trigger_effect:
            reaction_id = upsert_auto_reaction(connection, guild_id, trigger_effect["auto_reaction"])
            tag_id = upsert_special_effect_tag(connection, guild_id, trigger_effect["tag"])
            upsert_special_effect_assignment(connection, guild_id, tag_id, "auto_reaction", reaction_id)
        upsert_trigger_condition(
            connection,
            guild_id,
            mode_id,
            "core_probability",
            spec.get("trigger_condition_type", "probability"),
            spec["probability_config"],
            "AND",
            True,
        )
        upsert_trigger_condition(
            connection,
            guild_id,
            mode_id,
            "core_once_per_month",
            "period_not_triggered",
            {"period": "monthly", "reset": "month_start"},
            "AND",
            True,
        )
        upsert_exit_condition(connection, guild_id, mode_id, "core_duration", "duration", {"seconds": 180}, True)
        allowed_names = []
        for name, body, weight in spec.get("reply_choices", []):
            allowed_names.append(name)
            upsert_reply_choice(connection, guild_id, mode_id, name, body, weight, True)
        if allowed_names:
            disable_unlisted_reply_choices(connection, guild_id, mode_id, allowed_names)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed core ichiyon modes into every enabled ichiyon guild.")
    parser.add_argument("--ichiyon-user-id", default=ICHIYON_USER_ID)
    parser.add_argument("--coconuts-user-id", default=COCONUTS_USER_ID)
    args = parser.parse_args()

    specs = build_mode_specs(args.ichiyon_user_id, args.coconuts_user_id)
    with get_connection() as connection:
        guild_ids = list_ichiyon_guild_ids(connection)
        for guild_id in guild_ids:
            seed_guild(connection, guild_id, specs)
        connection.commit()

    print("Seeded {0} core modes for {1} ichiyon guild(s).".format(len(specs), len(guild_ids)))
    for guild_id in guild_ids:
        print("- {0}".format(guild_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
