import assert from "node:assert/strict";
import { createSafeWarpController } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/safe_warp.js";
import { getSharedLoader, inspectArrival } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/shared_loader.js";

function harness({ full = false, neverLoaded = false, hazard = null, missingRoute = false, manager,
  floorType = "minecraft:stone", bodyType = "minecraft:air", exposeIsSolid = true,
  gameId = "sparrow", target: suppliedTarget } = {}) {
  let tick = 0, readOnly = false, maxOwned = 0, lastRemove = -Infinity;
  const tasks = [], commands = [], messages = [], existing = new Set(["narita_probe", "oreka_fix", "koumou_fix", "narita_fix"]);
  const areas = new Set(existing), players = [], entities = [];
  const target = suppliedTarget ?? { x: 20022, y: 67, z: 20021 };
  const system = { get currentTick() { return tick; },
    run: callback => tasks.push({ tick: tick + 1, callback }),
    runTimeout: (callback, delay = 1) => tasks.push({ tick: tick + Math.max(1, delay), callback }) };
  const d = { id: "minecraft:overworld", heightRange: { min: -64, max: 320 },
    runCommand(command) {
      assert.equal(readOnly, false, "read-only before event cannot mutate world"); commands.push(command);
      const parts = command.split(" ");
      if (parts[1] === "add") {
        if (full) throw new Error("tickingarea limit 10");
        assert.equal(areas.size, existing.size, "one shared temporary region at most");
        assert.ok(tick - lastRemove >= 40, "async unload must settle before next add");
        areas.add(parts.at(-2)); maxOwned = Math.max(maxOwned, areas.size - existing.size);
      } else if (parts[1] === "remove") {
        assert.ok(!existing.has(parts[2]), "existing areas cannot be removed");
        assert.ok(areas.delete(parts[2]), "only this loader's lease can be removed");
        lastRemove = tick;
      }
      return { successCount: 1 };
    },
    getBlock(at) {
      if (neverLoaded || areas.size === existing.size) return undefined;
      const floor = at.y === (at.x < 15000 ? 64 : target.y - 1);
      const typeId = hazard && at.x === Math.floor(target.x + .31) && at.z === Math.floor(target.z + .31) &&
        at.y === (hazard === "head" ? target.y + 1 : target.y - 1)
        ? hazard === "head" ? "minecraft:stone" : "minecraft:lava" : floor ? floorType : bodyType;
      return { typeId, ...(exposeIsSolid ? { isSolid: typeId === "minecraft:stone" } : {}),
        isLiquid: typeId === "minecraft:lava" };
    },
    spawnEntity(typeId, at) {
      const entity = { typeId, location: { ...at }, dimension: d, removed: false, addTag() {},
        teleport(point, options) { assert.equal(options.checkForBlocks, true); this.location = { ...point }; this.dimension = options.dimension; },
        remove() { this.removed = true; } };
      entities.push(entity); return entity;
    } };
  const world = { getAllPlayers: () => players, getDimension: () => d };
  function player(id) {
    const p = { id, typeId: "minecraft:player", dimension: d, location: { x: 10048, y: 66, z: 10102 }, warps: 0,
      sendMessage(message) { assert.equal(readOnly, false); messages.push([id, message]); },
      addTag() { assert.equal(readOnly, false); throw new Error("native presser never needs tags"); },
      removeTag() { assert.equal(readOnly, false); throw new Error("native presser never needs tags"); },
      teleport(at, options) { assert.equal(readOnly, false); assert.equal(options.checkForBlocks, true);
        this.warps++; this.location = { ...at }; this.dimension = options.dimension; } };
    players.push(p); return p;
  }
  const game = { id: gameId, title: "ウラギリスズメ", enabled: true, command_mode: "script_load", command_block: [10048, 65, 10102] };
  const catalog = { WARP_OFFLINE_INSTALLED: true, WARP_DIMENSION: d.id, WARP_CONTROLS: [game],
    WARP_RETURN_POINT: { location: [10052, 65, 10078], rotation: 180 } };
  const controller = createSafeWarpController({ world, system, catalog, manager,
    destinations: missingRoute ? {} : { [gameId]: () => ({ location: target, dimension: d, rotation: 0 }) } });
  function press(p) { readOnly = true; controller.capture({ player: p }, game); readOnly = false; }
  async function drain() {
    for (let round = 0; round < 5000; round++) {
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
      if (!tasks.length) {
        await new Promise(resolve => setImmediate(resolve));
        if (!tasks.length) return;
      }
      tasks.sort((a, b) => a.tick - b.tick);
      const task = tasks.shift(); tick = Math.max(tick, task.tick); task.callback();
    }
    throw new Error("scheduler did not quiesce");
  }
  return { world, system, d, target, player, press, drain, controller, game, players, messages, commands, areas,
    existing, entities, get maxOwned() { return maxOwned; } };
}

