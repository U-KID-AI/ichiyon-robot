import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const source = await readFile(new URL("../minecraft/behavior_packs/import_structures/scripts/cosmetics_core.js", import.meta.url), "utf8");
const { createCosmetics } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

test("swim ring coexists with every DB slot and returns via the existing removal menu", async () => {
  for (const type of ["molcar", "molcar2", "molcar3"]) {
    const f = setup(), p = f.makePlayer(), mob = f.makeEntity(`ichiyon:${type}`);
    for (const slot of ["hat", "face", "neck", "back"]) mob.properties[`ichiyon:${slot}`] = 1;
    p.items[0] = { typeId: "ichiyon:molcar_float_ring", amount: 1 };
    const event = { player: p, target: mob, itemStack: p.items[0] };
    f.core.interact(event); assert.equal(event.cancel, true); await f.advance();
    assert.equal(mob.properties["ichiyon:float_ring"], 1); assert.equal(p.items[0], undefined);
    for (const slot of ["hat", "face", "neck", "back"]) assert.equal(mob.properties[`ichiyon:${slot}`], 1);
    f.answers.push({ selection: 4 }); await f.core.accessoryMenu(p, mob); await f.advance();
    assert.equal(mob.properties["ichiyon:float_ring"], 0);
    assert.equal(p.items[0].typeId, "ichiyon:molcar_float_ring");
  }
});

test("swim ring rejects garbage and unrelated mobs; riding/lead/carrot still pass through", async () => {
  const f = setup(), p = f.makePlayer();
  for (const type of ["ichiyon:garbage_molcar", "ichiyon:mokuro", "minecraft:pig"]) {
    const event = { player: p, target: f.makeEntity(type), itemStack: { typeId: "ichiyon:molcar_float_ring", amount: 1 } };
    f.core.interact(event); assert.notEqual(event.cancel, true);
  }
  for (const itemStack of [undefined, { typeId: "minecraft:lead" }, { typeId: "minecraft:carrot" }]) {
    const event = { player: p, target: f.makeEntity("ichiyon:molcar"), itemStack };
    f.core.interact(event); assert.notEqual(event.cancel, true);
  }
});

test("ring contention, failed property write, full inventory and creative use preserve items", async () => {
  const f = setup(), a = f.makePlayer(), b = f.makePlayer(), mob = f.makeEntity("ichiyon:molcar");
  a.items[0] = b.items[0] = { typeId: "ichiyon:molcar_float_ring", amount: 1 };
  mob.failSet = true; assert.throws(() => f.core.mutateAccessory(a, mob, "float_ring", 1));
  assert.equal(a.items[0].amount, 1); await f.advance(); mob.failSet = false;
  f.core.mutateAccessory(a, mob, "float_ring", 1);
  assert.throws(() => f.core.mutateAccessory(b, mob, "float_ring", 1)); await f.advance();
  assert.equal(b.items[0].amount, 1); assert.equal(a.items[0], undefined);
  a.items.fill({ typeId: "minecraft:stone", amount: 64 });
  f.core.mutateAccessory(a, mob, "float_ring", 0); await f.advance();
  assert.equal(mob.properties["ichiyon:float_ring"], 1);
  a.mode = "creative"; f.core.mutateAccessory(a, mob, "float_ring", 0); await f.advance();
  assert.equal(mob.properties["ichiyon:float_ring"], 0); assert.equal(a.items[0].typeId, "minecraft:stone");
});

