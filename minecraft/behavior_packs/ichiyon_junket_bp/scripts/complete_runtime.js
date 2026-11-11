import { isServiceGuide } from "./service_controls.js";
import {
  FIRE_CARD_LABELS, FIRE_TICK_STEP, JUNKET_INPUT_TICKS, PIG_TICK_STEP,
  pigAttackTargets,
} from "./strategy_games.js";
import { HOPE_PAD_SYMBOLS, HOPE_SYMBOL_NAMES, rabbitLegalMoves } from "./board_games.js";
import { createRuntimeForms, requestCatalogReturn, showReceptionNavigation } from "./venue_presentation.js";
import { createDecisionPrompts } from "./decision_prompts.js";

const DICE = ["9", "10", "J", "Q", "K", "A"];
const HOPE_REGISTRATION_TICKS = 300;
const DRAW = "引き分け";
const permutations = values => values.length < 2 ? [values] : values.flatMap((value, index) =>
  permutations(values.filter((_, i) => i !== index)).map(tail => [value, ...tail]));
const combinations = (values, count) => count === 0 ? [[]] : values.flatMap((value, index) =>
  combinations(values.slice(index + 1), count - 1).map(tail => [value, ...tail]));
// Spatial games retain walking and falling; their decision contracts decide
// exactly when a modal menu is appropriate, rather than disabling all forms.

const actionSpec = (phases, seat = null, anytime = false) =>
  Object.freeze({ phases: Object.freeze(phases), seat, anytime });
const seatActions = (prefix, count, phases) => Object.freeze(Object.fromEntries(
  Array.from({ length: count }, (_, seat) => [`${prefix}_${seat + 1}`, actionSpec(phases, seat)]),
));

const pigControls = {};
for (let seat = 0; seat < 4; seat++) {
  for (let card = 1; card <= 4; card++) {
    pigControls[`pig_card_${seat + 1}_${card}`] = actionSpec(["choose"], seat);
  }
  for (let target = 1; target <= 4; target++) {
    if (target !== seat + 1) {
      pigControls[`pig_target_${seat + 1}_${target}`] = actionSpec(["attack"], seat);
    }
  }
}

const aceControls = {
  ace_table_100_up: actionSpec(["table_bet"]),
  ace_table_100_down: actionSpec(["table_bet"]),
  ace_table_10_up: actionSpec(["table_bet"]),
  ace_table_10_down: actionSpec(["table_bet"]),
  ace_table_1_up: actionSpec(["table_bet"]),
  ace_table_1_down: actionSpec(["table_bet"]),
  ace_table_confirm: actionSpec(["table_bet"]),
  ace_raise_100_up: actionSpec(["betting"]),
  ace_raise_100_down: actionSpec(["betting"]),
  ace_raise_10_up: actionSpec(["betting"]),
  ace_raise_10_down: actionSpec(["betting"]),
  ace_raise_1_up: actionSpec(["betting"]),
  ace_raise_1_down: actionSpec(["betting"]),
  ace_raise_confirm: actionSpec(["betting"]),
};
for (let seat = 0; seat < 4; seat++) {
  const number = seat + 1;
  aceControls[`ace_tray_${number}`] = actionSpec(["roll", "reroll"], seat);
  aceControls[`ace_call_${number}`] = actionSpec(["betting", "raise_response"], seat);
  aceControls[`ace_drop_${number}`] = actionSpec(["betting", "raise_response"], seat);
  aceControls[`ace_raise_${number}`] = actionSpec(["betting"], seat);
  aceControls[`ace_reroll_confirm_${number}`] = actionSpec(["reroll_choice"], seat);
  for (let die = 1; die <= 5; die++) {
    aceControls[`ace_die_${number}_${die}`] = actionSpec(["reroll_choice"], seat);
  }
}

// The dynamic property on every venue control must resolve to one of these
// game-scoped actions. A display is deliberately not executable.
export const COMPLETE_CONTROL_SPECS = Object.freeze({
  ace: Object.freeze(aceControls),
  grail: Object.freeze({
    grail_cup_1: actionSpec(["arrange", "cup"]),
    grail_cup_2: actionSpec(["arrange", "cup"]),
    grail_cup_3: actionSpec(["arrange", "cup"]),
    grail_potion_1: actionSpec(["arrange", "potion"]),
    grail_potion_2: actionSpec(["arrange", "potion"]),
    grail_potion_3: actionSpec(["arrange", "potion"]),
  }),
  hope: Object.freeze({
    ...Object.fromEntries(Array.from({ length: 16 }, (_, index) => [
      `floor_${index + 1}`, actionSpec(["choose", "reveal"], null, true),
    ])),
  }),
  auction: Object.freeze(Object.fromEntries(
    Array.from({ length: 4 }, (_, seat) => Array.from({ length: 4 }, (_, value) => [
      `auction_bid_${seat + 1}_${value + 1}`, actionSpec(["bid"], seat),
    ])).flat(),
  )),
  fire: Object.freeze({
    rock_hot_1: actionSpec(["choice"], 0), scissors_hot_1: actionSpec(["choice"], 0),
    paper_hot_1: actionSpec(["choice"], 0), rock_ice_1: actionSpec(["choice"], 0),
    scissors_ice_1: actionSpec(["choice"], 0), paper_ice_1: actionSpec(["choice"], 0),
    rock_hot_2: actionSpec(["choice"], 1), scissors_hot_2: actionSpec(["choice"], 1),
    paper_hot_2: actionSpec(["choice"], 1), rock_ice_2: actionSpec(["choice"], 1),
    scissors_ice_2: actionSpec(["choice"], 1), paper_ice_2: actionSpec(["choice"], 1),
  }),
  pig: Object.freeze(pigControls),
  light: Object.freeze(Object.fromEntries(
    Array.from({ length: 2 }, (_, seat) => Array.from({ length: 3 }, (_, slot) => [
      `light_grave_${seat + 1}_${slot + 1}`,
      actionSpec(["guard_arrange", "tourist_arrange"], seat),
    ])).flat(),
  )),
  mine: Object.freeze(Object.fromEntries(
    Array.from({ length: 2 }, (_, seat) => Array.from({ length: 4 }, (_, slot) => [
      `mine_rock_${seat + 1}_${slot + 1}`, actionSpec(["arrange", "choose"], seat),
    ])).flat(),
  )),
  clock: Object.freeze({
    clock_hour_plus: actionSpec(["setup"]), clock_hour_minus: actionSpec(["setup"]),
    clock_minute_plus: actionSpec(["setup"]), clock_minute_minus: actionSpec(["setup"]),
    clock_time_confirm: actionSpec(["setup"]),
    clock_sector_cw: actionSpec(["move"]), clock_sector_ccw: actionSpec(["move"]),
    clock_sector_confirm: actionSpec(["move"]),
  }),
  walker: Object.freeze({
    walker_stop_1: actionSpec(["walk"], 0),
    walker_stop_2: actionSpec(["walk"], 1),
    bomb_prev_1: actionSpec(["bomb_setup"], 0),
    bomb_prev_2: actionSpec(["bomb_setup"], 1),
    bomb_next_1: actionSpec(["bomb_setup"], 0),
    bomb_next_2: actionSpec(["bomb_setup"], 1),
    bomb_confirm_1: actionSpec(["bomb_setup"], 0),
    bomb_confirm_2: actionSpec(["bomb_setup"], 1),
  }),
  garden: Object.freeze({
    switch: actionSpec(["choose"], null, true),
    double: actionSpec(["choose"], null, true),
    open: actionSpec(["choose"], null, true),
    pick_1: actionSpec(["choose"], 0, true),
    pick_2: actionSpec(["choose"], 1, true),
  }),
  rabbit: Object.freeze({
    ...Object.fromEntries(Array.from({ length: 60 }, (_, index) => [
      `floor_${index + 1}`, actionSpec(["trap", "move"]),
    ])),
    guard_3: actionSpec(["move"], 0),
    guard_4: actionSpec(["move"], 0),
    guard_5: actionSpec(["move"], 0),
    guard_6: actionSpec(["move"], 0),
  }),
});

