from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    apply_script = (ROOT / "scripts/minecraft/apply_bedrock_creative_settings.ps1").read_text(
        encoding="utf-8"
    )
    backup_script = (ROOT / "scripts/minecraft/backup_bedrock.ps1").read_text(encoding="utf-8")
    assert 'ProjectDir = "/home/ubuntu/minecraft-bedrock"' in apply_script
    assert 'Service = "bedrock"' in apply_script
    assert 'docker compose config --quiet' in apply_script
    assert "minecraft-bedrock-stg" in apply_script
    assert 'Service = "bedrock"' in backup_script
    assert "bot" not in apply_script.lower().replace("minecraft", ""), "must not target bot services"
    print("check_minecraft_compose_runtime OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
