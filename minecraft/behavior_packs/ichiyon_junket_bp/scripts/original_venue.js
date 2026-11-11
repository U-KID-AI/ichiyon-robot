// Source-derived buildings stay in private packs. This module contains only
// reusable placement/coordinate plumbing, never textures or structure data.
const originals = new Map();

const vector = point => Array.isArray(point) ? { x: point[0], y: point[1], z: point[2] } : point;
// Geometry metadata contains both coordinates and scalar parameters (radii,
// frame numbers, seat ids). Move coordinates without corrupting those scalars.
function translateGeometry(value, delta, name = "") {
  if (name.endsWith("offsets")) return value;
  if (Array.isArray(value)) {
    if (value.length === 3 && value.every(Number.isFinite)) return value.map((v, i) => v + delta[i]);
    return value.map(item => translateGeometry(item, delta));
  }
  if (!value || typeof value !== "object") return value;
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    const axis = /(?:^|_)([xyz])$/.exec(key)?.[1];
    const vertical = ["water_bottom", "water_top"].includes(key);
    result[key] = Number.isFinite(item) && (axis || vertical)
      ? item + delta[vertical ? 1 : { x: 0, y: 1, z: 2 }[axis]]
      : translateGeometry(item, delta, key);
  }
  return result;
}
export function translateVenue(source, targetOrigin = source.origin) {
  const delta = targetOrigin.map((value, index) => value - source.origin[index]);
  const point = value => {
    const v = vector(value);
    return { x: v.x + delta[0], y: v.y + delta[1], z: v.z + delta[2] };
  };
  const points = translateGeometry(source.points ?? {}, delta);
  const geometry = Object.fromEntries([
    "drop_areas", "hope_pads", "hope_ai_spawns", "hope_warp", "hope_spectator", "hope_lose",
    "clock_geometry", "walker", "rabbit",
  ].filter(key => source[key] !== undefined).map(key => [key, translateGeometry(source[key], delta)]));
  return {
    ...source, ...geometry, origin: [...targetOrigin], source_origin: [...source.origin],
    destination: point(source.destination), return_point: point(source.return_point),
    seats: source.seats.map(point), points,
    fixtures: source.fixtures.map(value => ({ ...value, location: Object.values(point(value.location)) })),
    block_overlays: (source.block_overlays ?? []).map(value => ({ ...value, location: point(value.location) })),
    probes: (source.probes ?? []).map(value => ({ ...value, location: point(value.location) })),
  };
}

export function registerOriginalVenues(venues) {
  for (const venue of venues) {
    if (!venue.id || !venue.structure || venue.origin.length !== 3 || venue.size.length !== 3 ||
        !venue.source || venue.distribution !== "private-local-only") throw new Error("原作会場定義が不正です。");
    originals.set(venue.id, venue);
  }
}
export function originalVenue(id) { return originals.get(id); }

// Called only by the isolated trial entry. Ordinary main.js never invokes it.
// Refuse occupied terrain on first installation; a persisted marker means we
// verify the building without re-placing it or wiping player changes.
export function createOriginalVenueInstaller({ world, system }) {
  const pending = new Map();
  const wait = ticks => new Promise(resolve => system.runTimeout(resolve, ticks));
  const marker = id => `junket:original_installed_${id}`;
  function completePlacement(venue) {
    const dimension = world.getDimension("minecraft:overworld");
    for (const overlay of venue.block_overlays ?? []) {
      const block = dimension.getBlock(overlay.location);
      if (block?.typeId === overlay.block) continue;
      if (!block?.isAir && block?.typeId !== "minecraft:air") {
        throw new Error("補助装飾の位置に別のブロックがあります。建築を保持して停止しました。");
      }
      block.setType(overlay.block);
    }
    verify(venue);
    world.setDynamicProperty(marker(venue.id), venue.source);
  }
  function verify(venue) {
    const dimension = world.getDimension("minecraft:overworld");
    for (const probe of venue.probes ?? []) {
      const block = dimension.getBlock(probe.location);
      if (!block || !probe.blocks.includes(block.typeId)) {
        throw new Error(`原作会場の${probe.label}を確認できません (${block?.typeId ?? "未読込"})。`);
      }
    }
    return true;
  }
  async function install(id) {
    if (pending.has(id)) return pending.get(id);
    const job = (async () => {
      const venue = originalVenue(id);
      if (!venue) throw new Error(`原作会場未登録: ${id}`);
      const dimension = world.getDimension("minecraft:overworld");
      // Wait for the spawn chunks, never create a persistent ticking area.
      let loaded = false;
      for (let attempt = 0; attempt < 60; attempt++) {
        try {
          loaded = [venue.origin, venue.origin.map((v, i) => v + venue.size[i] - 1)]
            .every(pos => dimension.getBlock(vector(pos)) !== undefined);
        } catch { loaded = false; }
        if (loaded) break;
        await wait(10);
      }
      if (!loaded) throw new Error("会場チャンク未読込。受付座標へ移動して /scriptevent junket:reference repair を実行してください。");
      const prior = world.getDynamicProperty(marker(id));
      if (prior) {
        if (prior === `${venue.source}:placed`) completePlacement(venue);
        else verify(venue);
        return venue;
      }
      if (!world.structureManager.getPackStructureIds().includes(venue.structure)) {
        throw new Error(`原作構造物 ${venue.structure} がありません。専用テストBPを確認してください。`);
      }
      // y=54..66 is air in the newly generated reference world. This check
      // also protects arbitrary worlds if someone enables this private pack.
      for (let x = 0; x < venue.size[0]; x++) {
        for (let y = 0; y < venue.size[1]; y++) for (let z = 0; z < venue.size[2]; z++) {
          const at = { x: venue.origin[0] + x, y: venue.origin[1] + y, z: venue.origin[2] + z };
          const block = dimension.getBlock(at);
          if (!block?.isAir && block?.typeId !== "minecraft:air") {
            throw new Error(`配置中止: ${at.x},${at.y},${at.z} は空気ではありません。既存の建築を保持しました。`);
          }
        }
        if (x % 4 === 3) await wait(1);
      }
      world.structureManager.place(venue.structure, dimension, vector(venue.origin), { includeEntities: false });
      // Record placement immediately, so a failed display/overlay check cannot
      // make a retry overwrite or misclassify a successfully placed building.
      world.setDynamicProperty(marker(id), `${venue.source}:placed`);
      completePlacement(venue);
      return venue;
    })();
    pending.set(id, job);
    try { return await job; } finally { pending.delete(id); }
  }
  return { install, verify, installed: id => Boolean(world.getDynamicProperty(marker(id))) };
}
