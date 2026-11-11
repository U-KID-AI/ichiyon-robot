"""Import supplied Molcar OGG records through the existing playback catalog.

Use a private output root for user-provided recordings. This importer does not
publish files and never recreates the catalog from just the latest track.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ITEM = "ichiyon:record_lets_cooking_molcar"
SOUND = "ichiyon:record.lets_cooking_molcar"
RECORD_GROUP = "minecraft:itemGroup.name.record"


def record_identifiers(key: str) -> tuple[str, str]:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
        raise ValueError("Record keys must be lowercase identifiers, not file paths")
    return "ichiyon:record_" + key, "ichiyon:record." + key


def read_catalog(path: Path) -> list[dict]:
    if not path.exists():
        return []
    source = path.read_text(encoding="utf-8")
    marker = "export const molcarRecords = "
    if source.count(marker) != 1:
        raise ValueError("Expected the existing JSON-only Molcar record catalog")
    records = json.loads(source.split(marker, 1)[1].strip().removesuffix(";"))
    if (not isinstance(records, list) or any(not isinstance(row, dict) for row in records)
            or len({row["itemId"] for row in records}) != len(records)
            or len({row["soundId"] for row in records}) != len(records)):
        raise ValueError("Duplicate or invalid Molcar record catalog")
    return records


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(audio: Path, icon: Path, root: Path = ROOT, *, write: bool = False,
          key: str = "lets_cooking_molcar", display_name: str = "Let's Cooking Molcar") -> dict:
    item_id, sound_id = record_identifiers(key)
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
        "itemId": item_id, "soundId": sound_id, "durationSeconds": duration,
        "audioSha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "iconSha256": hashlib.sha256(icon.read_bytes()).hexdigest(),
    }
    if not write:
        return track
    bp = root / "minecraft/behavior_packs/ichiyon_avatar_bp"
    rp = root / "minecraft/resource_packs/ichiyon_avatar_rp"
    catalog_path = root / "minecraft/behavior_packs/import_structures/scripts/molcar_records_catalog.js"
    records = read_catalog(catalog_path)
    records = [track if row["itemId"] == item_id else row for row in records]
    if not any(row["itemId"] == item_id for row in records):
        records.append(track)
    for source, destination in (
        (audio, rp / f"sounds/records/{key}.ogg"),
        (icon, rp / f"textures/items/record_{key}.png"),
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    write_json(bp / f"items/record_{key}.json", {
        "format_version": "1.21.100", "minecraft:item": {
            "description": {"identifier": item_id, "menu_category": {"category": "items", "group": RECORD_GROUP}},
            "components": {"minecraft:max_stack_size": 1, "minecraft:icon": item_id,
                           "minecraft:display_name": {"value": f"item.{item_id}.name"}},
        },
    })
    atlas_path = rp / "textures/item_texture.json"
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    atlas["texture_data"][item_id] = {"textures": f"textures/items/record_{key}"}
    write_json(atlas_path, atlas)
    sound_path = rp / "sounds/sound_definitions.json"
    sounds = json.loads(sound_path.read_text(encoding="utf-8"))
    sounds["sound_definitions"][sound_id] = {
        "category": "record", "sounds": [{"name": f"sounds/records/{key}", "stream": True}],
    }
    write_json(sound_path, sounds)
    for locale in ("ja_JP", "en_US"):
        path = rp / f"texts/{locale}.lang"
        name_key = f"item.{item_id}.name="
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith(name_key)]
        path.write_text("\n".join([*lines, name_key + display_name]) + "\n", encoding="utf-8")
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        "// Generated from supplied assets by scripts/build_molcar_record.py.\nexport const molcarRecords = "
        + json.dumps(records, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8",
    )
    return track


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--icon", required=True, type=Path)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT, help="Authoring output root; use private storage for supplied music")
    parser.add_argument("--key", default="lets_cooking_molcar")
    parser.add_argument("--name", default="Let's Cooking Molcar")
    args = parser.parse_args()
    print(json.dumps(build(args.audio, args.icon, args.root, write=args.write,
                           key=args.key, display_name=args.name), indent=2))
