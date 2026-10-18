import assert from "node:assert/strict";
import { setupReception } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/reception.js";

function fixture(y = -60, floor = "minecraft:grass_block", head = "minecraft:air") {
  const reads = [], spawned = [];
  const dimension = {
    getEntities(query) {
      assert.equal(query.type, "minecraft:npc");
      assert.deepEqual(query.tags, ["junket:temporary_reception"]);
      return []; // Nearby horses/chickens/pigs cannot match this query.
    },
    getBlock(pos) {
      assert.ok(Object.values(pos).every(Number.isInteger));
      reads.push(pos);
      const typeId = pos.y === -61 ? floor : pos.y === -59 ? head : "minecraft:air";
      // Actual stable API shape: intentionally NO isSolid property.
      return { typeId, isAir: typeId === "minecraft:air" };
    },
    spawnEntity(type, pos) { spawned.push({ type, pos }); return { addTag() {} }; },
  };
  return { player: { dimension, location: { x: 134.2, y, z: 7.8 } }, reads, spawned };
}
for (const y of [-60, -60.000003814697266, -59.999996185302734]) {
  for (const grass of ["minecraft:grass_block", "minecraft:grass"]) {
    const f = fixture(y, grass);
    assert.match(setupReception(f.player), /受付0\.1\.2/);
    assert.deepEqual(f.reads.map(p => p.y), [-60, -59, -61]);
    assert.deepEqual(f.spawned[0].pos, { x: 134.5, y: -60, z: 7.5 });
  }
}
for (const floor of ["minecraft:air", "minecraft:water", "minecraft:tallgrass"]) {
  const f = fixture(-60, floor);
  assert.throws(() => setupReception(f.player), error => error.message.includes(`床=(134,-61,7) ${floor}`));
  assert.equal(f.spawned.length, 0);
}
const blocked = fixture(-60, "minecraft:grass_block", "minecraft:stone");
assert.throws(() => setupReception(blocked.player), /頭上=\(134,-59,7\) minecraft:stone/);
assert.equal(blocked.spawned.length, 0);
const negativeXZ = fixture();
negativeXZ.player.location.x = -134.2;
negativeXZ.player.location.z = -7.8;
setupReception(negativeXZ.player);
assert.deepEqual(negativeXZ.reads[0], { x: -135, y: -60, z: -8 });
const unloaded = fixture();
unloaded.player.dimension.getBlock = () => undefined;
assert.throws(() => setupReception(unloaded.player), /床=\(134,-61,7\) 未取得/);
assert.equal(unloaded.spawned.length, 0);
console.log("PASS reception: stable API grass floors, negative coordinates, landing precision, blocked/missing blocks and diagnostics");
