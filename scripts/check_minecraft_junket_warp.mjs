import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { WARP_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_warp_generated.js";
import { createWarpSystem } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/warp.js";

const sourceCatalog = JSON.parse(readFileSync(new URL("../scripts/minecraft/junket_games.json", import.meta.url)));
const keyOf = ([x, y, z]) => `${x},${y},${z}`;
function parseCommandBlockSnbt(value) {
  const field = name => {
    const marker = `${name}:`;
    const index = value.indexOf(marker);
    if (index < 0) return undefined;
    const start = index + marker.length;
    if (value[start] !== '"') return undefined;
    let end = start + 1;
    while (end < value.length) {
      if (value[end] === "\\") { end += 2; continue; }
      if (value[end] === '"') break;
      end++;
    }
    return JSON.parse(value.slice(start, end + 1));
  };
  return { Command: field("Command"), CustomName: field("CustomName") };
}
const cloneCatalog = () => ({
  ...WARP_CONFIG,
  PROTECTED_ANCHOR: { ...WARP_CONFIG.PROTECTED_ANCHOR },
  WARP_CONTROLS: WARP_CONFIG.WARP_CONTROLS.map(game => ({
    ...game,
    command_block: [...game.command_block],
    sign: [...game.sign],
    button: [...game.button],
  })),
});

function fixture({
  catalog = WARP_CONFIG,
  overrides = new Map(),
  unloadedControlReads = 0,
  unloadedBlockReads = new Map(),
  unloadedSignReads = new Map(),
  ignoreSignText = false,
  route,
} = {}) {
  const events = {};
  const commands = [];
  const messages = [];
  const signTexts = new Map();
  const commandData = new Map();
  const blocks = new Map();
  const entities = [];
  const entityQueries = [];
  const deferredTimeouts = [];
  let tickingLoaded = false;

  const addBlock = (position, typeId, states = {}) => {
    const at = keyOf(position);
    blocks.set(at, { typeId, states: { ...states }, location: { x: position[0], y: position[1], z: position[2] } });
  };
  const anchor = catalog.PROTECTED_ANCHOR;
  addBlock(anchor.command_block, anchor.command_block_type, anchor.command_block_states);
  addBlock(anchor.sign, anchor.sign_type, anchor.sign_states);
  addBlock(anchor.button, anchor.button_type, anchor.button_states);
  signTexts.set(keyOf(anchor.sign), "拠点行き");
  for (const game of catalog.WARP_CONTROLS) {
    addBlock(game.command_block, game.command_block_type, game.command_block_states);
    addBlock(game.sign, game.sign_type, game.sign_states);
    addBlock(game.button, game.button_type, game.button_states);
    signTexts.set(keyOf(game.sign), "");
  }
  for (const cell of catalog.MISPLACED_ROW_CLEANUP?.cells ?? []) {
    addBlock(cell.location, cell.restore_type);
  }
  for (const [position, value] of overrides) {
    if (value === null) blocks.delete(position);
    else if (typeof value === "string") addBlock(position.split(",").map(Number), value);
    else addBlock(position.split(",").map(Number), value.typeId, value.states);
  }

  const dimension = {
    id: catalog.WARP_DIMENSION,
    getBlock({ x, y, z }) {
      const at = `${x},${y},${z}`;
      const unloaded = unloadedBlockReads.get(at) ?? 0;
      if (unloaded > 0) {
        unloadedBlockReads.set(at, unloaded - 1);
        return undefined;
      }
      if (unloadedControlReads > 0 && !blocks.has(at)) {
        unloadedControlReads -= 1;
        return undefined;
      }
      const state = blocks.get(at);
      if (!state) {
        return {
          typeId: "minecraft:air", isAir: true, dimension, location: { x, y, z },
          permutation: { getState: () => undefined },
        };
      }
      const block = {
        typeId: state.typeId,
        isAir: state.typeId === "minecraft:air",
        dimension,
        location: { x, y, z },
        permutation: {
          getState(name) { return state.states[name]; },
          getAllStates() { return { ...state.states }; },
        },
        getComponent(component) {
          if (component !== "minecraft:sign" || !state.typeId.endsWith("_sign")) return undefined;
          const unloaded = unloadedSignReads.get(at) ?? 0;
          if (unloaded > 0) {
            unloadedSignReads.set(at, unloaded - 1);
            return undefined;
          }
          return {
            getText: () => signTexts.get(at),
            setText(text) { if (!ignoreSignText) signTexts.set(at, text); },
          };
        },
      };
      return block;
    },
    runCommand(command) {
      commands.push(command);
      if (command.startsWith("tickingarea add ")) {
        tickingLoaded = true;
        return { successCount: 1 };
      }
      if (command.startsWith("tickingarea remove ")) {
        tickingLoaded = false;
        return { successCount: 1 };
      }
      const blockdata = command.match(/^blockdata (-?\d+) (-?\d+) (-?\d+) (\{.*\})$/s);
      if (blockdata) {
        const at = keyOf(blockdata.slice(1, 4).map(Number));
        const data = parseCommandBlockSnbt(blockdata[4]);
        if (data.Command !== undefined) commandData.set(at, data);
        return { successCount: 1 };
      }
      const setblock = command.match(/^setblock (-?\d+) (-?\d+) (-?\d+) (minecraft:[a-z_]+)$/);
      if (setblock) {
        blocks.set(keyOf(setblock.slice(1, 4).map(Number)), {
          typeId: setblock[4], states: {},
          location: { x: Number(setblock[1]), y: Number(setblock[2]), z: Number(setblock[3]) },
        });
        return { successCount: 1 };
      }
      return { successCount: 1 };
    },
    getEntities({ tags = [], location: center, maxDistance }) {
      entityQueries.push({ tags: [...tags], location: center, maxDistance });
      return entities.filter(entity => !entity.removed &&
        tags.every(tag => entity.tags.has(tag)) &&
        Math.hypot(entity.location.x - center.x, entity.location.z - center.z) <= maxDistance);
    },
  };
  const world = {
    getDimension(id) {
      assert.equal(id, catalog.WARP_DIMENSION);
      return dimension;
    },
    getAllPlayers: () => [player, otherPlayer],
    beforeEvents: {
      playerInteractWithBlock: { subscribe: callback => { events.block = callback; } },
      playerInteractWithEntity: { subscribe: callback => { events.entity = callback; } },
    },
    afterEvents: {
      playerSpawn: { subscribe: callback => { events.playerSpawn = callback; } },
    },
    structureManager: {
      place() { throw new Error("warp controls must never place structures"); },
    },
  };
  const system = {
    run: callback => callback(),
    runTimeout: (callback, ticks) => {
      if (ticks === 30) {
        deferredTimeouts.push(callback);
        return deferredTimeouts.length;
      }
      callback();
      return 0;
    },
    afterEvents: {
      scriptEventReceive: { subscribe: callback => { events.script = callback; } },
    },
  };
  const makePlayer = (id, name) => ({
    id, name: name ?? id, typeId: "minecraft:player", teleports: [], tags: new Set(),
    sendMessage: message => messages.push(`${id}: ${message}`),
    teleport(location, options) { this.teleports.push({ location, options }); },
    addTag(tag) { this.tags.add(tag); },
    removeTag(tag) { this.tags.delete(tag); },
    hasTag(tag) { return this.tags.has(tag); },
  });
  const player = makePlayer("p1", "Player One");
  const otherPlayer = makePlayer("p2", "Player Two");
  const manager = { forPlayer: () => null };
  const destinations = Object.fromEntries(catalog.WARP_CONTROLS.map((game, index) => [
    game.id,
    () => route ?? ({
      location: { x: 20000 + index * 96, y: 67, z: 20001 },
      dimension: catalog.WARP_DIMENSION,
      rotation: 180,
    }),
  ]));
  const warp = createWarpSystem({ world, system, manager, catalog, destinations });
  return {
    catalog, events, commands, messages, player, otherPlayer,
    warp, dimension, blocks, entities, signTexts, commandData, ticking: () => tickingLoaded,
    entityQueries,
    flushWarpTimeouts() {
      const pending = deferredTimeouts.splice(0);
      for (const callback of pending) callback();
    },
  };
}

assert.equal(WARP_CONFIG.WARP_CONTROL_SCHEMA, 3);
assert.equal(WARP_CONFIG.WARP_CONTROLS.length, 17);
assert.equal(WARP_CONFIG.MISPLACED_ROW_CLEANUP.cells.length, 53);
assert.equal(new Set(WARP_CONFIG.WARP_CONTROLS.map(game => game.title)).size, 17);
assert.equal(WARP_CONFIG.WARP_CONTROLS.find(game => game.id === "temperance").title, "ブルー・テンパランス");
assert.deepEqual(WARP_CONFIG.PROTECTED_ANCHOR, sourceCatalog.controls.protected_anchor);
assert.deepEqual(WARP_CONFIG.WARP_RETURN_POINT, sourceCatalog.controls.return_point);
assert.deepEqual(
  WARP_CONFIG.WARP_CONTROLS.map(game => game.command_block),
  Array.from({ length: 17 }, (_, index) => [10048, 65, 10102 - index * 2]),
);
assert.ok(WARP_CONFIG.WARP_CONTROLS.every(game =>
  !("structure" in game) && !("structure_origin" in game)));
const warpSource = readFileSync(new URL(
  "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/warp.js", import.meta.url,
), "utf8");
assert.ok(!warpSource.includes("structureManager.place"), "warp controls must only update existing block entities");

{
  const f = fixture();
  const game = f.catalog.WARP_CONTROLS[0];
  f.events.block({
    player: f.player,
    block: { dimension: f.dimension, location: { x: game.button[0], y: game.button[1], z: game.button[2] } },
    cancel: false,
  });
  assert.equal(f.player.hasTag(`junket:warp_${game.id}`), true);
  assert.equal(f.otherPlayer.hasTag(`junket:warp_${game.id}`), false);
  assert.equal(f.player.teleports.length, 0,
    "the command block, not the interaction callback, performs the transfer");
  f.events.script({ id: "junket:warp", message: game.id, sourceEntity: undefined });
  assert.deepEqual(f.player.teleports, [{
    location: { x: 20000, y: 67, z: 20001 },
    options: { dimension: f.dimension, rotation: { x: 0, y: 180 } },
  }]);
  assert.equal(f.otherPlayer.teleports.length, 0, "another player must never be transferred");
  f.flushWarpTimeouts();
  assert.equal(f.player.teleports.length, 1, "the timeout fallback must not repeat a completed warp");
}

{
  const f = fixture();
  const game = f.catalog.WARP_CONTROLS[0];
  const block = { dimension: f.dimension, location: { x: game.button[0], y: game.button[1], z: game.button[2] } };
  f.events.block({ player: f.player, block, cancel: false });
  f.events.block({ player: f.otherPlayer, block, cancel: false });
  assert.equal(f.player.teleports.length, 1, "the first presser moves exactly once");
  assert.equal(f.otherPlayer.teleports.length, 0,
    "the later presser must not inherit the displaced presser's event");
  f.events.script({ id: "junket:warp", message: game.id, sourceEntity: undefined });
  assert.equal(f.player.teleports.length, 1);
  assert.equal(f.otherPlayer.teleports.length, 1, "the second presser moves exactly once");
  f.flushWarpTimeouts();
  assert.equal(f.player.teleports.length, 1);
  assert.equal(f.otherPlayer.teleports.length, 1);
}

{
  const f = fixture();
  const game = f.catalog.WARP_CONTROLS[0];
  const block = { dimension: f.dimension, location: { x: game.button[0], y: game.button[1], z: game.button[2] } };
  f.events.block({ player: f.player, block, cancel: false });
  f.events.script({ id: "junket:warp", message: `${game.id} ${f.otherPlayer.name}`, sourceEntity: undefined });
  assert.equal(f.otherPlayer.teleports.length, 0,
    "a forged player name must never select another player");
  assert.equal(f.player.teleports.length, 1,
    "the sole tagged presser remains the only transfer target");
  assert.equal(f.player.hasTag(`junket:warp_${game.id}`), false);
  f.flushWarpTimeouts();
  assert.equal(f.player.teleports.length, 1);
  assert.equal(f.otherPlayer.teleports.length, 0);
}

{
  const f = fixture();
  const game = f.catalog.WARP_CONTROLS[0];
  const block = { dimension: f.dimension, location: { x: game.button[0], y: game.button[1], z: game.button[2] } };
  f.events.block({ player: f.player, block, cancel: false });
  f.events.script({ id: "junket:warp", message: `${game.id} @s`, sourceEntity: undefined });
  assert.equal(f.player.teleports.length, 1,
    "a literal @s from a command block must use the sole tagged presser");
  assert.equal(f.otherPlayer.teleports.length, 0);
  f.flushWarpTimeouts();
  assert.equal(f.player.teleports.length, 1);
}

{
  const f = fixture();
  const game = f.catalog.WARP_CONTROLS[0];
  const block = { dimension: f.dimension, location: { x: game.button[0], y: game.button[1], z: game.button[2] } };
  f.events.block({ player: f.player, block, cancel: false });
  f.events.block({ player: f.otherPlayer, block, cancel: false });
  f.events.script({ id: "junket:warp", message: game.id, sourceEntity: undefined });
  assert.equal(f.player.teleports.length, 1,
    "the displaced earlier presser must be handed off exactly once");
  assert.equal(f.otherPlayer.teleports.length, 1,
    "a legacy event may consume the sole remaining pending presser");
  f.flushWarpTimeouts();
  assert.equal(f.player.teleports.length, 1);
  assert.equal(f.otherPlayer.teleports.length, 1);
}

{
  const f = fixture();
  const game = f.catalog.WARP_CONTROLS[0];
  f.events.block({
    player: f.player,
    block: { dimension: f.dimension, location: { x: game.button[0], y: game.button[1], z: game.button[2] } },
    cancel: false,
  });
  f.flushWarpTimeouts();
  assert.equal(f.player.teleports.length, 1,
    "a failed command-block path must fall back to the exact interacting player");
  assert.equal(f.otherPlayer.teleports.length, 0);
}

{
  const catalog = cloneCatalog();
  catalog.WARP_CONTROLS[0].readiness = "venue-review";
  catalog.WARP_CONTROLS[0].enabled = false;
  const f = fixture({ catalog });
  const game = catalog.WARP_CONTROLS[0];
  f.events.block({
    player: f.player,
    block: { dimension: f.dimension, location: { x: game.button[0], y: game.button[1], z: game.button[2] } },
    cancel: false,
  });
  assert.equal(f.player.teleports.length, 0);
  assert.match(f.messages.at(-1), /準備中/);
}

{
  const cleanup = WARP_CONFIG.MISPLACED_ROW_CLEANUP;
  const overrides = new Map(cleanup.cells.map(cell => [
    keyOf(cell.location),
    cell.current_type,
  ]));
  const f = fixture({ overrides });
  const tagged = {
    typeId: "minecraft:armor_stand", location: { x: 10060, y: 66, z: 10077 }, removed: false,
    tags: new Set(["junket:warp_label"]), hasTag(tag) { return this.tags.has(tag); },
    remove() { this.removed = true; },
  };
  const broadLabel = {
    typeId: "minecraft:armor_stand", location: { x: 10090, y: 70, z: 10077 }, removed: false,
    tags: new Set(["junket:warp_label"]), hasTag(tag) { return this.tags.has(tag); },
    remove() { this.removed = true; },
  };
  const unrelated = {
    typeId: "minecraft:armor_stand", location: { x: 10120, y: 70, z: 10077 }, removed: false,
    tags: new Set(["junket:warp_label"]), hasTag(tag) { return this.tags.has(tag); },
    remove() { this.removed = true; },
  };
  const otherEntity = {
    typeId: "minecraft:armor_stand", location: { x: 10060, y: 70, z: 10077 }, removed: false,
    tags: new Set(["junket:unrelated"]), hasTag(tag) { return this.tags.has(tag); },
    remove() { this.removed = true; },
  };
  const player = {
    typeId: "minecraft:player", location: { x: 10060, y: 70, z: 10077 }, removed: false,
    tags: new Set(["junket:warp_label"]), hasTag(tag) { return this.tags.has(tag); },
    remove() { this.removed = true; },
  };
  f.entities.push(tagged, broadLabel, unrelated, otherEntity, player);
  assert.equal(await f.warp.installControls(f.player), true);
  assert.equal(f.commands.filter(command => command.startsWith("setblock ")).length, 53);
  for (const cell of cleanup.cells) {
    assert.equal(f.blocks.get(keyOf(cell.location))?.typeId, cell.restore_type);
  }
  assert.equal(tagged.removed, true);
  assert.equal(broadLabel.removed, true, "labels in the broad guarded entity area must be removed");
  assert.equal(unrelated.removed, false, "entities outside the guarded cleanup bounds must remain");
  assert.equal(otherEntity.removed, false, "only explicitly tagged entities may be removed");
  assert.equal(player.removed, false, "players must never be removed");
  assert.ok(f.entityQueries.some(query => query.tags.includes("junket:warp_label")));
  const anchor = WARP_CONFIG.PROTECTED_ANCHOR;
  assert.equal(f.commands.some(command => command.startsWith(`setblock ${anchor.command_block.join(" ")} `)), false);
  assert.equal(f.commands.some(command => command.startsWith(`setblock ${anchor.sign.join(" ")} `)), false);
  assert.equal(f.commands.some(command => command.startsWith(`setblock ${anchor.button.join(" ")} `)), false);
}

{
  const cleanup = WARP_CONFIG.MISPLACED_ROW_CLEANUP;
  const occupied = cleanup.cells[0];
  const f = fixture({ overrides: new Map([[keyOf(occupied.location), "minecraft:stone"]]) });
  assert.equal(await f.warp.installControls(f.player), false);
  assert.equal(f.commands.filter(command => command.startsWith("setblock ")).length, 0,
    "an unexpected block must abort before any cleanup write");
  assert.match(f.messages.at(-1), /別ブロック/);
}

{
  const f = fixture();
  assert.equal(await f.warp.installControls(f.player), true);
  assert.equal(f.commands.filter(command => command.startsWith("blockdata ")).length, 17);
  assert.ok(f.commands.every(command => !command.startsWith("blockdata ") ||
    (!command.includes('"Command"') && !command.includes("FrontText"))),
  "command blocks must use Bedrock SNBT and signs use the sign component");
  assert.equal(f.commands.filter(command => command.startsWith("setblock ")).length, 0);
  assert.ok(f.commands.every(command => !command.includes("minecraft:acacia_button") && !command.includes("minecraft:wooden_button")));
  for (const game of WARP_CONFIG.WARP_CONTROLS) {
    const data = f.commandData.get(keyOf(game.command_block));
    const [x, y, z] = game.button;
    assert.equal(data.Command,
      `execute as @a[tag=junket:warp_${game.id},x=${x},y=${y},z=${z},r=4] run scriptevent junket:warp ${game.id}`);
    assert.equal(data.CustomName, `JUNKET ${game.title}`);
    assert.equal(f.signTexts.get(keyOf(game.sign)), game.title);
  }
  const cleanup = WARP_CONFIG.MISPLACED_ROW_CLEANUP;
  assert.equal(cleanup.entity_bounds[3], 10092);
  assert.ok(f.commands.includes("tickingarea add 10046 62 10068 10092 76 10102 junket_install"));
  assert.ok(f.commands.includes("tickingarea remove junket_install"));
  assert.equal(f.ticking(), false);
  assert.match(f.messages.at(-1), /17ゲーム/);
}

{
  const game = WARP_CONFIG.WARP_CONTROLS[0];
  const f = fixture();
  f.signTexts.set(keyOf(game.sign), "既存イベント用");
  assert.equal(await f.warp.installControls(f.player), false);
  assert.equal(f.commands.filter(command => command.startsWith("blockdata ")).length, 0,
    "a sign used by another feature must abort before command or sign writes");
  assert.equal(f.blocks.get(keyOf(game.command_block))?.typeId, game.command_block_type);
  assert.equal(f.signTexts.get(keyOf(game.sign)), "既存イベント用");
  assert.match(f.messages.at(-1), /別用途/);
}

{
  const game = WARP_CONFIG.WARP_CONTROLS[0];
  const f = fixture();
  f.signTexts.set(keyOf(game.sign), `${game.title} / JUNKET`);
  assert.equal(await f.warp.installControls(f.player), true);
  assert.equal(f.signTexts.get(keyOf(game.sign)), game.title);
  assert.match(f.messages.at(-1), /設定済み看板1件/);
}

{
  const f = fixture({ unloadedControlReads: 2 });
  assert.equal(await f.warp.installControls(), true);
  assert.match(f.commands.at(-1), /^tickingarea remove /);
}

{
  const game = WARP_CONFIG.WARP_CONTROLS[0];
  const f = fixture({ unloadedSignReads: new Map([[keyOf(game.sign), 2]]) });
  assert.equal(await f.warp.installControls(f.player), true);
  assert.equal(f.signTexts.get(keyOf(game.sign)), game.title,
    "a temporarily unloaded sign component must be retried instead of aborting");
  assert.match(f.messages.at(-1), /17ゲーム/);
}

{
  const game = WARP_CONFIG.WARP_CONTROLS[0];
  const f = fixture({ unloadedSignReads: new Map([[keyOf(game.sign), 1000]]) });
  assert.equal(await f.warp.installControls(f.player), false);
  assert.match(f.messages.at(-1), /看板コンポーネントを読み込めません/);
  assert.doesNotMatch(f.messages.at(-1), /設定・検証しました/);
}

{
  const cleanup = WARP_CONFIG.MISPLACED_ROW_CLEANUP;
  const occupied = cleanup.cells[0];
  const overrides = new Map(cleanup.cells.map(cell => [keyOf(cell.location), cell.current_type]));
  const f = fixture({
    overrides,
    unloadedBlockReads: new Map([[keyOf(occupied.location), 2]]),
  });
  assert.equal(await f.warp.installControls(), true);
  assert.equal(f.commands.filter(command => command.startsWith("setblock ")).length, 53);
  assert.equal(f.blocks.get(keyOf(occupied.location))?.typeId, occupied.restore_type);
}

{
  const game = WARP_CONFIG.WARP_CONTROLS[6];
  const f = fixture({ overrides: new Map([[keyOf(game.sign), "minecraft:oak_sign"]]) });
  assert.equal(await f.warp.installControls(f.player), false);
  assert.equal(f.commands.filter(command => command.startsWith("blockdata ")).length, 0);
  assert.match(f.messages.at(-1), /既存設備の種類または状態が不一致/);
}

{
  const anchor = WARP_CONFIG.PROTECTED_ANCHOR;
  const f = fixture({ overrides: new Map([[keyOf(anchor.sign), "minecraft:oak_sign"]]) });
  assert.equal(await f.warp.installControls(f.player), false);
  assert.match(f.messages.at(-1), /拠点行き.*中止/);
}

{
  const anchor = WARP_CONFIG.PROTECTED_ANCHOR;
  const f = fixture();
  f.signTexts.set(keyOf(anchor.sign), "");
  assert.equal(await f.warp.installControls(f.player), false);
  assert.match(f.messages.at(-1), /拠点行き看板が空/);
}

{
  const f = fixture({ ignoreSignText: true });
  assert.equal(await f.warp.installControls(f.player), false);
  assert.match(f.messages.at(-1), /看板の表示を確認できません/);
  assert.equal(f.commands.filter(command => /^setblock .* minecraft:air$/.test(command)).length, 0);
}

{
  const f = fixture();
  assert.equal(f.warp.returnToEntry(f.player), true);
  assert.deepEqual(f.player.teleports, [{
    location: { x: 10052, y: 65, z: 10078 },
    options: { dimension: f.dimension, rotation: { x: 0, y: 180 } },
  }]);
}

{
  const f = fixture();
  const target = { dimension: f.dimension, location: { x: 1, y: 2, z: 3 }, hasTag: tag => tag === "junket:return" };
  f.events.entity({ player: f.player, target, cancel: false });
  assert.equal(f.player.teleports.length, 1);
  assert.equal(f.otherPlayer.teleports.length, 0);
}

{
  const catalog = cloneCatalog();
  catalog.WARP_OFFLINE_INSTALLED = true;
  for (const game of catalog.WARP_CONTROLS) game.command_mode = "native_tp";
  const obsolete = catalog.MISPLACED_ROW_CLEANUP.cells[0];
  const f = fixture({ catalog, overrides: new Map([[keyOf(obsolete.location), "minecraft:diamond_block"]]) });
  for (const game of catalog.WARP_CONTROLS) f.signTexts.set(keyOf(game.sign), game.title);
  assert.equal(await f.warp.installControls(f.player), true);
  assert.ok(f.commands.every(command => command.startsWith("tickingarea ")),
    "offline installation inspection must never write command NBT, signs or unrelated terrain");
  assert.equal(f.entityQueries.length, 0, "native mode must never remove historical entities");
  const game = catalog.WARP_CONTROLS[0];
  const event = { player: f.player, block: {
    dimension: f.dimension, location: { x: game.button[0], y: game.button[1], z: game.button[2] },
  }, cancel: false };
  f.events.block(event);
  f.flushWarpTimeouts();
  assert.equal(event.cancel, false, "the real top-mounted button must power its native command");
  assert.equal(f.player.tags.size, 0);
  assert.equal(f.player.teleports.length, 0, "native tp must not be followed by a scripted duplicate");
}

{
  const catalog = cloneCatalog();
  catalog.WARP_OFFLINE_INSTALLED = true;
  catalog.WARP_PRESERVE_OBSERVED_ANCHOR = true;
  const anchor = catalog.PROTECTED_ANCHOR;
  const positions = [anchor.command_block, anchor.sign, anchor.button];
  const f = fixture({ catalog, overrides: new Map(positions.map(at => [keyOf(at), "minecraft:air"])) });
  for (const game of catalog.WARP_CONTROLS) f.signTexts.set(keyOf(game.sign), game.title);
  assert.equal(await f.warp.installControls(f.player), true,
    "native inspection preserves the present world even when historical equipment is already absent");
  assert.ok(f.commands.every(command => command.startsWith("tickingarea ")));
  for (const at of positions) assert.equal(f.blocks.get(keyOf(at)).typeId, "minecraft:air");
  assert.equal(f.entityQueries.length, 0);
}

console.log("PASS exact 17-slot corridor preflight, protected anchor, per-player teleport, chunk retry, block-entity-only persistence, native offline inspection and no structure placement");

for (const pressed of [0, 1, false, true, undefined, 2]) {
  const catalog = cloneCatalog(); catalog.WARP_OFFLINE_INSTALLED = true;
  const game = catalog.WARP_CONTROLS[0], at = keyOf(game.button);
  const f = fixture({ catalog, overrides: new Map([[at, {
    typeId: game.button_type, states: { ...game.button_states, button_pressed_bit: pressed },
  }]]) });
  for (const item of catalog.WARP_CONTROLS) f.signTexts.set(keyOf(item.sign), item.title);
  assert.equal(await f.warp.installControls(f.player), [0, 1, false, true].includes(pressed));
  assert.equal(f.blocks.get(at).states.button_pressed_bit, pressed, "inspection preserves pressed state");
  assert.ok(f.commands.every(command => command.startsWith("tickingarea ")), "buttons remain unmodified");
}
{
  const catalog = cloneCatalog(); catalog.WARP_OFFLINE_INSTALLED = true;
  const game = catalog.WARP_CONTROLS[0];
  const f = fixture({ catalog, overrides: new Map([[keyOf(game.button), {
    typeId: game.button_type, states: { ...game.button_states, button_pressed_bit: 1, facing_direction: 0 },
  }]]) });
  for (const item of catalog.WARP_CONTROLS) f.signTexts.set(keyOf(item.sign), item.title);
  assert.equal(await f.warp.installControls(f.player), false, "pressed state does not excuse wrong orientation");
}
console.log("PASS pressed button 0/1/false/true preserved; missing/invalid press state and wrong orientation rejected");
