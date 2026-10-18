// Independent Bedrock implementations of the non-spatial JUNKET games.
// These states are virtual: no inventory, world block, or player health is changed.

export const JUNKET_MAX_HP = 60000;
export const JUNKET_INPUT_TICKS = 6000;

const isSeat = (seat, count) => Number.isInteger(seat) && seat >= 0 && seat < count;
const requireSeat = (seat, count) => {
  if (!isSeat(seat, count)) throw new Error("プレイヤー番号が不正です。");
};
const requireTurn = (state, seat, phase) => {
  if (state.phase !== phase || state.actor !== seat) {
    throw new Error("現在の手番ではありません。");
  }
};
const aliveWinners = hp => {
  const alive = hp.map((value, seat) => value > 0 ? seat : -1).filter(seat => seat >= 0);
  return { winner: alive.length === 1 ? alive[0] : null, alive };
};
const copyArray = value => Array.isArray(value) ? [...value] : value;
const permutation = (value, expected, message) => {
  if (!Array.isArray(value) || value.length !== expected.length ||
      [...value].sort((a, b) => a - b).join(",") !== [...expected].sort((a, b) => a - b).join(",")) {
    throw new Error(message);
  }
  return [...value];
};

// ---------------------------------------------------------------- Grail

export const GRAIL_ROUNDS = 10;
export const GRAIL_HEALTH = JUNKET_MAX_HP;
export const GRAIL_HOLY_DOSE = 100;
export const GRAIL_LETHAL_DOSE = 500;
const grailDamage = dose => {
  const bands = [
    [500, 60000], [450, 17500], [400, 10000], [300, 5000],
    [200, 3000], [100, 1000], [1, 500],
  ];
  return bands.find(([minimum]) => dose >= minimum)?.[1] ?? 0;
};
const grailFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, winner, reason,
});
function grailResult(state, grailSeat, potionSeat, cupIndex, potionIndex) {
  const grail = state.arrangements[grailSeat];
  const potion = state.arrangements[potionSeat];
  if (!grail || !potion) throw new Error("聖杯とポーションの配置が完了していません。");
  const trueCup = cupIndex === grail.trueIndex;
  const holyPotion = potionIndex === potion.holyIndex;
  const holy = trueCup ? holyPotion : !holyPotion;
  const drinker = potionSeat;
  const hp = [...state.hp];
  const poison = [...state.poison];
  const holyScore = [...state.holy];
  if (holy) holyScore[drinker] += GRAIL_HOLY_DOSE;
  else poison[drinker] += GRAIL_HOLY_DOSE;
  hp[drinker] = Math.max(0, hp[drinker] - grailDamage(poison[drinker]));
  const next = {
    ...state,
    hp,
    poison,
    holy: holyScore,
    round: state.round + 1,
    grailSide: 1 - state.grailSide,
    actor: 1 - state.grailSide,
    phase: "finished",
    arrangements: [null, null],
    cupIndex: null,
    potionIndex: null,
    last: {
      round: state.round,
      kind: holy ? "holy" : "poison",
      drinker,
      trueCup,
      holyPotion,
    },
    winner: null,
    reason: null,
  };
  if (hp[0] <= 0 || hp[1] <= 0) {
    return grailFinish(next, hp[0] <= 0 && hp[1] <= 0 ? null : hp[0] <= 0 ? 1 : 0, "health");
  }
  if (state.round >= GRAIL_ROUNDS) {
    const winner = holyScore[0] === holyScore[1] ? null : holyScore[0] > holyScore[1] ? 0 : 1;
    return grailFinish({ ...next, phase: "result" }, winner, "holy_volume");
  }
  return { ...next, phase: "arrange", actor: next.grailSide };
}
export function createGrail() {
  return {
    phase: "arrange",
    round: 1,
    actor: 0,
    grailSide: 0,
    arrangements: [null, null],
    cupIndex: null,
    potionIndex: null,
    hp: [GRAIL_HEALTH, GRAIL_HEALTH],
    holy: [0, 0],
    poison: [0, 0],
    last: null,
    winner: null,
    reason: null,
  };
}
export function advanceGrail(state, seat, action, value) {
  requireSeat(seat, 2);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "arrange") {
    requireTurn(state, seat, "arrange");
    if (!value || !Number.isInteger(value.trueIndex) || !Number.isInteger(value.holyIndex) ||
        value.trueIndex < 0 || value.trueIndex > 2 || value.holyIndex < 0 || value.holyIndex > 2) {
      throw new Error("本物の聖杯と聖水の位置を1つずつ選んでください。");
    }
    const arrangements = [...state.arrangements];
    arrangements[seat] = { trueIndex: value.trueIndex, holyIndex: value.holyIndex };
    if (arrangements.some(entry => !entry)) {
      return { ...state, arrangements, actor: 1 - seat };
    }
    return { ...state, arrangements, phase: "cup", actor: state.grailSide };
  }
  if (action === "cup") {
    requireTurn(state, seat, "cup");
    if (seat !== state.grailSide) throw new Error("聖杯側の手番ではありません。");
    if (!Number.isInteger(value) || value < 0 || value > 2) throw new Error("聖杯を選んでください。");
    return { ...state, cupIndex: value, phase: "potion", actor: 1 - state.grailSide };
  }
  if (action === "potion") {
    requireTurn(state, seat, "potion");
    if (seat === state.grailSide) throw new Error("ポーション側の手番ではありません。");
    if (!Number.isInteger(value) || value < 0 || value > 2) throw new Error("ポーションを選んでください。");
    return grailResult(state, state.grailSide, seat, state.cupIndex, value);
  }
  throw new Error("不正な操作です。");
}
export function grailSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") return grailFinish(state, 1 - state.actor, "timeout");
  throw new Error("不正な内部操作です。");
}
export function grailView(state) {
  return {
    phase: state.phase, round: state.round, actor: state.actor, grailSide: state.grailSide,
    hp: [...state.hp], holy: [...state.holy], poison: [...state.poison],
    arrangementsReady: state.arrangements.map(Boolean), last: state.last && { ...state.last },
    winner: state.winner, reason: state.reason,
  };
}
export const grailGame = Object.freeze({
  id: "grail", title: "アンハッピー・ホーリーグレイル", seats: 2,
  create: createGrail, advance: advanceGrail, view: grailView, systemAdvance: grailSystem,
});

