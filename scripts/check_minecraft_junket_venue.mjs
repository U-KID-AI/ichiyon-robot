// Focused checks for the generated special-venue manifest. This is an
// offline Script API mock, not a Bedrock client or visual integration test.
import assert from "node:assert/strict";
import { createVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue.js";
import { permanentFixtureLabel } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue_presentation.js";
import {
  fixtureRoleTag, locationPoint, specialVenue,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/special_venue.js";

const ID = "sparrow";
const definition = specialVenue(ID);
const properties = new Map();
const entities = [];

function makeEntity(typeId, location, tags = [], dynamic = {}) {
  const entityTags = new Set(tags);
  const entityProperties = new Map(Object.entries(dynamic));
  return {
    typeId,
    getComponent: name => name === "minecraft:health" ? { currentValue: 20, effectiveMax: 20 } : undefined,
    location: { ...location },
    removed: false,
    nameTag: "",
    dimension: { id: "minecraft:overworld" },
    addTag(tag) { entityTags.add(tag); },
    hasTag(tag) { return entityTags.has(tag); },
    getTags() { return [...entityTags]; },
    setDynamicProperty(name, value) { entityProperties.set(name, value); },
    getDynamicProperty(name) { return entityProperties.get(name); },
    teleport(next) { this.location = { ...next }; },
    remove() { this.removed = true; },
  };
}

const center = {
  x: definition.origin[0] + definition.size[0] / 2,
  y: definition.origin[1] + definition.size[1] / 2,
  z: definition.origin[2] + definition.size[2] / 2,
};
const dimension = {
  id: "minecraft:overworld",
  getEntities({ location, maxDistance, tags = [] } = {}) {
    return entities.filter(entity => !entity.removed && tags.every(tag => entity.hasTag(tag)) &&
      (!location || Math.hypot(entity.location.x - location.x, entity.location.y - location.y,
        entity.location.z - location.z) <= maxDistance));
  },
  spawnEntity(typeId, location) {
    const entity = makeEntity(typeId, location);
    entities.push(entity);
    return entity;
  },
};
const world = {
  getDimension: id => {
    assert.equal(id, "minecraft:overworld");
    return dimension;
  },
  getDynamicProperty: key => properties.get(key),
  setDynamicProperty: (key, value) => properties.set(key, value),
  getAllPlayers: () => [],
};
const system = { runTimeout: callback => { callback(); return 0; } };
const venue = createVenue({ world, system });
const player = {
  id: "A",
  name: "A",
  location: { x: 0, y: 0, z: 0 },
  dimension,
  teleports: [],
  teleport(location, options) { this.teleports.push({ location, options }); },
};
const roleEntity = role => entities.find(entity => !entity.removed &&
  entity.hasTag(fixtureRoleTag(ID, role)));

await assert.rejects(venue.install(player, "crow-hotel"), /旧設置経路/,
  "the retired world-copy path must remain disabled");
assert.equal(entities.length, 0, "a rejected legacy install must not spawn fixtures");

await venue.install(player, "hall");
assert.equal(entities.length, definition.fixtures.length,
  "all manifest fixtures must exist after install");
assert.deepEqual(venue.config().origin, {
  x: definition.origin[0], y: definition.origin[1], z: definition.origin[2],
});
for (const expected of definition.fixtures) {
  const entity = roleEntity(expected.role);
  assert.ok(entity, `missing fixture ${expected.role}`);
  assert.equal(entity.typeId, expected.type, `wrong type for ${expected.role}`);
  assert.deepEqual(entity.location, locationPoint(expected.location),
    `wrong location for ${expected.role}`);
  assert.equal(entity.getDynamicProperty(definition.provider.role_property), expected.role);
  assert.equal(entity.nameTag, permanentFixtureLabel(definition, expected), `wrong label for ${expected.role}`);
  for (const tag of [definition.provider.collection, ...(expected.tags ?? [])]) {
    assert.equal(entity.hasTag(tag), true, `${expected.role} lacks ${tag}`);
  }
}
assert.equal(venue.verify(), true);
assert.equal(venue.boxValue(roleEntity("bigbox")), "big");
assert.equal(venue.boxValue(roleEntity("littlebox")), "small",
  "small box must expose the value consumed by the physical-click runtime");

const unrelated = makeEntity("minecraft:cow", { ...center }, ["world:existing"]);
entities.push(unrelated);
await venue.install(player, "hall");
assert.equal(entities.filter(entity => !entity.removed).length, definition.fixtures.length + 1,
  "a second install must be verification-only and idempotent");
assert.equal(unrelated.removed, false, "unmanaged entities must never be deleted");

const reception = roleEntity("reception");
const roleProperty = definition.provider.role_property;
reception.setDynamicProperty(roleProperty, undefined);
venue.ensure();
assert.equal(reception.getDynamicProperty(roleProperty), "reception",
  "ensure must restore missing role metadata on an existing fixture");
assert.equal(venue.verify(), true);

const tv = roleEntity("tv");
const expectedTv = definition.fixtures.find(entry => entry.role === "tv");
tv.location = { ...locationPoint(expectedTv.location), x: locationPoint(expectedTv.location).x + 3 };
venue.ensure();
assert.equal(roleEntity("tv"), tv, "a live displaced fixture must retain its identity");
assert.deepEqual(tv.location, locationPoint(expectedTv.location), "restore its manifest position");
assert.equal(tv.removed, false);
assert.equal(venue.verify(), true);

const duplicate = makeEntity("minecraft:npc", locationPoint(expectedTv.location),
  [definition.provider.collection, fixtureRoleTag(ID, "tv")], { [roleProperty]: "tv" });
entities.push(duplicate);
venue.ensure();
assert.equal(entities.filter(entity => !entity.removed && entity.hasTag(fixtureRoleTag(ID, "tv"))).length, 1,
  "duplicate role fixtures must collapse to one");
assert.equal(venue.verify(), true);

const obsolete = makeEntity("junket:obsolete", { ...center },
  [definition.provider.collection, fixtureRoleTag(ID, "obsolete")], {
    [roleProperty]: "obsolete", "junket:venue_id": ID,
  });
entities.push(obsolete);
venue.ensure();
assert.equal(obsolete.removed, true, "obsolete managed fixtures must be removed");
assert.equal(unrelated.removed, false);

const missing = roleEntity("diamond");
missing.remove();
assert.throws(() => venue.verify(), /diamond/,
  "verify must reject a stale complete marker or missing fixture");
venue.ensure();
assert.ok(roleEntity("diamond"), "ensure must recreate a missing fixture");
assert.equal(venue.verify(), true);

venue.go(player);
assert.deepEqual(player.teleports.at(-1).location, definition.destination);
assert.equal(player.teleports.at(-1).options.rotation.y, definition.rotation ?? 0);
assert.equal(player.teleports.at(-1).options.dimension, dimension);

console.log("PASS manifest fixture locations, labels, tags, box values, idempotence, repair, isolation, destination");
