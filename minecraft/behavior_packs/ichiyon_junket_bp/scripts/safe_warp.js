import { arrivalBounds, getSharedLoader, inspectArrival } from "./shared_loader.js";

const copy = at => ({ x: at.x, y: at.y, z: at.z });
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
const cellKey = at => `${Math.floor(at.x)},${Math.floor(at.y)},${Math.floor(at.z)}`;

export function createSafeWarpController({ world, system, manager, catalog, destinations }) {
  const loader = getSharedLoader(world, system);
  const pending = new Map(), active = new Map();
  const games = new Map(catalog.WARP_CONTROLS.map(game => [game.id, game]));
  let proofPending = null;
  const native = game => catalog.WARP_OFFLINE_INSTALLED === true && game?.command_mode === "script_load";
  const tell = (player, message) => { try { player?.sendMessage(`[JUNKET WARP] ${message}`); } catch {} };
  const findPlayer = id => (world.getAllPlayers?.() ?? []).find(player => player.id === id);
  const dimension = value => typeof value === "string" ? world.getDimension(value) : value;
  function routeFor(id, player) {
    const route = destinations[id]?.(player);
    if (!route?.location || !route?.dimension) throw new Error("対応する会場の到着地点がありません。");
    return { ...route, location: copy(route.location), dimension: dimension(route.dimension) };
  }
  function requestFor(player, gameId) {
    return { playerId: player.id, gameId, origin: copy(player.location), dimensionId: player.dimension.id,
      requested: loader.now(), state: "captured" };
  }
  function guard(request) {
    if (request.cancelled) throw new Error("退出したため移動を中止しました。");
    const player = findPlayer(request.playerId);
    if (!player) throw new Error("退出したため移動を中止しました。");
    if (loader.now() - request.requested > 1200) throw new Error("移動要求の制限時間を超えました。もう一度操作してください。");
    if (player.dimension.id !== request.dimensionId || distance(player.location, request.origin) > 1.5) {
      throw new Error("待機場所から移動したためワープを中止しました。");
    }
    return player;
  }
  function cancel(playerId) {
    const captured = pending.get(playerId), running = active.get(playerId);
    if (captured) captured.cancelled = true;
    if (running) running.cancelled = true;
    if (pending.get(playerId) === captured) pending.delete(playerId);
    if (active.get(playerId) === running) active.delete(playerId);
  }
  async function waitSafe(route, context, recordInspection = null) {
    let inspection;
    for (let attempt = 0; attempt < 80; attempt++) {
      context.guard();
      inspection = inspectArrival(route.dimension, route.location);
      recordInspection?.(inspection);
      if (inspection.loaded) {
        if (!inspection.safe) throw new Error(inspection.message);
        return inspection;
      }
      await context.wait(5);
    }
    throw new Error(inspection?.message ?? "到着地点を読み込めませんでした。");
  }
  async function transfer(request, route, title) {
    const playerId = request.playerId;
    if (active.has(playerId)) { tell(findPlayer(playerId), "移動処理中です。"); return false; }
    active.set(playerId, request);
    let success = false;
    try {
      const player = guard(request);
      const table = manager?.forPlayer?.(playerId);
      const recovery = request.recoveryGame && manager?.recoveringGames?.has(request.recoveryGame) &&
        (!table || (table.gameId ?? table.game?.id) === request.recoveryGame);
      if (table && !recovery) throw new Error("試合の退出操作を完了してから移動してください。");
      tell(player, `${title}を読み込んでいます。移動せずにお待ちください。`);
      // Fixture repair uses the same global loader, so finish that lease before
      // acquiring the arrival lease. Never nest a shared-loader request.
      if (route.beforeTeleport) await route.beforeTeleport(player);
      guard(request);
      await loader.withArea({ dimension: route.dimension, bounds: arrivalBounds(route.location),
        timeoutTicks: 1200, guard: () => guard(request) }, async context => {
        await waitSafe(route, context);
        const current = guard(request);
        current.teleport(route.location, { dimension: route.dimension,
          rotation: { x: 0, y: route.rotation ?? 0 }, checkForBlocks: true });
        if (current.dimension.id !== route.dimension.id || distance(current.location, route.location) > .75) {
          throw new Error("テレポート後の到着位置を確認できませんでした。");
        }
        success = true;
        // Keep the lease alive briefly while the player's own chunks begin
        // ticking; source game fixtures have already been verified above.
        await new Promise(resolve => system.runTimeout(resolve, 10));
      });
      tell(findPlayer(playerId), `${title}へ移動しました。`);
      return true;
    } catch (error) {
      tell(findPlayer(playerId), `${success ? "到着後の後処理" : "移動"}を完了できませんでした: ${error?.message ?? error}`);
      return false;
    } finally {
      if (pending.get(playerId) === request) pending.delete(playerId);
      const release = () => { if (active.get(playerId) === request) active.delete(playerId); };
      if (success) system.runTimeout(release, 20);
      else release();
    }
  }
  function submit(request) {
    if (pending.get(request.playerId) !== request || request.state !== "captured") return;
    request.state = "queued";
    const game = games.get(request.gameId);
    try {
      const player = guard(request);
      if (!game?.enabled) throw new Error("この会場は準備中です。");
      void transfer(request, routeFor(game.id, player), game.title);
    } catch (error) {
      if (pending.get(request.playerId) === request) pending.delete(request.playerId);
      tell(findPlayer(request.playerId), error?.message ?? String(error));
    }
  }
  function capture(event, game) {
    // This callback runs in a read-only before event. Capture immutable values
    // and schedule all mutations/messages. No addTag/removeTag/teleport here.
    const player = event.player;
    const playerId = player.id;
    if (pending.has(player.id) || active.has(player.id)) {
      event.cancel = true;
      system.run(() => tell(findPlayer(playerId), "移動要求は受付済みです。"));
      return;
    }
    const request = requestFor(player, game.id);
    pending.set(player.id, request);
    // Command-block script events are normally the trigger; this bounded
    // fallback preserves the exact captured presser if redstone is delayed.
    system.runTimeout(() => submit(request), 4);
  }
  function handleRequest(event) {
    const gameId = event.message.trim().split(/\s+/)[0];
    const game = games.get(gameId);
    if (!native(game)) return false;
    if (event.sourceBlock) {
      if (event.sourceBlock.dimension.id !== catalog.WARP_DIMENSION ||
          cellKey(event.sourceBlock.location) !== game.command_block.join(",")) return true;
    }
    for (const request of pending.values()) {
      if (request.gameId === gameId && request.state === "captured") system.run(() => submit(request));
    }
    return true;
  }
  function warp(player, gameId) {
    const game = games.get(gameId);
    if (!native(game)) return null;
    if (!game.enabled) { tell(player, "この会場は準備中です。"); return Promise.resolve(false); }
    if (pending.has(player.id) || active.has(player.id)) { tell(player, "移動処理中です。"); return Promise.resolve(false); }
    let route;
    try { route = routeFor(gameId, player); }
    catch (error) { tell(player, error?.message ?? String(error)); return Promise.resolve(false); }
    const request = requestFor(player, gameId);
    pending.set(player.id, request);
    request.state = "queued";
    return transfer(request, route, game.title);
  }
  function returnToEntry(player) {
    const at = catalog.WARP_RETURN_POINT;
    if (pending.has(player.id) || active.has(player.id)) { tell(player, "移動処理中です。"); return false; }
    const request = requestFor(player, "return");
    pending.set(player.id, request); request.state = "queued";
    void transfer(request, { location: { x: at.location[0], y: at.location[1], z: at.location[2] },
      dimension: dimension(catalog.WARP_DIMENSION), rotation: at.rotation }, catalog.WARP_RETURN_TITLE ?? "カタログ会場");
    return true;
  }
  async function returnToEntryAndWait(player, recoveryGame = null) {
    const at = catalog.WARP_RETURN_POINT;
    if (pending.has(player.id) || active.has(player.id)) {
      tell(player, "移動処理中です。完了後にもう一度操作してください。");
      return false;
    }
    const request = requestFor(player, "return");
    if (recoveryGame !== null) {
      if (!manager?.recoveringGames?.has(recoveryGame)) throw new Error("会場復旧ロックがありません。");
      request.recoveryGame = recoveryGame;
    }
    pending.set(player.id, request); request.state = "queued";
    return transfer(request, { location: { x: at.location[0], y: at.location[1], z: at.location[2] },
      dimension: dimension(catalog.WARP_DIMENSION), rotation: at.rotation }, catalog.WARP_RETURN_TITLE ?? "カタログ会場");
  }
  function checkAll(requestId = "") {
    if (proofPending) return proofPending;
    proofPending = (async () => {
      const checks = [], failures = [];
      // Allow boot-time repair leases to settle before measuring cold state;
      // never force-unload an existing area or a player's chunks.
      await new Promise(resolve => system.runTimeout(resolve, 80));
      const order = catalog.WARP_CONTROLS.filter(game => native(game));
      const scheduled = [...order.filter((_, index) => index % 2 === 0), ...order.filter((_, index) => index % 2 === 1)];
      for (const game of scheduled) {
        const check = { id: game.id, request_id: requestId, loaded_before: false, loaded_after: false,
          safe: false, teleported: false, cleaned: true };
        try {
          const route = routeFor(game.id);
          check.location = route.location;
          check.loaded_before = inspectArrival(route.dimension, route.location).loaded;
          await loader.withArea({ dimension: route.dimension, bounds: arrivalBounds(route.location) }, async context => {
            await waitSafe(route, context, inspection => {
              check.loaded_after = inspection.loaded;
              check.safe = inspection.safe;
            });
            let entity = null;
            try {
              entity = route.dimension.spawnEntity("minecraft:armor_stand", { ...route.location, y: route.location.y + 1 });
              check.cleaned = false;
              check.from = copy(entity.location);
              entity.addTag("junket:warp_probe");
              entity.teleport(route.location, { dimension: route.dimension, checkForBlocks: true });
              check.teleported = entity.dimension.id === route.dimension.id && distance(entity.location, route.location) <= .75;
              check.location = copy(entity.location);
              check.teleport_distance = distance(check.from, check.location);
              if (!check.teleported) throw new Error("検査entityの到着位置が不一致です。");
            } finally {
              if (entity) { entity.remove(); check.cleaned = true; }
            }
          });
        } catch (error) {
          check.message = error?.message ?? String(error);
          failures.push({ id: game.id, message: check.message });
        }
        checks.push(check);
        console.log(`[JUNKET WARP] destination progress: ${JSON.stringify(check)}`);
      }
      const report = { schema: 1, request_id: requestId, ok: checks.length === 17 && failures.length === 0,
        checks, failures, player_test: false, test_entity: "minecraft:armor_stand" };
      console.log(`[JUNKET WARP] destination proof: ${JSON.stringify(report)}`);
      return report;
    })().finally(() => { proofPending = null; });
    return proofPending;
  }
  return { native, capture, handleRequest, warp, returnToEntry, returnToEntryAndWait,
    evacuateForRecovery: (player, gameId) => returnToEntryAndWait(player, gameId), checkAll, cancel, pending: () => pending.size };
}
