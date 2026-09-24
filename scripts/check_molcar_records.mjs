import assert from "node:assert/strict";
import { createMolcarRecords } from "../minecraft/behavior_packs/import_structures/scripts/molcar_records_core.js";

const dim = { id: "minecraft:overworld" };
const handles = [];
const messages = [];
const player = {
  id: "p1", isValid: true, dimension: dim, location: { x: 0, y: 0, z: 0 },
  sendMessage: (text) => messages.push(text), stopSound() {},
  playSound(id, options) {
    const handle = { id, options, stopped: false, volume: options.volume, seek: 0,
      stop() { this.stopped = true; }, setVolume(value) { this.volume = value; }, seekTo(value) { this.seek = value; } };
    handles.push(handle);
    return handle;
  },
};
let players = [player];
let riders = [];
let speed = 0;
const entities = new Map();
const molcar = { id: "m1", typeId: "ichiyon:molcar", isValid: true, dimension: dim,
  location: { x: 1, y: 0, z: 0 }, getVelocity: () => ({ x: speed, y: 0, z: 0 }),
  getComponent: () => ({ getRiders: () => riders }) };
entities.set(molcar.id, molcar);
const tracks = [{ itemId: "record1", soundId: "song1", durationSeconds: 261 },
  { itemId: "record2", soundId: "song2", durationSeconds: 261 }];
const system = { currentTick: 0, run: (fn) => fn() };
const runtime = createMolcarRecords({ world: { getAllPlayers: () => players, getEntity: (id) => entities.get(id) }, system, tracks, now: () => system.currentTick * 50 });
assert.equal(runtime.use(player, molcar, "record1"), true);
assert.equal(handles.length, 1);
assert.ok(handles[0].volume < 1);
runtime.use(player, molcar, "record1");
assert.equal(handles[0].stopped, true);
assert.equal(runtime.playback.size, 0);
speed = 0.04;
assert.equal(runtime.use(player, molcar, "record1"), false);
riders = [player];
assert.equal(runtime.use(player, molcar, "record1"), true);
runtime.use(player, molcar, "record2");
assert.equal(handles[1].stopped, true);
assert.equal(handles[2].id, "song2");
system.currentTick = 200;
player.location.x = 50;
runtime.tick();
assert.equal(handles[2].stopped, true);
player.location.x = 0;
runtime.tick();
assert.equal(handles[3].seek, 10);
const other = { ...molcar, id: "m2" };
entities.set(other.id, other);
runtime.use(player, other, "record2");
runtime.stop("m1");
assert.equal(handles[3].stopped, true);
assert.equal(handles[4].stopped, false, "same sound in another car is not stopped");
players = [];
runtime.tick();
assert.equal(handles[4].stopped, true);
players = [player];
runtime.tick();
entities.delete("m2");
runtime.tick();
assert.equal(runtime.playback.size, 0);
runtime.use(player, molcar, "record1");
system.currentTick += 261 * 20;
runtime.tick();
assert.equal(runtime.playback.size, 0, "261-second recording expires");
assert.equal(handles.at(-1).stopped, true);
assert.equal(runtime.use(player, { ...molcar, typeId: "minecraft:player" }, "record1"), false);
console.log("Molcar record playback: PASS (toggle/switch/moving rider/attenuation/late listener/multiple cars/logout/unload/261s)");

let passed = 0;
function test(name, fn) { fn(); passed++; console.log(`PASS ${name}`); }
function fixture() {
  let milliseconds = 100000;
  const calls = [], pending = [], errors = [], sounds = [];
  let held = { typeId: "record1", amount: 1 }, mount;
  const p = { ...player, location: { ...player.location }, selectedSlotIndex: 0,
    getComponent: (name) => name === "minecraft:inventory" ? { container: { getItem: () => held } }
      : name === "minecraft:riding" && mount ? { entityRidingOn: mount } : undefined,
    stopSound: (id) => calls.push(["stopSound", id]),
    playSound(id, options) {
      calls.push(["play", id, options.volume]);
      const handle = { options, stopped: false,
        stop() { this.stopped = true; calls.push(["stop", id]); },
        seekTo(value) { calls.push(["seek", value]); },
        setVolume(value) { calls.push(["volume", value]); } };
      sounds.push(handle);
      return handle;
    } };
  const m = { ...molcar, location: { ...molcar.location }, getVelocity: () => ({ x: 0, y: 0, z: 0 }), getComponent: () => ({ getRiders: () => [] }) };
  const all = [p];
  const clock = { currentTick: 0, run: (fn) => pending.push(fn) };
  const state = createMolcarRecords({ world: { getAllPlayers: () => all, getEntity: () => m }, system: clock,
    tracks, now: () => milliseconds, report: (message) => errors.push(message) });
  return { state, p, m, all, clock, calls, errors, sounds,
    advance(seconds) { milliseconds += seconds * 1000; },
    held: () => held, setHeld(item) { held = item; }, setMount(entity) { mount = entity; }, flush() { pending.splice(0).forEach((fn) => fn()); } };
}
test("initial sound uses a copied car location and listener motion never reanchors", () => {
  const f = fixture(); f.state.use(f.p, f.m, "record1");
  assert.deepEqual(f.sounds[0].options, { location: f.m.location, volume: 0, pitch: 1 });
  assert.notStrictEqual(f.sounds[0].options.location, f.m.location);
  const initialVolume = f.calls.at(-1)[1];
  for (let i = 0; i < 100; i++) { f.advance(0.05); f.state.tick(); }
  f.p.location.x = 8; f.state.tick();
  assert(f.calls.at(-1)[1] < initialVolume);
  assert.equal(f.sounds.length, 1); assert(!f.sounds[0].stopped);
});

