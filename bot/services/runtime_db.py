import calendar
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from bot.db import get_connection
from bot.messages import get_mention_command_text, send_text_or_image
from bot.ng_words import normalize_ng_match_text
from bot.repositories import (
    AutoReactionRepository,
    CounterRepository,
    FeatureFlagRepository,
    MentionReactionRepository,
    MentionLimitedEffectRepository,
    ModeRepository,
    NgWordRepository,
    SpecialEffectRepository,
)
from bot.services.deck_search import search_decks


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


@dataclass
class MatchResult:
    row: Dict[str, Any]
    groups: Dict[str, str]


@dataclass
class RuntimeAction:
    handled: bool
    count_changed: bool = False


@dataclass
class EffectExecutionResult:
    count_changed: bool = False
    repeat_count: int = 0


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


def choose_weighted_choice(choices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    weighted = []
    total = 0
    for choice in choices:
        weight = int(choice.get("appearance_rate") or 1)
        if weight < 1:
            continue
        total += weight
        weighted.append((choice, total))
    if total <= 0:
        return None

    selected = random.randint(1, total)
    for choice, threshold in weighted:
        if selected <= threshold:
            return choice
    return None


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
    probability = parse_probability(config)
    if probability is None:
        return True
    return random.randint(1, probability["denominator"]) <= probability["numerator"]


def get_additional_message(effect: Dict[str, Any]) -> str:
    return (
        effect.get("additional_message")
        or effect.get("additional_text")
        or ""
    )


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
            print("[WARN] probability_multiplier target mismatch: id={0}".format(effect.get("id")))
            continue
        value = get_config_float(config, ["multiplier", "rate", "factor"], 1.0)
        if value <= 0:
            print("[WARN] probability_multiplier skipped invalid multiplier: id={0}".format(effect.get("id")))
            continue
        multiplier *= value
    return multiplier


def choose_weighted_choice_with_effects(connection, guild_id: str, choices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not choices:
        return None
    weighted = []
    total = 0
    for choice in choices:
        choice_id = int(choice["id"])
        effects = list_effects(connection, guild_id, "mention_reaction_choice", choice_id)
        multiplier = get_probability_multiplier_for_target(effects, "mention_reaction_choice", choice_id)
        base_weight = max(1, int(choice.get("appearance_rate") or choice.get("weight") or 1))
        weight = max(1, int(round(base_weight * multiplier)))
        weighted.append((choice, weight))
        total += weight
    if total <= 0:
        return choices[0]
    roll = random.randint(1, total)
    cursor = 0
    for choice, weight in weighted:
        cursor += weight
        if roll <= cursor:
            return choice
    return weighted[-1][0]


def choose_auto_match_with_effects(connection, guild_id: str, matches: List[MatchResult]) -> Optional[MatchResult]:
    if not matches:
        return None
    sorted_matches = sort_auto_matches(matches)
    top_priority = int(sorted_matches[0].row.get("priority") or 0)
    candidates = [match for match in sorted_matches if int(match.row.get("priority") or 0) == top_priority]
    weighted = []
    total = 0
    has_multiplier = False
    for match in candidates:
        reaction_id = int(match.row["id"])
        effects = list_effects(connection, guild_id, "auto_reaction", reaction_id)
        multiplier = get_probability_multiplier_for_target(effects, "auto_reaction", reaction_id)
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
        if emoji:
            try:
                await message.add_reaction(emoji)
                handled = True
            except discord.DiscordException as exc:
                print("[WARN] Failed to add repeated DB reaction emoji {0!r}: {1}".format(emoji, exc))
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
                render_template(reason, template_values),
            )
        )
        return result

    if action == "send_message":
        text = get_config_text(config, ["message", "text"]) or get_additional_message(effect)
        if not text:
            print("[WARN] destroy send_message skipped without message: id={0}".format(effect.get("id")))
            return result
        await message.channel.send(render_template(text, template_values))
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
) -> EffectExecutionResult:
    result = EffectExecutionResult()
    for effect in effects:
        try:
            config = normalize_json(effect.get("effect_config_json"))
            effect_type = effect.get("effect_type")
            if effect_type == "probability_message":
                if not probability_hit(config):
                    continue
                additional = get_additional_message(effect)
                timing = effect.get("additional_message_timing") or effect.get("additional_post_timing")
                if additional and timing in (None, "", "effect_success", "tag_triggered"):
                    await message.channel.send(render_template(additional, template_values))
            elif effect_type == "message":
                additional = get_additional_message(effect) or get_config_text(
                    config,
                    ["message", "text", "additional_message", "additional_text"],
                )
                timing = effect.get("additional_message_timing") or effect.get("additional_post_timing")
                if additional and timing in (None, "", "effect_success", "tag_triggered"):
                    await message.channel.send(render_template(additional, template_values))
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
                repository.increment(guild_id, counter_key, delta)
                result.count_changed = True
            elif effect_type == "counter_set":
                if not probability_hit(config):
                    continue
                counter_key = get_counter_key(config)
                if counter_key is None:
                    print("[WARN] counter_set skipped without counter_key")
                    continue
                value = get_config_int(config, ["set_value", "value", "count"], 1)
                repository = CounterRepository(connection)
                repository.ensure_counter(guild_id, counter_key, counter_key)
                repository.set_value(guild_id, counter_key, value)
                result.count_changed = True
            elif effect_type == "probability_multiplier":
                multiplier = get_config_float(config, ["multiplier", "rate", "factor"], 1.0)
                if multiplier <= 0:
                    print("[WARN] probability_multiplier skipped invalid multiplier: id={0}".format(effect.get("id")))
                else:
                    print("[INFO] probability_multiplier applied during selection when target is selectable: id={0}".format(effect.get("id")))
            elif effect_type == "next_action_count":
                target_action = get_config_text(config, ["target_action", "action"]) or "same"
                if target_action not in ("same", "mention_reaction_choice", "auto_reaction"):
                    print("[WARN] next_action_count skipped invalid target_action: {0}".format(target_action))
                    continue
                count = get_config_int(config, ["count", "repeat", "times"], 1)
                if count <= 0:
                    print("[WARN] next_action_count skipped invalid count: {0}".format(count))
                    continue
                result.repeat_count += min(count, 5)
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
    result.repeat_count = min(result.repeat_count, 5)
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
    reactions = repository.list_reactions(guild_id, enabled=True, reaction_kind="random_draw")
    matches = []
    for reaction in reactions:
        groups = match_pattern(reaction.get("keyword") or "", reaction.get("match_type") or "exact", command_text)
        if groups is not None:
            matches.append(MatchResult(reaction, groups))
    if not matches:
        search_matches = []
        for reaction in repository.list_reactions(guild_id, enabled=True, reaction_kind="search"):
            groups = match_pattern(
                reaction.get("keyword") or "",
                reaction.get("match_type") or "exact",
                command_text,
            )
            if groups is not None:
                search_matches.append(MatchResult(reaction, groups))
        if not search_matches or not limited_effects:
            return RuntimeAction(False)
        selected_search = sort_mention_matches(search_matches)[0]
        values = build_template_values(message, command_text, selected_search.groups)
        config_json = normalize_json(selected_search.row.get("config_json"))
        if config_json.get("search_type") == "deck_search":
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
            return RuntimeAction(True, count_changed)
        effect_result = await execute_effects(connection, guild_id, limited_effects, message, values)
        return RuntimeAction(bool(limited_effects), effect_result.count_changed)

    selected = sort_mention_matches(matches)[0]
    choices = repository.list_choices(guild_id, int(selected.row["id"]), enabled=True)
    choice = choose_weighted_choice_with_effects(connection, guild_id, choices)
    if choice is None:
        return RuntimeAction(False)

    values = build_template_values(message, command_text, selected.groups)
    text = render_template(choice.get("body"), values)
    image_path = choice.get("image_path") or ""
    handled = await send_text_or_image(message.channel, text, image_path)
    choice_effects = list_effects(connection, guild_id, "mention_reaction_choice", int(choice["id"]))
    effects = merge_effects(choice_effects, limited_effects)
    effect_result = await execute_effects(connection, guild_id, effects, message, values)
    if effect_result.repeat_count:
        repeated = await repeat_text_image_action(message, text, image_path, "", effect_result.repeat_count)
        handled = handled or repeated
    return RuntimeAction(handled or bool(effects), effect_result.count_changed)


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

    selected = choose_auto_match_with_effects(connection, guild_id, matches)
    if selected is None:
        return RuntimeAction(False)
    values = build_template_values(message, message.content, selected.groups)
    text = render_template(selected.row.get("response_text"), values)
    image_path = selected.row.get("image_path") or ""
    sent = await send_text_or_image(message.channel, text, image_path)

    emoji = selected.row.get("emoji_internal") or ""
    if emoji:
        try:
            await message.add_reaction(emoji)
            sent = True
        except discord.DiscordException as exc:
            print("[WARN] Failed to add DB auto reaction emoji {0!r}: {1}".format(emoji, exc))

    effects = list_effects(connection, guild_id, "auto_reaction", int(selected.row["id"]))
    effect_result = await execute_effects(connection, guild_id, effects, message, values)
    if effect_result.repeat_count:
        repeated = await repeat_text_image_action(message, text, image_path, emoji, effect_result.repeat_count)
        sent = sent or repeated
    return RuntimeAction(sent or bool(effects), effect_result.count_changed)


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