// ---------------------------------------------------------------- Auction

export const AUCTION_SEATS = 4;
export const AUCTION_TEAM_A = Object.freeze([0, 2]);
export const AUCTION_TEAM_B = Object.freeze([1, 3]);
export const AUCTION_DIAMONDS_TO_WIN = 3;
export const AUCTION_MAX_PAYMENT = 15;
const teamOf = seat => AUCTION_TEAM_A.includes(seat) ? 0 : 1;
const auctionFinish = (state, winnerTeam, reason) => ({
  ...state, phase: "finished", actor: null, winnerTeam, winner: null, reason,
});
function resetAuctionRound(state, round = state.round + 1) {
  return {
    ...state, phase: "bid", round, actor: 0, bids: [null, null, null, null],
    selected: [null, null, null, null], last: null, lastWinner: null,
  };
}
function resolveAuction(state) {
  const counts = [0, 0, 0, 0, 0];
  for (const bid of state.bids) counts[bid]++;
  const value = [4, 3, 2, 1].find(candidate => counts[candidate] === 1) ?? null;
  const winner = value === null ? null : state.bids.indexOf(value);
  const roundWins = [...state.roundWins];
  const payment = [...state.payment];
  const diamonds = [...state.diamonds];
  let next = {
    ...state, phase: "result", actor: null, last: { bids: [...state.bids], value, winner },
    lastWinner: winner,
  };
  if (winner !== null) {
    const team = teamOf(winner);
    roundWins[team]++;
    if (roundWins[team] >= 2) {
      diamonds[team]++;
      payment[winner] += value;
      next = { ...next, phase: "set_result", diamonds, payment, roundWins };
      if (diamonds[team] >= AUCTION_DIAMONDS_TO_WIN) {
        return auctionFinish(next, team, "diamonds");
      }
      if (payment[winner] > AUCTION_MAX_PAYMENT) {
        return auctionFinish(next, 1 - team, "payment");
      }
      return next;
    }
  }
  if (state.round >= 4) return { ...next, phase: "set_result", roundWins };
  return resetAuctionRound({ ...next, roundWins }, state.round + 1);
}
export function createAuction() {
  return {
    phase: "bid", set: 1, round: 1, actor: 0, bids: [null, null, null, null],
    selected: [null, null, null, null], roundWins: [0, 0], diamonds: [0, 0],
    payment: [0, 0, 0, 0], last: null, lastWinner: null,
    winnerTeam: null, winner: null, reason: null,
  };
}
export function advanceAuction(state, seat, action, value) {
  requireSeat(seat, AUCTION_SEATS);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action !== "bid") throw new Error("不正な操作です。");
  requireTurn(state, seat, "bid");
  if (!Number.isInteger(value) || value < 1 || value > 4) throw new Error("1〜4の札を選んでください。");
  if (state.selected[seat] !== null) throw new Error("このセットでは同じ札を2回使えません。");
  const bids = [...state.bids];
  const selected = [...state.selected];
  bids[seat] = value;
  selected[seat] = value;
  if (seat < AUCTION_SEATS - 1) return { ...state, bids, selected, actor: seat + 1 };
  return resolveAuction({ ...state, bids, selected });
}
export function auctionSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") {
    return auctionFinish(state, 1 - teamOf(state.actor), "timeout");
  }
  if (action === "next_set" && state.phase === "set_result") {
    return resetAuctionRound({
      ...state, set: state.set + 1, roundWins: [0, 0],
      selected: [null, null, null, null],
    }, 1);
  }
  throw new Error("不正な内部操作です。");
}
export function auctionView(state) {
  return {
    phase: state.phase, set: state.set, round: state.round, actor: state.actor,
    bids: state.phase === "bid" ? state.bids.map(() => null) : [...state.bids],
    selected: [...state.selected], roundWins: [...state.roundWins],
    diamonds: [...state.diamonds], payment: [...state.payment],
    last: state.last && { ...state.last, bids: [...state.last.bids] },
    lastWinner: state.lastWinner, winnerTeam: state.winnerTeam, winner: state.winner, reason: state.reason,
  };
}
export const auctionGame = Object.freeze({
  id: "auction", title: "ライフ・イズ・オークショニア", seats: 4,
  create: createAuction, advance: advanceAuction, view: auctionView, systemAdvance: auctionSystem,
});