test("car travel accumulates to 1.5 blocks on every axis before stop, muted restart, seek and volume", () => {
  for (const axis of ["x", "y", "z"]) {
    const f = fixture(); f.state.use(f.p, f.m, "record1");
    for (let i = 0; i < 2; i++) { f.m.location[axis] += 0.5; f.state.tick(); }
    assert.equal(f.sounds.length, 1); assert(!f.sounds[0].stopped);
    f.advance(12.75); f.calls.length = 0;
    f.m.location[axis] += 0.5; f.state.tick();
    assert.equal(f.clock.currentTick, 0); assert.equal(f.sounds.length, 2);
    assert(f.sounds[0].stopped); assert(!f.sounds[1].stopped);
    assert.deepEqual(f.sounds[1].options.location, f.m.location);
    assert.deepEqual(f.calls.slice(0, 3), [["stop", "song1"], ["play", "song1", 0], ["seek", 12.75]]);
    assert.equal(f.calls[3][0], "volume"); assert(f.calls[3][1] > 0);
    f.m.location[axis] += 1; f.state.tick(); assert.equal(f.sounds.length, 2);
    f.advance(2); f.m.location[axis] += 0.5; f.calls.length = 0; f.state.tick();
    assert.equal(f.sounds.length, 3); assert(f.sounds[1].stopped);
    assert.deepEqual(f.calls.slice(0, 3), [["stop", "song1"], ["play", "song1", 0], ["seek", 14.75]]);
  }
});

test("reanchoring uses each listener's own anchor and three-dimensional distance", () => {
  const f = fixture(); f.state.use(f.p, f.m, "record1");
  f.m.location.x += 0.9; f.advance(3);
  f.all.push({ ...f.p, id: "p2" }); f.state.tick();
  assert.equal(f.sounds.length, 2);
  f.m.location.y += 1.2; f.state.tick();
  assert.equal(f.sounds.length, 3); assert(f.sounds[0].stopped); assert(!f.sounds[1].stopped);
  f.m.location.y += 0.3; f.state.tick();
  assert.equal(f.sounds.length, 4); assert(f.sounds[1].stopped); assert(!f.sounds[2].stopped);
});

test("reanchored handles retain the original lifetime and clearPlayer cleanup", () => {
  for (const clear of [false, true]) {
    const f = fixture(); f.state.use(f.p, f.m, "record1");
    f.advance(260); f.m.location.x += 1.5; f.state.tick();
    assert.equal(f.sounds.length, 2); assert(f.sounds[0].stopped);
    if (clear) f.state.clearPlayer(f.p.id);
    else { f.advance(1); f.state.tick(); assert.equal(f.state.playback.size, 0); }
    assert(f.sounds[1].stopped);
    assert.equal(f.state.playback.get(f.m.id)?.listeners.size ?? 0, 0);
  }
});

