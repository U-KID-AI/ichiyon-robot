"""Import the original Let's Cooking Molcar OGG and selected fixed PNG icon."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ITEM = "ichiyon:record_lets_cooking_molcar"
SOUND = "ichiyon:record.lets_cooking_molcar"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(audio: Path, icon: Path, root: Path = ROOT, *, write: bool = False) -> dict:
    if audio.suffix.lower() != ".ogg" or icon.suffix.lower() != ".png":
        raise ValueError("Supply the original OGG and a fixed PNG icon")
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(audio),
    ], text=True, encoding="utf-8"))
    duration = float(probe["format"]["duration"])
    if duration <= 0 or not any(s["codec_type"] == "audio" for s in probe["streams"]):
        raise ValueError("The record must contain playable audio")
    with Image.open(icon) as image:
        image.verify()
    track = {
        "itemId": ITEM, "soundId": SOUND, "durationSeconds": duration,
        "audioSha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "iconSha256": hashlib.sha256(icon.read_bytes()).hexdigest(),
    }
    if not write:
        return track
    bp = root / "minecraft/behavior_packs/ichiyon_avatar_bp"
    rp = root / "minecraft/resource_packs/ichiyon_avatar_rp"
    for source, destination in (
        (audio, rp / "sounds/records/lets_cooking_molcar.ogg"),
        (icon, rp / "textures/items/record_lets_cooking_molcar.png"),
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    write_json(bp / "items/record_lets_cooking_molcar.json", {
        "format_version": "1.21.100", "minecraft:item": {
            "description": {"identifier": ITEM, "menu_category": {"category": "items"}},
            "components": {"minecraft:max_stack_size": 1, "minecraft:icon": ITEM,
                           "minecraft:display_name": {"value": f"item.{ITEM}.name"}},
        },
    })
    atlas_path = rp / "textures/item_texture.json"
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    atlas["texture_data"][ITEM] = {"textures": "textures/items/record_lets_cooking_molcar"}
    write_json(atlas_path, atlas)
    sound_path = rp / "sounds/sound_definitions.json"
    sounds = json.loads(sound_path.read_text(encoding="utf-8"))
    sounds["sound_definitions"][SOUND] = {
        "category": "record", "sounds": [{"name": "sounds/records/lets_cooking_molcar", "stream": True}],
    }
    write_json(sound_path, sounds)
    for locale in ("ja_JP", "en_US"):
        path = rp / f"texts/{locale}.lang"
        key = f"item.{ITEM}.name="
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith(key)]
        path.write_text("\n".join([*lines, key + "Let's Cooking Molcar"]) + "\n", encoding="utf-8")
    catalog_path = root / "minecraft/behavior_packs/import_structures/scripts/molcar_records_catalog.js"
    catalog_path.write_text(
        "// Generated from supplied assets by scripts/build_molcar_record.py.\nexport const molcarRecords = "
        + json.dumps([track], ensure_ascii=False, indent=2) + ";\n", encoding="utf-8",
    )
    return track


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--icon", required=True, type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.audio, args.icon, write=args.write), indent=2))
