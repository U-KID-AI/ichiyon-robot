"""Small executable regression suite for native garbage inventory and direct Mob removal."""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    for path in (ROOT / "minecraft").rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
    subprocess.run(["node", str(Path(__file__).with_suffix(".mjs"))], check=True)
    print("Minecraft JSON parse: PASS")
