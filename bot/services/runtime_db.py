import calendar
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from bot import config
from bot.db import get_connection
from bot.messages import get_bot, get_mention_command_text, send_text_or_image
from bot.messages import update_bot_avatar, update_bot_nickname
from bot.ng_words import normalize_ng_match_text
from bot.repositories import (
    AutoReactionRepository,
    CounterRepository,
    DeckSearchSettingsRepository,
    FeatureFlagRepository,
    MentionReactionRepository,
    MentionLimitedEffectRepository,
    ModeRepository,
    NgWordRepository,
    PermissionRepository,
    SpecialEffectRepository,
)
from bot.services.deck_search import search_decks
from bot.services.deck_search_settings import (
    apply_deck_search_settings,
    format_fetch_since_date,
    parse_deck_fetch_since_command,
    settings_fetch_since_date,
    settings_max_lookback_days,
    validate_fetch_since_date,
)


FEATURE_MENTION_REACTIONS = "mention_reactions"
FEATURE_AUTO_REACTIONS = "reactions"
FEATURE_NG_WORDS = "ng_words"
JST = timezone(timedelta(hours=9))

MATCH_TYPE_RANK = {
    "exact": 3,
    "prefix": 2,
    "regex": 1,
}
SHIKOCCHI_RECOVERY_MESSAGE = "まずは女子供から殺す"
MAX_NEXT_ACTION_COUNT = 5
_PENDING_NEXT_EFFECTS: Dict[str, List[Dict[str, Any]]] = {}


@dataclass
class MatchResult:
    row: Dict[str, Any]
    groups: Dict[str, str]


@dataclass
class RuntimeAction:
    handled: bool
    count_changed: bool = False
    pending_effects: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class EffectExecutionResult:
    count_changed: bool = False
    repeat_count: int = 0
    pending_effects: List[Dict[str, Any]] = field(default_factory=list)


def get_message_guild_id(message: discord.Message) -> Optional[str]:
    guild = getattr(message, "guild", None)
    if guild is None:
        return None
    guild_id = getattr(guild, "id", None)
    if guild_id is None:
        return None
    return str(guild_id)


def feature_enabled(connection, guild_id: str, feature_key: str) -> bool:
    repository = FeatureFlagRepository(connection)
    return repository.is_enabled(guild_id, feature_key, default=True)


def can_update_deck_fetch_since(connection, guild_id: str, message: discord.Message) -> bool:
    user_id = get_message_author_id(message)
    if user_id and config.DEVELOPER_USER_ID and user_id == str(config.DEVELOPER_USER_ID):
        return True
    repository = PermissionRepository(connection)
    if repository.has_global_admin(user_id):
        return True
    permission = repository.get_guild_permission(guild_id, user_id)
    return bool(permission and permission.get("role") == "guild_admin")


def load_deck_search_settings(connection, guild_id: str) -> Optional[Dict[str, Any]]:
    try:
        return DeckSearchSettingsRepository(connection).get(config.BOT_INSTANCE_ID, guild_id)
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        print("[WARN] deck search settings unavailable: {0}".format(type(exc).__name__))
        return None


def build_template_values(message: discord.Message, message_text: str, groups: Dict[str, str]) -> Dict[str, str]:
    values = {
        "user_name": getattr(message.author, "display_name", None) or getattr(message.author, "name", ""),
        "user_mention": getattr(message.author, "mention", ""),
        "message_text": message_text,
    }
    values.update(groups)
    return values


def render_template(text: Optional[str], values: Dict[str, str]) -> str:
    rendered = text or ""
    pattern = re.compile(r"\{([A-Za-z0-9_]+):([A-Za-z0-9_]+)\}")

    def replace_transformed(match: re.Match) -> str:
        key = match.group(1)
        transform = match.group(2)
        value = values.get(key)
        if value is None:
            return match.group(0)
        if transform in ("hankaku", "mini_ichiyon"):
            return to_hankaku_text(value)
        return match.group(0)

    rendered = pattern.sub(replace_transformed, rendered)
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def render_choice_body(choice: Dict[str, Any], values: Dict[str, str]) -> str:
    body = render_template(choice.get("body"), values)
    label = (choice.get("result_label") or "").strip()
    if not label:
        return body
    if body.startswith(label):
        return body
    if body:
        return "{0}\n{1}".format(label, body)
    return label


def to_hankaku_text(value: str) -> str:
    katakana = []
    for char in value:
        code = ord(char)
        if 0x3041 <= code <= 0x3096:
            katakana.append(chr(code + 0x60))
        else:
            katakana.append(char)
    table = {
        "ア": "ｱ", "イ": "ｲ", "ウ": "ｳ", "エ": "ｴ", "オ": "ｵ",
        "カ": "ｶ", "キ": "ｷ", "ク": "ｸ", "ケ": "ｹ", "コ": "ｺ",
        "サ": "ｻ", "シ": "ｼ", "ス": "ｽ", "セ": "ｾ", "ソ": "ｿ",
        "タ": "ﾀ", "チ": "ﾁ", "ツ": "ﾂ", "テ": "ﾃ", "ト": "ﾄ",
        "ナ": "ﾅ", "ニ": "ﾆ", "ヌ": "ﾇ", "ネ": "ﾈ", "ノ": "ﾉ",
        "ハ": "ﾊ", "ヒ": "ﾋ", "フ": "ﾌ", "ヘ": "ﾍ", "ホ": "ﾎ",
        "マ": "ﾏ", "ミ": "ﾐ", "ム": "ﾑ", "メ": "ﾒ", "モ": "ﾓ",
        "ヤ": "ﾔ", "ユ": "ﾕ", "ヨ": "ﾖ",
        "ラ": "ﾗ", "リ": "ﾘ", "ル": "ﾙ", "レ": "ﾚ", "ロ": "ﾛ",
        "ワ": "ﾜ", "ヲ": "ｦ", "ン": "ﾝ",
        "ァ": "ｧ", "ィ": "ｨ", "ゥ": "ｩ", "ェ": "ｪ", "ォ": "ｫ",
        "ッ": "ｯ", "ャ": "ｬ", "ュ": "ｭ", "ョ": "ｮ", "ー": "ｰ",
        "ガ": "ｶﾞ", "ギ": "ｷﾞ", "グ": "ｸﾞ", "ゲ": "ｹﾞ", "ゴ": "ｺﾞ",
        "ザ": "ｻﾞ", "ジ": "ｼﾞ", "ズ": "ｽﾞ", "ゼ": "ｾﾞ", "ゾ": "ｿﾞ",
        "ダ": "ﾀﾞ", "ヂ": "ﾁﾞ", "ヅ": "ﾂﾞ", "デ": "ﾃﾞ", "ド": "ﾄﾞ",
        "バ": "ﾊﾞ", "ビ": "ﾋﾞ", "ブ": "ﾌﾞ", "ベ": "ﾍﾞ", "ボ": "ﾎﾞ",
        "パ": "ﾊﾟ", "ピ": "ﾋﾟ", "プ": "ﾌﾟ", "ペ": "ﾍﾟ", "ポ": "ﾎﾟ",
        "ヴ": "ｳﾞ",
    }
    return "".join(table.get(char, char) for char in "".join(katakana))


def regex_groups(match: re.Match) -> Dict[str, str]:
    groups = {}
    for index, value in enumerate(match.groups(), start=1):
        groups["match_{0}".format(index)] = value or ""
    return groups


def match_pattern(pattern: str, match_type: str, content: str) -> Optional[Dict[str, str]]:
    content = normalize_command_text(content)
    pattern = normalize_command_text(pattern)
    if match_type == "exact":
        if content == pattern:
            return {}
        return None
    if match_type == "prefix":
        if content.startswith(pattern):
            return {}
        return None
    if match_type == "contains":
        if pattern in content:
            return {}
        return None
    if match_type == "regex":
        try:
            matched = re.search(pattern, content)
        except re.error as exc:
            print("[WARN] Invalid DB regex pattern {0!r}: {1}".format(pattern, exc))
            return None
        if matched is None:
            return None
        return regex_groups(matched)
    return None


def sort_mention_matches(matches: List[MatchResult]) -> List[MatchResult]:
    return sorted(
        matches,
        key=lambda item: (
            -len(item.row.get("keyword") or ""),
            -MATCH_TYPE_RANK.get(item.row.get("match_type"), 0),
            item.row.get("created_at"),
        ),
    )


def sort_auto_matches(matches: List[MatchResult]) -> List[MatchResult]:
    return sorted(
        matches,
        key=lambda item: (
            -int(item.row.get("priority") or 0),
            -len(item.row.get("trigger_text") or ""),
            item.row.get("created_at"),
        ),
    )


