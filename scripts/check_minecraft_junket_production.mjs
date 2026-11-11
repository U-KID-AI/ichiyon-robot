import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { VENUE_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_venue_generated.js";
import {
  createProductionVenues, WALKER_LABEL_DEAD, WALKER_LABEL_INITIAL,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/production_venue.js";
import { createVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue.js";
import { createTemperanceVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/temperance_venue.js";
import { createLucyVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/lucy_venue.js";
import { createSilenceVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/silence_venue.js";
import { createJinnVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/jinn_venue.js";
import {
  createGarden, createHope, createRabbit, gardenGame, gardenSystem, hopeSystem, HOPE_AI_TRADERS,
  RABBIT_CPU_NAMES, rabbitFloorCategories, rabbitGame, rabbitLegalMoves,
  rabbitLegalTrapFloors, rabbitSystem, rabbitView,
  RABBIT_RESULT_TICKS, RABBIT_RESULT_OPEN_TICKS, RABBIT_WIPE_RESULT_TICKS,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/board_games.js";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";
import { physicalControlRequest } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/complete_runtime.js";

const catalog = JSON.parse(readFileSync(
  new URL("../scripts/minecraft/junket_games.json", import.meta.url), "utf8"));
assert.equal(VENUE_CONFIG.VENUES.length, 17);
assert.deepEqual(VENUE_CONFIG.VENUES.map(entry => entry.id), catalog.games.map(game => game.id));
assert.equal(new Set(VENUE_CONFIG.VENUES.map(entry => entry.structure)).size, 17);

// Hope and Rabbit must not silently fall back to the generic prop geometry.
// Their runtime state frames are only meaningful when the client entity maps
// those frames to dedicated original textures and a dedicated render
// controller.
{
  const rp = new URL("../minecraft/resource_packs/ichiyon_junket_rp/", import.meta.url);
  const geometry = JSON.parse(readFileSync(new URL("models/entity/junket.geo.json", rp), "utf8"));
  const modelIds = new Set(geometry["minecraft:geometry"]
    .map(model => model.description.identifier));
  for (const identifier of [
    "geometry.junket.hope.tv", "geometry.junket.hope.code",
    "geometry.junket.hope.code_display", "geometry.junket.rabbit.floor",
    "geometry.junket.walker.label", "geometry.junket.walker.bomb",
  ]) {
    assert.ok(modelIds.has(identifier), `missing dedicated JUNKET model ${identifier}`);
  }
  const rabbitFloor = geometry["minecraft:geometry"].find(model =>
    model.description.identifier === "geometry.junket.rabbit.floor");
  const floorCube = rabbitFloor.bones[0].cubes[0];
  assert.deepEqual(floorCube.size, [48, 0.75, 48],
    "Rabbit floor model must cover one 3x3 source tile");
  assert.deepEqual(floorCube.uv.up.uv_size, [48, 48],
    "Rabbit floor model must map its full 3x3 texture to the top face");

  const hopeDisplay = JSON.parse(readFileSync(
    new URL("entity/junket_hope_code_display.entity.json", rp), "utf8"))
    ["minecraft:client_entity"].description;
  assert.equal(hopeDisplay.geometry.default, "geometry.junket.hope.code_display");
  assert.equal(hopeDisplay.textures.normal, "textures/entity/junket/hope_code_display");
  assert.equal(hopeDisplay.textures.broken, "textures/entity/junket/hope_code_display_broken");

  const rabbitClient = JSON.parse(readFileSync(
    new URL("entity/junket_rabbit_floor.entity.json", rp), "utf8"))
    ["minecraft:client_entity"].description;
  assert.equal(rabbitClient.geometry.default, "geometry.junket.rabbit.floor");
  assert.deepEqual(Object.keys(rabbitClient.textures).sort(),
    ["falling", "guarded", "normal", "trap"]);
  assert.equal(rabbitClient.render_controllers[0],
    "controller.render.junket.rabbit_floor");

  const controllers = JSON.parse(readFileSync(
    new URL("render_controllers/junket.render_controllers.json", rp), "utf8"))
    .render_controllers;
  assert.match(controllers["controller.render.junket.hope_code_display"].textures[0],
    /frame'\) == 1 \? Texture\.broken : Texture\.normal/);
  assert.match(controllers["controller.render.junket.rabbit_floor"].textures[0],
    /== 3 \? Texture\.falling : Texture\.normal/);

  const walkerLabel = JSON.parse(readFileSync(
    new URL("entity/junket_walker_label.entity.json", rp), "utf8"))
    ["minecraft:client_entity"].description;
  assert.equal(walkerLabel.geometry.default, "geometry.junket.walker.label");
  assert.equal(walkerLabel.render_controllers[0],
    "controller.render.junket.walker_label",
    "the Walker floor kana labels need an existing render controller");
  assert.equal(Object.keys(walkerLabel.textures).length, 22,
    "Walker labels need the ten kana rows, two number groups and ten dead labels");
  const walkerLabelController = controllers["controller.render.junket.walker_label"];
  assert.ok(walkerLabelController,
    "the Walker label client entity must not reference a missing controller");
  assert.match(walkerLabelController.textures[0], /Texture\.a/);
  assert.match(walkerLabelController.textures[0], /frame'\) == 20 \? Texture\.dead_five : Texture\.dead_zero/,
    "frames 20 and 21 must select the source dead five and zero labels");
  const walkerBomb = JSON.parse(readFileSync(
    new URL("entity/junket_walker_bomb.entity.json", rp), "utf8"))
    ["minecraft:client_entity"].description;
  assert.equal(walkerBomb.geometry.default, "geometry.junket.walker.bomb");
}

// A runtime layout is valid only when its RLE length also fills the declared
// structure volume. Otherwise a truncated manifest could look "complete" while
// leaving an unchecked tail of a venue in the world.
{
  const venue = VENUE_CONFIG.VENUES.find(entry => entry.id === "ace");
  const originalLayout = venue.layout;
  const changed = JSON.parse(JSON.stringify(originalLayout));
  const runs = changed.runs.split(";").filter(Boolean);
  const [paletteIndex, count] = runs.at(-1).split(",").map(Number);
  runs[runs.length - 1] = `${paletteIndex},${count - 1}`;
  changed.runs = runs.join(";");
  changed.cells -= 1;
  venue.layout = changed;
  try {
    assert.throws(() => createProductionVenues(createMock()), /会場寸法/,
      "a layout shorter than the declared venue volume must be rejected");
  } finally {
    venue.layout = originalLayout;
  }
}

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
  entry.destination.y >= entry.origin[1] + 1 &&
  entry.destination.y <= entry.origin[1] + entry.size[1] - 2 &&
  entry.destination.z >= entry.origin[2] + 1 &&
  entry.destination.z <= entry.origin[2] + entry.size[2] - 2));

const keyOf = value => `${value.x},${value.y},${value.z}`;
const keyAt = (x, y, z) => `${x},${y},${z}`;
const layouts = new Map(VENUE_CONFIG.VENUES.map(entry => [entry.id, parseLayout(entry)]));

function layoutBlockAt(venue, localX, localY, localZ) {
  const [sx, sy, sz] = venue.size;
  assert.ok(localX >= 0 && localX < sx && localY >= 0 && localY < sy &&
    localZ >= 0 && localZ < sz, `${venue.id} local cell out of bounds`);
  const wanted = localX * sy * sz + localY * sz + localZ;
  let offset = 0;
  for (const run of layouts.get(venue.id)) {
    if (wanted < offset + run.count) return venue.layout.palette[run.paletteIndex];
    offset += run.count;
  }
  throw new Error(`${venue.id} layout cell was not found`);
}

function runtimeClearanceTargets(venue) {
  const targets = [venue.destination, venue.control, venue.return_point, ...venue.seats];
  targets.push(...venue.fixtures.map(fixture => ({
    x: fixture.location[0], y: fixture.location[1], z: fixture.location[2],
  })));
  for (const value of Object.values(venue.points ?? {})) {
    if (Array.isArray(value)) {
      targets.push(...value.filter(item => item && typeof item === "object"));
    } else if (value && typeof value === "object") {
      targets.push(value);
    }
  }
  if (venue.id === "hope") {
    targets.push(...(venue.hope_ai_spawns ?? []));
    for (const value of [venue.hope_spectator, venue.hope_warp, venue.hope_lose]) {
      if (value) targets.push(value);
    }
  }
  return targets;
}

function parseLayout(venue) {
  const runs = venue.layout.runs.split(";").filter(Boolean).map(value => {
    const [paletteIndex, count] = value.split(",").map(Number);
    assert.ok(Number.isInteger(paletteIndex) && Number.isInteger(count) && count > 0);
    assert.ok(paletteIndex >= 0 && paletteIndex < venue.layout.palette.length);
    return { paletteIndex, count };
  });
  assert.equal(runs.reduce((sum, run) => sum + run.count, 0), venue.layout.cells);
  return runs;
}

function eachCell(venue, callback) {
  const [sx, sy, sz] = venue.size;
  const plane = sy * sz;
  let index = 0;
  for (const run of layouts.get(venue.id)) {
    const expected = venue.layout.palette[run.paletteIndex];
    for (let offset = 0; offset < run.count; offset++, index++) {
      const x = Math.floor(index / plane);
      const remainder = index - x * plane;
      const y = Math.floor(remainder / sz);
      const z = remainder - y * sz;
      callback(venue.origin[0] + x, venue.origin[1] + y, venue.origin[2] + z, expected);
    }
  }
  assert.equal(index, venue.layout.cells);
}

// The saved structure, generated RLE layout and runtime teleport targets must
// describe the same clearance envelope. Checking only a single venue allowed a
// displaced fixture to pass while its Y or Y+1 cell was still solid.
{
  for (const venue of VENUE_CONFIG.VENUES) {
    const expected = new Set();
    for (const target of runtimeClearanceTargets(venue)) {
      assert.ok(Number.isFinite(target.x) && Number.isFinite(target.y) &&
        Number.isFinite(target.z), `${venue.id} runtime target must have world coordinates`);
      const x = Math.floor(target.x - venue.origin[0]);
      const y = Math.floor(target.y - venue.origin[1]);
      const z = Math.floor(target.z - venue.origin[2]);
      for (const height of [y, y + 1]) {
        const key = `${x},${height},${z}`;
        expected.add(key);
        const block = layoutBlockAt(venue, x, height, z);
        const typeId = typeof block === "string" ? block : block?.type;
        assert.equal(typeId, "minecraft:air",
          `${venue.id} runtime clearance ${key} must be air in the generated layout`);
      }
    }
    const actual = new Set(venue.clearance.map(cell => cell.join(",")));
    assert.equal(actual.size, venue.clearance.length,
      `${venue.id} clearance must not contain duplicate cells`);
    assert.deepEqual([...actual].sort(), [...expected].sort(),
      `${venue.id} clearance must contain exactly the runtime targets' Y/Y+1 columns, no horizontal extras`);

    const floorTargets = [
      venue.destination, venue.return_point, ...venue.seats,
      ...venue.fixtures.filter(fixture => fixture.role === "reception" || fixture.role === "return")
        .map(fixture => ({ x: fixture.location[0], y: fixture.location[1], z: fixture.location[2] })),
    ];
    if (venue.id === "hope") floorTargets.push(venue.hope_warp, venue.hope_lose);
    for (const target of floorTargets) {
      const x = Math.floor(target.x - venue.origin[0]);
      const y = Math.floor(target.y - venue.origin[1]) - 1;
      const z = Math.floor(target.z - venue.origin[2]);
      const support = layoutBlockAt(venue, x, y, z);
      const supportType = typeof support === "string" ? support : support?.type;
      assert.ok(supportType && supportType !== "minecraft:air",
        `${venue.id} floor target ${target.x},${target.y},${target.z} lost its support block`);
    }
  }
}

function applyLayout(blocks, venue, preserveAir = false) {
  eachCell(venue, (x, y, z, expected) => {
    const typeId = typeof expected === "string" ? expected : expected.type;
    if (typeId === "minecraft:air") {
      if (!preserveAir) blocks.delete(keyAt(x, y, z));
    }
    else blocks.set(keyAt(x, y, z), expected);
  });
}

function firstAirCell(venue) {
  let found = null;
  eachCell(venue, (x, y, z, expected) => {
    if (!found && expected === "minecraft:air") found = { x, y, z };
  });
  return found;
}

function makeEntity(typeId, location, tags = []) {
  const properties = new Map();
  const health = { currentValue: 3, defaultValue: 3, effectiveMax: 3 };
  return {
    typeId,
    location: { ...location },
    removed: false,
    tags: new Set(tags),
    nameTag: "",
    visualProperties: new Map(),
    effects: [],
    rotation: { x: 0, y: 0 },
    addTag(tag) { this.tags.add(tag); },
    removeTag(tag) { this.tags.delete(tag); },
    hasTag(tag) { return this.tags.has(tag); },
    getTags() { return [...this.tags]; },
    remove() { this.removed = true; },
    teleport(location) { this.location = { ...location }; },
    setRotation(rotation) { this.rotation = { ...rotation }; },
    addEffect(identifier, duration, options) {
      this.effects.push({ identifier, duration, options });
    },
    setProperty(name, value) { this.visualProperties.set(name, value); },
    getProperty(name) { return this.visualProperties.get(name); },
    setDynamicProperty(name, value) { properties.set(name, value); },
    getDynamicProperty(name) { return properties.get(name); },
    getComponent(identifier) {
      if (identifier !== "minecraft:health") return undefined;
      return {
        get currentValue() { return health.currentValue; },
        get defaultValue() { return health.defaultValue; },
        get effectiveMax() { return health.effectiveMax; },
        setCurrentValue(value) { health.currentValue = value; },
        resetToMaxValue() { health.currentValue = health.defaultValue; },
      };
    },
    clearVelocity() { this.velocityCleared = (this.velocityCleared ?? 0) + 1; },
    playSound(identifier, options) {
      (this.sounds ??= []).push({ identifier, options });
    },
  };
}

function roleEntity(mock, id, role) {
  const tag = `junket:${id}:fixture:${role}`;
  return mock.entities.find(entity => !entity.removed && entity.hasTag(tag));
}

function createMock({ unloadedReads = 0, preserveAirOnPlace = false } = {}) {
  const properties = new Map();
  const blocks = new Map();
  const entities = [];
  const placements = [];
  const players = [];
  const particles = [];
  const sounds = [];
  const timers = [];
  const interactListeners = [];
  const dieListeners = [];
  let timerId = 0;
  let unloaded = unloadedReads;

  const dimension = {
    id: "minecraft:overworld",
    getBlock({ x, y, z }) {
      const at = keyAt(x, y, z);
      if (unloaded > 0 && !blocks.has(at)) {
        unloaded -= 1;
        return undefined;
      }
      const block = {
        get typeId() {
          const stored = blocks.get(at) ?? "minecraft:air";
          return typeof stored === "string" ? stored : stored.type;
        },
        get isAir() { return this.typeId === "minecraft:air"; },
        location: { x, y, z },
        permutation: {
          getState(name) {
            const stored = blocks.get(at) ?? "minecraft:air";
            const states = typeof stored === "string" ? {} : stored.states ?? {};
            return states[name];
          },
        },
        setType(type) {
          if (type === "minecraft:air") blocks.delete(at);
          else blocks.set(at, type);
        },
      };
      return block;
    },
    runCommand() { return { successCount: 1 }; },
    spawnParticle(typeId, location) {
      particles.push({ typeId, location: { ...location } });
    },
    playSound(identifier, location, options) {
      sounds.push({ identifier, location: { ...location }, options });
    },
    getEntities({ tags = [], location, maxDistance = Infinity }) {
      return entities.filter(entity => !entity.removed &&
        tags.every(tag => entity.tags.has(tag)) &&
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
    getDimension(id) {
      assert.equal(id, "minecraft:overworld");
      return dimension;
    },
    getDynamicProperty(name) { return properties.get(name); },
    setDynamicProperty(name, value) { properties.set(name, value); },
    getAllPlayers() { return players; },
    beforeEvents: {
      playerInteractWithEntity: {
        subscribe(callback) {
          interactListeners.push(callback);
          return callback;
        },
      },
    },
    afterEvents: {
      entityDie: {
        subscribe(callback) {
          dieListeners.push(callback);
          return callback;
        },
      },
    },
    structureManager: {
      place(structure, target, location, options) {
        assert.equal(target, dimension);
        assert.equal(options.includeEntities, false);
        const venue = VENUE_CONFIG.VENUES.find(candidate => candidate.structure === structure);
        assert.ok(venue, `unexpected structure ${structure}`);
        assert.deepEqual(location, { x: venue.origin[0], y: venue.origin[1], z: venue.origin[2] });
        placements.push({ structure, location: { ...location } });
        applyLayout(blocks, venue, preserveAirOnPlace);
      },
    },
  };

  const system = {
    runTimeout(callback, ticks) {
      const id = ++timerId;
      timers.push({ id, callback, ticks, cancelled: false });
      return id;
    },
    clearRun(id) {
      const timer = timers.find(value => value.id === id);
      if (timer) timer.cancelled = true;
    },
  };

  return {
    properties, blocks, entities, placements, players, particles, sounds,
    timers, dimension, world, system,
    emitInteract(event) {
      for (const callback of [...interactListeners]) callback(event);
      return event;
    },
    emitEntityDie(deadEntity, damagingEntity) {
      const event = { deadEntity, damageSource: { damagingEntity } };
      for (const callback of [...dieListeners]) callback(event);
      return event;
    },
  };
}

async function drain(mock, promise) {
  let done = false, value, error;
  promise.then(result => { done = true; value = result; },
    reason => { done = true; error = reason; });
  for (let cycle = 0; cycle < 300000 && !done; cycle++) {
    const index = mock.timers.findIndex(timer => !timer.cancelled);
    if (index >= 0) {
      const [timer] = mock.timers.splice(index, 1);
      timer.callback();
      await new Promise(resolve => setImmediate(resolve));
      continue;
    }
    await new Promise(resolve => setImmediate(resolve));
  }
  if (!done) throw new Error("venue timer loop did not settle");
  if (error) throw error;
  return value;
}

function specialSetup(mock) {
  const calls = [];
  const providers = {
    sparrow: createVenue({ world: mock.world, system: mock.system }),
    temperance: createTemperanceVenue({ world: mock.world }),
    lucy: createLucyVenue({ world: mock.world }),
    silence: createSilenceVenue({ world: mock.world }),
    jinn: createJinnVenue({ world: mock.world }),
  };
  for (const [id, provider] of Object.entries(providers)) {
    const ensure = provider.ensure.bind(provider);
    const verify = provider.verify.bind(provider);
    provider.ensure = async () => {
      calls.push(`${id}:ensure`);
      return ensure();
    };
    provider.verify = async () => {
      calls.push(`${id}:verify`);
      return verify();
    };
  }
  return { providers, calls };
}

const genericFixtureCount = VENUE_CONFIG.VENUES
  .filter(venue => !venue.special_runtime)
  .reduce((sum, venue) => sum + (venue.fixtures?.length ?? 0), 0);
const specialEntityCount = VENUE_CONFIG.VENUES
  .filter(venue => venue.special_runtime)
  .reduce((sum, venue) => sum + venue.fixtures.length, 0);
const expectedEntityCount = genericFixtureCount + specialEntityCount +
  RABBIT_CPU_NAMES.length + HOPE_AI_TRADERS + 37;

// Fresh install must materialize every RLE cell, spawn all fixtures and only
// run the corridor control installer after all 17 venues verify.
{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  let warpInstalls = 0;
  const warp = { installControls() { warpInstalls++; return true; } };
  const report = await drain(mock, venues.installAll(warp, special.providers));

  assert.equal(report.ok, true);
  assert.equal(report.failures.length, 0);
  assert.equal(report.completed.length, 17);
  assert.equal(mock.placements.length, 17);
  assert.deepEqual(mock.placements.map(entry => entry.structure),
    VENUE_CONFIG.VENUES.map(entry => entry.structure));
  assert.equal(warpInstalls, 1);
  assert.equal(mock.properties.get(VENUE_CONFIG.VENUE_INSTALL_KEY), VENUE_CONFIG.VENUE_SCHEMA);
  assert.ok(special.calls.filter(call => call.endsWith(":verify")).length >= 5,
    "every special venue must run its provider verification");
  assert.equal(mock.entities.length, expectedEntityCount);
  const rabbitCpus = mock.entities.filter(entity => !entity.removed && entity.hasTag("junket:rabbit:cpu"));
  assert.deepEqual(rabbitCpus.map(entity => entity.nameTag), [...RABBIT_CPU_NAMES]);

  const hope = VENUE_CONFIG.VENUES.find(entry => entry.id === "hope");
  const hopeStandY = hope.origin[1] + 6;
  const hopeReception = hope.fixtures.find(fixture => fixture.role === "reception").location;
  for (const target of [
    hope.destination, hope.return_point, ...hope.seats,
    { x: hopeReception[0], y: hopeReception[1], z: hopeReception[2] },
  ]) {
    assert.equal(target.y, hopeStandY,
      "Hope destinations and seats must stand one block above the pad floor");
    const support = mock.blocks.get(keyAt(
      Math.floor(target.x), Math.floor(target.y) - 1, Math.floor(target.z)));
    const supportType = typeof support === "string" ? support : support?.type;
    assert.ok(supportType && supportType !== "minecraft:air",
      `Hope runtime target has no physical support at ${target.x},${target.y},${target.z}`);
  }
  assert.equal(hope.hope_pads.length, 16);
  for (const pad of hope.hope_pads) {
    assert.equal(pad.y, hopeStandY, `Hope pad ${pad.pad} standing Y drifted`);
    assert.ok(pad.cells.every(cell => cell.y === hope.origin[1] + 5),
      `Hope pad ${pad.pad} floor must remain below its standing cells`);
  }

  for (const venue of VENUE_CONFIG.VENUES) {
    eachCell(venue, (x, y, z, expected) => {
      assert.equal(mock.blocks.get(keyAt(x, y, z)) ?? "minecraft:air", expected,
        `block mismatch at ${venue.id} ${x},${y},${z}`);
    });
    const marker = JSON.parse(mock.properties.get(`${VENUE_CONFIG.VENUE_INSTALL_KEY}:${venue.id}`));
    assert.equal(marker.verified, true);
    assert.equal(marker.schema, VENUE_CONFIG.VENUE_SCHEMA);
    assert.equal(marker.layoutCells, venue.layout.cells);
    if (!venue.special_runtime) {
      for (const fixture of venue.fixtures) {
        const roleTag = `junket:${venue.id}:fixture:${fixture.role}`;
        assert.equal(mock.entities.filter(entity => !entity.removed && entity.hasTag(roleTag)).length, 1,
          `missing fixture ${venue.id}:${fixture.role}`);
        if (fixture.role.startsWith("display_")) {
          assert.equal(mock.entities.filter(entity => !entity.removed &&
            entity.hasTag(`junket:${venue.id}:display`)).length,
          venue.fixtures.filter(candidate => candidate.role.startsWith("display_")).length,
          `missing display tag ${venue.id}:${fixture.role}`);
        }
      }
    }
  }

  const sparrow = VENUE_CONFIG.VENUES.find(entry => entry.id === "sparrow");
  assert.deepEqual(venues.config("sparrow").origin, {
    x: sparrow.origin[0], y: sparrow.origin[1], z: sparrow.origin[2],
  });
  for (const venue of VENUE_CONFIG.VENUES) {
    assert.deepEqual(venues.config(venue.id).origin, {
      x: venue.origin[0], y: venue.origin[1], z: venue.origin[2],
    }, `${venue.id} runtime origin must be the single generated origin`);
    assert.equal(Object.hasOwn(venue, "runtime_origin"), false,
      `${venue.id} must not keep a second origin field`);
  }
  assert.deepEqual(venues.destination("sparrow").location, sparrow.destination);
  assert.equal(typeof venues.destination("ace").afterTeleport, "function");

  const players = Array.from({ length: 4 }, (_, index) => ({
    id: `p${index}`,
    teleports: [],
    teleport(location, options) { this.teleports.push({ location, options }); },
  }));
  mock.players.push(...players);
  await venues.start("ace", { players: players.map(player => player.id), state: { phase: "betting" } });
  assert.ok(players.every(player => player.teleports.length === 1));
  assert.ok(players.every(player => player.teleports[0].options.dimension === mock.dimension));
  assert.equal(mock.entities.filter(entity => !entity.removed && entity.hasTag("junket:ace:reception")).length, 1);
  assert.equal(mock.entities.filter(entity => !entity.removed && entity.hasTag("junket:ace:fixture:control_1")).length, 1);

  // A second install is verification-only and must not place structures again.
  mock.placements.length = 0;
  const second = await drain(mock, venues.installAll(warp, special.providers));
  assert.equal(second.ok, true);
  assert.equal(mock.placements.length, 0, "a complete venue must not be placed again");
  assert.equal(mock.entities.length, expectedEntityCount, "fixture ensure must be idempotent");

  // A missing origin cell must be repaired and persisted without duplicating fixtures.
  const originKey = keyAt(...sparrow.origin);
  mock.blocks.delete(originKey);
  mock.properties.delete(`${VENUE_CONFIG.VENUE_INSTALL_KEY}:sparrow`);
  mock.placements.length = 0;
  const partial = await drain(mock, venues.installAll(warp, special.providers));
  assert.equal(partial.ok, true);
  assert.equal(mock.placements.length, 1);
  assert.equal(mock.placements[0].structure, sparrow.structure);
  assert.equal(mock.blocks.get(originKey), sparrow.origin_block);
  assert.equal(mock.entities.length, expectedEntityCount);

  // Missing and duplicate managed fixtures are repaired; obsolete ones are removed.
  const receptionTag = "junket:ace:reception";
  const reception = mock.entities.find(entity => !entity.removed && entity.hasTag(receptionTag));
  reception.remove();
  await drain(mock, venues.repair("ace"));
  assert.equal(mock.entities.filter(entity => !entity.removed && entity.hasTag(receptionTag)).length, 1);

  const ace = VENUE_CONFIG.VENUES.find(entry => entry.id === "ace");
  const duplicate = makeEntity("minecraft:npc", { x: ace.origin[0] + 1, y: ace.origin[1] + 2, z: ace.origin[2] + 1 },
    ["junket:ace:managed", "junket:ace:fixture:reception", receptionTag]);
  duplicate.setDynamicProperty("junket:venue_role", "reception");
  mock.entities.push(duplicate);
  await drain(mock, venues.repair("ace"));
  assert.equal(mock.entities.filter(entity => !entity.removed && entity.hasTag(receptionTag)).length, 1);

  const obsolete = makeEntity("junket:ace_prop", { x: ace.origin[0] + 1, y: ace.origin[1] + 2, z: ace.origin[2] + 1 },
    ["junket:ace:managed", "junket:ace:fixture:obsolete"]);
  obsolete.setDynamicProperty("junket:venue_role", "obsolete");
  obsolete.setDynamicProperty("junket:venue_id", "ace");
  mock.entities.push(obsolete);
  await drain(mock, venues.repair("ace"));
  assert.equal(obsolete.removed, true, "obsolete managed fixtures must be removed");

  const display = mock.entities.find(entity => !entity.removed && entity.hasTag("junket:ace:display"));
  assert.ok(display, "ace display entity must exist");
  display.tags.delete("junket:ace:display");
  await drain(mock, venues.repair("ace"));
  assert.equal(display.hasTag("junket:ace:display"), true,
    "repair must restore the display tag on an existing fixture");
}

// Walker's source floor letters and bomb object are managed entities, not a
// redstone block overlay or floating name tags. Their identity, exact floor
// positions, phase visibility, restart recovery and duplicate cleanup are part
// of the venue repair contract.
{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  const walker = VENUE_CONFIG.VENUES.find(entry => entry.id === "walker");
  const cellOf = floor => walker.walker.cells.find(cell => cell.floor === floor);
  const labels = () => mock.entities.filter(entity => !entity.removed &&
    entity.typeId === "junket:walker_label" && entity.hasTag("junket:walker:floor_label"));
  const bombs = () => mock.entities.filter(entity => !entity.removed &&
    entity.typeId === "junket:walker_bomb" && entity.hasTag("junket:walker:bomb"));
  const labelOf = floor => labels().filter(entity =>
    entity.hasTag(`junket:walker:floor_label:${floor}`));
  const expectedAt = floor => ({
    x: cellOf(floor).x + 0.5,
    y: walker.walker.platform_y + 1.04,
    z: cellOf(floor).z + 0.5,
  });

  assert.equal(labels().length, 37, "install must create one source floor label for all 37 floors");
  assert.equal(bombs().length, 0, "no bomb may be visible before the breaker completes setup");
  for (let floor = 1; floor <= 37; floor++) {
    const matches = labelOf(floor);
    assert.equal(matches.length, 1, `Walker floor ${floor} must have exactly one label`);
    assert.deepEqual(matches[0].location, expectedAt(floor));
    assert.equal(matches[0].getProperty("junket:floor"), floor);
    assert.equal(matches[0].getProperty("junket:frame"), WALKER_LABEL_INITIAL[floor - 1]);
    assert.equal(matches[0].nameTag, "", "floor letters must be textures, not name tags");
    assert.equal(matches[0].hasTag("junket:walker:auxiliary"), true);
  }

  const active = {
    players: [], state: {
      phase: "dodge", bombVisible: true, bombPositions: [19], bombs: [19],
      deadFloors: [21], positions: [19, 17], water: [false, false],
    },
  };
  venues.update("walker", active);
  assert.equal(labelOf(21)[0].getProperty("junket:frame"), WALKER_LABEL_DEAD[20],
    "a departed floor must switch to its source dead label");
  assert.equal(labelOf(19)[0].getProperty("junket:frame"), WALKER_LABEL_INITIAL[18],
    "a live bomb floor must keep its live floor label");
  assert.equal(bombs().length, 1, "a visible bomb must be a real managed entity");
  assert.deepEqual(bombs()[0].location, expectedAt(19));
  assert.equal(bombs()[0].nameTag, "", "the bomb must use its model, not a floating label");

  const duplicateBomb = makeEntity("junket:walker_bomb", expectedAt(19), [
    "junket:walker:managed", "junket:walker:auxiliary", "junket:walker:bomb",
    "junket:walker:bomb:19",
  ]);
  const staleBomb = makeEntity("junket:walker_bomb", expectedAt(18), [
    "junket:walker:managed", "junket:walker:auxiliary", "junket:walker:bomb",
    "junket:walker:bomb:18",
  ]);
  const duplicateLabel = makeEntity("junket:walker_label", expectedAt(5), [
    "junket:walker:managed", "junket:walker:auxiliary", "junket:walker:floor_label",
    "junket:walker:floor_label:5",
  ]);
  mock.entities.push(duplicateBomb, staleBomb, duplicateLabel);
  venues.update("walker", { ...active, state: { ...active.state, phase: "fuse" } });
  assert.equal(bombs().length, 1, "duplicate and stale bomb entities must be removed");
  assert.equal(labelOf(5).length, 1, "duplicate floor labels must be collapsed");

  venues.update("walker", {
    players: [], state: {
      ...active.state, phase: "bomb_setup", bombVisible: false, bombPositions: [],
    },
  });
  assert.equal(bombs().length, 0, "bombs remain hidden during breaker setup");
  venues.update("walker", {
    players: [], state: {
      ...active.state, phase: "finished", bombVisible: true, bombPositions: [19],
    },
  });
  assert.equal(bombs().length, 0, "finished matches must clear bomb props");

  const missing = labelOf(12)[0];
  missing.remove();
  const restarted = createProductionVenues(mock);
  restarted.update("walker", active);
  assert.equal(labels().length, 37, "a runtime restart must recreate a missing floor label");
  assert.equal(labelOf(21)[0].getProperty("junket:frame"), WALKER_LABEL_DEAD[20],
    "a runtime restart must restore dead-floor frames from game state");
  assert.equal(bombs().length, 1, "a runtime restart must restore visible bombs from game state");
}

// A foreign block in an expected-air cell must stop the repair before any
// structure placement, even when the venue origin happens to match.
{
  const mock = createMock({ preserveAirOnPlace: true });
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  const grail = VENUE_CONFIG.VENUES.find(entry => entry.id === "grail");
  const cell = firstAirCell(grail);
  assert.ok(cell);
  mock.blocks.set(keyAt(cell.x, cell.y, cell.z), "minecraft:oak_planks");
  const report = await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  assert.equal(report.ok, false);
  assert.ok(report.failures.some(failure => failure.id === "grail"));
  assert.equal(mock.placements.some(entry => entry.structure === grail.structure), false);
  assert.equal(mock.blocks.get(keyAt(cell.x, cell.y, cell.z)), "minecraft:oak_planks");
  assert.equal(mock.properties.get(VENUE_CONFIG.VENUE_INSTALL_KEY), 0);
}

// A verified venue may contain an old known block in an expected-air cell.
// Repair must remove it explicitly even when the platform leaves structure air
// cells untouched, then verify the clearance contract again.
{
  const mock = createMock({ preserveAirOnPlace: true });
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  const installed = await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  assert.equal(installed.ok, true);
  const ace = VENUE_CONFIG.VENUES.find(entry => entry.id === "ace");
  const clearance = ace.clearance.find(cell => cell[1] >= 2);
  assert.ok(clearance, "ace needs an elevated clearance cell");
  const clearanceKey = keyAt(
    ace.origin[0] + clearance[0], ace.origin[1] + clearance[1], ace.origin[2] + clearance[2]);
  assert.equal(mock.blocks.get(clearanceKey), undefined);
  const legacy = ace.legacy_blocks[0];
  mock.blocks.set(clearanceKey, legacy);
  mock.placements.length = 0;
  const repaired = await drain(mock, venues.repair("ace"));
  assert.equal(repaired.repaired, true);
  assert.equal(mock.placements.length, 1);
  assert.equal(mock.blocks.get(clearanceKey), undefined,
    "clearance cells must be explicitly cleared before saved-structure placement");
  assert.equal(JSON.parse(mock.properties.get(`${VENUE_CONFIG.VENUE_INSTALL_KEY}:ace`)).verified, true);
}

// A verified marker is only a cache. A fresh runtime must re-inspect every RLE
// cell and every fixture, repair a missing structure block, recreate a removed
// fixture, and restore a marker that was deleted without placing the structure
// again.
{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  const ace = VENUE_CONFIG.VENUES.find(entry => entry.id === "ace");
  const markerKey = `${VENUE_CONFIG.VENUE_INSTALL_KEY}:ace`;
  assert.equal(JSON.parse(mock.properties.get(markerKey)).verified, true);

  let removedCell = null;
  eachCell(ace, (x, y, z, expected) => {
    if (!removedCell && !(x === ace.origin[0] && y === ace.origin[1] && z === ace.origin[2]) &&
        expected !== "minecraft:air" && (typeof expected !== "object" || expected.type !== "minecraft:air")) {
      removedCell = { x, y, z, expected };
    }
  });
  assert.ok(removedCell, "ace needs a non-origin structure cell for the missing-block case");
  const removedKey = keyAt(removedCell.x, removedCell.y, removedCell.z);
  mock.blocks.delete(removedKey);
  assert.equal(JSON.parse(mock.properties.get(markerKey)).verified, true,
    "the stale verified marker must remain present to exercise re-inspection");
  mock.placements.length = 0;
  const structureRestart = createProductionVenues(mock);
  const structureRepair = await drain(mock, structureRestart.repair("ace"));
  assert.equal(structureRepair.repaired, true);
  assert.equal(mock.placements.length, 1);
  const restored = mock.blocks.get(removedKey);
  const restoredType = typeof restored === "string" ? restored : restored?.type;
  const expectedType = typeof removedCell.expected === "string"
    ? removedCell.expected : removedCell.expected.type;
  assert.equal(restoredType, expectedType,
    "a missing block must be restored even when the verified marker is intact");

  const reception = roleEntity(mock, "ace", "reception");
  reception.remove();
  assert.equal(JSON.parse(mock.properties.get(markerKey)).verified, true);
  const fixtureRestart = createProductionVenues(mock);
  const fixtureRepair = await drain(mock, fixtureRestart.repair("ace"));
  assert.equal(fixtureRepair.repaired, false,
    "fixture-only repair must not rebuild an otherwise complete structure");
  assert.equal(mock.placements.length, 1);
  for (const fixture of ace.fixtures) {
    assert.equal(mock.entities.filter(entity => !entity.removed &&
      entity.hasTag(`junket:ace:fixture:${fixture.role}`)).length, 1,
    `fresh-runtime repair is missing fixture ${fixture.role}`);
  }
  const recreatedReception = roleEntity(mock, "ace", "reception");
  assert.equal(recreatedReception.hasTag("junket:ace:reception"), true);
  assert.equal(recreatedReception.hasTag("junket:ace:control"), true);

  mock.properties.delete(markerKey);
  mock.placements.length = 0;
  const markerRestart = createProductionVenues(mock);
  const markerRepair = await drain(mock, markerRestart.repair("ace"));
  assert.equal(markerRepair.repaired, false);
  assert.equal(mock.placements.length, 0,
    "a complete structure must not be placed again just to recreate its marker");
  assert.equal(JSON.parse(mock.properties.get(markerKey)).verified, true);

  eachCell(ace, (x, y, z, expected) => {
    assert.equal(mock.blocks.get(keyAt(x, y, z)) ?? "minecraft:air", expected,
      `post-repair RLE mismatch at ace ${x},${y},${z}`);
  });
}

// A verified marker is a cache, not proof of a complete venue. Damage every
// venue at once, then start a fresh runtime and confirm that each structure is
// re-inspected, its clearance is cleaned, and its operation entities survive.
{
  const mock = createMock();
  const special = specialSetup(mock);
  const venues = createProductionVenues(mock);
  const installed = await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  assert.equal(installed.ok, true);

  const damage = [];
  for (const venue of VENUE_CONFIG.VENUES) {
    const markerKey = `${VENUE_CONFIG.VENUE_INSTALL_KEY}:${venue.id}`;
    assert.equal(JSON.parse(mock.properties.get(markerKey)).verified, true);

    const clearance = venue.clearance[0];
    assert.ok(clearance, `${venue.id} needs a clearance cell`);
    const clearanceKey = keyAt(venue.origin[0] + clearance[0],
      venue.origin[1] + clearance[1], venue.origin[2] + clearance[2]);
    mock.blocks.set(clearanceKey, venue.legacy_blocks[0]);

    let structureCell = null;
    const clearanceSet = new Set(venue.clearance.map(cell => cell.join(",")));
    eachCell(venue, (x, y, z, expected) => {
      const local = `${x - venue.origin[0]},${y - venue.origin[1]},${z - venue.origin[2]}`;
      const typeId = typeof expected === "string" ? expected : expected.type;
      if (!structureCell && typeId !== "minecraft:air" && !clearanceSet.has(local)) {
        structureCell = { x, y, z, expected };
      }
    });
    assert.ok(structureCell, `${venue.id} needs a non-air structure cell for damage injection`);
    const structureKey = keyAt(structureCell.x, structureCell.y, structureCell.z);
    mock.blocks.delete(structureKey);

    const fixture = venue.fixtures[0];
    const fixtureTag = `junket:${venue.id}:fixture:${fixture.role}`;
    const fixtureEntity = mock.entities.find(entity => !entity.removed && entity.hasTag(fixtureTag));
    assert.ok(fixtureEntity, `${venue.id} needs an installed operation entity`);
    fixtureEntity.remove();

    assert.equal(JSON.parse(mock.properties.get(markerKey)).verified, true,
      `${venue.id} verified marker must remain present during damage injection`);
    damage.push({ venue, clearanceKey, structureCell, structureKey, fixture });
  }

  mock.placements.length = 0;
  const restarted = createProductionVenues(mock);
  const restored = await drain(mock, restarted.installAll(
    { installControls() { return true; } }, specialSetup(mock).providers));
  assert.equal(restored.ok, true, `repair failures: ${JSON.stringify(restored.failures)}`);
  assert.deepEqual(new Set(mock.placements.map(entry => entry.structure)),
    new Set(VENUE_CONFIG.VENUES.map(entry => entry.structure)),
    "fresh runtime must re-place every damaged venue exactly once");

  for (const { venue, clearanceKey, structureCell, structureKey, fixture } of damage) {
    assert.equal(mock.blocks.get(clearanceKey), undefined,
      `${venue.id} clearance pollution must be removed`);
    const expectedType = typeof structureCell.expected === "string"
      ? structureCell.expected : structureCell.expected.type;
    const actual = mock.blocks.get(structureKey);
    assert.equal(typeof actual === "string" ? actual : actual?.type, expectedType,
      `${venue.id} missing structure cell must be restored`);
    const fixtureTag = `junket:${venue.id}:fixture:${fixture.role}`;
    const fixtureEntities = mock.entities.filter(entity => !entity.removed && entity.hasTag(fixtureTag));
    assert.equal(fixtureEntities.length, 1,
      `${venue.id} operation entity ${fixture.role} must be restored exactly once`);
    if (!venue.special_runtime) {
      assert.equal(fixtureEntities[0].getDynamicProperty("junket:venue_id"), venue.id);
      assert.equal(fixtureEntities[0].getDynamicProperty("junket:venue_role"), fixture.role);
      assert.equal(fixtureEntities[0].getDynamicProperty("junket:venue_action"),
        fixture.action ?? fixture.role, `${venue.id} fixture action must survive repair`);
    }
    const marker = JSON.parse(mock.properties.get(`${VENUE_CONFIG.VENUE_INSTALL_KEY}:${venue.id}`));
    assert.equal(marker.verified, true);
    assert.equal(marker.layoutCells, venue.layout.cells);
  }

  for (const venue of VENUE_CONFIG.VENUES) {
    eachCell(venue, (x, y, z, expected) => {
      assert.equal(mock.blocks.get(keyAt(x, y, z)) ?? "minecraft:air", expected,
        `repaired RLE mismatch at ${venue.id} ${x},${y},${z}`);
    });
  }
}

// Unloaded chunks are retried instead of being mistaken for complete or empty venues.
{
  const mock = createMock({ unloadedReads: 12 });
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  const report = await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  assert.equal(report.ok, true);
  assert.equal(mock.placements.length, 17);
}

// Block states are part of the layout contract. A same-type block with a wrong
// permutation must be reported and repaired, not accepted because its typeId matches.
{
  const ace = VENUE_CONFIG.VENUES.find(entry => entry.id === "ace");
  const palette = ace.layout.palette;
  const stateIndex = palette.findIndex((value, index) => index > 0 && typeof value === "string");
  assert.ok(stateIndex > 0, "stateful test palette entry must exist");
  const original = palette[stateIndex];
  palette[stateIndex] = { type: original, states: { facing_direction: 1 } };
  try {
    const mock = createMock();
    const venues = createProductionVenues(mock);
    const special = specialSetup(mock);
    const first = await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
    assert.equal(first.ok, true);
    let target = null;
    eachCell(ace, (x, y, z, expected) => {
      if (!target && typeof expected === "object" && expected.type === original) {
        target = { x, y, z };
      }
    });
    assert.ok(target, "stateful layout cell must exist");
    const at = keyAt(target.x, target.y, target.z);
    mock.blocks.set(at, { type: original, states: { facing_direction: 2 } });
    mock.placements.length = 0;
    const repaired = await drain(mock, venues.repair("ace"));
    assert.equal(repaired.repaired, true);
    assert.equal(mock.placements.length, 1);
    assert.deepEqual(mock.blocks.get(at), { type: original, states: { facing_direction: 1 } });
  } finally {
    palette[stateIndex] = original;
  }
}

// A failing venue must fail the overall install without blocking the independent
// corridor controls repair.
{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  special.providers.sparrow.ensure = () => { throw new Error("sparrow fixture failure"); };
  let warpInstalls = 0;
  const report = await drain(mock, venues.installAll({
    installControls() { warpInstalls++; return true; },
  }, special.providers));
  assert.equal(report.ok, false);
  assert.ok(report.failures.some(failure => failure.id === "sparrow"));
  assert.equal(warpInstalls, 1);
  assert.equal(report.warpOk, true);
  assert.equal(mock.properties.get(VENUE_CONFIG.VENUE_INSTALL_KEY), 0);
}

// Special venues must repair real fixtures, not just markers. These cases cover
// missing, wrong-type, duplicate, displaced, and role-metadata-free entities.
{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  const installed = await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  assert.equal(installed.ok, true);

  for (const role of ["box0", "box1"]) {
    assert.equal(roleEntity(mock, "lucy", role).hasTag("junket:lucy_box"), true);
  }
  for (let box = 0; box < 2; box++) {
    for (let row = 0; row < 5; row++) {
      for (let column = 0; column < 5; column++) {
        assert.equal(roleEntity(mock, "lucy", `keyhole_${box}_${row}_${column}`)
          .hasTag("junket:lucy_keyhole"), true);
      }
      assert.equal(roleEntity(mock, "lucy", `confirm_${box}_${row}`)
        .hasTag("junket:lucy_confirm"), true);
    }
  }
  for (const seat of [0, 1]) {
    for (const card of ["gold", "thief", "demon"]) {
      const entity = roleEntity(mock, "jinn", `card_${card}_${seat}`);
      assert.equal(entity.hasTag("junket:jinn_card"), true);
      assert.equal(entity.getDynamicProperty("junket:jinn_card_type"), card);
      assert.equal(entity.getDynamicProperty("junket:jinn_seat"), seat);
    }
  }

  const lucy = VENUE_CONFIG.VENUES.find(entry => entry.id === "lucy");
  const keyholeSpec = lucy.fixtures.find(entry => entry.role === "keyhole_0_2_3");
  const keyholeTag = "junket:lucy:fixture:keyhole_0_2_3";
  const missing = roleEntity(mock, "lucy", "keyhole_0_2_3");
  missing.remove();
  await drain(mock, venues.repair("lucy"));
  let keyhole = roleEntity(mock, "lucy", "keyhole_0_2_3");
  assert.ok(keyhole, "missing keyhole must be recreated");
  assert.deepEqual(keyhole.location, {
    x: keyholeSpec.location[0], y: keyholeSpec.location[1], z: keyholeSpec.location[2],
  });
  assert.equal(keyhole.hasTag("junket:lucy_keyhole"), true);

  keyhole.typeId = "minecraft:pig";
  await drain(mock, venues.repair("lucy"));
  keyhole = roleEntity(mock, "lucy", "keyhole_0_2_3");
  assert.equal(keyhole.typeId, "junket:lucy_keyhole", "wrong-type fixture must be replaced");

  keyhole.location = { x: keyholeSpec.location[0] + 5, y: keyholeSpec.location[1], z: keyholeSpec.location[2] };
  await drain(mock, venues.repair("lucy"));
  keyhole = roleEntity(mock, "lucy", "keyhole_0_2_3");
  const expectedKeyhole = {
    x: keyholeSpec.location[0], y: keyholeSpec.location[1], z: keyholeSpec.location[2],
  };
  assert.ok(Math.hypot(keyhole.location.x - expectedKeyhole.x,
    keyhole.location.y - expectedKeyhole.y, keyhole.location.z - expectedKeyhole.z) < 0.75,
  "displaced fixture must return to its manifest location");

  const duplicate = makeEntity("junket:lucy_keyhole", { ...expectedKeyhole }, [
    "junket:lucy_fixture", keyholeTag, "junket:lucy_keyhole", "junket:lucy_control",
  ]);
  duplicate.setDynamicProperty("junket:lucy_role", "keyhole3");
  duplicate.setDynamicProperty("junket:lucy_slot", 3);
  mock.entities.push(duplicate);
  await drain(mock, venues.repair("lucy"));
  assert.equal(mock.entities.filter(entity => !entity.removed && entity.hasTag(keyholeTag)).length, 1,
    "duplicate fixture must be removed");

  const recover = roleEntity(mock, "lucy", "keyhole_0_0_0");
  recover.tags.delete("junket:lucy:fixture:keyhole_0_0_0");
  recover.setDynamicProperty("junket:lucy_role", undefined);
  assert.equal(recover.hasTag("junket:lucy_fixture"), true);
  await drain(mock, venues.repair("lucy"));
  const recovered = roleEntity(mock, "lucy", "keyhole_0_0_0");
  assert.equal(recovered.hasTag("junket:lucy_fixture"), true);
  assert.equal(recovered.getDynamicProperty("junket:lucy_role"), "keyhole_0_0_0");

  const card = roleEntity(mock, "jinn", "card_gold_0");
  card.tags.delete("junket:jinn_card");
  await assert.rejects(special.providers.jinn.verify(), /タグがありません/);
  await drain(mock, venues.repair("jinn"));
  assert.equal(roleEntity(mock, "jinn", "card_gold_0").hasTag("junket:jinn_card"), true);

  const bystander = makeEntity("minecraft:pig", {
    x: lucy.origin[0] + 1, y: lucy.origin[1] + 2, z: lucy.origin[2] + 1,
  });
  mock.entities.push(bystander);
  await drain(mock, venues.repair("lucy"));
  assert.equal(bystander.removed, false, "unmanaged entities must not be removed");

  const originKey = keyAt(...lucy.origin);
  mock.blocks.delete(originKey);
  mock.properties.delete(`${VENUE_CONFIG.VENUE_INSTALL_KEY}:lucy`);
  await drain(mock, venues.repair("lucy"));
  assert.equal(mock.blocks.get(originKey), lucy.origin_block);
  const marker = JSON.parse(mock.properties.get(`${VENUE_CONFIG.VENUE_INSTALL_KEY}:lucy`));
  assert.equal(marker.verified, true);
  assert.equal(marker.repairVersion, 11);
  assert.equal(marker.fixtures, lucy.fixtures.length);
}

// If verification fails after teleport, the player must not remain in a broken venue.
{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  const originalEnsure = special.providers.lucy.ensure;
  special.providers.lucy.ensure = async () => { throw new Error("post-teleport fixture failure"); };
  const player = {
    id: "evacuee",
    teleports: [],
    messages: [],
    teleport(location, options) { this.teleports.push({ location: { ...location }, options }); },
    sendMessage(message) { this.messages.push(message); },
  };
  await assert.rejects(venues.destination("lucy").afterTeleport(player), /post-teleport fixture failure/);
  assert.deepEqual(player.teleports.at(-1).location, { x: -44.5, y: 75, z: 210.5 });
  assert.ok(player.messages.some(message => message.includes("安全地点")));
  special.providers.lucy.ensure = originalEnsure;
}

// Fire must drive physical card, TV and glass props from the live state, and
// the same presentation must return after fixture repair and a fresh runtime.
{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  const revealed = {
    players: ["p1", "p2"],
    state: {
      phase: "choice", actor: 0, series: 1, round: 1,
      totalWins: [0, 0], wins: [0, 0], hp: [600, 600], stock: [0, 0],
      temperature: [36, 36], windRemaining: [1200, 1200], windPower: [0, 0],
      glass: [1, 0], glassStage: [4, 0], used: [["1:hot", "2:hot"], []],
      choices: [
        { id: "1:hot", number: 1, heat: 1 },
        { id: "2:ice", number: 2, heat: -1 },
      ],
      revealResolved: true, phaseTicks: 0, pendingDamage: [0, 0],
      sanctionTargets: [], sanctionReason: null, endingRemaining: 0,
      last: null, lastWindStock: null,
    },
  };
  venues.update("fire", revealed);
  assert.equal(roleEntity(mock, "fire", "control_1").getProperty("junket:frame"), 2,
    "selected HOT card must show the HOT frame");
  assert.equal(roleEntity(mock, "fire", "control_2").getProperty("junket:frame"), 1,
    "used card must show the used frame");
  assert.equal(roleEntity(mock, "fire", "control_11").getProperty("junket:frame"), 3,
    "selected ICE card must show the ICE frame");
  assert.equal(roleEntity(mock, "fire", "display_1").getProperty("junket:frame"), 5);
  assert.equal(roleEntity(mock, "fire", "display_1").nameTag, "", "state remains in UI, not dense floating text");
  assert.equal(roleEntity(mock, "fire", "display_2").getProperty("junket:frame"), 10);
  assert.equal(roleEntity(mock, "fire", "display_3").getProperty("junket:frame"), 11);
  assert.equal(roleEntity(mock, "fire", "display_2").nameTag, "");
  assert.equal(roleEntity(mock, "fire", "display_3").nameTag, "");
  assert.equal(roleEntity(mock, "fire", "display_4").nameTag, "");
  assert.match(roleEntity(mock, "fire", "reception").nameTag, /受付/);

  const hidden = {
    players: ["p1", "p2"],
    state: {
      ...revealed.state, revealResolved: false, used: [[], []],
      choices: [
        { id: "1:hot", number: 1, heat: 1 },
        { id: "2:ice", number: 2, heat: -1 },
      ],
    },
  };
  venues.update("fire", hidden);
  assert.equal(roleEntity(mock, "fire", "control_1").getProperty("junket:frame"), 0,
    "a seat which already sealed its simultaneous choice is no longer active");
  venues.update("fire", { ...hidden, state: { ...hidden.state, choices: [null, null] } });
  assert.equal(roleEntity(mock, "fire", "control_1").getProperty("junket:frame"), 4);
  assert.equal(roleEntity(mock, "fire", "control_7").getProperty("junket:frame"), 4,
    "both unanswered seats have active cards in the original simultaneous phase");
  assert.doesNotMatch(roleEntity(mock, "fire", "display_1").nameTag, /グー HOT/,
    "sealed choices must not leak to the TV");

  roleEntity(mock, "fire", "display_2").remove();
  await drain(mock, venues.repair("fire"));
  const restored = roleEntity(mock, "fire", "display_2");
  assert.ok(restored, "repair must restore a missing Fire glass display");
  assert.equal(restored.getProperty("junket:frame"), 6,
    "a repaired display must start from a known healthy frame");
  venues.update("fire", revealed);
  assert.equal(restored.getProperty("junket:frame"), 10,
    "repair must not sever the live presentation link");

  const restarted = createProductionVenues(mock);
  await drain(mock, restarted.repair("fire"));
  restarted.update("fire", revealed);
  assert.equal(roleEntity(mock, "fire", "display_1").nameTag, "");
  assert.equal(roleEntity(mock, "fire", "display_3").getProperty("junket:frame"), 11);
}

// A verified venue must be replaceable when its manifest layout changes. A
// previous installation may leave blocks in cells that the new layout now
// expects to be air, but only the already-verified venue may authorize that
// repair; arbitrary foreign blocks must still be refused.
{
  const mock = createMock({ preserveAirOnPlace: true });
  const first = createProductionVenues(mock);
  const venue = VENUE_CONFIG.VENUES.find(entry => entry.id === "ace");
  const originalLayout = venue.layout;
  await drain(mock, first.repair("ace"));

  const changedLayout = JSON.parse(JSON.stringify(originalLayout));
  const runs = changedLayout.runs.split(";").filter(Boolean).map(value => {
    const [paletteIndex, count] = value.split(",").map(Number);
    return { paletteIndex, count };
  });
  const palette = changedLayout.palette;
  const nonAirRuns = runs.map((run, index) => ({ ...run, index }))
    .filter(run => typeof palette[run.paletteIndex] === "string"
      ? palette[run.paletteIndex] !== "minecraft:air"
      : palette[run.paletteIndex].type !== "minecraft:air");
  assert.ok(nonAirRuns.length >= 2, "the layout needs two non-air runs for this regression");
  const clearedRun = nonAirRuns.at(-1);
  const changedRun = nonAirRuns.at(-2);
  const airIndex = palette.length;
  palette.push("minecraft:air");
  const newBlock = "minecraft:bedrock";
  const changedIndex = palette.length;
  palette.push(newBlock);
  runs[clearedRun.index].paletteIndex = airIndex;
  runs[changedRun.index].paletteIndex = changedIndex;
  changedLayout.runs = runs.map(run => `${run.paletteIndex},${run.count}`).join(";");
  venue.layout = changedLayout;
  const originalParsedLayout = layouts.get("ace");
  layouts.set("ace", parseLayout(venue));

  const [sx, sy, sz] = venue.size;
  const plane = sy * sz;
  const cellAt = index => ({
    x: venue.origin[0] + Math.floor(index / plane),
    y: venue.origin[1] + Math.floor((index % plane) / sz),
    z: venue.origin[2] + (index % sz),
  });
  const startOf = run => runs.slice(0, run.index).reduce((sum, entry) => sum + entry.count, 0);
  const clearedCell = cellAt(startOf(clearedRun));
  const changedCell = cellAt(startOf(changedRun));
  const oldCleared = mock.blocks.get(keyOf(clearedCell));
  const oldChanged = mock.blocks.get(keyOf(changedCell));
  assert.notEqual(oldCleared, newBlock);
  assert.ok(oldCleared);
  assert.ok(oldChanged);

  mock.placements.length = 0;
  const restarted = createProductionVenues(mock);
  const repaired = await drain(mock, restarted.repair("ace"));
  assert.equal(repaired.repaired, true);
  assert.equal(mock.placements.length, 1);
  assert.equal(mock.blocks.get(keyOf(clearedCell)), undefined,
    "the new layout's air cell must remove the old verified block");
  const changedValue = mock.blocks.get(keyOf(changedCell));
  const changedType = typeof changedValue === "string" ? changedValue : changedValue?.type;
  assert.equal(changedType, newBlock,
    "the new layout must replace the old verified block at a changed cell");
  assert.equal(JSON.parse(mock.properties.get(`${VENUE_CONFIG.VENUE_INSTALL_KEY}:ace`)).verified, true);

  venue.layout = originalLayout;
  layouts.set("ace", originalParsedLayout);
}

// Garden's timeout verdict must be physical: open only the managed floor bays,
// detect brown wool under the corresponding player, and restore the complete
// layout after the five-second result hold. A fresh runtime must reopen a floor
// for a restored drop state instead of trusting the installation marker alone.
{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  const garden = VENUE_CONFIG.VENUES.find(entry => entry.id === "garden");
  assert.equal(garden.drop_areas.length, 2);
  const seatPoint = seat => Array.isArray(seat)
    ? { x: seat[0], y: seat[1], z: seat[2] }
    : seat;
  const floorCellKeys = area => {
    const seat = seatPoint(garden.seats[area.seat]);
    const centerX = Math.floor(seat.x);
    const centerZ = Math.floor(seat.z);
    const floorY = Math.floor(seat.y) - 1;
    const keys = [];
    for (let dx = -1; dx <= 1; dx++) {
      for (let dz = -1; dz <= 1; dz++) keys.push(keyAt(centerX + dx, floorY, centerZ + dz));
    }
    return keys;
  };

  for (const area of garden.drop_areas) {
    for (const key of floorCellKeys(area)) assert.equal(mock.blocks.get(key), area.floor);
  }

  const gardenPlayers = garden.seats.map((seat, index) => {
    const point = seatPoint(seat);
    return {
      id: `garden-${index}`,
      dimension: mock.dimension,
      location: { x: point.x, y: point.y, z: point.z },
    };
  });
  mock.players.push(...gardenPlayers);
  const started = { players: gardenPlayers.map(player => player.id), state: createGarden() };
  const dropState = gardenSystem(started.state, "timeout");
  assert.equal(dropState.dropRemaining, 3);
  venues.update("garden", { ...started, state: dropState });
  for (const area of garden.drop_areas) {
    for (const key of floorCellKeys(area)) assert.equal(mock.blocks.get(key), area.floor,
      "the managed floor must stay closed during the source pre-drop delay");
  }
  assert.equal(venues.pollGardenLanding({ ...started, state: dropState }), null,
    "landing must not resolve before the floor opens");

  const openState = gardenSystem(gardenSystem(gardenSystem(dropState, "tick", 1), "tick", 1), "tick", 1);
  assert.equal(openState.dropRemaining, 0);
  venues.update("garden", { ...started, state: openState });
  for (const area of garden.drop_areas) {
    for (const key of floorCellKeys(area)) assert.equal(mock.blocks.get(key), undefined,
      "after the delay a double timeout must physically open both managed floor bays");
  }

  const firstSeat = seatPoint(garden.seats[0]);
  const secondSeat = seatPoint(garden.seats[1]);
  gardenPlayers[1].location.y = Math.floor(secondSeat.y) - 1;
  assert.equal(venues.pollGardenLanding({ ...started, state: openState }), 1,
    "brown wool under the second seat must be detected without a form");
  gardenPlayers[0].location.y = Math.floor(firstSeat.y) - 1;
  assert.equal(venues.pollGardenLanding({ ...started, state: openState }), 0,
    "simultaneous landing follows the source's player-1-first command order");

  gardenPlayers[0].location.y = firstSeat.y;
  const landed = gardenSystem(openState, "land", 1);
  assert.equal(landed.phase, "result");
  venues.update("garden", { ...started, state: landed });
  for (const key of floorCellKeys(garden.drop_areas[0])) {
    assert.equal(mock.blocks.get(key), garden.drop_areas[0].floor,
      "the winner's floor must close as soon as the loser's bay is opened");
  }
  for (const key of floorCellKeys(garden.drop_areas[1])) {
    assert.equal(mock.blocks.get(key), undefined,
      "the loser's bay remains open through the five-second end hold");
  }

  const finished = gardenSystem(landed, "tick", 5);
  venues.update("garden", { ...started, state: finished });
  for (const area of garden.drop_areas) {
    for (const key of floorCellKeys(area)) assert.equal(mock.blocks.get(key), area.floor);
  }

  const restarted = createProductionVenues(mock);
  restarted.update("garden", { ...started, state: dropState });
  for (const area of garden.drop_areas) {
    for (const key of floorCellKeys(area)) assert.equal(mock.blocks.get(key), area.floor,
      "a restored pre-drop state must remain closed after restart");
  }
  restarted.update("garden", { ...started, state: openState });
  for (const area of garden.drop_areas) {
    for (const key of floorCellKeys(area)) assert.equal(mock.blocks.get(key), undefined,
      "a restored opened-floor state must reopen the physical floor after restart");
  }

  const manager = new GameManager([gardenGame], { world: mock.world });
  manager.join("garden", "garden", gardenPlayers[0].id);
  manager.join("garden", "garden", gardenPlayers[1].id);
  manager.systemAct("garden", "timeout");
  const restoredManager = new GameManager([gardenGame], { world: mock.world });
  assert.deepEqual(restoredManager.restore(), { restored: 1, skipped: 0 });
  let restored = restoredManager.forPlayer(gardenPlayers[0].id);
  assert.equal(restored.state.phase, "drop");
  assert.equal(restored.state.dropRemaining, 3);
  venues.update("garden", restored);
  for (const key of floorCellKeys(garden.drop_areas[0])) {
    assert.equal(mock.blocks.get(key), garden.drop_areas[0].floor,
      "a GameManager restore must keep the pre-drop floor closed");
  }
  restoredManager.systemAct("garden", "tick", 1);
  restoredManager.systemAct("garden", "tick", 1);
  restoredManager.systemAct("garden", "tick", 1);
  restored = restoredManager.forPlayer(gardenPlayers[0].id);
  venues.update("garden", restored);
  for (const key of floorCellKeys(garden.drop_areas[0])) {
    assert.equal(mock.blocks.get(key), undefined,
      "a restored GameManager session must open the floor after the source delay");
  }
  gardenPlayers[0].location.y = Math.floor(firstSeat.y) - 1;
  gardenPlayers[1].location.y = Math.floor(secondSeat.y) - 1;
  const restoredLanding = venues.pollGardenLanding(restored);
  assert.equal(restoredLanding, 0,
    "a restored session must accept the physical landing instead of a replacement form");
  assert.equal(restoredManager.systemAct("garden", "land", restoredLanding).state.phase, "result");
}

// Rabbit CPUs must remain physical falling tokens. Their water observation,
// not the drop timer or installation marker, decides elimination. A guarded
// floor stays closed, and a runtime restart must resume polling the same token.
{
  // Source rabbit/start.mcfunction: p1 and p3..p6 are the five escapers; p2 is
  // the oni seat and must never appear in the escape participant list.
  const initial = createRabbit();
  assert.deepEqual(initial.participants.map(participant => participant.id),
    ["p1", "p3", "p4", "p5", "p6"]);
  assert.deepEqual(initial.participants.map(participant => participant.position),
    [25, 1, 13, 37, 49]);
  assert.equal(initial.phase, "trap");
  assert.equal(initial.actor, 1);
  assert.equal(initial.trapQuota, 5);

  // Source set/trap/first.mcfunction assigns あ/か/さ/た/な per row and marks
  // the current positions with う/く/す/つ/ぬ.
  const initialFloors = rabbitFloorCategories(initial);
  assert.deepEqual(initialFloors.slice(0, 12),
    ["う", ...Array.from({ length: 11 }, () => "あ")]);
  assert.equal(initialFloors[12], "く");
  assert.equal(initialFloors[24], "す");
  assert.equal(initialFloors[36], "つ");
  assert.equal(initialFloors[48], "ぬ");
  assert.deepEqual(rabbitLegalTrapFloors(initial),
    Array.from({ length: 60 }, (_, index) => index + 1));

  // A trap on an occupied floor is legal (the source marks it with the row
  // number, not by excluding the floor). A second trap on that floor is not.
  let trapping = rabbitGame.advance(initial, 1, "trap", 25);
  assert.equal(rabbitFloorCategories(trapping)[24], "3");
  assert.ok(!rabbitLegalTrapFloors(trapping).includes(25));
  assert.throws(() => rabbitGame.advance(trapping, 1, "trap", 25), /既に罠/);
  trapping = rabbitGame.advance(trapping, 1, "trap", 2);
  assert.equal(rabbitFloorCategories(trapping)[1], "お");
  assert.ok(!rabbitLegalTrapFloors(trapping).includes(2));

  // Source move/canmove.mcfunction permits the current tile or an orthogonal
  // move of one to three tiles, never a diagonal or a fourth tile.
  const moves = rabbitLegalMoves(initial, "p1");
  for (const tile of [25, 26, 27, 28]) {
    assert.ok(moves.includes(tile), `Rabbit move ${tile} must be legal`);
  }
  for (const tile of [13, 1, 37, 49, 24, 21, 38, 12, 36, 61]) {
    assert.ok(!moves.includes(tile), `Rabbit move ${tile} must be illegal`);
  }
  const physicalStay = rabbitSystem({ ...initial, phase: "move", actor: 0 },
    "position", { tile: 25 });
  assert.equal(physicalStay.phase, "travel",
    "a physical floor press on the current tile must confirm the legal stay move");
  assert.deepEqual(physicalStay.plannedMoves.find(move => move.id === "p1"),
    { id: "p1", from: 25, to: 25 });

  // Source open/open.mcfunction: 20 ticks to the move snapshot, 60 ticks of
  // movement, a 20-tick hidden settle, 20 ticks of trap reveal, 30 ticks of
  // floor drop, then the result.
  let sequence = createRabbit();
  for (const tile of [2, 3, 4, 5, 6]) {
    sequence = rabbitGame.advance(sequence, 1, "trap", tile);
  }
  assert.equal(sequence.phase, "move");
  assert.equal(sequence.actor, 0);
  assert.deepEqual(rabbitView(sequence).traps, [],
    "trap floors must stay hidden from the escape side");
  sequence = rabbitGame.advance(sequence, 0, "move", 25);
  assert.equal(sequence.phase, "travel");
  assert.equal(sequence.phaseRemaining, 20);
  for (let tick = 0; tick < 20; tick++) sequence = rabbitSystem(sequence, "tick");
  assert.equal(sequence.phase, "advance");
  assert.equal(sequence.phaseRemaining, 60);
  for (let tick = 0; tick < 60; tick++) sequence = rabbitSystem(sequence, "tick");
  assert.equal(sequence.phase, "settle");
  assert.equal(sequence.phaseRemaining, 20);
  assert.deepEqual(rabbitView(sequence).traps, [],
    "traps remain hidden during the source's post-move settle window");
  for (let tick = 0; tick < 20; tick++) sequence = rabbitSystem(sequence, "tick");
  assert.equal(sequence.phase, "reveal");
  assert.equal(sequence.phaseRemaining, 20);
  assert.deepEqual(rabbitView(sequence).traps, [2, 3, 4, 5, 6]);
  for (let tick = 0; tick < 20; tick++) sequence = rabbitSystem(sequence, "tick");
  assert.equal(sequence.phase, "drop");
  assert.equal(sequence.phaseRemaining, 30);
  for (let tick = 0; tick < 29; tick++) sequence = rabbitSystem(sequence, "tick");
  assert.equal(sequence.phase, "drop");
  assert.equal(sequence.phaseRemaining, 1,
    "the drop window must count down on real ticks");
  sequence = rabbitSystem(sequence, "tick");
  assert.equal(sequence.phase, "drop");
  assert.equal(sequence.phaseRemaining, 0,
    "the 30th drop tick must leave phaseRemaining at zero");
  assert.equal(rabbitSystem(sequence, "tick").phaseRemaining, 0,
    "a resolved drop must not rewind the timer");

  // Source timeup/player-2.mcfunction: an oni timeout hands victory to the
  // escape side; timeup/player-1.mcfunction hands it to the oni.
  let oniTimeout = createRabbit();
  for (let tick = 0; tick < 6000; tick++) oniTimeout = rabbitSystem(oniTimeout, "tick");
  assert.equal(oniTimeout.phase, "result");
  assert.equal(oniTimeout.winnerTeam, 0);
  assert.equal(oniTimeout.reason, "trap_timeout");
  assert.equal(oniTimeout.phaseRemaining, RABBIT_RESULT_TICKS);
  for (let tick = 0; tick < RABBIT_RESULT_TICKS - RABBIT_RESULT_OPEN_TICKS - 1; tick++) {
    oniTimeout = rabbitSystem(oniTimeout, "tick");
  }
  assert.equal(oniTimeout.oniFloorOpen, false,
    "floor 61 stays closed for the first 20 ticks of the result");
  oniTimeout = rabbitSystem(oniTimeout, "tick");
  assert.equal(oniTimeout.oniFloorOpen, true,
    "the oni timeout opens floor 61 after its announcement");
  for (let tick = 0; tick < RABBIT_RESULT_OPEN_TICKS; tick++) {
    oniTimeout = rabbitSystem(oniTimeout, "tick");
  }
  assert.equal(oniTimeout.phase, "finished");
  let escapeTimeout = createRabbit();
  for (const tile of [2, 3, 4, 5, 6]) {
    escapeTimeout = rabbitGame.advance(escapeTimeout, 1, "trap", tile);
  }
  for (let tick = 0; tick < 6000; tick++) escapeTimeout = rabbitSystem(escapeTimeout, "tick");
  assert.equal(escapeTimeout.phase, "result");
  assert.equal(escapeTimeout.winnerTeam, 1);
  assert.equal(escapeTimeout.reason, "move_timeout");
  assert.equal(escapeTimeout.phaseRemaining, RABBIT_RESULT_TICKS);
  for (let tick = 0; tick < RABBIT_RESULT_TICKS - RABBIT_RESULT_OPEN_TICKS - 1; tick++) {
    escapeTimeout = rabbitSystem(escapeTimeout, "tick");
  }
  assert.equal(escapeTimeout.allFloorsOpen, false);
  escapeTimeout = rabbitSystem(escapeTimeout, "tick");
  assert.equal(escapeTimeout.allFloorsOpen, true,
    "the escape timeout opens all sixty floors after its announcement");
  for (let tick = 0; tick < RABBIT_RESULT_OPEN_TICKS; tick++) {
    escapeTimeout = rabbitSystem(escapeTimeout, "tick");
  }
  assert.equal(escapeTimeout.phase, "finished");

  // Source open/wincheck.mcfunction: a goal on 12/24/36/48/60 wins for the
  // escape side, while losing every escaper wins for the oni.
  const allIds = initial.participants.map(participant => participant.id);
  const goal = rabbitSystem({
    ...initial, phase: "drop", actor: null, phaseRemaining: 30,
    traps: [13], dropFloors: [], falling: [], guardedFloors: [],
    participants: initial.participants.map(participant =>
      participant.id === "p1" ? { ...participant, position: 12 } : participant),
  }, "land", []);
  assert.equal(goal.phase, "result");
  assert.equal(goal.winnerTeam, 0);
  assert.equal(goal.reason, "goal");
  assert.equal(goal.phaseRemaining, RABBIT_RESULT_TICKS);
  const wiped = rabbitSystem({
    ...initial, phase: "drop", actor: null, phaseRemaining: 30,
    traps: [], dropFloors: [], falling: [], guardedFloors: [],
  }, "land", allIds);
  assert.equal(wiped.phase, "result");
  assert.equal(wiped.winnerTeam, 1);
  assert.equal(wiped.reason, "全滅");
  assert.equal(wiped.phaseRemaining, RABBIT_WIPE_RESULT_TICKS,
    "wincheck's wipe result lasts five seconds");
  assert.equal(wiped.oniFloorOpen, false);
  assert.equal(wiped.allFloorsOpen, false);
}

// Rabbit CPUs must remain physical falling tokens. Their water observation,
// not the drop timer or installation marker, decides elimination. A guarded
// floor stays closed, and a runtime restart must resume polling the same token.
{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  const rabbit = VENUE_CONFIG.VENUES.find(entry => entry.id === "rabbit");
  assert.equal(rabbit.rabbit.cells.length, 60);
  assert.deepEqual(rabbit.seat_rotations, [90, -90],
    "Rabbit must face the source escaper west and the source oni east");
  const rabbitByTile = new Map(rabbit.rabbit.cells.map(cell => [cell.tile, cell]));
  const guardFixtures = rabbit.fixtures.filter(fixture =>
    /^guard_[3-6]$/.test(String(fixture.action ?? "")));
  assert.deepEqual(guardFixtures.map(fixture => fixture.action).sort(),
    ["guard_3", "guard_4", "guard_5", "guard_6"],
    "Rabbit's four manual guards must be real escape-side controls");
  const guardShelf = rabbit.rabbit.guard_shelf;
  const oniCells = new Set(rabbit.rabbit.oni.floor_cells.map(value => keyAt(...value)));
  for (const fixture of guardFixtures) {
    const [x, y, z] = fixture.location;
    assert.ok(x > rabbit.rabbit.max_x,
      `${fixture.action} must sit outside the legal board on the escape side`);
    assert.ok(x >= guardShelf.min_x && x <= guardShelf.max_x &&
      z >= guardShelf.min_z && z <= guardShelf.max_z,
    `${fixture.action} must stand on the authored safe shelf`);
    assert.ok(Math.abs(y - (guardShelf.floor_y + 1.05)) <= 0.11,
      `${fixture.action} must stand one block above the shelf floor`);
    assert.ok(!rabbit.rabbit.cells.some(cell => cell.floor_cells.some(value =>
      Math.floor(x) === value[0] && Math.floor(z) === value[2])),
    `${fixture.action} must not occupy a legal 3x3 floor`);
    assert.ok(!oniCells.has(keyAt(Math.floor(x), Math.floor(y) - 1, Math.floor(z))),
      `${fixture.action} must not reuse the oni-side tile 61 platform`);
    assert.ok(fixture.tags.includes("junket:rabbit:control"),
      `${fixture.action} must be wired as a control entity`);
  }
  assert.deepEqual(physicalControlRequest("rabbit", { phase: "move" }, "guard_3"),
    { action: "guard", value: "p3" },
    "pressing a guard fixture must request the matching guard action");
  assert.equal(physicalControlRequest("rabbit", { phase: "trap" }, "guard_3"), null);
  const guardTurn = { ...createRabbit(), phase: "move", actor: 0 };
  assert.equal(rabbitGame.advance(guardTurn, 0, "guard", "p3")
    .participants.find(participant => participant.id === "p3").guard, true,
  "the physical guard control must execute as escaper seat 0");
  assert.throws(() => rabbitGame.advance(
    { ...createRabbit(), phase: "move", actor: 1 }, 1, "guard", "p3"),
    /お守りを使えるのは脱出側です/);
  assert.equal(rabbitByTile.get(1).x, rabbitByTile.get(13).x);
  assert.equal(rabbitByTile.get(1).x - rabbitByTile.get(2).x, 3,
    "Rabbit tile columns must decrease along X like the source");
  assert.equal(rabbitByTile.get(1).z - rabbitByTile.get(13).z, 3,
    "Rabbit tile rows must decrease along Z like the source");
  for (const tile of [13, 25, 37, 49, 60]) {
    assert.ok(rabbitByTile.get(tile - 12).z > rabbitByTile.get(tile).z,
      `Rabbit tile ${tile} must be north of tile ${tile - 12}`);
  }
  assert.ok(rabbit.rabbit.oni, "tile 61 must be stored separately from the 60 legal tiles");
  assert.equal(rabbit.rabbit.oni.tile, 61);
  assert.equal(rabbit.rabbit.oni.floor_cells.length, 9);
  assert.ok(!rabbit.rabbit.cells.some(cell => cell.tile === 61));
  assert.ok(!(rabbit.drop_areas ?? []).some(area => area.tile === 61));
  for (const cell of rabbit.rabbit.cells) {
    assert.equal(cell.floor_cells.length, 9, `Rabbit tile ${cell.tile} must be 3x3`);
    assert.equal(new Set(cell.floor_cells.map(value => keyAt(...value))).size, 9);
  }
  const base = createRabbit();
  const human = base.participants.find(participant => participant.id === "p1");
  const falling = base.participants.find(participant => participant.id === "p3");
  const guarded = base.participants.find(participant => participant.id === "p4");
  const fallingCell = rabbit.rabbit.cells.find(cell => cell.tile === falling.position);
  const guardedCell = rabbit.rabbit.cells.find(cell => cell.tile === guarded.position);
  const player = {
    id: "rabbit-physical", dimension: mock.dimension,
    location: { x: fallingCell.x + 0.5, y: rabbit.rabbit.stand_y, z: fallingCell.z + 0.5 },
    teleports: [],
    teleport(location, options) {
      this.teleports.push({ location: { ...location }, options });
      this.location = { ...location };
    },
  };
  mock.players.push(player);
  const floorKeys = cell => cell.floor_cells.map(value => keyAt(...value));
  const waterKeys = cell => cell.water_cells.map(value => keyAt(...value));
  assert.ok(waterKeys(fallingCell).every(key => mock.blocks.get(key) === "minecraft:water"));
  const state = {
    ...base, phase: "drop", actor: null, phaseRemaining: 30,
    traps: [falling.position, guarded.position], dropFloors: [falling.position],
    guardedFloors: [guarded.position], falling: [falling.id], guarded: [guarded.id], dropped: [],
  };
  const current = { players: [player.id], state };
  venues.update("rabbit", current);
  assert.equal(player.teleports.length, 1,
    "Rabbit must move the human player onto their own physical floor tile");
  assert.deepEqual(player.teleports[0].location, {
    x: rabbit.rabbit.cells.find(cell => cell.tile === human.position).x + 0.5,
    y: rabbit.rabbit.stand_y,
    z: rabbit.rabbit.cells.find(cell => cell.tile === human.position).z + 0.5,
  });
  assert.equal(player.teleports[0].options.rotation.y, 90,
    "the source escaper must face west after a floor teleport");
  const movedHumanState = {
    ...state, phase: "move",
    participants: state.participants.map(participant => participant.id === human.id
      ? { ...participant, position: 13 } : participant),
  };
  venues.update("rabbit", { ...current, state: movedHumanState });
  assert.equal(player.teleports.at(-1).options.rotation.y, 90,
    "Rabbit must reapply the source west-facing rotation after the human changes floor");
  assert.deepEqual(player.teleports.at(-1).location, {
    x: rabbitByTile.get(13).x + 0.5,
    y: rabbit.rabbit.stand_y,
    z: rabbitByTile.get(13).z + 0.5,
  });
  venues.update("rabbit", current);
  assert.ok(floorKeys(fallingCell).every(key => mock.blocks.get(key) === undefined),
    "the trapped floor must physically open");
  assert.ok(floorKeys(guardedCell).every(key => mock.blocks.get(key) === guardedCell.floor),
    "a guarded floor must stay closed");
  const fallingCpu = mock.entities.filter(entity => !entity.removed &&
    entity.hasTag("junket:rabbit:cpu:p3"));
  const guardedCpu = mock.entities.filter(entity => !entity.removed &&
    entity.hasTag("junket:rabbit:cpu:p4"));
  assert.equal(fallingCpu.length, 1);
  assert.equal(guardedCpu.length, 1);
  assert.equal(fallingCpu[0].location.y, rabbit.rabbit.stand_y,
    "a CPU starts the drop above its now-open floor");
  assert.deepEqual(venues.pollRabbitLanding(current), [],
    "an airborne CPU must not be resolved by elapsed time alone");
  fallingCpu[0].location = {
    x: fallingCell.x + 0.5, y: rabbit.rabbit.water_y + 0.1, z: fallingCell.z + 0.5,
  };
  venues.update("rabbit", current);
  assert.deepEqual(venues.pollRabbitLanding(current), [falling.id],
    "water must be sampled every tick during the source's drop window");
  assert.deepEqual(rabbitSystem(state, "observe", [falling.id]).waterHits, [falling.id],
    "an early water hit must be persisted before the drop deadline");

  // An early water hit remains an elimination even if the token is back on a
  // surviving floor when the source closes the board at the end of the window.
  fallingCpu[0].location = {
    x: fallingCell.x + 0.5, y: rabbit.rabbit.stand_y, z: fallingCell.z + 0.5,
  };
  const observedDrop = rabbitSystem(state, "observe", [falling.id]);
  const timedOutDrop = { ...current, state: { ...observedDrop, phaseRemaining: 0 } };
  assert.deepEqual(venues.pollRabbitLanding(timedOutDrop), [],
    "the deadline reports no new water sighting once the token is back above water");
  const resolvedDrop = rabbitSystem(timedOutDrop.state, "land", []);
  assert.equal(resolvedDrop.phase, "trap",
    "an observed drop must advance instead of leaving the match stuck");
  assert.equal(resolvedDrop.round, 2);
  assert.equal(resolvedDrop.participants.find(participant => participant.id === falling.id).alive,
    false, "a persisted early water hit must still eliminate the participant");

  // A token that never reaches water must not be eliminated by the deadline.
  const unobservedDrop = { ...current, state: { ...state, waterHits: [], phaseRemaining: 0 } };
  venues.update("rabbit", unobservedDrop);
  assert.equal(venues.pollRabbitLanding(unobservedDrop).length, 0,
    "an unobserved token must not be reported as landed");
  const unobservedLanding = rabbitSystem(unobservedDrop.state, "land", []);
  assert.equal(unobservedLanding.phase, "trap");
  assert.equal(unobservedLanding.round, 2);
  assert.equal(unobservedLanding.participants.find(participant => participant.id === falling.id).alive,
    true);

  // Source losecheck.mcfunction samples all five escapee position markers,
  // not only the floors it predicted would fall. A solid guarded floor still
  // prevents a false hit in normal play; actual water contact is observable.
  guardedCpu[0].location = {
    x: guardedCell.x + 0.5, y: rabbit.rabbit.water_y + 0.1, z: guardedCell.z + 0.5,
  };
  assert.deepEqual(venues.pollRabbitLanding(current), [guarded.id],
    "water observation must cover every living participant");
  assert.deepEqual(rabbitSystem(state, "observe", [guarded.id]).waterHits, [guarded.id],
    "a non-falling participant's physical water contact must be persistable");
  assert.equal(rabbitSystem(state, "observe", ["p999"]).waterHits.length, 0,
    "unknown participants must never enter the water-hit set");
  guardedCpu[0].location = {
    x: guardedCell.x + 0.5, y: rabbit.rabbit.stand_y, z: guardedCell.z + 0.5,
  };
  // rabbit/losecheck.mcfunction tests only the participant position marker's
  // own block. Water one level above or below the feet must not eliminate a
  // participant before the floor actually opens.
  const aboveKey = keyAt(guardedCell.x, rabbit.rabbit.stand_y + 1, guardedCell.z);
  mock.blocks.set(aboveKey, "minecraft:water");
  assert.deepEqual(venues.pollRabbitLanding(current), [],
    "water above a participant's feet must not be mistaken for a fall");
  mock.blocks.delete(aboveKey);
  venues.update("rabbit", current);

  // A round with no falling tokens still consumes the physical drop window.
  const emptyWindowStart = {
    ...base, phase: "drop", actor: null, phaseRemaining: 30,
    traps: [], dropFloors: [], falling: [], guardedFloors: [], dropped: [],
  };
  let emptyWindow = emptyWindowStart;
  const emptyCurrent = { ...current, state: emptyWindow };
  assert.deepEqual(venues.pollRabbitLanding(emptyCurrent), [],
    "an empty drop must not resolve before the source window ends");
  for (let tick = 0; tick < 30; tick++) emptyWindow = rabbitSystem(emptyWindow, "tick");
  assert.equal(emptyWindow.phaseRemaining, 0);
  assert.deepEqual(venues.pollRabbitLanding({ ...current, state: emptyWindow }), []);
  const emptyLanding = rabbitSystem(emptyWindow, "land", []);
  assert.equal(emptyLanding.phase, "trap");
  assert.equal(emptyLanding.round, 2);

  fallingCpu[0].location = {
    x: fallingCell.x + 0.5, y: rabbit.rabbit.water_y + 0.1, z: fallingCell.z + 0.5,
  };
  venues.update("rabbit", current);
  assert.equal(fallingCpu[0].location.y, rabbit.rabbit.water_y + 0.1,
    "the venue observer must not pin a falling CPU back onto the board");
  assert.deepEqual(venues.pollRabbitLanding(timedOutDrop), [falling.id],
    "only CPUs physically observed in water may be reported as landed");
  const applied = rabbitSystem(state, "land", [falling.id]);
  assert.equal(applied.participants.find(participant => participant.id === falling.id).alive, false);
  assert.equal(applied.participants.find(participant => participant.id === guarded.id).alive, true,
    "a guarded CPU must survive the drop");

  const restarted = createProductionVenues(mock);
  restarted.update("rabbit", current);
  const resumedCpu = mock.entities.filter(entity => !entity.removed &&
    entity.hasTag("junket:rabbit:cpu:p3"));
  assert.equal(resumedCpu.length, 1, "restart must reuse the persisted CPU token");
  assert.equal(resumedCpu[0].location.y, rabbit.rabbit.water_y + 0.1,
    "restart must not rewind a CPU that already entered the water");
  assert.ok(floorKeys(fallingCell).every(key => mock.blocks.get(key) === undefined),
    "restart during the drop must not close the physically opened trap floor");
  assert.deepEqual(restarted.pollRabbitLanding(timedOutDrop), [falling.id],
    "a restored drop state must resume the physical water observation");

  const humanCell = rabbitByTile.get(human.position);
  player.location = {
    x: humanCell.x + 0.5, y: rabbit.rabbit.water_y + 0.1, z: humanCell.z + 0.5,
  };
  const humanDrop = {
    ...observedDrop, phaseRemaining: 0,
    dropFloors: [falling.position, human.position],
    falling: [falling.id, human.id],
    waterHits: [falling.id, human.id],
  };
  const teleportsBeforeRestart = player.teleports.length;
  restarted.update("rabbit", { ...current, state: humanDrop });
  assert.equal(player.teleports.length, teleportsBeforeRestart,
    "restart must not rewind a human who already fell below the trap floor");
  assert.equal(player.location.y, rabbit.rabbit.water_y + 0.1);
  assert.deepEqual(restarted.pollRabbitLanding({ ...current, state: humanDrop })
    .sort(), [falling.id, human.id].sort(),
  "restart must resume water observation for both the human and CPU tokens");
  const humanDeadline = rabbitSystem(humanDrop, "land", []);
  assert.equal(humanDeadline.participants
    .find(participant => participant.id === human.id).alive, false,
  "a persisted human water hit must eliminate at the restored deadline");
  assert.equal(humanDeadline.participants
    .find(participant => participant.id === falling.id).alive, false,
  "a persisted CPU water hit must eliminate at the same restored deadline");

  // A second match can reach the same result frame as the previous match.
  // The oni's presentation key must be cleared when the result leaves the
  // screen, otherwise the identical round/reason/open frame is silently
  // skipped and the source floor-61 drop never plays again.
  const oniPlayer = {
    id: "rabbit-oni", dimension: mock.dimension,
    location: {
      x: rabbit.rabbit.oni.x + 0.5, y: rabbit.rabbit.stand_y,
      z: rabbit.rabbit.oni.z + 0.5,
    },
    rotation: { x: 0, y: -90 },
    teleports: [],
    teleport(location, options) {
      this.teleports.push({ location: { ...location }, options });
      this.location = { ...location };
    },
  };
  mock.players.push(oniPlayer);
  const namedPlayers = [player.id, oniPlayer.id];
  const resultState = {
    ...base, phase: "result", actor: null,
    phaseRemaining: RABBIT_RESULT_TICKS, reason: "goal",
    oniFloorOpen: false, allFloorsOpen: false,
    traps: [], dropFloors: [], guardedFloors: [], falling: [],
  };
  venues.update("rabbit", { players: namedPlayers, state: resultState });
  assert.equal(oniPlayer.teleports.length, 1,
    "the first Rabbit result must position the oni player");
  venues.update("rabbit", {
    players: namedPlayers,
    state: { ...resultState, oniFloorOpen: true },
  });
  assert.equal(oniPlayer.teleports.length, 2,
    "opening floor 61 must drop the oni player once");
  assert.equal(oniPlayer.teleports.at(-1).location.y, rabbit.rabbit.water_y,
    "the opened oni floor must place the player in the water cell");
  venues.update("rabbit", {
    players: namedPlayers,
    state: { ...resultState, phase: "trap", phaseRemaining: 6000 },
  });
  venues.update("rabbit", {
    players: namedPlayers,
    state: { ...resultState, oniFloorOpen: true },
  });
  assert.equal(oniPlayer.teleports.length, 3,
    "a later match with an identical result key must replay the oni drop");

  // The early water hit, not a second polling chance after restart, must be
  // persisted with the GameManager session and applied at the deadline.
  const savedManager = new GameManager([rabbitGame], { world: mock.world });
  savedManager.join("rabbit", "rabbit", player.id);
  savedManager.join("rabbit", "rabbit", "rabbit-second-member");
  savedManager.tables.get("rabbit").state = { ...observedDrop, phaseRemaining: 0 };
  savedManager.persist();
  const restoredManager = new GameManager([rabbitGame], { world: mock.world });
  assert.deepEqual(restoredManager.restore(), { restored: 1, skipped: 0 });
  const restoredRabbit = restoredManager.forPlayer(player.id);
  assert.deepEqual(restoredManager.tables.get("rabbit").state.waterHits, [falling.id],
    "an early water hit must survive a BDS restart");
  assert.equal(restoredRabbit.state.phase, "drop");
  const restoredDrop = restoredManager.systemAct("rabbit", "land", []);
  assert.equal(restoredDrop.state.participants
    .find(participant => participant.id === falling.id).alive, false,
  "the restored deadline must apply the persisted physical water hit");
  assert.equal(human.position, 25);
  mock.players.splice(mock.players.indexOf(player), 1);
}

// The saved Rabbit floor props must expose the source transition visually:
// trap/guard/falling frames, a pitched falling floor, restored blocks outside
// the drop window, and no elimination observation for the oni seat p2.
{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  const rabbit = VENUE_CONFIG.VENUES.find(entry => entry.id === "rabbit");
  const base = createRabbit();
  const state = {
    ...base, phase: "drop", actor: null, phaseRemaining: 30,
    traps: [1, 13], dropFloors: [1], guardedFloors: [13],
    falling: ["p3"], guarded: ["p4"], dropped: [],
  };
  const current = { players: [], state };
  venues.update("rabbit", current);
  const frameOf = tile => roleEntity(mock, "rabbit", `control_${tile}`)
    ?.getProperty("junket:frame");
  const rotationOf = tile => roleEntity(mock, "rabbit", `control_${tile}`)?.rotation;
  assert.equal(frameOf(1), 3, "a dropped Rabbit floor must use the falling frame");
  assert.equal(rotationOf(1)?.x, 61.9275,
    "the source right_rotation pitch must be applied to the falling floor prop");
  assert.equal(frameOf(13), 2, "a guarded Rabbit floor must use the guarded frame");
  assert.equal(rotationOf(13)?.x, 0, "a guarded floor must stay upright");
  assert.equal(frameOf(25), 0, "a normal Rabbit floor must use the normal frame");
  const opened = mock.sounds.filter(sound => sound.identifier === "block.copper_trapdoor.open").length;
  assert.equal(opened, 1, "opening the source floors must play the copper trapdoor sound once");
  venues.update("rabbit", current);
  assert.equal(mock.sounds.filter(sound => sound.identifier === "block.copper_trapdoor.open").length,
    opened, "an unchanged drop state must not replay the opening sound");

  assert.equal(state.participants.some(participant => participant.id === "p2"), false,
    "Rabbit state must keep the oni seat separate from the five escapers");
  assert.deepEqual(rabbitSystem(state, "observe", ["p2"]).waterHits, [],
    "the oni must never be persisted as a fallen escaper");
  assert.equal(mock.entities.filter(entity => !entity.removed &&
    entity.hasTag("junket:rabbit:cpu:p2")).length, 0,
  "the venue must not create an escaper CPU token for p2");
  const oni = rabbit.rabbit.oni;
  const p2Marker = mock.dimension.spawnEntity("minecraft:armor_stand", {
    x: oni.x + 0.5, y: rabbit.rabbit.water_y + 0.1, z: oni.z + 0.5,
  });
  p2Marker.addTag("junket:rabbit:cpu:p2");
  assert.deepEqual(venues.pollRabbitLanding(current), [],
    "a p2 marker in water must not be observed as a lost escaper");
  p2Marker.remove();

  const reveal = {
    ...state, phase: "reveal", phaseRemaining: 20, traps: [1, 13],
    dropFloors: [], falling: [], guardedFloors: [], dropped: [],
  };
  venues.update("rabbit", { players: [], state: reveal });
  assert.equal(frameOf(1), 1, "a revealed trap must use the trap frame");
  assert.equal(rotationOf(1)?.x, 0, "a revealed floor must return upright");
  assert.equal(frameOf(13), 1, "a formerly guarded floor is revealed as a trap");
  assert.equal(frameOf(25), 0, "an untrapped floor must remain normal");
  for (const cell of rabbit.rabbit.cells.filter(value => value.tile === 1)) {
    assert.ok(cell.floor_cells.every(([x, y, z]) =>
      mock.blocks.get(keyAt(x, y, z)) === cell.floor),
    "closing the drop window must restore every floor block");
  }
  assert.equal(mock.sounds.filter(sound => sound.identifier === "block.copper_trapdoor.close").length,
    1, "closing the drop window must play the copper trapdoor close sound once");
  assert.equal(mock.sounds.filter(sound => sound.identifier === "block.note_block.bit").length,
    1, "entering the reveal phase must play the source note block sound once");
  venues.update("rabbit", { players: [], state: reveal });
  assert.equal(mock.sounds.filter(sound => sound.identifier === "block.note_block.bit").length,
    1, "an unchanged reveal state must not replay the note block sound");

  const restarted = createProductionVenues(mock);
  restarted.update("rabbit", { players: [], state: reveal });
  assert.equal(frameOf(1), 1, "a restart must restore the revealed trap frame");
  assert.equal(frameOf(13), 1, "a restart must restore every trap frame");
  for (const cell of rabbit.rabbit.cells.filter(value => value.tile === 1)) {
    assert.ok(cell.floor_cells.every(([x, y, z]) =>
      mock.blocks.get(keyAt(x, y, z)) === cell.floor),
    "a restart outside the drop window must keep the floor closed");
  }
}

{
  const mock = createMock();
  const venues = createProductionVenues(mock);
  const special = specialSetup(mock);
  await drain(mock, venues.installAll({ installControls() { return true; } }, special.providers));
  const hope = VENUE_CONFIG.VENUES.find(entry => entry.id === "hope");
  assert.equal(hope.hope_pads.length, 16);
  assert.equal(hope.hope_ai_spawns.length, HOPE_AI_TRADERS);
  const hopeCells = new Set();
  for (const pad of hope.hope_pads) {
    assert.equal(pad.cells.length, 16, `Hope pad ${pad.pad} must contain 16 cells`);
    const xs = pad.cells.map(cell => cell.x);
    const zs = pad.cells.map(cell => cell.z);
    assert.equal(Math.max(...xs) - Math.min(...xs), 3);
    assert.equal(Math.max(...zs) - Math.min(...zs), 3);
    for (const cell of pad.cells) {
      const key = keyAt(cell.x, cell.y, cell.z);
      assert.ok(!hopeCells.has(key), `Hope pad ${pad.pad} overlaps at ${key}`);
      hopeCells.add(key);
      assert.equal(mock.blocks.get(key), pad.floor,
        `Hope pad ${pad.pad} must be a continuous 4x4 floor`);
    }
  }
  assert.equal(hopeCells.size, 256, "Hope pads must fill the source 16x16 barrier");
  assert.equal(hope.hope_pit_depth, 5,
    "Hope must save the five-block fall shaft below the removable pads");
  for (const pad of hope.hope_pads) {
    for (const cell of pad.cells) {
      for (let depth = 1; depth <= hope.hope_pit_depth; depth++) {
        assert.equal(mock.blocks.get(keyAt(cell.x, cell.y - depth, cell.z)), undefined,
          `Hope pad ${pad.pad} fall shaft is blocked at depth ${depth}`);
      }
    }
  }
  assert.deepEqual(hope.hope_ai_spawns.map(spawn => spawn.id),
    Array.from({ length: HOPE_AI_TRADERS }, (_, index) => index + 1));
  for (const spawn of hope.hope_ai_spawns) {
    assert.ok(spawn.x >= hope.origin[0] && spawn.x < hope.origin[0] + hope.size[0]);
    assert.ok(spawn.y >= hope.origin[1] && spawn.y < hope.origin[1] + hope.size[1]);
    assert.ok(spawn.z >= hope.origin[2] && spawn.z < hope.origin[2] + hope.size[2]);
    assert.equal(mock.blocks.get(keyAt(spawn.x, spawn.y, spawn.z)), undefined,
      `Hope AI ${spawn.id} spawn must be air`);
    const pad = hope.hope_pads[Math.min(spawn.pad, 16) - 1];
    assert.equal(spawn.x, pad.center_x,
      `Hope AI ${spawn.id} must use the source judge column`);
    assert.equal(spawn.y, pad.center_y,
      `Hope AI ${spawn.id} must stand on the source pad floor height`);
    assert.equal(spawn.z, pad.center_z + 2,
      `Hope AI ${spawn.id} must stand two blocks south of the floor centre`);
    assert.ok(Math.hypot(spawn.x - pad.center_x, spawn.z - pad.center_z) >= 2,
      `Hope AI ${spawn.id} must not share the player/control cell`);
  }
  const spectator = hope.hope_spectator;
  assert.ok(spectator.x >= hope.origin[0] && spectator.x < hope.origin[0] + hope.size[0]);
  assert.ok(spectator.y >= hope.origin[1] && spectator.y < hope.origin[1] + hope.size[1]);
  assert.ok(spectator.z >= hope.origin[2] && spectator.z < hope.origin[2] + hope.size[2]);
  const spectatorX = Math.floor(spectator.x);
  const spectatorY = Math.floor(spectator.y);
  const spectatorZ = Math.floor(spectator.z);
  assert.equal(mock.blocks.get(keyAt(spectatorX, spectatorY, spectatorZ)), undefined,
    "Hope spectator feet must be clear");
  assert.notEqual(mock.blocks.get(keyAt(spectatorX, spectatorY - 1, spectatorZ)), undefined,
    "Hope spectator must stand on the gallery floor");
  const lose = hope.hope_lose;
  assert.ok(lose, "Hope must expose the source F-room elimination point");
  const loseX = Math.floor(lose.x);
  const loseY = Math.floor(lose.y);
  const loseZ = Math.floor(lose.z);
  assert.equal(mock.blocks.get(keyAt(loseX, loseY, loseZ)), undefined,
    "Hope elimination point feet must be clear");
  assert.notEqual(mock.blocks.get(keyAt(loseX, loseY - 1, loseZ)), undefined,
    "Hope elimination point must have a physical floor");
  assert.notDeepEqual([loseX, loseY, loseZ], [spectatorX, spectatorY, spectatorZ]);
  assert.notDeepEqual([loseX, loseY, loseZ],
    [Math.floor(hope.hope_warp.x), Math.floor(hope.hope_warp.y), Math.floor(hope.hope_warp.z)]);
  const hopeAiEntities = () => mock.entities.filter(entity =>
    !entity.removed && entity.hasTag("junket:hope:ai"));
  assert.equal(hopeAiEntities().length, HOPE_AI_TRADERS);
  assert.ok(hopeAiEntities().every(entity => entity.typeId === "junket:hope_judge"),
    "Hope judges must use the persistent custom entity, not a vanilla wandering trader");
  const firstAi = hopeAiEntities().find(entity => entity.hasTag("junket:hope:ai:1"));
  assert.ok(firstAi);
  assert.deepEqual([firstAi.location.x, firstAi.location.y, firstAi.location.z],
    [hope.hope_ai_spawns[0].x, hope.hope_ai_spawns[0].y, hope.hope_ai_spawns[0].z],
    "the first judge must materialize on its saved source marker");
  const players = hope.hope_pads.slice(0, 2).map((pad, seat) => ({
    id: `hope-physical-${seat}`,
    typeId: "minecraft:player",
    dimension: mock.dimension,
    location: { x: pad.center_x, y: pad.center_y, z: pad.center_z },
    teleports: [],
    effects: [],
    damage: [],
    commands: [],
    sounds: [],
    tags: new Set(),
    addTag(tag) { this.tags.add(tag); },
    removeTag(tag) { this.tags.delete(tag); },
    hasTag(tag) { return this.tags.has(tag); },
    addEffect(identifier, duration, options) {
      this.effects.push({ identifier, duration, options });
    },
    removeEffect(identifier) {
      this.effects = this.effects.filter(effect => effect.identifier !== identifier);
    },
    applyDamage(amount, options) { this.damage.push({ amount, options }); },
    runCommand(command) { this.commands.push(command); return { successCount: 1 }; },
    playSound(identifier, options) { this.sounds.push({ identifier, options }); },
    teleport(location, options) {
      this.teleports.push({ location: { ...location }, options });
      this.location = { ...location };
    },
  }));
  const warpPlayer = {
    id: "hope-warp-observer",
    typeId: "minecraft:player",
    dimension: mock.dimension,
    location: { x: hope.hope_warp.x, y: hope.hope_warp.y, z: hope.hope_warp.z },
    teleports: [],
    effects: [],
    messages: [],
    tags: new Set(),
    addTag(tag) { this.tags.add(tag); },
    removeTag(tag) { this.tags.delete(tag); },
    hasTag(tag) { return this.tags.has(tag); },
    addEffect(identifier, duration, options) {
      this.effects.push({ identifier, duration, options });
    },
    removeEffect(identifier) {
      this.effects = this.effects.filter(effect => effect.identifier !== identifier);
    },
    teleport(location, options) {
      this.teleports.push({ location: { ...location }, options });
      this.location = { ...location };
    },
    onScreenDisplay: {
      setActionBar(message) { warpPlayer.messages.push(message); },
    },
  };
  mock.players.push(warpPlayer);
  mock.players.push(...players);
  const started = { players: players.map(player => player.id), state: createHope(2) };
  venues.update("hope", { ...started, state: started.state });
  assert.equal(warpPlayer.teleports.length, 1,
    "standing on the physical hope-warp plate must open the observer route");
  assert.deepEqual(warpPlayer.teleports[0].location,
    { x: spectator.x, y: spectator.y, z: spectator.z });
  assert.equal(warpPlayer.effects.find(effect => effect.identifier === "invisibility")
    ?.duration, 1000000, "the observer route must grant the source invisibility");
  assert.equal(warpPlayer.hasTag("junket:hope:observer"), true,
    "the observer route must be tagged for source-equivalent cleanup");
  assert.deepEqual(venues.pollHopePads(started), [1, 2],
    "Hope must read simultaneous floor positions from the player's feet");
  players[1].location.x = hope.hope_pads[7].x + 0.5;
  players[1].location.z = hope.hope_pads[7].z + 0.5;
  assert.deepEqual(venues.pollHopePads(started), [1, 8],
    "moving to another physical pad must change the selected floor without a form");
  players[0].location.y = hope.hope_pads[0].y + 6;
  assert.equal(venues.pollHopePads(started)[0], 1,
    "Hope uses the source's nearest-floor rule even when a participant is above the pads");

  let state = hopeSystem(started.state, "position", [1, 8]);
  const activeState = { ...started.state };
  players[0].location = {
    x: hope.hope_pads[0].center_x,
    y: hope.hope_pads[0].center_y,
    z: hope.hope_pads[0].center_z,
  };
  const beforeWander = { ...firstAi.location };
  venues.update("hope", { ...started, state: activeState });
  venues.update("hope", { ...started, state: activeState });
  assert.equal(hopeAiEntities().length, HOPE_AI_TRADERS,
    "a living round must keep all seventeen judges");
  assert.equal(firstAi.removed, false);
  assert.ok(Math.hypot(firstAi.location.x - beforeWander.x,
    firstAi.location.z - beforeWander.z) > 0.01,
  `Hope judges must physically wander during the choose window: ` +
  `before=${JSON.stringify(beforeWander)} after=${JSON.stringify(firstAi.location)} ` +
  `spawn=${JSON.stringify(hope.hope_ai_spawns[0])} ` +
  `all=${JSON.stringify(hopeAiEntities().map(entity => entity.location))}`);
  assert.ok(!players[0].effects.some(effect => effect.identifier === "slowness"),
    "the source must not apply judge slowness to the player");
  players[0].damage.length = 0;
  firstAi.location = {
    x: players[0].location.x + 0.5,
    y: players[0].location.y,
    z: players[0].location.z,
  };
  venues.update("hope", { ...started, state: { ...activeState, last: {} } });
  assert.ok(players[0].damage.length > 0,
    `contact damage must be driven by the nearest physical judge: ` +
    `player=${JSON.stringify(players[0].location)} ai=${JSON.stringify(firstAi.location)} ` +
    `entities=${hopeAiEntities().map(entity => `${entity.location.x},${entity.location.z}`).join("|")}`);

  const revealState = {
    ...started.state, phase: "reveal", phaseRemaining: 50,
    choices: [{ pad: 1, symbol: started.state.padSymbols[0] },
      { pad: 8, symbol: started.state.padSymbols[7] }],
  };
  venues.update("hope", { ...started, state: revealState });
  const earlyRevealStart = { ...firstAi.location };
  venues.update("hope", { ...started, state: revealState });
  assert.ok(Math.hypot(firstAi.location.x - earlyRevealStart.x,
    firstAi.location.z - earlyRevealStart.z) > 0.01,
  "rounds 1-3 must keep walking until timeup ends the reveal");
  const lateState = {
    ...started.state,
    round: 4,
    phase: "reveal",
    phaseRemaining: 99,
    ai: started.state.ai.map(value => ({ ...value, loser: value.id === 1 })),
  };
  venues.update("hope", { ...started, state: lateState });
  const lateStart = { ...firstAi.location };
  for (let tick = 0; tick < 20; tick++) {
    venues.update("hope", { ...started, state: lateState });
  }
  assert.ok(Math.hypot(firstAi.location.x - lateStart.x, firstAi.location.z - lateStart.z) > 0.01,
    "rounds 4-9 must keep walking through the source's 20-tick late window");
  const lateReveal = { ...firstAi.location };
  for (let tick = 0; tick < 20; tick++) {
    venues.update("hope", { ...started, state: lateState });
  }
  assert.ok(Math.hypot(firstAi.location.x - lateReveal.x,
    firstAi.location.z - lateReveal.z) > 0.01,
    "rounds 4-9 must keep walking beyond the 20-tick branch delay");
  const dropState = { ...lateState, phase: "drop", phaseRemaining: 85 };
  venues.update("hope", { ...started, state: dropState });
  const dropStopped = { ...firstAi.location };
  const dropStoppedRotation = { ...firstAi.rotation };
  for (let tick = 0; tick < 5; tick++) {
    venues.update("hope", { ...started, state: dropState });
  }
  assert.deepEqual([firstAi.location.x, firstAi.location.y, firstAi.location.z],
    [dropStopped.x, dropStopped.y, dropStopped.z],
    "timeup freezes every judge when drop starts");
  assert.deepEqual(firstAi.rotation, dropStoppedRotation,
    "walk.mcfunction must stop facing updates once NoAI:1b is applied");

  const secondAiEntity = hopeAiEntities().find(entity => entity.hasTag("junket:hope:ai:2"));
  const thirdAiEntity = hopeAiEntities().find(entity => entity.hasTag("junket:hope:ai:3"));
  const fourthAiEntity = hopeAiEntities().find(entity => entity.hasTag("junket:hope:ai:4"));
  assert.ok(secondAiEntity && thirdAiEntity && fourthAiEntity);
  const roleState = {
    ...lateState,
    phase: "choose",
    ai: lateState.ai.map(value => ({
      ...value,
      loser: value.id === 2 || value.id === 3,
    })),
  };
  const yawTo = (from, to) => Math.atan2(to.x - from.x, to.z - from.z) * 180 / Math.PI;
  const roleBase = {
    x: hope.hope_pads[0].center_x,
    y: hope.hope_pads[0].center_y,
    z: hope.hope_pads[0].center_z,
  };
  const normalStart = { ...roleBase };
  const loserStart = { x: roleBase.x, y: roleBase.y, z: roleBase.z + 5 };
  firstAi.location = { ...normalStart };
  fourthAiEntity.location = { x: roleBase.x + 2, y: roleBase.y, z: roleBase.z };
  secondAiEntity.location = { ...loserStart };
  thirdAiEntity.location = { x: loserStart.x, y: loserStart.y, z: loserStart.z + 2.5 };
  for (const [index, entity] of hopeAiEntities().filter(entity =>
    !["junket:hope:ai:1", "junket:hope:ai:2", "junket:hope:ai:3", "junket:hope:ai:4"]
      .some(tag => entity.hasTag(tag))).entries()) {
    // Keep distant actors inside this venue's managed rectangle. A long
    // single row placed its final actor outside the room; scoped repair must
    // preserve that actor rather than treating it as a local duplicate.
    entity.location = {
      x: roleBase.x + 10 + index % 5,
      y: roleBase.y,
      z: roleBase.z + 10 + Math.floor(index / 5),
    };
  }
  const normalTarget = { ...fourthAiEntity.location };
  const loserTarget = { ...thirdAiEntity.location };
  venues.update("hope", { ...started, state: roleState });
  assert.ok(Math.abs(firstAi.rotation.y - yawTo(normalStart, normalTarget)) < 0.001,
    `a normal trader must face the nearest !hope-out judge: ` +
    `rotation=${JSON.stringify(firstAi.rotation)} location=${JSON.stringify(firstAi.location)} ` +
    `target=${JSON.stringify(normalTarget)} entities=${hopeAiEntities().map(entity => `${entity.getTags().join(",")}=${entity.location.x},${entity.location.z}`).join("|")}`);
  assert.ok(Math.abs(secondAiEntity.rotation.y - yawTo(loserStart, loserTarget)) < 0.001,
    `a hope-loser trader must face the nearest hope-out judge instead: ` +
    `rotation=${JSON.stringify(secondAiEntity.rotation)} location=${JSON.stringify(secondAiEntity.location)} ` +
    `target=${JSON.stringify(loserTarget)}`);

  fourthAiEntity.location = {
    x: firstAi.location.x,
    y: firstAi.location.y,
    z: firstAi.location.z + 2,
  };
  venues.update("hope", { ...started, state: roleState });
  assert.ok(Math.abs(firstAi.rotation.y) < 0.001,
    "a moving trader must update its facing on every source walk tick");

  firstAi.effects.length = 0;
  firstAi.location = { ...roleBase };
  fourthAiEntity.location = {
    x: firstAi.location.x,
    y: firstAi.location.y + 2,
    z: firstAi.location.z,
  };
  venues.update("hope", { ...started, state: roleState });
  assert.equal(firstAi.effects.length, 0,
    "walk.mcfunction's 1.5-block slowness check must use 3D distance");
  fourthAiEntity.location = {
    x: firstAi.location.x + 1,
    y: firstAi.location.y,
    z: firstAi.location.z,
  };
  venues.update("hope", { ...started, state: roleState });
  assert.ok(firstAi.effects.some(effect => effect.identifier === "slowness"),
    "walk.mcfunction must apply slowness to the trader within 1.5 blocks");

  fourthAiEntity.location = {
    x: firstAi.location.x + 5,
    y: firstAi.location.y,
    z: firstAi.location.z,
  };
  const farRotation = { ...firstAi.rotation };
  venues.update("hope", { ...started, state: roleState });
  assert.deepEqual(firstAi.rotation, farRotation,
    "walk.mcfunction must not face a role target outside its 1.3..3 range");
  venues.update("hope", { ...started, state: lateState });
  assert.equal(firstAi.hasTag("junket:hope:loser"), true,
    "the loser role must be persisted on the physical trader tag");
  assert.equal(firstAi.getDynamicProperty("junket:hope_ai_loser"), true,
    "the loser role must survive entity re-materialization through dynamic properties");
  const clearedLoser = {
    ...lateState,
    ai: lateState.ai.map(value => value.id === 1 ? { ...value, loser: false } : value),
  };
  venues.update("hope", { ...started, state: clearedLoser });
  assert.equal(firstAi.hasTag("junket:hope:loser"), false,
    "a reassigned trader must lose the previous round's loser tag");
  const legacyJudge = mock.dimension.spawnEntity("minecraft:wandering_trader", {
    x: hope.hope_ai_spawns[0].x,
    y: hope.hope_ai_spawns[0].y,
    z: hope.hope_ai_spawns[0].z,
  });
  legacyJudge.addTag("junket:hope:ai");
  legacyJudge.addTag("junket:hope:ai:1");
  const restartedVenues = createProductionVenues(mock);
  restartedVenues.update("hope", { ...started, state: lateState });
  const restoredTraders = mock.entities.filter(entity =>
    !entity.removed && entity.hasTag("junket:hope:ai"));
  assert.equal(restoredTraders.length, HOPE_AI_TRADERS,
    "restart must restore all 17 judges without duplication");
  assert.equal(legacyJudge.removed, true,
    "a tagged vanilla wandering trader from an older deployment must be replaced");
  assert.ok(restoredTraders.every(entity => entity.typeId === "junket:hope_judge"),
    "restart must restore only durable custom judges");
  assert.equal(restoredTraders.find(entity => entity.hasTag("junket:hope:ai:1"))
    .hasTag("junket:hope:loser"), true, "restart must restore the loser role");
  restartedVenues.update("hope", { ...started, state: roleState });
  const restoredNormal = restoredTraders.find(entity => entity.hasTag("junket:hope:ai:1"));
  const restoredLoser = restoredTraders.find(entity => entity.hasTag("junket:hope:ai:2"));
  const restoredLoserTarget = restoredTraders.find(entity => entity.hasTag("junket:hope:ai:3"));
  const restoredNormalTarget = restoredTraders.find(entity => entity.hasTag("junket:hope:ai:4"));
  assert.equal(restoredNormal.hasTag("junket:hope:loser"), false);
  assert.equal(restoredLoser.hasTag("junket:hope:loser"), true);
  restoredNormal.location = { ...roleBase };
  restoredNormalTarget.location = { x: roleBase.x + 2, y: roleBase.y, z: roleBase.z };
  restoredLoser.location = { x: roleBase.x, y: roleBase.y, z: roleBase.z + 5 };
  restoredLoserTarget.location = { x: roleBase.x, y: roleBase.y, z: roleBase.z + 7.5 };
  for (const [index, entity] of restoredTraders.filter(entity =>
    !["junket:hope:ai:1", "junket:hope:ai:2", "junket:hope:ai:3", "junket:hope:ai:4"]
      .some(tag => entity.hasTag(tag))).entries()) {
    entity.location = {
      x: roleBase.x + 10 + index % 5,
      y: roleBase.y,
      z: roleBase.z + 10 + Math.floor(index / 5),
    };
  }
  const restoredNormalTargetPoint = { ...restoredNormalTarget.location };
  const restoredLoserTargetPoint = { ...restoredLoserTarget.location };
  restartedVenues.update("hope", { ...started, state: roleState });
  assert.ok(Math.abs(restoredNormal.rotation.y - yawTo(roleBase, restoredNormalTargetPoint)) < 0.001,
    "a restarted normal trader must restore its !hope-out target: " +
    `rotation=${JSON.stringify(restoredNormal.rotation)} location=${JSON.stringify(restoredNormal.location)} ` +
    `target=${JSON.stringify(restoredNormalTarget.location)}`);
  assert.ok(Math.abs(restoredLoser.rotation.y - yawTo({ ...roleBase, z: roleBase.z + 5 }, restoredLoserTargetPoint)) < 0.001,
    "a restarted hope-loser trader must restore its hope-out target: " +
    `rotation=${JSON.stringify(restoredLoser.rotation)} location=${JSON.stringify(restoredLoser.location)} ` +
    `target=${JSON.stringify(restoredLoserTarget.location)}`);
  const eliminatedState = {
    ...roleState,
    ai: roleState.ai.map(value => value.id === 1 ? { ...value, alive: false } : value),
  };
  restartedVenues.update("hope", { ...started, state: eliminatedState });
  assert.equal(hopeAiEntities().length, HOPE_AI_TRADERS - 1,
    "walk.mcfunction must remove a judge that resolved on a hope-out floor");
  assert.equal(firstAi.removed, true);
  assert.equal(hopeAiEntities().some(entity => entity.hasTag("junket:hope:ai:1")), false);
  for (let tick = 0; tick < 120; tick++) {
    restartedVenues.update("hope", { ...started, state: eliminatedState });
  }
  assert.equal(hopeAiEntities().length, HOPE_AI_TRADERS - 1,
    "an eliminated judge must stay dead for the rest of the match");

  const eliminatedRestart = createProductionVenues(mock);
  eliminatedRestart.update("hope", { ...started, state: eliminatedState });
  assert.equal(hopeAiEntities().length, HOPE_AI_TRADERS - 1,
    "a BDS restart must restore the saved alive:false state without respawning the judge");
  assert.equal(hopeAiEntities().some(entity => entity.hasTag("junket:hope:ai:1")), false);

  const newMatchState = createHope(2);
  const newMatchVenues = createProductionVenues(mock);
  newMatchVenues.update("hope", {
    players: players.map(player => player.id), state: newMatchState,
  });
  assert.equal(hopeAiEntities().length, HOPE_AI_TRADERS,
    "only a newly created round may materialize all seventeen judges again");
  assert.ok(hopeAiEntities().some(entity => entity.hasTag("junket:hope:ai:1")));

  // elec.mcfunction runs in the middle of the 85-tick local drop window. It
  // must affect only participants whose recorded pad is a hazard, matching
  // the source hope-outplayer tag.
  const electricSymbol = state.hazards[0];
  const electricPad = state.padSymbols.findIndex(symbol => symbol === electricSymbol) + 1;
  const electricChoices = players.map(() => ({
    pad: electricPad, symbol: electricSymbol,
  }));
  const electricState = {
    ...state, phase: "drop", phaseRemaining: 44, floorOpen: false,
    alive: [true, true], choices: electricChoices,
  };
  mock.particles.length = 0;
  venues.update("hope", { ...started, state: electricState });
  for (const player of players) {
    const slowness = player.effects.find(effect => effect.identifier === "slowness");
    const blindness = player.effects.find(effect => effect.identifier === "blindness");
    assert.deepEqual(slowness?.options, { amplifier: 99, showParticles: false },
      "Hope elec must apply source Slowness level 100");
    assert.deepEqual(blindness?.options, { amplifier: 99, showParticles: false },
      "Hope elec must apply source Blindness level 100");
    assert.ok(player.commands.includes("attribute @s minecraft:jump_strength base set 0"),
      "Hope elec must suppress jump strength");
    assert.ok(player.damage.some(entry => entry.amount === 0.01),
      "Hope elec must apply the source 0.01 damage");
    assert.ok(player.sounds.some(entry => entry.identifier === "entity.firework_rocket.blast"),
      "Hope elec must play the source firework blast");
  }
  assert.equal(mock.particles.length, players.length,
    "Hope elec must spawn one dust-equivalent particle burst per hazard participant");
  const electricEffectCounts = players.map(player => player.effects.length);
  const electricCommandCounts = players.map(player => player.commands.length);
  venues.update("hope", {
    ...started, state: { ...electricState, phaseRemaining: 33 },
  });
  assert.deepEqual(players.map(player => player.effects.length), electricEffectCounts,
    "effects must not be applied after the source elec window");
  assert.deepEqual(players.map(player => player.commands.length), electricCommandCounts,
    "jump suppression must not be reapplied after the source elec window");

  const sourceHazardSymbol = state.hazards[0];
  const sourceHazardPad = state.padSymbols.findIndex(symbol => symbol === sourceHazardSymbol) + 1;
  state = {
    ...state,
    choices: state.choices.map(() => ({ pad: sourceHazardPad, symbol: sourceHazardSymbol })),
  };
  while (state.phase !== "drop") state = hopeSystem(state, "tick");
  assert.equal(state.floorOpen, false);
  venues.update("hope", { ...started, state });
  const hazardPads = (hope.drop_areas ?? []).filter(area => state.hazards.includes(area.symbol));
  for (const area of hazardPads) {
    for (const cell of area.cells) {
      assert.notEqual(mock.blocks.get(keyAt(cell.x, cell.y, cell.z)), undefined,
        "Hope hazard floors stay closed during the whole drop countdown");
    }
  }
  while (state.phase !== "result") state = hopeSystem(state, "tick");
  venues.update("hope", { ...started, state });
  for (const area of hazardPads) {
    for (const cell of area.cells) {
      assert.equal(mock.blocks.get(keyAt(cell.x, cell.y, cell.z)), undefined,
        "Hope hazard floors open only when the fall resolves");
    }
    const floorControl = roleEntity(mock, "hope", `control_${area.pad}`);
    assert.equal(floorControl?.rotation.y, 15,
      `Hope hazard floor ${area.pad} must rotate 15 degrees like fall.mcfunction`);
  }
  for (const seat of state.last?.eliminated ?? []) {
    const player = players[seat];
    assert.deepEqual(player.teleports.at(-1)?.location, { x: lose.x, y: lose.y, z: lose.z },
      `eliminated player ${seat + 1} must go to the source F room, not the observer gallery`);
    assert.equal(player.effects.some(effect => effect.identifier === "invisibility"), false,
      "automatic elimination must not grant the manual observer-plate invisibility");
    assert.equal(player.effects.some(effect => effect.identifier === "slowness"), false,
      "lose.mcfunction must clear Slowness");
    assert.equal(player.effects.some(effect => effect.identifier === "blindness"), false,
      "lose.mcfunction must clear Blindness");
    assert.equal(player.commands.at(-1),
      "attribute @s minecraft:jump_strength base set 0.4",
      "lose.mcfunction must restore the source base jump strength");
  }
  // A participant can be offline when Hope ends. Their observer/status
  // cleanup must remain pending and run when that same player reconnects,
  // not be consumed once by the finish transition.
  const reconnectingHope = players[0];
  const reconnectingIndex = mock.players.indexOf(reconnectingHope);
  assert.ok(reconnectingIndex >= 0, "the reconnecting Hope participant must start online");
  mock.players.splice(reconnectingIndex, 1);
  reconnectingHope.addTag("junket:hope:observer");
  reconnectingHope.addEffect("invisibility", 1000000, {
    amplifier: 0, showParticles: false,
  });
  venues.update("hope", {
    ...started,
    state: { ...state, phase: "finished", last: state.last },
  });
  assert.equal(reconnectingHope.effects.some(effect => effect.identifier === "invisibility"), true,
    "an offline participant must keep the pending cleanup until reconnect");
  mock.players.push(reconnectingHope);
  venues.update("hope", {
    ...started,
    state: { ...state, phase: "finished", last: state.last },
  });
  assert.equal(reconnectingHope.effects.some(effect => effect.identifier === "invisibility"), false,
    "a participant who reconnects after Hope ends must lose observer invisibility");
  assert.equal(reconnectingHope.hasTag("junket:hope:observer"), false,
    "a participant who reconnects after Hope ends must lose the observer tag");
  assert.equal(warpPlayer.effects.some(effect => effect.identifier === "invisibility"), false,
    "source end.mcfunction must clear observer invisibility");
  assert.equal(warpPlayer.hasTag("junket:hope:observer"), false,
    "finished games must clear the observer cleanup tag");

  // Source hope/killtv.json: a tagged participant kills hope-tv-code, the six
  // TV surfaces disappear, the code display changes to its broken model, and
  // the cure sound plays. Apparatus interaction is not a substitute for the
  // physical kill, and repairing or restarting the venue must not silently
  // recreate the broken code/TV props.
  const apparatusState = { ...state, phase: "reveal", phaseRemaining: 10 };
  venues.update("hope", { ...started, state: apparatusState });
  assert.equal(players[0].hasTag("hope-player"), true,
    "a live Hope participant must receive the source hope-player tag");
  const hopeCode = roleEntity(mock, "hope", "hope_code");
  const hopeDisplay = roleEntity(mock, "hope", "hope_code_display");
  assert.ok(hopeCode && hopeDisplay);
  assert.equal(hopeCode.getComponent("minecraft:health").currentValue, 3,
    "the code target must expose the source three-health villager");
  const interact = mock.emitInteract({ target: hopeCode, player: players[0], cancel: false });
  assert.equal(interact.cancel, true,
    "an apparatus must not open a UI when a player interacts with it");
  const cureSounds = () => mock.sounds.filter(sound =>
    sound.identifier === "entity.zombie_villager.cure").length;
  const curesBefore = cureSounds();
  mock.emitEntityDie(hopeCode, warpPlayer);
  assert.equal(hopeCode.removed, false,
    "a player outside the Hope match must not break the apparatus");
  players[0].removeTag("hope-player");
  mock.emitEntityDie(hopeCode, players[0]);
  assert.equal(hopeCode.removed, false,
    "a participant without the source hope-player tag must not break the apparatus");
  players[0].addTag("hope-player");
  mock.emitEntityDie(hopeCode, players[0]);
  assert.equal(hopeCode.removed, true,
    "the killing participant must remove the code target");
  for (let index = 1; index <= 6; index++) {
    assert.equal(roleEntity(mock, "hope", `hope_tv_${index}`), undefined,
      `source killtv must remove TV surface ${index}`);
  }
  assert.equal(hopeDisplay.removed, false,
    "the code display remains as the source broken-model marker");
  assert.equal(hopeDisplay.getProperty("junket:frame"), 1,
    "the code display must switch to the broken frame");
  assert.equal(cureSounds(), curesBefore + 1,
    "breaking the code must play entity.zombie_villager.cure once");
  assert.equal(mock.properties.get("junket:hope:apparatus:broken"), true,
    "the broken apparatus state must be persisted in the world");
  mock.emitEntityDie(hopeCode, players[0]);
  assert.equal(cureSounds(), curesBefore + 1,
    "a second kill event must not replay the cure sound");

  await drain(mock, venues.repair("hope"));
  assert.equal(mock.properties.get("junket:hope:apparatus:broken"), true,
    "a verified repair must not reset the broken apparatus state");
  assert.equal(roleEntity(mock, "hope", "hope_code"), undefined,
    "repair must not respawn the killed code target");
  for (let index = 1; index <= 6; index++) {
    assert.equal(roleEntity(mock, "hope", `hope_tv_${index}`), undefined,
      `repair must not respawn TV surface ${index}`);
  }
  assert.equal(roleEntity(mock, "hope", "hope_code_display")
    .getProperty("junket:frame"), 1,
  "repair must preserve the broken code display frame");

  const restartedHope = createProductionVenues(mock);
  restartedHope.update("hope", {
    ...started, state: { ...state, phase: "finished", last: state.last },
  });
  assert.equal(mock.properties.get("junket:hope:apparatus:broken"), true,
    "a BDS restart must preserve the broken apparatus state");
  assert.equal(roleEntity(mock, "hope", "hope_code"), undefined,
    "a BDS restart must not respawn the code while the apparatus is broken");
  assert.equal(roleEntity(mock, "hope", "hope_code_display")
    .getProperty("junket:frame"), 1,
  "a BDS restart must restore the broken display from its entity property");

  restartedHope.update("hope", {
    players: players.map(player => player.id), state: createHope(2),
  });
  assert.equal(mock.properties.get("junket:hope:apparatus:broken"), false,
    "starting a new Hope game must restore the source apparatus");
  const restoredCode = roleEntity(mock, "hope", "hope_code");
  assert.ok(restoredCode, "a new game must recreate the code target");
  assert.equal(restoredCode.getComponent("minecraft:health").currentValue, 3,
    "the recreated code target must start at three health");
  for (let index = 1; index <= 6; index++) {
    assert.ok(roleEntity(mock, "hope", `hope_tv_${index}`),
      `a new game must recreate TV surface ${index}`);
  }
  assert.equal(roleEntity(mock, "hope", "hope_code_display")
    .getProperty("junket:frame"), 0,
  "a new game must restore the normal code display frame");

  warpPlayer.addTag("junket:hope:observer");
  warpPlayer.addEffect("invisibility", 1000000, { amplifier: 0, showParticles: false });
  venues.update("hope", { players: players.map(player => player.id), state: null });
  assert.equal(warpPlayer.effects.some(effect => effect.identifier === "invisibility"), false,
    "an aborted game with no state must clear observer invisibility");
  assert.equal(warpPlayer.hasTag("junket:hope:observer"), false,
    "an aborted game with no state must clear the observer tag");
  mock.players.splice(mock.players.length - players.length, players.length);
}

console.log("PASS 17 venues: full RLE materialization, strict foreign-block refusal, partial repair, idempotent install, fixture cleanup and special failure reporting");
