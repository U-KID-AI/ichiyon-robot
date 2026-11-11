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
export const GRAIL_POISON_DELAY_TICKS = 100;
export const GRAIL_RESULT_MIN_TICKS = 200;
export const GRAIL_ENDING_TICKS = 200;
const GRAIL_EXCHANGES_PER_ROUND = 2;
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
const grailRackReady = state => state.racks.cup.trueIndex !== null &&
  state.racks.potion.holyIndex !== null;
const grailNextRound = round => {
  const cupOwner = (round - 1) % 2;
  return {
    phase: "arrange",
    inputRemaining: JUNKET_INPUT_TICKS,
    round,
    actor: cupOwner,
    grailSide: cupOwner,
    cupOwner,
    potionOwner: 1 - cupOwner,
    racks: {
      cup: { trueIndex: null, used: [] },
      potion: { holyIndex: null, used: [] },
    },
    cupIndex: null,
    potionIndex: null,
    exchange: 0,
    roundPoisonCup: [0, 0],
    roundHolyCup: [0, 0],
    last: null,
    winner: null,
    reason: null,
  };
};
function grailSettle(state) {
  // open/settle pours the large grail into each player's vessel. It does not
  // force drinking. cup/player-* is an independent voluntary interaction.
  const settled = {
    ...state,
    poisonCup: state.poisonCup.map((value, seat) => value + state.roundPoisonCup[seat]),
    holyCup: state.holyCup.map((value, seat) => value + state.roundHolyCup[seat]),
    roundPoisonCup: [0, 0], roundHolyCup: [0, 0],
  };
  if (state.round >= GRAIL_ROUNDS) {
    const winner = state.holy[0] === state.holy[1] ? null
      : state.holy[0] > state.holy[1] ? 0 : 1;
    return { ...settled, phase: "final_drink", actor: 0, winner,
      reason: "holy_volume", resultTicks: 0 };
  }
  return { ...settled, ...grailNextRound(state.round + 1) };
}
function grailDrink(state, seat) {
  const poisonCup = [...state.poisonCup], holyCup = [...state.holyCup];
  if (poisonCup[seat] + holyCup[seat] <= 0) throw new Error("自分の運命の器は空です。");
  const poison = [...state.poison];
  const neutralised = Math.floor(holyCup[seat] / 2);
  poison[seat] += poisonCup[seat] - neutralised;
  const pendingDamage = [...state.pendingDamage], poisonRemaining = [...state.poisonRemaining];
  // Drinking clears an earlier poison schedule, including when holy water
  // cancels this dose. Negative stored poison is valid in the original.
  pendingDamage[seat] = poisonCup[seat] > neutralised ? grailDamage(poison[seat]) : 0;
  poisonRemaining[seat] = pendingDamage[seat] > 0 ? GRAIL_POISON_DELAY_TICKS : 0;
  poisonCup[seat] = 0; holyCup[seat] = 0;
  const actor = state.phase === "final_drink"
    ? [0, 1].find(player => poisonCup[player] + holyCup[player] > 0) ?? null : state.actor;
  return { ...state, poison, poisonCup, holyCup, pendingDamage, poisonRemaining, actor };
}
function grailResult(state, cupIndex, potionIndex) {
  const grailSeat = state.grailSide;
  const potionSeat = 1 - grailSeat;
  const trueCup = cupIndex === state.racks.cup.trueIndex;
  const holyPotion = potionIndex === state.racks.potion.holyIndex;
  // The source datapack compares the cup result (-1 true / +1 fake) with
  // the potion result (+1 holy / -1 poison). A holy dose is therefore the
  // XOR of the two hidden booleans, not an XNOR.
  const holy = trueCup !== holyPotion;
  const drinker = grailSeat;
  const holyScore = [...state.holy];
  const poisonCup = [...state.roundPoisonCup];
  const holyCup = [...state.roundHolyCup];
  if (holy) {
    holyScore[drinker] += GRAIL_HOLY_DOSE;
    holyCup[drinker] += GRAIL_HOLY_DOSE;
  } else {
    poisonCup[drinker] += GRAIL_HOLY_DOSE;
  }
  const racks = {
    cup: {
      ...state.racks.cup,
      used: [...state.racks.cup.used, cupIndex],
    },
    potion: {
      ...state.racks.potion,
      used: [...state.racks.potion.used, potionIndex],
    },
  };
  const next = {
    ...state,
    racks,
    holy: holyScore,
    roundPoisonCup: poisonCup,
    roundHolyCup: holyCup,
    cupIndex: null,
    potionIndex: null,
    exchange: state.exchange + 1,
    last: {
      round: state.round,
      exchange: state.exchange + 1,
      kind: holy ? "holy" : "poison",
      drinker,
      grailSeat,
      potionSeat,
      trueCup,
      holyPotion,
    },
    winner: null,
    reason: null,
  };
  if (next.exchange >= GRAIL_EXCHANGES_PER_ROUND) return grailSettle(next);
  const grailSide = 1 - state.grailSide;
  return { ...next, grailSide, actor: grailSide, phase: "cup", inputRemaining: JUNKET_INPUT_TICKS };
}
export function createGrail() {
  return {
    ...grailNextRound(1),
    phase: "arrange",
    round: 1,
    hp: [GRAIL_HEALTH, GRAIL_HEALTH],
    holy: [0, 0],
    poison: [0, 0],
    poisonCup: [0, 0], holyCup: [0, 0],
    pendingDamage: [0, 0], poisonRemaining: [0, 0], elapsedTicks: 0,
    resultTicks: 0, endingRemaining: 0,
  };
}
export function advanceGrail(state, seat, action, value) {
  requireSeat(seat, 2);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "drink") return grailDrink(state, seat);
  if (action === "arrange") {
    // game:grail/set/player-* enables both rearrangers together and waits for
    // both confirmations. The actor field is only the first pending seat.
    if (state.phase !== "arrange") throw new Error("現在は配置できません。");
    const cupOwner = seat === state.cupOwner;
    if (cupOwner ? state.racks.cup.trueIndex !== null : state.racks.potion.holyIndex !== null) {
      throw new Error("このラウンドの配置は確定済みです。");
    }
    const index = cupOwner ? value?.trueIndex : value?.holyIndex;
    if (!Number.isInteger(index) || index < 0 || index > 2) {
      throw new Error(cupOwner ? "本物の聖杯の位置を選んでください。"
        : "聖水ポーションの位置を選んでください。");
    }
    const racks = cupOwner
      ? { ...state.racks, cup: { ...state.racks.cup, trueIndex: index } }
      : { ...state.racks, potion: { ...state.racks.potion, holyIndex: index } };
    const next = { ...state, racks };
    if (!grailRackReady(next)) {
      return { ...next, actor: 1 - seat };
    }
    return { ...next, phase: "cup", actor: state.grailSide, inputRemaining: JUNKET_INPUT_TICKS };
  }
  if (action === "cup") {
    requireTurn(state, seat, "cup");
    if (seat !== state.grailSide) throw new Error("聖杯側の手番ではありません。");
    if (!Number.isInteger(value) || value < 0 || value > 2) throw new Error("聖杯を選んでください。");
    if (state.racks.cup.used.includes(value)) throw new Error("その聖杯はすでに選ばれています。");
    return { ...state, cupIndex: value, phase: "potion", actor: 1 - state.grailSide,
      inputRemaining: JUNKET_INPUT_TICKS };
  }
  if (action === "potion") {
    requireTurn(state, seat, "potion");
    if (seat === state.grailSide) throw new Error("ポーション側の手番ではありません。");
    if (!Number.isInteger(value) || value < 0 || value > 2) throw new Error("ポーションを選んでください。");
    if (state.racks.potion.used.includes(value)) throw new Error("そのポーションはすでに選ばれています。");
    return grailResult(state, state.cupIndex, value);
  }
  throw new Error("不正な操作です。");
}
export function grailSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "tick") {
    const elapsedTicks = (state.elapsedTicks ?? 0) + 1;
    const poisonRemaining = state.poisonRemaining.map(value => Math.max(0, value - 1));
    const pendingDamage = [...state.pendingDamage], hp = [...state.hp];
    if (elapsedTicks % 5 === 0) for (let seat = 0; seat < 2; seat++) {
      if (poisonRemaining[seat] === 0 && pendingDamage[seat] > 0) {
        hp[seat] = Math.max(0, hp[seat] - pendingDamage[seat]); pendingDamage[seat] = 0;
      }
    }
    const next = { ...state, elapsedTicks, poisonRemaining, pendingDamage, hp };
    if (state.phase === "ending") {
      const endingRemaining = Math.max(0, state.endingRemaining - 1);
      return endingRemaining === 0 ? grailFinish(next, state.winner, state.reason)
        : { ...next, endingRemaining };
    }
    if (state.phase === "final_drink") {
      const resultTicks = state.resultTicks + 1;
      return resultTicks >= GRAIL_RESULT_MIN_TICKS &&
        state.poisonCup.every((value, seat) => value + state.holyCup[seat] === 0)
        ? { ...next, phase: "ending", actor: null, resultTicks, endingRemaining: GRAIL_ENDING_TICKS }
        : { ...next, resultTicks };
    }
    if (hp.some(value => value <= 0)) return grailFinish(next,
      hp.every(value => value <= 0) ? null : hp[0] <= 0 ? 1 : 0, "health");
    if (["arrange", "cup", "potion"].includes(state.phase)) {
      const inputRemaining = Math.max(0, (state.inputRemaining ?? JUNKET_INPUT_TICKS) - 1);
      return inputRemaining === 0 ? grailSystem({ ...next, inputRemaining }, "timeout")
        : { ...next, inputRemaining };
    }
    return next;
  }
  if (action === "timeout") {
    if (["final_drink", "ending"].includes(state.phase)) return state;
    if (state.phase === "arrange" && !grailRackReady(state) &&
        state.racks.cup.trueIndex === null && state.racks.potion.holyIndex === null) {
      return grailFinish(state, null, "timeout_both");
    }
    return grailFinish(state, 1 - state.actor, "timeout");
  }
  throw new Error("不正な内部操作です。");
}
export function grailView(state) {
  return {
    phase: state.phase, round: state.round, exchange: state.exchange,
    actor: state.actor, grailSide: state.grailSide,
    hp: [...state.hp], holy: [...state.holy], poison: [...state.poison],
    poisonCup: [...state.poisonCup], holyCup: [...state.holyCup],
    roundPoisonCup: [...state.roundPoisonCup], roundHolyCup: [...state.roundHolyCup],
    poisonRemaining: [...state.poisonRemaining], resultTicks: state.resultTicks,
    endingRemaining: state.endingRemaining, inputRemaining: state.inputRemaining,
    cupOwner: state.cupOwner, potionOwner: state.potionOwner,
    arrangementsReady: [state.racks.cup.trueIndex !== null, state.racks.potion.holyIndex !== null],
    usedCups: [...state.racks.cup.used], usedPotions: [...state.racks.potion.used],
    last: state.last && { ...state.last },
    winner: state.winner, reason: state.reason,
  };
}
const GRAIL_PHASE_LABELS = Object.freeze({
  arrange: "配置", cup: "聖杯選択", potion: "ポーション選択",
  final_drink: "結果発表・運命の器を飲む", ending: "終了演出",
  finished: "終了",
});
export function grailVenuePresentation(state, names = ["1P", "2P"]) {
  const playerNames = [names[0] ?? "1P", names[1] ?? "2P"];
  if (!state) {
    return {
      tv: "アンハッピー・ホーリーグレイル\n受付で2人そろうと開始します",
      instruction: "聖杯棚とポーション棚を確認してください。",
      result: "結果表示なし",
      cupRack: ["聖杯棚 1", "聖杯棚 2", "聖杯棚 3"],
      potionRack: ["ポーション棚 1", "ポーション棚 2", "ポーション棚 3"],
    };
  }
  const phase = GRAIL_PHASE_LABELS[state.phase] ?? state.phase;
  const cupOwner = playerNames[state.cupOwner] ?? "聖杯側";
  const potionOwner = playerNames[state.potionOwner] ?? "ポーション側";
  let instruction;
  if (state.phase === "arrange") {
    instruction = `同時配置: ${cupOwner} は本物の聖杯、${potionOwner} は聖水の位置を選択`;
  } else if (state.phase === "cup") {
    instruction = `${playerNames[state.grailSide]} は聖杯棚から1つ選択`;
  } else if (state.phase === "potion") {
    instruction = `${playerNames[1 - state.grailSide]} はポーション棚から1つ選択`;
  } else if (state.phase === "final_drink") {
    instruction = "最終結果: 両者とも残っている飲み物をメニューから飲んでください";
  } else if (state.phase === "ending") {
    instruction = "終了処理中";
  } else {
    instruction = state.winner === null ? "引き分け" : `${playerNames[state.winner]} の勝ち`;
  }
  const last = state.last;
  const result = last
    ? `第${last.round}R 交換${last.exchange}/2  ${playerNames[last.drinker]} は${last.kind === "holy" ? "聖水" : "毒"}`
    : "このラウンドの結果はまだ公開されていません";
  // The used arrays record exchanged positions, not whether the owner has
  // finished arranging. Marking an individual slot from either array would
  // both lie about readiness and leak the secret position.
  const cupReady = state.arrangementsReady?.[0] ?? (state.racks?.cup?.trueIndex != null);
  const potionReady = state.arrangementsReady?.[1] ?? (state.racks?.potion?.holyIndex != null);
  const cup = Array.from({ length: 3 }, (_, index) =>
    `聖杯棚 ${index + 1}${cupReady ? "・配置済" : ""}`);
  const potion = Array.from({ length: 3 }, (_, index) =>
    `ポーション棚 ${index + 1}${potionReady ? "・配置済" : ""}`);
  return {
    tv: `第${state.round}/10R  交換${state.exchange + 1}/2  ${phase}\n`
      + `${playerNames[0]} 聖水${state.holy[0]}ml 毒${state.poison[0]} HP${state.hp[0]}\n`
      + `${playerNames[1]} 聖水${state.holy[1]}ml 毒${state.poison[1]} HP${state.hp[1]}`,
    instruction,
    result,
    cupRack: cup,
    potionRack: potion,
  };
}
export function grailViewForPlayer(state, seat) {
  requireSeat(seat, 2);
  return { ...grailView(state), self: { seat,
    arrangement: seat === state.cupOwner
      ? { trueIndex: state.racks.cup.trueIndex } : { holyIndex: state.racks.potion.holyIndex } } };
}
export const grailGame = Object.freeze({
  id: "grail", title: "アンハッピー・ホーリーグレイル", seats: 2,
  create: createGrail, advance: advanceGrail, view: grailView, viewForPlayer: grailViewForPlayer,
  systemAdvance: grailSystem,
});

