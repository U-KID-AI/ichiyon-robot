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
  const online = ["one", "two", "spectator"].map(id => ({ id, name: id, typeId: "minecraft:player",
    messages: [], sendMessage(message) { this.messages.push(message); } }));
  const signal = name => ({ subscribe(fn) { signals[name] = fn; } });
  class Form {
    title(value) { this.heading = value; return this; }
    body(value) { this.text = value; return this; }
    button() { return this; }
    textField() { return this; }
    show(player) { return new Promise(resolve => forms.push({ player, form: this, resolve })); }
  }
  const world = { getAllPlayers: () => online, beforeEvents: { playerInteractWithEntity: signal("interact") },
    afterEvents: { playerLeave: signal("leave") } };
  const system = { run: fn => fn(), runTimeout(fn) { timers.push(fn); }, afterEvents: { scriptEventReceive: signal("script") } };
  return { ...createRuntime({ world, system, ActionFormData: Form, ModalFormData: Form }), online, forms, timers, signals };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
async function submit(f, response) { f.forms.shift().resolve(response); await flush(); }
await test("runtime two voluntary joins, private choice, reveal and disconnect", async () => {
  const f = fixture(), [a, b, spectator] = f.online;
  let pending = f.open(a); await submit(f, { selection: 0 }); await pending;
  pending = f.open(b); await submit(f, { selection: 0 }); await pending;
  assert.equal(f.manager.forPlayer(a.id).state.phase, "bet");
  pending = f.open(a); await submit(f, { selection: 0 }); await submit(f, { formValues: ["1000001"] }); await pending;
  pending = f.open(a); await submit(f, { selection: 0 }); await submit(f, { selection: 1 }); await pending;
  assert.equal(f.manager.forPlayer(b.id).state.phase, "choose");
  assert.equal(b.messages.some(message => message.includes("宝石=小箱")), false);
  assert.equal(spectator.messages.length, 0);
  pending = f.open(b); await submit(f, { selection: 0 }); await submit(f, { selection: 1 }); await pending;
  assert.deepEqual(f.manager.forPlayer(b.id).state.money, [49_500_000, 50_500_000]);
  assert.ok(b.messages.some(message => message.includes("宝石=小箱")));
  f.signals.leave({ playerId: a.id });
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
  pending = f.open(a); await submit(f, { selection: 0 });
  f.signals.leave({ playerId: b.id });
  f.manager.join("sparrow", "sparrow", a.id); f.manager.join("sparrow", "sparrow", b.id);
  await submit(f, { formValues: ["1000000"] }); await pending;
  assert.equal(f.manager.forPlayer(a.id).state.phase, "bet");
  assert.ok(a.messages.some(message => message.includes("古く")));
});
await test("only tagged receptionist opens UI; spectators cannot abort", async () => {
  const f = fixture(), [a, b, spectator] = f.online;
  f.signals.interact({ player: a, target: { hasTag: () => false } });
  assert.equal(f.forms.length, 0);
  f.manager.join("sparrow", "sparrow", a.id); f.manager.join("sparrow", "sparrow", b.id);
  f.signals.script({ id: "junket:leave", sourceEntity: spectator });
  assert.ok(f.manager.forPlayer(a.id));
  f.signals.interact({ player: a, target: { hasTag: tag => tag === "junket:reception" } });
  await submit(f, { selection: 1 });
  assert.equal(f.manager.forPlayer(a.id), null);
});
await test("independent pack manifest uses stable modules and unique UUIDs", () => {
  const manifest = JSON.parse(readFileSync(new URL("../minecraft/behavior_packs/ichiyon_junket_bp/manifest.json", import.meta.url)));
  const uuids = [manifest.header.uuid, ...manifest.modules.map(m => m.uuid)];
  assert.equal(new Set(uuids).size, uuids.length);
  assert.deepEqual(manifest.header.version, [0, 9, 4]);
  assert.deepEqual(manifest.header.min_engine_version, [1, 26, 44]);
  assert.deepEqual(manifest.modules.map(module => module.version), [[0, 9, 4], [0, 9, 4]]);
  assert.equal(manifest.modules.find(module => module.type === "script").entry, "scripts/main.js");
  assert.deepEqual(manifest.dependencies.map(d => d.module_name), ["@minecraft/server", "@minecraft/server-ui"]);
  assert.ok(manifest.dependencies.every(d => !d.version.includes("beta")));
});
console.log(`${count} JUNKET checks passed (offline; no Bedrock runtime).`);
