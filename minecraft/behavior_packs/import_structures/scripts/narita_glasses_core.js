// Same equipment/effect lease pattern as Mokuro, with outside-effect detection.
export const NARITA_GLASSES = "ichiyon:narita_glasses";
export const GLASSES_LEASE = "ichiyon:narita_glasses_lease";
export const CHECK_TICKS = 5;
// addEffect duration is ticks (20 ticks = 1 second). A 15-second effect never
// leaves the client's flash. Hold 180 seconds and renew once 60 seconds remain.
export const EFFECT_TICKS = 180 * 20;
export const RENEW_AT_TICKS = 60 * 20;
const EFFECT = "night_vision";
// BDS 2.11 beta reports the display name "Night Vision" in before.effectAdd,
// while getEffect/addEffect use the identifier. Accept both without changing
// the native effect or assigning ownership to a different effect's event.
const nightVision = id => typeof id === "string" &&
  id.trim().replace(/^minecraft:/i, "").toLowerCase().replace(/\s+/g, "_") === EFFECT;
const valid = player => player?.isValid !== false;

export function createNaritaGlasses({ world, system, report = console.warn }) {
  const states = new Map(), writes = new Map(), revoked = new Set();
  let lastError = -200;
  function log(error) {
    if (system.currentTick - lastError >= 200) {
      lastError = system.currentTick;
      report(`[NaritaGlasses] ${String(error).slice(0, 250)}`);
    }
  }
  function clear(player) {
    states.delete(player.id);
    if (player.getDynamicProperty(GLASSES_LEASE) !== undefined) player.setDynamicProperty(GLASSES_LEASE, undefined);
  }
  function snapshot(player, state, effect) {
    player.setDynamicProperty(GLASSES_LEASE, JSON.stringify({ schema: 1, amplifier: 0,
      remaining: effect.duration, source: NARITA_GLASSES }));
    state.expiresTick = system.currentTick + effect.duration;
  }
  function owns(state, effect) {
    if (!state || !effect || effect.amplifier !== 0 || effect.duration <= 0) return false;
    // A replacement/extension must never be mistaken for our countdown.
    return Math.abs(effect.duration - (state.expiresTick - system.currentTick)) <= 1;
  }
  function restore(player, effect) {
    const raw = player.getDynamicProperty(GLASSES_LEASE);
    if (typeof raw !== "string") return;
    let saved;
    try { saved = JSON.parse(raw); } catch { clear(player); return; }
    if (saved.schema !== 1 || saved.source !== NARITA_GLASSES || saved.amplifier !== 0 ||
        !Number.isInteger(saved.remaining) || saved.remaining < 1 || saved.remaining > EFFECT_TICKS ||
        !effect || effect.amplifier !== 0 || effect.duration < 1 || effect.duration > saved.remaining) {
      clear(player); return;
    }
    // Effect timers can pause while the player is offline. Never use a server
    // tick persisted across restart as if it were the new process's tick.
    const state = { expiresTick: system.currentTick + effect.duration };
    states.set(player.id, state);
    return state;
  }
  function beforeEffect(event) {
    if (!nightVision(event.effectType) || event.entity?.typeId !== "minecraft:player") return;
    const player = event.entity, own = writes.get(player.id);
    if (own && !own.observed && event.duration === EFFECT_TICKS) { own.observed = true; return; }
    // This event belongs to a potion, command, beacon or another add-on.
    // Do not cancel/modify it. A shorter replacement is protected as well.
    revoked.add(player.id);
    states.delete(player.id);
    system.run(() => {
      if (valid(player)) {
        try { player.setDynamicProperty(GLASSES_LEASE, undefined); } catch (error) { log(error); }
      }
    });
  }
  function grant(player) {
    const intent = { observed: false };
    writes.set(player.id, intent);
    try {
      player.addEffect(EFFECT, EFFECT_TICKS, { amplifier: 0, showParticles: false });
    } finally { writes.delete(player.id); }
    const effect = player.getEffect(EFFECT);
    if (revoked.has(player.id) || !effect || effect.amplifier !== 0 || effect.duration !== EFFECT_TICKS) return;
    const state = { expiresTick: system.currentTick + effect.duration };
    states.set(player.id, state);
    snapshot(player, state, effect);
  }
  function inspect(player) {
    if (!valid(player)) { states.delete(player.id); return; }
    let effect = player.getEffect(EFFECT);
    if (revoked.delete(player.id)) clear(player);
    let state = states.get(player.id) ?? restore(player, effect);
    const equipped = player.getComponent("minecraft:equippable")?.getEquipment("Head")?.typeId === NARITA_GLASSES;
    const alive = (player.getComponent("minecraft:health")?.currentValue ?? 1) > 0;
    if (state && !owns(state, effect)) { clear(player); state = undefined; }
    if (!equipped || !alive) {
      if (owns(state, effect)) player.removeEffect(EFFECT);
      clear(player);
      return;
    }
    if (!effect || (state && effect.duration <= RENEW_AT_TICKS)) {
      grant(player);
    }
    // An existing unrelated night-vision effect has priority. Do not shorten,
    // overwrite, refresh or remove it merely because these glasses are worn.
  }
  function tick() {
    const online = new Set();
    for (const player of world.getAllPlayers()) {
      online.add(player.id);
      try { inspect(player); } catch (error) { log(error); }
    }
    for (const id of states.keys()) if (!online.has(id)) states.delete(id);
    for (const id of revoked) if (!online.has(id)) revoked.delete(id);
  }
  function forget(playerId) { states.delete(playerId); writes.delete(playerId); revoked.delete(playerId); }
  function died(player) {
    if (player?.typeId !== "minecraft:player") return;
    try { clear(player); } catch { forget(player.id); }
  }
  return { tick, inspect, beforeEffect, forget, died };
}