// ---------------------------------------------------------------- Auction

export const AUCTION_SEATS = 4;
export const AUCTION_TEAM_A = Object.freeze([0, 2]);
export const AUCTION_TEAM_B = Object.freeze([1, 3]);
export const AUCTION_DIAMONDS_TO_WIN = 3;
export const AUCTION_MAX_PAYMENT = 15;
export const AUCTION_PAYMENT_LIMIT = 16;
export const AUCTION_LOW_BID_DAMAGE = 1700;
export const AUCTION_HIGH_BID_DAMAGE = 8000;
export const AUCTION_HP_TICK_STEP = 5;
const teamOf = seat => AUCTION_TEAM_A.includes(seat) ? 0 : 1;
const auctionFinish = (state, winnerTeam, reason) => ({
  ...state, phase: "finished", actor: null, winnerTeam, winner: null, reason,
});
const auctionBidOrder = host => Array.from(
  { length: AUCTION_SEATS }, (_, offset) => (host + offset) % AUCTION_SEATS);
function resetAuctionRound(state, round = state.round + 1) {
  const bidOrder = auctionBidOrder(state.host);
  return {
    ...state, phase: "bid", round, bidOrder, bidIndex: 0, actor: bidOrder[0],
    bids: [null, null, null, null], last: null, lastWinner: null, phaseTicks: 0, phaseRemaining: 0,
  };
}
function resolveAuction(state) {
  const counts = [0, 0, 0, 0, 0];
  for (const bid of state.bids) counts[bid]++;
  const value = [4, 3, 2, 1].find(candidate => counts[candidate] === 1) ?? null;
  const winner = value === null ? null : state.bids.indexOf(value);
  const roundWins = [...state.roundWins];
  const payment = [...state.payment];
  const damage = [0, 0, 0, 0];
  let next = {
    ...state, phase: "result", actor: null, phaseTicks: 0,
    phaseRemaining: 95, last: { bids: [...state.bids], value, winner },
    lastWinner: winner, host: (state.host + 1) % AUCTION_SEATS, damage,
  };
  if (winner !== null) {
    const team = teamOf(winner);
    roundWins[team]++;
    payment[winner] += value;
    // open/open lasts55 ticks before win/player-* begins. The latter skips
    //50 ticks below payment16; a fatal payment additionally waits100 ticks.
    next = { ...next, payment, roundWins, phase: "payment", phaseRemaining:
      55 + (payment[winner] >= AUCTION_PAYMENT_LIMIT ? 290 : 140) };
  }
  return next;
}
function auctionSettle(state) {
  const winner = state.lastWinner;
  const diamonds = [...state.diamonds];
  const roundWins = state.roundWins;
  let next = state;
  if (winner !== null) {
    const team = teamOf(winner);
    if (state.payment[winner] >= AUCTION_PAYMENT_LIMIT) return auctionFinish(next, 1 - team, "payment");
    if (roundWins[team] >= 2) {
      diamonds[team]++;
      next = { ...next, phase: "set_result", diamonds };
      if (diamonds[team] >= AUCTION_DIAMONDS_TO_WIN) return auctionFinish(next, team, "diamonds");
      return next;
    }
  }
  if (state.round >= 4) return { ...next, phase: "set_result", roundWins };
  return resetAuctionRound({ ...next, roundWins }, state.round + 1);
}
export function createAuction() {
  return {
    phase: "bid", set: 1, round: 1, host: 0, bidOrder: [0, 1, 2, 3], bidIndex: 0,
    actor: 0, bids: [null, null, null, null],
    selected: [[], [], [], []], roundWins: [0, 0], diamonds: [0, 0],
    payment: [0, 0, 0, 0], hp: [JUNKET_MAX_HP, JUNKET_MAX_HP, JUNKET_MAX_HP, JUNKET_MAX_HP],
    damage: [0, 0, 0, 0], pendingDamage: [0, 0, 0, 0], hpClock: 0,
    phaseTicks: 0, phaseRemaining: 0, rulesVersion: 2, last: null, lastWinner: null,
    winnerTeam: null, winner: null, reason: null,
  };
}
export function advanceAuction(state, seat, action, value) {
  requireSeat(seat, AUCTION_SEATS);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action !== "bid") throw new Error("不正な操作です。");
  requireTurn(state, seat, "bid");
  if (!Number.isInteger(value) || value < 1 || value > 4) throw new Error("1〜4の札を選んでください。");
  if (state.selected[seat].includes(value)) throw new Error("このセットでは同じ札を2回使えません。");
  const bids = [...state.bids];
  const selected = state.selected.map(cards => [...cards]);
  bids[seat] = value;
  selected[seat].push(value);
  const bidIndex = state.bidIndex + 1;
  if (bidIndex < AUCTION_SEATS) {
    return { ...state, bids, selected, bidIndex, actor: state.bidOrder[bidIndex] };
  }
  return resolveAuction({ ...state, bids, selected });
}
export function auctionSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "tick") {
    const hpClock = ((state.hpClock ?? 0) + 1) % AUCTION_HP_TICK_STEP;
    const phaseTicks = (state.phaseTicks ?? 0) + 1;
    const pendingDamage = [...state.pendingDamage];
    const damage = [...state.damage];
    const hp = [...state.hp];
    if (state.phase === "payment") {
      const seat = state.lastWinner, total = state.payment[seat];
      const elapsed = phaseTicks - 55;
      const clock = 6000 - elapsed - (total < AUCTION_PAYMENT_LIMIT && elapsed >= 50 ? 50 : 0);
      const lower = total <= 4 ? 5835 : total <= 9 ? 5830 : total <= 15 ? 5820 : 5810;
      if (clock >= lower && clock <= 5840) pendingDamage[seat] =
        total >= AUCTION_PAYMENT_LIMIT ? AUCTION_HIGH_BID_DAMAGE : AUCTION_LOW_BID_DAMAGE;
    }
    if (hpClock === 0) for (let seat = 0; seat < AUCTION_SEATS; seat++) {
      hp[seat] = Math.max(0, hp[seat] - pendingDamage[seat]);
      damage[seat] += pendingDamage[seat]; pendingDamage[seat] = 0;
    }
    const phaseRemaining = ["payment", "result"].includes(state.phase)
      ? Math.max(0, state.phaseRemaining - 1) : state.phaseRemaining;
    const next = { ...state, hpClock, phaseTicks, phaseRemaining, pendingDamage, damage, hp };
    return ["payment", "result"].includes(state.phase) && phaseRemaining === 0 ? auctionSettle(next) : next;
  }
  if (action === "timeout") {
    if (state.phase !== "bid") return state;
    return auctionFinish(state, 1 - teamOf(state.actor), "timeout");
  }
  if (action === "next_set" && state.phase === "set_result") {
    return resetAuctionRound({
      ...state, set: state.set + 1, roundWins: [0, 0],
      selected: [[], [], [], []],
    }, 1);
  }
  throw new Error("不正な内部操作です。");
}
export function auctionView(state) {
  const hidden = state.phase === "bid" || ["payment", "result"].includes(state.phase) && state.phaseTicks < 40;
  return {
    phase: state.phase, set: state.set, round: state.round, host: state.host,
    phaseRemaining: state.phaseRemaining,
    bidOrder: [...state.bidOrder], bidIndex: state.bidIndex, actor: state.actor,
    bids: hidden ? state.bids.map(() => null) : [...state.bids],
    // A face-down bid is not a publicly spent card until all four bids open.
    // Publishing the selected array unchanged disclosed each current bid.
    selected: state.selected.map((cards, seat) => hidden && state.bids[seat] !== null
      ? cards.filter(card => card !== state.bids[seat]) : [...cards]),
    ready: state.bids.map(bid => bid !== null), roundWins: [...state.roundWins],
    diamonds: [...state.diamonds], payment: [...state.payment],
    hp: [...state.hp], damage: [...state.damage],
    last: !hidden && state.last ? { ...state.last, bids: [...state.last.bids] } : null,
    lastWinner: state.lastWinner, winnerTeam: state.winnerTeam, winner: state.winner, reason: state.reason,
  };
}
export function auctionViewForPlayer(state, seat) {
  requireSeat(seat, AUCTION_SEATS);
  const result = auctionView(state);
  result.selected[seat] = [...state.selected[seat]];
  return { ...result, self: { seat, bid: state.bids[seat], used: [...state.selected[seat]] } };
}
export const auctionGame = Object.freeze({
  id: "auction", title: "ライフ・イズ・オークショニア", seats: 4,
  create: createAuction, advance: advanceAuction, view: auctionView, viewForPlayer: auctionViewForPlayer,
  systemAdvance: auctionSystem,
});

// ---------------------------------------------------------------- Fire

// The source datapack gives each player six cards: rock/scissors/paper in
// HOT and ICE variants. A card's number decides the RPS result, while its
// temperature decides the stock added to the winner's room.
export const FIRE_ROUNDS_PER_ATTEMPT = 6;
export const FIRE_SERIES_COUNT = 4;
export const FIRE_ROUND_JUDGE_TICKS = 70;
export const FIRE_ROUND_DAMAGE_TICKS = 120;
export const FIRE_ROUND_RESULT_TICKS = 140;
export const FIRE_SERIES_SETTLE_TICKS = 50;
export const FIRE_SERIES_RESULT_TICKS = 180;
export const FIRE_WAIT_TICKS = 600;
export const FIRE_WAIT_DAMAGE_FIRST = 30;
export const FIRE_WAIT_DAMAGE_CYCLE = 50;
export const FIRE_BURN_TICKS = 6000;
export const FIRE_BURN_DELAY_TICKS = 180;
export const FIRE_TICK_STEP = 1;
// zzz_hp/1 schedules /2 after one tick; /2 schedules /1 after four more.
export const FIRE_HP_TICK_STEP = 5;
export const FIRE_GLASS_BREAK_TICKS = 300;
export const FIRE_BURN_DAMAGE = 900;
export const FIRE_SANCTION_TICKS = 100;
export const FIRE_END_DELAY_TICKS = 100;
export const FIRE_WAIT_END_DELAY_TICKS = 300;
export const FIRE_WIND_MULTIPLIER = 3;
export const FIRE_CARD_POWER = Object.freeze({ 1: 0, 2: 2, 3: 5 });
export const FIRE_CARD_LABELS = Object.freeze({
  "1:hot": "グー HOT", "2:hot": "チョキ HOT", "3:hot": "パー HOT",
  "1:ice": "グー ICE", "2:ice": "チョキ ICE", "3:ice": "パー ICE",
});
export const FIRE_CONTROL_CARDS = Object.freeze({
  rock_hot_1: Object.freeze({ id: "1:hot", seat: 0 }),
  scissors_hot_1: Object.freeze({ id: "2:hot", seat: 0 }),
  paper_hot_1: Object.freeze({ id: "3:hot", seat: 0 }),
  rock_ice_1: Object.freeze({ id: "1:ice", seat: 0 }),
  scissors_ice_1: Object.freeze({ id: "2:ice", seat: 0 }),
  paper_ice_1: Object.freeze({ id: "3:ice", seat: 0 }),
  rock_hot_2: Object.freeze({ id: "1:hot", seat: 1 }),
  scissors_hot_2: Object.freeze({ id: "2:hot", seat: 1 }),
  paper_hot_2: Object.freeze({ id: "3:hot", seat: 1 }),
  rock_ice_2: Object.freeze({ id: "1:ice", seat: 1 }),
  scissors_ice_2: Object.freeze({ id: "2:ice", seat: 1 }),
  paper_ice_2: Object.freeze({ id: "3:ice", seat: 1 }),
});
export const FIRE_PROP_FRAME = Object.freeze({
  available: 0, used: 1, hot: 2, ice: 3, active: 4, television: 5,
  glassHealthy: 6, glassCrack1: 7, glassCrack2: 8, glassCrack3: 9,
  glassCrack4: 10, glassBroken: 11,
});
const FIRE_CHOICES = Object.freeze({ rock: 1, scissors: 2, paper: 3 });
const fireFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, winner, reason,
});
const fireBeginEnding = (state, winner, reason, ticks) => ({
  ...state, phase: "ending", actor: null, winner, reason,
  endingRemaining: ticks, pendingDamage: [0, 0],
});
const fireCard = value => {
  if (!value || typeof value !== "object") return null;
  const number = Number.isInteger(value.number) ? value.number : FIRE_CHOICES[value.choice];
  const heat = value.heat === 1 || value.heat === -1 ? value.heat
    : value.hot === true ? 1 : value.hot === false ? -1 : null;
  return [1, 2, 3].includes(number) && heat !== null
    ? { number, heat, id: `${number}:${heat === 1 ? "hot" : "ice"}` }
    : null;
};
const fireRoundWinner = (first, second) => {
  if (first === second) return null;
  if (first === 1 && second === 2) return 0;
  if (first === 2 && second === 1) return 1;
  if (first === 2 && second === 3) return 0;
  if (first === 3 && second === 2) return 1;
  if (first === 3 && second === 1) return 0;
  return 1;
};

