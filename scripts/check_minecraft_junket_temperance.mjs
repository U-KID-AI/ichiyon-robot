import assert from "node:assert/strict";
import {
  TEMPERANCE_TURN_TICKS, advanceTemperance, cardAmount, createTemperance,
  temperanceSystem, temperanceView,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/temperance.js";

const arrange = (state, seat, cards) => advanceTemperance(state, seat, "arrange", cards);
const choose = (state, seat, value) => advanceTemperance(state, seat, "choose", value);

assert.equal(TEMPERANCE_TURN_TICKS, 6000);
assert.equal(cardAmount(1, 7), 1);
assert.equal(cardAmount(-1, 7), -1);
assert.equal(cardAmount(2, 7), 10);
assert.equal(cardAmount(-2, 7), -10);
assert.equal(cardAmount(3, 7), 14);
assert.equal(cardAmount(-3, 7), -14);

{
  const initial = createTemperance();
  assert.equal(initial.phase, "arrange");
  assert.equal(initial.arranger, 1);
  assert.equal(initial.actor, 1);
  for (const invalid of [
    [1, 1, 2],
    [1, 2, 3],
    [-1, -2, -3],
    [1, -2],
    [1, -2, 4],
  ]) {
    assert.throws(() => arrange(initial, 1, invalid), /正位置と逆位置/);
  }
  assert.throws(() => arrange(initial, 0, [1, -2, 3]), /手番/);
  assert.deepEqual(initial, createTemperance());
}

{
  let state = createTemperance();
  state = arrange(state, 1, [1, 2, -3]);
  assert.equal(state.phase, "target");
  assert.equal(state.actor, 0);
  state = advanceTemperance(state, 0, "target", 1);
  assert.equal(state.phase, "choose");
  state = choose(state, 0, 1);
  assert.deepEqual(state.stones, [0, 10]);
  assert.equal(state.delta, -10);
  // The physical balance moves after the second chooser, matching ver1.09.
  assert.deepEqual(state.hp, [60000, 60000]);
  assert.equal(state.phase, "arrange");
  assert.equal(state.round, 2);
  assert.equal(state.arranger, 0);
  assert.equal(state.actor, 0);
  assert.equal(state.last.amount, 10);
  assert.equal(state.last.target, 1);
}

{
  const view = temperanceView({
    ...createTemperance(), phase: "choose", cards: [1, -2, 3], target: 0,
  });
  assert.equal("cards" in view, false);
  assert.equal("target" in view, false);
}

{
  let state = createTemperance();
  state = { ...state, stones: [20, 0], delta: 20, balance: 20 };
  state = arrange(state, 1, [1, -2, 3]);
  state = advanceTemperance(state, 0, "target", 1);
  state = choose(state, 0, 2);
  assert.deepEqual(state.stones, [20, 2]);
  assert.equal(state.delta, 18);
  assert.ok(state.hp[0] < 60000);
  assert.ok(state.hp[1] < 60000);
}

{
  let state = { ...createTemperance(), balance: 101 };
  const before = state.hp[0];
  const once = temperanceSystem(state, "air");
  assert.equal(once.hp[0], before - 10000);
  const twice = temperanceSystem(once, "air");
  assert.equal(twice.hp[0], before - 20000);
  assert.throws(() => temperanceSystem(twice, "invalid"), /不正/);
}

{
  const state = createTemperance();
  const timeout = temperanceSystem(state, "timeout");
  assert.equal(timeout.phase, "finished");
  assert.equal(timeout.winner, 0);
  assert.equal(timeout.reason, "timeout");
  assert.throws(() => arrange(timeout, 1, [1, -2, 3]), /手番|試合/);
}

console.log("PASS Temperance source cadence, mixed cards, 2R scaling, hidden input, pressure, air trap, timeout and terminal guard");
