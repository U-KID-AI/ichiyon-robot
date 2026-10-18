import assert from "node:assert/strict";
import {
  SILENCE_DURATIONS, SILENCE_MAX_HP, SILENCE_PLAY_TICKS, SILENCE_TICKS_PER_SECOND,
  SILENCE_TURN_TICKS, advanceSilence, createSilence, silenceGame, silenceSystem, silenceView,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/silence.js";

assert.deepEqual([...SILENCE_DURATIONS], [0, 20, 30]);
assert.equal(SILENCE_TICKS_PER_SECOND, 20);
assert.equal(SILENCE_TURN_TICKS, 6000);
assert.equal(SILENCE_PLAY_TICKS, 2000);
assert.equal(SILENCE_MAX_HP, 60000);
assert.equal(silenceGame.seats, 2);

{
  let state = createSilence();
  assert.equal(state.arranger, 1);
  assert.equal(state.chooser, 0);
  assert.equal(state.actor, 1);
  assert.throws(() => advanceSilence(state, 1, "arrange", [0, 20, 20]), /1枚ずつ/);
  assert.throws(() => advanceSilence(state, 0, "arrange", [30, 20, 0]), /手番|配置/);
  state = advanceSilence(state, 1, "arrange", [30, 0, 20]);
  assert.equal(state.phase, "choose");
  assert.equal(state.actor, 0);
  const view = silenceView(state);
  assert.equal("arrangements" in view, false);
  state = advanceSilence(state, 0, "choose", 2);
  assert.equal(state.phase, "playback");
  assert.equal(state.playSeat, 0);
  assert.equal(state.playTicks, 20 * SILENCE_TICKS_PER_SECOND);
  assert.equal(state.totalSeconds, 20);
  assert.equal(state.last.duration, 20);
}

{
  let state = createSilence();
  state = advanceSilence(state, 1, "arrange", [0, 20, 30]);
  state = advanceSilence(state, 0, "choose", 1);
  for (let index = 0; index < 10; index++) state = silenceSystem(state, "tick");
  assert.deepEqual(state.hp, [58100, 58100]);
  state = advanceSilence(state, 0, "guard", undefined);
  assert.equal(state.guard[0], true);
  state = advanceSilence(state, 1, "guard", undefined);
  assert.equal(state.guard[1], true);
  for (let index = 0; index < 10; index++) state = silenceSystem(state, "tick");
  assert.deepEqual(state.hp, [56700, 56700]);
  assert.equal(state.phase, "playback");
  assert.equal(state.remainingTicks, 1980);
}

{
  let state = createSilence();
  state = advanceSilence(state, 1, "arrange", [0, 20, 30]);
  state = advanceSilence(state, 0, "choose", 0);
  assert.equal(state.phase, "return");
  assert.equal(state.totalSeconds, 0);
  state = advanceSilence(state, 0, "return", undefined);
  assert.equal(state.phase, "arrange");
  assert.equal(state.arranger, 0);
  assert.equal(state.chooser, 1);
}

{
  let state = {
    ...createSilence(),
    phase: "playback",
    actor: 0,
    playSeat: 0,
    playTicks: 4,
    remainingTicks: 1,
  };
  state = silenceSystem(state, "tick");
  assert.deepEqual(state.hp, [59810, 59810]);
  state = silenceSystem(state, "tick");
  assert.deepEqual(state.hp, [53810, 53810]);
  assert.equal(state.remainingTicks, -1);
}

{
  let state = {
    ...createSilence(),
    phase: "playback",
    actor: 1,
    playSeat: 1,
    playTicks: 2,
    hp: [100, 100],
    remainingTicks: -1,
  };
  state = silenceSystem(state, "tick");
  assert.equal(state.phase, "finished");
  assert.equal(state.winner, null);
  assert.equal(state.reason, "health");
}

{
  const choosing = silenceSystem({ ...createSilence(), phase: "choose", actor: 0 }, "timeout");
  assert.equal(choosing.phase, "finished");
  assert.equal(choosing.winner, 1);
  assert.equal(choosing.reason, "timeout");
  assert.throws(() => silenceSystem(createSilence(), "tick"), /不正/);
}

console.log("PASS Silence jacket order, hidden choice, playback cadence, guards, 100-second overflow, HP and timeout");
