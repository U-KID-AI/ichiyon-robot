"""Build an isolated, PRIVATE Sparrow reference trial from existing conversions.

Only the converted original Sparrow hall and its five display entities are
included. The current game scripts are retained, but the trial has its own entry
point and fresh pack UUIDs. No live/development pack or existing world is edited.
Every build creates a new directory beneath data/junket-private/sparrow-reference.
Requires nbtlib and Pillow; the existing Chunker conversion is not rewritten.
"""
from __future__ import annotations
from junket_private_profile import private_value

import argparse
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import re
import struct
import uuid

import nbtlib as n
from PIL import Image

from junket_private_bundle import json_bytes, private_manifests, select_conversion, sha256, write_zip


ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "minecraft/behavior_packs/ichiyon_junket_bp"
PRIVATE_ROOT = ROOT / "data/junket-private/sparrow-reference"
ENTRY = "scripts/sparrow_trial_main.js"
VERSION = [0, 1, 0]
KINDS = ("bigbox", "littlebox", "diamond", "tv", "partition")
HALL = "structures/junket/sparrow_hall.mcstructure"
TITLE = "JUNKET Sparrow Reference - PRIVATE"
NOTICE = """PRIVATE LOCAL TEST ONLY / 原作由来素材・再配布禁止

This package contains locally converted JUNKET CRAFT 1.09 artwork and architecture.
No permission to redistribute those assets is asserted by this build.
Do not upload this directory, .mcpack, or .mcworld to GitHub, public hosting,
production distribution packs, or another person's server without permission.
This test has separate pack UUIDs and does not install into any existing world.
The tests in build-report.json are packaging checks, not a two-player Minecraft run.
"""
USAGE = """ウラギリスズメ 原作会場・ローカル確認用

1. junket-sparrow-reference.mcworld を Minecraft for Windows で開いてください。
   新しい独立ワールドとしてインポートされ、専用BP/RPも同梱されています。
   .mcpack 2本は個別確認用です。通常は先にインポートする必要はありません。
2. インポートされた「JUNKET Sparrow Reference - PRIVATE」を開きます。
   初回起動時にprivate profileの指定位置へ会場を配置し、受付前へ移動します。
3. 別のプレイヤーがこのテストワールドへ参加し、2人とも受付を操作します。
   賭け金決定→大箱/小箱に宝石を隠す→相手が箱を選ぶ、を6ラウンド確認します。
4. 結果、再戦、退出、入口への帰還を確認してください。

画面を閉じた場合: 受付を操作、または /scriptevent junket:menu open
会場状態の表示: /scriptevent junket:reference status
未準備時の再試行: /scriptevent junket:reference repair

build-report.json のPASSは素材・構造・参照・アーカイブの検証です。
Minecraft実機での2人完走はこのビルダーでは実行していません。
原作由来素材を含むため、このフォルダと成果物は公開・本番配信しないでください。
"""


def level_data():
    # The 118 layers occupy y=-64..53, immediately below the hall's y=54 base.
    # A late script start therefore leaves players on solid ground at the venue.
    layers = {"biome_id": 1, "block_layers": [
        {"block_name": "minecraft:bedrock", "count": 1},
        {"block_name": "minecraft:dirt", "count": 116},
        {"block_name": "minecraft:grass_block", "count": 1}],
        "encoding_version": 6, "preset_id": "ClassicFlat", "world_version": "version.post_1_18"}
    metadata = n.File({
        "LevelName": n.String(TITLE), "StorageVersion": n.Int(10), "Generator": n.Int(2),
        "GameType": n.Int(2), "ForceGameType": n.Byte(1), "Difficulty": n.Int(0),
        "RandomSeed": n.Long(141020),
        **{f"Spawn{axis}": n.Int(value) for axis, value in zip("XYZ", private_value("sparrow.spawn"))},
        "FlatWorldLayers": n.String(json.dumps(layers)), "baseGameVersion": n.String("*"),
        "MinimumCompatibleClientVersion": n.List[n.Int]([1, 26, 44, 0, 0]),
        "lastOpenedWithVersion": n.List[n.Int]([1, 26, 52, 3, 0]),
        "commandsEnabled": n.Byte(1), "hasBeenLoadedInCreative": n.Byte(1),
        "MultiplayerGame": n.Byte(1), "MultiplayerGameIntent": n.Byte(1),
        "LANBroadcast": n.Byte(1), "LANBroadcastIntent": n.Byte(1),
        "playerPermissionsLevel": n.Int(1), "permissionsLevel": n.Int(0),
        "experiments": n.Compound({"experiments_ever_used": n.Byte(0), "saved_with_toggled_experiments": n.Byte(0)}),
        "dodaylightcycle": n.Byte(0), "domobspawning": n.Byte(0),
        "doweathercycle": n.Byte(0), "keepinventory": n.Byte(1), "Time": n.Long(6000),
    })
    output = BytesIO()
    metadata.write(output, byteorder="little")
    payload = output.getvalue()
    return struct.pack("<II", 10, len(payload)) + payload