// These are the damage values assigned by fire/temperature/player*-temperature.
// The source table is intentionally asymmetric: cold and hot use different
// bands, and 11..35 has no assignment, so the per-tick damage variable stays
// reset and no health is lost in that comfortable range.
export const fireTemperatureDamage = temperature => {
  const value = Math.trunc(Number(temperature));
  if (!Number.isFinite(value)) return 0;
  if (value <= -100) return 60000;
  if (value <= -90) return 30000;
  if (value <= -80) return 22500;
  if (value <= -70) return 15000;
  if (value <= -60) return 9000;
  if (value <= -50) return 7500;
  if (value <= -40) return 6000;
  if (value <= -30) return 4000;
  if (value <= -26) return 3000;
  if (value <= -20) return 2250;
  if (value <= -15) return 1800;
  if (value <= -11) return 1500;
  if (value <= -7) return 1000;
  if (value <= -4) return 750;
  if (value <= -1) return 600;
  if (value <= 4) return 500;
  if (value <= 7) return 300;
  if (value <= 10) return 100;
  if (value <= 35) return 0;
  if (value <= 40) return 30;
  if (value <= 45) return 45;
  if (value <= 50) return 60;
  if (value <= 60) return 75;
  if (value <= 70) return 150;
  if (value <= 75) return 300;
  if (value <= 80) return 480;
  if (value <= 83) return 600;
  if (value <= 87) return 900;
  if (value <= 90) return 1200;
  if (value <= 99) return 1500;
  if (value === 100) return 1800;
  if (value <= 110) return 1950;
  if (value <= 120) return 2400;
  if (value <= 130) return 3000;
  if (value <= 140) return 4500;
  if (value <= 150) return 6000;
  if (value <= 160) return 8100;
  if (value <= 170) return 10500;
  if (value <= 180) return 13500;
  if (value <= 190) return 16500;
  if (value <= 200) return 19500;
  if (value <= 210) return 22500;
  if (value <= 220) return 30000;
  return 60000;
};

// fire/temperature/set assigns these wind values; player*-wind multiplies by
// three whenever the per-player wind timer is still running.
export const fireWindPower = temperature => {
  const value = Math.trunc(Number(temperature));
  if (!Number.isFinite(value)) return 0;
  if (value <= -130) return 1000;
  if (value <= -120) return 500;
  if (value <= -110) return 450;
  if (value <= -100) return 405;
  if (value <= -90) return 300;
  if (value <= -80) return 252;
  if (value <= -70) return 216;
  if (value <= -60) return 181;
  if (value <= -50) return 148;
  if (value <= -40) return 121;
  if (value <= -30) return 102;
  if (value <= -26) return 84;
  if (value <= -20) return 68;
  if (value <= -16) return 54;
  if (value <= -11) return 48;
  if (value <= -7) return 40;
  if (value <= -4) return 35;
  if (value <= -1) return 30;
  if (value <= 4) return 23;
  if (value <= 7) return 20;
  if (value <= 10) return 18;
  if (value <= 15) return 15;
  if (value <= 20) return 5;
  // The source scoreboard has no 21..40 assignment. Its wind value is retained
  // from the previous settlement, so null tells the caller to preserve it.
  if (value <= 40) return null;
  if (value <= 45) return 15;
  if (value <= 50) return 20;
  if (value <= 60) return 25;
  if (value <= 70) return 45;
  if (value <= 75) return 60;
  if (value <= 80) return 80;
  if (value <= 83) return 100;
  if (value <= 87) return 150;
  if (value <= 89) return 200;
  if (value <= 99) return 250;
  if (value === 100) return 300;
  if (value <= 110) return 325;
  if (value <= 120) return 400;
  if (value <= 130) return 500;
  if (value <= 140) return 750;
  return 1000;
};
export const fireWindDamage = temperature =>
  (fireWindPower(temperature) ?? 0) * FIRE_WIND_MULTIPLIER;

export function firePurgeWinner(totalWins, spend) {
  if (totalWins[0] !== totalWins[1]) return totalWins[0] > totalWins[1] ? 0 : 1;
  if (spend[0] !== spend[1]) return spend[0] < spend[1] ? 0 : 1;
  return 0;
}

const fireHpAfter = (hp, damage) => hp.map((value, seat) =>
  Math.max(0, value - damage[seat]));
const fireLethalWinner = hp => {
  if (hp[0] > 0 && hp[1] <= 0) return 0;
  if (hp[1] > 0 && hp[0] <= 0) return 1;
  if (hp[0] <= 0 && hp[1] <= 0) return 0;
  return null;
};
function fireResolveChoices(state) {
  const [first, second] = state.choices;
  const winner = fireRoundWinner(first.number, second.number);
  const wins = [...state.wins];
  const totalWins = [...state.totalWins];
  const stock = [...state.stock];
  let change = 0;
  if (winner !== null) {
    wins[winner]++;
    totalWins[winner]++;
    change = FIRE_CARD_POWER[state.choices[winner].number] * state.choices[winner].heat;
    stock[winner] += change;
  }
  return {
    ...state, wins, totalWins, stock, revealResolved: true,
    last: {
      round: state.round, first: { ...first }, second: { ...second },
      winner, change, tie: winner === null,
    },
  };
}
const fireAssign = (state, assignments) => {
  const pendingDamage = [...(state.pendingDamage ?? [0, 0])];
  assignments.forEach((value, seat) => {
    if (value !== null && value !== undefined) pendingDamage[seat] = value;
  });
  return { ...state, pendingDamage };
};
const fireAssignTemperature = state => fireAssign(state, state.temperature.map((value, seat) =>
  state.glass?.[seat] === 0 ? null : fireTemperatureDamage(value)));
const fireApplyZzzHp = state => ({
  ...state,
  hp: fireHpAfter(state.hp, state.pendingDamage ?? [0, 0]),
  pendingDamage: [0, 0],
});
const fireZzzDue = ticks => ticks % FIRE_HP_TICK_STEP === 0;
function fireAfterHp(state, reason, endingTicks = FIRE_END_DELAY_TICKS) {
  const dead = fireLethalWinner(state.hp);
  return dead === null ? state : fireBeginEnding(state, dead, reason, endingTicks);
}
function fireNextRound(state) {
  const reachesSeries = state.round >= FIRE_ROUNDS_PER_ATTEMPT &&
    (state.wins[0] >= 3 || state.wins[1] >= 3);
  if (reachesSeries) {
    return { ...state, phase: "series_result", actor: null, phaseTicks: 0 };
  }
  if (state.round >= FIRE_ROUNDS_PER_ATTEMPT) {
    // The source keeps stock/temperature but redeals all six cards when a
    // six-round attempt ends without either player reaching three wins.
    return {
      ...state, phase: "choice", actor: 0, round: 1, wins: [0, 0],
      choices: [null, null], used: [[], []], phaseTicks: 0,
      revealResolved: false,
    };
  }
  return {
    ...state, phase: "choice", actor: 0, round: state.round + 1,
    choices: [null, null], phaseTicks: 0, revealResolved: false,
  };
}
function firePrepareSettlement(state) {
  const stock = [...state.stock];
  const temperature = state.temperature.map((value, seat) => value + stock[seat] - 10);
  const windRemaining = stock.map(value => Math.max(0, Math.abs(value - 10) * 20));
  const windPower = temperature.map((value, seat) => {
    const power = fireWindPower(value);
    return power === null ? (state.windPower?.[seat] ?? 0) : power;
  });
  return {
    ...state, temperature, windRemaining, windPower,
    lastWindStock: [...stock], seriesPrepared: true,
  };
}
function fireStartWind(state) {
  return {
    ...state, phase: "wind", actor: null, phaseTicks: 0,
    windRemaining: [...state.windRemaining], windPower: [...state.windPower],
  };
}
function fireFinishWind(state) {
  const series = state.series + 1;
  if (series > FIRE_SERIES_COUNT) {
    return {
      ...state, phase: "wait", actor: null, phaseTicks: 0,
      waitRemaining: FIRE_WAIT_TICKS, waitDamageTimer: 0,
      stock: [0, 0], windRemaining: [0, 0],
    };
  }
  return {
    ...state, phase: "choice", actor: 0, series, round: 1, wins: [0, 0],
    choices: [null, null], used: [[], []], phaseTicks: 0, windRemaining: [0, 0],
    stock: [0, 0], revealResolved: false, seriesPrepared: false,
  };
}
function fireStartBurn(state) {
  // open/waiting sets the score to 110 and immediately calls
  // temperature/set, which subtracts 10. The burn phase therefore starts at
  // 100 C with the 100 C wind value, not at 110 C.
  const temperature = [100, 100];
  const windPower = [fireWindPower(100) ?? 0, fireWindPower(100) ?? 0];
  return {
    ...state, phase: "burn", actor: null, phaseTicks: 0,
    temperature, windPower, burnRemaining: FIRE_BURN_TICKS,
    burnDelayRemaining: FIRE_BURN_DELAY_TICKS,
  };
}
function fireStartSanction(state) {
  const targets = state.choices
    .map((choice, seat) => choice === null ? seat : null)
    .filter(seat => seat !== null);
  return {
    ...state, phase: "sanction", actor: null, phaseTicks: 0,
    sanctionTargets: targets, sanctionRemaining: FIRE_SANCTION_TICKS,
    sanctionAssigned: false, sanctionReason: "timeout",
  };
}
function fireStartPurge(state) {
  const winner = firePurgeWinner(state.totalWins, state.spend);
  return {
    ...state, phase: "sanction", actor: null, phaseTicks: 0,
    sanctionTargets: [1 - winner], sanctionRemaining: FIRE_SANCTION_TICKS,
    sanctionAssigned: false, sanctionReason: "purge",
  };
}

