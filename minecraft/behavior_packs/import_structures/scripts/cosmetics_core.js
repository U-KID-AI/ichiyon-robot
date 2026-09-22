// Injected engine APIs keep state transitions testable without a live world.
export function createCosmetics({ catalog, world, system, ItemStack, ActionFormData, GameMode }) {
  const egg = "ichiyon:avatar_selector";
  const slots = ["hat", "face", "neck", "back"];
  const slotLabels = { hat: "頭", face: "顔", neck: "首", back: "背中" };
  const skins = new Map(catalog.skins.map(skin => [skin.id, skin]));
  const accessories = new Map(catalog.accessories.map(item => [item.id, item]));
  const itemMap = new Map(catalog.accessories.map(item => [item.item, item]));
  const playersBusy = new Map();
  const entitiesBusy = new Set();
  const valid = entity => !!entity && entity.isValid;
  const creative = player => player.getGameMode() === GameMode.Creative;
  const nearby = (player, entity) => valid(player) && valid(entity) && player.dimension.id === entity.dimension.id && distance(player.location, entity.location) <= 64;
  const inventory = player => player.getComponent("minecraft:inventory")?.container;
  const say = (player, message) => { if (valid(player)) player.sendMessage(`§e${message}`); };

  function queue(player, task) {
    if (playersBusy.has(player.id)) return;
    const token = {};
    playersBusy.set(player.id, token);
    system.run(() => {
      Promise.resolve().then(() => { if (valid(player)) return task(); })
        .catch(() => say(player, "操作できませんでした。ほかの画面を閉じて、もう一度お試しください。"))
        .finally(() => system.runTimeout(() => {
          if (playersBusy.get(player.id) === token) playersBusy.delete(player.id);
        }, 4));
    });
  }

  async function choose(player, title, entries, description = "") {
    let page = 0;
    const pageSize = 12;
    while (valid(player)) {
      const visible = entries.slice(page * pageSize, (page + 1) * pageSize);
      const choices = visible.map(entry => ({ value: entry.value, text: entry.text }));
      if (page > 0) choices.push({ page: page - 1, text: "前のページ" });
      if ((page + 1) * pageSize < entries.length) choices.push({ page: page + 1, text: "次のページ" });
      const form = new ActionFormData().title(`${title} (${page + 1}/${Math.max(1, Math.ceil(entries.length / pageSize))})`).body(description);
      choices.forEach(choice => form.button(choice.text));
      const result = await form.show(player);
      if (result.canceled || !Number.isInteger(result.selection)) return undefined;
      const choice = choices[result.selection];
      if (!choice) return undefined;
      if (choice.page !== undefined) { page = choice.page; continue; }
      return choice.value;
    }
    return undefined;
  }

  function changeSkin(player, id) {
    if (id !== 0 && !skins.has(id)) throw new Error("unknown skin");
    const previous = player.getDynamicProperty("ichiyon:selected_skin");
    player.setDynamicProperty("ichiyon:selected_skin", id);
    try { player.setProperty("ichiyon:skin_id", id); }
    catch (error) { player.setDynamicProperty("ichiyon:selected_skin", previous); throw error; }
  }

  function restoreSkin(player) {
    const id = player.getDynamicProperty("ichiyon:selected_skin");
    if (skins.has(id)) {
      player.setProperty("ichiyon:skin_id", id);
      return;
    }
    // Deleted or otherwise unavailable skin IDs are normalized to vanilla.
    player.setDynamicProperty("ichiyon:selected_skin", 0);
    player.setProperty("ichiyon:skin_id", 0);
  }

  function safePlacement(player, location) {
    if (!valid(player) || distance(player.location, location) > 64) throw new Error("too far");
    const dimension = player.dimension;
    const feet = dimension.getBlock(location);
    const head = dimension.getBlock({ ...location, y: location.y + 1 });
    const floor = dimension.getBlock({ ...location, y: location.y - 1 });
    if (!feet?.isAir || !head?.isAir || !floor || floor.isAir || floor.isLiquid) throw new Error("no room");
  }

  function spawn(player, id, location) {
    if (!skins.has(id)) throw new Error("unknown skin");
    safePlacement(player, location);
    const entity = player.dimension.spawnEntity("ichiyon:avatar", location);
    try {
      entity.triggerEvent(`ichiyon:cosmetic_${id}`);
      entity.setRotation({ x: 0, y: player.getRotation().y });
      entity.nameTag = skins.get(id).name;
      return entity;
    } catch (error) {
      entity.remove();
      throw error;
    }
  }

  async function selectSkin(player, target, location) {
    if (!creative(player)) { say(player, "マネキンの配置・変更はクリエイティブで行ってください。"); return; }
    const dimension = player.dimension.id;
    if (!catalog.skins.length) { say(player, "配置できるスキンが登録されていません。"); return; }
    const id = await choose(player, "マネキンを選択", catalog.skins.map(skin => ({ value: skin.id, text: skin.name })), "配置するキャラクターを選んでください。");
    if (id === undefined || !valid(player) || !creative(player) || player.dimension.id !== dimension) return;
    if (target) {
      if (!nearby(player, target)) { say(player, "マネキンから離れたため中止しました。"); return; }
      target.triggerEvent(`ichiyon:cosmetic_${id}`);
      target.nameTag = skins.get(id).name;
    } else {
      // Recheck item after the form; switching inventory cannot create a free placement.
      if (inventory(player)?.getItem(player.selectedSlotIndex)?.typeId !== egg) return;
      spawn(player, id, location);
    }
  }

  async function mannequinMenu(player, target) {
    if (!nearby(player, target)) return;
    const skin = skins.get((target.getComponent("minecraft:variant")?.value ?? -1) + 1);
    if (!skin) { say(player, "このマネキンの素材を読み込めません。管理者に連絡してください。"); return; }
    const editing = creative(player) && player.isSneaking;
    const options = [{ value: "wear", text: `${skin.name}に着替える` }, { value: "restore", text: "元のスキンに戻す" }];
    if (editing) options.push({ value: "edit", text: "このマネキンを変更" }, { value: "remove", text: "このマネキンを撤去" });
    const choice = await choose(player, skin.name, options, "着替えはこのサーバー内で保存され、ほかのプレイヤーにも表示されます。");
    if (!nearby(player, target)) return;
    if (choice === "wear") {
      // Another creative player may have changed the mannequin while this form was open.
      if (target.getComponent("minecraft:variant")?.value !== skin.id - 1) { say(player, "マネキンが変更されました。もう一度選んでください。"); return; }
      changeSkin(player, skin.id);
      say(player, `${skin.name}に着替えました。`);
    } else if (choice === "restore") {
      changeSkin(player, 0);
      say(player, "元のスキンに戻しました。");
    } else if (editing && creative(player) && choice === "edit") {
      await selectSkin(player, target);
    } else if (editing && creative(player) && choice === "remove") {
      const confirmed = await choose(player, "マネキンの撤去", [{ value: true, text: "撤去する" }, { value: false, text: "戻る" }]);
      if (confirmed === true && nearby(player, target) && creative(player)) target.remove();
    }
  }

  function mutateAccessory(player, target, slot, nextId) {
    if (!nearby(player, target) || entitiesBusy.has(target.id)) throw new Error("busy or too far");
    const component = `ichiyon:${slot}`;
    const oldId = target.getProperty(component);
    if (oldId === nextId) return;
    const previous = accessories.get(oldId);
    const next = accessories.get(nextId);
    if (!slots.includes(slot) || (oldId !== 0 && !previous) || (nextId !== 0 && (!next || next.slot !== slot))) throw new Error("invalid slot");
    const bag = inventory(player);
    if (!bag) throw new Error("no inventory");
    let index = player.selectedSlotIndex;
    let before;
    let after;
    const consumes = !creative(player);
    if (next) {
      before = bag.getItem(index);
      if (before?.typeId !== next.item || before.amount !== 1) throw new Error("held item changed");
      after = previous ? new ItemStack(previous.item, 1) : undefined;
    } else if (consumes && previous) {
      index = Array.from({ length: bag.size }, (_, i) => i).find(i => !bag.getItem(i));
      if (index === undefined) { say(player, "返却するため、持ち物に空きを1つ作ってください。"); return; }
      before = undefined;
      after = new ItemStack(previous.item, 1);
    }
    entitiesBusy.add(target.id);
    try {
      if (consumes) bag.setItem(index, after);
      try { target.setProperty(component, nextId); }
      catch (error) { if (consumes) bag.setItem(index, before); throw error; }
    } finally {
      // Entity properties take effect on the next tick: do not read stale values twice.
      system.runTimeout(() => entitiesBusy.delete(target.id), 2);
    }
    say(player, next ? `${slotLabels[slot]}に${next.name}を装着しました。` : `${slotLabels[slot]}のアクセサリーを外しました。`);
  }

  async function accessoryMenu(player, target) {
    const equipped = slots.map(slot => ({ slot, id: target.getProperty(`ichiyon:${slot}`) })).filter(item => accessories.has(item.id));
    if (!equipped.length) { say(player, "アクセサリーを手に持って使うと装着できます。"); return; }
    const chosen = await choose(player, "アクセサリーを外す", equipped.map(item => ({ value: item, text: `${slotLabels[item.slot]}：${accessories.get(item.id).name}` })));
    if (!chosen || !nearby(player, target)) return;
    if (target.getProperty(`ichiyon:${chosen.slot}`) !== chosen.id) { say(player, "装着状態が変わりました。もう一度選んでください。"); return; }
    mutateAccessory(player, target, chosen.slot, 0);
  }

  function interact(event) {
    const { player, target } = event;
    if (target.typeId === "ichiyon:avatar") {
      event.cancel = true;
      queue(player, () => mannequinMenu(player, target));
    } else if (["ichiyon:molcar", "ichiyon:molcar2", "ichiyon:molcar3"].includes(target.typeId)) {
      const accessory = itemMap.get(event.itemStack?.typeId);
      if (accessory) {
        event.cancel = true;
        queue(player, () => mutateAccessory(player, target, accessory.slot, accessory.id));
      } else if (player.isSneaking && !event.itemStack) {
        event.cancel = true;
        queue(player, () => accessoryMenu(player, target));
      }
      // Other interactions remain available to riding, feeding and leads.
    }
  }

  function useEgg(event) {
    if (event.itemStack?.typeId !== egg) return;
    event.cancel = true;
    if (event.isFirstEvent === false) return;
    const player = event.player || event.source;
    const block = event.block || player.getBlockFromViewDirection({ maxDistance: 6 })?.block;
    if (!block) { queue(player, () => say(player, "置きたい場所の地面に向けて使ってください。")); return; }
    const location = { x: block.location.x + 0.5, y: block.location.y + 1, z: block.location.z + 0.5 };
    queue(player, () => selectSkin(player, undefined, location));
  }

  async function handleCommand(command, helpers) {
    return false;
  }

  function start() {
    world.beforeEvents.playerInteractWithEntity.subscribe(interact);
    world.beforeEvents.playerInteractWithBlock.subscribe(useEgg);
    world.beforeEvents.itemUse.subscribe(useEgg);
    world.afterEvents.playerSpawn.subscribe(({ player }) => system.run(() => {
      try { if (valid(player)) restoreSkin(player); } catch { say(player, "着替えを復元できません。パックの読み込みを確認してください。"); }
    }));
    world.afterEvents.playerLeave.subscribe(({ playerId }) => playersBusy.delete(playerId));
    // Also handles a script reload with players already online.
    system.run(() => { for (const player of world.getAllPlayers()) { try { restoreSkin(player); } catch {} } });
  }
  return { start, interact, useEgg, changeSkin, restoreSkin, mutateAccessory, mannequinMenu, accessoryMenu, selectSkin, choose, spawn, handleCommand };
}

function distance(a, b) {
  return (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2;
}
