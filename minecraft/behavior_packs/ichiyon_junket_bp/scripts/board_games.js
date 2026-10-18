// Independent turn-based board adaptations of the spatial JUNKET games.
// They keep the source game decisions and win conditions testable without
// modifying terrain, entities, inventories, or vanilla player health.

const MAX_HP = 60000;
const INPUT_LIMIT = 6000;
const requireSeat = (seat, count = 2) => {
  if (!Number.isInteger(seat) || seat < 0 || seat >= count) throw new Error("プレイヤー番号が不正です。");
};
const requireTurn = (state, seat, phase) => {
  if (state.phase !== phase || state.actor !== seat) throw new Error("現在の手番ではありません。");
};
const winnerByHp = (state, health, reason) => {
  const dead = health.map(value => value <= 0);
  if (!dead[0] && !dead[1]) return state;
  return {
    ...state, phase: "finished", actor: null,
    winner: dead[0] && dead[1] ? null : dead[0] ? 1 : 0, reason,
  };
};
const copyChoices = choices => choices.map(value => value && { ...value });

// ---------------------------------------------------------------- Short Hope

export const HOPE_FLOORS = 5;
export const HOPE_ROUNDS = 10;
export const HOPE_MAX_PLAYERS = 16;
const nextRandom = seed => ((seed * 1664525 + 1013904223) >>> 0);
function hopeRound(state) {
  let seed = state.seed;
  seed = nextRandom(seed);
  const safe = (seed % HOPE_FLOORS) + 1;
  const hazardCount = Math.min(HOPE_FLOORS - 1,
    state.round <= 3 ? state.round : state.round === 10 ? 4 : 3);
  const candidates = Array.from({ length: HOPE_FLOORS }, (_, index) => index + 1)
    .filter(floor => floor !== safe);
  for (let index = candidates.length - 1; index > 0; index--) {
    seed = nextRandom(seed);
    const swap = seed % (index + 1);
    [candidates[index], candidates[swap]] = [candidates[swap], candidates[index]];
  }
  return { ...state, seed, safe, hazards: candidates.slice(0, hazardCount) };
}
function hopeResolve(state) {
  const alive = [...state.alive];
  for (let seat = 0; seat < alive.length; seat++) {
    if (alive[seat] && state.hazards.includes(state.choices[seat].floor)) alive[seat] = false;
  }
  const survivors = alive.map((value, seat) => value ? seat : -1).filter(seat => seat >= 0);
  const last = {
    round: state.round, safe: state.safe, hazards: [...state.hazards],
    choices: state.choices.map(choice => choice.floor), eliminated: [],
  };
  for (let seat = 0; seat < alive.length; seat++) {
    if (state.alive[seat] && !alive[seat]) last.eliminated.push(seat);
  }
  const next = {
    ...state, alive, choices: Array(state.alive.length).fill(null),
    phase: "result", actor: null, last,
  };
  if (survivors.length <= 1) {
    return { ...next, phase: "finished", winner: survivors[0] ?? null, winners: survivors, reason: "fallen" };
  }
  if (state.round >= HOPE_ROUNDS) {
    return { ...next, phase: "finished", winner: survivors.length === 1 ? survivors[0] : null,
      winners: survivors, reason: "survived" };
  }
  return hopeRound({
    ...next, phase: "choose", actor: survivors[0], round: state.round + 1,
  });
}
export function createHope(playerCount = 2) {
  const count = Math.max(1, Math.min(HOPE_MAX_PLAYERS, Number(playerCount) || 2));
  return hopeRound({
    phase: "choose", round: 1, actor: 0, playerCount: count,
    alive: Array(count).fill(true), choices: Array(count).fill(null),
    safe: null, hazards: [], seed: (Date.now() ^ count) >>> 0,
    last: null, winner: null, winners: [], reason: null,
  });
}
export function advanceHope(state, seat, action, value) {
  requireSeat(seat, state.playerCount);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action !== "floor") throw new Error("不正な操作です。");
  requireTurn(state, seat, "choose");
  if (!state.alive[seat]) throw new Error("脱落済みです。");
  if (!Number.isInteger(value) || value < 1 || value > HOPE_FLOORS) {
    throw new Error("1〜5の床を選んでください。");
  }
  const choices = [...state.choices];
  choices[seat] = { floor: value };
  let nextActor = -1;
  for (let candidate = seat + 1; candidate < state.playerCount; candidate++) {
    if (state.alive[candidate] && choices[candidate] === null) {
      nextActor = candidate;
      break;
    }
  }
  if (nextActor >= 0) return { ...state, choices, actor: nextActor };
  return hopeResolve({ ...state, choices });
}
export function hopeSystem(state, action, value) {
  if (state.phase === "finished") return state;
  const alive = [...state.alive];
  if (action === "timeout") {
    alive[state.actor] = false;
  } else if (action === "disconnect") {
    if (!Number.isInteger(value) || value < 0 || value >= alive.length) throw new Error("脱落対象が不正です。");
    alive[value] = false;
  } else {
    throw new Error("不正な内部操作です。");
  }
  const survivors = alive.map((value, seat) => value ? seat : -1).filter(seat => seat >= 0);
  if (action === "disconnect" && survivors.length > 1) {
    const choices = alive.map((isAlive, seat) => isAlive ? state.choices[seat] : null);
    return {
      ...state, alive, choices,
      actor: survivors.includes(state.actor) ? state.actor : survivors[0],
      reason: state.reason,
    };
  }
  return {
    ...state, alive, phase: "finished",
    winner: survivors.length === 1 ? survivors[0] : null,
    winners: survivors, actor: null,
    reason: action === "disconnect" ? "disconnect" : "timeout",
  };
}
export function hopeView(state) {
  return {
    phase: state.phase, round: state.round, actor: state.actor, playerCount: state.playerCount,
    alive: [...state.alive],
    choices: state.phase === "choose" ? state.choices.map(() => null) : copyChoices(state.choices),
    safe: state.phase === "result" || state.phase === "finished" ? state.safe : null,
    hazards: state.phase === "result" || state.phase === "finished" ? [...state.hazards] : [],
    last: state.last && {
      ...state.last, hazards: [...state.last.hazards],
      choices: [...state.last.choices], eliminated: [...state.last.eliminated],
    },
    winner: state.winner, winners: [...state.winners], reason: state.reason,
  };
}
export const hopeGame = Object.freeze({
  id: "hope", title: "ザ・ショートホープ", seats: HOPE_MAX_PLAYERS,
  minPlayers: 1, maxPlayers: HOPE_MAX_PLAYERS, registration: true,
  create: createHope, advance: advanceHope, view: hopeView, systemAdvance: hopeSystem,
});

