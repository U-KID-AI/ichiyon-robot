"""Managed poster pack assets; fixed poster identifiers and files remain untouched."""
from copy import deepcopy
import json

from bot.services.minecraft_cosmetics import json_bytes, public_asset, POSTER_PIXELS_PER_BLOCK

BP = "behavior_packs/ichiyon_avatar_bp/"
RP = "resource_packs/ichiyon_avatar_rp/"
PREFIX = "poster_managed_"


def poster_entry(record):
    base = f"ichiyon:{PREFIX}{record['id']}"
    return public_asset(record) | {"baseId": base, "segmentPrefix": base, "label": record["name"],
                                   "columns": record["width"], "rows": record["height"]}


def compile_posters(root, posters, files):
    if not posters:
        return

    def read(path):
        return json.loads(files[path] if path in files else (root / path).read_bytes())

    def put(path, value):
        files[path] = json_bytes(value)

    template = read(BP + "blocks/poster_raio.json")
    blocks = read(RP + "blocks.json")
    terrain = read(RP + "textures/terrain_texture.json")
    creative_path = BP + "item_catalog/crafting_item_catalog.json"
    creative = read(creative_path)
    categories = creative["minecraft:crafting_items_catalog"]["categories"]
    construction = next(c for c in categories if c["category_name"] == "construction")
    group = next(g for g in construction["groups"]
                 if g.get("group_identifier", {}).get("name") == "ichiyon:itemGroup.posters")
    group["items"] = [item for item in group["items"] if not item.startswith("ichiyon:" + PREFIX)]

    for record in posters:
        base = f"{PREFIX}{record['id']}"
        width, height = record["width"], record["height"]
        pixels = POSTER_PIXELS_PER_BLOCK
        texture_size = [width * pixels, height * pixels]
        files[RP + f"textures/blocks/{base}.png"] = record["texture"]
        terrain["texture_data"][base] = {"textures": f"textures/blocks/{base}"}
        group["items"].append("ichiyon:" + base)
        geometries = []
        # The inventory/base model fits one block; runtime expands it into full cells.
        scale = 16 / max(width, height)
        parts = [(base, [0, 0], texture_size, [-width * scale / 2, (16 - height * scale) / 2, 7.46875],
                  [width * scale, height * scale, 0.03125])]
        parts.extend((f"{base}_r{row}c{column}", [column * pixels, (height - 1 - row) * pixels],
                      [pixels, pixels], [-8, 0, 7.46875], [16, 16, 0.03125])
                     for row in range(height) for column in range(width))
        for name, uv, uv_size, origin, size in parts:
            block = deepcopy(template)
            body = block["minecraft:block"]
            body["description"]["identifier"] = "ichiyon:" + name
            if name != base:
                body["description"].pop("menu_category", None)
            body["components"]["minecraft:geometry"] = "geometry." + name
            body["components"]["minecraft:material_instances"]["*"]["texture"] = base
            body["components"]["minecraft:loot"] = f"loot_tables/blocks/{name}.json"
            put(BP + f"blocks/{name}.json", block)
            # Script owns segment drops; native loot must never duplicate the base item.
            put(BP + f"loot_tables/blocks/{name}.json", {"pools": [{"rolls": 1, "entries": [
                {"type": "item", "name": "ichiyon:" + base}]}]} if name == base else {"pools": []})
            blocks["ichiyon:" + name] = {"textures": base, "sound": "wood"}
            geometries.append({"description": {"identifier": "geometry." + name,
                "texture_width": texture_size[0], "texture_height": texture_size[1],
                "visible_bounds_width": 1.25, "visible_bounds_height": 1.5, "visible_bounds_offset": [0, 0.5, 0]},
                "bones": [{"name": "poster", "pivot": [0, 8, 0], "cubes": [{
                    "origin": origin, "size": size, "uv": {"north": {"uv": uv, "uv_size": uv_size}}}]}]})
        put(RP + f"models/blocks/{base}.geo.json", {"format_version": "1.16.0", "minecraft:geometry": geometries})

    put(RP + "blocks.json", blocks)
    put(RP + "textures/terrain_texture.json", terrain)
    put(creative_path, creative)
    for locale in ("ja_JP", "en_US"):
        path = RP + f"texts/{locale}.lang"
        lines = (files[path] if path in files else (root / path).read_bytes()).decode("utf-8").splitlines()
        lines = [line for line in lines if not line.startswith("tile.ichiyon:" + PREFIX)]
        lines.extend(f"tile.ichiyon:{PREFIX}{record['id']}.name={record['name']}" for record in posters)
        files[path] = ("\n".join(lines) + "\n").encode("utf-8")
