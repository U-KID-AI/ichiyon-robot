import assert from "node:assert/strict";
import { createQualityProbe } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/quality_probe.js";
import { QUALITY_GAMES } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/quality_cases.js";
import { permanentFixtureLabel } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue_presentation.js";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";
import { ensureFixedFixtureHealth } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/fixture_health.js";

function setup(fault = null) {
  let serial = 0;
  const entities = [], definitions = QUALITY_GAMES.map((game, index) => ({
    id: game.id, title: game.title, origin: [index * 100, 64, 0], size: [20, 12, 20],
    destination: { x: index * 100 + 5, y: 65, z: 5 },
    fixtures: ["reception", "return"].map((role, j) => ({ role, type: "junket:probe_fixture",
      location: [index * 100 + 5 + j * 4, 65, 5] })),
  }));
  const dimension = {
    getEntities: ({ location, maxDistance }) => entities.filter(entity => entity.isValid &&
      Math.hypot(entity.location.x - location.x, entity.location.y - location.y, entity.location.z - location.z) < maxDistance),
    spawnEntity(typeId, location) {
      const tags = new Set(), properties = new Map();
      const entity = { id: `entity-${++serial}`, typeId, location, isValid: true, nameTag: "", health: 20,
        hasTag: tag => tags.has(tag), addTag: tag => tags.add(tag), getDynamicProperty: key => properties.get(key),
        setDynamicProperty: (key, value) => properties.set(key, value),
        getComponent() { return { currentValue: this.health, effectiveMax: 20, resetToMaxValue: () => { this.health = 20; } }; }, remove() { this.isValid = false; } };
      entities.push(entity); return entity;
    },
  };
  const lookup = id => definitions.find(venue => venue.id === id);
  function ensure(id) {
    const venue = lookup(id);
    for (const fixture of venue.fixtures) {
      const tag = `junket:${id}:fixture:${fixture.role}`;
      let entity = entities.find(value => value.isValid && value.hasTag(tag));
      if (!entity) {
        entity = dimension.spawnEntity(fixture.type, { x: fixture.location[0], y: 65, z: 5 });
        entity.addTag(tag); entity.addTag(`junket:${id}:managed`);
      }
      entity.nameTag = permanentFixtureLabel(venue, fixture);
    }
  }
  const world = { getDimension: () => dimension, getAllPlayers: () => [] };
  const venues = { repair: ensure, ensure, withLoaded: async (id, callback) => callback({ guard() {} }),
    start(id) {
      if (id !== "lucy") return;
      const entity = entities.find(value => value.hasTag("junket:lucy:fixture:reception"));
      if (fault === "replace") { entity.remove(); ensure(id); }
      if (fault === "damage") entity.health--;
      if (fault === "masked-damage") {
        entity.health--;
        ensureFixedFixtureHealth(lookup(id), lookup(id).fixtures[0], entity,
          () => { throw new Error("Healing a live fixture must not spawn an actor"); });
      }
      if (fault === "crowding") entity.nameTag = "stale floating label";
      if (fault === "broad-cleanup") entities.find(value => value.hasTag("junket:quality_probe") && value.isValid)?.remove();
    }, update() {}, end() {},
  };
  const live = { venues, manager: { tables: new Map() }, specialVenues: {} };
  const system = { runTimeout: callback => queueMicrotask(callback) };
  return { probe: createQualityProbe({ world, system, live, definitions }), entities, world };
}

const previous = console.log;
const manager = new GameManager(QUALITY_GAMES);
manager.maintenance = "quality-test";
assert.throws(() => manager.join("lucy", "lucy", "real-player"), /自動検証中/);
assert.equal(manager.tables.size, 0);
manager.maintenance = null;
assert.ok(manager.join("lucy", "lucy", "real-player"));
console.log = () => {};
try {
  const success = setup();
  const proof = await success.probe.run("eafc7cdf-0a83-40cd-b4dd-2e682bb07f9b");
  assert.equal(proof.ok, true, JSON.stringify(proof.failures));
  assert.equal(proof.checks.length, 17);
  assert.ok(proof.checks.every(check => check.stages.length === 9 && check.health_preserved));
  assert.ok(success.entities.filter(entity => entity.hasTag("junket:quality_probe")).every(entity => !entity.isValid));
  for (const fault of ["replace", "damage", "masked-damage", "crowding", "broad-cleanup"]) {
    const probe = setup(fault);
    const failure = await probe.probe.run("eafc7cdf-0a83-40cd-b4dd-2e682bb07f9b");
    assert.equal(failure.ok, false, fault);
    assert.ok(failure.failures.some(value => value.id === "lucy"), fault);
    assert.equal(failure.checks.length, 17, "one venue failure must not skip the remaining venues");
  }
  const occupied = setup();
  occupied.world.getAllPlayers = () => [{ id: "real-player" }];
  const refused = await occupied.probe.run("eafc7cdf-0a83-40cd-b4dd-2e682bb07f9b");
  assert.equal(refused.ok, false);
  assert.equal(occupied.entities.length, 0, "an occupied server must not be mutated");
} finally { console.log = previous; }
console.log("PASS quality probe: 17 lifecycles; replacement, damage, stale text and foreign cleanup rejected; occupied server protected");
