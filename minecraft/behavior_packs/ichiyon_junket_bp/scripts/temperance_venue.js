import { applyVenuePresentation } from "./venue_presentation.js";
import {
  ensureManifestFixtures, fixture, locationPoint, setVenueConfig, specialVenue,
  venueConfig, venueDimension, verifyManifestFixtures, setVisualFrame,
} from "./special_venue.js";
import { VENUE_CONFIG } from "./junket_venue_generated.js";

const ID = "temperance";
const KEY = "junket:temperance_venue_v1";
export const TEMPERANCE_RECEPTION = "junket:temperance_reception";

export function createTemperanceVenue({ world }) {
  let beamSignature;
  const config = () => {
    const raw = world.getDynamicProperty(KEY);
    return typeof raw === "string" ? JSON.parse(raw) : null;
  };
  const manifest = () => specialVenue(ID);
  const fixtureOf = role => fixture(world, ID, role);
  const position = role => locationPoint(manifest().fixtures.find(value => value.role === role).location);

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
  function updateVisuals(table, reveal = false) {
    if (!table?.state) return;
    const s = table.state;
    const venue = manifest();
    const tv = fixtureOf("tv");
    if (tv) {
      tv.nameTag = `ラウンド${s.round} / 石 ${s.stones[0]} : ${s.stones[1]}\n体力 ${Math.max(0, s.hp[0])} : ${Math.max(0, s.hp[1])}`;
    }
    for (const role of ["balance0", "balance1", "stone0", "stone1"]) {
      const entity = fixtureOf(role);
      if (!entity) continue;
      const stone = role.startsWith("stone");
      const seat = Number(role.slice(stone ? 5 : 7));
      const offset = venue.points.balance_radius * Math.max(-100, Math.min(100, s.balance));
      const base = position(role);
      entity.teleport({
        x: base.x,
        y: base.y + (seat === 0 ? -offset : offset),
        z: base.z,
      });
      entity.nameTag = `${seat + 1}P / 石 ${s.stones[seat]}`;
    }
    for (const index of [0, 1, 2]) {
      const card = fixtureOf(`card${index}`);
      const frame = fixtureOf(`frame${index}`);
      const value = s.last?.cards[index];
      if (card) {
        setVisualFrame(card,
          reveal && value ? (Math.abs(value) - 1) * 2 + (value > 0 ? 1 : 2) : 0);
      }
      if (frame) {
        setVisualFrame(frame, !reveal && s.phase !== "arrange" ? 1 : 0);
      }
    }
    const k = [9, 26, 42, 59, 76, 92].filter(value => Math.abs(s.balance) >= value).length;
    const index = 6 - Math.sign(s.balance) * k;
    const signature = JSON.stringify([venue.origin, index]);
    if (beamSignature !== signature) {
      world.structureManager.place(`junket:temperance_beam_${index}`, venueDimension(world),
        venue.points.beam_origin, { includeEntities: false });
      beamSignature = signature;
    }
  }
  function destination() {
    const venue = manifest();
    return { location: locationPoint(venue.destination), dimension: venueDimension(world), rotation: 0 };
  }
  function go(player) {
    const target = destination();
    player.teleport(target.location, { dimension: target.dimension, rotation: { x: 0, y: target.rotation } });
  }
  function start(table) {
    ensure();
    const venue = manifest();
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(value => value.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(venue.seats[seat], {
        dimension: venueDimension(world), rotation: { x: 0, y: seat === 0 ? -90 : 90 },
      });
    }
    update(table);
    verify();
  }
  function end(table) {
    if (table?.state) {
      update({ ...table, state: { ...table.state, actor: 1, balance: 0, phase: "arrange" } });
    }
    const venue = manifest();
    const center = venue.points.stage_center ?? venue.destination;
    for (const player of world.getAllPlayers().filter(value => table.players.includes(value.id))) {
      if (player.dimension.id === "minecraft:overworld" &&
          Math.hypot(player.location.x - center.x, player.location.z - center.z) < 80) go(player);
    }
  }
  return { ensure, verify, start, update, end, go, destination, config };
}
