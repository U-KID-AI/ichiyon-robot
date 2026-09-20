import json
import math
import re
import sys
import hashlib
from pathlib import Path

from PIL import Image


ROOT_DIR = Path(__file__).resolve().parent.parent
ASSET_DIR = ROOT_DIR / "assets" / "minecraft" / "posters"
BP_DIR = ROOT_DIR / "minecraft" / "behavior_packs" / "ichiyon_avatar_bp"
RP_DIR = ROOT_DIR / "minecraft" / "resource_packs" / "ichiyon_avatar_rp"
SCRIPT_PATH = ROOT_DIR / "minecraft" / "behavior_packs" / "import_structures" / "scripts" / "main.js"
ORIGINAL_SOURCE_DIR = Path.home() / "Desktop" / "神社" / "ping"

CANVAS_SIZE = (367, 512)
LARGE_POSTER_COLUMNS = 4
LARGE_POSTER_ROWS = 5
SEGMENT_COUNT = LARGE_POSTER_COLUMNS * LARGE_POSTER_ROWS
EXPECTED_WIDTH_GU = 80.0 * CANVAS_SIZE[0] / CANVAS_SIZE[1]
EXPECTED_HEIGHT_GU = 80.0
EXPECTED_SEGMENT_WIDTH_GU = EXPECTED_WIDTH_GU / LARGE_POSTER_COLUMNS
EXPECTED_SEGMENT_HEIGHT_GU = EXPECTED_HEIGHT_GU / LARGE_POSTER_ROWS
EXPECTED_Z_ORIGIN = 7.46875
EXPECTED_Z_SIZE = 0.03125

LARGE_POSTERS = {
    "raio": {
        "command_type": "poster_raio",
        "base": "ichiyon:poster_raio",
        "texture": "poster_raio",
        "source": "raio_source.jpg",
        "ja": "ライオポスター",
        "en": "Raio Poster",
    },
    "trent": {
        "command_type": "poster_trent",
        "base": "ichiyon:poster_trent",
        "texture": "poster_trent",
        "source": "trent_source.jpg",
        "original": "トレントポスター.jpg",
        "ja": "トレントポスター",
        "en": "Trent Poster",
    },
    "aurelia": {
        "command_type": "poster_aurelia",
        "base": "ichiyon:poster_aurelia",
        "texture": "poster_aurelia",
        "source": "aurelia_source.jpg",
        "original": "オーレリアポスター.jpg",
        "ja": "オーレリアポスター",
        "en": "Aurelia Poster",
    },
    "killzael": {
        "command_type": "poster_killzael",
        "base": "ichiyon:poster_killzael",
        "texture": "poster_killzael",
        "source": "killzael_source.jpg",
        "original": "キルザエルポスター.jpg",
        "ja": "キルザエルポスター",
        "en": "Killzael Poster",
    },
    "caravan_mammoth": {
        "command_type": "poster_caravan_mammoth",
        "base": "ichiyon:poster_caravan_mammoth",
        "texture": "poster_caravan_mammoth",
        "source": "caravan_mammoth_source.jpg",
        "original": "キャラバンマンモスポスター.jpg",
        "ja": "キャラバンマンモスポスター",
        "en": "Caravan Mammoth Poster",
    },
    "itsutake": {
        "command_type": "poster_itsutake",
        "base": "ichiyon:poster_itsutake",
        "texture": "poster_itsutake",
        "source": "itsutake_source.jpg",
        "original": "イツタケポスター.jpg",
        "ja": "イツタケポスター",
        "en": "Itsutake Poster",
    },
}


def check(name, ok, detail=""):
    print("[{0}] {1}{2}".format("OK" if ok else "NG", name, " - {0}".format(detail) if detail else ""))
    return ok


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approx(left, right, tolerance=0.0001):
    return abs(float(left) - float(right)) <= tolerance


def geometry_by_id(path):
    data = load_json(path)
    return {item["description"]["identifier"]: item for item in data["minecraft:geometry"]}


def first_cube(geometry):
    return geometry["bones"][0]["cubes"][0]


def alpha_bbox(image):
    if image.mode != "RGBA":
        return None
    alpha = image.getchannel("A")
    return alpha.getbbox()


def is_empty_loot(path):
    return load_json(path) == {"pools": []}