{
  const h = harness(), a = h.player("actual-presser"), b = h.player("second-presser"), observer = h.player("nearby-bystander");
  h.press(a); h.press(a); h.press(b);
  assert.equal(h.commands.length, 0, "before events only capture immutable player identity");
  h.controller.handleRequest({ message: "sparrow", sourceBlock: { dimension: h.d, location: { x: 10048, y: 65, z: 10102 } } });
  await h.drain();
  assert.equal(a.warps, 1); assert.equal(b.warps, 1); assert.equal(observer.warps, 0);
  assert.equal(h.maxOwned, 1); assert.deepEqual(h.areas, h.existing); assert.equal(h.controller.pending(), 0);
}
for (const change of ["move", "logout", "timeout"]) {
  const h = harness(), p = h.player(change);
  const loader = getSharedLoader(h.world, h.system);
  if (change === "timeout") void loader.withArea({ dimension: h.d, bounds: [0, 0, 0, 16, 1, 16], timeoutTicks: 4000 },
    async context => context.wait(1300));
  h.press(p);
  if (change === "move") p.location.x += 3;
  if (change === "logout") h.players.splice(0);
  await h.drain();
  assert.equal(p.warps, 0, `${change} cancels transfer`); assert.deepEqual(h.areas, h.existing);
}
for (const options of [{ full: true }, { neverLoaded: true }, { hazard: "head" }, { hazard: "floor" }]) {
  const h = harness(options), p = h.player("unsafe"); h.press(p); await h.drain();
  assert.equal(p.warps, 0); assert.deepEqual(h.areas, h.existing); assert.equal(h.controller.pending(), 0);
  assert.ok(h.messages.some(([, text]) => /完了できません/.test(text)));
}
{
  const h = harness(), oldSession = h.player("rejoining-player");
  let newSession;
  h.press(oldSession);
  h.system.runTimeout(() => {
    h.controller.cancel(oldSession.id); h.players.splice(0);
    newSession = h.player(oldSession.id); h.press(newSession);
  }, 6);
  await h.drain();
  assert.equal(oldSession.warps, 0, "old queued session remains cancelled after same-ID rejoin");
  assert.equal(newSession.warps, 1, "old cleanup cannot delete the new session request");
  assert.equal(h.controller.pending(), 0); assert.deepEqual(h.areas, h.existing);
}
{
  const h = harness({ missingRoute: true }), p = h.player("missing-route");
  assert.equal(await h.controller.warp(p, "sparrow"), false);
  assert.equal(h.controller.pending(), 0);
}
{
  const h = harness();
  assert.equal(inspectArrival(h.d, { x: 0, y: 320, z: 0 }).safe, false);
  h.controller.handleRequest({ message: "sparrow" }); await h.drain();
  assert.equal(h.commands.length, 0, "script event cannot invent a presser or choose @p");
}
console.log("PASS safe warp: strict two-presser identity, readonly capture, duplicate suppression, shared leases, existing areas, movement/logout/rejoin/timeout, ten-area refusal, unloaded/hazard/headroom, missing route");
{
  const manager = { recoveringGames: new Set(), forPlayer: () => ({ gameId: "sparrow" }) };
  const h = harness({ manager }), p = h.player("active-participant");
  assert.equal(await h.controller.returnToEntryAndWait(p), false, "ordinary return cannot escape an active table");
  await assert.rejects(() => h.controller.evacuateForRecovery(p, "sparrow"), /ロック/);
  manager.recoveringGames.add("lucy");
  assert.equal(await h.controller.evacuateForRecovery(p, "lucy"), false, "another venue lock cannot authorize this player");
  manager.recoveringGames.add("sparrow");
  const transfer = h.controller.evacuateForRecovery(p, "sparrow");
  await h.drain();
  assert.equal(await transfer, true);
  assert.equal(p.warps, 1);
  assert.deepEqual(p.location, { x: 10052, y: 65, z: 10078 });
  assert.deepEqual(h.areas, h.existing);
}
console.log("PASS recovery evacuation: matching locked venue only, awaited safe arrival, active membership preserved until recovery ends");
{
  const h = harness({ gameId: "temperance", target: { x: 20105.5, y: 67, z: 20038.5 },
    floorType: "minecraft:quartz_pillar", bodyType: "minecraft:light_block_12", exposeIsSolid: false });
  const player = h.player("temperance-presser");
  const pending = h.controller.warp(player, "temperance");
  await h.drain();
  assert.equal(await pending, true, "API 2.0 lacks isSolid; original quartz pillar is still a full floor");
  assert.equal(player.warps, 1); assert.deepEqual(player.location, h.target);
  assert.deepEqual(h.areas, h.existing);
}
for (const options of [
  { floorType: "minecraft:quartz_pillar", bodyType: "minecraft:light_block_12", expectedSafe: true },
  { floorType: "minecraft:quartz_slab", expectedSafe: false },
  { floorType: "minecraft:quartz_pillar", hazard: "head", expectedSafe: false },
  { floorType: "minecraft:quartz_pillar", hazard: "floor", expectedSafe: false },
  { floorType: "minecraft:unknown_pillar", expectedSafe: false },
]) {
  const h = harness({ ...options, exposeIsSolid: false });
  const pending = h.controller.checkAll("arrival-floor-regression");
  await h.drain();
  const result = (await pending).checks[0];
  assert.equal(result.loaded_before, false);
  assert.equal(result.loaded_after, true, "unsafe floor/headroom must not be misreported as a loading failure");
  assert.equal(result.safe, options.expectedSafe);
  assert.equal(result.teleported, options.expectedSafe);
  assert.equal(result.cleaned, true); assert.deepEqual(h.areas, h.existing);
}
{
  const at = { x: 20105.5, y: 67, z: 20038.5 };
  const partial = { getBlock: cell => cell.y === 66 ? { typeId: "minecraft:quartz_slab" } : undefined };
  assert.equal(inspectArrival(partial, at).loaded, false, "all player-volume cells must be loaded before a floor decision");
}
console.log("PASS Temperance arrival: quartz pillar with API 2.0 fields, light-block headroom, exact destination, unsafe partial floors/unknown pillar/hazards rejected, loaded and safe proof separated");
