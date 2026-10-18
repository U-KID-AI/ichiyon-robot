// Independent implementation of JUNKET CRAFT ver1.09 Jackpot Jinn.
export const JINN_CARDS = Object.freeze(['gold', 'thief', 'demon']);
export const JINN_INITIAL_HAND = Object.freeze({ gold: 4, thief: 1, demon: 1 });
export const JINN_ROUNDS_PER_GAME = 6;
export const JINN_GAMES = 3;
export const JINN_INPUT_TICKS = 6000;
export const JINN_PAY_PER_TICK = 175;
export const JINN_HUNGER_TICKS = 3600;
export const JINN_STARVE_DAMAGE = 200;
export const JINN_MAX_HP = 60000;

const hand = () => ({ ...JINN_INITIAL_HAND });
const choiceIndex = card => JINN_CARDS.indexOf(card);
function finish(state, winner, reason) {
  return { ...state, phase: "finished", winner, reason };
}
function terminal(state) {
  const dead = state.hp.map(value => value <= 0);
  if (!dead.some(Boolean)) return state;
  return finish(state, dead[0] && dead[1] ? null : dead[0] ? 1 : 0, "health");
}
function resolveCards(coins, first, second) {
  const result = [...coins];
  const [p1, p2] = [first, second];
  if (p1 === 'gold' && p2 === 'gold') {
    result[0] *= 4; result[1] *= 4;
  } else if (p1 === 'gold' && p2 === 'thief') {
    result[0] *= 2; result[1] += result[0];
  } else if (p1 === 'gold' && p2 === 'demon') {
    result[0] *= 4;
  } else if (p1 === 'thief' && p2 === 'gold') {
    result[1] *= 2; result[0] += result[1];
  } else if (p1 === 'thief' && p2 === 'demon') {
    const taken = Math.floor(result[0] * 9 / 10);
    result[0] -= taken; result[1] += taken;
  } else if (p1 === 'demon' && p2 === 'gold') {
    result[1] *= 4;
  } else if (p1 === 'demon' && p2 === 'thief') {
    const taken = Math.floor(result[1] * 9 / 10);
    result[1] -= taken; result[0] += taken;
  }
  return result;
}
function newBatch(state, game) {
  return {
    ...state,
    phase: 'cards',
    game,
    round: 1,
    hands: [hand(), hand()],
    choices: [null, null],
    last: null,
  };
}
function settlement(state) {
  return {
    ...state,
    phase: 'settlement',
    choices: [null, null],
    payment: [...state.coins],
    lever: [false, false],
    settlementTicks: 0,
    hungerClock: state.game === 2 ? JINN_HUNGER_TICKS : 0,
  };
}

export function createJinn() {
  return {
    ...newBatch({
      coins: [1, 1],
      hp: [JINN_MAX_HP, JINN_MAX_HP],
      food: [20, 20],
      winner: null,
      reason: null,
    }, 1),
    payment: [0, 0],
    lever: [false, false],
    settlementTicks: 0,
    hungerClock: 0,
  };
}

export function advanceJinn(state, seat, action, value) {
  if (seat !== 0 && seat !== 1) throw new Error("プレイヤー番号が不正です。");
  if (state.phase === 'finished') throw new Error("試合は終了しています。");
  if (action === 'choose') {
    if (state.phase !== 'cards') throw new Error("カード選択中ではありません。");
    if (state.choices[seat] !== null) throw new Error("このラウンドのカードは選択済みです。");
    if (!JINN_CARDS.includes(value) || state.hands[seat][value] <= 0) throw new Error("使用できるカードを選んでください。");
    const choices = [...state.choices];
    choices[seat] = value;
    const hands = state.hands.map(cards => ({ ...cards }));
    hands[seat][value]--;
    if (choices.some(choice => choice === null)) return { ...state, choices, hands };
    const coins = resolveCards(state.coins, choices[0], choices[1]);
    const last = {
      game: state.game, round: state.round,
      choices: [...choices], coins: [...coins],
    };
    return { ...state, choices, hands, coins, last, phase: 'reveal' };
  }
  if (action === 'reveal') {
    if (state.phase !== 'reveal') throw new Error("公開中ではありません。");
    if (state.round === JINN_ROUNDS_PER_GAME) return settlement(state);
    return {
      ...state,
      phase: 'cards',
      round: state.round + 1,
      choices: [null, null],
    };
  }
  if (action === 'lever') {
    if (state.phase !== 'settlement') throw new Error("精算中ではありません。");
    if (state.lever[seat]) return state;
    const lever = [...state.lever];
    lever[seat] = true;
    return { ...state, lever };
  }
  if (action === 'surrender') {
    if (state.phase !== 'settlement') throw new Error("降参は精算中のみ可能です。");
    return finish(state, 1 - seat, 'surrender');
  }
  throw new Error("不正な操作です。");
}

