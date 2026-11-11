import { decisionKey, decisionSeats, supplementalKeys } from "./decision_contracts.js";
export const GAME_STATE_KEY = "junket:game_state_v1";
const STORAGE_SCHEMA = 1;

const isId = value => typeof value === "string" && value.length > 0 && value.length <= 128;
const isToken = value => Number.isSafeInteger(value) && value > 0;
const MAX_DYNAMIC_PROPERTY_CHARS = 32767;

function migrateStoredState(game, state, playerCount) {
  if (!state || typeof state !== "object" || Array.isArray(state)) return state;
  if (game.id === "lucy") {
    const template = game.create(playerCount);
    const next = { ...state };
    if (!Array.isArray(next.lockDrafts) || next.lockDrafts.length !== 2) {
      next.lockDrafts = template.lockDrafts.map(player =>
        player.map(row => row.map(value => value === true)));
      if (Array.isArray(next.patterns) && next.patterns.length === 2) {
        next.patterns = [next.patterns[1], next.patterns[0]];
      }
      if (Array.isArray(next.progress) && next.progress.length === 2) {
        next.progress = [next.progress[1], next.progress[0]];
      }
    }
    for (const [name, value] of Object.entries(template)) {
      if (!(name in next)) next[name] = Array.isArray(value) ? [...value] : value;
    }
    return next;
  }
  if (game.id === "silence") {
    const template = game.create(playerCount);
    const next = { ...state };
    const validOrder = order => Array.isArray(order) && order.length === 3 &&
      new Set(order).size === 3 && [0, 20, 30].every(value => order.includes(value));
    const validSeat = value => Number.isInteger(value) && value >= 0 && value < playerCount;
    const arranger = validSeat(next.arranger) ? next.arranger : template.arranger;
    const arrangements = [0, 1].map(seat =>
      validOrder(next.arrangements?.[seat]) ? [...next.arrangements[seat]] : null);
    const drafts = [0, 1].map(seat =>
      validOrder(next.arrangeDrafts?.[seat]) ? [...next.arrangeDrafts[seat]]
        : [...template.arrangeDrafts[seat]]);
    const hp = Array.isArray(next.hp) && next.hp.length === playerCount &&
      next.hp.every(value => Number.isFinite(value) && value >= 0)
      ? [...next.hp] : [...template.hp];
    const guard = Array.isArray(next.guard) && next.guard.length === playerCount
      ? next.guard.map(value => value === true) : [...template.guard];
    const playSeat = validSeat(next.playSeat) ? next.playSeat : null;
    const duration = next.last?.duration;
    const last = next.last && typeof next.last === "object" && !Array.isArray(next.last) &&
      [0, 20, 30].includes(duration) ? { ...next.last } : null;
    const playTicks = Number.isInteger(next.playTicks) && next.playTicks >= 0
      ? next.playTicks : template.playTicks;
    const remainingTicks = Number.isInteger(next.remainingTicks)
      ? next.remainingTicks : playTicks;
    const totalSeconds = Number.isInteger(next.totalSeconds) && next.totalSeconds >= 0
      ? next.totalSeconds : template.totalSeconds;
    let phase = typeof next.phase === "string" ? next.phase : "arrange";
    if (!["arrange", "choose", "set", "playback", "return", "finished"].includes(phase)) {
      phase = "arrange";
    }
    if (phase === "choose" && !arrangements[arranger]) phase = "arrange";
    if (["set", "playback", "return"].includes(phase) &&
        (playSeat === null || !last || !arrangements[arranger])) {
      phase = arrangements[arranger] ? "choose" : "arrange";
    }
    if (phase === "playback" && playTicks <= 0) phase = "return";
    const actor = phase === "choose" ? 1 - arranger
      : phase === "finished" ? null
      : ["set", "playback", "return"].includes(phase) ? playSeat
      : arranger;
    for (const [name, value] of Object.entries(template)) {
      if (!(name in next)) {
        next[name] = Array.isArray(value)
          ? value.map(item => Array.isArray(item) ? [...item] : item)
          : value;
      }
    }
    Object.assign(next, {
      phase,
      actor,
      arranger,
      chooser: 1 - arranger,
      arrangements,
      arrangeDrafts: drafts,
      arrangeSelected: phase === "arrange" && Number.isInteger(next.arrangeSelected) &&
        next.arrangeSelected >= 0 && next.arrangeSelected < 3 ? next.arrangeSelected : null,
      hp,
      guard,
      totalSeconds,
      remainingTicks,
      playTicks,
      playSeat: ["set", "playback", "return"].includes(phase) ? playSeat : null,
      last,
      winner: Number.isInteger(next.winner) && next.winner >= 0 && next.winner < playerCount
        ? next.winner : null,
      reason: typeof next.reason === "string" ? next.reason : null,
    });
    return next;
  }
  if (game.id === "grail") {
    const template = game.create(playerCount);
    const next = { ...state };
    const round = Number.isInteger(next.round) && next.round >= 1 && next.round <= 10
      ? next.round : template.round;
    const grailSide = next.grailSide === 0 || next.grailSide === 1
      ? next.grailSide : (round - 1) % 2;
    const cupOwner = next.cupOwner === 0 || next.cupOwner === 1
      ? next.cupOwner : grailSide;
    const potionOwner = 1 - cupOwner;
    const legacyCup = next.arrangements?.[cupOwner];
    const legacyPotion = next.arrangements?.[potionOwner];
    const trueIndex = Number.isInteger(next.racks?.cup?.trueIndex)
      ? next.racks.cup.trueIndex
      : Number.isInteger(legacyCup?.trueIndex) ? legacyCup.trueIndex : null;
    const holyIndex = Number.isInteger(next.racks?.potion?.holyIndex)
      ? next.racks.potion.holyIndex
      : Number.isInteger(legacyPotion?.holyIndex) ? legacyPotion.holyIndex : null;
    const used = values => Array.isArray(values)
      ? values.filter(value => Number.isInteger(value) && value >= 0 && value <= 2)
      : [];
    const pair = values => Array.isArray(values) && values.length === 2
      ? values.map(value => Number.isFinite(value) ? value : 0) : [0, 0];
    Object.assign(next, {
      round,
      grailSide,
      cupOwner,
      potionOwner,
      racks: {
        cup: { trueIndex, used: used(next.racks?.cup?.used) },
        potion: { holyIndex, used: used(next.racks?.potion?.used) },
      },
      exchange: Number.isInteger(next.exchange) && next.exchange >= 0 && next.exchange < 2
        ? next.exchange : 0,
      hp: pair(next.hp), holy: pair(next.holy), poison: pair(next.poison),
      poisonCup: pair(next.poisonCup), holyCup: pair(next.holyCup),
      actor: next.phase === "finished" ? null
        : next.actor === 0 || next.actor === 1 ? next.actor : 0,
      winner: next.winner === 0 || next.winner === 1 ? next.winner : null,
      reason: typeof next.reason === "string" ? next.reason : null,
    });
    if (next.phase === "arrange" && trueIndex !== null && holyIndex !== null) {
      next.phase = "cup";
      next.actor = grailSide;
    }
    if (next.phase === "potion" && !Number.isInteger(next.cupIndex)) next.phase = "cup";
    for (const [name, value] of Object.entries(template)) {
      if (!(name in next)) next[name] = Array.isArray(value) ? [...value] : value;
    }
    return next;
  }
  if (game.id === "light") {
    const template = game.create(playerCount);
    const numberPair = value => Array.isArray(value) && value.length === 2 &&
      value.every(item => Number.isFinite(item) && item >= 0) ? [...value] : null;
    const hearts = numberPair(state.hearts) ?? [...template.hearts];
    const torches = numberPair(state.torches) ?? [...template.torches];
    const oxygen = numberPair(state.oxygen) ?? numberPair(state.fuel) ?? [...template.oxygen];
    const validPermutation = values => Array.isArray(values) && values.length === 3 &&
      values.every(value => value === null ||
        (Number.isInteger(value) && value >= 0 && value < 3)) &&
      new Set(values.filter(value => value !== null)).size ===
        values.filter(value => value !== null).length;
    const validDamageClaimed = Array.isArray(state.damageClaimed) &&
      state.damageClaimed.length === 2 &&
      state.damageClaimed.every(row => Array.isArray(row) &&
        row.length === template.damageClaimed[0].length && row.every(value => typeof value === "boolean"));
    const validPhases = ["intro", "guard_arrange", "tourist_arrange", "result", "finished"];
    const compatible = validPhases.includes(state.phase) &&
      validPermutation(state.guards) && validPermutation(state.tourists) &&
      numberPair(state.oxygen) !== null && numberPair(state.hearts) !== null &&
      numberPair(state.torches) !== null &&
      Array.isArray(state.oxygenLack) && state.oxygenLack.length === 2 &&
      state.oxygenLack.every(value => typeof value === "boolean") &&
      numberPair(state.oxygenRecovery) !== null && numberPair(state.torchBurn) !== null &&
      numberPair(state.damage) !== null && validDamageClaimed &&
      Array.isArray(state.pendingDamage) &&
      Number.isInteger(state.introRemaining) && typeof state.introTorchLit === "boolean" &&
      Number.isInteger(state.placementRemaining) && Number.isInteger(state.resultRemaining);
    if (!compatible) {
      // Version 1 used arrangements/selections/fuel and an arrange/select
      // phase. Those fields do not map one-to-one to the source's alternating
      // guard/tourist placement, so never resume a half-converted round.
      const finished = state.phase === "finished";
      return {
        ...template,
        hearts,
        torches,
        oxygen,
        phase: finished ? "finished" : "intro",
        actor: null,
        winner: finished && (state.winner === 0 || state.winner === 1) ? state.winner : null,
        reason: finished && typeof state.reason === "string" ? state.reason : null,
        introRemaining: finished ? 0 : template.introRemaining,
        placementRemaining: 0,
        resultRemaining: 0,
      };
    }
    const next = { ...template, ...state };
    for (const [name, value] of Object.entries(template)) {
      if (!(name in next)) next[name] = Array.isArray(value) ? [...value] : value;
    }
    return next;
  }
  if (game.id === "rabbit") {
    const template = game.create(playerCount);
    const next = { ...state };
    if (!Array.isArray(next.participants)) {
      // Version 1 stored a single human position and a filtered CPU array.
      // Rebuild the five source participants without inventing extra survivors.
      const legacyCpu = Array.isArray(state.cpu)
        ? state.cpu.filter(value => Number.isInteger(value) && value >= 1 && value <= 60)
        : [];
      const positions = [1, 13, 37, 49];
      next.participants = template.participants.map((participant, index) => {
        if (index === 0) {
          return {
            ...participant,
            position: Number.isInteger(state.escaper) && state.escaper >= 1 && state.escaper <= 60
              ? state.escaper : participant.position,
            haveGuard: false,
          };
        }
        const alive = index - 1 < legacyCpu.length;
        return {
          ...participant, alive,
          position: alive ? legacyCpu[index - 1] : positions[index - 1],
          haveGuard: false,
        };
      });
      next.traps = [];
      next.plannedMoves = [];
      next.dropped = [];
      next.guarded = [];
      next.phase = state.phase === "finished" ? "finished" : "trap";
      next.actor = next.phase === "trap" ? 1 : null;
      next.trapQuota = Math.min(5, next.participants.filter(participant => participant.alive).length);
      next.round = Number.isInteger(state.round) && state.round > 0 ? state.round : 1;
      next.phaseRemaining = next.phase === "finished" ? 0 : template.phaseRemaining;
      next.winnerTeam = Number.isInteger(state.winnerTeam)
        ? state.winnerTeam : Number.isInteger(state.winner) ? state.winner : null;
      next.last = null;
    }
    for (const [name, value] of Object.entries(template)) {
      if (!(name in next)) next[name] = Array.isArray(value) ? [...value] : value;
    }
    return next;
  }
  if (game.id !== "hope") return state;
  const template = game.create(playerCount);
  const next = { ...state };
  for (const [name, value] of Object.entries(template)) {
    if (!(name in next)) next[name] = Array.isArray(value) ? [...value] : value;
  }
  if (!Array.isArray(next.padSymbols) || next.padSymbols.length !== 16) {
    next.padSymbols = [...template.padSymbols];
  }
  if (!Number.isInteger(next.safePad) || next.safePad < 0 || next.safePad >= 16) {
    const index = next.padSymbols.indexOf(next.safe);
    next.safePad = index >= 0 ? index : 0;
  }
  if (Array.isArray(next.choices)) {
    next.choices = next.choices.map(choice => {
      if (!Number.isInteger(choice?.pad)) return null;
      return {
        ...choice,
        symbol: Number.isInteger(choice.symbol)
          ? choice.symbol : next.padSymbols[choice.pad - 1],
      };
    });
  }
  if (!Array.isArray(next.ai) || next.ai.length !== template.ai.length) {
    next.ai = template.ai.map(value => ({ ...value }));
  } else {
    next.ai = next.ai.map((value, index) => ({
      id: Number.isInteger(value?.id) ? value.id : index + 1,
      pad: Number.isInteger(value?.pad) && value.pad >= 1 && value.pad <= template.ai.length
        ? value.pad : index + 1,
      alive: value?.alive !== false,
      loser: value?.loser === true,
    }));
  }
  if (!Number.isInteger(next.aiSeed)) next.aiSeed = template.aiSeed;
  // Older snapshots could persist the round-ten no-out fast-forward before
  // the one-shot check marker. The two fields describe the same source
  // time=5790 decision, so never restore the hold with the check still armed.
  if (next.roundTenNoOut === true) next.roundTenNoOutChecked = true;
  if (next.last) {
    next.last = {
      ...next.last,
      hazards: Array.isArray(next.last.hazards) ? next.last.hazards : [],
      choices: Array.isArray(next.last.choices) ? next.last.choices : [],
      symbols: Array.isArray(next.last.symbols) ? next.last.symbols : [],
      eliminated: Array.isArray(next.last.eliminated) ? next.last.eliminated : [],
      aiLost: Array.isArray(next.last.aiLost) ? next.last.aiLost : [],
    };
  }
  return next;
}