// Translate a physical venue control into the exact game-state action. A null
// result means the control is a display or needs a multi-field form fallback.
export function physicalControlRequest(gameId, state, action) {
  if (!state) return null;
  if (gameId === "ace") {
    const tray = /^ace_tray_([1-4])$/.exec(action);
    const traySeat = tray ? Number(tray[1]) - 1 : -1;
    if (tray && ["roll", "reroll"].includes(state.phase) && state.actor === traySeat) {
      return { action: state.phase, value: shuffledDice() };
    }
    const actionMatch = /^ace_(call|drop)_([1-4])$/.exec(action);
    const actionSeat = actionMatch ? Number(actionMatch[2]) - 1 : -1;
    if (actionMatch && state.actor === actionSeat) {
      if (state.phase === "betting") {
        return {
          action: "bet",
          value: { type: actionMatch[1] === "call" ? "call" : "fold" },
        };
      }
      if (state.phase === "raise_response") {
        return {
          action: "respond",
          value: { type: actionMatch[1] === "call" ? "call" : "fold" },
        };
      }
    }
    return null;
  }
  if (gameId === "hope") {
    const match = /^floor_(\d{1,2})$/.exec(action);
    const value = match ? Number(match[1]) : 0;
    return match && ["choose", "reveal"].includes(state.phase) && value >= 1 && value <= 16
      ? { action: "floor", value } : null;
  }
  if (gameId === "walker") {
    const match = /^(walker_stop|bomb_prev|bomb_next|bomb_confirm)_([12])$/.exec(action);
    if (!match) return null;
    const expected = match[1] === "walker_stop" ? "walk" : "bomb_setup";
    if (state.phase !== expected) return null;
    return { action: match[1] === "walker_stop" ? "stop" : match[1].replace(/^bomb_/, "bomb_") };
  }
  if (gameId === "rabbit") {
    const match = /^floor_(\d{1,2})$/.exec(action);
    const value = match ? Number(match[1]) : 0;
    if (value >= 1 && value <= 60 && state.phase === "trap") return { action: "trap", value };
    if (value >= 1 && value <= 60 && state.phase === "move") return { action: "move", value };
    const guard = /^guard_([3-6])$/.exec(action);
    if (guard && state.phase === "move") return { action: "guard", value: `p${guard[1]}` };
    return null;
  }
  if (gameId === "clock") {
    const requests = {
      clock_hour_plus: { phase: "setup", action: "hour_plus" },
      clock_hour_minus: { phase: "setup", action: "hour_minus" },
      clock_minute_plus: { phase: "setup", action: "minute_plus" },
      clock_minute_minus: { phase: "setup", action: "minute_minus" },
      clock_time_confirm: { phase: "setup", action: "time_confirm" },
      clock_sector_cw: { phase: "move", action: "sector_cw" },
      clock_sector_ccw: { phase: "move", action: "sector_ccw" },
      clock_sector_confirm: { phase: "move", action: "sector_confirm" },
    };
    const request = requests[action];
    if (request && state.phase === request.phase) return { action: request.action };
    return null;
  }
  if (gameId === "fire") {
    const match = /^(rock|scissors|paper)_(hot|ice)_([12])$/.exec(action);
    const seat = match ? Number(match[3]) - 1 : -1;
    if (!match || state.phase !== "choice" || state.actor !== seat) return null;
    return {
      action: "choice",
      value: {
        number: { rock: 1, scissors: 2, paper: 3 }[match[1]],
        heat: match[2] === "hot" ? 1 : -1,
      },
    };
  }
  if (gameId === "pig") {
    const cardMatch = /^pig_card_([1-4])_([1-4])$/.exec(action);
    if (cardMatch && state.phase === "choose") {
      return { action: "choose", value: { card: Number(cardMatch[2]) } };
    }
    const targetMatch = /^pig_target_([1-4])_([1-4])$/.exec(action);
    if (targetMatch && state.phase === "attack") {
      const target = Number(targetMatch[2]) - 1;
      return pigAttackTargets(state, state.actor).includes(target)
        ? { action: "target", value: target }
        : null;
    }
    return null;
  }
  if (gameId === "grail") {
    const cup = /^grail_cup_([1-3])$/.exec(action);
    const potion = /^grail_potion_([1-3])$/.exec(action);
    if (cup && state.phase === "arrange" && state.actor === state.cupOwner) {
      return { action: "arrange", value: { trueIndex: Number(cup[1]) - 1 } };
    }
    if (potion && state.phase === "arrange" && state.actor === state.potionOwner) {
      return { action: "arrange", value: { holyIndex: Number(potion[1]) - 1 } };
    }
    if (cup && state.phase === "cup") {
      return { action: "cup", value: Number(cup[1]) - 1 };
    }
    if (potion && state.phase === "potion") {
      return { action: "potion", value: Number(potion[1]) - 1 };
    }
    return null;
  }
  if (gameId === "auction") {
    const bid = /^auction_bid_([1-4])_([1-4])$/.exec(action);
    if (bid) {
      const seat = Number(bid[1]) - 1;
      return state.phase === "bid" && state.actor === seat
        ? { action: "bid", value: Number(bid[2]) }
        : null;
    }
    // Compatibility for a previously installed four-pad row. New venue
    // installs use the per-seat cards above and reject out-of-turn presses.
    const legacy = /^ticket_([1-4])$/.exec(action);
    return legacy && state.phase === "bid" && state.actor === 0
      ? { action: "bid", value: Number(legacy[1]) }
      : null;
  }
  if (gameId === "garden") {
    if (action === "pick_1" || action === "pick_2") return { action: "pick" };
    return ["switch", "double", "open"].includes(action) && state.phase === "choose"
      ? { action: "choose", value: action }
      : null;
  }
  return null;
}

const GRAIL_LABELS = Object.freeze(["位置1", "位置2", "位置3"]);
const LIGHT_GUARD_LABELS = Object.freeze(["鉄壁", "歓迎", "居眠り"]);
const LIGHT_TOURIST_LABELS = Object.freeze(["心臓", "槌", "松明"]);
const MINE_CARD_LABELS = Object.freeze(["ブランク", "大喰らいの人魂", "先人のツルハシ", "無気力瓶"]);

const controlSlot = (action, prefix, count) => {
  const match = new RegExp(`^${prefix}_([1-${count}])$`).exec(action);
  return match ? Number(match[1]) - 1 : null;
};
const controlScope = state => ({
  phase: state.phase, round: state.round ?? null, actor: state.actor ?? null,
});
const sameControlScope = (progress, state) => Boolean(progress) &&
  progress.phase === state.phase && progress.round === (state.round ?? null) &&
  progress.actor === (state.actor ?? null);