def base_loot_drops(path, item_id):
    data = load_json(path)
    return item_id in json.dumps(data, ensure_ascii=False) and data.get("pools")


def block_identifier(data):
    return data["minecraft:block"]["description"]["identifier"]


def block_components(data):
    return data["minecraft:block"]["components"]


def block_has_no_creative_menu(data):
    return "menu_category" not in data["minecraft:block"]["description"]


def segment_id(key, row, column):
    return f"ichiyon:poster_{key}_r{row}c{column}"


def segment_file(key, row, column):
    return BP_DIR / "blocks" / f"poster_{key}_r{row}c{column}.json"


def segment_loot_file(key, row, column):
    return BP_DIR / "loot_tables" / "blocks" / f"poster_{key}_r{row}c{column}.json"


def verify_texture(key, meta):
    results = []
    source = ASSET_DIR / meta["source"]
    texture = RP_DIR / "textures" / "blocks" / f"{meta['texture']}.png"
    results.append(check(f"{key} source asset exists", source.exists(), str(source)))
    results.append(check(f"{key} processed texture exists", texture.exists(), str(texture)))
    if "original" in meta:
        original = ORIGINAL_SOURCE_DIR / meta["original"]
        results.append(check(f"{key} original source exists", original.exists(), str(original)))
        if source.exists() and original.exists():
            results.append(check(f"{key} source asset matches original bytes", sha256(source) == sha256(original)))
    if not source.exists() or not texture.exists():
        return results
    with Image.open(source) as source_image, Image.open(texture) as texture_image:
        source_ratio = source_image.width / source_image.height
        results.append(check(f"{key} texture is RGBA 367x512", texture_image.mode == "RGBA" and texture_image.size == CANVAS_SIZE, f"{texture_image.mode} {texture_image.size}"))
        bbox = alpha_bbox(texture_image)
        results.append(check(f"{key} texture has non-transparent poster area", bbox is not None, str(bbox)))
        if bbox:
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            texture_ratio = width / height
            results.append(check(f"{key} texture preserves source aspect ratio", abs(source_ratio - texture_ratio) < 0.01, f"source={source_ratio:.4f} texture={texture_ratio:.4f}"))
            results.append(check(f"{key} texture is contained without crop", width <= CANVAS_SIZE[0] and height <= CANVAS_SIZE[1], f"{width}x{height}"))
    return results


def verify_large_poster_geometry():
    results = []
    geometries = geometry_by_id(RP_DIR / "models" / "blocks" / "poster_raio.geo.json")
    results.append(check("shared base geometry exists", "geometry.poster_raio" in geometries))
    for row in range(LARGE_POSTER_ROWS):
        for column in range(LARGE_POSTER_COLUMNS):
            geometry_id = f"geometry.poster_raio_r{row}c{column}"
            geometry = geometries.get(geometry_id)
            results.append(check(f"{geometry_id} exists", geometry is not None))
            if not geometry:
                continue
            cube = first_cube(geometry)
            uv = cube.get("uv", {})
            size = cube.get("size", [])
            origin = cube.get("origin", [])
            expected_uv = [column * CANVAS_SIZE[0] / LARGE_POSTER_COLUMNS, row * CANVAS_SIZE[1] / LARGE_POSTER_ROWS]
            expected_uv_size = [CANVAS_SIZE[0] / LARGE_POSTER_COLUMNS, CANVAS_SIZE[1] / LARGE_POSTER_ROWS]
            results.append(check(f"{geometry_id} uses north face only", set(uv.keys()) == {"north"}, sorted(uv.keys())))
            results.append(check(f"{geometry_id} within 30 geometry unit limit", max(size) <= 30.0, size))
            results.append(check(f"{geometry_id} keeps z-fighting fix", approx(origin[2], EXPECTED_Z_ORIGIN) and approx(size[2], EXPECTED_Z_SIZE), f"origin.z={origin[2]} size.z={size[2]}"))
            results.append(check(f"{geometry_id} has expected segment size", approx(size[0], EXPECTED_SEGMENT_WIDTH_GU) and approx(size[1], EXPECTED_SEGMENT_HEIGHT_GU), size))
            north = uv.get("north", {})
            results.append(check(f"{geometry_id} UV split is correct", all(approx(a, b) for a, b in zip(north.get("uv", []), expected_uv)) and all(approx(a, b) for a, b in zip(north.get("uv_size", []), expected_uv_size)), north))
    results.append(check("no obsolete r5 geometries", all(not key.startswith("geometry.poster_raio_r5") for key in geometries)))
    return results


