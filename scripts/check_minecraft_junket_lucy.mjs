import assert from "node:assert/strict";
import {
  LUCY_COLUMNS, LUCY_MAX_HP, LUCY_MISS_DAMAGE, LUCY_ROWS, LUCY_TURN_TICKS,
  advanceLucy, createLucy, lucyGame, lucySystem, lucyView,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/lucy.js";

const row = cells => Array.from({ length: LUCY_COLUMNS }, (_, index) => cells.includes(index));
const attempt = (state, seat, r, c) => advanceLucy(state, seat, "attempt", { row: r, column: c });

assert.equal(LUCY_ROWS, 5);
assert.equal(LUCY_COLUMNS, 5);
assert.equal(LUCY_TURN_TICKS, 6000);
assert.equal(LUCY_MAX_HP, 60000);
assert.equal(LUCY_MISS_DAMAGE, 3000);
assert.equal(lucyGame.seats, 2);

{
  let state = createLucy();
  assert.equal(state.phase, "lock");
  assert.equal(state.actor, null);
  assert.throws(() => advanceLucy(state, 0, "lock", row([])), /最低一つ/);
  assert.throws(() => advanceLucy(state, 0, "lock", [true]), /列の指定/);
  for (let index = 0; index < LUCY_ROWS; index++) {
    state = advanceLucy(state, 1, "lock", row([index]));
    assert.equal(state.patterns[0][index][index], true);
    assert.equal(state.progress[0], index + 1);
    state = advanceLucy(state, 0, "lock", row([(index + 2) % LUCY_COLUMNS]));
    assert.equal(state.patterns[1][index][(index + 2) % LUCY_COLUMNS], true);
    assert.equal(state.progress[1], index + 1);
  }
  assert.equal(state.phase, "attempt");
  assert.equal(state.actor, 0);
  assert.deepEqual(state.progress, [5, 5]);
  const view = lucyView(state);
  assert.equal("patterns" in view, false);
  assert.ok(view.attempted.flat(2).every(cell => cell === 0));
}

{
  let state = createLucy();
  state = advanceLucy(state, 1, "lock-toggle", { row: 0, column: 2 });
  assert.equal(state.lockDrafts[0][0][2], true);
  assert.equal(state.patterns[0][0][2], false);
  assert.equal(state.progress[0], 0);
  state = advanceLucy(state, 1, "lock-toggle", { row: 0, column: 2 });
  assert.equal(state.lockDrafts[0][0][2], false);
  state = advanceLucy(state, 1, "lock-toggle", { row: 0, column: 1 });
  assert.throws(() => advanceLucy(state, 1, "lock-toggle", { row: 1, column: 1 }), /次の面/);
  state = advanceLucy(state, 1, "lock-confirm", { row: 0 });
  assert.equal(state.progress[0], 1);
  assert.equal(state.patterns[0][0][1], true);
  assert.equal(state.lockDrafts[0][0][1], false);
  assert.throws(() => advanceLucy(state, 1, "lock-confirm", { row: 0 }), /次の面/);

  state = advanceLucy(state, 0, "lock-toggle", { row: 0, column: 4 });
  assert.equal(state.lockDrafts[1][0][4], true);
  assert.equal(state.patterns[1][0][4], false);
  assert.throws(() => advanceLucy(state, 1, "lock-confirm", { row: 1 }), /最低一つ/);
}

{
  let state = createLucy();
  state.patterns = state.patterns.map(pattern => pattern.map(line => [...line]));
  state.patterns[0][0][0] = true;
  state.patterns[1][0][0] = false;
  state = { ...state, phase: "attempt", actor: 1 };
  state = attempt(state, 1, 0, 0);
  assert.equal(state.last.hit, false);
  assert.equal(state.last.seat, 1);
}

{
  let state = createLucy();
  state = { ...state, phase: "attempt", actor: 0, remaining: [1, 5] };
  state.patterns = state.patterns.map(pattern => pattern.map(line => [...line]));
  state.patterns[0][4][4] = true;
  assert.throws(() => attempt(state, 0, 2, 4), /現在開錠/);
  state = attempt(state, 0, 4, 4);
  assert.equal(state.phase, "finished");
  assert.equal(state.winner, 0);
  assert.equal(state.reason, "vault");
  assert.equal(state.last.hit, true);
  assert.equal(state.remaining[0], 0);
}

{
  let state = createLucy();
  state = { ...state, phase: "attempt", actor: 0 };
  state = attempt(state, 0, 0, 0);
  assert.equal(state.phase, "recover");
  assert.equal(state.last.hit, false);
  assert.deepEqual(state.hp, [60000, 60000]);
  assert.throws(() => attempt(state, 0, 0, 1), /手番/);
  state = advanceLucy(state, 0, "recover", undefined);
  assert.deepEqual(state.hp, [57000, 60000]);
  assert.equal(state.actor, 1);
  assert.equal(state.phase, "attempt");
}

{
  let state = createLucy();
  state = {
    ...state,
    phase: "attempt",
    actor: 1,
    round: 3,
    remaining: [5, 4],
  };
  state.patterns = state.patterns.map(pattern => pattern.map(line => [...line]));
  state.patterns[1][1][1] = true;
  state = attempt(state, 1, 1, 1);
  assert.equal(state.phase, "success");
  state = advanceLucy(state, 1, "ack", undefined);
  assert.equal(state.actor, 0);
  assert.equal(state.round, 4);
}

{
  let state = createLucy();
  state = { ...state, phase: "attempt", actor: 0, hp: [2000, 60000] };
  state = attempt(state, 0, 0, 0);
  state = advanceLucy(state, 0, "recover", undefined);
  assert.equal(state.phase, "finished");
  assert.equal(state.winner, 1);
  assert.equal(state.reason, "health");
}

{
  const locked = lucySystem({ ...createLucy(), progress: [5, 4] }, "timeout");
  assert.equal(locked.winner, 1);
  assert.equal(locked.reason, "timeout");
  const choosing = lucySystem({ ...createLucy(), phase: "attempt", actor: 0 }, "timeout");
  assert.equal(choosing.winner, 1);
  const tied = lucySystem(createLucy(), "timeout");
  assert.equal(tied.winner, null);
  assert.throws(() => lucySystem(createLucy(), "other"), /不正/);
}

console.log("PASS Lucy 5x5 secret patterns, row completion, key attempts, recovery damage, vault/health/timeout endings");
