export const PRAIRIE = "ichiyon:death_prairie_dog";
export const STATE = "ichiyon:prairie_state";
export const RANGE = 32;
export const SLEEP_CLEARANCE = 8;
export const TRANSITION_TICKS = 10;
export const RELEASE_TICKS = 20;
export const TARGET_BITS = 16;
export const TAG_PREFIX = "ichiyon:prairie_";

export function isPrairieNight(time) {
  return time >= 13000 && time <= 23000;
}

function distanceSquared(a, b) {
  return (a.x-b.x)**2 + (a.y-b.y)**2 + (a.z-b.z)**2;
}

function playerSnapshot(player) {
  try {
    if (!player.isValid || player.getComponent("minecraft:health")?.currentValue <= 0) return;
    return { id: player.id, entity: player, dimension: player.dimension.id, location: player.location,
      sleeping: player.isSleeping, mode: player.getGameMode() };
  } catch { return; }
}

export function nearestAwake(location, players) {
  return players.filter(p => !p.sleeping && p.mode !== "Spectator" &&
      distanceSquared(location, p.location) <= RANGE**2)
    .sort((a, b) => distanceSquared(location, a.location) - distanceSquared(location, b.location) ||
      a.id.localeCompare(b.id))[0];
}

export function createDeathPrairieDog({ world, system, report = console.warn }) {
  const tracked = new Map();
  const playerSlots = new Map();
  let lastReport = -1200;
  function warn(error) {
    if (system.currentTick - lastReport >= 1200) {
      report(`[DeathPrairieDog] ${String(error).slice(0, 240)}`);
      lastReport = system.currentTick;
    }
  }
  function change(entry, state, force = false) {
    if (!force && entry.state === state) return;
    entry.entity.triggerEvent(`ichiyon:prairie_${state}`);
    entry.state = state;
    entry.since = system.currentTick;
    if (state === "wander" || state === "flee") entry.targetId = undefined;
  }
  function clearTags(player) {
    for (const tag of player.getTags()) {
      if (tag === TAG_PREFIX + "candidate" || /^ichiyon:prairie_bit_\d+$/.test(tag)) player.removeTag(tag);
    }
  }
  function syncPlayers(players) {
    const present = new Set(players.map(p => p.id));
    for (const id of playerSlots.keys()) if (!present.has(id)) playerSlots.delete(id);
    const used = new Set(playerSlots.values());
    for (const player of players) {
      if (playerSlots.has(player.id)) continue;
      // Clear persisted tags before slot reuse on reconnect or Script restart.
      clearTags(player.entity);
      let slot = 1;
      while (used.has(slot)) slot++;
      if (slot >= 2**TARGET_BITS) throw Error("Prairie player matching capacity exceeded");
      for (let bit = 0; bit < TARGET_BITS; bit++) {
        if (slot & (1 << bit)) player.entity.addTag(TAG_PREFIX + `bit_${bit}`);
      }
      player.entity.addTag(TAG_PREFIX + "candidate");
      playerSlots.set(player.id, slot);
      used.add(slot);
    }
  }
  function select(entry, player) {
    if (entry.targetId === player.id) return;
    const slot = playerSlots.get(player.id);
    for (let bit = 0; bit < TARGET_BITS; bit++) {
      entry.entity.setProperty(TAG_PREFIX + `bit_${bit}`, Boolean(slot & (1 << bit)));
    }
    entry.targetId = player.id;
    if (entry.state === "chase") change(entry, "chase", true);
  }
  function recover(entity) {
    if (entity.typeId !== PRAIRIE || !entity.isValid) return;
    const entry = { entity, state: undefined, since: system.currentTick, targetId: undefined };
    // Reset saved component groups before reevaluating current time and players.
    change(entry, "wander", true);
    tracked.set(entity.id, entry);
  }
  function scan() {
    for (const id of ["overworld", "nether", "the_end"]) {
      try {
        for (const entity of world.getDimension(id).getEntities({ type: PRAIRIE })) {
          if (!tracked.has(entity.id)) recover(entity);
        }
      } catch (error) { warn(error); }
    }
  }
  function tick() {
    const night = isPrairieNight(world.getTimeOfDay());
    const players = world.getAllPlayers().map(playerSnapshot).filter(Boolean);
    syncPlayers(players);
    for (const [id, entry] of tracked) {
      const mob = entry.entity;
      try {
        if (!mob.isValid || mob.getComponent("minecraft:health")?.currentValue <= 0) {
          tracked.delete(id);
          continue;
        }
        const local = players.filter(p => p.dimension === mob.dimension.id);
        if (!night) {
          change(entry, "wander");
          continue;
        }
        const sleepersNear = local.some(p => p.sleeping &&
          distanceSquared(mob.location, p.location) <= SLEEP_CLEARANCE**2);
        const previousAsleep = local.some(p => p.id === entry.targetId && p.sleeping);
        if (sleepersNear || previousAsleep) {
          change(entry, "flee");
          continue;
        }
        // No timeout escape from a blocked path: sleepers must actually be clear.
        if (entry.state === "flee" && system.currentTick-entry.since < RELEASE_TICKS) continue;
        const nearest = nearestAwake(mob.location, local);
        if (!nearest) {
          change(entry, "wander");
          continue;
        }
        if (entry.state !== "transition" && entry.state !== "chase") change(entry, "transition");
        else if (entry.state === "transition" && system.currentTick-entry.since >= TRANSITION_TICKS) {
          change(entry, "chase");
        }
        // Native follow filters match only this player's tags, including Creative.
        select(entry, nearest);
      } catch (error) {
        warn(error);
        try { change(entry, "wander", true); } catch { /* Unloaded: recover on entityLoad. */ }
        tracked.delete(id);
      }
    }
  }
  function forget(id) { tracked.delete(id); }
  return { tick, scan, recover, forget, tracked, playerSlots };
}
