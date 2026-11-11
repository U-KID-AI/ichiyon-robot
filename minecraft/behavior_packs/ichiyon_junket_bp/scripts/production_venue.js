import { VENUE_CONFIG } from "./junket_venue_generated.js";
import { originalVenue } from "./original_venue.js";
import { setVisualFrame } from "./special_venue.js";
import { getSharedLoader } from "./shared_loader.js";
import { insideFixtureScope, sameFixtureActor } from "./fixture_scope.js";
import { ensureFixedFixtureHealth } from "./fixture_health.js";
import { anchorVenueFixtures } from "./fixture_anchor.js";
import { installFixtureActorTracking, markCreatedFixture, observeExistingFixture,
  preferredFixture, waitForFixtureActors } from "./fixture_reconciliation.js";
import { ensureServiceControls } from "./service_control_install.js";
import { applyFixturePresentation, applyVenuePresentation } from "./venue_presentation.js";
import {
  FIRE_PROP_FRAME, PIG_PROP_FRAME, fireVenuePresentation, grailVenuePresentation,
  pigVenuePresentation,
} from "./strategy_games.js";
import {
  gardenVenuePresentation, HOPE_AI_TRADERS, HOPE_SYMBOL_NAMES,
  RABBIT_CPU_NAMES, hopeAiMotionActive,
  clockNeedleAngles, clockNeedleContacts, CLOCK_NEEDLE_RECOVERY_TICKS,
} from "./board_games.js";

const DIMENSION = "minecraft:overworld";
const INSTALL_KEY = VENUE_CONFIG.VENUE_INSTALL_KEY;
const SCHEMA = VENUE_CONFIG.VENUE_SCHEMA;
const PER_VENUE_PREFIX = `${INSTALL_KEY}:`;
const REPAIR_VERSION = 11;
const SAFE_RETURN = { x: -44.5, y: 75, z: 210.5 };
const WALKER_COLORS = Object.freeze({
  red: "minecraft:red_concrete",
  orange: "minecraft:orange_concrete",
  yellow: "minecraft:yellow_concrete",
  lime: "minecraft:lime_concrete",
  green: "minecraft:green_concrete",
  cyan: "minecraft:cyan_concrete",
  light_blue: "minecraft:light_blue_concrete",
  blue: "minecraft:blue_concrete",
  purple: "minecraft:purple_concrete",
  magenta: "minecraft:magenta_concrete",
  pink: "minecraft:pink_concrete",
});
export const WALKER_LABEL_INITIAL = Object.freeze([
  0, 0,
  1, 1, 1,
  2, 2, 2, 2,
  3, 3, 3,
  4, 4, 4, 4,
  5, 5, 5, 5, 5,
  6, 6, 6, 6,
  7, 7, 7,
  8, 8, 8, 8,
  9, 9, 9,
  10, 10,
]);
export const WALKER_LABEL_DEAD = Object.freeze([
  11, 11,
  12, 12, 12,
  13, 13, 13, 13,
  14, 14, 14,
  15, 15, 15, 15,
  16, 16, 16, 16, 16,
  17, 17, 17, 17,
  18, 18, 18,
  19, 19, 19, 19,
  20, 20, 20,
  21, 21,
]);
const WALKER_LABEL_TYPE = "junket:walker_label";
const WALKER_BOMB_TYPE = "junket:walker_bomb";
const WALKER_LABEL_TAG = "junket:walker:floor_label";
const WALKER_BOMB_TAG = "junket:walker:bomb";
const WALKER_AUXILIARY_TAG = "junket:walker:auxiliary";
const WALKER_LABEL_Y_OFFSET = 1.04;
const HOPE_PAD_COLORS = Object.freeze({
  1: "minecraft:red_wool", 2: "minecraft:yellow_wool",
  3: "minecraft:lime_wool", 4: "minecraft:light_blue_wool",
  5: "minecraft:magenta_wool",
});
const SPECIAL_KEYS = {
  sparrow: "junket:venue_v1",
  temperance: "junket:temperance_venue_v1",
  lucy: "junket:lucy_venue_v1",
  silence: "junket:silence_venue_v1",
  jinn: "junket:jinn_venue_v1",
};
const SPECIAL_RECEPTIONS = {
  sparrow: "junket:reception",
  temperance: "junket:temperance_reception",
  lucy: "junket:lucy_reception",
  silence: "junket:silence_reception",
  jinn: "junket:jinn_reception",
};

const point = value => ({ x: value.x, y: value.y, z: value.z });
const air = block => block?.isAir === true || block?.typeId === "minecraft:air";
const expectedBlock = value => typeof value === "string"
  ? { typeId: value, states: {} }
  : { typeId: value?.type ?? value?.typeId, states: value?.states ?? {} };
const stateMatches = (block, states) => {
  if (!states || Object.keys(states).length === 0) return true;
  const permutation = block?.permutation;
  if (!permutation || typeof permutation.getState !== "function") return false;
  return Object.entries(states).every(([name, value]) => {
    try { return permutation.getState(name) === value; } catch { return false; }
  });
};
const atKey = (venue, cell) => {
  const x = venue.origin[0] + cell[0];
  const y = venue.origin[1] + cell[1];
  const z = venue.origin[2] + cell[2];
  return { x, y, z, key: `${x},${y},${z}` };
};

function parseRuns(layout, size) {
  if (!layout || layout.order !== "x,y,z" || !Array.isArray(layout.palette) || typeof layout.runs !== "string") {
    throw new Error("会場の完全レイアウトデータがありません。");
  }
  const runs = layout.runs.split(";").filter(Boolean).map(value => {
    const [paletteIndex, count] = value.split(",").map(Number);
    if (!Number.isInteger(paletteIndex) || !Number.isInteger(count) || count < 1 ||
        paletteIndex < 0 || paletteIndex >= layout.palette.length) {
      throw new Error("会場レイアウトのRLEデータが不正です。");
    }
    return { paletteIndex, count };
  });
  const cells = runs.reduce((total, run) => total + run.count, 0);
  if (cells !== layout.cells) throw new Error(`会場レイアウトのセル数が不一致です: ${cells}/${layout.cells}`);
  const expectedCells = size[0] * size[1] * size[2];
  if (layout.cells !== expectedCells) {
    throw new Error(`会場レイアウトが会場寸法と一致しません: ${layout.cells}/${expectedCells}`);
  }
  return runs;
}