// Extends a physical control into the multi-choice actions used by Grail,
// Candlelight and Devil's Mine. Each press remains a real venue interaction;
// progress is intentionally ephemeral and scoped to the current turn.
export function advancePhysicalControl(gameId, state, action, progress = null) {
  if (!state) return null;
  const current = sameControlScope(progress, state) ? progress : null;
  if (gameId === "ace") {
    const raiseStart = /^ace_raise_([1-4])$/.exec(action);
    if (raiseStart) {
      if (state.phase !== "betting") return null;
      return {
        request: null,
        progress: { ...controlScope(state), stage: "ace-raise", digits: [0, 0, 0] },
        notice: "中央のレイズ桁を上下し、確定を押してください。",
      };
    }
    const digit = /^ace_(table|raise)_(100|10|1)_(up|down)$/.exec(action);
    if (digit) {
      const table = digit[1] === "table";
      if (state.phase !== (table ? "table_bet" : "betting")) return null;
      const stage = table ? "ace-table" : "ace-raise";
      const kept = current?.stage === stage ? current : null;
      const digits = kept ? [...kept.digits] : [0, 0, 0];
      const index = { 100: 0, 10: 1, 1: 2 }[digit[2]];
      const limit = index === 0 ? 4 : 10;
      digits[index] = (digits[index] + (digit[3] === "up" ? 1 : -1) + limit) % limit;
      const amount = digits[0] * 100 + digits[1] * 10 + digits[2];
      return {
        request: null,
        progress: { ...controlScope(state), stage, digits },
        notice: `${table ? "場代" : "レイズ"}を${amount}枚に設定しました。`,
      };
    }
    if (action === "ace_table_confirm") {
      if (state.phase !== "table_bet") return null;
      const digits = current?.stage === "ace-table" ? current.digits : [0, 0, 0];
      const amount = digits[0] * 100 + digits[1] * 10 + digits[2];
      if (amount < 10) return { request: null, progress: current, notice: "場代は10枚以上です。" };
      if (amount > 399) {
        return { request: null, progress: current, notice: "原作の場代上限は399枚です。" };
      }
      return { request: { action: "table_bet", value: amount }, progress: null, notice: `場代を${amount}枚で確定しました。` };
    }
    if (action === "ace_raise_confirm") {
      if (state.phase !== "betting") return null;
      const digits = current?.stage === "ace-raise" ? current.digits : [0, 0, 0];
      const amount = digits[0] * 100 + digits[1] * 10 + digits[2];
      if (amount < 1) return { request: null, progress: current, notice: "レイズは1枚以上です。" };
      if (amount > state.raiseLimit) {
        return { request: null, progress: current, notice: `レイズ上限は${state.raiseLimit}枚です。` };
      }
      return {
        request: { action: "bet", value: { type: "raise", amount } }, progress: null,
        notice: `${amount}枚をレイズしました。`,
      };
    }
    const die = /^ace_die_([1-4])_([1-5])$/.exec(action);
    if (die) {
      if (state.phase !== "reroll_choice") return null;
      const kept = current?.stage === "ace-reroll" ? current : null;
      const selected = kept ? [...kept.selected] : [false, false, false, false, false];
      const index = Number(die[2]) - 1;
      selected[index] = !selected[index];
      return {
        request: null,
        progress: { ...controlScope(state), stage: "ace-reroll", selected },
        notice: `ダイス${index + 1}を${selected[index] ? "振り直します" : "保持します"}。`,
      };
    }
    const rerollConfirm = /^ace_reroll_confirm_([1-4])$/.exec(action);
    if (rerollConfirm) {
      if (state.phase !== "reroll_choice") return null;
      const selected = current?.stage === "ace-reroll"
        ? current.selected : [false, false, false, false, false];
      const dice = [...state.dice[state.actor]];
      const indices = [];
      selected.forEach((value, index) => {
        if (!value) return;
        indices.push(index);
        dice[index] = DICE[Math.floor(Math.random() * DICE.length)];
      });
      return {
        request: { action: "reroll_selected", value: { indices, dice } },
        progress: null,
        notice: indices.length ? `${indices.length}個のダイスを振り直しました。` : "ダイスを保持しました。",
      };
    }
    return null;
  }
  if (gameId === "grail") {
    const cup = /^grail_cup_([1-3])$/.exec(action);
    const potion = /^grail_potion_([1-3])$/.exec(action);
    const slot = Number(cup?.[1] ?? potion?.[1] ?? 0) - 1;
    if (slot < 0) return null;
    if (state.phase === "arrange") {
      if (cup && state.actor !== state.cupOwner) {
        return { request: null, progress: null, notice: "聖杯側のプレイヤーが聖杯棚を使用します。" };
      }
      if (potion && state.actor !== state.potionOwner) {
        return { request: null, progress: null, notice: "ポーション側のプレイヤーがポーション棚を使用します。" };
      }
      return {
        request: {
          action: "arrange",
          value: cup ? { trueIndex: slot } : { holyIndex: slot },
        },
        progress: null,
        notice: cup
          ? `本物の聖杯を${GRAIL_LABELS[slot]}に置きました。`
          : `聖水ポーションを${GRAIL_LABELS[slot]}に置きました。`,
      };
    }
    if (state.phase === "cup") {
      if (!cup) return { request: null, progress: null, notice: "聖杯棚から選んでください。" };
      return {
        request: { action: "cup", value: slot }, progress: null,
        notice: `聖杯${GRAIL_LABELS[slot]}を選びました。`,
      };
    }
    if (state.phase === "potion") {
      if (!potion) return { request: null, progress: null, notice: "ポーション棚から選んでください。" };
      return {
        request: { action: "potion", value: slot }, progress: null,
        notice: `ポーション${GRAIL_LABELS[slot]}を選びました。`,
      };
    }
    return null;
  }
  if (gameId === "light") {
    const seated = /^light_grave_([12])_([1-3])$/.exec(action);
    const legacy = /^grave_([1-3])$/.exec(action);
    const slot = seated ? Number(seated[2]) - 1 : legacy ? Number(legacy[1]) - 1 : null;
    if (slot === null) return null;
    if (seated && state.actor !== Number(seated[1]) - 1) {
      return { request: null, progress: null, notice: "自分のフォルダを操作してください。" };
    }
    const role = state.phase === "guard_arrange" ? "guard"
      : state.phase === "tourist_arrange" ? "tourist" : null;
    if (!role) {
      return { request: null, progress: null, notice: "現在は配置できません。判定表示を確認してください。" };
    }
    const placed = (role === "guard" ? state.guards : state.tourists)
      .filter(value => value !== null).length;
    const label = role === "guard" ? LIGHT_GUARD_LABELS[placed] : LIGHT_TOURIST_LABELS[placed];
    return {
      request: { action: "place", value: { role, slot } },
      progress: null,
      notice: `${role === "guard" ? "門番" : "死者"}「${label ?? "配置"}」を${slot + 1}枠へ置きます。`,
    };
  }
  if (gameId === "mine") {
    const seated = /^mine_rock_([12])_([1-4])$/.exec(action);
    const legacy = /^rock_([1-4])$/.exec(action);
    const slot = seated ? Number(seated[2]) - 1 : legacy ? Number(legacy[1]) - 1 : null;
    if (slot === null) return null;
    if (seated && state.actor !== Number(seated[1]) - 1) {
      return { request: null, progress: null, notice: "自分の岩を操作してください。" };
    }
    if (state.phase === "arrange") {
      const cards = current?.stage === "mine-cards" ? [...current.cards] : [null, null, null, null];
      const assigned = cards.filter(value => value !== null).length;
      if (cards[slot] !== null) {
        return { request: null, progress: current, notice: "その岩にはカードを配置済みです。" };
      }
      cards[slot] = assigned;
      if (assigned === 3) {
        return {
          request: { action: "arrange", value: { cards } },
          progress: null,
          notice: "4つの岩の配置を確定しました。",
        };
      }
      return {
        request: null,
        progress: { ...controlScope(state), stage: "mine-cards", cards },
        notice: `「${MINE_CARD_LABELS[assigned]}」を岩${slot + 1}に配置しました。`,
      };
    }
    if (state.phase === "choose") {
      const count = state.chooseCount ?? 2;
      const indices = current?.stage === "mine-pair" && current.count === count
        ? [...current.indices] : [];
      if (!(state.availableIndices ?? [0,1,2,3]).includes(slot) || indices.includes(slot)) {
        return { request: null, progress: current, notice: "未採掘の別の岩を選んでください。" };
      }
      indices.push(slot);
      if (indices.length < count) return { request: null,
        progress: { ...controlScope(state), stage: "mine-pair", indices, count },
        notice: `${indices.length}/${count}個の岩を選択しました。` };
      return {
        request: { action: "mine", value: { indices } },
        progress: null,
        notice: `岩${indices.map(value => value + 1).join("・")}を採掘します。`,
      };
    }
    return null;
  }
  return null;
}

const actorName = (table, seat) =>
  table.players[seat] ? `${seat + 1}P` : `${seat + 1}P`;

function shuffledDice() {
  return Array.from({ length: 5 }, () => DICE[Math.floor(Math.random() * DICE.length)]);
}

