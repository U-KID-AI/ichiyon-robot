import assert from "node:assert/strict";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";
import { makeQualityCase, QUALITY_GAMES } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/quality_cases.js";
import { createVenueRecovery } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue_recovery.js";
import { anchorVenueFixtures } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/fixture_anchor.js";

function scene(id = "sparrow", confirm = async () => true) {
  const sample = makeQualityCase(id, "reset");
  const properties = new Map(), order = [], timers = new Map(), leases = [];
  let loadedId = null;
  let players = sample.initial.players.map(playerId => ({ id: playerId, name: playerId,
    typeId: "minecraft:player", dimension: { id: "minecraft:overworld" }, location: { x: 2, y: 70, z: 2 },
    sendMessage() {} }));
  const leaveHandlers = [];
  const world = { getAllPlayers: () => players, getDynamicProperty: key => properties.get(key),
    setDynamicProperty: (key, value) => properties.set(key, value),
    afterEvents: { playerLeave: { subscribe: callback => leaveHandlers.push(callback) } } };
  const system = { currentTick: 0, runTimeout(callback) { const key = timers.size + 1; timers.set(key, callback); return key; },
    clearRun(key) { timers.delete(key); } };
  const manager = new GameManager([sample.game], { world, system });
  for (const player of players) manager.join(id, id, player.id);
  if (!manager.view(id).state) manager.start(id);
  const venue = { id, title: id, origin: [0, 64, 0], size: [20, 20, 20], fixtures: [] };
  const venues = { entries: [venue], async withLoaded(gameId, action) {
    assert.equal(loadedId, null, "shared loader must not be acquired recursively");
    loadedId = gameId; leases.push(`load:${gameId}`);
    try { return await action({ guard() { assert.equal(loadedId, gameId); } }); }
    finally { loadedId = null; leases.push(`unload:${gameId}`); }
  }, async repair() { return venues.withLoaded(id, async () => { order.push("repair"); return { id }; }); },
    async restoreOriginal(gameId, options) { return venues.withLoaded(gameId, async () => {
      options.guard(); order.push("source"); await options.afterRestore(gameId); return { id };
    }); } };
  const runtimes = new Map([[id, { async resetSession(current, reason, options) {
    assert.equal(loadedId, id, `${id}: cold end() must load board blocks and auxiliary actors first`);
    assert.equal(options.evacuated, true); assert.ok(current.players.every(playerId => manager.forPlayer(playerId)));
    order.push("end");
  } }]]);
  const args = { world, system, manager, venues, runtimes, confirm,
    async evacuate(people) { order.push("evacuate"); for (const person of people) person.location = { x: -100, y: 70, z: -100 }; },
    async restoreServices() { order.push("services"); } };
  const recovery = createVenueRecovery(args);
  return { id, sample, properties, order, leases, world, system, timers, manager, venue, venues, runtimes, args, recovery,
    get loadedId() { return loadedId; },
    get players() { return players; }, leave(playerId) { players = players.filter(player => player.id !== playerId);
      for (const handler of leaveHandlers) handler({ playerId }); }, rejoin(player) { players.push(player); } };
}

for (const id of QUALITY_GAMES.map(game => game.id)) {
  const f = scene(id);
  const previous = f.manager.view(id);
  const result = await f.recovery.requestReset(f.players[0], id);
  assert.equal(result.completed, true);
  assert.deepEqual(f.order, ["evacuate", "end", "repair"]);
  assert.deepEqual(f.leases, [`load:${id}`, `unload:${id}`, `load:${id}`, `unload:${id}`]);
  assert.equal(f.manager.view(id), null);
  assert.equal(f.manager.members.size, 0);
  assert.equal(f.manager.recoveringGames.size, 0);
  assert.throws(() => f.manager.act(f.players[0].id, previous.token, "invalid", null), /古く/);
  for (const player of f.players) f.manager.join(id, id, player.id);
  assert.ok(f.manager.view(id).token > previous.token, `${id}: stale pre-reset forms cannot enter the rematch`);
}

// Actual BDS exposed cold idle resetSession -> end -> board/actor writes.
// Exercise both active cancellation and already-closed journal recovery for
// every game; source restoration owns a later, non-nested loading lease.
for (const id of QUALITY_GAMES.map(game => game.id)) {
  for (const idle of [false, true]) {
    const f = scene(id);
    if (idle) f.manager.close(id);
    await f.recovery.forceRecover(id, { sourceType: "Server" });
    assert.deepEqual(f.order, ["evacuate", "end", "source", "services"], `${id} idle=${idle}`);
    assert.deepEqual(f.leases, [`load:${id}`, `unload:${id}`, `load:${id}`, `unload:${id}`]);
    assert.equal(f.loadedId, null);
    assert.equal(f.manager.view(id), null);
  }
}

{
  const f = scene("hope");
  const reset = f.runtimes.get(f.id).resetSession;
  f.runtimes.get(f.id).resetSession = async (...args) => { await reset(...args); throw new Error("end interrupted"); };
  await assert.rejects(f.recovery.forceRecover(f.id, { sourceType: "Server" }), /end interrupted/);
  assert.equal(f.loadedId, null, "failed ending releases its lease");
  assert.ok(f.manager.view(f.id), "unfinished ending does not discard the table");
  assert.ok(f.manager.recoveringGames.has(f.id));
  assert.ok(!f.order.includes("source"), "failed ending cannot start a source restore");
  f.runtimes.get(f.id).resetSession = reset;
  await createVenueRecovery(f.args).resumePending();
  assert.equal(f.loadedId, null);
  assert.equal(f.manager.view(f.id), null);
  assert.equal(f.manager.recoveringGames.size, 0);
}

