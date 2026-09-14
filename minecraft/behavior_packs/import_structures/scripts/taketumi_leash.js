import { system, world } from "@minecraft/server";

const TAKETUMI_TYPE = "ichiyon:taketumi";
const HOME_TAG_PREFIX = "taketumi_home_";
const LEASHED_TAG = "taketumi_leashed";
const RETURNING_HOME_TAG = "taketumi_returning_home";
const HOME_RESET_EVENT = "ichiyon:taketumi_reset_home";
const START_RETURN_HOME_EVENT = "ichiyon:taketumi_start_return_home";
const STOP_RETURN_HOME_EVENT = "ichiyon:taketumi_stop_return_home";
const SPIDER_TYPES = ["minecraft:spider", "minecraft:cave_spider"];
const COMBAT_MEMORY_TICKS = 200;
const SCAN_INTERVAL_TICKS = 20;
const RETURN_HOME_COMPLETE_DISTANCE = 1.0;
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

function getMirroredHome(entity) {
  const tag = entity.getTags().find((value) => value.startsWith(HOME_TAG_PREFIX));
  if (!tag) return undefined;

  const parts = tag.slice(HOME_TAG_PREFIX.length).split("_");
  if (parts.length < 4) return undefined;

  const z = Number.parseInt(parts.pop(), 10);
  const y = Number.parseInt(parts.pop(), 10);
  const x = Number.parseInt(parts.pop(), 10);
  const dimension = parts.join("_");
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z) || !dimension) {
    return undefined;
  }

  return { x, y, z, dimension };
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

  stopReturnHome(entity);

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
    return isLeashed;
  }

  if (!isLeashed && wasLeashed) {
    entity.removeTag(LEASHED_TAG);
    if (!isInCombat(entity)) {
      resetHomeToCurrentLocation(entity, "leash_release");
    }
  }

  return isLeashed;
}

function horizontalDistanceToHome(entity, home) {
  if (home.dimension !== getDimensionKey(entity)) {
    return undefined;
  }
  const dx = entity.location.x - home.x;
  const dz = entity.location.z - home.z;
  return Math.hypot(dx, dz);
}

function startReturnHome(entity) {
  if (entity.hasTag(RETURNING_HOME_TAG)) return;

  try {
    entity.triggerEvent(START_RETURN_HOME_EVENT);
    entity.addTag(RETURNING_HOME_TAG);
  } catch (error) {
    console.warn(`[TaketumiAI] start return home failed: ${String(error)}`);
  }
}

function stopReturnHome(entity) {
  if (!entity.hasTag(RETURNING_HOME_TAG)) {
    return;
  }

  try {
    entity.triggerEvent(STOP_RETURN_HOME_EVENT);
  } catch (error) {
    console.warn(`[TaketumiAI] stop return home failed: ${String(error)}`);
  }
  entity.removeTag(RETURNING_HOME_TAG);
}

function updateReturnHomeState(entity, isLeashed, combat) {
  if (isLeashed || combat) {
    stopReturnHome(entity);
    return;
  }

  const home = getMirroredHome(entity);
  if (!home) {
    stopReturnHome(entity);
    return;
  }

  const distance = horizontalDistanceToHome(entity, home);
  if (distance === undefined || distance <= RETURN_HOME_COMPLETE_DISTANCE) {
    stopReturnHome(entity);
    return;
  }

  startReturnHome(entity);
}

function tickTaketumi(entity) {
  if (!isTaketumi(entity)) return;

  ensureHome(entity);
  if (hasNearbySpider(entity)) {
    markCombat(entity);
  }
  const combat = isInCombat(entity);
  const isLeashed = updateLeashState(entity);
  updateReturnHomeState(entity, isLeashed, combat);
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
