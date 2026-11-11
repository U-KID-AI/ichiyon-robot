// Independent turn-based board adaptations of the spatial JUNKET games.
// They keep the source game decisions and win conditions testable without
// modifying terrain, entities, inventories, or vanilla player health.

const requireSeat = (seat, count = 2) => {
  if (!Number.isInteger(seat) || seat < 0 || seat >= count) throw new Error("プレイヤー番号が不正です。");
};
const requireTurn = (state, seat, phase) => {
  if (state.phase !== phase || state.actor !== seat) throw new Error("現在の手番ではありません。");
};
const copyChoices = choices => choices.map(value => value && { ...value });

// ---------------------------------------------------------------- Short Hope

export const HOPE_FLOORS = 5;
export const HOPE_PADS = 16;
export const HOPE_ROUNDS = 10;
export const HOPE_MAX_PLAYERS = 16;
export const HOPE_AI_TRADERS = 17;
// Source timing: rounds 1-3 reveal at time=5950, rounds 4-10 at 5900;
// timeup is 149 ticks after a round starts, the floor fall is 85 ticks after
// timeup, and the next round is 36 ticks after the fall event.
export const HOPE_EARLY_CHOICE_TICKS = 50;
export const HOPE_LATE_CHOICE_TICKS = 100;
export const HOPE_REVEAL_TICKS = 99;
export const HOPE_DROP_TICKS = 85;
export const HOPE_RESULT_TICKS = 36;
// round10.mcfunction checks the no-outplayer fast-forward at time=5790.
// timeup runs at 5801, so the check happens eleven ticks into the drop
// window. The source then jumps the clock to 5710 and reaches tonext/all at
// 5680, leaving a thirty-tick hold before the match is declared safe.
export const HOPE_ROUND_TEN_CHECK_AFTER_DROP_TICKS = 11;
export const HOPE_ROUND_TEN_NO_OUT_REMAINING_TICKS = 30;
// game:hope/enemy/walk uses start=5950 in rounds 1-3 and start=5880 in
// rounds 4-10. Both are only the point where the custom follow/teleport
// branch begins; the source traders keep their physics active until timeup
// (5851 / 5801) and are frozen by NoAI:1b only at the end of the 99-tick
// reveal. The value is retained for source comparisons of that branch delay.
export const HOPE_LATE_AI_STOP_DELAY = 20;
// The source map assigns one of five symbols to each of the sixteen 4x4
// floor markers. This is the marker layout read from hope/prepare.mcfunction.
export const HOPE_PAD_SYMBOLS = Object.freeze([
  2, 1, 4, 1,
  3, 5, 3, 2,
  1, 2, 1, 5,
  5, 4, 3, 4,
]);
export const HOPE_SYMBOL_NAMES = Object.freeze({
  1: "サイコロ", 2: "目", 3: "ビール", 4: "注射", 5: "ハート",
});
const nextRandom = seed => ((seed * 1664525 + 1013904223) >>> 0);
const drawRandom = (seed, size) => {
  const next = nextRandom(seed);
  return { seed: next, value: next % size };
};
const shuffledValues = (seed, values) => {
  const result = [...values];
  for (let index = result.length - 1; index > 0; index--) {
    const draw = drawRandom(seed, index + 1);
    seed = draw.seed;
    [result[index], result[draw.value]] = [result[draw.value], result[index]];
  }
  return { seed, values: result };
};
const symbolPool = safe => [1, 2, 3, 4, 5].filter(symbol => symbol !== safe);
// These are the fixed source choices for round 3. The source chooses a
// different set for each possible safe symbol rather than three arbitrary
// symbols.
const HOPE_ROUND3_HAZARDS = Object.freeze({
  1: Object.freeze([2, 4, 5]),
  2: Object.freeze([1, 3, 4]),
  3: Object.freeze([1, 2, 5]),
  4: Object.freeze([1, 2, 5]),
  5: Object.freeze([1, 3, 4]),
});
// Rounds 4-9 come from hope/main/round4-9.mcfunction. The source uses seven
// equally likely branches, two of which contain an extra 50/50 branch. The
// counts are the 15 non-safe marker assignments; the safe marker is assigned
// separately and is never included in its own hazard set.
const HOPE_LATE_PATTERNS = Object.freeze([
  { safe: 5, hazards: [1, 2, 4], counts: { 1: 3, 2: 3, 3: 3, 4: 3, 5: 3 } },
  { safe: 5, hazards: [1, 3, 4], counts: { 1: 3, 2: 3, 3: 3, 4: 3, 5: 3 } },
  { safe: 3, hazards: [1, 4, 5], counts: { 1: 2, 2: 2, 3: 1, 4: 1, 5: 9 } },
  { safe: 1, hazards: [2, 3, 4], counts: { 1: 3, 2: 3, 3: 3, 4: 3, 5: 3 } },
  { safe: 4, hazards: [2, 3, 5], counts: { 1: 3, 2: 3, 3: 3, 4: 3, 5: 3 } },
  { safe: 3, hazards: [2, 4, 5], counts: { 1: 3, 2: 3, 3: 3, 4: 3, 5: 3 } },
  { safe: 2, hazards: [3, 4, 5], counts: { 1: 2, 2: 1, 3: 4, 4: 4, 5: 4 } },
]);
function hopePadState(state) {
  const padSymbols = Array.isArray(state.padSymbols) && state.padSymbols.length === HOPE_PADS
    ? [...state.padSymbols] : [...HOPE_PAD_SYMBOLS];
  let safePad = Number.isInteger(state.safePad) && state.safePad >= 0 && state.safePad < HOPE_PADS
    ? state.safePad : padSymbols.indexOf(state.safe);
  if (safePad < 0) safePad = 0;
  return { padSymbols, safePad };
}
function assignHopePadSymbols(padSymbols, safePad, safe, counts, seed) {
  const result = [...padSymbols];
  result[safePad] = safe;
  const positions = Array.from({ length: HOPE_PADS }, (_, index) => index)
    .filter(index => index !== safePad);
  const shuffled = shuffledValues(seed, positions);
  seed = shuffled.seed;
  let cursor = 0;
  for (let symbol = 1; symbol <= HOPE_FLOORS; symbol++) {
    const count = Number(counts?.[symbol] ?? 0);
    for (let index = 0; index < count; index++) {
      if (cursor >= shuffled.values.length) throw new Error("Hope床の図柄割当が不正です。");
      result[shuffled.values[cursor++]] = symbol;
    }
  }
  if (cursor !== shuffled.values.length) throw new Error("Hope床の図柄数が不正です。");
  return { seed, padSymbols: result };
}
function hopeRoundSetup(round, safe, safePad, seed, padSymbols) {
  let nextSeed = seed;
  let nextSafe = safe;
  let hazards;
  let nextPads = [...padSymbols];
  if (round === 1) {
    const draw = drawRandom(nextSeed, HOPE_FLOORS - 1);
    nextSeed = draw.seed;
    hazards = [symbolPool(safe)[draw.value]];
  } else if (round === 2) {
    const pairs = [];
    for (let first = 1; first <= HOPE_FLOORS; first++) {
      for (let second = first + 1; second <= HOPE_FLOORS; second++) {
        if (first !== safe && second !== safe) pairs.push([first, second]);
      }
    }
    const draw = drawRandom(nextSeed, pairs.length);
    nextSeed = draw.seed;
    hazards = pairs[draw.value];
  } else if (round === 3) {
    hazards = [...HOPE_ROUND3_HAZARDS[safe]];
  } else if (round === HOPE_ROUNDS) {
    nextSafe = 2;
    hazards = [1, 3, 5];
    const assigned = assignHopePadSymbols(nextPads, safePad, nextSafe,
      { 1: 3, 2: 3, 3: 3, 4: 3, 5: 3 }, nextSeed);
    nextSeed = assigned.seed;
    nextPads = assigned.padSymbols;
  } else {
    const draw = drawRandom(nextSeed, HOPE_LATE_PATTERNS.length);
    nextSeed = draw.seed;
    let pattern = HOPE_LATE_PATTERNS[draw.value];
    if (draw.value === 2) {
      const sub = drawRandom(nextSeed, 2);
      nextSeed = sub.seed;
      if (sub.value === 1) {
        pattern = { safe: 2, hazards: [1, 4, 5], counts: { 1: 2, 2: 1, 3: 2, 4: 1, 5: 9 } };
      }
    } else if (draw.value === 6) {
      const sub = drawRandom(nextSeed, 2);
      nextSeed = sub.seed;
      if (sub.value === 1) {
        pattern = { safe: 1, hazards: [3, 4, 5], counts: { 1: 1, 2: 2, 3: 4, 4: 4, 5: 4 } };
      }
    }
    nextSafe = pattern.safe;
    hazards = [...pattern.hazards];
    const assigned = assignHopePadSymbols(nextPads, safePad, nextSafe, pattern.counts, nextSeed);
    nextSeed = assigned.seed;
    nextPads = assigned.padSymbols;
  }
  return { safe: nextSafe, hazards, seed: nextSeed, padSymbols: nextPads };
}
function hopeSurvivors(alive) {
  return alive.map((value, seat) => value ? seat : -1).filter(seat => seat >= 0);
}
function hopeAiInitial() {
  return Array.from({ length: HOPE_AI_TRADERS }, (_, index) => ({
    id: index + 1, pad: index + 1, alive: true, loser: false,
  }));
}
function hopeAiLoserCount(round) {
  if (round === 3) return 2;
  if (round === 4) return 6;
  if (round === 6 || round === 8) return 2;
  return 1;
}
function hopeAiRound(state, round, hazards, padSymbols) {
  const ai = Array.isArray(state.ai) && state.ai.length === HOPE_AI_TRADERS
    ? state.ai.map(value => ({
      ...value,
      alive: value.alive !== false,
      loser: value.loser === true,
    })) : hopeAiInitial();
  let seed = Number.isInteger(state.aiSeed) ? state.aiSeed >>> 0 : 0x9e3779b9;
  // tonext/*.mcfunction selects wandering_traders from the entities that are
  // still alive. A hope-loser tag is never removed between rounds, so an AI
  // selected again keeps that role. A trader on a hazard floor is killed by
  // walk.mcfunction and does not return in the next round.
  const alive = ai.map((value, index) => value.alive ? index : -1)
    .filter(index => index >= 0);
  const hazardPads = [];
  const safePads = [];
  for (let pad = 1; pad <= HOPE_PADS; pad++) {
    if (hazards.includes(padSymbols[pad - 1])) hazardPads.push(pad);
    else safePads.push(pad);
  }
  // The seventeenth source trader stands behind the grid and has no falling
  // marker; it is a safe wanderer rather than a seventeenth hazard floor.
  safePads.push(HOPE_AI_TRADERS);
  const order = shuffledValues(seed, alive);
  seed = order.seed;
  const loserCount = Math.min(hopeAiLoserCount(round), alive.length);
  for (let rank = 0; rank < loserCount; rank++) {
    const index = order.values[rank];
    ai[index] = { ...ai[index], loser: true };
  }
  const loserOrder = shuffledValues(seed,
    alive.filter(index => ai[index].loser));
  seed = loserOrder.seed;
  const normalOrder = shuffledValues(seed,
    alive.filter(index => !ai[index].loser));
  seed = normalOrder.seed;
  const hazardOrder = shuffledValues(seed, hazardPads);
  seed = hazardOrder.seed;
  const safeOrder = shuffledValues(seed, safePads);
  seed = safeOrder.seed;
  let hazardRank = 0;
  let safeRank = 0;
  // walk.mcfunction sends normal traders toward !hope-out judges and loser
  // traders toward hope-out judges. Assigning every living loser to a hazard
  // floor preserves that targeting, including a loser selected again later.
  // Dead traders keep their last pad as historical state and are excluded.
  const hazardTargets = hazardOrder.values.length ? hazardOrder.values : safeOrder.values;
  for (const index of loserOrder.values) {
    const pad = hazardTargets[hazardRank++ % hazardTargets.length];
    ai[index] = { ...ai[index], pad };
  }
  for (const index of normalOrder.values) {
    const pad = safeOrder.values[safeRank++ % safeOrder.values.length];
    ai[index] = { ...ai[index], pad };
  }
  return { ai, aiSeed: seed };
}
export function hopeAiMotionActive(state) {
  if (!state) return false;
  // The source only freezes wandering_trader entities at timeup, which is the
  // end of the 99-tick reveal in every round. Do not stop them when the
  // reveal begins (rounds 1-3) or after the 20-tick late-start delay
  // (rounds 4-10); drop is the first phase with NoAI:1b in effect.
  return state.phase === "choose" || state.phase === "reveal";
}
function hopeBeginRound(state, round) {
  const { padSymbols, safePad } = hopePadState(state);
  const setup = hopeRoundSetup(round, state.safe, safePad, state.seed, padSymbols);
  const survivors = hopeSurvivors(state.alive);
  const aiSetup = hopeAiRound(state, round, setup.hazards, setup.padSymbols);
  return {
    ...state, phase: "choose", round, actor: survivors[0] ?? null,
    choices: Array(state.alive.length).fill(null),
    safe: setup.safe, safePad, padSymbols: setup.padSymbols,
    hazards: setup.hazards, seed: setup.seed,
    ai: aiSetup.ai, aiSeed: aiSetup.aiSeed,
    phaseRemaining: round <= 3 ? HOPE_EARLY_CHOICE_TICKS : HOPE_LATE_CHOICE_TICKS,
    floorOpen: false,
  };
}
function hopeResolve(state) {
  const alive = [...state.alive];
  const eliminated = [];
  const { padSymbols } = hopePadState(state);
  for (let seat = 0; seat < alive.length; seat++) {
    const choice = state.choices[seat];
    // The source always resolves a participant against the nearest floor
    // marker. A missing position therefore means the player was not on any
    // valid floor when the fall resolved and must not survive by default.
    if (alive[seat] && (!choice || state.hazards.includes(choice.symbol))) {
      alive[seat] = false;
      eliminated.push(seat);
    }
  }
  const last = {
    round: state.round, safe: state.safe, hazards: [...state.hazards],
    choices: state.choices.map(choice => choice?.pad ?? null),
    symbols: state.choices.map(choice => choice?.symbol ?? null), eliminated,
    aiLost: (state.ai ?? []).filter(value =>
      value.alive && value.pad <= HOPE_PADS && state.hazards.includes(padSymbols[value.pad - 1])
    ).map(value => value.id),
  };
  // walk.mcfunction gives hope-outplayer to every hope-player on a hope-out
  // judge and kills it at time=death. The dead trader is not restored by a
  // later tonext call.
  const ai = (state.ai ?? []).map(value => {
    const onHazard = value.alive !== false && Number.isInteger(value.pad) &&
      value.pad >= 1 && value.pad <= HOPE_PADS &&
      state.hazards.includes(padSymbols[value.pad - 1]);
    return onHazard ? { ...value, alive: false } : { ...value };
  });
  return {
    ...state, alive, ai, last, phase: "result", actor: null,
    phaseRemaining: HOPE_RESULT_TICKS, floorOpen: true,
  };
}
function hopeRoundTenNoOut(state) {
  if (state.round !== HOPE_ROUNDS) return false;
  const { padSymbols } = hopePadState(state);
  for (let seat = 0; seat < state.alive.length; seat++) {
    if (!state.alive[seat]) continue;
    const choice = state.choices[seat];
    if (!choice || !Number.isInteger(choice.pad) || choice.pad < 1 || choice.pad > HOPE_PADS) {
      return false;
    }
    const symbol = padSymbols[choice.pad - 1];
    if (symbol !== choice.symbol || state.hazards.includes(symbol)) return false;
  }
  // round10.mcfunction checks tag=hope-outplayer for players and wandering
  // traders together. A living AI on a hope-out judge must therefore prevent
  // the early no-out finish.
  for (const value of state.ai ?? []) {
    if (value.alive === false || !Number.isInteger(value.pad) ||
        value.pad < 1 || value.pad > HOPE_PADS) continue;
    if (state.hazards.includes(padSymbols[value.pad - 1])) return false;
  }
  return true;
}
function hopeFinishRoundTenNoOut(state) {
  const last = {
    round: state.round, safe: state.safe, hazards: [...state.hazards],
    choices: state.choices.map(choice => choice?.pad ?? null),
    symbols: state.choices.map(choice => choice?.symbol ?? null),
    eliminated: [], aiLost: [], noOut: true,
  };
  return hopeFinish({ ...state, last }, "survived");
}
function hopeFinish(state, reason) {
  const survivors = hopeSurvivors(state.alive);
  return {
    ...state, phase: "finished", actor: null, floorOpen: false,
    winner: survivors.length === 1 ? survivors[0] : null,
    winners: survivors, reason,
  };
}
export function createHope(playerCount = 2) {
  const count = Math.max(1, Math.min(HOPE_MAX_PLAYERS, Number(playerCount) || 2));
  const seed = (Date.now() ^ count) >>> 0;
  const safePad = seed % HOPE_PADS;
  const padSymbols = [...HOPE_PAD_SYMBOLS];
  const safe = padSymbols[safePad];
  return hopeBeginRound({
    phase: "choose", round: 1, actor: 0, playerCount: count,
    alive: Array(count).fill(true), choices: Array(count).fill(null),
    safe, safePad, padSymbols, hazards: [], seed, phaseRemaining: 0, floorOpen: false,
    ai: hopeAiInitial(), aiSeed: (seed ^ 0x9e3779b9) >>> 0,
    last: null, winner: null, winners: [], reason: null,
  }, 1);
}
export function advanceHope(state, seat, action, value) {
  requireSeat(seat, state.playerCount);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action !== "floor") throw new Error("不正な操作です。");
  if (!["choose", "reveal"].includes(state.phase)) throw new Error("床を選べる時間ではありません。");
  if (!state.alive[seat]) throw new Error("脱落済みです。");
  if (!Number.isInteger(value) || value < 1 || value > HOPE_PADS) {
    throw new Error("1〜16の床を選んでください。");
  }
  const choices = [...state.choices];
  const padSymbols = hopePadState(state).padSymbols;
  choices[seat] = { pad: value, symbol: padSymbols[value - 1] };
  const nextActor = state.alive.findIndex((alive, index) => alive && choices[index] === null);
  return { ...state, choices, actor: nextActor >= 0 ? nextActor : null };
}
function hopePositionChoices(state, positions) {
  if (!Array.isArray(positions) || positions.length !== state.playerCount) {
    throw new Error("Hopeの立位置が不正です。");
  }
  const padSymbols = hopePadState(state).padSymbols;
  const choices = state.choices.map((choice, seat) => {
    if (!state.alive[seat]) return null;
    const pad = positions[seat];
    if (!Number.isInteger(pad) || pad < 1 || pad > HOPE_PADS) return null;
    return { pad, symbol: padSymbols[pad - 1] };
  });
  const nextActor = state.alive.findIndex((alive, index) => alive && choices[index] === null);
  return { ...state, choices, actor: nextActor >= 0 ? nextActor : null };
}
export function hopeSystem(state, action, value) {
  if (state.phase === "finished") return state;
  if (action === "position") {
    if (!["choose", "reveal"].includes(state.phase)) return state;
    return hopePositionChoices(state, value);
  }
  if (action === "tick") {
    if (!["choose", "reveal", "drop", "result"].includes(state.phase)) return state;
    const remaining = Math.max(0, (state.phaseRemaining ?? 0) - 1);
    if (state.phase === "choose") {
      return remaining > 0
        ? { ...state, phaseRemaining: remaining }
        : { ...state, phase: "reveal", phaseRemaining: HOPE_REVEAL_TICKS, floorOpen: false };
    }
    if (state.phase === "reveal") {
      return remaining > 0
        ? { ...state, phaseRemaining: remaining }
        : { ...state, phase: "drop", phaseRemaining: HOPE_DROP_TICKS, floorOpen: false };
    }
    if (state.phase === "drop") {
      // The round-ten check is not a final-drop check. The source evaluates
      // it at time=5790, then skips the hazard/fall animation by advancing
      // the clock to 5710. Preserve that timing so the result is not shown
      // 80 ticks late. A restored state whose phaseRemaining is already inside
      // the window still gets checked exactly once.
      if (state.roundTenNoOutChecked !== true && state.round === HOPE_ROUNDS &&
          remaining <= HOPE_DROP_TICKS - HOPE_ROUND_TEN_CHECK_AFTER_DROP_TICKS &&
          hopeRoundTenNoOut(state)) {
        return {
          ...state,
          roundTenNoOut: true,
          roundTenNoOutChecked: true,
          phaseRemaining: HOPE_ROUND_TEN_NO_OUT_REMAINING_TICKS,
          floorOpen: false,
        };
      }
      if (state.roundTenNoOutChecked !== true && state.round === HOPE_ROUNDS &&
          remaining <= HOPE_DROP_TICKS - HOPE_ROUND_TEN_CHECK_AFTER_DROP_TICKS) {
        return { ...state, roundTenNoOutChecked: true, phaseRemaining: remaining };
      }
      if (remaining === 0 && state.roundTenNoOut === true) {
        return hopeFinishRoundTenNoOut(state);
      }
      return remaining > 0
        ? { ...state, phaseRemaining: remaining }
        : hopeResolve(state);
    }
    if (remaining > 0) return { ...state, phaseRemaining: remaining };
    const survivors = hopeSurvivors(state.alive);
    if (!survivors.length) return hopeFinish(state, "fallen");
    if (state.round >= HOPE_ROUNDS) return hopeFinish(state, "survived");
    return hopeBeginRound(state, state.round + 1);
  }
  const alive = [...state.alive];
  if (action === "timeout") {
    // The ticker captures every survivor's physical floor before the source
    // timeup. A generic form timeout must not eliminate by turn order.
    return state;
  } else if (action === "disconnect") {
    if (!Number.isInteger(value) || value < 0 || value >= alive.length) throw new Error("脱落対象が不正です。");
    alive[value] = false;
  } else {
    throw new Error("不正な内部操作です。");
  }
  const survivors = hopeSurvivors(alive);
  if (!survivors.length) return hopeFinish({ ...state, alive }, action === "disconnect" ? "disconnect" : "timeout");
  if (state.phase !== "choose") {
    return { ...state, alive, reason: action === "disconnect" ? "disconnect" : state.reason };
  }
  const choices = alive.map((isAlive, seat) => isAlive ? state.choices[seat] : null);
  let nextActor = choices.findIndex((choice, seat) => alive[seat] && choice === null);
  return { ...state, alive, choices, actor: nextActor >= 0 ? nextActor : null };
}
export function hopeView(state) {
  const revealed = ["reveal", "drop", "result", "finished"].includes(state.phase);
  const { padSymbols } = hopePadState(state);
  return {
    phase: state.phase, round: state.round, actor: state.actor, playerCount: state.playerCount,
    alive: [...state.alive],
    choices: state.phase === "choose" ? state.choices.map(() => null) : copyChoices(state.choices),
    safe: state.phase === "result" || state.phase === "finished" ? state.safe : null,
    hazards: revealed ? [...state.hazards] : [],
    // loser is an internal targeting role. It is not a public hazard clue and
    // must not be exposed through the player view.
    ai: Array.isArray(state.ai) ? state.ai.map(value => ({
      id: value.id, pad: value.pad, alive: value.alive,
    })) : [],
    pads: padSymbols.map((symbol, index) => ({
      pad: index + 1, symbol, name: HOPE_SYMBOL_NAMES[symbol],
    })),
    phaseRemaining: state.phaseRemaining ?? 0,
    floorOpen: state.floorOpen === true,
    last: state.last && {
      ...state.last, hazards: [...state.last.hazards],
      choices: [...state.last.choices], symbols: [...state.last.symbols],
      eliminated: [...state.last.eliminated],
      aiLost: Array.isArray(state.last.aiLost) ? [...state.last.aiLost] : [],
    },
    winner: state.winner, winners: [...state.winners], reason: state.reason,
  };
}
export function hopeViewForPlayer(state, seat) {
  requireSeat(seat, state.playerCount);
  return { ...hopeView(state), self: { seat, choice: state.choices[seat] && { ...state.choices[seat] } } };
}
export const hopeGame = Object.freeze({
  id: "hope", title: "ザ・ショートホープ", seats: HOPE_MAX_PLAYERS,
  minPlayers: 1, maxPlayers: HOPE_MAX_PLAYERS, registration: true,
  create: createHope, advance: advanceHope, view: hopeView, viewForPlayer: hopeViewForPlayer,
  systemAdvance: hopeSystem,
});

