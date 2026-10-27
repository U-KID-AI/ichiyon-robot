import assert from "node:assert/strict";
import { createNaritaGlasses, NARITA_GLASSES, GLASSES_LEASE, EFFECT_TICKS, RENEW_AT_TICKS, CHECK_TICKS } from "../minecraft/behavior_packs/import_structures/scripts/narita_glasses_core.js";

let passed = 0;
function test(name, fn) { fn(); passed++; console.log(`PASS ${name}`); }
function rig({ eventName = "Night Vision" } = {}) {
  const system = { currentTick: 0, queue: [], run(fn) { this.queue.push(fn); } };
  const world = { players: [], getAllPlayers() { return this.players; } };
  const reports = [], effectEvents = [];
  let runtime = createNaritaGlasses({ world, system, report: e => reports.push(e) });
  function player(id = "a") {
    const value = { id, typeId: "minecraft:player", isValid: true, head: undefined, health: 20,
      props: new Map(), effect: undefined, additions: [], removals: [], writes: 0,
      getDynamicProperty(key) { return this.props.get(key); },
      setDynamicProperty(key, data) { this.writes++; if (data === undefined) this.props.delete(key); else this.props.set(key, data); },
      getComponent(key) { return key === "minecraft:equippable" ? { getEquipment: slot => { assert.equal(slot, "Head"); return this.head ? {typeId: this.head} : undefined; } } : key === "minecraft:health" ? { currentValue: this.health } : undefined; },
      getEffect(type) { assert.equal(type, "night_vision"); return this.effect; },
      addEffect(type, duration, options) {
        const event = { effectType: eventName, duration, entity: this };
        effectEvents.push(event); runtime.beforeEffect(event);
        this.effect = { typeId: "minecraft:night_vision", duration, amplifier: options.amplifier };
        this.additions.push({duration, ...options});
      },
      removeEffect(type) { assert.equal(type, "night_vision"); this.effect = undefined; this.removals.push(type); },
    };
    world.players.push(value); return value;
  }
  function advance(ticks) {
    for (let i = 0; i < ticks; i++) {
      system.currentTick++;
      for (const p of world.players) if (p.effect && --p.effect.duration <= 0) p.effect = undefined;
      system.queue.splice(0).forEach(fn => fn());
      if (system.currentTick % CHECK_TICKS === 0) runtime.tick();
    }
  }
  function external(p, duration, amplifier = 0) {
    // Native BDS rejects a shorter same-strength grant without effectAdd.
    if (p.effect && amplifier <= p.effect.amplifier && duration <= p.effect.duration) return false;
    const event = { effectType: eventName, duration, entity: p };
    effectEvents.push(event); runtime.beforeEffect(event);
    p.effect = {duration, amplifier};
    system.queue.splice(0).forEach(fn => fn());
    return true;
  }
  return { player, system, world, advance, external, reports, effectEvents,
    get runtime() { return runtime; },
    restart() { system.currentTick = 0; runtime = createNaritaGlasses({ world, system }); } };
}

test("inventory and held glasses do not grant night vision", () => {
  const r = rig(), p = r.player(); p.inventory = NARITA_GLASSES; p.hand = NARITA_GLASSES;
  r.advance(100); assert.equal(p.effect, undefined); assert.equal(p.writes, 0);
});
test("head-slot glasses grant amplifier zero hidden-particle night vision", () => {
  const r = rig(), p = r.player(); p.head = NARITA_GLASSES; r.advance(5);
  assert.equal(p.effect.duration, EFFECT_TICKS); assert.equal(p.additions[0].amplifier, 0);
  assert.equal(p.additions[0].showParticles, false);
});
test("wearer-only lease ends within five ticks after removal", () => {
  const r = rig(), p = r.player(); p.head = NARITA_GLASSES; r.advance(5); p.head = undefined; r.advance(5);
  assert.equal(p.effect, undefined); assert.equal(p.removals.length, 1); assert.equal(p.props.size, 0);
});
test("refresh stays above the ten-second flash and renews only at sixty seconds", () => {
  const r = rig(), p = r.player(); p.head = NARITA_GLASSES; r.advance(5);
  assert.equal(p.additions.length, 1);
  assert.equal(EFFECT_TICKS, 180 * 20); assert.equal(RENEW_AT_TICKS, 60 * 20);
  for (let n = 0; n < EFFECT_TICKS; n++) {
    r.advance(1);
    assert.ok(p.effect.duration > 10 * 20);
    assert.ok(p.effect.duration >= RENEW_AT_TICKS);
  }
  assert.equal(p.additions.length, 2); assert.equal(p.writes, 2);
});
test("existing potion is neither overwritten nor removed", () => {
  const r = rig(), p = r.player(); p.effect = {duration: 1000, amplifier: 0}; p.head = NARITA_GLASSES;
  r.advance(100); p.head = undefined; r.advance(5);
  assert.equal(p.effect.duration, 895); assert.equal(p.removals.length, 0); assert.equal(p.additions.length, 0);
});
for (const duration of [20, 300, 2000]) test(`accepted outside effect duration ${duration} relinquishes our lease`, () => {
  const r = rig(), p = r.player(); p.head = NARITA_GLASSES; r.advance(5);
  // A short replacement is possible after an external clear. An equal-length
  // replacement is accepted once the existing effect has ticked down.
  if (duration < EFFECT_TICKS) p.effect = undefined;
  if (duration === EFFECT_TICKS) r.advance(1);
  assert.equal(r.external(p, duration), true);
  p.head = undefined; r.advance(5); assert.equal(p.effect.duration, duration - 5); assert.equal(p.removals.length, 0);
});