def verify_large_poster_blocks(key, meta, blocks_json, terrain, ja, en):
    results = []
    base_path = BP_DIR / "blocks" / f"poster_{key}.json"
    base_loot_path = BP_DIR / "loot_tables" / "blocks" / f"poster_{key}.json"
    results.append(check(f"{key} base block exists", base_path.exists(), str(base_path)))
    results.append(check(f"{key} base loot exists", base_loot_path.exists(), str(base_loot_path)))
    if base_path.exists():
        base_block = load_json(base_path)
        components = block_components(base_block)
        results.append(check(f"{key} base identifier", block_identifier(base_block) == meta["base"], block_identifier(base_block)))
        results.append(check(f"{key} base uses shared Raio geometry", components.get("minecraft:geometry") == "geometry.poster_raio", components.get("minecraft:geometry")))
        results.append(check(f"{key} base is creative item", "menu_category" in base_block["minecraft:block"]["description"]))
        results.append(check(f"{key} base collision false", components.get("minecraft:collision_box") is False))
    if base_loot_path.exists():
        results.append(check(f"{key} base drops one base item", bool(base_loot_drops(base_loot_path, meta["base"]))))
    results.append(check(f"{key} blocks.json base entry", blocks_json.get(meta["base"], {}).get("textures") == meta["texture"]))
    results.append(check(f"{key} terrain texture entry", terrain.get("texture_data", {}).get(meta["texture"], {}).get("textures") == f"textures/blocks/{meta['texture']}"))
    results.append(check(f"{key} Japanese lang", f"tile.{meta['base']}.name={meta['ja']}" in ja))
    results.append(check(f"{key} English lang", f"tile.{meta['base']}.name={meta['en']}" in en))
    segment_total = 0
    for row in range(LARGE_POSTER_ROWS):
        for column in range(LARGE_POSTER_COLUMNS):
            segment_total += 1
            expected_id = segment_id(key, row, column)
            block_path = segment_file(key, row, column)
            loot_path = segment_loot_file(key, row, column)
            results.append(check(f"{expected_id} block exists", block_path.exists()))
            results.append(check(f"{expected_id} empty loot exists", loot_path.exists()))
            if block_path.exists():
                block = load_json(block_path)
                components = block_components(block)
                results.append(check(f"{expected_id} identifier", block_identifier(block) == expected_id))
                results.append(check(f"{expected_id} hidden from creative inventory", block_has_no_creative_menu(block)))
                results.append(check(f"{expected_id} uses shared segment geometry", components.get("minecraft:geometry") == f"geometry.poster_raio_r{row}c{column}", components.get("minecraft:geometry")))
                results.append(check(f"{expected_id} collision false", components.get("minecraft:collision_box") is False))
                results.append(check(f"{expected_id} texture", components.get("minecraft:material_instances", {}).get("*", {}).get("texture") == meta["texture"]))
                results.append(check(f"{expected_id} has explicit empty loot reference", components.get("minecraft:loot") == f"loot_tables/blocks/poster_{key}_r{row}c{column}.json", components.get("minecraft:loot")))
            if loot_path.exists():
                results.append(check(f"{expected_id} loot is exactly empty", is_empty_loot(loot_path)))
            results.append(check(f"{expected_id} blocks.json entry", blocks_json.get(expected_id, {}).get("textures") == meta["texture"]))
    results.append(check(f"{key} has 20 segment slots", segment_total == SEGMENT_COUNT, segment_total))
    return results


