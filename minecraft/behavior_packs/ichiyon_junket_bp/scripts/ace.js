// Independent implementation of ver1.09 Tumbling Ace: five-card dice poker.
// five games, four-player fixed betting and two-player team totals.
export const ACE_SEATS = 4;
export const ACE_GAMES = 5;
export const ACE_INITIAL_COINS = 100;
export const ACE_MIN_TABLE_BET = 10;
export const ACE_MAX_TABLE_BET = 399;
export const ACE_RAISE_LIMIT = 200;
export const ACE_INPUT_TICKS = 6000;
const FACE_ORDER = ['9', '10', 'J', 'Q', 'K', 'A'];

const countsOf = dice => FACE_ORDER.map(face => dice.filter(value => value === face).length);
const rank = face => FACE_ORDER.indexOf(face);
function compareArrays(a, b) {
  for (let index = 0; index < Math.max(a.length, b.length); index++) {
    const difference = (a[index] ?? -1) - (b[index] ?? -1);
    if (difference) return difference;
  }
  return 0;
}
export function aceHand(dice) {
  if (!Array.isArray(dice) || dice.length !== 5 || dice.some(face => !FACE_ORDER.includes(face))) {
    throw new Error("5個のダイス結果が不正です。");
  }
  const counts = countsOf(dice);
  const groups = counts.map((count, face) => ({ count, face })).filter(group => group.count);
  const byCount = [...groups].sort((a, b) => b.count - a.count || b.face - a.face);
  const exact = count => groups.filter(group => group.count === count).map(group => group.face).sort((a, b) => b - a);
  if (byCount[0].count === 5) return { name: 'five', key: [6, byCount[0].face] };
  if (byCount[0].count === 4) {
    const kicker = exact(1)[0];
    return { name: 'four', key: [5, byCount[0].face, kicker] };
  }
  if (byCount[0].count === 3 && byCount[1]?.count === 2) {
    // ver1.09 fullhouse.mcfunction explicitly ranks 10-10-10-J-J above
    // 10-10-10-Q-Q (166000 vs 165000). Preserve that source ordering.
    let pair = byCount[1].face;
    if (byCount[0].face === 1 && (pair === 2 || pair === 3)) pair = 5 - pair;
    return { name: 'full_house', key: [4, byCount[0].face, pair] };
  }
  if (byCount[0].count === 3) {
    const kickers = groups.filter(group => group.count === 1).map(group => group.face).sort((a, b) => b - a);
    return { name: 'three', key: [3, byCount[0].face, ...kickers] };
  }
  const pairs = exact(2).sort((a, b) => b - a);
  if (pairs.length === 2) return { name: 'two_pair', key: [2, ...pairs, exact(1)[0]] };
  if (pairs.length === 1) {
    const kickers = exact(1).sort((a, b) => b - a);
    return { name: 'one_pair', key: [1, pairs[0], ...kickers] };
  }
  // The ver1.09 0pair function scores an unpaired hand solely from the
  // face that is absent (A=10 ... 9=60), and the higher score wins.
  const missing = counts.indexOf(0);
  return { name: 'high', key: [0, (FACE_ORDER.length - missing) * 10] };
}
export function compareAceDice(first, second) {
  return compareArrays(aceHand(first).key, aceHand(second).key);
}

function finish(state, winnerTeam, reason) {
  return { ...state, phase: 'finished', actor: null, winnerTeam, reason };
}
function newGame(state) {
  const parent = (state.parent + 1) % ACE_SEATS;
  return {
    ...state,
    phase: 'table_bet',
    game: state.game + 1,
    parent,
    actor: parent,
    tableBet: 0,
    currentBet: 0,
    committed: [0, 0, 0, 0],
    folded: [false, false, false, false],
    acted: [false, false, false, false],
    dice: [null, null, null, null],
    rolled: [false, false, false, false],
    hands: [null, null, null, null],
    rollOrder: Array.from({ length: 4 }, (_, index) => (parent + index) % ACE_SEATS),
    rollIndex: 0,
    raiseLimit: ACE_RAISE_LIMIT,
    last: null,
  };
}

export function createAce() {
  return {
    phase: 'table_bet',
    game: 1,
    parent: 0,
    actor: 0,
    coins: Array(ACE_SEATS).fill(ACE_INITIAL_COINS),
    tableBet: 0,
    currentBet: 0,
    committed: [0, 0, 0, 0],
    folded: [false, false, false, false],
    acted: [false, false, false, false],
    dice: [null, null, null, null],
    hands: [null, null, null, null],
    rolled: [false, false, false, false],
    rollOrder: [0, 1, 2, 3],
    rollIndex: 0,
    raiseLimit: ACE_RAISE_LIMIT,
    last: null,
    winnerTeam: null,
    reason: null,
  };
}

const activeSeats = state => state.folded
  .map((folded, seat) => folded ? -1 : seat)
  .filter(seat => seat >= 0);

function nextSeat(state, after, predicate) {
  for (let step = 1; step <= ACE_SEATS; step++) {
    const candidate = (after + step) % ACE_SEATS;
    if (predicate(candidate)) return candidate;
  }
  return null;
}

