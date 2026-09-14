from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BP_ENTITY = ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp/entities/taketumi.json"
BP_EGG = ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp/items/taketumi_spawn_egg.json"
RP_CLIENT = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp/entity/taketumi.entity.json"
RP_ANIMATIONS = ROOT / "minecraft/resource_packs/ichiyon_avatar_rp/animations/taketumi.animation.json"
LEASH_SCRIPT = ROOT / "minecraft/behavior_packs/import_structures/scripts/taketumi_leash.js"
SPAWN_RULES = ROOT / "minecraft/behavior_packs/ichiyon_avatar_bp/spawn_rules"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> None:
    entity_doc = load_json(BP_ENTITY)
    entity = entity_doc["minecraft:entity"]
    description = entity["description"]
    components = entity["components"]
    groups = entity.get("component_groups", {})
    events = entity.get("events", {})

    assert_true(description["identifier"] == "ichiyon:taketumi", "taketumi identifier changed")
    assert_true(description.get("is_spawnable") is True, "taketumi spawn egg compatibility changed")
    assert_true(description.get("is_summonable") is True, "taketumi summon compatibility changed")
    assert_true("runtime_identifier" not in description, "taketumi must not inherit vanilla runtime behavior")

    health = components.get("minecraft:health", {})
    assert_true(health.get("value") == 500 and health.get("max") == 500, "taketumi HP must be 500/500")

    attack = components.get("minecraft:attack", {})
    assert_true(attack.get("damage") == 30, "taketumi melee damage must be 30")

    assert_true("minecraft:behavior.random_stroll" not in components, "taketumi must not randomly stroll")
    assert_true("minecraft:behavior.random_swim" not in components, "taketumi must not randomly swim")
    assert_true("minecraft:behavior.panic" not in components, "taketumi must not panic-walk by default")
    assert_true("minecraft:behavior.follow_owner" not in components, "taketumi must not follow players by default")
    assert_true("minecraft:behavior.hurt_by_target" in components, "taketumi must retaliate when attacked")
    assert_true(components.get("minecraft:behavior.hurt_by_target", {}).get("priority") == 1,
                "hurt_by_target priority must be 1")
    assert_true("minecraft:leashable" in components, "taketumi must remain leashable without cow runtime")

    target = components.get("minecraft:behavior.nearest_attackable_target", {})
    assert_true(target.get("within_radius") == 16, "taketumi proactive target radius must be 16")
    target_types = target.get("entity_types", [])
    target_values = {
        entry.get("filters", {}).get("value")
        for entry in target_types
        if isinstance(entry.get("filters"), dict)
    }
    assert_true(target_values == {"spider", "cavespider"}, "taketumi must proactively target only spiders")
    assert_true("player" not in target_values, "taketumi must not proactively target players")

    assert_true("minecraft:behavior.melee_box_attack" in components, "taketumi needs melee attack behavior")
    assert_true("minecraft:behavior.move_towards_home_restriction" not in components,
                "home return behavior must not run during normal idle")
    assert_true(components.get("minecraft:behavior.nearest_attackable_target", {}).get("priority") == 2,
                "nearest_attackable_target priority must be 2")
    assert_true(components.get("minecraft:behavior.melee_box_attack", {}).get("priority") == 3,
                "melee attack priority must be 3")

    home_group = groups.get("ichiyon:taketumi_home", {})
    home = home_group.get("minecraft:home", {})
    assert_true(home.get("restriction_radius") == 0, "taketumi home radius must force exact home return")
    assert_true(home.get("restriction_type") == "none", "taketumi home restriction must not block combat pursuit")

    returning_group = groups.get("ichiyon:taketumi_returning_home", {})
    returning_home = returning_group.get("minecraft:behavior.move_towards_home_restriction", {})
    assert_true(returning_home.get("priority") == 4, "home return priority must be 4")
    assert_true(returning_home.get("speed_multiplier") == 0.8, "home return speed multiplier changed")

    reset_event = events.get("ichiyon:taketumi_reset_home", {})
    reset_text = json.dumps(reset_event, ensure_ascii=False)
    assert_true("ichiyon:taketumi_home" in reset_text, "home reset event must reinitialize home group")
    assert_true("ichiyon:taketumi_start_return_home" in events, "start return home event is missing")
    assert_true("ichiyon:taketumi_stop_return_home" in events, "stop return home event is missing")
    assert_true("ichiyon:taketumi_returning_home" in json.dumps(events["ichiyon:taketumi_start_return_home"], ensure_ascii=False),
                "start return event must add returning_home group")
    assert_true("ichiyon:taketumi_returning_home" in json.dumps(events["ichiyon:taketumi_stop_return_home"], ensure_ascii=False),
                "stop return event must remove returning_home group")

    if SPAWN_RULES.exists():
        spawn_rule_text = "\n".join(path.read_text(encoding="utf-8") for path in SPAWN_RULES.glob("*.json"))
        assert_true("ichiyon:taketumi" not in spawn_rule_text, "taketumi must not have natural spawn rules")

    egg = load_json(BP_EGG)
    egg_components = egg["minecraft:item"]["components"]
    placer = egg_components.get("minecraft:entity_placer", {})
    assert_true(placer.get("entity") == "ichiyon:taketumi", "taketumi egg must still place taketumi")

    client = load_json(RP_CLIENT)
    animations = client["minecraft:client_entity"]["description"].get("animations", {})
    for name in ("idle", "walk", "attack", "hurt"):
        assert_true(animations.get(name) == f"animation.taketumi.{name}", f"RP animation {name} is not wired")

    animation_doc = load_json(RP_ANIMATIONS)
    defined_animations = animation_doc.get("animations", {})
    for name in ("idle", "walk", "attack", "hurt"):
        assert_true(f"animation.taketumi.{name}" in defined_animations, f"RP animation {name} is missing")

    leash_script = LEASH_SCRIPT.read_text(encoding="utf-8")
    required_snippets = [
        "taketumi.home_x",
        "taketumi.home_y",
        "taketumi.home_z",
        "taketumi.home_dimension",
        "taketumi_home_",
        "const combatUntilByEntityId = new Map();",
        "combatUntilByEntityId.set(entity.id",
        "combatUntilByEntityId.get(entity.id)",
        "combatUntilByEntityId.delete(entity.id)",
        "mirrorHome",
        "resetHomeToCurrentLocation",
        "manual_unleash",
        "leash_release",
        "HOME_RESET_EVENT",
        "START_RETURN_HOME_EVENT",
        "STOP_RETURN_HOME_EVENT",
        "RETURNING_HOME_TAG",
        "RETURN_HOME_COMPLETE_DISTANCE",
        "hasNearbySpider",
        "entityHurt",
        "getMirroredHome",
        "horizontalDistanceToHome",
        "startReturnHome",
        "stopReturnHome",
        "updateReturnHomeState",
    ]
    for snippet in required_snippets:
        assert_true(snippet in leash_script, f"leash/home script missing {snippet}")

    assert_true("taketumi_combat_until_" not in leash_script,
                "combat memory must not use persistent entity tags")

    assert_true(re.search(r"if \(!isInCombat\(.*?\)\)\s*{\s*resetHomeToCurrentLocation", leash_script, re.S) is not None,
                "leash release must not update home during combat")
    assert_true("if (isLeashed || combat)" in leash_script and "stopReturnHome(entity);" in leash_script,
                "leash/combat state must disable home return")
    assert_true("startReturnHome(entity);" in leash_script,
                "combat end / away from home must start return home")
    assert_true("distance <= RETURN_HOME_COMPLETE_DISTANCE" in leash_script,
                "home arrival must stop return home")
    assert_true("function ensureHome" in leash_script and "mirrorHome(entity);" in leash_script,
                "initial/load handling must mirror home without resetting minecraft:home")
    ensure_home_match = re.search(r"function ensureHome\(entity\).*?}\n}", leash_script, re.S)
    assert_true(ensure_home_match is not None, "ensureHome function is missing")
    assert_true("triggerEvent" not in ensure_home_match.group(0),
                "ensureHome must not reset minecraft:home during load/initial mirror")
    assert_true("components.join" not in leash_script, "debug component dump must not remain")

    print("taketumi AI checks OK")


if __name__ == "__main__":
    main()
