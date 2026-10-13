"""Built-in functional equipment; independent of permanent DB accessory IDs."""
from copy import deepcopy
from io import BytesIO
import math

from PIL import Image, ImageDraw

from bot.services.minecraft_cosmetics import MOLCARS

ITEM = "ichiyon:molcar_float_ring"
PROPERTY = "ichiyon:float_ring"
BP = "behavior_packs/ichiyon_avatar_bp/"
RP = "resource_packs/ichiyon_avatar_rp/"


def ring_texture(icon=False):
    image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(32):
        for x in range(32):
            if icon:
                radius = math.hypot((x - 15.5) / 14, (y - 15.5) / 11)
                if not .53 < radius < 1:
                    continue
                red = int((math.atan2(y - 15.5, x - 15.5) + math.pi) / (math.pi / 4)) % 2 == 0
            else:
                red = (x // 8) % 2 == 0
            color = (239, 91, 76) if red else (249, 240, 207)
            shade = .78 if y % 16 >= 12 else 1
            pixels[x, y] = (*[int(c * shade) for c in color], 255)
    if icon:
        ImageDraw.Draw(image).arc((3, 5, 28, 24), 205, 290, fill=(255, 255, 240), width=1)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def compile_float_ring(read, put, files, atlas):
    """Extend generated Molcars, retaining their existing equipment and skeletons."""
    for name in MOLCARS:
        path = BP + f"entities/{name}.behavior.json"
        mob = read(path)
        # The new buoyancy fields require 1.26.10; use the existing Mokuro era.
        mob["format_version"] = "1.26.30"
        entity = mob["minecraft:entity"]
        entity["description"]["properties"][PROPERTY] = {
            "type": "int", "range": [0, 1], "default": 0, "client_sync": True}
        components = entity["components"]
        # 1.26.30 replaced pushable; preserve the original push semantics.
        components.pop("minecraft:pushable", None)
        components["minecraft:pushable_by_entity"] = {}
        components["minecraft:pushable_by_block"] = {}
        normal_float = components.pop("minecraft:behavior.float", {"priority": 0})
        groups = entity["component_groups"]
        for group in groups.values():
            rider = group.get("minecraft:addrider")
            if rider and "entity_type" in rider:
                group["minecraft:addrider"] = {"riders": [rider]}
        normal, floating = "ichiyon:normal_water", "ichiyon:float_ring"
        # Attribute components outlive removal of their group in Bedrock. Restore
        # the original engine default explicitly, including on unequipped spawns.
        groups[normal] = {"minecraft:behavior.float": normal_float,
                          "minecraft:underwater_movement": {"value": .02}}
        groups[floating] = {"minecraft:underwater_movement": {"value": .04}, "minecraft:buoyant": {
            "apply_gravity": False, "base_buoyancy": 0.55,
            "movement_type": "none", "can_auto_step_from_liquid": True,
            "drag_down_on_buoyancy_removed": 0,
            "liquid_blocks": ["minecraft:water", "minecraft:flowing_water"]}}
        # Native sensors repair old entities and persisted state after reload.
        # They do not restart movement or change the boost attribute.
        components["minecraft:environment_sensor"] = {"triggers": [
            {"filters": {"all_of": [
                {"test": "int_property", "domain": PROPERTY, "value": 1},
                {"test": "has_component", "operator": "not", "value": "minecraft:buoyant"}]},
             "event": "ichiyon:float_ring_on"},
            {"filters": {"all_of": [
                {"test": "int_property", "domain": PROPERTY, "value": 0},
                {"any_of": [
                    {"test": "has_component", "value": "minecraft:buoyant"},
                    {"test": "has_component", "operator": "not", "value": "minecraft:behavior.float"}]}]},
             "event": "ichiyon:float_ring_off"}]}
        for event, add, remove in [("on", floating, normal), ("off", normal, floating)]:
            entity["events"][f"ichiyon:float_ring_{event}"] = {
                "remove": {"component_groups": [remove]}, "add": {"component_groups": [add]}}
        interactions = components["minecraft:interact"]["interactions"]
        interactions.append({"interact_text": "action.interact.ichiyon_cosmetic_equip", "use_item": False, "swing": False,
                             "on_interact": {"filters": {"test": "has_equipment", "subject": "other", "domain": "hand", "value": ITEM}}})
        put(path, mob)

        skeleton = read(RP + f"models/entity/{name}.geo.json")["minecraft:geometry"][0]
        bones = [{k: deepcopy(v) for k, v in bone.items() if k not in ("cubes", "locators", "poly_mesh")} for bone in skeleton["bones"]]
        cubes = []
        # Twelve overlapping tangent sections form an oval around the lower body.
        # The common body coordinate system is shared by all three source models.
        for i in range(12):
            angle = i * math.tau / 12
            x, z = 14 * math.cos(angle), 11 * math.sin(angle)
            tangent = math.atan2(11 * math.cos(angle), -14 * math.sin(angle))
            cubes.append({"origin": [round(x - 3.8, 4), 5.5, round(z - 1.75, 4)],
                          "size": [7.6, 3.5, 3.5], "pivot": [round(x, 4), 7.25, round(z, 4)],
                          "rotation": [0, round(-math.degrees(tangent), 4), 0],
                          "uv": {face: {"uv": [0 if i % 3 else 8, 0], "uv_size": [8, 16]}
                                 for face in ("north", "south", "east", "west", "up", "down")}})
        bones.append({"name": "float_ring", "parent": "body", "pivot": [0, 7.25, 0], "cubes": cubes})
        identifier = f"geometry.ichiyon.{name}.float_ring"
        put(RP + f"models/entity/cosmetics/{name}_float_ring.geo.json", {
            "format_version": "1.16.0", "minecraft:geometry": [{"description": {
                "identifier": identifier, "texture_width": 32, "texture_height": 32,
                "visible_bounds_width": 3, "visible_bounds_height": 2.5, "visible_bounds_offset": [0, .75, 0]}, "bones": bones}]})
        path = RP + f"entity/{name}.entity.json"
        client = read(path)
        desc = client["minecraft:client_entity"]["description"]
        desc["geometry"]["float_ring"] = identifier
        desc["textures"]["float_ring"] = "textures/entity/molcar_float_ring"
        desc["animations"]["float_ring"] = "animation.molcar.float_ring"
        condition = f"query.property('{PROPERTY}') == 1"
        desc["scripts"]["animate"] = [a for a in desc["scripts"]["animate"] if not isinstance(a, dict) or "float_ring" not in a]
        desc["scripts"]["animate"].append({"float_ring": condition + " && query.is_in_water"})
        controller = "controller.render.ichiyon.float_ring"
        desc["render_controllers"] = [c for c in desc["render_controllers"] if not isinstance(c, dict) or controller not in c]
        desc["render_controllers"].append({controller: condition})
        put(path, client)
    put(RP + "render_controllers/molcar_float_ring.json", {"format_version": "1.8.0", "render_controllers": {
        "controller.render.ichiyon.float_ring": {"geometry": "Geometry.float_ring", "materials": [{"*": "Material.default"}], "textures": ["Texture.float_ring"]}}})
    put(RP + "animations/molcar_float_ring.animation.json", {"format_version": "1.8.0", "animations": {
        "animation.molcar.float_ring": {"loop": True, "animation_length": 2, "bones": {
            "float_ring": {"rotation": ["math.sin(query.anim_time * 180) * 0.35", 0, 0]}}}}})
    files[RP + "textures/entity/molcar_float_ring.png"] = ring_texture()
    files[RP + "textures/items/molcar_float_ring.png"] = ring_texture(icon=True)
    atlas["texture_data"][ITEM] = {"textures": "textures/items/molcar_float_ring"}
    put(BP + "items/molcar_float_ring.json", {"format_version": "1.26.0", "minecraft:item": {
        "description": {"identifier": ITEM, "menu_category": {"category": "equipment"}},
        "components": {"minecraft:display_name": {"value": "item.ichiyon:molcar_float_ring.name"},
                       "minecraft:icon": ITEM, "minecraft:max_stack_size": 1, "minecraft:interact_button": "モルカーに装着"}}})
    put(BP + "recipes/molcar_float_ring.json", {"format_version": "1.20.10", "minecraft:recipe_shaped": {
        "description": {"identifier": ITEM}, "tags": ["crafting_table"], "pattern": [" WR", "W W", "RW "],
        "unlock": [{"item": "minecraft:white_wool"}],
        "key": {"W": {"item": "minecraft:white_wool"}, "R": {"item": "minecraft:red_wool"}},
        "result": {"item": ITEM, "count": 1}}})