// ---------------------------------------------------------------- Fire

export const FIRE_TEMPERATURE_MAX = 140;
export const FIRE_TEMPERATURE_MIN = -130;
const RPS = Object.freeze(["rock", "paper", "scissors"]);
const rpsResult = (first, second) => {
  if (first === second) return null;
  return (first - second + 3) % 3 === 1 ? 0 : 1;
};
export const fireTemperatureDamage = temperature => {
  const absolute = Math.abs(temperature);
  if (absolute >= 141) return 1000;
  if (absolute >= 131) return 750;
  if (absolute >= 121) return 500;
  if (absolute >= 111) return 400;
  if (absolute >= 101) return 325;
  if (absolute >= 100) return 300;
  if (absolute >= 90) return 250;
  if (absolute >= 84) return 150;
  if (absolute >= 76) return 80;
  if (absolute >= 61) return 45;
  if (absolute >= 51) return 25;
  if (absolute >= 41) return 15;
  if (absolute >= 21) return 5;
  return 23;
};
const fireFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, winner, reason,
});
function fireResolve(state) {
  const [first, second] = state.choices;
  const winner = rpsResult(first.choice, second.choice);
  const wins = [...state.wins];
  const temperature = [...state.temperature];
  const choiceTicks = [...state.choiceTicks];
  choiceTicks[0] += first.ticks;
  choiceTicks[1] += second.ticks;
  let change = 0;
  if (winner !== null) {
    wins[winner]++;
    const winningMove = state.choices[winner].choice;
    const losingMove = state.choices[1 - winner].choice;
    if (winningMove === 1 && losingMove === 0) change = 5;
    if (winningMove === 2 && losingMove === 1) change = 2;
    temperature[winner] = Math.max(FIRE_TEMPERATURE_MIN,
      Math.min(FIRE_TEMPERATURE_MAX, temperature[winner] - change));
    temperature[1 - winner] = Math.max(FIRE_TEMPERATURE_MIN,
      Math.min(FIRE_TEMPERATURE_MAX, temperature[1 - winner] + change));
  }
  const next = {
    ...state, choices: [null, null], wins, temperature, choiceTicks,
    last: { choices: state.choices.map(entry => entry.choice), winner, change },
  };
  if (wins[0] >= 3 || wins[1] >= 3) {
    let matchWinner = wins[0] >= 3 ? 0 : 1;
    if (wins[0] === wins[1] && choiceTicks[0] !== choiceTicks[1]) {
      matchWinner = choiceTicks[0] > choiceTicks[1] ? 1 : 0;
    }
    return fireFinish(next, matchWinner, "wins");
  }
  return { ...next, phase: "choice", actor: 0 };
}
export function createFire() {
  return {
    phase: "choice", actor: 0, round: 1, choices: [null, null],
    wins: [0, 0], hp: [JUNKET_MAX_HP, JUNKET_MAX_HP],
    temperature: [30, 30], choiceTicks: [0, 0], ticks: 0,
    last: null, winner: null, reason: null,
  };
}
export function advanceFire(state, seat, action, value) {
  requireSeat(seat, 2);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action !== "choice") throw new Error("不正な操作です。");
  requireTurn(state, seat, "choice");
  if (!value || !RPS.includes(value.choice)) throw new Error("グー・パー・チョキを選んでください。");
  const choices = [...state.choices];
  choices[seat] = {
    choice: RPS.indexOf(value.choice),
    ticks: Math.max(0, Math.min(JUNKET_INPUT_TICKS, Number(value.ticks) || 0)),
  };
  if (seat === 0) return { ...state, choices, actor: 1 };
  return fireResolve({ ...state, choices, round: state.round + 1 });
}
export function fireSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") return fireFinish(state, 1 - state.actor, "timeout");
  if (action !== "tick") throw new Error("不正な内部操作です。");
  const hp = [...state.hp];
  for (let seat = 0; seat < 2; seat++) {
    hp[seat] = Math.max(0, hp[seat] - fireTemperatureDamage(state.temperature[seat]));
  }
  const next = { ...state, hp, ticks: state.ticks + 1 };
  if (hp[0] <= 0 || hp[1] <= 0) {
    return fireFinish(next, hp[0] <= 0 && hp[1] <= 0 ? null : hp[0] <= 0 ? 1 : 0, "temperature");
  }
  return next;
}
export function fireView(state) {
  return {
    ...state, choices: state.phase === "choice"
      ? state.choices.map(() => null)
      : state.choices.map(entry => entry && { ...entry }),
    last: state.last && { ...state.last, choices: [...state.last.choices] },
    wins: [...state.wins], hp: [...state.hp], temperature: [...state.temperature],
    choiceTicks: [...state.choiceTicks],
  };
}
export const fireGame = Object.freeze({
  id: "fire", title: "シヴァリング・ファイア", seats: 2,
  create: createFire, advance: advanceFire, view: fireView, systemAdvance: fireSystem,
});

