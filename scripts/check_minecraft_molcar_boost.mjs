import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { createMolcarBoost, MOLCAR_TYPES, BOOST_MARKER } from "../minecraft/behavior_packs/import_structures/scripts/molcar_boost_core.js";

const root = new URL("../", import.meta.url);
let passed = 0;
function test(name, fn) { fn(); passed++; console.log(`PASS ${name}`); }
function fixture(typeId = MOLCAR_TYPES[0]) {
  const scheduled = [], sounds = [], errors = [], writes = [];
  const system = { currentTick: 10, run: fn => scheduled.push(fn) };
  const dimension = { id: "overworld", playSound: (...args) => sounds.push(args),
    getEntities: q => q.type === mount.typeId && mount.isValid ? [mount] : [] };
  const movement = { currentValue: .4, effectiveMax: .65,
    setCurrentValue(value) {
      assert(value <= this.effectiveMax);
      if (this.reject) return false;
      this.currentValue = value; writes.push(value); return true;
    } };
  const props = new Map();
  const mount = { typeId, id: "mount", isValid: true, health: 30, dimension, location: { x: 0, y: 64, z: 0 },
    getComponent: k => k === "minecraft:movement" ? movement : k === "minecraft:health" ? { currentValue: mount.health } : undefined,
    getDynamicProperty: k => props.get(k), setDynamicProperty: (k, v) => v === undefined ? props.delete(k) : props.set(k, v) };
  let item = { typeId: "minecraft:carrot", amount: 4, nameTag: "special", opaque: "kept" };
  const container = { getItem: () => item && { ...item }, setItem: (slot, next) => { item = next && { ...next }; } };
  const player = { typeId: "minecraft:player", id: "player", isValid: true, health: 20, dimension, selectedSlotIndex: 0, riding: mount,
    getComponent: k => k === "minecraft:riding" ? { entityRidingOn: player.riding }
      : k === "minecraft:inventory" ? { container } : k === "minecraft:health" ? { currentValue: player.health } : undefined };
  const world = { getDimension: id => id === "overworld" ? dimension : { getEntities: () => [] } };
  const deps = { world, system, report: e => errors.push(e) };
  const core = createMolcarBoost(deps);
  const event = () => ({ source: player, itemStack: container.getItem(0), cancel: false });
  const flush = () => scheduled.splice(0).forEach(fn => fn());
  const use = () => { const e = event(); core.use(e); flush(); return e; };
  return { core, system, mount, player, movement, container, scheduled, sounds, errors, writes, deps, event, flush, use };
}