function validateState(game, state, playerCount) {
  if (!state || typeof state !== "object" || Array.isArray(state)) {
    throw new Error("invalid state");
  }
  if (typeof state.phase !== "string" || !state.phase) throw new Error("invalid phase");
  if (state.actor !== undefined && state.actor !== null &&
      (!Number.isInteger(state.actor) || state.actor < 0 || state.actor >= playerCount)) {
    throw new Error("invalid actor");
  }
  const template = game.create(playerCount);
  for (const [name, value] of Object.entries(template)) {
    if (!(name in state)) throw new Error(`missing state field: ${name}`);
    if (Array.isArray(value) && !Array.isArray(state[name])) {
      throw new Error(`invalid state array: ${name}`);
    }
  }
  if (game.id === "hope" && state.playerCount !== playerCount) throw new Error("invalid player count");
  // Some games shape the returned view, so this also checks fields that are
  // consumed by the next form or timer rather than only top-level JSON syntax.
  game.view(state);
  return state;
}

// Sessions are server-only dynamic-property data. Secret arrangements stay in
// state and are never placed in a player-facing view.
export class GameManager {
  constructor(games, { world = null, system = null, storageKey = GAME_STATE_KEY } = {}) {
    this.games = new Map(games.map(game => [game.id, game]));
    this.tables = new Map();
    this.members = new Map();
    this.sequence = 0;
    this.maxTables = 32;
    this.recoveringGames = new Set();
    this.world = world;
    this.system = system;
    this.storageKey = storageKey;
  }

