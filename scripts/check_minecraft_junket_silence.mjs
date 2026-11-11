import assert from "node:assert/strict";
import {
  SILENCE_DURATIONS, SILENCE_MAX_HP, SILENCE_PLAY_TICKS, SILENCE_TICKS_PER_SECOND,
  SILENCE_TURN_TICKS, advanceSilence, createSilence, silenceGame, silenceSystem, silenceView,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/silence.js";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";

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
  assert.equal(state.phase, "set");
  assert.equal(state.playSeat, 0);
  assert.equal(state.playTicks, 20 * SILENCE_TICKS_PER_SECOND);
  assert.equal(state.totalSeconds, 20);
  assert.equal(state.last.duration, 20);
  state = advanceSilence(state, 0, "set", undefined);
  assert.equal(state.phase, "playback");
}

{
  let state = createSilence();
  state = advanceSilence(state, 1, "arrange-select", 0);
  assert.equal(state.arrangeSelected, 0);
  state = advanceSilence(state, 1, "arrange-select", 2);
  assert.deepEqual(state.arrangeDrafts[1], [0, 30, 20]);
  assert.equal(state.arrangeSelected, null);
  state = advanceSilence(state, 1, "arrange-confirm", undefined);
  assert.equal(state.phase, "choose");
  assert.deepEqual(state.arrangements[1], [0, 30, 20]);
  assert.throws(() => advanceSilence(state, 1, "arrange-select", 0), /手番/);
}

{
  let state = createSilence();
  state = advanceSilence(state, 1, "arrange", [0, 20, 30]);
  state = advanceSilence(state, 0, "choose", 1);
  state = advanceSilence(state, 0, "set", undefined);
  for (let index = 0; index < 10; index++) state = silenceSystem(state, "tick");
  assert.deepEqual(state.hp, [59620, 59620]);
  state = advanceSilence(state, 0, "guard", undefined);
  assert.equal(state.guard[0], true);
  state = advanceSilence(state, 1, "guard", undefined);
  assert.equal(state.guard[1], true);
  for (let index = 0; index < 10; index++) state = silenceSystem(state, "tick");
  assert.deepEqual(state.hp, [59340, 59340]);
  assert.equal(state.phase, "playback");
  assert.equal(state.remainingTicks, 1980);
}

{
  let state = createSilence();
  state = advanceSilence(state, 1, "arrange", [0, 20, 30]);
  state = advanceSilence(state, 0, "choose", 0);
  assert.equal(state.phase, "set");
  assert.equal(state.totalSeconds, 0);
  state = advanceSilence(state, 0, "set", undefined);
  assert.equal(state.phase, "return");
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
    playTicks: 10,
    damageClock: 4,
    remainingTicks: 1,
  };
  state = silenceSystem(state, "tick");
  assert.deepEqual(state.hp, [59810, 59810]);
  for (let index = 0; index < 5; index++) state = silenceSystem(state, "tick");
  assert.deepEqual(state.hp, [53810, 53810]);
  assert.equal(state.remainingTicks, -5);
}

{
  let state = {
    ...createSilence(),
    phase: "playback",
    actor: 1,
    playSeat: 1,
    playTicks: 2,
    damageClock: 4,
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

{
  // Version 1 saves predate the physical jacket draft fields. Restoring one
  // must keep the arrange phase usable instead of accepting a state whose
  // view or next interaction reads undefined.
  const state = {
    ...createSilence(),
    phase: "arrange",
    actor: 1,
    arranger: 1,
    chooser: 0,
  };
  delete state.arrangeDrafts;
  delete state.arrangeSelected;
  const payload = {
    schema: 1,
    sequence: 7,
    tables: [{ id: "silence", gameId: "silence", players: ["p1", "p2"], token: 7, state }],
  };
  const manager = new GameManager([silenceGame]);
  assert.deepEqual(manager.restore(payload), { restored: 1, skipped: 0 });
  const restored = manager.forPlayer("p2").state;
  assert.deepEqual(restored.arrangeDraft, [20, 30, 0]);
  assert.equal(restored.arrangeSelected, null);
  assert.deepEqual(advanceSilence(manager.tables.get("silence").state, 1,
    "arrange-select", 0).arrangeDrafts[1], [20, 30, 0]);
}

{
  // Playback and return are timer phases. Their saved actor, seat, duration
  // and remaining ticks must survive a restart without reopening arrangement.
  const last = { round: 20, arranger: 1, chooser: 0, slot: 1, duration: 20 };
  for (const [phase, playTicks, remainingTicks] of [["playback", 400, 321], ["return", 0, 321]]) {
    const state = {
      ...createSilence(),
      phase,
      actor: 0,
      arranger: 1,
      chooser: 0,
      arrangements: [null, [0, 20, 30]],
      arrangeDrafts: [[20, 30, 0], [20, 30, 0]],
      arrangeSelected: null,
      playSeat: 0,
      playTicks,
      remainingTicks,
      totalSeconds: 20,
      last,
    };
    const payload = {
      schema: 1,
      sequence: 8,
      tables: [{ id: "silence", gameId: "silence", players: ["p1", "p2"], token: 8, state }],
    };
    const manager = new GameManager([silenceGame]);
    assert.deepEqual(manager.restore(payload), { restored: 1, skipped: 0 });
    const restored = manager.tables.get("silence").state;
    assert.equal(restored.phase, phase);
    assert.equal(restored.actor, 0);
    assert.equal(restored.playSeat, 0);
    assert.equal(restored.remainingTicks, remainingTicks);
    assert.deepEqual(restored.last, last);
  }
}

{
  // A partially-written choose/set save must not crash on restore or trap the
  // table in an unplayable phase. It falls back to the last complete phase.
  const missingChoice = {
    ...createSilence(),
    phase: "choose",
    actor: 0,
    arrangements: [null, null],
  };
  const missingSet = {
    ...createSilence(),
    phase: "set",
    actor: 0,
    arrangements: [null, [0, 20, 30]],
    playSeat: 0,
    last: null,
  };
  for (const [state, expectedPhase] of [[missingChoice, "arrange"], [missingSet, "choose"]]) {
    const payload = {
      schema: 1,
      sequence: 9,
      tables: [{ id: "silence", gameId: "silence", players: ["p1", "p2"], token: 9, state }],
    };
    const manager = new GameManager([silenceGame]);
    assert.deepEqual(manager.restore(payload), { restored: 1, skipped: 0 });
    assert.equal(manager.tables.get("silence").state.phase, expectedPhase);
  }
}

console.log("PASS Silence jacket order, hidden choice, playback cadence, guards, 100-second overflow, HP, timeout and legacy restore migration");
