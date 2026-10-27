// Offline tests for mobile/block routing and form lifetimes. No client rendering.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServiceControls, createResetPrompt, isServiceGuide } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/service_controls.js";
import { createRuntimeForms, showFormSafely, registerCatalogReturn, requestCatalogReturn, showReceptionNavigation } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue_presentation.js";

let checks = 0;
async function test(name, fn) { await fn(); console.log(`OK ${++checks} ${name}`); }
const flush = async () => { for (let n = 0; n < 4; n++) await new Promise(resolve => setImmediate(resolve)); };
const fixture = { role: "reception", type: "junket:guide", location: [5, 65, 5] };
const definition = { id: "sparrow", title: "ウラギリスズメ", fixtures: [fixture,
  { role: "return", type: "junket:guide", location: [8, 65, 5] }],
service_controls: ["menu"].map((action, index) => ({ action,
  button: [10 + index * 2, 65, 10], sign: [10 + index * 2, 65, 11],
  button_block: { name: "minecraft:stone_button" }, sign_block: { name: "minecraft:standing_sign" },
})) };

function harness() {
  const calls = [], callbacks = [], forms = [], subscriptions = {};
  const players = ["first", "second"].map(id => ({ id, isValid: true, messages: [],
    dimension: { id: "minecraft:overworld" }, name: id,
    sendMessage(text) { this.messages.push(text); }, hasTag: () => true }));
  const signal = name => ({ subscribe(callback) { (subscriptions[name] ??= []).push(callback); } });
  const world = { getAllPlayers: () => players.filter(player => player.isValid),
    beforeEvents: { playerInteractWithBlock: signal("block"), playerInteractWithEntity: signal("entity") },
    afterEvents: { playerLeave: signal("leave") } };
  const system = { currentTick: 10, run(callback) { callbacks.push(callback); } };
  let table = null, membership = null, operator = false;
  const manager = { forPlayer: () => membership, view: () => table };
  class Form {
    title(value) { this.heading = value; return this; }
    body(value) { this.text = value; return this; }
    button(value) { (this.buttons ??= []).push(value); return this; }
    show(player) { return new Promise(resolve => forms.push({ form: this, player, resolve })); }
  }
  const runtime = { async openReception(player) { calls.push(["reception", player.id]); } };
  const recovery = {
    isOperator: () => operator,
    async requestReset(player, id) { calls.push(["reset", player.id, id]); },
    async recover(id, options) {
      if (!operator) throw new Error("operator permission revoked");
      calls.push(["repair", options.player.id, id, options.force]);
    },
  };
  const warp = { async returnToEntryAndWait(player) { calls.push(["return", player.id]); return true; } };
  const service = createServiceControls({ world, system, manager, ActionFormData: Form,
    runtimes: new Map([["sparrow", runtime]]), venues: [definition], recovery, warp }).install();
  return { world, system, manager, players, calls, callbacks, forms, Form, runtime, recovery, service,
    setTable: value => { table = value; }, setMembership: value => { membership = value; },
    setOperator: value => { operator = value; },
    emit(name, event) { for (const callback of subscriptions[name] ?? []) callback(event); },
    async drain() { while (callbacks.length) callbacks.shift()(); await flush(); },
    block(action, player = players[0], kind = "button") {
      const control = definition.service_controls.find(value => value.action === action);
      const [x, y, z] = control[kind];
      return { cancel: false, player, block: { typeId: control[`${kind}_block`].name,
        dimension: { id: "minecraft:overworld" }, location: { x, y, z } } };
    },
  };
}