// ---------------------------------------------------------------- Pig Palace

export const PIG_CARDS = Object.freeze({
  1: "わらの家", 2: "木の家", 3: "レンガの家", 4: "オオカミ",
});
export const PIG_STARTING_PIGS = 5;
export const PIG_SEATS = 4;
const pigFinish = (state, alive, reason) => {
  let winner = null;
  if (alive.length === 1) winner = alive[0];
  else if (alive.length) {
    const highest = Math.max(...alive.map(seat => state.pigs[seat]));
    const leaders = alive.filter(seat => state.pigs[seat] === highest);
    winner = leaders.length === 1 ? leaders[0] : null;
  }
  return { ...state, phase: "finished", actor: null, winner, reason, alive: [...alive] };
};
function pigResolve(state) {
  const alive = state.pigs.map((pigs, seat) => pigs > 0 ? seat : -1).filter(seat => seat >= 0);
  const counts = [0, 0, 0, 0, 0];
  for (const seat of alive) counts[state.choices[seat].card]++;
  const card = [4, 3, 2, 1].find(candidate => counts[candidate] === 1) ?? null;
  const winner = card === null ? null : alive.find(seat => state.choices[seat].card === card);
  const pigs = [...state.pigs];
  let target = null;
  if (winner !== null) {
    target = state.choices[winner].target;
    if (!alive.includes(target) || target === winner) {
      target = alive.find(seat => seat !== winner) ?? null;
    }
    if (target !== null && pigs[target] > 0) pigs[target]--;
  }
  const remaining = pigs.map((count, seat) => count > 0 ? seat : -1).filter(seat => seat >= 0);
  const next = {
    ...state, pigs, alive: remaining, phase: "result", actor: null,
    last: { cards: state.choices.map(entry => entry?.card ?? null), winner, target },
  };
  if (remaining.length <= 1) return pigFinish(next, remaining, "palace");
  if (state.round >= 4) return pigFinish(next, remaining, "palace");
  return {
    ...next, phase: "choose", actor: remaining[0], round: state.round + 1,
    choices: [null, null, null, null], used: [[], [], [], []],
  };
}
export function createPig() {
  return {
    phase: "choose", round: 1, actor: 0, choices: [null, null, null, null],
    pigs: Array(PIG_SEATS).fill(PIG_STARTING_PIGS),
    used: [[], [], [], []], last: null, alive: [0, 1, 2, 3],
    winner: null, reason: null,
  };
}
export function advancePig(state, seat, action, value) {
  requireSeat(seat, PIG_SEATS);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action !== "choose") throw new Error("不正な操作です。");
  requireTurn(state, seat, "choose");
  if (state.pigs[seat] <= 0) {
    const nextSeat = state.choices.findIndex((entry, candidate) =>
      candidate > seat && entry === null && state.pigs[candidate] > 0);
    if (nextSeat >= 0) return { ...state, actor: nextSeat };
    return pigResolve(state);
  }
  if (!value || !Number.isInteger(value.card) || value.card < 1 || value.card > 4) {
    throw new Error("わら・木・レンガ・オオカミのカードを選んでください。");
  }
  if (state.used[seat].includes(value.card)) throw new Error("同じカードは1回だけ使えます。");
  const choices = [...state.choices];
  choices[seat] = { card: value.card, target: Number.isInteger(value.target) ? value.target : null };
  const used = state.used.map(entry => [...entry]);
  used[seat].push(value.card);
  let nextActor = -1;
  for (let candidate = seat + 1; candidate < PIG_SEATS; candidate++) {
    if (state.pigs[candidate] > 0 && choices[candidate] === null) {
      nextActor = candidate;
      break;
    }
  }
  if (nextActor >= 0) return { ...state, choices, used, actor: nextActor };
  return pigResolve({ ...state, choices, used });
}
export function pigSystem(state, action) {
  if (action === "timeout") return pigFinish(state, state.pigs.map((p, s) => p > 0 ? s : -1).filter(s => s >= 0), "timeout");
  throw new Error("不正な内部操作です。");
}
export function pigView(state) {
  return {
    ...state, choices: state.phase === "choose"
      ? state.choices.map(() => null)
      : state.choices.map(entry => entry && { ...entry }),
    pigs: [...state.pigs], used: state.used.map(entry => [...entry]),
    last: state.last && { ...state.last, cards: [...state.last.cards] },
    alive: [...state.alive],
  };
}
export const pigGame = Object.freeze({
  id: "pig", title: "ピーキー・ピッグ・パレス", seats: PIG_SEATS,
  create: createPig, advance: advancePig, view: pigView, systemAdvance: pigSystem,
});

