// Local licensed structures/models are supplied by a separate optional pack.
// Only explicit install commands change terrain. No ticking areas or intervals.
const KEY = "junket:venue_v1";
const TAG = "junket:venue_fixture";
const TEMP = "junket:venue_temporary";
const BOX = "junket:venue_box";
const FLOOR = new Set(["minecraft:grass_block", "minecraft:grass", "minecraft:dirt"]);
const SPECS = {
  hall: { id: "junket:sparrow_hall", size: [27, 13, 29], source: [210, 54, 198] },
  hotel: { id: "junket:crow_reception", size: [14, 10, 10], source: [360, 55, -59] },
  temperance: { id: "junket:temperance_hall", size: [29, 33, 62], source: [80, 99, 205] },
  lucy: { id: "junket:lucy_hall", size: [15, 11, 5], source: [194, 55, 209] },
  silence: { id: "junket:silence_hall", size: [31, 13, 33], source: [79, 55, 281] },
  jinn: { id: "junket:jinn_hall", size: [28, 38, 37], source: [80, 120, 230] },
};

export function createVenue({ world, system }) {
  let epoch = 0, presenting = false, installing = false;
  const venueKey = kind => kind === "hall" ? KEY : kind === "temperance"
    ? "junket:temperance_venue_v1" : kind === "lucy"
      ? "junket:lucy_venue_v1" : kind === "hotel"
        ? `${KEY}_hotel` : `junket:${kind}_venue_v1`;
  const config = (kind = "hall") => {
    const raw = world.getDynamicProperty?.(venueKey(kind));
    return typeof raw === "string" ? JSON.parse(raw) : null;
  };
  const dimension = c => world.getDimension(c.dimension);
  const at = (c, x, y, z, kind = "hall") => {
    const source = SPECS[kind].source;
    return { x: c.origin.x + x - source[0], y: c.origin.y + y - source[1], z: c.origin.z + z - source[2] };
  };
  const online = table => world.getAllPlayers().filter(p => table.players.includes(p.id));
  function entities(c, tag = TAG) {
    return dimension(c).getEntities({ tags: [tag], location: at(c, 225, 57, 217), maxDistance: 40 });
  }
  function removeTemporary(c) { for (const e of entities(c, TEMP)) e.remove(); }
  function spawn(c, type, location, name, temporary = false) {
    const e = dimension(c).spawnEntity(type, location);
    e.addTag(temporary ? TEMP : TAG);
    if (name) e.nameTag = name;
    return e;
  }
  function fixture(c, type) { return entities(c).find(e => e.typeId === type); }
  function ensure(c) {
    for (const [box, type, y, z] of [["big", "bigbox", 57.3, 217], ["small", "littlebox", 57.2, 218]]) {
      let e = fixture(c, `junket:${type}`);
      if (!e) e = spawn(c, `junket:${type}`, at(c, 226, y, z), box === "big" ? "大箱（全額）" : "小箱（半額）");
      e.addTag(BOX); e.setDynamicProperty("junket:box", box);
    }
    if (!fixture(c, "junket:tv")) spawn(c, "junket:tv", at(c, 225, 61.6, 214), "ウラギリスズメ / 受付から参加");
    if (!fixture(c, "junket:diamond")) spawn(c, "junket:diamond", at(c, 232, 57.5, 218), "宝石（見本）");
    if (!fixture(c, "minecraft:npc")) {
      const npc = spawn(c, "minecraft:npc", at(c, 232, 56, 220), "ウラギリスズメ受付 / 参加・操作・再戦");
      npc.addTag("junket:reception");
    }
    if (!dimension(c).getEntities({ tags: ["junket:return"], location: at(c, 231, 56, 219), maxDistance: 8 }).length) {
      const exit = spawn(c, "minecraft:npc", at(c, 231, 56, 219), "入口へ戻る / JUNKET");
      exit.addTag("junket:return");
    }
  }
  function reset(c) {
    removeTemporary(c);
    for (const e of entities(c, BOX)) e.setProperty("junket:frame", 0);
  }
  function pose(c, state) {
    const x = state.actor === 0 ? 226 : 224;
    for (const e of entities(c, BOX)) {
      const big = e.getDynamicProperty("junket:box") === "big";
      e.teleport(at(c, x, big ? 57.3 : 57.2, big ? 217 : 218), { rotation: { x: 0, y: state.actor === 0 ? 270 : 90 } });
      e.setProperty("junket:frame", state.phase === "hide" ? 3 : 0);
    }
    removeTemporary(c);
    if (state.phase === "hide") spawn(c, "junket:partition", at(c, 225, 57.5, 217.5), "", true);
  }
  function display(c, table) {
    const tv = fixture(c, "junket:tv"), s = table.state;
    if (tv) {
      tv.setProperty("junket:frame", s.phase === "finished" ? 1 : 0);
      tv.nameTag = `${s.round}/6 ラウンド\n${s.money[0]}円 / ${s.money[1]}円`;
    }
    for (const p of online(table)) p.onScreenDisplay?.setActionBar(`${s.round}/6 | ${s.money[0]}円 : ${s.money[1]}円`);
  }
  async function install(player, kind = "hall") {
    if (!SPECS[kind]) throw new Error("会場指定が不正です。");
    if (installing) throw new Error("会場の設置処理中です。");
    if (config(kind)) throw new Error("設置済みです。/scriptevent junket:venue repair または goto を使ってください。");
    const spec = SPECS[kind], d = player.dimension;
    // Floor and foundation align to the current superflat surface.
    const c = { dimension: d.id, origin: { x: Math.floor(player.location.x),
      y: Math.floor(player.location.y + .0001) - (kind === "hotel" ? 1 : 2), z: Math.floor(player.location.z) } };
    const to = { x: c.origin.x + spec.size[0] - 1, y: c.origin.y + spec.size[1] - 1, z: c.origin.z + spec.size[2] - 1 };
    if (kind !== "hotel") {
      for (const otherKind of Object.keys(SPECS)) {
        if (otherKind === kind || otherKind === "hotel") continue;
        const other = config(otherKind);
        if (other && other.dimension === c.dimension) {
          const size = SPECS[otherKind].size;
          const dx = Math.max(0, other.origin.x - to.x, c.origin.x - (other.origin.x + size[0] - 1));
          const dz = Math.max(0, other.origin.z - to.z, c.origin.z - (other.origin.z + size[2] - 1));
          if (Math.hypot(dx, dz) < 1024) throw new Error("他の会場から1,024ブロック以上離してください。");
        }
      }
    }
    installing = true;
    try {
      // Yield only while this explicit job runs, then release the job completely.
      await new Promise((resolve, reject) => system.runJob((function* () {
        try {
          let count = 0;
          for (let x = c.origin.x; x <= to.x; x++) for (let z = c.origin.z; z <= to.z; z++) for (let y = c.origin.y; y <= to.y; y++) {
            const b = d.getBlock({ x, y, z });
            const ground = y < c.origin.y + (kind === "hotel" ? 1 : 2);
            if (!b || !(b.isAir || (ground && FLOOR.has(b.typeId)))) {
              throw new Error(`設置を中止: (${x},${y},${z}) ${b?.typeId ?? "未読込"}。平坦な草原の空き領域で実行してください。`);
            }
            if (++count % 256 === 0) yield;
          }
          resolve();
        } catch (error) { reject(error); }
      })()));
      c.backup = `junket:before_${kind}_${Date.now()}`;
      world.structureManager.createFromWorld(c.backup, d, c.origin, to, { includeEntities: false });
      try { world.structureManager.place(spec.id, d, c.origin, { includeEntities: false }); }
      catch (error) { throw new Error(`構造物 ${spec.id} を配置できません。Local Venue 素材BPを確認してください: ${error.message}`); }
      world.setDynamicProperty(venueKey(kind), JSON.stringify(c));
      if (kind === "hall") {
        ensure(c); reset(c);
        player.teleport(at(c, 232, 56, 219), { dimension: d });
      } else if (kind === "temperance") {
        player.teleport(at(c, 94, 101, 241, kind), { dimension: d });
      } else if (kind === "lucy") {
        player.teleport(at(c, 202, 56, 211, kind), { dimension: d });
      } else if (kind === "silence") {
        player.teleport(at(c, 91, 56, 302, kind), { dimension: d });
      } else if (kind === "jinn") {
        player.teleport(at(c, 94, 135, 244, kind), { dimension: d });
      } else {
        const npc = spawn(c, "minecraft:npc", at(c, 366, 56, -54.1, "hotel"), "CROW HOTEL / ウラギリスズメ会場へ");
        npc.addTag("junket:hotel_route");
        player.teleport(at(c, 366, 56, -57, "hotel"), { dimension: d });
      }
      return `${kind === "hotel" ? "CROW HOTEL受付部分" : "会場"}を設置しました。元領域は ${c.backup} に保存済みです。`;
    } finally { installing = false; }
  }
  function destination(kind = "hall") {
    const c = config(kind);
    if (!c) return null;
    const target = kind === "hall" ? at(c, 232, 56, 219) : at(c, 366, 56, -57, "hotel");
    return { location: target, dimension: dimension(c), rotation: kind === "hall" ? 180 : 0 };
  }
  function go(player, kind = "hall") {
    const target = destination(kind);
    if (!target) throw new Error("会場はまだ設置されていません。");
    player.teleport(target.location, { dimension: target.dimension, rotation: { x: 0, y: target.rotation } });
  }
  function start(table) {
    const c = config(); if (!c) return;
    ++epoch; presenting = false; ensure(c); reset(c);
    for (const p of online(table)) {
      const seat = table.players.indexOf(p.id);
      p.teleport(at(c, seat === 0 ? 227 : 223, 56, 217), { dimension: dimension(c), rotation: { x: 0, y: seat === 0 ? 90 : -90 } });
    }
    pose(c, table.state); display(c, table);
  }
  async function transition(before, next) {
    const c = config(); if (!c) return;
    const generation = ++epoch;
    display(c, next);
    if (before.state.phase !== "choose") { pose(c, next.state); return; }
    presenting = true;
    const wait = ticks => new Promise(resolve => system.runTimeout(resolve, ticks));
    try {
      const last = next.state.last;
      const chosen = entities(c, BOX).find(e => e.getDynamicProperty("junket:box") === last.chosen);
      for (const p of online(next)) {
        try { p.playSound("random.chestopen"); } catch { /* Optional sound. */ }
      }
      for (let frame = 1; frame <= 3; frame++) {
        if (generation !== epoch) return;
        chosen?.setProperty("junket:frame", frame + (last.hidden === last.chosen ? 3 : 0));
        await wait(5);
      }
      for (const p of online(next)) {
        const winner = world.getAllPlayers().find(p => p.id === next.players[last.winner]);
        p.onScreenDisplay?.setTitle(last.hidden === last.chosen ? "的中！" : "空の箱", {
          subtitle: `${winner?.name ?? "勝者"} +${last.amount}円`, stayDuration: 50, fadeInDuration: 5, fadeOutDuration: 10 });
      }
      // Both boxes open after the reveal, so the gem can also be seen on a miss.
      for (const e of entities(c, BOX)) e.setProperty("junket:frame", e.getDynamicProperty("junket:box") === last.hidden ? 6 : 3);
      await wait(55);
      if (generation !== epoch) return;
      if (next.state.phase !== "finished") pose(c, next.state);
    } finally { if (generation === epoch) presenting = false; }
  }
  function end(table, finished = false) {
    ++epoch; presenting = false;
    const c = config(); if (!c) return;
    removeTemporary(c);
    if (!finished) reset(c);
    for (const p of online(table)) {
      if (p.dimension.id === c.dimension) {
        const center = at(c, 225, 56, 217);
        if (Math.hypot(p.location.x - center.x, p.location.z - center.z) < 40) go(p);
      }
      if (finished) p.onScreenDisplay?.setTitle(table.state.winner === null ? "引き分け" : `${world.getAllPlayers().find(p => p.id === table.players[table.state.winner])?.name ?? "勝者"}の勝利`, {
        subtitle: "試合終了 / 受付から再戦できます", stayDuration: 80, fadeInDuration: 5, fadeOutDuration: 10 });
    }
  }
  function repair() {
    const c = config(); if (!c) throw new Error("会場が未設置です。");
    ++epoch; presenting = false; ensure(c); reset(c);
    const tv = fixture(c, "junket:tv"); if (tv) { tv.setProperty("junket:frame", 0); tv.nameTag = "ウラギリスズメ / 受付から参加"; }
    return "受付・箱・宝石・モニターを復旧しました。";
  }
  function boxValue(target) {
    const c = config();
    if (!c || !target.hasTag(BOX) || target.dimension.id !== c.dimension) return null;
    return target.getDynamicProperty("junket:box");
  }
  return { install, go, destination, start, transition, end, repair, boxValue,
    provision(c, kind = "hall") { ensure(c); reset(c); },
    enabled: () => !!config(), locked: () => presenting, installing: () => installing };
}