// ---------------------------------------------------------------- Clock

export const CLOCK_SECTORS = 12;
export const CLOCK_SETUP_TICKS = 6000;
export const CLOCK_MOVE_TICKS = 6000;
export const CLOCK_MOVE_END_TICKS = 160;
export const CLOCK_DROP_TICKS = 6000;
export const CLOCK_FLOOR_OPEN_TICKS = 5980;
export const CLOCK_FLOOR_RESTORE_TICKS = 5921;
export const CLOCK_NEXT_TURN_TICKS = 5910;
export const CLOCK_LANDING_END_TICKS = 60;
export const CLOCK_TIMEOUT_END_TICKS = 100;
// Source game:clock/move/* rotates the hands from time 5900 through 5841
// (three seconds). The hands damage participants they sweep through. Keep the
// same window and derive the angle from the persisted timer so a restart in
// the middle of the sweep resumes at the same position.
export const CLOCK_NEEDLE_START_TICKS = 5900;
export const CLOCK_NEEDLE_END_TICKS = 5840;
export const CLOCK_NEEDLE_HOUR_REACH = 5.2;
export const CLOCK_NEEDLE_MINUTE_REACH = 8.0;
export const CLOCK_NEEDLE_MIN_RADIUS = 1.1;
export const CLOCK_NEEDLE_HALF_WIDTH = 0.7;
export const CLOCK_NEEDLE_RECOVERY_TICKS = 200;

