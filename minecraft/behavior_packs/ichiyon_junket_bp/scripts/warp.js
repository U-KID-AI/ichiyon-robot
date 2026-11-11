import { WARP_CONFIG } from "./junket_warp_generated.js";
import { createSafeWarpController } from "./safe_warp.js";
import { getSharedLoader } from "./shared_loader.js";
import { registerCatalogReturn } from "./venue_presentation.js";
import { isServiceGuide } from "./service_controls.js";

const RETURN_TAG = "junket:return";
const CONTROL_TAG = "junket:warp_control";
const TICKING_NAME = "junket_install";
const WARP_REQUEST_TIMEOUT = 30;
const SIGN_COMPONENT_ATTEMPTS = 100;

const location = value => ({ x: value[0], y: value[1], z: value[2] });
const floorLocation = value => ({
  x: Math.floor(value.x),
  y: Math.floor(value.y),
  z: Math.floor(value.z),
});
const key = (dimensionId, value) => {
  const at = floorLocation(value);
  return `${dimensionId}:${at.x},${at.y},${at.z}`;
};
const enabled = game => game.enabled === true;
const requestTag = gameId => `junket:warp_${gameId}`;

function boundsForPoints(points) {
  if (!points.length) throw new Error("ワープ設備の座標がありません。");
  const coordinates = points.map(point => [point[0], point[1], point[2]]);
  return [
    Math.min(...coordinates.map(point => point[0])),
    Math.min(...coordinates.map(point => point[1])),
    Math.min(...coordinates.map(point => point[2])),
    Math.max(...coordinates.map(point => point[0])),
    Math.max(...coordinates.map(point => point[1])),
    Math.max(...coordinates.map(point => point[2])),
  ];
}

