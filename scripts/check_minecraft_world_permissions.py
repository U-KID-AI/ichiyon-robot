from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    doc = (ROOT / "docs/operations/minecraft-bedrock.md").read_text(encoding="utf-8")
    assert "default-player-permission-level=member" in doc
    assert "permissions.json" in doc
    assert "allow-list=false" in doc
    assert "LevelDB" in doc
    assert "tar" in doc
    print("check_minecraft_world_permissions OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