export function createCompleteRuntime({
  game, world, system, ActionFormData, ModalFormData, manager, venue = null,
}) {
  const runtimeForms = createRuntimeForms({ ActionFormData, ModalFormData, world, system });
  ({ ActionFormData, ModalFormData } = runtimeForms);
  const tableId = game.id;
  const receptionTag = `junket:${game.id}:reception`;
  const controlTag = `junket:${game.id}:control`;
  const displayTag = `junket:${game.id}:display`;
  const busy = new Set();
  const formTokens = new Map();
  const physicalProgress = new Map();
  let timer;
  let registrationTimer;
  let interval;
  let landingInterval;
  let intervalTicks = 0;
  let generation = 0;
  const table = () => manager.view(tableId);
  const tell = (player, message) => {
    try { player?.sendMessage(`[${game.title}] ${message}`); } catch { /* Disconnected. */ }
  };
  const members = current =>
    current ? world.getAllPlayers().filter(player => current.players.includes(player.id)) : [];
  const notify = (current, message) => members(current).forEach(player => tell(player, message));
  const seatOf = (current, player) => current?.players.indexOf(player.id) ?? -1;
  const allMembersOnline = current =>
    Boolean(current) && current.players.every((id, seat) =>
      (game.id === "hope" && current.state?.alive?.[seat] === false) || world.getAllPlayers().some(player => player.id === id));
  const prompts = createDecisionPrompts({ world, system, read: table,
    eligible: current => allMembersOnline(current) ? current.decisionSeats : [],
    key: (current, seat) => `${current.sessionToken}:${current.decisionSerials[seat]}:${manager.forPlayer(current.players[seat])?.decisionKey}`,
    busy: id => busy.has(id), open: player => open(player), notify: tell });
  const canDecide = (current, seat) => current?.decisionSeats?.includes(seat) === true;
  const canManual = (current, seat) => seat >= 0 && (
    (game.id === "walker" && current.state.phase === "walk" && current.state.actor === seat) ||
    (game.id === "rabbit" && current.state.phase === "move" && seat === 0) ||
    (game.id === "grail" && !["finished", "ending"].includes(current.state.phase) &&
      current.state.poisonCup[seat] + current.state.holyCup[seat] > 0) ||
    (game.id === "garden" && current.state.phase === "choose" && current.state.loads[seat] > 0) ||
    (game.id === "mine" && current.state.phase !== "finished" && current.state.self?.keys > 0));

  function validateControl(player, current, action) {
    const spec = COMPLETE_CONTROL_SPECS[game.id]?.[action];
    if (!spec) throw new Error("このゲームの操作物ではありません。");
    if (!current || current.gameId !== tableId) throw new Error("受付からこのゲームへ参加してください。");
    if (!current.state) throw new Error("試合開始前です。受付で参加者を待ってください。");
    if (!allMembersOnline(current)) throw new Error("参加者の再接続を待っています。");
    if (current.state.phase === "finished") throw new Error("試合は終了しています。");
    const seat = seatOf(current, player);
    if (seat < 0) throw new Error("受付からこのゲームへ参加してください。");
    if (spec.seat !== null && spec.seat !== seat) throw new Error("自分の操作物ではありません。");
    if (!spec.anytime && current.state.actor !== seat) throw new Error("現在の手番ではありません。");
    if (!spec.phases.includes(current.state.phase)) {
      throw new Error("この局面では使えない操作物です。");
    }
    return { seat, spec };
  }

  function winnerText(current) {
    const state = current?.state;
    if (!state) return "";
    if (state.reason === "timeout") return "時間切れ";
    if (Array.isArray(state.winners)) {
      if (!state.winners.length) return "全員脱落";
      return state.winners.map(seat => actorName(current, seat)).join("・");
    }
    if (Number.isInteger(state.winnerTeam) && game.id === "rabbit") {
      return state.winnerTeam === 0 ? "脱出側" : "オニ側";
    }
    if (Number.isInteger(state.winner)) return actorName(current, state.winner);
    if (Number.isInteger(state.winnerTeam)) {
      if (game.id === "pig") return state.winnerTeam === 0 ? "1P・4Pチーム" : "2P・3Pチーム";
      return `チーム${state.winnerTeam === 0 ? "A" : "B"}`;
    }
    return DRAW;
  }

  function status(current) {
    const s = current?.state;
    let text = statusText(current);
    if (!s) return text;
    const labels = { arrange: "秘密の配置", table_bet: "場代の設定", roll: "ダイスを振る",
      betting: "ベット", raise_response: "レイズへの応答", reroll_choice: "振り直すダイスの選択",
      reroll: "同点の振り直し", cup: "聖杯の選択", potion: "ポーションの選択", final_drink: "運命の器を飲む",
      choice: "両者の選択", choose: "行動の選択", bid: "入札", attack: "襲撃先の選択",
      reveal: "公開", result: "結果発表", finished: "終了", between: "次のゲームの準備",
      set_result: "セット結果", walk: "歩いて移動", dodge: "歩いて回避", bomb_setup: "爆弾の設置",
      trap: "罠の設置", move: game.id === "clock" ? "区画の選択" : "歩いて移動", setup: "時刻設定",
      drop: "落下判定", ending: "終了演出", sanction: "執行", burn: "温度の精算", waiting: "待機",
      payment: "落札の精算" };
    if (labels[s.phase]) text = text.replace(s.phase, labels[s.phase]);
    const remaining = s.inputRemaining ?? s.phaseRemaining;
    if (Number.isFinite(remaining) && remaining > 0) text += `\n残り ${Math.ceil(remaining / 20)}秒`;
    return text;
  }
  function statusText(current) {
    if (!current) return "参加者募集中です。";
    if (!current.state) return `${current.players.length}人参加済み。開始を待っています。`;
    const s = current.state;
    const names = current.players.map((id, seat) =>
      world.getAllPlayers().find(player => player.id === id)?.name ?? `参加者${seat + 1}`);
    switch (game.id) {
      case "ace":
        return `第${s.game}/5ゲーム / ${s.phase}\nコイン ${s.coins.join(" : ")}\n場代 ${s.tableBet} / 現在 ${s.currentBet}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "grail":
        return `第${s.round}/10ラウンド / ${s.phase}\n聖水 ${s.holy.join(" : ")} / 毒 ${s.poison.join(" : ")} / 体力 ${s.hp.map(value => Math.max(0, value)).join(" : ")}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "auction":
        return `第${s.set}セット ${s.round}/4 / ${s.phase}\nダイヤ A ${s.diamonds[0]} : B ${s.diamonds[1]}\nラウンド A ${s.roundWins[0]} : B ${s.roundWins[1]}\n支払 ${s.payment.join(" : ")} / 手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "fire":
        return `第${s.series}/4シリーズ 第${s.round}/6ラウンド / ${s.phase}\n`
          + `カード勝ち ${s.wins.join(" : ")} / 累積 ${s.totalWins.join(" : ")}\n`
          + `室温 ${s.temperature.join("℃ : ")}℃ / ストック ${s.stock.join(" : ")}℃ / 体力 ${s.hp.map(v => Math.max(0, v)).join(" : ")}\n`
          + `ガラス ${s.glass.map((v, seat) => v
            ? ["健全", "ひび1", "ひび2", "ひび3", "ひび4"][s.glassStage?.[seat] ?? 0] ?? "ひび4"
            : "破損").join(" : ")} / 風 ${s.windRemaining.join(" : ")}t\n`
          + (s.phase === "sanction"
            ? `執行 ${s.sanctionRemaining}t / 対象 ${s.sanctionTargets.map(seat => names[seat]).join("・")}\n`
            : s.phase === "ending" ? `終了演出 ${s.endingRemaining}t\n` : "")
          + `手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "pig":
        return `第${s.setNumber}セット ${s.handRound}/3ラウンド / ${s.phase}\n`
          + `子豚 ${s.pigs.join(" : ")} / 労働 ${s.labor.join(" : ")}\n`
          + `酸素 ${s.oxygen.map(value => `${value.toFixed(1)}%`).join(" : ")}\n`
          + `カード ${s.phase === "choose" ? "非公開" : s.choices.map(entry => entry ? entry.card : "-").join(" : ")}\n`
          + `手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "light": {
        const phaseName = {
          intro: "点火待ち", guard_arrange: "門番配置", tourist_arrange: "死者配置",
          result: "判定", finished: "終了",
        }[s.phase] ?? s.phase;
        const timer = s.phase === "intro" ? `開始まで${s.introRemaining}t`
          : ["guard_arrange", "tourist_arrange"].includes(s.phase) ? `配置残り${s.placementRemaining}t`
          : s.phase === "result" ? `判定残り${s.resultRemaining}t` : "";
        return `第${s.round}ラウンド / ${phaseName} ${timer}\n`
          + `ハート ${s.hearts.join(" : ")} / 松明 ${s.torches.join(" : ")} / 酸素 ${s.oxygen.join(" : ")}\n`
          + `門番 ${s.guardsPlaced}/3・死者 ${s.touristsPlaced}/3 / 手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      }
      case "mine":
        return `第${s.round}/20ラウンド / ${s.phase}\n体力 ${s.hp.map(v => Math.max(0, v)).join(" : ")} / インゴット ${s.ingots.join(" : ")} / 採掘 ${s.mined.join(" : ")}\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "hope":
        return `第${s.round}/10ラウンド / ${s.phase}`
          + (s.phaseRemaining ? `  残り${s.phaseRemaining}t` : "")
          + `\n危険図柄 ${s.hazards.length ? s.hazards.map(value => HOPE_SYMBOL_NAMES[value]).join("・") : "未公開"}`
          + `\n生存 ${s.alive.map((alive, seat) => alive ? names[seat] : `${names[seat]}（脱落）`).join(" / ")}`
          + `\n手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "clock":
        return `第${s.turn}ターン / ${s.phase}\n`
          + `現在 ${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}`
          + ` → 次回 ${String(s.nextHour).padStart(2, "0")}:${String(s.nextMinute).padStart(2, "0")}\n`
          + `区画 ${s.selected.length}/${s.requiredSectors} 確定 ${s.selectedLabels.join("") || "なし"}`
          + ` / カーソル ${s.cursorLabel}\n`
          + (s.phase === "setup" ? `設定残り ${s.setupRemaining}t`
            : s.phase === "move" ? `区画選択残り ${s.moveRemaining}t`
            : s.phase === "drop" ? `落下演出 ${s.dropRemaining}t`
            : s.phase === "result" ? `終了まで ${s.resultRemaining}t` : "終了")
          + ` / 手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "walker": {
        const bombs = s.phase === "bomb_setup"
          ? `${s.bombCount}/${s.bombQuota}（候補 ${s.bombCursor}）`
          : s.last
            ? `${s.last.bombs.join(", ") || "なし"} / 命中 ${s.last.hit.map((hit, seat) => `${names[seat]}:${hit ? "被弾" : "回避"}`).join(" ")}`
            : "なし";
        const timer = s.phase === "walk" ? `移動残り ${s.walkRemaining}t`
          : s.phase === "bomb_setup" ? `設置残り ${s.setupRemaining}t`
          : s.phase === "dodge" ? `回避残り ${s.dodgeRemaining}t`
          : s.phase === "fuse" ? `起爆まで ${s.fuseRemaining}t`
          : s.phase === "aftermath" ? `結果まで ${s.aftermathRemaining}t` : "終了";
        return `第${s.round}ラウンド / ${s.phase}\n位置 ${s.positions.join(" : ")} / 爆弾 ${bombs}\n`
          + `${timer} / 死床 ${s.deadFloors.length} / 手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      }
      case "garden":
        return `第${s.round}ラウンド / ${s.phase}\n装置 ${s.device}（実数 ${s.deviceNow}） / 宝石箱 ${s.loads.join(" : ")} / 弁 ${s.valve + 1}P側\n切替 ${s.changeAvailable.map(value => value ? "可" : "休止").join(" : ")} / 手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      case "rabbit": {
        const reveal = ["reveal", "drop", "result", "finished"].includes(s.phase);
        const participants = s.participants.map(participant =>
          `${participant.name} ${participant.position}${participant.alive ? "" : "（脱落）"}`).join(" / ");
        const traps = reveal ? (s.traps.join(", ") || "なし") : "非公開";
        const guards = reveal && s.guardedFloors?.length
          ? ` / 守護床 ${s.guardedFloors.join(", ")}` : "";
        const result = s.phase === "finished"
          ? ` / 勝者 ${s.winnerTeam === 0 ? "脱出側" : "オニ側"}（${s.reason}）`
          : "";
        return `第${s.round}ラウンド / ${s.phase} 残り${s.phaseRemaining}t\n`
          + `${participants}\n罠 ${traps}${guards}${result} / 手番 ${s.actor === null ? "なし" : names[s.actor]}`;
      }
      default:
        return `${s.phase}`;
    }
  }

  function clearTimer() {
    if (timer !== undefined) system.clearRun(timer);
    timer = undefined;
    generation++;
  }
  function clearRegistration() {
    if (registrationTimer !== undefined) system.clearRun(registrationTimer);
    registrationTimer = undefined;
  }
  function stopTicker() {
    if (interval !== undefined) system.clearRun(interval);
    interval = undefined;
    if (landingInterval !== undefined) system.clearRun(landingInterval);
    landingInterval = undefined;
  }
  function finish(current, message) {
    prompts.clear(); runtimeForms.invalidate();
    clearTimer();
    clearRegistration();
    stopTicker();
    const result = manager.close(tableId);
    for (const playerId of current?.players ?? []) physicalProgress.delete(playerId);
    try { venue?.update(game.id, result ?? current); } catch { /* The optional venue may not be installed. */ }
    try { venue?.end?.(game.id, result ?? current); } catch { /* A disconnected participant cannot be teleported. */ }
    notify(result ?? current, message);
    return result;
  }
  function complete(current) {
    if (current?.state?.phase !== "finished") return false;
    finish(current, `終了：${winnerText(current)}（${current.state.reason ?? "決着"}）。受付から再戦できます。`);
    return true;
  }
  function arm(current) {
    clearTimer();
    // Clock persists and decrements its source-derived setup, selection, drop
    // and landing timers in the game ticker. A second generic input timeout
    // would fire during the physical drop and corrupt the turn sequence.
    if (["ace", "grail", "hope", "clock", "walker", "rabbit", "light", "mine"].includes(game.id)) return;
    if (!current?.state || current.state.phase === "finished" || current.state.actor === null) return;
    const activeGeneration = generation;
    timer = system.runTimeout(() => {
      timer = undefined;
      if (activeGeneration !== generation || !table()?.state) return;
      const next = manager.systemAct(tableId, "timeout");
      if (!next || !complete(next)) {
        notify(next, "入力時間切れです。");
        afterAction(next);
      }
    }, JUNKET_INPUT_TICKS);
  }
  function automatic(current) {
    const phase = current?.state?.phase;
    if (!["between", "set_result"].includes(phase)) return false;
    const action = phase === "between" ? "next_game" : "next_set";
    const activeGeneration = generation;
    system.runTimeout(() => {
      if (activeGeneration !== generation || table()?.token !== current.token) return;
      const next = manager.systemAct(tableId, action);
      if (!next || !complete(next)) afterAction(next);
    }, 60);
    return true;
  }
  function afterAction(current) {
    if (!current || complete(current)) return;
    try { venue?.update(game.id, current); } catch { /* The optional venue may not be installed. */ }
    notify(current, status(current));
    if (!automatic(current)) {
      arm(current);
      void openActor(current);
    }
  }
  async function openActor(current) {
    prompts.schedule(current);
  }
  function startLandingObserver() {
    if (!["garden", "clock", "rabbit"].includes(game.id) || landingInterval !== undefined) return;
    landingInterval = system.runInterval(() => {
      const current = table();
      if (!current?.state || current.state.phase === "finished") {
        if (landingInterval !== undefined) system.clearRun(landingInterval);
        landingInterval = undefined;
        return;
      }
      if (game.id === "garden") {
        if (current.state.phase !== "drop" || current.state.dropRemaining > 0) return;
        if (!allMembersOnline(current)) return;
        try {
          const landed = venue?.pollGardenLanding?.(current);
          if (!Number.isInteger(landed)) return;
          const next = manager.systemAct(tableId, "land", landed);
          afterAction(next);
        } catch (error) {
          notify(current, error.message);
        }
        return;
      }
      if (game.id === "rabbit") {
        if (current.state.phase !== "drop") return;
        if (!allMembersOnline(current)) return;
        try {
          const landed = venue?.pollRabbitLanding?.(current);
          if (!Array.isArray(landed)) return;
          if (Number(current.state.phaseRemaining ?? 0) > 0) {
            if (landed.length) manager.systemAct(tableId, "observe", landed);
            return;
          }
          const next = manager.systemAct(tableId, "land", landed);
          afterAction(next);
        } catch (error) {
          notify(current, error.message);
        }
        return;
      }
      if (!["drop", "result"].includes(current.state.phase) || current.state.reason === "timeout") return;
      if (!allMembersOnline(current)) return;
      try {
        const landed = venue?.pollClockLanding?.(current);
        if (!Array.isArray(landed) || !landed.length) return;
        if (landed.every(seat => current.state.landed.includes(seat))) return;
        const next = manager.systemAct(tableId, "land", landed);
        afterAction(next);
      } catch (error) {
        notify(current, error.message);
      }
    }, 1);
  }
  function syncWalkerVenue(current) {
    if (game.id !== "walker" || !current?.state || current.state.phase === "finished") return current;
    let latest = current;
    let changed = false;
    try {
      const observed = venue?.pollWalker?.(latest);
      for (let seat = 0; seat < 2; seat++) {
        if (latest.state.actor !== seat || !["walk", "dodge"].includes(latest.state.phase)) continue;
        const floor = observed?.positions?.[seat];
        if (Number.isInteger(floor) && floor !== latest.state.positions[seat]) {
          latest = manager.systemAct(tableId, "position", { seat, floor }, { persist: false }) ?? latest;
          changed = true;
        }
      }
      if (Array.isArray(observed?.water) && observed.water.length) {
        const before = [...latest.state.water];
        const next = manager.systemAct(tableId, "water", observed.water, { persist: false });
        if (next && (!Array.isArray(next.state?.water) ||
            next.state.water[0] !== before[0] || next.state.water[1] !== before[1])) {
          latest = next;
          changed = true;
        }
      }
      if (changed) manager.persist();
    } catch (error) {
      notify(current, error.message);
    }
    return latest;
  }
  function syncHopePositions(current) {
    if (game.id !== "hope" || !current?.state ||
        !["choose", "reveal"].includes(current.state.phase)) return current;
    try {
      const observed = venue?.pollHopePads?.(current);
      if (!Array.isArray(observed) || observed.length !== current.state.playerCount ||
          !observed.some(Number.isInteger)) return current;
      const before = current.state.choices.map(choice => choice?.pad ?? null);
      if (observed.every((pad, seat) => pad === before[seat])) return current;
      const next = manager.systemAct(tableId, "position", observed, { persist: false });
      if (next) manager.persist();
      return next ?? current;
    } catch (error) {
      notify(current, error.message);
      return current;
    }
  }
  function syncRabbitVenue(current) {
    if (game.id !== "rabbit" || !current?.state || current.state.phase !== "move") return current;
    try {
      const tile = venue?.pollRabbitPosition?.(current);
      const human = current.state.participants?.find(participant => participant.human);
      if (!Number.isInteger(tile) || !human?.alive || tile === human.position) return current;
      if (!rabbitLegalMoves(current.state, "p1").includes(tile)) return current;
      const next = manager.systemAct(tableId, "position", { tile }, { persist: false });
      if (next) manager.persist();
      return next ?? current;
    } catch (error) {
      notify(current, error.message);
      return current;
    }
  }
  function startTicker() {
    if (interval !== undefined || !["grail", "auction", "hope", "clock", "fire", "garden", "light", "mine", "pig", "walker", "rabbit"].includes(game.id)) return;
    const tickStep = game.id === "fire" ? FIRE_TICK_STEP
      : game.id === "hope" || game.id === "clock" || game.id === "walker" ||
        game.id === "rabbit" || game.id === "light" || game.id === "mine" || game.id === "grail" || game.id === "auction" ? 1
      : game.id === "garden" ? 20
      : game.id === "pig" ? PIG_TICK_STEP : 20;
    intervalTicks = 0;
    startLandingObserver();
    interval = system.runInterval(() => {
      let current = table();
      if (!current?.state || current.state.phase === "finished") {
        stopTicker();
        return;
      }
      // A restored match must not lose health or oxygen while a participant is
      // reconnecting after a server restart.
      if (!allMembersOnline(current)) return;
      try {
        if (game.id === "walker") current = syncWalkerVenue(current);
        if (game.id === "hope") current = syncHopePositions(current);
        if (game.id === "rabbit") current = syncRabbitVenue(current);
        intervalTicks += tickStep;
        // A one-tick simulation must not serialize every table 20 times a
        // second. Persist on every phase/turn transition and at least once a
        // second so a restart loses no more than a short deterministic window.
        const next = manager.systemAct(tableId, "tick", undefined, { persist: false });
        if (!next) return;
        const changed = current.token !== next.token || current.state.phase !== next.state.phase;
        if (changed || intervalTicks % 20 === 0) manager.persist();
        if (changed || intervalTicks % 20 === 0 || game.id === "clock" || game.id === "hope") {
          try { venue?.update(game.id, next); } catch { /* The optional venue may not be installed. */ }
        }
        if (!complete(next)) {
          if (changed && (game.id === "auction" || ["between", "set_result"].includes(next.state.phase))) afterAction(next);
          else if (current.token !== next.token) void openActor(next);
          if (changed || intervalTicks % (game.id === "fire" ? 10 : 20) === 0) {
            for (const player of members(next)) {
              player.onScreenDisplay?.setActionBar(status(next).replace(/\n/g, " / "));
            }
          }
        }
      } catch (error) {
        notify(current, error.message);
      }
    }, tickStep);
  }
  function armRegistration(current) {
    clearRegistration();
    if (!game.registration || !current || current.state || !current.players.length) return;
    registrationTimer = system.runTimeout(() => {
      registrationTimer = undefined;
      const live = table();
      if (!live || live.state || !live.players.length) return;
      try {
        start(manager.start(tableId));
      } catch (error) {
        notify(live, error.message);
      }
    }, HOPE_REGISTRATION_TICKS);
  }
  function start(current) {
    clearTimer();
    clearRegistration();
    try { venue?.start(game.id, current); } catch (error) { notify(current, `会場移動: ${error.message}`); }
    notify(current, status(current));
    notify(current, "選択メニューは自動で開きます。閉じた場合は、しゃがむ操作を素早く2回行うとその場で再開できます。移動・落下中のゲームは歩いて操作してください。");
    startTicker();
    arm(current);
    void openActor(current);
  }

  async function aceForm(player, current) {
    const s = current.state, seat = seatOf(current, player);
    let value;
    if (s.phase === "table_bet") {
      const response = await new ModalFormData().title("タンブリング・エース：場代")
        .textField(`${status(current)}\n場代（10〜399枚の整数。借り入れを含みます）`, "半角数字", { defaultValue: "10" }).show(player);
      if (response.canceled) return null;
      value = Number(String(response.formValues?.[0] ?? "").trim());
    } else if (s.phase === "betting") {
      const response = await new ActionFormData().title("タンブリング・エース：ベット")
        .body(status(current)).button("コール").button("レイズ").button("フォールド").show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      if (response.selection === 2) value = { type: "fold" };
      else if (response.selection === 0) value = { type: "call" };
      else {
        const raise = await new ModalFormData().title("レイズ枚数")
          .textField(`1〜${s.raiseLimit}枚`, "半角数字", { defaultValue: "20" }).show(player);
        if (raise.canceled) return null;
        value = { type: "raise", amount: Number(String(raise.formValues?.[0] ?? "").trim()) };
      }
    } else if (s.phase === "raise_response") {
      const response = await new ActionFormData().title("タンブリング・エース：レイズ応答")
        .body(status(current)).button("コール").button("ドロップ").show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      value = { type: response.selection === 0 ? "call" : "fold" };
      return { action: "respond", value };
    } else if (s.phase === "reroll_choice") {
      const form = new ModalFormData().title("タンブリング・エース：振り直し");
      for (let index = 0; index < 5; index++) {
        form.toggle(`ダイス${index + 1}「${s.dice[seat][index]}」を振り直す`, { defaultValue: false });
      }
      const response = await form.show(player);
      if (response.canceled) return null;
      const dice = [...s.dice[seat]];
      const indices = [];
      (response.formValues ?? []).forEach((selected, index) => {
        if (!selected) return;
        indices.push(index);
        dice[index] = DICE[Math.floor(Math.random() * DICE.length)];
      });
      return { action: "reroll_selected", value: { indices, dice } };
    } else if (s.phase === "roll" || s.phase === "reroll") {
      const response = await new ActionFormData().title("タンブリング・エース：ダイス")
        .body(status(current)).button("5個のダイスを振る").show(player);
      if (response.canceled || response.selection !== 0) return null;
      value = shuffledDice();
    } else {
      tell(player, status(current));
      return null;
    }
    return { action: s.phase === "betting" ? "bet" : s.phase, value };
  }

  async function grailForm(player, current) {
    const s = current.state, seat = seatOf(current, player);
    const canDrink = s.poisonCup[seat] + s.holyCup[seat] > 0;
    if (canDrink) {
      const proceed = s.phase !== "final_drink" && canDecide(current, seat);
      const options = proceed ? ["continue", "drink"] : ["drink"];
      const form = new ActionFormData().title("ホーリーグレイル：運命の器")
        .body(`${status(current)}\n自分の器：聖水${s.holyCup[seat]} / 毒${s.poisonCup[seat]}\n飲む時期を選べます。聖水は半量の毒を相殺します。`);
      if (proceed) form.button("今回の配置・選択へ進む");
      form.button("自分の運命の器を飲む");
      const response = await form.show(player);
      if (response.canceled || !options[response.selection]) return null;
      if (options[response.selection] === "drink") return { action: "drink" };
    }
    if (!canDecide(current, seat) || s.phase === "final_drink") return null;
    if (s.phase === "arrange") {
      const cupOwner = seat === s.cupOwner;
      const response = await new ModalFormData().title("ホーリーグレイル：配置")
        .dropdown(cupOwner ? "本物の聖杯" : "聖水ポーション",
          ["位置1", "位置2", "位置3"], { defaultValue: 0 }).show(player);
      if (response.canceled) return null;
      const index = response.formValues[0];
      return {
        action: "arrange",
        value: cupOwner ? { trueIndex: index } : { holyIndex: index },
      };
    }
    const action = s.phase === "cup" ? "cup" : "potion";
    const used = action === "cup" ? s.usedCups ?? [] : s.usedPotions ?? [];
    const choices = [0, 1, 2].filter(index => !used.includes(index));
    const form = new ActionFormData().title(action === "cup" ? "聖杯を選ぶ" : "ポーションを選ぶ").body(status(current));
    for (const index of choices) form.button(`位置${index + 1}`);
    const response = await form.show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return null;
    return choices[response.selection] === undefined ? null : { action, value: choices[response.selection], seat };
  }

  async function auctionForm(player, current) {
    const s = current.state, seat = seatOf(current, player);
    const used = Array.isArray(s.selected?.[seat]) ? s.selected[seat] : [];
    const available = [1, 2, 3, 4].filter(card => !used.includes(card));
    const form = new ActionFormData().title("ライフ・イズ・オークショニア")
      .body(`${status(current)}\n未使用：${available.join("・")}`);
    for (const card of available) form.button(`${card}の札`);
    const response = await form.show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return null;
    return { action: "bid", value: available[response.selection] };
  }

  async function fireForm(player, current) {
    const s = current.state, seat = seatOf(current, player);
    const available = [
      [1, 1], [2, 1], [3, 1], [1, -1], [2, -1], [3, -1],
    ].filter(([number, heat]) => !s.used[seat].includes(`${number}:${heat === 1 ? "hot" : "ice"}`));
    const form = new ActionFormData().title("シヴァリング・ファイア：カード")
      .body(`${status(current)}\nHOTは室温を上げ、ICEは下げます。`);
    for (const [number, heat] of available) {
      form.button(FIRE_CARD_LABELS[`${number}:${heat === 1 ? "hot" : "ice"}`]);
    }
    const response = await form.show(player);
    if (response.canceled || !Number.isInteger(response.selection)) return null;
    const [number, heat] = available[response.selection];
    return { action: "choice", value: { number, heat } };
  }

  async function pigForm(player, current) {
    const seat = seatOf(current, player), s = current.state;
    if (s.phase === "choose") {
      const available = [1, 2, 3, 4].filter(card => !s.used[seat].includes(card));
      const form = new ActionFormData().title("ピーキー・ピッグ・パレス：手札")
        .body(`${status(current)}\nこのセットの残り手札から1枚を伏せて選びます。`);
      for (const card of available) {
        form.button({ 1: "わらの家", 2: "木の家", 3: "レンガの家", 4: "オオカミ" }[card]);
      }
      const response = await form.show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      return { action: "choose", value: { card: available[response.selection] } };
    }
    if (s.phase === "attack") {
      const targets = pigAttackTargets(s, seat);
      const form = new ActionFormData().title("ピーキー・ピッグ・パレス：襲撃")
        .body(`${status(current)}\n家は1回だけ、狼も1回だけ指定できます。`);
      for (const target of targets) {
        const kind = s.choices[target]?.card === 4 ? "オオカミ" : "家";
        form.button(`${target + 1}P の${kind}`);
      }
      const response = await form.show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      return { action: "target", value: targets[response.selection] };
    }
    return null;
  }

  async function lightForm(player, current) {
    const s = current.state;
    const role = s.phase === "guard_arrange" ? "guard"
      : s.phase === "tourist_arrange" ? "tourist" : null;
    if (!role) return null;
    const options = role === "guard" ? LIGHT_GUARD_LABELS : LIGHT_TOURIST_LABELS;
    const choices = permutations([0, 1, 2]);
    const form = new ActionFormData().title(`キャンドルライト：${role === "guard" ? "門番" : "死者"}配置`)
      .body(`${status(current)}\n左から枠1・2・3の順です。`);
    for (const order of choices) form.button(order.map(index => options[index]).join(" → "));
    const response = await form.show(player);
    if (response.canceled || !choices[response.selection]) return null;
    return {
      action: "arrange",
      value: role === "guard"
        ? { guards: choices[response.selection] }
        : { tourists: choices[response.selection] },
    };
  }

  async function mineForm(player, current) {
    const s = current.state;
    const keyOptions = [];
    if (s.self?.keys > 0) for (let target = 0; target < 2; target++) {
      for (const [skill, label, max] of [["clean", "清掃", 1], ["work", "働き者", 2], ["fate", "運命", 2]]) {
        if (s.skills[target][skill] < max) keyOptions.push({ target, skill, label: `${target + 1}P：${label}` });
      }
    }
    if (keyOptions.length || s.canWait) {
      const options = [];
      if (canDecide(current, seatOf(current, player))) options.push({ action: "continue", label: s.phase === "arrange" ? "岩の配置へ" : `${s.chooseCount}つの岩を採掘する` });
      if (s.canWait && canDecide(current, seatOf(current, player))) options.push({ action: "wait_30", label: "30秒待機する" });
      if (keyOptions.length) options.push({ action: "key", label: "鍵を使う（1回限り）" });
      const form = new ActionFormData().title("デビルズマイン：ゲーム操作").body(status(current));
      for (const option of options) form.button(option.label);
      const response = await form.show(player);
      const option = response.canceled ? null : options[response.selection];
      if (!option) return null;
      if (option.action === "wait_30") return { action: "wait_30" };
      if (option.action === "key") {
        const select = new ActionFormData().title("鍵の対象と効果")
          .body("清掃：洗浄装置。働き者：採掘数の変化。運命：ヘルインゴットを半分にします。使用後の取り消しはできません。");
        for (const key of keyOptions) select.button(key.label);
        const selected = await select.show(player);
        const key = selected.canceled ? null : keyOptions[selected.selection];
        return key ? { action: "key", value: { target: key.target, skill: key.skill } } : null;
      }
    }
    if (!canDecide(current, seatOf(current, player))) return null;
    if (s.phase === "arrange") {
      const options = ["ブランク", "大喰らいの人魂", "先人のツルハシ", "無気力瓶"];
      const choices = permutations([0, 1, 2, 3]);
      const form = new ActionFormData().title("デビルズマイン：岩の配置")
        .body(`${status(current)}\n位置1〜4の順に並べます。`);
      for (const order of choices) form.button(order.map(index => options[index]).join(" → "));
      const response = await form.show(player);
      if (response.canceled || !choices[response.selection]) return null;
      return { action: "arrange", value: { cards: choices[response.selection] } };
    }
    const choices = combinations(s.availableIndices ?? [0,1,2,3], s.chooseCount ?? 2);
    const form = new ActionFormData().title(`デビルズマイン：${s.chooseCount ?? 2}つの岩を選ぶ`).body(status(current));
    for (const indices of choices) form.button(indices.map(index => `位置${index + 1}`).join("・"));
    const response = await form.show(player);
    if (response.canceled || !choices[response.selection]) return null;
    return { action: "mine", value: { indices: choices[response.selection] } };
  }

  async function boardForm(player, current) {
    const s = current.state;
    if (game.id === "walker" && s.phase === "walk") {
      const response = await new ActionFormData().title("クレバス・ウォーカー：移動")
        .body(`${status(current)}\n移動は画面を閉じて歩いてください。停止を選ぶと現在の床で確定します。`)
        .button("この床で停止する").show(player);
      return response.canceled || response.selection !== 0 ? null : { action: "stop" };
    }
    if (game.id === "walker" && s.phase === "bomb_setup") {
      const actions = ["bomb_prev", "bomb_next", "bomb_confirm"];
      const response = await new ActionFormData().title("クレバス・ウォーカー：爆弾配置")
        .body(status(current)).button("候補を前へ").button("候補を次へ").button("この床に配置").show(player);
      return response.canceled || !actions[response.selection] ? null : { action: actions[response.selection] };
    }
    if (game.id === "hope") {
      const form = new ActionFormData().title("ショートホープ：安全な床").body(status(current));
      const pads = s.pads ?? HOPE_PAD_SYMBOLS.map((symbol, index) => ({
        pad: index + 1, symbol, name: HOPE_SYMBOL_NAMES[symbol],
      }));
      for (const pad of pads) form.button(`床${pad.pad} ${pad.name}`);
      const response = await form.show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      return { action: "floor", value: response.selection + 1 };
    }
    if (game.id === "clock") {
      const setup = s.phase === "setup";
      const options = setup
        ? [
          { label: "時を+1", action: "hour_plus" },
          { label: "時を-1", action: "hour_minus" },
          { label: "分を+5", action: "minute_plus" },
          { label: "分を-5", action: "minute_minus" },
          { label: "この時刻で決定", action: "time_confirm" },
        ]
        : [
          { label: "区画を時計回り", action: "sector_cw" },
          { label: "区画を反時計回り", action: "sector_ccw" },
          { label: "カーソル区画を決定", action: "sector_confirm" },
        ];
      const form = new ActionFormData().title(
        setup ? "ヘル・オ・クロック：時間設定" : "ヘル・オ・クロック：区画選択",
      ).body(status(current));
      options.forEach(option => form.button(option.label));
      const response = await form.show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      const option = options[response.selection];
      return option ? { action: option.action } : null;
    }
    if (game.id === "garden") {
      const seat = current.players.indexOf(player.id);
      const options = [];
      if (!s.self?.choice) {
        if (s.changeAvailable[seat]) options.push({ label: "切替", value: "switch" });
        options.push({ label: "宝石倍化", value: "double" }, { label: "開放", value: "open" });
      }
      if (s.loads[seat] > 0) options.push({ label: "宝石箱から1個戻す", value: "pick" });
      const form = new ActionFormData().title("ジュエルシャワー").body(status(current));
      options.forEach(option => form.button(option.label));
      const response = await form.show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      const option = options[response.selection];
      if (!option) return null;
      return option.value === "pick" ? { action: "pick" } : { action: "choose", value: option.value };
    }
    if (game.id === "rabbit") {
      if (s.phase === "trap") {
        const options = s.self?.legalTraps ?? [];
        const form = new ActionFormData().title("オニ：罠設置").body(`${status(current)}\n設置済み ${s.self?.traps?.join("・") || "なし"}`);
        for (const tile of options) form.button(`床${tile}`);
        const response = await form.show(player);
        if (response.canceled || options[response.selection] === undefined) return null;
        return { action: "trap", value: options[response.selection] };
      }
      if (s.phase !== "move") {
        tell(player, status(current));
        return null;
      }
      const human = s.participants.find(participant => participant.human);
      const options = [];
      if (human?.haveGuard) {
        for (const participant of s.participants) {
          if (!participant.human && participant.alive && !participant.guard) {
            options.push({ label: `お守り: ${participant.name}`, kind: "guard", value: participant.id });
          }
        }
      }
      for (const target of rabbitLegalMoves(s, "p1")) {
        options.push({
          label: target === human.position ? "その場に留まる" : `${target}へ移動`,
          kind: "move", value: target,
        });
      }
      const form = new ActionFormData().title("脱出側：移動").body(status(current));
      options.forEach(option => form.button(option.label));
      const response = await form.show(player);
      if (response.canceled || !Number.isInteger(response.selection)) return null;
      const option = options[response.selection];
      if (!option) return null;
      return { action: option.kind, value: option.value };
    }
    return null;
  }

  async function actionForm(player, current) {
    let request;
    if (game.id === "ace") request = await aceForm(player, current);
    else if (game.id === "grail") request = await grailForm(player, current);
    else if (game.id === "auction") request = await auctionForm(player, current);
    else if (game.id === "fire") request = await fireForm(player, current);
    else if (game.id === "pig") request = await pigForm(player, current);
    else if (game.id === "light") request = await lightForm(player, current);
    else if (game.id === "mine") request = await mineForm(player, current);
    else request = await boardForm(player, current);
    if (!request) return;
    if (!allMembersOnline(manager.view(tableId))) throw new Error("参加者の再接続を待っています。");
    const next = canManual(current, seatOf(current, player)) && !canDecide(current, seatOf(current, player)) &&
      !current.supplementalKeys?.[request.action]
      ? manager.act(player.id, current.token, request.action, request.value)
      : manager.actDecision(player.id, current, request.action, request.value);
    afterAction(next);
  }

  async function open(player, navigation = false) {
    if (manager.maintenance) { tell(player, "会場を確認中です。完了後に再操作してください。"); return; }
    if (busy.has(player.id)) return;
    busy.add(player.id);
    let reopenAfterJoin = false;
    try {
      const mine = manager.forPlayer(player.id);
      if (mine && mine.gameId !== tableId) throw new Error("別のゲームに参加しています。");
      if (mine?.state) {
        const seat = seatOf(mine, player);
        if (!navigation &&
            (!mine.state.alive || mine.state.alive?.[seat] !== false)) {
          if (canDecide(mine, seat) || canManual(mine, seat)) {
            await actionForm(player, mine);
            return;
          }
        }
        const action = await showReceptionNavigation({ ActionFormData, player, gameId: game.id,
          title: game.title, body: status(mine), rules: gameRules(game.id), joined: true,
          canAct: navigation && (canDecide(mine, seat) || canManual(mine, seat)) &&
            (!mine.state.alive || mine.state.alive[seat] !== false),
          seats: game.seats, registration: game.registration });
        if (manager.forPlayer(player.id)?.token !== mine.token) throw new Error("画面が古くなりました。");
        if (action === "leave") {
          finish(mine, "参加者が退出したため中止しました。");
        } else if (action === "act") {
          await actionForm(player, mine);
        } else if (action === "catalog") tell(player, "退出を選んでからカタログへ戻ってください。");
        return;
      }
      const current = mine ?? table();
      const canJoin = !mine && (!current || current.players.length < (game.maxPlayers ?? game.seats));
      const action = await showReceptionNavigation({ ActionFormData, player, gameId: game.id,
        title: game.title, body: current ? status(current) : "参加者募集中です。",
        rules: gameRules(game.id), joined: !!mine, canJoin,
        seats: game.seats, registration: game.registration });
      if ((manager.forPlayer(player.id)?.token ?? null) !== (mine?.token ?? null) ||
          (table()?.token ?? null) !== (current?.token ?? null)) throw new Error("画面が古くなりました。");
      if (!action) return;
      if (action === "catalog") {
        if (mine) tell(player, "退出を選んでからカタログへ戻ってください。");
        else await requestCatalogReturn(player, world);
        return;
      }
      if (action === "leave" && mine) {
        finish(mine, "参加者が退出したため中止しました。");
        return;
      }
      if (!canJoin || !["join", "rematch"].includes(action)) return;
      const joined = manager.join(tableId, tableId, player.id);
      notify(joined, status(joined));
      if (joined.state) {
        start(joined);
        reopenAfterJoin = canDecide(joined, seatOf(joined, player)) &&
          joined.state.phase !== "finished";
      }
      else if (game.registration) {
        armRegistration(joined);
        notify(joined, "15秒以内の追加参加を受け付けます。");
      }
    } catch (error) {
      tell(player, error.message ?? "操作に失敗しました。");
    } finally {
      busy.delete(player.id);
      if (reopenAfterJoin) {
        prompts.schedule(table());
      } else if (!navigation) {
        const current = manager.forPlayer(player.id);
        if (current?.state && canDecide(current, seatOf(current, player))) {
          void openActor(current);
        }
      }
    }
  }

  async function activateControl(player, action) {
    const current = manager.forPlayer(player.id);
    validateControl(player, current, action);
    const sequence = advancePhysicalControl(
      game.id, current.state, action, physicalProgress.get(player.id));
    if (sequence) {
      if (sequence.progress) physicalProgress.set(player.id, sequence.progress);
      else physicalProgress.delete(player.id);
      if (sequence.notice) tell(player, sequence.notice);
      if (!sequence.request) return;
      const next = manager.act(player.id, current.token, sequence.request.action, sequence.request.value);
      afterAction(next);
      return;
    }
    const request = physicalControlRequest(game.id, current.state, action);
    if (request) {
      const next = manager.act(player.id, current.token, request.action, request.value);
      afterAction(next);
      return;
    }
    await open(player);
  }

  world.beforeEvents.playerInteractWithEntity.subscribe(event => {
    if (isServiceGuide(world, event.target)) return;
    const isReception = event.target.hasTag(receptionTag);
    const isControl = event.target.hasTag(controlTag);
    const isDisplay = event.target.hasTag(displayTag);
    if (!isReception && !isControl && !isDisplay) return;
    event.cancel = true;
    const player = event.player;
    const target = event.target;
    system.run(() => {
      if (isReception) {
        void open(player);
        return;
      }
      let action = "";
      try { action = String(target.getDynamicProperty?.("junket:venue_action") ?? "").trim(); }
      catch { /* A removed target cannot be activated. */ }
      if (isDisplay || action === "display") {
        void open(player, true);
        return;
      }
      void activateControl(player, action).catch(error => tell(player, error.message));
    });
  });

  const disconnectTimers = new Map();
  function waitForReconnect(playerId, mine) {
    if (disconnectTimers.has(playerId)) return;
    const sessionToken = mine.sessionToken;
    const timerId = system.runTimeout(() => {
      disconnectTimers.delete(playerId);
      const live = manager.view(tableId);
      if (live?.sessionToken !== sessionToken) return;
      if (world.getAllPlayers().some(player => player.id === playerId)) return;
      if (game.id === "hope") {
        const seat = live.players.indexOf(playerId);
        const next = manager.systemAct(tableId, "disconnect", seat);
        manager.detach(playerId);
        if (next?.state?.phase === "finished") complete(next);
        else {
          const current = table();
          if (current) { afterAction(current); startTicker(); }
        }
        return;
      }
      finish(live, "参加者が切断したため中止しました。");
    }, 200);
    disconnectTimers.set(playerId, timerId);
  }
  world.afterEvents.playerLeave.subscribe(event => {
    runtimeForms.invalidate([event.playerId]); prompts.forget(event.playerId);
    const mine = manager.forPlayer(event.playerId);
    busy.delete(event.playerId); formTokens.delete(event.playerId); physicalProgress.delete(event.playerId);
    if (!mine || mine.gameId !== tableId) return;
    clearTimer(); clearRegistration(); stopTicker();
    waitForReconnect(event.playerId, mine);
  });

  function resume(joinedPlayerId = null) {
    if (joinedPlayerId) {
      const pending = disconnectTimers.get(joinedPlayerId);
      if (pending !== undefined) system.clearRun(pending);
      disconnectTimers.delete(joinedPlayerId);
    }
    clearTimer();
    clearRegistration();
    stopTicker();
    const current = table();
    if (!current) return;
    for (const id of current.players) {
      if (!world.getAllPlayers().some(player => player.id === id)) waitForReconnect(id, current);
    }
    try { venue?.update(game.id, current); } catch { /* Venue may not be installed. */ }
    if (!current.state) {
      armRegistration(current);
      return;
    }
    if (complete(current)) return;
    if (!allMembersOnline(current)) return;
    startTicker();
    if (!automatic(current)) {
      arm(current);
      void openActor(current);
    }
  }

  system.afterEvents.scriptEventReceive.subscribe(event => {
    if (event.sourceEntity?.typeId !== "minecraft:player") return;
    const menuId = `junket:${game.id}`;
    const menuAlias = `junket:${game.id}menu`;
    if (event.id !== menuId && event.id !== menuAlias) return;
    const player = event.sourceEntity;
    const command = event.message.trim();
    if (!command || command === "menu") {
      system.run(() => { void open(player); });
    } else if (command === "leave") {
      const mine = manager.forPlayer(player.id);
      if (mine?.gameId === tableId) finish(mine, "参加者が退出したため中止しました。");
    } else if (command === "start" && game.registration) {
      system.run(() => {
        try { start(manager.start(tableId)); } catch (error) { tell(player, error.message); }
      });
    }
  });

  async function openReception(player) {
    if (busy.has(player.id)) {
      tell(player, "別の操作画面を処理中です。画面やチャットを閉じて、受付ボタンを再操作してください。");
      return false;
    }
    await open(player, true);
    return true;
  }
  async function resetSession(current, reason = "会場をリセットしました。", { evacuated = false } = {}) {
    runtimeForms.invalidate();
    prompts.clear();
    clearTimer(); clearRegistration(); stopTicker();
    formTokens.clear(); physicalProgress.clear();
    for (const timerId of disconnectTimers.values()) system.clearRun?.(timerId);
    disconnectTimers.clear();
    busy.clear();
    const resetTable = current ? { ...current, players: evacuated ? [] : [...current.players] } : { players: [], state: null };
    await venue?.end?.(game.id, resetTable);
    if (current) notify(current, reason);
  }
  function startRegistration(player) {
    const mine = manager.forPlayer(player.id);
    if (!game.registration || mine?.gameId !== tableId || mine.state) throw new Error("開始できる参加受付がありません。");
    start(manager.start(tableId));
  }
  return { open, openReception, resetSession, table, status, venue, resume,
    ...(game.registration ? { startRegistration } : {}) };
}

export function gameRules(id) {
  return ({
    ace: "4人・100枚から開始。場代とベットを行い、5ダイスの役で勝者総取り。5ゲーム後のチーム合計で判定します。",
    grail: "2人・10ラウンド。両者が聖杯・ポーションを秘密に配置し、交互に選びます。器の中身は持ち越せ、ゲーム操作から自分で飲みます。聖水は半量の毒を相殺し、毒の効果は5秒後に発生。最終ラウンド後は両者が器を飲んで終了します。",
    auction: "4人。1・3がA、2・4がBチーム。1〜4の札を同時公開し、最高値の単独提示が落札。支払い16以上か、先にダイヤ3個で決着。",
    fire: "2人。各員6枚（グー・チョキ・パー×HOT/ICE）を1シリーズに1回ずつ使用。勝者のカード温度がストックとなり、精算時に室温へ加算。6ラウンドで3勝ならシリーズ取得、未達ならカードを再配布。4シリーズ後の30秒待機を経て室温100℃の最終粛清へ。ガラスは-70℃以下で破損し、風ダメージを防ぐ。最終決戦は累積勝利数、同数なら選択時間が短い側の勝ち。",
    pig: "4人・1P/4P対2P/3P。各セット3ラウンド、4種類のカードを1回ずつ伏せて選びます。襲撃はホスト席から時計回り。家は各1回、狼も各1回。対象のない狼は子豚2匹を失い、家を1回襲撃すると労働1。労働6で宮殿完成、子豚0で死亡、対面チーム勝利です。",
    light: "2人。奇数ラウンドは1Pが門番3体、2Pが死者3人を並べ、偶数ラウンドは役割を交代します。3組の判定を合計し、門番を並べた側がハートと松明を獲得。ハート7個で勝利です。松明は点火時に酸素を半減させ、点火できるほど長く燃えます。",
    mine: "2人・20ラウンド。4つの岩を伏せて配置し、技能・瓶の効果に応じて1〜3つを採掘。ツルハシが出ると残った岩を追加採掘できます。1人1本の鍵で自分または相手の清掃・働き者・運命を選択でき、残り時間が十分なら30秒待機も可能。猛毒とヘルインゴット残数などで決着します。",
    hope: "1〜16人・10ラウンド。4×4の16床には5種類の図柄があり、危険図柄の発表後に自分の立っている床で判定します。危険図柄の床が落下し、安全図柄の床は残ります。4〜10ラウンドは安全図柄も配置が変わるため、毎回足元を確認してください。15秒後に開始し、10ラウンド生存した参加者が勝利します。",
    clock: "2人。現在 1:00 から開始し、時+1/-1・分+5/-5で次の時刻を設定します。設定決定後、時計回り/反時計回りで区画を選び、そのターン数ぶん（最大12）確定します。確定区画の物理床が開き、下の水へ落ちた側の負けです。両者同時着水は引き分け、時間切れは相手の勝ちです。",
    walker: "2人・37マス。ウォーカーは1〜5マス移動して停止し、移動数に応じてブレイカーが最大5個の爆弾を設置。その後3カウントの間にウォーカーが避け、移動元の床は崩落します。爆発で水没した側の負け、同時水没は引き分けです。",
    garden: "2人。切替・宝石倍化・開放を選び、装置の宝石を弁の向きへ放出します。自分の宝石箱をちょうど100個にして5秒保持すると勝利。超過分は自分の箱から1個ずつ戻せます。",
    rabbit: "2人。オニが罠を伏せ、脱出側は現在地または直交1〜3マス移動。仲間とともにゴール12・24・36・48・60を目指し、罠・時間切れを避けます。",
  })[id] ?? "";
}

export function bestOfAceTeam(state) {
  if (!state || state.phase !== "finished" || !Number.isInteger(state.winnerTeam)) return null;
  return state.winnerTeam;
}