function setup(skinEntries) {
  let tick = 0;
  const tasks = [];
  const system = { run: fn => tasks.push({ at: tick + 1, fn }), runTimeout: (fn, delay) => tasks.push({ at: tick + delay, fn }) };
  const advance = async (count = 5) => { for (let i = 0; i < count; i++) { tick++; const due = tasks.filter(t => t.at <= tick); for (const task of due) { tasks.splice(tasks.indexOf(task), 1); task.fn(); } for (let j = 0; j < 15; j++) await Promise.resolve(); } };
  const spawned = [];
  const dimension = { id: "overworld", getBlock: loc => ({ isAir: loc.y >= 64, isLiquid: false }), spawnEntity: () => { const entity = makeEntity("ichiyon:avatar"); spawned.push(entity); return entity; } };
  let entityIndex = 0;
  function makeEntity(typeId) {
    const properties = { "ichiyon:skin_id": 0, "ichiyon:hat": 0, "ichiyon:face": 0, "ichiyon:neck": 0, "ichiyon:back": 0, "ichiyon:float_ring": 0 };
    const dynamic = {};
    const entity = {
      id: `entity${entityIndex++}`, typeId, isValid: true, dimension, location: { x: 0, y: 64, z: 0 }, variant: 0, events: [],
      getProperty: key => properties[key], setProperty: (key, value) => { if (entity.failSet) throw new Error("set failed"); system.run(() => properties[key] = value); },
      getDynamicProperty: key => dynamic[key], setDynamicProperty: (key, value) => dynamic[key] = value,
      triggerEvent: event => {
        entity.events.push(event);
        if (entity.failTrigger) throw new Error("trigger failed");
        const cosmetic = /^ichiyon:cosmetic_(\d+)$/.exec(event);
        if (cosmetic) entity.variant = Number(cosmetic[1]) - 1;
      },
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
    body(value) { this.bodyText = value; return this; }
    button(value) { this.buttons.push(value); return this; }
    async show() { forms.push(this); const answer = answers.shift(); return typeof answer === "function" ? answer() : answer || { canceled: true }; }
  }
  const catalog = {
    digest: "a".repeat(64), skins: skinEntries ?? Array.from({ length: 20 }, (_, i) => ({ id: i + 1, name: `Skin ${i + 1}`, category_id: i < 4 ? "honkai3rd" : "uncategorized", category: i < 4 ? "崩壊3rd" : "その他・未分類" })),
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
test("deleted catalog skin resets saved choice to vanilla", async () => {
  const { core, makePlayer, advance } = setup(); const player = makePlayer(); player.dynamic["ichiyon:selected_skin"] = 126;
  core.restoreSkin(player); await advance(); assert.equal(player.getProperty("ichiyon:skin_id"), 0); assert.equal(player.dynamic["ichiyon:selected_skin"], 0);
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
  player.items[0] = { typeId: "ichiyon:avatar_selector", amount: 1 }; answers.push({ selection: 0 }, { selection: 2 });
  const event = { player, itemStack: player.items[0], block: { location: { x: 0, y: 63, z: 2 } }, isFirstEvent: true };
  core.useEgg(event); core.useEgg(event); assert.equal(event.cancel, true); assert.equal(spawned.length, 0);
  await advance(); assert.equal(spawned.length, 1); assert.equal(spawned[0].variant, 2);
});
test("venue light blocks are standing room and a light floor is not", async () => {
  const { core, makePlayer, answers, spawned, dimension } = setup();
  const player = makePlayer();
  player.mode = "creative";
  player.location = { x: 20205, y: 67, z: 20012 };
  player.items[0] = { typeId: "ichiyon:avatar_selector", amount: 1 };
  const place = { x: 20205.5, y: 67, z: 20012.5 };
  answers.push({ selection: 0 }, { selection: 2 });
  dimension.getBlock = (loc) => loc.y < place.y
    ? { isAir: false, isLiquid: false, typeId: "minecraft:smooth_quartz" }
    : { isAir: false, isLiquid: false, typeId: "minecraft:light_block_12" };
  await core.selectSkin(player, undefined, place);
  assert.equal(spawned.length, 1);
  assert.equal(spawned[0].variant, 2);
  dimension.getBlock = () => ({ isAir: false, isLiquid: false, typeId: "minecraft:light_block_12" });
  answers.push({ selection: 0 }, { selection: 0 });
  await core.selectSkin(player, undefined, place).catch(() => {});
  assert.equal(spawned.length, 1);
});
test("UserBusy retries without a second spawn and a closed form does not retry", async () => {
  const busy = setup();
  const player = busy.makePlayer();
  player.mode = "creative";
  player.items[0] = { typeId: "ichiyon:avatar_selector", amount: 1 };
  busy.answers.push({ canceled: true, cancelationReason: "UserBusy" }, { selection: 0 }, { selection: 2 });
  const pending = busy.core.selectSkin(player, undefined, { x: 0, y: 64, z: 2 });
  await busy.advance(40);
  await pending;
  assert.equal(busy.spawned.length, 1);
  assert.equal(busy.spawned[0].variant, 2);
  assert.equal(busy.forms.length, 3);
  const closed = setup();
  const other = closed.makePlayer();
  other.mode = "creative";
  other.items[0] = { typeId: "ichiyon:avatar_selector", amount: 1 };
  closed.answers.push({ canceled: true, cancelationReason: "UserClosed" }, { selection: 0 });
  await closed.core.selectSkin(other, undefined, { x: 0, y: 64, z: 2 });
  assert.equal(closed.forms.length, 1);
  assert.equal(closed.spawned.length, 0);
});
test("common egg denies survival, cancellation, changed held item, and blocked placement", async () => {
  for (const mode of ["survival", "cancel", "switch", "blocked"]) {
    const { core, makePlayer, answers, spawned, dimension } = setup(); const player = makePlayer(); player.mode = mode === "survival" ? "survival" : "creative";
    player.items[0] = { typeId: "ichiyon:avatar_selector", amount: 1 };
    answers.push({ selection: 0 }, () => { if (mode === "switch") player.items[0] = undefined; return mode === "cancel" ? { canceled: true } : { selection: 0 }; });
    if (mode === "blocked") dimension.getBlock = () => ({ isAir: false });
    await core.selectSkin(player, undefined, { x: 0, y: 64, z: 2 }).catch(() => {}); assert.equal(spawned.length, 0);
  }
});

test("category picker shows counts and only the selected category's skins", async () => {
  const f = setup(), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
  f.answers.push({ selection: 1 }, { selection: 0 });
  await f.core.selectSkin(player, target);
  assert.deepEqual(f.forms[0].buttons, ["崩壊3rd（4件）", "その他・未分類（16件）", "すべてのキャラクター（20件）"]);
  assert.deepEqual(f.forms[1].buttons, [...Array.from({ length: 12 }, (_, i) => `Skin ${i + 5}`), "次のページ", "カテゴリに戻る"]);
  assert.match(f.forms[1].titleText, /その他・未分類.*1\/2/);
  assert.equal(target.variant, 4); assert.equal(target.nameTag, "Skin 5");
});

test("category pagination reaches the last skin and can return to the previous page", async () => {
  for (const previous of [false, true]) {
    const f = setup(), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
    f.answers.push({ selection: 1 }, { selection: 12 });
    if (previous) f.answers.push({ selection: 4 }, { selection: 0 });
    else f.answers.push({ selection: 3 });
    await f.core.selectSkin(player, target);
    assert.deepEqual(f.forms[2].buttons, ["Skin 17", "Skin 18", "Skin 19", "Skin 20", "前のページ", "カテゴリに戻る"]);
    assert.equal(target.variant, previous ? 4 : 19);
  }
});

test("back is available on every skin page and permits choosing another category", async () => {
  for (const secondPage of [false, true]) {
    const f = setup(), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
    f.answers.push({ selection: 1 });
    if (secondPage) f.answers.push({ selection: 12 });
    f.answers.push({ selection: secondPage ? 5 : 13 }, { selection: 0 }, { selection: 3 });
    await f.core.selectSkin(player, target);
    assert.equal(target.variant, 3); assert.equal(target.nameTag, "Skin 4");
    assert.deepEqual(f.forms.at(-1).buttons, ["Skin 1", "Skin 2", "Skin 3", "Skin 4", "カテゴリに戻る"]);
  }
});

test("sparse permanent IDs, duplicate labels and empty categories remain selectable", async () => {
  for (const skinEntries of [
    [{ id: 3, name: "same" }, { id: 4, name: "same" }],
    [{ id: 8, name: "same" }, { id: 127, name: "same" }],
  ]) {
    const f = setup(skinEntries), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
    f.answers.push({ selection: 0 }, { selection: 1 });
    await f.core.selectSkin(player, target);
    assert.deepEqual(f.forms[0].buttons, ["その他・未分類（2件）", "すべてのキャラクター（2件）"]);
    assert.deepEqual(f.forms[1].buttons, ["same", "same", "カテゴリに戻る"]);
    assert.equal(target.variant, skinEntries[1].id - 1);
  }
  const f = setup([]), player = f.makePlayer(); player.mode = "creative";
  await f.core.selectSkin(player, undefined, { x: 0, y: 64, z: 2 });
  assert.equal(f.forms.length, 0); assert.equal(f.spawned.length, 0);
  assert.match(player.messages[0], /スキンが登録されていません/);
});

test("cancel or invalid selection at either level or after back never mutates a mannequin", async () => {
  for (const response of [{ canceled: true }, { selection: 99 }, { selection: -1 }, { selection: 0.5 }, {}]) {
    for (const prefix of [[], [{ selection: 0 }], [{ selection: 1 }, { selection: 13 }]]) {
      const f = setup(), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
      target.variant = 7; target.nameTag = "unchanged";
      f.answers.push(...prefix, response);
      await f.core.selectSkin(player, target);
      assert.equal(target.variant, 7); assert.equal(target.nameTag, "unchanged"); assert.equal(f.spawned.length, 0);
    }
  }
});

test("creative sneaking edit uses categories and preserves the player's worn skin", async () => {
  const f = setup(), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar");
  player.mode = "creative"; player.isSneaking = true; f.core.changeSkin(player, 2); await f.advance();
  f.answers.push({ selection: 2 }, { selection: 1 }, { selection: 1 });
  await f.core.mannequinMenu(player, target);
  assert.equal(target.variant, 5); assert.equal(target.nameTag, "Skin 6");
  assert.equal(player.getProperty("ichiyon:skin_id"), 2); assert.equal(f.spawned.length, 0);
});

test("stale players and targets abort category, skin, next-page and back navigation", async () => {
  const mutations = [
    (p, t) => p.mode = "survival", (p, t) => p.dimension = { id: "nether" },
    (p, t) => p.isValid = false, (p, t) => t.isValid = false, (p, t) => p.location.x = 100,
  ];
  for (const mutate of mutations) {
    for (const [prefix, selection] of [[[], 1], [[{ selection: 1 }], 0], [[{ selection: 1 }], 12], [[{ selection: 1 }], 13]]) {
      const f = setup(), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
      f.answers.push(...prefix, () => { mutate(player, target); return { selection }; });
      await f.core.selectSkin(player, target);
      assert.equal(target.variant, 0); assert.equal(f.forms.length, prefix.length + 1);
    }
  }
});

test("egg placement rechecks state after both levels", async () => {
  for (const mutate of [p => p.items[0] = undefined, p => p.mode = "survival", p => p.isValid = false, p => p.dimension = { id: "nether" }]) {
    for (const categoryStage of [true, false]) {
      const f = setup(), player = f.makePlayer(); player.mode = "creative";
      player.items[0] = { typeId: "ichiyon:avatar_selector", amount: 1 };
      const changed = () => { mutate(player); return { selection: 0 }; };
      f.answers.push(...(categoryStage ? [changed, { selection: 0 }] : [{ selection: 0 }, changed]));
      await f.core.selectSkin(player, undefined, { x: 0, y: 64, z: 2 });
      assert.equal(f.spawned.length, 0);
    }
  }
});

test("category page boundaries preserve all IDs through the maximum catalog size", async () => {
  for (const count of [1, 12, 13, 24, 123]) {
    const entries = Array.from({ length: count }, (_, i) => ({ id: i + 5, name: `Uploaded ${i + 5}` }));
    const f = setup(entries), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
    const pages = Math.ceil(count / 12);
    f.answers.push({ selection: 0 });
    for (let page = 0; page < pages - 1; page++) f.answers.push({ selection: page === 0 ? 12 : 13 });
    f.answers.push({ selection: (count - 1) % 12 });
    await f.core.selectSkin(player, target);
    assert.equal(target.variant, count + 3);
    assert.equal(f.forms.length, pages + 1);
    assert.deepEqual(f.forms.slice(1).flatMap(form => form.buttons.filter(text => text.startsWith("Uploaded "))), entries.map(skin => skin.name));
    assert.ok(f.forms.slice(1).every(form => form.buttons.at(-1) === "カテゴリに戻る"));
    assert.ok(!f.forms.at(-1).buttons.includes("次のページ"));
  }
});

test("generated built-ins use work categories and all characters without ID inference", async () => {
  const generated = await readFile(new URL("../minecraft/cosmetics/catalog.lock.json", import.meta.url), "utf8");
  const entries = JSON.parse(generated).skins;
  assert.deepEqual(entries.map(skin => skin.category_id), ["honkai3rd", "honkai3rd", "honkai3rd", "uncategorized"]);
  for (const [category, skinIndex, expectedId] of [[0, 2, 3], [1, 0, 4], [2, 3, 4]]) {
    const f = setup(entries), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
    f.answers.push({ selection: category }, { selection: skinIndex });
    await f.core.selectSkin(player, target);
    assert.deepEqual(f.forms[0].buttons, ["崩壊3rd（3件）", "その他・未分類（1件）", "すべてのキャラクター（4件）"]);
    assert.equal(target.variant, expectedId - 1);
  }
});

test("new and renamed works, reassigned built-ins, and all characters follow catalog metadata", async () => {
  const entries = [
    { id: 1, name: "キアナ", category_id: "custom", category: "管理画面で変更した作品名" },
    { id: 5, name: "追加キャラ", category_id: "arknights", category: "アークナイツ" },
    { id: 9, name: "不明なキャラ", category_id: "uncategorized", category: "その他・未分類" },
  ];
  for (const [category, expectedId] of [[0, 1], [1, 5], [2, 9], [3, 1]]) {
    const f = setup(entries), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
    f.answers.push({ selection: category }, { selection: 0 });
    await f.core.selectSkin(player, target);
    assert.deepEqual(f.forms[0].buttons, ["管理画面で変更した作品名（1件）", "アークナイツ（1件）", "その他・未分類（1件）", "すべてのキャラクター（3件）"]);
    assert.equal(target.variant, expectedId - 1);
    if (category === 3) assert.deepEqual(f.forms[1].buttons, [...entries.map(skin => skin.name), "カテゴリに戻る"]);
  }
});

test("categories themselves paginate as works are added without code changes", async () => {
  const entries = Array.from({ length: 13 }, (_, i) => ({ id: i + 1, name: `Skin ${i + 1}`, category_id: `work_${i}`, category: `Work ${i}` }));
  const f = setup(entries), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
  f.answers.push({ selection: 12 }, { selection: 0 }, { selection: 1 }, { selection: 1 }, { selection: 12 }, { selection: 0 });
  await f.core.selectSkin(player, target);
  assert.deepEqual(f.forms[1].buttons, ["Work 12（1件）", "すべてのキャラクター（13件）", "前のページ"]);
  assert.deepEqual(f.forms[2].buttons, ["Skin 13", "カテゴリに戻る"]);
  assert.deepEqual(f.forms[3].buttons, f.forms[1].buttons);
  assert.equal(f.forms[3].titleText, f.forms[1].titleText);
  assert.equal(target.variant, 12);
  assert.match(f.forms.at(-1).titleText, /すべてのキャラクター.*2\/2/);
});

test("returning to a later category page permits previous-page navigation and cancellation", async () => {
  const entries = Array.from({ length: 25 }, (_, i) => ({ id: i + 1, name: `Skin ${i + 1}`, category_id: `work_${i}`, category: `Work ${i}` }));
  for (const cancel of [false, true]) {
    const f = setup(entries), player = f.makePlayer(), target = f.makeEntity("ichiyon:avatar"); player.mode = "creative";
    target.variant = 7;
    f.answers.push({ selection: 12 }, { selection: 13 }, { selection: 0 }, { selection: 1 });
    if (cancel) f.answers.push({ canceled: true });
    else f.answers.push({ selection: 2 }, { selection: 0 }, { selection: 0 });
    await f.core.selectSkin(player, target);
    assert.match(f.forms[4].titleText, /3\/3/);
    assert.equal(target.variant, cancel ? 7 : 12);
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
test("web placement bridge command is no longer handled by cosmetics", async () => {
  const { core, makePlayer, catalog, spawned } = setup(); const player = makePlayer(); const results = [];
  const helpers = { isValidPlayerName: name => name === "Steve", findOnlinePlayer: () => player, playerForwardSpawnLocation: () => ({ x: 0, y: 64, z: 2 }), postResult: async (...args) => results.push(args) };
  const command = { type: "cosmetic_avatar_spawn", request_id: "request", minecraft_player: "Steve", skin_id: 2, catalog_digest: "wrong" };
  assert.equal(await core.handleCommand(command, helpers), false); assert.equal(spawned.length, 0); assert.equal(results.length, 0);
  assert.equal(await core.handleCommand({ type: "legacy" }, helpers), false);
});
test("startup registers reconnect and death-respawn restoration", async () => {
  const { core, subscriptions, makePlayer, advance } = setup(); core.start(); const player = makePlayer(); player.dynamic["ichiyon:selected_skin"] = 3;
  subscriptions.spawn({ player, initialSpawn: false }); await advance(); assert.equal(player.getProperty("ichiyon:skin_id"), 3);
  assert.equal(typeof subscriptions.leave, "function");
});
test("mannequin menu switches free roam per entity and keeps the other actions", async () => {
  const f = setup(), player = f.makePlayer(), first = f.makeEntity("ichiyon:avatar"), second = f.makeEntity("ichiyon:avatar");
  player.mode = "creative"; player.isSneaking = true;
  f.answers.push({ selection: 4 });
  await f.core.mannequinMenu(player, first);
  const form = f.forms.at(-1);
  assert.equal(form.buttons[0], "Skin 1に着替える");
  assert.equal(form.buttons[1], "元のスキンに戻す");
  assert.equal(form.buttons[2], "このマネキンを変更");
  assert.equal(form.buttons[3], "このマネキンを撤去");
  assert.equal(form.buttons[4], "自由行動に切り替える");
  assert.match(form.bodyText, /現在の行動：通常行動/);
  assert.equal(first.dynamic["ichiyon:avatar_roam"], 1);
  assert.equal(first.events.at(-1), "ichiyon:avatar_roam");
  assert.equal(second.dynamic["ichiyon:avatar_roam"], undefined);
  assert.equal(second.events.length, 0);
  f.answers.push({ selection: 4 });
  await f.core.mannequinMenu(player, first);
  assert.equal(f.forms.at(-1).buttons[4], "通常行動に戻す");
  assert.match(f.forms.at(-1).bodyText, /現在の行動：自由行動/);
  assert.equal(first.dynamic["ichiyon:avatar_roam"], 0);
  assert.equal(first.events.at(-1), "ichiyon:avatar_calm");
  assert.equal(second.events.length, 0);
});
test("free roam survives reload and walks back before calm movement returns", async () => {
  const { core, makeEntity } = setup();
  const entity = makeEntity("ichiyon:avatar");
  entity.dynamic["ichiyon:avatar_roam"] = 1;
  entity.dynamic["ichiyon:avatar_anchor_x"] = 0;
  entity.dynamic["ichiyon:avatar_anchor_y"] = 64;
  entity.dynamic["ichiyon:avatar_anchor_z"] = 0;
  entity.location = { x: 10, y: 64, z: 0 };
  core.restoreRoam(entity);
  assert.equal(entity.events.at(-1), "ichiyon:avatar_roam");
  assert.equal(entity.dynamic["ichiyon:avatar_anchor_x"], 0);
  const other = makeEntity("ichiyon:avatar");
  other.dynamic["ichiyon:avatar_roam"] = 0;
  core.restoreRoam(other);
  assert.equal(other.events.at(-1), "ichiyon:avatar_calm");
  assert.equal(entity.dynamic["ichiyon:avatar_roam"], 1);
});
test("leaving free roam outside the calm radius walks home before restoring it", async () => {
  const { core, makePlayer, makeEntity, answers, forms } = setup();
  const player = makePlayer(), entity = makeEntity("ichiyon:avatar");
  player.location = { x: 10, y: 64, z: 0 };
  entity.dynamic["ichiyon:avatar_roam"] = 1;
  entity.dynamic["ichiyon:avatar_anchor_x"] = 0;
  entity.dynamic["ichiyon:avatar_anchor_y"] = 64;
  entity.dynamic["ichiyon:avatar_anchor_z"] = 0;
  entity.location = { x: 10, y: 64, z: 0 };
  answers.push({ selection: 2 });
  await core.mannequinMenu(player, entity);
  assert.equal(forms.at(-1).buttons.at(-1), "通常行動に戻す");
  assert.equal(entity.dynamic["ichiyon:avatar_roam"], 0);
  assert.equal(entity.dynamic["ichiyon:avatar_returning"], 1);
  assert.equal(entity.events.at(-1), "ichiyon:avatar_return");
  core.maintainRoam(entity);
  assert.equal(entity.dynamic["ichiyon:avatar_returning"], 1);
  entity.location = { x: 1, y: 64, z: 1 };
  core.maintainRoam(entity);
  assert.equal(entity.dynamic["ichiyon:avatar_returning"], 0);
  assert.equal(entity.events.at(-1), "ichiyon:avatar_calm");
});
test("free roam attempts movement more often and cannot leave its home radius", async () => {
  const avatar = JSON.parse(await readFile(new URL("../minecraft/behavior_packs/ichiyon_avatar_bp/entities/avatar.json", import.meta.url), "utf8"));
  const entity = avatar["minecraft:entity"];
  const calm = entity.components["minecraft:behavior.random_stroll"];
  const free = entity.component_groups["ichiyon:avatar_free"]["minecraft:behavior.random_stroll"];
  const calmRadius = entity.components["minecraft:home"].restriction_radius;
  const freeRadius = entity.component_groups["ichiyon:avatar_free"]["minecraft:home"].restriction_radius;
  const attempts = interval => Math.floor(4000 / interval);
  assert.equal(attempts(calm.interval), 50);
  assert.equal(attempts(free.interval), 200);
  let calmReach = 0, freeReach = 0;
  for (let i = 0; i < attempts(calm.interval); i++) calmReach = Math.min(calmRadius, calmReach + calm.xz_dist);
  for (let i = 0; i < attempts(free.interval); i++) freeReach = Math.min(freeRadius, freeReach + free.xz_dist);
  assert.equal(calmReach, 2);
  assert.equal(freeReach, 16);
  assert.equal(entity.components["minecraft:movement"].value, 0.12);
});