// ---------------------------------------------------------------- Clock

export const CLOCK_ROUNDS = 8;
const clockDistance = (first, second) => {
  const direct = Math.abs(first - second);
  return Math.min(direct, 12 - direct);
};
function clockFinish(state, winner, reason) {
  return { ...state, phase: "finished", actor: null, winner, reason };
}
function clockResolve(state) {
  const hp = [...state.hp];
  const results = [];
  for (let attacker = 0; attacker < 2; attacker++) {
    const target = state.hellAreas[1 - attacker];
    const distance = clockDistance(state.hellAreas[attacker], target);
    const correct = state.attacks[attacker].kind === (distance <= 2 ? "short" : "long");
    const damage = correct ? 8000 : 4000;
    const victim = correct ? 1 - attacker : attacker;
    hp[victim] = Math.max(0, hp[victim] - damage);
    results.push({ attacker, target, distance, correct, victim, damage });
  }
  let next = {
    ...state, hp, phase: "result", actor: null,
    last: { results, hellAreas: [...state.hellAreas], attacks: copyChoices(state.attacks) },
  };
  next = winnerByHp(next, hp, "fell");
  if (next.phase === "finished") return next;
  if (state.round >= CLOCK_ROUNDS) {
    const winner = hp[0] === hp[1] ? null : hp[0] > hp[1] ? 0 : 1;
    return clockFinish(next, winner, "clock");
  }
  return {
    ...next, phase: "set", actor: 0, round: state.round + 1,
    hellAreas: [null, null], attacks: [null, null],
  };
}
export function createClock() {
  return {
    phase: "set", round: 1, actor: 0, hellAreas: [null, null], attacks: [null, null],
    hp: [MAX_HP, MAX_HP], last: null, winner: null, reason: null,
  };
}
export function advanceClock(state, seat, action, value) {
  requireSeat(seat);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "set") {
    requireTurn(state, seat, "set");
    if (!Number.isInteger(value) || value < 0 || value > 11) throw new Error("0〜11の時刻位置を選んでください。");
    const hellAreas = [...state.hellAreas];
    hellAreas[seat] = value;
    if (seat === 0) return { ...state, hellAreas, actor: 1 };
    return { ...state, hellAreas, phase: "attack", actor: 0 };
  }
  if (action === "attack") {
    requireTurn(state, seat, "attack");
    if (value !== "short" && value !== "long") throw new Error("短針・長針の攻撃を選んでください。");
    const attacks = [...state.attacks];
    attacks[seat] = { kind: value };
    if (seat === 0) return { ...state, attacks, actor: 1 };
    return clockResolve({ ...state, attacks });
  }
  throw new Error("不正な操作です。");
}
export function clockSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") return clockFinish(state, 1 - state.actor, "timeout");
  throw new Error("不正な内部操作です。");
}
export function clockView(state) {
  return {
    phase: state.phase, round: state.round, actor: state.actor,
    hellAreasReady: state.hellAreas.map(value => value !== null),
    attacks: state.phase === "attack" ? state.attacks.map(() => null) : copyChoices(state.attacks),
    hp: [...state.hp],
    last: state.last && {
      results: state.last.results.map(result => ({ ...result })),
      hellAreas: [...state.last.hellAreas], attacks: copyChoices(state.last.attacks),
    },
    winner: state.winner, reason: state.reason,
  };
}
export const clockGame = Object.freeze({
  id: "clock", title: "ヘル・オ・クロック", seats: 2,
  create: createClock, advance: advanceClock, view: clockView, systemAdvance: clockSystem,
});

