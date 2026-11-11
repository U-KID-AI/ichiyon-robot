// Independent implementation of the ver1.09 silence/dead-end game.
export const SILENCE_TURN_TICKS = 6000;
export const SILENCE_PLAY_TICKS = 2000;
export const SILENCE_MAX_HP = 60000;
export const SILENCE_DURATIONS = Object.freeze([0, 20, 30]);
export const SILENCE_TICKS_PER_SECOND = 20;
// game:zzz_hp/1 -> /2 after 1t -> /1 after 4t consumes the last damage once per 5 ticks.
export const SILENCE_DAMAGE_PERIOD = 5;

const copyOrder = value => Array.isArray(value) ? [...value] : value;
const sameOrder = order => Array.isArray(order) && order.length === 3 &&
  [...order].sort((a, b) => a - b).join(",") === "0,20,30";
function finish(state, winner, reason) {
  return { ...state, phase: "finished", actor: null, winner, reason };
}
function terminal(state) {
  const dead = state.hp.map(value => value <= 0);
  if (!dead.some(Boolean)) return state;
  return finish(state, dead[0] && dead[1] ? null : dead[0] ? 1 : 0, "health");
}
function requireTurn(state, seat, phase) {
  if (state.phase !== phase || state.actor !== seat) throw new Error("現在の手番ではありません。");
}

export function createSilence() {
  return {
    phase: "arrange",
    actor: 1,
    arranger: 1,
    round: 1,
    chooser: 0,
    arrangements: [null, null],
    arrangeDrafts: [[20, 30, 0], [20, 30, 0]],
    arrangeSelected: null,
    hp: [SILENCE_MAX_HP, SILENCE_MAX_HP],
    guard: [false, false],
    totalSeconds: 0,
    remainingTicks: SILENCE_PLAY_TICKS,
    playTicks: 0,
    damageClock: 0,
    playSeat: null,
    last: null,
    winner: null,
    reason: null,
  };
}

export function advanceSilence(state, seat, action, value) {
  if (seat !== 0 && seat !== 1) throw new Error("プレイヤー番号が不正です。");
  if (state.phase === "finished") throw new Error("試合は終了しています。");

  if (action === "arrange") {
    requireTurn(state, seat, "arrange");
    if (seat !== state.arranger) throw new Error("配置するプレイヤーではありません。");
    if (!sameOrder(value)) throw new Error("0秒・20秒・30秒を1枚ずつ配置してください。");
    const arrangements = state.arrangements.map(copyOrder);
    arrangements[seat] = [...value];
    return {
      ...state,
      arrangements,
      phase: "choose",
      actor: state.chooser,
    };
  }

  if (action === "arrange-select") {
    requireTurn(state, seat, "arrange");
    if (seat !== state.arranger) throw new Error("配置するプレイヤーではありません。");
    if (!Number.isInteger(value) || value < 0 || value > 2) throw new Error("ジャケットを選んでください。");
    const arrangeDrafts = state.arrangeDrafts.map(order => copyOrder(order));
    if (state.arrangeSelected === null) {
      return { ...state, arrangeSelected: value };
    }
    if (state.arrangeSelected !== value) {
      const selected = state.arrangeSelected;
      [arrangeDrafts[seat][selected], arrangeDrafts[seat][value]] =
        [arrangeDrafts[seat][value], arrangeDrafts[seat][selected]];
    }
    return { ...state, arrangeDrafts, arrangeSelected: null };
  }

  if (action === "arrange-confirm") {
    requireTurn(state, seat, "arrange");
    if (seat !== state.arranger) throw new Error("配置するプレイヤーではありません。");
    const order = state.arrangeDrafts[seat];
    if (!sameOrder(order)) throw new Error("0秒・20秒・30秒を1枚ずつ配置してください。");
    const arrangements = state.arrangements.map(copyOrder);
    arrangements[seat] = [...order];
    return {
      ...state,
      arrangements,
      phase: "choose",
      actor: state.chooser,
      arrangeSelected: null,
    };
  }

  if (action === "choose") {
    requireTurn(state, seat, "choose");
    if (!Number.isInteger(value) || value < 0 || value > 2) throw new Error("ジャケットを選んでください。");
    const duration = state.arrangements[state.arranger][value];
    const playTicks = duration * SILENCE_TICKS_PER_SECOND;
    const last = {
      round: state.totalSeconds + duration,
      arranger: state.arranger,
      chooser: state.chooser,
      slot: value,
      duration,
    };
    const next = {
      ...state,
      phase: "set",
      actor: state.chooser,
      playSeat: state.chooser,
      playTicks,
      totalSeconds: state.totalSeconds + duration,
      last,
    };
    return next;
  }

  if (action === "set") {
    requireTurn(state, seat, "set");
    return {
      ...state,
      phase: state.playTicks === 0 ? "return" : "playback",
      actor: state.playSeat,
    };
  }

  if (action === "guard") {
    if (state.phase !== "playback") {
      throw new Error("再生中に耳栓を使用してください。");
    }
    if (state.guard[seat]) return state;
    const guard = [...state.guard];
    guard[seat] = true;
    return { ...state, guard };
  }

  if (action === "return") {
    requireTurn(state, seat, "return");
    const arranger = state.playSeat;
    return {
      ...state,
      phase: "arrange",
      arranger,
      round: (state.round ?? 1) + 1,
      chooser: 1 - arranger,
      actor: arranger,
      playSeat: null,
      playTicks: 0,
      arrangeSelected: null,
      arrangeDrafts: state.arrangeDrafts.map((order, index) =>
        index === arranger ? [20, 30, 0] : copyOrder(order)),
    };
  }
  throw new Error("不正な操作です。");
}