export function createFire() {
  return {
    phase: "choice", actor: 0, series: 1, round: 1,
    choices: [null, null], wins: [0, 0], totalWins: [0, 0],
    stock: [0, 0], used: [[], []], hp: [JUNKET_MAX_HP, JUNKET_MAX_HP],
    temperature: [30, 30], glass: [1, 1], spend: [0, 0], ticks: 0, phaseTicks: 0,
    windRemaining: [0, 0], waitRemaining: 0, burnRemaining: 0,
    burnDelayRemaining: 0, windPower: [0, 0], pendingDamage: [0, 0],
    revealResolved: false, seriesPrepared: false, waitDamageTimer: 0,
    sanctionTargets: [], sanctionRemaining: 0, sanctionAssigned: false,
    sanctionReason: null, endingRemaining: 0, glassStage: [0, 0],
    last: null, lastWindStock: null, winner: null, reason: null,
  };
}
export function advanceFire(state, seat, action, value) {
  requireSeat(seat, 2);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action !== "choice") throw new Error("不正な操作です。");
  // game:fire/set/set starts both clocks and accepts both sealed cards in
  // either order. Never give seat 1 a forced extra wait before it may answer.
  if (state.phase !== "choice") throw new Error("現在はカードを選べません。");
  if (state.choices[seat] !== null) throw new Error("このラウンドは選択済みです。");
  const card = fireCard(value);
  if (!card) throw new Error("HOTまたはICEのグー・チョキ・パーのカードを選んでください。");
  if (state.used[seat].includes(card.id)) throw new Error("そのカードはこのシリーズで使用済みです。");
  const choices = [...state.choices];
  const used = state.used.map((cards, index) => index === seat ? [...cards, card.id] : [...cards]);
  choices[seat] = card;
  const pending = choices.findIndex(choice => choice === null);
  if (pending >= 0) return { ...state, choices, used, actor: pending };
  return {
    ...state, choices, used, phase: "round_result", actor: null,
    phaseTicks: 0, revealResolved: false,
  };
}
export function fireSystem(state, action) {
  if (state.phase === "finished") return state;
  if (state.phase === "ending") {
    if (action !== "tick") return state;
    const endingRemaining = Math.max(0,
      (state.endingRemaining ?? FIRE_END_DELAY_TICKS) - FIRE_TICK_STEP);
    const next = {
      ...state, endingRemaining,
      ticks: state.ticks + FIRE_TICK_STEP,
      phaseTicks: state.phaseTicks + FIRE_TICK_STEP,
    };
    return endingRemaining === 0
      ? fireFinish(next, next.winner ?? 0, next.reason ?? "ending")
      : next;
  }
  if (action === "timeout") {
    if (state.phase !== "choice") return state;
    return fireStartSanction(state);
  }
  if (action !== "tick") throw new Error("不正な内部操作です。");
  const phaseTicks = state.phaseTicks + FIRE_TICK_STEP;
  const ticks = state.ticks + FIRE_TICK_STEP;
  const zzzDue = fireZzzDue(ticks);
  if (state.phase === "choice") {
    const spend = state.spend.map((value, seat) =>
      state.choices[seat] === null ? value + FIRE_TICK_STEP : value);
    const next = { ...state, spend, ticks, phaseTicks };
    return phaseTicks >= JUNKET_INPUT_TICKS ? fireStartSanction(next) : next;
  }
  if (state.phase === "round_result") {
    let next = { ...state, ticks, phaseTicks };
    if (!next.revealResolved && phaseTicks >= FIRE_ROUND_JUDGE_TICKS) {
      next = fireResolveChoices(next);
    }
    if (phaseTicks >= FIRE_ROUND_DAMAGE_TICKS) next = fireAssignTemperature(next);
    if (zzzDue) next = fireAfterHp(fireApplyZzzHp(next), "temperature");
    if (next.phase === "ending" || next.phase === "finished") return next;
    return phaseTicks >= FIRE_ROUND_RESULT_TICKS
      ? fireNextRound(next)
      : next;
  }
  if (state.phase === "series_result") {
    let next = { ...state, ticks, phaseTicks };
    if (!next.seriesPrepared && phaseTicks >= FIRE_SERIES_SETTLE_TICKS) {
      next = firePrepareSettlement(next);
    }
    return phaseTicks >= FIRE_SERIES_RESULT_TICKS
      ? fireStartWind(next)
      : next;
  }
  if (state.phase === "wind") {
    const glass = state.glass ?? [1, 1];
    const windRemaining = state.windRemaining.map((value, seat) =>
      Math.max(0, value - FIRE_TICK_STEP * (glass[seat] === 0 ? 5 : 1)));
    let next = { ...state, windRemaining, ticks, phaseTicks };
    if (zzzDue) {
      const damage = state.temperature.map((value, seat) =>
        glass[seat] === 1 && windRemaining[seat] > 0
          ? (state.windPower?.[seat] ?? fireWindPower(value) ?? 0) * FIRE_WIND_MULTIPLIER
          : null);
      next = fireAssign(next, damage);
      next = fireAfterHp(fireApplyZzzHp(next), "temperature");
      if (next.phase === "ending" || next.phase === "finished") return next;
    }
    // The source cracks the glass at 60/80/100/120 ticks and breaks it at 300
    // ticks when the settled stock is -70 or colder. A broken glass stops
    // damage and drains the remaining wind timer five times faster.
    const glassStage = [...(state.glassStage ?? [0, 0])];
    (state.lastWindStock ?? [0, 0]).forEach((stock, seat) => {
      if (stock > -70 || glass[seat] === 0) return;
      if (phaseTicks >= 120) glassStage[seat] = 4;
      else if (phaseTicks >= 100) glassStage[seat] = 3;
      else if (phaseTicks >= 80) glassStage[seat] = 2;
      else if (phaseTicks >= 60) glassStage[seat] = 1;
    });
    const nextGlass = glass.map((value, seat) =>
      value === 1 && phaseTicks >= FIRE_GLASS_BREAK_TICKS &&
        (state.lastWindStock?.[seat] ?? 0) <= -70 ? 0 : value);
    glassStage.forEach((value, seat) => {
      if (nextGlass[seat] === 0) glassStage[seat] = 5;
    });
    next = { ...next, glass: nextGlass, glassStage };
    return windRemaining.every(value => value === 0) ? fireFinishWind(next) : next;
  }
  if (state.phase === "wait") {
    const waitDamageTimer = (state.waitDamageTimer ?? 0) + FIRE_TICK_STEP;
    const cycleTimer = waitDamageTimer >= FIRE_WAIT_DAMAGE_CYCLE ? 0 : waitDamageTimer;
    let next = { ...state, waitDamageTimer: cycleTimer, ticks, phaseTicks };
    if (cycleTimer === FIRE_WAIT_DAMAGE_FIRST) next = fireAssignTemperature(next);
    if (zzzDue) next = fireAfterHp(fireApplyZzzHp(next), "temperature",
      FIRE_WAIT_END_DELAY_TICKS);
    if (next.phase === "ending" || next.phase === "finished") return next;
    const waitRemaining = Math.max(0, state.waitRemaining - FIRE_TICK_STEP);
    next = { ...next, waitRemaining };
    return waitRemaining === 0 ? fireStartBurn(next) : next;
  }
  if (state.phase === "burn") {
    const burnRemaining = Math.max(0, state.burnRemaining - FIRE_TICK_STEP);
    const burnDelayRemaining = Math.max(0, (state.burnDelayRemaining ?? 0) - FIRE_TICK_STEP);
    let next = { ...state, burnRemaining, burnDelayRemaining, ticks, phaseTicks };
    // burn/wind assigns 900 every tick after the 180-tick delay. zzz_hp then
    // applies the pending damage every fifth tick.
    if (burnDelayRemaining === 0) {
      next = fireAssign(next, (state.glass ?? [1, 1]).map(value =>
        value === 0 ? null : FIRE_BURN_DAMAGE));
    }
    if (zzzDue) next = fireAfterHp(fireApplyZzzHp(next), "temperature");
    if (next.phase === "ending" || next.phase === "finished") return next;
    if (burnRemaining === 0) {
      return fireStartPurge(next);
    }
    return next;
  }
  if (state.phase === "sanction") {
    const sanctionRemaining = Math.max(0, state.sanctionRemaining - FIRE_TICK_STEP);
    let next = { ...state, sanctionRemaining, ticks, phaseTicks };
    if (sanctionRemaining === 0 && !next.sanctionAssigned) {
      next = fireAssign(next, [0, 1].map(seat =>
        next.sanctionTargets.includes(seat) ? 60000 : null));
      next = { ...next, sanctionAssigned: true };
    }
    if (zzzDue) next = fireAfterHp(fireApplyZzzHp(next), next.sanctionReason ?? "timeout");
    return next;
  }
  return { ...state, ticks, phaseTicks };
}
export function fireView(state) {
  return {
    ...state,
    choices: state.revealResolved
      ? state.choices.map(entry => entry && { ...entry })
      : state.choices.map(() => null),
    last: state.last && {
      ...state.last, first: { ...state.last.first }, second: { ...state.last.second },
    },
    wins: [...state.wins], totalWins: [...state.totalWins], stock: [...state.stock],
    used: state.used.map((cards, seat) => !state.revealResolved && state.choices[seat]
      ? cards.filter(card => card !== state.choices[seat].id) : [...cards]),
    ready: state.choices.map(choice => choice !== null), hp: [...state.hp],
    temperature: [...state.temperature], spend: [...state.spend],
    glass: [...(state.glass ?? [1, 1])],
    windRemaining: [...state.windRemaining], windPower: [...(state.windPower ?? [0, 0])],
    pendingDamage: [...(state.pendingDamage ?? [0, 0])],
    sanctionTargets: [...(state.sanctionTargets ?? [])],
    sanctionReason: state.sanctionReason ?? null,
    glassStage: [...(state.glassStage ?? [0, 0])],
    endingRemaining: state.endingRemaining ?? 0,
    lastWindStock: state.lastWindStock && [...state.lastWindStock],
  };
}

const FIRE_PHASE_LABELS = Object.freeze({
  choice: "カード選択", round_result: "判定", series_result: "シリーズ精算",
  wind: "風・ガラス", wait: "30秒待機", burn: "焼却", sanction: "粛清",
  ending: "終了演出", finished: "試合終了",
});
const fireSeconds = ticks => Math.max(0, Math.ceil((ticks ?? 0) / 20));
const fireGlassName = (glass, stage) => glass === 0
  ? "破損"
  : ["健全", "ひび1", "ひび2", "ひび3", "ひび4"][stage ?? 0] ?? "ひび4";
const fireGlassFrame = (glass, stage) => glass === 0
  ? FIRE_PROP_FRAME.glassBroken
  : FIRE_PROP_FRAME.glassHealthy + Math.min(4, Math.max(0, stage ?? 0));
const fireCardName = card => FIRE_CARD_LABELS[card?.id] ?? "非公開";

// The venue consumes only this player-safe presentation. Sealed choices are
// read from the already-filtered view, never from the manager's private state.
export function fireVenuePresentation(state, names = []) {
  const s = state ?? createFire();
  const player = seat => names[seat] || `P${seat + 1}`;
  const phase = state ? FIRE_PHASE_LABELS[s.phase] ?? s.phase : "参加者待ち";
  const choices = s.revealResolved ? s.choices ?? [null, null] : [null, null];
  const cards = Object.fromEntries(Object.entries(FIRE_CONTROL_CARDS).map(([action, card]) => {
    const used = (s.used?.[card.seat] ?? []).includes(card.id);
    const selected = choices[card.seat]?.id === card.id;
    const frame = selected
      ? (card.id.endsWith("hot") ? FIRE_PROP_FRAME.hot : FIRE_PROP_FRAME.ice)
      : used ? FIRE_PROP_FRAME.used
        : state?.phase === "choice" && !(state.ready?.[card.seat] ?? state.choices?.[card.seat])
          ? FIRE_PROP_FRAME.active : FIRE_PROP_FRAME.available;
    return [action, { frame, used, selected }];
  }));
  const glassFrames = [0, 1].map(seat => fireGlassFrame(s.glass?.[seat], s.glassStage?.[seat]));
  const lines = [
    "シヴァリング・ファイア",
    `S${s.series}/${FIRE_SERIES_COUNT}  R${s.round}/${FIRE_ROUNDS_PER_ATTEMPT}  ${phase}`,
    `${player(0)} ${s.totalWins?.[0] ?? 0}勝  HP ${Math.max(0, s.hp?.[0] ?? 0)}  ${s.temperature?.[0] ?? 0}℃  ST ${s.stock?.[0] ?? 0}℃`,
    `${player(1)} ${s.totalWins?.[1] ?? 0}勝  HP ${Math.max(0, s.hp?.[1] ?? 0)}  ${s.temperature?.[1] ?? 0}℃  ST ${s.stock?.[1] ?? 0}℃`,
    `シリーズ ${s.wins?.[0] ?? 0}:${s.wins?.[1] ?? 0}  風 ${s.windRemaining?.[0] ?? 0}t:${s.windRemaining?.[1] ?? 0}t`,
    `ガラス P1 ${fireGlassName(s.glass?.[0], s.glassStage?.[0])} / P2 ${fireGlassName(s.glass?.[1], s.glassStage?.[1])}`,
  ];
  if (s.last) {
    const result = s.last.winner === null ? "あいこ" : `${player(s.last.winner)}の勝ち`;
    lines.push(`前R  ${player(0)} ${fireCardName(s.last.first)} vs ${player(1)} ${fireCardName(s.last.second)} → ${result}`);
  }
  if (!state) lines.push("受付で2人そろうと開始");
  else if (s.phase === "choice") {
    const waiting = [0, 1].filter(seat => !(s.ready?.[seat] ?? s.choices?.[seat])).map(player).join("・");
    lines.push(`同時選択 ${waiting || "全員確定"}  残り${fireSeconds(JUNKET_INPUT_TICKS - (s.phaseTicks ?? 0))}秒`);
  } else if (s.phase === "round_result") {
    lines.push(`判定 ${Math.max(0, FIRE_ROUND_JUDGE_TICKS - (s.phaseTicks ?? 0))}t  温度反映 ${Math.max(0, FIRE_ROUND_DAMAGE_TICKS - (s.phaseTicks ?? 0))}t`);
  } else if (s.phase === "series_result") {
    lines.push(s.seriesPrepared ? "ストックを室温へ反映" : "ストック精算待ち");
  } else if (s.phase === "wind") {
    lines.push("低温側は300tでガラス破損 / 破損後は風が5倍速");
  } else if (s.phase === "wait") {
    lines.push(`待機 残り${fireSeconds(s.waitRemaining)}秒 / その後 室温100℃で焼却`);
  } else if (s.phase === "burn") {
    lines.push(`焼却 残り${fireSeconds(s.burnRemaining)}秒 / 執行遅延 ${fireSeconds(s.burnDelayRemaining)}秒`);
  } else if (s.phase === "sanction") {
    const targets = (s.sanctionTargets ?? []).map(player).join("・") || "なし";
    lines.push(`粛清 ${fireSeconds(s.sanctionRemaining)}秒 / 対象 ${targets}`);
  } else if (s.phase === "ending") {
    lines.push(`終了演出 ${fireSeconds(s.endingRemaining)}秒`);
  } else if (s.phase === "finished") {
    lines.push(`勝者 ${Number.isInteger(s.winner) ? player(s.winner) : "引き分け"} / ${s.reason ?? "決着"}`);
  }
  const actor = s.actor === null || s.actor === undefined ? "なし" : player(s.actor);
  return {
    title: "シヴァリング・ファイア",
    phase,
    tvText: lines.join("\n"),
    receptionText: [
      "シヴァリング・ファイア 受付",
      phase,
      state ? `${s.phase === "choice" ? "両者が同時に選択" : `手番 ${actor}`} / 再戦は受付から` : "2人そろうと自動開始",
    ].join("\n"),
    executionText: s.phase === "sanction"
      ? `粛清執行\n対象 ${(s.sanctionTargets ?? []).map(player).join("・") || "なし"}\n${fireSeconds(s.sanctionRemaining)}秒`
      : "最終粛清装置\n第4シリーズ後の焼却に備える",
    cards,
    glassText: [0, 1].map(seat =>
      `${player(seat)} ガラス\n${fireGlassName(s.glass?.[seat], s.glassStage?.[seat])}`),
    glassFrames,
  };
}
export function fireViewForPlayer(state, seat) {
  requireSeat(seat, 2);
  const result = fireView(state);
  result.used[seat] = [...state.used[seat]];
  return { ...result, self: { seat, choice: state.choices[seat] && { ...state.choices[seat] },
    used: [...state.used[seat]] } };
}
export const fireGame = Object.freeze({
  id: "fire", title: "シヴァリング・ファイア", seats: 2,
  create: createFire, advance: advanceFire, view: fireView, viewForPlayer: fireViewForPlayer,
  systemAdvance: fireSystem,
});

// ---------------------------------------------------------------- Pig Palace

