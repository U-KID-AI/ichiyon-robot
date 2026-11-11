import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createSparrow, advanceSparrow, sparrowView } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/sparrow.js";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";
import { sparrowGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/sparrow.js";
import { createRuntime } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/runtime.js";

let count = 0;
async function test(name, fn) { await fn(); console.log(`OK ${++count} ${name}`); }
function round(s, bet, hidden, chosen) {
  s = advanceSparrow(s, s.setter, "bet", bet);
  s = advanceSparrow(s, s.setter, "hide", hidden);
  assert.equal("hidden" in sparrowView(s), false);
  return advanceSparrow(s, 1 - s.setter, "choose", chosen);
}

await test("all eight reveal branches and odd half-bet rounding", () => {
  for (const setter of [0, 1]) for (const hidden of ["big", "small"]) for (const chosen of ["big", "small"]) {
    const state = { ...createSparrow(), setter };
    const result = round(state, 1_000_001, hidden, chosen);
    const winner = chosen === hidden ? 1 - setter : setter;
    const amount = chosen === "big" ? 1_000_001 : 500_000;
    assert.equal(result.last.winner, winner);
    assert.equal(result.last.amount, amount);
    assert.equal(result.money[winner], 50_000_000 + amount);
    assert.equal(result.money[1 - winner], 50_000_000 - amount);
    assert.deepEqual(state.money, [50_000_000, 50_000_000]);
    assert.equal(result.setter, 1 - setter);
    assert.equal(result.hidden, null);
  }
});
await test("six rounds end in draw; cannot settle twice", () => {
  let s = createSparrow();
  for (let i = 0; i < 6; i++) {
    s = round(s, 1_000_000, "big", "big");
    assert.equal(s.money.reduce((a, b) => a + b), 100_000_000);
  }
  assert.equal(s.phase, "finished"); assert.equal(s.round, 6);
  assert.equal(s.reason, "six_rounds"); assert.equal(s.winner, null);
  assert.throws(() => advanceSparrow(s, 0, "bet", 1_000_000));
});
await test("insufficient funds ends early, retaining source's bettor-only cap", () => {
  let s = round(createSparrow(), 49_000_001, "big", "small");
  assert.equal(s.phase, "bet");
  assert.deepEqual(s.money, [74_500_000, 25_500_000]);
  s = round(s, 1_000_000, "big", "big");
  s = round(s, 75_500_000, "small", "big");
  assert.equal(s.phase, "finished"); assert.equal(s.winner, 0);
  assert.equal(s.reason, "insufficient_funds"); assert.deepEqual(s.money, [151_000_000, -51_000_000]);
  const zero = round(createSparrow(), 50_000_000, "big", "big");
  assert.equal(zero.phase, "finished"); assert.deepEqual(zero.money, [0, 100_000_000]);
});
await test("reject wrong actor, wrong phase, malformed input without mutation", () => {
  const s = createSparrow(), before = JSON.stringify(s);
  for (const amount of [0, 999_999, 50_000_001, 1_000_000.5, NaN, Infinity, "1000000"]) {
    assert.throws(() => advanceSparrow(s, 0, "bet", amount));
  }
  assert.throws(() => advanceSparrow(s, 1, "bet", 1_000_000));
  assert.throws(() => advanceSparrow(s, 0, "hide", "big"));
  const hiding = advanceSparrow(s, 0, "bet", 1_000_000);
  assert.throws(() => advanceSparrow(hiding, 0, "hide", "unknown"));
  assert.equal(JSON.stringify(s), before);
});
await test("table membership, game isolation, stale forms and cleanup", () => {
  const m = new GameManager([sparrowGame]);
  m.join("a", "sparrow", "one");
  assert.throws(() => m.join("b", "sparrow", "one"));
  const started = m.join("a", "sparrow", "two");
  assert.throws(() => m.join("a", "sparrow", "three"));
  m.join("b", "sparrow", "three"); m.join("b", "sparrow", "four");
  assert.throws(() => m.act("two", started.token, "bet", 1_000_000));
  const afterBet = m.act("one", started.token, "bet", 1_000_000);
  assert.throws(() => m.act("one", started.token, "bet", 1_000_000));
  m.act("one", afterBet.token, "hide", "small");
  assert.equal("hidden" in m.forPlayer("two").state, false);
  const detached = m.forPlayer("two"); detached.players[0] = "intruder"; detached.state.money[0] = 0;
  assert.equal(m.forPlayer("one").state.money[0], 50_000_000);
  assert.equal(m.forPlayer("one").players[0], "one");
  assert.equal(m.leave("one").players.length, 2);
  assert.equal(m.forPlayer("two"), null); assert.equal(m.view("a"), null);
  assert.equal(m.view("b").players.length, 2);
  m.join("a", "sparrow", "one"); m.join("a", "sparrow", "two");
  assert.throws(() => m.act("one", afterBet.token, "hide", "big"));
});

function fixture() {
  const forms = [], timers = [], signals = {};
  const venueEntities = [], venueProperties = new Map();
  const venueDimension = {
    id: "minecraft:overworld",
    getEntities({ tags = [], location, maxDistance } = {}) {
      return venueEntities.filter(entity => !entity.removed &&
        tags.every(tag => entity.hasTag(tag)) &&
        (!location || Math.hypot(entity.location.x - location.x,
          entity.location.y - location.y, entity.location.z - location.z) <= maxDistance));
    },
    spawnEntity(typeId, location) {
      const tags = new Set(), properties = new Map();
      const entity = {
        typeId, location: { ...location }, dimension: venueDimension, nameTag: "", removed: false,
        addTag(tag) { tags.add(tag); }, hasTag(tag) { return tags.has(tag); },
        getTags() { return [...tags]; }, remove() { this.removed = true; },
        teleport(at) { this.location = { ...at }; },
        setProperty(name, value) { properties.set(name, value); },
        setDynamicProperty(name, value) { properties.set(name, value); },
        getDynamicProperty(name) { return properties.get(name); },
        getComponent(name) { return name === "minecraft:health" ? { currentValue: 20, effectiveMax: 20 } : undefined; },
      };
      venueEntities.push(entity);
      return entity;
    },
  };
  const online = ["one", "two", "spectator"].map(id => ({ id, name: id, typeId: "minecraft:player", connected: true,
    dimension: venueDimension, location: { x: 0, y: 0, z: 0 }, messages: [],
    sendMessage(message) { this.messages.push(message); },
    teleport(location, options) { this.location = { ...location }; this.dimension = options?.dimension ?? this.dimension; },
    onScreenDisplay: { setActionBar() {}, setTitle() {} } }));
  const signal = name => ({ subscribe(fn) { signals[name] = fn; } });
  const leaveSignal = { subscribe(fn) {
    signals.leave = event => {
      const player = online.find(candidate => candidate.id === event.playerId);
      if (player) player.connected = false;
      fn(event);
    };
  } };
  class Form {
    title(value) { this.heading = value; return this; }
    body(value) { this.text = value; return this; }
    button(value) { (this.buttons ??= []).push(value); return this; }
    textField() { return this; }
    show(player) { return new Promise(resolve => forms.push({ player, form: this, resolve })); }
  }
  const world = { getAllPlayers: () => online.filter(player => player.connected),
    beforeEvents: { playerInteractWithEntity: signal("interact") },
    afterEvents: { playerLeave: leaveSignal },
    getDimension: () => venueDimension,
    getDynamicProperty: key => venueProperties.get(key),
    setDynamicProperty: (key, value) => venueProperties.set(key, value) };
  const system = { run: fn => fn(), runTimeout(fn) { timers.push(fn); }, afterEvents: { scriptEventReceive: signal("script") } };
  return { ...createRuntime({ world, system, ActionFormData: Form, ModalFormData: Form }), online, forms, timers, signals, venueEntities };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
async function submit(f, response) { f.forms.shift().resolve(response); await flush(); }
async function clickBox(f, player, choice) {
  const target = f.venueEntities.find(entity => !entity.removed &&
    entity.hasTag("junket:venue_box") && entity.getDynamicProperty("junket:box") === choice);
  assert.ok(target, `Expected the ${choice} box`);
  const event = { player, target, cancel: false };
  f.signals.interact(event);
  assert.equal(event.cancel, true);
  await flush();
}
await test("runtime two voluntary joins, private choice, reveal and disconnect", async () => {
  const f = fixture(), [a, b, spectator] = f.online;
  let pending = f.open(a); await submit(f, { selection: 0 }); await pending;
  pending = f.open(b); await submit(f, { selection: 0 }); await pending;
  assert.ok(f.manager.forPlayer(a.id), [...a.messages, ...b.messages].join(" | "));
  assert.equal(f.manager.forPlayer(a.id).state.phase, "bet");
  pending = f.open(a); await submit(f, { formValues: ["1000001"] }); await pending;
  assert.equal(f.manager.forPlayer(a.id).state.phase, "hide");
  await clickBox(f, a, "small");
  assert.equal(f.manager.forPlayer(b.id).state.phase, "choose");
  assert.equal(b.messages.some(message => message.includes("宝石=小箱")), false);
  assert.equal(spectator.messages.length, 0);
  await clickBox(f, b, "small");
  assert.deepEqual(f.manager.forPlayer(b.id).state.money, [49_500_000, 50_500_000]);
  assert.ok(b.messages.some(message => message.includes("宝石=小箱")));
  f.signals.leave({ playerId: a.id });
  assert.ok(f.timers.length > 0, "disconnect must arm the reconnect grace timer");
  f.timers.at(-1)();
  assert.equal(f.manager.forPlayer(b.id), null);
  for (const timer of f.timers) timer();
  assert.equal(f.forms.length, 0);
});
await test("UI cancellation, duplicate open, stale reply after a new match", async () => {
  const f = fixture(), [a, b] = f.online;
  let pending = f.open(a); await f.open(a); assert.equal(f.forms.length, 1);
  await submit(f, { canceled: true }); await pending;
  assert.equal(f.manager.forPlayer(a.id), null);
  f.manager.join("sparrow", "sparrow", a.id); f.manager.join("sparrow", "sparrow", b.id);
  pending = f.open(a);
  f.signals.leave({ playerId: b.id });
  f.timers.at(-1)();
  assert.equal(f.manager.forPlayer(a.id), null);
  f.manager.join("sparrow", "sparrow", a.id); f.manager.join("sparrow", "sparrow", b.id);
  await submit(f, { formValues: ["1000000"] }); await pending;
  assert.equal(f.manager.forPlayer(a.id).state.phase, "bet");
  assert.equal(f.manager.forPlayer(a.id).state.phase, "bet", "disconnection invalidates the old pending form");
});
await test("only tagged receptionist opens UI; spectators cannot abort", async () => {
  const f = fixture(), [a, b, spectator] = f.online;
  f.signals.interact({ player: a, target: { hasTag: () => false } });
  assert.equal(f.forms.length, 0);
  f.manager.join("sparrow", "sparrow", a.id); f.manager.join("sparrow", "sparrow", b.id);
  f.signals.script({ id: "junket:leave", sourceEntity: spectator });
  assert.ok(f.manager.forPlayer(a.id));
  f.signals.interact({ player: a, target: { hasTag: tag => tag === "junket:reception" } });
  const leave = f.forms[0].form.buttons.indexOf("退出");
  assert.notEqual(leave, -1);
  await submit(f, { selection: leave });
  assert.equal(f.manager.forPlayer(a.id), null);
});
await test("catalog return fixture does not also abort or move a Sparrow participant", () => {
  const f = fixture(), [a, b] = f.online;
  f.manager.join("sparrow", "sparrow", a.id); f.manager.join("sparrow", "sparrow", b.id);
  const before = { ...a.location };
  f.signals.interact({ player: a, target: { hasTag: tag => tag === "junket:return" } });
  assert.ok(f.manager.forPlayer(a.id), "shared warp route requires an explicit leave first");
  assert.deepEqual(a.location, before);
  assert.equal(f.forms.length, 0);
});
await test("recovery hook fences a pending private form without closing membership or undoing evacuation", async () => {
  const f = fixture(), [a, b] = f.online;
  f.manager.join("sparrow", "sparrow", a.id); f.manager.join("sparrow", "sparrow", b.id);
  f.venue.ensure();
  const pending = f.open(a);
  assert.ok(f.forms.length, "private bet form must be pending");
  const table = f.manager.view("sparrow");
  a.location = { x: 10000, y: 65, z: 10000 };
  await f.resetSession(table, "test reset", { evacuated: true });
  await pending;
  assert.ok(f.manager.forPlayer(a.id), "the recovery controller alone closes membership");
  assert.deepEqual(a.location, { x: 10000, y: 65, z: 10000 });
  f.manager.close("sparrow");
  f.manager.join("sparrow", "sparrow", a.id); f.manager.join("sparrow", "sparrow", b.id);
  await submit(f, { formValues: ["1000000"] });
  assert.equal(f.manager.forPlayer(a.id).state.phase, "bet", "late private form must not reach the rematch");
});
await test("independent pack manifest uses stable modules and unique UUIDs", () => {
  const manifest = JSON.parse(readFileSync(new URL("../minecraft/behavior_packs/ichiyon_junket_bp/manifest.json", import.meta.url)));
  const uuids = [manifest.header.uuid, ...manifest.modules.map(m => m.uuid)];
  assert.equal(new Set(uuids).size, uuids.length);
  assert.deepEqual(manifest.header.version, [0, 9, 27]);
  assert.deepEqual(manifest.header.min_engine_version, [1, 26, 44]);
  assert.deepEqual(manifest.modules.map(module => module.version), [[0, 9, 27], [0, 9, 27]]);
  assert.equal(manifest.modules.find(module => module.type === "script").entry, "scripts/main.js");
  assert.deepEqual(manifest.dependencies.map(d => d.module_name), ["@minecraft/server", "@minecraft/server-ui"]);
  assert.ok(manifest.dependencies.every(d => !d.version.includes("beta")));
});
await test("Hope judges are persistent custom entities without trader despawn", () => {
  const behavior = JSON.parse(readFileSync(new URL(
    "../minecraft/behavior_packs/ichiyon_junket_bp/entities/hope_judge.json", import.meta.url)));
  const entity = behavior["minecraft:entity"];
  assert.equal(entity.description.identifier, "junket:hope_judge");
  assert.equal(entity.description.is_spawnable, false);
  assert.ok(entity.components["minecraft:persistent"]);
  assert.equal(entity.components["minecraft:despawn"], undefined);
  assert.equal(entity.components["minecraft:timer"], undefined);
  assert.equal(entity.components["minecraft:physics"].has_collision, true);

  const resource = JSON.parse(readFileSync(new URL(
    "../minecraft/resource_packs/ichiyon_junket_rp/entity/junket_hope_judge.entity.json", import.meta.url)));
  const client = resource["minecraft:client_entity"].description;
  assert.equal(client.identifier, "junket:hope_judge");
  assert.equal(client.materials.default, "wandering_trader");
  assert.equal(client.geometry.default, "geometry.villager_v2");
  assert.equal(client.textures.default, "textures/entity/wandering_trader");
  assert.deepEqual(client.render_controllers, ["controller.render.wandering_trader"]);
});
console.log(`${count} JUNKET checks passed (offline; no Bedrock runtime).`);
