import { createSparrow, advanceSparrow, sparrowView } from "./sparrow.js";
import { createLucy, advanceLucy, lucySystem, lucyView, lucyViewForPlayer } from "./lucy.js";
import { createSilence, advanceSilence, silenceSystem, silenceView, silenceViewForPlayer } from "./silence.js";
import { createJinn, advanceJinn, jinnSystem, jinnView, jinnViewForPlayer } from "./jinn.js";
import { createTemperance, advanceTemperance, temperanceSystem } from "./temperance.js";
import { createAce, advanceAce, aceSystem, compareAceDice } from "./ace.js";

// Runs inside BDS as well as Node. It invokes the production reducers, with
// deterministic server-side choices. It does not pretend to operate a client.
export function runSpecialRuleTraces({ only = null } = {}) {
  const results = [];
  function trace(game, execute) {
    if (only && only !== game) return;
    let checks = 0, actions = 0, ticks = 0;
    const check = (condition, name) => { checks++; if (!condition) throw new Error(`${game}: ${name}`); };
    const rejects = (operation, name) => {
      let rejected = false; try { operation(); } catch { rejected = true; }
      check(rejected, name);
    };
    const act = (fn, ...args) => { actions++; return fn(...args); };
    const tick = (fn, ...args) => { ticks++; return fn(...args); };
    const state = execute({ check, rejects, act, tick });
    check(state.phase === "finished", "start-to-finish trace");
    results.push({ game, checks, actions, ticks, phase: state.phase,
      winner: state.winner ?? state.winnerTeam ?? null, reason: state.reason,
      test_kind: "production reducer progression", client_play: false });
  }
  trace("sparrow", ({ check, rejects, act }) => {
    let s = createSparrow();
    check(s.money.every(n => n === 50000000) && s.round === 1, "initial state");
    rejects(() => advanceSparrow(s, 1, "bet", 1000000), "wrong actor");
    rejects(() => advanceSparrow(s, 0, "bet", 999999), "minimum bet");
    while (s.phase !== "finished") {
      const setter = s.setter;
      s = act(advanceSparrow, s, setter, "bet", 1000001);
      s = act(advanceSparrow, s, setter, "hide", "small");
      check(!("hidden" in sparrowView(s)), "secret box");
      s = act(advanceSparrow, s, 1 - setter, "choose", "small");
      check(s.last.amount === 500000, "small payout integer division");
    }
    check(s.round === 6 && s.winner === null, "six-round tie");
    return s;
  });
  trace("lucy", ({ check, rejects, act }) => {
    let s = createLucy();
    s = act(advanceLucy, s, 0, "lock-toggle", { row: 0, column: 0 });
    check(!lucyView(s).lockDrafts.flat(2).some(Boolean), "public drafts redacted");
    check(lucyViewForPlayer(s, 0).lockDrafts[1][0][0], "own draft retained");
    check(!lucyViewForPlayer(s, 1).lockDrafts[1][0][0], "opponent draft hidden");
    for (let row = 0; row < 5; row++) for (const seat of [1, 0])
      s = act(advanceLucy, s, seat, "lock", [true, false, false, false, false]);
    rejects(() => advanceLucy(s, 0, "attempt", { row: 4, column: 0 }), "cannot skip faces");
    for (let row = 0; row < 5; row++) {
      s = act(advanceLucy, s, 0, "attempt", { row, column: 0 });
      if (s.phase === "finished") break;
      s = act(advanceLucy, s, 0, "ack");
      s = act(advanceLucy, s, 1, "attempt", { row: 0, column: row + 1 });
      s = act(advanceLucy, s, 1, "recover");
    }
    check(s.winner === 0 && s.remaining[0] === 0 && s.hp[1] === 48000, "five distinct faces win and miss damage");
    const setup = { ...createLucy(), progress: [5, 0] };
    check(lucySystem(setup, "timeout").winner === 1, "unfinished configuring player loses");
    return s;
  });
  trace("silence", ({ check, rejects, act, tick }) => {
    let s = createSilence();
    s = act(advanceSilence, s, 1, "arrange-select", 0);
    check(silenceView(s).arrangeDraft === null, "public arrangement hidden");
    check(silenceViewForPlayer(s, 1).arrangeDraft[0] === 20, "arranger sees draft");
    check(silenceViewForPlayer(s, 0).arrangeDraft === null, "chooser cannot infer arrangement");
    while (s.phase !== "finished") {
      s = act(advanceSilence, s, s.arranger, "arrange", [0, 20, 30]);
      s = act(advanceSilence, s, s.chooser, "choose", 1);
      s = act(advanceSilence, s, s.actor, "set");
      for (const seat of [0, 1]) if (!s.guard[seat]) s = act(advanceSilence, s, seat, "guard");
      const before = s.hp[s.playSeat];
      let count = 0;
      while (s.phase === "playback") {
        s = tick(silenceSystem, s, "tick", { otherInside: false }); count++;
        if (s.totalSeconds === 20 && count === 5) check(s.hp[s.playSeat] === before - 140, "damage consumed every5ticks");
      }
      if (s.phase === "finished") break;
      s = act(advanceSilence, s, s.actor, "return");
      check(s.totalSeconds <= 120, "bounded trace");
    }
    check(s.reason === "health" && s.remainingTicks < 0, "100s shared deadline damage branch");
    rejects(() => advanceSilence(s, 0, "guard"), "finished input rejected");
    return s;
  });
  trace("jinn", ({ check, rejects, act, tick }) => {
    let s = createJinn();
    rejects(() => advanceJinn({ ...s, phase: "settlement" }, 0, "surrender"), "no early surrender");
    const cards = ["thief", "demon", "gold", "gold", "gold", "gold"];
    for (let game = 1; game <= 3; game++) {
      for (const card of cards) {
        const before = s.hands[0][card];
        s = act(advanceJinn, s, 0, "choose", card);
        check(jinnView(s).hands[0][card] === before, "pending choice not inferred from counts");
        check(jinnViewForPlayer(s, 0).hands[0][card] === before - 1, "own hand updated");
        s = act(advanceJinn, s, 1, "choose", card);
        s = act(advanceJinn, s, 0, "reveal");
      }
      check(s.phase === "settlement", "six rounds to settlement");
      if (game === 3) return act(advanceJinn, s, 1, "surrender");
      for (const seat of [0, 1]) s = act(advanceJinn, s, seat, "lever");
      let limit = 2000;
      while (s.phase === "settlement" && limit-- > 0) s = tick(jinnSystem, s, "tick");
      check(s.game === game + 1 && s.phase === "cards", "settlement completes before next batch");
    }
    return s;
  });
  trace("temperance", ({ check, rejects, act }) => {
    let s = createTemperance();
    rejects(() => advanceTemperance(s, 1, "arrange", [1, 2, 3]), "mixed orientation required");
    let limit = 100;
    while (s.phase !== "finished" && limit-- > 0) {
      s = act(advanceTemperance, s, s.actor, "arrange", [1, 2, -3]);
      s = act(advanceTemperance, s, s.actor, "target", 1);
      const old = s.balance, chooser = s.actor;
      s = act(advanceTemperance, s, s.actor, "choose", 1);
      if (chooser === 0) check(s.balance === old, "balance waits for second selection");
    }
    check(s.phase === "finished" && s.reason === "health", "pressure ends game");
    const trapped = temperanceSystem({ ...createTemperance(), balance: 101 }, "air");
    check(trapped.hp[0] === 50000 && trapped.hp[1] === 60000, "blocked air damages correct side");
    return s;
  });
  trace("ace", ({ check, rejects, act }) => {
    let s = createAce();
    check(compareAceDice(["10","10","10","J","J"], ["10","10","10","Q","Q"]) > 0, "original full-house exception");
    check(aceSystem(s, "timeout") === s, "source has no input timeout");
    check(advanceAce(s, 0, "table_bet", 200).tableBet === 200, "source allows debt");
    const dice = ["A", "K", "Q", "J"].map(face => Array(5).fill(face));
    let limit = 300;
    while (s.phase !== "finished" && limit-- > 0) {
      if (s.phase === "table_bet") s = act(advanceAce, s, s.actor, "table_bet", 10);
      else if (s.phase === "roll") s = act(advanceAce, s, s.actor, "roll", dice[s.actor]);
      else if (s.phase === "betting") s = act(advanceAce, s, s.actor, "bet", { type: "call" });
      else if (s.phase === "reroll_choice") s = act(advanceAce, s, s.actor, "reroll_selected", { indices: [], dice: s.dice[s.actor] });
      else if (s.phase === "between") s = act(aceSystem, s, "next_game");
      else throw new Error(`unexpected Ace phase ${s.phase}`);
    }
    check(s.game === 5 && s.reason === "team_coins" && s.winnerTeam === 0, "five-game team settlement");
    const tied = { ...createAce(), phase: "reroll", actor: 0, tied: [0,1], tiedRolled: [false,false,false,false],
      dice, hands: [null,null,null,null] };
    rejects(() => advanceAce(tied, 1, "reroll", dice[1]), "tie reroll actor enforced");
    const first = advanceAce(tied, 0, "reroll", dice[0]);
    rejects(() => advanceAce(first, 0, "reroll", dice[0]), "tie reroll replay rejected");
    return s;
  });
  return results;
}
