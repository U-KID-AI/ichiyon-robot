from __future__ import annotations

import importlib.util
from pathlib import Path


runtime_path = Path(__file__).resolve().parent / "minecraft" / "check_bedrock_runtime.py"
spec = importlib.util.spec_from_file_location("check_bedrock_runtime", runtime_path)
assert spec and spec.loader
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
parse_properties = runtime.parse_properties
validate_creative_settings = runtime.validate_creative_settings


def main() -> int:
    before = parse_properties(
        """
        level-name=ichiyon-lab-stg
        gamemode=survival
        force-gamemode=false
        allow-cheats=false
        online-mode=true
        """
    )
    assert validate_creative_settings(before), "survival settings must be flagged"

    after = parse_properties(
        """
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
    )
    assert validate_creative_settings(after) == []
    assert after["level-name"] == "ichiyon-lab-stg"
    print("check_minecraft_server_properties OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