  persist() {
    if (!this.world?.setDynamicProperty) return;
    try {
      const payload = JSON.stringify(this.serialize());
      if (payload.length > MAX_DYNAMIC_PROPERTY_CHARS) {
        throw new Error(`session payload is too large: ${payload.length}/${MAX_DYNAMIC_PROPERTY_CHARS}`);
      }
      this.world.setDynamicProperty(this.storageKey, payload);
    } catch (error) {
      console.warn(`[JUNKET] session persistence failed: ${error?.message ?? error}`);
    }
  }

  serialize() {
    return {
      schema: STORAGE_SCHEMA,
      sequence: this.sequence,
      tables: [...this.tables.values()].map(table => ({
        id: table.id,
        gameId: table.game.id,
        players: [...table.players],
        token: table.token,
        sessionToken: table.sessionToken,
        decisionSerials: table.decisionSerials,
        state: table.state,
      })),
    };
  }

  restore(raw = this.world?.getDynamicProperty(this.storageKey)) {
    this.tables = new Map();
    this.members = new Map();
    this.sequence = 0;
    if (raw === undefined || raw === null || raw === "") return { restored: 0, skipped: 0 };
    let payload = raw;
    try {
      if (typeof raw === "string") payload = JSON.parse(raw);
      if (!payload || payload.schema !== STORAGE_SCHEMA || !Array.isArray(payload.tables)) {
        throw new Error("unsupported session payload");
      }
      let skipped = 0;
      for (const saved of payload.tables.slice(0, this.maxTables)) {
        try {
          const game = this.games.get(saved?.gameId);
          const maximum = game?.maxPlayers ?? game?.seats;
          if (!game || !isId(saved.id) || !Array.isArray(saved.players) ||
              saved.players.length > maximum || !saved.players.every(isId) ||
              new Set(saved.players).size !== saved.players.length || !isToken(saved.token) ||
              this.tables.has(saved.id)) {
            throw new Error("invalid table");
          }
          const duplicate = saved.players.some(playerId => this.members.has(playerId));
          if (duplicate) throw new Error("duplicate membership");
          let state = null;
          if (saved.state !== null) {
            if (game.id === "grail" && !Array.isArray(saved.state.pendingDamage)) {
              console.warn("[JUNKET] Grail legacy match retired: old automatic drinking cannot be migrated into voluntary vessels. Join again at the game panel.");
              throw new Error("incompatible legacy Grail vessels");
            }
            if (game.id === "mine" && saved.state.rulesVersion !== 2) {
              console.warn("[JUNKET] Mine legacy match retired: old fixed-two-rock state has no source key/skill/opened history. Join again at the game panel.");
              throw new Error("incompatible legacy Mine history");
            }
            if (game.id === "auction" && saved.state.rulesVersion !== 2) {
              console.warn("[JUNKET] Auction legacy match retired: old single-pulse payment has no original electrical damage history. Join again at the game panel.");
              throw new Error("incompatible legacy Auction payment history");
            }
            const minimum = game.minPlayers ?? game.seats;
            if (saved.players.length < minimum) throw new Error("too few players for state");
            state = validateState(game, migrateStoredState(game, saved.state, saved.players.length),
              saved.players.length);
          }
          const table = {
            id: saved.id, game, players: [...saved.players], state, token: saved.token,
            sessionToken: isToken(saved.sessionToken) ? saved.sessionToken : saved.token,
            decisionSerials: saved.players.map((_, seat) => Number.isSafeInteger(saved.decisionSerials?.[seat])
              ? saved.decisionSerials[seat] : 0),
          };
          this.tables.set(table.id, table);
          for (const playerId of table.players) this.members.set(playerId, table.id);
          this.sequence = Math.max(this.sequence, table.token);
        } catch {
          skipped++;
        }
      }
      const savedSequence = Number(payload.sequence);
      if (Number.isSafeInteger(savedSequence) && savedSequence >= 0) {
        this.sequence = Math.max(this.sequence, savedSequence);
      }
      return { restored: this.tables.size, skipped };
    } catch (error) {
      this.tables.clear();
      this.members.clear();
      console.warn(`[JUNKET] session restore failed: ${error?.message ?? error}`);
      return { restored: 0, skipped: 0 };
    }
  }

