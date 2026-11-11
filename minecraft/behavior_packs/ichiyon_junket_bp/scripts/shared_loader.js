// One owner/queue for every temporary JUNKET area. Never remove an area that
// this instance did not successfully create; unrelated server areas are leases
// owned by their own systems, even when all ten slots are occupied.
const instances = new WeakMap();
const AIR = new Set(["minecraft:air", "minecraft:cave_air", "minecraft:void_air"]);
const BAD = /(?:lava|water|fire|campfire|magma|cactus|powder_snow|sweet_berry|wither_rose|portal|pointed_dripstone)/;
const FULL_FLOOR = /(?:_wool|_planks|_concrete(?:_powder)?|_terracotta|_ore|_block|_bricks?|_stone|_quartz|_sandstone|_diorite|_andesite|_granite|_basalt|_deepslate|_blackstone)$/;
// @minecraft/server 2.0 has no Block.isSolid. Explicit full-cube identities
// remain necessary even when a newer host exposes that optional property.
// Quartz pillars have a full collision cube for every pillar_axis, including
// the original horizontal pillar at the Temperance entrance.
const SOLID = new Set(["minecraft:stone", "minecraft:dirt", "minecraft:grass_block", "minecraft:sandstone", "minecraft:smooth_quartz", "minecraft:nether_brick", "minecraft:bedrock", "minecraft:barrier", "minecraft:quartz_block", "minecraft:quartz_pillar", "minecraft:polished_blackstone"]);

export function inspectArrival(dimension, at) {
  if (![at.x, at.y, at.z].every(Number.isFinite)) throw new Error("到着座標が不正です。");
  const range = dimension.heightRange ?? { min: -64, max: 320 };
  if (at.y - .01 < range.min || at.y + 1.8 >= range.max) {
    return { loaded: true, safe: false, message: "到着地点が高さ制限外です。" };
  }
  const cells = new Map();
  const radius = .31;
  for (const x of [Math.floor(at.x - radius), Math.floor(at.x + radius)]) {
    for (const z of [Math.floor(at.z - radius), Math.floor(at.z + radius)]) {
      cells.set(`${x},${Math.floor(at.y - .01)},${z}`, { x, y: Math.floor(at.y - .01), z, floor: true });
      for (let y = Math.floor(at.y); y <= Math.floor(at.y + 1.8 - .0001); y++) {
        cells.set(`${x},${y},${z}`, { x, y, z, floor: false });
      }
    }
  }
  const observed = [];
  for (const cell of cells.values()) {
    let block;
    try { block = dimension.getBlock({ x: cell.x, y: cell.y, z: cell.z }); } catch { block = null; }
    if (!block) return { loaded: false, safe: false, message: `到着地点が未読込です: ${cell.x},${cell.y},${cell.z}` };
    observed.push({ cell, block });
  }
  // "loaded" describes the entire player volume, independently of whether
  // its already-loaded floor or headroom is safe to enter.
  for (const { cell, block } of observed) {
    const id = block.typeId;
    if (BAD.test(id) || block.isLiquid === true || block.isWaterlogged === true) {
      return { loaded: true, safe: false, message: `到着地点に危険ブロックがあります: ${id}` };
    }
    if (cell.floor) {
      const solid = block.isSolid === true || SOLID.has(id) || FULL_FLOOR.test(id);
      if (!solid || /(?:slab|stairs|fence|wall|carpet|leaves|trapdoor|door|bed)$/.test(id)) {
        return { loaded: true, safe: false, message: `安全な床を確認できません: ${id}` };
      }
    } else if (!AIR.has(id) && !/^minecraft:light_block(?:_\d+)?$/.test(id)) {
      return { loaded: true, safe: false, message: `到着地点の足元・頭上が塞がっています: ${id}` };
    }
  }
  return { loaded: true, safe: true, cells: cells.size };
}

export function arrivalBounds(at) {
  return [Math.floor(at.x - .31) - 16, Math.floor(at.y) - 1, Math.floor(at.z - .31) - 16,
    Math.floor(at.x + .31) + 16, Math.ceil(at.y + 1.8), Math.floor(at.z + .31) + 16];
}

export function getSharedLoader(world, system) {
  if (instances.has(world)) return instances.get(world);
  let queue = Promise.resolve(), serial = 0, owned = null;
  const prefix = `jkrt_${Date.now().toString(36)}_${Math.floor(Math.random() * 65536).toString(36)}`;
  const now = () => typeof system.currentTick === "number" ? system.currentTick : Math.floor(Date.now() / 50);
  const wait = ticks => new Promise(resolve => system.runTimeout(resolve, ticks));
  function withArea(options, action) {
    const requested = now();
    const timeout = options.timeoutTicks ?? 2400;
    const guard = () => {
      if (now() - requested > timeout) throw new Error("読込待ちが制限時間を超えました。もう一度操作してください。");
      options.guard?.();
    };
    const task = queue.then(async () => {
      guard();
      if (owned) throw new Error(`前回の一時読込領域の解除が未完了です: ${owned}`);
      const bounds = options.bounds.map(Math.floor);
      if (bounds.length !== 6 || !bounds.every(Number.isFinite)) throw new Error("読込範囲が不正です。");
      const chunks = (Math.floor(bounds[3] / 16) - Math.floor(bounds[0] / 16) + 1) *
        (Math.floor(bounds[5] / 16) - Math.floor(bounds[2] / 16) + 1);
      if (chunks < 1 || chunks > 100) throw new Error(`読込範囲が上限を超えています: ${chunks}`);
      const name = `${prefix}_${++serial}`;
      let created = false;
      try {
        let result;
        try { result = options.dimension.runCommand(`tickingarea add ${bounds.join(" ")} ${name} true`); }
        catch (error) { throw new Error(`一時読込領域を追加できません。10領域上限またはサーバー制限を確認してください: ${error?.message ?? error}`); }
        if ((result?.successCount ?? 0) < 1) throw new Error("一時読込領域を追加できません（最大10領域）。既存領域は保持しました。");
        created = true; owned = name;
        const checkedWait = async ticks => { await wait(ticks); guard(); };
        await checkedWait(20);
        return await action({ guard, wait: checkedWait, name });
      } finally {
        if (created) {
          try {
            const result = options.dimension.runCommand(`tickingarea remove ${name}`);
            if ((result?.successCount ?? 0) < 1) throw new Error("一時読込領域を解除できませんでした。");
            owned = null;
          } catch (error) {
            // Retain the failed lease name and block subsequent requests.
            // Never try to recover by deleting another system's areas.
            console.error(`[JUNKET LOADER] cleanup ${name}: ${error?.message ?? error}`);
            throw error;
          }
          await wait(40);
        }
      }
    });
    queue = task.catch(() => {});
    return task;
  }
  const api = { withArea, now, active: () => owned };
  instances.set(world, api);
  return api;
}