test("three native movement definitions allow boost and preserve steering", () => {
  for (const type of MOLCAR_TYPES) {
    const name = type.split(":")[1];
    const bp = JSON.parse(readFileSync(new URL(`minecraft/behavior_packs/ichiyon_avatar_bp/entities/${name}.behavior.json`, root)))["minecraft:entity"];
    assert.deepEqual(bp.components["minecraft:movement"], { max: .65, value: .4 });
    assert("minecraft:input_ground_controlled" in bp.components);
    assert("minecraft:rideable" in bp.components);
    assert("minecraft:horse.jump_strength" in bp.components);
  }
});
test("each ordinary Molcar consumes one carrot, preserves item data and plays pui", () => {
  for (const type of MOLCAR_TYPES) {
    const f = fixture(type); assert(f.use().cancel);
    assert.equal(f.movement.currentValue, .65); assert.equal(f.core.active.get("mount").until, 110);
    assert.deepEqual(f.container.getItem(0), { typeId: "minecraft:carrot", amount: 3, nameTag: "special", opaque: "kept" });
    assert.equal(f.sounds[0][0], "ichiyon:molcar.pui"); assert.equal(f.mount.getDynamicProperty(BOOST_MARKER), true);
  }
});
test("last carrot clears slot, including Creative (explicit consumption contract)", () => {
  const f = fixture(); f.player.getGameMode = () => "Creative";
  f.container.setItem(0, { typeId: "minecraft:carrot", amount: 1 }); f.use();
  assert.equal(f.container.getItem(0), undefined); assert.equal(f.movement.currentValue, .65);
});
test("reuse resets 100-tick expiry, never multiplies or stacks speed", () => {
  const f = fixture(); f.use(); f.system.currentTick = 109; f.use();
  assert.equal(f.movement.currentValue, .65); assert.equal(f.core.active.get("mount").until, 209);
  f.system.currentTick = 110; f.core.tick(); assert.equal(f.movement.currentValue, .65);
  f.system.currentTick = 208; f.core.tick(); assert.equal(f.movement.currentValue, .65);
  f.system.currentTick = 209; f.core.tick(); assert.equal(f.movement.currentValue, .4);
  assert.equal(f.mount.getDynamicProperty(BOOST_MARKER), undefined); assert.equal(f.core.active.size, 0);
});
test("before event only cancels; mutations deferred; two event paths consume once", () => {
  const f = fixture(), e = f.event(); f.core.use(e);
  f.core.interact({ player: f.player, target: f.mount, itemStack: e.itemStack });
  assert(e.cancel); assert.equal(f.scheduled.length, 1); assert.equal(f.movement.currentValue, .4);
  assert.equal(f.container.getItem(0).amount, 4);
  f.flush(); f.use(); assert.equal(f.container.getItem(0).amount, 3); assert.equal(f.sounds.length, 1);
});
test("direct mounted interaction supports carrot boost without completing food consumption", () => {
  const f = fixture(); const e = { player: f.player, target: f.mount, itemStack: f.container.getItem(0) };
  f.core.interact(e); assert(e.cancel); f.flush(); assert.equal(f.movement.currentValue, .65);
});
test("garbage, other mounts, unmounted use, other items and cancelled events untouched", () => {
  for (const cause of ["garbage", "pig", "unmounted", "potato", "cancelled", "dead"]) {
    const f = fixture(cause === "garbage" ? "ichiyon:garbage_molcar" : cause === "pig" ? "minecraft:pig" : MOLCAR_TYPES[0]);
    if (cause === "unmounted") f.player.riding = undefined;
    if (cause === "potato") f.container.setItem(0, { typeId: "minecraft:potato", amount: 4 });
    if (cause === "dead") f.player.health = 0;
    const e = f.event(); if (cause === "cancelled") e.cancel = true;
    f.core.use(e); f.flush(); assert.equal(f.container.getItem(0).amount, 4, cause);
    assert.equal(f.movement.currentValue, .4, cause); assert.equal(f.core.active.size, 0, cause);
  }
});
test("deferred callback rechecks selected slot, carrot, mount, validity and dimension", () => {
  for (const cause of ["slot", "item", "mount", "dead", "dimension", "invalid"]) {
    const f = fixture(); f.core.use(f.event());
    if (cause === "slot") f.player.selectedSlotIndex = 1;
    if (cause === "item") f.container.setItem(0, { typeId: "minecraft:potato", amount: 4 });
    if (cause === "mount") f.player.riding = { ...f.mount, id: "other" };
    if (cause === "dead") f.player.health = 0;
    if (cause === "dimension") f.player.dimension = { id: "nether" };
    if (cause === "invalid") f.mount.isValid = false;
    f.flush(); assert.equal(f.container.getItem(0).amount, 4, cause); assert.equal(f.movement.currentValue, .4, cause);
  }
});
test("dismount, rider death/logout, mount death, dimension and rider replacement clear boost", () => {
  for (const cause of ["dismount", "playerDeath", "logout", "mountDeath", "dimension", "replacement"]) {
    const f = fixture(); f.use();
    if (cause === "dismount") f.player.riding = undefined;
    if (cause === "playerDeath") { f.player.health = 0; f.core.died(f.player); }
    if (cause === "logout") f.core.releasePlayer(f.player.id);
    if (cause === "mountDeath") { f.mount.health = 0; f.core.died(f.mount); }
    if (cause === "dimension") f.player.dimension = { id: "nether" };
    if (cause === "replacement") f.player.riding = { ...f.mount, id: "other" };
    f.core.tick(); assert.equal(f.movement.currentValue, .4, cause); assert.equal(f.core.active.size, 0, cause);
  }
});
test("unload forgets inaccessible entity; entity load restores native base", () => {
  const f = fixture(); f.use(); f.mount.isValid = false; f.core.tick(); assert.equal(f.core.active.size, 0);
  f.mount.isValid = true; f.core.recover(f.mount); assert.equal(f.movement.currentValue, .4);
  assert.equal(f.mount.getDynamicProperty(BOOST_MARKER), undefined);
});
test("reload scan repairs persisted and markerless boosted attributes in loaded chunks", () => {
  for (const clearMarker of [true, false]) {
    const f = fixture(); f.use(); if (clearMarker) f.mount.setDynamicProperty(BOOST_MARKER, undefined);
    const reloaded = createMolcarBoost(f.deps); reloaded.scan();
    assert.equal(f.movement.currentValue, .4); assert.equal(f.mount.getDynamicProperty(BOOST_MARKER), undefined);
    assert.equal(f.container.getItem(0).amount, 3);
  }
});
test("periodic recovery scan does not cancel an active valid timer", () => {
  const f = fixture(); f.use(); f.core.scan(); assert.equal(f.movement.currentValue, .65);
});
test("different cars keep independent speeds and expiry timers", () => {
  const a = fixture(), b = fixture(MOLCAR_TYPES[1]);
  b.mount.id = "second-mount"; b.player.id = "second-player";
  a.use(); a.system.currentTick = 50;
  assert(a.core.activate(b.player, b.mount.id, 0)); assert.equal(a.core.active.size, 2);
  a.system.currentTick = 110; a.core.tick();
  assert.equal(a.movement.currentValue, .4); assert.equal(b.movement.currentValue, .65);
  a.system.currentTick = 150; a.core.tick();
  assert.equal(b.movement.currentValue, .4); assert.equal(a.core.active.size, 0);
});
test("unsupported attribute max rejects boost without consuming carrot", () => {
  const f = fixture(); f.movement.effectiveMax = .4; f.use();
  assert.equal(f.container.getItem(0).amount, 4); assert.equal(f.movement.currentValue, .4);
  assert.equal(f.core.active.size, 0); assert.equal(f.mount.getDynamicProperty(BOOST_MARKER), undefined);
  assert.equal(f.errors.length, 1);
});
test("BDS float32 movement max accepts 0.65 and idle scans ignore float32 base rounding", () => {
  const f = fixture(); f.movement.effectiveMax = Math.fround(.65);
  f.movement.currentValue = Math.fround(.4); f.core.scan(); assert.equal(f.writes.length, 0);
  f.use(); assert.equal(f.movement.currentValue, Math.fround(.65));
  assert.equal(f.container.getItem(0).amount, 3); assert.equal(f.errors.length, 0);
  f.system.currentTick += 100; f.core.tick(); assert.equal(f.movement.currentValue, .4);
});
test("inventory write failure restores old speed and does not reset a running timer", () => {
  for (const existing of [true, false]) {
    const f = fixture(); if (existing) { f.use(); f.system.currentTick++; }
    const amount = f.container.getItem(0).amount;
    f.container.setItem = () => { throw Error("inventory unavailable"); }; f.use();
    assert.equal(f.container.getItem(0).amount, amount); assert.equal(f.movement.currentValue, existing ? .65 : .4);
    assert.equal(f.core.active.get("mount")?.until, existing ? 110 : undefined);
  }
});
test("failed reset retries next tick without losing recovery state", () => {
  const f = fixture(); f.use(); f.player.riding = undefined; f.movement.reject = true; f.core.tick();
  assert.equal(f.core.active.size, 1); f.movement.reject = false; f.core.tick();
  assert.equal(f.core.active.size, 0); assert.equal(f.movement.currentValue, .4);
});
test("boost module binds actual API event signals once and schedules recovery", () => {
  const signals = {}, intervals = [], scheduled = [];
  const signal = name => ({ subscribe(fn) { assert(!signals[name]); signals[name] = fn; } });
  const world = { beforeEvents: Object.fromEntries(["itemUse", "playerInteractWithEntity"].map(n => [n, signal(n)])),
    afterEvents: Object.fromEntries(["entityLoad", "entitySpawn", "entityDie", "playerLeave", "playerDimensionChange"].map(n => [n, signal(n)])) };
  const calls = [], core = Object.fromEntries(["use", "interact", "recover", "died", "releasePlayer", "tick", "scan"].map(n => [n, e => calls.push([n, e])]));
  const source = readFileSync(new URL("minecraft/behavior_packs/import_structures/scripts/molcar_boost.js", root), "utf8").replace(/^import .*;\r?\n/gm, "");
  vm.runInNewContext(source, { world, system: { run: fn => scheduled.push(fn), runInterval: (fn, ticks) => intervals.push([fn, ticks]) },
    createMolcarBoost: () => core, console: { warn() {} } });
  assert.equal(Object.keys(signals).length, 7); assert.deepEqual(intervals.map(x => x[1]), [1, 100]);
  scheduled.shift()(); assert.equal(calls[0][0], "scan");
  signals.itemUse("use-event"); signals.playerInteractWithEntity("interact-event");
  assert.deepEqual(calls.slice(1), [["use", "use-event"], ["interact", "interact-event"]]);
});
console.log(`${passed} Molcar boost checks passed`);
