import assert from "node:assert/strict";
import { installFixtureActorTracking, markCreatedFixture, observeExistingFixture,
  preferredFixture, waitForFixtureActors } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/fixture_reconciliation.js";
import { VENUE_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_venue_generated.js";
import { ensureManifestFixtures, specialVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/special_venue.js";
import { createProductionVenues } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/production_venue.js";
import { registerOriginalVenues } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/original_venue.js";

let cases = 0;
for (const visibleAt of [0, 6, 24, 38]) {
  let tick = 0;
  const result = await waitForFixtureActors({ read: () => tick < visibleAt ? [] : ["saved"],
    wait: async step => { tick += step; } });
  assert.equal(tick, visibleAt + 2, "complete actors need only one stable engine tick pair");
  assert.equal(result.visible_actors, 1); cases++;
}
{
  let tick = 0;
  const result = await waitForFixtureActors({ expectedCount: 6,
    read: () => tick < 24 ? ["one"] : ["one", "two", "three", "four", "five", "six"],
    wait: async step => { tick += step; } });
  assert.equal(tick, 26); assert.equal(result.visible_actors, 6); cases++;
}
{
  let tick = 0;
  const result = await waitForFixtureActors({ read: () => [], wait: async step => { tick += step; } });
  assert.equal(tick, 40); assert.equal(result.visible_actors, 0); cases++;
}
{
  let tick = 0;
  await assert.rejects(waitForFixtureActors({ read: () => [String(tick)],
    wait: async step => { tick += step; }, timeoutTicks: 12 }), /読込が安定しません/);
  assert.equal(tick, 12); cases++;
  await assert.rejects(waitForFixtureActors({ read() { throw new Error("native query unavailable"); },
    wait: async () => { throw new Error("must not wait"); } }), /native query unavailable/); cases++;
}
{
  const saved = observeExistingFixture({ id: "saved", typeId: "junket:guide", correct: false });
  const replacement = markCreatedFixture({ id: "replacement", typeId: "junket:guide", correct: true });
  assert.equal(preferredFixture([replacement, saved], "junket:guide", row => row.correct), saved);
  assert.equal(preferredFixture([saved, replacement], "junket:guide", row => row.correct), saved); cases++;
}

let environmentSequence = 0;
function environment() {
  const environmentId = ++environmentSequence;
  const actors = [], signals = {}, properties = new Map();
  let sequence = 0, revealSaved = false;
  const dimension = { id: "minecraft:overworld", getBlock: () => ({ typeId: "minecraft:stone" }),
    getEntities({ tags = [] } = {}) {
      return actors.filter(actor => !actor.removed && (!actor.saved || revealSaved) && tags.every(tag => actor.hasTag(tag)))
        .sort((a, b) => Number(Boolean(a.saved)) - Number(Boolean(b.saved))); // BDS new-before-saved order.
    },
    spawnEntity(typeId, location) {
      const tags = new Set(), props = new Map();
      const entity = { id: `test-${environmentId}-${++sequence}`, typeId, location: { ...location }, dimension,
        hasTag: tag => tags.has(tag), getTags: () => [...tags], addTag: tag => tags.add(tag),
        getDynamicProperty: key => props.get(key), setDynamicProperty: (key, value) => props.set(key, value),
        getComponent: () => ({ currentValue: 20, effectiveMax: 20 }),
        setProperty() {}, teleport(at) { this.location = { ...at }; }, clearVelocity() {},
        remove() { this.removed = true; } };
      actors.push(entity); return entity;
    } };
  const world = { getDimension: () => dimension, getAllPlayers: () => [],
    getDynamicProperty: key => properties.get(key), setDynamicProperty: (key, value) => properties.set(key, value),
    afterEvents: Object.fromEntries(["entityLoad", "entitySpawn"].map(name =>
      [name, { subscribe(callback) { signals[name] = callback; } }])) };
  installFixtureActorTracking(world);
  return { actors, world, dimension, system: { runTimeout: callback => setImmediate(callback) },
    reveal() { revealSaved = true; for (const entity of actors.filter(actor => actor.saved)) signals.entityLoad({ entity }); },
    seed(venue) {
      return venue.fixtures.map(expected => {
        const entity = dimension.spawnEntity(expected.type, { x: expected.location[0], y: expected.location[1], z: expected.location[2] });
        entity.saved = true;
        entity.addTag(venue.provider?.collection ?? `junket:${venue.id}:managed`);
        entity.addTag(`junket:${venue.id}:fixture:${expected.role}`);
        entity.setDynamicProperty("junket:venue_id", venue.id);
        entity.setDynamicProperty(venue.provider?.role_property ?? "junket:venue_role", expected.role);
        return entity;
      });
    } };
}
for (const id of ["sparrow", "ace"]) {
  const env = environment();
  const source = id === "sparrow" ? specialVenue(id) : VENUE_CONFIG.VENUES.find(row => row.id === id);
  const venue = { ...source, source: "synthetic actor race fixture", distribution: "private-local-only",
    fixtures: source.fixtures.map(fixture => ({ ...fixture,
      type: fixture.type === "minecraft:npc" ? "junket:guide" : fixture.type })) };
  registerOriginalVenues([venue]);
  const saved = env.seed(venue);
  const runtime = createProductionVenues({ ...env, venues: [venue] });
  const ensure = () => id === "sparrow" ? ensureManifestFixtures(env.world, id) : runtime.ensure(id);
  await ensure();
  const replacement = env.actors.filter(actor => !actor.saved);
  assert.equal(replacement.length, venue.fixtures.length, "reproduce initial empty actor query");
  env.reveal();
  assert.equal(env.dimension.getEntities({})[0].saved, undefined, "premature replacement appears before saved actor");
  await ensure();
  for (const entity of saved) assert.equal(entity.removed, undefined, `${id}: original actor retained`);
  for (const entity of replacement) assert.equal(entity.removed, true, `${id}: premature replacement removed`);
  await ensure();
  assert.equal(env.actors.filter(actor => !actor.removed).length, saved.length, `${id}: repeated repair stays idempotent`);
  cases++;
}
console.log(`PASS fixture startup reconciliation: ${cases} cases; delayed read-only observation, existing IDs preferred in special/generic repair, bounded timeout, no duplicate growth (offline)`);
