// Integration checks of the real Sparrow runtime, synthetic reference manifest,
// and fixture adapter through an offline Script API mock. This is NOT a Bedrock
// client playthrough and cannot validate rendering, reach, or mobile controls.
import assert from "node:assert/strict";
import { createRuntime } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/runtime.js";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";
import { sparrowGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/sparrow.js";
import { registerOriginalVenues, translateVenue } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/original_venue.js";
import { SPARROW_REFERENCE } from "./fixtures/junket_sparrow_reference.mjs";

registerOriginalVenues([translateVenue(SPARROW_REFERENCE)]);
const flush = () => new Promise(resolve => setImmediate(resolve));
let checks = 0;
async function test(name, fn) { await fn(); console.log(`OK ${++checks} ${name}`); }

function fixture() {
  let tick = 0, sequence = 0;
  const timers = new Map(), signals = {}, forms = [], entities = [], properties = new Map();
  const signal = name => ({ subscribe(fn) { signals[name] = fn; } });
  const dimension = {
    id: "minecraft:overworld",
    getEntities({ location, maxDistance } = {}) {
      return entities.filter(entity => !entity.removed && (!location ||
        Math.hypot(entity.location.x - location.x, entity.location.y - location.y,
          entity.location.z - location.z) <= maxDistance));
    },
    spawnEntity(typeId, location) {
      const tags = new Set(), dynamic = new Map(), display = new Map();
      const entity = {
        id: `entity-${entities.length}`, typeId, location: { ...location }, dimension,
        getComponent: name => name === "minecraft:health" ? { currentValue: 20, effectiveMax: 20 } : undefined,
        nameTag: "", removed: false, tags, dynamic, display,
        hasTag: tag => tags.has(tag), addTag: tag => tags.add(tag), getTags: () => [...tags],
        setDynamicProperty: (key, value) => dynamic.set(key, value),
        getDynamicProperty: key => dynamic.get(key),
        setProperty: (key, value) => display.set(key, value),
        remove() { this.removed = true; },
        teleport(at, options) { this.location = { ...at }; this.rotation = options?.rotation; },
      };
      entities.push(entity);
      return entity;
    },
  };
  const players = ["A", "B", "spectator"].map(id => ({
    id, name: id, typeId: "minecraft:player", dimension, location: { ...SPARROW_REFERENCE.destination },
    connected: true, messages: [], titles: [], actionBars: [], teleports: [],
    sendMessage(message) { this.messages.push(message); },
    playSound() {},
    teleport(at, options) {
      this.location = { ...at };
      this.dimension = options?.dimension ?? this.dimension;
      this.teleports.push({ at: { ...at }, options });
    },
  }));
  for (const player of players) player.onScreenDisplay = {
    setTitle: (title, options) => player.titles.push({ title, ...options }),
    setActionBar: value => player.actionBars.push(value),
  };
  class Form {
    buttons = [];
    title(value) { this.heading = value; return this; }
    body(value) { this.text = value; return this; }
    button(value) { this.buttons.push(value); return this; }
    textField(label, placeholder, options) { this.field = { label, placeholder, options }; return this; }
    show(player) { return new Promise(resolve => forms.push({ player, form: this, resolve })); }
  }
  const world = {
    getAllPlayers: () => players.filter(player => player.connected),
    getDimension: () => dimension,
    getDynamicProperty: key => properties.get(key),
    setDynamicProperty: (key, value) => properties.set(key, value),
    beforeEvents: { playerInteractWithEntity: signal("interact") },
    afterEvents: { playerLeave: signal("leave") },
  };
  const system = {
    run: fn => fn(),
    runTimeout(fn, delay = 0) { const id = ++sequence; timers.set(id, { at: tick + delay, fn }); return id; },
    clearRun: id => timers.delete(id),
    afterEvents: { scriptEventReceive: signal("script") },
  };
  const manager = new GameManager([sparrowGame], { world, system });
  const runtime = createRuntime({ world, system, manager, ActionFormData: Form, ModalFormData: Form });
  runtime.venue.ensure();
  runtime.venue.verify();
  return {
    ...runtime, world, system, players, forms, entities, signals, Form,
    role: role => entities.find(entity => !entity.removed && entity.getDynamicProperty("junket:sparrow_role") === role),
    async advance(ticks) {
      const until = tick + ticks;
      for (;;) {
        const due = [...timers.entries()].filter(([, value]) => value.at <= until)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        tick = due[1].at;
        timers.delete(due[0]);
        due[1].fn();
        await flush();
      }
      tick = until;
      await flush();
    },
    leave(player) { player.connected = false; signals.leave({ playerId: player.id }); },
    reconnect(player) { player.connected = true; runtime.resume(player.id); },
  };
}
async function click(f, player, role) {
  const target = typeof role === "string" ? f.role(role) : role;
  assert.ok(target, `missing fixture ${role}`);
  const event = { player, target, cancel: false };
  f.signals.interact(event);
  await flush();
  return event;
}
async function reply(f, player, response) {
  const index = f.forms.findIndex(entry => entry.player === player);
  assert.notEqual(index, -1, `no form for ${player.id}`);
  f.forms.splice(index, 1)[0].resolve(response);
  await flush();
}
async function join(f, player) {
  assert.equal((await click(f, player, "reception")).cancel, true);
  await reply(f, player, { selection: 0 });
}
async function joinBoth(f) { await join(f, f.players[0]); await join(f, f.players[1]); }
async function bet(f, player, value = "1000000", role = "tv") {
  await click(f, player, role);
  if (!f.forms.find(entry => entry.player === player)?.form.field) {
    await reply(f, player, { selection: 0 });
  }
  const form = f.forms.find(entry => entry.player === player).form;
  assert.equal(form.field.options.defaultValue, "1000000", "default bet must be a real input value");
  await reply(f, player, { formValues: [value] });
}
const state = f => f.manager.view("sparrow")?.state;
const partitionCount = f => f.entities.filter(entity => !entity.removed && entity.hasTag("junket:venue_temporary")).length;
const publicAppearance = f => f.entities.filter(entity => !entity.removed).map(entity => ({
  typeId: entity.typeId, location: entity.location, rotation: entity.rotation,
  name: entity.nameTag, properties: [...entity.display], dynamic: [...entity.dynamic],
}));

await test("synthetic hall coordinates, two reception joins, seating and physical turn validation", async () => {
  const f = fixture(), [a, b, spectator] = f.players;
  assert.deepEqual(f.venue.config().origin, { x: 0, y: 64, z: 0 });
  await join(f, a);
  assert.equal(state(f), null);
  await join(f, b);
  assert.deepEqual(a.location, SPARROW_REFERENCE.seats[0]);
  assert.deepEqual(b.location, SPARROW_REFERENCE.seats[1]);
  assert.deepEqual(state(f).money, [50_000_000, 50_000_000]);
  await bet(f, a, "999999");
  assert.equal(state(f).phase, "bet");
  await bet(f, a, "1000000.5");
  assert.equal(state(f).phase, "bet");
  await bet(f, a, "50000001");
  assert.equal(state(f).phase, "bet");
  await bet(f, a);
  assert.equal(state(f).phase, "hide");
  assert.equal(partitionCount(f), 1);
  assert.equal(f.venue.boxValue({ ...f.role("littlebox") }), "small",
    "a different Script API wrapper for the same native entity must be recognized");
  assert.equal(f.venue.control({ ...f.role("tv") }), "tv");
  for (const player of [b, spectator]) {
    await click(f, player, "bigbox");
    assert.equal(state(f).phase, "hide", "only the current participant may hide");
  }
  const decoy = f.world.getDimension().spawnEntity("junket:bigbox", f.role("bigbox").location);
  decoy.addTag("junket:venue_box"); decoy.setDynamicProperty("junket:box", "big");
  assert.equal((await click(f, a, decoy)).cancel, false, "unmanaged lookalike must not submit a choice");
  assert.equal(state(f).phase, "hide");
  await click(f, a, "littlebox");
  assert.equal(state(f).phase, "choose");
  assert.equal(partitionCount(f), 0);
  assert.equal("hidden" in state(f), false);
  assert.equal(f.role("tv").nameTag, "", "status must not return as dense floating text");
  assert.ok(a.actionBars.some(text => text.includes("箱を選ぶ")));
});

await test("hidden box choice has identical public visuals and messages", async () => {
  const snapshots = [];
  for (const hidden of ["bigbox", "littlebox"]) {
    const f = fixture(), [a, b] = f.players;
    await joinBoth(f); await bet(f, a); await click(f, a, hidden);
    snapshots.push({ appearance: publicAppearance(f), messages: b.messages, actionBars: b.actionBars });
    assert.equal("hidden" in state(f), false);
    assert.deepEqual(f.role("diamond").location, { x: 5, y: 67, z: 5 });
  }
  assert.deepEqual(snapshots[0], snapshots[1]);
});

await test("six physical rounds, both box sizes, reveal lock, settlement, finish, return and rematch", async () => {
  const f = fixture(), [a, b, spectator] = f.players;
  await joinBoth(f);
  const choices = [["big", "big"], ["small", "big"], ["small", "small"],
    ["big", "small"], ["big", "big"], ["small", "small"]];
  let expected = [50_000_000, 50_000_000];
  const role = box => box === "big" ? "bigbox" : "littlebox";
  for (let round = 1; round <= 6; round++) {
    const setter = (round - 1) % 2, chooser = 1 - setter;
    assert.equal(state(f).round, round);
    assert.equal(state(f).actor, setter);
    await bet(f, f.players[setter], "1000001");
    const [hidden, chosen] = choices[round - 1];
    await click(f, f.players[setter], role(hidden));
    await click(f, f.players[chooser], role(chosen));
    const winner = hidden === chosen ? chooser : setter;
    const amount = chosen === "big" ? 1_000_001 : 500_000;
    expected[winner] += amount; expected[1 - winner] -= amount;
    assert.deepEqual(state(f).money, expected);
    assert.equal(f.venue.locked(), true);
    await click(f, f.players[chooser], role(chosen));
    assert.deepEqual(state(f).money, expected, "double click must not settle twice");
    await f.advance(70);
    assert.equal(f.venue.locked(), false);
    if (round < 6) assert.equal(state(f).phase, "bet");
  }
  assert.equal(f.manager.forPlayer(a.id), null);
  assert.equal(f.manager.forPlayer(b.id), null);
  assert.deepEqual(a.location, SPARROW_REFERENCE.destination);
  assert.deepEqual(b.location, SPARROW_REFERENCE.destination);
  assert.equal(partitionCount(f), 0);
  assert.equal(spectator.messages.length, 0);
  assert.ok(a.titles.some(value => value.title === "Bの勝利"));
  assert.equal(f.role("tv").nameTag, "");
  assert.ok(a.actionBars.some(text => text.includes("6/6 終了")));
  await joinBoth(f);
  assert.equal(state(f).round, 1);
  assert.deepEqual(state(f).money, [50_000_000, 50_000_000]);
  assert.equal(f.role("bigbox").display.get("junket:frame"), 0);
  await click(f, a, "return");
  assert.ok(f.manager.view("sparrow"), "catalog return does not implicitly abort the match");
  f.signals.script({ id: "junket:leave", sourceEntity: a, message: "" });
  assert.equal(f.manager.view("sparrow"), null);
  assert.deepEqual(a.location, SPARROW_REFERENCE.destination);
  assert.deepEqual(b.location, SPARROW_REFERENCE.destination);
});

await test("diamond private form, canceled form, menu leave and early bankruptcy", async () => {
  const f = fixture(), [a, b] = f.players;
  await joinBoth(f); await bet(f, a);
  await click(f, a, "diamond");
  await reply(f, a, { canceled: true });
  assert.equal(state(f).phase, "hide");
  await click(f, a, "diamond");
  await reply(f, a, { selection: 1 });
  assert.equal(state(f).phase, "choose");
  await click(f, b, "reception");
  const leaveForm = f.forms.find(entry => entry.player === b).form;
  await reply(f, b, { selection: leaveForm.buttons.findIndex(label => label.includes("退出")) });
  assert.equal(f.manager.view("sparrow"), null);
  assert.equal(partitionCount(f), 0);
  await joinBoth(f); await bet(f, a, "50000000");
  await click(f, a, "bigbox"); await click(f, b, "bigbox");
  assert.equal(state(f).reason, "insufficient_funds");
  await f.advance(70);
  assert.equal(f.manager.view("sparrow"), null);
  assert.ok(a.titles.some(value => value.title === "Bの勝利"));
});

await test("disconnect pauses actions, reconnect restores seat, timeout aborts waiting and active matches", async () => {
  const f = fixture(), [a, b] = f.players;
  await joinBoth(f); await bet(f, a); await click(f, a, "littlebox");
  const token = f.manager.view("sparrow").token;
  f.leave(a);
  await click(f, b, "bigbox");
  assert.equal(f.manager.view("sparrow").token, token, "offline opponent must block settlement");
  await f.advance(100);
  a.location = { x: 0, y: 0, z: 0 };
  f.reconnect(a);
  assert.deepEqual(a.location, SPARROW_REFERENCE.seats[0]);
  assert.equal(state(f).phase, "choose");
  await f.advance(100);
  assert.ok(f.manager.view("sparrow"), "cleared disconnect timer cannot abort a reconnected match");
  await click(f, b, "bigbox"); await f.advance(70);
  f.leave(a); await f.advance(200);
  assert.equal(f.manager.view("sparrow"), null);
  assert.deepEqual(b.location, SPARROW_REFERENCE.destination);

  const waiting = fixture(), [one, two] = waiting.players;
  await join(waiting, one); waiting.leave(one);
  await join(waiting, two); // changes the token after the first player's departure
  await waiting.advance(200);
  assert.equal(waiting.manager.view("sparrow"), null,
    "joining a disconnected waiting player must not invalidate the abort timer");
  const late = fixture(), [latePlayer] = late.players;
  await click(late, latePlayer, "reception");
  late.leave(latePlayer);
  await reply(late, latePlayer, { selection: 0 });
  assert.equal(late.manager.view("sparrow"), null,
    "a form response arriving after disconnect must not create an orphan registration");
});

await test("abort during reveal invalidates old animation and does not affect a rematch", async () => {
  const f = fixture(), [a, b] = f.players;
  await joinBoth(f); await bet(f, a);
  await click(f, a, "bigbox"); await click(f, b, "bigbox");
  assert.equal(f.venue.locked(), true);
  await click(f, a, "return");
  assert.ok(f.manager.view("sparrow"), "explicit leave is required even during the reveal");
  f.signals.script({ id: "junket:leave", sourceEntity: a, message: "" });
  assert.equal(f.manager.view("sparrow"), null);
  await joinBoth(f);
  await f.advance(5);
  assert.equal(state(f).round, 1);
  assert.deepEqual(state(f).money, [50_000_000, 50_000_000]);
  assert.equal(f.venue.locked(), false);
  assert.equal(f.role("bigbox").display.get("junket:frame"), 0);
});

await test("persisted hidden choice resumes without revealing it, then settles correctly", async () => {
  const f = fixture(), [a, b] = f.players;
  await joinBoth(f); await bet(f, a); await click(f, a, "littlebox");
  const manager = new GameManager([sparrowGame], { world: f.world, system: f.system });
  assert.deepEqual(manager.restore(), { restored: 1, skipped: 0 });
  const resumed = createRuntime({ world: f.world, system: f.system, manager,
    ActionFormData: f.Form, ModalFormData: f.Form });
  resumed.resume();
  assert.equal("hidden" in resumed.manager.view("sparrow").state, false);
  assert.equal(f.role("bigbox").display.get("junket:frame"), 0);
  assert.equal(f.role("littlebox").display.get("junket:frame"), 0);
  assert.deepEqual(f.role("diamond").location, { x: 5, y: 67, z: 5 });
  await click(f, b, "littlebox");
  assert.deepEqual(manager.view("sparrow").state.money, [49_500_000, 50_500_000]);
  await f.advance(70);
  assert.equal(manager.view("sparrow").state.phase, "bet");
});

await test("restored sessions with missing players expire even without a playerLeave event", async () => {
  for (const onlineCount of [0, 1]) {
    const f = fixture();
    await joinBoth(f);
    for (let index = onlineCount; index < 2; index++) f.players[index].connected = false;
    const manager = new GameManager([sparrowGame], { world: f.world, system: f.system });
    assert.deepEqual(manager.restore(), { restored: 1, skipped: 0 });
    const resumed = createRuntime({ world: f.world, system: f.system, manager,
      ActionFormData: f.Form, ModalFormData: f.Form });
    resumed.resume();
    await f.advance(199);
    assert.ok(manager.view("sparrow"), "restored players get the full reconnect grace period");
    await f.advance(1);
    assert.equal(manager.view("sparrow"), null, "an offline restored table must not wait forever");
    if (onlineCount) assert.deepEqual(f.players[0].location, SPARROW_REFERENCE.destination);
  }
});

console.log(`${checks} Sparrow runtime checks passed with synthetic reference data (offline Script API mock; Bedrock rendering/two-client playthrough NOT verified).`);