export const PIG_CARDS = Object.freeze({
  1: "わらの家", 2: "木の家", 3: "レンガの家", 4: "オオカミ",
});
export const PIG_STARTING_PIGS = 5;
export const PIG_SEATS = 4;
export const PIG_PALACE_LABOR = 6;
export const PIG_TEAMS = Object.freeze([0, 1, 1, 0]);
// Source tv/o2 maps 0..5 pigs to 6.00 / 15.0 / 16.5 / 18.0 / 19.5 / 21.0%.
export const PIG_OXYGEN = Object.freeze([6, 15, 16.5, 18, 19.5, 21]);
export const PIG_TICK_STEP = 5;
export const PIG_REVEAL_HIDDEN_TICKS = 40;
export const PIG_REVEAL_TICKS = 70;
export const PIG_SETTLE_INTRO_TICKS = 20;
export const PIG_CONSTRUCTION_TICKS = 40;
export const PIG_OXYGEN_TICKS = 120;
export const PIG_ROUND_RESULT_TICKS = 40;
export const PIG_PALACE_ENDING_TICKS = 100;
export const PIG_DEATH_ENDING_TICKS = 200;
export const PIG_PROP_FRAME = Object.freeze({
  available: 0, hidden: 1, straw: 2, wood: 3, brick: 4, wolf: 5, used: 6,
  targetIdle: 7, targetReady: 8, targetSpent: 9, television: 10,
});
const pigTeam = seat => PIG_TEAMS[seat];
const pigCard = (state, seat) => state.choices[seat]?.card ?? null;
const pigOxygenFor = pigs => PIG_OXYGEN[Math.max(0, Math.min(PIG_OXYGEN.length - 1, pigs))] ?? 0;
const pigPhaseLabel = phase => ({
  choose: "手札選択", reveal: "カード公開", attack: "襲撃",
  settle: "襲撃結果", construction: "宮殿建築", oxygen: "酸素反映",
  round_result: "ラウンド結果", ending: "終了演出", finished: "試合終了",
})[phase] ?? phase;

const pigFinish = (state, winnerTeam, reason) => ({
  ...state,
  phase: "finished",
  actor: null,
  winner: null,
  winnerTeam,
  reason,
  endingRemaining: 0,
  phaseTicks: 0,
  alive: state.pigs.map(pigs => pigs > 0),
  last: { ...(state.last ?? {}), kind: reason, winnerTeam },
});

function pigBeginEnding(state, winnerTeam, reason) {
  const endingRemaining = reason === "palace"
    ? PIG_PALACE_ENDING_TICKS : PIG_DEATH_ENDING_TICKS;
  return {
    ...state,
    phase: "ending",
    actor: null,
    phaseTicks: 0,
    endingRemaining,
    winner: null,
    winnerTeam,
    reason,
    alive: state.pigs.map(pigs => pigs > 0),
    last: { ...(state.last ?? {}), kind: reason, winnerTeam },
  };
}

function pigBeginRoundResult(state, kind) {
  return {
    ...state,
    phase: "round_result",
    actor: null,
    phaseTicks: 0,
    last: { ...(state.last ?? {}), kind },
  };
}

export function pigAttackTargets(state, attacker) {
  if (!state || !Number.isInteger(attacker) || state.choices[attacker]?.card !== 4) return [];
  const cards = state.choices.map(entry => entry?.card ?? null);
  const straws = cards.filter(card => card === 1).length;
  const attackableHouses = straws > 0 ? new Set([1]) : new Set([2]);
  const targets = [];
  for (let seat = 0; seat < PIG_SEATS; seat++) {
    if (seat === attacker) continue;
    const card = cards[seat];
    if (state.pigs[seat] <= 0) continue;
    if (card === 4 && !state.wolfSpent[seat]) targets.push(seat);
    else if (attackableHouses.has(card) && !state.houseSpent[seat]) targets.push(seat);
  }
  return targets;
}

function pigContinueAttack(state) {
  let next = { ...state };
  while (next.attackIndex < next.attackOrder.length) {
    const attacker = next.attackOrder[next.attackIndex];
    if (pigCard(next, attacker) !== 4 || next.wolfActed[attacker] || next.wolfSpent[attacker]) {
      next.attackIndex++;
      continue;
    }
    const targets = pigAttackTargets(next, attacker);
    if (targets.length) {
      return { ...next, phase: "attack", actor: attacker };
    }
    const pigs = [...next.pigs];
    const damage = [...next.damage];
    pigs[attacker] = Math.max(0, pigs[attacker] - 2);
    damage[attacker] = Math.max(damage[attacker], 2);
    next = {
      ...next,
      pigs,
      damage,
      o2Damage: 2,
      wolfActed: next.wolfActed.map((value, seat) => seat === attacker ? true : value),
      attackIndex: next.attackIndex + 1,
      last: {
        ...next.last,
        attacks: [...next.last.attacks, { attacker, target: null, kind: "rage" }],
      },
    };
  }
  return pigSettle({ ...next, phase: "settle", actor: null });
}

function pigNextHand(state) {
  const handEnded = state.handRound >= 3;
  const host = (state.host + 1) % PIG_SEATS;
  const round = handEnded ? 1 : state.round + 1;
  const used = handEnded ? [[], [], [], []] : state.used.map(entry => [...entry]);
  const next = {
    ...state,
    phase: "choose",
    actor: host,
    round,
    handRound: round,
    setNumber: handEnded ? state.setNumber + 1 : state.setNumber,
    host,
    choices: [null, null, null, null],
    used,
    houseSpent: [false, false, false, false],
    wolfSpent: [false, false, false, false],
    wolfActed: [false, false, false, false],
    attackOrder: [],
    attackIndex: 0,
    damage: [0, 0, 0, 0],
    o2Damage: 0,
    phaseTicks: 0,
    revealed: false,
    endingRemaining: 0,
    alive: state.pigs.map(pigs => pigs > 0),
  };
  // The source checks this at the start of every round. A skipped round still
  // advances the host and round counter before the next hand is dealt.
  if (used.every(cards => cards.includes(4))) return pigBeginRoundResult(next, "no_wolf");
  return next;
}

function pigSettle(state) {
  const pigs = [...state.pigs];
  const oxygen = pigs.map(pigOxygenFor);
  const alive = pigs.map(count => count > 0);
  return {
    ...state,
    phase: "settle",
    actor: null,
    phaseTicks: 0,
    pigs,
    oxygen,
    alive,
    last: {
      ...state.last,
      pigs: [...pigs],
      labor: [...state.labor],
      oxygen: [...oxygen],
      deaths: alive.map((value, seat) => value ? -1 : seat).filter(seat => seat >= 0),
      completedTeams: [],
    },
  };
}

function pigStartReveal(state) {
  return {
    ...state,
    phase: "reveal",
    actor: null,
    phaseTicks: 0,
    revealed: false,
  };
}

function pigStartAttack(state) {
  const cards = state.choices.map(entry => entry?.card ?? null);
  const counts = [0, 0, 0, 0, 0];
  for (const card of cards) if (card) counts[card]++;
  const peaceful = counts[1] === 1 && counts[2] === 1 && counts[3] === 1;
  const wolves = cards.filter(card => card === 4).length;
  const attackOrder = Array.from({ length: PIG_SEATS }, (_, offset) =>
    (state.host + offset) % PIG_SEATS).filter(seat => cards[seat] === 4);
  const base = {
    ...state,
    phase: "attack",
    actor: null,
    phaseTicks: 0,
    revealed: true,
    used: state.used.map((cards, seat) => [...cards, state.choices[seat].card]),
    attackOrder,
    attackIndex: 0,
    houseSpent: [false, false, false, false],
    wolfSpent: [false, false, false, false],
    wolfActed: [false, false, false, false],
    damage: [0, 0, 0, 0],
    o2Damage: 0,
    last: {
      setNumber: state.setNumber,
      round: state.round,
      cards: [...cards],
      draw: !wolves || peaceful,
      attacks: [],
      kind: !wolves || peaceful ? "draw" : "attack",
    },
  };
  if (!wolves || peaceful) return pigBeginRoundResult(base, "draw");
  return pigContinueAttack(base);
}

function pigStartOxygen(state) {
  const oxygen = state.pigs.map(pigOxygenFor);
  return {
    ...state,
    phase: "oxygen",
    actor: null,
    phaseTicks: 0,
    oxygen,
    last: state.last && { ...state.last, oxygen: [...oxygen] },
  };
}

function pigResolveOxygen(state) {
  const deadSeats = state.pigs
    .map((pigs, seat) => pigs <= 0 ? seat : -1)
    .filter(seat => seat >= 0);
  const deadTeams = new Set(deadSeats.map(pigTeam));
  // The source only evaluates palace completion on a construction settlement
  // (o2Damage=1). A wolf-only or rage settlement skips the build presentation.
  const palaceTeams = state.o2Damage === 1
    ? new Set(state.labor
      .map((labor, seat) => labor >= PIG_PALACE_LABOR ? pigTeam(seat) : null)
      .filter(team => team !== null))
    : new Set();
  const resolved = {
    ...state,
    alive: state.pigs.map(pigs => pigs > 0),
    last: {
      ...state.last,
      deaths: deadSeats,
      completedTeams: [...palaceTeams],
    },
  };
  if (palaceTeams.size) {
    return pigBeginEnding(resolved,
      palaceTeams.size === 1 ? [...palaceTeams][0] : null, "palace");
  }
  if (deadTeams.size) {
    return pigBeginEnding(resolved,
      deadTeams.size === 1 ? 1 - [...deadTeams][0] : null, "death");
  }
  return pigBeginRoundResult(resolved, "round");
}

function pigTick(state) {
  if (state.phase === "finished") return state;
  if (state.phase === "ending") {
    const endingRemaining = Math.max(0,
      (state.endingRemaining ?? PIG_DEATH_ENDING_TICKS) - PIG_TICK_STEP);
    const next = {
      ...state,
      endingRemaining,
      phaseTicks: state.phaseTicks + PIG_TICK_STEP,
    };
    return endingRemaining === 0
      ? pigFinish(next, next.winnerTeam, next.reason)
      : next;
  }
  if (!["reveal", "settle", "construction", "oxygen", "round_result"].includes(state.phase)) {
    return state;
  }
  const phaseTicks = state.phaseTicks + PIG_TICK_STEP;
  let next = { ...state, phaseTicks };
  if (state.phase === "reveal") {
    if (phaseTicks >= PIG_REVEAL_HIDDEN_TICKS) next = { ...next, revealed: true };
    return phaseTicks >= PIG_REVEAL_TICKS ? pigStartAttack(next) : next;
  }
  if (state.phase === "settle") {
    if (phaseTicks < PIG_SETTLE_INTRO_TICKS) return next;
    if (state.o2Damage === 1) {
      return { ...next, phase: "construction", phaseTicks: 0 };
    }
    if (state.o2Damage === 2) return pigStartOxygen(next);
    return pigBeginRoundResult(next, "no_damage");
  }
  if (state.phase === "construction") {
    return phaseTicks >= PIG_CONSTRUCTION_TICKS ? pigStartOxygen(next) : next;
  }
  if (state.phase === "oxygen") {
    return phaseTicks >= PIG_OXYGEN_TICKS ? pigResolveOxygen(next) : next;
  }
  if (phaseTicks >= PIG_ROUND_RESULT_TICKS) return pigNextHand(next);
  return next;
}
export function createPig() {
  return {
    phase: "choose", round: 1, handRound: 1, setNumber: 1, host: 0,
    actor: 0, choices: [null, null, null, null],
    pigs: Array(PIG_SEATS).fill(PIG_STARTING_PIGS),
    labor: [0, 0, 0, 0], oxygen: Array(PIG_SEATS).fill(21),
    used: [[], [], [], []], houseSpent: [false, false, false, false],
    wolfSpent: [false, false, false, false], wolfActed: [false, false, false, false],
    attackOrder: [], attackIndex: 0, damage: [0, 0, 0, 0], o2Damage: 0,
    phaseTicks: 0, revealed: false, endingRemaining: 0,
    last: null, alive: [true, true, true, true],
    winner: null, winnerTeam: null, reason: null,
  };
}
export function advancePig(state, seat, action, value) {
  requireSeat(seat, PIG_SEATS);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "choose") {
    requireTurn(state, seat, "choose");
    if (!value || !Number.isInteger(value.card) || value.card < 1 || value.card > 4) {
      throw new Error("わら・木・レンガ・オオカミのカードを選んでください。");
    }
    if (state.used[seat].includes(value.card)) throw new Error("同じカードは1回だけ使えます。");
    const choices = [...state.choices];
    choices[seat] = { card: value.card };
    const next = { ...state, choices };
    const nextSeat = Array.from({ length: PIG_SEATS }, (_, offset) =>
      (state.host + offset) % PIG_SEATS).find(candidate => choices[candidate] === null);
    if (nextSeat !== undefined) return { ...next, actor: nextSeat };
    return pigStartReveal(next);
  }
  if (action === "target") {
    requireTurn(state, seat, "attack");
    if (!Number.isInteger(value) || value < 0 || value >= PIG_SEATS || value === seat) {
      throw new Error("襲撃先を選んでください。");
    }
    const targets = pigAttackTargets(state, seat);
    if (!targets.includes(value)) throw new Error("その相手は現在襲撃できません。");
    const pigs = [...state.pigs];
    const labor = [...state.labor];
    const houseSpent = [...state.houseSpent];
    const wolfSpent = [...state.wolfSpent];
    const wolfActed = [...state.wolfActed];
    const damage = [...state.damage];
    const attacks = [...state.last.attacks];
    if (pigCard(state, value) === 4) {
      wolfSpent[value] = true;
      attacks.push({ attacker: seat, target: value, kind: "wolf" });
    } else {
      pigs[value] = Math.max(0, pigs[value] - 1);
      labor[seat] = Math.min(PIG_PALACE_LABOR, labor[seat] + 1);
      houseSpent[value] = true;
      damage[value] = Math.max(damage[value], 1);
      attacks.push({ attacker: seat, target: value, kind: "house" });
    }
    wolfActed[seat] = true;
    const next = {
      ...state,
      pigs,
      labor,
      houseSpent,
      wolfSpent,
      wolfActed,
      damage,
      o2Damage: Math.max(state.o2Damage, pigCard(state, value) === 4 ? 0 : 1),
      attackIndex: state.attackIndex + 1,
      last: { ...state.last, attacks },
      phase: "attack",
      actor: null,
      phaseTicks: 0,
    };
    return pigContinueAttack(next);
  }
  throw new Error("不正な操作です。");
}
export function pigSystem(state, action) {
  if (action === "timeout") {
    if (!["choose", "attack"].includes(state.phase)) return state;
    const seat = state.actor;
    if (!Number.isInteger(seat) || seat < 0 || seat >= PIG_SEATS) return state;
    return pigBeginEnding({ ...state, timeoutSeat: seat }, 1 - pigTeam(seat), "timeout");
  }
  if (action === "tick") return pigTick(state);
  throw new Error("不正な内部操作です。");
}
export function pigView(state) {
  const choicesVisible = state.phase !== "choose" &&
    (state.phase !== "reveal" || state.revealed === true);
  return {
    ...state, choices: choicesVisible
      ? state.choices.map(entry => entry && { ...entry })
      : state.choices.map(() => null),
    pigs: [...state.pigs], labor: [...state.labor], oxygen: [...state.oxygen],
    damage: [...state.damage], used: state.used.map(entry => [...entry]),
    houseSpent: [...state.houseSpent], wolfSpent: [...state.wolfSpent],
    wolfActed: [...state.wolfActed],
    attackOrder: [...state.attackOrder],
    last: state.last && { ...state.last, cards: [...state.last.cards],
      attacks: [...(state.last.attacks ?? [])],
      deaths: [...(state.last.deaths ?? [])],
      completedTeams: [...(state.last.completedTeams ?? [])] },
    alive: [...state.alive],
  };
}