test("native rejected shorter grant emits no event and preserves glasses ownership", () => {
  const r = rig(), p = r.player(); p.head = NARITA_GLASSES; r.advance(7);
  const events = r.effectEvents.length, saved = p.props.get(GLASSES_LEASE);
  assert.equal(p.effect.duration, EFFECT_TICKS - 2); assert.equal(r.external(p, 100), false);
  assert.equal(p.effect.duration, EFFECT_TICKS - 2); assert.equal(r.effectEvents.length, events);
  assert.equal(p.props.get(GLASSES_LEASE), saved);
  p.head = undefined; r.advance(5); assert.equal(p.effect, undefined); assert.equal(p.removals.length, 1);
});

test("native display name and identifier aliases distinguish own and outside grants", () => {
  for (const eventName of ["Night Vision", "night_vision", "minecraft:night_vision", " MINECRAFT:Night Vision "]) {
    const r = rig({eventName}), p = r.player(); p.head = NARITA_GLASSES; r.advance(5);
    assert.ok(p.props.has(GLASSES_LEASE));
    const outside = EFFECT_TICKS + 400;
    assert.equal(r.external(p, outside), true); assert.equal(p.props.has(GLASSES_LEASE), false);
    p.head = undefined; r.advance(5); assert.equal(p.effect.duration, outside - 5); assert.equal(p.removals.length, 0);
  }
});

test("unrelated effect display names never revoke the glasses lease", () => {
  const r = rig(), p = r.player(); p.head = NARITA_GLASSES; r.advance(5);
  for (const effectType of ["Strength", "Darkness", "night_vision_extra", null])
    r.runtime.beforeEffect({effectType,duration:1000,entity:p});
  assert.ok(p.props.has(GLASSES_LEASE));p.head=undefined;r.advance(5);
  assert.equal(p.effect,undefined);assert.equal(p.removals.length,1);
});
test("outside amplifier and unobserved longer replacement are preserved", () => {
  const r = rig(), p = r.player(); p.head = NARITA_GLASSES; r.advance(5);
  p.effect = {duration: 900, amplifier: 2}; p.head = undefined; r.advance(5);
  assert.equal(p.effect.amplifier, 2); assert.equal(p.removals.length, 0);
});
test("two wearers cannot remove each other's effect", () => {
  const r = rig(), a = r.player("a"), b = r.player("b"); a.head = b.head = NARITA_GLASSES;
  r.advance(5); a.head = undefined; const outside = EFFECT_TICKS + 200; r.external(b, outside); r.advance(5);
  assert.equal(a.effect, undefined); assert.equal(b.effect.duration, outside - 5); assert.equal(b.removals.length, 0);
});
test("restart with retained head equipment safely resumes our lease", () => {
  const r = rig(), p = r.player(); p.head = NARITA_GLASSES; r.advance(80); r.restart(); r.advance(5);
  assert.ok(p.effect.duration > 10 * 20); assert.ok(p.effect.duration < EFFECT_TICKS);
  assert.ok(p.props.has(GLASSES_LEASE));
  p.head = undefined; r.advance(5); assert.equal(p.effect, undefined);
});
test("rejoin with equipment removed clears only persisted glasses effect", () => {
  const r = rig(), p = r.player(); p.head = NARITA_GLASSES; r.advance(5);
  r.runtime.forget(p.id); p.head = undefined; r.advance(5); assert.equal(p.effect, undefined);
});
test("death and later spawn do not leave an equipment lease", () => {
  const r = rig(), p = r.player(); p.head = NARITA_GLASSES; r.advance(5); p.health = 0;
  r.advance(5); assert.equal(p.effect, undefined); r.runtime.died(p); assert.equal(p.props.size, 0);
  p.health = 20; p.head = undefined; r.advance(5); assert.equal(p.effect, undefined);
});
test("malformed or mismatched persistent metadata cannot clear other effects", () => {
  for (const data of ["bad JSON", JSON.stringify({schema: 1, source: NARITA_GLASSES, remaining: 9999, amplifier: 0})]) {
    const r = rig(), p = r.player(); p.props.set(GLASSES_LEASE, data); p.effect = {duration: 700, amplifier: 0};
    r.advance(5); assert.equal(p.effect.duration, 695); assert.equal(p.removals.length, 0);
  }
});
test("one invalid player does not stop equipment updates for another", () => {
  const r = rig(), a = r.player("a"), b = r.player("b"); a.getEffect = () => {throw Error("departed");};
  b.head = NARITA_GLASSES; r.advance(10); assert(b.effect); assert.equal(r.reports.length, 1);
});
console.log(`Narita glasses equipment/effect cases: ${passed} PASS; client rendering not tested`);