const clockHour = value => ((Number(value) - 1) % 12 + 12) % 12 + 1;
const clockMinute = value => (Number(value) % 60 + 60) % 60;
const clockSectorName = index => String.fromCharCode(97 + index);
const clockRequired = turn => Math.min(CLOCK_SECTORS, Math.max(1, turn));
const clockSelected = state => new Set(state.selected);
const clockLanded = value => Array.isArray(value) ? value : Number.isInteger(value) ? [value] : [];
const clockTau = Math.PI * 2;
const clockAngle = value => ((value % clockTau) + clockTau) % clockTau;

export function clockNeedleProgress(state) {
  if (state?.phase !== "move") return null;
  const remaining = Number(state.moveRemaining);
  if (!Number.isFinite(remaining) ||
      remaining > CLOCK_NEEDLE_START_TICKS ||
      remaining < CLOCK_NEEDLE_END_TICKS) return null;
  return Math.min(1, Math.max(0,
    (CLOCK_NEEDLE_START_TICKS - remaining) /
      (CLOCK_NEEDLE_START_TICKS - CLOCK_NEEDLE_END_TICKS)));
}

// The source advances each hand clockwise to its selected value, wrapping
// through twelve rather than taking the shortest path backwards.
export function clockNeedleAngles(state) {
  const hour = clockHour(state.hour);
  const minute = clockMinute(state.minute);
  const nextHour = clockHour(state.nextHour);
  const nextMinute = clockMinute(state.nextMinute);
  const hourSteps = (nextHour - hour + 12) % 12;
  const minuteSteps = ((nextMinute - minute + 60) % 60) / 5;
  const progress = clockNeedleProgress(state);
  const sweep = progress ?? (state?.phase === "move" || state?.phase === "setup" ? 0 : 1);
  return {
    hour: clockAngle(hour * Math.PI / 6 + hourSteps * Math.PI / 6 * sweep),
    minute: clockAngle(minute * Math.PI / 30 + minuteSteps * Math.PI / 6 * sweep),
    progress,
    rotating: progress !== null,
  };
}

function clockNeedleHit(angle, reach, position, geometry) {
  const dx = Number(position?.x) - Number(geometry.center_x);
  const dz = Number(position?.z) - Number(geometry.center_z);
  const distance = Math.hypot(dx, dz);
  if (!Number.isFinite(distance) || distance < CLOCK_NEEDLE_MIN_RADIUS || distance > reach) {
    return false;
  }
  const pointAngle = Math.atan2(dx, -dz);
  const difference = Math.atan2(Math.sin(pointAngle - angle), Math.cos(pointAngle - angle));
  const along = Math.cos(difference) * distance;
  const across = Math.abs(Math.sin(difference) * distance);
  return along >= CLOCK_NEEDLE_MIN_RADIUS && along <= reach &&
    across <= Math.max(CLOCK_NEEDLE_HALF_WIDTH, geometry.half_width ?? CLOCK_NEEDLE_HALF_WIDTH);
}

// Return the hands that sweep through a participant's current position. This
// is called from the venue runtime only while the source three-second window
// is active, so it never damages someone standing on a stationary hand.
export function clockNeedleContacts(state, position, geometry) {
  if (!geometry || clockNeedleProgress(state) === null) return [];
  const angles = clockNeedleAngles(state);
  const hits = [];
  if (clockNeedleHit(angles.hour, geometry.hour_reach ?? CLOCK_NEEDLE_HOUR_REACH,
    position, geometry)) hits.push("hour");
  if (clockNeedleHit(angles.minute, geometry.minute_reach ?? CLOCK_NEEDLE_MINUTE_REACH,
    position, geometry)) hits.push("minute");
  return hits;
}

