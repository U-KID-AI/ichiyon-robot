import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { WARP_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_warp_generated.js";
import { createWarpSystem } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/warp.js";

const fixtureCatalog = (readiness = "verified") => ({
  WARP_DIMENSION: "minecraft:overworld",
  WARP_RETURN_POINT: { location: [10, 65, 20], rotation: 180 },
  PROTECTED_ANCHOR: {
    label: "拠点行き",
    command_block: [1, 65, 3],
    sign: [1, 65, 2],
    button: [1, 66, 3],
  },
  WARP_CONTROLS: [{
    id: "fixture", title: "検証ゲーム", readiness,
    enabled: readiness === "verified" || readiness === "implemented",
    command_block: [4, 65, 3], sign: [4, 65, 2], button: [4, 66, 3],
    command: "scriptevent junket:warp fixture",
  }],
});

function fixture({ readiness = "verified", occupied = false, playerInside = false, route = null, failedCommand = null } = {}) {
  const events = {}, commands = [], messages = [];
  const catalog = fixtureCatalog(readiness);
  if (occupied) catalog.WARP_CONTROLS[0].sign = [3, 65, 2];
  const anchor = catalog.PROTECTED_ANCHOR;
  const fixed = new Map([
    [`${anchor.command_block}`, "minecraft:command_block"],
    [`${anchor.sign}`, "minecraft:wall_sign"],
    [`${anchor.button}`, "minecraft:wooden_button"],
  ]);
  if (occupied) fixed.set(`${catalog.WARP_CONTROLS[0].sign}`, "minecraft:stone");
  const dimension = {
    id: "minecraft:overworld",
    getBlock({ x, y, z }) {
      const typeId = fixed.get(`${[x, y, z]}`) ?? "minecraft:air";
      return { typeId, isAir: typeId === "minecraft:air", dimension, location: { x, y, z } };
    },
    runCommand(command) {
      commands.push(command);
      return { successCount: failedCommand && command.includes(failedCommand) ? 0 : 1 };
    },
  };
  const world = {
    getDimension: id => {
      assert.equal(id, "minecraft:overworld");
      return dimension;
    },
    beforeEvents: {
      playerInteractWithBlock: { subscribe: callback => { events.block = callback; } },
      playerInteractWithEntity: { subscribe: callback => { events.entity = callback; } },
    },
  };
  const system = {
    run: callback => callback(),
    afterEvents: { scriptEventReceive: { subscribe: callback => { events.script = callback; } } },
  };
  const player = {
    id: "p1", typeId: "minecraft:player", teleports: [],
    sendMessage: message => messages.push(message),
    teleport(location, options) { this.teleports.push({ location, options }); },
  };
  const manager = { forPlayer: () => playerInside ? { id: "table" } : null };
  const warp = createWarpSystem({
    world, system, manager, catalog,
    destinations: { fixture: () => route ?? ({ location: { x: 40, y: 70, z: 50 }, dimension: "minecraft:overworld", rotation: 90 }) },
  });
  return { catalog, events, commands, messages, player, warp, dimension };
}

assert.equal(WARP_CONFIG.WARP_CONTROLS.length, 17);
assert.equal(new Set(WARP_CONFIG.WARP_CONTROLS.map(game => game.title)).size, 17);
assert.ok(WARP_CONFIG.WARP_CONTROLS.filter(game => game.id === "temperance")[0].title === "ブルー・テンパランス");
const sourceCatalog = JSON.parse(readFileSync(new URL("../scripts/minecraft/junket_games.json", import.meta.url)));
assert.deepEqual(WARP_CONFIG.PROTECTED_ANCHOR, sourceCatalog.controls.protected_anchor);
assert.deepEqual(WARP_CONFIG.WARP_RETURN_POINT, sourceCatalog.controls.return_point);

{
  const f = fixture({ readiness: "venue-review" });
  const target = { dimension: f.dimension, location: { x: 4, y: 66, z: 3 } };
  const event = { player: f.player, block: target, cancel: false };
  f.events.block(event);
  assert.equal(event.cancel, true);
  assert.equal(f.player.teleports.length, 0);
  assert.match(f.messages.at(-1), /準備中/);
}

{
  let opened = false;
  const f = fixture({ readiness: "implemented", route: { open: () => { opened = true; } } });
  f.events.block({
    player: f.player,
    block: { dimension: f.dimension, location: { x: 4, y: 66, z: 3 } },
    cancel: false,
  });
  assert.equal(opened, true);
  assert.equal(f.player.teleports.length, 0);
}

{
  const f = fixture();
  const target = { dimension: f.dimension, location: { x: 4, y: 66, z: 3 } };
  f.events.block({ player: f.player, block: target, cancel: false });
  assert.deepEqual(f.player.teleports, [{
    location: { x: 40, y: 70, z: 50 },
    options: { dimension: f.dimension, rotation: { x: 0, y: 90 } },
  }]);
}

{
  let prepared = false;
  const f = fixture({
    route: {
      location: { x: 40, y: 70, z: 50 },
      dimension: "minecraft:overworld",
      rotation: 0,
      afterTeleport: player => { prepared = player.id === "p1"; },
    },
  });
  f.events.block({
    player: f.player,
    block: { dimension: f.dimension, location: { x: 4, y: 66, z: 3 } },
    cancel: false,
  });
  assert.equal(prepared, true, "venue entities are prepared only after teleport");
}

{
  const f = fixture({ playerInside: true });
  assert.equal(f.warp.returnToEntry(f.player), false);
  assert.equal(f.player.teleports.length, 0);
  assert.match(f.messages.at(-1), /退出操作/);
}

{
  const f = fixture();
  assert.equal(f.warp.returnToEntry(f.player), true);
  assert.deepEqual(f.player.teleports, [{
    location: { x: 10, y: 65, z: 20 },
    options: { dimension: f.dimension, rotation: { x: 0, y: 180 } },
  }]);
}

{
  const f = fixture({ occupied: true });
  assert.equal(f.warp.installControls(f.player), false);
  assert.equal(f.commands.length, 0);
  assert.match(f.messages.at(-1), /空ではありません/);
}

{
  const f = fixture();
  assert.equal(f.warp.installControls(f.player), true);
  assert.equal(f.commands.filter(command => command.startsWith("setblock ")).length, 3);
  assert.ok(f.commands.every(command => !command.includes("1 65 3") && !command.includes("1 66 3")));
}

{
  const f = fixture({ failedCommand: "blockdata 4 65 2" });
  assert.equal(f.warp.installControls(f.player), false);
  assert.match(f.messages.at(-1), /ブロック操作に失敗/);
  assert.ok(f.commands.some(command => command === "setblock 4 65 2 minecraft:air"));
  assert.ok(f.commands.some(command => command === "setblock 4 65 3 minecraft:air"));
}

console.log("PASS 17-game warp catalog, exact-player teleport, readiness gate, return guard, anchor protection and empty-area preflight");
