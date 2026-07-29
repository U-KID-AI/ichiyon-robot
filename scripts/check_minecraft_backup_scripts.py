from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    backup = read("scripts/minecraft/backup_bedrock.ps1")
    restore = read("scripts/minecraft/restore_bedrock.ps1")
    apply_settings = read("scripts/minecraft/apply_bedrock_creative_settings.ps1")
    combined = "\n".join([backup, restore, apply_settings]).lower()

    forbidden = [
        "docker compose down",
        "down -v",
        "--volumes",
        "docker volume rm",
        "rm -rf data",
        "online-mode=false",
    ]
    for token in forbidden:
        assert token not in combined, f"forbidden token found: {token}"

    assert "[switch]$Apply" in backup
    assert "dry_run=true" in backup
    assert "[switch]$ConfirmWorldReplace" in restore
    assert "restore requires both -Apply and -ConfirmWorldReplace" in restore
    assert 'docker compose stop "$Service"' in backup
    assert 'docker compose up -d "$Service"' in backup
    assert 'docker compose stop "$SERVICE"' in apply_settings
    assert 'docker compose up -d "$SERVICE"' in apply_settings
    print("check_minecraft_backup_scripts OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