if (process.argv.includes("--manifest")) await test("all generated 17 venues expose one game operation wall panel", () => {
  const path = process.argv.includes("--manifest") ? process.argv[process.argv.indexOf("--manifest") + 1]
    : null;
  const venues = JSON.parse(readFileSync(path, "utf8")).venues;
  assert.equal(venues.length, 17);
  const points = new Set();
  for (const venue of venues) {
    assert.deepEqual(venue.service_controls.map(value => value.action).sort(), ["menu"]);
    assert.equal(venue.legacy_service_controls.length, 4);
    for (const control of venue.service_controls) for (const kind of ["button", "sign"]) {
      const key = control[kind].join(","); assert.ok(!points.has(key), key); points.add(key);
    }
  }
  assert.equal(points.size, 34);
});
else console.log("SKIP private 17-venue panel coverage: supply --manifest explicitly; synthetic UI contracts run below");
await test("before-event only cancels, then dispatches the exact presser", async () => {
  const h = harness(), event = h.block("menu", h.players[1]);
  h.emit("block", event);
  assert.equal(event.cancel, true); assert.equal(h.calls.length, 0);
  await h.drain(); assert.deepEqual(h.calls, [["reception", "second"]]);
});
await test("two simultaneous players remain separate while rapid repeats are ignored", async () => {
  const h = harness();
  h.emit("block", h.block("menu", h.players[0]));
  h.emit("block", h.block("menu", h.players[0]));
  h.emit("block", h.block("menu", h.players[1]));
  await h.drain(); assert.deepEqual(h.calls, [["reception", "first"], ["reception", "second"]]);
  h.system.currentTick += 7; h.emit("block", h.block("menu")); await h.drain();
  assert.equal(h.calls.length, 3);
});
await test("wrong dimensions, replaced blocks and unrelated blocks stay untouched", async () => {
  const h = harness();
  const events = [h.block("menu"), h.block("menu"), h.block("menu")];
  events[0].block.dimension.id = "minecraft:nether";
  events[1].block.typeId = "minecraft:air";
  events[2].block.location.x += 100;
  for (const event of events) { h.emit("block", event); assert.equal(event.cancel, false); }
  await h.drain(); assert.equal(h.calls.length, 0);
});
await test("sign tap opens the same action without the vanilla editor", async () => {
  const h = harness(), event = h.block("menu", h.players[0], "sign");
  h.emit("block", event); assert.equal(event.cancel, true); await h.drain();
  assert.deepEqual(h.calls, [["reception", "first"]]);
});
await test("owned custom and legacy guides cancel NPC editing and share one route", async () => {
  for (const typeId of ["junket:guide", "minecraft:npc"]) {
    const h = harness();
    const target = { typeId, dimension: { id: "minecraft:overworld" }, location: { x: 5, y: 65, z: 5 },
      hasTag: tag => tag === "junket:sparrow:fixture:reception",
      getDynamicProperty: key => key === "junket:venue_id" ? "sparrow" : undefined };
    assert.equal(isServiceGuide(h.world, target), true);
    const event = { cancel: false, player: h.players[0], target };
    h.emit("entity", event); assert.equal(event.cancel, true); await h.drain();
    assert.deepEqual(h.calls, [["reception", "first"]]);
    target.location.x += 30; assert.equal(isServiceGuide(h.world, target), false);
  }
});
await test("catalog return is direct and requires an explicit leave", async () => {
  const h = harness(); h.setMembership({ id: "sparrow", token: 1, players: ["first"] });
  void h.service.dispatch(h.players[0], "sparrow", "return"); await h.drain(); assert.equal(h.calls.length, 0);
  assert.match(h.players[0].messages.at(-1), /退出/);
  h.setMembership(null); h.system.currentTick += 7;
  void h.service.dispatch(h.players[0], "sparrow", "return"); await h.drain(); assert.deepEqual(h.calls, [["return", "first"]]);
  registerCatalogReturn(h.world, player => h.calls.push(["direct", player.id]));
  requestCatalogReturn(h.players[0], h.world); assert.deepEqual(h.calls.at(-1), ["direct", "first"]);
});
await test("ordinary reset delegates to the unanimous consent controller", async () => {
  const h = harness(); void h.service.dispatch(h.players[0], "sparrow", "reset"); await h.drain();
  assert.deepEqual(h.calls, [["reset", "first", "sparrow"]]);
});
await test("a self-added tag does not authorize administrator repair", async () => {
  const h = harness(); void h.service.dispatch(h.players[0], "sparrow", "repair"); await h.drain();
  assert.equal(h.forms.length, 0); assert.equal(h.calls.length, 0);
  assert.match(h.players[0].messages.at(-1), /管理者専用/);
});
await test("operator repair requires confirmation and uses the forced engine API", async () => {
  const h = harness(); h.setOperator(true); void h.service.dispatch(h.players[0], "sparrow", "repair"); await h.drain();
  assert.deepEqual(h.forms[0].form.buttons, ["キャンセル", "強制修復を実行"]);
  h.forms.shift().resolve({ selection: 1 }); await flush();
  assert.deepEqual(h.calls, [["repair", "first", "sparrow", true]]);
});
await test("session change or revoked permission invalidates a repair confirmation", async () => {
  for (const change of ["session", "permission"]) {
    const h = harness(); h.setOperator(true); void h.service.dispatch(h.players[0], "sparrow", "repair"); await h.drain();
    if (change === "session") h.setTable({ id: "sparrow", token: 9, players: ["first"] });
    else h.setOperator(false);
    h.forms.shift().resolve({ selection: 1 }); await flush();
    assert.equal(h.calls.length, 0); assert.ok(h.players[0].messages.length);
  }
});
await test("logout and immediate rejoin cannot consume the old deferred press", async () => {
  const h = harness(); h.emit("block", h.block("menu"));
  h.emit("leave", { playerId: "first" });
  await h.drain(); assert.equal(h.calls.length, 0);
  h.emit("block", h.block("menu")); await h.drain();
  assert.deepEqual(h.calls, [["reception", "first"]]);
});
await test("all service errors are reported and release the next interaction", async () => {
  const h = harness(); h.runtime.openReception = async () => { throw new Error("fixture unavailable"); };
  h.emit("block", h.block("menu")); await h.drain();
  assert.match(h.players[0].messages.at(-1), /fixture unavailable/);
  h.system.currentTick += 7; void h.service.dispatch(h.players[0], "sparrow", "return"); await h.drain();
  assert.deepEqual(h.calls, [["return", "first"]]);
});
await test("UserBusy and rejected form promises explain how to retry", async () => {
  const h = harness(), player = h.players[0];
  const busy = await showFormSafely({ show: async () => ({ canceled: true, cancelationReason: "UserBusy" }) }, player);
  assert.equal(busy.canceled, true); assert.match(player.messages.at(-1), /チャットを閉じ/);
  const failed = await showFormSafely({ show: async () => { throw new Error("UI failure"); } }, player);
  assert.equal(failed.canceled, true); assert.match(player.messages.at(-1), /再操作/);
});
await test("reset fence cancels pending private forms immediately and ignores late replies", async () => {
  const h = harness();
  const wrapped = createRuntimeForms({ ActionFormData: h.Form, world: h.world });
  const pending = new wrapped.ActionFormData().title("private turn").button("bet").show(h.players[0]);
  wrapped.invalidate(["first"]);
  assert.deepEqual(await pending, { canceled: true, cancelationReason: "SessionReset" });
  h.forms.shift().resolve({ selection: 0 }); await flush();
  const next = new wrapped.ActionFormData().title("new match").show(h.players[0]);
  h.forms.shift().resolve({ selection: 1 }); assert.deepEqual(await next, { selection: 1 });
});
await test("reset consent is explicit and cancellation is a refusal", async () => {
  for (const response of [{ canceled: true }, { selection: 0 }, { selection: 1 }]) {
    const h = harness(), prompt = createResetPrompt({ ActionFormData: h.Form });
    const result = prompt(h.players[0], { title: "ウラギリスズメ", participants: h.players });
    assert.equal(h.forms[0].player.id, "first");
    h.forms.shift().resolve(response);
    assert.equal(await result, response.selection === 1);
  }
});
await test("one panel exposes state-aware reset and operator repair in the existing reception form", async () => {
  const h = harness(); h.setOperator(true);
  h.setMembership({ id: "sparrow", gameId: "sparrow", token: 7, players: ["first", "second"], state: {} });
  let insideRuntimeForm = false;
  h.runtime.openReception = async player => {
    insideRuntimeForm = true;
    try { return await showReceptionNavigation({ ActionFormData: h.Form, player,
      gameId: "sparrow", title: definition.title, joined: true, canAct: true }); }
    finally { insideRuntimeForm = false; }
  };
  h.recovery.requestReset = async (player, id) => {
    assert.equal(insideRuntimeForm, false, "reset never mutates a still-open per-game form token");
    h.calls.push(["reset", player.id, id]);
  };
  h.emit("block", h.block("menu")); await h.drain();
  const shown = h.forms.shift();
  assert.equal(shown.player.id, "first"); assert.match(shown.form.heading, /ゲーム操作$/);
  assert.ok(shown.form.buttons.includes("ゲームをリセット（全員同意）"));
  assert.ok(shown.form.buttons.includes("管理者：会場を修復"));
  assert.ok(!shown.form.buttons.includes("カタログへ戻る"), "participant leaves before catalog return");
  shown.resolve({ selection: shown.form.buttons.indexOf("ゲームをリセット（全員同意）") }); await flush();
  assert.deepEqual(h.calls, [["reset", "first", "sparrow"]]);
});
await test("two personal panel forms do not grant guests reset or operator actions", async () => {
  const h = harness();
  h.runtime.openReception = player => showReceptionNavigation({ ActionFormData: h.Form, player,
    gameId: "sparrow", title: definition.title, canJoin: true });
  for (const player of h.players) h.emit("block", h.block("menu", player));
  await h.drain(); assert.deepEqual(h.forms.map(value => value.player.id), ["first", "second"]);
  for (const shown of h.forms.splice(0)) {
    assert.ok(shown.form.buttons.includes("参加する"));
    assert.ok(shown.form.buttons.includes("カタログへ戻る"));
    assert.ok(!shown.form.buttons.some(value => /リセット|修復/.test(value)));
    shown.resolve({ canceled: true });
  }
  await flush(); assert.deepEqual(h.calls, []);
});
await test("stale panel reset cannot affect a new session and legacy positions are not bound", async () => {
  const h = harness();
  h.setMembership({ id: "sparrow", gameId: "sparrow", token: 7, players: ["first"] });
  h.runtime.openReception = player => showReceptionNavigation({ ActionFormData: h.Form, player,
    gameId: "sparrow", title: definition.title, joined: true });
  h.emit("block", h.block("menu")); await h.drain();
  const shown = h.forms.shift();
  h.setMembership({ id: "sparrow", gameId: "sparrow", token: 8, players: ["first"] });
  shown.resolve({ selection: shown.form.buttons.indexOf("ゲームをリセット（全員同意）") }); await flush();
  assert.deepEqual(h.calls, []);
  const legacy = h.block("menu"); legacy.block.location.x += 2;
  h.emit("block", legacy); assert.equal(legacy.cancel, false);
});
console.log(`PASS ${checks} service UI checks (offline; ordinary mobile client not exercised)`);
