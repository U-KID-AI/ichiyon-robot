import { WARP_CONFIG } from "./junket_warp_generated.js";

const RETURN_TAG = "junket:return";
const CONTROL_TAG = "junket:warp_control";

const floorLocation = location => ({
  x: Math.floor(location.x),
  y: Math.floor(location.y),
  z: Math.floor(location.z),
});
const key = (dimension, location) => {
  const at = floorLocation(location);
  return `${dimension}:${at.x},${at.y},${at.z}`;
};
const enabled = game => game.readiness === "verified" || game.readiness === "implemented";

export function createWarpSystem({ world, system, manager, destinations = {}, catalog = WARP_CONFIG }) {
  const dimensions = new Map();
  let installing = false;
  const games = new Map(catalog.WARP_CONTROLS.map(game => [game.id, game]));
  const buttons = new Map(catalog.WARP_CONTROLS.map(game =>
    [key(catalog.WARP_DIMENSION, { x: game.button[0], y: game.button[1], z: game.button[2] }), game]));

  function tell(player, message) {
    try { player?.sendMessage(`[JUNKET WARP] ${message}`); } catch { /* Player disconnected. */ }
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
  function warp(player, gameId) {
    const game = games.get(gameId);
    if (!game) {
      tell(player, "ゲームIDが不正です。");
      return false;
    }
    if (!enabled(game)) {
      tell(player, `${game.title}は準備中です。完成確認後に公開します。`);
      return false;
    }
    const route = destination(game, player);
    if (!route) {
      tell(player, `${game.title}の会場がまだ設置されていません。`);
      return false;
    }
    if (route.open) {
      system.run(() => route.open(player));
      return true;
    }
    player.teleport(route.location, {
      dimension: teleportDimension(route.dimension),
      rotation: route.rotation ? { x: 0, y: route.rotation } : undefined,
    });
    if (route.afterTeleport) system.run(() => route.afterTeleport(player));
    return true;
  }
  function returnToEntry(player) {
    if (manager?.forPlayer(player.id)) {
      tell(player, "試合参加中は退出操作を完了してから入口へ戻ってください。");
      return false;
    }
    const point = catalog.WARP_RETURN_POINT;
    player.teleport({ x: point.location[0], y: point.location[1], z: point.location[2] }, {
      dimension: dimension(catalog.WARP_DIMENSION),
      rotation: { x: 0, y: point.rotation },
    });
    return true;
  }
  function targetCells(game) {
    return [game.command_block, game.sign, game.button];
  }
  function installedControlState() {
    const d = dimension(catalog.WARP_DIMENSION);
    let complete = true;
    for (const game of catalog.WARP_CONTROLS) {
      const expected = [
        [game.command_block, "minecraft:command_block"],
        [game.sign, "minecraft:wall_sign"],
        [game.button, "minecraft:wooden_button"],
      ];
      for (const [location, typeId] of expected) {
        const block = d.getBlock({ x: location[0], y: location[1], z: location[2] });
        if (block?.typeId !== typeId) complete = false;
      }
    }
    return { dimension: d, complete };
  }
  function preflight() {
    const anchor = catalog.PROTECTED_ANCHOR;
    if (!anchor) throw new Error("保護対象の拠点行き座標が設定されていません。");
    const d = dimension(catalog.WARP_DIMENSION);
    const expected = [
      [anchor.command_block, "minecraft:command_block"],
      [anchor.sign, "minecraft:wall_sign"],
      [anchor.button, "minecraft:wooden_button"],
    ];
    for (const [location, typeId] of expected) {
      const block = d.getBlock({ x: location[0], y: location[1], z: location[2] });
      if (block === undefined || block.typeId === typeId) continue;
      if (block?.typeId !== typeId) {
        throw new Error(`${anchor.label}の既存設備が想定と異なるため中止しました: (${location.join(",")})`);
      }
    }
    const cells = new Set();
    for (const game of catalog.WARP_CONTROLS) {
      for (const location of targetCells(game)) {
        const expectedType = location === game.command_block
          ? "minecraft:command_block"
          : location === game.sign ? "minecraft:wall_sign" : "minecraft:wooden_button";
        const position = key(catalog.WARP_DIMENSION, { x: location[0], y: location[1], z: location[2] });
        if (cells.has(position)) throw new Error(`JUNKET操作位置が重複しています: ${position}`);
        cells.add(position);
        const block = d.getBlock({ x: location[0], y: location[1], z: location[2] });
        if (block === undefined || block.typeId === expectedType) continue;
        if (!block?.isAir) throw new Error(`増設予定位置が空ではありません: (${location.join(",")})`);
      }
    }
    return d;
  }
  function commandBlockData(game) {
    return JSON.stringify({
      Command: game.command,
      CustomName: `JUNKET ${game.title}`,
      auto: false,
      powered: false,
      conditionMet: true,
    });
  }
  function signData(game) {
    const state = enabled(game) ? "OPEN" : "準備中";
    return JSON.stringify({
      FrontText: { Text: `${game.title}\n${state}`, TextOwner: "JUNKET" },
    });
  }
  function runChecked(blockDimension, command) {
    const result = blockDimension.runCommand(command);
    if ((result?.successCount ?? 0) < 1) throw new Error(`ブロック操作に失敗しました: ${command}`);
  }
  function installControls(player = null) {
    if (installing) {
      tell(player, "ワープ設備の設置処理中です。");
      return false;
    }
    installing = true;
    const placed = [];
    let ticking = false;
    try {
      const state = installedControlState();
      if (state.complete) return true;
      const d = preflight();
      try { d.runCommand("tickingarea remove junket_install"); } catch { /* No prior temporary area. */ }
      runChecked(d, "tickingarea add circle 10053 65 10077 4 junket_install");
      ticking = true;
      for (const game of catalog.WARP_CONTROLS) {
        const [x, y, z] = game.command_block;
        const [sx, sy, sz] = game.sign;
        const [bx, by, bz] = game.button;
        runChecked(d, `setblock ${x} ${y} ${z} minecraft:command_block ["facing_direction"=2,"conditional_bit"=false]`);
        placed.push(game.command_block);
        runChecked(d, `blockdata ${x} ${y} ${z} ${commandBlockData(game)}`);
        runChecked(d, `setblock ${sx} ${sy} ${sz} minecraft:wall_sign ["facing_direction"=2]`);
        placed.push(game.sign);
        runChecked(d, `blockdata ${sx} ${sy} ${sz} ${signData(game)}`);
        runChecked(d, `setblock ${bx} ${by} ${bz} minecraft:wooden_button ["facing_direction"=1,"button_pressed_bit"=false]`);
        placed.push(game.button);
      }
      tell(player, "17ゲーム分のワープ操作設備を増設しました。既存の拠点行き設備は変更していません。");
      return true;
    } catch (error) {
      console.warn(`[JUNKET WARP] ${error.message}`);
      for (const [x, y, z] of placed.reverse()) {
        try { dimension(catalog.WARP_DIMENSION).runCommand(`setblock ${x} ${y} ${z} minecraft:air`); } catch { /* Best-effort rollback. */ }
      }
      tell(player, `設置を中止しました: ${error.message}`);
      return false;
    } finally {
      if (ticking) {
        try { dimension(catalog.WARP_DIMENSION).runCommand("tickingarea remove junket_install"); } catch { /* Best effort cleanup. */ }
      }
      installing = false;
    }
  }

  world.beforeEvents.playerInteractWithBlock.subscribe(event => {
    const game = buttons.get(key(event.block.dimension.id, event.block.location));
    if (!game) return;
    event.cancel = true;
    system.run(() => warp(event.player, game.id));
  });
  world.beforeEvents.playerInteractWithEntity.subscribe(event => {
    if (!event.target.hasTag(RETURN_TAG)) return;
    event.cancel = true;
    system.run(() => returnToEntry(event.player));
  });
  system.afterEvents.scriptEventReceive.subscribe(event => {
    const player = event.sourceEntity;
    if (player?.typeId !== "minecraft:player") return;
    if (event.id === "junket:warpadmin") {
      if (event.message.trim() === "install") installControls(player);
      else tell(player, "/scriptevent junket:warpadmin install");
      return;
    }
    if (event.id === "junket:warp") {
      warp(player, event.message.trim());
      return;
    }
    if (event.id === "junket:warpreturn") returnToEntry(player);
  });
  return { warp, returnToEntry, installControls, destination, controls: catalog.WARP_CONTROLS, tag: CONTROL_TAG };
}
