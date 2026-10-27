import { createServiceControls } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/service_controls.js";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { registerOriginalVenues } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/original_venue.js";
import { createProductionVenues } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/production_venue.js";
import { createVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue.js";
import { createTemperanceVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/temperance_venue.js";
import { createLucyVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/lucy_venue.js";
import { createSilenceVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/silence_venue.js";
import { createJinnVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/jinn_venue.js";
import { fixture, ensureManifestFixtures, setVisualFrame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/special_venue.js";
import { makeQualityCase, QUALITY_GAMES } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/quality_cases.js";
import { permanentFixtureLabel } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue_presentation.js";
import { ensureFixedFixtureHealth, fixtureHealthRecoveryCount, fixtureUsesGameplayHealth } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/fixture_health.js";

const index = process.argv.indexOf("--manifest");
if (index < 0) {
  console.log("SKIP private lifecycle integration: supply --manifest explicitly; no private input is bundled");
  process.exit(0);
}
const { ORIGINAL_VENUES: entries } = await import(pathToFileURL(process.argv[index + 1]));
registerOriginalVenues(entries);
const packsIndex = process.argv.indexOf("--packs");
const entityDefinitions = packsIndex < 0 ? null : new Map(readdirSync(join(process.argv[packsIndex + 1], "behavior_packs/ichiyon_junket_bp/entities"))
  .filter(name => name.endsWith(".json")).map(name => {
    const entity = JSON.parse(readFileSync(join(process.argv[packsIndex + 1], "behavior_packs/ichiyon_junket_bp/entities", name), "utf8"))["minecraft:entity"].description;
    return [entity.identifier, entity.properties ?? {}];
  }));
const actors = [], blocks = new Map(), dynamic = new Map();
const blockStates = new Map(), signTexts = new Map();
let players = [], nextId = 1, failedQuery = false;
const blockKey = at => `${Math.floor(at.x)},${Math.floor(at.y)},${Math.floor(at.z)}`;
const dimension = { id: "minecraft:overworld",
  getBlock(at) { const key = blockKey(at); return { get typeId() { return blocks.get(key) ?? "minecraft:air"; },
    get permutation() { const typeId = blocks.get(key) ?? "minecraft:air", states = blockStates.get(key) ?? {};
      return { typeId, states, getAllStates() { return { ...this.states }; }, withState(name, value) { return { ...this, states: { ...this.states, [name]: value } }; } }; },
    setType(type) { blocks.set(key, type); }, setPermutation(value) { blocks.set(key, value.type?.id ?? value.typeId); blockStates.set(key, value.states ?? {}); },
    getComponent(name) { return name === "minecraft:sign" ? { getText: () => signTexts.get(key) ?? "",
      setText: value => signTexts.set(key, value), setWaxed() {} } : undefined; } }; },
  getEntities({ tags = [], location, maxDistance = Infinity } = {}) {
    if (failedQuery) throw new Error("simulated native entity-query unavailable");
    return actors.filter(entity => !entity.removed && tags.every(tag => entity.hasTag(tag)) && (!location ||
      Math.hypot(entity.location.x-location.x, entity.location.y-location.y, entity.location.z-location.z) <= maxDistance));
  },
  runCommand() { return { successCount: 1 }; }, spawnParticle() {},
  spawnEntity(typeId, at) {
    const tags = new Set(), props = new Map(), frames = new Map();
    const entity = { id: `e${nextId++}`, typeId, dimension, location: { ...at }, nameTag: "", removed: false,
      health: typeId.endsWith(":hope_code") ? 3 : 100, hasTag: tag => tags.has(tag), getTags: () => [...tags], addTag: tag => tags.add(tag), removeTag: tag => tags.delete(tag),
      getDynamicProperty: key => props.get(key), setDynamicProperty: (key, value) => props.set(key, value),
      setProperty(key, value) {
        if (entityDefinitions) {
          const definition = entityDefinitions.get(typeId)?.[key];
          assert.ok(definition, `${typeId}: property ${key} is not declared in the final BP`);
          if (definition.range) assert.ok(value >= definition.range[0] && value <= definition.range[1], `${typeId}/${key}: valid source frame range`);
        }
        frames.set(key, value);
      }, getProperty: key => frames.get(key),
      teleport(point, options = {}) { this.location = { ...point }; this.rotation = options.rotation; },
      setRotation(rotation) { this.rotation = { ...rotation }; },
      remove() { this.removed = true; }, clearVelocity() {},
      getComponent(name) { return name === "minecraft:health" ? { currentValue: entity.health,
        effectiveMax: typeId.endsWith(":hope_code") ? 3 : 100,
        setCurrentValue(value) { entity.health = value; }, resetToMaxValue() { entity.health = 100; } } : undefined; },
      applyImpulse() {}, runCommand() { return { successCount: 1 }; } };
    actors.push(entity); return entity;
  } };
const world = { getDimension: () => dimension, getAllPlayers: () => players,
  getDynamicProperty: key => dynamic.get(key), setDynamicProperty: (key, value) => dynamic.set(key, value),
  afterEvents: { entityDie: { subscribe() {} }, entityHurt: { subscribe() {} } },
  structureManager: { place(id, target, at, options) {
    assert.match(id, /^junket:temperance_beam_\d+$/, "only the declared moving beam may change");
    assert.equal(options.includeEntities, false);
  } } };
const system = { runTimeout: callback => setImmediate(callback), run: callback => setImmediate(callback) };
for (const venue of entries) for (const probe of venue.probes) blocks.set(blockKey(probe.location), probe.blocks[0]);
for (const venue of entries) for (const control of [...(venue.service_controls ?? []), ...(venue.legacy_service_controls ?? [])]) {
  for (const role of ["support", ...(control.sign_support ? ["sign_support"] : [])]) {
    blocks.set(control[role].join(","), control[`${role}_block`].name);
    blockStates.set(control[role].join(","), { ...control[`${role}_block`].states });
  }
  for (const role of ["button", "sign"]) {
    const original = control.source_evidence[`${role}_original_block`] ?? control.source_evidence[`${role}_original`];
    blocks.set(control[role].join(","), original.name ?? original);
    blockStates.set(control[role].join(","), { ...(original.states ?? {}) });
  }
}
const serviceSignal = { subscribe() {} };
world.beforeEvents = { playerInteractWithBlock: serviceSignal, playerInteractWithEntity: serviceSignal };
world.afterEvents.playerLeave = serviceSignal;
createServiceControls({ world, system, venues: entries, runtimes: new Map(entries.map(v => [v.id, { openReception() {} }])) }).install();
const context = { world, system };
const venues = createProductionVenues({ ...context, venues: entries });
const special = { sparrow: createVenue(context), temperance: createTemperanceVenue(context),
  lucy: createLucyVenue(context), silence: createSilenceVenue(context), jinn: createJinnVenue(context) };
venues.setSpecialVenues(special);
const actualFixture = (venue, definition) => {
  const found = actors.filter(entity => !entity.removed && entity.hasTag(`junket:${venue.id}:fixture:${definition.role}`) &&
    entity.getDynamicProperty("junket:venue_id") === venue.id);
  assert.equal(found.length, 1, `${venue.id}/${definition.role}: unique managed fixed actor`);
  return found[0];
};
for (const venue of entries) {
  const sample = makeQualityCase(venue.id, venue.id);
  assert.equal(QUALITY_GAMES.length, 17);
  const sentinel = dimension.spawnEntity("minecraft:armor_stand", venue.destination);
  sentinel.addTag(`junket:${venue.id}:fixture:quality_sentinel`);
  sentinel.health = 0;
  const remote = dimension.spawnEntity("minecraft:armor_stand", { x: venue.origin[0]-20, y: venue.origin[1], z: venue.origin[2] });
  remote.addTag(`junket:${venue.id}:managed`); remote.setDynamicProperty("junket:venue_id", venue.id);
  remote.setDynamicProperty("junket:venue_role", "quality_sentinel");
  remote.health = 0;
  await venues.repair(venue.id);
  // Emulate persisted actors from a pack that had no explicit health. A
  // native dying actor is migrated before the lifecycle baseline; a damaged
  // but live fixed actor is healed without replacement.
  const healthFixtures = venue.fixtures.filter(definition => !definition.type.startsWith("minecraft:") &&
    !fixtureUsesGameplayHealth(venue, definition));
  const dead = actualFixture(venue, healthFixtures[0]);
  const hurt = actualFixture(venue, healthFixtures[1]);
  dead.health = 0; hurt.health = 12;
  const priorRecoveries = fixtureHealthRecoveryCount(venue.id);
  await venues.repair(venue.id);
  assert.equal(dead.removed, true, `${venue.id}: migrate only the owned dead fixed actor`);
  assert.notEqual(actualFixture(venue, healthFixtures[0]).id, dead.id);
  assert.equal(actualFixture(venue, healthFixtures[1]).id, hurt.id, `${venue.id}: heal live actor in place`);
  assert.equal(fixtureHealthRecoveryCount(venue.id), priorRecoveries + 2);
  const baselineRecoveries = fixtureHealthRecoveryCount(venue.id);
  const ids = new Map(venue.fixtures.map(definition => [definition.role, actualFixture(venue, definition).id]));
  const verify = stage => {
    for (const definition of venue.fixtures) {
      const entity = actualFixture(venue, definition);
      assert.equal(entity.id, ids.get(definition.role), `${venue.id}/${stage}/${definition.role}: identity retained`);
      assert.equal(entity.health, definition.type.endsWith(":hope_code") ? 3 : 100);
      assert.equal(entity.nameTag, permanentFixtureLabel(venue, definition), `${venue.id}/${stage}: permanent labels only`);
    }
    assert.equal(sentinel.removed, false, `${venue.id}/${stage}: unknown role survives`);
    assert.equal(remote.removed, false, `${venue.id}/${stage}: out-of-scope actor survives`);
    assert.equal(sentinel.health, 0, `${venue.id}/${stage}: unknown actor is not healed`);
    assert.equal(remote.health, 0, `${venue.id}/${stage}: out-of-scope actor is not healed`);
    assert.equal(fixtureHealthRecoveryCount(venue.id), baselineRecoveries, `${venue.id}/${stage}: no hidden recovery`);
  };
  verify("entry");
  players = sample.initial.players.map((id, seat) => ({ id, name: id, dimension,
    location: { ...venue.destination }, tags: new Set(), teleports: 0,
    teleport(at) { this.location = { ...at }; this.teleports++; }, sendMessage() {},
    hasTag(tag) { return this.tags.has(tag); }, addTag(tag) { this.tags.add(tag); }, removeTag(tag) { this.tags.delete(tag); },
    onScreenDisplay: { setTitle() {}, setActionBar() {} }, runCommand() { return { successCount: 1 }; },
    getRotation() { return { x: 0, y: 0 }; }, getComponent() {}, removeEffect() {}, addEffect() {} }));
  const observer = { id: "unrelated-observer", dimension, location: { ...venue.destination }, teleports: 0,
    teleport() { this.teleports++; }, hasTag: () => false, removeTag() {}, removeEffect() {}, addEffect() {}, runCommand() {} };
  players.push(observer);
  for (const repeat of [1, 2]) {
    if (special[venue.id]) await special[venue.id].start(sample.initial);
    else await venues.start(venue.id, sample.initial);
    verify(`start${repeat}`);
    if (venue.id === "sparrow") await special.sparrow.transition(sample.initial, sample.advanced);
    else if (special[venue.id]) special[venue.id].update(sample.advanced);
    else venues.update(venue.id, sample.advanced);
    verify(`round${repeat}`);
    if (special[venue.id]) special[venue.id].end(sample.advanced);
    else venues.end(venue.id, sample.advanced);
    verify(`end${repeat}`);
    await venues.repair(venue.id); verify(`reentry${repeat}`);
  }
  assert.equal(observer.teleports, 0, `${venue.id}: only participating IDs are moved`);
  players = [];
  verify("reinit");
  if (venue.id === "hope" && venue.fixtures.some(f => f.visual_source_role)) {
    const floorControls = venue.fixtures.filter(f => /^floor_\d+$/.test(f.action ?? ""));
    assert.equal(floorControls.length, 16);
    assert.equal(new Set(floorControls.map(f => f.visual_source_role)).size, 16);
    const originalIds = new Map(floorControls.map(control => {
      const definition = venue.fixtures.find(f => f.role === control.visual_source_role);
      assert.ok(definition?.source_tags.includes("hope-floor"));
      assert.equal(control.type, "junket:interaction_anchor");
      const actor = actualFixture(venue, definition);
      return [definition.role, actor.id];
    }));
    const symbols = floorControls.map(f => f.properties["junket:hope_symbol"]);
    const hazards = [symbols[0]];
    const drop = { ...sample.initial, state: { ...sample.initial.state, phase: "drop",
      floorOpen: true, padSymbols: symbols, hazards } };
    function verifyFloorPose(falling) {
      for (const control of floorControls) {
        const definition = venue.fixtures.find(f => f.role === control.visual_source_role);
        const visual = actualFixture(venue, definition), anchor = actualFixture(venue, control);
        assert.equal(visual.id, originalIds.get(definition.role), "source floor actor is retained");
        assert.equal(visual.rotation.y, falling && hazards.includes(control.properties["junket:hope_symbol"]) ? 15 : 0);
        assert.deepEqual([anchor.location.x, anchor.location.y, anchor.location.z], control.location,
          "transparent interaction position stays unchanged");
        assert.notEqual(anchor.rotation?.y, 15, "rotation belongs to the visible floor, not its invisible control");
        assert.equal(anchor.getDynamicProperty("junket:venue_action"), control.action);
      }
    }
    venues.update("hope", drop); verifyFloorPose(true);
    venues.update("hope", { ...drop, state: { ...drop.state, floorOpen: false } }); verifyFloorPose(false);
    venues.update("hope", drop); verifyFloorPose(true);
    venues.end("hope", { ...drop, players: [] }); verifyFloorPose(false);
    await venues.repair("hope"); verifyFloorPose(false);
    const resumed = createProductionVenues({ ...context, venues: entries });
    resumed.update("hope", drop); verifyFloorPose(true);
    resumed.end("hope", { ...drop, players: [] }); verifyFloorPose(false);
    console.log("PASS Hope source floor animation: 16 explicit visible roles, stable anchors/IDs, drop, next round, end, repair and runtime recreation");
  }
  if (special[venue.id]) {
    const duplicates = venue.fixtures.map(definition => {
      const original = actualFixture(venue, definition);
      const duplicate = dimension.spawnEntity(definition.type, { ...original.location });
      for (const tag of original.getTags()) duplicate.addTag(tag);
      duplicate.setDynamicProperty("junket:venue_id", venue.id);
      actors.splice(actors.indexOf(duplicate), 1);
      actors.unshift(duplicate); // Native queries may put the uninitialized actor first.
      const writes = [], set = duplicate.setProperty.bind(duplicate);
      duplicate.setProperty = (key, value) => { set(key, value); writes.push([key, value]); };
      return { definition, duplicate, writes };
    });
    await special[venue.id].end({ ...sample.advanced, players: [] });
    for (const { definition, duplicate, writes } of duplicates) {
      assert.equal(duplicate.getDynamicProperty("junket:frame_property"), undefined, "end does not need prior metadata repair");
      if (definition.frame_property === "none") assert.equal(writes.length, 0, `${venue.id}/${definition.role}: static source actor has no visual writes`);
      else {
        const property = definition.frame_property ?? "junket:frame";
        assert.ok(writes.every(([key]) => key === property), `${venue.id}/${definition.role}: end uses the source property`);
        // The shared resolver must also preserve nonzero source mappings when
        // only ownership and role tags survived, not merely skip bad writes.
        if (writes.length || definition.frame_property || entityDefinitions?.get(definition.type)?.[property]) {
          setVisualFrame(duplicate, 1);
          assert.deepEqual(writes.at(-1), [property, definition.frame_map?.[1] ?? 1]);
        }
      }
    }
    await venues.repair(venue.id);
    for (const definition of venue.fixtures) actualFixture(venue, definition);
    assert.equal(sentinel.removed, false);
    console.log(`PASS frame recovery ${venue.id}: metadata-free duplicates survive end and reconcile; static textures and dynamic source mappings remain correct`);
  }
  console.log(`PASS lifecycle ${venue.id}: stable actors, real rule transition, rematch, exit, reentry, labels, unrelated preservation`);
}
const before = actors.length;
failedQuery = true;
assert.throws(() => ensureManifestFixtures(world, "lucy"), /読み込めません/);
assert.equal(actors.length, before, "unreadable query cannot create duplicate Lucy fixtures");
failedQuery = false;
assert.ok(fixture(world, "lucy", "reception"));
const getBlock = dimension.getBlock;
dimension.getBlock = () => undefined;
assert.throws(() => ensureManifestFixtures(world, "lucy"), /チャンク未読込/);
assert.equal(actors.length, before, "unloaded Lucy fixtures cannot trigger removal or spawning");
dimension.getBlock = getBlock;
const hope = entries.find(value => value.id === "hope");
const hopeCode = hope.fixtures.find(value => value.role === "hope_code");
const code = actualFixture(hope, hopeCode);
code.health = 1;
assert.equal(ensureFixedFixtureHealth(hope, hopeCode, code, () => assert.fail("Hope game health must not respawn")), code);
assert.equal(code.health, 1, "generic fixed-actor migration cannot heal the destructible Hope code");
assert.throws(() => ensureFixedFixtureHealth({ id: "health-test" }, { role: "fixed", type: "junket:test" }, null,
  () => ({ id: "missing-health" })), /体力component/);
assert.throws(() => ensureFixedFixtureHealth({ id: "health-test" }, { role: "fixed", type: "junket:test" }, null,
  () => ({ id: "unhealthy-new", getComponent: () => ({ currentValue: 0, effectiveMax: 0, resetToMaxValue() {} }) })), /正値/);
const npc = { id: "native-npc", getComponent() {
  assert.fail("NPC health must not enter the FLT_MAX healing path");
} };
const reception = { role: "reception", type: "minecraft:npc" };
const npcRecoveries = fixtureHealthRecoveryCount("npc-test");
assert.equal(ensureFixedFixtureHealth({ id: "npc-test" }, reception, npc,
  () => assert.fail("live reception must keep its identity")), npc);
let npcSpawned = 0;
assert.equal(ensureFixedFixtureHealth({ id: "npc-test" }, reception, null,
  () => { npcSpawned++; return npc; }), npc);
assert.equal(npcSpawned, 1, "missing reception is installed once without healing");
assert.equal(fixtureHealthRecoveryCount("npc-test"), npcRecoveries);
console.log("PASS all17 offline lifecycle and isolated participant IDs; not a Bedrock client match");