  join(tableId, gameId, playerId) {
    if (this.maintenance) throw new Error("会場の自動検証中です。終了後に参加してください。");
    if (this.recoveringGames.has(gameId)) throw new Error("会場の復旧中です。終了後に参加してください。");
    const game = this.games.get(gameId);
    if (!game || !playerId || !tableId) throw new Error("参加先が不正です。");
    if (this.members.has(playerId)) throw new Error("既に参加しています。");
    let table = this.tables.get(tableId);
    const maximum = game.maxPlayers ?? game.seats;
    if (table && (table.game !== game || table.state || table.players.length >= maximum)) {
      throw new Error("この卓には参加できません。");
    }
    if (!table) {
      if (this.tables.size >= this.maxTables) throw new Error("卓の上限です。");
      table = { id: tableId, game, players: [], state: null, token: ++this.sequence };
      table.sessionToken = table.token;
      table.decisionSerials = [];
      this.tables.set(tableId, table);
    }
    table.players.push(playerId);
    (table.decisionSerials ??= []).push(0);
    this.members.set(playerId, tableId);
    if (!game.registration && table.players.length === maximum) {
      table.state = game.create(table.players.length);
    }
    table.token = ++this.sequence;
    this.persist();
    return this.view(tableId);
  }

  start(tableId) {
    const table = this.tables.get(tableId);
    if (table && this.recoveringGames.has(table.game.id)) throw new Error("会場の復旧中です。");
    if (!table || table.state) return this.view(tableId);
    const minimum = table.game.minPlayers ?? table.game.seats;
    const maximum = table.game.maxPlayers ?? table.game.seats;
    if (table.players.length < minimum || table.players.length > maximum) {
      throw new Error(`開始人数は${minimum}〜${maximum}人です。`);
    }
    table.state = table.game.create(table.players.length);
    table.token = ++this.sequence;
    this.persist();
    return this.view(tableId);
  }

