// Offline state-machine tests using doubles for documented Script API members, not BDS verification.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("../minecraft/behavior_packs/import_structures/scripts/poster_core.js", import.meta.url), "utf8");
const { createPosterRuntime, posterVectors, segmentLocation, baseLocation } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const AIR = "minecraft:air";
const CARDINAL = "minecraft:cardinal_direction";
const directions = ["north", "south", "east", "west"];
const BlockPermutation = {
  resolve(id, states = {}) { return { type: { id }, getState: name => states[name] }; },
};
class ItemStack {
  constructor(typeId, amount) { this.typeId = typeId; this.amount = amount; }
}

function fixture(columns = 3, rows = 2, direction = "north", mode = "Survival", baseId = "ichiyon:poster_managed_1") {
  const poster = { baseId, segmentPrefix: baseId, label: "Test", columns, rows };
  const base = { x: 10, y: 60, z: 20 };
  const blocks = new Map();
  const bag = [];
  const drops = [];
  const timers = [];
  const unavailable = new Set();
  let failNextAt;
  let full = false;
  const key = p => `${p.x},${p.y},${p.z}`;
  const dimension = {
    id: "minecraft:overworld",
    getBlock(p) {
      if (unavailable.has(key(p))) throw new Error("Unloaded chunk");
      if (!blocks.has(key(p))) {
        const block = {
          location: { ...p }, dimension, permutation: BlockPermutation.resolve(AIR),
          get typeId() { return this.permutation.type.id; },
          get isAir() { return this.typeId === AIR; },
          get isLiquid() { return this.typeId === "minecraft:water"; },
          setPermutation(permutation) {
            if (failNextAt === key(p)) { failNextAt = undefined; throw new Error("Write failed"); }
            this.permutation = permutation;
          },
        };
        blocks.set(key(p), block);
      }
      return blocks.get(key(p));
    },
    spawnItem(item, location) { drops.push({ item, location }); },
  };
  const player = {
    getGameMode: () => mode,
    getComponent(name) {
      assert.equal(name, "minecraft:inventory");
      return { container: { addItem(item) { if (full) return item; bag.push(item); return undefined; } } };
    },
    sendMessage() {},
  };
  const system = { runTimeout(callback) { timers.push(callback); } };
  const runtime = createPosterRuntime({ posters: [poster], BlockPermutation, ItemStack, system });
  const cells = [];
  for (let row = 0; row < rows; row++) {
    for (let column = 0; column < columns; column++) {
      const segment = { row, column };
      const location = segmentLocation(base, direction, segment);
      assert.deepEqual(baseLocation(location, direction, segment), base);
      cells.push(dimension.getBlock(location));
      const { wall } = posterVectors(direction);
      dimension.getBlock({ x: location.x + wall.x, y: location.y, z: location.z + wall.z })
        .setPermutation(BlockPermutation.resolve("minecraft:stone"));
    }
  }
  const anchor = dimension.getBlock(base);
  anchor.setPermutation(BlockPermutation.resolve(baseId, { [CARDINAL]: direction }));
  function breakCell(block) {
    const permutation = block.permutation;
    block.setPermutation(BlockPermutation.resolve(AIR));
    return () => runtime.cleanup(block, permutation, player);
  }
  return { poster, runtime, player, anchor, cells, bag, drops, blocks, dimension, base, breakCell, timers,
    full() { full = true; }, failAt(p) { failNextAt = key(p); }, unload(p) { unavailable.add(key(p)); } };
}

test("all 100 integer sizes place and remove in each of four directions with exactly one return", () => {
  for (let width = 1; width <= 10; width++) {
    for (let height = 1; height <= 10; height++) {
      for (const direction of directions) {
        const f = fixture(width, height, direction);
        assert.equal(f.runtime.expand(f.anchor, f.player), true);
        f.cells.forEach((cell, i) => {
          assert.equal(cell.typeId, `${f.poster.baseId}_r${Math.floor(i / width)}c${i % width}`);
          assert.equal(cell.permutation.getState(CARDINAL), direction);
        });
        assert.equal(f.bag.length, 0);
        const cleanup = f.breakCell(f.cells.at(-1));
        assert.equal(cleanup(), true);
        assert.ok(f.cells.every(cell => cell.isAir));
        assert.deepEqual(f.bag.map(item => [item.typeId, item.amount]), [[f.poster.baseId, 1]]);
        assert.equal(cleanup(), false);
        assert.equal(f.bag.length, 1);
      }
    }
  }
});