export function createWarpSystem({ world, system, manager, destinations = {}, catalog = WARP_CONFIG }) {
  const sharedLoader = getSharedLoader(world, system);
  const safe = createSafeWarpController({ world, system, manager, destinations, catalog });
  const dimensions = new Map();
  const activeWarps = new Map();
  let installing = false;
  let legacyNavigator = null;
  const games = new Map(catalog.WARP_CONTROLS.map(game => [game.id, game]));
  const buttons = new Map(catalog.WARP_CONTROLS.map(game =>
    [key(catalog.WARP_DIMENSION, location(game.button)), game]));

  function tell(player, message) {
    try { player?.sendMessage(`[JUNKET WARP] ${message}`); } catch { /* Player disconnected. */ }
  }
  function taggedPlayers(gameId) {
    const tag = requestTag(gameId);
    return (world.getAllPlayers?.() ?? []).filter(player => {
      try { return player.hasTag(tag); } catch { return false; }
    });
  }
  function releaseRequest(player, gameId) {
    try {
      player.removeTag(requestTag(gameId));
      return true;
    } catch {
      return false;
    }
  }
  function claimPress(player, gameId) {
    // A command block can only select by tag, so leave exactly one tagged
    // player per game. If another press is still pending, honour it now
    // instead of letting the new press erase that player's request.
    const pendingPresses = taggedPlayers(gameId)
      .filter(pending => pending.id !== player.id);
    for (const pending of pendingPresses) {
      // Clear every earlier tag synchronously so the command block can never
      // observe two candidates. The earlier press still gets its transfer in
      // a deferred callback outside the read-only interaction event.
      if (!releaseRequest(pending, gameId)) continue;
      system.run(() => void warp(pending, gameId));
    }
    player.addTag(requestTag(gameId));
  }
  function handleWarpRequest(event) {
    if (safe.handleRequest(event)) return;
    const message = event.message.trim();
    const separator = message.indexOf(" ");
    const gameId = separator < 0 ? message : message.slice(0, separator);
    const source = [event.initiator, event.sourceEntity]
      .find(candidate => candidate?.typeId === "minecraft:player");
    if (source) {
      let tagged = false;
      try { tagged = source.hasTag(requestTag(gameId)); } catch { tagged = false; }
      if (!tagged) {
        tell(source, "通路のボタンを押してから移動してください。");
        return;
      }
      if (releaseRequest(source, gameId)) void warp(source, gameId);
      return;
    }
    // Bedrock command-block script events have no source entity, and selector
    // tokens in the freeform message are not a reliable identity channel.
    // Require one unambiguous presser; concurrent presses are serialized by
    // claimPress and retain their interaction timeout fallback.
    const pending = taggedPlayers(gameId);
    if (pending.length !== 1) return;
    const [target] = pending;
    if (releaseRequest(target, gameId)) void warp(target, gameId);
  }
  function dimension(id) {
    let value = dimensions.get(id);
    if (!value) {
      value = world.getDimension(id);
      dimensions.set(id, value);
    }
    return value;
  }
  function destination(game, player) {
    if (!enabled(game)) return null;
    const route = destinations[game.id]?.(player);
    if (!route || (!route.open && (!route.location || !route.dimension))) return null;
    return route;
  }
  function teleportDimension(value) {
    return typeof value === "string" ? dimension(value) : value;
  }
  async function warp(player, gameId) {
    if (!player?.id) return false;
    const game = games.get(gameId);
    if (safe.native(game)) return safe.warp(player, gameId);
    if (!game) {
      tell(player, "ゲームIDが不正です。");
      return false;
    }
    if (!enabled(game)) {
      tell(player, `${game.title}は準備中です。完成確認後に公開します。`);
      return false;
    }
    if (activeWarps.has(player.id)) {
      tell(player, "移動処理中です。完了後に操作してください。");
      return false;
    }
    activeWarps.set(player.id, gameId);
    let keepGuard = false;
    let route = destination(game, player);
    if (!route) {
      activeWarps.delete(player.id);
      tell(player, `${game.title}の会場がまだ設置されていません。`);
      return false;
    }
    try {
      if (route.open) {
        await route.open(player);
        return true;
      }
      if (route.beforeTeleport) await route.beforeTeleport(player);
      const refreshed = destination(game, player);
      if (refreshed && !refreshed.open) route = refreshed;
      player.teleport(route.location, {
        dimension: teleportDimension(route.dimension),
        rotation: route.rotation ? { x: 0, y: route.rotation } : undefined,
      });
      keepGuard = true;
      system.runTimeout(() => activeWarps.delete(player.id), 20);
      if (route.afterTeleport) await route.afterTeleport(player);
      return true;
    } catch (error) {
      tell(player, `${game.title}へ移動できませんでした: ${error?.message ?? error}`);
      return false;
    } finally {
      if (!keepGuard) activeWarps.delete(player.id);
    }
  }
  function returnToEntry(player) {
    if (catalog.WARP_OFFLINE_INSTALLED === true && catalog.WARP_CONTROLS.some(game => safe.native(game))) {
      return safe.returnToEntry(player);
    }
    if (manager?.forPlayer(player.id)) {
      tell(player, "試合参加中は退出操作を完了してからカタログへ戻ってください。");
      return false;
    }
    const point = catalog.WARP_RETURN_POINT;
    player.teleport({ x: point.location[0], y: point.location[1], z: point.location[2] }, {
      dimension: dimension(catalog.WARP_DIMENSION),
      rotation: { x: 0, y: point.rotation },
    });
    return true;
  }
  function blockAt(blockDimension, value) {
    try { return blockDimension.getBlock(location(value)); } catch { return undefined; }
  }
  function stateMatches(block, expected = {}, allowPressedButton = false) {
    const permutation = block?.permutation;
    if (!permutation || typeof permutation.getState !== "function") return !allowPressedButton;
    return Object.entries(expected).every(([name, value]) => {
      try {
        const actual = permutation.getState(name);
        // A button may be held while preflight runs. Its transient press state
        // is legal in either position; type, orientation and other states stay exact.
        if (allowPressedButton && name === "button_pressed_bit") {
          return actual === 0 || actual === 1 || actual === false || actual === true;
        }
        return actual === value;
      } catch { return false; }
    });
  }
  function signComponent(block) {
    try {
      return block?.getComponent("minecraft:sign");
    } catch {
      return undefined;
    }
  }
  function signText(block) {
    const sign = signComponent(block);
    if (!sign || typeof sign.getText !== "function") return undefined;
    try {
      const text = sign.getText();
      return typeof text === "string" ? text : undefined;
    } catch {
      return undefined;
    }
  }
  function verifyAnchor(d) {
    const anchor = catalog.PROTECTED_ANCHOR;
    if (!anchor) throw new Error("保護対象の拠点行き座標が設定されていません。");
    const expected = [
      [anchor.command_block, anchor.command_block_type, anchor.command_block_states],
      [anchor.sign, anchor.sign_type, anchor.sign_states],
      [anchor.button, anchor.button_type, anchor.button_states],
    ];
    for (const [at, typeId, states] of expected) {
      const block = blockAt(d, at);
      if (!block) throw new Error(`拠点行き設備のチャンクを読み込めません: (${at.join(",")})`);
      if (block.typeId !== typeId || !stateMatches(block, states)) {
        throw new Error(`${anchor.label}の既存設備が想定と異なるため中止しました: (${at.join(",")})`);
      }
    }
    if (anchor.require_nonblank_sign) {
      const text = signText(blockAt(d, anchor.sign));
      if (typeof text !== "string") {
        throw new Error("拠点行き看板の表示を読み取れないため、既存設備を保護して中止しました。");
      }
      if (!text.trim()) throw new Error("拠点行き看板が空のため、既存設備を保護して中止しました。");
    }
  }
  function observedAnchor(d) {
    const anchor = catalog.PROTECTED_ANCHOR;
    return JSON.stringify([anchor.command_block, anchor.sign, anchor.button].map(at => {
      const block = blockAt(d, at);
      if (!block) throw new Error(`拠点行き記録位置のチャンクを読み込めません: (${at.join(",")})`);
      return { at, typeId: block.typeId, states: block.permutation?.getAllStates?.(), text: signText(block) };
    }));
  }
  function inBounds(locationValue, bounds) {
    return locationValue.x >= bounds[0] && locationValue.x <= bounds[3] &&
      locationValue.y >= bounds[1] && locationValue.y <= bounds[4] &&
      locationValue.z >= bounds[2] && locationValue.z <= bounds[5];
  }
  function cleanupMisplacedRow(d) {
    const cleanup = catalog.MISPLACED_ROW_CLEANUP;
    if (!cleanup) return { blocks: 0, entities: 0 };
    const controlCells = new Set([
      ...catalog.WARP_CONTROLS.flatMap(game => [game.command_block, game.sign, game.button]),
      catalog.PROTECTED_ANCHOR.command_block,
      catalog.PROTECTED_ANCHOR.sign,
      catalog.PROTECTED_ANCHOR.button,
    ].map(value => key(catalog.WARP_DIMENSION, location(value))));
    const restore = [];
    for (const cell of cleanup.cells) {
      const at = key(catalog.WARP_DIMENSION, location(cell.location));
      if (controlCells.has(at)) throw new Error(`誤設置撤去対象が現行設備と重複しています: ${at}`);
      const block = blockAt(d, cell.location);
      if (!block) throw new Error(`誤設置撤去対象のチャンク未読込: ${at}`);
      if (block.typeId === cell.restore_type) continue;
      if (block.typeId !== cell.current_type) {
        throw new Error(`誤設置撤去対象に別ブロックがあるため中止しました: ${at} ${block.typeId}`);
      }
      restore.push({ cell, at });
    }
    let removedEntities = 0;
    const bounds = cleanup.entity_bounds ?? cleanup.scan_bounds;
    const center = {
      x: (bounds[0] + bounds[3]) / 2,
      y: (bounds[1] + bounds[4]) / 2,
      z: (bounds[2] + bounds[5]) / 2,
    };
    const maxDistance = Math.ceil(Math.hypot(
      bounds[3] - bounds[0], bounds[4] - bounds[1], bounds[5] - bounds[2],
    ) / 2) + 4;
    const removed = new Set();
    if (typeof d.getEntities === "function") {
      for (const tag of cleanup.entity_tags ?? []) {
        const entities = d.getEntities({ tags: [tag], location: center, maxDistance });
        for (const entity of entities) {
          if (entity.typeId === "minecraft:player") continue;
          if (typeof entity.hasTag !== "function" || !entity.hasTag(tag)) continue;
          if (!inBounds(entity.location, bounds)) continue;
          if (removed.has(entity)) continue;
          entity.remove();
          removed.add(entity);
          removedEntities++;
        }
      }
    }
    for (const { cell, at } of restore) {
      const [x, y, z] = cell.location;
      runChecked(d, `setblock ${x} ${y} ${z} ${cell.restore_type}`);
      const restored = blockAt(d, cell.location);
      if (restored?.typeId !== cell.restore_type) {
        throw new Error(`誤設置ブロックを復元できませんでした: ${at}`);
      }
    }
    verifyAnchor(d);
    return { blocks: restore.length, entities: removedEntities };
  }
  function expectedCells() {
    return catalog.WARP_CONTROLS.flatMap(game => [
      { game, role: "command_block", location: game.command_block,
        typeId: game.command_block_type, states: game.command_block_states },
      { game, role: "sign", location: game.sign, typeId: game.sign_type, states: game.sign_states },
      { game, role: "button", location: game.button, typeId: game.button_type, states: game.button_states },
    ]);
  }
  function verifyCells(d) {
    const cells = expectedCells();
    const seen = new Set();
    for (const cell of cells) {
      const at = key(catalog.WARP_DIMENSION, location(cell.location));
      if (seen.has(at)) throw new Error(`JUNKET操作位置が重複しています: ${at}`);
      seen.add(at);
      const block = blockAt(d, cell.location);
      if (!block) return { ready: false, reason: `チャンク未読込: ${at}` };
      if (block.typeId !== cell.typeId || !stateMatches(block, cell.states, cell.role === "button")) {
        return { ready: false, reason: `既存設備の種類または状態が不一致: ${at}` };
      }
    }
    if (seen.size !== 51) throw new Error(`JUNKET既存枠が51セルではありません: ${seen.size}`);
    return { ready: true, reason: "" };
  }
  function commandBlockData(game) {
    // Bedrock consumes SNBT here. JSON.stringify would quote the keys and can
    // be rejected by the block-data parser, leaving a commandless block.
    return `{Command:${JSON.stringify(String(game.command))},` +
      `CustomName:${JSON.stringify(`JUNKET ${game.title}`)},` +
      "auto:0b,powered:0b,conditionMet:1b}";
  }
  function runChecked(d, command) {
    const result = d.runCommand(command);
    if ((result?.successCount ?? 0) < 1) throw new Error(`ブロック操作に失敗しました: ${command}`);
  }
  function waitTicks(ticks) {
    return new Promise(resolve => system.runTimeout(resolve, ticks));
  }
  async function waitForAllCells(d, attempts = 60) {
    for (let remaining = attempts; remaining > 0; remaining--) {
      const state = verifyCells(d);
      if (state.ready) return;
      if (!state.reason.startsWith("チャンク未読込")) throw new Error(state.reason);
      await waitTicks(20);
    }
    throw new Error("JUNKET control chunks did not become ready");
  }
  async function waitForCleanupCells(d, attempts = 60) {
    const cells = catalog.MISPLACED_ROW_CLEANUP?.cells ?? [];
    for (let remaining = attempts; remaining > 0; remaining--) {
      const missing = cells.find(cell => !blockAt(d, cell.location));
      if (!missing) return;
      await waitTicks(20);
    }
    throw new Error("JUNKET misplaced-row cleanup chunks did not become ready");
  }
  async function waitForAnchor(d) {
    let lastError;
    for (let remaining = SIGN_COMPONENT_ATTEMPTS; remaining > 0; remaining--) {
      try {
        verifyAnchor(d);
        return;
      } catch (error) {
        lastError = error;
        const message = String(error?.message);
        if (!message.includes("表示を読み取れない") && !message.includes("チャンクを読み込めません")) {
          throw error;
        }
        await waitTicks(2);
      }
    }
    throw lastError;
  }
  async function waitForSignComponents(d) {
    let missing = [];
    for (let remaining = SIGN_COMPONENT_ATTEMPTS; remaining > 0; remaining--) {
      missing = [];
      for (const game of catalog.WARP_CONTROLS) {
        const block = blockAt(d, game.sign);
        if (!block) {
          missing.push(`${game.id}: チャンク未読込`);
          continue;
        }
        if (block.typeId !== game.sign_type) {
          throw new Error(`看板の種類が不一致: ${game.id}`);
        }
        const sign = signComponent(block);
        if (!sign || typeof sign.setText !== "function") missing.push(`${game.id}: 看板コンポーネント未読込`);
      }
      if (!missing.length) return;
      await waitTicks(2);
    }
    throw new Error(`看板コンポーネントを読み込めません: ${missing.join(", ")}`);
  }
  function verifyAssignableSigns(d) {
    let blank = 0;
    let configured = 0;
    for (const game of catalog.WARP_CONTROLS) {
      const block = blockAt(d, game.sign);
      if (!block) throw new Error(`JUNKET看板のチャンクを読み込めません: ${game.id}`);
      if (block.typeId !== game.sign_type) {
        throw new Error(`JUNKET看板の種類が不一致: ${game.id}`);
      }
      const text = signText(block);
      if (typeof text !== "string") {
        throw new Error(`JUNKET看板の表示を読み取れません: ${game.id}`);
      }
      const trimmed = text.trim();
      if (!trimmed) {
        blank++;
        continue;
      }
      if (trimmed.includes(game.title)) {
        configured++;
        continue;
      }
      throw new Error(
        `既存看板が別用途で使用中のため中止しました: ${game.id} (${trimmed.slice(0, 40)})`);
    }
    return { blank, configured };
  }
  async function waitForSignTexts(d) {
    let mismatched = [];
    for (let remaining = SIGN_COMPONENT_ATTEMPTS; remaining > 0; remaining--) {
      mismatched = [];
      for (const game of catalog.WARP_CONTROLS) {
        const block = blockAt(d, game.sign);
        if (!block) {
          mismatched.push(`${game.id}: チャンク未読込`);
          continue;
        }
        if (block.typeId !== game.sign_type) {
          throw new Error(`看板の種類が不一致: ${game.id}`);
        }
        const text = signText(block);
        if (typeof text !== "string" || !text.includes(game.title)) {
          mismatched.push(`${game.id}: ${typeof text === "string" ? "表示不一致" : "表示未読込"}`);
        }
      }
      if (!mismatched.length) return;
      await waitTicks(2);
    }
    throw new Error(`看板の表示を確認できません: ${mismatched.join(", ")}`);
  }
  async function installControls(player = null) {
    if (installing) {
      tell(player, "ワープ設備の設定処理中です。");
      return false;
    }
    installing = true;
    let ticking = false;
    const d = dimension(catalog.WARP_DIMENSION);
    try {
      if (catalog.WARP_OFFLINE_INSTALLED === true) {
        // Native impulse commands were written and verified in a stopped-world
        // LevelDB transaction. Bedrock has no supported /blockdata writer.
        // Here only inspect the existing row; do not repair terrain or touch
        // the unrelated historical row and protected return controls.
        const bounds = boundsForPoints([
          ...expectedCells().map(cell => cell.location),
          catalog.PROTECTED_ANCHOR.command_block,
          catalog.PROTECTED_ANCHOR.sign,
          catalog.PROTECTED_ANCHOR.button,
        ]);
        return await sharedLoader.withArea({ dimension: d, bounds }, async () => {
          await waitForAllCells(d);
          const preserveObserved = catalog.WARP_PRESERVE_OBSERVED_ANCHOR === true;
          const anchorBefore = preserveObserved ? observedAnchor(d) : null;
          if (!preserveObserved) await waitForAnchor(d);
          await waitForSignTexts(d);
          if (preserveObserved && anchorBefore !== observedAnchor(d)) {
            throw new Error("拠点行き記録位置の状態が検証中に変化しました。");
          }
          tell(player, "本番NBTで設定した17枠の種類・ボタン配置・正式看板名を確認しました。コマンドの永続化はoffline検証記録を参照してください。");
          return true;
        });
      }
      const tickingBounds = boundsForPoints([
        ...expectedCells().map(cell => cell.location),
        ...(catalog.MISPLACED_ROW_CLEANUP?.cells ?? []).map(cell => cell.location),
        catalog.MISPLACED_ROW_CLEANUP.entity_bounds.slice(0, 3),
        catalog.MISPLACED_ROW_CLEANUP.entity_bounds.slice(3, 6),
        catalog.PROTECTED_ANCHOR.command_block,
        catalog.PROTECTED_ANCHOR.sign,
        catalog.PROTECTED_ANCHOR.button,
      ]);
      try { d.runCommand(`tickingarea remove ${TICKING_NAME}`); } catch { /* No prior temporary area. */ }
      runChecked(d, `tickingarea add ${tickingBounds.join(" ")} ${TICKING_NAME}`);
      ticking = true;
      await waitTicks(20);
      verifyCells(d);
      await waitForAnchor(d);
      await waitForAllCells(d);
      await waitForCleanupCells(d);
      await waitForSignComponents(d);
      const signState = verifyAssignableSigns(d);
      verifyAnchor(d);
      const cleaned = cleanupMisplacedRow(d);

      for (const game of catalog.WARP_CONTROLS) {
        const [x, y, z] = game.command_block;
        runChecked(d, `blockdata ${x} ${y} ${z} ${commandBlockData(game)}`);
      }
      await waitForSignComponents(d);
      for (const game of catalog.WARP_CONTROLS) {
        const sign = signComponent(blockAt(d, game.sign));
        if (!sign || typeof sign.setText !== "function") {
          throw new Error(`看板コンポーネントを読み込めません: ${game.id}`);
        }
        try {
          sign.setText(game.title);
        } catch (error) {
          // /blockdata remains the persistence source; report only after verification.
          console.warn(`[JUNKET WARP] 看板API設定を保留: ${game.id} ${error?.message ?? error}`);
        }
      }
      await waitTicks(2);
      const state = verifyCells(d);
      if (!state.ready) throw new Error(state.reason);
      await waitForSignTexts(d);
      verifyAnchor(d);
      tell(player, `既存通路の17ゲーム用コマンドブロックと看板を設定・検証しました。空欄看板${signState.blank}件 / 設定済み看板${signState.configured}件。誤設置撤去: ブロック${cleaned.blocks}件 / エンティティ${cleaned.entities}件。`);
      return true;
    } catch (error) {
      console.warn(`[JUNKET WARP] ${error.message}`);
      tell(player, `設定を中止しました。既存ブロックは削除していません: ${error.message}`);
      return false;
    } finally {
      if (ticking) {
        try { d.runCommand(`tickingarea remove ${TICKING_NAME}`); } catch { /* Best-effort cleanup. */ }
      }
      installing = false;
    }
  }

  world.beforeEvents.playerInteractWithBlock.subscribe(event => {
    const game = buttons.get(key(event.block.dimension.id, event.block.location));
    if (!game) return;
    if (legacyNavigator) {
      event.cancel = true;
      const id = event.player.id;
      system.run(() => { const p = world.getAllPlayers().find(p => p.id === id); if (p) legacyNavigator(p, game); });
      return;
    }
    if (safe.native(game)) { safe.capture(event, game); return; }
    // Offline-installed native commands directly teleport the nearby presser.
    // Let the physical button/redstone run and avoid a second scripted warp.
    if (catalog.WARP_OFFLINE_INSTALLED === true && game.command_mode === "native_tp") return;
    if (!enabled(game)) {
      event.cancel = true;
      tell(event.player, `${game.title}は準備中です。完成確認後に公開します。`);
      return;
    }
    const tag = requestTag(game.id);
    const presserId = event.player.id;
    system.run(() => { try {
      const presser = (world.getAllPlayers?.() ?? []).find(value => value.id === presserId);
      if (!presser) return;
      claimPress(presser, game.id);
      // The command block executes as a block, so ScriptEventReceive has no
      // sourceEntity/initiator. The temporary tag carries the exact presser to
      // the command block. This fallback keeps the button usable if redstone or
      // the command block fails, without ever selecting a nearby bystander.
      system.runTimeout(() => {
        try {
          if (!presser.hasTag(tag)) return;
          presser.removeTag(tag);
          void warp(presser, game.id);
        } catch { /* Player disconnected or tag already consumed. */ }
      }, WARP_REQUEST_TIMEOUT);
    } catch (error) {
      tell((world.getAllPlayers?.() ?? []).find(value => value.id === presserId), `ワープ要求を作成できませんでした: ${error?.message ?? error}`);
    } });
  });
  world.afterEvents.playerSpawn.subscribe(event => {
    if (!event.initialSpawn) return;
    safe.cancel(event.player.id);
    // Tags survive a crash. Clear a request that never reached its command
    // block so a later button press cannot move the reconnecting player.
    for (const gameId of games.keys()) {
      try { event.player.removeTag(requestTag(gameId)); } catch { /* Player disconnected. */ }
    }
  });
  world.afterEvents.playerLeave?.subscribe(event => safe.cancel(event.playerId));
  world.beforeEvents.playerInteractWithEntity.subscribe(event => {
    if (isServiceGuide(world, event.target)) return;
    if (!event.target.hasTag(RETURN_TAG)) return;
    event.cancel = true;
    system.run(() => returnToEntry(event.player));
  });
  system.afterEvents.scriptEventReceive.subscribe(event => {
    const player = event.sourceEntity;
    if (event.id === "junket:warpcheck" && !player) {
      const [command, requestId = ""] = event.message.trim().split(/\s+/);
      if (command === "all") void safe.checkAll(requestId);
      return;
    }
    if (event.id === "junket:warp") {
      handleWarpRequest(event);
      return;
    }
    if (player?.typeId !== "minecraft:player") return;
    if (event.id === "junket:warpadmin") {
      const command = event.message.trim();
      if (command === "install") void installControls(player);
      else if (command === "cleanup") {
        try {
          const d = dimension(catalog.WARP_DIMENSION);
          verifyAnchor(d);
          const cleaned = cleanupMisplacedRow(d);
          tell(player, `誤設置撤去を検証しました: ブロック${cleaned.blocks}件 / エンティティ${cleaned.entities}件。`);
        } catch (error) {
          tell(player, `誤設置撤去を中止しました: ${error?.message ?? error}`);
        }
      } else tell(player, "/scriptevent junket:warpadmin install | cleanup");
      return;
    }
    if (event.id === "junket:warpreturn") returnToEntry(player);
  });
  registerCatalogReturn(world, player => safe.returnToEntryAndWait(player));
  return {
    setLegacyNavigator: navigate => { legacyNavigator = navigate; },
    warp, returnToEntry, returnToEntryAndWait: player => safe.returnToEntryAndWait(player),
    evacuateForRecovery: (player, gameId) => safe.evacuateForRecovery(player, gameId), installControls, destination, checkDestinations: safe.checkAll,
    controls: catalog.WARP_CONTROLS, tag: CONTROL_TAG,
  };
}
