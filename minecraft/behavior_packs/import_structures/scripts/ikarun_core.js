import { isLiving, nearbyLiving, ownerOf, findOwner, claimOwner, releaseOwner } from "./companion_owner.js";
import { healFriendly } from "./friendly_healing.js";

export const IKARUN = "ichiyon:ikarun";
export const IKARUN_OWNER = "ikarun:owner";
export const HEAL = Object.freeze({ range: 6, amount: 4, intervalTicks: 100, scanTicks: 20, healersPerScan: 4, targetsPerHealer: 16 });
export const HOME_RADIUS = 4;
const HOME_X = "ikarun:home_x";
const HOME_Y = "ikarun:home_y";
const HOME_Z = "ikarun:home_z";
const HOME_DIM = "ikarun:home_dim";
const LIGHT_BLOCK = /^minecraft:light_block(?:_\d+)?$/;
const MANNEQUIN_EGG = "ichiyon:avatar_selector";
const placementInstalled = new WeakSet();

export function horizontalDistance(a, b) {
  return Math.hypot(a.x - b.x, a.z - b.z);
}

export function readHome(entity) {
  const x = entity.getDynamicProperty(HOME_X);
  const y = entity.getDynamicProperty(HOME_Y);
  const z = entity.getDynamicProperty(HOME_Z);
  if (![x, y, z].every(Number.isFinite)) return undefined;
  return { x, y, z, dimension: entity.getDynamicProperty(HOME_DIM) };
}

export function writeHome(entity, location) {
  entity.setDynamicProperty(HOME_X, location.x);
  entity.setDynamicProperty(HOME_Y, location.y);
  entity.setDynamicProperty(HOME_Z, location.z);
  if (entity.dimension?.id) entity.setDynamicProperty(HOME_DIM, entity.dimension.id);
  return readHome(entity);
}

export function ensureHome(entity) {
  return readHome(entity) ?? writeHome(entity, entity.location);
}

// A proposed hover stays when it is inside the home radius. A point outside is
// not taken; an entity already outside steps at most one hover (2 blocks) back.
export function roamStep(location, home, proposed, radius = HOME_RADIUS) {
  const next = proposed ?? location;
  if (horizontalDistance(next, home) <= radius) return { location: { x: next.x, y: next.y, z: next.z }, returning: false };
  const dx = home.x - location.x;
  const dz = home.z - location.z;
  const dist = Math.hypot(dx, dz);
  if (dist <= radius) return { location: { x: location.x, y: location.y, z: location.z }, returning: false };
  const travel = Math.min(2, dist - radius);
  return {
    location: { x: location.x + dx / dist * travel, y: location.y, z: location.z + dz / dist * travel },
    returning: true,
  };
}

function volumeOpen(dimension, pos) {
  return [0, 1].every((dy) => {
    let block;
    try { block = dimension.getBlock({ x: pos.x, y: pos.y + dy, z: pos.z }); } catch { return false; }
    return block?.isAir === true || LIGHT_BLOCK.test(block?.typeId ?? "");
  });
}

export function safeHomeReturn(entity, home) {
  const spots = [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1], [2, 0], [-2, 0], [0, 2], [0, -2]];
  for (const [dx, dz] of spots) {
    const pos = { x: Math.floor(home.x) + dx + .5, y: home.y, z: Math.floor(home.z) + dz + .5 };
    try {
      if (!volumeOpen(entity.dimension, pos)) continue;
      if (entity.tryTeleport(pos, { checkForBlocks: true })) { entity.clearVelocity?.(); return true; }
    } catch { /* Unloaded cell: try the next spot, then the next scan. */ }
  }
  return false;
}

export function cellKey(location) {
  return `${Math.floor(location.x)},${Math.floor(location.y)},${Math.floor(location.z)}`;
}

export function rememberMannequinVolume(watched, location, until) {
  watched.set(cellKey(location), until);
  watched.set(cellKey({ x: location.x, y: location.y + 1, z: location.z }), until);
}

export function mannequinReadBlock(block, key, watched, now) {
  const until = watched.get(key);
  if (!block || until === undefined || now > until) return block;
  if (!LIGHT_BLOCK.test(block.typeId ?? "")) return block;
  return { ...block, isAir: true };
}