export function silenceSystem(state, action, value) {
  if (state.phase === "finished") return state;
  if (action === "timeout") {
    const winner = state.actor === 0 ? 1 : state.actor === 1 ? 0 : null;
    return finish(state, winner, "timeout");
  }
  if (action !== "tick" || state.phase !== "playback") throw new Error("不正な内部操作です。");
  if (state.playTicks <= 0) return { ...state, phase: "return" };

  const hp = [...state.hp];
  const remainingTicks = state.remainingTicks - 1;
  const active = state.playSeat;
  const other = 1 - active;
  const guarded = state.guard;
  const damageFor = seat => {
    if (remainingTicks < 0) return guarded[seat] ? 2000 : 6000;
    return guarded[seat] ? 140 : 190;
  };
  const damageClock = ((state.damageClock ?? 0) + 1) % SILENCE_DAMAGE_PERIOD;
  if (damageClock === 0) {
    hp[active] -= damageFor(active);
    if (value?.otherInside !== false) hp[other] -= damageFor(other);
  }
  const playTicks = state.playTicks - 1;
  const next = { ...state, hp, remainingTicks, playTicks, damageClock };
  const ended = terminal(next);
  if (ended.phase === "finished") return ended;
  return playTicks === 0 ? { ...next, phase: "return" } : next;
}

export function silenceView(state) {
  return {
    phase: state.phase,
    actor: state.actor,
    round: state.round,
    arranger: state.arranger,
    chooser: state.chooser,
    arrangeDraft: null,
    arrangeSelected: null,
    hp: [...state.hp],
    guard: [...state.guard],
    totalSeconds: state.totalSeconds,
    remainingTicks: state.remainingTicks,
    playTicks: state.playTicks,
    playSeat: state.playSeat,
    last: state.last && { ...state.last },
    winner: state.winner,
    reason: state.reason,
  };
}

export function silenceViewForPlayer(state, seat) {
  const view = silenceView(state);
  if (seat === state.arranger && state.phase === "arrange") {
    view.arrangeDraft = [...state.arrangeDrafts[seat]];
    view.arrangeSelected = state.arrangeSelected;
  }
  return view;
}

export const silenceGame = Object.freeze({
  id: "silence",
  title: "サウンド・オブ・サイレンス",
  seats: 2,
  create: createSilence,
  advance: advanceSilence,
  view: silenceView,
  viewForPlayer: silenceViewForPlayer,
  systemAdvance: silenceSystem,
});
