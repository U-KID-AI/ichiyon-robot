// Independent implementation derived from the local ver1.09 game functions.
// Virtual health only; never damages Minecraft players or changes inventories.
export const TEMPERANCE_TURN_TICKS = 6000;
export const cardAmount = (card, round) => Math.sign(card) * ([0, 1, 10, round * 2][Math.abs(card)]);
const clamp = n => Math.max(-101, Math.min(101, n));
const transferDamage = distance => {
  const bands = [[181,60000],[161,52800],[141,40800],[121,28800],[101,24000],
    [91,19200],[81,17520],[71,15600],[61,13200],[51,10320],[41,7680],
    [31,6000],[21,3600],[11,2400],[6,1200]];
  return bands.find(([minimum]) => distance >= minimum)?.[1] ?? 0;
};
function terminal(s) {
  if (!s.hp.some(h => h <= 0)) return s;
  return { ...s, phase: "finished", winner: s.hp.every(h => h <= 0) ? null : s.hp[0] <= 0 ? 1 : 0, reason: "health" };
}
function pressure(s, delta) {
  if (!delta) return;
  const high = delta > 0 ? 1 : 0;
  const damage = Math.abs(delta) > 100 ? 1440 : Math.ceil(Math.abs(delta) / 10) * 120;
  s.hp[high] -= damage; s.hp[1 - high] -= damage / 10;
}
export function createTemperance() {
  return { phase: "arrange", round: 1, arranger: 1, actor: 1, cards: null, target: null,
    stones: [0, 0], hp: [60000, 60000], delta: 0, balance: 0, pairStart: 0,
    last: null, winner: null, reason: null };
}
export function advanceTemperance(state, seat, action, value) {
  if (state.phase === "finished" || seat !== state.actor || action !== state.phase) throw new Error("現在の手番ではありません。");
  const s = { ...state, hp: [...state.hp], stones: [...state.stones] };
  if (action === "arrange") {
    if (!Array.isArray(value) || value.length !== 3 || value.some(v => !Number.isInteger(v) || ![1,2,3].includes(Math.abs(v))) ||
        new Set(value.map(Math.abs)).size !== 3 || value.every(v => v > 0) || value.every(v => v < 0)) {
      throw new Error("1・10・2Rを1枚ずつ配置し、正位置と逆位置を混ぜてください。");
    }
    return { ...s, cards: [...value], phase: "target", actor: 1 - s.arranger };
  }
  if (action === "target") {
    if (value !== 0 && value !== 1) throw new Error("対象者を選んでください。");
    return { ...s, target: value, phase: "choose" };
  }
  if (!Number.isInteger(value) || value < 0 || value > 2) throw new Error("カードを選んでください。");
  const selected = s.cards[value], amount = cardAmount(selected, s.round);
  s.stones[s.target] = Math.max(0, s.stones[s.target] + amount);
  s.delta = clamp(s.stones[0] - s.stones[1]);
  // Original updates the physical balance after player 2's selection (even rounds).
  if (s.actor === 0) { s.pairStart = state.delta; pressure(s, state.balance); }
  else {
    const change = s.pairStart - s.delta, damage = transferDamage(Math.abs(change));
    if (change) { const high = change > 0 ? 0 : 1; s.hp[high] -= damage; s.hp[1 - high] -= damage / 10; }
    s.balance = s.delta; pressure(s, s.balance);
  }
  s.last = { round: s.round, card: selected, cards: [...s.cards], chosen: value, target: s.target, amount, balance: s.balance };
  s.phase = "arrange"; s.round++; s.arranger = 1 - s.arranger; s.actor = s.arranger; s.cards = null; s.target = null;
  return terminal(s);
}
export function temperanceSystem(state, action) {
  if (state.phase === "finished") return state;
  if (action === "timeout") return { ...state, phase: "finished", winner: 1 - state.actor, reason: "timeout" };
  if (action !== "air") throw new Error("不正な内部操作です。");
  const s = { ...state, hp: [...state.hp] };
  if (Math.abs(s.balance) === 101) s.hp[s.balance > 0 ? 0 : 1] -= 10000;
  return terminal(s);
}
export function temperanceView(s) {
  const { cards, target, ...view } = s;
  return { ...view, hp: [...s.hp], stones: [...s.stones], last: s.last && { ...s.last, cards: [...s.last.cards] } };
}
export const temperanceGame = Object.freeze({ id: "temperance", title: "ブルー・テンパランス", seats: 2,
  create: createTemperance, advance: advanceTemperance, view: temperanceView, systemAdvance: temperanceSystem });