function userBusy(value) {
  const reason = value?.cancelationReason ?? value?.cancellationReason ?? value?.message ?? value;
  return /userbusy/i.test(String(reason ?? ""));
}

export async function showMannequinForm(show, wait, player) {
  let result;
  for (let attempt = 0; attempt < 3; attempt++) {
    try { result = await show(player); }
    catch (error) {
      if (attempt < 2 && userBusy(error)) { await wait(); continue; }
      throw error;
    }
    if (!(result?.canceled && userBusy(result) && attempt < 2)) return result;
    await wait();
  }
  return result;
}

export function installMannequinPlacement({ world, system, ActionFormData }) {
  if (!world || placementInstalled.has(world)) return;
  placementInstalled.add(world);
  const watched = new Map();
  const opened = new Map();
  const watch = (location) => rememberMannequinVolume(watched, location, (system.currentTick ?? 0) + 20 * 180);
  const openCell = (dimension, at) => {
    let block;
    try { block = dimension.getBlock(at); } catch { return; }
    if (!block || !LIGHT_BLOCK.test(block.typeId ?? "") || typeof block.setType !== "function") return;
    const key = cellKey(at);
    if (opened.has(key)) return;
    const typeId = block.typeId;
    const saved = { dimension, x: Math.floor(at.x), y: Math.floor(at.y), z: Math.floor(at.z), typeId };
    try { block.setType("minecraft:air"); } catch { return; }
    opened.set(key, saved);
  };
  const restoreKey = (key) => {
    const saved = opened.get(key);
    if (!saved) return;
    try {
      const block = saved.dimension.getBlock(saved);
      if (block?.typeId === "minecraft:air") block.setType(saved.typeId);
      opened.delete(key);
    } catch { /* The chunk is unloading; the expiry pass tries again. */ }
  };
  try {
    const proto = world.getDimension("overworld")?.constructor?.prototype;
    const original = proto?.getBlock;
    if (typeof original === "function" && !original.__mannequinLight) {
      function getBlock(location) {
        const block = original.call(this, location);
        if (!location) return block;
        const read = mannequinReadBlock(block, cellKey(location), watched, system.currentTick ?? 0);
        if (read === block) return block;
        try {
          return new Proxy(block, { get(target, key) {
            if (key === "isAir") return true;
            const value = target[key];
            return typeof value === "function" ? value.bind(target) : value;
          } });
        } catch { return read; }
      }
      getBlock.__mannequinLight = true;
      proto.getBlock = getBlock;
    }
  } catch { /* The placement check still uses the block's own air flag. */ }
  const show = ActionFormData?.prototype?.show;
  if (typeof show === "function" && !show.__mannequinRetry) {
    async function retryingShow(player) {
      return showMannequinForm(
        (value) => show.call(this, value),
        () => new Promise((resolve) => system.runTimeout(resolve, 10)),
        player,
      );
    }
    retryingShow.__mannequinRetry = true;
    ActionFormData.prototype.show = retryingShow;
  }
  const note = (event) => {
    if (event.itemStack?.typeId !== MANNEQUIN_EGG || event.isFirstEvent === false) return;
    const player = event.player || event.source;
    const block = event.block || player?.getBlockFromViewDirection?.({ maxDistance: 6 })?.block;
    if (!block?.location) return;
    const location = { x: block.location.x + .5, y: block.location.y + 1, z: block.location.z + .5 };
    watch(location);
    const open = () => {
      const dimension = player?.dimension;
      if (!dimension) return;
      openCell(dimension, location);
      openCell(dimension, { x: location.x, y: location.y + 1, z: location.z });
    };
    if (typeof system.run === "function") system.run(open);
    else open();
  };
  world.beforeEvents?.playerInteractWithBlock?.subscribe(note);
  world.beforeEvents?.itemUse?.subscribe(note);
  world.afterEvents?.entitySpawn?.subscribe((event) => {
    if (event.entity?.typeId !== "ichiyon:avatar") return;
    const at = event.entity.location;
    if (!at) return;
    const feet = cellKey(at);
    const head = cellKey({ x: at.x, y: at.y + 1, z: at.z });
    const putBack = () => { restoreKey(feet); restoreKey(head); watched.delete(feet); watched.delete(head); };
    if (typeof system.run === "function") system.run(putBack);
    else putBack();
  });
  system.runInterval?.(() => {
    const now = system.currentTick ?? 0;
    for (const [key, until] of watched) if (until < now) { restoreKey(key); watched.delete(key); }
  }, 200);
}