function clockResetSelection(state, turn = state.turn) {
  return {
    ...state, turn, requiredSectors: clockRequired(turn),
    selected: [], cursor: 0, selectionComplete: false,
  };
}
function clockNextCursor(state, direction) {
  const selected = clockSelected(state);
  if (selected.size >= state.requiredSectors) return state.cursor;
  let cursor = state.cursor;
  for (let step = 0; step < CLOCK_SECTORS; step++) {
    cursor = (cursor + direction + CLOCK_SECTORS) % CLOCK_SECTORS;
    if (!selected.has(cursor)) return cursor;
  }
  return state.cursor;
}
function clockFinish(state, winner, reason) {
  return { ...state, phase: "finished", actor: null, winner, reason, resultRemaining: 0 };
}
function clockStartDrop(state) {
  return {
    ...state, phase: "drop", actor: state.actor,
    hour: state.nextHour, minute: state.nextMinute,
    moveRemaining: CLOCK_MOVE_END_TICKS,
    dropRemaining: CLOCK_DROP_TICKS, resultRemaining: 0, landed: [],
  };
}
function clockTimeout(state) {
  if (state.actor === null) return state;
  return {
    ...state, phase: "result", actor: null,
    winner: 1 - state.actor, reason: "timeout",
    resultRemaining: CLOCK_TIMEOUT_END_TICKS,
    last: { kind: "timeout", loser: state.actor, landed: [] },
  };
}
function clockNextTurn(state) {
  const turn = state.turn + 1;
  return clockResetSelection({
    ...state, phase: "setup", actor: 1 - state.actor,
    hour: state.nextHour, minute: state.nextMinute,
    nextHour: state.nextHour, nextMinute: state.nextMinute,
    setupRemaining: CLOCK_SETUP_TICKS,
    moveRemaining: CLOCK_MOVE_TICKS, dropRemaining: CLOCK_DROP_TICKS,
    resultRemaining: 0, landed: [], winner: null, reason: null,
  }, turn);
}
function clockLandingWinner(landed) {
  const unique = [...new Set(landed)].filter(seat => seat === 0 || seat === 1);
  return unique.length === 2 ? null : unique.length === 1 ? 1 - unique[0] : null;
}
export function createClock() {
  return {
    phase: "setup", turn: 1, actor: 0, requiredSectors: 1,
    hour: 1, minute: 0, nextHour: 1, nextMinute: 0,
    selected: [], cursor: 0, selectionComplete: false,
    setupRemaining: CLOCK_SETUP_TICKS, moveRemaining: CLOCK_MOVE_TICKS,
    dropRemaining: CLOCK_DROP_TICKS, resultRemaining: 0, landed: [],
    last: null, winner: null, reason: null,
  };
}
export function advanceClock(state, seat, action, value) {
  requireSeat(seat);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  requireTurn(state, seat, state.phase);
  if (state.phase === "setup") {
    if (action === "hour_plus" || action === "hour_minus") {
      return { ...state, nextHour: clockHour(state.nextHour + (action === "hour_plus" ? 1 : -1)) };
    }
    if (action === "minute_plus" || action === "minute_minus") {
      return { ...state, nextMinute: clockMinute(state.nextMinute + (action === "minute_plus" ? 5 : -5)) };
    }
    if (action !== "time_confirm") throw new Error("時刻設定の操作を選んでください。");
    return clockResetSelection({
      ...state, phase: "move",
      moveRemaining: CLOCK_MOVE_TICKS,
    });
  }
  if (state.phase !== "move") throw new Error("現在の局面では区画を選択できません。");
  if (action === "sector_cw" || action === "sector_ccw") {
    if (state.selectionComplete) throw new Error("必要な区画は確定済みです。");
    const direction = action === "sector_cw" ? 1 : -1;
    return { ...state, cursor: clockNextCursor(state, direction) };
  }
  if (action !== "sector_confirm") throw new Error("区画の操作を選んでください。");
  if (state.selectionComplete) throw new Error("必要な区画は確定済みです。");
  if (state.selected.includes(state.cursor)) {
    return { ...state, cursor: clockNextCursor(state, 1) };
  }
  const selected = [...state.selected, state.cursor];
  const complete = selected.length >= state.requiredSectors;
  const next = {
    ...state, selected, selectionComplete: complete,
    cursor: complete ? state.cursor : clockNextCursor({ ...state, selected }, 1),
  };
  return complete && next.moveRemaining <= CLOCK_MOVE_END_TICKS ? clockStartDrop(next) : next;
}
export function clockSystem(state, action, value) {
  if (state.phase === "finished") return state;
  if (action === "land") {
    if (state.phase !== "drop" && state.phase !== "result") return state;
    if (state.reason === "timeout") return state;
    const incoming = clockLanded(value).filter(seat => seat === 0 || seat === 1);
    if (!incoming.length) return state;
    const landed = [...new Set([...state.landed, ...incoming])];
    return {
      ...state, phase: "result", actor: null, landed,
      winner: clockLandingWinner(landed), reason: "landed",
      resultRemaining: state.resultRemaining > 0 ? state.resultRemaining : CLOCK_LANDING_END_TICKS,
      last: { kind: "landed", landed, droppedSectors: [...state.selected] },
    };
  }
  if (action === "timeout") return clockTimeout(state);
  if (action !== "tick") throw new Error("不正な内部操作です。");
  if (state.phase === "result") {
    const resultRemaining = state.resultRemaining - 1;
    return resultRemaining <= 0
      ? clockFinish(state, state.winner, state.reason)
      : { ...state, resultRemaining };
  }
  if (state.phase === "setup") {
    const setupRemaining = state.setupRemaining - 1;
    return setupRemaining <= 0 ? clockTimeout({ ...state, setupRemaining: 0 }) : { ...state, setupRemaining };
  }
  if (state.phase === "move") {
    const moveRemaining = state.moveRemaining - 1;
    if (moveRemaining <= 0) return clockTimeout({ ...state, moveRemaining: 0 });
    const next = { ...state, moveRemaining };
    return state.selectionComplete && moveRemaining <= CLOCK_MOVE_END_TICKS
      ? clockStartDrop(next) : next;
  }
  if (state.phase === "drop") {
    if (state.landed.length) return state;
    const dropRemaining = state.dropRemaining - 1;
    if (dropRemaining <= CLOCK_NEXT_TURN_TICKS) return clockNextTurn({ ...state, dropRemaining: 0 });
    return { ...state, dropRemaining };
  }
  return state;
}
export function clockView(state) {
  return {
    phase: state.phase, turn: state.turn, round: state.turn, actor: state.actor,
    requiredSectors: state.requiredSectors, selected: [...state.selected],
    selectedLabels: state.selected.map(clockSectorName),
    cursor: state.cursor, cursorLabel: String.fromCharCode(65 + state.cursor),
    selectionComplete: state.selectionComplete,
    hour: state.hour, minute: state.minute,
    nextHour: state.nextHour, nextMinute: state.nextMinute,
    setupRemaining: state.setupRemaining, moveRemaining: state.moveRemaining,
    dropRemaining: state.dropRemaining, resultRemaining: state.resultRemaining,
    floorOpen: state.phase === "drop" && state.dropRemaining <= CLOCK_FLOOR_OPEN_TICKS &&
      state.dropRemaining > CLOCK_FLOOR_RESTORE_TICKS && state.landed.length === 0,
    landed: [...state.landed],
    last: state.last && {
      ...state.last, landed: [...(state.last.landed ?? [])],
      droppedSectors: [...(state.last.droppedSectors ?? [])],
    },
    winner: state.winner, reason: state.reason,
  };
}
export const clockGame = Object.freeze({
  id: "clock", title: "ヘル・オ・クロック", seats: 2, minPlayers: 2, maxPlayers: 2,
  create: createClock, advance: advanceClock, view: clockView, systemAdvance: clockSystem,
});

// ---------------------------------------------------------------- Walker

export const WALKER_CELLS = 37;
export const WALKER_MAX_BOMBS = 5;
export const WALKER_WALK_TICKS = 6000;
export const WALKER_SETUP_TICKS = 6000;
export const WALKER_DODGE_TICKS = 100;
export const WALKER_FUSE_TICKS = 40;
export const WALKER_AFTERMATH_TICKS = 60;
export const WALKER_START_FLOORS = Object.freeze([21, 17]);
// Logical board coordinates, independent of any private world's placement.
// The row populations encode the game graph; no source-world coordinates are
// shipped with this reducer. Rendering reads its separate venue manifest.
const walkerRows = [2, 3, 4, 3, 4, 5, 4, 3, 4, 3, 2];
const walkerColors = ["red", "orange", "yellow", "lime", "green", "cyan",
  "light_blue", "blue", "purple", "magenta", "pink"];
let walkerFloor = 0;
export const WALKER_FLOOR_CELLS = Object.freeze(walkerRows.flatMap((count, row) =>
  Array.from({ length: count }, (_, column) => Object.freeze({
    floor: ++walkerFloor, x: ((count - 1) / 2 - column) * 6,
    z: -row * 2, color: walkerColors[row],
  }))));
const walkerCell = floor => WALKER_FLOOR_CELLS[floor - 1];
const walkerAdjacent = (left, right) => {
  const a = walkerCell(left);
  const b = walkerCell(right);
  if (!a || !b || left === right) return false;
  const dx = Math.abs(a.x - b.x);
  const dz = Math.abs(a.z - b.z);
  // walker/floor/place builds overlapping 5x5 tiles and walker/floor/check
  // accepts a step only on a tile touching the player's current tile. Two
  // tile centers therefore touch when each axis differs by at most four.
  return dx <= 4 && dz <= 4;
};
export function walkerFloorDistance(from, to) {
  if (from === to) return 0;
  const queue = [[from, 0]];
  const seen = new Set([from]);
  while (queue.length) {
    const [floor, distance] = queue.shift();
    for (let next = 1; next <= WALKER_CELLS; next++) {
      if (seen.has(next) || !walkerAdjacent(floor, next)) continue;
      if (next === to) return distance + 1;
      seen.add(next);
      queue.push([next, distance + 1]);
    }
  }
  return Infinity;
}
const walkerBombAllowed = (state, floor) => Number.isInteger(floor) &&
  floor >= 1 && floor <= WALKER_CELLS &&
  !state.deadFloors.includes(floor) &&
  !state.bombs.includes(floor) &&
  !state.positions.includes(floor);
