import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.db import get_connection


DEFAULT_DATA_DIR = ROOT_DIR / "data"


@dataclass
class Stats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    planned: int = 0

    def add(self, action: str) -> None:
        if action == "inserted":
            self.inserted += 1
        elif action == "updated":
            self.updated += 1
        elif action == "planned":
            self.planned += 1
        else:
            self.skipped += 1


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    return value


def clean_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    return default


def clean_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_match_type(value: Any) -> str:
    value = clean_text(value) or "contains"
    if value in ("contains", "exact", "regex"):
        return value
    return "contains"


def get_items(data: Any, key: str) -> List[Dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return [item for item in data[key] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def ensure_guild(cursor, guild_id: str, guild_name: str) -> str:
    cursor.execute(
        "SELECT guild_id FROM guilds WHERE guild_id = %s",
        (guild_id,),
    )
    exists = cursor.fetchone() is not None
    cursor.execute(
        """
        INSERT INTO guilds (guild_id, name)
        VALUES (%s, %s)
        ON CONFLICT (guild_id) DO UPDATE
        SET name = EXCLUDED.name,
            updated_at = NOW()
        """,
        (guild_id, guild_name),
    )
    return "updated" if exists else "inserted"


def upsert_mention_reaction(
    cursor,
    guild_id: str,
    reaction_key: str,
    keyword: str,
    name: str,
    description: str,
    is_system: bool,
    is_deletable: bool,
) -> Tuple[int, str]:
    cursor.execute(
        """
        SELECT id
        FROM mention_reactions
        WHERE guild_id = %s AND reaction_key = %s
        """,
        (guild_id, reaction_key),
    )
    row = cursor.fetchone()

    if row:
        reaction_id = row[0]
        cursor.execute(
            """
            UPDATE mention_reactions
            SET keyword = %s,
                match_type = 'contains',
                reaction_kind = 'random',
                name = %s,
                description = %s,
                admin_only = FALSE,
                is_system = %s,
                is_deletable = %s,
                enabled = TRUE,
                updated_at = NOW()
            WHERE id = %s
            """,
            (keyword, name, description, is_system, is_deletable, reaction_id),
        )
        return reaction_id, "updated"

    cursor.execute(
        """
        INSERT INTO mention_reactions (
            guild_id,
            reaction_key,
            keyword,
            match_type,
            reaction_kind,
            name,
            description,
            admin_only,
            is_system,
            is_deletable,
            enabled
        )
        VALUES (%s, %s, %s, 'contains', 'random', %s, %s, FALSE, %s, %s, TRUE)
        RETURNING id
        """,
        (guild_id, reaction_key, keyword, name, description, is_system, is_deletable),
    )
    return cursor.fetchone()[0], "inserted"


def upsert_choice(
    cursor,
    guild_id: str,
    mention_reaction_id: int,
    name: str,
    body: Optional[str],
    image_path: Optional[str],
    appearance_rate: int,
    enabled: bool,
    sort_order: int,
    result_label: Optional[str] = None,
    emoji_internal: Optional[str] = None,
) -> str:
    if body is None and image_path is None and emoji_internal is None:
        return "skipped"

    cursor.execute(
        """
        SELECT id
        FROM mention_reaction_choices
        WHERE guild_id = %s AND mention_reaction_id = %s AND name = %s
        """,
        (guild_id, mention_reaction_id, name),
    )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            """
            UPDATE mention_reaction_choices
            SET body = %s,
                image_path = %s,
                appearance_rate = %s,
                enabled = %s,
                sort_order = %s,
                result_label = %s,
                emoji_internal = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (body, image_path, appearance_rate, enabled, sort_order, result_label, emoji_internal, row[0]),
        )
        return "updated"

    cursor.execute(
        """
        INSERT INTO mention_reaction_choices (
            guild_id,
            mention_reaction_id,
            name,
            body,
            image_path,
            appearance_rate,
            enabled,
            sort_order,
            result_label,
            emoji_internal
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            guild_id,
            mention_reaction_id,
            name,
            body,
            image_path,
            appearance_rate,
            enabled,
            sort_order,
            result_label,
            emoji_internal,
        ),
    )
    return "inserted"


def upsert_reaction(
    cursor,
    guild_id: str,
    trigger_text: str,
    response_text: Optional[str],
    image_path: Optional[str],
    emoji_internal: Optional[str],
    match_type: str,
    priority: int,
    enabled: bool,
) -> str:
    if response_text is None and image_path is None and emoji_internal is None:
        return "skipped"

    cursor.execute(
        """
        SELECT id
        FROM reactions
        WHERE guild_id = %s AND trigger_text = %s
        """,
        (guild_id, trigger_text),
    )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            """
            UPDATE reactions
            SET response_text = %s,
                image_path = %s,
                emoji_internal = %s,
                match_type = %s,
                priority = %s,
                enabled = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                response_text,
                image_path,
                emoji_internal,
                match_type,
                priority,
                enabled,
                row[0],
            ),
        )
        return "updated"

    cursor.execute(
        """
        INSERT INTO reactions (
            guild_id,
            trigger_text,
            response_text,
            image_path,
            emoji_internal,
            match_type,
            priority,
            enabled
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            guild_id,
            trigger_text,
            response_text,
            image_path,
            emoji_internal,
            match_type,
            priority,
            enabled,
        ),
    )
    return "inserted"


