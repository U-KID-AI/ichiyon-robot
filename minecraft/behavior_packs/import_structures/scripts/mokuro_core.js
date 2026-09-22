export const MOKURO = "ichiyon:mokuro";
export const OWNER = "mokuro:owner";
export const RETURN = "mokuro:return";
const ATTACH = "ichiyon:mokuro_attach";
const DETACH = "ichiyon:mokuro_detach";
const GLIDE = "ichiyon:gliding";
const CARRIED = "ichiyon:carried";
const BOOST = "ichiyon:boosting";
export const JUMP_ASSIST = 1.15;

const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
const point = (v) => ({ x: v.x, y: v.y, z: v.z });
const alive = (e) => e?.isValid && e.getComponent("minecraft:health")?.currentValue > 0;

export function attachmentPose(player, mode) {
  const yaw = player.getRotation().y;
  const r = yaw * Math.PI / 180;
  if (mode === "head") {
    const head = player.getHeadLocation();
    return { location: { x: head.x, y: head.y + 0.22, z: head.z }, rotation: { x: 0, y: yaw } };
  }
  const p = player.location;
  return { location: { x: p.x + Math.sin(r) * 0.55, y: p.y + 0.65, z: p.z - Math.cos(r) * 0.55 },
    rotation: { x: 0, y: ((yaw + 360) % 360) - 180 } };
}

export function glideImpulse(velocity, view) {
  const horizontal = Math.hypot(view.x, view.z);
  const speed = 0.32 + Math.max(0, -view.y) * 0.18;
  const descent = -0.10 - Math.max(0, -view.y) * 0.20;
  const clamp = (v, n) => Math.max(-n, Math.min(n, v));
  return {
    x: horizontal > 0.01 ? clamp((view.x / horizontal * speed - velocity.x) * 0.08, 0.04) : 0,
    y: clamp(descent - velocity.y, 0.12),
    z: horizontal > 0.01 ? clamp((view.z / horizontal * speed - velocity.z) * 0.08, 0.04) : 0,
  };
}