function walkerNextBombCursor(state, start, direction, includeStart = false) {
  if (!Number.isInteger(start)) return -1;
  if (includeStart && walkerBombAllowed(state, start)) return start;
  for (let offset = 1; offset <= WALKER_CELLS; offset++) {
    let floor = ((start - 1 + direction * offset) % WALKER_CELLS) + 1;
    if (floor < 1) floor += WALKER_CELLS;
    if (walkerBombAllowed(state, floor)) return floor;
  }
  return -1;
}
const walkerFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, winner, reason,
});
function walkerRound(state) {
  const walker = state.round % 2 === 1 ? 0 : 1;
  const next = {
    ...state, phase: "walk", actor: walker, walker,
    breaker: 1 - walker, roundStart: [...state.positions],
    moveOrigins: [null, null], movedSteps: [0, 0], movedBomb: 0, bombQuota: 0,
    bombs: [], bombCursor: -1, bombVisible: false,
    walkRemaining: WALKER_WALK_TICKS, setupRemaining: 0, dodgeRemaining: 0,
    dodgeOrigin: null, dodgeMoved: false,
    fuseRemaining: 0, aftermathRemaining: 0,
    winner: null, reason: null,
  };
  next.bombCursor = walkerNextBombCursor(next, 1, 1, true);
  return next;
}
function walkerBeginBombSetup(state) {
  if (state.positions[0] === state.positions[1]) {
    return walkerRound({ ...state, round: state.round + 1, bombs: [] });
  }
  const quota = Math.min(WALKER_MAX_BOMBS, state.movedBomb);
  if (quota <= 0) return walkerRound({ ...state, round: state.round + 1, bombs: [] });
  return {
    ...state, phase: "bomb_setup", actor: state.breaker,
    bombs: [], bombQuota: quota, bombVisible: false,
    walkRemaining: 0, setupRemaining: WALKER_SETUP_TICKS,
    bombCursor: walkerNextBombCursor(state, 1, 1, true),
  };
}
function walkerStartDodge(state) {
  return {
    ...state, phase: "dodge", actor: state.walker,
    dodgeRemaining: WALKER_DODGE_TICKS,
    dodgeOrigin: state.positions[state.walker],
    dodgeMoved: false,
    bombVisible: true,
  };
}
function walkerStartFuse(state) {
  return {
    ...state, phase: "fuse", actor: null, bombVisible: true,
    fuseRemaining: WALKER_FUSE_TICKS,
  };
}
function walkerApplyWater(state, water, reason = "water") {
  if (!water[0] && !water[1]) return state;
  const changed = (water[0] && !state.water[0]) || (water[1] && !state.water[1]);
  if (!changed) return state;
  const winner = water[0] && water[1] ? null : water[0] ? 1 : 0;
  return {
    ...state, phase: "aftermath", actor: null, water: [...water],
    winner, reason, aftermathRemaining: WALKER_AFTERMATH_TICKS,
    bombVisible: state.bombs.length > 0,
  };
}
function walkerExplodeBombs(state) {
  const positions = [...state.positions];
  const bombs = [...state.bombs].sort((left, right) => left - right);
  const sameFloor = positions[0] === positions[1];
  const hit = sameFloor
    ? [false, false]
    : [bombs.includes(positions[0]), bombs.includes(positions[1])];
  const deadFloors = new Set(state.deadFloors);
  const droppedOrigin = state.dodgeMoved && Number.isInteger(state.dodgeOrigin) &&
    state.dodgeOrigin !== positions[1 - state.walker]
    ? state.dodgeOrigin
    : null;
  if (droppedOrigin !== null) deadFloors.add(droppedOrigin);
  for (const floor of bombs) {
    if ((hit[0] && floor === positions[0]) || (hit[1] && floor === positions[1])) {
      deadFloors.add(floor);
    }
  }
  const water = [
    state.water[0] || hit[0] || deadFloors.has(positions[0]),
    state.water[1] || hit[1] || deadFloors.has(positions[1]),
  ];
  const next = {
    ...state, phase: "aftermath", actor: null,
    deadFloors: [...deadFloors], water, bombVisible: true,
    aftermathRemaining: WALKER_AFTERMATH_TICKS,
    last: {
      walker: state.walker, breaker: state.breaker,
      positions, bombs, hit, water: [...water], round: state.round,
      deadFloors: [...deadFloors],
      dodgeOrigin: droppedOrigin, dodgeMoved: state.dodgeMoved,
    },
  };
  if (water[0] && water[1]) return { ...next, winner: null, reason: "water" };
  if (water[0]) return { ...next, winner: 1, reason: "water" };
  if (water[1]) return { ...next, winner: 0, reason: "water" };
  return next;
}
export function createWalker() {
  return walkerRound({
    phase: "walk", round: 1, actor: 0, walker: 0, breaker: 1,
    positions: [...WALKER_START_FLOORS], roundStart: [...WALKER_START_FLOORS],
    moveOrigins: [null, null], movedSteps: [0, 0], movedBomb: 0, bombQuota: 0,
    deadFloors: [], bombs: [], bombCursor: -1, bombVisible: false,
    walkRemaining: WALKER_WALK_TICKS, setupRemaining: 0, dodgeRemaining: 0,
    dodgeOrigin: null, dodgeMoved: false,
    fuseRemaining: 0, aftermathRemaining: 0,
    water: [false, false], last: null, winner: null, reason: null,
  });
}
export function advanceWalker(state, seat, action, value) {
  requireSeat(seat);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "position" || action === "walk") {
    if (!["walk", "dodge"].includes(state.phase)) throw new Error("現在は移動できません。");
    requireTurn(state, seat, state.phase);
    if (seat !== state.walker) throw new Error("ウォーカーの手番ではありません。");
    if (state.phase === "dodge" && state.dodgeMoved) {
      throw new Error("回避中に移動できるのは一度だけです。");
    }
    if (!Number.isInteger(value) || value < 1 || value > WALKER_CELLS) throw new Error("移動先の床が不正です。");
    if (state.deadFloors.includes(value)) throw new Error("落下済みの床には移動できません。");
    const from = state.positions[seat];
    if (value === from) return state;
    if (!walkerAdjacent(from, value)) throw new Error("隣接する床へ1マスずつ移動してください。");
    if (state.phase === "walk" && state.movedSteps[seat] >= 5) {
      throw new Error("1ラウンドに移動できるのは5マスまでです。");
    }
    const sharedBefore = state.positions[0] === state.positions[1];
    const positions = [...state.positions];
    positions[seat] = value;
    const movedSteps = [...state.movedSteps];
    movedSteps[seat] += 1;
    const moveOrigins = [...state.moveOrigins];
    if (moveOrigins[seat] === null) moveOrigins[seat] = from;
    const deadFloors = new Set(state.deadFloors);
    // game:walker/floor/move erases the departed tile immediately during the
    // walking phase. The dodge step is different: its origin is copied into
    // pos-now-bomb and game:walker/floor/move-2 only erases it at detonation.
    if (state.phase === "walk" && !sharedBefore && positions[1 - seat] !== from) {
      deadFloors.add(from);
    }
    const next = {
      ...state, positions, movedSteps, moveOrigins,
      movedBomb: state.phase === "walk" && !sharedBefore ? state.movedBomb + 1 : state.movedBomb,
      dodgeMoved: state.phase === "dodge" ? true : state.dodgeMoved,
      deadFloors: [...deadFloors],
    };
    if (movedSteps[seat] >= 5) return walkerBeginBombSetup(next);
    return next;
  }
  if (action === "stop") {
    requireTurn(state, seat, "walk");
    if (seat !== state.walker) throw new Error("ウォーカーの手番ではありません。");
    if (state.movedSteps[seat] < 1) throw new Error("1マス以上移動してから確定してください。");
    return walkerBeginBombSetup(state);
  }
  if (action === "bomb_prev" || action === "bomb_next") {
    requireTurn(state, seat, "bomb_setup");
    if (seat !== state.breaker) throw new Error("ブレイカーの手番ではありません。");
    const direction = action === "bomb_next" ? 1 : -1;
    const cursor = walkerNextBombCursor(state, state.bombCursor, direction);
    if (cursor < 0) throw new Error("爆弾を置ける床がありません。");
    return { ...state, bombCursor: cursor };
  }
  if (action === "bomb" && Number.isInteger(value)) {
    requireTurn(state, seat, "bomb_setup");
    if (seat !== state.breaker) throw new Error("ブレイカーの手番ではありません。");
    if (!walkerBombAllowed(state, value)) {
      throw new Error("落下済み・設置済み・プレイヤーが立つ床には設置できません。");
    }
    const bombs = [...state.bombs, value];
    if (bombs.length >= state.bombQuota) return walkerStartDodge({ ...state, bombs });
    return { ...state, bombs, bombCursor: walkerNextBombCursor({ ...state, bombs }, value, 1) };
  }
  if (action === "bomb_confirm" || action === "confirm") {
    requireTurn(state, seat, "bomb_setup");
    if (seat !== state.breaker) throw new Error("ブレイカーの手番ではありません。");
    if (!walkerBombAllowed(state, state.bombCursor)) throw new Error("爆弾を置ける床がありません。");
    const bombs = [...state.bombs, state.bombCursor];
    if (bombs.length >= state.bombQuota) return walkerStartDodge({ ...state, bombs });
    return { ...state, bombs, bombCursor: walkerNextBombCursor({ ...state, bombs }, state.bombCursor, 1) };
  }
  throw new Error("不正な操作です。");
}
export function walkerSystem(state, action, value) {
  if (state.phase === "finished") return state;
  if (action === "position") {
    const seat = value?.seat;
    const floor = value?.floor;
    if (!Number.isInteger(seat) || seat < 0 || seat > 1) throw new Error("プレイヤー番号が不正です。");
    return advanceWalker(state, seat, "position", floor);
  }
  if (action === "water") {
    if (!["walk", "bomb_setup", "dodge", "fuse", "aftermath"].includes(state.phase)) return state;
    const water = [state.water[0], state.water[1]];
    // Venue adapters may report either the flooded seat numbers or a two-entry
    // boolean mask. Normalize both forms here so a false entry can never be
    // mistaken for seat 0.
    const reported = Array.isArray(value) ? value : [value];
    const seats = reported.length === 2 && reported.every(entry => typeof entry === "boolean")
      ? [0, 1].filter(seat => reported[seat])
      : reported;
    for (const seat of seats) {
      if (!Number.isInteger(seat) || seat < 0 || seat > 1) throw new Error("水没プレイヤーが不正です。");
      water[seat] = true;
    }
    return walkerApplyWater(state, water);
  }
  if (action === "tick") {
    if (state.phase === "walk") {
      const remaining = Math.max(0, state.walkRemaining - 1);
      if (remaining > 0) return { ...state, walkRemaining: remaining };
      // game:walker/walk/player-* only sets $selected through walker-stop.
      // Merely moving fewer than five tiles does not stop the 6000-tick clock.
      return walkerApplyWater({ ...state, walkRemaining: 0 },
        [state.walker === 0, state.walker === 1], "timeout");
    }
    if (state.phase === "bomb_setup") {
      const remaining = Math.max(0, (state.setupRemaining ?? WALKER_SETUP_TICKS) - 1);
      if (remaining > 0) return { ...state, setupRemaining: remaining };
      return walkerApplyWater({ ...state, setupRemaining: 0 },
        [state.breaker === 0, state.breaker === 1], "setup_timeout");
    }
    if (state.phase === "dodge") {
      const remaining = Math.max(0, state.dodgeRemaining - 1);
      if (remaining > 0) return { ...state, dodgeRemaining: remaining };
      return walkerStartFuse({ ...state, dodgeRemaining: 0 });
    }
    if (state.phase === "fuse") {
      const remaining = Math.max(0, state.fuseRemaining - 1);
      return remaining > 0 ? { ...state, fuseRemaining: remaining } : walkerExplodeBombs(state);
    }
    if (state.phase === "aftermath") {
      const remaining = Math.max(0, state.aftermathRemaining - 1);
      if (remaining > 0) return { ...state, aftermathRemaining: remaining };
      if (!state.water[0] && !state.water[1]) return walkerRound({ ...state, round: state.round + 1 });
      return walkerFinish(state, state.winner, state.reason ?? "water");
    }
    return state;
  }
  if (action === "timeout") {
    if (state.phase === "walk") {
      return walkerApplyWater(state, [state.walker === 0, state.walker === 1], "timeout");
    }
    if (state.phase === "dodge") return walkerStartFuse(state);
    // The source keeps the breaker's 6000-tick selection loop active until
    // every earned bomb is placed. A timeout ends the breaker's turn; it must
    // not silently place bombs on the breaker's behalf.
    if (state.phase === "bomb_setup") {
      return walkerApplyWater(state,
        [state.breaker === 0, state.breaker === 1], "setup_timeout");
    }
    return state;
  }
  throw new Error("不正な内部操作です。");
}
export function walkerView(state) {
  return {
    phase: state.phase, round: state.round, actor: state.actor,
    walker: state.walker, breaker: state.breaker, positions: [...state.positions],
    roundStart: [...state.roundStart], movedSteps: [...state.movedSteps],
    movedBomb: state.movedBomb,
    bombCount: state.bombs.length, bombQuota: state.bombQuota,
    bombsPlaced: state.bombs.length, bombCursor: state.bombCursor,
    bombVisible: state.bombVisible,
    bombPositions: state.bombVisible ? [...state.bombs] : [],
    deadFloors: [...state.deadFloors], water: [...state.water],
    walkRemaining: state.walkRemaining, dodgeRemaining: state.dodgeRemaining,
    setupRemaining: state.setupRemaining,
    dodgeOrigin: state.dodgeOrigin, dodgeMoved: state.dodgeMoved,
    fuseRemaining: state.fuseRemaining,
    aftermathRemaining: state.aftermathRemaining,
    last: state.last && {
      ...state.last, positions: [...state.last.positions],
      bombs: [...state.last.bombs], hit: [...state.last.hit],
      water: [...(state.last.water ?? [])],
      deadFloors: [...(state.last.deadFloors ?? [])],
    },
    winner: state.winner, reason: state.reason,
  };
}
export function walkerViewForPlayer(state, seat) {
  requireSeat(seat);
  return { ...walkerView(state), self: { seat,
    bombs: seat === state.breaker ? [...state.bombs] : [],
    bombCursor: seat === state.breaker ? state.bombCursor : null } };
}
export const walkerGame = Object.freeze({
  id: "walker", title: "ザ・クレバス・ウォーカー", seats: 2,
  create: createWalker, advance: advanceWalker, view: walkerView, viewForPlayer: walkerViewForPlayer,
  systemAdvance: walkerSystem,
});

// ---------------------------------------------------------------- Jewel Garden