// ---------------------------------------------------------------- Candlelight

export const LIGHT_HEARTS_TO_WIN = 7;
const LIGHT_GUARDS = Object.freeze(["強欲", "幸運", "怠惰"]);
const LIGHT_TOURISTS = Object.freeze(["心臓", "槌", "松明"]);
const lightOutcome = (guard, tourist) => {
  if (guard === 0) return { hearts: 0, torches: 0 };
  const strong = guard === 1 ? 2 : 1;
  if (tourist === 0) return { hearts: strong, torches: 0 };
  if (tourist === 1) return { hearts: -strong, torches: 0 };
  return { hearts: 0, torches: strong };
};
const lightFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, winner, reason,
});
function lightResolve(state) {
  const results = [];
  const hearts = [...state.hearts];
  const torches = [...state.torches];
  const fuel = [...state.fuel];
  for (let seat = 0; seat < 2; seat++) {
    const targetSeat = 1 - seat;
    const arrangement = state.arrangements[targetSeat];
    const selection = state.selections[seat];
    const outcome = lightOutcome(arrangement.guards[selection.guard],
      arrangement.tourists[selection.tourist]);
    hearts[seat] = Math.max(0, hearts[seat] + outcome.hearts);
    torches[seat] = Math.max(0, Math.min(5, torches[seat] + outcome.torches));
    fuel[seat] = Math.min(24000, fuel[seat] + outcome.torches * 2400);
    results.push(outcome);
  }
  let next = {
    ...state, hearts, torches, fuel, phase: "result", actor: null,
    last: { results, selections: state.selections.map(value => ({ ...value })) },
  };
  const won = hearts.map(value => value >= LIGHT_HEARTS_TO_WIN);
  if (won[0] || won[1]) {
    const winner = won[0] && won[1] ? null : won[0] ? 0 : 1;
    return lightFinish(next, winner, "hearts");
  }
  const died = fuel.map(value => value <= 0);
  if (died[0] || died[1]) {
    const winner = died[0] && died[1] ? null : died[0] ? 1 : 0;
    return lightFinish(next, winner, "oxygen");
  }
  return {
    ...next, phase: "arrange", actor: 0, round: state.round + 1,
    arrangements: [null, null], selections: [null, null],
  };
}
export function createLight() {
  return {
    phase: "arrange", round: 1, actor: 0, arrangements: [null, null],
    selections: [null, null], hearts: [0, 0], torches: [1, 1],
    fuel: [24000, 24000], last: null, winner: null, reason: null,
  };
}
export function advanceLight(state, seat, action, value) {
  requireSeat(seat, 2);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "arrange") {
    requireTurn(state, seat, "arrange");
    if (!value) throw new Error("門番と死者を並べてください。");
    const guards = permutation(value.guards, [0, 1, 2], "門番を1体ずつ並べてください。");
    const tourists = permutation(value.tourists, [0, 1, 2], "死者を1人ずつ並べてください。");
    const arrangements = [...state.arrangements];
    arrangements[seat] = { guards, tourists };
    if (arrangements.some(entry => !entry)) return { ...state, arrangements, actor: 1 };
    return { ...state, arrangements, phase: "select", actor: 0 };
  }
  if (action === "select") {
    requireTurn(state, seat, "select");
    if (!value || !Number.isInteger(value.guard) || !Number.isInteger(value.tourist) ||
        value.guard < 0 || value.guard > 2 || value.tourist < 0 || value.tourist > 2) {
      throw new Error("相手の門番と死者を選んでください。");
    }
    const selections = [...state.selections];
    selections[seat] = { guard: value.guard, tourist: value.tourist };
    if (seat === 0) return { ...state, selections, actor: 1 };
    return lightResolve({ ...state, selections });
  }
  throw new Error("不正な操作です。");
}
export function lightSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") return lightFinish(state, 1 - state.actor, "timeout");
  if (action !== "tick") throw new Error("不正な内部操作です。");
  const fuel = state.fuel.map(value => Math.max(0, value - 1));
  if (fuel.some(value => value <= 0)) {
    const next = { ...state, fuel };
    const dead = fuel.map(value => value <= 0);
    return lightFinish(next, dead[0] && dead[1] ? null : dead[0] ? 1 : 0, "oxygen");
  }
  return { ...state, fuel };
}
export function lightView(state) {
  return {
    phase: state.phase, round: state.round, actor: state.actor,
    arrangementsReady: state.arrangements.map(Boolean),
    selections: state.phase === "select"
      ? state.selections.map(() => null)
      : state.selections.map(entry => entry && { ...entry }),
    hearts: [...state.hearts], torches: [...state.torches], fuel: [...state.fuel],
    last: state.last && {
      results: state.last.results.map(result => ({ ...result })),
      selections: state.last.selections.map(value => ({ ...value })),
    },
    winner: state.winner, reason: state.reason,
  };
}
export const lightGame = Object.freeze({
  id: "light", title: "デッドマンズ・キャンドルライト", seats: 2,
  create: createLight, advance: advanceLight, view: lightView, systemAdvance: lightSystem,
});

