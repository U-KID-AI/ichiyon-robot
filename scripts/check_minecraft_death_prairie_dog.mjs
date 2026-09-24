import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  createDeathPrairieDog, isPrairieNight, nearestAwake, PRAIRIE, STATE,
  TARGET_BITS, TAG_PREFIX,
} from "../minecraft/behavior_packs/import_structures/scripts/death_prairie_dog_core.js";

const read = path => JSON.parse(readFileSync(new URL(path, import.meta.url)));
const bp = read("../minecraft/behavior_packs/ichiyon_avatar_bp/entities/death_prairie_dog.json")["minecraft:entity"];

function fixture() {
  const entities = [], players = [], reports = [], queries = [];
  const dimensions = Object.fromEntries(["overworld", "nether", "the_end"].map(id => [id, {
    id, getEntities(query) {
      queries.push(query);
      return entities.filter(e => e.typeId === query.type && e.isValid && e.dimension.id === id);
    },
  }]));
  const world = { time: 6000, getTimeOfDay() { return this.time; },
    getAllPlayers: () => players, getDimension: id => dimensions[id] };
  const system = { currentTick: 0 };
  const deps = { world, system, report: value => reports.push(value) };
  const core = createDeathPrairieDog(deps);
  let next = 0;
  function entity(typeId = PRAIRIE, x = 0, dimension = "overworld") {
    const e = { id: String(++next), typeId, location: { x, y: 64, z: 0 },
      dimension: dimensions[dimension], isValid: true, health: 20, isSleeping: false,
      mode: "Survival", events: [], properties: {}, groups: new Set(), nativeTarget: undefined,
      tags: new Set(),
      getTags() { return [...this.tags]; },
      addTag(tag) { this.tags.add(tag); },
      removeTag(tag) { this.tags.delete(tag); },
      setProperty(key, value) { assert(key in bp.description.properties); this.properties[key] = value; },
      getGameMode() { return this.mode; },
      getComponent: name => name === "minecraft:health" ? { currentValue: e.health } : undefined,
      triggerEvent(name) {
        if (this.failEvent) throw Error("test event failure");
        const event = bp.events[name];
        assert(event, name);
        for (const group of event.remove?.component_groups ?? []) this.groups.delete(group);
        for (const group of event.add?.component_groups ?? []) this.groups.add(group);
        Object.assign(this.properties, event.set_property);
        this.events.push(name);
      },
    };
    Object.defineProperty(e, "target", { get: () => e.nativeTarget });
    entities.push(e);
    return e;
  }
  function player(x = 10, dimension) {
    const p = entity("minecraft:player", x, dimension); players.push(p); return p;
  }
  const mob = entity(), p = player();
  core.recover(mob);
  function tick(n = 1) { for (let i = 0; i < n; i++) { system.currentTick++; core.tick(); } }
  function chase() { world.time = 13000; tick(11); assert.equal(mob.properties[STATE], "chase"); }
  return { core, mob, p, player, entity, world, system, tick, chase, players, entities,
    dimensions, reports, queries, deps };
}