export function pigVenuePresentation(state, names = []) {
  const player = seat => names[seat] ?? `${seat + 1}P`;
  const seatLabels = Array.from({ length: PIG_SEATS }, (_, seat) =>
    `${seat + 1}P ${player(seat)}`);
  const cards = {};
  const targets = {};
  const visible = Boolean(state) && state.phase !== "choose" &&
    (state.phase !== "reveal" || state.revealed === true);
  for (let seat = 0; seat < PIG_SEATS; seat++) {
    for (let card = 1; card <= 4; card++) {
      const action = `pig_card_${seat + 1}_${card}`;
      const chosen = state?.choices?.[seat]?.card === card;
      const selected = visible && chosen;
      const used = Boolean(state?.used?.[seat]?.includes(card));
      const frame = selected
        ? [PIG_PROP_FRAME.straw, PIG_PROP_FRAME.wood, PIG_PROP_FRAME.brick,
          PIG_PROP_FRAME.wolf][card - 1]
        : chosen && !visible ? PIG_PROP_FRAME.hidden
          : used ? PIG_PROP_FRAME.used
          : state?.phase === "choose" ? PIG_PROP_FRAME.available : PIG_PROP_FRAME.hidden;
      cards[action] = {
        frame,
        text: `${seat + 1}P ${PIG_CARDS[card]}${used ? "（使用済み）" : ""}`,
      };
    }
    for (let target = 1; target <= 4; target++) {
      if (target === seat + 1) continue;
      const action = `pig_target_${seat + 1}_${target}`;
      const available = state?.phase === "attack" && state.actor === seat &&
        pigAttackTargets(state, seat).includes(target - 1);
      const kind = state?.choices?.[target - 1]?.card === 4 ? "オオカミ" : "家";
      const spent = Boolean(state?.houseSpent?.[target - 1] || state?.wolfSpent?.[target - 1]);
      targets[action] = {
        available,
        frame: available ? PIG_PROP_FRAME.targetReady
          : spent ? PIG_PROP_FRAME.targetSpent : PIG_PROP_FRAME.targetIdle,
        text: `${seat + 1}P → ${target}P ${kind}${available ? "（指定可）" : ""}`,
      };
    }
  }
  const phase = state ? pigPhaseLabel(state.phase) : "参加者待ち";
  const seatTexts = seatLabels.map((label, seat) => {
    if (!state) return `${label}\n受付で4人そろうと開始`;
    const card = visible && state.choices?.[seat]?.card
      ? PIG_CARDS[state.choices[seat].card] : "非公開";
    const damage = state.damage?.[seat] > 0 ? ` / 損傷${state.damage[seat]}` : "";
    return `${label}\nカード ${card} / 子豚 ${state.pigs[seat]} / 労働 ${state.labor[seat]}/6\n`
      + `酸素 ${Number(state.oxygen[seat] ?? pigOxygenFor(state.pigs[seat])).toFixed(1)}%${damage}`;
  });
  const lines = [];
  if (!state) {
    lines.push("4人で開始します。1P・4P 対 2P・3P。\n各セット3ラウンド、手札は1回ずつ。");
  } else {
    lines.push(`第${state.setNumber}セット 第${state.handRound}/3ラウンド / ${phase}`);
    lines.push(`ホスト ${player(state.host)} / 手番 ${state.actor === null ? "なし" : player(state.actor)}`);
    for (let seat = 0; seat < PIG_SEATS; seat++) {
      const card = visible && state.choices?.[seat]?.card
        ? PIG_CARDS[state.choices[seat].card] : state.phase === "choose" ? "選択中" : "非公開";
      lines.push(`${seat + 1}P ${player(seat)}: ${card} / 🐖${state.pigs[seat]} / 🔨${state.labor[seat]}`
        + ` / ${Number(state.oxygen[seat]).toFixed(1)}%`);
    }
    if (state.phase === "reveal") {
      const remaining = Math.max(0, PIG_REVEAL_TICKS - (state.phaseTicks ?? 0));
      lines.push(visible ? `公開済み。襲撃判定まで ${remaining}t` : `全員選択完了。公開まで ${remaining}t`);
    } else if (state.phase === "attack") {
      lines.push(`襲撃順 ${state.attackOrder.map(seat => player(seat)).join(" → ") || "なし"}`);
    } else if (state.phase === "settle") {
      lines.push("襲撃結果を集計しています。");
    } else if (state.phase === "construction") {
      lines.push("獲得した労働で宮殿を建築しています。");
    } else if (state.phase === "oxygen") {
      const remaining = Math.max(0, PIG_OXYGEN_TICKS - (state.phaseTicks ?? 0));
      lines.push(`酸素濃度を反映 / 残り${remaining}t`);
    } else if (state.phase === "round_result") {
      lines.push(state.last?.kind === "draw" ? "ドロー。次のラウンドへ。"
        : state.last?.kind === "no_wolf" ? "全員のオオカミ使用済み。ラウンドをスキップします。"
          : "ラウンド終了。次のラウンドへ。");
    } else if (state.phase === "ending") {
      lines.push(state.reason === "palace" ? "宮殿完成" :
        state.reason === "timeout" ? "時間切れ" : "子豚が全滅");
      lines.push(`残り${state.endingRemaining ?? 0}t`);
    } else if (state.phase === "finished") {
      lines.push(Number.isInteger(state.winnerTeam)
        ? `${state.winnerTeam === 0 ? "1P・4P" : "2P・3P"}チームの勝利`
        : "引き分け");
    }
  }
  const palaceText = seatLabels.map((label, seat) => {
    const labor = state?.labor?.[seat] ?? 0;
    return `${label} 宮殿 ${"■".repeat(labor)}${"□".repeat(PIG_PALACE_LABOR - labor)} ${labor}/6`;
  }).join("\n");
  return {
    phaseLabel: phase,
    tvText: lines.join("\n"),
    receptionText: [
      "ピーキー・ピッグ・パレス 受付",
      "4人・1P/4P対2P/3P",
      state ? `${phase} / 再戦は受付から` : "4人そろうと自動開始",
    ].join("\n"),
    seatTexts,
    palaceText,
    cards,
    targets,
  };
}
export function pigViewForPlayer(state, seat) {
  requireSeat(seat, PIG_SEATS);
  return { ...pigView(state), self: { seat, choice: state.choices[seat] && { ...state.choices[seat] },
    used: [...state.used[seat]], targets: state.phase === "attack" && state.actor === seat
      ? pigAttackTargets(state, seat) : [] } };
}
export const pigGame = Object.freeze({
  id: "pig", title: "ピーキー・ピッグ・パレス", seats: PIG_SEATS,
  create: createPig, advance: advancePig, view: pigView, viewForPlayer: pigViewForPlayer,
  systemAdvance: pigSystem,
});

// ---------------------------------------------------------------- Candlelight

