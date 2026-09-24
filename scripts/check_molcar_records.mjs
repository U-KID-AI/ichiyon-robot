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
const runtime = createMolcarRecords({ world: { getAllPlayers: () => players, getEntity: (id) => entities.get(id) }, system, tracks });
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
