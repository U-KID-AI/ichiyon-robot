import { createServiceControls } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/service_controls.js";
import { inspectLegacyServiceControls } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/service_control_install.js";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { VENUE_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_venue_generated.js";
import { createProductionVenues } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/production_venue.js";
import { registerOriginalVenues, translateVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/original_venue.js";
import { locationPoint, setVisualFrame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/special_venue.js";
import { createVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue.js";
import { createTemperanceVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/temperance_venue.js";
import { createLucyVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/lucy_venue.js";
import { createSilenceVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/silence_venue.js";
import { createJinnVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/jinn_venue.js";
import { COMPLETE_CONTROL_SPECS } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/complete_runtime.js";

const properties = new Map(), entities = [], commands = [], placements = [];
let packEntityTypes = null;
let packEntityProperties = null;
const block = { typeId: "minecraft:stone", isAir: false };
const dimension = {
  id: "minecraft:overworld", getBlock: () => block,
  runCommand(command) { commands.push(command); return { successCount: 1 }; },
  getEntities({ tags = [] }) { return entities.filter(e => !e.removed && tags.every(t => e.hasTag(t))); },
  spawnEntity(typeId, location) {
    if (packEntityTypes && !typeId.startsWith("minecraft:")) {
      assert.ok(packEntityTypes.has(typeId), `spawnEntity must resolve to final BP and RP: ${typeId}`);
    }
    const tags = new Set(), props = new Map(), visual = new Map();
    const entity = { typeId, location, nameTag: "", hasTag: t => tags.has(t), getTags: () => [...tags], addTag: t => tags.add(t),
      getComponent: name => name === "minecraft:health" ? { currentValue: 20, effectiveMax: 20 } : undefined,
      setDynamicProperty: (k, v) => props.set(k, v), getDynamicProperty: k => props.get(k),
      setProperty(k, v) {
        if (packEntityProperties) assert.ok(packEntityProperties.get(typeId)?.has(k), `${typeId}: undefined visual property ${k}`);
        this.frame = [k, v]; visual.set(k, v);
      }, getProperty: k => visual.get(k), remove() { this.removed = true; },
      teleport(at, options) { this.location = at; this.rotation = options.rotation; } };
    entities.push(entity); return entity;
  },
};
const world = { getDynamicProperty: k => properties.get(k), setDynamicProperty: (k, v) => properties.set(k, v),
  getDimension: () => dimension, getAllPlayers: () => [],
  structureManager: { place: (...args) => placements.push(args) } };
const system = { runTimeout: callback => setImmediate(callback) };
const generic = VENUE_CONFIG.VENUES.find(v => v.id === "ace");
const original = { ...translateVenue(generic, [22000, 80, 22000]),
  source: "private-source-proof", distribution: "private-local-only", installation: "native-preinstalled",
  layout: undefined, probes: [{ label: "floor", location: { x: 22001, y: 80, z: 22001 }, blocks: ["minecraft:stone"] }] };
const before = createProductionVenues({ world, system });
assert.equal(before.entries.find(v => v.id === "ace"), generic);
registerOriginalVenues([original]);
const runtime = createProductionVenues({ world, system });
assert.equal(runtime.entries.find(v => v.id === "ace"), original);
assert.equal(runtime.entries.find(v => v.id === "fire"), VENUE_CONFIG.VENUES.find(v => v.id === "fire"));
await runtime.repair("ace");
assert.equal(placements.length, 0, "native installed source building must never be replaced");
assert.equal(entities.length, original.fixtures.length);
await runtime.repair("ace");
assert.equal(entities.filter(e => !e.removed).length, original.fixtures.length, "fixtures remain idempotent");
const fixedFixture = original.fixtures.find(f => f.role.startsWith("control_"));
const fixedRole = `junket:ace:fixture:${fixedFixture.role}`;
const displaced = entities.find(e => !e.removed && e.hasTag(fixedRole));
displaced.location = { ...displaced.location, x: displaced.location.x + 3 };
const unrelated = dimension.spawnEntity(fixedFixture.type, { ...displaced.location });
unrelated.addTag(fixedRole); unrelated.addTag("junket:ace:control");
await runtime.repair("ace");
assert.equal(displaced.removed, undefined, "managed displaced fixed actor is retained");
assert.deepEqual(displaced.location, locationPoint(fixedFixture.location), "managed fixed actor is reanchored");
assert.equal(unrelated.removed, undefined, "unmanaged matching role actor is preserved");
assert.ok(commands.filter(command => command.startsWith("tickingarea add ")).every(command => command.endsWith(" true")),
  "temporary venue areas explicitly preload without connected players");
let interiorChecks = 0;
dimension.getBlock = at => at.x === 22032 && at.z === 22032 && ++interiorChecks < 3 ? undefined : block;
const wide = { ...original, size: [80, 14, 80],
  previous_managed_bounds: { origin: [22000, 80, 21984], size: [27, 14, 29] } };
await createProductionVenues({ world, system, venues: [wide] }).repair("ace");
assert.ok(interiorChecks >= 3, "native initialization waits for an interior chunk, not just hall corners");
assert.ok(commands.some(command => command.startsWith("tickingarea add 21984 80 21968 ")),
  "old managed footprint and one adjacent chunk are loaded while no players are present");
const fixtureCell = original.fixtures[0].location.map(Math.floor);
dimension.getBlock = at => [at.x, at.y, at.z].every((value, axis) => value === fixtureCell[axis]) ? undefined : block;
await assert.rejects(runtime.repair("ace"), /fixture reception/, "available chunk anchors cannot hide an unloaded fixture position");
dimension.getBlock = () => block;
block.typeId = "minecraft:dirt";
await assert.rejects(runtime.repair("ace"), /自動上書きせず/);
assert.equal(placements.length, 0);
assert.ok(commands.every(command => command.startsWith("tickingarea ") || command.startsWith("testforblock ")));
// Reproduce asynchronous area unloading: simultaneous repairs must share one
// queue and may not add an area until 40 ticks after a successful removal.
let loaderTick = 0, lastRemoval = -Infinity, loaderAdds = 0;
const preservedAreas = ["narita_probe", "oreka_fix", "koumou_fix", "narita_fix"];
const loaderAreas = new Set(preservedAreas);
const queuedDimension = { ...dimension, getBlock: () => ({ typeId: "minecraft:stone" }),
  runCommand(command) {
    const parts = command.split(" ");
    if (parts[1] === "remove") {
      assert.ok(!preservedAreas.includes(parts[2]), "existing server area must never be removed");
      if (!loaderAreas.delete(parts[2])) return { successCount: 0 };
      lastRemoval = loaderTick;
      return { successCount: 1 };
    }
    assert.equal(loaderAreas.size, preservedAreas.length, "repairs cannot operate two temporary areas concurrently");
    assert.ok(loaderTick - lastRemoval >= 40, "next area add waits for pending asynchronous unload");
    loaderAreas.add(parts.at(-2)); loaderAdds++;
    return { successCount: 1 };
  } };
const second = { ...original, id: "fire" };
const queued = createProductionVenues({ world: { ...world, getDimension: () => queuedDimension },
  system: { runTimeout: (callback, ticks = 0) => setImmediate(() => { loaderTick += ticks; callback(); }) },
  venues: [original, second] });
await Promise.all([queued.repair("ace"), queued.repair("fire")]);
assert.equal(loaderAdds, 2);
assert.deepEqual([...loaderAreas], preservedAreas);
assert.deepEqual(locationPoint({ x: 3, y: 4, z: 5 }), { x: 3, y: 4, z: 5 });
assert.deepEqual(locationPoint([3, 4, 5]), { x: 3, y: 4, z: 5 });
const source = VENUE_CONFIG.VENUES.find(v => v.id === "temperance");
const shifted = translateVenue(source, source.origin.map(v => v + 100));
assert.equal(shifted.points.balance_radius, source.points.balance_radius);
assert.equal(shifted.points.beam_origin.x, source.points.beam_origin.x + 100);
const rabbit = VENUE_CONFIG.VENUES.find(v => v.id === "rabbit");
const moved = translateVenue(rabbit, rabbit.origin.map(v => v + 10));
assert.equal(moved.rabbit.cells[0].tile, rabbit.rabbit.cells[0].tile);
assert.equal(moved.rabbit.cells[0].x, rabbit.rabbit.cells[0].x + 10);
assert.equal(moved.rabbit.floor_y, rabbit.rabbit.floor_y + 10);
assert.equal(moved.rabbit.cells[0].floor_cells[0][1], rabbit.rabbit.cells[0].floor_cells[0][1] + 10);
// Unmanaged auxiliary models may still supply an explicit dynamic mapping.
// Registered fixtures are tested separately and must prefer their manifest.
const converted = dimension.spawnEntity("junket:test_visual", { x: 0, y: 80, z: 0 });
converted.setDynamicProperty("junket:frame_property", "junket:lucy_frame");
converted.setDynamicProperty("junket:frame_map", JSON.stringify([0, 2, 4]));
setVisualFrame(converted, 2);
assert.deepEqual(converted.frame, ["junket:lucy_frame", 4]);
converted.setDynamicProperty("junket:frame_property", "none");
setVisualFrame(converted, 1);
assert.deepEqual(converted.frame, ["junket:lucy_frame", 4]);
setVisualFrame(converted, 1, "junket:frame", { frame_property: "junket:source_frame", frame_map: [3, 7] });
assert.deepEqual(converted.frame, ["junket:source_frame", 7], "immutable definition takes priority over stale actor metadata");
converted.setDynamicProperty("junket:frame_property", "junket:frame");
setVisualFrame(converted, 8, "junket:frame", { frame_property: "none" });
assert.deepEqual(converted.frame, ["junket:source_frame", 7], "static source definition skips writes even with stale metadata");
const entry = readFileSync(new URL("../minecraft/behavior_packs/ichiyon_junket_bp/scripts/all17_reference_main.js", import.meta.url), "utf8");
assert.match(entry, /world\.afterEvents\.worldLoad\.subscribe/);
assert.match(entry, /system\.runTimeout\(bootstrap, 40\)/, "headless hot reload has a deferred startup fallback");
assert.match(entry, /else if \(event.id === "junket:installall"\) void installVenues\(\)/,
  "manual retry produces the same complete JSON report as automatic startup");
assert.doesNotMatch(entry, /from ["']\.\/main\.js/);
const manifestIndex = process.argv.indexOf("--manifest");
if (manifestIndex >= 0) {
  const { ORIGINAL_VENUES: venues } = await import(pathToFileURL(process.argv[manifestIndex + 1]));
  const packsIndex = process.argv.indexOf("--packs");
  if (packsIndex >= 0) {
    const root = process.argv[packsIndex + 1];
    const definitions = (folder, key) => new Map(readdirSync(join(root, folder)).filter(name => name.endsWith(".json")).map(name => {
      const value = JSON.parse(readFileSync(join(root, folder, name), "utf8").replace(/^\uFEFF/, ""))[key].description;
      return [value.identifier, value];
    }));
    const bp = definitions("behavior_packs/ichiyon_junket_bp/entities", "minecraft:entity");
    const rp = definitions("resource_packs/ichiyon_junket_rp/entity", "minecraft:client_entity");
    packEntityTypes = new Set([...bp.keys()].filter(type => rp.has(type)));
    packEntityProperties = new Map([...bp].map(([id, value]) => [id, new Set(Object.keys(value.properties ?? {}))]));
    for (const venue of venues) for (const fixture of venue.fixtures) {
      if (fixture.type.startsWith("minecraft:")) continue;
      assert.ok(bp.has(fixture.type), `${venue.id}/${fixture.role}: entity ${fixture.type} exists in final BP`);
      assert.ok(rp.has(fixture.type), `${venue.id}/${fixture.role}: entity ${fixture.type} exists in final RP`);
      if (fixture.frame_property && fixture.frame_property !== "none") {
        assert.ok(bp.get(fixture.type).properties?.[fixture.frame_property],
          `${venue.id}/${fixture.role}: frame property ${fixture.frame_property} exists in final BP`);
      }
    }
    console.log(`PASS final BP/RP identifier and explicit frame-property resolution for ${venues.reduce((n, v) => n + v.fixtures.length, 0)} fixtures`);
  }
  assert.equal(venues.length, 17);
  registerOriginalVenues(venues);
  const probes = new Map(venues.flatMap(v => v.probes.map(p => [
    [p.location.x, p.location.y, p.location.z].join(","), p.blocks[0],
  ])));
  const blockStates = new Map(), signTexts = new Map();
  function sourceControls(venue) {
    for (const control of [...(venue.service_controls ?? []), ...(venue.legacy_service_controls ?? [])]) {
      for (const role of ["support", ...(control.sign_support ? ["sign_support"] : [])]) {
        probes.set(control[role].join(","), control[`${role}_block`].name);
        blockStates.set(control[role].join(","), { ...control[`${role}_block`].states });
      }
      for (const role of ["button", "sign"]) {
        const original = control.source_evidence[`${role}_original_block`] ?? control.source_evidence[`${role}_original`];
        probes.set(control[role].join(","), original.name ?? original);
        blockStates.set(control[role].join(","), { ...(original.states ?? {}) });
      }
    }
  }
  for (const venue of venues) sourceControls(venue);
  function legacyControls(venue) {
    for (const control of venue.legacy_service_controls ?? []) {
      for (const role of ["button", "sign"]) {
        const key = control[role].join(","), expected = control[`${role}_block`];
        probes.set(key, expected.name);
        blockStates.set(key, { ...expected.states });
      }
      signTexts.set(control.sign.join(","), control.text);
    }
  }
  // Upgrade the actual previous installation, not an already cleared source
  // world. Its old Ace repair sign occupies the new arrival/source probe.
  for (const venue of venues) legacyControls(venue);
  const serviceSignal = { subscribe() {} };
  world.beforeEvents = { playerInteractWithBlock: serviceSignal, playerInteractWithEntity: serviceSignal };
  world.afterEvents = { ...world.afterEvents, playerLeave: serviceSignal };
  createServiceControls({ world, system, venues, runtimes: new Map(venues.map(v => [v.id, { openReception() {} }])) }).install();
  dimension.getBlock = at => {
    const key = [at.x, at.y, at.z].join(",");
    return { get typeId() { return probes.get(key) ?? "minecraft:air"; },
      setType(type) { probes.set(key, type); blockStates.set(key, {}); signTexts.delete(key); },
      get permutation() { return { typeId: probes.get(key) ?? "minecraft:air", states: blockStates.get(key) ?? {},
        getAllStates() { return { ...this.states }; }, withState(name, value) { return { ...this, states: { ...this.states, [name]: value } }; } }; },
      setPermutation(value) { probes.set(key, value.type?.id ?? value.typeId); blockStates.set(key, value.states); },
      getComponent(name) { return name === "minecraft:sign" ? { getText: () => signTexts.get(key) ?? "",
        setText: value => signTexts.set(key, value), setWaxed() {} } : undefined; } };
  };
  const context = { world, system };
  const all = createProductionVenues({ ...context, venues });
  const special = { sparrow: createVenue(context), temperance: createTemperanceVenue(context),
    lucy: createLucyVenue(context), silence: createSilenceVenue(context), jinn: createJinnVenue(context) };
  for (const venue of venues) {
    assert.ok(venue.probes.length > 0, `${venue.id}: native source probes`);
    assert.ok(venue.loading_probes.length > 0, `${venue.id}: real source blocks for chunk availability`);
    for (const probe of venue.loading_probes) for (const candidate of probe.candidates) {
      assert.notEqual(candidate.block, "minecraft:air", `${venue.id}: source loading anchor is non-air`);
      assert.deepEqual([Math.floor(candidate.location.x / 16), Math.floor(candidate.location.z / 16)], probe.chunk,
        `${venue.id}: loading anchor belongs to the required chunk`);
    }
    assert.ok(venue.seats.length > 0, `${venue.id}: original player seats`);
    assert.equal(new Set(venue.fixtures.map(f => f.role)).size, venue.fixtures.length, `${venue.id}: unique roles`);
    for (const fixture of venue.fixtures) {
      assert.ok(fixture.location.every(Number.isFinite), `${venue.id}/${fixture.role}: finite position`);
      assert.ok(fixture.location.every((v, i) => v >= venue.origin[i] - 1 && v < venue.origin[i] + venue.size[i] + 1),
        `${venue.id}/${fixture.role}: source hall bounds`);
    }
    if (COMPLETE_CONTROL_SPECS[venue.id]) {
      const actions = new Set(venue.fixtures.map(f => f.action));
      for (const action of Object.keys(COMPLETE_CONTROL_SPECS[venue.id])) {
        assert.ok(actions.has(action), `${venue.id}/${action}: executable physical control`);
      }
    }
  }
  const report = await all.installAll({ installControls: async () => true }, special);
  assert.deepEqual(report.failures, []);
  assert.equal(report.completed.length, 17);
  for (const venue of venues) {
    assert.ok(inspectLegacyServiceControls(world, venue).every(row => ["restored", "protected"].includes(row.status)),
      `${venue.id}: strict legacy retirement completes before original layout verification`);
    assert.equal((await all.inspect(venue.id)).complete, true);
  }
  const legacyProbeConflicts = venues.flatMap(venue => (venue.legacy_service_controls ?? []).flatMap(control =>
    ["button", "sign"].flatMap(role => venue.probes
      .filter(probe => [probe.location.x, probe.location.y, probe.location.z].join(",") === control[role].join(","))
      .map(probe => ({ venue, control, role, probe })))));
  for (const { venue, control, role, probe } of legacyProbeConflicts) {
    for (const alteration of ["foreign-block", "foreign-sign-text"]) {
      legacyControls(venue);
      const key = control[role].join(",");
      if (alteration === "foreign-block") probes.set(key, "minecraft:diamond_block");
      else signTexts.set(control.sign.join(","), "unrelated replacement sign");
      const saved = [probes.get(key), signTexts.get(control.sign.join(","))];
      await assert.rejects(all.repair(venue.id), /原作建築を確認できません/, `${venue.id}: unknown legacy identity must not bypass probe`);
      assert.deepEqual([probes.get(key), signTexts.get(control.sign.join(","))], saved,
        "unknown block or edited sign remains untouched");
      assert.equal(probes.get(control.button.join(",")), control.button_block.name,
        "an unowned pair is retained in full");
      assert.equal(placements.length, 0, "ordinary boot never restores an entire native building");
      assert.ok(!probe.blocks.includes(probes.get(key)));
      legacyControls(venue);
      await all.repair(venue.id);
    }
  }
  console.log(`PASS all17 legacy service migration precedes source probes; ${legacyProbeConflicts.length} intersecting probes reject unknown blocks and changed signs`);
  const reception = entities.find(entity => !entity.removed && entity.hasTag("junket:sparrow:fixture:reception"));
  const pendingRemovals = [];
  const duplicates = Array.from({ length: 2 }, () => {
    const entity = dimension.spawnEntity(reception.typeId, { ...reception.location });
    for (const tag of reception.getTags()) entity.addTag(tag);
    entity.setDynamicProperty("junket:sparrow_role", "reception");
    entity.remove = () => { if (!pendingRemovals.includes(entity)) pendingRemovals.push(entity); };
    return entity;
  });
  const originalTimeout = system.runTimeout;
  let removalApplied = false;
  system.runTimeout = (callback, ticks) => originalTimeout(() => {
    if (ticks > 0 && pendingRemovals.length) {
      removalApplied = true;
      for (const entity of pendingRemovals.splice(0)) entity.removed = true;
    }
    callback();
  }, ticks);
  try { await all.repair("sparrow"); } finally { system.runTimeout = originalTimeout; }
  assert.equal(removalApplied, true, "special ensure must yield a tick before strict verification");
  assert.ok(duplicates.every(entity => entity.removed), "delayed managed duplicate removals settle");
  assert.equal(reception.removed, undefined, "retained reception is preserved");
  assert.equal(special.sparrow.verify(), true, "exactly one reception is required after the settle wait");
  assert.equal(placements.length, 0, "all17 native fixture initialization preserves every source building");
  assert.equal(venues.find(v => v.id === "hope").hope_pads.length, 16);
  assert.equal(venues.find(v => v.id === "clock").drop_areas.length, 12);
  assert.equal(venues.find(v => v.id === "walker").walker.cells.length, 37);
  assert.equal(venues.find(v => v.id === "rabbit").rabbit.cells.length, 60);
  if (venues.every(venue => venue.repair_structures?.length)) {
    world.structureManager.getPackStructureIds = () => venues.flatMap(venue => venue.repair_structures.map(tile => tile.id));
    world.structureManager.place = (id, target, origin, options) => {
      const venue = venues.find(value => value.repair_structures.some(tile => tile.id === id));
      const tile = venue?.repair_structures.find(value => value.id === id);
      assert.ok(tile, "full repair must place the registered private source tile");
      assert.equal(target, dimension);
      assert.deepEqual(origin, Object.fromEntries(["x", "y", "z"].map((axis, i) => [axis, venue.origin[i] + tile.offset[i]])));
      assert.equal(options.includeEntities, false);
      assert.equal(options.includeBlocks, true);
      placements.push({ id, venue: venue.id, origin });
      for (const probe of venue.probes) probes.set([probe.location.x, probe.location.y, probe.location.z].join(","), probe.blocks[0]);
      sourceControls(venue);
    };
    probes.set("0,80,0", "minecraft:diamond_block");
    for (const venue of venues) {
      const before = placements.length;
      let frameDuplicate;
      if (["fire", "pig"].includes(venue.id)) {
        const definition = venue.fixtures.find(fixture => fixture.role === "control_1");
        assert.equal(definition.frame_property, "none", "original card texture is static");
        const retained = entities.find(entity => !entity.removed && entity.typeId === definition.type &&
          entity.hasTag(`junket:${venue.id}:fixture:control_1`) &&
          [entity.location.x, entity.location.y, entity.location.z].every((value, axis) => value === definition.location[axis]));
        frameDuplicate = dimension.spawnEntity(definition.type, { ...retained.location });
        for (const tag of retained.getTags()) frameDuplicate.addTag(tag);
        frameDuplicate.setDynamicProperty("junket:venue_id", venue.id);
        // Entity query order is not stable. A newly spawned damaged duplicate
        // can be selected before ensure reapplies its manifest frame metadata.
        entities.splice(entities.indexOf(frameDuplicate), 1);
        entities.unshift(frameDuplicate);
        frameDuplicate.setProperty = (name) => { throw new Error(`${definition.type}: undefined visual property ${name}`); };
        assert.equal(frameDuplicate.getDynamicProperty("junket:frame_property"), undefined);
        assert.doesNotThrow(() => all.update(venue.id, null), `${venue.id}: idle update must respect static manifest before repair`);
      }
      const broken = venue.probes.find(probe => probe.blocks[0] !== "minecraft:air") ?? venue.probes[0];
      probes.set([broken.location.x, broken.location.y, broken.location.z].join(","), "minecraft:air");
      const result = await all.restoreOriginal(venue.id);
      assert.equal(result.restored, true);
      assert.equal(placements.length - before, venue.repair_structures.length);
      assert.ok(placements.slice(before).every(value => value.venue === venue.id));
      assert.equal(probes.get("0,80,0"), "minecraft:diamond_block", "unrelated building stays intact");
      if (frameDuplicate && !frameDuplicate.removed) assert.equal(frameDuplicate.getDynamicProperty("junket:frame_property"), "none");
      if (frameDuplicate) assert.equal(entities.filter(entity => !entity.removed && entity.typeId === frameDuplicate.typeId && entity.hasTag(`junket:${venue.id}:fixture:control_1`)).length, 1,
        `${venue.id}: recovery resolves metadata-free duplicates without frame writes`);
      for (const control of venue.service_controls ?? []) {
        assert.equal(probes.get(control.support.join(",")), control.support_block.name);
        assert.equal(probes.get(control.button.join(",")), control.button_block.name);
        assert.equal(signTexts.get(control.sign.join(",")), control.text);
      }
      await all.restoreOriginal(venue.id);
      assert.equal((await all.inspect(venue.id)).complete, true, "repeated source recovery remains complete");
    }
    const tile = venues[0].repair_structures[0], originalOffset = tile.offset;
    const writes = placements.length;
    tile.offset = [venues[0].size[0], 0, 0];
    assert.throws(() => all.restoreOriginal(venues[0].id), /範囲/);
    tile.offset = originalOffset;
    assert.equal(placements.length, writes, "out-of-bounds source tile is rejected before mutation");
    console.log("PASS all17 full private-source recovery, declared bounds, service controls, unrelated terrain and repeated restore");
  }
  console.log(`PASS private original manifest: 17 native fixture installations, ${venues.reduce((n,v) => n+v.fixtures.length,0)} fixtures, every physical action, 16/12/37/60 source floor cells`);
}
console.log("PASS original manifest override, native terrain preservation, fixture recovery, source frame mapping, coordinate translation and delayed all17 entry (offline, not Bedrock playthrough)");
