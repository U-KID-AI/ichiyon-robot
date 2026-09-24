"""Partition the authoring RP after DB assets are compiled, without recoding media."""
import json

from bot.services.minecraft_cosmetics import json_bytes

LEGACY = "resource_packs/ichiyon_avatar_rp/"
LEGACY_UUID = "3e1bcf76-b5e3-465a-a184-d2d90cfa0d74"
SPLIT_RESOURCE_PACKS = tuple("resource_packs/" + name + "/" for name in (
    "ichiyon_core_rp", "ichiyon_mannequin_skins_rp", "ichiyon_accessories_rp",
    "ichiyon_posters_rp", "ichiyon_video_rp", "ichiyon_records_rp",
))
CORE, SKINS, ACCESSORIES, POSTERS, VIDEO, RECORDS = SPLIT_RESOURCE_PACKS
VIDEO_BIG = "resource_packs/ichiyon_video_big_rp/"
DIRECT_RESOURCE_PACKS = (VIDEO_BIG,)
RESOURCE_PACKS = (*SPLIT_RESOURCE_PACKS, *DIRECT_RESOURCE_PACKS)

# Mirrored by the standalone Control API; checked for agreement in archive tests.
MAX_ARCHIVE = 192 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_FILES = 16384


def owner(path):
    if "video_screen" in path or path == "materials/entity.material":
        return VIDEO
    if path.startswith(("sounds/records/", "textures/items/record_")):
        return RECORDS
    if path.startswith(("textures/blocks/poster_", "models/blocks/poster_")):
        return POSTERS
    if (path.startswith(("textures/entity/cosmetics/accessory_", "textures/items/cosmetics/accessory_",
                         "models/entity/cosmetics/", "render_controllers/cosmetics_accessories"))
            or path in {f"entity/{name}.entity.json" for name in ("molcar", "molcar2", "molcar3")}):
        return ACCESSORIES
    if (path.startswith(("entity/avatar", "entity/player.", "animations/avatar.",
                         "models/entity/avatar.", "models/entity/cosmetics_avatar.", "models/entity/cosmetics_player.",
                         "render_controllers/avatar.", "render_controllers/cosmetics_player.",
                         "textures/entity/avatar/", "textures/entity/cosmetics/skin_"))
            or path in {"textures/entity/cosmetics/deleted_skin.png", "COPYING-Mojang.txt"}):
        return SKINS
    return CORE


def entry_owner(key):
    if "video_screen" in key:
        return VIDEO
    if "record_" in key or ":record." in key:
        return RECORDS
    if "poster" in key:
        return POSTERS
    if ("accessory_" in key or "itemGroup.molcar_" in key
            or key in {"action.interact.ichiyon_cosmetic_equip", "action.interact.ichiyon_cosmetic_remove"}):
        return ACCESSORIES
    if "avatar" in key or key == "action.interact.ichiyon_cosmetic_wardrobe":
        return SKINS
    return CORE


REGISTRIES = {
    "textures/item_texture.json": "texture_data",
    "textures/terrain_texture.json": "texture_data",
    "sounds/sound_definitions.json": "sound_definitions",
    "blocks.json": None,
}


def split_resource_packs(root, files):
    """Each asset/key has one owner; manifests are stable, never tied to DB revision.

    Bedrock's keyed registries are distributed by entry, not duplicated whole files.
    The legacy authoring pack is deliberately absent from the deployed archive.
    """
    result = {path: data for path, data in files.items() if not path.startswith(LEGACY)}
    for path, data in files.items():
        if not path.startswith(LEGACY):
            continue
        relative = path[len(LEGACY):]
        if relative == "manifest.json":
            continue
        if relative in REGISTRIES:
            document = json.loads(data)
            field = REGISTRIES[relative]
            entries = document[field] if field else {k: v for k, v in document.items() if k != "format_version"}
            metadata = {k: v for k, v in document.items() if k != field} if field else {"format_version": document["format_version"]}
            for pack in SPLIT_RESOURCE_PACKS:
                subset = {k: v for k, v in entries.items() if entry_owner(k) == pack}
                if subset:
                    value = dict(metadata)
                    if "resource_pack_name" in value:
                        value["resource_pack_name"] = pack.rstrip("/").split("/")[-1]
                    if field:
                        value[field] = subset
                    else:
                        value.update(subset)
                    result[pack + relative] = json_bytes(value)
        elif relative.startswith("texts/") and relative.endswith(".lang"):
            buckets = {pack: [] for pack in SPLIT_RESOURCE_PACKS}
            for line in data.decode("utf-8-sig").splitlines():
                key = line.partition("=")[0]
                if key in {"pack.name", "pack.description"}:
                    continue  # Each manifest contains its own literal display name.
                buckets[entry_owner(key)].append(line)
            for pack, lines in buckets.items():
                if lines:
                    result[pack + relative] = ("\n".join(lines) + "\n").encode("utf-8")
        elif relative == "texts/languages.json":
            for pack in SPLIT_RESOURCE_PACKS:
                result[pack + relative] = data
        else:
            result[owner(relative) + relative] = data
    for pack in RESOURCE_PACKS:
        result[pack + "manifest.json"] = (root / pack / "manifest.json").read_bytes()
    return result
