// Independent implementation of ver1.09 Tumbling Ace: five-card dice poker,
// five games, four-player fixed betting and two-player team totals.
export const ACE_SEATS = 4;
export const ACE_GAMES = 5;
export const ACE_INITIAL_COINS = 100;
export const ACE_MIN_TABLE_BET = 10;
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
    return { name: 'full_house', key: [4, byCount[0].face, byCount[1].face] };
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
  return { name: 'high', key: [0, ...exact(1).sort((a, b) => b - a)] };
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
    dice: [null, null, null, null],
    rolled: [false, false, false, false],
    hands: [null, null, null, null],
    rollOrder: Array.from({ length: 4 }, (_, index) => (parent + index) % ACE_SEATS),
    rollIndex: 0,
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
    dice: [null, null, null, null],
    hands: [null, null, null, null],
    rolled: [false, false, false, false],
    rollOrder: [0, 1, 2, 3],
    rollIndex: 0,
    last: null,
    winnerTeam: null,
    reason: null,
  };
}

export function advanceAce(state, seat, action, value) {
  if (!Number.isInteger(seat) || seat < 0 || seat >= ACE_SEATS) throw new Error("プレイヤー番号が不正です。");
  if (state.phase === 'finished') throw new Error("試合は終了しています。");
  if (action === 'table_bet') {
    if (state.phase !== 'table_bet' || seat !== state.parent) throw new Error("場代を決める手番ではありません。");
    if (!Number.isInteger(value) || value < ACE_MIN_TABLE_BET) throw new Error("場代は10枚以上です。");
    if (value > state.coins[seat]) throw new Error("所持コインを超える場代です。");
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
    let currentBet = state.currentBet;
    if (value.type === 'fold') {
      folded[seat] = true;
    } else if (value.type === 'call') {
      if (currentBet - committed[seat] > state.coins[seat]) {
        throw new Error("コールに必要なコインがありません。");
      }
      committed[seat] = currentBet;
    } else {
      if (!Number.isInteger(value.amount) || value.amount < 1 || value.amount > ACE_RAISE_LIMIT) {
        throw new Error("レイズは1〜200枚です。");
      }
      if (currentBet + value.amount - committed[seat] > state.coins[seat]) {
        throw new Error("レイズに必要なコインがありません。");
      }
      currentBet += value.amount;
      committed[seat] = currentBet;
    }
    const active = folded.map((didFold, index) => didFold ? -1 : index).filter(index => index >= 0);
    if (active.length === 1) return showdown({ ...state, committed, folded, currentBet }, active[0]);
    let next = seat;
    for (let step = 1; step <= ACE_SEATS; step++) {
      const candidate = (seat + step) % ACE_SEATS;
      if (!folded[candidate]) { next = candidate; break; }
    }
    const settled = active.every(index => committed[index] === currentBet);
    if (value.type === 'call' && settled) return showdown({ ...state, committed, folded, currentBet }, null);
    return {
      ...state,
      committed, folded, currentBet, actor: next,
      lastAction: seat,
    };
  }
  if (action === 'reroll') {
    if (state.phase !== 'reroll' || !state.tied.includes(seat)) throw new Error("振り直しの対象ではありません。");
    if (!Array.isArray(value) || value.length !== 5) throw new Error("5個のダイスを振ってください。");
    const dice = state.dice.map((entry, index) => index === seat ? [...value] : entry);
    const hands = state.hands.map((entry, index) => index === seat ? aceHand(value) : entry);
    const pending = state.tied.filter(index => !hands[index] || index === seat && false);
    const remaining = state.tied.filter(index => index !== seat && !state.tiedRolled[index]);
    const tiedRolled = [...state.tiedRolled]; tiedRolled[seat] = true;
    if (remaining.length) {
      return { ...state, dice, hands, tiedRolled, actor: remaining[0] };
    }
    return showdown({ ...state, dice, hands, tiedRolled }, null, true);
  }
  throw new Error("不正な操作です。");
}

function showdown(state, forcedWinner = null, afterTieReroll = false) {
  const eligible = state.folded.map((folded, index) => folded ? -1 : index).filter(index => index >= 0);
  if (forcedWinner !== null) {
    return payout(state, forcedWinner);
  }
  const ordered = [...eligible].sort((a, b) => compareAceDice(state.dice[b], state.dice[a]));
  const best = state.dice[ordered[0]];
  const tied = ordered.filter(index => compareAceDice(state.dice[index], best) === 0);
  if (tied.length > 1) {
    if (afterTieReroll) {
      // Repeated ties are resolved by stable seat order after the one reroll pass.
      return payout(state, tied[0]);
    }
    return {
      ...state,
      phase: 'reroll',
      tied,
      tiedRolled: Array(ACE_SEATS).fill(false),
      actor: tied[0],
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
  if (action === 'timeout') return finish(state, null, 'timeout');
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
