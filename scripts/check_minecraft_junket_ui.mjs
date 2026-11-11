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
  button(value) { (this.buttons ??= []).push(value); return this; }
  dropdown() { return this; }
  textField() { return this; }
  toggle() { return this; }
  show(playerValue) {
    return new Promise(resolve => this.harness.forms.push({ player: playerValue, form: this, resolve }));
  }
}

function harness(game, { venue = undefined } = {}) {
  const events = {}, forms = [], timers = [], intervals = [], players = [];
  const signal = name => ({ subscribe(callback) { events[name] = callback; } });
  const world = {
    getAllPlayers: () => players,
    beforeEvents: { playerInteractWithEntity: signal("interact") },
    afterEvents: { playerLeave: signal("leave") },
  };
  const system = {
    run(callback) { callback(); },
    runTimeout(callback) { timers.push(callback); return timers.length; },
    runInterval(callback) { intervals.push(callback); return intervals.length; },
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
    venue,
  });
  return { game, events, forms, timers, intervals, players, manager, runtime };
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

async function pressControl(test, playerValue, action) {
  const event = {
    player: playerValue, cancel: false,
    target: {
      hasTag: tag => tag === `junket:${test.game.id}:control`,
      getDynamicProperty: name => name === "junket:venue_action" ? action : undefined,
    },
  };
  test.events.interact(event);
  await flush();
  assert.equal(event.cancel, true, `${test.game.id}: ${action} must be handled as a venue control`);
}

const started = new Map();
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
    test.forms.splice(lateIndex, 1)[0].resolve({ canceled: true });
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
  assert.equal(actorForm, undefined,
    `${game.id}: turn input must use the physical venue controls, not a form`);
  const formCount = test.forms.length;
  test.runtime.resume(actorId);
  test.runtime.resume(actorId);
  assert.equal(test.forms.length, formCount,
    `${game.id}: repeated spawn/resume must not open a turn form`);
  started.set(game.id, {
    test, actorId, actor: test.players.find(value => value.id === actorId),
  });
  const participant = test.players[0];
  const pending = test.runtime.openReception(participant);
  await flush();
  const navigation = test.forms.findIndex(entry => entry.player === participant);
  assert.notEqual(navigation, -1, `${game.id}: explicit reception opens participant navigation`);
  const menu = test.forms.splice(navigation, 1)[0];
  assert.ok(menu.form.buttons.includes("説明"));
  assert.ok(menu.form.buttons.includes("退出"));
  menu.resolve({ canceled: true }); await pending;
  assert.equal(test.forms.length, formCount, `${game.id}: closing navigation never autoopens another turn form`);
}

{
  const hope = harness(BOARD_GAMES.find(game => game.id === "hope"));
  const member = player("early-start"), guest = player("not-joined");
  await join(hope, member);
  assert.equal(hope.manager.forPlayer(member.id).state, null);
  assert.throws(() => hope.runtime.startRegistration(guest), /開始できる参加受付/);
  hope.runtime.startRegistration(member);
  assert.ok(hope.manager.forPlayer(member.id).state);
  assert.throws(() => hope.runtime.startRegistration(member), /開始できる参加受付/);
}

{
  const { test: rabbit, actor: oni } = started.get("rabbit");
  for (const tile of ["26", "1", "13", "37", "49"]) {
    await pressControl(rabbit, oni, `floor_${tile}`);
  }
  const move = rabbit.manager.forPlayer(oni.id).state;
  assert.equal(move.phase, "move", "rabbit: five physical traps start the human move");
  assert.equal(move.actor, 0, "rabbit: the escaper takes the move turn");
  assert.equal(rabbit.forms.length, 0,
    "rabbit: trap placement must not open a form or notify the escaper");
  const escaper = rabbit.players.find(value => value.id !== oni.id);
  assert.equal(escaper.messages.some(message => /罠 26/.test(message)), false,
    "rabbit: the trap position must stay hidden from the escaper");
}