export function createProductionVenues({ world, system, venues = null }) {
  installFixtureActorTracking(world);
  const sharedLoader = getSharedLoader(world, system);
  const definitions = venues ?? VENUE_CONFIG.VENUES.map(entry => originalVenue(entry.id) ?? entry);
  const entries = new Map(definitions.map(entry => [entry.id, entry]));
  const layouts = new Map(definitions.filter(entry => entry.installation !== "native-preinstalled").map(entry =>
    [entry.id, parseRuns(entry.layout, entry.size)]));
  const activeRepairs = new Map();
  const loadedActors = new Set();
  let repairQueue = Promise.resolve();
  let specialVenues = {};
  let installPromise = null;
  let lastReport = null;
  let gardenCurrent = null;
  let hopeCurrent = null;
  let hopeTeleportedRound = -1;
  let hopeAiSyncKey = "";
  const hopeAiMotion = new Map();
  let hopeMotionRound = null;
  let hopeMotionPhase = null;
  let hopeMotionTick = 0;
  let hopeFreshGameReset = false;
  let hopeFinishedCleanupKey = "";
  const hopeEliminated = new Set();
  const hopeWarped = new Set();
  const hopeFinishedCleaned = new Set();
  const HOPE_OBSERVER_TAG = "junket:hope:observer";
  const HOPE_JUDGE_TYPE = "junket:hope_judge";
  const HOPE_APPARATUS_BROKEN_KEY = "junket:hope:apparatus:broken";
  const HOPE_DISPLAY_FRAME = Object.freeze({ normal: 0, broken: 1 });
  const HOPE_APPARATUS_ROLES = new Set([
    "hope_tv_1", "hope_tv_2", "hope_tv_3", "hope_tv_4", "hope_tv_5", "hope_tv_6",
    "hope_code", "hope_code_display",
  ]);
  let walkerCurrent = null;
  let walkerSyncKey = "";
  let walkerSyncDirty = true;
  let walkerPresentationKey = "";
  let walkerPresentationTick = 0;
  let rabbitCurrent = null;
  let rabbitSyncKey = "";
  let rabbitHumanKey = "";
  let rabbitOniKey = "";
  let rabbitLastPhase = null;
  let rabbitLastResultOpen = false;
  const CLOCK_NEEDLE_RECOVERY_KEY = "junket:clock:needle_recovery";
  // Each participant remains tracked for the whole match. "remaining" is the
  // source 10-second jump-strength recovery timer, while "cleanup" records the
  // end.mcfunction cleanup that must still run when the player reconnects.
  const clockNeedleRecovery = new Map();
  const clockNeedleHits = new Map();
  const clockWeaknessApplied = new Map();
  let clockNeedleRecoveryDirty = false;
  let clockNeedleNow = -1;
  try {
    const stored = world.getDynamicProperty?.(CLOCK_NEEDLE_RECOVERY_KEY);
    const entries = typeof stored === "string" ? JSON.parse(stored) : [];
    if (Array.isArray(entries)) {
      for (const entry of entries) {
        if (typeof entry?.playerId !== "string" || !Number.isFinite(entry.remaining)) continue;
        clockNeedleRecovery.set(entry.playerId, {
          remaining: Math.max(0, Math.floor(entry.remaining)),
          affected: entry.affected === true,
          cleanup: entry.cleanup === true,
        });
      }
    }
  } catch { /* A corrupt recovery record must not prevent venue startup. */ }
  const RABBIT_FLOOR_FRAME = Object.freeze({
    normal: 0, trap: 1, guarded: 2, falling: 3,
  });
  const RABBIT_FALL_PITCH = 61.9275;

  const entry = id => {
    const value = entries.get(id);
    if (!value) throw new Error(`unknown JUNKET venue: ${id}`);
    return value;
  };
  const dimension = () => world.getDimension(DIMENSION);
  const originName = id => `${PER_VENUE_PREFIX}${id}`;
  const managedTag = id => `junket:${id}:managed`;
  const roleTag = (id, role) => `junket:${id}:fixture:${role}`;
  const isHopeApparatus = fixture => fixture?.tags?.includes("junket:hope:apparatus") === true;
  const hopeApparatusBroken = () => world.getDynamicProperty(HOPE_APPARATUS_BROKEN_KEY) === true;
  function setHopeApparatusBroken(broken) {
    world.setDynamicProperty(HOPE_APPARATUS_BROKEN_KEY, broken === true);
  }
  function expectedFixtures(id) {
    const fixtures = entry(id).fixtures ?? [];
    if (id !== "hope" || !hopeApparatusBroken()) return fixtures;
    return fixtures.filter(fixture =>
      !isHopeApparatus(fixture) || fixture.role === "hope_code_display");
  }
  const config = id => {
    const venue = entry(id);
    return {
      dimension: DIMENSION,
      origin: point({
        x: venue.origin[0],
        y: venue.origin[1],
        z: venue.origin[2],
      }),
      backup: null,
      destination: point(venue.destination),
      returnPoint: point(venue.return_point ?? venue.destination),
    };
  };
  const tell = (player, message) => {
    try { player?.sendMessage(`[JUNKET] ${message}`); } catch { /* Disconnected. */ }
  };
  const notifyAll = message => {
    for (const player of world.getAllPlayers?.() ?? []) tell(player, message);
  };
  const waitTicks = ticks => new Promise(resolve => system.runTimeout(resolve, ticks));
  const blockAt = (d, x, y, z) => {
    try { return d.getBlock({ x, y, z }); } catch { return undefined; }
  };
  const runFixed = (d, command) => {
    const result = d.runCommand(command);
    if ((result?.successCount ?? 0) < 1) throw new Error(`JUNKET command failed: ${command}`);
  };
  const seatPoint = seat => Array.isArray(seat)
    ? { x: seat[0], y: seat[1], z: seat[2] }
    : seat?.x !== undefined && seat?.y !== undefined && seat?.z !== undefined ? seat : null;
  function gardenFloorCells(area) {
    const venue = entry("garden");
    const seat = seatPoint(venue.seats?.[area.seat]);
    if (!seat) throw new Error(`Gardenの${area.seat + 1}P席がありません。`);
    const centerX = Math.floor(seat.x);
    const floorY = Math.floor(seat.y) - 1;
    const centerZ = Math.floor(seat.z);
    const radius = Math.max(0, Math.floor(area.radius ?? 1));
    const cells = [];
    for (let dx = -radius; dx <= radius; dx++) {
      for (let dz = -radius; dz <= radius; dz++) {
        cells.push({ x: centerX + dx, y: floorY, z: centerZ + dz });
      }
    }
    return cells;
  }
  function syncGardenDropFloors(current) {
    const venue = entry("garden");
    const state = current?.state;
    const active = state?.phase === "result" ||
      (state?.phase === "drop" && state.dropRemaining === 0);
    const dimensionValue = dimension();
    for (const area of venue.drop_areas ?? []) {
      const open = active && (state.loser === null || state.loser === area.seat);
      const expected = open ? "minecraft:air" : area.floor;
      for (const cell of gardenFloorCells(area)) {
        const block = blockAt(dimensionValue, cell.x, cell.y, cell.z);
        if (!block) throw new Error(`Gardenの床チャンクを読み込めません: ${cell.x},${cell.y},${cell.z}`);
        if (block.typeId === expected) continue;
        block.setType(expected);
      }
    }
  }
  function pollGardenLanding(current) {
    if (current?.state?.phase !== "drop" || current.state.dropRemaining > 0) return null;
    syncGardenDropFloors(current);
    const venue = entry("garden");
    const players = world.getAllPlayers?.() ?? [];
    for (let seat = 0; seat < 2; seat++) {
      if (current.state.loser !== null && current.state.loser !== seat) continue;
      const area = (venue.drop_areas ?? []).find(value => value.seat === seat);
      if (!area) continue;
      const playerId = current.players?.[seat];
      const player = players.find(candidate => candidate.id === playerId);
      if (!player || player.dimension?.id !== DIMENSION) continue;
      const position = seatPoint(venue.seats?.[seat]);
      if (!position) continue;
      const centerX = Math.floor(position.x);
      const centerZ = Math.floor(position.z);
      const x = Math.floor(player.location.x);
      const z = Math.floor(player.location.z);
      if (Math.abs(x - centerX) > (area.radius ?? 1) ||
          Math.abs(z - centerZ) > (area.radius ?? 1)) continue;
      const below = blockAt(dimension(), x, Math.floor(player.location.y - 0.01), z);
      if (below?.typeId === area.detect) return seat;
    }
    return null;
  }
  function clockDropCells(area) {
    if (!Array.isArray(area.cells)) throw new Error(`Clock区画${area.sector}の物理セルがありません。`);
    return area.cells.map(cell => Array.isArray(cell)
      ? { x: cell[0], y: cell[1], z: cell[2] }
      : { x: cell.x, y: cell.y, z: cell.z });
  }
  function syncClockDropFloors(current) {
    const venue = entry("clock");
    const state = current?.state;
    const selected = new Set(state?.selected ?? []);
    const open = state?.floorOpen === true && !(state?.landed?.length);
    const d = dimension();
    for (const area of venue.drop_areas ?? []) {
      const shouldOpen = open && selected.has(area.sector);
      const expected = shouldOpen ? "minecraft:air" : area.floor;
      for (const cell of clockDropCells(area)) {
        const block = blockAt(d, cell.x, cell.y, cell.z);
        if (!block) throw new Error(`Clockの床チャンクを読み込めません: ${cell.x},${cell.y},${cell.z}`);
        if (block.typeId !== expected) block.setType(expected);
      }
    }
  }
  function pollClockLanding(current) {
    if (!current?.state || !["drop", "result"].includes(current.state.phase)) return [];
    syncClockDropFloors(current);
    const venue = entry("clock");
    const selected = new Set(current.state.selected ?? []);
    const cells = new Set();
    for (const area of venue.drop_areas ?? []) {
      if (!selected.has(area.sector)) continue;
      for (const cell of clockDropCells(area)) cells.add(`${cell.x},${cell.z}`);
    }
    if (!cells.size) return [];
    const players = world.getAllPlayers?.() ?? [];
    const landed = [];
    for (let seat = 0; seat < 2; seat++) {
      const playerId = current.players?.[seat];
      const player = players.find(candidate => candidate.id === playerId);
      if (!player || player.dimension?.id !== DIMENSION) continue;
      const x = Math.floor(player.location.x);
      const y = Math.floor(player.location.y + 0.01);
      const z = Math.floor(player.location.z);
      if (!cells.has(`${x},${z}`)) continue;
      const inWater = [y, y - 1, y + 1]
        .map(level => blockAt(dimension(), x, level, z)?.typeId)
        .some(typeId => typeId === "minecraft:water" || typeId === "minecraft:flowing_water");
      if (inWater) landed.push(seat);
    }
    return landed;
  }

  function persistClockNeedleRecovery() {
    if (!clockNeedleRecoveryDirty) return;
    clockNeedleRecoveryDirty = false;
    try {
      world.setDynamicProperty?.(CLOCK_NEEDLE_RECOVERY_KEY, JSON.stringify(
        [...clockNeedleRecovery].map(([playerId, record]) => ({
          playerId,
          remaining: record.remaining,
          affected: record.affected,
          cleanup: record.cleanup,
        }))));
    } catch {
      // Keep the in-memory value and retry on the next venue tick.
      clockNeedleRecoveryDirty = true;
    }
  }

  function applyClockNeedle(player) {
    // game:clock/sting/elec-* uses 0.1 damage, Slowness 10/10,
    // Darkness 10/10, jump_strength 0 and a yellow dust burst. Bedrock has no
    // colour parameter for dust, so the bundled yellow sparkler is the
    // closest vanilla particle.
    try {
      player.addEffect?.("slowness", 10, { amplifier: 10, showParticles: true });
      player.addEffect?.("darkness", 10, { amplifier: 10, showParticles: false });
    } catch { /* The participant may disconnect during the sweep. */ }
    try {
      player.runCommand?.("attribute @s minecraft:jump_strength base set 0");
    } catch { /* Attribute commands are unavailable on old engine builds. */ }
    try {
      player.applyDamage?.(0.1, { cause: "entityAttack" });
    } catch { /* The source hit is intentionally tiny. */ }
    try {
      player.playSound?.("entity.firework_rocket.blast");
    } catch { /* Optional sound. */ }
    try {
      dimension().spawnParticle?.("minecraft:sparkler_emitter", {
        x: player.location.x,
        y: player.location.y + 1,
        z: player.location.z,
      });
    } catch { /* Particles are best effort while a chunk reloads. */ }
  }

  function restoreClockNeedle(player, clearStatus = false) {
    try {
      player.runCommand?.("attribute @s minecraft:jump_strength base set 0.4");
    } catch { /* Attribute commands are unavailable on old engine builds. */ }
    if (!clearStatus) return;
    try {
      player.removeEffect?.("slowness");
      player.removeEffect?.("darkness");
      player.removeEffect?.("weakness");
      player.removeEffect?.("invisibility");
    } catch { /* Disconnected participant. */ }
  }

  function renderClockNeedle(state) {
    const angles = clockNeedleAngles(state);
    if (!angles.rotating) return;
    const venue = entry("clock");
    const geometry = venue.clock_geometry;
    if (!geometry) return;
    const d = dimension();
    const points = [];
    const addHand = (angle, reach) => {
      for (let step = 0; step < 8; step++) {
        const distance = geometry.min_radius +
          (reach - geometry.min_radius) * step / 7;
        points.push({
          x: geometry.center_x + Math.sin(angle) * distance,
          y: geometry.center_y,
          z: geometry.center_z - Math.cos(angle) * distance,
        });
      }
    };
    addHand(angles.hour, geometry.hour_reach);
    addHand(angles.minute, geometry.minute_reach);
    for (const point of points) {
      try {
        d.spawnParticle?.("minecraft:sparkler_emitter", point);
      } catch { /* Chunk not loaded yet. */ }
    }
    if (Number(state.moveRemaining) % 10 === 0) {
      for (const player of world.getAllPlayers?.() ?? []) {
        try {
          player.playSound?.("block.piston.extend", { pitch: 2, volume: 0.7 });
        } catch { /* Optional sound. */ }
      }
    }
  }

  function applyClockMatchStatus(player) {
    // game:clock/start.mcfunction grants Weakness infinite 10 to every player
    // in the venue. Bedrock's amplifier is zero-based, so level 10 is 9.
    try {
      player.addEffect?.("weakness", 1000000, { amplifier: 9, showParticles: true });
    } catch { /* The participant may disconnect during the opening tick. */ }
  }

  function syncClockNeedle(current) {
    const state = current?.state;
    const online = world.getAllPlayers?.() ?? [];
    const nowTick = Number(system.currentTick);
    const advanceRecovery = !Number.isFinite(nowTick) || nowTick !== clockNeedleNow;
    if (Number.isFinite(nowTick)) clockNeedleNow = nowTick;

    if (!state || state.phase === "finished") {
      const participants = new Set(current?.players ?? []);
      for (const playerId of participants) {
        if (typeof playerId !== "string" || clockNeedleRecovery.has(playerId)) continue;
        clockNeedleRecovery.set(playerId, {
          remaining: 0, affected: false, cleanup: true,
        });
        clockNeedleRecoveryDirty = true;
      }
      for (const [playerId, record] of [...clockNeedleRecovery]) {
        record.cleanup = true;
        clockNeedleRecoveryDirty = true;
        const player = online.find(candidate => candidate.id === playerId);
        if (!player) continue;
        restoreClockNeedle(player, true);
        clockNeedleRecovery.delete(playerId);
        clockNeedleRecoveryDirty = true;
      }
      // Even if a recovery record was lost, end.mcfunction still clears every
      // participant. This covers an upgrade from an older installed schema.
      for (const playerId of participants) {
        const player = online.find(candidate => candidate.id === playerId);
        if (player) restoreClockNeedle(player, true);
      }
      clockWeaknessApplied.clear();
      clockNeedleHits.clear();
      persistClockNeedleRecovery();
      return;
    }

    for (const playerId of current?.players ?? []) {
      if (typeof playerId !== "string" || clockNeedleRecovery.has(playerId)) continue;
      clockNeedleRecovery.set(playerId, {
        remaining: 0, affected: false, cleanup: false,
      });
      clockNeedleRecoveryDirty = true;
    }
    const matchToken = String(current?.token ?? "active");
    for (const playerId of current?.players ?? []) {
      const player = online.find(candidate => candidate.id === playerId);
      if (!player) continue;
      if (clockWeaknessApplied.get(playerId) !== matchToken) {
        applyClockMatchStatus(player);
        clockWeaknessApplied.set(playerId, matchToken);
      }
    }
    for (const [playerId, record] of [...clockNeedleRecovery]) {
      if (!record.affected) continue;
      const player = online.find(candidate => candidate.id === playerId);
      if (!player) continue;
      if (advanceRecovery && record.remaining > 0) {
        record.remaining = Math.max(0, record.remaining - 1);
        clockNeedleRecoveryDirty = true;
      }
      if (record.remaining <= 0) {
        restoreClockNeedle(player, false);
        record.affected = false;
        clockNeedleRecoveryDirty = true;
      }
    }
    persistClockNeedleRecovery();
    if (state.phase !== "move") {
      clockNeedleHits.clear();
      return;
    }
    renderClockNeedle(state);
    const geometry = entry("clock").clock_geometry;
    if (!geometry) throw new Error("Clockの針接触データがありません。");
    for (let seat = 0; seat < 2; seat++) {
      const playerId = current.players?.[seat];
      const player = online.find(candidate => candidate.id === playerId);
      if (!player || player.dimension?.id !== DIMENSION) continue;
      const hits = clockNeedleContacts(state, player.location, geometry);
      if (!hits.length) continue;
      // The source sting function recurses along the moving hand. A player can
      // therefore be hit again on a later sweep tick, not only once per turn.
      const sweepTick = Number.isFinite(nowTick) ? nowTick : Number(state.moveRemaining);
      const key = `${state.turn}:${sweepTick}:${hits.join("+")}`;
      if (clockNeedleHits.get(playerId) === key) continue;
      clockNeedleHits.set(playerId, key);
      applyClockNeedle(player);
      const record = clockNeedleRecovery.get(playerId) ?? {
        remaining: 0, affected: false, cleanup: false,
      };
      record.remaining = CLOCK_NEEDLE_RECOVERY_TICKS;
      record.affected = true;
      record.cleanup = false;
      clockNeedleRecovery.set(playerId, record);
      clockNeedleRecoveryDirty = true;
    }
    persistClockNeedleRecovery();
  }

  function resumeClockNeedle(playerId = null) {
    if (!clockNeedleRecovery.size) return;
    const online = world.getAllPlayers?.() ?? [];
    for (const [id, record] of [...clockNeedleRecovery]) {
      if (playerId && id !== playerId) continue;
      const player = online.find(candidate => candidate.id === id);
      if (!player) continue;
      if (record.cleanup) {
        restoreClockNeedle(player, true);
        clockNeedleRecovery.delete(id);
        clockWeaknessApplied.delete(id);
        clockNeedleRecoveryDirty = true;
      } else if (record.affected && record.remaining <= 0) {
        // A player who reconnects after the 10-second recovery still needs the
        // jump strength restored from the persisted source hit.
        restoreClockNeedle(player, false);
        record.affected = false;
        clockNeedleRecoveryDirty = true;
      }
    }
    persistClockNeedleRecovery();
  }

  function pollHopePads(current) {
    if (!current?.state || !["choose", "reveal"].includes(current.state.phase)) return [];
    const venue = entry("hope");
    const pads = Array.isArray(venue.hope_pads) ? venue.hope_pads : [];
    if (pads.length !== 16) throw new Error("Hopeの16床データがありません。");
    const players = world.getAllPlayers?.() ?? [];
    return (current.players ?? []).map((playerId, seat) => {
      if (current.state.alive?.[seat] === false) return null;
      const player = players.find(candidate => candidate.id === playerId);
      if (!player || player.dimension?.id !== DIMENSION) return null;
      let best = null;
      for (const pad of pads) {
        const centerX = Number.isFinite(pad.center_x) ? pad.center_x : pad.x + 0.5;
        const centerZ = Number.isFinite(pad.center_z) ? pad.center_z : pad.z + 0.5;
        const distance = Math.hypot(player.location.x - centerX, player.location.z - centerZ);
        // game:hope/out uses sort=nearest over every floor marker without a
        // distance limit. Keep the same rule so a participant who never steps
        // on a pad is still judged against the closest floor.
        if (!Number.isFinite(distance) || (best && distance >= best.distance)) continue;
        best = { distance, pad: pad.pad };
      }
      return best?.pad ?? null;
    });
  }
  function walkerFloorCells(venue) {
    const cells = venue.walker?.cells;
    if (!Array.isArray(cells) || cells.length !== 37) {
      throw new Error("Walkerの37床データがありません。");
    }
    return cells;
  }
  function walkerAdjacentFloor(left, right) {
    if (!left || !right || left.floor === right.floor) return false;
    const dx = Math.abs(left.x - right.x);
    const dz = Math.abs(left.z - right.z);
    // The source's 5x5 floor tiles overlap rather than sit six blocks apart.
    return dx <= 4 && dz <= 4;
  }
  function walkerFloorAtPlayer(venue, player) {
    const area = venue.walker;
    if (!area) return null;
    const y = Number(player.location?.y);
    if (!Number.isFinite(y) || y < area.platform_y + 0.25 || y > area.platform_y + 3.25) {
      return null;
    }
    const blockX = Math.floor(Number(player.location.x));
    const blockZ = Math.floor(Number(player.location.z));
    const underfoot = blockAt(dimension(), blockX, area.platform_y, blockZ);
    let observed = null;
    // walker/floor/check runs floors 1..37 in order, requires the marker within
    // 2.8 blocks and the block below the player to match that floor's color.
    // Later overlapping tiles overwrite earlier matches, so the last hit wins.
    for (const cell of [...walkerFloorCells(venue)].sort((left, right) => left.floor - right.floor)) {
      const dx = player.location.x - (cell.x + 0.5);
      const dz = player.location.z - (cell.z + 0.5);
      if (Math.hypot(dx, dz) > 2.8) continue;
      if (underfoot?.typeId !== WALKER_COLORS[cell.color]) continue;
      observed = cell.floor;
    }
    return observed;
  }
  function walkerSeatRotation(venue, seat) {
    const rotations = Array.isArray(venue.seat_rotations) ? venue.seat_rotations : [];
    return Number.isFinite(rotations[seat]) ? rotations[seat] : venue.rotation ?? 0;
  }
  function walkerTeleportToFloor(player, venue, seat, floor) {
    const cell = walkerFloorCells(venue).find(value => value.floor === floor);
    if (!cell) return false;
    try {
      player.teleport({ x: cell.x + 0.5, y: venue.walker.platform_y + 1, z: cell.z + 0.5 }, {
        dimension: dimension(), rotation: { x: 0, y: walkerSeatRotation(venue, seat) },
      });
      return true;
    } catch {
      return false;
    }
  }
  function walkerExpectedFloors(venue, state) {
    const dead = new Set(state?.deadFloors ?? []);
    const planned = new Map();
    const cells = [...walkerFloorCells(venue)].sort((left, right) => left.floor - right.floor);
    if (cells.every(cell => Array.isArray(cell.floor_cells))) {
      // The source clone template has irregular footprints. Never expand
      // those into the substitute venue's 5x5 squares over original aisles.
      return cells.flatMap(cell => cell.floor_cells.map(at => ({
        x: at.x, z: at.z,
        blockType: dead.has(cell.floor) ? "minecraft:air" : WALKER_COLORS[cell.color],
      })));
    }
    // The source places every 5x5 tile in floor order, so a later tile
    // overwrites an earlier tile where the footprints overlap.
    for (const cell of cells) {
      const blockType = WALKER_COLORS[cell.color];
      if (!blockType) throw new Error(`Walkerの床色が不正です: ${cell.color}`);
      for (let dx = -2; dx <= 2; dx++) {
        for (let dz = -2; dz <= 2; dz++) {
          const x = cell.x + dx;
          const z = cell.z + dz;
          planned.set(`${x},${z}`, { x, z, blockType });
        }
      }
    }
    // The source deletes a departed tile with `fill ... air replace <color>`;
    // cells already overwritten by a later, different-colored tile remain.
    for (const cell of cells) {
      if (!dead.has(cell.floor)) continue;
      const blockType = WALKER_COLORS[cell.color];
      for (let dx = -2; dx <= 2; dx++) {
        for (let dz = -2; dz <= 2; dz++) {
          const plannedCell = planned.get(`${cell.x + dx},${cell.z + dz}`);
          if (plannedCell?.blockType === blockType) plannedCell.blockType = "minecraft:air";
        }
      }
    }
    return [...planned.values()];
  }
  function syncWalkerFloors(current) {
    const venue = entry("walker");
    const state = current?.state;
    if (!state || !venue.walker) return;
    const key = `${state.phase}|${state.deadFloors?.join(",") ?? ""}`;
    if (!walkerSyncDirty && walkerSyncKey === key) return;
    walkerSyncDirty = false;
    walkerSyncKey = key;
    const d = dimension();
    for (const cell of walkerExpectedFloors(venue, state)) {
      const block = blockAt(d, cell.x, venue.walker.platform_y, cell.z);
      if (!block || block.typeId === cell.blockType) continue;
      block.setType(cell.blockType);
    }
  }
  function walkerLabelFrame(floor, deadFloors = []) {
    if (!Number.isInteger(floor) || floor < 1 || floor > 37) return null;
    return (deadFloors ?? []).includes(floor)
      ? WALKER_LABEL_DEAD[floor - 1]
      : WALKER_LABEL_INITIAL[floor - 1];
  }
  function walkerPresentationLocation(venue, cell) {
    return {
      x: cell.x + 0.5,
      y: venue.walker.platform_y + WALKER_LABEL_Y_OFFSET,
      z: cell.z + 0.5,
    };
  }
  function samePosition(left, right) {
    try {
      return Math.abs(left.x - right.x) <= 0.08 &&
        Math.abs(left.y - right.y) <= 0.08 &&
        Math.abs(left.z - right.z) <= 0.08;
    } catch {
      return false;
    }
  }
  function walkerVisibleBombFloors(state) {
    if (!state || state.bombVisible !== true ||
        !["dodge", "fuse", "aftermath"].includes(state.phase)) return new Set();
    const values = state.bombPositions ?? state.bombs ?? [];
    return new Set((Array.isArray(values) ? values : []).filter(Number.isInteger));
  }
  function ensureWalkerPresentationEntities(current = walkerCurrent) {
    const venue = entry("walker");
    const area = venue.walker;
    if (!area) throw new Error("Walkerの会場データがありません。");
    const cells = walkerFloorCells(venue);
    const deadFloors = current?.state?.deadFloors ?? [];
    const labels = entitiesWithTag("walker", WALKER_LABEL_TAG)
      .filter(entity => isManagedFixture("walker", entity) && entity.typeId === WALKER_LABEL_TYPE);
    const labelsByFloor = new Map();
    for (const entity of labels) {
      const floorTag = (entity.getTags?.() ?? []).find(tag =>
        tag.startsWith(`${WALKER_LABEL_TAG}:`));
      const floor = Number(floorTag?.slice(WALKER_LABEL_TAG.length + 1));
      if (entity.typeId !== WALKER_LABEL_TYPE || !Number.isInteger(floor) ||
          floor < 1 || floor > 37) {
        entity.remove();
        continue;
      }
      const bucket = labelsByFloor.get(floor) ?? [];
      bucket.push(entity);
      labelsByFloor.set(floor, bucket);
    }
    for (const cell of cells) {
      const expected = walkerPresentationLocation(venue, cell);
      const matches = labelsByFloor.get(cell.floor) ?? [];
      let valid = matches.find(entity =>
        entity.typeId === WALKER_LABEL_TYPE &&
        samePosition(entity.location, expected)) ?? null;
      for (const candidate of matches) {
        if (candidate !== valid) candidate.remove();
      }
      if (!valid) {
        valid = dimension().spawnEntity(WALKER_LABEL_TYPE, point(expected));
        valid.addTag(managedTag("walker"));
        valid.addTag(WALKER_AUXILIARY_TAG);
        valid.addTag(WALKER_LABEL_TAG);
        valid.addTag(`${WALKER_LABEL_TAG}:${cell.floor}`);
      }
      valid.nameTag = "";
      valid.setProperty?.("junket:floor", cell.floor);
      setVisualFrame(valid, walkerLabelFrame(cell.floor, deadFloors));
      valid.setDynamicProperty?.("junket:walker_role", "floor_label");
      valid.setDynamicProperty?.("junket:walker_floor", cell.floor);
    }

    const expectedBombs = walkerVisibleBombFloors(current?.state);
    const bombs = entitiesWithTag("walker", WALKER_BOMB_TAG)
      .filter(entity => isManagedFixture("walker", entity) && entity.typeId === WALKER_BOMB_TYPE);
    const bombsByFloor = new Map();
    for (const entity of bombs) {
      const floorTag = (entity.getTags?.() ?? []).find(tag =>
        tag.startsWith(`${WALKER_BOMB_TAG}:`));
      const floor = Number(floorTag?.slice(WALKER_BOMB_TAG.length + 1));
      if (entity.typeId !== WALKER_BOMB_TYPE || !Number.isInteger(floor) ||
          !expectedBombs.has(floor)) {
        entity.remove();
        continue;
      }
      const bucket = bombsByFloor.get(floor) ?? [];
      bucket.push(entity);
      bombsByFloor.set(floor, bucket);
    }
    for (const floor of expectedBombs) {
      const cell = cells.find(value => value.floor === floor);
      if (!cell || (deadFloors ?? []).includes(floor)) continue;
      const expected = walkerPresentationLocation(venue, cell);
      const matches = bombsByFloor.get(floor) ?? [];
      let valid = matches.find(entity =>
        entity.typeId === WALKER_BOMB_TYPE &&
        samePosition(entity.location, expected)) ?? null;
      for (const candidate of matches) {
        if (candidate !== valid) candidate.remove();
      }
      if (!valid) {
        valid = dimension().spawnEntity(WALKER_BOMB_TYPE, point(expected));
        valid.addTag(managedTag("walker"));
        valid.addTag(WALKER_AUXILIARY_TAG);
        valid.addTag(WALKER_BOMB_TAG);
        valid.addTag(`${WALKER_BOMB_TAG}:${floor}`);
      }
      valid.nameTag = "";
      valid.setDynamicProperty?.("junket:walker_role", "bomb");
      valid.setDynamicProperty?.("junket:walker_floor", floor);
    }
  }
  function verifyWalkerPresentationEntities(current = walkerCurrent) {
    const venue = entry("walker");
    const cells = walkerFloorCells(venue);
    const deadFloors = current?.state?.deadFloors ?? [];
    const labels = entitiesWithTag("walker", WALKER_LABEL_TAG)
      .filter(entity => isManagedFixture("walker", entity) && entity.typeId === WALKER_LABEL_TYPE);
    for (const cell of cells) {
      const expected = walkerPresentationLocation(venue, cell);
      const matches = labels.filter(entity => {
        try {
          return entity.typeId === WALKER_LABEL_TYPE &&
            entity.hasTag(`${WALKER_LABEL_TAG}:${cell.floor}`);
        } catch {
          return false;
        }
      });
      if (matches.length !== 1 || !samePosition(matches[0].location, expected) ||
          matches[0].getProperty?.("junket:frame") !==
            walkerLabelFrame(cell.floor, deadFloors)) {
        throw new Error(`Walkerの床表示が不正です: ${cell.floor}`);
      }
    }
    if (labels.length !== cells.length) {
      throw new Error(`Walkerの床表示数が不一致です: ${labels.length}/${cells.length}`);
    }
    const expectedBombs = walkerVisibleBombFloors(current?.state);
    const bombs = entitiesWithTag("walker", WALKER_BOMB_TAG)
      .filter(entity => isManagedFixture("walker", entity) && entity.typeId === WALKER_BOMB_TYPE);
    if (bombs.length !== expectedBombs.size) {
      throw new Error(`Walkerの爆弾表示数が不一致です: ${bombs.length}/${expectedBombs.size}`);
    }
    for (const floor of expectedBombs) {
      const cell = cells.find(value => value.floor === floor);
      const expected = cell ? walkerPresentationLocation(venue, cell) : null;
      const matches = bombs.filter(entity => {
        try {
          return entity.typeId === WALKER_BOMB_TYPE && entity.hasTag(`${WALKER_BOMB_TAG}:${floor}`);
        } catch {
          return false;
        }
      });
      if (!expected || matches.length !== 1 || !samePosition(matches[0].location, expected)) {
        throw new Error(`Walkerの爆弾表示が不正です: ${floor}`);
      }
    }
    return true;
  }
  function syncWalkerPresentation(current) {
    const state = current?.state;
    const bombFloors = [...walkerVisibleBombFloors(state)].sort((left, right) => left - right);
    const key = [
      state?.phase ?? "idle",
      state?.bombVisible === true ? "bomb-visible" : "bomb-hidden",
      state?.deadFloors?.join(",") ?? "",
      bombFloors.join(","),
    ].join("|");
    walkerPresentationTick += 1;
    if (key === walkerPresentationKey && walkerPresentationTick % 20 !== 0) return;
    ensureWalkerPresentationEntities(current);
    walkerPresentationKey = key;
  }
  function pollWalker(current) {
    walkerCurrent = current;
    const state = current?.state;
    const venue = entry("walker");
    const area = venue.walker;
    if (!state || !area) return { positions: [null, null], water: [] };
    syncWalkerFloors(current);
    syncWalkerPresentation(current);
    const online = world.getAllPlayers?.() ?? [];
    const positions = [null, null];
    const water = [];
    for (let seat = 0; seat < 2; seat++) {
      const playerId = current.players?.[seat];
      const player = online.find(candidate => candidate.id === playerId);
      if (!player || player.dimension?.id !== DIMENSION) continue;
      const x = Math.floor(player.location.x);
      const y = player.location.y;
      const z = Math.floor(player.location.z);
      if (y < area.platform_y - 1.25 && x >= area.min_x && x <= area.max_x &&
          z >= area.min_z && z <= area.max_z) {
        const wet = [Math.floor(y), Math.floor(y) + 1, Math.floor(y) + 2]
          .some(level => ["minecraft:water", "minecraft:flowing_water"]
            .includes(blockAt(dimension(), x, level, z)?.typeId));
        if (wet || y <= area.water_top + 1.25) water.push(seat);
        continue;
      }
      const expected = state.positions?.[seat];
      const observed = walkerFloorAtPlayer(venue, player);
      const expectedCell = walkerFloorCells(venue).find(cell => cell.floor === expected);
      const observedCell = walkerFloorCells(venue).find(cell => cell.floor === observed);
      const mayMove = state.actor === seat && ["walk", "dodge"].includes(state.phase) &&
        observedCell && !(state.deadFloors ?? []).includes(observed) &&
        (state.phase !== "dodge" || state.dodgeMoved !== true) &&
        walkerAdjacentFloor(expectedCell, observedCell) &&
        (state.phase !== "walk" || (state.movedSteps?.[seat] ?? 0) < 5);
      if (mayMove) {
        positions[seat] = observed;
      } else {
        if (observed !== expected) walkerTeleportToFloor(player, venue, seat, expected);
        positions[seat] = expected;
      }
    }
    return { positions, water };
  }
  function rabbitCell(venue, tile) {
    const cells = venue.rabbit?.cells;
    if (!Array.isArray(cells) || cells.length !== 60 ||
        !venue.rabbit?.oni || venue.rabbit.oni.tile !== 61) {
      throw new Error("Rabbitの60床データがありません。");
    }
    if (tile === 61 || cells.some(cell => cell.tile === 61)) return null;
    return cells.find(cell => cell.tile === tile) ?? null;
  }
  function rabbitCpuMatches(id, participantId) {
    return entitiesWithTag(id, `junket:rabbit:cpu:${participantId}`)
      .filter(entity => entity.typeId === "minecraft:armor_stand" && entity.hasTag("junket:rabbit:cpu"));
  }
  function ensureRabbitCpuEntities(id, current = rabbitCurrent) {
    if (id !== "rabbit") return;
    const venue = entry(id);
    const starts = [1, 13, 37, 49];
    for (let index = 0; index < RABBIT_CPU_NAMES.length; index++) {
      const participantId = `p${index + 3}`;
      const matches = rabbitCpuMatches(id, participantId);
      let entity = matches[0] ?? null;
      for (const extra of matches.slice(1)) extra.remove();
      if (!entity) {
        const participant = current?.state?.participants
          ?.find(candidate => candidate.id === participantId);
        const cell = rabbitCell(venue, participant?.position ?? starts[index]);
        if (!cell) throw new Error(`Rabbit CPU ${participantId} の初期床がありません。`);
        entity = dimension().spawnEntity("minecraft:armor_stand", {
          x: cell.x + 0.5, y: venue.rabbit.stand_y, z: cell.z + 0.5,
        });
        entity.addTag("junket:rabbit:cpu");
        entity.addTag(`junket:rabbit:cpu:${participantId}`);
      }
      entity.addTag(managedTag(id));
      entity.setDynamicProperty?.("junket:venue_id", id);
      entity.nameTag = RABBIT_CPU_NAMES[index];
    }
  }
  function syncRabbitFloors(current) {
    const venue = entry("rabbit");
    const state = current?.state;
    if (!state || !venue.rabbit) return;
    const traps = state.phase === "reveal" || ["drop", "result", "finished"].includes(state.phase)
      ? (state.traps ?? []) : [];
    const guarded = (state.guardedFloors ?? []);
    const allFloorsOpen = state.phase === "result" && state.allFloorsOpen === true;
    const oniFloorOpen = state.phase === "result" && state.oniFloorOpen === true;
    const dropFloors = ["drop", "result"].includes(state.phase) ? (state.dropFloors ?? []) : [];
    const falling = state.phase === "drop" ? (state.falling ?? []) : [];
    const key = JSON.stringify({
      round: state.round, phase: state.phase, traps, guarded, dropFloors, falling,
      allFloorsOpen, oniFloorOpen,
    });
    if (key === rabbitSyncKey) return;
    const trapSet = new Set(traps);
    const guardedSet = new Set(guarded);
    const drop = new Set(dropFloors);
    const roles = new Map((venue.fixtures ?? [])
      .filter(fixture => /^floor_\d+$/.test(fixture.action ?? ""))
      .map(fixture => [Number(fixture.action.slice(6)), fixture.role]));
    const d = dimension();
    for (const cell of venue.rabbit.cells) {
      const expected = allFloorsOpen || drop.has(cell.tile) ? "minecraft:air" : cell.floor;
      for (const [x, y, z] of cell.floor_cells) {
        const block = blockAt(d, x, y, z);
        if (!block) throw new Error(`Rabbitの床チャンクを読み込めません: ${x},${y},${z}`);
        if (block.typeId !== expected) block.setType(expected);
      }
      const role = roles.get(cell.tile);
      const entity = role ? findRoleEntities("rabbit", role)[0] : null;
      if (!entity) continue;
      const frame = drop.has(cell.tile) ? RABBIT_FLOOR_FRAME.falling
        : guardedSet.has(cell.tile) ? RABBIT_FLOOR_FRAME.guarded
          : trapSet.has(cell.tile) ? RABBIT_FLOOR_FRAME.trap
            : RABBIT_FLOOR_FRAME.normal;
      try {
        setVisualFrame(entity, frame);
        entity.setRotation?.({ x: frame === RABBIT_FLOOR_FRAME.falling ? RABBIT_FALL_PITCH : 0, y: 0 });
      } catch { /* Retry on the next repaired state. */ }
    }
    const oni = venue.rabbit.oni;
    if (oni) {
      const expected = oniFloorOpen ? "minecraft:air" : oni.floor;
      for (const [x, y, z] of oni.floor_cells ?? []) {
        const block = blockAt(d, x, y, z);
        if (!block) throw new Error(`Rabbitの鬼床チャンクを読み込めません: ${x},${y},${z}`);
        if (block.typeId !== expected) block.setType(expected);
      }
    }
    if (state.phase === "drop" && rabbitLastPhase !== "drop") {
      try {
        d.playSound?.("block.copper_trapdoor.open", center(venue), { volume: 1, pitch: 1 });
      } catch { /* Optional sound. */ }
    } else if (state.phase !== "drop" && rabbitLastPhase === "drop") {
      try {
        d.playSound?.("block.copper_trapdoor.close", center(venue), { volume: 1, pitch: 1 });
      } catch { /* Optional sound. */ }
    }
    if (state.phase === "reveal" && rabbitLastPhase !== "reveal") {
      try {
        d.playSound?.("block.note_block.bit", center(venue), { volume: 1, pitch: 1 });
      } catch { /* Optional sound. */ }
    }
    const resultOpen = oniFloorOpen || allFloorsOpen;
    if (resultOpen && !rabbitLastResultOpen) {
      try {
        d.playSound?.("block.copper_trapdoor.open", center(venue), { volume: 1, pitch: 1 });
      } catch { /* Optional sound. */ }
    }
    rabbitLastResultOpen = resultOpen;
    rabbitLastPhase = state.phase;
    rabbitSyncKey = key;
  }
  function rabbitPlayerInWater(current, player, venue) {
    return rabbitLocationInWater(venue, player.location);
  }
  function rabbitLocationInWater(venue, location) {
    if (!Number.isFinite(location?.x) || !Number.isFinite(location?.y) ||
        !Number.isFinite(location?.z)) return false;
    const x = Math.floor(location.x);
    const y = Math.floor(location.y + 0.01);
    const z = Math.floor(location.z);
    if (x < venue.rabbit.min_x || x > venue.rabbit.max_x ||
        z < venue.rabbit.min_z || z > venue.rabbit.max_z) return false;
    // rabbit/losecheck.mcfunction checks exactly the position marker's block
    // (`if block ~ ~ ~ water`) after teleporting the participant token onto it.
    // The Bedrock adaptation keeps the participant physical, so the closest
    // equivalent is the single block occupied by their feet. Sampling Y-1 or
    // Y+1 would eliminate a participant beside water, not in it.
    return ["minecraft:water", "minecraft:flowing_water"]
      .includes(blockAt(dimension(), x, y, z)?.typeId);
  }
  function pollRabbitLanding(current) {
    rabbitCurrent = current;
    const state = current?.state;
    const venue = entry("rabbit");
    if (!state || state.phase !== "drop" || !venue.rabbit) return null;
    syncRabbitFloors(current);
    // Source rabbit/losecheck.mcfunction checks all five rabbit-player
    // position markers every tick. It does not use the trap cursor or a
    // precomputed falling list. Safe and guarded floors remain solid, so they
    // cannot produce a false hit; an actual water contact still loses even if
    // the participant was not present in the earlier falling snapshot.
    const participants = (state.participants ?? [])
      .filter(participant => participant.alive)
      .map(participant => participant.id);
    if (!participants.length) return [];
    const online = world.getAllPlayers?.() ?? [];
    const player = online.find(candidate => candidate.id === current.players?.[0]);
    const landed = [];
    for (const participantId of participants) {
      if (participantId === "p1") {
        if (player?.dimension?.id === DIMENSION && rabbitPlayerInWater(current, player, venue)) {
          landed.push(participantId);
        }
      } else {
        const marker = rabbitCpuMatches("rabbit", participantId)[0];
        if (marker && rabbitLocationInWater(venue, marker.location)) {
          landed.push(participantId);
        }
      }
    }
    // Source rabbit/losecheck.mcfunction samples the water every tick. Return
    // observations during the window so the runtime can persist them before a
    // restart; the caller still waits for the 30-tick floor reset before
    // applying eliminations. An unobserved participant survives when the
    // source closes the floor at 5850.
    return landed;
  }
  function rabbitFloorAtPlayer(venue, player) {
    const area = venue.rabbit;
    if (!area) return null;
    const y = Number(player.location?.y);
    if (!Number.isFinite(y) || y < area.stand_y - 1.25 || y > area.stand_y + 1.75) return null;
    let best = null;
    for (const cell of area.cells ?? []) {
      const dx = player.location.x - (cell.x + 0.5);
      const dz = player.location.z - (cell.z + 0.5);
      const distance = Math.hypot(dx, dz);
      if (distance > 1.2 || (best && distance >= best.distance)) continue;
      best = { distance, tile: cell.tile };
    }
    return best?.tile ?? null;
  }
  function pollRabbitPosition(current) {
    if (!current?.state || current.state.phase !== "move") return null;
    const venue = entry("rabbit");
    if (!venue.rabbit) return null;
    const player = (world.getAllPlayers?.() ?? [])
      .find(candidate => candidate.id === current.players?.[0]);
    if (!player || player.dimension?.id !== DIMENSION) return null;
    return rabbitFloorAtPlayer(venue, player);
  }
  function updateRabbit(id, current) {
    const venue = entry(id);
    rabbitCurrent = current;
    syncRabbitFloors(current);
    ensureRabbitCpuEntities(id, current);
    const state = current?.state;
    const online = world.getAllPlayers?.() ?? [];
    if (!state || !venue.rabbit) {
      const reception = findRoleEntities(id, "reception")[0];
      if (reception) reception.nameTag = `${venue.title}受付 / 参加・操作・再戦`;
      return;
    }
    const revealed = ["reveal", "drop", "result", "finished"].includes(state.phase);
    const traps = new Set(revealed ? (state.traps ?? []) : []);
    const guarded = new Set(revealed ? (state.guardedFloors ?? []) : []);
    const dropping = new Set(revealed ? (state.dropFloors ?? []) : []);
    const falling = new Set(state.phase === "drop" ? (state.falling ?? []) : []);
    for (const fixture of venue.fixtures ?? []) {
      if (!fixture.role.startsWith("control_")) continue;
      const entity = findRoleEntities(id, fixture.role)[0];
      if (!entity) continue;
      const floorMatch = /^floor_(\d+)$/.exec(fixture.action ?? "");
      if (floorMatch) {
        const tile = Number(floorMatch[1]);
        entity.nameTag = `床${tile}${traps.has(tile) ? "【罠】" : ""}`
          + `${guarded.has(tile) ? "【守護】" : ""}${dropping.has(tile) ? "【落下】" : ""}`;
      } else if (/^guard_[3-6]$/.test(fixture.action ?? "")) {
        const human = state.participants?.find(participant => participant.human);
        entity.nameTag = human?.haveGuard && state.phase === "move"
          ? fixture.name : `${fixture.name}（使用不可）`;
      }
    }
    const displays = ["display_1", "display_2", "display_3"]
      .map(role => findRoleEntities(id, role)[0]);
    const trapText = revealed ? ((state.traps ?? []).join(", ") || "なし") : "非公開";
    const participants = (state.participants ?? []).map(participant =>
      `${participant.name} ${participant.position}${participant.alive ? "" : "(脱落)"}`).join(" / ");
    if (displays[0]) displays[0].nameTag = `第${state.round}ラウンド  ${state.phase}  残り${state.phaseRemaining}t\n罠 ${trapText}`;
    if (displays[1]) displays[1].nameTag = `生存 ${participants}\n手番 ${state.actor === null ? "なし" : state.actor === 0 ? "脱出側" : "オニ"}`;
    if (displays[2]) displays[2].nameTag = "現在地または直交1〜3マス移動 / 罠5個 / ゴール12・24・36・48・60\nお守りは罠の床全体を守ります";
    const reception = findRoleEntities(id, "reception")[0];
    if (reception) reception.nameTag = `${venue.title}\n${state.phase} / 参加・操作・再戦`;

    for (const participant of state.participants ?? []) {
      if (participant.human || participant.id === "p2") continue;
      const marker = rabbitCpuMatches(id, participant.id)[0];
      const cell = rabbitCell(venue, participant.position);
      if (!marker || !cell) continue;
      marker.nameTag = `${participant.name}${participant.alive ? "" : "(脱落)"}`;
      if (falling.has(participant.id)) continue;
      const target = participant.alive ? {
        x: cell.x + 0.5, y: venue.rabbit.stand_y, z: cell.z + 0.5,
      } : venue.rabbit.spectator;
      try {
        marker.teleport({ x: target.x, y: target.y, z: target.z }, { dimension: dimension() });
      } catch { /* A token can be temporarily unavailable while reloading. */ }
    }
    const human = state.participants?.find(participant => participant.human);
    const player = online.find(candidate => candidate.id === current.players?.[0]);
    const humanKey = `${state.round}|${state.phase}|${human?.position}|${human?.alive}`;
    if (human && player && humanKey !== rabbitHumanKey) {
      rabbitHumanKey = humanKey;
      if (falling.has("p1")) return;
      const cell = rabbitCell(venue, human.position);
      const target = human.alive && cell ? {
        x: cell.x + 0.5, y: venue.rabbit.stand_y, z: cell.z + 0.5,
      } : venue.rabbit.spectator;
      try {
        player.teleport(target, {
          dimension: dimension(),
          rotation: { x: 0, y: human.alive ? walkerSeatRotation(venue, 0) : 0 },
        });
      } catch { /* Retry on the next verified state update. */ }
    }
    const oni = venue.rabbit.oni;
    const oniPlayer = online.find(candidate => candidate.id === current.players?.[1]);
    if (state.phase !== "result") {
      // A later match can reuse the same round, reason and floor state. Clear
      // the presentation key whenever the result leaves the screen so the next
      // result is acknowledged instead of inheriting the previous match key.
      rabbitOniKey = "";
    } else if (oni && oniPlayer) {
      const oniKey = `${state.round}|${state.reason}|${state.oniFloorOpen === true}`;
      if (oniKey !== rabbitOniKey) {
        rabbitOniKey = oniKey;
        // timeup/player-2 and open/goal keep the oni above floor 61 during the
        // 20-tick announcement, then drop it two blocks when the 3x3 floor
        // opens. The escape-side wipe in wincheck has no drop animation.
        const target = {
          x: oni.x + 0.5,
          y: state.oniFloorOpen === true ? venue.rabbit.water_y : venue.rabbit.stand_y,
          z: oni.z + 0.5,
        };
        try {
          oniPlayer.teleport(target, {
            dimension: dimension(),
            rotation: { x: 0, y: oniPlayer.rotation?.y ?? -90 },
          });
        } catch { /* Retry on the next verified state update. */ }
      }
    }
  }
  const center = venue => ({
    x: venue.origin[0] + venue.size[0] / 2,
    y: venue.origin[1] + venue.size[1] / 2,
    z: venue.origin[2] + venue.size[2] / 2,
  });
  const entitiesWithTag = (id, tag) => {
    try {
      const current = dimension().getEntities({
        tags: [tag], location: center(entry(id)), maxDistance: 96,
      });
      const previous = entry(id).previous_managed_bounds;
      const stale = previous ? dimension().getEntities({
        tags: [tag], location: center(previous), maxDistance: Math.hypot(...previous.size) / 2 + 8,
      }) : [];
      return [...new Map([...current, ...stale].map(entity => [entity.id ?? entity, entity])).values()]
        .filter(entity => entity.typeId !== "minecraft:player" && insideFixtureScope(entry(id), entity));
    } catch (error) {
      throw new Error(`${id}の操作物を読み込めません: ${error?.message ?? error}`);
    }
  };

  function isHopeCodeEntity(entity) {
    try {
      return entity?.hasTag?.("junket:hope:code") === true ||
        entity?.hasTag?.("junket:hope:fixture:hope_code") === true ||
        entity?.getDynamicProperty?.("junket:venue_role") === "hope_code";
    } catch {
      return false;
    }
  }

  function removeHopeApparatusRoles(roles) {
    const wanted = new Set(roles);
    for (const fixture of entry("hope").fixtures ?? []) {
      if (!wanted.has(fixture.role)) continue;
      for (const entity of findRoleEntities("hope", fixture.role)) {
        try { entity.remove(); } catch { /* The chunk may be unloading. */ }
      }
    }
  }

  function hopeCodeDisplay() {
    return findRoleEntities("hope", "hope_code_display")[0] ?? null;
  }

  function playHopeCureSound() {
    const origin = hopeCodeDisplay()?.location ?? {
      x: entry("hope").origin[0], y: entry("hope").origin[1],
      z: entry("hope").origin[2],
    };
    try {
      dimension().playSound?.("entity.zombie_villager.cure", origin, {
        volume: 2, pitch: 1,
      });
      return;
    } catch { /* Fall back to player-local playback on older engines. */ }
    for (const player of world.getAllPlayers?.() ?? []) {
      try { player.playSound?.("entity.zombie_villager.cure"); } catch { /* Optional sound. */ }
    }
  }

  function breakHopeApparatus() {
    if (hopeApparatusBroken()) return false;
    setHopeApparatusBroken(true);
    removeHopeApparatusRoles(["hope_code", ...Array.from({ length: 6 }, (_, index) =>
      `hope_tv_${index + 1}`)]);
    // A missing display is the only apparatus piece recreated while broken.
    try { ensureGenericEntities("hope"); } catch { /* Retried by the next repair tick. */ }
    const display = hopeCodeDisplay();
    try { setVisualFrame(display, HOPE_DISPLAY_FRAME.broken); } catch { /* Best effort. */ }
    playHopeCureSound();
    return true;
  }

  function restoreHopeApparatus() {
    setHopeApparatusBroken(false);
    // Reset the existing three-health apparatus in place. Recreate only when
    // the source break interaction actually removed it; ordinary entry/start
    // must not delete a healthy fixed actor.
    ensureGenericEntities("hope");
    const code = findRoleEntities("hope", "hope_code")[0];
    try {
      const health = code?.getComponent?.("minecraft:health");
      if (health?.setCurrentValue) health.setCurrentValue(3);
      else health?.resetToMaxValue?.();
    } catch { /* The entity definition supplies the default health. */ }
    const display = hopeCodeDisplay();
    try { setVisualFrame(display, HOPE_DISPLAY_FRAME.normal); } catch { /* Best effort. */ }
  }

  function clearHopePlayerStatus(player) {
    resetHopeElectricity(player);
    try { player.removeEffect?.("invisibility"); } catch { /* Disconnected participant. */ }
    try { player.clearVelocity?.(); } catch { /* Older engines omit this method. */ }
    try { player.removeTag?.(HOPE_OBSERVER_TAG); } catch { /* Best effort. */ }
    try { player.removeTag?.("hope-player"); } catch { /* Best effort. */ }
    try { player.removeTag?.("hope-outplayer"); } catch { /* Best effort. */ }
    hopeWarped.delete(player.id);
  }

  function cleanupHopePlayers(playerIds, online = world.getAllPlayers?.() ?? []) {
    const targets = new Map();
    for (const playerId of playerIds ?? []) {
      const player = online.find(candidate => candidate.id === playerId);
      if (player) targets.set(player.id, player);
    }
    for (const player of online) {
      try {
        if (player.hasTag?.(HOPE_OBSERVER_TAG)) targets.set(player.id, player);
      } catch { /* Player state unavailable while leaving. */ }
    }
    for (const player of targets.values()) clearHopePlayerStatus(player);
  }

  try {
    world.beforeEvents?.playerInteractWithEntity?.subscribe(event => {
      if (!event?.target?.hasTag?.("junket:hope:apparatus")) return;
      event.cancel = true;
    });
  } catch { /* Older engines may not expose this event. */ }
  try {
    world.afterEvents?.entityDie?.subscribe(event => {
      if (!isHopeCodeEntity(event?.deadEntity)) return;
      const killer = event?.damageSource?.damagingEntity;
      if (killer?.typeId !== "minecraft:player" ||
          !hopeCurrent?.players?.includes(killer.id)) return;
      // Source hope/killtv.json is granted only to a player carrying the
      // hope-player tag. The participant list alone could still contain a
      // player whose source tag was removed during end-of-game cleanup.
      try {
        if (killer.hasTag?.("hope-player") !== true) return;
      } catch { return; }
      breakHopeApparatus();
    });
  } catch { /* Older engines may not expose this event. */ }

  function applySpecialConfig(id) {
    const key = SPECIAL_KEYS[id];
    if (key) world.setDynamicProperty(key, JSON.stringify(config(id)));
  }

  async function callSpecial(id) {
    const provider = specialVenues[id];
    if (!provider) return false;
    if (typeof provider.ensure === "function") {
      await provider.ensure();
      if (typeof provider.verify !== "function") {
        throw new Error(`${entry(id).title}の専用会場検証がありません。`);
      }
      // remove() can leave actors visible in the current tick's query. Verify
      // only after the engine has applied the managed-actor reconciliation.
      await waitTicks(2);
      await provider.verify();
      return true;
    }
    if (typeof provider.provision === "function") {
      await provider.provision(provider.config?.() ?? config(id));
      return true;
    }
    return false;
  }

  function findRoleEntities(id, role) {
    return entitiesWithTag(id, roleTag(id, role)).filter(entity => isManagedFixture(id, entity));
  }
  function isManagedFixture(id, entity) {
    try {
      const owner = entity.getDynamicProperty?.("junket:venue_id");
      return entity.hasTag(managedTag(id)) && (owner === undefined || owner === id);
    } catch { return false; }
  }

  function initialFixtureFrame(id, fixture) {
    if (id === "hope" && fixture.role === "hope_code_display") {
      return hopeApparatusBroken() ? HOPE_DISPLAY_FRAME.broken : HOPE_DISPLAY_FRAME.normal;
    }
    if (id === "fire") {
      if (fixture.role.startsWith("control_")) return FIRE_PROP_FRAME.available;
      if (fixture.role === "display_1" || fixture.role === "display_4") {
        return FIRE_PROP_FRAME.television;
      }
      if (fixture.role === "display_2" || fixture.role === "display_3") {
        return FIRE_PROP_FRAME.glassHealthy;
      }
    }
    if (id === "pig") {
      if (fixture.action?.startsWith("pig_card_")) return PIG_PROP_FRAME.available;
      if (fixture.action?.startsWith("pig_target_")) return PIG_PROP_FRAME.targetIdle;
      if (fixture.role.startsWith("display_")) return PIG_PROP_FRAME.television;
    }
    return null;
  }

  function withinFixtureLocation(entity, fixture) {
    const [x, y, z] = fixture.location;
    try {
      return Math.hypot(entity.location.x - x, entity.location.y - y, entity.location.z - z) <= 0.75;
    } catch {
      return false;
    }
  }

  function spawnFixture(id, fixture) {
    const d = dimension();
    const entity = markCreatedFixture(d.spawnEntity(fixture.type, point({
      x: fixture.location[0], y: fixture.location[1], z: fixture.location[2],
    })));
    entity.addTag(managedTag(id));
    entity.addTag(roleTag(id, fixture.role));
    for (const tag of fixture.tags ?? []) entity.addTag(tag);
    if (fixture.role === "reception") {
      entity.addTag(`junket:${id}:reception`);
      entity.addTag(`junket:${id}:control`);
    } else if (fixture.role.startsWith("control_")) {
      entity.addTag(`junket:${id}:control`);
    } else if (fixture.role.startsWith("display_")) {
      entity.addTag(`junket:${id}:display`);
    } else if (fixture.role === "return") {
      entity.addTag("junket:return");
    }
    entity.nameTag = fixture.name;
    entity.setDynamicProperty?.("junket:venue_id", id);
    entity.setDynamicProperty?.("junket:venue_role", fixture.role);
    entity.setDynamicProperty?.("junket:venue_action", fixture.action ?? fixture.role);
    if (fixture.frame_property !== undefined) entity.setDynamicProperty?.("junket:frame_property", fixture.frame_property);
    if (fixture.frame_map) entity.setDynamicProperty?.("junket:frame_map", JSON.stringify(fixture.frame_map));
    for (const [name, value] of Object.entries(fixture.properties ?? {})) entity.setDynamicProperty?.(name, value);
    if (fixture.rotation !== undefined) entity.teleport(entity.location, { rotation: { x: 0, y: fixture.rotation } });
    const frame = initialFixtureFrame(id, fixture);
    if (frame !== null) setVisualFrame(entity, frame, "junket:frame", fixture);
    return entity;
  }

  function ensureGenericEntities(id) {
    const venue = entry(id);
    const fixtures = expectedFixtures(id);
    if (venue.installation === "native-preinstalled") {
      for (const fixture of fixtures) {
        const at = fixture.location.map(Math.floor);
        if (!blockAt(dimension(), ...at)) throw new Error(`${id}の操作物チャンク未読込: ${fixture.role} (${at.join(",")})`);
      }
    }
    anchorVenueFixtures(world, venue, { idle: true });
    const expectedRoles = new Set(fixtures.map(fixture => fixture.role));
    for (const entity of entitiesWithTag(id, managedTag(id))) {
      let role;
      try { role = entity.getDynamicProperty?.("junket:venue_role"); } catch { role = undefined; }
      if (typeof role === "string" && !expectedRoles.has(role) &&
          entity.getDynamicProperty?.("junket:venue_id") === id) entity.remove();
    }
    for (const fixture of fixtures) {
      const matches = findRoleEntities(id, fixture.role).filter(entity => isManagedFixture(id, entity));
      let valid = preferredFixture(matches, fixture.type, entity => withinFixtureLocation(entity, fixture)) ?? null;
      for (const candidate of matches) {
        if (!sameFixtureActor(candidate, valid)) candidate.remove();
      }
      valid = ensureFixedFixtureHealth(venue, fixture, valid, () => spawnFixture(id, fixture));
      // Retain managed actors across restarts and restore their declared fixed
      // position instead of replacing a displaced actor. Never touch an actor
      // with only a coincidentally matching role tag.
      if (!withinFixtureLocation(valid, fixture)) {
        const before = { ...valid.location };
        valid.teleport(seatPoint(fixture.location), { dimension: dimension(),
          rotation: { x: 0, y: fixture.rotation ?? 0 } });
        valid.clearVelocity?.();
        console.warn(`[JUNKET] fixture reposition: ${JSON.stringify({ id, role: fixture.role,
          entity: valid.id, type: valid.typeId, before, after: valid.location })}`);
      }
      if (fixture.role === "reception") {
        valid.nameTag = `${venue.title}\n受付・参加・再戦`;
        valid.addTag(`junket:${id}:reception`);
        valid.addTag(`junket:${id}:control`);
      } else if (fixture.role === "return") {
        valid.nameTag = "入口へ戻る / JUNKET";
        valid.addTag("junket:return");
      } else {
        valid.nameTag = fixture.name;
        if (fixture.role.startsWith("control_")) valid.addTag(`junket:${id}:control`);
        if (fixture.role.startsWith("display_")) valid.addTag(`junket:${id}:display`);
      }
      valid.setDynamicProperty?.("junket:venue_action", fixture.action ?? fixture.role);
      valid.setDynamicProperty?.("junket:venue_id", id);
      valid.setDynamicProperty?.("junket:venue_role", fixture.role);
      if (fixture.frame_property !== undefined) valid.setDynamicProperty?.("junket:frame_property", fixture.frame_property);
      if (fixture.frame_map) valid.setDynamicProperty?.("junket:frame_map", JSON.stringify(fixture.frame_map));
      for (const tag of fixture.tags ?? []) valid.addTag(tag);
      for (const [name, value] of Object.entries(fixture.properties ?? {})) valid.setDynamicProperty?.(name, value);
      if (fixture.rotation !== undefined) valid.teleport(valid.location, { rotation: { x: 0, y: fixture.rotation } });
      const frame = initialFixtureFrame(id, fixture);
      if (frame !== null) setVisualFrame(valid, frame, "junket:frame", fixture);
      applyFixturePresentation(valid, venue, fixture);
    }
    ensureRabbitCpuEntities(id, rabbitCurrent);
    if (id === "hope") ensureHopeAiEntities(id, hopeCurrent);
    if (id === "walker") ensureWalkerPresentationEntities(walkerCurrent);
  }

  async function ensure(id) {
    const venue = entry(id);
    applySpecialConfig(id);
    if (venue.special_runtime) {
      if (!await callSpecial(id)) throw new Error(`${venue.title}の専用操作物を検証できません。`);
      return venue;
    }
    ensureGenericEntities(id);
    return venue;
  }

  function loadingBounds(venue) {
    const origin = [...venue.origin];
    const end = venue.origin.map((value, axis) => value + venue.size[axis] - 1);
    if (venue.installation === "native-preinstalled") {
      const previous = venue.previous_managed_bounds;
      if (previous) {
        for (let axis = 0; axis < 3; axis++) {
          origin[axis] = Math.min(origin[axis], previous.origin[axis]);
          end[axis] = Math.max(end[axis], previous.origin[axis] + previous.size[axis] - 1);
        }
      }
      for (const fixture of venue.fixtures ?? []) {
        const at = seatPoint(fixture.location);
        for (const [axis, name] of ["x", "y", "z"].entries()) {
          origin[axis] = Math.min(origin[axis], Math.floor(at[name]));
          end[axis] = Math.max(end[axis], Math.floor(at[name]));
        }
      }
    }
    return { origin, end };
  }

  function probeLocations(venue) {
    if (venue.installation === "native-preinstalled") {
      // A loaded corner is not proof that a distant fixture's chunk is ready.
      // Include the previous managed footprint so migration can see stale
      // actors even when no player is near the production halls.
      const { origin, end } = loadingBounds(venue);
      const cells = [];
      const source = new Map((venue.loading_probes ?? []).map(probe => [probe.chunk.join(","), probe.candidates]));
      for (let cx = Math.floor(origin[0] / 16); cx <= Math.floor(end[0] / 16); cx++) {
        for (let cz = Math.floor(origin[2] / 16); cz <= Math.floor(end[2] / 16); cz++) {
          const anchors = source.get(`${cx},${cz}`) ?? [];
          const x = Math.min(end[0], Math.max(origin[0], cx * 16 + 8));
          const z = Math.min(end[2], Math.max(origin[2], cz * 16 + 8));
          // Every chunk still needs a successful API read. Real source blocks
          // avoid treating one undefined boundary cell as the whole chunk.
          const candidates = anchors.length ? anchors.map(anchor => seatPoint(anchor.location)) : [
            { x, y: origin[1], z },
            { x, y: Math.floor((origin[1] + end[1]) / 2), z },
            { x, y: Math.max(-64, origin[1] - 1), z },
          ];
          cells.push({ label: `chunk ${cx},${cz}`, candidates, expected: anchors[0]?.block ?? "minecraft:air" });
          // Retain the original full chunk-grid checks as required reads too;
          // solid anchors add evidence rather than hiding an undefined cell.
          cells.push({ label: `boundary ${cx},${cz}`, candidates: [{
            x: Math.max(origin[0], cx * 16), y: origin[1], z: Math.max(origin[2], cz * 16),
          }], expected: "minecraft:air" });
        }
      }
      const fixtureCells = new Set();
      for (const fixture of venue.fixtures ?? []) {
        const value = seatPoint(fixture.location);
        const at = { x: Math.floor(value.x), y: Math.floor(value.y), z: Math.floor(value.z) };
        const key = `${at.x},${at.y},${at.z}`;
        if (fixtureCells.has(key)) continue;
        fixtureCells.add(key);
        cells.push({ label: `fixture ${fixture.role}`, candidates: [at], expected: "minecraft:air" });
      }
      return cells;
    }
    const [sx, sy, sz] = venue.size;
    const cells = [
      [0, 0, 0], [sx - 1, 0, sz - 1], [sx - 1, 0, 0], [0, 0, sz - 1],
      [Math.floor(sx / 2), 2, Math.floor(sz / 2)],
      [Math.floor(sx / 2), sy - 1, Math.floor(sz / 2)],
    ];
    return cells.map(cell => atKey(venue, cell));
  }

  async function inspectLayout(venue) {
    const d = dimension();
    if (venue.installation === "native-preinstalled") {
      const report = { loaded: true, complete: false, missing: [], mismatched: [], foreign: [], unloaded: [], cells: 0 };
      if (!(venue.probes?.length)) throw new Error(`${venue.title}の原作配置検証地点がありません。`);
      for (const probe of venue.probes) {
        const at = seatPoint(probe.location);
        const block = blockAt(d, at.x, at.y, at.z);
        const key = `${at.x},${at.y},${at.z}`;
        if (!block) { report.loaded = false; report.unloaded.push(key); continue; }
        report.cells++;
        if (!probe.blocks.includes(block.typeId)) report.mismatched.push({ key, actual: block.typeId, expected: probe.blocks.join("|") });
      }
      report.complete = report.loaded && report.mismatched.length === 0;
      return report;
    }
    const [, sy, sz] = venue.size;
    const plane = sy * sz;
    const legacy = new Set(venue.legacy_blocks ?? []);
    const report = { loaded: true, complete: false, missing: [], mismatched: [], foreign: [], unloaded: [], cells: 0 };
    let index = 0;
    for (const run of layouts.get(venue.id)) {
      const expected = expectedBlock(venue.layout.palette[run.paletteIndex]);
      for (let offset = 0; offset < run.count; offset++, index++) {
        const x = Math.floor(index / plane);
        const remainder = index - x * plane;
        const y = Math.floor(remainder / sz);
        const z = remainder - y * sz;
        const wx = venue.origin[0] + x;
        const wy = venue.origin[1] + y;
        const wz = venue.origin[2] + z;
        const block = blockAt(d, wx, wy, wz);
        if (!block) {
          report.loaded = false;
          report.unloaded.push(`${wx},${wy},${wz}`);
          return report;
        }
        report.cells++;
        if (expected.typeId === "minecraft:air") {
          if (!air(block)) {
            report.foreign.push({ key: `${wx},${wy},${wz}`, expected: expected.typeId, actual: block.typeId });
          }
        } else if (air(block)) {
          report.missing.push({ key: `${wx},${wy},${wz}`, expected: expected.typeId });
        } else if (block.typeId !== expected.typeId || !stateMatches(block, expected.states)) {
          report.mismatched.push({
            key: `${wx},${wy},${wz}`, expected: expected.typeId,
            expectedStates: expected.states, actual: block.typeId,
            legacy: legacy.has(block.typeId),
          });
        }
        if ((report.cells & 4095) === 0) await waitTicks(0);
      }
    }
    const expectedCells = venue.size[0] * venue.size[1] * venue.size[2];
    if (report.cells !== expectedCells) {
      throw new Error(`会場の検査セル数が不一致です: ${venue.id} ${report.cells}/${expectedCells}`);
    }
    report.complete = report.missing.length === 0 && report.mismatched.length === 0 && report.foreign.length === 0;
    return report;
  }

  async function waitForLoaded(venue, attempts = 80) {
    let unavailable = [];
    for (let remaining = attempts; remaining > 0; remaining--) {
      unavailable = [];
      for (const check of probeLocations(venue)) {
        const candidates = check.candidates ?? [check];
        const failures = [];
        let loaded = false;
        for (const at of candidates) {
          try {
            if (dimension().getBlock(at)) { loaded = true; break; }
            failures.push(`${at.x},${at.y},${at.z}=undefined`);
          } catch (error) {
            failures.push(`${at.x},${at.y},${at.z}=${error?.message ?? error}`);
          }
        }
        if (!loaded) unavailable.push({ check, candidates, message: `${check.label ?? "block"}: ${failures.join("|")}` });
      }
      if (unavailable.length === 0) return true;
      await waitTicks(10);
    }
    // A read-only command comparison distinguishes native chunk availability
    // from a Script API read failure in the actual server log.
    for (const missing of unavailable.slice(0, 3)) {
      const at = missing.candidates[0];
      const command = `testforblock ${at.x} ${at.y} ${at.z} ${missing.check.expected ?? "minecraft:air"}`;
      try {
        const result = dimension().runCommand(command);
        console.warn(`[JUNKET] chunk API/command probe ${venue.id}: ${command} => successCount=${result?.successCount ?? "unavailable"}`);
      } catch (error) {
        console.warn(`[JUNKET] chunk API/command probe ${venue.id}: ${command} => ${error?.message ?? error}`);
      }
    }
    throw new Error(`会場チャンクを読み込めません: ${venue.id} (${unavailable.length}地点: ${unavailable.slice(0, 5).map(value => value.message).join("; ")})`);
  }

  function repairAllowed(venue, report) {
    const d = dimension();
    const origin = blockAt(d, ...venue.origin);
    const originMatches = origin?.typeId === venue.origin_block;
    let marker = null;
    try {
      const stored = world.getDynamicProperty(originName(venue.id));
      if (typeof stored === "string") marker = JSON.parse(stored);
    } catch { marker = null; }
    const priorInstall = marker === 1 || marker?.verified === true;
    const legacy = new Set(venue.legacy_blocks ?? []);
    const nonLegacy = [
      ...report.mismatched.filter(item => !legacy.has(item.actual)),
      ...report.foreign.filter(item => !legacy.has(item.actual)),
    ];
    if (nonLegacy.length > 0) return { allowed: false, conflicts: nonLegacy };
    // Expected-air cells are the strongest protection against overwriting an
    // unrelated build. Only a venue that this pack already verified may retain
    // blocks from its previous layout in those cells.
    if (report.foreign.length > 0 && !priorInstall) {
      return { allowed: false, conflicts: report.foreign.slice(0, 5) };
    }
    // Mismatched non-air cells are accepted only as known legacy venue blocks
    // from a previous verified installation, never from an unmarked build.
    if (report.mismatched.length > 0 && !priorInstall) {
      return { allowed: false, conflicts: report.mismatched.slice(0, 5) };
    }
    if (!priorInstall && !originMatches && report.foreign.length > 0) {
      return { allowed: false, conflicts: report.foreign.slice(0, 5) };
    }
    return { allowed: true, conflicts: [] };
  }

  function genericFixtureMatches(id, fixture) {
    return findRoleEntities(id, fixture.role)
      .filter(entity => isManagedFixture(id, entity) && entity.typeId === fixture.type && withinFixtureLocation(entity, fixture)).length;
  }

  function verifyGenericEntities(id) {
    const venue = entry(id);
    if (venue.special_runtime) return true;
    const fixtures = expectedFixtures(id);
    for (const fixture of fixtures) {
      const actual = genericFixtureMatches(id, fixture);
      if (actual !== 1) {
        const candidates = findRoleEntities(id, fixture.role).map(entity => {
          try { return { id: entity.id, type: entity.typeId, location: entity.location,
            managed: isManagedFixture(id, entity), within: withinFixtureLocation(entity, fixture) }; }
          catch (error) { return { error: error?.message ?? String(error) }; }
        });
        console.warn(`[JUNKET] fixture mismatch: ${JSON.stringify({ id, role: fixture.role, actual,
          expected: { type: fixture.type, location: fixture.location }, candidates })}`);
        throw new Error(`${venue.title}の操作物が1個ではありません: ${fixture.role} (actual=${actual})`);
      }
    }
    const expectedControls = fixtures.filter(fixture => fixture.role.startsWith("control_")).length;
    if (entitiesWithTag(id, `junket:${id}:control`).filter(entity => isManagedFixture(id, entity)).length !== expectedControls + 1) {
      throw new Error(`${venue.title}の操作エンティティ数が不一致です。`);
    }
    const expectedDisplays = fixtures.filter(fixture => fixture.role.startsWith("display_")).length;
    if (entitiesWithTag(id, `junket:${id}:display`).filter(entity => isManagedFixture(id, entity)).length !== expectedDisplays) {
      throw new Error(`${venue.title}の表示エンティティ数が不一致です。`);
    }
    if (id === "hope") {
      const expected = Array.isArray(hopeCurrent?.state?.ai)
        ? hopeCurrent.state.ai.filter(value => value.alive !== false).length
        : HOPE_AI_TRADERS;
      const actual = entitiesWithTag(id, "junket:hope:ai").length;
      if (actual !== expected) {
        throw new Error(`${venue.title}の審判トレーダー数が不一致です: ${actual}/${expected}`);
      }
    }
    if (id === "walker") verifyWalkerPresentationEntities(walkerCurrent);
    return true;
  }

  async function verifySpecialEntities(id) {
    const venue = entry(id);
    if (!venue.special_runtime) return true;
    const provider = specialVenues[id];
    if (!provider || typeof provider.verify !== "function") {
      throw new Error(`${venue.title}の専用会場検証がありません。`);
    }
    await provider.verify();
    const reception = SPECIAL_RECEPTIONS[id];
    if (reception && entitiesWithTag(id, reception).length === 0) {
      throw new Error(`${venue.title}の受付が不足しています。`);
    }
    if (entitiesWithTag(id, "junket:return").length === 0) {
      throw new Error(`${venue.title}の帰還設備が不足しています。`);
    }
    return true;
  }

  async function verifyVenue(id) {
    verifyGenericEntities(id);
    await verifySpecialEntities(id);
    return true;
  }

  function safeEvacuate(player, id, error) {
    try {
      player.teleport(SAFE_RETURN, { dimension: dimension(), rotation: { x: 0, y: 0 } });
      tell(player, `${entry(id).title}の会場検証に失敗したため安全地点へ戻しました: ${error?.message ?? error}`);
    } catch (failure) {
      tell(player, `${entry(id).title}の会場検証に失敗し、安全地点へ戻せませんでした: ${failure?.message ?? failure}`);
    }
  }

  async function repairInternal(id) {
    const venue = entry(id);
    const d = dimension();
    let repaired = false;
    const [x, y, z] = venue.origin;
    const { origin: loadOrigin, end: loadEnd } = loadingBounds(venue);
    if (venue.installation === "native-preinstalled") {
      for (const axis of [0, 2]) { loadOrigin[axis] -= 16; loadEnd[axis] += 16; }
    }
    return sharedLoader.withArea({ dimension: d, bounds: [...loadOrigin, ...loadEnd], timeoutTicks: 7200 }, async () => {
      await waitForLoaded(venue);
      if (venue.installation === "native-preinstalled" && !loadedActors.has(id)) {
        const tag = venue.provider?.collection ?? managedTag(id);
        const readiness = await waitForFixtureActors({ wait: waitTicks, expectedCount: venue.fixtures.length,
          read: () => d.getEntities({ tags: [tag] }).filter(entity =>
            entity.typeId !== "minecraft:player" && insideFixtureScope(venue, entity))
            .map(observeExistingFixture)
            .map(entity => entity.id ?? `${entity.typeId}:${JSON.stringify(entity.location)}`) });
        loadedActors.add(id);
        console.log(`[JUNKET] fixture actors settled: ${JSON.stringify({ id, ...readiness })}`);
      }
      // A newly chosen arrival probe can occupy an old service sign (Ace).
      // Establish the bound replacement panel and retire only identity-proven
      // legacy pairs before requiring the unchanged source-building probes.
      // Unknown blocks/signs remain untouched and still fail those probes.
      const migrateServicesFirst = venue.installation === "native-preinstalled" &&
        venue.legacy_service_controls?.length > 0;
      if (migrateServicesFirst) await ensureServiceControls(world, system, venue);
      let report = await inspectLayout(venue);
      if (!report.loaded) {
        await waitForLoaded(venue, 10);
        report = await inspectLayout(venue);
      }
      if (!report.complete) {
        // Native deployment has already verified every structure voxel and
        // backed up the world. Runtime only checks its anchor probes; it must
        // never replace this building with the public substitute structure.
        if (venue.installation === "native-preinstalled") {
          throw new Error(`${venue.title}の原作建築を確認できません。自動上書きせず停止しました: ${report.mismatched.slice(0, 5).map(item => item.key).join(",")}`);
        }
        const permission = repairAllowed(venue, report);
        if (!permission.allowed) {
          const conflicts = permission.conflicts.slice(0, 5).map(item => `${item.key}=${item.actual}`);
          throw new Error(`${venue.title}の修復対象に想定外ブロックがあります: ${conflicts.join(", ")}`);
        }
        // Structure placement is not guaranteed to replace occupied cells on
        // every Bedrock build. Clear every already-authorized stale cell,
        // including a previous layout's block where the new layout expects a
        // different solid block, before placing the saved structure.
        for (const item of [...report.foreign, ...report.mismatched]) {
          const [x, y, z] = item.key.split(",").map(Number);
          const block = blockAt(d, x, y, z);
          if (!block) throw new Error(`修復対象のチャンクを読み込めません: ${item.key}`);
          block.setType("minecraft:air");
        }
        world.structureManager.place(venue.structure, d, point({ x, y, z }), { includeEntities: false });
        repaired = true;
        await waitTicks(20);
        await waitForLoaded(venue, 10);
        report = await inspectLayout(venue);
      }
      if (!report.complete) {
        const missing = report.missing.slice(0, 5).map(item => item.key);
        const wrong = report.mismatched.slice(0, 5).map(item => item.key);
        const foreign = report.foreign.slice(0, 5).map(item => item.key);
        throw new Error(`${venue.title}の構造検証に失敗しました: missing=${missing.join(",")} wrong=${wrong.join(",")} foreign=${foreign.join(",")}`);
      }

      applySpecialConfig(id);
      try {
        await ensure(id);
        if (!migrateServicesFirst) await ensureServiceControls(world, system, venue);
        await waitTicks(10);
        await verifyVenue(id);
      }
      catch (error) {
        if (venue.installation !== "native-preinstalled") throw error;
        // Entity queries can settle after chunk activation. Reconcile owned
        // fixtures once, then require the exact same strict verification.
        console.warn(`[JUNKET] fixture reconcile retry ${id}: ${error?.message ?? error}`);
        await ensure(id);
        if (!migrateServicesFirst) await ensureServiceControls(world, system, venue);
        await waitTicks(10);
        await verifyVenue(id);
      }
      world.setDynamicProperty(originName(id), JSON.stringify({
        schema: SCHEMA,
        repairVersion: REPAIR_VERSION,
        verified: true,
        layoutCells: report.cells,
        fixtures: venue.fixtures?.length ?? 0,
      }));
      if (id === "garden" && gardenCurrent) syncGardenDropFloors(gardenCurrent);
      if (id === "walker") {
        walkerSyncKey = "";
        walkerSyncDirty = true;
        walkerPresentationKey = "";
        walkerPresentationTick = 0;
        ensureWalkerPresentationEntities(walkerCurrent);
        if (walkerCurrent?.state) syncWalkerFloors(walkerCurrent);
      }
      if (id === "rabbit") {
        rabbitSyncKey = "";
        rabbitHumanKey = "";
        rabbitOniKey = "";
        rabbitLastResultOpen = false;
        if (rabbitCurrent?.state) syncRabbitFloors(rabbitCurrent);
        ensureRabbitCpuEntities(id, rabbitCurrent);
      }
      if (id === "hope") {
        hopeAiSyncKey = "";
        hopeAiMotion.clear();
        hopeMotionRound = null;
        hopeMotionPhase = null;
        hopeMotionTick = 0;
        ensureHopeAiEntities(id, hopeCurrent);
        if (hopeCurrent?.state) updateHopeAiMotion(id, hopeCurrent);
      }
      return { id, repaired, layoutCells: report.cells, entities: venue.fixtures?.length ?? 0 };
    });
  }

  function repair(id) {
    if (activeRepairs.has(id)) return activeRepairs.get(id);
    const promise = repairQueue.then(() => repairInternal(id)).finally(() => activeRepairs.delete(id));
    // Repairs requested by warps and bootstrap share one loader lifecycle.
    // A failure is returned to its caller without poisoning the next request.
    repairQueue = promise.catch(() => {});
    activeRepairs.set(id, promise);
    return promise;
  }

  function withLoaded(id, action) {
    const venue = entry(id);
    const { origin, end } = loadingBounds(venue);
    if (venue.installation === "native-preinstalled") {
      for (const axis of [0, 2]) { origin[axis] -= 16; end[axis] += 16; }
    }
    return sharedLoader.withArea({ dimension: dimension(), bounds: [...origin, ...end], timeoutTicks: 7200 },
      async context => { await waitForLoaded(venue); context.guard(); return action(context); });
  }

  function restoreOriginal(id, { guard = () => {}, afterRestore = async () => {} } = {}) {
    const venue = entry(id);
    if (venue.installation !== "native-preinstalled" || !venue.repair_structures?.length) {
      throw new Error(`${venue.title}: 原作復旧用structureがありません`);
    }
    const tiles = venue.repair_structures;
    const contains = (offset, size) => offset.length === 3 && size.length === 3 &&
      offset.every((value, axis) => Number.isInteger(value) && value >= 0 &&
        Number.isInteger(size[axis]) && size[axis] > 0 && value + size[axis] <= venue.size[axis]);
    const available = new Set(world.structureManager.getPackStructureIds());
    for (const tile of tiles) {
      if (!contains(tile.offset, tile.size) || !available.has(tile.id)) {
        throw new Error(`${id}: 復旧structure範囲/資産が不正です: ${tile.id}`);
      }
    }
    const work = repairQueue.then(() => withLoaded(id, async context => {
      const d = dimension();
      const check = () => { context.guard(); guard(); };
      check();
      let cells = 0;
      for (const tile of tiles) {
        check();
        const origin = tile.offset.map((offset, axis) => venue.origin[axis] + offset);
        world.structureManager.place(tile.id, d, seatPoint(origin), { includeEntities: false, includeBlocks: true });
        cells += tile.size.reduce((a, b) => a * b, 1);
        await context.wait(1);
      }
      for (const overlay of venue.block_overlays ?? []) {
        check();
        const at = Array.isArray(overlay.location) ? seatPoint(overlay.location) : overlay.location;
        const offset = [at.x, at.y, at.z].map((value, axis) => value - venue.origin[axis]);
        if (!contains(offset, [1, 1, 1])) throw new Error(`${id}: 復旧overlayが会場外です`);
        const block = d.getBlock(at);
        if (!block) throw new Error(`${id}: 復旧overlayが未読込です`);
        block.setType(overlay.block ?? overlay.name);
        let permutation = block.permutation;
        for (const [state, value] of Object.entries(overlay.states ?? {})) permutation = permutation.withState(state, value);
        if (Object.keys(overlay.states ?? {}).length) block.setPermutation(permutation);
      }
      // Reconcile damaged/duplicate actors and their metadata before an idle
      // transition reads them. Native removal queries can settle a tick later.
      await ensure(id);
      await context.wait(2);
      check();
      // Game boards use the existing idle transition after the source tiles;
      // no public substitute hall is placed in this path.
      update(id, null);
      await ensureServiceControls(world, system, venue);
      await afterRestore(id);
      await context.wait(10);
      check();
      await verifyVenue(id);
      const report = await inspectLayout(venue);
      if (!report.complete) throw new Error(`${id}: 原作復旧後の構造検証に失敗しました`);
      const result = { id, restored: true, structures: tiles.length, sourceCells: cells,
        fixtures: venue.fixtures.length, bounds: { origin: venue.origin, size: venue.size } };
      console.log(`[JUNKET RECOVERY] original structure restored: ${JSON.stringify(result)}`);
      return result;
    }));
    repairQueue = work.catch(() => {});
    return work;
  }

  function destination(id) {
    const venue = entry(id);
    return {
      location: point(venue.destination),
      dimension: dimension(),
      rotation: venue.rotation ?? 0,
      beforeTeleport: async () => { await repair(id); },
      afterTeleport: async player => {
        try {
          await ensure(id);
          await verifyVenue(id);
        } catch (error) {
          if (player) safeEvacuate(player, id, error);
          throw error;
        }
      },
    };
  }

  async function goto(id, player) {
    const target = destination(id);
    await target.beforeTeleport();
    player.teleport(target.location, {
      dimension: target.dimension, rotation: { x: 0, y: target.rotation },
    });
    await target.afterTeleport(player);
  }

  async function start(id, current) {
    const venue = entry(id);
    await ensure(id);
    const online = world.getAllPlayers();
    for (const [seat, playerId] of current.players.entries()) {
      const player = online.find(candidate => candidate.id === playerId);
      const at = venue.seats[seat] ?? venue.destination;
      player?.teleport(point(at), {
        dimension: dimension(), rotation: { x: 0, y: walkerSeatRotation(venue, seat) },
      });
    }
    update(id, current);
  }

  function end(id, current) {
    const venue = entry(id);
    update(id, null);
    const at = venue.return_point ?? venue.destination;
    for (const player of world.getAllPlayers?.() ?? []) {
      if (!current?.players?.includes(player.id)) continue;
      if (player.dimension?.id !== DIMENSION) continue;
      try { player.teleport(point(at), { dimension: dimension(), rotation: { x: 0, y: venue.rotation ?? 0 } }); }
      catch { /* A disconnected player is handled when they reconnect. */ }
    }
  }

  function updateFire(id, current) {
    const venue = entry(id);
    const online = world.getAllPlayers?.() ?? [];
    const names = (current?.players ?? []).map((playerId, seat) =>
      online.find(candidate => candidate.id === playerId)?.name ?? `P${seat + 1}`);
    const presentation = fireVenuePresentation(current?.state ?? null, names);
    for (const fixture of venue.fixtures ?? []) {
      if (fixture.role.startsWith("control_")) {
        const entity = findRoleEntities(id, fixture.role)[0];
        setVisualFrame(entity,
          presentation.cards[fixture.action]?.frame ?? FIRE_PROP_FRAME.available, "junket:frame", fixture);
        continue;
      }
      if (!fixture.role.startsWith("display_")) continue;
      const display = findRoleEntities(id, fixture.role)[0];
      if (!display) continue;
      if (fixture.role === "display_1") {
        display.nameTag = presentation.tvText;
        setVisualFrame(display, FIRE_PROP_FRAME.television, "junket:frame", fixture);
      } else if (fixture.role === "display_2" || fixture.role === "display_3") {
        const seat = fixture.role === "display_2" ? 0 : 1;
        display.nameTag = presentation.glassText[seat];
        setVisualFrame(display, presentation.glassFrames[seat], "junket:frame", fixture);
      } else {
        display.nameTag = presentation.executionText;
        setVisualFrame(display, FIRE_PROP_FRAME.television, "junket:frame", fixture);
      }
    }
    const reception = findRoleEntities(id, "reception")[0];
    if (reception) reception.nameTag = presentation.receptionText;
  }

  function updatePig(id, current) {
    const venue = entry(id);
    const online = world.getAllPlayers?.() ?? [];
    const names = (current?.players ?? []).map((playerId, seat) =>
      online.find(candidate => candidate.id === playerId)?.name ?? `P${seat + 1}`);
    const presentation = pigVenuePresentation(current?.state ?? null, names);
    for (const fixture of venue.fixtures ?? []) {
      const entity = findRoleEntities(id, fixture.role)[0];
      if (!entity) continue;
      if (fixture.role.startsWith("control_")) {
        const visual = presentation.cards[fixture.action] ?? presentation.targets[fixture.action];
        if (visual) {
          entity.nameTag = visual.text;
          setVisualFrame(entity, visual.frame, "junket:frame", fixture);
        }
        continue;
      }
      if (!fixture.role.startsWith("display_")) continue;
      const displayIndex = Number(/^display_(\d+)$/.exec(fixture.role)?.[1] ?? 0);
      if (displayIndex === 1) entity.nameTag = presentation.tvText;
      else if (displayIndex >= 2 && displayIndex <= 5) {
        entity.nameTag = presentation.seatTexts[displayIndex - 2];
      } else entity.nameTag = presentation.palaceText;
      setVisualFrame(entity, PIG_PROP_FRAME.television, "junket:frame", fixture);
    }
    const reception = findRoleEntities(id, "reception")[0];
    if (reception) reception.nameTag = presentation.receptionText;
  }

  function updateGrail(id, current) {
    const venue = entry(id);
    const online = world.getAllPlayers?.() ?? [];
    const names = (current?.players ?? []).map((playerId, seat) =>
      online.find(candidate => candidate.id === playerId)?.name ?? `${seat + 1}P`);
    const presentation = grailVenuePresentation(current?.state ?? null, names);
    for (const fixture of venue.fixtures ?? []) {
      const entity = findRoleEntities(id, fixture.role)[0];
      if (!entity) continue;
      if (fixture.role.startsWith("control_")) {
        const cup = /^grail_cup_([1-3])$/.exec(fixture.action);
        const potion = /^grail_potion_([1-3])$/.exec(fixture.action);
        if (cup) entity.nameTag = presentation.cupRack[Number(cup[1]) - 1];
        else if (potion) entity.nameTag = presentation.potionRack[Number(potion[1]) - 1];
        continue;
      }
      if (!fixture.role.startsWith("display_")) continue;
      if (fixture.role === "display_1") entity.nameTag = presentation.tv;
      else if (fixture.role === "display_2") entity.nameTag = presentation.instruction;
      else if (fixture.role === "display_3") entity.nameTag = presentation.result;
      else if (fixture.role === "display_4") {
        entity.nameTag = "聖杯棚\n本物の聖杯を1つ配置";
      } else if (fixture.role === "display_5") {
        entity.nameTag = "ポーション棚\n聖水を1つ配置";
      }
    }
    const reception = findRoleEntities(id, "reception")[0];
    if (reception) {
      const state = current?.state;
      reception.nameTag = `${venue.title}\n${state
        ? `第${state.round}/10R ${state.phase}`
        : "参加・操作・再戦"}`;
    }
  }

  function updateGarden(id, current) {
    const venue = entry(id);
    gardenCurrent = current;
    syncGardenDropFloors(current);
    const online = world.getAllPlayers?.() ?? [];
    const names = (current?.players ?? []).map((playerId, seat) =>
      online.find(candidate => candidate.id === playerId)?.name ?? `${seat + 1}P`);
    const presentation = gardenVenuePresentation(current?.state ?? null, names);
    for (const fixture of venue.fixtures ?? []) {
      if (!fixture.role.startsWith("display_")) continue;
      const display = findRoleEntities(id, fixture.role)[0];
      if (!display) continue;
      if (fixture.role === "display_1") display.nameTag = presentation.total;
      else if (fixture.role === "display_2") display.nameTag = presentation.chests[0];
      else if (fixture.role === "display_3") display.nameTag = presentation.chests[1];
      else if (fixture.role === "display_4") display.nameTag = presentation.valve;
    }
    const reception = findRoleEntities(id, "reception")[0];
    if (reception) reception.nameTag = `${venue.title}\n${presentation.reception}`;
  }

  function updateClock(id, current) {
    const venue = entry(id);
    syncClockDropFloors(current);
    syncClockNeedle(current);
    const state = current?.state;
    if (!state) return;
    const currentTime = `${String(state.hour).padStart(2, "0")}:${String(state.minute).padStart(2, "0")}`;
    const nextTime = `${String(state.nextHour).padStart(2, "0")}:${String(state.nextMinute).padStart(2, "0")}`;
    const selected = state.selectedLabels?.join("") || "なし";
    for (const fixture of venue.fixtures ?? []) {
      if (!fixture.role.startsWith("display_")) continue;
      const display = findRoleEntities(id, fixture.role)[0];
      if (!display) continue;
      if (fixture.role === "display_1") {
        display.nameTag = `時計盤  ${currentTime} → ${nextTime}`;
      } else if (fixture.role === "display_2") {
        display.nameTag = `区画 ${state.selected.length}/${state.requiredSectors}  確定 ${selected}\n`
          + `カーソル ${state.cursorLabel} / ${state.phase}`;
      }
    }
    const reception = findRoleEntities(id, "reception")[0];
    if (reception) reception.nameTag = `${venue.title}受付 / 参加・操作・再戦`;
  }

  function updateWalker(id, current) {
    const venue = entry(id);
    syncWalkerFloors(current);
    syncWalkerPresentation(current);
    const state = current?.state;
    if (!state) {
      const reception = findRoleEntities(id, "reception")[0];
      if (reception) reception.nameTag = `${venue.title}受付 / 参加・操作・再戦`;
      return;
    }
    const online = world.getAllPlayers?.() ?? [];
    const name = seat => online.find(candidate => candidate.id === current.players?.[seat])?.name
      ?? `${seat + 1}P`;
    const walkerName = name(state.walker);
    const breakerName = name(state.breaker);
    let first;
    let second;
    if (state.phase === "walk") {
      first = `第${state.round}ラウンド  ウォーカー ${walkerName}\n`
        + `移動 ${state.movedSteps?.[state.walker] ?? 0}/5  残り ${state.walkRemaining}t`;
      second = `現在床 ${state.positions[state.walker]}  爆弾候補 ${state.movedBomb}\n`
        + "1〜5マス移動して「移動確定」";
    } else if (state.phase === "bomb_setup") {
      first = `ブレイカー ${breakerName}  設置 ${state.bombCount ?? state.bombsPlaced ?? 0}/${state.bombQuota}\n`
        + `選択中の床 ${state.bombCursor}`;
      second = "前・次で対象を選び、決定で設置";
    } else if (state.phase === "dodge") {
      first = `回避 ${state.dodgeRemaining}t  ウォーカー ${walkerName}`;
      second = `爆弾 ${state.bombCount ?? state.bombsPlaced ?? 0}個  公開中`;
    } else if (state.phase === "fuse") {
      first = `起爆まで ${state.fuseRemaining}t`;
      second = "床の崩落に備えて移動";
    } else if (state.phase === "aftermath") {
      first = `判定 ${state.aftermathRemaining}t\n`
        + `水位 1P=${state.water?.[0] ? "没" : "安全"} 2P=${state.water?.[1] ? "没" : "安全"}`;
      second = state.winner === null ? "同時水没または継続"
        : `${state.winner + 1}P の勝ち`;
    } else {
      first = `ゲーム終了  勝者 ${state.winner === null ? "引き分け" : `${state.winner + 1}P`}`;
      second = "受付から再戦できます";
    }
    const displays = ["display_1", "display_2", "display_3"]
      .map(role => findRoleEntities(id, role)[0]);
    if (displays[0]) displays[0].nameTag = first;
    if (displays[1]) displays[1].nameTag = second;
    if (displays[2]) {
      displays[2].nameTag = `死床 ${state.deadFloors?.length ?? 0}  落下 ${state.water?.filter(Boolean).length ?? 0}\n`
        + `1P床 ${state.positions?.[0] ?? "-"} / 2P床 ${state.positions?.[1] ?? "-"}`;
    }
    const controlLabels = {
      walker_stop_1: "1P 移動確定", walker_stop_2: "2P 移動確定",
      bomb_prev_1: "1P 爆弾を前へ", bomb_prev_2: "2P 爆弾を前へ",
      bomb_next_1: "1P 爆弾を次へ", bomb_next_2: "2P 爆弾を次へ",
      bomb_confirm_1: "1P 爆弾を決定", bomb_confirm_2: "2P 爆弾を決定",
    };
    for (const fixture of venue.fixtures ?? []) {
      if (!fixture.role.startsWith("control_")) continue;
      const control = findRoleEntities(id, fixture.role)[0];
      if (control) control.nameTag = controlLabels[fixture.action] ?? fixture.name;
    }
    const reception = findRoleEntities(id, "reception")[0];
    if (reception) reception.nameTag = `${venue.title}\n${state.phase} / 参加・操作・再戦`;
  }

  function updateAuction(id, current) {
    const venue = entry(id);
    const state = current?.state;
    const displays = Array.from({ length: 8 }, (_, index) =>
      findRoleEntities(id, `display_${index + 1}`)[0]);
    const reception = findRoleEntities(id, "reception")[0];
    if (!state) {
      if (reception) reception.nameTag = `${venue.title}受付 / 参加・操作・再戦`;
      return;
    }
    const actor = Number.isInteger(state.actor) ? `${state.actor + 1}P` : "-";
    const last = state.last
      ? `${state.last.value ?? "なし"}${state.last.winner === null ? "（流札）" : ` / ${state.last.winner + 1}P`}`
      : "未公開";
    const used = seat => {
      const cards = state.selected?.[seat];
      if (Array.isArray(cards)) return cards.length ? cards.join("・") : "なし";
      return cards == null ? "なし" : `${cards}`;
    };
    if (displays[0]) {
      displays[0].nameTag = `第${state.set}セット 第${state.round}ラウンド  手番 ${actor}\n`
        + `落札 ${last}  ${state.phase}`;
    }
    if (displays[1]) {
      displays[1].nameTag = `ダイヤ A=${state.diamonds?.[0] ?? 0} B=${state.diamonds?.[1] ?? 0}  `
        + `セット内 A${state.roundWins?.[0] ?? 0}-B${state.roundWins?.[1] ?? 0}\n`
        + `総支払 ${(state.payment ?? []).map((value, seat) => `${seat + 1}P:${value}`).join(" ")}`;
    }
    if (displays[2]) {
      const shownBids = state.phase === "bid"
        ? "入札は全員の選択後に公開"
        : (state.last?.bids ?? []).map((value, seat) => `${seat + 1}P:${value}`).join(" ");
      displays[2].nameTag = `入札状況\n${shownBids}`;
    }
    if (displays[3]) displays[3].nameTag = "競売ダイヤ";
    for (let seat = 0; seat < 4; seat++) {
      if (displays[4 + seat]) {
        displays[4 + seat].nameTag = `${seat + 1}P 入札席\n使用済み ${used(seat)}`;
      }
    }
    for (const fixture of venue.fixtures ?? []) {
      const match = fixture.action && /^auction_bid_([1-4])_([1-4])$/.exec(fixture.action);
      if (!match) continue;
      const seat = Number(match[1]) - 1;
      const value = Number(match[2]);
      const control = findRoleEntities(id, fixture.role)[0];
      if (!control) continue;
      const cards = state.selected?.[seat];
      const consumed = Array.isArray(cards) ? cards.includes(value) : cards === value;
      const turn = state.phase === "bid" && state.actor === seat;
      control.nameTag = `${seat + 1}P 札${value}${consumed ? "（使用済み）" : turn ? "（あなたの手番）" : ""}`;
    }
    if (reception) {
      const status = state.phase === "finished"
        ? `終了 勝者 ${state.winnerTeam === null ? "引き分け" : `${state.winnerTeam + 1}チーム`}`
        : state.phase === "set_result" ? "セット終了・受付で次へ" : `第${state.round}R / 手番 ${actor}`;
      reception.nameTag = `${venue.title}受付 / 参加・操作・再戦\n${status}`;
    }
  }

  function updateLight(id, current) {
    const venue = entry(id);
    const state = current?.state;
    const displays = Array.from({ length: 14 }, (_, index) =>
      findRoleEntities(id, `display_${index + 1}`)[0]);
    const reception = findRoleEntities(id, "reception")[0];
    if (!state) {
      if (reception) reception.nameTag = `${venue.title}受付 / 参加・操作・再戦`;
      return;
    }
    const guardNames = ["鉄壁", "歓迎", "居眠り"];
    const touristNames = ["心臓", "槌", "松明"];
    const reveal = ["result", "finished"].includes(state.phase);
    const actor = Number.isInteger(state.actor) ? `${state.actor + 1}P` : "-";
    const clock = ticks => {
      const seconds = Math.max(0, Math.ceil((Number(ticks) || 0) / 20));
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor(seconds % 3600 / 60);
      const rest = seconds % 60;
      return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
    };
    const phaseText = state.phase === "intro" ? `点火演出 残り${clock(state.introRemaining)}`
      : state.phase === "guard_arrange"
        ? `${state.guardSetter + 1}Pが門番を配置 / 残り${clock(state.placementRemaining)}`
        : state.phase === "tourist_arrange"
          ? `${state.touristSetter + 1}Pが死者を配置 / 残り${clock(state.placementRemaining)}`
          : state.phase === "result" ? `判定表示 / 残り${clock(state.resultRemaining)}`
            : `終了 勝者 ${Number.isInteger(state.winner) ? `${state.winner + 1}P` : "引き分け"}`;
    if (displays[0]) {
      const seatText = seat => `♥${state.hearts?.[seat] ?? 0} 松明${state.torches?.[seat] ?? 0}/5 `
        + `酸素${clock(state.oxygen?.[seat])}${state.oxygenLack?.[seat] ? " 酸欠" : ""} `
        + `被傷${state.damage?.[seat] ?? 0}`;
      displays[0].nameTag = `1P ${seatText(0)}\n2P ${seatText(1)}`;
    }
    if (displays[1]) {
      displays[1].nameTag = `第${state.round}ラウンド  手番 ${actor}\n${phaseText}`;
    }
    if (displays[2]) {
      if (state.last?.slotResults?.length === 3) {
        const slots = state.last.slotResults.map(result =>
          `${result.slot + 1}枠 ${guardNames[result.guard]}×${touristNames[result.tourist]} `
          + `♥${result.hearts >= 0 ? "+" : ""}${result.hearts}`
          + `${result.torches ? ` 松明+${result.torches}` : ""}`).join("  ");
        const ignition = state.last.ignition?.failure === "oxygen_lack" ? "点火失敗（酸欠）"
          : state.last.ignition?.success ? `点火 酸素${state.last.ignition.halfCount}回半減` : "点火なし";
        displays[2].nameTag = `合計 ♥${state.last.heartsDelta >= 0 ? "+" : ""}${state.last.heartsDelta}`
          + ` 松明+${state.last.torchesDelta}\n${slots}\n${ignition}`;
      } else {
        displays[2].nameTag = "判定待ち\n配置完了後に3枠を一括判定します";
      }
    }
    for (let side = 0; side < 2; side++) {
      const role = side === state.guardSetter ? "門番" : "死者";
      const values = role === "門番" ? state.guards : state.tourists;
      const names = role === "門番" ? guardNames : touristNames;
      for (let slot = 0; slot < 3; slot++) {
        const display = displays[3 + side * 3 + slot];
        if (!display) continue;
        const visible = reveal && Number.isInteger(values?.[slot]) ? names[values[slot]] : null;
        display.nameTag = `${side + 1}P ${role}枠${slot + 1}\n`
          + `${visible ?? (values?.[slot] === null ? "未配置" : "配置済み")}`;
      }
    }
    for (let slot = 0; slot < 3; slot++) {
      const display = displays[9 + slot];
      if (!display) continue;
      const result = reveal ? state.last?.slotResults?.[slot] : null;
      display.nameTag = result
        ? `墓所${slot + 1}\n${guardNames[result.guard]}×${touristNames[result.tourist]}`
        : `墓所${slot + 1}\n開示待ち`;
    }
    for (let seat = 0; seat < 2; seat++) {
      const display = displays[12 + seat];
      if (!display) continue;
      const torches = state.torches?.[seat] ?? 0;
      const burn = torches === 5 ? ` 燃焼残り${clock(10000 - (state.torchBurn?.[seat] ?? 0))}` : "";
      display.nameTag = `ロウソク穴${seat + 1} / ${seat + 1}P\n`
        + `松明 ${torches}/5${burn}${state.oxygenLack?.[seat] ? " 点火不可" : ""}`;
    }
    for (const fixture of venue.fixtures ?? []) {
      const match = fixture.action && /^light_grave_([12])_([1-3])$/.exec(fixture.action);
      if (!match) continue;
      const seat = Number(match[1]) - 1;
      const slot = Number(match[2]) - 1;
      const control = findRoleEntities(id, fixture.role)[0];
      if (!control) continue;
      const role = seat === state.guardSetter ? "門番" : seat === state.touristSetter ? "死者" : null;
      const activePhase = role === "門番" ? "guard_arrange" : role === "死者" ? "tourist_arrange" : null;
      const active = activePhase === state.phase && state.actor === seat;
      const values = role === "門番" ? state.guards : role === "死者" ? state.tourists : null;
      const placed = values?.[slot] !== null && values?.[slot] !== undefined;
      control.nameTag = `${seat + 1}P ${role ?? "参加者"}枠${slot + 1}\n`
        + `${active ? placed ? "配置済み" : "押して配置" : "待機"}`;
    }
    if (reception) {
      reception.nameTag = `${venue.title}受付 / 参加・操作・再戦\n`
        + `第${state.round}R / ${state.phase} / 手番 ${actor}`;
    }
  }

  function updateMine(id, current) {
    const venue = entry(id);
    const state = current?.state;
    const displays = Array.from({ length: 9 }, (_, index) =>
      findRoleEntities(id, `display_${index + 1}`)[0]);
    const reception = findRoleEntities(id, "reception")[0];
    if (!state) {
      if (reception) reception.nameTag = `${venue.title}受付 / 参加・操作・再戦`;
      return;
    }
    const actor = Number.isInteger(state.actor) ? `${state.actor + 1}P` : "-";
    if (displays[0]) {
      displays[0].nameTag = `猛毒 / HP\n1P ${state.hp?.[0] ?? 0}  2P ${state.hp?.[1] ?? 0}`
        + `${Number.isInteger(state.last?.poison) ? `\n前回 猛毒 ${state.last.poison + 1}P` : ""}`;
    }
    if (displays[1]) {
      displays[1].nameTag = `インゴット / 採掘数\n`
        + `1P ${state.ingots?.[0] ?? 0} / ${state.mined?.[0] ?? 0}  `
        + `2P ${state.ingots?.[1] ?? 0} / ${state.mined?.[1] ?? 0}`;
    }
    if (displays[2]) {
      const action = state.phase === "arrange"
        ? "自分の岩を4回押して4種類のカードを伏せる"
        : state.phase === "mine" ? "自分の岩を2回押して2か所を採掘" : "判定中";
      displays[2].nameTag = `第${state.round ?? "-"} / 20ラウンド  手番 ${actor}\n${action}`;
    }
    for (let seat = 0; seat < 2; seat++) {
      if (displays[3 + seat * 2]) {
        displays[3 + seat * 2].nameTag = `${seat + 1}P 岩\n`
          + `${state.boardsReady?.[seat] ? "配置済み" : "4枚の配置待ち"}`;
      }
      if (displays[4 + seat * 2]) {
        displays[4 + seat * 2].nameTag = `${seat + 1}P インゴット\n${state.ingots?.[seat] ?? 0}`;
      }
    }
    for (let seat = 0; seat < 2; seat++) {
      if (displays[7 + seat]) {
        displays[7 + seat].nameTag = `${seat + 1}P ドリル\n`
          + `${state.phase === "mine" ? "採掘受付" : state.phase}`;
      }
    }
    for (const fixture of venue.fixtures ?? []) {
      const match = fixture.action && /^mine_rock_([12])_([1-4])$/.exec(fixture.action);
      if (!match) continue;
      const seat = Number(match[1]) - 1;
      const slot = Number(match[2]);
      const control = findRoleEntities(id, fixture.role)[0];
      if (!control) continue;
      const phaseLabel = state.phase === "arrange" ? "カードを伏せる"
        : state.phase === "mine" ? "採掘する岩" : state.phase;
      control.nameTag = `${seat + 1}P 岩${slot}\n${phaseLabel}`;
    }
    if (reception) {
      const status = state.phase === "finished"
        ? `終了 勝者 ${state.winner === null ? "引き分け" : `${state.winner + 1}P`}`
        : `第${state.round}R / ${state.phase} / 手番 ${actor}`;
      reception.nameTag = `${venue.title}受付 / 参加・操作・再戦\n${status}`;
    }
  }

  function hopeFloorCells(area) {
    if (!Array.isArray(area.cells)) throw new Error(`Hope床${area.pad}の物理セルがありません。`);
    return area.cells.map(cell => Array.isArray(cell)
      ? { x: cell[0], y: cell[1], z: cell[2] }
      : { x: cell.x, y: cell.y, z: cell.z });
  }

  function syncHopeDropFloors(current) {
    const venue = entry("hope");
    const state = current?.state;
    const hazards = new Set(state?.hazards ?? []);
    const open = state?.floorOpen === true;
    const padSymbols = Array.isArray(state?.padSymbols) && state.padSymbols.length === 16
      ? state.padSymbols : null;
    const d = dimension();
    for (const area of venue.drop_areas ?? []) {
      const symbol = padSymbols ? padSymbols[area.pad - 1] : area.symbol;
      const floor = HOPE_PAD_COLORS[symbol] ?? area.floor;
      const expected = open && hazards.has(symbol) ? "minecraft:air" : floor;
      for (const cell of hopeFloorCells(area)) {
        const block = blockAt(d, cell.x, cell.y, cell.z);
        if (!block) throw new Error(`Hopeの床チャンクを読み込めません: ${cell.x},${cell.y},${cell.z}`);
        if (block.typeId !== expected) block.setType(expected);
      }
    }
  }

  // game:hope/elec runs from time 5820 through 5800. At that point the local
  // drop phase (which starts at the source timeup 5851 and ends at the source
  // fall 5766) has 54..34 ticks left. Keep the window explicit so a restored
  // state reproduces the same effects without relying on wall-clock time.
  const HOPE_ELEC_MAX_REMAINING = 54;
  const HOPE_ELEC_MIN_REMAINING = 34;

  function hopePlayerOnHazard(state, seat) {
    if (state?.alive?.[seat] === false) return false;
    const symbol = state?.choices?.[seat]?.symbol;
    return Number.isInteger(symbol) && (state?.hazards ?? []).includes(symbol);
  }

  function applyHopeElectricity(player) {
    // Source elec.mcfunction: Slowness/Blindness level 100 for 15 seconds,
    // jump strength 0, 0.01 stalagmite-equivalent damage, yellow dust and a
    // firework blast. Bedrock has no dust color parameter on all clients, so
    // the yellow sparkler emitter is the closest bundled vanilla effect.
    try {
      player.addEffect?.("slowness", 300, { amplifier: 99, showParticles: false });
      player.addEffect?.("blindness", 300, { amplifier: 99, showParticles: false });
    } catch { /* A dead or disconnected participant is retried next tick. */ }
    try {
      player.runCommand?.("attribute @s minecraft:jump_strength base set 0");
    } catch { /* Attribute commands are unavailable on old engine builds. */ }
    try {
      player.applyDamage?.(0.01, { cause: "entityAttack" });
    } catch { /* The source damage is intentionally nonlethal per tick. */ }
    try {
      player.playSound?.("entity.firework_rocket.blast");
    } catch { /* Optional sound. */ }
    try {
      dimension().spawnParticle?.("minecraft:sparkler_emitter", {
        x: player.location.x,
        y: player.location.y + 1,
        z: player.location.z,
      });
    } catch { /* Particles are best effort while chunks reload. */ }
  }

  function resetHopeElectricity(player) {
    try {
      player.removeEffect?.("slowness");
      player.removeEffect?.("blindness");
    } catch { /* Disconnected participant. */ }
    try {
      // lose.mcfunction explicitly restores the base jump strength to 0.4.
      player.runCommand?.("attribute @s minecraft:jump_strength base set 0.4");
    } catch { /* Attribute commands are unavailable on old engine builds. */ }
  }

  function syncHopeElectricity(current) {
    const state = current?.state;
    if (!state || state.phase !== "drop") return;
    const remaining = Number(state.phaseRemaining);
    if (!Number.isFinite(remaining) ||
        remaining < HOPE_ELEC_MIN_REMAINING || remaining > HOPE_ELEC_MAX_REMAINING) {
      return;
    }
    const online = world.getAllPlayers?.() ?? [];
    for (let seat = 0; seat < (current.players?.length ?? 0); seat++) {
      if (!hopePlayerOnHazard(state, seat)) continue;
      const player = online.find(candidate => candidate.id === current.players[seat]);
      if (!player || player.dimension?.id !== DIMENSION) continue;
      applyHopeElectricity(player);
    }
  }

  function hopeAiPoint(venue, aiId, pad) {
    const spawn = (venue.hope_ai_spawns ?? []).find(value => value.id === aiId);
    const floor = Number.isInteger(pad)
      ? (venue.hope_pads ?? []).find(value => value.pad === pad) : null;
    if (floor && Number.isFinite(floor.center_x) && Number.isFinite(floor.center_z)) {
      // The source judge marker is two blocks south of the floor display
      // centre. Keeping that offset prevents traders from spawning inside the
      // floor interaction entity and the player's standing cell.
      return {
        x: floor.center_x,
        y: floor.center_y ?? floor.y,
        z: floor.center_z + 2,
      };
    }
    if (!spawn) return null;
    return { x: spawn.x, y: spawn.y, z: spawn.z };
  }

  function ensureHopeAiEntities(id, current = null, { resetPositions = false } = {}) {
    if (id !== "hope") return;
    const venue = entry(id);
    const spawns = venue.hope_ai_spawns ?? [];
    if (spawns.length !== HOPE_AI_TRADERS) {
      throw new Error(`HopeのAI出現位置が${HOPE_AI_TRADERS}件ではありません。`);
    }
    const saved = Array.isArray(current?.state?.ai) ? current.state.ai : null;
    for (const spawn of spawns) {
      const stateAi = saved?.find(value => value.id === spawn.id);
      const matches = entitiesWithTag(id, `junket:hope:ai:${spawn.id}`)
        .filter(entity => [HOPE_JUDGE_TYPE, "minecraft:wandering_trader"].includes(entity.typeId) &&
          entity.hasTag("junket:hope:ai"));
      let entity = matches.find(candidate => candidate.typeId === HOPE_JUDGE_TYPE) ?? null;
      // Worlds deployed before the durable judge entity existed still contain
      // vanilla wandering traders. Remove those tagged traders and replace
      // them with the persistent type instead of accepting a despawn-prone
      // entity because only its tags look valid.
      for (const extra of matches) {
        if (extra !== entity) extra.remove();
      }
      // walk.mcfunction kills a trader that resolves on a hope-out judge. The
      // state keeps alive:false so chunk reloads cannot recreate that trader.
      if (stateAi?.alive === false) {
        entity?.remove();
        continue;
      }
      if (!entity) {
        const at = hopeAiPoint(venue, spawn.id, stateAi?.pad ?? spawn.pad);
        if (!at) throw new Error(`HopeのAI ${spawn.id} の出現位置がありません。`);
        entity = dimension().spawnEntity(HOPE_JUDGE_TYPE, at);
      } else if (resetPositions) {
        const at = hopeAiPoint(venue, spawn.id, stateAi?.pad ?? spawn.pad);
        if (!at) throw new Error(`HopeのAI ${spawn.id} の再配置先がありません。`);
        entity.teleport(at, { dimension: dimension() });
      }
      entity.addTag("junket:hope:ai");
      entity.addTag(`junket:hope:ai:${spawn.id}`);
      entity.addTag(managedTag(id));
      entity.setDynamicProperty?.("junket:venue_id", id);
      // The source's traders are silent and unnamed. The custom judge keeps
      // the trader appearance without trader UI, despawn timer or AI trading.
      entity.nameTag = "";
      entity.setDynamicProperty?.("junket:hope_ai_id", spawn.id);
      // prepare/tonext assign the loser role by tag. Persist it both as a tag
      // (for vanilla-style inspection) and as a dynamic property (restorable
      // even when the tag list is rebuilt).
      if (stateAi?.loser) entity.addTag("junket:hope:loser");
      else entity.removeTag?.("junket:hope:loser");
      entity.setDynamicProperty?.("junket:hope_ai_loser", stateAi?.loser === true);
    }
  }

  function hopeAiNoise(value) {
    let mixed = Number(value) >>> 0;
    mixed ^= mixed >>> 16;
    mixed = Math.imul(mixed, 0x7feb352d);
    mixed ^= mixed >>> 15;
    mixed = Math.imul(mixed, 0x846ca68b);
    mixed ^= mixed >>> 16;
    return (mixed >>> 0) / 0x100000000;
  }

  function hopeFaceJudge(entity, center) {
    try {
      const dx = center.x - entity.location.x;
      const dz = center.z - entity.location.z;
      if (Math.hypot(dx, dz) < 0.01) return;
      entity.setRotation?.({ x: 0, y: Math.atan2(dx, dz) * 180 / Math.PI });
    } catch { /* Optional on older Bedrock builds. */ }
  }

  function hopeDistance3d(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
  }

  // walk.mcfunction reads the nearest judge of the role selected by the
  // trader: a normal trader follows a !hope-out judge, while a hope-loser
  // trader follows an hope-out judge. The source AI is a physical trader, so
  // its facing target must be another trader rather than the center of its own
  // assigned pad. Keep the pad center only as the documented fallback when a
  // role has no other physical entity (for example while a chunk is loading).
  function hopeAiRoleTarget(id, state, spawnId, loser, from) {
    let best = null;
    let bestDistance = Infinity;
    for (const candidate of state?.ai ?? []) {
      if (candidate.id === spawnId || candidate.alive === false ||
          candidate.loser !== loser) continue;
      const entity = entitiesWithTag(id, `junket:hope:ai:${candidate.id}`)[0];
      if (!entity) continue;
      const point = {
        x: entity.location.x,
        y: entity.location.y,
        z: entity.location.z,
      };
      const distance = from ? Math.hypot(
        point.x - from.x, point.y - from.y, point.z - from.z) : 0;
      // The role target itself is the source's nearest matching judge. The
      // distance check below is only used for the walk range; it must not
      // prevent a valid role target from being selected.
      if (distance < bestDistance) {
        best = { entity, point, distance };
        bestDistance = distance;
      }
    }
    return best;
  }

  function hopeApplyJudgeSlowness(entity, center, aiId) {
    try {
      const distance = hopeDistance3d(entity.location, center);
      if (distance > 1.5) return;
      if (typeof entity.addEffect === "function") {
        entity.addEffect("slowness", 20, { amplifier: 9, showParticles: false });
        return;
      }
      // walk.mcfunction applies Slowness to the trader itself, never to a
      // player. Fall back to the entity command when the script API lacks it.
      dimension().runCommand(`effect @e[tag=junket:hope:ai:${aiId}] slowness 1 10 true`);
    } catch { /* Effect application is best effort during chunk recovery. */ }
  }

  function updateHopeAiMotion(id, current) {
    if (id !== "hope") return;
    const venue = entry(id);
    const state = current?.state;
    if (!state) {
      ensureHopeAiEntities(id, current);
      hopeAiMotion.clear();
      hopeMotionRound = null;
      hopeMotionPhase = null;
      hopeMotionTick = 0;
      return;
    }
    const roundChanged = hopeMotionRound !== state.round;
    const phaseChanged = hopeMotionPhase !== state.phase;
    if (roundChanged || phaseChanged) {
      hopeAiMotion.clear();
      hopeMotionRound = state.round;
      hopeMotionPhase = state.phase;
      hopeMotionTick = 0;
    }
    hopeMotionTick++;
    const syncKey = `${state.round}:${state.phase}:${(state.ai ?? [])
      .map(value => `${value.id}:${value.pad}:${value.alive === false ? 0 : 1}:${value.loser ? 1 : 0}`)
      .join(",")}`;
    if (hopeAiSyncKey !== syncKey || hopeMotionTick % 20 === 0 || roundChanged) {
      ensureHopeAiEntities(id, current, { resetPositions: roundChanged });
      hopeAiSyncKey = syncKey;
    }
    // Source timeup.mcfunction applies NoAI:1b at 5851 in rounds 1-3 and at
    // 5801 in rounds 4-10, i.e. after the complete 99-tick reveal. The
    // walk.mcfunction start values (5950 / 5880) gate its custom follow branch,
    // not the trader's movement, so motion stays enabled through reveal and
    // stops when drop starts.
    const moving = hopeAiMotionActive(state);
    for (const spawn of venue.hope_ai_spawns ?? []) {
      const entity = entitiesWithTag(id, `junket:hope:ai:${spawn.id}`)[0];
      if (!entity) continue;
      const stateAi = (state.ai ?? []).find(value => value.id === spawn.id);
      if (stateAi?.alive === false) continue;
      const assignedPad = stateAi?.pad ?? spawn.pad;
      const center = hopeAiPoint(venue, spawn.id, assignedPad);
      if (!center) throw new Error(`HopeのAI ${spawn.id} の移動先がありません。`);
      const roleTarget = hopeAiRoleTarget(
        id, state, spawn.id, stateAi?.loser === true, entity.location);
      const facePoint = roleTarget?.point ?? center;
      let motion = hopeAiMotion.get(spawn.id);
      if (!motion || motion.round !== state.round) {
        motion = { round: state.round, pad: assignedPad, turn: 0, expires: 0, target: null, stop: null };
        hopeAiMotion.set(spawn.id, motion);
      } else if (motion.pad !== assignedPad) {
        // A judge may be reassigned to another floor without changing the
        // round. Drop the previous floor's target immediately so the trader
        // walks to the new source judge marker instead of finishing old motion.
        motion.pad = assignedPad;
        motion.target = null;
        motion.expires = 0;
      }
      if (!moving) {
        const stop = motion.stop ?? {
          x: entity.location.x, y: center.y, z: entity.location.z,
        };
        motion.stop = stop;
        try {
          if (Math.hypot(stop.x - entity.location.x, stop.z - entity.location.z) > 0.001) {
            entity.teleport(stop, { dimension: dimension() });
          }
        } catch { /* The trader is retried when its chunk ticks again. */ }
        continue;
      }
      motion.stop = null;
      // Once the trader is within the source walk range it moves toward the
      // selected physical judge. Outside that range the vanilla trader's
      // wandering behavior is represented by the deterministic pad-local
      // fallback below, but facing still uses the role target.
      const targetDistance = roleTarget
        ? hopeDistance3d(facePoint, entity.location)
        : Infinity;
      let roleLocked = false;
      if (roleTarget && targetDistance <= 1.5) {
        hopeApplyJudgeSlowness(entity, facePoint, spawn.id);
      }
      if (roleTarget && targetDistance >= 1.3 && targetDistance <= 3) {
        // Two judges can select each other as role targets and chase forever.
        // Clamp the walk target to the assigned floor so a reassigned trader
        // remains at its source judge marker while still facing its role.
        const dx = facePoint.x - center.x;
        const dz = facePoint.z - center.z;
        const distance = Math.hypot(dx, dz);
        const scale = distance > 1.4 ? 1.4 / distance : 1;
        motion.target = {
          x: center.x + dx * scale,
          y: center.y,
          z: center.z + dz * scale,
        };
        roleLocked = true;
        // Face from the pre-step position. The visible facing must match the
        // source walk tick's target even when the clamped step changes x/z.
        hopeFaceJudge(entity, facePoint);
      }
      // Source traders keep their vanilla wander even when another judge is
      // closer than 1.3 blocks. The previous early return froze every trader
      // that shared a reused safe pad for the rest of the choose phase.
      if (!roleLocked && (!motion.target || hopeMotionTick >= motion.expires)) {
        const salt = Math.imul(state.round + 1, 0x9e3779b1) ^
          Math.imul(spawn.id, 0x85ebca6b) ^
          Math.imul(motion.turn + 1, 0xc2b2ae35);
        const angle = hopeAiNoise(salt) * Math.PI * 2;
        const radius = 0.35 + hopeAiNoise(salt ^ 0x68bc21eb) * 1.15;
        motion.target = {
          x: center.x + Math.cos(angle) * radius,
          y: center.y,
          z: center.z + Math.sin(angle) * radius,
        };
        motion.turn++;
        motion.expires = hopeMotionTick + 10 + Math.floor(hopeAiNoise(salt) * 13);
      }
      try {
        const dx = motion.target.x - entity.location.x;
        const dz = motion.target.z - entity.location.z;
        const distance = Math.hypot(dx, dz);
        if (distance > 0.04) {
          const step = Math.min(0.13, distance);
          const at = {
            x: entity.location.x + dx / distance * step,
            y: center.y,
            z: entity.location.z + dz / distance * step,
          };
          try {
            entity.teleport(at, { dimension: dimension(), rotation: { x: 0, y: 0 } });
          } catch {
            entity.teleport(at, { dimension: dimension() });
          }
        }
      } catch {
        // A trader that is between ticking chunks is retried on the next pass.
      }
    }
  }

  let hopeDamageNoticeTick = 0;
  function damageHopeContact(id, current) {
    const state = current?.state;
    if (!state || !["choose", "reveal", "drop"].includes(state.phase)) return;
    hopeDamageNoticeTick++;
    const traders = entitiesWithTag(id, "junket:hope:ai");
    if (!traders.length) return;
    const online = world.getAllPlayers?.() ?? [];
    for (let seat = 0; seat < (current.players?.length ?? 0); seat++) {
      if (state.alive?.[seat] === false) continue;
      const player = online.find(candidate => candidate.id === current.players[seat]);
      if (!player || player.dimension?.id !== DIMENSION) continue;
      let nearest = null;
      for (const entity of traders) {
        try {
          const distance = Math.hypot(
            player.location.x - entity.location.x,
            player.location.y - entity.location.y,
            player.location.z - entity.location.z,
          );
          if (!nearest || distance < nearest.distance) nearest = { entity, distance };
        } catch {
          // Ignore an unloaded trader until its chunk is available again.
        }
      }
      if (!nearest) continue;
      if (nearest.distance > 1.05) continue;
      try {
        player.applyDamage?.(1, {
          cause: "entityAttack", damagingEntity: nearest.entity,
        });
      } catch { /* A spectator or another damage-immune player. */ }
      if (hopeDamageNoticeTick % 20 === 0) {
        player.onScreenDisplay?.setActionBar("審判トレーダーに接触しています。離れてください。");
      }
    }
  }

  function hopeTeleportObserver(player, spectator, message) {
    if (!spectator) return false;
    player.teleport({ x: spectator.x, y: spectator.y, z: spectator.z }, {
      dimension: dimension(), rotation: { x: 0, y: 90 },
    });
    try {
      player.addEffect?.("invisibility", 1000000, {
        amplifier: 0, showParticles: false,
      });
    } catch { /* A spectator without effects can still watch. */ }
    try { player.addTag?.(HOPE_OBSERVER_TAG); } catch { /* Best effort. */ }
    player.onScreenDisplay?.setActionBar(message);
    return true;
  }

  function hopeTeleportEliminated(player, lose) {
    if (!lose) return false;
    clearHopePlayerStatus(player);
    player.teleport({ x: lose.x, y: lose.y, z: lose.z }, {
      dimension: dimension(), rotation: { x: 0, y: 90 },
    });
    return true;
  }

  function updateHopeWarp(id, current) {
    const venue = entry(id);
    const warp = venue.hope_warp;
    const spectator = venue.hope_spectator ?? venue.points?.spectator;
    if (!warp || !spectator) return;
    const online = world.getAllPlayers?.() ?? [];
    for (const player of online) {
      if (hopeWarped.has(player.id)) continue;
      let distance = Infinity;
      try {
        distance = Math.hypot(
          player.location.x - warp.x,
          player.location.y - warp.y,
          player.location.z - warp.z,
        );
      } catch { continue; }
      if (distance > 0.8) continue;
      if (hopeTeleportObserver(player, spectator, "観戦部屋へ移動しました")) {
        hopeWarped.add(player.id);
      }
    }
  }

  function updateHope(id, current) {
    const venue = entry(id);
    const previous = hopeCurrent;
    hopeCurrent = current;
    const state = current?.state;
    const online = world.getAllPlayers?.() ?? [];
    const freshGame = Boolean(state) && state.round === 1 &&
      state.phase === "choose" && state.last === null;
    if (!state || (freshGame && !hopeFreshGameReset)) {
      hopeTeleportedRound = -1;
      hopeEliminated.clear();
      hopeWarped.clear();
      hopeAiMotion.clear();
      hopeMotionRound = null;
      hopeMotionPhase = null;
      hopeMotionTick = 0;
      hopeAiSyncKey = "";
      hopeFreshGameReset = Boolean(state);
      hopeFinishedCleanupKey = "";
      hopeFinishedCleaned.clear();
      // A game can disappear without ever reaching its finished phase (abort,
      // deleted state or a failed restart restore). The source's observer
      // invisibility must not leak into the rest of the world in that case.
      if (!state || freshGame) cleanupHopePlayers(previous?.players, online);
      if (!state) hopeEliminated.clear();
      if (freshGame) {
        hopeEliminated.clear();
        restoreHopeApparatus();
      }
    } else if (!freshGame) {
      // Allow the next round-one choose phase to initialise a new game again
      // without resetting motion on every tick of the current one.
      hopeFreshGameReset = false;
    }
    syncHopeDropFloors(current);
    syncHopeElectricity(current);
    updateHopeAiMotion(id, current);
    damageHopeContact(id, current);
    updateHopeWarp(id, current);
    const names = (current?.players ?? []).map((playerId, seat) =>
      online.find(candidate => candidate.id === playerId)?.name ?? `P${seat + 1}`);
    const revealed = state && ["reveal", "drop", "result", "finished"].includes(state.phase);
    const hazards = revealed ? (state.hazards ?? []) : [];
    const hazardText = hazards.length
      ? hazards.map(value => HOPE_SYMBOL_NAMES[value]).join("・") : "未公開";
    for (const fixture of venue.fixtures ?? []) {
      if (!fixture.role.startsWith("control_")) continue;
      const entity = findRoleEntities(id, fixture.role)[0];
      if (!entity) continue;
      if (/^floor_\d+$/.test(fixture.action ?? "")) {
        const pad = Number(String(fixture.action).replace("floor_", ""));
        const symbol = Array.isArray(state?.padSymbols) && state.padSymbols.length === 16
          ? state.padSymbols[pad - 1] : fixture.properties?.["junket:hope_symbol"];
        const name = HOPE_SYMBOL_NAMES[symbol] ?? "不明";
        entity.nameTag = `床${pad} ${name}${hazards.includes(symbol) ? "【危険】" : ""}`;
        // A private source venue keeps the original 4x4 floor mesh and uses
        // a separate invisible interaction anchor. Animate its explicit
        // source role, never another floor selected by shared source tags.
        const visual = fixture.visual_source_role
          ? findRoleEntities(id, fixture.visual_source_role)[0] : entity;
        // fall.mcfunction rotates hazard floors; tonext/end restore yaw 0.
        // The same control and source actor survive every round.
        try {
          visual?.setRotation?.({
            x: 0,
            y: state?.floorOpen === true && hazards.includes(symbol) ? 15 : 0,
          });
        } catch { /* Rotation is optional while an entity is reloading. */ }
      } else {
        entity.nameTag = fixture.name;
      }
    }
    const displays = ["display_1", "display_2", "display_3"]
      .map(role => findRoleEntities(id, role)[0]);
    if (displays[0]) {
      displays[0].nameTag = `第${state?.round ?? "-"}/10ラウンド\n危険図柄 ${hazardText}`;
    }
    if (displays[1]) {
      displays[1].nameTag = state
        ? `生存 ${state.alive.map((alive, seat) => alive ? names[seat] : `${names[seat]}(脱落)`).join(" / ")}`
        : "参加者待ち";
    }
    if (displays[2]) {
      displays[2].nameTag = state
        ? `${state.phase}${state.phaseRemaining ? ` 残り${state.phaseRemaining}t` : ""}\n床開放 ${state.floorOpen ? "あり" : "なし"}`
        : "受付で参加";
    }
    const reception = findRoleEntities(id, "reception")[0];
    if (reception) {
      reception.nameTag = `${venue.title}\n${state ? `${state.phase} / ${state.round}/10` : "参加・操作・再戦"}`;
    }
    if (state?.phase === "finished") {
      const cleanupKey = `${current.token ?? ""}|finished`;
      if (hopeFinishedCleanupKey !== cleanupKey) {
        hopeFinishedCleanupKey = cleanupKey;
        hopeFinishedCleaned.clear();
      }
      // A participant or observer may have been offline when the game ended.
      // Clear each newly connected player once, and keep the unfinished IDs
      // pending so their later reconnect cannot retain invisibility or tags.
      for (const player of online) {
        if (hopeFinishedCleaned.has(player.id)) continue;
        let observer = false;
        try { observer = player.hasTag?.(HOPE_OBSERVER_TAG) === true; } catch { /* Disconnected. */ }
        if (!observer && !current.players?.includes(player.id)) continue;
        clearHopePlayerStatus(player);
        hopeFinishedCleaned.add(player.id);
      }
    }
    if (!state) return;
    if (state.phase !== "finished") {
      for (const playerId of current.players ?? []) {
        const player = online.find(candidate => candidate.id === playerId);
        try { player?.addTag?.("hope-player"); } catch { /* Disconnected participant. */ }
      }
    }
    if (state.phase === "reveal" && hopeTeleportedRound !== state.round) {
      for (let seat = 0; seat < state.choices.length; seat++) {
        const choice = state.choices[seat];
        if (!choice?.pad) continue;
        const pad = (venue.hope_pads ?? []).find(value => value.pad === choice.pad);
        const player = online.find(candidate => candidate.id === current.players?.[seat]);
        if (!pad || !player) continue;
        player.teleport({
          x: Number.isFinite(pad.center_x) ? pad.center_x : pad.x + 0.5,
          y: pad.center_y ?? pad.y,
          z: Number.isFinite(pad.center_z) ? pad.center_z : pad.z + 0.5,
        }, {
          dimension: dimension(), rotation: { x: 0, y: 0 },
        });
      }
      hopeTeleportedRound = state.round;
    }
    if (state.phase === "drop" && state.phaseRemaining % 20 === 0) {
      for (const player of online.filter(candidate => current.players?.includes(candidate.id))) {
        try { player.playSound?.("entity.firework_rocket.blast"); } catch { /* Optional sound. */ }
        player.onScreenDisplay?.setActionBar("危険図柄の床が落下します");
      }
    }
    if (["result", "finished"].includes(state.phase) && state.last) {
      const lose = venue.hope_lose;
      for (const seat of state.last.eliminated ?? []) {
        const playerId = current.players?.[seat];
        const player = online.find(candidate => candidate.id === playerId);
        if (!player || hopeEliminated.has(playerId)) continue;
        hopeEliminated.add(playerId);
        hopeTeleportEliminated(player, lose);
        player.onScreenDisplay?.setTitle("脱落", {
          subtitle: "危険図柄の床が落下しました",
          stayDuration: 50, fadeInDuration: 5, fadeOutDuration: 10,
        });
        try { player.playSound?.("entity.player.death"); } catch { /* Optional sound. */ }
      }
    }
  }

  function updateState(id, current) {
    const venue = entry(id);
    if (venue.special_runtime) return;
    if (id === "hope") {
      updateHope(id, current);
      return;
    }
    if (id === "fire") {
      updateFire(id, current);
      return;
    }
    if (id === "pig") {
      updatePig(id, current);
      return;
    }
    if (id === "grail") {
      updateGrail(id, current);
      return;
    }
    if (id === "garden") {
      updateGarden(id, current);
      return;
    }
    if (id === "clock") {
      updateClock(id, current);
      return;
    }
    if (id === "walker") {
      walkerCurrent = current;
      updateWalker(id, current);
      return;
    }
    if (id === "auction") {
      updateAuction(id, current);
      return;
    }
    if (id === "light") {
      updateLight(id, current);
      return;
    }
    if (id === "mine") {
      updateMine(id, current);
      return;
    }
    if (id === "rabbit") {
      updateRabbit(id, current);
      return;
    }
    const state = current?.state;
    let status;
    if (!current) status = "参加者待ち";
    else if (state?.phase === "finished") status = "ゲーム終了・再戦受付";
    else if (state) status = `進行中: ${state.phase} / ${state.round ?? ""}`;
    else status = `${current.players.length}人参加済み`;
    for (const fixture of venue.fixtures ?? []) {
      if (!fixture.role.startsWith("display_")) continue;
      const display = findRoleEntities(id, fixture.role)[0];
      if (display) display.nameTag = `${fixture.name}\n${status}`;
    }
    const reception = findRoleEntities(id, "reception")[0];
    if (reception) reception.nameTag = `${venue.title}\n${status}`;
  }

  function update(id, current) {
    try { return updateState(id, current); }
    finally {
      const venue = entry(id);
      if (!venue.special_runtime) applyVenuePresentation(venue, value => findRoleEntities(id, value.role));
    }
  }

  async function installAll(warp, special = {}) {
    if (installPromise) return installPromise;
    specialVenues = special;
    installPromise = (async () => {
      const completed = [];
      const failures = [];
      const native = definitions.every(venue => venue.installation === "native-preinstalled");
      // Avoid immediately reusing the neighboring chunks of the area that
      // just unloaded. Coordinates and catalog order are unchanged.
      const order = native ? [...definitions.filter((_, index) => index % 2 === 0),
        ...definitions.filter((_, index) => index % 2 === 1)] : definitions;
      for (const venue of order) {
        try {
          const result = await repair(venue.id);
          completed.push(result);
          if (native) console.log(`[JUNKET ORIGINAL] runtime fixture progress: ${JSON.stringify({ id: venue.id, status: "completed", result })}`);
        } catch (error) {
          failures.push({ id: venue.id, message: error?.message ?? String(error) });
          if (native) console.log(`[JUNKET ORIGINAL] runtime fixture progress: ${JSON.stringify({ id: venue.id, status: "failed", message: error?.message ?? String(error) })}`);
          console.warn(`[JUNKET] venue repair failed: ${venue.id}: ${error?.message ?? error}`);
        }
      }
      let warpOk = false;
      try {
        warpOk = await warp.installControls() !== false;
      } catch (error) {
        failures.push({ id: "warp", message: error?.message ?? String(error) });
      }
      const ok = failures.length === 0 && warpOk;
      world.setDynamicProperty(INSTALL_KEY, ok ? SCHEMA : 0);
      lastReport = { ok, completed, failures, warpOk, schema: SCHEMA };
      if (ok) notifyAll("全17会場の全ブロックと操作物、既存ワープ設備を検証・修復しました。");
      else notifyAll(`JUNKETの一部を検証できませんでした。通路設定=${warpOk ? "完了" : "失敗"}、失敗${failures.length}件を管理ログで確認してください。`);
      return lastReport;
    })().finally(() => { installPromise = null; });
    return installPromise;
  }

  return {
    config, destination, ensure, goto, start, end, update, installAll, repair, withLoaded, restoreOriginal,
    ensureServices: id => ensureServiceControls(world, system, entry(id)),
    pollGardenLanding, pollClockLanding, pollHopePads, pollWalker,
    pollRabbitLanding, pollRabbitPosition,
    resume: resumeClockNeedle,
    inspect: async id => inspectLayout(entry(id)),
    setSpecialVenues: value => { specialVenues = value; },
    installing: () => installPromise !== null,
    complete: () => world.getDynamicProperty(INSTALL_KEY) === SCHEMA,
    report: () => lastReport,
    entries: definitions,
  };
}
