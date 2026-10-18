// Offline UI-contract rehearsal. This does not render a Bedrock client form.
import assert from "node:assert/strict";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";
import { createCompleteRuntime } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/complete_runtime.js";
import { aceGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/ace.js";
import { STRATEGY_GAMES } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/strategy_games.js";
import { BOARD_GAMES } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/board_games.js";

const flush = async () => {
  for (let index = 0; index < 4; index++) await new Promise(resolve => setImmediate(resolve));
};

function player(id) {
  return {
    id, name: id, typeId: "minecraft:player", messages: [],
    sendMessage(message) { this.messages.push(message); },
    onScreenDisplay: { setActionBar() {} },
  };
}

class Form {
  title() { return this; }
  body(value) { this.text = value; return this; }
  button() { return this; }
  dropdown() { return this; }
  textField() { return this; }
  toggle() { return this; }
  show(playerValue) {
    return new Promise(resolve => this.harness.forms.push({ player: playerValue, form: this, resolve }));
  }
}

function harness(game) {
  const events = {}, forms = [], timers = [], players = [];
  const signal = name => ({ subscribe(callback) { events[name] = callback; } });
  const world = {
    getAllPlayers: () => players,
    beforeEvents: { playerInteractWithEntity: signal("interact") },
    afterEvents: { playerLeave: signal("leave") },
  };
  const system = {
    run(callback) { callback(); },
    runTimeout(callback) { timers.push(callback); return timers.length; },
    runInterval(callback) { timers.push(callback); return timers.length; },
    clearRun() {},
    afterEvents: { scriptEventReceive: signal("script") },
  };
  const manager = new GameManager([aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES]);
  class HarnessForm extends Form {
    constructor() { super(); this.harness = { forms }; }
  }
  const runtime = createCompleteRuntime({
    game, world, system, manager,
    ActionFormData: HarnessForm, ModalFormData: HarnessForm,
  });
  return { game, events, forms, timers, players, manager, runtime };
}

async function join(test, value) {
  test.players.push(value);
  const event = {
    player: value, cancel: false,
    target: { hasTag: tag => tag === `junket:${test.game.id}:reception` },
  };
  test.events.interact(event);
  assert.equal(event.cancel, true);
  await flush();
  const index = test.forms.findIndex(entry => entry.player === value);
  assert.notEqual(index, -1, `${test.game.id}: join form for ${value.id}`);
  test.forms.splice(index, 1)[0].resolve({ selection: 0 });
  await flush();
}

const actorForms = new Map();
const cases = [
  [aceGame, 4],
  [STRATEGY_GAMES.find(game => game.id === "grail"), 2],
  [STRATEGY_GAMES.find(game => game.id === "auction"), 4],
  [STRATEGY_GAMES.find(game => game.id === "fire"), 2],
  [STRATEGY_GAMES.find(game => game.id === "pig"), 4],
  [STRATEGY_GAMES.find(game => game.id === "light"), 2],
  [STRATEGY_GAMES.find(game => game.id === "mine"), 2],
  [BOARD_GAMES.find(game => game.id === "hope"), 16],
  [BOARD_GAMES.find(game => game.id === "clock"), 2],
  [BOARD_GAMES.find(game => game.id === "walker"), 2],
  [BOARD_GAMES.find(game => game.id === "garden"), 2],
  [BOARD_GAMES.find(game => game.id === "rabbit"), 2],
];

for (const [game, count] of cases) {
  assert.ok(game, "missing runtime game");
  const test = harness(game);
  for (let index = 0; index < count; index++) {
    await join(test, player(`p${index + 1}`));
  }
  if (game.id === "hope") {
    const late = player("p17");
    test.players.push(late);
    test.events.interact({
      player: late, cancel: false,
      target: { hasTag: tag => tag === "junket:hope:reception" },
    });
    await flush();
    const lateIndex = test.forms.findIndex(entry => entry.player === late);
    assert.notEqual(lateIndex, -1, "hope: full table should return a form");
    test.forms.splice(lateIndex, 1)[0].resolve({ selection: 0 });
    await flush();
    assert.equal(test.manager.forPlayer(late.id), null, "hope: a 17th player must be rejected");
  }
  if (game.registration) {
    const registration = test.timers.shift();
    assert.equal(typeof registration, "function", `${game.id}: registration timer`);
    registration();
    await flush();
  }
  const joined = test.manager.forPlayer("p1");
  assert.ok(joined?.state, `${game.id}: game starts after required joins`);
  if (game.id === "hope") assert.equal(joined.players.length, 16);
  const actorId = joined.players[joined.state.actor];
  const actorForm = test.forms.find(entry => entry.player.id === actorId);
  assert.ok(actorForm, `${game.id}: first actor receives an input form`);
  actorForms.set(game.id, { test, actorForm, actorId });
}

{
  const { test: rabbit, actorForm: oniForm, actorId: oniId } = actorForms.get("rabbit");
  rabbit.forms.splice(rabbit.forms.indexOf(oniForm), 1)[0].resolve({ formValues: ["26"] });
  await flush();
  const escaperForm = rabbit.forms.find(entry => entry.player.id !== oniId);
  assert.ok(escaperForm, "rabbit: escaper receives the move form after the trap");
  assert.match(escaperForm.form.text, /罠 非公開/);
  assert.doesNotMatch(escaperForm.form.text, /罠 26/);
}

console.log("PASS 12 shared runtime flows: reception click, join, start/timer, 16-player Hope and first-actor UI");