{
  const { test: ace, actorId, actor } = started.get("ace");
  const outsider = player("outsider");
  await testInteraction(ace, outsider, "ace_tray_1", { control: true }, "受付から");
  await testInteraction(ace, ace.players.find(value => value.id !== actorId), "ace_tray_2",
    { control: true }, "現在の手番");
  await testInteraction(ace, ace.players.find(value => value.id === actorId), "ace_tray_2",
    { control: true }, "自分の操作物");
  await testInteraction(ace, ace.players.find(value => value.id === actorId), "ace_tray_1",
    { control: true }, "この局面");
  const token = ace.manager.forPlayer(actorId).token;
  const formsBeforeDisplay = ace.forms.length;
  await testInteraction(ace, ace.players.find(value => value.id === actorId), "display",
    { display: true });
  assert.equal(ace.manager.forPlayer(actorId).token, token,
    "display navigation must not mutate game state");
  assert.equal(ace.forms.length, formsBeforeDisplay + 1, "display interaction opens readable status navigation");
  const displayMenu = ace.forms.pop();
  assert.deepEqual(displayMenu.form.buttons, ["ゲーム操作", "説明", "退出", "カタログへ戻る", "閉じる"]);
  displayMenu.resolve({ canceled: true });
  await flush();
  await pressControl(ace, actor, "ace_table_10_up");
  await pressControl(ace, actor, "ace_table_confirm");
  assert.equal(ace.manager.forPlayer(actorId).state.phase, "roll",
    "ace: the physical table-bet dial starts the first roll turn");
  assert.equal(ace.forms.length, formsBeforeDisplay,
    "ace: a physical turn must not open a replacement form");
}

{
  const garden = BOARD_GAMES.find(game => game.id === "garden");
  let landingPolls = 0;
  const updates = [];
  const venue = {
    start() {},
    update(id, current) { updates.push({ id, phase: current?.state?.phase }); },
    pollGardenLanding(current) {
      landingPolls += 1;
      assert.equal(current.state.phase, "drop");
      return 1;
    },
  };
  const test = harness(garden, { venue });
  await join(test, player("garden-p1"));
  await join(test, player("garden-p2"));
  assert.equal(test.manager.forPlayer("garden-p1").state.phase, "choose");
  test.manager.systemAct("garden", "timeout");
  assert.equal(test.manager.forPlayer("garden-p1").state.phase, "drop");
  assert.equal(test.manager.forPlayer("garden-p1").state.dropRemaining, 3);
  const formsBeforeLanding = test.forms.length;
  test.intervals[0]();
  await flush();
  assert.equal(landingPolls, 0,
    "the landing observer must stay inert while the source pre-drop delay runs");
  test.manager.systemAct("garden", "tick", 1);
  test.manager.systemAct("garden", "tick", 1);
  test.manager.systemAct("garden", "tick", 1);
  test.intervals[0]();
  await flush();
  const landed = test.manager.forPlayer("garden-p1").state;
  assert.equal(landingPolls, 1, "the one-tick observer must poll the physical drop area");
  assert.equal(landed.phase, "result", "brown-wool landing must advance without a form");
  assert.equal(landed.loser, 1);
  assert.equal(landed.winner, 0);
  assert.equal(test.forms.length, formsBeforeLanding,
    "a physical landing must not require a replacement form UI");
  assert.ok(updates.some(update => update.phase === "result"));
}

async function testInteraction(test, playerValue, action, roles, expected) {
  const tags = new Set();
  if (roles.control) tags.add(`junket:${test.game.id}:control`);
  if (roles.display) tags.add(`junket:${test.game.id}:display`);
  const event = {
    player: playerValue,
    cancel: false,
    target: {
      hasTag: tag => tags.has(tag),
      getDynamicProperty: name => name === "junket:venue_action" ? action : undefined,
    },
  };
  test.events.interact(event);
  await flush();
  assert.equal(event.cancel, true);
  if (expected) assert.ok(playerValue.messages.some(message => message.includes(expected)),
    `${test.game.id}: expected refusal containing ${expected}: ${playerValue.messages.join(" | ")}`);
}

console.log("PASS 12 shared runtime flows: reception click, join, start/timer, 16-player Hope and first-actor UI");
