const KEY = "junket:silence_venue_v1";
const TAG = "junket:silence_fixture";
export const SILENCE_RECEPTION = "junket:silence_reception";
export const SILENCE_CONTROL = "junket:silence_control";
export const SILENCE_GUARD = "junket:silence_guard";

export function createSilenceVenue({ world }) {
  const config = () => {
    const raw = world.getDynamicProperty?.(KEY);
    return typeof raw === "string" ? JSON.parse(raw) : null;
  };
  const at = (c, x, y, z) => ({
    x: c.origin.x + x - 79,
    y: c.origin.y + y - 55,
    z: c.origin.z + z - 281,
  });
  const dim = c => world.getDimension(c.dimension);
  const objects = c => dim(c).getEntities({ tags: [TAG], location: at(c, 91, 58, 297), maxDistance: 48 });
  function ensure() {
    const c = config();
    if (!c) throw new Error("サウンド・オブ・サイレンス会場が未設置です。");
    const definitions = [
      ["reception", "minecraft:npc", 91, 56, 302, "サウンド・オブ・サイレンス受付 / 参加・再戦"],
      ["return", "minecraft:npc", 89, 56, 302, "入口へ戻る / JUNKET"],
      ["guard", "minecraft:npc", 91, 56, 286, "耳栓を使う / 再生中のみ"],
      ["tv", "junket:silence_tv", 91.45, 61, 312, "サウンド・オブ・サイレンス"],
      ["cover", "junket:silence_covering", 91.5, 57.51, 297, ""],
    ];
    const zSlots = [298.175, 297, 295.825];
    for (let slot = 0; slot < 3; slot++) {
      definitions.push([`jacket${slot}`, "junket:silence_jacket", 91.5, 57.51, zSlots[slot], `ジャケット ${slot + 1}`]);
      definitions.push([`cd${slot}`, "junket:silence_cd", 91.5, 57.06, zSlots[slot], ""]);
    }
    const existing = objects(c);
    for (const [role, type, x, y, z, name] of definitions) {
      let entity = existing.find(item => item.getDynamicProperty("junket:silence_role") === role);
      if (!entity) {
        entity = dim(c).spawnEntity(type, at(c, x, y, z));
        entity.addTag(TAG);
        entity.setDynamicProperty("junket:silence_role", role);
      }
      entity.nameTag = name;
      if (role === "reception") entity.addTag(SILENCE_RECEPTION);
      if (role === "return") entity.addTag("junket:return");
      if (role === "guard") entity.addTag(SILENCE_GUARD);
      if (role.startsWith("jacket") || role.startsWith("cd") || role === "cover") {
        entity.addTag(SILENCE_CONTROL);
      }
      const slot = Number(role.match(/^(?:jacket|cd)(\d)$/)?.[1]);
      if (Number.isInteger(slot)) entity.setDynamicProperty("junket:silence_slot", slot);
    }
    return c;
  }
  function fixture(c, role) {
    return objects(c).find(entity => entity.getDynamicProperty("junket:silence_role") === role);
  }
  function update(table) {
    const c = config();
    if (!c || !table?.state) return;
    const s = table.state;
    const revealed = s.last && ["playback", "return"].includes(s.phase);
    for (let slot = 0; slot < 3; slot++) {
      const jacket = fixture(c, `jacket${slot}`);
      const disc = fixture(c, `cd${slot}`);
      const selected = revealed && s.last.slot === slot;
      if (jacket) jacket.setProperty("junket:silence_frame", selected
        ? ({ 0: 1, 20: 2, 30: 3 }[s.last.duration] ?? 0) : 0);
      if (disc) disc.setProperty("junket:silence_frame", selected
        ? ({ 0: 0, 20: 1, 30: 2 }[s.last.duration] ?? 0) : 0);
    }
    const cover = fixture(c, "cover");
    if (cover) cover.nameTag = revealed ? "" : "選択前";
    const tv = fixture(c, "tv");
    if (tv) {
      tv.nameTag = `${s.phase} / 合計${s.totalSeconds}秒 / 残り${Math.max(0, s.remainingTicks)}t\n体力 ${Math.max(0, s.hp[0])} : ${Math.max(0, s.hp[1])}`;
    }
  }
  function selectionPositions(table) {
    const c = ensure();
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(candidate => candidate.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(at(c, seat === 0 ? 90 : 92, 56, 297), {
        dimension: dim(c),
        rotation: { x: 0, y: seat === 0 ? 90 : -90 },
      });
    }
  }
  function playbackPositions(table) {
    const c = config();
    if (!c) return;
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(candidate => candidate.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(at(c, seat === 0 ? 90 : 92, 56, 287), {
        dimension: dim(c),
        rotation: { x: 0, y: seat === 0 ? 90 : -90 },
      });
    }
  }
  function isInsideSoundRoom(player) {
    const c = config();
    if (!c || player.dimension.id !== c.dimension) return false;
    const lo = at(c, 89, 56, 285), hi = at(c, 93, 59, 289);
    return player.location.x >= lo.x && player.location.x <= hi.x &&
      player.location.y >= lo.y && player.location.y <= hi.y &&
      player.location.z >= lo.z && player.location.z <= hi.z;
  }
  function start(table) {
    ensure();
    selectionPositions(table);
    update(table);
  }
  function end(table) {
    const c = config();
    if (!c) return;
    if (table?.state) update({ ...table, state: { ...table.state, phase: "finished", last: null } });
    for (const player of world.getAllPlayers().filter(candidate => table.players.includes(candidate.id))) {
      if (player.dimension.id === c.dimension) {
        player.teleport(at(c, 91, 56, 302), { dimension: dim(c), rotation: { x: 0, y: 0 } });
      }
    }
  }
  function destination() {
    const c = config();
    return c ? { location: at(c, 91, 56, 302), dimension: dim(c), rotation: 0 } : null;
  }
  function go(player) {
    const target = destination();
    if (!target) throw new Error("サウンド・オブ・サイレンス会場が未設置です。");
    player.teleport(target.location, { dimension: target.dimension, rotation: { x: 0, y: target.rotation } });
  }
  return {
    ensure, start, update, end, destination, go, config,
    selectionPositions, playbackPositions, isInsideSoundRoom,
  };
}
