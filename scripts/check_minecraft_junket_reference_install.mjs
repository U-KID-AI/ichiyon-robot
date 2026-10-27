// Offline Script API regression checks. These prove installation/lifecycle
// contracts, not Bedrock rendering, physical reach, or a real two-client match.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  createOriginalVenueInstaller, originalVenue, registerOriginalVenues, translateVenue,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/original_venue.js";
import { SPARROW_REFERENCE } from "./fixtures/junket_sparrow_reference.mjs";
import { VENUE_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_venue_generated.js";
import {
  ensureManifestFixtures, fixture, specialVenue, verifyManifestFixtures,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/special_venue.js";

let checks = 0;
async function test(name, action) {
  await action();
  console.log(`OK ${++checks} ${name}`);
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const keyOf = location => [location.x, location.y, location.z].join(",");
const marker = id => `junket:original_installed_${id}`;

function environment(venue = SPARROW_REFERENCE, options = {}) {
  const blocks = new Map(), properties = new Map(), placements = [], waits = [], entities = [];
  let loaded = !options.unloaded;
  const dimension = {
    id: "minecraft:overworld",
    getBlock(location) {
      if (!loaded) {
        if (options.throwUnloaded) throw new Error("chunk unavailable");
        return undefined;
      }
      const key = keyOf(location), typeId = blocks.get(key) ?? "minecraft:air";
      return { typeId, isAir: typeId === "minecraft:air", setType: value => blocks.set(key, value) };
    },
    getEntities({ location, maxDistance = Infinity, tags = [] }) {
      return entities.filter(entity => !entity.removed && tags.every(tag => entity.hasTag(tag)) &&
        (!location || Math.hypot(entity.location.x - location.x, entity.location.y - location.y,
          entity.location.z - location.z) <= maxDistance));
    },
    spawnEntity(typeId, location) {
      const tags = new Set(), dynamic = new Map();
      const entity = {
        typeId, location: { ...location }, removed: false,
        getComponent: name => name === "minecraft:health" ? { currentValue: 20, effectiveMax: 20 } : undefined,
        hasTag: tag => tags.has(tag), getTags: () => [...tags], addTag: tag => tags.add(tag),
        setDynamicProperty: (key, value) => dynamic.set(key, value),
        getDynamicProperty: key => dynamic.get(key),
        teleport(at, options) { this.location = { ...at }; this.rotation = options?.rotation; },
        remove() { this.removed = true; },
      };
      entities.push(entity);
      return entity;
    },
  };
  const world = {
    getDimension(id) { assert.equal(id, dimension.id); return dimension; },
    getDynamicProperty: name => properties.get(name),
    setDynamicProperty: (name, value) => properties.set(name, value),
    structureManager: {
      getPackStructureIds: () => options.missingStructure ? [] : [venue.structure],
      place(id, target, location, settings) {
        assert.equal(target, dimension);
        placements.push({ id, location, settings });
        for (const probe of venue.probes) blocks.set(keyOf(probe.location), probe.blocks[0]);
      },
    },
  };
  const system = {
    runTimeout(callback, ticks) {
      waits.push(ticks);
      if (options.loadAfter === waits.length) loaded = true;
      setImmediate(callback);
      return waits.length;
    },
  };
  return { world, system, blocks, properties, placements, waits, entities, dimension };
}

await test("ordinary generated 17-game definitions remain the fallback", () => {
  assert.equal(VENUE_CONFIG.VENUES.length, 17);
  assert.equal(originalVenue("sparrow"), undefined);
  for (const venue of VENUE_CONFIG.VENUES.filter(entry => entry.special_runtime)) {
    assert.equal(specialVenue(venue.id), venue);
  }
});

await test("translation moves absolute coordinates but preserves local box offsets", () => {
  const snapshot = JSON.stringify(SPARROW_REFERENCE);
  const moved = translateVenue(SPARROW_REFERENCE, [100, 74, 100]);
  assert.deepEqual(moved.source_origin, [0, 64, 0]);
  assert.deepEqual(moved.origin, [100, 74, 100]);
  assert.deepEqual(moved.destination, { x: 102, y: 75, z: 102 });
  assert.deepEqual(moved.return_point, moved.destination);
  assert.deepEqual(moved.seats, [{ x: 104, y: 75, z: 106 }, { x: 107, y: 75, z: 106 }]);
  assert.deepEqual(moved.fixtures.find(value => value.role === "tv").location, [105, 79, 101]);
  assert.deepEqual(moved.points.partition, { x: 105, y: 76, z: 105 });
  assert.deepEqual(moved.points.box_pose[1], { x: 107, y: 76, z: 106 });
  assert.deepEqual(moved.points.box_offsets, SPARROW_REFERENCE.points.box_offsets);
  assert.deepEqual(moved.block_overlays[0].location, { x: 105, y: 75, z: 105 });
  assert.deepEqual(moved.probes[0].location, { x: 102, y: 74, z: 102 });
  assert.equal(JSON.stringify(SPARROW_REFERENCE), snapshot);
});

await test("original registration requires private distribution and only overrides its game", () => {
  assert.throws(() => registerOriginalVenues([{ ...SPARROW_REFERENCE, distribution: "public" }]), /不正/);
  registerOriginalVenues([translateVenue(SPARROW_REFERENCE)]);
  assert.equal(specialVenue("sparrow").source, SPARROW_REFERENCE.source);
  assert.equal(specialVenue("temperance"), VENUE_CONFIG.VENUES.find(entry => entry.id === "temperance"));
});

await test("empty reference world installs source hall and overlays once without source entities", async () => {
  const f = environment(), installer = createOriginalVenueInstaller(f);
  const [a, b] = await Promise.all([installer.install("sparrow"), installer.install("sparrow")]);
  assert.equal(a, b);
  assert.equal(f.placements.length, 1, "concurrent startup must share one placement");
  assert.deepEqual(f.placements[0], {
    id: "junket:sparrow_hall", location: { x: 0, y: 64, z: 0 }, settings: { includeEntities: false },
  });
  assert.equal(f.blocks.get("5,65,5"), "minecraft:green_carpet");
  assert.equal(f.properties.get(marker("sparrow")), SPARROW_REFERENCE.source);
  assert.equal(installer.installed("sparrow"), true);
  assert.equal(installer.verify(SPARROW_REFERENCE), true);
  f.blocks.set("1,64,1", "minecraft:diamond_block");
  await createOriginalVenueInstaller(f).install("sparrow");
  assert.equal(f.placements.length, 1, "persistent marker must prevent terrain replacement on restart");
  assert.equal(f.blocks.get("1,64,1"), "minecraft:diamond_block", "player edits must survive restart");
});

await test("interior occupied terrain is refused before any write and remains recoverable", async () => {
  const f = environment(), installer = createOriginalVenueInstaller(f);
  f.blocks.set("6,68,6", "minecraft:chest");
  await assert.rejects(installer.install("sparrow"), /6,68,6.*空気/);
  assert.equal(f.placements.length, 0);
  assert.equal(f.blocks.get("6,68,6"), "minecraft:chest");
  assert.equal(installer.installed("sparrow"), false);
  f.blocks.delete("6,68,6");
  await installer.install("sparrow");
  assert.equal(f.placements.length, 1, "failed pending job must be cleared for a retry");
});

await test("unloaded chunks are awaited and timeout never writes terrain", async () => {
  const f = environment(SPARROW_REFERENCE, { unloaded: true, throwUnloaded: true, loadAfter: 2 });
  await createOriginalVenueInstaller(f).install("sparrow");
  assert.deepEqual(f.waits.slice(0, 2), [10, 10]);
  assert.equal(f.placements.length, 1);
  const unavailable = environment(SPARROW_REFERENCE, { unloaded: true });
  await assert.rejects(createOriginalVenueInstaller(unavailable).install("sparrow"), /チャンク未読込/);
  assert.equal(unavailable.waits.length, 60);
  assert.equal(unavailable.placements.length, 0);
  assert.equal(unavailable.properties.size, 0);
});

await test("missing private structure refuses fallback to the independently generated hall", async () => {
  const f = environment(SPARROW_REFERENCE, { missingStructure: true });
  await assert.rejects(createOriginalVenueInstaller(f).install("sparrow"), /原作構造物.*ありません/);
  assert.equal(f.placements.length, 0);
  assert.equal(f.properties.size, 0);
});

await test("persisted marker still verifies original probes and accepts converted invisible light", async () => {
  const f = environment(), installer = createOriginalVenueInstaller(f);
  await installer.install("sparrow");
  f.blocks.set("2,65,2", "minecraft:light_block_12");
  f.blocks.set("2,66,2", "minecraft:light_block_12");
  await installer.install("sparrow");
  f.blocks.set("2,64,2", "minecraft:stone");
  await assert.rejects(installer.install("sparrow"), /floor/);
  assert.equal(f.placements.length, 1, "failed verification must not re-place the hall");
  assert.equal(f.blocks.get("2,64,2"), "minecraft:stone");
});

await test("interrupted overlay resumes after restart without placing the structure again", async () => {
  const f = environment();
  const getBlock = f.dimension.getBlock;
  let interrupted = false;
  f.dimension.getBlock = location => {
    const block = getBlock(location);
    if (keyOf(location) === "5,65,5" && !interrupted) {
      block.setType = () => { interrupted = true; throw new Error("simulated overlay interruption"); };
    }
    return block;
  };
  await assert.rejects(createOriginalVenueInstaller(f).install("sparrow"), /simulated overlay interruption/);
  assert.equal(f.properties.get(marker("sparrow")), `${SPARROW_REFERENCE.source}:placed`);
  assert.equal(f.placements.length, 1);
  f.blocks.set("1,64,1", "minecraft:diamond_block");
  await createOriginalVenueInstaller(f).install("sparrow");
  assert.equal(f.blocks.get("5,65,5"), "minecraft:green_carpet");
  assert.equal(f.properties.get(marker("sparrow")), SPARROW_REFERENCE.source);
  assert.equal(f.blocks.get("1,64,1"), "minecraft:diamond_block");
  assert.equal(f.placements.length, 1);
});

await test("partial-placement repair preserves any conflicting player block", async () => {
  const f = environment(), place = f.world.structureManager.place;
  f.world.structureManager.place = (...args) => {
    place(...args);
    f.blocks.set("5,65,5", "minecraft:chest");
  };
  const installer = createOriginalVenueInstaller(f);
  await assert.rejects(installer.install("sparrow"), /補助装飾.*保持/);
  assert.equal(f.properties.get(marker("sparrow")), `${SPARROW_REFERENCE.source}:placed`);
  await assert.rejects(installer.install("sparrow"), /補助装飾.*保持/);
  assert.equal(f.blocks.get("5,65,5"), "minecraft:chest");
  assert.equal(f.placements.length, 1);
  f.blocks.delete("5,65,5");
  await installer.install("sparrow");
  assert.equal(f.placements.length, 1);
  assert.equal(f.properties.get(marker("sparrow")), SPARROW_REFERENCE.source);
});

await test("six fixtures are idempotent and manifest monitor rotation is restored", () => {
  const f = environment();
  ensureManifestFixtures(f.world, "sparrow");
  assert.equal(verifyManifestFixtures(f.world, "sparrow"), true);
  assert.equal(f.entities.length, 6);
  assert.deepEqual(fixture(f.world, "sparrow", "tv").rotation, { x: 0, y: 180 });
  assert.equal(fixture(f.world, "sparrow", "bigbox").getDynamicProperty("junket:box"), "big");
  assert.equal(fixture(f.world, "sparrow", "littlebox").getDynamicProperty("junket:box"), "small");
  fixture(f.world, "sparrow", "tv").rotation = { x: 0, y: 0 };
  ensureManifestFixtures(f.world, "sparrow");
  assert.equal(f.entities.length, 6);
  assert.deepEqual(fixture(f.world, "sparrow", "tv").rotation, { x: 0, y: 180 });
  assert.equal(verifyManifestFixtures(f.world, "sparrow"), true);
});

await test("managed reception duplicates reconcile while an unrelated actor survives", () => {
  const f = environment();
  ensureManifestFixtures(f.world, "sparrow");
  const reception = fixture(f.world, "sparrow", "reception");
  const extras = Array.from({ length: 2 }, () => {
    const entity = f.dimension.spawnEntity(reception.typeId, reception.location);
    for (const tag of reception.getTags()) entity.addTag(tag);
    entity.setDynamicProperty("junket:sparrow_role", "reception");
    return entity;
  });
  const unknown = f.dimension.spawnEntity(reception.typeId, reception.location);
  unknown.addTag("unrelated:npc");
  ensureManifestFixtures(f.world, "sparrow");
  assert.equal(verifyManifestFixtures(f.world, "sparrow"), true);
  assert.ok(extras.every(entity => entity.removed));
  assert.equal(reception.removed, false); assert.equal(unknown.removed, false);
  assert.equal(unknown.getDynamicProperty("junket:sparrow_role"), undefined);
});

const entry = new URL("../minecraft/behavior_packs/ichiyon_junket_bp/scripts/sparrow_trial_main.js", import.meta.url);
const entrySource = readFileSync(entry, "utf8");
await test("trial module graph cannot start ordinary 17-game installation", () => {
  const seen = new Set();
  function inspect(url) {
    if (seen.has(url.href)) return;
    seen.add(url.href);
    const text = readFileSync(url, "utf8");
    assert.doesNotMatch(text, /\b(?:createProductionVenues|installAll)\s*\(/, fileURLToPath(url));
    for (const [, path] of text.matchAll(/^import\s+[\s\S]*?from\s+["'](\.[^"']+)["'];/gm)) {
      inspect(new URL(path, url));
    }
  }
  inspect(entry);
  const normal = readFileSync(new URL("main.js", entry), "utf8");
  assert.doesNotMatch(normal, /sparrow_trial_main|sparrow_reference|registerOriginalVenues|createOriginalVenueInstaller/);
  assert.match(normal, /productionVenues\.installAll/);
});

await test("trial entry waits for worldLoad, coalesces spawn, and gates runtime on hall checks", async () => {
  const handlers = {}, calls = [], messages = [];
  let finishInstall;
  const player = { id: "tester", typeId: "minecraft:player", sendMessage: value => messages.push(value) };
  const signal = name => ({ subscribe: fn => { handlers[name] = fn; } });
  const context = {
    world: {
      getAllPlayers: () => [player],
      afterEvents: { worldLoad: signal("worldLoad"), playerSpawn: signal("spawn"), playerLeave: signal("leave") },
    },
    system: { run: fn => fn(), afterEvents: { scriptEventReceive: signal("script") } },
    ActionFormData: class {}, ModalFormData: class {},
    GameManager: class {
      tables = new Map();
      constructor(games) { assert.equal(games.length, 1); calls.push("manager"); }
      restore() { calls.push("restore"); }
      view() { return { players: [] }; }
    },
    sparrowGame: { id: "sparrow" },
    createRuntime: ({ manager }) => {
      calls.push("runtime");
      return { manager, resume: id => calls.push(`resume:${id ?? "all"}`), venue: {
        go: value => calls.push(`go:${value.id}`), repair: () => "repaired",
      } };
    },
    registerOriginalVenues: values => { assert.equal(values.length, 1); },
    createOriginalVenueInstaller: () => ({
      install: id => { assert.equal(id, "sparrow"); calls.push("install"); return new Promise(resolve => { finishInstall = resolve; }); },
      verify: () => true,
    }),
    translateVenue, SPARROW_REFERENCE,
    ensureManifestFixtures: () => calls.push("fixtures"),
    verifyManifestFixtures: () => calls.push("verify"),
    console: { log() {}, error(value) { throw new Error(value); } },
  };
  // Execute the unchanged entry body with injected API modules. No actual
  // Minecraft import is available to Node, and no live world is accessed.
  const body = entrySource.replace(/^import\s+[\s\S]*?from\s+["'][^"']+["'];\r?\n/gm, "");
  new Function(...Object.keys(context), body)(...Object.values(context));
  assert.deepEqual(calls, [], "early module execution must not read world state");
  handlers.worldLoad();
  handlers.spawn({ player, initialSpawn: true });
  assert.deepEqual(calls, ["install"]);
  finishInstall();
  await flush();
  assert.deepEqual(calls, ["install", "fixtures", "verify", "manager", "restore", "runtime", "resume:all", "go:tester", "resume:tester"]);
  assert.equal(messages.length, 1);
  handlers.spawn({ player, initialSpawn: true });
  assert.equal(messages.length, 1, "duplicate spawn must not teleport an active participant");
  handlers.leave({ playerId: player.id });
  handlers.spawn({ player, initialSpawn: true });
  assert.equal(messages.length, 2, "reconnect should restore welcome and participant state");
});

await test("trial startup failure creates no runtime and explicit repair retries initialization", async () => {
  const handlers = {}, messages = [], calls = [];
  let fail = true;
  const player = { id: "tester", typeId: "minecraft:player", sendMessage: value => messages.push(value) };
  const signal = name => ({ subscribe: fn => { handlers[name] = fn; } });
  const context = {
    world: {
      getAllPlayers: () => [player],
      afterEvents: { worldLoad: signal("load"), playerSpawn: signal("spawn"), playerLeave: signal("leave") },
    },
    system: { run: fn => fn(), afterEvents: { scriptEventReceive: signal("script") } },
    ActionFormData: class {}, ModalFormData: class {},
    GameManager: class {
      tables = new Map();
      constructor() { calls.push("manager"); }
      restore() {}
    },
    sparrowGame: { id: "sparrow" },
    createRuntime: ({ manager }) => { calls.push("runtime"); return { manager, resume() {}, venue: { go() {} } }; },
    registerOriginalVenues() {}, translateVenue, SPARROW_REFERENCE,
    createOriginalVenueInstaller: () => ({ install: async () => {
      calls.push("install");
      if (fail) throw new Error("simulated probe failure");
    } }),
    ensureManifestFixtures: () => calls.push("fixtures"),
    verifyManifestFixtures: () => calls.push("verify"),
    console: { log() {}, error() {} },
  };
  const body = entrySource.replace(/^import\s+[\s\S]*?from\s+["'][^"']+["'];\r?\n/gm, "");
  new Function(...Object.keys(context), body)(...Object.values(context));
  handlers.load();
  await flush();
  assert.deepEqual(calls, ["install"]);
  assert.match(messages[0], /会場準備失敗.*simulated probe failure/);
  fail = false;
  handlers.script({ id: "junket:reference", sourceEntity: player, message: "repair" });
  await flush();
  assert.deepEqual(calls, ["install", "install", "fixtures", "verify", "manager", "runtime"]);
});

console.log(`PASS ${checks} offline original-venue installation checks; Bedrock client playthrough remains required.`);
