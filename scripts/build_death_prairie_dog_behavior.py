"""Generate a non-combat native follower with per-player tag matching."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp/entities/death_prairie_dog.json"
TARGET_BITS = 16
PREFIX = "ichiyon:prairie_"


def behavior():
    # Binary tags avoid thousands of duplicate follow goals while matching exactly
    # one online player per mob. No native attack target or writable target API.
    match = []
    properties = {
        PREFIX + "state": {"type": "enum", "values": ["wander", "transition", "chase", "flee"],
                           "default": "wander", "client_sync": True},
    }
    for bit in range(TARGET_BITS):
        prop = PREFIX + f"bit_{bit}"
        properties[prop] = {"type": "bool", "default": False}
        match.append({"any_of": [
            {"all_of": [{"test": "bool_property", "subject": "self", "domain": prop, "value": True},
                        {"test": "has_tag", "subject": "other", "value": prop}]},
            {"all_of": [{"test": "bool_property", "subject": "self", "domain": prop, "value": False},
                        {"test": "has_tag", "subject": "other", "operator": "not", "value": prop}]},
        ]})
    components = {
        "minecraft:type_family": {"family": ["death_prairie_dog", "mob"]},
        "minecraft:health": {"value": 20, "max": 20},
        "minecraft:collision_box": {"width": 0.6, "height": 1},
        "minecraft:nameable": {}, "minecraft:persistent": {},
        "minecraft:movement": {"value": 0.22},
        "minecraft:movement.basic": {"max_turn": 30},
        "minecraft:navigation.walk": {"avoid_water": True, "avoid_damage_blocks": True, "can_jump": True, "can_float": True},
        "minecraft:jump.static": {},
        "minecraft:physics": {"has_gravity": True, "has_collision": True},
        "minecraft:pushable_by_entity": {}, "minecraft:pushable_by_block": {},
        "minecraft:behavior.float": {"priority": 0},
        "minecraft:behavior.random_look_around": {"priority": 8},
        "minecraft:behavior.avoid_mob_type": {
            "priority": 1, "avoid_target_xz": 16, "avoid_target_y": 7,
            "entity_types": [{"filters": {"all_of": [
                {"test": "is_family", "subject": "other", "value": "player"},
                {"test": "is_sleeping", "subject": "other", "value": True},
            ]}, "max_dist": 8, "max_flee": 10, "walk_speed_multiplier": 2.5, "sprint_speed_multiplier": 2.5}],
        },
    }
    wander, chase = PREFIX + "wander", PREFIX + "chase"
    groups = {
        wander: {"minecraft:behavior.random_stroll": {
            "priority": 6, "speed_multiplier": 1, "interval": 40, "xz_dist": 6, "y_dist": 1}},
        chase: {"minecraft:behavior.follow_mob": {
            "priority": 3, "speed_multiplier": 2.5, "search_range": 32, "stop_distance": 1.5,
            "filters": {"all_of": [
                {"test": "is_family", "subject": "other", "value": "player"},
                {"test": "is_sleeping", "subject": "other", "value": False},
                {"test": "has_tag", "subject": "other", "value": PREFIX + "candidate"},
                *match,
            ]},
        }},
    }
    events = {"minecraft:entity_spawned": {"add": {"component_groups": [wander]}}}
    for state in ("wander", "transition", "chase", "flee"):
        event = {"remove": {"component_groups": [wander, chase]},
                 "set_property": {PREFIX + "state": state}}
        if state in ("wander", "chase"):
            event["add"] = {"component_groups": [PREFIX + state]}
        events[PREFIX + state] = event
    return {"format_version": "1.26.30", "minecraft:entity": {
        "description": {"identifier": "ichiyon:death_prairie_dog", "is_spawnable": True,
                        "is_summonable": True, "is_experimental": False, "properties": properties},
        "component_groups": groups, "components": components, "events": events,
    }}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = (json.dumps(behavior(), indent=2) + "\n").encode()
    if args.check:
        assert OUTPUT.read_bytes().replace(b"\r\n", b"\n") == content, "Behavior export differs"
    else:
        OUTPUT.write_bytes(content)
    print(f"OK {OUTPUT.relative_to(ROOT)}")