function finishBettingRound(state) {
  // Source roll/tonext completes the whole parent-to-last call order.
  // The last player still chooses even when everyone before them dropped.
  const pending = nextSeat(state, state.actor ?? 0, seat => !state.acted[seat]);
  if (pending !== null) {
    return { ...state, phase: 'betting', actor: pending };
  }
  return showdown(state, null);
}

export function advanceAce(state, seat, action, value) {
  if (!Number.isInteger(seat) || seat < 0 || seat >= ACE_SEATS) throw new Error("プレイヤー番号が不正です。");
  if (state.phase === 'finished') throw new Error("試合は終了しています。");
  if (action === 'table_bet') {
    if (state.phase !== 'table_bet' || seat !== state.parent) throw new Error("場代を決める手番ではありません。");
    if (!Number.isInteger(value) || value < ACE_MIN_TABLE_BET) throw new Error("場代は10枚以上です。");
    // Source input has a 0..3 hundreds digit, and 0..9 tens/ones.
    // It has no wallet restriction: negative final coins are legal.
    if (value > ACE_MAX_TABLE_BET) throw new Error("場代は399枚以下です。");
    return {
      ...state,
      phase: 'roll',
      tableBet: value,
      currentBet: value,
      committed: Array(ACE_SEATS).fill(value),
      actor: state.rollOrder[0],
    };
  }
  if (action === 'roll') {
    if (state.phase !== 'roll' || seat !== state.actor) throw new Error("ダイスの手番ではありません。");
    if (!Array.isArray(value) || value.length !== 5) throw new Error("5個のダイスを振ってください。");
    const hand = aceHand(value);
    const dice = state.dice.map((entry, index) => index === seat ? [...value] : entry);
    const hands = state.hands.map((entry, index) => index === seat ? hand : entry);
    const rolled = [...state.rolled]; rolled[seat] = true;
    const rollIndex = state.rollIndex + 1;
    if (rollIndex < state.rollOrder.length) {
      return { ...state, dice, hands, rolled, rollIndex, actor: state.rollOrder[rollIndex] };
    }
    return {
      ...state,
      phase: 'betting',
      dice, hands, rolled, rollIndex,
      actor: state.rollOrder[0],
      lastAction: -1,
    };
  }
  if (action === 'bet') {
    if (state.phase !== 'betting' || seat !== state.actor) throw new Error("ベットの手番ではありません。");
    if (state.folded[seat]) throw new Error("降りたプレイヤーは行動できません。");
    if (!value || !['call', 'raise', 'fold'].includes(value.type)) throw new Error("行動が不正です。");
    const committed = [...state.committed];
    const folded = [...state.folded];
    const acted = [...state.acted];
    let currentBet = state.currentBet;
    if (value.type === 'fold') {
      folded[seat] = true;
    } else if (value.type === 'call') {
      committed[seat] = currentBet;
    } else {
      if (!Number.isInteger(value.amount) || value.amount < 1 || value.amount > state.raiseLimit) {
        throw new Error(`レイズは1〜${state.raiseLimit}枚です。`);
      }
      currentBet += value.amount;
      committed[seat] = currentBet;
    }
    acted[seat] = true;
    if (value.type === 'raise') {
      // The source reopens everyone from the parent up to the seat before the
      // raiser. Seats after the raiser still take their normal turn later.
      const responders = [];
      for (let cursor = state.parent; cursor !== seat; cursor = (cursor + 1) % ACE_SEATS) {
        if (!folded[cursor]) responders.push(cursor);
      }
      const withRaise = {
        ...state, committed, folded, acted, currentBet,
        raiseLimit: state.raiseLimit - value.amount,
        lastAction: seat,
      };
      if (!responders.length) {
        return { ...withRaise, phase: 'reroll_choice', actor: seat, raiseSeat: seat };
      }
      return {
        ...withRaise, phase: 'raise_response', actor: responders[0],
        responders, responderIndex: 0, raiseSeat: seat,
      };
    }
    if (value.type === 'fold') {
      return finishBettingRound({ ...state, committed, folded, acted, currentBet });
    }
    return {
      ...state,
      phase: 'reroll_choice', committed, folded, acted, currentBet,
      actor: seat, lastAction: seat,
    };
  }
  if (action === 'respond') {
    if (state.phase !== 'raise_response' || seat !== state.actor) {
      throw new Error("レイズへの応答手番ではありません。");
    }
    if (!value || !['call', 'fold'].includes(value.type)) throw new Error("応答が不正です。");
    const committed = [...state.committed];
    const folded = [...state.folded];
    const acted = [...state.acted];
    if (value.type === 'fold') {
      folded[seat] = true;
    } else {
      committed[seat] = state.currentBet;
    }
    acted[seat] = true;
    const responderIndex = state.responderIndex + 1;
    const next = {
      ...state, committed, folded, acted,
      responderIndex,
      lastAction: seat,
    };
    if (responderIndex < state.responders.length) {
      return { ...next, actor: state.responders[responderIndex] };
    }
    return {
      ...next, phase: 'reroll_choice', actor: state.raiseSeat,
      responders: undefined, responderIndex: undefined,
    };
  }
  if (action === 'reroll_selected') {
    if (state.phase !== 'reroll_choice' || seat !== state.actor) {
      throw new Error("振り直すダイスを選ぶ手番ではありません。");
    }
    if (!value || !Array.isArray(value.indices) || !Array.isArray(value.dice) ||
        value.dice.length !== 5 || value.indices.length > 5 ||
        value.indices.some((index, position) => !Number.isInteger(index) ||
          index < 0 || index >= 5 || value.indices.indexOf(index) !== position)) {
      throw new Error("振り直すダイスの選択が不正です。");
    }
    const indices = new Set(value.indices);
    for (let index = 0; index < 5; index++) {
      if (!indices.has(index) && value.dice[index] !== state.dice[seat][index]) {
        throw new Error("選択していないダイスは変更できません。");
      }
    }
    const dice = state.dice.map((entry, index) => index === seat ? [...value.dice] : entry);
    const hands = state.hands.map((entry, index) => index === seat ? aceHand(value.dice) : entry);
    return finishBettingRound({ ...state, dice, hands, lastAction: seat });
  }
  if (action === 'reroll') {
    if (state.phase !== 'reroll' || seat !== state.actor || !state.tied.includes(seat) || state.tiedRolled[seat]) throw new Error("振り直しの対象ではありません。");
    if (!Array.isArray(value) || value.length !== 5) throw new Error("5個のダイスを振ってください。");
    const dice = state.dice.map((entry, index) => index === seat ? [...value] : entry);
    const hands = state.hands.map((entry, index) => index === seat ? aceHand(value) : entry);
    const remaining = state.tied.filter(index => index !== seat && !state.tiedRolled[index]);
    const tiedRolled = [...state.tiedRolled]; tiedRolled[seat] = true;
    if (remaining.length) {
      return { ...state, dice, hands, tiedRolled, actor: remaining[0] };
    }
    return showdown({ ...state, dice, hands, tiedRolled });
  }
  throw new Error("不正な操作です。");
}

