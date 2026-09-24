// Shared by fixed and managed posters. Call only from writable after-event callbacks.
const AIR = "minecraft:air";
const CARDINAL = "minecraft:cardinal_direction";

export function posterVectors(direction) {
  if (direction === "south") return { right: { x: 1, z: 0 }, wall: { x: 0, z: -1 } };
  if (direction === "east") return { right: { x: 0, z: -1 }, wall: { x: -1, z: 0 } };
  if (direction === "west") return { right: { x: 0, z: 1 }, wall: { x: 1, z: 0 } };
  return { right: { x: -1, z: 0 }, wall: { x: 0, z: 1 } };
}

export function segmentLocation(base, direction, segment) {
  const { right } = posterVectors(direction);
  return { x: base.x + right.x * segment.column, y: base.y + segment.row, z: base.z + right.z * segment.column };
}

export function baseLocation(location, direction, segment) {
  const { right } = posterVectors(direction);
  return { x: location.x - right.x * segment.column, y: location.y - segment.row, z: location.z - right.z * segment.column };
}

export function createPosterRuntime({ posters, BlockPermutation, ItemStack, system }) {
  const byBase = new Map();
  const bySegment = new Map();
  const removed = new Set();
  const safe = (read, fallback) => { try { return read(); } catch { return fallback; } };
  const directionOf = permutation => permutation.getState(CARDINAL);
  const keyOf = (dimension, base, direction, poster) => `${dimension.id}:${base.x},${base.y},${base.z}:${direction}:${poster.baseId}`;
  for (const entry of posters) {
    const poster = { ...entry, columns: entry.columns ?? 4, rows: entry.rows ?? 6, segments: [] };
    if (![poster.columns, poster.rows].every(value => Number.isInteger(value) && value >= 1 && value <= 10)
        || byBase.has(poster.baseId)) throw new Error("Invalid poster catalog");
    byBase.set(poster.baseId, poster);
    for (let row = 0; row < poster.rows; row++) {
      for (let column = 0; column < poster.columns; column++) {
        const segment = { id: `${poster.segmentPrefix}_r${row}c${column}`, row, column, poster };
        if (bySegment.has(segment.id)) throw new Error("Duplicate poster segment");
        poster.segments.push(segment);
        bySegment.set(segment.id, segment);
      }
    }
  }

  function refund(player, dimension, location, poster) {
    const mode = String(safe(() => player.getGameMode(), "")).toLowerCase();
    if (mode !== "survival" && mode !== "adventure") return;
    const item = new ItemStack(poster.baseId, 1);
    const bag = safe(() => player.getComponent("minecraft:inventory")?.container, undefined);
    const leftover = bag ? safe(() => bag.addItem(item), item) : item;
    if (leftover) dimension.spawnItem(leftover, location);
  }

  function matches(block, segment, direction) {
    return block.typeId === segment.id && directionOf(block.permutation) === direction;
  }

  function expand(block, player) {
    const poster = byBase.get(block.typeId);
    if (!poster) return false;
    const dimension = block.dimension;
    const base = { ...block.location };
    const direction = directionOf(block.permutation);
    if (!["north", "south", "east", "west"].includes(direction)) return false;
    const changed = [];
    try {
      const { wall } = posterVectors(direction);
      // Resolve and validate every cell before mutating any of them.
      const targets = poster.segments.map(segment => {
        const location = segmentLocation(base, direction, segment);
        const target = dimension.getBlock(location);
        const support = dimension.getBlock({ x: location.x + wall.x, y: location.y, z: location.z + wall.z });
        const expected = segment.row === 0 && segment.column === 0 ? poster.baseId : AIR;
        if (!target || target.typeId !== expected || !support || support.isAir || support.isLiquid) throw new Error("No free wall");
        return { target, segment, permutation: BlockPermutation.resolve(segment.id, { [CARDINAL]: direction }) };
      });
      for (const target of targets) {
        target.target.setPermutation(target.permutation);
        changed.push(target);
      }
      removed.delete(keyOf(dimension, base, direction, poster));
      return true;
    } catch (error) {
      // A failed expansion must not leave refundable segments behind.
      for (const { target, segment } of changed) {
        if (matches(target, segment, direction)) target.setPermutation(BlockPermutation.resolve(AIR));
      }
      if (block.typeId === poster.baseId) block.setPermutation(BlockPermutation.resolve(AIR));
      refund(player, dimension, base, poster);
      safe(() => player.sendMessage(`${poster.label}: ${poster.columns} x ${poster.rows} blocks of free wall required.`));
      return false;
    }
  }

  function cleanup(block, brokenPermutation, player) {
    const segment = bySegment.get(brokenPermutation.type.id);
    if (!segment) return false;
    const direction = directionOf(brokenPermutation);
    const poster = segment.poster;
    const base = baseLocation(block.location, direction, segment);
    const dimension = block.dimension;
    const key = keyOf(dimension, base, direction, poster);
    if (removed.has(key)) return false;
    // Do not refund a partially removed poster across an unloaded chunk boundary.
    const targets = poster.segments.map(candidate => {
      const target = dimension.getBlock(segmentLocation(base, direction, candidate));
      if (!target) throw new Error("Poster chunk unavailable");
      return { target, candidate };
    });
    for (const { target, candidate } of targets) {
      if (matches(target, candidate, direction)) target.setPermutation(BlockPermutation.resolve(AIR));
    }
    // Two break events queued in the same tick must produce only one item, even at 1x1.
    removed.add(key);
    system.runTimeout(() => removed.delete(key), 2);
    refund(player, dimension, block.location, poster);
    return true;
  }

  return { expand, cleanup };
}
