from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    doc = (ROOT / "docs/operations/minecraft-bedrock.md").read_text(encoding="utf-8")
    apply_script = (ROOT / "scripts/minecraft/apply_bedrock_creative_settings.ps1").read_text(
        encoding="utf-8"
    )
    assert "既存ワールド削除" in doc
    assert "level-name" in doc
    assert "ichiyon-creative-flat" in doc
    assert "この作業では既存 world を切り替えません" in doc
    assert "online-mode=false" in doc
    assert "GAMEMODE" in apply_script and "creative" in apply_script
    assert "FORCE_GAMEMODE" in apply_script
    assert "ALLOW_CHEATS" in apply_script
    assert "LEVEL_NAME" not in apply_script, "apply script must not change level name"
    assert "SPAWN_PROTECTION" not in apply_script, "spawn protection needs separate evidence"
    print("check_minecraft_deploy_safety OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
