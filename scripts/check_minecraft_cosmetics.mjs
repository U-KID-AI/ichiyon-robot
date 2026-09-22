import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const source = await readFile(new URL("../minecraft/behavior_packs/import_structures/scripts/cosmetics_core.js", import.meta.url), "utf8");
const { createCosmetics } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

function setup() {
  let tick = 0;
  const tasks = [];
  const system = { run: fn => tasks.push({ at: tick + 1, fn }), runTimeout: (fn, delay) => tasks.push({ at: tick + delay, fn }) };
  const advance = async (count = 5) => { for (let i = 0; i < count; i++) { tick++; const due = tasks.filter(t => t.at <= tick); for (const task of due) { tasks.splice(tasks.indexOf(task), 1); task.fn(); } for (let j = 0; j < 15; j++) await Promise.resolve(); } };
  const spawned = [];
  const dimension = { id: "overworld", getBlock: loc => ({ isAir: loc.y >= 64, isLiquid: false }), spawnEntity: () => { const entity = makeEntity("ichiyon:avatar"); spawned.push(entity); return entity; } };
  let entityIndex = 0;
  function makeEntity(typeId) {
    const properties = { "ichiyon:skin_id": 0, "ichiyon:hat": 0, "ichiyon:face": 0, "ichiyon:neck": 0, "ichiyon:back": 0 };
    const dynamic = {};
    const entity = {
      id: `entity${entityIndex++}`, typeId, isValid: true, dimension, location: { x: 0, y: 64, z: 0 }, variant: 0,
      getProperty: key => properties[key], setProperty: (key, value) => { if (entity.failSet) throw new Error("set failed"); system.run(() => properties[key] = value); },
      getDynamicProperty: key => dynamic[key], setDynamicProperty: (key, value) => dynamic[key] = value,
      triggerEvent: event => { if (entity.failTrigger) throw new Error("trigger failed"); entity.variant = Number(event.split("_").at(-1)) - 1; },
      setRotation: () => {}, getComponent: () => ({ value: entity.variant }), remove: () => entity.isValid = false,
      properties, dynamic,
    };
    return entity;
  }
  class ItemStack { constructor(typeId, amount) { Object.assign(this, { typeId, amount }); } }
  function makePlayer() {
    const player = makeEntity("minecraft:player");
    const items = Array(3).fill(undefined);
    const bag = { size: 3, getItem: i => items[i], setItem: (i, item) => { items[i] = item; } };
    player.items = items; player.messages = []; player.mode = "survival"; player.selectedSlotIndex = 0;
    player.getComponent = () => ({ container: bag });
    player.getGameMode = () => player.mode;
    player.getRotation = () => ({ y: 0 });
    player.sendMessage = message => player.messages.push(message);
    player.getBlockFromViewDirection = () => ({ block: { location: { x: 0, y: 63, z: 2 } } });
    return player;
  }
  const answers = [], forms = [];
  class ActionFormData {
    constructor() { this.buttons = []; }
    title(value) { this.titleText = value; return this; }
    body() { return this; }
    button(value) { this.buttons.push(value); return this; }
    async show() { forms.push(this); const answer = answers.shift(); return typeof answer === "function" ? answer() : answer || { canceled: true }; }
  }
  const catalog = {
    digest: "a".repeat(64), skins: Array.from({ length: 20 }, (_, i) => ({ id: i + 1, name: `Skin ${i + 1}` })),
    accessories: [{ id: 1, item: "ichiyon:accessory_1", slot: "hat", name: "帽子" }, { id: 2, item: "ichiyon:accessory_2", slot: "face", name: "眼鏡" }, { id: 3, item: "ichiyon:accessory_3", slot: "hat", name: "王冠" }],
  };
  const subscriptions = {};
  const signal = name => ({ subscribe: callback => subscriptions[name] = callback });
  const world = { beforeEvents: { playerInteractWithEntity: signal("interact"), playerInteractWithBlock: signal("block"), itemUse: signal("use") }, afterEvents: { playerSpawn: signal("spawn"), playerLeave: signal("leave") }, getAllPlayers: () => [] };
  const core = createCosmetics({ catalog, world, system, ItemStack, ActionFormData, GameMode: { Creative: "creative" } });
  return { core, makePlayer, makeEntity, advance, catalog, answers, forms, spawned, dimension, subscriptions };
}