export const LIGHT_HEARTS_TO_WIN = 7;
export const LIGHT_START_OXYGEN = 384000;
export const LIGHT_INTRO_TICKS = 80;
export const LIGHT_PLACEMENT_TICKS = 6000;
export const LIGHT_RESULT_TICKS = 250;
export const LIGHT_TORCH_MAX = 5;
export const LIGHT_TORCH_BURN_TICKS = 10000;
export const LIGHT_OXYGEN_RECOVERY_TICKS = 30000;
export const LIGHT_DAMAGE_DELAY_TICKS = 100;
export const LIGHT_GUARDS = Object.freeze(["鉄壁", "歓迎", "居眠り"]);
export const LIGHT_TOURISTS = Object.freeze(["心臓", "槌", "松明"]);
export const LIGHT_OXYGEN_DAMAGE = Object.freeze([
  Object.freeze([192000, 3000]),
  Object.freeze([96000, 3000]),
  Object.freeze([48000, 6000]),
  Object.freeze([28800, 12000]),
  Object.freeze([12000, 12000]),
  Object.freeze([3600, 15000]),
]);
const lightOutcome = (guard, tourist) => {
  if (guard === 0) return { hearts: 0, torches: 0 };
  const strong = guard === 1 ? 2 : 1;
  if (tourist === 0) return { hearts: strong, torches: 0 };
  if (tourist === 1) return { hearts: -strong, torches: 0 };
  return { hearts: 0, torches: strong };
};
const lightFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, placementRemaining: 0,
  resultRemaining: 0, winner, reason,
});
const lightSetters = round => {
  const guardSetter = (round - 1) % 2;
  return { guardSetter, touristSetter: 1 - guardSetter };
};
const lightNextRound = round => ({
  ...lightSetters(round),
  round,
  guards: [null, null, null],
  tourists: [null, null, null],
  placementRemaining: LIGHT_PLACEMENT_TICKS,
  resultRemaining: 0,
  last: null,
});
function lightApplyOxygenTick(state) {
  const oxygen = [...state.oxygen];
  const oxygenLack = [...state.oxygenLack];
  const oxygenRecovery = [...state.oxygenRecovery];
  const torchBurn = [...state.torchBurn];
  const torches = [...state.torches];
  const damageClaimed = state.damageClaimed.map(values => [...values]);
  const pendingDamage = state.pendingDamage.map(entry => ({ ...entry }));
  const damage = [...state.damage];
  for (let seat = 0; seat < 2; seat++) {
    // The source loops in setup and result phases consume oxygen while a
    // torch is lit. Only the initial candle-lighting function omits that
    // decrement, which is represented by the intro phase here.
    if (state.phase !== "intro" && torches[seat] > 0) {
      oxygen[seat] = Math.max(0, oxygen[seat] - 1);
    }
    if (torches[seat] === LIGHT_TORCH_MAX) {
      torchBurn[seat] += 1;
      if (torchBurn[seat] >= LIGHT_TORCH_BURN_TICKS) {
        torches[seat] = 0;
        torchBurn[seat] = 0;
        oxygenLack[seat] = true;
        oxygenRecovery[seat] = 0;
        if (oxygen[seat] <= 0) oxygen[seat] = 25;
      }
    } else {
      torchBurn[seat] = 0;
    }
  }
  if (state.phase !== "intro") {
    for (let seat = 0; seat < 2; seat++) {
      for (let band = 0; band < LIGHT_OXYGEN_DAMAGE.length; band++) {
        const [threshold, amount] = LIGHT_OXYGEN_DAMAGE[band];
        if (damageClaimed[seat][band] || oxygen[seat] > threshold) continue;
        damageClaimed[seat][band] = true;
        pendingDamage.push({ seat, amount, ticks: LIGHT_DAMAGE_DELAY_TICKS, band });
      }
    }
  }
  for (let index = pendingDamage.length - 1; index >= 0; index--) {
    pendingDamage[index].ticks -= 1;
    if (pendingDamage[index].ticks > 0) continue;
    damage[pendingDamage[index].seat] += pendingDamage[index].amount;
    pendingDamage.splice(index, 1);
  }
  for (let seat = 0; seat < 2; seat++) {
    if (oxygenLack[seat]) {
      oxygenRecovery[seat] += 1;
      if (oxygenRecovery[seat] >= LIGHT_OXYGEN_RECOVERY_TICKS) {
        oxygenLack[seat] = false;
        oxygenRecovery[seat] = 0;
      }
    }
  }
  return {
    ...state, oxygen, oxygenLack, oxygenRecovery, torchBurn, torches,
    damageClaimed, pendingDamage, damage,
  };
}
function lightResolve(state) {
  const slotResults = state.guards.map((guard, slot) => {
    const tourist = state.tourists[slot];
    return { slot, guard, tourist, ...lightOutcome(guard, tourist) };
  });
  const heartsDelta = slotResults.reduce((sum, result) => sum + result.hearts, 0);
  const torchesDelta = slotResults.reduce((sum, result) => sum + result.torches, 0);
  const recipient = state.guardSetter;
  const hearts = [...state.hearts];
  hearts[recipient] = Math.max(0, hearts[recipient] + heartsDelta);
  const torches = [...state.torches];
  const oxygen = [...state.oxygen];
  const torch = torches[recipient];
  const ignition = {
    attempted: hearts[recipient] < LIGHT_HEARTS_TO_WIN &&
      torchesDelta > 0 && torch < LIGHT_TORCH_MAX,
    success: false, halfCount: 0, failure: null,
  };
  const next = {
    ...state,
    hearts,
    torches,
    oxygen,
    phase: "result",
    actor: null,
    placementRemaining: 0,
    resultRemaining: LIGHT_RESULT_TICKS,
    last: {
      round: state.round,
      recipient,
      slotResults,
      heartsDelta,
      torchesDelta,
      ignition,
    },
  };
  // The source checks the seventh heart at time 5901, before it attempts the
  // candle ignition at 5830. A hearts victory must therefore not consume the
  // round's oxygen or modify the torch count.
  if (hearts[recipient] >= LIGHT_HEARTS_TO_WIN) {
    return lightFinish(next, recipient, "hearts");
  }
  if (ignition.attempted) {
    if (state.oxygenLack[recipient]) {
      ignition.failure = "oxygen_lack";
    } else {
      oxygen[recipient] = Math.floor(oxygen[recipient] / 2);
      ignition.halfCount = 1;
      torches[recipient] += torchesDelta;
      if (torchesDelta === 2 && torches[recipient] !== 6) {
        oxygen[recipient] = Math.floor(oxygen[recipient] / 2);
        ignition.halfCount = 2;
      }
      if (torches[recipient] >= 6) torches[recipient] = LIGHT_TORCH_MAX;
      ignition.success = true;
    }
  }
  return next;
}
export function createLight() {
  return {
    ...lightNextRound(1),
    phase: "intro",
    actor: null,
    hearts: [0, 0],
    torches: [0, 0],
    oxygen: [LIGHT_START_OXYGEN, LIGHT_START_OXYGEN],
    oxygenLack: [false, false],
    oxygenRecovery: [0, 0],
    torchBurn: [0, 0],
    damage: [0, 0],
    damageClaimed: [Array(LIGHT_OXYGEN_DAMAGE.length).fill(false),
      Array(LIGHT_OXYGEN_DAMAGE.length).fill(false)],
    pendingDamage: [],
    introRemaining: LIGHT_INTRO_TICKS,
    introTorchLit: false,
    winner: null,
    reason: null,
  };
}
export function advanceLight(state, seat, action, value) {
  requireSeat(seat, 2);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "place") {
    const role = value?.role;
    const slot = value?.slot;
    if (!["guard", "tourist"].includes(role) || !Number.isInteger(slot) || slot < 0 || slot > 2) {
      throw new Error("門番または死者の配置枠を選んでください。");
    }
    const expectedPhase = role === "guard" ? "guard_arrange" : "tourist_arrange";
    requireTurn(state, seat, expectedPhase);
    if (seat !== (role === "guard" ? state.guardSetter : state.touristSetter)) {
      throw new Error("現在の配置担当ではありません。");
    }
    const name = role === "guard" ? "guards" : "tourists";
    const placed = [...state[name]];
    if (placed[slot] !== null) throw new Error("その枠には配置済みです。");
    const identity = [0, 1, 2].find(candidate => !placed.includes(candidate));
    if (!Number.isInteger(identity)) throw new Error("配置できる人物がありません。");
    placed[slot] = identity;
    if (placed.some(candidate => candidate === null)) return { ...state, [name]: placed };
    if (role === "guard") {
      return {
        ...state,
        guards: placed,
        phase: "tourist_arrange",
        actor: state.touristSetter,
        placementRemaining: LIGHT_PLACEMENT_TICKS,
      };
    }
    return lightResolve({ ...state, tourists: placed });
  }
  if (action === "arrange") {
    if (state.phase === "guard_arrange") {
      requireTurn(state, seat, "guard_arrange");
      if (seat !== state.guardSetter) throw new Error("門番側ではありません。");
      const guards = permutation(value?.guards, [0, 1, 2], "門番を1体ずつ並べてください。");
      return {
        ...state,
        guards,
        phase: "tourist_arrange",
        actor: state.touristSetter,
        placementRemaining: LIGHT_PLACEMENT_TICKS,
      };
    }
    if (state.phase === "tourist_arrange") {
      requireTurn(state, seat, "tourist_arrange");
      if (seat !== state.touristSetter) throw new Error("死者側ではありません。");
      const tourists = permutation(value?.tourists, [0, 1, 2], "死者を1人ずつ並べてください。");
      return lightResolve({ ...state, tourists });
    }
    throw new Error("現在は配置できません。");
  }
  throw new Error("不正な操作です。");
}
export function lightSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") {
    return state.actor === null ? state : lightFinish(state, 1 - state.actor, "timeout");
  }
  if (action !== "tick") throw new Error("不正な内部操作です。");
  if (state.phase === "intro") {
    const remaining = Math.max(0, state.introRemaining - 1);
    const torchLit = state.introTorchLit || remaining <= LIGHT_INTRO_TICKS - 40;
    if (remaining > 0) {
      return {
        ...state, introRemaining: remaining, introTorchLit: torchLit,
        torches: torchLit ? [1, 1] : [...state.torches],
      };
    }
    return {
      ...state, phase: "guard_arrange", actor: state.guardSetter,
      introRemaining: 0, introTorchLit: true, torches: [1, 1],
      placementRemaining: LIGHT_PLACEMENT_TICKS,
    };
  }
  let next = lightApplyOxygenTick(state);
  const dead = next.oxygen.map(value => value <= 0);
  if (dead[0] || dead[1]) {
    return lightFinish(next, dead[0] && dead[1] ? null : dead[0] ? 1 : 0, "oxygen");
  }
  if (["guard_arrange", "tourist_arrange"].includes(next.phase)) {
    const remaining = Math.max(0, next.placementRemaining - 1);
    next = { ...next, placementRemaining: remaining };
    if (remaining === 0 && Number.isInteger(next.actor)) {
      return lightFinish(next, 1 - next.actor, "timeout");
    }
    return next;
  }
  if (next.phase === "result") {
    const remaining = Math.max(0, next.resultRemaining - 1);
    if (remaining > 0) return { ...next, resultRemaining: remaining };
    const round = next.round + 1;
    return {
      ...next,
      ...lightNextRound(round),
      phase: "guard_arrange",
      actor: lightSetters(round).guardSetter,
    };
  }
  return next;
}
export function lightView(state) {
  const reveal = ["result", "finished"].includes(state.phase);
  return {
    phase: state.phase, round: state.round, actor: state.actor,
    guardSetter: state.guardSetter, touristSetter: state.touristSetter,
    guardsPlaced: state.guards.filter(value => value !== null).length,
    touristsPlaced: state.tourists.filter(value => value !== null).length,
    guards: reveal ? state.guards.map(value => value) : null,
    tourists: reveal ? state.tourists.map(value => value) : null,
    hearts: [...state.hearts], torches: [...state.torches], oxygen: [...state.oxygen],
    oxygenLack: [...state.oxygenLack], damage: [...state.damage],
    placementRemaining: state.placementRemaining,
    resultRemaining: state.resultRemaining,
    introRemaining: state.introRemaining,
    last: state.last && {
      round: state.last.round,
      recipient: state.last.recipient,
      slotResults: state.last.slotResults.map(result => ({ ...result })),
      heartsDelta: state.last.heartsDelta,
      torchesDelta: state.last.torchesDelta,
      ignition: { ...state.last.ignition },
    },
    winner: state.winner, reason: state.reason,
  };
}
export function lightViewForPlayer(state, seat) {
  requireSeat(seat, 2);
  return { ...lightView(state), self: { seat,
    role: seat === state.guardSetter ? "guard" : "tourist",
    arrangement: [...(seat === state.guardSetter ? state.guards : state.tourists)] } };
}
export const lightGame = Object.freeze({
  id: "light", title: "デッドマンズ・キャンドルライト", seats: 2,
  create: createLight, advance: advanceLight, view: lightView, viewForPlayer: lightViewForPlayer,
  systemAdvance: lightSystem,
});

// ---------------------------------------------------------------- Devil's Mine

