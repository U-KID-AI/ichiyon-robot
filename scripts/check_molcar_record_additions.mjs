import assert from "node:assert/strict";
import { createMolcarRecords } from "../minecraft/behavior_packs/import_structures/scripts/molcar_records_core.js";

const keys = ["lets_cooking_molcar", "shachiku_molcar", "kanden_molcar", "chika_molcar"];
// Synthetic registrations exercise the existing player without publishing
// supplied recordings or adding missing sounds to the public pack catalog.
const molcarRecords = keys.map((key, i) => ({ itemId: `ichiyon:record_${key}`,
  soundId: `ichiyon:record.${key}`, durationSeconds: 61 + i }));
let checks = 0;
for (const track of molcarRecords) {
  assert.equal(track.soundId, "ichiyon:record." + track.itemId.slice("ichiyon:record_".length));
  assert.ok(Number.isFinite(track.durationSeconds) && track.durationSeconds > 60);
  const sounds = [];
  const dimension = { id: "minecraft:overworld" };
  const players = ["p1", "p2"].map((id, i) => ({ id, isValid: true, dimension,
    location: { x: i * 2, y: 64, z: 0 }, sendMessage() {},
    playSound(soundId, options) {
      const handle = { player: id, soundId, stopped: false, options, seek: 0,
        stop() { this.stopped = true; }, setVolume(v) { this.volume = v; }, seekTo(v) { this.seek = v; } };
      sounds.push(handle); return handle;
    },
  }));
  const cars = new Map(["car1", "car2"].map(id => [id, { id, typeId: "ichiyon:molcar", isValid: true,
    dimension, location: { x: 1, y: 64, z: 0 }, getVelocity: () => ({ x: 0, y: 0, z: 0 }) }]));
  let clock = 0;
  const runtime = createMolcarRecords({ world: { getEntity: id => cars.get(id), getAllPlayers: () => players },
    system: { currentTick: 0 }, tracks: molcarRecords, now: () => clock });
  assert.equal(runtime.use(players[0], cars.get("car1"), track.itemId), true);
  assert.equal(sounds.length, 2); assert(sounds.every(h => h.soundId === track.soundId));
  assert.equal(runtime.use(players[1], cars.get("car2"), track.itemId), true);
  assert.equal(sounds.length, 4);
  runtime.use(players[0], cars.get("car1"), track.itemId);
  assert(sounds.slice(0, 2).every(h => h.stopped));
  assert(sounds.slice(2).every(h => !h.stopped));
  clock = (track.durationSeconds - 0.001) * 1000; runtime.tick();
  assert.equal(runtime.playback.size, 1);
  clock = track.durationSeconds * 1000 + 1; runtime.tick();
  assert.equal(runtime.playback.size, 0); assert(sounds.every(h => h.stopped));
  checks++;
}
console.log(`PASS ${checks} synthetic tracks: exact sound identity, two listeners/two cars, toggle and duration expiry`);