export function createMokuro({ world, system, ActionFormData, report = console.warn }) {
  const owners = new Map();
  const entities = new Map();
  const forms = new Set();
  const notices = new Map();
  function error(label, e) {
    if ((notices.get(label) ?? -200) + 200 > system.currentTick) return;
    notices.set(label, system.currentTick);
    report(`[Mokuro] ${label}: ${String(e).slice(0, 300)}`);
  }
  function emptyHand(player) {
    return !player.getComponent("minecraft:inventory")?.container?.getItem(player.selectedSlotIndex);
  }
  function forget(state) {
    if (owners.get(state.playerId) === state) owners.delete(state.playerId);
    entities.delete(state.entity.id);
  }
  function saveReturn(entity, location, dimension) {
    entity.setDynamicProperty(RETURN, JSON.stringify({ ...point(location), dimension }));
  }
  function restore(entity) {
    // Restore physics first; persistent ownership survives a failed event for load-time recovery.
    entity.triggerEvent(DETACH);
    entity.setProperty(GLIDE, false);
    entity.setProperty(BOOST, false);
    const raw = entity.getDynamicProperty(RETURN);
    if (typeof raw === "string") {
      try {
        const home = JSON.parse(raw);
        if ([home.x, home.y, home.z].every(Number.isFinite) && typeof home.dimension === "string") {
          entity.tryTeleport(point(home), { dimension: world.getDimension(home.dimension), checkForBlocks: true });
        }
      } catch (e) { error("return position unavailable; restored normal Mob in place", e); }
    }
    entity.setDynamicProperty(OWNER, undefined);
    entity.setDynamicProperty(RETURN, undefined);
  }
  function detach(state, message = false) {
    state.gliding = false;
    state.landingUntil = -1;
    if (alive(state.entity)) restore(state.entity);
    forget(state);
    if (message && alive(state.player)) state.player.sendMessage("モクローを降ろしました。");
  }
  function recover(entity) {
    if (entity?.typeId !== MOKURO || !alive(entity) || entities.has(entity.id)) return;
    if (entity.getDynamicProperty(OWNER) !== undefined || entity.getProperty(CARRIED)) restore(entity);
    // Spawn/load repair only when mobile components are absent; never restart a healthy path.
    else if (!entity.getComponent("minecraft:navigation.walk")
        || !(entity.getComponent("minecraft:movement")?.currentValue > 0)) entity.triggerEvent(DETACH);
  }
  function usable(player, entity) {
    return alive(player) && alive(entity) && player.dimension.id === entity.dimension.id
      && distance(player.location, entity.location) <= 5;
  }
  function attach(player, entity, mode) {
    if (!["head", "back"].includes(mode) || !usable(player, entity) || !emptyHand(player)) return false;
    if (owners.has(player.id) || entities.has(entity.id) || entity.getDynamicProperty(OWNER) !== undefined) return false;
    if (entity.getComponent("minecraft:leashable")?.isLeashed) {
      player.sendMessage("リードを外してからモクローを乗せてください。");
      return false;
    }
    const state = { player, playerId: player.id, entity, mode, gliding: false, landingUntil: -1,
      boosted: false, leftGround: false, launchTick: -100,
      last: point(player.location), dimension: player.dimension.id, groundTick: -100,
      saved: point(entity.location), savedDimension: entity.dimension.id };
    // Save before disabling AI so even a Script reload mid-attach can recover the same entity.
    saveReturn(entity, entity.location, entity.dimension.id);
    entity.setDynamicProperty(OWNER, player.id);
    owners.set(player.id, state);
    entities.set(entity.id, state);
    try {
      entity.triggerEvent(ATTACH);
      const pose = attachmentPose(player, mode);
      entity.teleport(pose.location, { rotation: pose.rotation, keepVelocity: false });
      player.sendMessage(mode === "head"
        ? "モクローを頭に乗せました。地上でジャンプすると大ジャンプ→回転→自動滑空。落下中のジャンプでも滑空、しゃがむと解除。地上で素手のまま、しゃがみ＋ジャンプで降ろせます。"
        : "モクローを背中に乗せました。地上で素手のまま、しゃがみ＋ジャンプで降ろせます。背中では滑空できません。");
      return true;
    } catch (e) {
      detach(state);
      throw e;
    }
  }
  async function menu(player, entity) {
    if (forms.has(player.id) || !usable(player, entity) || !emptyHand(player)) return;
    forms.add(player.id);
    try {
      const state = entities.get(entity.id);
      if (state && state.playerId !== player.id) {
        player.sendMessage("このモクローは別のプレイヤーが連れています。");
        return;
      }
      const form = new ActionFormData().title("モクロー");
      if (state) form.body("通常のモクローに戻します。").button("降ろす").button("キャンセル");
      else form.body("このモクローをどこに乗せますか？").button("頭に乗せる").button("背中に背負う").button("キャンセル");
      const result = await form.show(player);
      if (result.canceled || !usable(player, entity) || !emptyHand(player)) return;
      if (state) {
        if (result.selection === 0 && owners.get(player.id) === state && !state.gliding) detach(state, true);
      } else if (result.selection === 0 || result.selection === 1) {
        attach(player, entity, result.selection === 0 ? "head" : "back");
      }
    } catch (e) { error("interaction", e); }
    finally { forms.delete(player.id); }
  }
  function interact(event) {
    if (event.target?.typeId !== MOKURO || !emptyHand(event.player)) return;
    event.cancel = true;
    system.run(() => void menu(event.player, event.target));
  }
  function flightAllowed(state) {
    const p = state.player;
    return state.mode === "head" && alive(p) && alive(state.entity) && !p.isGliding && !p.isFlying
      && !p.isInWater && !p.isSwimming && !p.isClimbing && !p.isSleeping
      && !p.getComponent("minecraft:riding")
      && p.getComponent("minecraft:equippable")?.getEquipment("Chest")?.typeId !== "minecraft:elytra";
  }
  function jump(player) {
    const state = owners.get(player.id);
    if (!state || !alive(player)) return;
    if (player.isSneaking && emptyHand(player) && (player.isOnGround || system.currentTick - state.groundTick <= 4)) {
      void menu(player, state.entity);
    } else if (!player.isSneaking && flightAllowed(state) && !state.boosted
        && system.currentTick - state.launchTick >= 20
        && (player.isOnGround || system.currentTick - state.groundTick <= 4)) {
      player.applyImpulse({ x: 0, y: JUMP_ASSIST, z: 0 });
      state.gliding = false;
      state.entity.setProperty(GLIDE, false);
      state.boosted = true;
      state.leftGround = false;
      state.launchTick = system.currentTick;
      state.entity.setProperty(BOOST, true);
    } else if (!player.isOnGround && !player.isSneaking && player.getVelocity().y < -0.03 && flightAllowed(state)) {
      state.gliding = true;
      state.entity.setProperty(GLIDE, true);
    }
  }
  function releasePlayer(id) {
    const state = owners.get(id);
    if (state) detach(state);
    forms.delete(id);
  }
  function died(entity) {
    const state = entities.get(entity.id);
    if (state) forget(state);
    else releasePlayer(entity.id);
  }
  function fall(event) {
    const state = owners.get(event.hurtEntity?.id);
    if (event.damageSource?.cause === "fall" && state && flightAllowed(state)
        && (state.boosted || state.gliding || state.landingUntil >= system.currentTick)) event.cancel = true;
  }
  function tick() {
    for (const state of owners.values()) {
      try {
        const { player: p, entity } = state;
        if (!alive(entity)) { forget(state); continue; }
        if (!alive(p) || p.dimension.id !== state.dimension || distance(p.location, state.last) > 16) {
          detach(state); continue;
        }
        state.last = point(p.location);
        if (state.boosted) {
          if (!flightAllowed(state) || p.isSneaking) {
            state.boosted = false;
            entity.setProperty(BOOST, false);
          } else if (!p.isOnGround) {
            state.leftGround = true;
            if (p.getVelocity().y <= 0.03) {
              state.boosted = false;
              entity.setProperty(BOOST, false);
              state.gliding = true;
              entity.setProperty(GLIDE, true);
            }
          } else if (state.leftGround || system.currentTick - state.launchTick > 4) {
            state.boosted = false;
            state.landingUntil = system.currentTick + 2;
            entity.setProperty(BOOST, false);
          }
        }
        if (p.isOnGround) {
          state.groundTick = system.currentTick;
          if (distance(state.saved, p.location) > 1 || state.savedDimension !== p.dimension.id) {
            // The player's feet are an occupied but non-solid, grounded recovery position.
            saveReturn(entity, p.location, p.dimension.id);
            state.saved = point(p.location);
            state.savedDimension = p.dimension.id;
          }
        }
        if (state.gliding) {
          if (!flightAllowed(state) || p.isSneaking || p.isOnGround) {
            state.landingUntil = p.isOnGround && flightAllowed(state) ? system.currentTick + 2 : -1;
            state.gliding = false;
            entity.setProperty(GLIDE, false);
          } else {
            p.applyImpulse(glideImpulse(p.getVelocity(), p.getViewDirection()));
          }
        }
        const pose = attachmentPose(p, state.mode);
        entity.teleport(pose.location, { rotation: pose.rotation, keepVelocity: false });
      } catch (e) {
        error("follow/flight; restoring normal Mob", e);
        try { detach(state); } catch (cleanupError) { error("detach will retry", cleanupError); }
      }
    }
  }
  function scan() {
    for (const id of ["overworld", "nether", "the_end"]) {
      try {
        for (const e of world.getDimension(id).getEntities({ type: MOKURO })) {
          try { recover(e); } catch (errorValue) { error("load recovery will retry", errorValue); }
        }
      } catch (e) { error(`loaded Mokuro scan ${id}`, e); }
    }
  }
  return { attach, detach, recover, menu, interact, jump, releasePlayer, died, fall, tick, scan, owners, entities };
}