  view(tableId, playerId = null) {
    const table = this.tables.get(tableId);
    if (!table) return null;
    const seat = table.players.indexOf(playerId);
    const state = table.state ? (seat >= 0 && table.game.viewForPlayer
      ? table.game.viewForPlayer(table.state, seat) : table.game.view(table.state)) : null;
    return { id: table.id, gameId: table.game.id, title: table.game.title,
      players: [...table.players], token: table.token,
      sessionToken: table.sessionToken ?? table.token,
      decisionSerials: [...(table.decisionSerials ?? table.players.map(() => 0))],
      decisionSeats: table.state ? decisionSeats(table.game.id, table.state) : [],
      ...(seat >= 0 && state ? { decisionKey: decisionKey(table.game.id, table.state, seat),
        supplementalKeys: supplementalKeys(table.game.id, table.state, seat) } : {}), state };
  }

  forPlayer(playerId) { return this.view(this.members.get(playerId), playerId); }

  // Two players may answer the same simultaneous-choice window in either
  // order. Rebase only their unchanged private decision, never a new round or
  // another match. Per-seat serials reject repeated/replayed submissions.
  actDecision(playerId, previous, action, value) {
    const live = this.forPlayer(playerId);
    const seat = live?.players.indexOf(playerId) ?? -1;
    const supplemental = Boolean(live?.supplementalKeys?.[action]) &&
      previous?.supplementalKeys?.[action] === live.supplementalKeys[action];
    const primary = live?.decisionSeats.includes(seat) && previous?.decisionKey === live?.decisionKey;
    if (!live?.state || !previous || seat < 0 || previous.id !== live.id ||
        previous.gameId !== live.gameId || previous.sessionToken !== live.sessionToken ||
        previous.decisionSerials?.[seat] !== live.decisionSerials[seat] ||
        !(supplemental || primary)) {
      throw new Error("選択できる状況が変わりました。ゲーム操作パネルから開き直してください。");
    }
    return this.act(playerId, live.token, action, value);
  }

