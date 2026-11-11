import { applyVenuePresentation } from "./venue_presentation.js";
import {
  ensureManifestFixtures, fixture, locationPoint, setVenueConfig, specialVenue,
  venueConfig, venueDimension, verifyManifestFixtures, setVisualFrame,
} from "./special_venue.js";

const ID = "silence";
const KEY = "junket:silence_venue_v1";
export const SILENCE_RECEPTION = "junket:silence_reception";
export const SILENCE_CONTROL = "junket:silence_control";
export const SILENCE_GUARD = "junket:silence_guard";
export const SILENCE_JACKET = "junket:silence_jacket";
export const SILENCE_DISC = "junket:silence_cd";
export const SILENCE_COVER = "junket:silence_cover";
export const SILENCE_SETTER = "junket:silence_setter";
export const SILENCE_RETURNER = "junket:silence_returner";

export function createSilenceVenue({ world }) {
  const config = () => {
    const raw = world.getDynamicProperty?.(KEY);
    return typeof raw === "string" ? JSON.parse(raw) : null;
  };
  const manifest = () => specialVenue(ID);
  const fixtureOf = role => fixture(world, ID, role);
  function ensure() {
    if (!config()) setVenueConfig(world, ID, KEY);
    ensureManifestFixtures(world, ID);
    return venueConfig(ID);
  }
  function verify() {
    return verifyManifestFixtures(world, ID);
  }
  function update(table, reveal = false) {
    try { return updateVisuals(table, reveal); }
    finally { applyVenuePresentation(manifest(), expected => fixtureOf(expected.role)); }
  }
  function updateVisuals(table) {
    if (!table?.state) return;
    const s = table.state;
    const revealed = s.last && ["set", "playback", "return"].includes(s.phase);
    for (let slot = 0; slot < 3; slot++) {
      const jacket = fixtureOf(`jacket${slot}`);
      const disc = fixtureOf(`cd${slot}`);
      const selected = revealed && s.last.slot === slot;
      const arranging = s.phase === "arrange" && s.arrangeSelected === slot;
      if (jacket) setVisualFrame(jacket, selected
        ? ({ 0: 1, 20: 2, 30: 3 }[s.last.duration] ?? 0) : arranging ? 4 : 0);
      if (disc) setVisualFrame(disc, selected
        ? ({ 0: 0, 20: 1, 30: 2 }[s.last.duration] ?? 0) : 0);
    }
    const cover = fixtureOf("cover");
    if (cover) {
      cover.nameTag = s.phase === "arrange"
        ? `配置を確定 / ${s.arrangeSelected === null ? "ジャケットを選択" : `位置${s.arrangeSelected + 1}選択中`}`
        : "";
    }
    const tv = fixtureOf("tv");
    if (tv) {
      tv.nameTag = `${s.phase} / 合計${s.totalSeconds}秒 / 残り${Math.max(0, s.remainingTicks)}t\n体力 ${Math.max(0, s.hp[0])} : ${Math.max(0, s.hp[1])}`;
    }
  }
  function selectionPositions(table) {
    ensure();
    const venue = manifest();
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(value => value.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(venue.seats[seat], {
        dimension: venueDimension(world), rotation: { x: 0, y: seat === 0 ? 90 : -90 },
      });
    }
  }
  function playbackPositions(table) {
    const venue = manifest();
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(value => value.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(locationPoint(venue.points.playback_seats[seat]), {
        dimension: venueDimension(world), rotation: { x: 0, y: seat === 0 ? 90 : -90 },
      });
    }
  }
  function isInsideSoundRoom(player) {
    if (player.dimension.id !== "minecraft:overworld") return false;
    const venue = manifest();
    const lo = venue.points.sound_room_lo, hi = venue.points.sound_room_hi;
    return player.location.x >= lo.x && player.location.x <= hi.x &&
      player.location.y >= lo.y && player.location.y <= hi.y &&
      player.location.z >= lo.z && player.location.z <= hi.z;
  }
  function start(table) {
    ensure();
    selectionPositions(table);
    update(table);
    verify();
  }
  function end(table) {
    if (table?.state) update({ ...table, state: { ...table.state, last: null, phase: "finished" } });
    for (const player of world.getAllPlayers().filter(value => table.players.includes(value.id))) {
      if (player.dimension.id === "minecraft:overworld") go(player);
    }
  }
  function destination() {
    const venue = manifest();
    return { location: locationPoint(venue.destination), dimension: venueDimension(world), rotation: 180 };
  }
  function go(player) {
    const target = destination();
    player.teleport(target.location, { dimension: target.dimension, rotation: { x: 0, y: target.rotation } });
  }
  return {
    ensure, verify, start, update, end, destination, go, config,
    selectionPositions, playbackPositions, isInsideSoundRoom,
  };
}
