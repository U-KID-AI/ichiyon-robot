// Independent implementation derived from JUNKET CRAFT ver1.09 Lucy functions.
// The five-by-five vault patterns remain server-only until an attempt reveals a cell.
export const LUCY_ROWS = 5;
export const LUCY_COLUMNS = 5;
export const LUCY_TURN_TICKS = 6000;
export const LUCY_MAX_HP = 60000;
export const LUCY_MISS_DAMAGE = 3000;

const grid = () => Array.from({ length: LUCY_ROWS }, () => Array(LUCY_COLUMNS).fill(false));
const cloneGrid = value => value.map(row => [...row]);
const seatIndex = seat => {
  if (seat !== 0 && seat !== 1) throw new Error("プレイヤー番号が不正です。");
  return seat;
};
const checkTurn = (state, seat, phase) => {
  if (state.phase !== phase || state.actor !== seat) throw new Error("現在の手番ではありません。");
};
function finish(state, winner, reason) {
  return { ...state, phase: "finished", actor: null, winner, reason };
}
function nextTurn(state, seat) {
  return {
    ...state,
    phase: "attempt",
    actor: 1 - seat,
    round: seat === 1 ? state.round + 1 : state.round,
  };
}
function timeoutWinner(state) {
  if (state.phase === "lock") {
    const completed = state.progress.map(value => value >= LUCY_ROWS);
    if (completed[0] === completed[1]) return null;
    // ver1.09's timeout functions penalize the player who has already completed
    // the five vault rows while the other player has not.
    return completed[0] ? 1 : 0;
  }
  return state.actor === 0 ? 1 : state.actor === 1 ? 0 : null;
}

export function createLucy() {
  return {
    phase: "lock",
    actor: null,
    round: 1,
    progress: [0, 0],
    patterns: [grid(), grid()],
    attempted: [grid(), grid()],
    remaining: [LUCY_ROWS, LUCY_ROWS],
    hp: [LUCY_MAX_HP, LUCY_MAX_HP],
    last: null,
    winner: null,
    reason: null,
  };
}

export function advanceLucy(state, seat, action, value) {
  const player = seatIndex(seat);
  if (state.phase === "finished") throw new Error("試合は終了しています。");
  if (action === "lock") {
    if (state.phase !== "lock") throw new Error("金庫の当たり指定は完了しています。");
    const row = state.progress[player];
    if (row >= LUCY_ROWS) throw new Error("このプレイヤーは指定を完了しています。");
    if (!Array.isArray(value) || value.length !== LUCY_COLUMNS || value.some(cell => typeof cell !== "boolean")) {
      throw new Error("各列の指定状態が不正です。");
    }
    if (!value.some(Boolean)) throw new Error("各行で最低一つの当たりを指定してください。");
    const patterns = state.patterns.map(cloneGrid);
    patterns[player][row] = [...value];
    const progress = [...state.progress];
    progress[player]++;
    if (!progress.every(count => count === LUCY_ROWS)) {
      return { ...state, patterns, progress };
    }
    return { ...state, patterns, progress, phase: "attempt", actor: 0, round: 1 };
  }

  if (action === "attempt") {
    checkTurn(state, player, "attempt");
    if (!value || !Number.isInteger(value.row) || !Number.isInteger(value.column) ||
        value.row < 0 || value.row >= LUCY_ROWS || value.column < 0 || value.column >= LUCY_COLUMNS) {
      throw new Error("鍵穴を選んでください。");
    }
    if (state.attempted[player][value.row][value.column]) {
      throw new Error("その鍵穴はすでに選ばれています。");
    }
    const attempted = state.attempted.map(cloneGrid);
    attempted[player][value.row][value.column] = true;
    const remaining = [...state.remaining];
    const hit = state.patterns[player][value.row][value.column];
    if (hit) remaining[player]--;
    const last = { seat: player, row: value.row, column: value.column, hit, remaining: remaining[player] };
    const next = { ...state, attempted, remaining, last };
    if (remaining[player] === 0) return finish(next, player, "vault");
    return { ...next, phase: hit ? "success" : "recover" };
  }

  if (action === "ack") {
    checkTurn(state, player, "success");
    return nextTurn(state, player);
  }

  if (action === "recover") {
    checkTurn(state, player, "recover");
    const hp = [...state.hp];
    hp[player] -= hp[player] <= LUCY_MISS_DAMAGE ? 2000 : LUCY_MISS_DAMAGE;
    const next = { ...state, hp };
    if (hp[player] <= 0) return finish(next, 1 - player, "health");
    return nextTurn(next, player);
  }
  throw new Error("不正な操作です。");
}

export function lucySystem(state, action) {
  if (state.phase === "finished") return state;
  if (action !== "timeout") throw new Error("不正な内部操作です。");
  return finish(state, timeoutWinner(state), "timeout");
}

export function lucyView(state) {
  return {
    phase: state.phase,
    actor: state.actor,
    round: state.round,
    progress: [...state.progress],
    attempted: state.attempted.map(player =>
      player.map(row => row.map(value => value ? 1 : 0))),
    remaining: [...state.remaining],
    hp: [...state.hp],
    last: state.last && { ...state.last },
    winner: state.winner,
    reason: state.reason,
  };
}

export const lucyGame = Object.freeze({
  id: "lucy",
  title: "気分屋ルーシー",
  seats: 2,
  create: createLucy,
  advance: advanceLucy,
  view: lucyView,
  systemAdvance: lucySystem,
});
