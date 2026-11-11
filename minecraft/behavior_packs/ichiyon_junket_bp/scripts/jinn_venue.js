import { applyVenuePresentation } from "./venue_presentation.js";
import {
  ensureManifestFixtures, fixture, locationPoint, setVenueConfig, specialVenue,
  venueConfig, venueDimension, verifyManifestFixtures, setVisualFrame,
} from "./special_venue.js";

const ID = "jinn";
const KEY = "junket:jinn_venue_v1";
export const JINN_RECEPTION = "junket:jinn_reception";
export const JINN_CARD = "junket:jinn_card";
export const JINN_LEVER = "junket:jinn_lever";
export const JINN_SURRENDER = "junket:jinn_surrender";
export const JINN_CARD_TYPES = Object.freeze(["gold", "thief", "demon"]);
export const jinnCardRole = (seat, card) => `card_${card}_${seat}`;

export function createJinnVenue({ world }) {
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
  const cardFrame = card => ({ gold: 1, thief: 2, demon: 3 }[card] ?? 0);
  function update(table, reveal = false) {
    try { return updateVisuals(table, reveal); }
    finally { applyVenuePresentation(manifest(), expected => fixtureOf(expected.role)); }
  }
  function updateVisuals(table) {
    if (!table?.state) return;
    const s = table.state;
    for (const seat of [0, 1]) {
      for (const cardType of JINN_CARD_TYPES) {
        const card = fixtureOf(jinnCardRole(seat, cardType));
        if (!card) continue;
        const remaining = s.hands[seat][cardType];
        setVisualFrame(card, cardFrame(cardType));
        // Public views omit choices. Selection remains in each player's form;
        // neither name tags nor public visuals may reveal the hidden card.
        card.nameTag = "";
      }
      const lever = fixtureOf(`lever${seat}`);
      if (lever) lever.nameTag = `${seat + 1}Pレバー / ${s.lever[seat] ? "使用済み" : "未使用"}`;
    }
    const tv = fixtureOf("tv");
    if (tv) {
      tv.nameTag = `第${s.game}セット ${s.round}/6 / ${s.phase}\nコイン ${s.coins[0]} : ${s.coins[1]} / 支払い ${s.payment[0]} : ${s.payment[1]}`;
    }
  }
  function start(table) {
    ensure();
    const venue = manifest();
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(value => value.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(venue.seats[seat], {
        dimension: venueDimension(world), rotation: { x: 0, y: seat === 0 ? 90 : -90 },
      });
    }
    update(table);
    verify();
  }
  function settlementPositions(table) {
    const venue = manifest();
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(value => value.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(locationPoint(venue.points.settlement[seat]), {
        dimension: venueDimension(world), rotation: { x: 0, y: seat === 0 ? 90 : -90 },
      });
    }
  }
  function end(table) {
    if (table?.state) update({ ...table, state: { ...table.state, phase: "finished", last: null } });
    for (const player of world.getAllPlayers().filter(value => table.players.includes(value.id))) {
      if (player.dimension.id === "minecraft:overworld") go(player);
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
  return { ensure, verify, start, update, end, settlementPositions, destination, go, config };
}
