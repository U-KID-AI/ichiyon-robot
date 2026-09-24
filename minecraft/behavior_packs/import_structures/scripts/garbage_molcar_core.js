export const GARBAGE = "ichiyon:garbage_molcar";
export const PAW = "ichiyon:bartholomew_kuma_paw";
export const OWNER = "garbage_molcar:owner";
const inventory = (entity) => entity.getComponent("minecraft:inventory")?.container;
const held = (player) => inventory(player)?.getItem(player.selectedSlotIndex);
const alive = (entity) => entity?.isValid && entity.getComponent("minecraft:health")?.currentValue > 0;
const near = (player, entity) => alive(player) && alive(entity)
  && player.dimension.id === entity.dimension.id
  && Math.hypot(player.location.x - entity.location.x, player.location.y - entity.location.y,
    player.location.z - entity.location.z) <= 6;

export function canStore(entity, drop) {
  if (!entity.getDynamicProperty(OWNER)) return true;
  const stack = drop.getComponent("minecraft:item")?.itemStack;
  const container = inventory(entity);
  if (!stack || !container) return false;
  let room = 0;
  for (let i = 0; i < container.size; i++) {
    const item = container.getItem(i);
    if (!item || item.isStackableWith(stack)) room += stack.maxAmount - (item?.amount ?? 0);
    if (room >= stack.amount) return true;
  }
  return false;
}

// Short, unobstructed detours use physics impulses, not teleportation or attack targets.
export function seekDrop(entity, drops, player) {
  const from = entity.location;
  for (const drop of drops) {
    if (!drop.isValid || !canStore(entity, drop)) continue;
    const to = drop.location;
    if (Math.abs(to.y - from.y) > 1) continue;
    if (player && Math.hypot(to.x - player.location.x, to.z - player.location.z) > 8) continue;
    const dx = to.x - from.x, dz = to.z - from.z;
    const distance = Math.hypot(dx, dz);
    if (distance <= 0.8) continue;
    const direction = { x: dx / distance, y: 0, z: dz / distance };
    const blocked = [-0.65, 0, 0.65].some((side) => entity.dimension.getBlockFromRay(
      { x: from.x - direction.z * side, y: from.y + 0.5, z: from.z + direction.x * side },
      direction, { maxDistance: distance, includePassableBlocks: false, includeLiquidBlocks: true }));
    if (!blocked) return { direction, distance };
  }
  return undefined;
}

export function isMob(entity) {
  return entity?.typeId !== "minecraft:player" && alive(entity)
    && entity.getComponent("minecraft:type_family")?.hasTypeFamily("mob") === true;
}

export function collect(entity, drop) {
  if (!drop?.isValid || drop.typeId !== "minecraft:item") return false;
  if (!entity.getDynamicProperty(OWNER)) { drop.remove(); return true; }
  const container = inventory(entity);
  const stack = drop.getComponent("minecraft:item")?.itemStack;
  if (!container || !stack) return false;
  const edits = [];
  let remaining = stack.amount;
  // Plan the complete transfer before touching the ground entity or its opaque ItemStack data.
  for (let i = 0; i < container.size && remaining > 0; i++) {
    const before = container.getItem(i);
    if (before && !before.isStackableWith(stack)) continue;
    const add = Math.min(remaining, stack.maxAmount - (before?.amount ?? 0));
    if (add <= 0) continue;
    const after = (before ?? stack).clone();
    after.amount = (before?.amount ?? 0) + add;
    edits.push({ i, before, after });
    remaining -= add;
  }
  if (remaining) return false;
  const applied = [];
  try {
    for (const edit of edits) {
      container.setItem(edit.i, edit.after);
      applied.push(edit);
    }
    drop.remove();
    return true;
  } catch (error) {
    for (const edit of applied.reverse()) container.setItem(edit.i, edit.before);
    throw error;
  }
}

export function withdraw(entity, player, slot) {
  const source = inventory(entity);
  const destination = inventory(player);
  if (!source || !destination || slot < 0 || slot >= source.size) return 0;
  const count = source.getItem(slot)?.amount ?? 0;
  if (!count) return 0;
  // Native transfer retains metadata and leaves any untransferred remainder in the source slot.
  source.transferItem(slot, destination);
  return count - (source.getItem(slot)?.amount ?? 0);
}

