// Offline tests: import only the dependency-free avatar handler, never BDS/net/config.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../minecraft/behavior_packs/import_structures/scripts/avatar_commands.js", import.meta.url), "utf8");
const { handleAvatarCommand } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const skins = ["kiana", "mei", "bronya", "albert"];

function fixture() {
  const results = [], entities = [], spawns = [];
  const player = {
    location: { x: 0, y: 64, z: 0 },
    getRotation: () => ({ x: 30, y: 75 }),
    dimension: {
      spawnEntity(type, location) {
        const entity = makeEntity(0, 2);
        spawns.push({ type, location, entity });
        return entity;
      },
      getEntities(query) {
        assert.deepEqual(query, { type: "ichiyon:avatar", location: player.location, maxDistance: 16 });
        return entities.filter(e => e.typeId === query.type && e.location.x <= query.maxDistance);
      },
    },
  };
  const helpers = {
    isValidPlayerName: value => /^[A-Za-z0-9_]{1,16}$/.test(value),
    findOnlinePlayer: value => value === "Player_1" ? player : undefined,
    playerForwardSpawnLocation: () => ({ x: 0, y: 64, z: 2 }),
    postResult: async (...args) => { results.push(args); },
  };
  const command = type => ({ type, request_id: "request-1", minecraft_player: "Player_1" });
  return { helpers, command, results, entities, spawns, player };
}

function makeEntity(variant, x) {
  return {
    typeId: "ichiyon:avatar", location: { x, y: 64, z: 0 }, removed: false,
    getComponent(id) { assert.equal(id, "minecraft:variant"); return { value: variant }; },
    triggerEvent(event) { this.event = event; },
    setRotation(rotation) { this.rotation = rotation; },
    remove() { this.removed = true; },
  };
}

for (const [index, skin] of skins.entries()) {
  test(`${skin}: spawn shared entity with fixed event and one-time yaw`, async () => {
    const f = fixture();
    const command = { ...f.command(`avatar_${skin}_spawn_near_player`), entityId: "minecraft:wither", event: "bad", location: { x: 900 } };
    assert.equal(await handleAvatarCommand(command, f.helpers), true);
    assert.equal(f.spawns.length, 1);
    const spawn = f.spawns[0];
    assert.equal(spawn.type, "ichiyon:avatar");
    assert.deepEqual(spawn.location, { x: 0, y: 64, z: 2 });
    assert.equal(spawn.entity.event, `ichiyon:${skin}`);
    assert.deepEqual(spawn.entity.rotation, { x: 0, y: 75 });
    assert.equal(spawn.entity.removed, false);
    assert.deepEqual(f.results, [["request-1", "succeeded", "ok", ""]]);
  });
  test(`${skin}: remove only closest matching current variant`, async () => {
    const f = fixture();
    const far = makeEntity(index, 9), near = makeEntity(index, 2);
    const other = makeEntity((index + 1) % 4, 1), outside = makeEntity(index, 17);
    const unrelated = { ...makeEntity(index, 1), typeId: "ichiyon:taketumi" };
    f.entities.push(far, other, outside, unrelated, near);
    await handleAvatarCommand(f.command(`avatar_${skin}_remove_near_player`), f.helpers);
    assert.equal(near.removed, true);
    for (const e of [far, other, outside, unrelated]) assert.equal(e.removed, false);
    assert.equal(f.results[0][1], "succeeded");
    assert.match(f.results[0][3], /1体/);
  });
}

test("all removes all variants in range, including duplicate skins", async () => {
  const f = fixture();
  f.entities.push(...[0, 1, 2, 3, 3].map((v, i) => makeEntity(v, i + 1)));
  const outside = makeEntity(0, 17);
  f.entities.push(outside);
  await handleAvatarCommand(f.command("avatar_all_remove_near_player"), f.helpers);
  assert.equal(f.entities.filter(e => e.removed).length, 5);
  assert.equal(outside.removed, false);
  assert.match(f.results[0][3], /5体/);
});

test("empty selection succeeds with zero", async () => {
  const f = fixture();
  await handleAvatarCommand(f.command("avatar_mei_remove_near_player"), f.helpers);
  assert.equal(f.results[0][1], "succeeded");
  assert.match(f.results[0][3], /0体/);
});

test("unknown and prototype property commands have no effects", async () => {
  const f = fixture();
  for (const type of ["__proto__", "constructor", "avatar_unknown", "say hello", undefined]) {
    assert.equal(await handleAvatarCommand(f.command(type), f.helpers), false);
  }
  assert.deepEqual(f.results, []);
  assert.deepEqual(f.spawns, []);
});

test("missing request, invalid names and offline players fail before world access", async () => {
  const f = fixture();
  f.player.dimension = new Proxy({}, { get() { throw new Error("world accessed"); } });
  const command = f.command("avatar_kiana_spawn_near_player");
  await handleAvatarCommand({ ...command, request_id: "" }, f.helpers);
  assert.deepEqual(f.results, []);
  for (const value of ["", "@e", "a;kill", "a b", "a/b", "x".repeat(17)]) {
    await handleAvatarCommand({ ...command, minecraft_player: value }, f.helpers);
    assert.equal(f.results.at(-1)[2], "invalid_player_name");
  }
  await handleAvatarCommand({ ...command, minecraft_player: "Offline" }, f.helpers);
  assert.equal(f.results.at(-1)[2], "player_offline");
});

for (const failure of ["spawn", "event", "rotation", "cleanup"]) {
  test(`${failure} failure does not report success`, async () => {
    const f = fixture();
    const entity = makeEntity(0, 2);
    const fail = () => { throw new Error("test failure"); };
    f.player.dimension.spawnEntity = failure === "spawn" ? fail : () => entity;
    if (failure === "event" || failure === "cleanup") entity.triggerEvent = fail;
    if (failure === "rotation") entity.setRotation = fail;
    if (failure === "cleanup") entity.remove = fail;
    await handleAvatarCommand(f.command("avatar_bronya_spawn_near_player"), f.helpers);
    assert.deepEqual(f.results, [["request-1", "failed", failure === "cleanup" ? "avatar_cleanup_failed" : "avatar_spawn_failed", ""]]);
    if (failure === "event" || failure === "rotation") assert.equal(entity.removed, true);
  });
}

test("partial deletion fails; missing variants fail before deletion", async () => {
  for (const missing of [false, true]) {
    const f = fixture();
    const first = makeEntity(0, 1), second = makeEntity(1, 2);
    if (missing) second.getComponent = () => undefined;
    else second.remove = () => { throw new Error("remove failed"); };
    f.entities.push(first, second);
    await handleAvatarCommand(f.command("avatar_all_remove_near_player"), f.helpers);
    assert.equal(first.removed, !missing);
    assert.deepEqual(f.results, [["request-1", "failed", "avatar_remove_failed", ""]]);
  }
});