def verify_script():
    results = []
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    results.append(check("script has generic LARGE_POSTERS registry", "const LARGE_POSTERS = [" in script and "LARGE_POSTER_COLUMNS = 4" in script and "LARGE_POSTER_ROWS = 5" in script))
    results.append(check("script handles all four cardinal directions", all(direction in script for direction in ("north", "south", "east", "west")) and "posterVectors" in script))
    results.append(check("script checks all 20 cells before expansion", "allLargePosterCellsReady" in script and "LARGE_POSTER_SEGMENTS.filter" in script and "isReplaceablePosterTarget" in script))
    results.append(check("script removes incomplete placement base", "setBlockType(dimension, baseLocation, AIR_BLOCK_TYPE)" in script))
    results.append(check("script cleans up same poster segments on break", "cleanupLargePosterSegment" in script and "blockSegment.poster === poster" in script))
    results.append(check("script returns base item only in survival/adventure", "playerShouldReceivePosterDrop" in script and 'gameMode === "survival" || gameMode === "adventure"' in script))
    results.append(check("script adds no extra tick loop for posters", script.count("system.runInterval") == 1 and "POLL_INTERVAL_TICKS" in script))
    results.append(check("script does not expose arbitrary item IDs", "command.item" not in script and "BlockPermutation.resolve(command" not in script))
    for key, meta in LARGE_POSTERS.items():
        results.append(check(f"script has {key} base id", meta["base"] in script))
        results.append(check(f"script has {key} command type", meta["command_type"] in script))
        for row in range(LARGE_POSTER_ROWS):
            for column in range(LARGE_POSTER_COLUMNS):
                results.append(check(f"script can construct {key} r{row}c{column}", f"{meta['base']}_r${{row}}c${{column}}" in script or f"{meta['base']}_r{row}c{column}" in script or "segmentPrefix" in script))
    results.append(check("script has no obsolete 4x6 message", "4 x 6" not in script))
    results.append(check("script has no obsolete r5 constants", not re.search(r"r5c[0-3]", script)))
    return results


def verify_irsia_still_present(blocks_json, terrain, ja, en):
    results = []
    results.append(check("Irsia poster block remains", (BP_DIR / "blocks" / "poster_irsia.json").exists()))
    results.append(check("Irsia texture remains", (RP_DIR / "textures" / "blocks" / "poster_irsia.png").exists()))
    results.append(check("Irsia geometry remains", (RP_DIR / "models" / "blocks" / "poster_irsia.geo.json").exists()))
    results.append(check("Irsia blocks.json remains", blocks_json.get("ichiyon:poster_irsia", {}).get("textures") == "poster_irsia"))
    results.append(check("Irsia terrain texture remains", terrain.get("texture_data", {}).get("poster_irsia", {}).get("textures") == "textures/blocks/poster_irsia"))
    results.append(check("Irsia Japanese lang remains", "tile.ichiyon:poster_irsia.name=イルシアポスター" in ja))
    results.append(check("Irsia English lang remains", "tile.ichiyon:poster_irsia.name=" in en))
    return results


def verify_versions():
    results = []
    bp_manifest = load_json(BP_DIR / "manifest.json")
    rp_manifest = load_json(RP_DIR / "manifest.json")
    import_manifest = load_json(ROOT_DIR / "minecraft" / "behavior_packs" / "import_structures" / "manifest.json")
    results.append(check("avatar BP version [1, 0, 5]", bp_manifest["header"]["version"] == [1, 0, 5] and bp_manifest["modules"][0]["version"] == [1, 0, 5]))
    results.append(check("avatar RP version [1, 0, 4]", rp_manifest["header"]["version"] == [1, 0, 4] and rp_manifest["modules"][0]["version"] == [1, 0, 4]))
    results.append(check("import structures version remains [1, 0, 1]", import_manifest["header"]["version"] == [1, 0, 1] and all(module["version"] == [1, 0, 1] for module in import_manifest["modules"])))
    return results


def main():
    results = []
    blocks_json = load_json(RP_DIR / "blocks.json")
    terrain = load_json(RP_DIR / "textures" / "terrain_texture.json")
    ja = (RP_DIR / "texts" / "ja_JP.lang").read_text(encoding="utf-8")
    en = (RP_DIR / "texts" / "en_US.lang").read_text(encoding="utf-8")

    results.extend(verify_versions())
    results.extend(verify_irsia_still_present(blocks_json, terrain, ja, en))
    results.extend(verify_large_poster_geometry())
    for key, meta in LARGE_POSTERS.items():
        results.extend(verify_texture(key, meta))
        results.extend(verify_large_poster_blocks(key, meta, blocks_json, terrain, ja, en))
    results.extend(verify_script())
    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