{
  const f = scene("sparrow", async player => player.id.endsWith("0"));
  const saved = f.manager.serialize();
  await assert.rejects(f.recovery.requestReset(f.players[0], f.id), /同意/);
  assert.deepEqual(f.manager.serialize(), saved);
  assert.deepEqual(f.order, []);
}
{
  let complete;
  const f = scene("sparrow", () => new Promise(resolve => { complete = resolve; }));
  const request = f.recovery.requestReset(f.players[0], f.id);
  const player = f.players[1]; f.leave(player.id); f.rejoin(player);
  // Both callbacks may remain open. Expiration must not close the table, and
  // a reconnect with the same entity ID must not restore the old consent.
  for (const callback of f.timers.values()) callback();
  complete(true);
  await assert.rejects(request, /同意/);
  assert.ok(f.manager.view(f.id));
}
{
  const f = scene();
  f.players[0].hasTag = () => true;
  await assert.rejects(async () => f.recovery.recover(f.id, { force: true, player: f.players[0] }), /権限/);
  await assert.rejects(async () => f.recovery.forceRecover(f.id, { sourceType: "Block", sourceBlock: {} }), /権限/);
  await assert.rejects(async () => f.recovery.forceRecover(f.id, { sourceType: "NPCDialogue" }), /権限/);
  f.players[0].playerPermissionLevel = 2;
  assert.equal(f.recovery.isOperator(f.players[0]), true);
  await f.recovery.recover(f.id, { force: true, player: f.players[0] });
  assert.deepEqual(f.order, ["evacuate", "end", "source", "services"]);
}
{
  const f = scene();
  let failed = true;
  f.venues.restoreOriginal = async (id, options) => { options.guard(); if (failed) throw new Error("power interruption"); return { id }; };
  await assert.rejects(f.recovery.forceRecover(f.id, { sourceType: "Server" }), /power/);
  assert.ok(f.manager.recoveringGames.has(f.id));
  assert.equal(f.manager.view(f.id), null);
  assert.throws(() => f.manager.join(f.id, f.id, f.players[0].id), /復旧/);
  failed = false;
  f.runtimes.get(f.id).resetSession = async () => {};
  const restarted = createVenueRecovery(f.args);
  const result = await restarted.resumePending();
  assert.equal(result.length, 1);
  assert.equal(f.manager.recoveringGames.size, 0);
  assert.deepEqual(await restarted.resumePending(), [], "completed recovery is restart-idempotent");
}
{
  const f = scene();
  const visited = [];
  f.venues.entries = QUALITY_GAMES.map((game, index) => ({ ...f.venue, id: game.id,
    origin: [index * 100, 64, 0], size: [20, 20, 20] }));
  for (const venue of f.venues.entries) f.runtimes.set(venue.id, { async resetSession() {} });
  f.venues.restoreOriginal = async (id, options) => {
    options.guard(); visited.push(id); return { id, bounds: f.venues.entries.find(value => value.id === id).origin };
  };
  const recovery = createVenueRecovery(f.args);
  const result = await recovery.forceRecover("all", { sourceType: "Server" });
  assert.equal(result.completed, 17);
  assert.deepEqual(visited, QUALITY_GAMES.map(game => game.id), "all means only the registered seventeen rooms");
  assert.equal(f.manager.recoveringGames.size, 0);
}

{
  const venue = { id: "sparrow", origin: [100, 60, 100], size: [10, 10, 10], provider: { collection: "owned" },
    fixtures: ["tv", "bigbox"].map((role, i) => ({ role, type: `junket:${role}`, location: [102 + i, 65, 102], rotation: 180 })) };
  const entity = (role, owner, x = 102, y = 250) => ({ typeId: `junket:${role}`, location: { x, y, z: 102 },
    dimension: { id: "minecraft:overworld" }, getDynamicProperty: key => key === "junket:venue_id" ? owner : null,
    hasTag: tag => tag === `junket:sparrow:fixture:${role}`, velocityCleared: 0,
    clearVelocity() { this.velocityCleared++; }, teleport(at, options) { this.location = at; this.rotation = options.rotation; } });
  const fixed = entity("tv", "sparrow"), moving = entity("bigbox", "sparrow", 103),
    unknown = entity("tv", "other"), otherRoom = entity("tv", "sparrow", 300);
  const world = { getDimension: () => ({ getEntities: () => [fixed, moving, unknown, otherRoom] }) };
  anchorVenueFixtures(world, venue, { active: true });
  assert.equal(fixed.location.y, 65); assert.equal(moving.location.y, 250);
  assert.equal(unknown.location.y, 250); assert.equal(otherRoom.location.y, 250);
  assert.equal(moving.velocityCleared, 0, "gameplay moving actor must not be frozen");
  anchorVenueFixtures(world, venue, { idle: true });
  assert.equal(moving.location.y, 65);
  assert.equal(anchorVenueFixtures(world, venue, { idle: true }).anchored, 0);
}
console.log("PASS all17 unanimous reset and cold active/idle recovery leases, stale tokens, rejection/logout, real permissions, interrupted-end/restart recovery and scoped high-Y anchors (offline)");