def trigger_condition_met(connection, guild_id: str, mode: Dict[str, Any], condition: Dict[str, Any]) -> bool:
    condition_type = condition.get("condition_type")
    config = get_condition_config(condition)
    if condition_type == "counter_threshold":
        counter_key = get_counter_key(config)
        if counter_key is None:
            return False
        value = CounterRepository(connection).get_value(guild_id, counter_key, 0)
        return compare_counter(value, config.get("operator", ">="), get_threshold_value(config))
    if condition_type == "probability":
        return probability_hit(config)
    if condition_type == "period_not_triggered":
        return period_not_triggered_met(connection, guild_id, mode, condition)
    return False


def mode_triggers_met(connection, guild_id: str, mode: Dict[str, Any]) -> bool:
    repository = ModeRepository(connection)
    conditions = repository.list_trigger_conditions(guild_id, int(mode["id"]), enabled=True)
    actionable = [
        condition
        for condition in conditions
        if condition.get("condition_type") in ("counter_threshold", "probability", "period_not_triggered")
    ]
    if not actionable:
        return False

    operator = actionable[0].get("group_operator") or "AND"
    results = [trigger_condition_met(connection, guild_id, mode, condition) for condition in actionable]
    if operator == "OR":
        return any(results)
    return all(results)


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


