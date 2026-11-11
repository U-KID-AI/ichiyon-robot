import { showFormSafely, withReceptionPanel } from "./venue_presentation.js";

const installations = new WeakMap();
const actions = new Set(["menu", "reception", "return", "reset", "repair"]);
const atKey = (dimension, at) => `${dimension}:${Math.floor(at.x)},${Math.floor(at.y)},${Math.floor(at.z)}`;
const point = at => Array.isArray(at) ? { x: at[0], y: at[1], z: at[2] } : at;
const valid = entity => typeof entity?.isValid === "function" ? entity.isValid() : entity?.isValid !== false;
const session = table => table ? JSON.stringify([table.id, table.token, table.players]) : "none";
const tell = (player, message) => {
  try { player?.sendMessage(`[JUNKET受付] ${message}`); } catch { /* Disconnected. */ }
};

// Old per-game listeners use this to avoid opening a second form for the same
// guide. Block controls do not need an NPC or any creative/operator permission.
export function isServiceGuide(world, target) {
  return Boolean(installations.get(world)?.guide(target));
}

export function serviceControlBinding(world, venue, control) {
  return installations.get(world)?.bound(venue, control) === true;
}

export function createResetPrompt({ ActionFormData }) {
  return async function promptReset(player, request) {
    const names = (request.participants ?? []).map(value => value.name ?? value.id).join("、");
    const response = await showFormSafely(new ActionFormData()
      .title(`${request.title}｜リセットへの同意`)
      .body(`この試合を中止し、会場を初期状態へ戻します。\n参加者: ${names || "参加者なし"}\n全員の同意が必要です。拒否・時間切れ・退出・試合の進行で依頼は取り消されます。`)
      .button("同意しない")
      .button("リセットに同意する"), player);
    return !response.canceled && response.selection === 1;
  };
}