// ---------------------------------------------------------------- Walker

export const WALKER_CELLS = 36;
export const WALKER_ROUNDS = 10;
const walkerFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, winner, reason,
});
function walkerRound(state) {
  const walker = state.round % 2 === 1 ? 0 : 1;
  return {
    ...state, phase: "walk", actor: walker, walker,
    breaker: 1 - walker, move: null, bomb: null,
  };
}
function walkerResolveBomb(state) {
  const hp = [...state.hp];
  const position = state.positions[state.walker];
  const hit = state.bomb === position;
  if (hit) hp[state.walker] = Math.max(0, hp[state.walker] - 30000);
  let next = {
    ...state, hp, phase: "result", actor: null,
    last: { walker: state.walker, from: state.move.from, to: state.move.to,
      bomb: state.bomb, hit, round: state.round },
  };
  next = winnerByHp(next, hp, "bomb");
  if (next.phase === "finished") return next;
  if (state.round >= WALKER_ROUNDS) {
    const winner = hp[0] === hp[1] ? null : hp[0] > hp[1] ? 0 : 1;
    return walkerFinish(next, winner, "survival");
  }
  return walkerRound({ ...next, round: state.round + 1 });
}
export function createWalker() {
  return walkerRound({
    phase: "walk", round: 1, actor: 0, walker: 0, breaker: 1,
    positions: [21, 17], hp: [MAX_HP, MAX_HP], move: null, bomb: null,
    last: null, winner: null, reason: null,
  });
}
export function advanceWalker(state, seat, action, value) {
  requireSeat(seat);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "walk") {
    requireTurn(state, seat, "walk");
    if (seat !== state.walker) throw new Error("ウォーカーの手番ではありません。");
    if (!Number.isInteger(value) || value < 1 || value > WALKER_CELLS) throw new Error("移動先を選んでください。");
    const from = state.positions[seat];
    if (value === from || Math.abs(value - from) > 5) throw new Error("1〜5マス先へ移動してください。");
    const positions = [...state.positions];
    positions[seat] = value;
    return {
      ...state, positions, phase: "bomb", actor: state.breaker,
      move: { from, to: value },
    };
  }
  if (action === "bomb") {
    requireTurn(state, seat, "bomb");
    if (seat !== state.breaker) throw new Error("ブレイカーの手番ではありません。");
    if (!Number.isInteger(value) || value < 1 || value > WALKER_CELLS) throw new Error("爆弾の位置を選んでください。");
    return walkerResolveBomb({ ...state, bomb: value });
  }
  throw new Error("不正な操作です。");
}
export function walkerSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") return walkerFinish(state, 1 - state.actor, "timeout");
  throw new Error("不正な内部操作です。");
}
export function walkerView(state) {
  return {
    phase: state.phase, round: state.round, actor: state.actor,
    walker: state.walker, breaker: state.breaker, positions: [...state.positions],
    hp: [...state.hp], move: state.move && { ...state.move }, bomb: state.bomb,
    last: state.last && { ...state.last }, winner: state.winner, reason: state.reason,
  };
}
export const walkerGame = Object.freeze({
  id: "walker", title: "ザ・クレバス・ウォーカー", seats: 2,
  create: createWalker, advance: advanceWalker, view: walkerView, systemAdvance: walkerSystem,
});