function nextBatchOrFinish(state) {
  if (state.game < JINN_GAMES) return newBatch(state, state.game + 1);
  const winner = state.coins[0] === state.coins[1] ? null : state.coins[0] > state.coins[1] ? 0 : 1;
  return finish(state, winner, 'coins');
}

export function jinnSystem(state, action) {
  if (state.phase === 'finished') return state;
  if (action === 'timeout') {
    if (state.phase !== 'cards') throw new Error("不正な内部操作です。");
    const selected = state.choices.map(choice => choice !== null);
    return finish(state, selected[0] === selected[1] ? null : selected[0] ? 0 : 1, 'timeout');
  }
  if (action === 'deadline') {
    if (state.phase !== 'settlement') throw new Error("不正な内部操作です。");
    if (state.lever[0] && state.lever[1]) return state;
    return finish(state, state.lever[0] === state.lever[1] ? null : state.lever[0] ? 0 : 1, 'timeout');
  }
  if (action === 'tick') {
    if (state.phase !== 'settlement') throw new Error("不正な内部操作です。");
    const payment = state.payment.map((value, seat) => state.lever[seat]
      ? Math.max(0, value - JINN_PAY_PER_TICK) : value);
    const hp = [...state.hp];
    const food = [...state.food];
    let hungerClock = state.game >= 2 ? state.hungerClock + 1 : 0;
    if (state.game >= 2 && hungerClock >= JINN_HUNGER_TICKS) {
      hungerClock -= JINN_HUNGER_TICKS;
      for (let seat = 0; seat < 2; seat++) food[seat] = Math.max(0, food[seat] - 1);
    }
    if (food.some(value => value === 0)) {
      for (let seat = 0; seat < 2; seat++) if (food[seat] === 0) hp[seat] -= JINN_STARVE_DAMAGE;
    }
    const next = {
      ...state,
      payment,
      hp,
      food,
      hungerClock,
      settlementTicks: state.settlementTicks + 1,
    };
    const ended = terminal(next);
    if (ended.phase === 'finished') return ended;
    if (state.lever[0] && state.lever[1] && payment[0] === 0 && payment[1] === 0) {
      return nextBatchOrFinish(next);
    }
    return next;
  }
  throw new Error("不正な内部操作です。");
}

export function jinnView(state) {
  return {
    phase: state.phase,
    game: state.game,
    round: state.round,
    coins: [...state.coins],
    hands: state.hands.map(cards => ({ ...cards })),
    chosen: state.choices.map(choice => choice !== null),
    hp: [...state.hp],
    food: [...state.food],
    payment: [...state.payment],
    lever: [...state.lever],
    last: state.last && { ...state.last, choices: [...state.last.choices], coins: [...state.last.coins] },
    winner: state.winner,
    reason: state.reason,
  };
}

export const jinnGame = Object.freeze({
  id: 'jinn',
  title: 'ジャックポット・ジニー',
  seats: 2,
  create: createJinn,
  advance: advanceJinn,
  view: jinnView,
  systemAdvance: jinnSystem,
});