function showdown(state, forcedWinner = null) {
  const eligible = state.phase === "reroll" ? [...state.tied]
    : state.folded.map((folded, index) => folded ? -1 : index).filter(index => index >= 0);
  if (forcedWinner !== null) {
    return payout(state, forcedWinner);
  }
  const ordered = [...eligible].sort((a, b) => compareAceDice(state.dice[b], state.dice[a]));
  const best = state.dice[ordered[0]];
  // All four drops leave four zero hand scores in the Java judge.
  const tied = ordered.length ? ordered.filter(index => compareAceDice(state.dice[index], best) === 0) : [0, 1, 2, 3];
  if (tied.length > 1) {
    return {
      ...state,
      phase: 'reroll',
      // result/tonext resets every player's bet even on the tie path.
      committed: [0, 0, 0, 0],
      tied: [...tied].sort((a, b) => a - b),
      tieRound: (state.tieRound ?? 0) + 1,
      tiedRolled: Array(ACE_SEATS).fill(false),
      actor: Math.min(...tied),
    };
  }
  return payout(state, ordered[0]);
}

function payout(state, winner) {
  const coins = [...state.coins];
  for (let seat = 0; seat < ACE_SEATS; seat++) {
    if (seat === winner) continue;
    coins[winner] += state.committed[seat];
    coins[seat] -= state.committed[seat];
  }
  const last = {
    game: state.game, winner,
    hand: state.hands[winner]?.name ?? null,
  };
  const next = { ...state, coins, last, phase: 'result', actor: null };
  if (state.game < ACE_GAMES) return { ...next, phase: 'between' };
  const teams = [coins[0] + coins[1], coins[2] + coins[3]];
  return finish(next, teams[0] === teams[1] ? null : teams[0] > teams[1] ? 0 : 1, 'team_coins');
}

export function aceSystem(state, action) {
  if (state.phase === 'finished') return state;
  // No timeup handler exists in the ver1.09 multiplayer Ace functions.
  if (action === 'timeout') return state;
  if (action === 'next_game') {
    if (state.phase !== 'between') throw new Error("次のゲームへ進めません。");
    return newGame(state);
  }
  throw new Error("不正な内部操作です。");
}

export function aceView(state) {
  return {
    phase: state.phase,
    game: state.game,
    parent: state.parent,
    actor: state.actor,
    coins: [...state.coins],
    tableBet: state.tableBet,
    currentBet: state.currentBet,
    raiseLimit: state.raiseLimit,
    tieRound: state.tieRound ?? 0,
    committed: [...state.committed],
    folded: [...state.folded],
    hands: state.hands.map(hand => hand && { name: hand.name }),
    dice: state.dice.map(dice => dice && [...dice]),
    tied: state.tied ? [...state.tied] : [],
    last: state.last && { ...state.last },
    winnerTeam: state.winnerTeam,
    reason: state.reason,
  };
}

export const aceGame = Object.freeze({
  id: 'ace', title: 'タンブリング・エース', seats: 4,
  create: createAce, advance: advanceAce, view: aceView, systemAdvance: aceSystem,
});