// ---------------------------------------------------------------- Jewel Garden

export const GARDEN_ROUNDS = 6;
export const GARDEN_MAX_JEWELS = 1280;
const GARDEN_ACTION = Object.freeze(["switch", "double", "open"]);
const gardenFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, winner, reason,
});
function gardenResolve(state) {
  let valve = state.valve;
  let chest = state.chest;
  const loads = [...state.loads];
  const switched = state.choices.filter(choice => choice.action === "switch").length;
  if (switched === 1) valve = state.choices.findIndex(choice => choice.action === "switch");
  if (state.choices.some(choice => choice.action === "double")) {
    chest = Math.min(GARDEN_MAX_JEWELS, chest * 2);
  }
  const opened = state.choices.some(choice => choice.action === "open");
  if (opened) {
    loads[valve] += chest;
    chest = 5;
  }
  let next = {
    ...state, valve, chest, loads, phase: "result", actor: null,
    last: { choices: state.choices.map(choice => choice.action), valve, chest, opened },
  };
  if (loads.some(load => load >= GARDEN_MAX_JEWELS)) {
    const crushed = loads[0] >= GARDEN_MAX_JEWELS ? 0 : 1;
    return gardenFinish(next, 1 - crushed, "crushed");
  }
  if (state.round >= GARDEN_ROUNDS) {
    const winner = loads[0] === loads[1] ? null : loads[0] < loads[1] ? 0 : 1;
    return gardenFinish(next, winner, "fall_order");
  }
  return {
    ...next, phase: "choose", actor: 0, round: state.round + 1,
    choices: [null, null],
  };
}
export function createGarden() {
  return {
    phase: "choose", round: 1, actor: 0, choices: [null, null],
    valve: 0, chest: 5, loads: [0, 0], last: null, winner: null, reason: null,
  };
}
export function advanceGarden(state, seat, action, value) {
  requireSeat(seat);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action !== "choose") throw new Error("不正な操作です。");
  requireTurn(state, seat, "choose");
  if (!GARDEN_ACTION.includes(value)) throw new Error("切替・宝石倍化・開放を選んでください。");
  const choices = [...state.choices];
  choices[seat] = { action: value };
  if (seat === 0) return { ...state, choices, actor: 1 };
  return gardenResolve({ ...state, choices });
}
export function gardenSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") return gardenFinish(state, 1 - state.actor, "timeout");
  throw new Error("不正な内部操作です。");
}
export function gardenView(state) {
  return {
    phase: state.phase, round: state.round, actor: state.actor,
    choices: state.phase === "choose" ? state.choices.map(() => null) : copyChoices(state.choices),
    valve: state.valve, chest: state.chest,
    loads: [...state.loads], last: state.last && {
      ...state.last, choices: [...state.last.choices],
    },
    winner: state.winner, reason: state.reason,
  };
}
export const gardenGame = Object.freeze({
  id: "garden", title: "ジュエルシャワー・ハイガーデン", seats: 2,
  create: createGarden, advance: advanceGarden, view: gardenView, systemAdvance: gardenSystem,
});

