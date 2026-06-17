import json
import os
from datetime import datetime, timezone
from typing import Optional

from bot import config


DEFAULT_STATE = {
    "current_mode": "normal",
    "mode_until": None,
    "last_hayusu_trigger_month": None,
    "annual_message_sent_years": [],
}

DEFAULT_RESPONSES = {
    "end_of_service_message": config.END_OF_SERVICE_MESSAGE,
}


def load_json_file(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Failed to load {path}: {e}")
        return default


def save_json_file(path: str, data) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:
        print(f"Failed to save {path}: {e}")


def get_now() -> datetime:
    return datetime.now(timezone.utc)


def get_current_month() -> str:
    return get_now().strftime("%Y-%m")


def get_local_now() -> datetime:
    return datetime.now().astimezone()


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def backup_json_file(path: str) -> None:
    if not os.path.exists(path):
        return

    os.makedirs("data/backups", exist_ok=True)
    timestamp = get_local_now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.basename(path)
    backup_path = os.path.join("data/backups", f"{timestamp}_{filename}")
    try:
        with open(path, "r", encoding="utf-8") as src:
            content = src.read()
        with open(backup_path, "w", encoding="utf-8") as dst:
            dst.write(content)
    except OSError as e:
        print(f"Failed to backup {path}: {e}")


def get_default_state() -> dict:
    state = DEFAULT_STATE.copy()
    state["annual_message_sent_years"] = []
    return state


def save_state(state: dict) -> None:
    save_json_file(config.STATE_FILE, state)


def load_state() -> dict:
    should_save = not os.path.exists(config.STATE_FILE)

    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Failed to load {config.STATE_FILE}: {e}")
        state = get_default_state()
        should_save = True

    if not isinstance(state, dict):
        state = get_default_state()
        should_save = True

    normalized_state = get_default_state()
    normalized_state.update(state)

    if not isinstance(normalized_state.get("annual_message_sent_years"), list):
        normalized_state["annual_message_sent_years"] = []
        should_save = True

    if state != normalized_state:
        should_save = True

    if should_save:
        save_state(normalized_state)

    return normalized_state


def load_responses() -> dict:
    responses = load_json_file("data/responses.json", {})
    if not isinstance(responses, dict):
        return {}

    should_save = False
    for key, value in DEFAULT_RESPONSES.items():
        if key not in responses:
            responses[key] = value
            should_save = True

    if should_save:
        save_json_file("data/responses.json", responses)

    return responses


def get_end_of_service_message() -> str:
    responses = load_responses()
    message = responses.get("end_of_service_message")
    if isinstance(message, str) and message:
        return message
    return config.END_OF_SERVICE_MESSAGE


def get_startup_message() -> Optional[str]:
    responses = load_responses()
    message = responses.get("startup_message")
    if isinstance(message, str) and message:
        return message
    return None