test("fixed vertical and horizontal posters use the same runtime", () => {
  for (const [baseId, cols, rows] of [["ichiyon:poster_raio", 4, 6], ["ichiyon:poster_akuki", 6, 4]]) {
    const f = fixture(cols, rows, "east", "Survival", baseId);
    assert.equal(f.runtime.expand(f.anchor, f.player), true);
    f.breakCell(f.cells[5])();
    assert.equal(f.bag[0].typeId, baseId);
  }
});

test("obstacle and missing or liquid support reject placement without overwriting obstacles", () => {
  for (const direction of directions) {
    for (const failure of ["obstacle", "missing-wall", "liquid-wall", "unloaded"]) {
      const f = fixture(3, 2, direction);
      const target = f.cells.at(-1);
      const { wall } = posterVectors(direction);
      const support = f.dimension.getBlock({ x: target.location.x + wall.x, y: target.location.y, z: target.location.z + wall.z });
      if (failure === "obstacle") target.setPermutation(BlockPermutation.resolve("minecraft:diamond_block"));
      if (failure === "missing-wall") support.setPermutation(BlockPermutation.resolve(AIR));
      if (failure === "liquid-wall") support.setPermutation(BlockPermutation.resolve("minecraft:water"));
      if (failure === "unloaded") f.unload(support.location);
      assert.equal(f.runtime.expand(f.anchor, f.player), false);
      assert.equal(f.bag.length, 1);
      assert.equal(f.anchor.typeId, AIR);
      assert.equal(target.typeId, failure === "obstacle" ? "minecraft:diamond_block" : AIR);
    }
  }
});

test("partial write failure rolls back segments before refund", () => {
  const f = fixture();
  f.failAt(f.cells[3].location);
  assert.equal(f.runtime.expand(f.anchor, f.player), false);
  assert.ok(f.cells.every(cell => cell.isAir));
  assert.equal(f.bag.length, 1);
});

test("Creative and Spectator never receive drops; Adventure receives one", () => {
  for (const mode of ["Creative", "Spectator", "Adventure"]) {
    const f = fixture(3, 2, "south", mode);
    f.runtime.expand(f.anchor, f.player);
    f.breakCell(f.cells[2])();
    assert.equal(f.bag.length, mode === "Adventure" ? 1 : 0);
    const rejected = fixture(3, 2, "south", mode);
    rejected.cells[1].setPermutation(BlockPermutation.resolve("minecraft:stone"));
    rejected.runtime.expand(rejected.anchor, rejected.player);
    assert.equal(rejected.bag.length, mode === "Adventure" ? 1 : 0);
  }
});

test("full inventory drops exactly one base item in the poster dimension", () => {
  const f = fixture();
  f.full();
  f.runtime.expand(f.anchor, f.player);
  f.breakCell(f.cells[1])();
  assert.equal(f.bag.length, 0);
  assert.equal(f.drops.length, 1);
  assert.equal(f.drops[0].item.typeId, f.poster.baseId);
});

test("neighbor segment and different direction are preserved", () => {
  const f = fixture();
  f.runtime.expand(f.anchor, f.player);
  f.cells[1].setPermutation(BlockPermutation.resolve(`${f.poster.baseId}_r0c0`, { [CARDINAL]: "north" }));
  f.cells[2].setPermutation(BlockPermutation.resolve(`${f.poster.baseId}_r0c2`, { [CARDINAL]: "south" }));
  f.breakCell(f.cells.at(-1))();
  assert.equal(f.cells[1].typeId, `${f.poster.baseId}_r0c0`);
  assert.equal(f.cells[2].typeId, `${f.poster.baseId}_r0c2`);
  assert.equal(f.bag.length, 1);
});

test("two simultaneous segment breaks return one item, then replacement can return another", () => {
  const f = fixture();
  f.runtime.expand(f.anchor, f.player);
  const first = f.breakCell(f.cells[1]);
  const second = f.breakCell(f.cells[2]);
  first(); second();
  assert.equal(f.bag.length, 1);
  f.timers.forEach(callback => callback());
  f.anchor.setPermutation(BlockPermutation.resolve(f.poster.baseId, { [CARDINAL]: "north" }));
  f.runtime.expand(f.anchor, f.player);
  f.breakCell(f.cells[0])();
  assert.equal(f.bag.length, 2);
});

test("script reload can remove an existing poster without placement state", () => {
  const f = fixture();
  f.runtime.expand(f.anchor, f.player);
  const reloaded = createPosterRuntime({ posters: [f.poster], BlockPermutation, ItemStack, system: { runTimeout() {} } });
  const broken = f.cells[2].permutation;
  f.cells[2].setPermutation(BlockPermutation.resolve(AIR));
  assert.equal(reloaded.cleanup(f.cells[2], broken, f.player), true);
  assert.equal(f.bag.length, 1);
});
