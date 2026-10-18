import assert from "node:assert/strict";
import {
  JINN_HUNGER_TICKS, JINN_INPUT_TICKS, JINN_PAY_PER_TICK, JINN_ROUNDS_PER_GAME,
  advanceJinn, createJinn, jinnGame, jinnSystem, jinnView,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/jinn.js";

assert.equal(JINN_INPUT_TICKS, 6000);
assert.equal(JINN_ROUNDS_PER_GAME, 6);
assert.equal(JINN_PAY_PER_TICK, 175);
assert.equal(JINN_HUNGER_TICKS, 3600);
assert.equal(jinnGame.seats, 2);

const play = (state, first, second) => {
  state = advanceJinn(state, 0, 'choose', first);
  assert.equal(state.phase, 'cards');
  state = advanceJinn(state, 1, 'choose', second);
  assert.equal(state.phase, 'reveal');
  return state;
};

{
  let state = createJinn();
  state.coins = [10, 20];
  state = play(state, 'gold', 'gold');
  assert.deepEqual(state.coins, [40, 80]);
}
{
  let state = createJinn();
  state.coins = [10, 20];
  state = play(state, 'gold', 'thief');
  assert.deepEqual(state.coins, [20, 40]);
}
{
  let state = createJinn();
  state.coins = [10, 20];
  state = play(state, 'gold', 'demon');
  assert.deepEqual(state.coins, [40, 20]);
}
{
  let state = createJinn();
  state.coins = [10, 20];
  state = play(state, 'thief', 'gold');
  assert.deepEqual(state.coins, [50, 40]);
}
{
  let state = createJinn();
  state.coins = [10, 20];
  state = play(state, 'thief', 'thief');
  assert.deepEqual(state.coins, [10, 20]);
}
{
  let state = createJinn();
  state.coins = [10, 20];
  state = play(state, 'thief', 'demon');
  assert.deepEqual(state.coins, [1, 29]);
}
{
  let state = createJinn();
  state.coins = [10, 20];
  state = play(state, 'demon', 'gold');
  assert.deepEqual(state.coins, [10, 80]);
}
{
  let state = createJinn();
  state.coins = [10, 20];
  state = play(state, 'demon', 'thief');
  assert.deepEqual(state.coins, [28, 2]);
}
{
  let state = createJinn();
  state.coins = [10, 20];
  state = play(state, 'demon', 'demon');
  assert.deepEqual(state.coins, [10, 20]);
}

{
  let state = createJinn();
  state = advanceJinn(state, 0, 'choose', 'gold');
  const view = jinnView(state);
  assert.deepEqual(view.chosen, [true, false]);
  assert.equal(view.last, null);
  assert.throws(() => advanceJinn(state, 0, 'choose', 'thief'), /選択済み/);
  state = advanceJinn(state, 1, 'choose', 'thief');
  assert.equal(state.phase, 'reveal');
  assert.equal(state.hands[0].gold, 3);
  assert.equal(state.hands[1].thief, 0);
  assert.equal(state.last.choices[0], 'gold');
}

{
  let state = createJinn();
  const batchCards = [
    ['gold', 'thief'], ['gold', 'demon'], ['thief', 'gold'],
    ['demon', 'gold'], ['gold', 'gold'], ['gold', 'gold'],
  ];
  for (const [first, second] of batchCards) {
    state = play(state, first, second);
    state = advanceJinn(state, 0, 'reveal', undefined);
  }
  assert.equal(state.phase, 'settlement');
  assert.deepEqual(state.payment, state.coins);
  assert.deepEqual(state.lever, [false, false]);
}

{
  let state = {
    ...createJinn(),
    phase: 'settlement',
    game: 1,
    payment: [175, 175],
    lever: [true, true],
    hungerClock: 0,
  };
  state = jinnSystem(state, 'tick');
  assert.equal(state.phase, 'cards');
  assert.equal(state.game, 2);
  assert.equal(state.round, 1);
  assert.deepEqual(state.hands, [
    { gold: 4, thief: 1, demon: 1 },
    { gold: 4, thief: 1, demon: 1 },
  ]);
}

{
  const oneLever = jinnSystem({
    ...createJinn(), phase: 'settlement', lever: [true, false],
  }, 'deadline');
  assert.equal(oneLever.phase, 'finished');
  assert.equal(oneLever.winner, 0);
  assert.equal(oneLever.reason, 'timeout');
  const bothLevers = jinnSystem({
    ...createJinn(), phase: 'settlement', lever: [true, true],
  }, 'deadline');
  assert.equal(bothLevers.phase, 'settlement');
}

{
  let state = {
    ...createJinn(),
    phase: 'settlement',
    game: 2,
    payment: [1000, 1000],
    lever: [true, true],
    hungerClock: JINN_HUNGER_TICKS,
    food: [20, 20],
  };
  state = jinnSystem(state, 'tick');
  assert.deepEqual(state.food, [19, 19]);
  state = jinnSystem({ ...state, food: [0, 1], hp: [1000, 1000] }, 'tick');
  assert.deepEqual(state.hp, [800, 1000]);
}

{
  let state = {
    ...createJinn(),
    phase: 'settlement',
    game: 3,
    coins: [10, 5],
    payment: [0, 0],
    lever: [true, true],
  };
  state = jinnSystem(state, 'tick');
  assert.equal(state.phase, 'finished');
  assert.equal(state.winner, 0);
  assert.equal(state.reason, 'coins');
}

{
  let state = advanceJinn(createJinn(), 0, 'choose', 'gold');
  state = jinnSystem(state, 'timeout');
  assert.equal(state.winner, 0);
  assert.equal(state.reason, 'timeout');
  const surrendered = advanceJinn({
    ...createJinn(), phase: 'settlement',
  }, 1, 'surrender', undefined);
  assert.equal(surrendered.phase, 'finished');
  assert.equal(surrendered.winner, 0);
  assert.equal(surrendered.reason, 'surrender');
}

console.log("PASS Jinn 9-card matrix, simultaneous secrecy, six-round batches, payment, hunger, surrender and final coin winner");
