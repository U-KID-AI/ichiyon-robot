import { system, world } from "@minecraft/server";

const TAKETUMI_TYPE = "ichiyon:taketumi";
const HOME_TAG_PREFIX = "taketumi_home_";
const LEASHED_TAG = "taketumi_leashed";
const HOME_RESET_EVENT = "ichiyon:taketumi_reset_home";
const SPIDER_TYPES = ["minecraft:spider", "minecraft:cave_spider"];
const COMBAT_MEMORY_TICKS = 200;
const SCAN_INTERVAL_TICKS = 20;
const combatUntilByEntityId = new Map();
let currentTick = 0;

function isTaketumi(entity) {
  if (entity?.typeId !== TAKETUMI_TYPE) return false;

  try {
    if (typeof entity.isValid === "function") {
      return entity.isValid();
    }
    return entity.isValid !== false;
  } catch {
    return false;
  }
}

function getDimensionKey(entity) {
  return String(entity.dimension?.id ?? "minecraft:overworld").replace(/^minecraft:/, "");
}

function getLeashable(entity) {
  try {
    return entity.getComponent("minecraft:leashable");
  } catch {
    return undefined;
  }
}

function markCombat(entity) {
  if (!isTaketumi(entity)) return;
  combatUntilByEntityId.set(entity.id, currentTick + COMBAT_MEMORY_TICKS);
}

function isInCombat(entity) {
  const combatUntil = combatUntilByEntityId.get(entity.id) ?? 0;
  if (combatUntil <= currentTick) {
    combatUntilByEntityId.delete(entity.id);
  }
  return combatUntil > currentTick || hasNearbySpider(entity);
}

function hasNearbySpider(entity) {
  try {
    return SPIDER_TYPES.some((type) => entity.dimension.getEntities({
      type,
      location: entity.location,
      maxDistance: 16
    }).length > 0);
  } catch {
    return false;
  }
}

function hasHomeTag(entity) {
  return entity.getTags().some((value) => value.startsWith(HOME_TAG_PREFIX));
}

function clearHomeTags(entity) {
  for (const tag of entity.getTags()) {
    if (tag.startsWith(HOME_TAG_PREFIX)) {
      entity.removeTag(tag);
    }
  }
}

function setDynamicHome(entity, home) {
  try {
    entity.setDynamicProperty("taketumi.home_x", home.x);
    entity.setDynamicProperty("taketumi.home_y", home.y);
    entity.setDynamicProperty("taketumi.home_z", home.z);
    entity.setDynamicProperty("taketumi.home_dimension", home.dimension);
  } catch {
    // Entity tags below remain the persistent fallback on worlds where dynamic properties are unavailable.
  }
}

function getCurrentBlockHome(entity) {
  return {
    x: Math.floor(entity.location.x),
    y: Math.floor(entity.location.y),
    z: Math.floor(entity.location.z),
    dimension: getDimensionKey(entity)
  };
}

function mirrorHome(entity) {
  if (!isTaketumi(entity)) return;

  const home = getCurrentBlockHome(entity);

  clearHomeTags(entity);
  entity.addTag(`${HOME_TAG_PREFIX}${home.dimension}_${home.x}_${home.y}_${home.z}`);
  setDynamicHome(entity, home);
}

function resetHomeToCurrentLocation(entity, reason) {
  if (!isTaketumi(entity)) return;

  try {
    entity.triggerEvent(HOME_RESET_EVENT);
  } catch (error) {
    console.warn(`[TaketumiAI] home reset failed (${reason}): ${String(error)}`);
  }

  mirrorHome(entity);
}

function ensureHome(entity) {
  if (!hasHomeTag(entity)) {
    mirrorHome(entity);
  }
}

function updateLeashState(entity) {
  const leashable = getLeashable(entity);
  const isLeashed = Boolean(leashable?.isLeashed);
  const wasLeashed = entity.hasTag(LEASHED_TAG);

  if (isLeashed && !wasLeashed) {
    entity.addTag(LEASHED_TAG);
    return;
  }

  if (!isLeashed && wasLeashed) {
    entity.removeTag(LEASHED_TAG);
    if (!isInCombat(entity)) {
      resetHomeToCurrentLocation(entity, "leash_release");
    }
  }
}

function tickTaketumi(entity) {
  if (!isTaketumi(entity)) return;

  ensureHome(entity);
  if (hasNearbySpider(entity)) {
    markCombat(entity);
  }
  updateLeashState(entity);
}

world.afterEvents.entityHurt.subscribe((ev) => {
  markCombat(ev.hurtEntity);

  const attacker = ev.damageSource?.damagingEntity;
  if (isTaketumi(attacker)) {
    markCombat(attacker);
  }
});

world.beforeEvents.playerInteractWithEntity.subscribe((ev) => {
  if (!isTaketumi(ev.target)) return;
  if (ev.itemStack?.typeId !== "minecraft:lead") return;

  ev.cancel = true;

  const player = ev.player;
  const target = ev.target;

  system.run(() => {
    try {
      const leashable = getLeashable(target);
      if (!leashable) {
        player.sendMessage("§c[Taketumi] リード操作に対応していません");
        return;
      }

      if (leashable.isLeashed) {
        leashable.unleash();
        target.removeTag(LEASHED_TAG);
        if (!isInCombat(target)) {
          resetHomeToCurrentLocation(target, "manual_unleash");
        }
      } else {
        ensureHome(target);
        leashable.leashTo(player);
        target.addTag(LEASHED_TAG);
      }
    } catch (error) {
      player.sendMessage("§c[Taketumi] リード操作に失敗しました");
      console.warn(`[TaketumiAI] leash interaction failed: ${String(error)}`);
    }
  });
});

system.runInterval(() => {
  currentTick += SCAN_INTERVAL_TICKS;

  for (const dimensionId of ["overworld", "nether", "the_end"]) {
    let entities = [];
    try {
      entities = world.getDimension(dimensionId).getEntities({ type: TAKETUMI_TYPE });
    } catch {
      continue;
    }

    for (const entity of entities) {
      tickTaketumi(entity);
    }
  }
}, SCAN_INTERVAL_TICKS);