def manifests():
    current = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    return private_manifests(current, entry=ENTRY, version=VERSION, label="JUNKET Sparrow Reference")


def inspect_hall(data):
    hall = n.File.parse(BytesIO(data), byteorder="little")
    size = [int(value) for value in hall["size"]]
    if size != [27, 13, 29]:
        raise ValueError(f"Unexpected original Sparrow bounds: {size}")
    structure = hall["structure"]
    if len(structure["entities"]):
        raise ValueError("Converted hall must not contain imported Java entities")
    palette = structure["palette"]["default"]["block_palette"]
    names = {str(block["name"]) for block in palette}
    forbidden = {"minecraft:command_block", "minecraft:chain_command_block", "minecraft:repeating_command_block",
                 "minecraft:structure_block", "minecraft:jigsaw"}
    if names & forbidden:
        raise ValueError("Executable source blocks are not allowed in the converted hall")
    layers = structure["block_indices"]
    volume = size[0] * size[1] * size[2]
    if len(layers) != 2 or any(len(layer) != volume for layer in layers):
        raise ValueError("Invalid mcstructure block-index dimensions")
    if any(index < -1 or index >= len(palette) for layer in layers for index in layer):
        raise ValueError("Out-of-range mcstructure block palette reference")
    safe_decorations = {"Sign", "HangingSign", "Banner", "Skull", "FlowerPot"}
    positions = structure["palette"]["default"]["block_position_data"]
    for entry in positions.values():
        tile = entry["block_entity_data"]
        if str(tile.get("id")) not in safe_decorations or "Items" in tile:
            raise ValueError("Unexpected block entity in the converted hall")
    local = [value - start for value, start in zip(private_value("sparrow.spawn"), private_value("sparrow.bounds")[0])]
    def block_at(x, y, z):
        index = int(layers[0][x * size[1] * size[2] + y * size[2] + z])
        return str(palette[index]["name"]) if index >= 0 else "minecraft:air"
    spawn_column = {str(dy): block_at(local[0], local[1] + dy, local[2]) for dy in (-1, 0, 1)}
    # Chunker preserves the original invisible lighting as light_block_N. These
    # blocks have no collision and are valid spawn space, unlike solid decor.
    def passable(name):
        return name == "minecraft:air" or re.fullmatch(r"minecraft:light_block_(?:[0-9]|1[0-5])", name) is not None
    if passable(spawn_column["-1"]) or any(not passable(spawn_column[str(dy)]) for dy in (0, 1)):
        raise ValueError(f"Original spawn is obstructed or has no floor: {spawn_column}")
    return {"size": size, "palette_entries": len(palette), "decorations": len(positions),
            "source_bounds": private_value("sparrow.bounds"), "spawn": private_value("sparrow.spawn"), "spawn_column": spawn_column,
            "sha256": sha256(data)}


