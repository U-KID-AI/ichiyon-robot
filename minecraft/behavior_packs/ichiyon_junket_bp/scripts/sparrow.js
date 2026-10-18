// Independently implemented rules, derived from the local ver1.09 behavior.
// No Java function text, models, textures, audio or world data is bundled.
export const MIN_BET = 1_000_000;
export const INITIAL_MONEY = 50_000_000;
export const ROUNDS = 6;

export function createSparrow() {
  return { phase: "bet", round: 1, setter: 0, money: [INITIAL_MONEY, INITIAL_MONEY],
    bet: 0, hidden: null, last: null, winner: null, reason: null };
}

export function actor(state) {
  return state.phase === "choose" ? 1 - state.setter : state.setter;
}

export function advanceSparrow(state, seat, action, value) {
  if (state.phase === "finished" || seat !== actor(state) || action !== state.phase) {
    throw new Error("現在の手番ではありません。");
  }
  if (action === "bet") {
    // Java checks the bettor's money only, despite its 'either player' message.
    // Retain this observable behavior; an opponent may finish with debt.
    if (!Number.isSafeInteger(value) || value < MIN_BET || value > state.money[seat]) {
      throw new Error("賭け金は100万円以上、自分の所持金以下の整数で指定してください。");
    }
    return { ...state, bet: value, phase: "hide" };
  }
  if (value !== "big" && value !== "small") throw new Error("箱の指定が不正です。");
  if (action === "hide") return { ...state, hidden: value, phase: "choose" };
  const winner = value === state.hidden ? seat : state.setter;
  const amount = value === "big" ? state.bet : Math.floor(state.bet / 2);
  const money = [...state.money];
  money[winner] += amount;
  money[1 - winner] -= amount;
  const reason = state.round === ROUNDS ? "six_rounds" : money.some(n => n < MIN_BET) ? "insufficient_funds" : null;
  return { ...state, money, hidden: null, bet: 0,
    last: { round: state.round, hidden: state.hidden, chosen: value, winner, amount },
    phase: reason ? "finished" : "bet", round: reason ? state.round : state.round + 1,
    setter: reason ? state.setter : 1 - state.setter, reason,
    winner: reason ? (money[0] === money[1] ? null : money[0] > money[1] ? 0 : 1) : null };
}

export function sparrowView(state) {
  // Never expose a pending hidden choice to any client UI or public status.
  const { hidden, ...visible } = state;
  return { ...visible, money: [...state.money], last: state.last && { ...state.last }, actor: actor(state) };
}

export const sparrowGame = Object.freeze({
  id: "sparrow", title: "ウラギリスズメ", seats: 2,
  create: createSparrow, advance: advanceSparrow, view: sparrowView,
});
