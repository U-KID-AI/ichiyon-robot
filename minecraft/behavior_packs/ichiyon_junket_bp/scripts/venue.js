// Sparrow runtime consumes the generated venue manifest. This path never
// places terrain and never derives gameplay coordinates from Java world data.
import {
  ensureManifestFixtures, fixture, locationPoint, nearbyEntities, setVenueConfig, specialVenue,
  venueDimension, venueObjects, verifyManifestFixtures, setVisualFrame,
} from "./special_venue.js";
import { applyVenuePresentation } from "./venue_presentation.js";

const ID = "sparrow";
const KEY = "junket:venue_v1";
const BOX = "junket:venue_box";
const TEMP = "junket:venue_temporary";

export function createVenue({ world, system }) {
  let epoch = 0;
  let presenting = false;
  let installing = false;

  const config = () => {
    const raw = world.getDynamicProperty?.(KEY);
    return typeof raw === "string" ? JSON.parse(raw) : null;
  };
  const manifest = () => specialVenue(ID);
  const objects = () => venueObjects(world, ID);
  const fixtureOf = role => fixture(world, ID, role);
  const present = () => applyVenuePresentation(manifest(), value => fixtureOf(value.role));
  const online = table => world.getAllPlayers().filter(player => table.players.includes(player.id));
  const point = (role) => locationPoint(manifest().fixtures.find(value => value.role === role).location);

  function removeTemporary() {
    for (const entity of nearbyEntities(world, ID)) {
      if (entity.typeId === "junket:partition" && entity.hasTag(TEMP)) entity.remove();
    }
  }
  function ensure() {
    if (!config()) setVenueConfig(world, ID, KEY);
    ensureManifestFixtures(world, ID);
    removeTemporary();
    return config();
  }
  function verify() {
    return verifyManifestFixtures(world, ID);
  }
  function reset() {
    removeTemporary();
    for (const entity of objects()) {
      if (entity.hasTag(BOX)) setVisualFrame(entity, 0);
    }
    const diamond = fixtureOf("diamond");
    if (diamond) diamond.teleport(point("diamond"));
  }
  function pose(state) {
    const venue = manifest();
    const posePoint = venue.points.box_pose[state.actor === 0 ? 0 : 1];
    for (const [role, defaultZ] of [["bigbox", 0], ["littlebox", 0.8]]) {
      const entity = fixtureOf(role);
      if (!entity) continue;
      const offset = venue.points.box_offsets?.[role] ?? { x: 0, y: 0, z: defaultZ };
      entity.teleport({ x: posePoint.x + offset.x, y: posePoint.y + offset.y, z: posePoint.z + offset.z }, {
        rotation: { x: 0, y: state.actor === 0 ? 270 : 90 },
      });
      setVisualFrame(entity, state.phase === "hide" ? 3 : 0);
    }
    // The public diamond stays at its neutral stand until both choices settle.
    // A hidden choice must never be encoded in an entity's position/property.
    const diamond = fixtureOf("diamond");
    if (diamond) diamond.teleport(point("diamond"));
    removeTemporary();
    if (state.phase === "hide") {
      const at = venue.points.partition;
      const entity = venueDimension(world).spawnEntity("junket:partition", at);
      entity.addTag(TEMP);
      entity.setDynamicProperty?.("junket:venue_id", ID);
      entity.setDynamicProperty?.("junket:venue_role", "temporary_partition");
    }
  }
  function display(table, reveal = false) {
    const tv = fixtureOf("tv");
    const s = table.state;
    const round = reveal ? s.last.round : s.round;
    const phase = reveal ? "開封結果" : ({ bet: "賭け金決定", hide: "宝石を隠す", choose: "箱を選ぶ", finished: "終了" })[s.phase];
    if (tv) {
      setVisualFrame(tv, s.phase === "finished" ? 1 : 0);
      tv.nameTag = `${round}/6 ラウンド / ${phase}\n${s.money[0]}円 / ${s.money[1]}円`;
    }
    for (const player of online(table)) {
      player.onScreenDisplay?.setActionBar(`${round}/6 ${phase} | ${s.money[0]}円 : ${s.money[1]}円`);
    }
    present();
  }
  async function install(player, kind = "hall") {
    if (kind !== "hall") throw new Error("CROW HOTELの旧設置経路は廃止しました。既存の拠点設備を使用してください。");
    if (installing) throw new Error("会場の確認処理中です。");
    installing = true;
    try {
      setVenueConfig(world, ID, KEY);
      ensure();
      verify();
      return "ウラギリスズメの既存会場と操作物を検証しました。";
    } finally {
      installing = false;
    }
  }
  function destination() {
    const venue = manifest();
    return {
      location: locationPoint([
        venue.destination.x, venue.destination.y, venue.destination.z,
      ]),
      dimension: venueDimension(world),
      rotation: venue.rotation ?? 0,
    };
  }
  function go(player) {
    const target = destination();
    player.teleport(target.location, { dimension: target.dimension, rotation: { x: 0, y: target.rotation } });
  }
  function start(table) {
    ++epoch;
    presenting = false;
    ensure();
    reset();
    const venue = manifest();
    for (const player of online(table)) {
      const seat = table.players.indexOf(player.id);
      const location = venue.seats[seat];
      player.teleport(location, {
        dimension: venueDimension(world), rotation: { x: 0, y: seat === 0 ? 90 : -90 },
      });
    }
    pose(table.state);
    display(table);
  }
  async function transition(before, next) {
    ++epoch;
    display(next, before.state.phase === "choose");
    if (before.state.phase !== "choose") {
      pose(next.state);
      return;
    }
    presenting = true;
    const generation = epoch;
    const wait = ticks => new Promise(resolve => system.runTimeout(resolve, ticks));
    try {
      const last = next.state.last;
      const chosen = fixtureOf(last.chosen === "big" ? "bigbox" : "littlebox");
      for (const player of online(next)) {
        try { player.playSound("random.chestopen"); } catch { /* Optional sound. */ }
      }
      for (let frame = 1; frame <= 3; frame++) {
        if (generation !== epoch) return;
        setVisualFrame(chosen, frame + (last.hidden === last.chosen ? 3 : 0));
        await wait(5);
      }
      if (generation !== epoch) return;
      for (const player of online(next)) {
        const winner = world.getAllPlayers().find(value => value.id === next.players[last.winner]);
        player.onScreenDisplay?.setTitle(last.hidden === last.chosen ? "的中！" : "空の箱", {
          subtitle: `${winner?.name ?? "勝者"} +${last.amount}円`,
          stayDuration: 50, fadeInDuration: 5, fadeOutDuration: 10,
        });
      }
      for (const [role, value] of [["bigbox", "big"], ["littlebox", "small"]]) {
        const entity = fixtureOf(role);
        if (entity) setVisualFrame(entity, value === last.hidden ? 6 : 3);
      }
      const hiddenBox = fixtureOf(last.hidden === "big" ? "bigbox" : "littlebox");
      const diamond = fixtureOf("diamond");
      if (hiddenBox && diamond) diamond.teleport({
        x: hiddenBox.location.x, y: hiddenBox.location.y + 0.5, z: hiddenBox.location.z,
      });
      await wait(55);
      if (generation !== epoch) return;
      if (next.state.phase !== "finished") pose(next.state);
      display(next);
    } finally {
      if (generation === epoch) presenting = false;
    }
  }
  function end(table, finished = false) {
    ++epoch;
    presenting = false;
    removeTemporary();
    if (!finished) reset();
    const center = point("bigbox");
    for (const player of online(table)) {
      if (player.dimension.id === "minecraft:overworld" &&
          Math.hypot(player.location.x - center.x, player.location.z - center.z) < 40) {
        go(player);
      }
      if (finished) {
        const winner = table.state.winner === null ? "引き分け"
          : `${world.getAllPlayers().find(value => value.id === table.players[table.state.winner])?.name ?? "勝者"}の勝利`;
        player.onScreenDisplay?.setTitle(winner, {
          subtitle: "試合終了 / 受付から再戦できます",
          stayDuration: 80, fadeInDuration: 5, fadeOutDuration: 10,
        });
      }
    }
    present();
  }
  function repair() {
    ++epoch;
    presenting = false;
    ensure();
    reset();
    const tv = fixtureOf("tv");
    if (tv) {
      setVisualFrame(tv, 0);
      tv.nameTag = "ウラギリスズメ / 受付から参加";
    }
    present();
    verify();
    return "受付・箱・宝石・モニターを実体検証して復旧しました。";
  }
  function boxValue(target) {
    if (!target?.hasTag?.(BOX) || target.dimension.id !== "minecraft:overworld") return null;
    const value = target.getDynamicProperty("junket:box");
    if (value !== "big" && value !== "small") return null;
    return sameEntity(fixtureOf(value === "big" ? "bigbox" : "littlebox"), target) ? value : null;
  }
  function sameEntity(expected, actual) {
    // Script API may return distinct JS wrappers for the same native entity.
    // Compare its stable ID while retaining identity fallback for older mocks.
    return Boolean(expected && actual && (expected === actual ||
      (typeof expected.id === "string" && expected.id === actual.id)));
  }
  function control(target) {
    if (!target?.getDynamicProperty || target.dimension?.id !== "minecraft:overworld") return null;
    const role = target.getDynamicProperty("junket:sparrow_role");
    if (!["diamond", "tv", "return"].includes(role) || !sameEntity(fixtureOf(role), target)) return null;
    // Standard 17-game worlds delegate their lobby return to warp.js.
    if (role === "return" && !target.hasTag("junket:sparrow_return")) return null;
    return role;
  }
  function resume(table, playerId = null) {
    if (presenting) return;
    ensure();
    pose(table.state);
    display(table);
    if (playerId) {
      const player = online(table).find(value => value.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player && seat >= 0) player.teleport(manifest().seats[seat], {
        dimension: venueDimension(world), rotation: { x: 0, y: seat === 0 ? 90 : -90 },
      });
    }
  }
  return {
    install, go, destination, start, transition, end, repair, boxValue, control, resume,
    ensure, verify, config,
    enabled: () => true,
    locked: () => presenting,
    installing: () => installing,
  };
}
