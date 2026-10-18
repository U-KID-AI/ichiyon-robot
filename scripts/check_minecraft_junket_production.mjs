import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { VENUE_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_venue_generated.js";
import { createProductionVenues } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/production_venue.js";

const catalog = JSON.parse(readFileSync(
  new URL("../scripts/minecraft/junket_games.json", import.meta.url), "utf8"));
assert.equal(VENUE_CONFIG.VENUES.length, 17);
assert.deepEqual(VENUE_CONFIG.VENUES.map(entry => entry.id), catalog.games.map(game => game.id));
assert.equal(new Set(VENUE_CONFIG.VENUES.map(entry => entry.structure)).size, 17);
assert.ok(VENUE_CONFIG.VENUES.every(entry =>
  entry.origin[0] >= 20000 && entry.origin[0] < 22000 && entry.origin[2] === 20000));

const sorted = [...VENUE_CONFIG.VENUES].sort((a, b) => a.origin[0] - b.origin[0]);
for (let index = 1; index < sorted.length; index++) {
  const previous = sorted[index - 1];
  const current = sorted[index];
  assert.ok(previous.origin[0] + previous.size[0] <= current.origin[0],
    `${previous.id} and ${current.id} must not overlap`);
}
assert.ok(VENUE_CONFIG.VENUES.every(entry =>
  entry.destination.x >= entry.origin[0] + 1 &&
  entry.destination.x <= entry.origin[0] + entry.size[0] - 2 &&
  entry.destination.y === entry.origin[1] + 2 &&
  entry.destination.z >= entry.origin[2] + 1 &&
  entry.destination.z <= entry.origin[2] + entry.size[2] - 2));

const properties = new Map();
const placements = [];
const entities = [];
const timers = [];
const specialCalls = [];
const blocks = new Map();
const key = value => `${value.x},${value.y},${value.z}`;
const dimension = {
  id: "minecraft:overworld",
  getBlock(location) {
    if (location.x === 20000 && location.y === 65 && location.z === 20000 &&
        !blocks.has(key(location))) return undefined;
    const typeId = blocks.get(key(location)) ?? "minecraft:air";
    return { typeId, isAir: typeId === "minecraft:air" };
  },
  getEntities({ tags, location, maxDistance }) {
    return entities.filter(entity => !entity.removed &&
      tags.every(tag => entity.tags.has(tag)) &&
      Math.hypot(entity.location.x - location.x, entity.location.z - location.z) <= maxDistance);
  },
  spawnEntity(typeId, location) {
    const entity = {
      typeId, location, removed: false, tags: new Set(), nameTag: "",
      addTag(tag) { this.tags.add(tag); },
      hasTag(tag) { return this.tags.has(tag); },
    };
    entities.push(entity);
    return entity;
  },
};
const world = {
  getDimension: id => {
    assert.equal(id, "minecraft:overworld");
    return dimension;
  },
  getDynamicProperty: name => properties.get(name),
  setDynamicProperty: (name, value) => properties.set(name, value),
  getAllPlayers: () => [],
  structureManager: {
    place(structure, target, location, options) {
      assert.equal(target, dimension);
      assert.equal(options.includeEntities, false);
      placements.push({ structure, location: { ...location } });
      const venue = VENUE_CONFIG.VENUES.find(candidate => candidate.structure === structure);
      blocks.set(key(location), venue.origin_block);
    },
  },
};
const system = {
  runTimeout(callback) { timers.push(callback); return timers.length; },
};
const specialVenues = Object.fromEntries(
  VENUE_CONFIG.VENUES.filter(entry => entry.special_runtime).map(entry => [
    entry.id,
    {
      provision() { specialCalls.push(`${entry.id}:provision`); },
      ensure() { specialCalls.push(`${entry.id}:ensure`); },
    },
  ]),
);
let warpInstalls = 0;
const warp = { installControls() { warpInstalls++; return true; } };
const venues = createProductionVenues({ world, system });
assert.equal(venues.installAll(warp, specialVenues), true);
while (timers.length) timers.shift()();

assert.equal(placements.length, 17);
assert.deepEqual(placements.map(entry => entry.structure),
  VENUE_CONFIG.VENUES.map(entry => entry.structure));
assert.equal(warpInstalls, 1);
assert.equal(properties.get(VENUE_CONFIG.VENUE_INSTALL_KEY), 1);
assert.equal(entities.filter(entity => entity.typeId === "minecraft:npc").length, 12);
assert.equal(entities.filter(entity => entity.typeId.endsWith("_prop")).length, 12);
assert.equal(specialCalls.length, 5);
for (const key of [
  "junket:venue_v1", "junket:temperance_venue_v1", "junket:lucy_venue_v1",
  "junket:silence_venue_v1", "junket:jinn_venue_v1",
]) assert.ok(properties.has(key), `missing special venue config ${key}`);

const sparrow = VENUE_CONFIG.VENUES.find(entry => entry.id === "sparrow");
assert.deepEqual(venues.config("sparrow").origin, {
  x: sparrow.runtime_origin[0],
  y: sparrow.runtime_origin[1],
  z: sparrow.runtime_origin[2],
});
assert.deepEqual(venues.destination("sparrow").location, sparrow.destination);

const players = Array.from({ length: 4 }, (_, index) => ({
  id: `p${index}`,
  teleports: [],
  teleport(location, options) { this.teleports.push({ location, options }); },
}));
world.getAllPlayers = () => players;
venues.start("ace", { players: players.map(player => player.id), state: { phase: "betting" } });
assert.ok(players.every(player => player.teleports.length === 1));
assert.ok(players.every(player => player.teleports[0].options.dimension === dimension));

properties.delete(VENUE_CONFIG.VENUE_INSTALL_KEY);
properties.delete(`${VENUE_CONFIG.VENUE_INSTALL_KEY}:sparrow`);
placements.length = 0;
specialCalls.length = 0;
const resumed = createProductionVenues({ world, system });
assert.equal(resumed.installAll(warp, specialVenues), true);
while (timers.length) timers.shift()();
assert.equal(placements.length, 0, "a partially placed venue must be recovered without overwriting its blocks");
assert.equal(properties.get(`${VENUE_CONFIG.VENUE_INSTALL_KEY}:sparrow`), 1);
assert.ok(specialCalls.includes("sparrow:provision"));

console.log("PASS original 17 venues: non-overlap, remote placement, resumable install, model kiosks, seats and protected warp hook");