def find_mention_fallback(reactions: List[Dict[str, Any]]) -> Optional[MatchResult]:
    preferred_keys = ("quotes", "quote", "meigen")
    for key in preferred_keys:
        for reaction in reactions:
            if (reaction.get("reaction_key") or "").lower() == key:
                return MatchResult(reaction, {})
    for reaction in reactions:
        keyword = (reaction.get("keyword") or "").strip()
        if not keyword:
            return MatchResult(reaction, {})
    return None


def normalize_command_text(value: str) -> str:
    return " ".join((value or "").replace("\u3000", " ").split())


def choose_weighted_item(weighted_items: List[Tuple[Any, int]]) -> Optional[Any]:
    weighted = []
    total = 0
    for item, weight_value in weighted_items:
        weight = int(weight_value or 1)
        if weight < 1:
            continue
        total += weight
        weighted.append((item, total))
    if total <= 0:
        return None

    selected = random.randint(1, total)
    for item, threshold in weighted:
        if selected <= threshold:
            return item
    return None


def choose_weighted_choice(choices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return choose_weighted_item(
        [
            (choice, int(choice.get("appearance_rate") or choice.get("weight") or 1))
            for choice in choices
        ]
    )


def build_effective_weighted_rows(
    rows: List[Dict[str, Any]],
    target_type: str,
    pending_effects: Optional[List[Dict[str, Any]]] = None,
    weight_keys: Optional[List[str]] = None,
) -> List[Tuple[Dict[str, Any], int]]:
    keys = weight_keys or ["appearance_rate", "weight"]
    pending = pending_effects or []
    weighted = []
    for row in rows:
        row_id = int(row["id"])
        multiplier = get_probability_multiplier_for_target(pending, target_type, row_id)
        base_weight = 1
        for key in keys:
            if row.get(key) is not None:
                base_weight = int(row.get(key) or 1)
                break
        weighted.append((row, max(1, int(round(max(1, base_weight) * multiplier)))))
    return weighted


def choose_weighted_row_with_effects(
    rows: List[Dict[str, Any]],
    target_type: str,
    pending_effects: Optional[List[Dict[str, Any]]] = None,
    weight_keys: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    weighted = build_effective_weighted_rows(rows, target_type, pending_effects, weight_keys)
    selected = choose_weighted_item(weighted)
    return selected if selected is not None else rows[0]


def list_effects(connection, guild_id: str, target_type: str, target_id: int) -> List[Dict[str, Any]]:
    repository = SpecialEffectRepository(connection)
    try:
        effects = repository.list_for_target(guild_id, target_type, target_id, enabled=True)
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        print(
            "[WARN] Failed to load special effect tags for {0}:{1}: {2}".format(
                target_type,
                target_id,
                exc,
            )
        )
        return []
    if effects:
        print(
            "[INFO] Loaded {0} special effect tag(s) for {1}:{2}".format(
                len(effects),
                target_type,
                target_id,
            )
        )
    return effects


def get_message_author_id(message: discord.Message) -> str:
    return str(getattr(getattr(message, "author", None), "id", "") or "")


def list_limited_effects(connection, guild_id: str, message: discord.Message) -> List[Dict[str, Any]]:
    discord_user_id = get_message_author_id(message)
    if not discord_user_id:
        return []
    repository = MentionLimitedEffectRepository(connection)
    try:
        effects = repository.list_effects_for_user(guild_id, discord_user_id, enabled=True)
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        print("[WARN] Failed to load mention limited effects for user {0}: {1}".format(discord_user_id, exc))
        return []
    if effects:
        print("[INFO] Loaded {0} mention limited effect tag(s) for user {1}".format(len(effects), discord_user_id))
    return effects


def merge_effects(*effect_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = []
    seen = set()
    for effects in effect_groups:
        for effect in effects:
            effect_id = effect.get("id")
            key = effect_id if effect_id is not None else ("limited", effect.get("limited_effect_id"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(effect)
    return merged


def pending_effect_key(guild_id: str, message: discord.Message) -> str:
    return "{0}:{1}".format(guild_id, get_message_author_id(message))


def pop_pending_next_effects(guild_id: str, message: discord.Message) -> List[Dict[str, Any]]:
    return _PENDING_NEXT_EFFECTS.pop(pending_effect_key(guild_id, message), [])


def store_pending_next_effects(guild_id: str, message: discord.Message, effects: List[Dict[str, Any]]) -> None:
    if not effects:
        return
    key = pending_effect_key(guild_id, message)
    current = _PENDING_NEXT_EFFECTS.get(key, [])
    _PENDING_NEXT_EFFECTS[key] = (current + effects)[-10:]


def next_action_matches(effect: Dict[str, Any], config: Dict[str, Any], action_type: str) -> bool:
    target_action = get_config_text(config, ["target_action", "action"])
    if target_action in (None, "", "next", "any", "same"):
        return True
    return target_action == action_type


def get_next_action_extra_repeats(effects: List[Dict[str, Any]], action_type: str) -> int:
    repeat_total = 1
    for effect in effects:
        if effect.get("effect_type") != "next_action_count":
            continue
        config = normalize_json(effect.get("effect_config_json"))
        if not next_action_matches(effect, config, action_type):
            continue
        count = get_config_int(config, ["count", "repeat", "times"], 1)
        if count <= 1:
            continue
        repeat_total = max(repeat_total, min(count, MAX_NEXT_ACTION_COUNT))
    return max(0, repeat_total - 1)


def is_shikocchi_counter_set_effect(effect: Dict[str, Any]) -> bool:
    if effect.get("effect_type") != "counter_set":
        return False
    return get_counter_key(normalize_json(effect.get("effect_config_json"))) == "shikocchi_count"


def normalize_json(value) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        import json

        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def parse_probability(config: Dict[str, Any]) -> Optional[Dict[str, int]]:
    source = config.get("probability")
    if not isinstance(source, dict):
        source = config
    numerator = source.get("numerator", 1)
    denominator = source.get("denominator", source.get("chance_denominator"))
    try:
        numerator_value = int(numerator)
        denominator_value = int(denominator)
    except (TypeError, ValueError):
        return None
    if numerator_value <= 0 or denominator_value <= 0:
        return None
    return {"numerator": numerator_value, "denominator": denominator_value}


def probability_hit(config: Dict[str, Any]) -> bool:
    return probability_hit_with_multiplier(config, 1.0)


def probability_hit_with_multiplier(config: Dict[str, Any], multiplier: float) -> bool:
    probability = parse_probability(config)
    if probability is None:
        return True
    threshold = float(probability["numerator"]) * max(0.0, multiplier)
    if threshold >= float(probability["denominator"]):
        return True
    if threshold <= 0:
        return False
    return float(random.randint(1, probability["denominator"])) <= threshold


def get_additional_message(effect: Dict[str, Any]) -> str:
    return (
        effect.get("additional_message")
        or effect.get("additional_text")
        or ""
    )


def format_effect_number(value: float) -> str:
    if abs(value - round(value)) < 0.000001:
        return str(int(round(value)))
    return ("{0:.4f}".format(value)).rstrip("0").rstrip(".")


def format_probability_percent(value: float) -> str:
    percent = max(0.0, min(1.0, value)) * 100.0
    if abs(percent - round(percent)) < 0.000001:
        return "{0}%".format(int(round(percent)))
    return ("{0:.2f}%".format(percent)).rstrip("0").rstrip(".")


def build_probability_template_values(config: Dict[str, Any], multiplier: float) -> Dict[str, str]:
    probability = parse_probability(config)
    if probability is None:
        return {
            "base_probability": "1/1",
            "effective_probability": "1/1",
            "probability_percent": "100%",
        }

    numerator = float(probability["numerator"])
    denominator = float(probability["denominator"])
    effective_numerator = numerator * max(0.0, multiplier)
    effective_ratio = effective_numerator / denominator
    if effective_ratio >= 1.0:
        effective_probability = "1/1"
    else:
        effective_probability = "{0}/{1}".format(
            format_effect_number(effective_numerator),
            probability["denominator"],
        )
    return {
        "base_probability": "{0}/{1}".format(probability["numerator"], probability["denominator"]),
        "effective_probability": effective_probability,
        "probability_percent": format_probability_percent(effective_ratio),
    }


def get_effect_name(effect: Dict[str, Any]) -> str:
    return str(effect.get("name") or effect.get("tag_name") or "")


def build_effect_template_values(
    connection,
    guild_id: str,
    effect: Dict[str, Any],
    config: Dict[str, Any],
    template_values: Dict[str, str],
    effect_multiplier: float = 1.0,
    effective_multiplier: float = 1.0,
    target_name: Optional[str] = None,
) -> Dict[str, str]:
    values = dict(template_values)
    values.update(build_probability_template_values(config, effective_multiplier))
    values.update(
        {
            "effect_multiplier": format_effect_number(effect_multiplier),
            "effective_multiplier": format_effect_number(effective_multiplier),
            "effect_label": get_config_text(config, ["label", "effect_label", "name"]) or "",
            "effect_name": get_effect_name(effect),
            "target_name": target_name or values.get("target_name", ""),
        }
    )
    return values


def get_counter_template_value(connection, guild_id: str, counter_key: str) -> str:
    if connection is None:
        return "0"
    try:
        value = CounterRepository(connection).get_value(guild_id, counter_key, 0)
    except Exception as exc:
        print("[WARN] Failed to resolve counter placeholder {0}: {1}".format(counter_key, exc))
        return "0"
    return str(value)


def render_effect_template(
    text: Optional[str],
    values: Dict[str, str],
    connection,
    guild_id: str,
) -> str:
    rendered = render_template(text, values)
    pattern = re.compile(r"\{counter:([^}]+)\}")

    def replace_counter(match: re.Match) -> str:
        counter_key = match.group(1).strip()
        if not counter_key:
            return "0"
        return get_counter_template_value(connection, guild_id, counter_key)

    return pattern.sub(replace_counter, rendered)


def get_counter_key(config: Dict[str, Any]) -> Optional[str]:
    value = config.get("counter_key") or config.get("count_key") or config.get("key")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def get_config_int(config: Dict[str, Any], keys: List[str], default: int) -> int:
    for key in keys:
        if key not in config:
            continue
        try:
            return int(config[key])
        except (TypeError, ValueError):
            return default
    return default


def get_config_float(config: Dict[str, Any], keys: List[str], default: float) -> float:
    for key in keys:
        if key not in config:
            continue
        try:
            return float(config[key])
        except (TypeError, ValueError):
            return default
    return default


def get_config_text(config: Dict[str, Any], keys: List[str]) -> Optional[str]:
    for key in keys:
        value = config.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def get_config_list(config: Dict[str, Any], keys: List[str]) -> List[str]:
    for key in keys:
        value = config.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).replace(",", "\n").splitlines() if item.strip()]
    return []


def get_effect_target_id(config: Dict[str, Any]) -> Optional[int]:
    target = config.get("target")
    if isinstance(target, dict):
        value = target.get("id") or target.get("target_id")
    else:
        value = config.get("target_id") or config.get("id")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_effect_target_type(config: Dict[str, Any]) -> Optional[str]:
    target = config.get("target")
    if isinstance(target, dict):
        value = target.get("type") or target.get("target_type")
    else:
        value = config.get("target_type")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def effect_targets_candidate(
    effect: Dict[str, Any],
    config: Dict[str, Any],
    target_type: str,
    target_id: int,
) -> bool:
    configured_type = get_effect_target_type(config)
    configured_id = get_effect_target_id(config)
    if configured_type is not None and configured_type != target_type:
        return False
    if configured_id is not None and configured_id != target_id:
        return False
    if configured_type is None and configured_id is None:
        target_action = get_config_text(config, ["target_action", "action"])
        if target_action in (None, "", "next", "any", "same"):
            return True
        return (effect.get("target_type") or "") == target_type
    return True


def get_probability_multiplier_for_target(
    effects: List[Dict[str, Any]],
    target_type: str,
    target_id: int,
) -> float:
    multiplier = 1.0
    for effect in effects:
        if effect.get("effect_type") != "probability_multiplier":
            continue
        config = normalize_json(effect.get("effect_config_json"))
        if not effect_targets_candidate(effect, config, target_type, target_id):
            continue
        value = get_config_float(config, ["multiplier", "rate", "factor"], 1.0)
        if value <= 0:
            print("[WARN] probability_multiplier skipped invalid multiplier: id={0}".format(effect.get("id")))
            continue
        multiplier *= value
        max_multiplier = get_probability_multiplier_limit(effect, config)
        if max_multiplier is not None and multiplier > max_multiplier:
            multiplier = max_multiplier
    return multiplier


def get_probability_multiplier_limit(effect: Dict[str, Any], config: Dict[str, Any]) -> Optional[float]:
    raw_value = effect.get("max_multiplier")
    if raw_value in (None, ""):
        raw_value = config.get("max_multiplier")
    if raw_value in (None, ""):
        raw_value = config.get("max_effective_multiplier")
    if raw_value in (None, ""):
        return None
    try:
        max_multiplier = float(raw_value)
    except (TypeError, ValueError):
        print("[WARN] probability_multiplier ignored invalid max_multiplier: id={0}".format(effect.get("id")))
        return None
    if max_multiplier <= 0:
        print("[WARN] probability_multiplier ignored non-positive max_multiplier: id={0}".format(effect.get("id")))
        return None
    return max_multiplier


def get_probability_multiplier_display_target(
    effect: Dict[str, Any],
    config: Dict[str, Any],
) -> Tuple[str, int]:
    target_type = get_effect_target_type(config)
    target_id = get_effect_target_id(config)
    if target_type is not None and target_id is not None:
        return target_type, target_id
    return "special_effect_tag", int(effect.get("id") or 0)


def get_pending_probability_multipliers(effects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [effect for effect in effects if effect.get("effect_type") == "probability_multiplier"]


async def send_effect_additional_message(
    connection,
    guild_id: str,
    effect: Dict[str, Any],
    config: Dict[str, Any],
    message: discord.Message,
    template_values: Dict[str, str],
    additional: Optional[str],
    effect_multiplier: float = 1.0,
    effective_multiplier: float = 1.0,
) -> bool:
    if not additional:
        return False
    rendered = render_effect_template(
        additional,
        build_effect_template_values(
            connection,
            guild_id,
            effect,
            config,
            template_values,
            effect_multiplier,
            effective_multiplier,
        ),
        connection,
        guild_id,
    )
    if not rendered:
        return False
    await message.channel.send(rendered)
    return True


def mention_has_required_suffix(message: discord.Message, command_text: str, config: Dict[str, Any]) -> bool:
    suffix = get_config_text(config, ["required_suffix", "suffix"]) or "さん"
    normalized_command = normalize_command_text(command_text)
    if normalized_command == suffix or normalized_command.startswith("{0} ".format(suffix)):
        return True

    content = getattr(message, "content", "") or ""
    if re.search(r"<@!?\d+>\s*{0}".format(re.escape(suffix)), content):
        return True

    for pattern in get_config_list(config, ["accepted_patterns"]):
        try:
            if re.search(pattern, content):
                return True
        except re.error:
            if pattern in content:
                return True

    for name in get_config_list(config, ["bot_display_names", "bot_names"]):
        if "{0}{1}".format(name, suffix) in content or "{0} {1}".format(name, suffix) in content:
            return True
    return False


def strip_required_suffix_from_command_text(command_text: str, config: Dict[str, Any]) -> str:
    suffix = get_config_text(config, ["required_suffix", "suffix"]) or "さん"
    normalized_command = normalize_command_text(command_text)
    if normalized_command == suffix:
        return ""
    prefix = "{0} ".format(suffix)
    if normalized_command.startswith(prefix):
        return normalized_command[len(prefix):].strip()
    return command_text


def mention_suffix_guard_applies(effect: Dict[str, Any], config: Dict[str, Any], message: discord.Message) -> bool:
    if effect.get("effect_type") != "mention_suffix_guard":
        return False
    if config.get("enabled") is False:
        return False
    target_user_ids = get_config_list(config, ["target_user_ids", "user_ids"])
    if target_user_ids and get_message_author_id(message) not in target_user_ids:
        return False
    return True


def normalize_command_after_mention_suffix_guard(
    effects: List[Dict[str, Any]],
    message: discord.Message,
    command_text: str,
) -> str:
    for effect in effects:
        config = normalize_json(effect.get("effect_config_json"))
        if not mention_suffix_guard_applies(effect, config, message):
            continue
        if mention_has_required_suffix(message, command_text, config):
            return strip_required_suffix_from_command_text(command_text, config)
    return command_text


async def handle_deck_fetch_since_command(
    connection,
    guild_id: str,
    message: discord.Message,
    command_text: str,
) -> Optional[RuntimeAction]:
    try:
        parsed = parse_deck_fetch_since_command(command_text)
    except ValueError:
        await message.channel.send("日付が読み取れません。例: デッキ 取得日更新 6/27から")
        return RuntimeAction(True)
    if parsed is None:
        return None

    repository = DeckSearchSettingsRepository(connection)
    settings = load_deck_search_settings(connection, guild_id)
    max_lookback_days = settings_max_lookback_days(settings)

    if parsed.action == "show":
        current = settings_fetch_since_date(settings)
        await message.channel.send(
            "デッキ検索の取得開始日: {0}".format(format_fetch_since_date(current))
        )
        return RuntimeAction(True)

    if not can_update_deck_fetch_since(connection, guild_id, message):
        await message.channel.send("取得開始日の変更は管理者だけ。")
        return RuntimeAction(True)

    updated_by = get_message_author_id(message)
    if parsed.action == "reset":
        try:
            repository.clear_fetch_since_date(config.BOT_INSTANCE_ID, guild_id, updated_by, max_lookback_days)
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            await message.channel.send("取得開始日を保存できませんでした。")
            return RuntimeAction(True)
        await message.channel.send("デッキ検索の取得開始日をリセットしました。")
        return RuntimeAction(True)

    if parsed.action == "update" and parsed.fetch_since_date is not None:
        error = validate_fetch_since_date(parsed.fetch_since_date, max_lookback_days)
        if error:
            await message.channel.send(error)
            return RuntimeAction(True)
        try:
            repository.upsert(
                config.BOT_INSTANCE_ID,
                guild_id,
                parsed.fetch_since_date,
                max_lookback_days,
                updated_by,
            )
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            await message.channel.send("取得開始日を保存できませんでした。")
            return RuntimeAction(True)
        await message.channel.send(
            "デッキ検索の取得開始日: {0}".format(parsed.fetch_since_date.isoformat())
        )
        return RuntimeAction(True)

    return None


async def apply_mention_suffix_guards(
    connection,
    guild_id: str,
    effects: List[Dict[str, Any]],
    message: discord.Message,
    command_text: str,
) -> Optional[RuntimeAction]:
    for effect in effects:
        config = normalize_json(effect.get("effect_config_json"))
        if not mention_suffix_guard_applies(effect, config, message):
            continue
        if mention_has_required_suffix(message, command_text, config):
            return None

        user_id = get_message_author_id(message)
        effect_id = str(effect.get("id") or effect.get("limited_effect_id") or "suffix_guard")
        counter_key = "mention_suffix_guard:{0}:{1}".format(effect_id, user_id)
        warn_every = max(1, get_config_int(config, ["warn_every"], 3))
        warning_message = get_config_text(config, ["warning_message", "message"]) or "さんを付けろよ"
        repository = CounterRepository(connection)
        repository.ensure_counter(guild_id, counter_key, counter_key)
        state = repository.increment(guild_id, counter_key, 1)
        try:
            connection.commit()
        except Exception:
            pass
        current_value = int(state.get("current_value") or 0)
        if current_value % warn_every == 0:
            await message.channel.send(render_template(warning_message, build_template_values(message, command_text, {})))
            return RuntimeAction(True, True)
        return RuntimeAction(True, True)
    return None


def choose_weighted_choice_with_effects(
    connection,
    guild_id: str,
    choices: List[Dict[str, Any]],
    pending_effects: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    return choose_weighted_row_with_effects(
        choices,
        "mention_reaction_choice",
        pending_effects,
        ["appearance_rate", "weight"],
    )


def choose_auto_match_with_effects(
    connection,
    guild_id: str,
    matches: List[MatchResult],
    pending_effects: Optional[List[Dict[str, Any]]] = None,
) -> Optional[MatchResult]:
    if not matches:
        return None
    sorted_matches = sort_auto_matches(matches)
    top_priority = int(sorted_matches[0].row.get("priority") or 0)
    candidates = [match for match in sorted_matches if int(match.row.get("priority") or 0) == top_priority]
    weighted = []
    total = 0
    has_multiplier = False
    pending = pending_effects or []
    for match in candidates:
        reaction_id = int(match.row["id"])
        multiplier = get_probability_multiplier_for_target(pending, "auto_reaction", reaction_id)
        if multiplier != 1.0:
            has_multiplier = True
        base_weight = max(1, len(match.row.get("trigger_text") or ""))
        weight = max(1, int(round(base_weight * multiplier)))
        weighted.append((match, weight))
        total += weight
    if not has_multiplier or total <= 0:
        return sorted_matches[0]
    roll = random.randint(1, total)
    cursor = 0
    for match, weight in weighted:
        cursor += weight
        if roll <= cursor:
            return match
    return weighted[-1][0]


async def add_message_reaction_safe(message: discord.Message, emoji: str, context: str) -> bool:
    emoji_value = (emoji or "").strip()
    if not emoji_value:
        return False
    try:
        await message.add_reaction(emoji_value)
        return True
    except discord.DiscordException as exc:
        print("[WARN] Failed to add DB {0} emoji: {1}".format(context, exc))
    except Exception as exc:
        print("[WARN] Failed to add DB {0} emoji: {1}".format(context, exc))
    return False


async def repeat_text_image_action(
    message: discord.Message,
    text: str,
    image_path: str,
    emoji: str,
    repeat_count: int,
) -> bool:
    handled = False
    for _ in range(repeat_count):
        if await send_text_or_image(message.channel, text, image_path):
            handled = True
        if await add_message_reaction_safe(message, emoji, "repeated reaction"):
            handled = True
    return handled


async def execute_destroy_effect(
    connection,
    guild_id: str,
    effect: Dict[str, Any],
    message: discord.Message,
    template_values: Dict[str, str],
    config: Dict[str, Any],
) -> EffectExecutionResult:
    result = EffectExecutionResult()
    action = get_config_text(config, ["action"])
    reason = get_config_text(config, ["reason"]) or ""
    if action == "log_only":
        print(
            "[INFO] destroy effect log_only: id={0} reason={1}".format(
                effect.get("id"),
                render_effect_template(
                    reason,
                    build_effect_template_values(connection, guild_id, effect, config, template_values),
                    connection,
                    guild_id,
                ),
            )
        )
        return result

    if action == "send_message":
        text = get_config_text(config, ["message", "text"]) or get_additional_message(effect)
        if not text:
            print("[WARN] destroy send_message skipped without message: id={0}".format(effect.get("id")))
            return result
        await message.channel.send(
            render_effect_template(
                text,
                build_effect_template_values(connection, guild_id, effect, config, template_values),
                connection,
                guild_id,
            )
        )
        print("[INFO] destroy effect send_message executed: id={0}".format(effect.get("id")))
        return result

    if action == "counter_reset":
        counter_key = get_counter_key(config)
        if counter_key is None:
            print("[WARN] destroy counter_reset skipped without counter_key: id={0}".format(effect.get("id")))
            return result
        if connection is None:
            print("[WARN] destroy counter_reset skipped without DB connection: id={0}".format(effect.get("id")))
            return result
        value = get_config_int(config, ["value", "set_value", "count"], 0)
        repository = CounterRepository(connection)
        repository.ensure_counter(guild_id, counter_key, counter_key)
        repository.set_value(guild_id, counter_key, value)
        result.count_changed = True
        print(
            "[INFO] destroy effect counter_reset executed: id={0} counter_key={1} value={2}".format(
                effect.get("id"),
                counter_key,
                value,
            )
        )
        return result

    print("[WARN] destroy effect skipped unsupported action: id={0} action={1}".format(effect.get("id"), action))
    return result


async def execute_effects(
    connection,
    guild_id: str,
    effects: List[Dict[str, Any]],
    message: discord.Message,
    template_values: Dict[str, str],
    pending_effects: Optional[List[Dict[str, Any]]] = None,
) -> EffectExecutionResult:
    result = EffectExecutionResult()
    pending = pending_effects or []
    carried_probability_multipliers = False
    for effect in effects:
        try:
            config = normalize_json(effect.get("effect_config_json"))
            effect_type = effect.get("effect_type")
            if effect_type == "probability_message":
                multiplier = get_probability_multiplier_for_target(
                    pending,
                    "special_effect_tag",
                    int(effect.get("id") or 0),
                )
                if not probability_hit_with_multiplier(config, multiplier):
                    continue
                additional = get_additional_message(effect)
                timing = effect.get("additional_message_timing") or effect.get("additional_post_timing")
                if additional and timing in (None, "", "effect_success", "tag_triggered"):
                    await send_effect_additional_message(
                        connection,
                        guild_id,
                        effect,
                        config,
                        message,
                        template_values,
                        additional,
                        multiplier,
                        multiplier,
                    )
            elif effect_type == "message":
                additional = get_additional_message(effect) or get_config_text(
                    config,
                    ["message", "text", "additional_message", "additional_text"],
                )
                timing = effect.get("additional_message_timing") or effect.get("additional_post_timing")
                if additional and timing in (None, "", "effect_success", "tag_triggered"):
                    await send_effect_additional_message(
                        connection,
                        guild_id,
                        effect,
                        config,
                        message,
                        template_values,
                        additional,
                    )
            elif effect_type == "reaction":
                emoji = get_config_text(config, ["emoji", "reaction", "emoji_internal"])
                if not emoji:
                    print("[WARN] reaction effect skipped without emoji")
                    continue
                try:
                    await message.add_reaction(emoji)
                except discord.DiscordException as exc:
                    print("[WARN] Failed to add special effect reaction {0!r}: {1}".format(emoji, exc))
            elif effect_type == "counter_delta":
                counter_key = get_counter_key(config)
                if counter_key is None:
                    print("[WARN] counter_delta skipped without counter_key")
                    continue
                delta = get_config_int(config, ["delta", "amount", "value"], 1)
                repository = CounterRepository(connection)
                repository.ensure_counter(guild_id, counter_key, counter_key)
                repository.increment(guild_id, counter_key, delta, get_counter_period_key(connection, guild_id, counter_key))
                result.count_changed = True
                additional = get_additional_message(effect)
                timing = effect.get("additional_message_timing") or effect.get("additional_post_timing")
                if additional and timing in (None, "", "effect_success", "tag_triggered"):
                    await send_effect_additional_message(
                        connection,
                        guild_id,
                        effect,
                        config,
                        message,
                        template_values,
                        additional,
                    )
            elif effect_type == "counter_set":
                if is_shikocchi_counter_set_effect(effect) and not shikocchi_mode_allows_trigger(connection, guild_id):
                    print("[INFO] shikocchi counter_set skipped by mode cooldown: id={0}".format(effect.get("id")))
                    continue
                multiplier = get_probability_multiplier_for_target(
                    pending,
                    "special_effect_tag",
                    int(effect.get("id") or 0),
                )
                if not probability_hit_with_multiplier(config, multiplier):
                    continue
                counter_key = get_counter_key(config)
                if counter_key is None:
                    print("[WARN] counter_set skipped without counter_key")
                    continue
                value = get_config_int(config, ["set_value", "value", "count"], 1)
                repository = CounterRepository(connection)
                repository.ensure_counter(guild_id, counter_key, counter_key)
                repository.set_value(guild_id, counter_key, value, get_counter_period_key(connection, guild_id, counter_key))
                result.count_changed = True
                additional = get_additional_message(effect)
                timing = effect.get("additional_message_timing") or effect.get("additional_post_timing")
                if additional and timing in (None, "", "effect_success", "tag_triggered"):
                    await send_effect_additional_message(
                        connection,
                        guild_id,
                        effect,
                        config,
                        message,
                        template_values,
                        additional,
                        multiplier,
                        multiplier,
                    )
            elif effect_type == "probability_multiplier":
                multiplier = get_config_float(config, ["multiplier", "rate", "factor"], 1.0)
                if multiplier <= 0:
                    print("[WARN] probability_multiplier skipped invalid multiplier: id={0}".format(effect.get("id")))
                else:
                    if not carried_probability_multipliers:
                        result.pending_effects.extend(get_pending_probability_multipliers(pending))
                        carried_probability_multipliers = True
                    result.pending_effects.append(effect)
                    display_target_type, display_target_id = get_probability_multiplier_display_target(effect, config)
                    effective_multiplier = get_probability_multiplier_for_target(
                        result.pending_effects,
                        display_target_type,
                        display_target_id,
                    )
                    additional = get_additional_message(effect)
                    timing = effect.get("additional_message_timing") or effect.get("additional_post_timing")
                    if additional and timing in (None, "", "effect_success", "tag_triggered"):
                        await send_effect_additional_message(
                            connection,
                            guild_id,
                            effect,
                            config,
                            message,
                            template_values,
                            additional,
                            multiplier,
                            effective_multiplier,
                        )
                    print("[INFO] probability_multiplier queued for next action: id={0}".format(effect.get("id")))
            elif effect_type == "next_action_count":
                target_action = get_config_text(config, ["target_action", "action"]) or "next"
                if target_action not in ("same", "next", "any", "mention_reaction_choice", "auto_reaction"):
                    print("[WARN] next_action_count skipped invalid target_action: {0}".format(target_action))
                    continue
                count = get_config_int(config, ["count", "repeat", "times"], 1)
                if count <= 0:
                    print("[WARN] next_action_count skipped invalid count: {0}".format(count))
                    continue
                result.pending_effects.append(effect)
                print("[INFO] next_action_count queued for next action: id={0}".format(effect.get("id")))
            elif effect_type == "destroy":
                destroy_result = await execute_destroy_effect(
                    connection,
                    guild_id,
                    effect,
                    message,
                    template_values,
                    config,
                )
                result.count_changed = result.count_changed or destroy_result.count_changed
        except Exception as exc:
            print("[WARN] Failed to execute special effect {0}: {1}".format(effect.get("id"), exc))
            try:
                connection.rollback()
            except Exception:
                pass
    result.repeat_count = min(result.repeat_count, MAX_NEXT_ACTION_COUNT)
    return result


def find_ng_word_match(connection, guild_id: str, content: str) -> Optional[Dict[str, Any]]:
    if not feature_enabled(connection, guild_id, FEATURE_NG_WORDS):
        return None

    repository = NgWordRepository(connection)
    normalized_content = normalize_ng_match_text(content)
    for row in repository.list_words(guild_id, enabled=True):
        word = row.get("word") or ""
        if not word:
            continue
        if normalize_ng_match_text(word) in normalized_content:
            return row
    return None


def message_has_ng_word(connection, guild_id: str, content: str) -> bool:
    return find_ng_word_match(connection, guild_id, content) is not None


async def process_db_mention(message: discord.Message, guild_id: str, connection) -> RuntimeAction:
    if not feature_enabled(connection, guild_id, FEATURE_MENTION_REACTIONS):
        return RuntimeAction(False)

    command_text = get_mention_command_text(message)
    if command_text is None:
        return RuntimeAction(False)

    repository = MentionReactionRepository(connection)
    limited_effects = list_limited_effects(connection, guild_id, message)
    suffix_guard_result = await apply_mention_suffix_guards(
        connection,
        guild_id,
        limited_effects,
        message,
        command_text,
    )
    if suffix_guard_result is not None:
        return suffix_guard_result
    command_text = normalize_command_after_mention_suffix_guard(limited_effects, message, command_text)
    deck_settings_action = await handle_deck_fetch_since_command(connection, guild_id, message, command_text)
    if deck_settings_action is not None:
        return deck_settings_action
    pending_effects = pop_pending_next_effects(guild_id, message)
    search_matches = []
    for reaction in repository.list_reactions(guild_id, enabled=True, reaction_kind="search"):
        groups = match_pattern(
            reaction.get("keyword") or "",
            reaction.get("match_type") or "exact",
            command_text,
        )
        if groups is not None:
            search_matches.append(MatchResult(reaction, groups))
    if search_matches:
        selected_search = sort_mention_matches(search_matches)[0]
        values = build_template_values(message, command_text, selected_search.groups)
        config_json = normalize_json(selected_search.row.get("config_json"))
        if config_json.get("search_type") == "deck_search":
            deck_settings = load_deck_search_settings(connection, guild_id)
            config_json = apply_deck_search_settings(config_json, deck_settings)
            response = await search_decks(
                guild_id,
                str(getattr(message.channel, "id", "")),
                command_text,
                config_json,
            )
            await message.channel.send(response)
            count_changed = False
            if limited_effects:
                effect_result = await execute_effects(connection, guild_id, limited_effects, message, values)
                count_changed = effect_result.count_changed
                store_pending_next_effects(guild_id, message, effect_result.pending_effects)
            return RuntimeAction(True, count_changed, effect_result.pending_effects if limited_effects else [])
        effect_result = await execute_effects(connection, guild_id, limited_effects, message, values)
        store_pending_next_effects(guild_id, message, effect_result.pending_effects)
        return RuntimeAction(bool(limited_effects), effect_result.count_changed, effect_result.pending_effects)

    reactions = repository.list_reactions(guild_id, enabled=True, reaction_kind="random_draw")
    matches = []
    for reaction in reactions:
        groups = match_pattern(reaction.get("keyword") or "", reaction.get("match_type") or "exact", command_text)
        if groups is not None:
            matches.append(MatchResult(reaction, groups))
    if not matches:
        fallback = find_mention_fallback(reactions)
        if fallback is None:
            store_pending_next_effects(guild_id, message, pending_effects)
            return RuntimeAction(False)
        matches = [fallback]

    selected = sort_mention_matches(matches)[0]
    choices = repository.list_choices(guild_id, int(selected.row["id"]), enabled=True)
    choice = choose_weighted_choice_with_effects(connection, guild_id, choices, pending_effects)
    if choice is None:
        store_pending_next_effects(guild_id, message, pending_effects)
        return RuntimeAction(False)

    values = build_template_values(message, command_text, selected.groups)
    text = render_choice_body(choice, values)
    image_path = choice.get("image_path") or ""
    emoji = choice.get("emoji_internal") or ""
    handled = await send_text_or_image(message.channel, text, image_path)
    if await add_message_reaction_safe(message, emoji, "mention reaction choice"):
        handled = True
    pending_repeats = get_next_action_extra_repeats(pending_effects, "mention_reaction_choice")
    if pending_repeats:
        repeated = await repeat_text_image_action(message, text, image_path, emoji, pending_repeats)
        handled = handled or repeated
    choice_effects = list_effects(connection, guild_id, "mention_reaction_choice", int(choice["id"]))
    effects = merge_effects(choice_effects, limited_effects)
    effect_result = await execute_effects(connection, guild_id, effects, message, values, pending_effects)
    store_pending_next_effects(guild_id, message, effect_result.pending_effects)
    if effect_result.repeat_count:
        repeated = await repeat_text_image_action(message, text, image_path, emoji, effect_result.repeat_count)
        handled = handled or repeated
    return RuntimeAction(handled or bool(effects), effect_result.count_changed, effect_result.pending_effects)


async def handle_db_mention(message: discord.Message, guild_id: str, connection) -> bool:
    return (await process_db_mention(message, guild_id, connection)).handled


async def process_db_auto_reaction(message: discord.Message, guild_id: str, connection) -> RuntimeAction:
    if not feature_enabled(connection, guild_id, FEATURE_AUTO_REACTIONS):
        return RuntimeAction(False)

    repository = AutoReactionRepository(connection)
    reactions = repository.list_reactions(guild_id, enabled=True)
    matches = []
    for reaction in reactions:
        groups = match_pattern(
            reaction.get("trigger_text") or "",
            reaction.get("match_type") or "contains",
            message.content,
        )
        if groups is not None:
            matches.append(MatchResult(reaction, groups))
    if not matches:
        return RuntimeAction(False)

    pending_effects = pop_pending_next_effects(guild_id, message)
    selected = choose_auto_match_with_effects(connection, guild_id, matches, pending_effects)
    if selected is None:
        store_pending_next_effects(guild_id, message, pending_effects)
        return RuntimeAction(False)
    values = build_template_values(message, message.content, selected.groups)
    text = render_template(selected.row.get("response_text"), values)
    image_path = selected.row.get("image_path") or ""
    effects = list_effects(connection, guild_id, "auto_reaction", int(selected.row["id"]))
    effect_result = None
    if any(is_shikocchi_counter_set_effect(effect) for effect in effects):
        effect_result = await execute_effects(connection, guild_id, effects, message, values, pending_effects)
        store_pending_next_effects(guild_id, message, effect_result.pending_effects)
        if effect_result.count_changed:
            return RuntimeAction(True, True, effect_result.pending_effects)

    sent = await send_text_or_image(message.channel, text, image_path)

    emoji = selected.row.get("emoji_internal") or ""
    if await add_message_reaction_safe(message, emoji, "auto reaction"):
        sent = True

    pending_repeats = get_next_action_extra_repeats(pending_effects, "auto_reaction")
    if pending_repeats:
        repeated = await repeat_text_image_action(message, text, image_path, emoji, pending_repeats)
        sent = sent or repeated

    if effect_result is None:
        effect_result = await execute_effects(connection, guild_id, effects, message, values, pending_effects)
        store_pending_next_effects(guild_id, message, effect_result.pending_effects)
    if effect_result.repeat_count:
        repeated = await repeat_text_image_action(message, text, image_path, emoji, effect_result.repeat_count)
        sent = sent or repeated
    return RuntimeAction(sent or bool(effects), effect_result.count_changed, effect_result.pending_effects)


async def handle_db_auto_reaction(message: discord.Message, guild_id: str, connection) -> bool:
    return (await process_db_auto_reaction(message, guild_id, connection)).handled


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def get_condition_config(condition: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_json(condition.get("condition_config_json"))


def get_threshold_value(config: Dict[str, Any]) -> int:
    return get_config_int(config, ["threshold", "value", "count"], 1)


def compare_counter(value: int, operator: str, threshold: int) -> bool:
    if operator == ">":
        return value > threshold
    if operator == "==":
        return value == threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    return value >= threshold


def normalize_period_config(config: Dict[str, Any], mode: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fallback = normalize_json(mode.get("cooldown_config_json")) if mode is not None else {}
    period = config.get("period") or fallback.get("period") or "monthly"
    reset = config.get("reset") if "reset" in config else fallback.get("reset")
    day = config.get("day") if "day" in config else fallback.get("day")

    reset_type = "month_start"
    if isinstance(reset, dict):
        day = reset.get("day", day)
        reset_type = reset.get("type") or ("day" if day is not None else "month_start")
    elif isinstance(reset, str):
        reset_type = reset
    elif day is not None:
        reset_type = "day"

    if reset_type == "monthly":
        reset_type = "month_start"
    if reset_type not in ("month_start", "day"):
        reset_type = "month_start"

    try:
        day_value = int(day) if day is not None else None
    except (TypeError, ValueError):
        day_value = None
    if day_value is not None:
        day_value = max(1, min(31, day_value))

    return {
        "period": period,
        "reset_type": reset_type,
        "day": day_value,
    }


def previous_month(year: int, month: int) -> Tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def bounded_month_day(year: int, month: int, day: int) -> int:
    return min(day, calendar.monthrange(year, month)[1])


def build_mode_period_info(
    config: Dict[str, Any],
    mode: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    normalized = normalize_period_config(config, mode)
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(JST)

    if normalized["period"] != "monthly":
        period_start = datetime(local_now.year, local_now.month, 1, tzinfo=JST)
        period_key = "monthly:{0}".format(period_start.date().isoformat())
        return {"period_start": period_start, "period_key": period_key}

    if normalized["reset_type"] == "day" and normalized["day"] is not None:
        day = int(normalized["day"])
        current_day = bounded_month_day(local_now.year, local_now.month, day)
        current_start = datetime(local_now.year, local_now.month, current_day, tzinfo=JST)
        if local_now < current_start:
            prev_year, prev_month_value = previous_month(local_now.year, local_now.month)
            prev_day = bounded_month_day(prev_year, prev_month_value, day)
            period_start = datetime(prev_year, prev_month_value, prev_day, tzinfo=JST)
        else:
            period_start = current_start
        period_key = "monthly-day-{0}:{1}".format(day, period_start.date().isoformat())
        return {"period_start": period_start, "period_key": period_key}

    period_start = datetime(local_now.year, local_now.month, 1, tzinfo=JST)
    period_key = "monthly:{0}".format(period_start.date().isoformat())
    return {"period_start": period_start, "period_key": period_key}


def get_counter_period_key(
    connection,
    guild_id: str,
    counter_key: str,
    now: Optional[datetime] = None,
) -> Optional[str]:
    try:
        counter = CounterRepository(connection).get_counter(guild_id, counter_key)
    except Exception:
        return None
    if counter is None:
        return None

    reset_type = str(counter.get("reset_type") or "").lower()
    if reset_type in ("monthly_day", "monthly-day", "once_per_month_day"):
        period_info = build_mode_period_info(
            {
                "period": "monthly",
                "reset": "day",
                "day": counter.get("reset_day"),
            },
            None,
            now,
        )
        return "counter:{0}".format(period_info["period_key"])
    if reset_type in ("monthly", "month_start"):
        period_info = build_mode_period_info(
            {
                "period": "monthly",
                "reset": "month_start",
            },
            None,
            now,
        )
        return "counter:{0}".format(period_info["period_key"])
    return None


def period_not_triggered_met(
    connection,
    guild_id: str,
    mode: Dict[str, Any],
    condition: Dict[str, Any],
) -> bool:
    period_info = build_mode_period_info(get_condition_config(condition), mode)
    history = ModeRepository(connection).get_trigger_history(
        guild_id,
        int(mode["id"]),
        str(period_info["period_key"]),
    )
    return history is None


def get_mode_once_per_period_info(
    mode: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    cooldown = normalize_json(mode.get("cooldown_config_json"))
    if cooldown.get("type") != "once_per_period":
        return None
    period_info = build_mode_period_info(cooldown, None, now)
    return {
        "period_start": period_info["period_start"],
        "period_key": "cooldown:{0}".format(period_info["period_key"]),
    }


def get_mode_counter_period_info(
    mode: Dict[str, Any],
    conditions: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    cooldown = normalize_json(mode.get("cooldown_config_json"))
    if cooldown.get("type") == "once_per_period":
        period_info = build_mode_period_info(cooldown, None, now)
        return {
            "period_start": period_info["period_start"],
            "period_key": "counter:{0}".format(period_info["period_key"]),
            "source": "cooldown_config_json",
        }

    for condition in conditions:
        if condition.get("condition_type") != "period_not_triggered":
            continue
        period_info = build_mode_period_info(get_condition_config(condition), mode, now)
        return {
            "period_start": period_info["period_start"],
            "period_key": "counter:{0}".format(period_info["period_key"]),
            "source": "period_not_triggered",
        }
    return None


def reset_counter_thresholds_on_period_change(
    connection,
    guild_id: str,
    mode: Dict[str, Any],
    conditions: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> None:
    period_info = get_mode_counter_period_info(mode, conditions, now)
    if period_info is None:
        return

    counter_repository = CounterRepository(connection)
    period_key = str(period_info["period_key"])
    for condition in conditions:
        if condition.get("condition_type") != "counter_threshold":
            continue
        counter_key = get_counter_key(get_condition_config(condition))
        if counter_key is None:
            continue
        try:
            state = counter_repository.get_state(guild_id, counter_key)
            current_period_key = str(state.get("period_key") or "") if state is not None else ""
            if current_period_key == period_key:
                continue
            counter_repository.set_value(guild_id, counter_key, 0, period_key)
            print(
                "[INFO] Reset mode trigger counter for new period: mode_key={0} counter_key={1} period_key={2}".format(
                    mode.get("mode_key"),
                    counter_key,
                    period_key,
                )
            )
        except Exception as exc:
            print("[WARN] Failed to reset period counter {0}: {1}".format(counter_key, exc))


def mode_cooldown_allows_trigger(connection, guild_id: str, mode: Dict[str, Any]) -> bool:
    period_info = get_mode_once_per_period_info(mode)
    if period_info is None:
        return True
    history = ModeRepository(connection).get_trigger_history(
        guild_id,
        int(mode["id"]),
        str(period_info["period_key"]),
    )
    return history is None


def record_mode_period_trigger(connection, guild_id: str, mode: Dict[str, Any]) -> None:
    repository = ModeRepository(connection)
    mode_id = int(mode["id"])
    for condition in repository.list_trigger_conditions(guild_id, mode_id, enabled=True):
        if condition.get("condition_type") != "period_not_triggered":
            continue
        period_info = build_mode_period_info(get_condition_config(condition), mode)
        repository.record_trigger_history(
            guild_id,
            mode_id,
            str(period_info["period_key"]),
            {
                "mode_key": mode.get("mode_key"),
                "period_start": period_info["period_start"].isoformat(),
            },
        )
    cooldown_period_info = get_mode_once_per_period_info(mode)
    if cooldown_period_info is not None:
        repository.record_trigger_history(
            guild_id,
            mode_id,
            str(cooldown_period_info["period_key"]),
            {
                "mode_key": mode.get("mode_key"),
                "period_start": cooldown_period_info["period_start"].isoformat(),
                "source": "cooldown_config_json",
            },
        )


def shikocchi_mode_allows_trigger(connection, guild_id: str) -> bool:
    try:
        repository = ModeRepository(connection)
        found_shikocchi_trigger = False
        for mode in repository.list_enabled_modes(guild_id):
            for condition in repository.list_trigger_conditions(guild_id, int(mode["id"]), enabled=True):
                if condition.get("condition_type") != "counter_threshold":
                    continue
                if get_counter_key(get_condition_config(condition)) != "shikocchi_count":
                    continue
                found_shikocchi_trigger = True
                if mode_cooldown_allows_trigger(connection, guild_id, mode):
                    return True
        return not found_shikocchi_trigger
    except Exception as exc:
        print("[WARN] shikocchi mode cooldown check skipped: {0}".format(exc))
        return True


def trigger_condition_met(
    connection,
    guild_id: str,
    mode: Dict[str, Any],
    condition: Dict[str, Any],
    pending_effects: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    condition_type = condition.get("condition_type")
    config = get_condition_config(condition)
    if condition_type == "counter_threshold":
        counter_key = get_counter_key(config)
        if counter_key is None:
            return False
        value = CounterRepository(connection).get_value(guild_id, counter_key, 0)
        return compare_counter(value, config.get("operator", ">="), get_threshold_value(config))
    if condition_type == "probability":
        multiplier = get_probability_multiplier_for_target(
            pending_effects or [],
            "mode_trigger_condition",
            int(condition.get("id") or 0),
        )
        return probability_hit_with_multiplier(config, multiplier)
    if condition_type == "period_not_triggered":
        return period_not_triggered_met(connection, guild_id, mode, condition)
    return False


def mode_triggers_met(
    connection,
    guild_id: str,
    mode: Dict[str, Any],
    pending_effects: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    repository = ModeRepository(connection)
    conditions = repository.list_trigger_conditions(guild_id, int(mode["id"]), enabled=True)
    actionable = [
        condition
        for condition in conditions
        if condition.get("condition_type") in ("counter_threshold", "probability", "period_not_triggered")
    ]
    if not actionable:
        return False

    reset_counter_thresholds_on_period_change(connection, guild_id, mode, actionable)

    operator = actionable[0].get("group_operator") or "AND"
    results = [trigger_condition_met(connection, guild_id, mode, condition, pending_effects) for condition in actionable]
    if operator == "OR":
        trigger_allowed = any(results)
    else:
        trigger_allowed = all(results)
    if not trigger_allowed:
        return False
    return mode_cooldown_allows_trigger(connection, guild_id, mode)


def reset_counter_thresholds(connection, guild_id: str, mode_id: int) -> None:
    repository = ModeRepository(connection)
    counter_repository = CounterRepository(connection)
    for condition in repository.list_trigger_conditions(guild_id, mode_id, enabled=True):
        if condition.get("condition_type") != "counter_threshold":
            continue
        counter_key = get_counter_key(get_condition_config(condition))
        if counter_key is None:
            continue
        try:
            counter_repository.reset(guild_id, counter_key)
        except Exception as exc:
            print("[WARN] Failed to reset mode trigger counter {0}: {1}".format(counter_key, exc))


def get_duration_seconds_from_exit(connection, guild_id: str, mode_id: int) -> Optional[int]:
    repository = ModeRepository(connection)
    for condition in repository.list_exit_conditions(guild_id, mode_id, enabled=True):
        if condition.get("condition_type") not in ("duration", "duration_elapsed"):
            continue
        config = get_condition_config(condition)
        seconds = get_config_int(config, ["seconds", "duration_seconds", "duration"], 0)
        if seconds > 0:
            return seconds
    return None


def get_mode_duration_seconds(connection, guild_id: str, mode: Dict[str, Any]) -> Optional[int]:
    try:
        duration_seconds = int(mode.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        duration_seconds = 0
    if duration_seconds > 0:
        return duration_seconds
    return get_duration_seconds_from_exit(connection, guild_id, int(mode["id"]))


def get_mode_nickname(mode: Dict[str, Any]) -> str:
    appearance = normalize_json(mode.get("appearance_config_json"))
    configured = get_config_text(appearance, ["nickname", "bot_nickname", "display_name"])
    if configured:
        return configured
    mode_key = (mode.get("mode_key") or "").lower()
    if "shikocchi" in mode_key:
        return "しこっち"
    return str(mode.get("name") or "")


async def update_bot_status(status: discord.Status) -> None:
    try:
        await get_bot().change_presence(status=status)
    except Exception as exc:
        print("[WARN] Failed to change bot status: {0}".format(exc))


async def apply_mode_identity(message: discord.Message, mode: Dict[str, Any]) -> None:
    nickname = get_mode_nickname(mode)
    if nickname:
        await update_bot_nickname(message.channel, nickname)
    icon_path = mode.get("mode_icon_path") or ""
    if icon_path:
        await update_bot_avatar(icon_path)
    if (mode.get("behavior_type") or "") == "offline":
        await update_bot_status(discord.Status.invisible)


async def apply_normal_identity_to_channel(channel: discord.abc.Messageable) -> None:
    await update_bot_nickname(channel, config.NORMAL_BOT_NICKNAME)
    await update_bot_avatar(config.NORMAL_AVATAR)
    await update_bot_status(discord.Status.online)


async def apply_normal_identity(message: discord.Message) -> None:
    await apply_normal_identity_to_channel(message.channel)


async def send_mode_enter_message(message: discord.Message, mode: Dict[str, Any]) -> None:
    enter_text = mode.get("enter_message") or mode.get("enter_text") or ""
    enter_image = mode.get("enter_gif_path") or ""
    if enter_text or enter_image:
        await send_text_or_image(message.channel, enter_text, enter_image)


async def send_mode_exit_message(message: discord.Message, mode: Dict[str, Any]) -> None:
    await send_mode_exit_message_to_channel(message.channel, mode)


async def send_mode_exit_message_to_channel(channel: discord.abc.Messageable, mode: Dict[str, Any]) -> None:
    exit_text = mode.get("exit_message") or mode.get("exit_text") or ""
    exit_image = mode.get("exit_gif_path") or ""
    if exit_text or exit_image:
        await send_text_or_image(channel, exit_text, exit_image)
    mode_key = (mode.get("mode_key") or "").lower()
    mode_name = (mode.get("name") or "").lower()
    if "shikocchi" in mode_key or "しこっち" in mode_name:
        await channel.send(SHIKOCCHI_RECOVERY_MESSAGE)


async def enter_mode_if_needed(
    message: discord.Message,
    guild_id: str,
    connection,
    pending_effects: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    repository = ModeRepository(connection)
    state = repository.get_mode_state(guild_id)
    if state and state.get("current_mode_id"):
        return False

    for mode in repository.list_enabled_modes(guild_id):
        try:
            if not mode_triggers_met(connection, guild_id, mode, pending_effects):
                continue
            duration = get_mode_duration_seconds(connection, guild_id, mode)
            active_until = utc_now() + timedelta(seconds=duration) if duration else None
            repository.enter_mode(
                guild_id,
                int(mode["id"]),
                active_until,
                {
                    "entered_by": "runtime_mvp",
                    "mode_key": mode.get("mode_key"),
                    "channel_id": str(getattr(message.channel, "id", "") or ""),
                },
            )
            record_mode_period_trigger(connection, guild_id, mode)
            reset_counter_thresholds(connection, guild_id, int(mode["id"]))
            connection.commit()
            await send_mode_enter_message(message, mode)
            await apply_mode_identity(message, mode)
            return True
        except Exception as exc:
            print("[WARN] Failed to evaluate/enter mode {0}: {1}".format(mode.get("id"), exc))
            try:
                connection.rollback()
            except Exception:
                pass
    return False


async def expire_mode_if_needed(message: discord.Message, guild_id: str, connection) -> bool:
    repository = ModeRepository(connection)
    state = repository.get_mode_state(guild_id)
    if not state or not state.get("current_mode_id"):
        return False
    active_until = parse_datetime(state.get("active_until"))
    if active_until is None or active_until > utc_now():
        return False

    mode = repository.get_by_id(guild_id, int(state["current_mode_id"]))
    return await expire_mode_state(
        connection,
        guild_id,
        state,
        mode,
        message.channel,
        {"ended_by": "duration", "ended_at": utc_now().isoformat()},
    )


async def expire_mode_state(
    connection,
    guild_id: str,
    state: Dict[str, Any],
    mode: Optional[Dict[str, Any]],
    channel: Optional[discord.abc.Messageable],
    state_json: Optional[Dict[str, Any]] = None,
) -> bool:
    repository = ModeRepository(connection)
    current_mode_id = state.get("current_mode_id")
    if not current_mode_id:
        return False
    latest_state = repository.get_mode_state(guild_id)
    if not latest_state or latest_state.get("current_mode_id") != current_mode_id:
        return False
    repository.clear_mode_state(guild_id, state_json or {"ended_by": "duration", "ended_at": utc_now().isoformat()})
    connection.commit()
    if mode is not None and channel is not None:
        await send_mode_exit_message_to_channel(channel, mode)
    if channel is not None:
        await apply_normal_identity_to_channel(channel)
    return True


def get_state_json_value(state: Dict[str, Any], key: str) -> Optional[str]:
    value = state.get("state_json")
    if not isinstance(value, dict):
        return None
    item = value.get(key)
    if item is None:
        return None
    text = str(item).strip()
    return text or None


def resolve_mode_exit_channel(bot: discord.Client, guild_id: str, state: Dict[str, Any], mode: Dict[str, Any]):
    channel_ids = [
        mode.get("exit_notify_channel_id"),
        get_state_json_value(state, "channel_id"),
        mode.get("enter_notify_channel_id"),
    ]
    for channel_id in channel_ids:
        if not channel_id:
            continue
        try:
            channel = bot.get_channel(int(channel_id))
        except (TypeError, ValueError):
            channel = None
        if channel is not None and hasattr(channel, "send"):
            return channel

    try:
        guild = bot.get_guild(int(guild_id))
    except (TypeError, ValueError):
        guild = None
    if guild is not None and getattr(guild, "system_channel", None) is not None:
        return guild.system_channel
    return None


async def expire_db_modes_once(bot: discord.Client) -> int:
    expired_count = 0
    with get_connection() as connection:
        repository = ModeRepository(connection)
        for state in repository.list_expired_mode_states():
            guild_id = str(state.get("guild_id") or "")
            mode_id = state.get("current_mode_id")
            if not guild_id or not mode_id:
                continue
            mode = repository.get_by_id(guild_id, int(mode_id))
            channel = resolve_mode_exit_channel(bot, guild_id, state, mode or {})
            try:
                if await expire_mode_state(
                    connection,
                    guild_id,
                    state,
                    mode,
                    channel,
                    {"ended_by": "duration_task", "ended_at": utc_now().isoformat()},
                ):
                    expired_count += 1
            except Exception as exc:
                print("[WARN] Failed to expire DB mode for guild {0}: {1}".format(guild_id, exc))
                try:
                    connection.rollback()
                except Exception:
                    pass
    return expired_count


async def handle_active_mode(message: discord.Message, guild_id: str, connection) -> bool:
    repository = ModeRepository(connection)
    state = repository.get_mode_state(guild_id)
    if not state or not state.get("current_mode_id"):
        return False
    mode = repository.get_by_id(guild_id, int(state["current_mode_id"]))
    if mode is None or not mode.get("enabled"):
        repository.clear_mode_state(guild_id, {"ended_by": "missing_mode"})
        connection.commit()
        return False

    behavior = mode.get("behavior_type") or "reply"
    if behavior == "offline":
        return True
    if behavior == "reply":
        pending_effects = pop_pending_next_effects(guild_id, message)
        choices = repository.list_reply_choices(guild_id, int(mode["id"]), enabled=True)
        choice = choose_weighted_row_with_effects(choices, "mode_reply_choice", pending_effects)
        if choice is None:
            store_pending_next_effects(guild_id, message, pending_effects)
            return True
        values = build_template_values(message, message.content, {})
        await send_text_or_image(
            message.channel,
            render_template(choice.get("body"), values),
            choice.get("image_path") or "",
        )
        return True
    return True


async def handle_db_message(message: discord.Message) -> bool:
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False

    try:
        with get_connection() as connection:
            if message_has_ng_word(connection, guild_id, message.content):
                print("[DEBUG] ignored by DB ng word")
                return True

            if await handle_db_mention(message, guild_id, connection):
                return True

            if await handle_db_auto_reaction(message, guild_id, connection):
                return True
    except Exception as exc:
        print("[WARN] DB runtime backend failed: {0}".format(exc))

    return False


async def handle_db_ng_word(message: discord.Message) -> bool:
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False

    try:
        with get_connection() as connection:
            if message_has_ng_word(connection, guild_id, message.content):
                print("[DEBUG] ignored by DB ng word")
                return True
    except Exception as exc:
        print("[WARN] DB ng word backend failed: {0}".format(exc))

    return False


async def handle_db_reactions(message: discord.Message) -> bool:
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False

    try:
        with get_connection() as connection:
            if get_mention_command_text(message) is not None:
                return await handle_db_mention(message, guild_id, connection)
            if await handle_db_mention(message, guild_id, connection):
                return True
            if await handle_db_auto_reaction(message, guild_id, connection):
                return True
    except Exception as exc:
        print("[WARN] DB reaction backend failed: {0}".format(exc))

    return False


async def handle_db_runtime_message(message: discord.Message) -> bool:
    guild_id = get_message_guild_id(message)
    if guild_id is None:
        return False

    try:
        with get_connection() as connection:
            expired = await expire_mode_if_needed(message, guild_id, connection)

            if await handle_active_mode(message, guild_id, connection):
                return True

            ng_word = find_ng_word_match(connection, guild_id, message.content)
            if ng_word is not None:
                values = build_template_values(message, message.content, {})
                effects = list_effects(connection, guild_id, "ng_word", int(ng_word["id"]))
                effect_result = await execute_effects(connection, guild_id, effects, message, values)
                store_pending_next_effects(guild_id, message, effect_result.pending_effects)
                connection.commit()
                await enter_mode_if_needed(message, guild_id, connection, effect_result.pending_effects)
                print("[DEBUG] ignored by DB ng word")
                return True

            action = RuntimeAction(False)
            if get_mention_command_text(message) is not None:
                action = await process_db_mention(message, guild_id, connection)
            else:
                action = await process_db_auto_reaction(message, guild_id, connection)

            entered = await enter_mode_if_needed(message, guild_id, connection, action.pending_effects)
            connection.commit()
            return action.handled or entered or expired
    except Exception as exc:
        print("[WARN] DB runtime backend failed: {0}".format(exc))
        return False