def validate_packs(bp, rp):
    bp_manifest, rp_manifest = json.loads(bp["manifest.json"]), json.loads(rp["manifest.json"])
    if ENTRY not in bp:
        raise ValueError(f"Trial entry is missing: {ENTRY}")
    script_entries = [module.get("entry") for module in bp_manifest["modules"] if module["type"] == "script"]
    if script_entries != [ENTRY]:
        raise ValueError("Trial BP must start only the isolated Sparrow entry")
    pack_ids = [manifest["header"]["uuid"] for manifest in (bp_manifest, rp_manifest)]
    pack_ids += [module["uuid"] for manifest in (bp_manifest, rp_manifest) for module in manifest["modules"]]
    current_manifest = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    current_ids = {current_manifest["header"]["uuid"], *(module["uuid"] for module in current_manifest["modules"])}
    if len(set(pack_ids)) != len(pack_ids) or set(pack_ids) & current_ids:
        raise ValueError("Trial pack UUIDs must be distinct from each other and the normal BP")
    for value in pack_ids:
        uuid.UUID(value)
    structures = [name for name in bp if name.endswith(".mcstructure")]
    if structures != [HALL]:
        raise ValueError(f"Trial must contain only the original Sparrow structure: {structures}")
    ids = []
    for name in bp:
        if name.startswith("entities/"):
            ids.append(json.loads(bp[name])["minecraft:entity"]["description"]["identifier"])
    if set(ids) != {f"junket:{kind}" for kind in KINDS} or len(ids) != len(KINDS):
        raise ValueError("Trial entities must be exactly the five original Sparrow displays")
    geometries = json.loads(rp["models/entity/sparrow.geo.json"])["minecraft:geometry"]
    geometry_ids = {geo["description"]["identifier"] for geo in geometries}
    controllers = {}
    for name, data in rp.items():
        if name.startswith("render_controllers/"):
            controllers.update(json.loads(data)["render_controllers"])
    referenced = set()
    for kind in KINDS:
        client = json.loads(rp[f"entity/{kind}.entity.json"])["minecraft:client_entity"]["description"]
        if client["identifier"] != f"junket:{kind}":
            raise ValueError(f"Client entity mismatch: {kind}")
        referenced.update(client["geometry"].values())
        if any(controller not in controllers for controller in client["render_controllers"]):
            raise ValueError(f"Unresolved render controller: {kind}")
        if any(texture + ".png" not in rp for texture in client["textures"].values()):
            raise ValueError(f"Unresolved original texture: {kind}")
    if referenced != geometry_ids:
        raise ValueError("Original geometry must match the five displays without extra models")
    if len(geometry_ids) != 18:
        raise ValueError("Expected 14 box frames, two monitor frames, one jewel and one partition")
    with Image.open(BytesIO(rp["textures/entity/junket_sparrow.png"])) as texture:
        if texture.size != (256, 128):
            raise ValueError("Unexpected original Sparrow texture atlas size")
    # Retain all current source scripts, but fail if a new entry import is missing.
    for name, data in bp.items():
        if name.startswith("scripts/") and name.endswith(".js"):
            for relative in re.findall(r"(?:from\s*|import\s*)['\"](\.[^'\"]+)['\"]", data.decode("utf-8-sig")):
                target = (PACK / name).parent.joinpath(relative).resolve().relative_to(PACK.resolve()).as_posix()
                if target not in bp:
                    raise ValueError(f"Missing script import {relative} in {name}")
    rp_id = rp_manifest["header"]["uuid"]
    if {"uuid": rp_id, "version": VERSION} not in bp_manifest["dependencies"]:
        raise ValueError("Trial BP must depend on this trial RP")
    return {"structure": inspect_hall(bp[HALL]), "entity_identifiers": sorted(ids),
            "geometry_count": len(geometry_ids), "script_count": sum(name.endswith(".js") for name in bp),
            "entrypoint": ENTRY, "uuid_isolation_check": "PASS",
            "pack_reference_check": "PASS", "model_reference_check": "PASS", "script_import_check": "PASS"}