export function safeHoverReturn(entity, player) {
  if (!isLiving(player) || player.dimension.id !== entity.dimension.id || entity.getComponent("minecraft:leashable")?.isLeashed) return false;
  if (Math.hypot(player.location.x - entity.location.x, player.location.y - entity.location.y, player.location.z - entity.location.z) < 32) return false;
  // Only already-loaded, clear cells around the owner. Native collision checking is still required.
  for (const [dx, dz] of [[2, 0], [-2, 0], [0, 2], [0, -2]]) {
    const pos = { x: Math.floor(player.location.x) + dx + .5, y: Math.floor(player.location.y) + 1, z: Math.floor(player.location.z) + dz + .5 };
    try {
      if (![0, 1].every((dy) => entity.dimension.getBlock({ ...pos, y: pos.y + dy })?.isAir === true)) continue;
      if (entity.tryTeleport(pos, { checkForBlocks: true, dimension: player.dimension })) { entity.clearVelocity(); return true; }
    } catch { /* Unloaded/invalid destination: keep the existing position and retry next scan. */ }
  }
  return false;
}

export function createIkarun({ world, system, report = console.warn }) {
  const debounce = new Map(), active = new Map(), nextHeal = new Map(), targetCooldown = new Map(), glowUntil = new Map(), stuck = new Map();
  const homeBound = new Set();
  let cursor = 0, lastError = -200;
  const error = (e) => {
    if (system.currentTick - lastError < 200) return;
    lastError = system.currentTick;
    report(`[Ikarun] ${String(e).slice(0, 250)}`);
  };
  const emptyHand = (player) => !player.getComponent("minecraft:inventory")?.container?.getItem(player.selectedSlotIndex);
  function toggle(player, entity) {
    if (!nearbyLiving(player, entity) || !emptyHand(player)) return false;
    const owner = ownerOf(entity, IKARUN_OWNER);
    if (owner && owner !== player.id) { player.sendMessage("このイカルンは別のプレイヤーを追従中です。"); return false; }
    if (owner) {
      writeHome(entity, entity.location);
      homeBound.delete(entity.id);
      stuck.delete(entity.id);
      entity.triggerEvent("ichiyon:ikarun_stop");
      releaseOwner(entity, IKARUN_OWNER, player.id);
      active.delete(entity.id);
      player.sendMessage("イカルンの追従を解除しました。周囲の回復は続けます。");
      return true;
    }
    if (!claimOwner(entity, IKARUN_OWNER, player)) return false;
    homeBound.delete(entity.id);
    entity.triggerEvent("ichiyon:ikarun_start");
    active.delete(entity.id);
    system.run(() => {
      try {
        if (!isLiving(entity) || ownerOf(entity, IKARUN_OWNER) !== player.id || !isLiving(player)) return;
        if (!entity.getComponent("minecraft:tameable")?.tame(player)) {
          player.sendMessage("追従を開始できませんでした。所有者は保持しています。再操作で解除し、もう一度お試しください。"); return;
        }
        player.sendMessage("イカルンがあなたを浮遊して追従します。素手でもう一度操作すると解除します。");
      } catch (e) { error(e); }
    });
    return true;
  }
  function interact(event) {
    if (event.target?.typeId !== IKARUN || !emptyHand(event.player)) return;
    event.cancel = true;
    const id = event.target.id, tick = system.currentTick;
    if ((debounce.get(id) ?? -100) + 10 > tick) return;
    debounce.set(id, tick);
    system.run(() => { try { toggle(event.player, event.target); } catch (e) { error(e); } });
  }
  function noteSpawn(entity) {
    if (entity?.typeId !== IKARUN || !isLiving(entity)) return;
    ensureHome(entity);
  }
  function keepNearHome(entity) {
    const home = ensureHome(entity);
    if (!home || (home.dimension && entity.dimension?.id && home.dimension !== entity.dimension.id)) return;
    const dist = horizontalDistance(entity.location, home);
    if (dist <= HOME_RADIUS) {
      stuck.delete(entity.id);
      if (!homeBound.has(entity.id)) {
        try { entity.triggerEvent("ichiyon:ikarun_home_bind"); } catch (e) { error(e); }
        homeBound.add(entity.id);
      }
      return;
    }
    const step = roamStep(entity.location, home, entity.location);
    let moved = false;
    if (typeof entity.tryTeleport === "function") {
      try {
        moved = entity.tryTeleport(step.location, { checkForBlocks: true }) === true;
        if (moved) entity.clearVelocity?.();
      } catch { moved = false; }
    }
    if (moved) { stuck.delete(entity.id); return; }
    const prev = stuck.get(entity.id) ?? { fails: 0 };
    const fails = prev.fails + 1;
    if (fails >= 10) {
      safeHomeReturn(entity, home);
      stuck.delete(entity.id);
      return;
    }
    stuck.set(entity.id, { fails });
  }
  function maintain(entity, players) {
    const owner = ownerOf(entity, IKARUN_OWNER), player = owner ? findOwner(players, entity, IKARUN_OWNER) : undefined;
    let following = false;
    if (owner) {
      const tame = entity.getComponent("minecraft:tameable");
      if (!tame) { entity.triggerEvent("ichiyon:ikarun_start"); active.delete(entity.id); }
      if (isLiving(player) && player.dimension.id === entity.dimension.id) {
        if (tame && tame.tamedToPlayerId !== owner) tame.tame(player);
        if (active.get(entity.id) !== true) entity.triggerEvent("ichiyon:ikarun_resume");
        active.set(entity.id, true);
        following = true;
        safeHoverReturn(entity, player);
      } else {
        if (active.get(entity.id) !== false) entity.triggerEvent("ichiyon:ikarun_pause");
        active.set(entity.id, false);
      }
    }
    if (following) homeBound.delete(entity.id);
    else keepNearHome(entity);
    // A restarted server clears stale one-shot animation state without affecting the stored owner.
    if ((glowUntil.get(entity.id) ?? 0) <= system.currentTick && entity.getProperty("ichiyon:healing")) entity.setProperty("ichiyon:healing", false);
  }
  function heal(entity) {
    if ((nextHeal.get(entity.id) ?? 0) > system.currentTick) return 0;
    nextHeal.set(entity.id, system.currentTick + HEAL.intervalTicks);
    let count = 0;
    for (const target of entity.dimension.getEntities({ location: entity.location, maxDistance: HEAL.range, closest: HEAL.targetsPerHealer })) {
      if ((targetCooldown.get(target.id) ?? 0) > system.currentTick) continue;
      if (!healFriendly(target, HEAL.amount)) continue;
      targetCooldown.set(target.id, system.currentTick + HEAL.intervalTicks);
      count++;
      try { target.dimension.spawnParticle("minecraft:heart_particle", { ...target.location, y: target.location.y + 1 }); } catch (e) { error(e); }
    }
    if (count) {
      entity.setProperty("ichiyon:healing", true);
      glowUntil.set(entity.id, system.currentTick + 20);
      try { entity.dimension.playSound("random.orb", entity.location, { volume: .35, pitch: 1.5 }); } catch (e) { error(e); }
    }
    return count;
  }
  function scan() {
    const entities = [], players = world.getAllPlayers();
    for (const id of ["overworld", "nether", "the_end"]) {
      try { entities.push(...world.getDimension(id).getEntities({ type: IKARUN }).filter(isLiving)); } catch (e) { error(e); }
    }
    for (const entity of entities) { try { maintain(entity, players); } catch (e) { error(e); } }
    for (let n = 0; n < Math.min(entities.length, HEAL.healersPerScan); n++) {
      const entity = entities[(cursor + n) % entities.length];
      try { heal(entity); } catch (e) { error(e); }
    }
    cursor = entities.length ? (cursor + HEAL.healersPerScan) % entities.length : 0;
    const seen = new Set(entities.map((e) => e.id));
    for (const map of [active, nextHeal, glowUntil, debounce, stuck]) for (const id of map.keys()) if (!seen.has(id)) map.delete(id);
    for (const id of homeBound) if (!seen.has(id)) homeBound.delete(id);
    for (const [id, until] of targetCooldown) if (until <= system.currentTick) targetCooldown.delete(id);
  }
  return { interact, toggle, scan, heal, maintain, noteSpawn };
}
