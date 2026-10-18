const KEY = "junket:lucy_venue_v1";
const TAG = "junket:lucy_fixture";
export const LUCY_RECEPTION = "junket:lucy_reception";
export const LUCY_BOX = "junket:lucy_box";

export function createLucyVenue({ world }) {
  const config = () => {
    const raw = world.getDynamicProperty?.(KEY);
    return typeof raw === "string" ? JSON.parse(raw) : null;
  };
  const at = (c, x, y, z) => ({
    x: c.origin.x + x - 194,
    y: c.origin.y + y - 55,
    z: c.origin.z + z - 209,
  });
  const dim = c => world.getDimension(c.dimension);
  function objects(c) {
    return dim(c).getEntities({ tags: [TAG], location: at(c, 202, 58, 211), maxDistance: 24 });
  }
  function ensure() {
    const c = config();
    if (!c) throw new Error("気分屋ルーシー会場が未設置です。");
    const definitions = [
      ["reception", "minecraft:npc", 206, 56, 211, "気分屋ルーシー受付 / 参加・再戦"],
      ["return", "minecraft:npc", 204, 56, 209, "入口へ戻る / JUNKET"],
      ["box0", LUCY_BOX, 202, 57.22, 210.2, "1P金庫"],
      ["box1", LUCY_BOX, 202, 57.22, 212.8, "2P金庫"],
      ["tv", "junket:lucy_tv", 205, 60, 211, "気分屋ルーシー"],
      ["doll", "junket:lucy_doll", 197, 58.7, 211, ""],
    ];
    const existing = objects(c);
    for (const [role, type, x, y, z, name] of definitions) {
      let entity = existing.find(item => item.getDynamicProperty("junket:lucy_role") === role);
      if (!entity) {
        entity = dim(c).spawnEntity(type, at(c, x, y, z));
        entity.addTag(TAG);
        entity.setDynamicProperty("junket:lucy_role", role);
      }
      entity.nameTag = name;
      if (role === "reception") entity.addTag(LUCY_RECEPTION);
      if (role === "return") entity.addTag("junket:return");
      if (role === "box0") entity.setDynamicProperty("junket:lucy_seat", 0);
      if (role === "box1") entity.setDynamicProperty("junket:lucy_seat", 1);
    }
    return c;
  }
  function fixture(c, role) {
    return objects(c).find(entity => entity.getDynamicProperty("junket:lucy_role") === role);
  }
  function update(table) {
    const c = config();
    if (!c || !table?.state) return;
    const s = table.state;
    for (const seat of [0, 1]) {
      const box = fixture(c, `box${seat}`);
      if (!box) continue;
      const opened = s.remaining[seat] === 0;
      box.setProperty("junket:lucy_frame", opened
        ? (s.winner === seat ? 11 : 10)
        : Math.min(9, 5 - s.remaining[seat]));
      box.nameTag = `${seat + 1}P金庫 / 残り${s.remaining[seat]}面`;
    }
    const tv = fixture(c, "tv");
    if (tv) {
      tv.nameTag = `ラウンド${s.round} / ${s.phase}\n体力 ${Math.max(0, s.hp[0])} : ${Math.max(0, s.hp[1])}`;
    }
  }
  function start(table) {
    const c = ensure();
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(candidate => candidate.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(at(c, 202, 56, seat === 0 ? 209 : 213), {
        dimension: dim(c),
        rotation: { x: 0, y: seat === 0 ? 0 : 180 },
      });
    }
    update(table);
  }
  function end(table) {
    const c = config();
    if (!c) return;
    if (table?.state) update({ ...table, state: { ...table.state, winner: null } });
    for (const player of world.getAllPlayers().filter(candidate => table.players.includes(candidate.id))) {
      if (player.dimension.id === c.dimension &&
          Math.hypot(player.location.x - at(c, 202, 56, 211).x,
            player.location.z - at(c, 202, 56, 211).z) < 20) {
        player.teleport(at(c, 206, 56, 211), { dimension: dim(c), rotation: { x: 0, y: 0 } });
      }
    }
  }
  function destination() {
    const c = config();
    return c ? { location: at(c, 206, 56, 211), dimension: dim(c), rotation: 0 } : null;
  }
  function go(player) {
    const target = destination();
    if (!target) throw new Error("気分屋ルーシー会場が未設置です。");
    player.teleport(target.location, { dimension: target.dimension, rotation: { x: 0, y: target.rotation } });
  }
  return { ensure, start, update, end, destination, go, config };
}