export const GARDEN_DEVICE_BASE = 5;
export const GARDEN_DEVICE_CAP = 1280;
export const GARDEN_WIN_JEWELS = 100;
export const GARDEN_WIN_HOLD_SECONDS = 5;
export const GARDEN_DROP_DELAY_SECONDS = 3;
const GARDEN_ACTION = Object.freeze(["switch", "double", "open"]);
const gardenFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, winner, reason, dropRemaining: 0,
});
const gardenDrop = (state, loser, reason) => ({
  ...state, phase: "drop", actor: null, choices: [null, null], pendingWin: [null, null],
  winner: loser === null ? null : 1 - loser,
  loser, reason, dropRemaining: GARDEN_DROP_DELAY_SECONDS,
});
function gardenRefreshPending(state) {
  const pending = state.pendingWin.map((remaining, seat) =>
    state.loads[seat] === GARDEN_WIN_JEWELS
      ? (remaining ?? GARDEN_WIN_HOLD_SECONDS)
      : null);
  return { ...state, pendingWin: pending };
}
function gardenNextRound(state) {
  return gardenRefreshPending({
    ...state,
    phase: "choose", actor: 0,
    round: state.round + 1, choices: [null, null],
    changeAvailable: [!state.changeUsed[0], !state.changeUsed[1]],
    changeUsed: [false, false],
  });
}
function gardenResolve(state) {
  let valve = state.valve;
  let device = state.device;
  let deviceNow = state.deviceNow;
  const loads = [...state.loads];
  const choices = state.choices.map(choice => choice.action);
  const switched = choices.filter(action => action === "switch").length;
  // game:garden/open/change swaps the existing valve (1 -> 2, 2 -> 1),
  // regardless of which player selected change. Two changes cancel.
  if (switched === 1) valve = 1 - valve;
  const doubles = choices.filter(action => action === "double").length;
  if (doubles > 0) {
    device = Math.min(GARDEN_DEVICE_CAP, device * (doubles === 2 ? 4 : 2));
    // open/double.mcfunction keeps adding five jewels per tick until the
    // physical count reaches the multiplied target. No other action can
    // interrupt that loop, so resolve the completed refill atomically here.
    deviceNow = device;
  }
  const opened = choices.includes("open");
  const released = opened ? deviceNow : 0;
  if (opened) {
    loads[valve] += deviceNow;
    device = GARDEN_DEVICE_BASE;
    deviceNow = GARDEN_DEVICE_BASE;
  }
  const changeUsed = [
    state.changeUsed[0] || choices[0] === "switch",
    state.changeUsed[1] || choices[1] === "switch",
  ];
  return gardenNextRound({
    ...state, valve, device, deviceNow, loads, changeUsed,
    last: {
      round: state.round, choices, valve, device, deviceNow, opened, released,
    },
  });
}
export function createGarden() {
  return {
    phase: "choose", round: 1, actor: 0, choices: [null, null],
    valve: 1, device: GARDEN_DEVICE_BASE, deviceNow: GARDEN_DEVICE_BASE,
    loads: [0, 0], changeAvailable: [true, true], changeUsed: [false, false],
    pendingWin: [null, null], last: null, winner: null, reason: null,
    loser: null, dropRemaining: 0,
  };
}
export function advanceGarden(state, seat, action, value) {
  requireSeat(seat);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (state.phase === "drop" || state.phase === "result") {
    throw new Error("落下判定中は操作できません。");
  }
  if (action === "pick") {
    if (state.loads[seat] <= 0) throw new Error("宝石箱は空です。");
    const loads = [...state.loads];
    loads[seat] -= 1;
    return gardenRefreshPending({ ...state, loads });
  }
  if (action !== "choose") throw new Error("不正な操作です。");
  if (state.phase !== "choose") throw new Error("現在は選択できません。");
  if (state.choices[seat] !== null) throw new Error("このラウンドは既に選択済みです。");
  if (!GARDEN_ACTION.includes(value)) throw new Error("切替・宝石倍化・開放を選んでください。");
  if (value === "switch" && !state.changeAvailable[seat]) {
    throw new Error("切替は使用した翌ラウンドまで使えません。");
  }
  const choices = [...state.choices];
  choices[seat] = { action: value };
  if (choices[0] !== null && choices[1] !== null) return gardenResolve({ ...state, choices });
  return { ...state, choices, actor: choices[0] === null ? 0 : 1 };
}
export function gardenSystem(state, action, value) {
  if (state.phase === "finished") return state;
  if (action === "timeout") {
    if (state.choices[0] === null && state.choices[1] === null) {
      return gardenDrop(state, null, "timeout_both");
    }
    return gardenDrop(state, state.actor, "timeout");
  }
  if (action === "land") {
    if (state.phase !== "drop") throw new Error("落下判定中ではありません。");
    if (state.dropRemaining > 0) throw new Error("落下用の床がまだ開いていません。");
    const landed = value === "both" ? [0, 1] : [Number(value)];
    if (!landed.every(seat => seat === 0 || seat === 1)) {
      throw new Error("落下した席が不正です。");
    }
    if (state.loser !== null && !landed.includes(state.loser)) {
      throw new Error("開いている床と落下した席が一致しません。");
    }
    // The source checks player-1 before player-2. If both occupy brown wool on
    // the same tick, player-1's landing resolves first and player-2 wins.
    const loser = state.loser ?? landed[0];
    return {
      ...state, phase: "result", actor: null, winner: 1 - loser, loser,
      dropRemaining: GARDEN_WIN_HOLD_SECONDS,
    };
  }
  if (action === "tick") {
    const seconds = Math.max(1, Math.min(60, Math.floor(Number(value) || 1)));
    if (state.phase === "result") {
      const remaining = Math.max(0, state.dropRemaining - seconds);
      if (remaining === 0) return gardenFinish(state, state.winner, state.reason);
      return { ...state, dropRemaining: remaining };
    }
    // The source starts drop/* one second after the trigger and opens the bay
    // forty ticks later. Until this countdown reaches zero the landing check is
    // deliberately inactive and the physical floor stays closed.
    if (state.phase === "drop") {
      return { ...state, dropRemaining: Math.max(0, state.dropRemaining - seconds) };
    }
    const refreshed = gardenRefreshPending(state);
    const pendingWin = refreshed.pendingWin.map(remaining =>
      remaining === null ? null : Math.max(0, remaining - seconds));
    const expired = pendingWin
      .map((remaining, seat) => remaining === 0 ? seat : -1)
      .filter(seat => seat >= 0);
    if (expired.length > 0) {
      const loser = expired.length === 1 ? 1 - expired[0] : 1;
      return gardenDrop({ ...refreshed, pendingWin }, loser,
        expired.length === 1 ? "box_100" : "both_100");
    }
    return { ...refreshed, pendingWin };
  }
  throw new Error("不正な内部操作です。");
}
export function gardenView(state) {
  return {
    phase: state.phase, round: state.round, actor: state.actor,
    choices: state.phase === "choose" ? state.choices.map(() => null) : copyChoices(state.choices),
    valve: state.valve, device: state.device, deviceNow: state.deviceNow,
    loads: [...state.loads], changeAvailable: [...state.changeAvailable],
    pendingWin: [...state.pendingWin], last: state.last && {
      ...state.last, choices: [...state.last.choices],
    },
    winner: state.winner, reason: state.reason,
    loser: state.loser ?? null, dropRemaining: state.dropRemaining ?? 0,
  };
}
export function gardenVenuePresentation(state, names = ["1P", "2P"]) {
  if (!state) {
    return {
      total: "参加者待ち",
      chests: ["1P宝石箱 0", "2P宝石箱 0"],
      valve: "弁 2P側",
      reception: "参加者待ち",
    };
  }
  const pending = state.pendingWin.map(remaining =>
    remaining === null ? "" : ` / あと${remaining}秒`);
  const settled = state.phase === "finished" || state.phase === "result";
  const result = settled
    ? state.winner === null ? "引き分け" : `${names[state.winner]}の勝利`
    : state.phase === "drop"
      ? state.dropRemaining > 0 ? `落下開始まで${state.dropRemaining}秒` : "床開放・落下判定"
      : state.phase;
  return {
    total: `第${state.round}ラウンド / ${result}\n装置 ${state.device}（実数 ${state.deviceNow}）\n1P ${state.loads[0]} / 2P ${state.loads[1]}\n弁 ${state.valve + 1}P側`,
    chests: [
      `1P宝石箱 ${state.loads[0]}${pending[0]}`,
      `2P宝石箱 ${state.loads[1]}${pending[1]}`,
    ],
    valve: `弁 → ${state.valve + 1}P側`,
    reception: state.phase === "finished"
      ? `終了：${result}`
      : state.phase === "result" ? `${result} / 終了まで${state.dropRemaining}秒`
      : state.phase === "drop"
        ? state.dropRemaining > 0 ? `落下開始まで${state.dropRemaining}秒` : "床開放・着地待ち"
        : `第${state.round}ラウンド ${state.phase}`,
  };
}
export function gardenViewForPlayer(state, seat) {
  requireSeat(seat);
  return { ...gardenView(state), self: { seat,
    choice: state.choices[seat] && { ...state.choices[seat] } } };
}
export const gardenGame = Object.freeze({
  id: "garden", title: "ジュエルシャワー・ハイガーデン", seats: 2,
  create: createGarden, advance: advanceGarden, view: gardenView, viewForPlayer: gardenViewForPlayer,
  systemAdvance: gardenSystem,
});

// ---------------------------------------------------------------- Rabbit Hole
//
// Source: data/junket-private/source-ver1.09/.../game/function/rabbit.
// The board is 12 columns x 5 rows. Tile 1 is the north-west corner; the row
// runs along X and the column along Z in the source display entities. A turn
// allows the current tile or an orthogonal move of one to three tiles. The
// oni places one trap per surviving escaper (maximum five), the escape side
// moves, traps are revealed, floors drop, and only then are goals checked.
export const RABBIT_COLUMNS = 12;
export const RABBIT_ROWS = 5;
export const RABBIT_FLOORS = RABBIT_COLUMNS * RABBIT_ROWS;
export const RABBIT_GOALS = Object.freeze([12, 24, 36, 48, 60]);
export const RABBIT_CPU_START = Object.freeze([1, 13, 37, 49]);
export const RABBIT_CPU_NAMES = Object.freeze(["りょうたん", "チェダー", "ワッホイ", "チーくん"]);
export const RABBIT_HUMAN_START = 25;
export const RABBIT_TRAP_LIMIT = 5;
export const RABBIT_ACTION_TICKS = 6000;
// Source open/open.mcfunction snapshots the planned moves at 5980 (20 ticks
// after the open sequence starts), moves tokens for 60 ticks (5921..5980), and
// waits another 20 ticks before revealing traps at 5900.
export const RABBIT_TRAVEL_TICKS = 20;
export const RABBIT_MOVE_TICKS = 60;
export const RABBIT_SETTLE_TICKS = 20;
export const RABBIT_REVEAL_TICKS = 20;
export const RABBIT_DROP_TICKS = 30;
// open/goal, timeup/player-2 and timeup/player-1 announce the result at
// time=5980, open the physical floor at 5960, then let game:rabbit/end run
// five seconds later. A trapped-side wipe in wincheck instead schedules the
// five-second end immediately, without opening floor 61 or every floor.
export const RABBIT_RESULT_TICKS = 120;
export const RABBIT_RESULT_OPEN_TICKS = 100;
// open/wincheck.mcfunction ends a wiped escape side after five seconds and
// does not run either floor-opening animation.
export const RABBIT_WIPE_RESULT_TICKS = 100;