export function createServiceControls({ world, system, ActionFormData, manager,
  runtimes, venues, warp, recovery }) {
  const byId = new Map(venues.map(venue => [venue.id, venue]));
  const controls = new Map(), active = new Map(), lastPress = new Map(), generations = new Map();
  let installed = false;
  for (const venue of venues) for (const control of venue.service_controls ?? []) {
    if (!actions.has(control.action)) throw new Error(`未知の会場操作: ${venue.id}/${control.action}`);
    for (const kind of ["button", "sign"]) {
      if (!control[kind]) continue;
      const key = atKey("minecraft:overworld", point(control[kind]));
      if (controls.has(key)) throw new Error(`会場操作の座標重複: ${key}`);
      controls.set(key, { venue, control, kind });
    }
  }
  const currentGeneration = id => generations.get(id) ?? 0;
  const runtimeOf = id => runtimes instanceof Map ? runtimes.get(id) : runtimes[id];
  const online = player => valid(player) && world.getAllPlayers().some(value => value.id === player.id);
  function guide(target) {
    try {
      if (!target || target.dimension?.id !== "minecraft:overworld") return null;
      const id = target.getDynamicProperty?.("junket:venue_id");
      const venue = byId.get(id);
      if (!venue) return null;
      for (const fixture of venue.fixtures.filter(value => ["reception", "return"].includes(value.role))) {
        if (!target.hasTag?.(`junket:${id}:fixture:${fixture.role}`)) continue;
        if (target.typeId !== fixture.type && target.typeId !== "minecraft:npc") continue;
        const at = point(fixture.location);
        if (Math.hypot(target.location.x - at.x, target.location.y - at.y, target.location.z - at.z) > 0.75) continue;
        return { venue, action: fixture.role === "return" ? "return" : "reception" };
      }
    } catch { /* Removed entity. */ }
    return null;
  }
  function blockControl(block) {
    const value = controls.get(atKey(block.dimension.id, block.location));
    if (!value) return null;
    const expected = value.control[`${value.kind}_block`]?.name;
    if (expected && block.typeId !== expected) return null;
    return value;
  }
  async function run(player, id, action, ticket) {
    const venue = byId.get(id);
    if (!venue || !actions.has(action)) throw new Error("この会場の操作定義がありません。");
    if (!online(player) || ticket.generation !== currentGeneration(player.id)) return false;
    if (ticket.session !== session(manager.forPlayer(player.id))) {
      tell(player, "参加状況が変わりました。もう一度ボタンをタップしてください。");
      return false;
    }
    if (action === "reception" || action === "menu") {
      const runtime = runtimeOf(id);
      if (!runtime?.openReception) throw new Error("受付を準備できていません。修復を依頼してください。");
      if (action === "menu") {
        const before = session(manager.view(id));
        const unchanged = () => online(player) && ticket.generation === currentGeneration(player.id) &&
          ticket.session === session(manager.forPlayer(player.id)) && before === session(manager.view(id));
        let selected = null;
        await withReceptionPanel(player, id, { valid: unchanged,
          actions() {
            const result = [], mine = manager.forPlayer(player.id);
            if (mine?.gameId === id || mine?.id === id) {
              if (!mine.state && runtime.startRegistration) result.push("start");
              result.push("reset");
            }
            if (recovery.isOperator(player)) result.push("repair");
            return result;
          },
          run(selection) { selected = selection; },
        }, () => runtime.openReception(player));
        // Close the per-game form and release its busy/session guard before
        // maintenance changes the table token or asks that player for consent.
        if (selected && unchanged()) {
          if (selected === "start") await runtime.startRegistration(player);
          else await run(player, id, selected, ticket);
        }
      } else await runtime.openReception(player);
      return true;
    }
    if (action === "return") {
      if (manager.forPlayer(player.id)) {
        tell(player, "参加受付で退出を選んでから、カタログへ戻ってください。");
        return false;
      }
      return await warp.returnToEntryAndWait(player);
    }
    if (action === "reset") {
      await recovery.requestReset(player, id);
      return true;
    }
    if (!recovery.isOperator(player)) {
      tell(player, "強制修復は管理者専用です。通常の参加者はリセットを使い、参加者全員で同意してください。");
      return false;
    }
    const before = session(manager.view(id));
    const response = await showFormSafely(new ActionFormData().title(`${venue.title}｜管理者の強制修復`)
      .body("参加中の試合を中止し、参加者を安全な場所へ退避して会場を修復します。通常のリセットと異なり全員同意を省略します。")
      .button("キャンセル").button("強制修復を実行"), player);
    if (response.canceled || response.selection !== 1) return false;
    if (!online(player) || ticket.generation !== currentGeneration(player.id)) return false;
    if (before !== session(manager.view(id))) {
      tell(player, "確認中に試合が変わりました。再操作してください。");
      return false;
    }
    // Recovery repeats the operator check; a self-added tag grants no right.
    await recovery.recover(id, { force: true, player });
    return true;
  }
  function dispatch(player, id, action) {
    const tick = system.currentTick ?? 0;
    if (active.has(player.id)) {
      system.run(() => tell(player, "受付の処理中です。開いている画面を閉じてから再操作してください。"));
      return Promise.resolve(false);
    }
    if (tick - (lastPress.get(player.id) ?? -100) < 6) return Promise.resolve(false);
    lastPress.set(player.id, tick);
    const ticket = { generation: currentGeneration(player.id), session: session(manager.forPlayer(player.id)) };
    active.set(player.id, ticket);
    return new Promise(resolve => system.run(() => {
      void run(player, id, action, ticket).then(resolve).catch(error => {
        tell(player, `操作できませんでした: ${error?.message ?? error}`);
        resolve(false);
      }).finally(() => { if (active.get(player.id) === ticket) active.delete(player.id); });
    }));
  }
  function handleBlock(event) {
    let entry;
    try { entry = blockControl(event.block); } catch { return; }
    if (!entry) return;
    // Cancel the vanilla button/sign action synchronously, open forms later.
    event.cancel = true;
    void dispatch(event.player, entry.venue.id, entry.control.action);
  }
  function handleEntity(event) {
    const entry = guide(event.target);
    if (!entry) return;
    event.cancel = true;
    void dispatch(event.player, entry.venue.id, entry.action);
  }
  function install() {
    if (installed) return api;
    const existing = installations.get(world);
    if (existing && existing !== api) return existing;
    installations.set(world, api);
    installed = true;
    world.beforeEvents.playerInteractWithBlock.subscribe(handleBlock);
    world.beforeEvents.playerInteractWithEntity.subscribe(handleEntity);
    world.afterEvents.playerLeave.subscribe(event => {
      generations.set(event.playerId, currentGeneration(event.playerId) + 1);
      active.delete(event.playerId); lastPress.delete(event.playerId);
    });
    return api;
  }
  const api = { install, guide, dispatch, handleBlock, handleEntity,
    bound(venue, control) {
      if (!installed || control.action !== "menu" || !runtimeOf(venue.id)?.openReception) return false;
      return ["button", "sign"].every(kind => {
        const value = controls.get(atKey("minecraft:overworld", point(control[kind])));
        return value?.venue.id === venue.id && value.control.action === "menu" &&
          value.control[`${kind}_block`]?.name === control[`${kind}_block`]?.name;
      });
    },
    controls: () => [...controls.values()].filter(value => value.kind === "button").length };
  return api;
}
