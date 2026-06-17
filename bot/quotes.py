import random
from typing import Dict, List, Optional, Tuple

from bot.data_store import backup_json_file, load_json_file, save_json_file


def normalize_quotes_data(data) -> Tuple[Dict, bool]:
    if isinstance(data, list):
        quotes = [
            {
                "id": f"quote_{index:03d}",
                "text": quote,
                "enabled": True,
            }
            for index, quote in enumerate(data, start=1)
            if isinstance(quote, str)
        ]
        return {"quotes": quotes}, True

    if not isinstance(data, dict):
        return {"quotes": []}, True

    raw_quotes = data.get("quotes", [])
    if not isinstance(raw_quotes, list):
        return {"quotes": []}, True

    normalized_quotes = []
    changed = False
    for index, quote in enumerate(raw_quotes, start=1):
        if not isinstance(quote, dict):
            changed = True
            continue

        quote_id = quote.get("id")
        text = quote.get("text")
        enabled = quote.get("enabled", True)
        if not isinstance(quote_id, str) or not quote_id:
            quote_id = f"quote_{index:03d}"
            changed = True
        if not isinstance(text, str):
            changed = True
            continue
        if not isinstance(enabled, bool):
            enabled = True
            changed = True

        normalized_quotes.append(
            {
                "id": quote_id,
                "text": text,
                "enabled": enabled,
            }
        )

    normalized_data = {"quotes": normalized_quotes}
    return normalized_data, changed or data != normalized_data


def load_quotes() -> List[str]:
    quotes_data = load_json_file("data/quotes.json", {"quotes": []})
    normalized_data, changed = normalize_quotes_data(quotes_data)
    if changed:
        backup_json_file("data/quotes.json")
        save_json_file("data/quotes.json", normalized_data)

    return [
        quote["text"]
        for quote in normalized_data["quotes"]
        if quote.get("enabled") is True
    ]


def draw_quote_message() -> Optional[str]:
    quotes = load_quotes()
    if not quotes:
        return None

    return random.choice(quotes)
