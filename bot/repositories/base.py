import json
from typing import Any, Dict, Iterable, List, Optional, Sequence


def rows_to_dicts(cursor, rows: Iterable[Sequence[Any]]) -> List[Dict[str, Any]]:
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def one_to_dict(cursor, row: Optional[Sequence[Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None

    columns = [column.name for column in cursor.description]
    return dict(zip(columns, row))


def fetch_all(cursor) -> List[Dict[str, Any]]:
    return rows_to_dicts(cursor, cursor.fetchall())


def fetch_one(cursor) -> Optional[Dict[str, Any]]:
    return one_to_dict(cursor, cursor.fetchone())


def normalize_reaction_kind(reaction_kind: Optional[str]) -> Optional[str]:
    if reaction_kind == "random_draw":
        return "random"
    return reaction_kind


def normalize_effect_target_type(target_type: str) -> str:
    if target_type == "auto_reaction":
        return "reaction"
    return target_type


def json_dumps(value: Dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)
