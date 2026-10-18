import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { WARP_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_warp_generated.js";
import {
  advanceAuction, advanceFire, advanceGrail, advanceLight, advanceMine, advancePig,
  auctionGame, createAuction, createFire, createGrail, createLight, createMine, createPig,
  fireGame, grailGame, lightGame, mineGame, pigGame, STRATEGY_GAMES,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/strategy_games.js";
import {
  advanceClock, advanceGarden, advanceHope, advanceRabbit, advanceWalker,
  clockGame, createClock, createGarden, createHope, createRabbit, createWalker,
  gardenGame, hopeGame, rabbitGame, walkerGame, BOARD_GAMES,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/board_games.js";
import { aceGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/ace.js";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";
import { gameRules } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/complete_runtime.js";

const catalog = JSON.parse(readFileSync(
  new URL("../scripts/minecraft/junket_games.json", import.meta.url), "utf8"));
assert.equal(catalog.games.length, 17);
assert.equal(WARP_CONFIG.WARP_CONTROLS.length, 17);
assert.ok(catalog.games.every(game =>
  ["verified", "implemented"].includes(game.readiness)));
assert.ok(WARP_CONFIG.WARP_CONTROLS.every(game => game.enabled));
assert.deepEqual(
  [...STRATEGY_GAMES, ...BOARD_GAMES].map(game => game.id),
  ["grail", "auction", "fire", "pig", "light", "mine",
    "hope", "clock", "walker", "garden", "rabbit"],
);
for (const game of [aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES]) {
  assert.ok(gameRules(game.id).length > 20, `${game.id} needs a visible rule summary`);
}

{
  const manager = new GameManager([aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES]);
  manager.join("hope-test", "hope", "p1");
  assert.equal(manager.view("hope-test").state, null);
  manager.join("hope-test", "hope", "p2");
  assert.equal(manager.start("hope-test").state.playerCount, 2);
  assert.throws(() => manager.join("hope-test", "hope", "late"), /参加できません/);
}

{
  let state = createGrail();
  assert.equal(grailGame.view(state).arrangements, undefined, "secret arrangement must not be in view");
  for (let round = 1; round <= 10; round++) {
    state = advanceGrail(state, state.actor, "arrange", { trueIndex: 0, holyIndex: 1 });
    state = advanceGrail(state, state.actor, "arrange", { trueIndex: 0, holyIndex: 0 });
    state = advanceGrail(state, state.grailSide, "cup", 0);
    state = advanceGrail(state, 1 - state.grailSide, "potion", 0);
  }
  assert.equal(state.phase, "finished");
  assert.equal(state.reason, "holy_volume");
  assert.equal(state.holy.reduce((sum, value) => sum + value, 0), 1000);
}

{
  let state = createAuction();
  const bidRound = (values) => {
    for (let seat = 0; seat < 4; seat++) state = advanceAuction(state, seat, "bid", values[seat]);
  };
  for (let seat = 0; seat < 4; seat++) {
    state = advanceAuction(state, seat, "bid", [4, 3, 2, 1][seat]);
    assert.deepEqual(auctionGame.view(state).bids, [null, null, null, null],
      "auction bids stay hidden until every player has placed one");
    if (seat < 3) assert.equal(state.phase, "bid");
  }
  state = createAuction();
  for (let set = 1; set <= 3; set++) {
    bidRound([4, 3, 2, 1]);
    assert.equal(state.phase, "bid");
    bidRound([3, 4, 4, 4]);
    if (set < 3) {
      assert.equal(state.phase, "set_result");
      state = auctionGame.systemAdvance(state, "next_set");
    }
  }
  assert.equal(state.phase, "finished");
  assert.equal(state.winnerTeam, 0);
  assert.equal(state.diamonds[0], 3);
  assert.equal(auctionGame.view(state).lastWinner, 0);
}

{
  let state = createFire();
  for (let index = 0; index < 3; index++) {
    state = advanceFire(state, 0, "choice", { choice: "rock", ticks: 100 });
    state = advanceFire(state, 1, "choice", { choice: "scissors", ticks: 120 });
  }
  assert.equal(state.phase, "finished");
  assert.equal(state.winner, 0);
  assert.deepEqual(state.wins, [3, 0]);
  assert.throws(() => createFire() && fireGame.advance(state, 0, "choice", { choice: "rock" }), /終了/);
}

{
  let state = createPig();
  state = advancePig(state, 0, "choose", { card: 4, target: 1 });
  assert.deepEqual(pigGame.view(state).choices, [null, null, null, null],
    "pig card selections stay hidden until the round resolves");
  state = createPig();
  for (let round = 0; round < 4 && state.phase !== "finished"; round++) {
    const cards = [4, 3, 2, 1];
    for (let seat = 0; seat < 4; seat++) {
      state = advancePig(state, seat, "choose", { card: cards[seat], target: (seat + 1) % 4 });
    }
  }
  assert.equal(state.phase, "finished");
  assert.equal(state.reason, "palace");
  assert.equal(state.last.cards.length, 4);
}

{
  let state = createLight();
  const setup = {
    guards: [0, 1, 2],
    tourists: [0, 1, 2],
  };
  while (state.phase !== "finished") {
    state = advanceLight(state, 0, "arrange", setup);
    state = advanceLight(state, 1, "arrange", setup);
    assert.deepEqual(lightGame.view(state).selections, [null, null]);
    state = advanceLight(state, 0, "select", { guard: 1, tourist: 0 });
    state = advanceLight(state, 1, "select", { guard: 1, tourist: 0 });
  }
  assert.equal(state.reason, "hearts");
  assert.deepEqual(state.hearts, [8, 8]);
  assert.equal(lightGame.view(state).arrangements, undefined, "guard identities stay hidden");
}

{
  let state = createMine();
  while (state.phase !== "finished") {
    state = advanceMine(state, 0, "arrange", { cards: [0, 1, 2, 3] });
    state = advanceMine(state, 1, "arrange", { cards: [0, 1, 2, 3] });
    state = advanceMine(state, 0, "mine", { indices: [2, 3], ticks: 100 });
    state = advanceMine(state, 1, "mine", { indices: [2, 3], ticks: 200 });
  }
  assert.ok(["poison", "ingots"].includes(state.reason));
  assert.equal(mineGame.view(state).boards, undefined, "rock layouts stay hidden");
}

{
  let state = createHope(3);
  state = advanceHope(state, 0, "floor", 1);
  assert.deepEqual(hopeGame.view(state).choices, [null, null, null],
    "hope floor choices stay hidden until every survivor chooses");
  state = createHope(3);
  for (let round = 0; round < 10; round++) {
    if (state.phase === "finished") break;
    const visible = hopeGame.view(state);
    assert.equal(visible.safe, null, "hazard and safe floor are hidden before resolution");
    assert.deepEqual(visible.hazards, []);
    while (state.phase === "choose") state = advanceHope(state, state.actor, "floor", state.safe);
  }
  assert.equal(state.phase, "finished");
  assert.equal(state.winners.length, 3);
  assert.equal(state.reason, "survived");
  assert.ok(hopeGame.view(state).hazards.length > 0);
}

{
  let state = createClock();
  state = advanceClock(state, 0, "set", 0);
  state = advanceClock(state, 1, "set", 2);
  state = advanceClock(state, 0, "attack", "short");
  assert.deepEqual(clockGame.view(state).attacks, [null, null],
    "clock attacks stay hidden until both players choose");
  state = createClock();
  for (let round = 0; round < 8 && state.phase !== "finished"; round++) {
    state = advanceClock(state, 0, "set", 0);
    state = advanceClock(state, 1, "set", 2);
    state = advanceClock(state, 0, "attack", "short");
    state = advanceClock(state, 1, "attack", "short");
    if (state.phase === "finished") break;
  }
  assert.ok(["finished"].includes(state.phase));
  assert.ok(state.hp.some(value => value < 60000));
  assert.equal(clockGame.view(state).last.results.length, 2);
}

{
  let state = createWalker();
  while (state.phase !== "finished") {
    const walker = state.walker;
    const from = state.positions[walker];
    const target = from < 36 ? from + 1 : from - 1;
    state = advanceWalker(state, walker, "walk", target);
    state = advanceWalker(state, state.breaker, "bomb", 36);
  }
  assert.equal(state.reason, "survival");
  assert.equal(state.round, 10);
}

{
  let state = createGarden();
  state = advanceGarden(state, 0, "choose", "switch");
  assert.deepEqual(gardenGame.view(state).choices, [null, null],
    "garden actions stay hidden until both players choose");
  state = createGarden();
  while (state.phase !== "finished") {
    state = advanceGarden(state, 0, "choose", "switch");
    state = advanceGarden(state, 1, "choose", "double");
  }
  assert.equal(state.reason, "fall_order");
  assert.equal(state.loads[0], 0);
}

{
  let state = createRabbit();
  state = advanceRabbit(state, 1, "trap", 26);
  assert.equal(state.phase, "move");
  assert.deepEqual(rabbitGame.view(state).traps, [],
    "the escaper cannot inspect the hidden trap before choosing");
  state = advanceRabbit(state, 0, "move", 23);
  assert.equal(state.phase, "trap");
  assert.equal(state.last, null);
  assert.ok(rabbitGame.view(state).traps.length === 0);
}

console.log("PASS ALL17_IMPLEMENTED: catalog, shared manager, 11 new game rules, hidden choices and terminal conditions");