export const MINE_ROUNDS = 20;
export const MINE_ARRANGE_TICKS = 3600;
export const MINE_CHOOSE_TICKS = 3600;
export const MINE_REVEAL_TICKS = 180;
export const MINE_REMINE_TICKS = 120;
export const MINE_DRILL_START_TICKS = 20;
export const MINE_DRILL_STEP_TICKS = 20;
export const MINE_WASH_TICKS = 20;
export const MINE_RESOLVE_TICKS = 20;
export const MINE_FINAL_TICKS = 140;
export const MINE_WAIT_TICKS = 72000;
export const MINE_CARDS = Object.freeze(["ブランク", "大喰らいの人魂", "先人のツルハシ", "無気力瓶"]);
const mineFinish = (state, winner, reason) => ({
  ...state, phase: "finished", actor: null, phaseRemaining: 0, winner, reason,
});
export function mineAvailableIndices(state) {
  const opened = state.opened?.[state.chooser] ?? [];
  return [0, 1, 2, 3].filter(index => !opened.includes(index));
}
function mineChoice(state, seat, value) {
  const count = state.chooseCount;
  if (!value || !Array.isArray(value.indices) || value.indices.length !== count) {
    throw new Error(`${count}個の岩を選んでください。`);
  }
  const indices = [...new Set(value.indices)];
  if (indices.length !== count || indices.some(index => !mineAvailableIndices(state).includes(index))) {
    throw new Error(`未採掘の異なる${count}個の岩を選んでください。`);
  }
  return { seat, indices };
}
const mineFirstSetter = round => round % 2 === 1 ? 1 : 0;
function mineRoundFields(round) {
  const setter = mineFirstSetter(round);
  return {
    phase: "arrange", round, leg: 0, setter, chooser: 1 - setter,
    actor: setter, phaseRemaining: MINE_ARRANGE_TICKS,
    boards: [null, null], choices: [null, null], roundMine: [0, 0],
    blank: [false, false], wash: [false, false], pickle: [false, false],
    remine: [false, false], zeroOrder: [], simultaneousZero: false,
    drillCycles: 0, last: null, opened: [[], []], chooseCount: 1,
    minedThisRound: [false, false], reminePending: false,
  };
}
function mineBeginChoose(state, repeat = false) {
  const seat = state.chooser;
  const level = state.skills[seat].work;
  let flag = state.workerFlags[seat];
  if (repeat && flag === 1) flag = 2;
  if (repeat && flag === 3) flag = 4;
  const pickaxe = repeat && state.last?.pickle === true;
  // This table follows choose/player-*.mcfunction, including the source's
  // distinct grant timing flags. A pickaxe reopens only the remaining rocks.
  let count = 1;
  if (level === 1 && !pickaxe && [0, 1].includes(flag)) count = 2;
  if (level === 1 && pickaxe && flag === 2) count = 2;
  if (level === 2 && !pickaxe && flag === 0) count = 3;
  if (level === 2 && [3, 4].includes(flag)) count = pickaxe && flag === 4 ? 3 : 2;
  // Java 1.09's player-2 flag=2 branches read player-1's skill, not its own.
  // Retain that literal behavior and record it in the source compatibility notes.
  if ((seat === 1 ? state.skills[0].work : level) === 2 && flag === 2) count = 2;
  const workerFlags = [...state.workerFlags];
  workerFlags[seat] = 0;
  return {
    ...state, phase: "choose", actor: seat, chooseCount: count,
    workerFlags, phaseRemaining: MINE_CHOOSE_TICKS, reminePending: false,
  };
}
function mineAfterArrange(state, seat, board) {
  const boards = [...state.boards];
  boards[seat] = board;
  return mineBeginChoose({ ...state, boards });
}
function mineApplyChoice(state, choice) {
  const chooser = state.chooser;
  const owner = state.setter;
  const board = state.boards[owner];
  if (!Array.isArray(board)) throw new Error("相手の岩が配置されていません。");
  const cards = choice.indices.map(index => board[index]);
  const roundMine = [...state.roundMine];
  const totalMine = [...state.totalMine];
  const blank = [...state.blank];
  const wash = [...state.wash];
  const pickle = [...state.pickle];
  const remine = [...state.remine];
  roundMine[chooser] += cards.length;
  // Source open/result counts a three-rock selection as TWO for the final
  // total-mine tiebreak, but as three for this round's drilling. Do not repair
  // this original quirk silently by substituting the apparent intended rule.
  totalMine[chooser] += Math.min(2, cards.length);
  blank[chooser] ||= cards.includes(0);
  wash[chooser] ||= cards.includes(1);
  pickle[chooser] ||= cards.includes(2);
  if (cards.includes(3)) roundMine[owner] -= 1;
  const again = cards.includes(2) || [1, 3].includes(state.workerFlags[chooser]);
  remine[chooser] ||= again;
  const opened = state.opened.map(indices => [...indices]);
  opened[chooser].push(...choice.indices);
  const choices = [...state.choices];
  choices[chooser] = { ...choice, cards: [...cards] };
  return {
    ...state, choices, roundMine, totalMine, blank, wash, pickle, remine, opened,
    reminePending: again, phase: "reveal", actor: null,
    phaseRemaining: again ? MINE_REMINE_TICKS : MINE_REVEAL_TICKS,
    last: {
      leg: state.leg, setter: owner, chooser,
      indices: [...choice.indices], cards: [...cards],
      mineGain: cards.length, potion: cards.includes(3),
      wash: wash[chooser], pickle: cards.includes(2), remine: again,
    },
  };
}
function mineBeginWash(state) {
  return { ...state, phase: "wash", actor: null, phaseRemaining: MINE_WASH_TICKS };
}
function mineBeginResolve(state) {
  return { ...state, phase: "resolve", actor: null, phaseRemaining: MINE_RESOLVE_TICKS };
}
function mineTiebreakLoser(state) {
  if (state.totalMine[0] !== state.totalMine[1]) {
    return state.totalMine[0] < state.totalMine[1] ? 0 : 1;
  }
  if (state.totalTime[0] !== state.totalTime[1]) {
    return state.totalTime[0] > state.totalTime[1] ? 0 : 1;
  }
  return 0;
}
function mineBeginFinal(state, fromWait = false) {
  const ingots = state.ingots;
  if (ingots[0] !== ingots[1]) {
    const fall = ingots[0] > ingots[1] ? 0 : 1;
    return {
      ...state, phase: "final", actor: null, phaseRemaining: MINE_FINAL_TICKS,
      fall: [fall === 0, fall === 1], instantPoison: [false, false],
      last: { ...state.last, fall },
    };
  }
  let loser = null;
  if (ingots[0] === 0 && ingots[1] === 0) {
    if (!state.simultaneousZero && state.zeroOrder.length === 2) loser = state.zeroOrder[1];
    else loser = mineTiebreakLoser(state);
  }
  if (loser === null) {
    if (!fromWait) {
      return {
        ...state, phase: "waiting", actor: null, phaseRemaining: MINE_WAIT_TICKS,
        last: { ...state.last, wait: true },
      };
    }
    loser = mineTiebreakLoser(state);
  }
  const instantPoison = [false, false];
  instantPoison[loser] = true;
  return {
    ...state, phase: "final", actor: null, phaseRemaining: MINE_FINAL_TICKS,
    fall: [false, false], instantPoison,
    last: { ...state.last, poison: loser, waited: fromWait },
  };
}
function mineFinishFromHealth(state, reason = "poison") {
  if (state.hp[0] > 0 && state.hp[1] > 0) return state;
  const winner = state.hp[0] <= 0 && state.hp[1] <= 0 ? null
    : state.hp[0] <= 0 ? 1 : 0;
  return mineFinish(state, winner, reason);
}
function minePoisonTick(state, ticks = 1) {
  const hp = [...state.hp];
  const poison = [...state.poison];
  for (let seat = 0; seat < 2; seat++) {
    if (state.instantPoison[seat]) hp[seat] = Math.max(0, hp[seat] - 2000 * ticks);
    if (state.bombs[seat] > 0) {
      const total = poison[seat] + ticks;
      const doses = Math.floor(total / 1200);
      poison[seat] = total % 1200;
      hp[seat] = Math.max(0, hp[seat] - doses * 250);
    }
  }
  return mineFinishFromHealth({ ...state, hp, poison });
}
function mineExplosionDamage(height) {
  if (height >= 1) return 100000;
  if (height >= -2) return 45000;
  if (height >= -5) return 30000;
  return 0;
}
function mineDrillCycle(state) {
  const hp = [...state.hp];
  const ingots = [...state.ingots];
  const bombs = [...state.bombs];
  const roundMine = [...state.roundMine];
  const zeroOrder = [...state.zeroOrder];
  const zeroedThisCycle = [];
  const minedThisRound = [...state.minedThisRound];
  for (let seat = 0; seat < 2; seat++) {
    if (roundMine[seat] <= 0 || ingots[seat] <= 0) {
      roundMine[seat] = 0;
      continue;
    }
    roundMine[seat] -= 1;
    minedThisRound[seat] = true;
    ingots[seat] -= 1;
    bombs[seat] += 1;
    if (ingots[seat] === 0) zeroedThisCycle.push(seat);
    if (bombs[seat] >= 5) {
      hp[seat] = Math.max(0, hp[seat] - mineExplosionDamage(state.heights[seat]));
      ingots[seat] = 0;
    }
  }
  let simultaneousZero = state.simultaneousZero;
  if (zeroedThisCycle.length === 2) simultaneousZero = true;
  const ordered = [...zeroOrder];
  zeroedThisCycle.forEach(seat => {
    if (!ordered.includes(seat)) ordered.push(seat);
  });
  const heights = [...ingots];
  let next = {
    ...state, hp, ingots, bombs, roundMine, heights, minedThisRound,
    zeroOrder: ordered, simultaneousZero, drillCycles: state.drillCycles + 1,
  };
  next = mineFinishFromHealth(next, "explosion");
  if (next.phase === "finished") return next;
  if (roundMine.some((value, seat) => value > 0 && ingots[seat] > 0)) {
    return { ...next, phaseRemaining: MINE_DRILL_STEP_TICKS };
  }
  return mineBeginWash(next);
}
function mineResetRound(state, nextRound, retry = false) {
  const round = retry ? state.round : nextRound;
  return {
    ...state, ...mineRoundFields(round),
    hp: [...state.hp], ingots: [...state.ingots], bombs: [...state.bombs],
    heights: [...state.heights], poison: [...state.poison],
    totalMine: [...state.totalMine], totalTime: [...state.totalTime],
    instantPoison: [...state.instantPoison], fall: [...state.fall],
    winner: null, reason: null,
  };
}
export function createMine() {
  return {
    ...mineRoundFields(1),
    hp: [JUNKET_MAX_HP, JUNKET_MAX_HP], ingots: [30, 30],
    bombs: [0, 0], heights: [30, 30], poison: [0, 0],
    totalMine: [0, 0], totalTime: [0, 0],
    rulesVersion: 2, keys: [1, 1],
    skills: [{ clean: 0, work: 0, fate: 0 }, { clean: 0, work: 0, fate: 0 }],
    workerFlags: [0, 0], instantPoison: [false, false], fall: [false, false],
    winner: null, reason: null,
  };
}
export function advanceMine(state, seat, action, value) {
  requireSeat(seat, 2);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "key") {
    const target = value?.target;
    const skill = value?.skill;
    if (state.keys[seat] <= 0) throw new Error("スケルトンキーは使用済みです。");
    if (![0, 1].includes(target) || !["clean", "work", "fate"].includes(skill)) {
      throw new Error("特性と付与するプレイヤーを選んでください。");
    }
    if (skill === "clean" && state.skills[target].clean > 0) {
      throw new Error("対象は既に潔癖症の特性を持っています。");
    }
    const keys = [...state.keys]; keys[seat] -= 1;
    const skills = state.skills.map(entry => ({ ...entry }));
    skills[target][skill] += 1;
    const ingots = [...state.ingots];
    const workerFlags = [...state.workerFlags];
    let chooseCount = state.chooseCount;
    if (skill === "fate") ingots[target] -= Math.floor(ingots[target] / 2);
    if (skill === "work") {
      const opening = state.phase === "reveal" && !state.reminePending;
      const flag = workerFlags[target];
      workerFlags[target] = flag === 0 ? (opening ? 2 : 1)
        : flag === 2 ? (opening ? 4 : 3) : flag === 1 ? 3 : flag;
      if (state.phase === "choose" && state.chooser === target) {
        chooseCount += 1;
        workerFlags[target] = 0;
      }
    }
    return { ...state, keys, skills, ingots, heights: [...ingots], workerFlags, chooseCount };
  }
  if (action === "wait_30") {
    requireTurn(state, seat, "choose");
    if (state.phaseRemaining < 801) throw new Error("30秒待機するには残り時間が不足しています。");
    const poison = state.poison.map((count, target) => count + (state.bombs[target] > 0 ? 600 : 0));
    const totalTime = [...state.totalTime]; totalTime[seat] += 600;
    return { ...state, poison, totalTime, phaseRemaining: state.phaseRemaining - 600 };
  }
  if (action === "arrange") {
    requireTurn(state, seat, "arrange");
    if (seat !== state.setter) throw new Error("自分の岩を配置してください。");
    if (!value) throw new Error("岩を並べてください。");
    const board = permutation(value.cards, [0, 1, 2, 3], "4種類の岩を1つずつ並べてください。");
    return mineAfterArrange(state, seat, board);
  }
  if (action === "choose" || action === "mine") {
    requireTurn(state, seat, "choose");
    if (seat !== state.chooser) throw new Error("相手の岩を選ぶ手番ではありません。");
    return mineApplyChoice(state, mineChoice(state, seat, value));
  }
  throw new Error("不正な操作です。");
}
export function mineSystem(state, action, value = null) {
  if (state.phase === "finished") return state;
  if (action === "timeout") {
    if (!Number.isInteger(state.actor)) return state;
    const instantPoison = [...state.instantPoison];
    instantPoison[state.actor] = true;
    return { ...state, instantPoison };
  }
  if (action !== "tick") throw new Error("不正な内部操作です。");
  const ticks = Math.max(1, Math.min(200, Number(value?.ticks) || 1));
  let next = minePoisonTick(state, ticks);
  if (next.phase === "finished") return next;
  if (["arrange", "choose"].includes(next.phase)) {
    const totalTime = [...next.totalTime];
    if (Number.isInteger(next.actor)) totalTime[next.actor] += ticks;
    next = { ...next, totalTime, phaseRemaining: next.phaseRemaining - ticks };
    if (next.phaseRemaining <= 0) {
      const instantPoison = [...next.instantPoison];
      instantPoison[next.actor] = true;
      next = { ...next, instantPoison, phaseRemaining: 0 };
      if (next.phase === "arrange") {
        next = mineAfterArrange(next, next.setter,
          next.boards[next.setter] ?? [0, 1, 2, 3]);
      } else {
        next = mineApplyChoice(next, mineChoice(next, next.chooser,
          { indices: mineAvailableIndices(next).slice(0, next.chooseCount) }));
      }
    }
  } else {
    next = { ...next, phaseRemaining: Math.max(0, next.phaseRemaining - ticks) };
  }
  if (next.phase === "reveal" && next.phaseRemaining <= 0) {
    if (next.reminePending) {
      next = mineBeginChoose(next, true);
    } else if (next.leg === 0) {
      const firstSetter = next.setter;
      next = {
        ...next, leg: 1, setter: next.chooser,
        chooser: firstSetter, actor: next.chooser,
        phase: "arrange", phaseRemaining: MINE_ARRANGE_TICKS,
      };
    } else if (!next.roundMine.some(value => value > 0)) {
      next = mineBeginWash(next);
    } else {
      next = { ...next, phase: "drill", actor: null,
        phaseRemaining: MINE_DRILL_START_TICKS };
    }
  } else if (next.phase === "drill" && next.phaseRemaining <= 0) {
    next = mineDrillCycle(next);
  } else if (next.phase === "wash" && next.phaseRemaining <= 0) {
    const wash = next.wash.map((value, seat) => value ||
      next.minedThisRound[seat] && next.skills[seat].clean === 1);
    const bombs = next.bombs.map((value, seat) => wash[seat] ? 0 : value);
    next = mineBeginResolve({ ...next, bombs, wash });
  } else if (next.phase === "resolve" && next.phaseRemaining <= 0) {
    if (next.blank[0] && next.blank[1]) {
      next = mineResetRound(next, next.round, true);
    } else if (next.round >= MINE_ROUNDS) {
      next = mineBeginFinal(next);
    } else {
      next = mineResetRound(next, next.round + 1);
    }
  } else if (next.phase === "waiting" && next.phaseRemaining <= 0) {
    next = mineBeginFinal(next, true);
  } else if (next.phase === "final" && next.phaseRemaining <= 0) {
    const hp = [...next.hp];
    for (let seat = 0; seat < 2; seat++) {
      if (next.instantPoison[seat] || next.fall[seat]) hp[seat] = 0;
    }
    next = mineFinishFromHealth({ ...next, hp }, "final");
    if (next.phase !== "finished") next = mineFinish(next, null, "draw");
  }
  return mineFinishFromHealth(next);
}
export function mineView(state) {
  return {
    phase: state.phase, round: state.round, leg: state.leg, actor: state.actor,
    setter: state.setter, chooser: state.chooser, phaseRemaining: state.phaseRemaining,
    chooseCount: state.chooseCount, availableIndices: mineAvailableIndices(state),
    canWait: state.phase === "choose" && state.phaseRemaining >= 801,
    skills: state.skills.map(entry => ({ ...entry })),
    boardsReady: state.boards.map(Boolean), hp: [...state.hp], ingots: [...state.ingots],
    bombs: [...state.bombs], heights: [...state.heights], poison: [...state.poison],
    roundMine: [...state.roundMine], totalMine: [...state.totalMine],
    totalTime: [...state.totalTime], mined: [...state.totalMine],
    wash: [...state.wash], pickle: [...state.pickle], remine: [...state.remine],
    instantPoison: [...state.instantPoison], fall: [...state.fall],
    last: state.last && {
      poison: state.last.poison,
      leg: state.last.leg, setter: state.last.setter, chooser: state.last.chooser,
      indices: state.last.indices ? [...state.last.indices] : [],
      cards: state.last.cards ? [...state.last.cards] : [],
      mineGain: state.last.mineGain, potion: state.last.potion,
      wash: state.last.wash, pickle: state.last.pickle, remine: state.last.remine,
      fall: state.last.fall, wait: state.last.wait, waited: state.last.waited,
    },
    winner: state.winner, reason: state.reason,
  };
}
export function mineViewForPlayer(state, seat) {
  requireSeat(seat, 2);
  return { ...mineView(state), self: { seat,
    keys: state.keys[seat],
    board: state.boards[seat] && [...state.boards[seat]],
    choice: state.choices[seat] && { ...state.choices[seat],
      indices: [...state.choices[seat].indices], cards: [...state.choices[seat].cards] } } };
}
export const mineGame = Object.freeze({
  id: "mine", title: "デビルズマイン・ツインズ", seats: 2,
  create: createMine, advance: advanceMine, view: mineView, viewForPlayer: mineViewForPlayer,
  systemAdvance: mineSystem,
});

export const STRATEGY_GAMES = Object.freeze([
  grailGame, auctionGame, fireGame, pigGame, lightGame, mineGame,
]);