test("night boundaries are inclusive 13000..23000", () => {
  for (const time of [0, 12999, 23001, 23999]) assert.equal(isPrairieNight(time), false);
  for (const time of [13000, 18000, 23000]) assert.equal(isPrairieNight(time), true);
});
test("daytime is friendly wander without target or repeated AI resets", () => {
  const f = fixture(); f.tick(300);
  assert.equal(f.mob.properties[STATE], "wander");
  assert(!f.mob.groups.has("ichiyon:prairie_chase"));
  assert.deepEqual(f.mob.events, ["ichiyon:prairie_wander"]);
});
test("transition lasts 10 ticks and is not replayed during continuous chase", () => {
  const f = fixture(); f.world.time = 13000; f.tick();
  assert.equal(f.mob.properties[STATE], "transition");
  assert(!f.mob.groups.has("ichiyon:prairie_chase"));
  f.tick(9); assert.equal(f.mob.properties[STATE], "transition");
  f.tick(); assert.equal(f.mob.properties[STATE], "chase");
  f.tick(200);
  assert.equal(f.mob.events.filter(e => e === "ichiyon:prairie_transition").length, 1);
});
test("closest awake player selected, sleeping and out-of-range players excluded", () => {
  const players = [
    { id: "far", mode: "Survival", sleeping: false, location: { x: 20, y: 0, z: 0 } },
    { id: "sleep", mode: "Survival", sleeping: true, location: { x: 1, y: 0, z: 0 } },
    { id: "near", mode: "Adventure", sleeping: false, location: { x: 2, y: 0, z: 0 } },
  ];
  assert.equal(nearestAwake({ x: 0, y: 0, z: 0 }, players).id, "near");
  assert.equal(nearestAwake({ x: 100, y: 0, z: 0 }, players), undefined);
});
test("changing nearest player during chase does not replay transition", () => {
  const f = fixture(); f.chase(); const count = f.mob.events.length; const other = f.player(4); f.tick();
  assert.equal(f.core.tracked.get(f.mob.id).targetId, other.id);
  assert.equal(f.mob.events.length, count+1);
  assert.equal(f.mob.events.at(-1), "ichiyon:prairie_chase");
  f.tick(20); assert.equal(f.mob.events.length, count+1);
  assert.equal(f.mob.events.filter(e => e === "ichiyon:prairie_transition").length, 1);
});
test("sleep releases pursuit before retargeting, with real clearance required", () => {
  const f = fixture(); f.p.location.x = 2; f.chase(); f.player(12);
  f.p.isSleeping = true; f.tick();
  assert.equal(f.mob.properties[STATE], "flee");
  assert.equal(f.core.tracked.get(f.mob.id).targetId, undefined);
  assert(!f.mob.groups.has("ichiyon:prairie_chase"));
  assert(!f.mob.groups.has("ichiyon:prairie_chase"));
  f.tick(1000); assert.equal(f.mob.properties[STATE], "flee", "blocked paths cannot time out into pursuit");
  f.mob.location.x = -7; f.tick(); assert.equal(f.mob.properties[STATE], "transition");
  f.tick(10); assert.equal(f.mob.properties[STATE], "chase");
  assert.notEqual(f.core.tracked.get(f.mob.id).targetId, f.p.id);
});
test("sleep during the transition cancels it", () => {
  const f = fixture(); f.world.time = 13000; f.tick(); f.p.isSleeping = true; f.tick();
  assert.equal(f.mob.properties[STATE], "flee");
});
test("far sleeping previous target still causes a release interval", () => {
  const f = fixture(); f.chase(); f.p.isSleeping = true; f.player(15); f.tick();
  assert.equal(f.mob.properties[STATE], "flee");
  f.tick(19); assert.equal(f.mob.properties[STATE], "flee");
  f.tick(); assert.equal(f.mob.properties[STATE], "transition");
});
test("native attack target is not used even if another addon assigns one", () => {
  const f = fixture(), other = f.player(20); f.mob.nativeTarget = other; f.chase();
  assert.equal(f.core.tracked.get(f.mob.id).targetId, f.p.id);
  f.p.isSleeping = true; f.tick(); assert.equal(f.mob.properties[STATE], "flee");
});
test("all sleeping means no chase; waking starts a fresh transition after release", () => {
  const f = fixture(); f.world.time = 13000; f.p.location.x = 1; f.p.isSleeping = true; f.tick(30);
  assert.equal(f.mob.properties[STATE], "flee");
  f.p.isSleeping = false; f.tick(); assert.equal(f.mob.properties[STATE], "transition");
});
test("dawn overrides transition, chase and flee immediately", () => {
  for (const state of ["transition", "chase", "flee"]) {
    const f = fixture(); f.world.time = 13000; f.tick(state === "transition" ? 1 : 11);
    if (state === "flee") { f.p.isSleeping = true; f.tick(); }
    f.world.time = 23001; f.tick(); assert.equal(f.mob.properties[STATE], "wander");
    assert(!f.mob.groups.has("ichiyon:prairie_chase"));
  }
});
test("logout, death, dimension change and leaving range stop chasing", () => {
  for (const reason of ["logout", "death", "dimension", "distance", "invalid"]) {
    const f = fixture(); f.chase();
    if (reason === "logout") f.players.length = 0;
    if (reason === "death") f.p.health = 0;
    if (reason === "dimension") f.p.dimension = f.dimensions.nether;
    if (reason === "distance") f.p.location.x = 100;
    if (reason === "invalid") f.p.isValid = false;
    f.tick(); assert.equal(f.mob.properties[STATE], "wander", reason);
  }
});
test("other awake player can replace a player who logs out", () => {
  const f = fixture(); f.chase(); const other = f.player(20); f.players.splice(0, 1); f.tick();
  assert.equal(f.core.tracked.get(f.mob.id).targetId, other.id);
  assert.equal(f.mob.properties[STATE], "chase");
});
test("Creative is pursued; non-interacting Spectator is not", () => {
  const f = fixture(); f.p.mode = "Creative"; f.chase();
  assert.equal(f.core.tracked.get(f.mob.id).targetId, f.p.id);
  f.p.mode = "Spectator"; f.tick(); assert.equal(f.mob.properties[STATE], "wander");
});
test("script restart resets saved chase, preserving location, identity and health", () => {
  const f = fixture(); f.chase(); f.mob.health = 13;
  const location = { ...f.mob.location }, id = f.mob.id;
  const restarted = createDeathPrairieDog(f.deps); restarted.scan();
  assert.equal(f.mob.properties[STATE], "wander");
  restarted.tick(); assert.equal(f.mob.properties[STATE], "transition");
  assert.equal(f.mob.id, id); assert.equal(f.mob.health, 13); assert.deepEqual(f.mob.location, location);
});
test("chunk unload removes tracking; reload reinitializes safely", () => {
  const f = fixture(); f.chase(); f.mob.isValid = false; f.tick();
  assert.equal(f.core.tracked.size, 0);
  f.mob.isValid = true; f.p.isSleeping = true; f.p.location.x = 1; f.core.recover(f.mob); f.tick();
  assert.equal(f.mob.properties[STATE], "flee");
  f.core.forget(f.mob.id); assert.equal(f.core.tracked.size, 0);
});
test("scan only queries new mob and never resets healthy tracked mobs", () => {
  const f = fixture(); const other = f.entity("ichiyon:mokuro"), second = f.entity();
  f.chase(); const count = f.mob.events.length; f.core.scan();
  assert.equal(f.mob.events.length, count); assert.equal(other.events.length, 0);
  assert.equal(second.properties[STATE], "wander");
  assert.deepEqual(f.queries, Array(3).fill({ type: PRAIRIE }));
});
test("an individual entity failure does not interrupt other mobs; recovery retries", () => {
  const f = fixture(), other = f.entity(); f.core.recover(other);
  f.world.time = 13000; f.mob.failEvent = true; f.tick();
  assert.equal(other.properties[STATE], "transition"); assert.equal(f.reports.length, 1);
  f.mob.failEvent = false; f.core.scan(); f.tick(); assert.equal(f.mob.properties[STATE], "transition");
});
test("native definitions follow one tagged awake player without any attack target selector", () => {
  assert(!bp.components["minecraft:behavior.nearest_attackable_target"]);
  const follow = bp.component_groups["ichiyon:prairie_chase"]["minecraft:behavior.follow_mob"];
  assert(follow.filters.all_of.some(f => f.test === "has_tag" && f.value.endsWith("candidate")));
  assert(follow.filters.all_of.some(f => f.test === "is_sleeping" && f.value === false));
  assert.equal(follow.speed_multiplier, 2.5); assert.equal(follow.stop_distance, 1.5);
  assert(bp.events["ichiyon:prairie_chase"].remove.component_groups.includes("ichiyon:prairie_chase"));
  const avoid = bp.components["minecraft:behavior.avoid_mob_type"];
  assert(avoid.priority < follow.priority);
  assert(avoid.entity_types[0].filters.all_of.some(f => f.test === "is_sleeping" && f.value === true));
  assert.equal(avoid.entity_types[0].max_dist, 8);
});
test("no attack execution, damage, commands, movement injection or target assignment", () => {
  for (const group of [bp.components, ...Object.values(bp.component_groups)]) {
    for (const key of Object.keys(group)) {
      assert(!/attack|damage_sensor|shooter|projectile|explode|teleport/.test(key), key);
    }
  }
  for (const name of ["death_prairie_dog_core.js", "death_prairie_dog.js"]) {
    const code = readFileSync(new URL(`../minecraft/behavior_packs/import_structures/scripts/${name}`, import.meta.url), "utf8");
    assert(!/\b(?:teleport|tryTeleport|applyImpulse|applyKnockback|applyDamage|runCommand)\s*\(/.test(code));
    assert(!/\.target\s*=/.test(code));
    assert(!/\.target\?\./.test(code));
  }
});

function matchesFilter(filter, mob, player) {
  if (filter.all_of) return filter.all_of.every(f => matchesFilter(f, mob, player));
  if (filter.any_of) return filter.any_of.some(f => matchesFilter(f, mob, player));
  if (filter.test === "is_family") return player.typeId === "minecraft:player";
  if (filter.test === "is_sleeping") return player.isSleeping === filter.value;
  if (filter.test === "bool_property") return mob.properties[filter.domain] === filter.value;
  if (filter.test === "has_tag") return player.tags.has(filter.value) !== (filter.operator === "not");
  throw Error(`Unexpected filter: ${filter.test}`);
}
const followFilters = bp.component_groups["ichiyon:prairie_chase"]["minecraft:behavior.follow_mob"].filters;
test("native binary filters match exactly one Creative target independently for multiple mobs", () => {
  const f = fixture(); f.p.mode = "Creative";
  const other = f.player(-12); other.mode = "Creative";
  const second = f.entity(PRAIRIE, -10); f.core.recover(second); f.chase();
  assert(matchesFilter(followFilters, f.mob, f.p));
  assert(!matchesFilter(followFilters, f.mob, other));
  assert(matchesFilter(followFilters, second, other));
  assert(!matchesFilter(followFilters, second, f.p));
  f.p.isSleeping = true; assert(!matchesFilter(followFilters, f.mob, f.p));
});
test("slot tags are rebuilt after restart/reconnect and foreign tags preserved", () => {
  const f = fixture(); f.p.tags.add("other:addon"); f.p.tags.add(TAG_PREFIX + "bit_12"); f.chase();
  assert(!f.p.tags.has(TAG_PREFIX + "bit_12")); assert(f.p.tags.has("other:addon"));
  const other = f.player(20); f.tick(); f.players.splice(0, 1); f.tick();
  const restarted = createDeathPrairieDog(f.deps); restarted.scan(); restarted.tick();
  assert.equal(restarted.playerSlots.get(other.id), 1);
  assert(other.tags.has(TAG_PREFIX + "bit_0")); assert(!other.tags.has(TAG_PREFIX + "bit_1"));
  f.players.push(f.p); restarted.tick();
  assert.equal(restarted.playerSlots.get(f.p.id), 2);
  assert(!f.p.tags.has(TAG_PREFIX + "bit_0")); assert(f.p.tags.has("other:addon"));
});
test("all 16 matching bits are represented; no duplicate group per player", () => {
  assert.equal(TARGET_BITS, 16);
  assert.equal(Object.keys(bp.component_groups).length, 2);
  for (let bit = 0; bit < TARGET_BITS; bit++) {
    assert.equal(bp.description.properties[TAG_PREFIX + `bit_${bit}`].type, "bool");
  }
  assert.equal(followFilters.all_of.filter(f => f.any_of).length, TARGET_BITS);
});

const controller = read("../minecraft/resource_packs/ichiyon_avatar_rp/animation_controllers/death_prairie_dog.controller.json")
  .animation_controllers["controller.animation.death_prairie_dog.state"];
function controllerStep(state, property, time = 0) {
  for (const transition of controller.states[state].transitions ?? []) {
    const [next, expression] = Object.entries(transition)[0];
    const js = expression.replaceAll("query.property('ichiyon:prairie_state')", JSON.stringify(property))
      .replaceAll("query.state_time", String(time));
    if (Function(`return (${js});`)()) return next;
  }
  return state;
}
test("all original clips are reachable; held transition exits once after 0.5 seconds", () => {
  assert.equal(controller.initial_state, "wander");
  assert.equal(controllerStep("wander", "transition"), "transition");
  assert.equal(controllerStep("transition", "chase", 0.49), "transition");
  assert.equal(controllerStep("transition", "chase", 0.5), "chase");
  assert.equal(controllerStep("chase", "chase", 50), "chase");
  assert.deepEqual(controller.states.transition.animations, ["transition"]);
  assert.deepEqual(controller.states.chase.animations, ["prairie4walk"]);
  assert("prairie2walk" in controller.states.wander.animations[0]);
});
test("late client arrival plays transition once; sleep/day override it immediately", () => {
  assert.equal(controllerStep("wander", "chase"), "transition");
  for (const property of ["wander", "flee"]) {
    assert.equal(controllerStep("transition", property, 0.1), "wander");
    assert.equal(controllerStep("chase", property, 0.1), "wander");
  }
});