def build(assets, source, output_root=PRIVATE_ROOT):
    assets, source = assets.resolve(), source.resolve()
    output_root = output_root.resolve()
    if not output_root.is_relative_to(PRIVATE_ROOT.resolve()):
        raise ValueError("Original-derived outputs must remain beneath data/junket-private/sparrow-reference")
    if not (PACK / ENTRY).is_file():
        raise ValueError(f"Implement the isolated trial entry first: {PACK / ENTRY}")
    rp_names = ["models/entity/sparrow.geo.json", "textures/entity/junket_sparrow.png",
                "texts/languages.json", "texts/ja_JP.lang", "texts/en_US.lang"]
    rp_names += [f"{folder}/{kind}{suffix}" for kind in KINDS
                 for folder, suffix in (("entity", ".entity.json"), ("render_controllers", ".json"))]
    bp, rp, source_evidence = select_conversion(assets, source,
        structures={"sparrow_hall": private_value("sparrow.bounds")}, entities=KINDS, resources=rp_names)
    bp.update({file.relative_to(PACK).as_posix(): file.read_bytes()
               for file in (PACK / "scripts").rglob("*.js") if file.is_file()})
    reference = private_value("sparrow.reference")
    if (reference.get("id") != "sparrow" or reference.get("distribution") != "private-local-only"
            or reference.get("origin") != private_value("sparrow.bounds")[0]
            or [reference.get("destination", {}).get(axis) for axis in "xyz"] != private_value("sparrow.spawn")):
        raise ValueError("Private Sparrow runtime reference differs from the selected extraction/spawn")
    bp["scripts/sparrow_reference.js"] = (
        "// PRIVATE ORIGINAL REFERENCE; do not redistribute.\nexport const SPARROW_REFERENCE = "
        + json.dumps(reference, ensure_ascii=False) + ";\n").encode("utf-8")
    bp_manifest, rp_manifest = manifests()
    bp["manifest.json"], rp["manifest.json"] = json_bytes(bp_manifest), json_bytes(rp_manifest)
    checks = validate_packs(bp, rp)
    generation = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = output_root / generation
    output.mkdir(parents=True, exist_ok=False)
    provenance = {
        "generation": generation, "distribution": "PRIVATE_LOCAL_TEST_ONLY_NO_REDISTRIBUTION",
        "permission_to_redistribute_original_assets": False,
        **source_evidence,
        "conversion_method": "Existing build_junket_venue.py / Chunker conversion; selected Sparrow assets copied byte-for-byte",
        "hall_source_bounds": private_value("sparrow.bounds"), "structure_placement": private_value("sparrow.bounds")[0], "spawn": private_value("sparrow.spawn"),
        "world_structure_initialization": "SCRIPT_ON_FIRST_LOAD",
        "selected_conversion_assets_copied_byte_for_byte": True,
        "runtime_script_sha256": {name: sha256(data) for name, data in bp.items() if name.startswith("scripts/")},
        "bp_uuid": bp_manifest["header"]["uuid"], "rp_uuid": rp_manifest["header"]["uuid"],
        "other_16_venue_assets_included": False, "production_world_modified": False,
        "minecraft_two_player_completion": "NOT_PERFORMED_BY_BUILDER",
    }
    for files in (bp, rp):
        files["PRIVATE_DO_NOT_REDISTRIBUTE.txt"] = NOTICE.encode("utf-8")
        files["provenance.json"] = json_bytes(provenance)
    for directory, files in (("behavior_pack", bp), ("resource_pack", rp)):
        for name, data in files.items():
            destination = output / directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    metadata = level_data()
    header, length = struct.unpack("<II", metadata[:8])
    if header != 10 or length != len(metadata) - 8:
        raise ValueError("Invalid Bedrock level.dat header")
    parsed_metadata = n.File.parse(BytesIO(metadata[8:]), byteorder="little")
    if [int(parsed_metadata[f"Spawn{axis}"]) for axis in "XYZ"] != private_value("sparrow.spawn"):
        raise ValueError("Unexpected world spawn")
    world = {"level.dat": metadata, "levelname.txt": (TITLE + "\n").encode(),
             "world_behavior_packs.json": json_bytes([{"pack_id": bp_manifest["header"]["uuid"], "version": VERSION}]),
             "world_resource_packs.json": json_bytes([{"pack_id": rp_manifest["header"]["uuid"], "version": VERSION}]),
             "PRIVATE_DO_NOT_REDISTRIBUTE.txt": NOTICE.encode("utf-8"), "provenance.json": json_bytes(provenance)}
    world.update({f"behavior_packs/junket_sparrow_reference_bp/{name}": data for name, data in bp.items()})
    world.update({f"resource_packs/junket_sparrow_reference_rp/{name}": data for name, data in rp.items()})
    results = [write_zip(output / "junket-sparrow-reference-bp.mcpack", bp),
               write_zip(output / "junket-sparrow-reference-rp.mcpack", rp),
               write_zip(output / "junket-sparrow-reference.mcworld", world)]
    report = {"generation": generation, "output": str(output), "checks": checks,
              "level_dat_check": "PASS", "artifacts": results,
              "minecraft_client_import": "NOT_PERFORMED_BY_BUILDER",
              "minecraft_two_player_completion": "NOT_PERFORMED_BY_BUILDER"}
    (output / "provenance.json").write_bytes(json_bytes(provenance))
    (output / "build-report.json").write_bytes(json_bytes(report))
    (output / "PRIVATE_DO_NOT_REDISTRIBUTE.txt").write_text(NOTICE, encoding="utf-8")
    (output / "START_HERE.txt").write_text(USAGE, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=ROOT / "data/junket-private/assets",
                        help="Existing build_junket_venue.py conversion directory")
    parser.add_argument("--source", type=Path, required=True, help="Original JUNKET ZIP matching conversion-report.json")
    parser.add_argument("--output-root", type=Path, default=PRIVATE_ROOT)
    options = parser.parse_args()
    build(options.assets, options.source, options.output_root)
