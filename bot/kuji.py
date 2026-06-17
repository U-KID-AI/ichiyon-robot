import random
from typing import Dict, Tuple

from bot.data_store import backup_json_file, load_json_file, save_json_file


def normalize_kuji_data(data) -> Tuple[Dict, bool]:
    if not isinstance(data, dict):
        return {"results": []}, True

    raw_results = data.get("results", [])
    if not isinstance(raw_results, list):
        return {"results": []}, True

    normalized_results = []
    changed = False
    for index, result in enumerate(raw_results, start=1):
        if not isinstance(result, dict):
            changed = True
            continue

        result_id = result.get("id")
        name = result.get("name")
        message = result.get("message")
        weight = result.get("weight", 1)
        enabled = result.get("enabled", True)
        if not isinstance(result_id, str) or not result_id:
            result_id = f"kuji_{index:03d}"
            changed = True
        if not isinstance(name, str) or not isinstance(message, str):
            changed = True
            continue
        if not isinstance(weight, int) or weight < 1:
            weight = 1
            changed = True
        if not isinstance(enabled, bool):
            enabled = True
            changed = True

        normalized_results.append(
            {
                "id": result_id,
                "name": name,
                "message": message,
                "weight": weight,
                "enabled": enabled,
            }
        )

    normalized_data = {"results": normalized_results}
    return normalized_data, changed or data != normalized_data


def load_kuji() -> dict:
    kuji_data = load_json_file("data/kuji.json", {"results": []})
    normalized_data, changed = normalize_kuji_data(kuji_data)
    if changed:
        backup_json_file("data/kuji.json")
        save_json_file("data/kuji.json", normalized_data)
    return normalized_data


def draw_kuji_message() -> str:
    kuji_data = load_kuji()
    results = [
        result
        for result in kuji_data.get("results", [])
        if result.get("enabled") is True
    ]
    if not results:
        return "くじが入っていません"

    weights = [result.get("weight", 1) for result in results]
    result = random.choices(results, weights=weights, k=1)[0]
    if not isinstance(result, dict):
        return "くじデータが読み込めませんでした"

    name = result.get("name", "結果不明")
    result_message = result.get("message", "")

    return f"🎲 **{name}**\n{result_message}"
