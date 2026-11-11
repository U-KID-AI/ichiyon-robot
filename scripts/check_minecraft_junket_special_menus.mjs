import assert from "node:assert/strict";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";
import { lucyGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/lucy.js";
import { silenceGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/silence.js";
import { jinnGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/jinn.js";
import { temperanceGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/temperance.js";
import { createLucyRuntime } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/lucy_runtime.js";
import { createSilenceRuntime } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/silence_runtime.js";
import { createJinnRuntime } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/jinn_runtime.js";
import { createTemperanceRuntime } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/temperance_runtime.js";
import { runSpecialRuleTraces } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/rules_special_probe.js";

function harness(game, factory) {
  let now = 0, nextId = 1;
  const tasks = new Map(), forms = [], messages = [];
  const system = {
    get currentTick() { return now; },
    runTimeout(callback, ticks = 1) { const id = nextId++; tasks.set(id, { callback, at: now + ticks }); return id; },
    runInterval(callback, ticks = 1) { const id = nextId++; tasks.set(id, { callback, at: now + ticks, period: ticks }); return id; },
    clearRun: id => tasks.delete(id),
    afterEvents: { scriptEventReceive: { subscribe() {} } },
  };
  system.run = fn => system.runTimeout(fn, 1);
  const players = [0, 1].map(index => ({ id: `p${index}`, name: `P${index + 1}`,
    sendMessage: text => messages.push({ id: `p${index}`, text }),
    onScreenDisplay: { setTitle() {}, setActionBar() {} }, playSound() {} }));
  const signal = { subscribe() {} };
  const world = { getAllPlayers: () => players, getDynamicProperty: () => undefined,
    setDynamicProperty() {}, getDimension: () => ({ getEntities: () => [] }),
    beforeEvents: { playerInteractWithEntity: signal }, afterEvents: { playerLeave: signal } };
  class Form {
    constructor() { this.labels = []; this.heading = ""; this.description = ""; }
    title(value) { this.heading = value; return this; }
    body(value) { this.description = value; return this; }
    button(value) { this.labels.push(value); return this; }
    toggle(value) { this.labels.push(value); return this; }
    dropdown(value) { this.labels.push(value); return this; }
    show(player) { return new Promise(resolve => forms.push({ player: player.id, form: this, resolve, answered: false })); }
  }
  const manager = new GameManager([game]);
  const runtime = factory({ world, system, ActionFormData: Form, ModalFormData: Form,
    manager, installer: { installing: () => false } });
  for (const name of ["start", "update", "end", "ensure", "verify", "selectionPositions", "playbackPositions"]) runtime.venue[name] = () => {};
  runtime.venue.isInsideSoundRoom = () => false;
  manager.join(game.id, game.id, "p0"); manager.join(game.id, game.id, "p1");
  runtime.resume();
  async function advance(ticks = 5) {
    for (let i = 0; i < ticks; i++) {
      now++;
      for (const [id, task] of [...tasks]) if (task.at <= now && tasks.has(id)) {
        if (task.period) task.at = now + task.period; else tasks.delete(id);
        task.callback();
      }
      await new Promise(resolve => setImmediate(resolve));
    }
  }
  function answer(player, response) {
    const entry = forms.find(value => value.player === player && !value.answered);
    assert.ok(entry, `${game.id} has pending form for ${player}; notices=${JSON.stringify(messages)}`);
    entry.answered = true; entry.resolve(response); return entry;
  }
  return { manager, runtime, forms, messages, players, advance, answer,
    live: () => manager.view(game.id), pending: () => forms.filter(value => !value.answered) };
}

{
  const h = harness(lucyGame, createLucyRuntime); await h.advance();
  assert.deepEqual(h.pending().map(x => x.player).sort(), ["p0", "p1"]);
  // Both forms were opened from the same state. Either response order must work.
  h.answer("p1", { formValues: [true, false, false, false, false] }); await h.advance();
  h.answer("p0", { formValues: [false, true, false, false, false] }); await h.advance();
  assert.deepEqual(h.live().state.progress, [1, 1]);
  assert.deepEqual(h.pending().map(x => x.player).sort(), ["p0", "p1"]);
  h.answer("p0", { canceled: true }); h.answer("p1", { canceled: true });
  const count = h.forms.length; await h.advance(50);
  assert.equal(h.forms.length, count, "cancel does not reopen forever");
  await h.runtime.resetSession(h.live());
}
{
  const h = harness(silenceGame, createSilenceRuntime); await h.advance();
  assert.equal(h.pending()[0].player, "p1");
  h.answer("p1", { formValues: [0, 1, 2] }); await h.advance();
  assert.equal(h.live().state.phase, "choose");
  h.answer("p0", { selection: 0 }); await h.advance();
  assert.equal(h.live().state.phase, "set");
  assert.match(h.pending()[0].form.heading, /設置/);
  h.answer("p0", { selection: 0 }); await h.advance();
  assert.equal(h.live().state.phase, "return", "nested busy flag must not swallow set action");
  h.answer("p0", { selection: 0 }); await h.advance();
  assert.equal(h.live().state.phase, "arrange");
  assert.equal(h.pending()[0].player, "p0");
  await h.runtime.resetSession(h.live());
}
{
  const h = harness(jinnGame, createJinnRuntime); await h.advance();
  assert.equal(h.pending().length, 2);
  h.answer("p1", { selection: 1 }); await h.advance();
  assert.deepEqual(h.live().state.chosen, [false, true]);
  h.answer("p0", { selection: 0 }); await h.advance();
  assert.equal(h.live().state.phase, "reveal");
  assert.deepEqual(h.live().state.last.choices, ["gold", "thief"]);
  await h.advance(45);
  assert.equal(h.live().state.round, 2);
  assert.equal(h.pending().length, 2);
  await h.runtime.resetSession(h.live());
}
{
  const h = harness(temperanceGame, createTemperanceRuntime); await h.advance();
  assert.equal(h.pending()[0].player, "p1");
  assert.match(h.pending()[0].form.heading, /3枚/);
  h.answer("p1", { formValues: [0, 2, 5] }); await h.advance();
  assert.equal(h.pending()[0].player, "p0");
  h.answer("p0", { selection: 0 }); await h.advance();
  assert.equal(h.live().state.phase, "choose");
  h.answer("p0", { selection: 0 }); await h.advance(65);
  assert.equal(h.live().state.round, 2);
  assert.equal(h.pending()[0].player, "p0");
  await h.runtime.resetSession(h.live());
}
for (const [game, factory] of [[lucyGame, createLucyRuntime], [silenceGame, createSilenceRuntime],
  [jinnGame, createJinnRuntime], [temperanceGame, createTemperanceRuntime]]) {
  const h = harness(game, factory); await h.advance();
  h.players.pop();
  // Restart/load recovery has no playerLeave event to install its grace timer.
  h.runtime.resume();
  await h.advance(205);
  assert.equal(h.live(), null, `${game.id}: offline restored player cannot hang the game forever`);
}
const traces = runSpecialRuleTraces();
assert.equal(traces.length, 6);
assert.ok(traces.every(result => result.phase === "finished" && result.client_play === false));
console.log(JSON.stringify({ status: "PASS", kind: "automated forms/reducers; not PC/mobile client play", traces }));