def upsert_ng_word(cursor, guild_id: str, word: str, enabled: bool) -> str:
    cursor.execute(
        "SELECT id FROM ng_words WHERE guild_id = %s AND word = %s",
        (guild_id, word),
    )
    exists = cursor.fetchone() is not None
    cursor.execute(
        """
        INSERT INTO ng_words (guild_id, word, enabled)
        VALUES (%s, %s, %s)
        ON CONFLICT (guild_id, word) DO UPDATE
        SET enabled = EXCLUDED.enabled,
            updated_at = NOW()
        """,
        (guild_id, word, enabled),
    )

    return "updated" if exists else "inserted"


def iter_quote_choices(data_dir: Path) -> Iterable[Dict[str, Any]]:
    data = load_json(data_dir / "quotes.json", {"quotes": []})
    for index, item in enumerate(get_items(data, "quotes")):
        yield {
            "name": clean_text(item.get("id")) or "quote_{0:03d}".format(index + 1),
            "body": clean_text(item.get("text")),
            "image_path": clean_text(item.get("image_path")),
            "emoji_internal": clean_text(item.get("emoji")) or clean_text(item.get("emoji_internal")),
            "appearance_rate": 1,
            "enabled": clean_bool(item.get("enabled"), True),
            "sort_order": index,
        }


def iter_kuji_choices(data_dir: Path) -> Iterable[Dict[str, Any]]:
    data = load_json(data_dir / "kuji.json", {"results": []})
    for index, item in enumerate(get_items(data, "results")):
        name = clean_text(item.get("id")) or clean_text(item.get("name"))
        result_label = clean_text(item.get("name")) or clean_text(item.get("label")) or clean_text(item.get("fortune"))
        yield {
            "name": name or "kuji_{0:03d}".format(index + 1),
            "result_label": result_label,
            "body": clean_text(item.get("message")) or clean_text(item.get("body")),
            "image_path": clean_text(item.get("image_path")),
            "emoji_internal": clean_text(item.get("emoji")) or clean_text(item.get("emoji_internal")),
            "appearance_rate": max(clean_int(item.get("weight"), 1), 1),
            "enabled": clean_bool(item.get("enabled"), True),
            "sort_order": index,
        }


def iter_reactions(data_dir: Path) -> Iterable[Dict[str, Any]]:
    data = load_json(data_dir / "reactions.json", {"reactions": []})
    for item in get_items(data, "reactions"):
        trigger = clean_text(item.get("trigger")) or clean_text(item.get("trigger_text"))
        if trigger is None:
            continue

        yield {
            "trigger_text": trigger,
            "response_text": clean_text(item.get("response")) or clean_text(item.get("response_text")),
            "image_path": clean_text(item.get("image_path")),
            "emoji_internal": clean_text(item.get("emoji")) or clean_text(item.get("emoji_internal")),
            "match_type": normalize_match_type(item.get("match_type")),
            "priority": clean_int(item.get("priority"), 0),
            "enabled": clean_bool(item.get("enabled"), True),
        }