test("wear, restart/rejoin, respawn and restore preserve per-player selection", async () => {
  const { core, makePlayer, advance } = setup(); const player = makePlayer(); const other = makePlayer();
  core.changeSkin(player, 2); await advance(); assert.equal(player.getProperty("ichiyon:skin_id"), 2);
  player.properties["ichiyon:skin_id"] = 0; core.restoreSkin(player); await advance();
  assert.equal(player.getProperty("ichiyon:skin_id"), 2); assert.equal(other.getProperty("ichiyon:skin_id"), 0);
  core.changeSkin(player, 0); core.restoreSkin(player); await advance(); assert.equal(player.getProperty("ichiyon:skin_id"), 0);
});
test("missing catalog skin displays default while retaining saved choice", async () => {
  const { core, makePlayer, advance } = setup(); const player = makePlayer(); player.dynamic["ichiyon:selected_skin"] = 126;
  core.restoreSkin(player); await advance(); assert.equal(player.getProperty("ichiyon:skin_id"), 0); assert.equal(player.dynamic["ichiyon:selected_skin"], 126);
  assert.throws(() => core.changeSkin(player, 126));
});
test("property failure rolls back saved clothing selection", () => {
  const { core, makePlayer } = setup(); const player = makePlayer(); player.failSet = true;
  assert.throws(() => core.changeSkin(player, 2)); assert.equal(player.dynamic["ichiyon:selected_skin"], undefined);
});
test("cancelling mannequin form changes nothing", async () => {
  const { core, makePlayer, makeEntity, advance } = setup(); const player = makePlayer();
  await core.mannequinMenu(player, makeEntity("ichiyon:avatar")); await advance(); assert.equal(player.getProperty("ichiyon:skin_id"), 0);
});
test("moving away, changing dimension or changing mannequin aborts stale form", async () => {
  for (const mutate of [(p, t) => p.location.x = 100, (p, t) => p.dimension = { id: "nether" }, (p, t) => t.variant = 1, (p, t) => t.isValid = false]) {
    const { core, makePlayer, makeEntity, answers, advance } = setup(); const player = makePlayer(), target = makeEntity("ichiyon:avatar");
    answers.push(() => { mutate(player, target); return { selection: 0 }; });
    await core.mannequinMenu(player, target); await advance(); assert.equal(player.getProperty("ichiyon:skin_id"), 0);
  }
});
test("picker pages reach skins beyond first page", async () => {
  const { core, makePlayer, answers } = setup(); answers.push({ selection: 12 }, { selection: 2 });
  const chosen = await core.choose(makePlayer(), "skins", Array.from({ length: 20 }, (_, i) => ({ value: i, text: String(i) })));
  assert.equal(chosen, 14);
});
test("common egg spawns selected variant only once under repeated events", async () => {
  const { core, makePlayer, answers, advance, spawned } = setup(); const player = makePlayer(); player.mode = "creative";
  player.items[0] = { typeId: "ichiyon:avatar_selector", amount: 1 }; answers.push({ selection: 2 });
  const event = { player, itemStack: player.items[0], block: { location: { x: 0, y: 63, z: 2 } }, isFirstEvent: true };
  core.useEgg(event); core.useEgg(event); assert.equal(event.cancel, true); assert.equal(spawned.length, 0);
  await advance(); assert.equal(spawned.length, 1); assert.equal(spawned[0].variant, 2);
});
test("common egg denies survival, cancellation, changed held item, and blocked placement", async () => {
  for (const mode of ["survival", "cancel", "switch", "blocked"]) {
    const { core, makePlayer, answers, spawned, dimension } = setup(); const player = makePlayer(); player.mode = mode === "survival" ? "survival" : "creative";
    player.items[0] = { typeId: "ichiyon:avatar_selector", amount: 1 };
    answers.push(() => { if (mode === "switch") player.items[0] = undefined; return mode === "cancel" ? { canceled: true } : { selection: 0 }; });
    if (mode === "blocked") dimension.getBlock = () => ({ isAir: false });
    await core.selectSkin(player, undefined, { x: 0, y: 64, z: 2 }).catch(() => {}); assert.equal(spawned.length, 0);
  }
});
test("equip combines slots and returns replaced item in survival", async () => {
  const { core, makePlayer, makeEntity, advance } = setup(); const player = makePlayer(), target = makeEntity("ichiyon:molcar");
  player.items[0] = { typeId: "ichiyon:accessory_1", amount: 1 }; core.mutateAccessory(player, target, "hat", 1); await advance();
  assert.equal(player.items[0], undefined); assert.equal(target.getProperty("ichiyon:hat"), 1);
  player.items[0] = { typeId: "ichiyon:accessory_2", amount: 1 }; core.mutateAccessory(player, target, "face", 2); await advance();
  assert.equal(target.getProperty("ichiyon:hat"), 1); assert.equal(target.getProperty("ichiyon:face"), 2);
  player.items[0] = { typeId: "ichiyon:accessory_3", amount: 1 }; core.mutateAccessory(player, target, "hat", 3); await advance();
  assert.equal(target.getProperty("ichiyon:hat"), 3); assert.equal(player.items[0].typeId, "ichiyon:accessory_1");
});
test("same-tick multiplayer equip cannot duplicate replaced accessory", async () => {
  const { core, makePlayer, makeEntity, advance } = setup(); const first = makePlayer(), second = makePlayer(), target = makeEntity("ichiyon:molcar");
  first.items[0] = second.items[0] = { typeId: "ichiyon:accessory_1", amount: 1 };
  core.mutateAccessory(first, target, "hat", 1); assert.throws(() => core.mutateAccessory(second, target, "hat", 1)); await advance();
  assert.equal(first.items[0], undefined); assert.equal(second.items[0].amount, 1); assert.equal(target.getProperty("ichiyon:hat"), 1);
});
test("failed property write restores consumed item", () => {
  const { core, makePlayer, makeEntity } = setup(); const player = makePlayer(), target = makeEntity("ichiyon:molcar"); target.failSet = true;
  player.items[0] = { typeId: "ichiyon:accessory_1", amount: 1 }; assert.throws(() => core.mutateAccessory(player, target, "hat", 1));
  assert.equal(player.items[0].typeId, "ichiyon:accessory_1"); assert.equal(target.getProperty("ichiyon:hat"), 0);
});
test("full inventory blocks removal without losing accessory", async () => {
  const { core, makePlayer, makeEntity, advance } = setup(); const player = makePlayer(), target = makeEntity("ichiyon:molcar");
  target.properties["ichiyon:hat"] = 1; player.items.fill({ typeId: "minecraft:stone", amount: 64 });
  core.mutateAccessory(player, target, "hat", 0); await advance(); assert.equal(target.getProperty("ichiyon:hat"), 1);
  player.items[1] = undefined; core.mutateAccessory(player, target, "hat", 0); await advance();
  assert.equal(target.getProperty("ichiyon:hat"), 0); assert.equal(player.items[1].typeId, "ichiyon:accessory_1");
});
test("creative does not consume or generate returned items", async () => {
  const { core, makePlayer, makeEntity, advance } = setup(); const player = makePlayer(), target = makeEntity("ichiyon:molcar"); player.mode = "creative";
  player.items[0] = { typeId: "ichiyon:accessory_1", amount: 1 }; core.mutateAccessory(player, target, "hat", 1); await advance();
  core.mutateAccessory(player, target, "hat", 0); await advance(); assert.equal(player.items[0].amount, 1); assert.equal(player.items[1], undefined);
});
test("stale held items, wrong slot and out-of-range target fail without mutation", () => {
  const { core, makePlayer, makeEntity } = setup(); const player = makePlayer(), target = makeEntity("ichiyon:molcar");
  assert.throws(() => core.mutateAccessory(player, target, "hat", 1)); player.items[0] = { typeId: "ichiyon:accessory_2", amount: 1 };
  assert.throws(() => core.mutateAccessory(player, target, "hat", 2)); target.location.x = 100;
  assert.throws(() => core.mutateAccessory(player, target, "face", 2)); assert.equal(player.items[0].amount, 1);
});
test("empty-hand riding and lead interactions are not cancelled", () => {
  const { core, makePlayer, makeEntity } = setup(); const player = makePlayer(), target = makeEntity("ichiyon:molcar");
  for (const itemStack of [undefined, { typeId: "minecraft:lead" }]) { const event = { player, target, itemStack }; core.interact(event); assert.equal(event.cancel, undefined); }
});
test("stale accessory removal form cannot remove a replacement", async () => {
  const { core, makePlayer, makeEntity, answers, advance } = setup(); const player = makePlayer(), target = makeEntity("ichiyon:molcar"); target.properties["ichiyon:hat"] = 1;
  answers.push(() => { target.properties["ichiyon:hat"] = 3; return { selection: 0 }; }); await core.accessoryMenu(player, target); await advance();
  assert.equal(target.getProperty("ichiyon:hat"), 3); assert.equal(player.items[0], undefined);
});
test("web placement rejects unknown catalog and offline player; known catalog spawns", async () => {
  const { core, makePlayer, catalog, spawned } = setup(); const player = makePlayer(); const results = [];
  const helpers = { isValidPlayerName: name => name === "Steve", findOnlinePlayer: () => player, playerForwardSpawnLocation: () => ({ x: 0, y: 64, z: 2 }), postResult: async (...args) => results.push(args) };
  const command = { type: "cosmetic_avatar_spawn", request_id: "request", minecraft_player: "Steve", skin_id: 2, catalog_digest: "wrong" };
  await core.handleCommand(command, helpers); assert.equal(spawned.length, 0); assert.equal(results.at(-1)[2], "catalog_not_loaded");
  command.catalog_digest = catalog.digest; helpers.findOnlinePlayer = () => undefined;
  await core.handleCommand(command, helpers); assert.equal(results.at(-1)[2], "player_offline");
  helpers.findOnlinePlayer = () => player; await core.handleCommand(command, helpers); assert.equal(spawned.length, 1); assert.equal(results.at(-1)[1], "succeeded");
  assert.equal(await core.handleCommand({ type: "legacy" }, helpers), false);
});
test("startup registers reconnect and death-respawn restoration", async () => {
  const { core, subscriptions, makePlayer, advance } = setup(); core.start(); const player = makePlayer(); player.dynamic["ichiyon:selected_skin"] = 3;
  subscriptions.spawn({ player, initialSpawn: false }); await advance(); assert.equal(player.getProperty("ichiyon:skin_id"), 3);
  assert.equal(typeof subscriptions.leave, "function");
});