export function createGarbageMolcar({ world, system, ActionFormData, report = console.warn }) {
  const forms = new Set();
  const sounds = new Map();
  const initialized = new Set();
  let lastError = -200;
  function error(e) {
    if (system.currentTick - lastError < 200) return;
    lastError = system.currentTick;
    report(`[GarbageMolcar] ${String(e).slice(0, 300)}`);
  }
  const authorized = (player, entity) => near(player, entity) && !held(player)
    && entity.getDynamicProperty(OWNER) === player.id;
  const sound = (entity, name) => {
    try { entity.dimension.playSound(`ichiyon:molcar.${name}`, entity.location, { volume: 0.7 }); }
    catch (e) { error(e); }
  };
  function stop(entity) {
    // Only the explicit, confirmed owner action calls this destructive operation.
    inventory(entity).clearAll();
    entity.triggerEvent("ichiyon:garbage_stop");
    entity.setDynamicProperty(OWNER, undefined);
  }
  function start(player, entity) {
    if (!near(player, entity) || held(player) || entity.getDynamicProperty(OWNER)) return;
    entity.setDynamicProperty(OWNER, player.id);
    entity.triggerEvent("ichiyon:garbage_start");
    system.run(() => {
      try {
        if (!alive(entity) || entity.getDynamicProperty(OWNER) !== player.id) return;
        if (!alive(player) || !entity.getComponent("minecraft:tameable")?.tame(player)) {
          // Keep stored contents even if taming fails; the owner can still withdraw them.
          if (alive(player)) player.sendMessage("追従の開始に失敗しました。保管物は保持しています。もう一度操作してください。");
          return;
        }
        player.sendMessage("追従を開始しました。拾った物は保管します。素手で操作すると取り出せます。");
        sound(entity, "pui");
      } catch (e) { error(e); }
    });
  }
  async function contents(player, entity) {
    let page = 0;
    while (authorized(player, entity)) {
      const container = inventory(entity);
      const slots = [];
      for (let i = 0; i < container.size; i++) if (container.getItem(i)) slots.push(i);
      page = Math.min(page, Math.max(0, Math.ceil(slots.length / 18) - 1));
      const displayed = slots.slice(page * 18, page * 18 + 18);
      const form = new ActionFormData().title("ゴミ収集モルカーの保管物")
        .body(`使用中 ${slots.length} / ${container.size} スロット`);
      for (const slot of displayed) {
        const item = container.getItem(slot);
        form.button(`${slot + 1}: ${item.nameTag || item.typeId} × ${item.amount}`);
      }
      form.button("前のページ").button("次のページ").button("閉じる");
      const result = await form.show(player);
      if (result.canceled || !authorized(player, entity)) return;
      const choice = result.selection;
      if (choice < displayed.length) {
        const moved = withdraw(entity, player, displayed[choice]);
        player.sendMessage(moved ? `${moved}個取り出しました。` : "インベントリに空きがありません。保管物は残っています。");
      } else if (choice === displayed.length) page = Math.max(0, page - 1);
      else if (choice === displayed.length + 1) page++;
      else return;
    }
  }
  async function menu(player, entity) {
    if (!near(player, entity) || held(player) || forms.has(entity.id)) return;
    if (!entity.getDynamicProperty(OWNER)) { start(player, entity); return; }
    if (!authorized(player, entity)) { player.sendMessage("別のプレイヤーを追従中です。"); return; }
    forms.add(entity.id);
    try {
      const result = await new ActionFormData().title("ゴミ収集モルカー")
        .body("追従中のアイテムは保管されます。追従OFFにすると保管物を全て消去します。")
        .button("中身を見る / 取り出す").button("全て取り出す").button("追従OFF").button("キャンセル").show(player);
      if (result.canceled || !authorized(player, entity)) return;
      if (result.selection === 0) await contents(player, entity);
      else if (result.selection === 1) {
        let moved = 0;
        for (let i = 0; i < inventory(entity).size; i++) moved += withdraw(entity, player, i);
        player.sendMessage(`${moved}個取り出しました。入りきらない物は保管したままです。`);
      } else if (result.selection === 2) {
        const confirmation = await new ActionFormData().title("保管物を全て消去しますか？")
          .body("追従を解除し、自動ゴミ削除モードに戻します。消した物は戻せません。")
          .button("消去して追従OFF").button("キャンセル").show(player);
        if (!confirmation.canceled && confirmation.selection === 0 && authorized(player, entity)) {
          stop(entity);
          player.sendMessage("保管物を消去して追従を解除しました。");
        }
      }
    } catch (e) { error(e); }
    finally { forms.delete(entity.id); }
  }
  function interact(event) {
    const { player, target } = event;
    if (held(player)?.typeId === PAW && isMob(target)) {
      event.cancel = true;
      system.run(() => {
        try { if (held(player)?.typeId === PAW && near(player, target) && isMob(target)) target.remove(); }
        catch (e) { error(e); }
      });
    } else if (target.typeId === GARBAGE && !held(player)) {
      event.cancel = true;
      system.run(() => { menu(player, target).catch(error); });
    }
  }
  function scan() {
    const seen = new Set();
    for (const name of ["overworld", "nether", "the_end"]) {
      try {
        const dimension = world.getDimension(name);
        for (const entity of dimension.getEntities({ type: GARBAGE })) {
          seen.add(entity.id);
          if (!alive(entity)) continue;
          try {
            const ownerId = entity.getDynamicProperty(OWNER);
            const player = ownerId ? world.getAllPlayers().find((p) => p.id === ownerId) : undefined;
            const pickupAllowed = !forms.has(entity.id) && (!ownerId || (player
              && player.dimension.id === dimension.id
              && Math.hypot(player.location.x - entity.location.x, player.location.z - entity.location.z) <= 8));
            if (ownerId) {
              const tame = entity.getComponent("minecraft:tameable");
              if (!tame) entity.triggerEvent("ichiyon:garbage_start");
              else if (player && tame.tamedToPlayerId !== ownerId) tame.tame(player);
            }
            if (!forms.has(entity.id)) {
              for (const drop of dimension.getEntities({ type: "minecraft:item", location: entity.location, maxDistance: 1.1, closest: 16 })) collect(entity, drop);
            }
            const target = pickupAllowed && !entity.getComponent("minecraft:leashable")?.isLeashed
              ? seekDrop(entity, dimension.getEntities({ type: "minecraft:item", location: entity.location, maxDistance: 8, closest: 8 }), player)
              : undefined;
            // Old saves have the owner group but not the newly separated following goal.
            if (!initialized.has(entity.id) || entity.getProperty("ichiyon:pickup_enabled") !== !!target) {
              entity.triggerEvent(target ? "ichiyon:garbage_pickup_on" : "ichiyon:garbage_pickup_off");
              initialized.add(entity.id);
            }
            if (target && entity.isOnGround) {
              const velocity = entity.getVelocity();
              const speed = Math.min(0.38, target.distance * 0.1);
              entity.setRotation({ x: 0, y: Math.atan2(-target.direction.x, target.direction.z) * 180 / Math.PI });
              entity.applyImpulse({ x: target.direction.x * speed - velocity.x, y: 0, z: target.direction.z * speed - velocity.z });
            }
            if ((sounds.get(entity.id) ?? 0) <= system.currentTick) {
              const velocity = entity.getVelocity();
              sound(entity, Math.hypot(velocity.x, velocity.z) > 0.03 ? "run" : "pui");
              sounds.set(entity.id, system.currentTick + 300 + Math.floor(Math.random() * 300));
            }
          } catch (e) { error(e); }
        }
      } catch (e) { error(e); }
    }
    for (const id of sounds.keys()) if (!seen.has(id)) sounds.delete(id);
    for (const id of initialized) if (!seen.has(id)) initialized.delete(id);
  }
  return { interact, scan, menu, start, stop, sound };
}
