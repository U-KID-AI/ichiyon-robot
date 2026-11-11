import {
  ensureManifestFixtures, fixture, locationPoint, setVenueConfig, specialVenue,
  venueConfig, venueDimension, verifyManifestFixtures, setVisualFrame,
} from "./special_venue.js";
import { applyVenuePresentation } from "./venue_presentation.js";
import { sameFixtureActor } from "./fixture_scope.js";

const ID = "lucy";
const KEY = "junket:lucy_venue_v1";
export const LUCY_RECEPTION = "junket:lucy_reception";
export const LUCY_BOX = "junket:lucy_box";
export const LUCY_KEYHOLE = "junket:lucy_keyhole";
export const LUCY_CONFIRM = "junket:lucy_confirm";
export const LUCY_CONTROL = "junket:lucy_control";

export function createLucyVenue({ world, system }) {
  const config = () => {
    const raw = world.getDynamicProperty?.(KEY);
    return typeof raw === "string" ? JSON.parse(raw) : null;
  };
  const manifest = () => specialVenue(ID);
  const fixtureOf = role => fixture(world, ID, role);
  const present = () => applyVenuePresentation(manifest(), value => fixtureOf(value.role));

  function ensure() {
    if (!config()) setVenueConfig(world, ID, KEY);
    ensureManifestFixtures(world, ID);
    return venueConfig(ID);
  }
  function verify() {
    return verifyManifestFixtures(world, ID);
  }
  function updateState(table) {
    if (!table?.state) {
      for (const value of manifest().fixtures) {
        if (value.role.startsWith("keyhole_") || value.role.startsWith("confirm_") || value.role.startsWith("box")) {
          setVisualFrame(fixtureOf(value.role), 0);
        }
      }
      return;
    }
    const s = table.state;
    for (const seat of [0, 1]) {
      const box = fixtureOf(`box${seat}`);
      if (box) {
        const opened = s.remaining[seat] === 0;
        setVisualFrame(box, opened
          ? (s.winner === seat ? 6 : 5)
          : Math.min(4, Math.max(0, 5 - s.remaining[seat])));
        box.nameTag = `${seat + 1}P金庫 / 残り${s.remaining[seat]}面\n設定担当 ${seat === 0 ? 2 : 1}P`;
      }
    }
    for (let box = 0; box < 2; box++) {
      const completedRows = s.progress?.[box] ?? 0;
      for (let row = 0; row < 5; row++) {
        const activeDraft = s.phase === "lock" && completedRows === row;
        for (let column = 0; column < 5; column++) {
          const keyhole = fixtureOf(`keyhole_${box}_${row}_${column}`);
          if (!keyhole) continue;
          const attempted = s.attempted?.[box]?.[row]?.[column] === 1;
          const selected = activeDraft && s.lockDrafts?.[box]?.[row]?.[column] === true;
          const committed = row < completedRows;
          const frame = attempted ? 2 : selected ? 1 : committed ? 3 : 0;
          setVisualFrame(keyhole, frame);
          keyhole.nameTag = `${box + 1}P金庫 ${row + 1}面 鍵穴 ${column + 1}` +
            `${attempted ? "（使用済み）" : selected ? "（当たり）" : committed ? "（設定済み）" : ""}`;
        }
        const confirm = fixtureOf(`confirm_${box}_${row}`);
        if (confirm) {
          const active = s.phase === "lock" && completedRows === row;
          setVisualFrame(confirm, active ? Math.min(6, row + 1) : 0);
          confirm.nameTag = `${box + 1}P金庫 ${row + 1}面 確定` +
            `${active ? "（操作可能）" : row < completedRows ? "（設定済み）" : "（未開放）"}`;
        }
      }
    }
    const tv = fixtureOf("tv");
    if (tv) {
      tv.nameTag = `ラウンド${s.round} / ${s.phase}\n体力 ${Math.max(0, s.hp[0])} : ${Math.max(0, s.hp[1])}`;
    }
  }
  function update(table) {
    try { updateState(table); } finally { present(); }
  }
  async function start(table) {
    ensure();
    if (system) await new Promise(resolve => system.runTimeout(resolve, 2));
    const venue = manifest();
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(value => value.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(venue.seats[seat], {
        dimension: venueDimension(world), rotation: { x: 0, y: seat === 0 ? 0 : 180 },
      });
    }
    update(table);
    verify();
  }
  function end(table) {
    update(null);
    const center = manifest().points.vault_focus;
    for (const player of world.getAllPlayers().filter(value => table?.players?.includes(value.id))) {
      if (player.dimension.id === "minecraft:overworld" &&
          Math.hypot(player.location.x - center.x, player.location.z - center.z) < 32) go(player);
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
  function controlRole(entity) {
    if (entity?.dimension?.id !== "minecraft:overworld") return null;
    const role = entity.getDynamicProperty?.("junket:lucy_role");
    if (typeof role !== "string" || !manifest().fixtures.some(value => value.role === role)) return null;
    return sameFixtureActor(fixtureOf(role), entity) ? role : null;
  }
  return { ensure, verify, start, update, end, destination, go, config, controlRole };
}