// ---------------------------------------------------------------- Rabbit Hole

export const RABBIT_GOALS = Object.freeze([12, 24, 36, 48, 60]);
export const RABBIT_CPU_START = Object.freeze([1, 13, 37, 49]);
export const RABBIT_ROUNDS = 10;
const rabbitFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, winner, reason,
});
function rabbitNextRound(state) {
  const cpu = state.cpu.map((position, index) => {
    let move = 1 + ((state.seed + index * 3 + state.round) % 5);
    let next = Math.min(60, position + move);
    if (state.traps.includes(next)) return null;
    if (RABBIT_GOALS.includes(next)) next = Math.min(60, next + 1);
    return next;
  });
  const survivors = cpu.filter(position => position !== null);
  return {
    ...state, cpu: survivors, traps: [], phase: "trap", actor: 1,
    seed: (state.seed * 1664525 + 1013904223) >>> 0,
    last: null,
  };
}
export function createRabbit() {
  return {
    phase: "trap", round: 1, actor: 1, escaper: 25,
    cpu: [...RABBIT_CPU_START], traps: [], seed: (Date.now() ^ 0x5a17) >>> 0,
    last: null, winner: null, reason: null,
  };
}
export function advanceRabbit(state, seat, action, value) {
  requireSeat(seat);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "trap") {
    requireTurn(state, seat, "trap");
    if (seat !== 1) throw new Error("オニの手番ではありません。");
    if (!Number.isInteger(value) || value < 1 || value > 60 || value === state.escaper) {
      throw new Error("脱出者以外の1〜60の床を選んでください。");
    }
    return { ...state, traps: [value], phase: "move", actor: 0 };
  }
  if (action === "move") {
    requireTurn(state, seat, "move");
    if (seat !== 0) throw new Error("脱出側の手番ではありません。");
    if (!Number.isInteger(value) || value < 1 || value > 60 || value === state.escaper ||
        Math.abs(value - state.escaper) > 5) {
      throw new Error("現在地から1〜5マス以内の別の床を選んでください。");
    }
    if (RABBIT_GOALS.includes(value)) {
      return rabbitFinish({ ...state, escaper: value, last: { to: value, escaped: true } }, 0, "goal");
    }
    if (state.traps.includes(value)) {
      return rabbitFinish({ ...state, escaper: value, last: { to: value, trapped: true } }, 1, "trap");
    }
    const next = rabbitNextRound({ ...state, escaper: value, round: state.round + 1 });
    if (!next.cpu.length) {
      return rabbitFinish(next, 1, "all_cpu_fell");
    }
    if (state.round >= RABBIT_ROUNDS) {
      return rabbitFinish(next, 1, "timeout");
    }
    return next;
  }
  throw new Error("不正な操作です。");
}
export function rabbitSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") return rabbitFinish(state, state.actor === 0 ? 1 : 0, "timeout");
  throw new Error("不正な内部操作です。");
}
export function rabbitView(state) {
  return {
    phase: state.phase, round: state.round, actor: state.actor,
    escaper: state.escaper, cpu: [...state.cpu],
    traps: state.phase === "finished" ? [...state.traps] : [],
    last: state.last && { ...state.last }, winner: state.winner, reason: state.reason,
  };
}
export const rabbitGame = Object.freeze({
  id: "rabbit", title: "ザ・ラビットホール", seats: 2,
  create: createRabbit, advance: advanceRabbit, view: rabbitView, systemAdvance: rabbitSystem,
});

export const BOARD_GAMES = Object.freeze([
  hopeGame, clockGame, walkerGame, gardenGame, rabbitGame,
]);