test("expiry follows real audio time when server ticks stall", () => {
  const f = fixture(); f.state.use(f.p, f.m, "record1");
  f.advance(260.99); f.state.tick(); assert.equal(f.state.playback.size, 1);
  f.advance(0.01); f.state.tick(); assert.equal(f.clock.currentTick, 0);
  assert.equal(f.state.playback.size, 0); assert(f.calls.some(([method]) => method === "stop"));
});
test("fast ticks alone cannot expire an unelapsed recording", () => {
  const f = fixture(); f.state.use(f.p, f.m, "record1");
  f.clock.currentTick += 261 * 20; f.state.tick(); assert.equal(f.state.playback.size, 1);
});
test("late arrival starts muted then seeks real elapsed seconds before unmuting", () => {
  const f = fixture(); f.all.length = 0; f.state.use(f.p, f.m, "record1");
  f.advance(130.5); f.all.push(f.p); f.state.tick();
  assert.deepEqual(f.calls.slice(0, 2), [["play", "song1", 0], ["seek", 130.5]]);
  assert.equal(f.calls[2][0], "volume"); assert(f.calls[2][1] > 0);
});
test("all three native control methods are checked and only the new handle stops", () => {
  const f = fixture(); let stopped = false;
  f.p.playSound = () => ({ stop() { stopped = true; }, seekTo() {} });
  f.state.use(f.p, f.m, "record1"); assert(stopped); assert.equal(f.state.playback.size, 0);
  assert.equal(f.errors.length, 1); assert.match(f.errors[0], /SoundInstance/);
  assert(!f.calls.some(([method]) => method === "stopSound"));
});
test("native seek failure stops retained handle and removes playback", () => {
  const f = fixture(); f.all.length = 0; f.state.use(f.p, f.m, "record1"); f.advance(10);
  let stopped = false;
  f.p.playSound = () => ({ stop() { stopped = true; }, seekTo() { throw Error("seek failure"); }, setVolume() {} });
  f.all.push(f.p); f.state.tick(); assert(stopped); assert.equal(f.state.playback.size, 0);
  assert.match(f.errors[0], /seek failure/);
});
test("record interaction cancels default action without consuming or replacing item", () => {
  const f = fixture(), original = f.held();
  const event = { player: f.p, target: f.m, itemStack: original, cancel: false };
  f.state.interact(event); assert(event.cancel); assert.equal(f.state.playback.size, 0);
  f.flush(); assert.equal(f.state.playback.size, 1); assert.strictEqual(f.held(), original); assert.equal(original.amount, 1);
  f.clock.currentTick++; event.cancel = false;
  f.state.interact(event); f.flush(); assert.equal(f.state.playback.size, 0); assert.equal(original.amount, 1);
});
test("switching held item before the queued interaction cancels playback start", () => {
  const f = fixture(); f.state.interact({ player: f.p, target: f.m, itemStack: f.held() });
  f.setHeld({ typeId: "minecraft:stick", amount: 1 }); f.flush(); assert.equal(f.state.playback.size, 0);
});
test("dimension change and invalid entity each stop active native handles", () => {
  const f = fixture(); f.state.use(f.p, f.m, "record1"); f.m.dimension = { id: "minecraft:nether" };
  f.state.tick(); assert.equal(f.state.playback.size, 0);
  f.m.dimension = dim; f.state.use(f.p, f.m, "record1"); f.m.isValid = false;
  f.state.tick(); assert.equal(f.state.playback.size, 0);
  assert.equal(f.calls.filter(([method]) => method === "stop").length, 2);
});
test("rider itemUse resolves only their native mount and never consumes record", () => {
  const f = fixture(); f.setMount(f.m);
  const event = { source: f.p, itemStack: f.held(), cancel: false };
  f.state.itemUse(event); assert(event.cancel); f.flush(); assert.equal(f.state.playback.size, 1);
  assert.equal(f.held().amount, 1); f.clock.currentTick++;
  f.state.itemUse({ ...event, cancel: false }); f.flush(); assert.equal(f.state.playback.size, 0);
  assert.equal(f.held().amount, 1);
});
test("itemUse plus interaction in the same tick does not toggle twice", () => {
  const f = fixture(); f.setMount(f.m);
  f.state.itemUse({ source: f.p, itemStack: f.held() });
  f.state.interact({ player: f.p, target: f.m, itemStack: f.held() }); f.flush();
  assert.equal(f.state.playback.size, 1);
  f.state.itemUse({ source: f.p, itemStack: f.held() }); f.flush(); assert.equal(f.state.playback.size, 1);
});
test("dismount or changed inventory slot before queued itemUse prevents activation", () => {
  for (const change of [f => f.setMount(undefined), f => { f.p.selectedSlotIndex = 1; }]) {
    const f = fixture(); f.setMount(f.m);
    f.state.itemUse({ source: f.p, itemStack: f.held() }); change(f); f.flush();
    assert.equal(f.state.playback.size, 0);
  }
});
test("unmounted, wrong-mount, unrelated and already-cancelled uses are untouched", () => {
  const f = fixture();
  for (const mount of [undefined, { ...f.m, typeId: "minecraft:pig" }]) {
    f.setMount(mount); const event = { source: f.p, itemStack: f.held(), cancel: false };
    f.state.itemUse(event); assert(!event.cancel); f.flush(); assert.equal(f.state.playback.size, 0);
  }
  f.setMount(f.m);
  const unrelated = { source: f.p, itemStack: { typeId: "minecraft:carrot" }, cancel: false };
  f.state.itemUse(unrelated); assert(!unrelated.cancel);
  f.state.itemUse({ source: f.p, itemStack: f.held(), cancel: true }); f.flush();
  assert.equal(f.state.playback.size, 0);
});
test("idle records perform no player enumeration or entity lookup", () => {
  const fail = () => { throw Error("unexpected idle world scan"); };
  const idle = createMolcarRecords({ world: { getAllPlayers: fail, getEntity: fail }, system, tracks });
  for (let i = 0; i < 100; i++) idle.tick();
  assert.equal(idle.playback.size, 0);
});
console.log(`${passed} focused record regressions passed`);