  act(playerId, token, action, value) {
    const table = this.tables.get(this.members.get(playerId));
    if (table && this.recoveringGames.has(table.game.id)) throw new Error("会場の復旧中です。");
    if (!table?.state || table.token !== token) throw new Error("画面が古くなりました。受付から開き直してください。");
    table.state = table.game.advance(table.state, table.players.indexOf(playerId), action, value);
    const seat = table.players.indexOf(playerId);
    table.decisionSerials ??= table.players.map(() => 0);
    table.decisionSerials[seat]++;
    table.token = ++this.sequence;
    this.persist();
    return this.view(table.id);
  }

  close(tableId) {
    const result = this.view(tableId);
    if (!result) return null;
    this.tables.delete(tableId);
    for (const id of result.players) this.members.delete(id);
    this.persist();
    return result;
  }

  systemAct(tableId, action, value, { persist = true } = {}) {
    const table = this.tables.get(tableId);
    if (table && this.recoveringGames.has(table.game.id)) return null;
    if (!table?.state || !table.game.systemAdvance) return null;
    const before = table.state;
    table.state = table.game.systemAdvance(before, action, value);
    // Health-only events do not invalidate a pending choice form.
    if (before.phase !== table.state.phase || before.actor !== table.state.actor) table.token = ++this.sequence;
    if (persist) this.persist();
    return this.view(tableId);
  }

  leave(playerId) { return this.close(this.members.get(playerId)); }

  setVenueRecovery(gameId, active) {
    if (active) {
      this.recoveringGames.add(gameId);
      for (const table of this.tables.values()) if (table.game.id === gameId) table.token = ++this.sequence;
      this.persist();
    } else this.recoveringGames.delete(gameId);
  }

  detach(playerId) {
    const tableId = this.members.get(playerId);
    const table = this.tables.get(tableId);
    if (!table) return null;
    const view = this.view(tableId);
    this.members.delete(playerId);
    table.token = ++this.sequence;
    this.persist();
    return view;
  }
}
