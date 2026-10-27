import assert from "node:assert/strict";
import { createResilienceProbe } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/resilience_probe.js";

const ids = ["sparrow", "temperance", "lucy", "silence", "jinn", "ace", "grail", "hope", "auction",
  "fire", "pig", "light", "mine", "clock", "walker", "garden", "rabbit"];
const values = new Map();
const definitions = ids.map((id, i) => ({ id, fixtures: [{ role: "reception", type: "junket:guide", location: [i, 67, 0], rotation: 0 }] }));
const actors = definitions.map(venue => ({ id: venue.id + "-persisted", typeId: "junket:guide",
  location: { x: venue.fixtures[0].location[0], y: 67, z: 0 },
  hasTag: tag => tag === `junket:${venue.id}:fixture:reception`,
  getVelocity: () => ({ x: 0, y: 0, z: 0 }), getRotation: () => ({ y: 0 }),
  getComponent: () => ({ currentValue: 20 }), venue: venue.id }));
const world = { getAllPlayers: () => [], getDynamicProperty: key => values.get(key),
  setDynamicProperty: (key, value) => values.set(key, value),
  getDimension: () => ({ getEntities: ({ tags }) => actors.filter(actor => tags.includes(`junket:${actor.venue}:managed`)) }) };
const system = { runTimeout: callback => { queueMicrotask(callback); return 1; } };
let repairs = 0;
const live = { manager: { tables: new Map(), maintenance: null }, venues: {
  repair() { repairs++; throw new Error("Snapshot must never invoke repair"); },
  withLoaded: async (id, work) => work({ wait: async () => {}, guard() {} }),
} };
const probe = createResilienceProbe({ world, system, live, definitions });
const originalLog = console.log;
console.log = () => {};
try {
  const seed = await probe.run("request", "snapshot");
  assert.equal(seed.ok, true);
  assert.equal(seed.scope, "fixture_snapshot_only");
  assert.equal(seed.checks.length, 17);
  assert.equal(repairs, 0);
  for (const check of seed.checks) {
    assert.equal(check.snapshot_only, true);
    assert.equal(check.actor_readiness.snapshot.settled, true);
    for (const forbidden of ["attack", "physics", "damage_injected", "recovery", "access"])
      assert.equal(check[forbidden], undefined);
  }
  const after = await probe.run("request", "snapshot_restart");
  assert.equal(after.ok, true);
  assert.ok(after.checks.every(check => check.restart_persistence));
  actors[0].id = "wrong-replacement";
  const changed = await probe.run("request", "snapshot_restart");
  assert.equal(changed.ok, false);
  assert.deepEqual(changed.failures.map(row => row.id), ["sparrow"]);
  assert.match(changed.failures[0].message, /identities changed/);
  await assert.rejects(probe.run("different", "snapshot_restart"), /Missing pre-restart/);
  await assert.rejects(probe.run("request", "other"), /Unknown resilience phase/);
  assert.equal(live.manager.maintenance, null);
  assert.equal(repairs, 0);
} finally { console.log = originalLog; }
console.log("PASS: snapshot-only seed/restart all17, strict identity failure, no repair or destructive work; mocked Script API, not BDS.");
