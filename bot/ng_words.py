from typing import Dict, List, Tuple

from bot.data_store import backup_json_file, load_json_file, save_json_file


def normalize_ng_words_data(data) -> Tuple[Dict, bool]:
    if isinstance(data, list):
        words = [
            {
                "id": f"ng_{index:03d}",
                "word": word,
                "enabled": True,
            }
            for index, word in enumerate(data, start=1)
            if isinstance(word, str)
        ]
        return {"words": words}, True

    if not isinstance(data, dict):
        return {"words": []}, True

    raw_words = data.get("words", [])
    if not isinstance(raw_words, list):
        return {"words": []}, True

    normalized_words = []
    changed = False
    for index, word_item in enumerate(raw_words, start=1):
        if isinstance(word_item, str):
            normalized_words.append(
                {
                    "id": f"ng_{index:03d}",
                    "word": word_item,
                    "enabled": True,
                }
            )
            changed = True
            continue

        if not isinstance(word_item, dict):
            changed = True
            continue

        word_id = word_item.get("id")
        word = word_item.get("word")
        enabled = word_item.get("enabled", True)
        if not isinstance(word_id, str) or not word_id:
            word_id = f"ng_{index:03d}"
            changed = True
        if not isinstance(word, str):
            changed = True
            continue
        if not isinstance(enabled, bool):
            enabled = True
            changed = True

        normalized_words.append(
            {
                "id": word_id,
                "word": word,
                "enabled": enabled,
            }
        )

    normalized_data = {"words": normalized_words}
    return normalized_data, changed or data != normalized_data


def load_ng_words() -> List[str]:
    ng_words_data = load_json_file("data/ng_words.json", {"words": []})
    normalized_data, changed = normalize_ng_words_data(ng_words_data)
    if changed:
        backup_json_file("data/ng_words.json")
        save_json_file("data/ng_words.json", normalized_data)

    return [
        word_item["word"]
        for word_item in normalized_data["words"]
        if word_item.get("enabled") is True
    ]
