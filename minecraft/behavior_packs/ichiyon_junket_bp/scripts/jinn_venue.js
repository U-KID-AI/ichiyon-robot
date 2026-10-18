const KEY = "junket:jinn_venue_v1";
const TAG = "junket:jinn_fixture";
export const JINN_RECEPTION = "junket:jinn_reception";
export const JINN_CARD = "junket:jinn_card";
export const JINN_LEVER = "junket:jinn_lever";
export const JINN_SURRENDER = "junket:jinn_surrender";

export function createJinnVenue({ world }) {
  const config = () => {
    const raw = world.getDynamicProperty?.(KEY);
    return typeof raw === "string" ? JSON.parse(raw) : null;
  };
  const at = (c, x, y, z) => ({
    x: c.origin.x + x - 80,
    y: c.origin.y + y - 120,
    z: c.origin.z + z - 230,
  });
  const dim = c => world.getDimension(c.dimension);
  const objects = c => dim(c).getEntities({ tags: [TAG], location: at(c, 94, 139, 252), maxDistance: 40 });
  function ensure() {
    const c = config();
    if (!c) throw new Error("ジャックポット・ジニー会場が未設置です。");
    const definitions = [
      ["reception", "minecraft:npc", 94, 135, 244, "ジャックポット・ジニー受付 / 参加・再戦"],
      ["return", "minecraft:npc", 91, 135, 244, "入口へ戻る / JUNKET"],
      ["tv", "junket:jinn_tv", 94, 143, 253.05, "ジャックポット・ジニー"],
      ["covering", "junket:jinn_covering", 94, 136, 252, ""],
      ["card0", JINN_CARD, 90, 136, 250, "1Pカード"],
      ["card1", JINN_CARD, 97, 136, 250, "2Pカード"],
      ["lever0", JINN_LEVER, 90, 135, 250, "1Pレバー"],
      ["lever1", JINN_LEVER, 97, 135, 250, "2Pレバー"],
      ["surrender0", "minecraft:npc", 82, 135, 252, "1P 降参"],
      ["surrender1", "minecraft:npc", 105, 135, 252, "2P 降参"],
    ];
    const existing = objects(c);
    for (const [role, type, x, y, z, name] of definitions) {
      let entity = existing.find(item => item.getDynamicProperty("junket:jinn_role") === role);
      if (!entity) {
        entity = dim(c).spawnEntity(type, at(c, x, y, z));
        entity.addTag(TAG);
        entity.setDynamicProperty("junket:jinn_role", role);
      }
      entity.nameTag = name;
      if (role === "reception") entity.addTag(JINN_RECEPTION);
      if (role === "return") entity.addTag("junket:return");
      if (role.startsWith("card")) entity.addTag(JINN_CARD);
      if (role.startsWith("lever")) {
        entity.addTag(JINN_LEVER);
        entity.setDynamicProperty("junket:jinn_seat", Number(role.slice(5)));
      }
      if (role.startsWith("surrender")) {
        entity.addTag(JINN_SURRENDER);
        entity.setDynamicProperty("junket:jinn_seat", Number(role.slice(9)));
      }
    }
    return c;
  }
  function fixture(c, role) {
    return objects(c).find(entity => entity.getDynamicProperty("junket:jinn_role") === role);
  }
  const cardFrame = card => ({ gold: 1, thief: 2, demon: 3 }[card] ?? 0);
  function update(table) {
    const c = config();
    if (!c || !table?.state) return;
    const s = table.state;
    const revealed = s.phase === "reveal" && s.last;
    for (const seat of [0, 1]) {
      const card = fixture(c, `card${seat}`);
      if (card) {
        card.setProperty("junket:jinn_frame", revealed ? cardFrame(s.last.choices[seat]) : 0);
        card.nameTag = `${seat + 1}Pカード / 残り 黄金${s.hands[seat].gold} 盗賊${s.hands[seat].thief} 魔人${s.hands[seat].demon}`;
      }
      const lever = fixture(c, `lever${seat}`);
      if (lever) lever.nameTag = `${seat + 1}Pレバー / ${s.lever[seat] ? "使用済み" : "未使用"}`;
    }
    const tv = fixture(c, "tv");
    if (tv) {
      tv.nameTag = `第${s.game}セット ${s.round}/6 / ${s.phase}\nコイン ${s.coins[0]} : ${s.coins[1]} / 支払い ${s.payment[0]} : ${s.payment[1]}`;
    }
  }
  function start(table) {
    const c = ensure();
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(candidate => candidate.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(at(c, seat === 0 ? 90 : 97, 136, 252), {
        dimension: dim(c), rotation: { x: 0, y: seat === 0 ? 90 : -90 },
      });
    }
    update(table);
  }
  function settlementPositions(table) {
    const c = config();
    if (!c) return;
    for (const playerId of table.players) {
      const player = world.getAllPlayers().find(candidate => candidate.id === playerId);
      const seat = table.players.indexOf(playerId);
      if (player) player.teleport(at(c, seat === 0 ? 87 : 100, 136, 252), {
        dimension: dim(c), rotation: { x: 0, y: seat === 0 ? 90 : -90 },
      });
    }
  }
  function end(table) {
    const c = config();
    if (!c) return;
    if (table?.state) update({ ...table, state: { ...table.state, phase: "finished", last: null } });
    for (const player of world.getAllPlayers().filter(candidate => table.players.includes(candidate.id))) {
      if (player.dimension.id === c.dimension) {
        player.teleport(at(c, 94, 135, 244), { dimension: dim(c), rotation: { x: 0, y: 0 } });
      }
    }
  }
  function destination() {
    const c = config();
    return c ? { location: at(c, 94, 135, 244), dimension: dim(c), rotation: 0 } : null;
  }
  function go(player) {
    const target = destination();
    if (!target) throw new Error("ジャックポット・ジニー会場が未設置です。");
    player.teleport(target.location, { dimension: target.dimension, rotation: { x: 0, y: target.rotation } });
  }
  return { ensure, start, update, end, settlementPositions, destination, go, config };
}