async def send_mode_enter_message(message: discord.Message, mode: Dict[str, Any]) -> None:
    enter_text = mode.get("enter_message") or mode.get("enter_text") or ""
    enter_image = mode.get("enter_gif_path") or ""
    if enter_text or enter_image:
        await send_text_or_image(message.channel, enter_text, enter_image)


async def send_mode_exit_message(message: discord.Message, mode: Dict[str, Any]) -> None:
    exit_text = mode.get("exit_message") or mode.get("exit_text") or ""
    exit_image = mode.get("exit_gif_path") or ""
    if exit_text or exit_image:
        await send_text_or_image(message.channel, exit_text, exit_image)
    mode_key = (mode.get("mode_key") or "").lower()
    mode_name = (mode.get("name") or "").lower()
    if "shikocchi" in mode_key or "しこっち" in mode_name:
        await message.channel.send(SHIKOCCHI_RECOVERY_MESSAGE)


async def enter_mode_if_needed(message: discord.Message, guild_id: str, connection) -> bool:
    repository = ModeRepository(connection)
    state = repository.get_mode_state(guild_id)
    if state and state.get("current_mode_id"):
        return False

    for mode in repository.list_enabled_modes(guild_id):
        try:
            if not mode_triggers_met(connection, guild_id, mode):
                continue
            duration = get_duration_seconds_from_exit(connection, guild_id, int(mode["id"]))
            active_until = utc_now() + timedelta(seconds=duration) if duration else None
            repository.enter_mode(
                guild_id,
                int(mode["id"]),
                active_until,
                {"entered_by": "runtime_mvp", "mode_key": mode.get("mode_key")},
            )
            record_mode_period_trigger(connection, guild_id, mode)
            reset_counter_thresholds(connection, guild_id, int(mode["id"]))
            connection.commit()
            await send_mode_enter_message(message, mode)
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
    repository.clear_mode_state(guild_id, {"ended_by": "duration", "ended_at": utc_now().isoformat()})
    connection.commit()
    if mode is not None:
        await send_mode_exit_message(message, mode)
    return True


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
        choices = repository.list_reply_choices(guild_id, int(mode["id"]), enabled=True)
        choice = choose_weighted_choice(choices)
        if choice is None:
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
                await execute_effects(connection, guild_id, effects, message, values)
                connection.commit()
                await enter_mode_if_needed(message, guild_id, connection)
                print("[DEBUG] ignored by DB ng word")
                return True

            action = RuntimeAction(False)
            if get_mention_command_text(message) is not None:
                action = await process_db_mention(message, guild_id, connection)
            else:
                action = await process_db_auto_reaction(message, guild_id, connection)

            entered = await enter_mode_if_needed(message, guild_id, connection)
            connection.commit()
            return action.handled or entered or expired
    except Exception as exc:
        print("[WARN] DB runtime backend failed: {0}".format(exc))
        return False