// Source set/trap/first.mcfunction, set/trap/determine.mcfunction.
export const RABBIT_BASE_SYMBOLS = Object.freeze(["あ", "か", "さ", "た", "な"]);
export const RABBIT_OCCUPIED_SYMBOLS = Object.freeze(["う", "く", "す", "つ", "ぬ"]);
export const RABBIT_TRAP_SYMBOLS = Object.freeze(["お", "こ", "そ", "と", "の"]);
const RABBIT_TRAP_MARKERS = new Set([...RABBIT_TRAP_SYMBOLS, "1", "2", "3", "4", "5"]);

const rabbitRandom = seed => ((seed * 1664525 + 1013904223) >>> 0);
const rabbitIsTile = value => Number.isInteger(value) && value >= 1 && value <= RABBIT_FLOORS;
const rabbitRow = tile => Math.floor((tile - 1) / RABBIT_COLUMNS);
const rabbitColumn = tile => (tile - 1) % RABBIT_COLUMNS;
const rabbitAlive = state => state.participants.filter(participant =>
  participant.alive && participant.id !== "p2");
const rabbitParticipant = (state, id) => state.participants.find(participant => participant.id === id);
const rabbitTrapQuota = state => Math.min(RABBIT_TRAP_LIMIT, state.participants.filter(
  participant => participant.alive && participant.id !== "p2").length);
const rabbitTrapVisible = phase => ["reveal", "drop", "result", "finished"].includes(phase);

function rabbitRoundFloorSymbols(state) {
  const symbols = Array.from({ length: RABBIT_FLOORS }, (_, index) =>
    RABBIT_BASE_SYMBOLS[Math.floor(index / RABBIT_COLUMNS)]);
  for (const participant of state.participants ?? []) {
    if (!participant.alive || !rabbitIsTile(participant.position)) continue;
    symbols[participant.position - 1] = RABBIT_OCCUPIED_SYMBOLS[rabbitRow(participant.position)];
  }
  return symbols;
}
function rabbitStoredFloorSymbols(state) {
  if (Array.isArray(state.floorCategories) && state.floorCategories.length === RABBIT_FLOORS) {
    return [...state.floorCategories];
  }
  return rabbitRoundFloorSymbols(state);
}
function rabbitFloorSymbolsForTrap(state, value) {
  const symbols = rabbitStoredFloorSymbols(state);
  const row = rabbitRow(value);
  const occupied = (state.participants ?? []).some(participant =>
    participant.alive && participant.position === value);
  symbols[value - 1] = occupied ? String(row + 1) : RABBIT_TRAP_SYMBOLS[row];
  return symbols;
}
function rabbitFloorSymbolsForRoundTraps(state) {
  const symbols = rabbitRoundFloorSymbols(state);
  for (const trap of state.traps ?? []) {
    if (!rabbitIsTile(trap)) continue;
    const row = rabbitRow(trap);
    const occupied = (state.participants ?? []).some(participant =>
      participant.alive && participant.position === trap);
    symbols[trap - 1] = occupied ? String(row + 1) : RABBIT_TRAP_SYMBOLS[row];
  }
  return symbols;
}
export function rabbitFloorCategories(state) {
  return rabbitStoredFloorSymbols(state);
}

function rabbitCanMoveTo(state, participant, value) {
  if (!rabbitIsTile(value)) return false;
  const fromRow = rabbitRow(participant.position);
  const fromColumn = rabbitColumn(participant.position);
  const toRow = rabbitRow(value);
  const toColumn = rabbitColumn(value);
  if (fromRow !== toRow && fromColumn !== toColumn) return false;
  if (Math.abs(fromRow - toRow) + Math.abs(fromColumn - toColumn) > 3) return false;
  return !rabbitAlive(state).some(other =>
    other.id !== participant.id && other.position === value);
}
function rabbitMoves(state, participant) {
  const row = rabbitRow(participant.position);
  const column = rabbitColumn(participant.position);
  const moves = [];
  for (let deltaRow = -3; deltaRow <= 3; deltaRow++) {
    for (let deltaColumn = -3; deltaColumn <= 3; deltaColumn++) {
      if (deltaRow !== 0 && deltaColumn !== 0) continue;
      if (Math.abs(deltaRow) + Math.abs(deltaColumn) > 3) continue;
      const nextRow = row + deltaRow;
      const nextColumn = column + deltaColumn;
      if (nextRow < 0 || nextRow >= RABBIT_ROWS ||
          nextColumn < 0 || nextColumn >= RABBIT_COLUMNS) continue;
      const value = nextRow * RABBIT_COLUMNS + nextColumn + 1;
      if (rabbitCanMoveTo(state, participant, value)) moves.push(value);
    }
  }
  return moves;
}
export function rabbitLegalMoves(state, id = "p1") {
  const participant = rabbitParticipant(state, id);
  return participant ? rabbitMoves(state, participant) : [];
}
export function rabbitLegalTrapFloors(state) {
  // The source cursor skips a floor whose display is already a trap letter
  // (お/こ/そ/と/の) or its occupied-trap numeric marker (row 1..5). A floor
  // occupied by a participant is still a legal trap target.
  const symbols = rabbitStoredFloorSymbols(state);
  const trapped = new Set((state.traps ?? []).filter(rabbitIsTile));
  return Array.from({ length: RABBIT_FLOORS }, (_, index) => index + 1)
    .filter(tile => !trapped.has(tile) && !RABBIT_TRAP_MARKERS.has(symbols[tile - 1]));
}
export function rabbitChooseCpuMove(state, participant) {
  const random = rabbitRandom(state.seed);
  const moves = rabbitMoves(state, participant);
  if (!moves.length) return { seed: random, value: participant.position };
  if (participant.haveGuard) {
    return { seed: random, value: moves[random % moves.length] };
  }
  // move/cpu.mcfunction chooses a two- or three-tile target ahead (50/50),
  // then picks the nearest usable floor to that target. The target may be
  // outside the row at the goal edge, so the nearest legal floor is selected.
  const targetRow = rabbitRow(participant.position);
  const targetColumn = rabbitColumn(participant.position) + (random % 2 === 0 ? 3 : 2);
  let best = moves[0];
  let bestDistance = Infinity;
  for (const value of moves) {
    const distance = Math.hypot(rabbitRow(value) - targetRow, rabbitColumn(value) - targetColumn);
    if (distance < bestDistance) {
      best = value;
      bestDistance = distance;
    }
  }
  return { seed: random, value: best };
}
function rabbitFinish(state, winnerTeam, reason) {
  return {
    ...state, phase: "result", actor: null,
    winner: winnerTeam === 0 ? 0 : 1,
    winnerTeam, reason,
    phaseRemaining: reason === "全滅" ? RABBIT_WIPE_RESULT_TICKS : RABBIT_RESULT_TICKS,
    oniFloorOpen: false, allFloorsOpen: false,
  };
}
function rabbitEnd(state) {
  return {
    ...state, phase: "finished", actor: null, phaseRemaining: 0,
    oniFloorOpen: false, allFloorsOpen: false,
  };
}
function rabbitResetGuards(state) {
  return {
    ...state,
    participants: state.participants.map(participant => ({ ...participant, guard: false })),
  };
}
function rabbitStartNextRound(state) {
  const next = rabbitResetGuards(state);
  return {
    ...next, phase: "trap", round: state.round + 1, actor: 1,
    traps: [], plannedMoves: [], dropped: [], guarded: [], falling: [],
    dropFloors: [], guardedFloors: [], waterHits: [],
    floorCategories: rabbitRoundFloorSymbols(next),
    trapQuota: Math.min(RABBIT_TRAP_LIMIT, rabbitAlive(next).length),
    phaseRemaining: RABBIT_ACTION_TICKS,
  };
}
function rabbitRevealFloors(state) {
  const trapSet = new Set(state.traps);
  const guardedFloors = state.participants
    .filter(participant => participant.alive && participant.guard &&
      trapSet.has(participant.position))
    .map(participant => participant.position);
  const guardedSet = new Set(guardedFloors);
  const dropFloors = state.traps.filter(value => !guardedSet.has(value));
  const dropSet = new Set(dropFloors);
  const falling = state.participants
    .filter(participant => participant.alive && dropSet.has(participant.position))
    .map(participant => participant.id);
  const guarded = state.participants
    .filter(participant => participant.alive && guardedSet.has(participant.position))
    .map(participant => participant.id);
  return {
    ...state,
    phase: "drop", actor: null, phaseRemaining: RABBIT_DROP_TICKS,
    dropFloors, guardedFloors: [...new Set(guardedFloors)], falling,
    dropped: [], guarded, waterHits: [],
    last: {
      ...(state.last ?? {}),
      traps: [...state.traps], dropFloors, guardedFloors: [...new Set(guardedFloors)],
      falling, dropped: [], guarded,
    },
  };
}

