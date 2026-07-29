from __future__ import annotations

import argparse
from pathlib import Path


EXPECTED_CREATIVE_SETTINGS = {
    "gamemode": "creative",
    "force-gamemode": "true",
    "difficulty": "peaceful",
    "allow-cheats": "true",
    "max-players": "10",
    "online-mode": "true",
    "default-player-permission-level": "member",
    "view-distance": "16",
    "tick-distance": "4",
}


def parse_properties(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def validate_creative_settings(values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not values.get("level-name"):
        errors.append("level-name is required and must not be blank")
    if values.get("online-mode") == "false":
        errors.append("online-mode=false is forbidden")
    for key, expected in EXPECTED_CREATIVE_SETTINGS.items():
        actual = values.get(key)
        if actual != expected:
            errors.append(f"{key} expected {expected!r}, got {actual!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-properties", type=Path)
    args = parser.parse_args()

    sample = """
level-name=ichiyon-lab-stg
gamemode=creative
force-gamemode=true
difficulty=peaceful
allow-cheats=true
max-players=10
online-mode=true
default-player-permission-level=member
view-distance=16
tick-distance=4
"""
    text = args.server_properties.read_text(encoding="utf-8") if args.server_properties else sample
    errors = validate_creative_settings(parse_properties(text))
    if errors:
        for error in errors:
            print(f"NG {error}")
        return 1
    print("check_bedrock_runtime OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
