"""Prepare user-supplied MP3s as a private overlay for the existing Molcar player.

Original MP3s and the existing Let's Cooking OGG are read-only. The three new
OGGs are saved both in the requested desktop folder and in a private authoring
tree. Shared registries are exported as additions, never applied to production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from build_molcar_record import ROOT, RECORD_GROUP, build, read_catalog, write_json

TRACKS = (("shachiku_molcar", "社畜"), ("kanden_molcar", "感電"), ("chika_molcar", "地下"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path),
    ], text=True, encoding="utf-8"))


def measurement(path, target):
    process = subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-map", "0:a:0",
        "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-",
    ], capture_output=True, text=True, encoding="utf-8", check=True)
    tail = process.stderr[process.stderr.rfind("{"):]
    return json.JSONDecoder().raw_decode(tail)[0]


def convert(source, destination, target):
    """Two-pass loudness adjustment; no trim, loop, tempo or duration filters."""
    if destination.exists():
        raise FileExistsError("Existing OGG is retained; inspect it before selecting a new output: " + str(destination))
    before = digest(source)
    first = measurement(source, target)
    options = ":".join([
        f"I={target}", "TP=-1.5", "LRA=11", f"measured_I={first['input_i']}",
        f"measured_TP={first['input_tp']}", f"measured_LRA={first['input_lra']}",
        f"measured_thresh={first['input_thresh']}", f"offset={first['target_offset']}",
        "linear=true", "print_format=json",
    ])
    destination.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-n", "-i", str(source), "-map", "0:a:0",
        "-vn", "-af", "loudnorm=" + options, "-ac", "2", "-ar", "44100",
        "-c:a", "libvorbis", "-b:a", "128k", "-map_metadata", "-1", str(destination),
    ], capture_output=True, text=True, encoding="utf-8", check=True)
    second = json.JSONDecoder().raw_decode(process.stderr[process.stderr.rfind("{"):])[0]
    output_probe = probe(destination)
    audio = [stream for stream in output_probe["streams"] if stream["codec_type"] == "audio"]
    if (len(audio) != 1 or audio[0]["codec_name"] != "vorbis" or audio[0]["channels"] != 2
            or int(audio[0]["sample_rate"]) != 44100 or digest(source) != before):
        raise ValueError("Record audio format or source preservation check failed")
    # MP3 container duration includes encoder padding. Validate decoded samples
    # at a common rate instead of silently truncating to the metadata duration.
    counts = []
    for path in (source, destination):
        pcm = subprocess.check_output([
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
            "-ac", "1", "-ar", "44100", "-f", "s16le", "-",
        ])
        counts.append(len(pcm) // 2)
    if abs(counts[0] - counts[1]) > 128:
        raise ValueError("Conversion unexpectedly changed the decoded audio length")
    return {"source_sha256": before, "audio_sha256": digest(destination),
            "source": str(source), "ogg": str(destination), "bytes": destination.stat().st_size,
            "duration_seconds": float(output_probe["format"]["duration"]),
            "source_decoded_samples": counts[0], "output_decoded_samples": counts[1],
            "before": first, "conversion": second, "after": measurement(destination, target),
            "codec": "vorbis", "sample_rate": 44100, "channels": 2, "nominal_bitrate": 128000}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--ogg-dir", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--icon", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    reference_sha = digest(args.reference)
    reference = probe(args.reference)
    stream = next(s for s in reference["streams"] if s["codec_type"] == "audio")
    if (stream["codec_name"], int(stream["sample_rate"]), stream["channels"], int(stream["bit_rate"])) != ("vorbis", 44100, 2, 128000):
        raise ValueError("Existing Let's Cooking format differs from the reviewed recording")
    source_inventory = {p.name: digest(p) for p in args.source_dir.glob("*.mp3")}
    reference_level = measurement(args.reference, -16)
    # Keep the unchanged, very loud reference within 2 LU while adding headroom
    # for new lossy encodes. Never raise the original OGG's clipped true peaks.
    target = round(float(reference_level["input_i"]) - 2, 2)
    if not -24 <= target <= -5:
        raise ValueError("Reference loudness is outside the supported music range")
    authoring = args.output / "authoring"
    legacy = Path("minecraft/resource_packs/ichiyon_avatar_rp")
    catalog = Path("minecraft/behavior_packs/import_structures/scripts/molcar_records_catalog.js")
    for relative in (legacy / "textures/item_texture.json", legacy / "sounds/sound_definitions.json",
                     legacy / "texts/ja_JP.lang", legacy / "texts/en_US.lang", catalog):
        target_file = authoring / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target_file)
    baseline = read_catalog(authoring / catalog)
    records, rows = [], []
    for key, title in TRACKS:
        source = args.source_dir / (title + ".mp3")
        if not source.is_file():
            raise FileNotFoundError(source)
        converted = convert(source, args.ogg_dir / (title + ".ogg"), target)
        records.append(build(Path(converted["ogg"]), args.icon, authoring, write=True,
                             key=key, display_name="モルカーレコード：" + title))
        rows.append({"key": key, "title": title, **converted})
        write_json(args.output / "conversion-progress.json", rows)
    if reference_sha != digest(args.reference) or source_inventory != {p.name: digest(p) for p in args.source_dir.glob("*.mp3")}:
        raise ValueError("An original source changed during conversion")
    final = read_catalog(authoring / catalog)
    requested = {f"ichiyon:record_{key}" for key, _ in TRACKS}
    if any(row not in final for row in baseline if row['itemId'] not in requested):
        raise ValueError("Existing playback catalog entries changed")
    atlas = json.loads((authoring / legacy / "textures/item_texture.json").read_text(encoding="utf-8"))
    sounds = json.loads((authoring / legacy / "sounds/sound_definitions.json").read_text(encoding="utf-8"))
    patch = {"texture_data": {track["itemId"]: atlas["texture_data"][track["itemId"]] for track in records},
             "sound_definitions": {track["soundId"]: sounds["sound_definitions"][track["soundId"]] for track in records},
             "language": {f"item.ichiyon:record_{key}.name": "モルカーレコード：" + title for key, title in TRACKS},
             "record_menu_category": {"category": "items", "group": RECORD_GROUP}}
    write_json(args.output / "registration-additions.json", patch)
    write_json(args.output / "records-proof.json", {
        "scope": "private_media_conversion_and_existing_player_registration", "source_sha256": source_inventory,
        "reference_sha256": reference_sha, "reference_loudness": reference_level, "target_lufs": target,
        "tracks": rows, "registered_tracks": final, "originals_unchanged": True,
        "new_player_created": False, "bds_verified": False, "client_audio_verified": False,
        "production_applied": False, "private_authoring_root": str(authoring),
    })
    print(json.dumps({"output": str(args.output), "tracks": len(rows), "originals_unchanged": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