function rabbitApplyLanding(state, landed) {
  // The source does not eliminate anyone while the trap is revealed. The
  // physical floor is opened first, then losecheck polls the water block at
  // every participant position. Only those observations may eliminate here.
  const aliveIds = new Set(state.participants
    .filter(participant => participant.alive)
    .map(participant => participant.id));
  const landedIds = new Set([
    ...(Array.isArray(state.waterHits) ? state.waterHits.filter(value => aliveIds.has(value)) : []),
    ...(Array.isArray(landed) ? landed : []),
  ].filter(value => typeof value === "string" && aliveIds.has(value)));
  const participants = state.participants.map(participant =>
    participant.alive && landedIds.has(participant.id)
      ? { ...participant, alive: false }
      : participant);
  const dropped = state.participants
    .filter(participant => participant.alive && landedIds.has(participant.id))
    .map(participant => participant.id);
  const alive = participants.filter(participant => participant.alive);
  // rabbit/start.mcfunction keeps p2 as the oni seat and gives the five
  // escapers the IDs p1, p3, p4, p5 and p6. The oni is not part of the
  // player-survive quota and its floor is opened only after the result, so a
  // p2 water sighting must never be folded into the escape roster here.
  const winners = alive.filter(participant => RABBIT_GOALS.includes(participant.position));
  const winnerTeam = alive.length === 0 ? 1 : winners.length > 0 ? 0 : null;
  const reason = alive.length === 0 ? "全滅" : winners.length > 0 ? "goal" : null;
  const last = {
    ...(state.last ?? {}), dropped, guarded: [...(state.guarded ?? [])],
    goals: winners.map(participant => participant.id),
    alive: alive.map(participant => participant.id),
  };
  if (winnerTeam === null) {
    return rabbitStartNextRound({
      ...state, participants, dropped, falling: [], waterHits: [], phaseRemaining: 0, last,
    });
  }
  return rabbitFinish({
    ...state, participants, dropped, falling: [], waterHits: [], last,
  }, winnerTeam, reason);
}
function rabbitPlaceTrap(state, value) {
  if (!rabbitIsTile(value)) throw new Error("1〜60の床を選んでください。");
  // trap/determine advances the cursor past a floor whose display is already
  // a trap letter, so the same floor cannot receive a second trap.
  if (state.traps.includes(value)) throw new Error("その床には既に罠があります。");
  if (state.traps.length >= (state.trapQuota ?? rabbitTrapQuota(state))) {
    throw new Error(`このラウンドの罠は${state.trapQuota}個です。`);
  }
  const traps = [...state.traps, value];
  let next = {
    ...state, traps, floorCategories: rabbitFloorSymbolsForTrap(state, value),
    last: { ...(state.last ?? {}), traps: [...traps] },
  };
  if (traps.length < (state.trapQuota ?? rabbitTrapQuota(state))) return next;

  // Source move/guard.mcfunction: each round any remaining CPU guard protects
  // the human escaper, and one CPU guard is consumed.
  const candidates = next.participants.filter(participant =>
    participant.alive && !participant.human && participant.haveGuard);
  if (candidates.length) {
    // Source move/guard.mcfunction: enemymode 1 consumes 6,5,4,3 and
    // enemymode 2 consumes 3,4,5,6. This is deterministic, not a random
    // candidate, and exactly one remaining guard is spent per round.
    const order = next.enemyMode === 2
      ? ["p3", "p4", "p5", "p6"]
      : ["p6", "p5", "p4", "p3"];
    const chosen = order.map(id => candidates.find(participant => participant.id === id))
      .find(Boolean);
    next = {
      ...next,
      participants: next.participants.map(participant => {
        if (participant.id === chosen.id) return { ...participant, haveGuard: false };
        if (participant.human) return { ...participant, guard: true };
        return participant;
      }),
      last: { ...(next.last ?? {}), guardUsedBy: chosen.id },
    };
  }
  return {
    ...next, phase: "move", actor: 0,
    phaseRemaining: RABBIT_ACTION_TICKS,
  };
}
function rabbitConfirmMove(state, value) {
  const human = rabbitParticipant(state, "p1");
  if (!human?.alive) throw new Error("脱出者は脱落しています。");
  if (!rabbitCanMoveTo(state, human, value)) {
    throw new Error("現在地または直交1〜3マスの空いている床を選んでください。");
  }
  let working = {
    ...state,
    participants: state.participants.map(participant =>
      participant.id === "p1" ? { ...participant, position: value } : participant),
  };
  const plannedMoves = [{ id: "p1", from: human.position, to: value }];
  for (const id of ["p3", "p4", "p5", "p6"]) {
    const participant = rabbitParticipant(working, id);
    if (!participant?.alive) continue;
    const choice = rabbitChooseCpuMove(working, participant);
    working = {
      ...working,
      seed: choice.seed,
      participants: working.participants.map(candidate =>
        candidate.id === id ? { ...candidate, position: choice.value } : candidate),
    };
    plannedMoves.push({ id, from: participant.position, to: choice.value });
  }
  return {
    ...working, phase: "travel", actor: null,
    plannedMoves, phaseRemaining: RABBIT_TRAVEL_TICKS,
    last: { ...(working.last ?? {}), plannedMoves },
  };
}

export function createRabbit() {
  const seed = (Date.now() ^ 0x5a17) >>> 0;
  const participants = [
    {
      id: "p1", name: "脱出者", human: true, alive: true,
      position: RABBIT_HUMAN_START, haveGuard: true, guard: false,
    },
    ...RABBIT_CPU_START.map((position, index) => ({
      id: `p${index + 3}`, name: RABBIT_CPU_NAMES[index], human: false, alive: true,
      position, haveGuard: true, guard: false,
    })),
  ];
  return {
    phase: "trap", round: 1, actor: 1,
    participants,
    floorCategories: rabbitRoundFloorSymbols({ participants }),
    traps: [], plannedMoves: [], dropped: [], guarded: [],
    waterHits: [],
    trapQuota: RABBIT_TRAP_LIMIT, seed,
    enemyMode: (seed % 2) + 1,
    phaseRemaining: RABBIT_ACTION_TICKS,
    last: null, winner: null, winnerTeam: null, reason: null,
  };
}
export function advanceRabbit(state, seat, action, value) {
  requireSeat(seat);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "trap") {
    requireTurn(state, seat, "trap");
    if (seat !== 1) throw new Error("オニの手番ではありません。");
    return rabbitPlaceTrap(state, value);
  }
  if (action === "move") {
    requireTurn(state, seat, "move");
    if (seat !== 0) throw new Error("脱出側の手番ではありません。");
    return rabbitConfirmMove(state, value);
  }
  if (action === "guard") {
    requireTurn(state, seat, "move");
    if (seat !== 0) throw new Error("お守りを使えるのは脱出側です。");
    const human = rabbitParticipant(state, "p1");
    if (!human?.haveGuard) throw new Error("使用できるお守りがありません。");
    const candidates = state.participants.filter(participant =>
      participant.alive && !participant.human);
    const target = typeof value === "string"
      ? candidates.find(participant => participant.id === value)
      : candidates[value];
    if (!target) throw new Error("お守りの対象が不正です。");
    if (target.guard) throw new Error("その仲間は既に守られています。");
    return {
      ...state,
      participants: state.participants.map(participant => {
        if (participant.id === "p1") return { ...participant, haveGuard: false };
        if (participant.id === target.id) return { ...participant, guard: true };
        return participant;
      }),
      last: { ...(state.last ?? {}), guardTarget: target.id },
    };
  }
  throw new Error("不正な操作です。");
}
export function rabbitSystem(state, action, value) {
  if (state.phase === "finished") return state;
  if (action === "position") {
    if (state.phase !== "move") return state;
    const tile = Number(value?.tile ?? value);
    if (!rabbitIsTile(tile)) return state;
    const human = rabbitParticipant(state, "p1");
    if (!human?.alive) return state;
    return rabbitConfirmMove(state, tile);
  }
  if (action === "observe") {
    if (state.phase !== "drop") return state;
    // losecheck.mcfunction samples every rabbit-player position each tick,
    // not only the floors it calculated as falling. The solid guarded and
    // safe floors make an accidental water sighting impossible, while this
    // also catches a participant whose physical token precedes the state.
    const alive = new Set(state.participants
      .filter(participant => participant.alive)
      .map(participant => participant.id));
    const observed = Array.isArray(value)
      ? value.filter(id => typeof id === "string" && alive.has(id))
      : [];
    if (!observed.length) return state;
    return {
      ...state,
      waterHits: [...new Set([...(state.waterHits ?? []), ...observed])],
    };
  }
  if (action === "land") {
    if (state.phase !== "drop") return state;
    return rabbitApplyLanding(state, value);
  }
  if (action === "timeout") {
    if (state.phase === "trap") return rabbitFinish(state, 0, "trap_timeout");
    if (state.phase === "move") return rabbitFinish(state, 1, "move_timeout");
    return state;
  }
  if (action !== "tick") throw new Error("不正な内部操作です。");
  if (!["trap", "move", "travel", "advance", "settle", "reveal", "drop", "result"].includes(state.phase)) return state;
  const remaining = Math.max(0, (state.phaseRemaining ?? 0) - 1);
  if (state.phase === "result") {
    let next = { ...state, phaseRemaining: remaining };
    if (remaining === RABBIT_RESULT_OPEN_TICKS) {
      if (state.reason === "move_timeout") {
        next = { ...next, allFloorsOpen: true };
      } else if (state.reason === "goal" || state.reason === "trap_timeout") {
        next = { ...next, oniFloorOpen: true };
      }
    }
    return remaining === 0 ? rabbitEnd(next) : next;
  }
  if (remaining > 0) return { ...state, phaseRemaining: remaining };
  if (state.phase === "trap") return rabbitFinish(state, 0, "trap_timeout");
  if (state.phase === "move") return rabbitFinish(state, 1, "move_timeout");
  if (state.phase === "travel") {
    return { ...state, phase: "advance", phaseRemaining: RABBIT_MOVE_TICKS };
  }
  if (state.phase === "advance") {
    return { ...state, phase: "settle", phaseRemaining: RABBIT_SETTLE_TICKS };
  }
  if (state.phase === "settle") {
    return { ...state, phase: "reveal", phaseRemaining: RABBIT_REVEAL_TICKS };
  }
  if (state.phase === "reveal") return rabbitRevealFloors({ ...state, phaseRemaining: 0 });
  // The drop phase always consumes the source's 30-tick window. The final
  // tick must store zero so the venue observer can resolve the drop instead
  // of seeing the previous phaseRemaining value forever.
  if (state.phase === "drop") return { ...state, phaseRemaining: 0 };
  throw new Error("不正なRabbitフェーズです。");
}
export function rabbitView(state) {
  const reveal = rabbitTrapVisible(state.phase);
  const floorCategories = reveal
    ? rabbitFloorSymbolsForRoundTraps(state)
    : rabbitRoundFloorSymbols(state);
  const last = state.last ? { ...state.last } : null;
  if (last && !reveal) {
    delete last.traps;
    delete last.guardUsedBy;
    delete last.guardTarget;
    delete last.guarded;
    delete last.guardedFloors;
    delete last.dropFloors;
    delete last.falling;
  }
  return {
    phase: state.phase, round: state.round, actor: state.actor,
    phaseRemaining: state.phaseRemaining ?? 0,
    floorCategories,
    traps: reveal ? [...state.traps] : [],
    trapQuota: state.trapQuota ?? rabbitTrapQuota(state),
    trapCount: reveal ? state.traps.length : null,
    participants: state.participants.map(participant => ({
      id: participant.id, name: participant.name, human: participant.human,
      alive: participant.alive, position: participant.position,
      haveGuard: participant.human || reveal ? participant.haveGuard : false,
      guard: reveal ? participant.guard : false,
    })),
    plannedMoves: reveal ? state.plannedMoves.map(move => ({ ...move })) : [],
    dropped: reveal ? [...(state.dropped ?? [])] : [],
    guarded: reveal ? [...(state.guarded ?? [])] : [],
    falling: reveal ? [...(state.falling ?? [])] : [],
    dropFloors: reveal ? [...(state.dropFloors ?? [])] : [],
    guardedFloors: reveal ? [...(state.guardedFloors ?? [])] : [],
    last, winner: state.winner, winnerTeam: state.winnerTeam, reason: state.reason,
    oniFloorOpen: state.oniFloorOpen === true,
    allFloorsOpen: state.allFloorsOpen === true,
  };
}
export function rabbitViewForPlayer(state, seat) {
  requireSeat(seat);
  return { ...rabbitView(state), self: { seat,
    traps: seat === 1 ? [...state.traps] : [],
    legalTraps: seat === 1 && state.phase === "trap" ? rabbitLegalTrapFloors(state) : [],
    legalMoves: seat === 0 && state.phase === "move" ? rabbitLegalMoves(state) : [],
    guardTargets: seat === 0 ? state.participants.filter(participant =>
      participant.alive && !participant.human && !participant.guard).map(participant => participant.id) : [] } };
}
export const rabbitGame = Object.freeze({
  id: "rabbit", title: "ザ・ラビットホール", seats: 2,
  create: createRabbit, advance: advanceRabbit, view: rabbitView, viewForPlayer: rabbitViewForPlayer,
  systemAdvance: rabbitSystem,
});

export const BOARD_GAMES = Object.freeze([
  hopeGame, clockGame, walkerGame, gardenGame, rabbitGame,
]);