def iter_ng_words(data_dir: Path) -> Iterable[Dict[str, Any]]:
    data = load_json(data_dir / "ng_words.json", {"words": []})
    for item in get_items(data, "words"):
        word = clean_text(item.get("word"))
        if word is None:
            continue

        yield {
            "word": word,
            "enabled": clean_bool(item.get("enabled"), True),
        }


def count_planned(data_dir: Path) -> Dict[str, int]:
    return {
        "guilds": 1,
        "mention_reactions": 2,
        "quote_choices": len(list(iter_quote_choices(data_dir))),
        "kuji_choices": len(list(iter_kuji_choices(data_dir))),
        "reactions": len(list(iter_reactions(data_dir))),
        "ng_words": len(list(iter_ng_words(data_dir))),
    }


def migrate(data_dir: Path, guild_id: str, guild_name: str, database_url: Optional[str], dry_run: bool) -> Dict[str, Stats]:
    stats = {
        "guilds": Stats(),
        "mention_reactions": Stats(),
        "quote_choices": Stats(),
        "kuji_choices": Stats(),
        "reactions": Stats(),
        "ng_words": Stats(),
    }

    if dry_run:
        planned = count_planned(data_dir)
        for key, value in planned.items():
            stats[key].planned = value
        return stats

    with get_connection(database_url) as connection:
        with connection.cursor() as cursor:
            action = ensure_guild(cursor, guild_id, guild_name)
            stats["guilds"].add(action)

            quote_reaction_id, action = upsert_mention_reaction(
                cursor,
                guild_id,
                "quotes",
                "名言",
                "名言",
                "既存 quotes.json から移行した名言メンション反応",
                True,
                False,
            )
            stats["mention_reactions"].add(action)

            kuji_reaction_id, action = upsert_mention_reaction(
                cursor,
                guild_id,
                "kuji",
                "おみくじ",
                "おみくじ",
                "既存 kuji.json から移行したおみくじメンション反応",
                True,
                True,
            )
            stats["mention_reactions"].add(action)

            for item in iter_quote_choices(data_dir):
                action = upsert_choice(cursor, guild_id, quote_reaction_id, **item)
                stats["quote_choices"].add(action)

            for item in iter_kuji_choices(data_dir):
                action = upsert_choice(cursor, guild_id, kuji_reaction_id, **item)
                stats["kuji_choices"].add(action)

            for item in iter_reactions(data_dir):
                action = upsert_reaction(cursor, guild_id, **item)
                stats["reactions"].add(action)

            for item in iter_ng_words(data_dir):
                action = upsert_ng_word(cursor, guild_id, **item)
                stats["ng_words"].add(action)

        connection.commit()

    return stats


def print_stats(stats: Dict[str, Stats], dry_run: bool) -> None:
    print("JSON migration {0}".format("dry-run" if dry_run else "completed"))
    for key in sorted(stats):
        item = stats[key]
        if dry_run:
            print("{0}: planned={1}".format(key, item.planned))
        else:
            print(
                "{0}: inserted={1} updated={2} skipped={3}".format(
                    key,
                    item.inserted,
                    item.updated,
                    item.skipped,
                )
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate existing JSON data to PostgreSQL.")
    parser.add_argument("--guild-id", required=True, help="Target Discord guild ID.")
    parser.add_argument(
        "--guild-name",
        help="Target guild display name. Defaults to guild ID.",
    )
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL for this run.",
    )
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        help="Directory containing existing JSON files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read JSON and print planned counts without connecting to the database.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    guild_name = args.guild_name or args.guild_id
    stats = migrate(data_dir, args.guild_id, guild_name, args.database_url, args.dry_run)
    print_stats(stats, args.dry_run)


if __name__ == "__main__":
    main()