// ---------------------------------------------------------------- Devil's Mine

export const MINE_ROUNDS = 20;
export const MINE_CARDS = Object.freeze(["ブランク", "大喰らいの人魂", "先人のツルハシ", "無気力瓶"]);
const mineFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, winner, reason,
});
function mineChoice(seat, value) {
  if (!value || !Array.isArray(value.indices) || value.indices.length !== 2) {
    throw new Error("2つの岩を選んでください。");
  }
  const indices = [...new Set(value.indices)];
  if (indices.length !== 2 || indices.some(index => !Number.isInteger(index) || index < 0 || index > 3)) {
    throw new Error("異なる2つの岩を選んでください。");
  }
  return { seat, indices, ticks: Math.max(0, Math.min(JUNKET_INPUT_TICKS, Number(value.ticks) || 0)) };
}
function mineResolve(state) {
  const hp = [...state.hp];
  const ingots = [...state.ingots];
  const mined = [...state.mined];
  const choiceTicks = [...state.choiceTicks];
  const revelations = [];
  for (let chooser = 0; chooser < 2; chooser++) {
    const owner = 1 - chooser;
    const board = state.boards[owner];
    const choice = state.choices[chooser];
    let count = 0;
    choiceTicks[chooser] += choice.ticks;
    for (const index of choice.indices) {
      const card = board[index];
      if (card === 1) {
        ingots[chooser] = Math.max(0, ingots[chooser] - 3);
      } else if (card === 2) {
        ingots[chooser] += 3;
      }
      if (card !== 0) count++;
    }
    mined[chooser] += count;
    revelations.push({ chooser, indices: [...choice.indices], cards: choice.indices.map(index => board[index]) });
  }
  let loser = null;
  if (mined[0] < mined[1]) loser = 0;
  else if (mined[1] < mined[0]) loser = 1;
  else if (choiceTicks[0] !== choiceTicks[1]) loser = choiceTicks[0] > choiceTicks[1] ? 0 : 1;
  else loser = 1;
  hp[loser] = Math.max(0, hp[loser] - 6000);
  let next = {
    ...state, hp, ingots, mined, choiceTicks, phase: "result", actor: null,
    last: { revelations, poison: loser },
  };
  if (hp[0] <= 0 || hp[1] <= 0) {
    return mineFinish(next, hp[0] <= 0 && hp[1] <= 0 ? null : hp[0] <= 0 ? 1 : 0, "poison");
  }
  if (state.round >= MINE_ROUNDS) {
    const winner = ingots[0] === ingots[1] ? null : ingots[0] < ingots[1] ? 0 : 1;
    return mineFinish(next, winner, "ingots");
  }
  return {
    ...next, phase: "arrange", actor: 0, round: state.round + 1,
    boards: [null, null], choices: [null, null],
  };
}
export function createMine() {
  return {
    phase: "arrange", round: 1, actor: 0, boards: [null, null],
    choices: [null, null], hp: [JUNKET_MAX_HP, JUNKET_MAX_HP],
    ingots: [30, 30], mined: [0, 0], choiceTicks: [0, 0],
    last: null, winner: null, reason: null,
  };
}
export function advanceMine(state, seat, action, value) {
  requireSeat(seat, 2);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "arrange") {
    requireTurn(state, seat, "arrange");
    if (!value) throw new Error("岩を並べてください。");
    const board = permutation(value.cards, [0, 1, 2, 3], "4種類の岩を1つずつ並べてください。");
    const boards = [...state.boards];
    boards[seat] = board;
    if (boards.some(entry => !entry)) return { ...state, boards, actor: 1 };
    return { ...state, boards, phase: "mine", actor: 0 };
  }
  if (action === "mine") {
    requireTurn(state, seat, "mine");
    const choices = [...state.choices];
    choices[seat] = mineChoice(seat, value);
    if (seat === 0) return { ...state, choices, actor: 1 };
    return mineResolve({ ...state, choices });
  }
  throw new Error("不正な操作です。");
}
export function mineSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") return mineFinish(state, 1 - state.actor, "timeout");
  throw new Error("不正な内部操作です。");
}
export function mineView(state) {
  return {
    phase: state.phase, round: state.round, actor: state.actor,
    boardsReady: state.boards.map(Boolean), hp: [...state.hp], ingots: [...state.ingots],
    mined: [...state.mined], choiceTicks: [...state.choiceTicks],
    last: state.last && {
      poison: state.last.poison,
      revelations: state.last.revelations.map(entry => ({ ...entry, indices: [...entry.indices], cards: [...entry.cards] })),
    },
    winner: state.winner, reason: state.reason,
  };
}
export const mineGame = Object.freeze({
  id: "mine", title: "デビルズマイン・ツインズ", seats: 2,
  create: createMine, advance: advanceMine, view: mineView, systemAdvance: mineSystem,
});

export const STRATEGY_GAMES = Object.freeze([
  grailGame, auctionGame, fireGame, pigGame, lightGame, mineGame,
]);
