import { insideFixtureScope } from "./fixture_scope.js";

const PREFIX = "junket:recovery_v1:";
const DIMENSION = "minecraft:overworld";

export function createVenueRecovery({ world, system, manager, venues, runtimes,
  confirm, evacuate, restoreServices = async () => {}, consentTicks = 600 }) {
  const pending = new Map(), running = new Map();
  const definitions = new Map(venues.entries.map(value => [value.id, value]));
  let sequence = 0, queue = Promise.resolve();
  const tick = () => typeof system.currentTick === "number" ? system.currentTick : Math.floor(Date.now() / 50);
  const online = id => world.getAllPlayers().find(player => player.id === id);
  const tables = id => [...manager.tables.values()].filter(table => table.game.id === id).map(table => manager.view(table.id));
  const tell = (player, message) => { try { player?.sendMessage(`[JUNKET] ${message}`); } catch {} };
  world.afterEvents?.playerLeave?.subscribe(event => {
    for (const [id, request] of pending) if (request.participants.some(member => member.id === event.playerId)) pending.delete(id);
  });
  function definition(id) {
    const value = definitions.get(id);
    if (!value) throw new Error(`会場IDが不正です: ${id}`);
    return value;
  }
  function isOperator(player) {
    const actual = player?.id ? online(player.id) : null;
    if (!actual || actual.typeId !== "minecraft:player") return false;
    try {
      return actual.playerPermissionLevel === 2 ||
        (Number.isInteger(actual.commandPermissionLevel) && actual.commandPermissionLevel >= 1 && actual.commandPermissionLevel <= 4) ||
        (typeof actual.isOp === "function" && actual.isOp() === true);
    } catch { return false; }
  }
  function authorized(event) {
    if (event?.sourceBlock) return false;
    if (event?.sourceEntity) return isOperator(event.sourceEntity);
    return event?.sourceType === "Server" && !event.initiator;
  }
  function write(record, phase, extra = {}) {
    Object.assign(record, extra, { phase });
    world.setDynamicProperty(`${PREFIX}${record.gameId}`, JSON.stringify(record));
    console.log(`[JUNKET RECOVERY] operation: ${JSON.stringify(record)}`);
  }
  function affected(id, participants, force) {
    const venue = definition(id);
    return world.getAllPlayers().filter(player => participants.includes(player.id) ||
      (force && player.dimension?.id === DIMENSION && insideFixtureScope(venue, player, 0)));
  }
  async function perform(record) {
    const id = record.gameId;
    definition(id);
    if (!manager.recoveringGames.has(id)) manager.setVenueRecovery(id, true);
    try {
      const current = tables(id);
      const participants = [...new Set([...record.participants, ...current.flatMap(table => table.players)])];
      write(record, "evacuating", { participants });
      if (typeof evacuate !== "function") throw new Error("安全退避機能が利用できません。");
      await evacuate(affected(id, participants, record.fullRepair), {
        gameId: id, operationId: record.id, force: record.fullRepair,
      });
      const guard = () => {
        if (!manager.recoveringGames.has(id)) throw new Error(`${id}: 復旧ロックが失われました`);
        if (record.fullRepair && world.getAllPlayers().some(player => player.dimension?.id === DIMENSION &&
            insideFixtureScope(definition(id), player, 0))) throw new Error(`${id}: 会場内にプレイヤーがいるため建築復旧を停止しました`);
      };
      write(record, "ending");
      // Even an idle end() restores board blocks and auxiliary actors. Player
      // evacuation may leave this venue cold, so its whole ending transition
      // needs a lease. Release it before repair/restore acquires its own lease;
      // nesting those operations would deadlock the shared loader queue.
      await venues.withLoaded(id, async context => {
        context.guard(); guard();
        const runtime = runtimes.get(id);
        if (typeof runtime?.resetSession !== "function") throw new Error(`${id}: 終了処理が未接続です`);
        for (const table of current) {
          context.guard(); guard();
          await runtime.resetSession(table, "全員同意または管理者操作により試合をリセットしました。", { evacuated: true });
          context.guard(); guard();
          // In-game stakes are virtual state only. Cancel the unfinished round;
          // no winner, reward, or external money transfer is fabricated.
          manager.close(table.id);
        }
        // A pending journal may resume after the session has already closed.
        // Keep runtime-local status idle even in that restart case.
        if (!current.length) await runtime.resetSession(
          { id, gameId: id, players: participants, state: null }, "会場復旧を再開しました。", { evacuated: true });
        context.guard(); guard();
      });
      write(record, "restoring");
      guard();
      const result = record.fullRepair
        ? await venues.restoreOriginal(id, { guard, afterRestore: restoreServices })
        : await venues.repair(id);
      write(record, "completed", { result });
      manager.setVenueRecovery(id, false);
      for (const playerId of participants) tell(online(playerId), "リセットが完了しました。受付から改めて参加できます。");
      return { id, operationId: record.id, completed: true, fullRepair: record.fullRepair, result };
    } catch (error) {
      write(record, "failed", { error: error?.message ?? String(error) });
      // Keep this venue locked. The persisted journal can resume safely after
      // restart, and another venue remains available throughout the failure.
      throw error;
    }
  }
  function enqueue(record) {
    if (running.has(record.gameId)) throw new Error("この会場は復旧中です。");
    // Reserve immediately, before waiting behind another venue's loader. A
    // form or ticker cannot change the consented session while it is queued.
    manager.setVenueRecovery(record.gameId, true);
    const work = queue.then(() => perform(record)).finally(() => running.delete(record.gameId));
    running.set(record.gameId, work); queue = work.catch(() => {});
    return work;
  }
  function record(id, fullRepair, requestedBy, participants) {
    return { schema: 1, id: `${Date.now().toString(36)}-${++sequence}`, gameId: id,
      fullRepair, requestedBy, participants, phase: "approved" };
  }
  async function requestReset(player, id) {
    const venue = definition(id);
    if (pending.has(id) || running.has(id) || manager.recoveringGames.has(id)) throw new Error("確認または復旧処理中です。");
    const snapshot = tables(id);
    const participants = [...new Set(snapshot.flatMap(table => table.players))];
    if (!participants.length || !participants.includes(player.id)) throw new Error("参加中のプレイヤーからリセットを依頼してください。");
    if (participants.some(member => !online(member))) throw new Error("参加者全員が接続している時だけリセットできます。");
    if (typeof confirm !== "function") throw new Error("全員確認の画面が利用できません。");
    const request = { id: `${id}-${++sequence}`, gameId: id, title: venue.title,
      participants: participants.map(member => ({ id: member, name: online(member).name })),
      token: snapshot.map(table => `${table.id}:${table.token}`).join("|"), expiresAt: tick() + consentTicks };
    pending.set(id, request);
    const valid = () => {
      if (pending.get(id) !== request || tick() >= request.expiresAt || participants.some(member => !online(member))) return false;
      const now = tables(id);
      return JSON.stringify(now.map(table => [table.id, table.token, table.players])) ===
        JSON.stringify(snapshot.map(table => [table.id, table.token, table.players]));
    };
    let timer;
    try {
      const expiration = new Promise(resolve => { timer = system.runTimeout(() => resolve(false), consentTicks); });
      const votes = Promise.all(participants.map(async member => {
        try { return await confirm(online(member), request) === true; } catch { return false; }
      })).then(values => values.every(Boolean));
      if (!await Promise.race([expiration, votes]) || !valid()) throw new Error("全員の同意が揃わないか、対戦状態が変わったため中止しました。");
      const operation = record(id, false, player.id, participants);
      write(operation, "approved");
      return await enqueue(operation);
    } finally {
      if (timer !== undefined) system.clearRun?.(timer);
      if (pending.get(id) === request) pending.delete(id);
    }
  }
  function recover(id, { force = false, player = null, event = null } = {}) {
    if (!force || !(event ? authorized(event) : isOperator(player))) throw new Error("管理者権限を確認できません。");
    const ids = id === "all" ? [...definitions.keys()] : [definition(id).id];
    return (async () => {
      const results = [];
      for (const gameId of ids) {
        if (running.has(gameId)) throw new Error("この会場は復旧中です。");
        pending.delete(gameId);
        const operation = record(gameId, true, player?.id ?? event?.sourceEntity?.id ?? "server", tables(gameId).flatMap(table => table.players));
        write(operation, "approved");
        results.push(await enqueue(operation));
      }
      return { completed: results.length, results };
    })();
  }
  async function resumePending() {
    const results = [];
    for (const id of definitions.keys()) {
      const raw = world.getDynamicProperty(`${PREFIX}${id}`);
      if (!raw) continue;
      const saved = JSON.parse(raw);
      if (saved.schema !== 1 || saved.gameId !== id || !Array.isArray(saved.participants) ||
          typeof saved.fullRepair !== "boolean") throw new Error(`${id}: 復旧記録が不正です`);
      if (saved.phase === "completed") continue;
      results.push(await enqueue(saved));
    }
    return results;
  }
  function handleScriptEvent(event) {
    if (event.id !== "junket:recover") return false;
    const id = event.message.trim();
    void recover(id, { force: true, event }).catch(error => {
      tell(event.sourceEntity, error?.message ?? String(error));
      console.error(`[JUNKET RECOVERY] rejected: ${error?.message ?? error}`);
    });
    return true;
  }
  return { requestReset, recover, isOperator, handleScriptEvent, resumePending,
    forceRecover: (id, event) => recover(id, { force: true, event }),
    busy: id => pending.has(id) || running.has(id) || manager.recoveringGames.has(id) };
}
