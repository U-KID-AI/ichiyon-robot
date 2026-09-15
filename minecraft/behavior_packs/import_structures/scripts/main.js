import { BlockPermutation, EntityComponentTypes, ItemStack, system, world } from "@minecraft/server";
import { HttpHeader, HttpRequest, HttpRequestMethod, http } from "@minecraft/server-net";
import { secrets, variables } from "@minecraft/server-admin";

console.warn("[NaritaBridge] main.js loaded");

const DEFAULT_API_BASE = "http://10.0.0.94:8000/internal/minecraft";
const STRUCTURE_ID = "mystructure:narita_map_item";
const E_SCHRIFT_STRUCTURE_ID = "mystructure:e_schrift_item";
const TAKETUMI_ENTITY_ID = "ichiyon:taketumi";
const TAKETUMI_NAME_TAG = "タケツミ";
const PLAYER_NAME_PATTERN = /^[A-Za-z0-9_]{1,16}$/;
const POLL_INTERVAL_TICKS = 40;
const SCRIPT_STARTED_AT_MS = Date.now();
const AIR_BLOCK_TYPE = "minecraft:air";
const CARDINAL_DIRECTION_STATE = "minecraft:cardinal_direction";
const LARGE_POSTER_COLUMNS = 4;
const LARGE_POSTER_ROWS = 5;
const LARGE_POSTERS = [
  { baseId: "ichiyon:poster_raio", segmentPrefix: "ichiyon:poster_raio", label: "ライオポスター" },
  { baseId: "ichiyon:poster_trent", segmentPrefix: "ichiyon:poster_trent", label: "トレントポスター" },
  { baseId: "ichiyon:poster_aurelia", segmentPrefix: "ichiyon:poster_aurelia", label: "オーレリアポスター" },
  { baseId: "ichiyon:poster_killzael", segmentPrefix: "ichiyon:poster_killzael", label: "キルザエルポスター" },
  { baseId: "ichiyon:poster_caravan_mammoth", segmentPrefix: "ichiyon:poster_caravan_mammoth", label: "キャラバンマンモスポスター" },
  { baseId: "ichiyon:poster_itsutake", segmentPrefix: "ichiyon:poster_itsutake", label: "イツタケポスター" },
];
const LARGE_POSTER_BY_BASE_ID = Object.fromEntries(
  LARGE_POSTERS.map((poster) => [poster.baseId, poster])
);
const LARGE_POSTER_SEGMENTS = [];
for (const poster of LARGE_POSTERS) {
  for (let row = 0; row < LARGE_POSTER_ROWS; row += 1) {
    for (let column = 0; column < LARGE_POSTER_COLUMNS; column += 1) {
      LARGE_POSTER_SEGMENTS.push({
        id: `${poster.segmentPrefix}_r${row}c${column}`,
        poster,
        row,
        column,
      });
    }
  }
}
const LARGE_POSTER_SEGMENT_BY_ID = Object.fromEntries(
  LARGE_POSTER_SEGMENTS.map((segment) => [segment.id, segment])
);
const ITEM_TYPES_BY_COMMAND = {
  structure_block: "minecraft:structure_block",
  command_block: "minecraft:command_block",
  barrier_block: "minecraft:barrier",
  light_block: "minecraft:light_block_15",
  jigsaw_block: "minecraft:jigsaw",
  structure_void: "minecraft:structure_void",
  repeating_command_block: "minecraft:repeating_command_block",
  chain_command_block: "minecraft:chain_command_block",
  taketumi_spawn_egg: "ichiyon:taketumi_spawn_egg",
  poster_irsia: "ichiyon:poster_irsia",
  poster_raio: "ichiyon:poster_raio",
  poster_trent: "ichiyon:poster_trent",
  poster_aurelia: "ichiyon:poster_aurelia",
  poster_killzael: "ichiyon:poster_killzael",
  poster_caravan_mammoth: "ichiyon:poster_caravan_mammoth",
  poster_itsutake: "ichiyon:poster_itsutake",
};

function configValue(name, fallback) {
  try {
    const value = variables.get(name);
    if (value === undefined || value === null || String(value).trim() === "") {
      return fallback;
    }
    return String(value).trim();
  } catch (error) {
    return fallback;
  }
}

function secretHeader() {
  return new HttpHeader("X-Minecraft-Bridge-Secret", secrets.get("MINECRAFT_BRIDGE_SECRET"));
}

function jsonHeaders() {
  return [new HttpHeader("Content-Type", "application/json"), secretHeader()];
}

function apiBase() {
  return configValue("MINECRAFT_BRIDGE_API_BASE", DEFAULT_API_BASE).replace(/\/+$/, "");
}

function botId() {
  return configValue("MINECRAFT_BRIDGE_BOT_ID", "ichiyon");
}

function guildId() {
  return configValue("MINECRAFT_BRIDGE_GUILD_ID", "");
}

function resultUrl(requestId) {
  return `${apiBase()}/commands/${encodeURIComponent(requestId)}/result`;
}

function isValidPlayerName(name) {
  return PLAYER_NAME_PATTERN.test(String(name || ""));
}

async function postResult(requestId, status, reason, message) {
  const request = new HttpRequest(resultUrl(requestId));
  request.method = HttpRequestMethod.Post;
  request.headers = jsonHeaders();
  request.body = JSON.stringify({
    status,
    reason,
    message: message || "",
  });
  await http.request(request);
}

function findOnlinePlayer(name) {
  for (const player of world.getAllPlayers()) {
    if (player.name === name) {
      return player;
    }
  }
  return undefined;
}

function playerForwardSpawnLocation(player) {
  const direction = safeValue(() => player.getViewDirection(), { x: 0, y: 0, z: 1 });
  const horizontalLength = Math.hypot(direction.x || 0, direction.z || 0);
  const forward = horizontalLength > 0.001
    ? { x: direction.x / horizontalLength, z: direction.z / horizontalLength }
    : { x: 0, z: 1 };
  return {
    x: player.location.x + forward.x * 2,
    y: player.location.y,
    z: player.location.z + forward.z * 2,
  };
}

function safeValue(readValue, fallback) {
  try {
    const value = readValue();
    if (value === undefined) {
      return fallback;
    }
    return value;
  } catch (error) {
    return fallback;
  }
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

function stringifyDiagnosticValue(value) {
  if (value === undefined) return "undefined";
  if (value === null) return "null";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch (error) {
    return String(value);
  }
}

function selectedHotbarIndex(player) {
  const fromProperty = safeValue(() => player.selectedSlotIndex, undefined);
  if (typeof fromProperty === "number") {
    return fromProperty;
  }
  const selectedSlot = safeValue(() => player.selectedSlot, undefined);
  if (typeof selectedSlot === "number") {
    return selectedSlot;
  }
  return 0;
}

function componentTypeIds(item) {
  const components = safeValue(() => item.getComponents(), []);
  if (!Array.isArray(components)) {
    return ["Script APIでは取得不可"];
  }
  return components.map((component) => String(component.typeId || component.id || component)).sort();
}

function dynamicProperties(item) {
  const ids = safeValue(() => item.getDynamicPropertyIds(), undefined);
  if (!Array.isArray(ids)) {
    return ["Script APIでは取得不可"];
  }
  if (ids.length === 0) {
    return ["なし"];
  }
  return ids.map((id) => `${id}=${stringifyDiagnosticValue(safeValue(() => item.getDynamicProperty(id), "取得不可"))}`);
}

function itemLore(item) {
  const lore = safeValue(() => item.getLore(), undefined);
  if (!Array.isArray(lore)) {
    return ["Script APIでは取得不可"];
  }
  if (lore.length === 0) {
    return ["なし"];
  }
  return lore;
}

function stringListFromItem(item, getterName) {
  const values = safeValue(() => item[getterName](), undefined);
  if (!Array.isArray(values)) {
    return ["Script APIでは取得不可"];
  }
  if (values.length === 0) {
    return ["なし"];
  }
  return values.map((value) => String(value));
}

function truncateMessage(message) {
  if (message.length <= 1700) {
    return message;
  }
  return `${message.slice(0, 1690)}\n...省略`;
}

async function handleHeldItemInspect(command) {
  const requestId = String(command.request_id || "");
  const playerName = String(command.minecraft_player || "");
  if (!requestId) {
    return;
  }
  if (!isValidPlayerName(playerName)) {
    await postResult(requestId, "failed", "invalid_player_name", "");
    return;
  }
  const player = findOnlinePlayer(playerName);
  if (!player) {
    await postResult(requestId, "failed", "player_offline", "");
    return;
  }
  const inventory = player.getComponent(EntityComponentTypes.Inventory);
  if (!inventory || !inventory.container) {
    await postResult(requestId, "failed", "inventory_unavailable", "");
    return;
  }
  const slotIndex = selectedHotbarIndex(player);
  const item = safeValue(() => inventory.container.getItem(slotIndex), undefined);
  if (!item) {
    await postResult(requestId, "failed", "empty_hand", "");
    return;
  }
  const lines = [
    `${playerName} の手持ち診断`,
    `slot: ${slotIndex}`,
    `typeId: ${item.typeId}`,
    `amount: ${item.amount}`,
    `nameTag: ${safeValue(() => item.nameTag, "") || "なし"}`,
    `isStackable: ${stringifyDiagnosticValue(safeValue(() => item.isStackable, "Script APIでは取得不可"))}`,
    `maxAmount: ${stringifyDiagnosticValue(safeValue(() => item.maxAmount, "Script APIでは取得不可"))}`,
    `weight: ${stringifyDiagnosticValue(safeValue(() => item.weight, "Script APIでは取得不可"))}`,
    `lockMode: ${stringifyDiagnosticValue(safeValue(() => item.lockMode, "Script APIでは取得不可"))}`,
    `keepOnDeath: ${stringifyDiagnosticValue(safeValue(() => item.keepOnDeath, "Script APIでは取得不可"))}`,
    `lore: ${itemLore(item).join(" / ")}`,
    `can_destroy: ${stringListFromItem(item, "getCanDestroy").join(", ")}`,
    `can_place_on: ${stringListFromItem(item, "getCanPlaceOn").join(", ")}`,
    `tags: ${stringListFromItem(item, "getTags").join(", ")}`,
    `components: ${componentTypeIds(item).join(", ") || "なし"}`,
    `dynamic_properties: ${dynamicProperties(item).join(", ")}`,
    `dynamic_property_bytes: ${stringifyDiagnosticValue(safeValue(() => item.getDynamicPropertyTotalByteCount(), "Script APIでは取得不可"))}`,
    "internal_nbt: Script APIでは取得不可",
  ];
  await postResult(requestId, "succeeded", "ok", truncateMessage(lines.join("\n")));
}

async function handleServerStatus(command) {
  const requestId = String(command.request_id || "");
  if (!requestId) {
    return;
  }
  const players = world.getAllPlayers();
  const names = players.map((player) => player.name).sort();
  const bdsVersion = configValue("MINECRAFT_BDS_VERSION", "Script APIでは取得不可");
  const lines = [
    "Minecraft Server: ONLINE",
    `Players: ${players.length}`,
    names.length ? names.map((name) => `- ${name}`).join("\n") : "- なし",
    `Uptime: ${formatDuration(Date.now() - SCRIPT_STARTED_AT_MS)}`,
    `BDS: ${bdsVersion}`,
    "Host CPU: Script APIでは取得不可",
    "Host Memory: Script APIでは取得不可",
    "Docker: Script APIでは取得不可",
  ];
  await postResult(requestId, "succeeded", "ok", truncateMessage(lines.join("\n")));
}

async function handleNaritaCarpet(command) {
  const requestId = String(command.request_id || "");
  const playerName = String(command.minecraft_player || "");
  if (!requestId) {
    return;
  }
  if (!isValidPlayerName(playerName)) {
    await postResult(requestId, "failed", "invalid_player_name", "");
    return;
  }
  const player = findOnlinePlayer(playerName);
  if (!player) {
    await postResult(requestId, "failed", "player_offline", "");
    return;
  }

  const x = Math.floor(player.location.x);
  const y = Math.floor(player.location.y) + 2;
  const z = Math.floor(player.location.z);

  let chestPlaced = false;

  try {
    player.dimension.runCommand(
      `structure load ${STRUCTURE_ID} ${x} ${y} ${z}`
    );
    chestPlaced = true;

    const block = player.dimension.getBlock({ x, y, z });
    if (!block) {
      await postResult(requestId, "failed", "narita_source_block_missing", "");
      return;
    }

    const sourceInventory = block.getComponent("minecraft:inventory");
    if (!sourceInventory || !sourceInventory.container) {
      await postResult(requestId, "failed", "narita_source_inventory_missing", "");
      return;
    }

    const source = sourceInventory.container;

    let mapSlot = -1;
    let mapItem;

    for (let i = 0; i < source.size; i++) {
      const item = source.getSlot(i).getItem();
      if (item && item.typeId === "minecraft:filled_map") {
        mapSlot = i;
        mapItem = item;
        break;
      }
    }

    if (mapSlot < 0 || !mapItem) {
      await postResult(requestId, "failed", "narita_map_missing", "");
      return;
    }

    const playerInventory = player.getComponent(EntityComponentTypes.Inventory);
    if (!playerInventory || !playerInventory.container) {
      await postResult(requestId, "failed", "inventory_unavailable", "");
      return;
    }

    const remaining = playerInventory.container.addItem(mapItem);
    if (remaining !== undefined) {
      await postResult(requestId, "failed", "inventory_full", "");
      return;
    }

    source.setItem(mapSlot, undefined);

    await postResult(requestId, "succeeded", "ok", "");
  } catch (error) {
    console.warn(`[NaritaBridge] Narita transfer failed: ${String(error)}`);
    await postResult(requestId, "failed", "narita_transfer_failed", "");
  } finally {
    if (chestPlaced) {
      try {
        player.dimension.runCommand(`setblock ${x} ${y} ${z} air`);
      } catch (error) {
        console.warn(`[NaritaBridge] Narita cleanup failed: ${String(error)}`);
      }
    }
  }
}

async function handleESchrift(command) {
  const requestId = String(command.request_id || "");
  const playerName = String(command.minecraft_player || "");

  if (!requestId) {
    return;
  }

  if (!isValidPlayerName(playerName)) {
    await postResult(requestId, "failed", "invalid_player_name", "");
    return;
  }

  const player = findOnlinePlayer(playerName);

  if (!player) {
    await postResult(requestId, "failed", "player_offline", "");
    return;
  }

  const x = Math.floor(player.location.x);
  const y = Math.floor(player.location.y) + 2;
  const z = Math.floor(player.location.z);

  let chestPlaced = false;

  try {
    player.dimension.runCommand(
      `structure load ${E_SCHRIFT_STRUCTURE_ID} ${x} ${y} ${z}`
    );

    chestPlaced = true;

    const block = player.dimension.getBlock({ x, y, z });

    if (!block) {
      await postResult(
        requestId,
        "failed",
        "e_schrift_source_block_missing",
        ""
      );
      return;
    }

    const sourceInventory =
      block.getComponent("minecraft:inventory");

    if (!sourceInventory || !sourceInventory.container) {
      await postResult(
        requestId,
        "failed",
        "e_schrift_source_inventory_missing",
        ""
      );
      return;
    }

    const source = sourceInventory.container;

    let itemSlot = -1;
    let sourceItem;

    for (let slot = 0; slot < source.size; slot++) {
      const item = source.getSlot(slot).getItem();

      if (
        item &&
        item.typeId === "minecraft:banner" &&
        safeValue(() => item.nameTag, "") === "E\u306e\u8056\u6587\u5b57"
      ) {
        itemSlot = slot;
        sourceItem = item;
        break;
      }
    }

    if (itemSlot < 0 || !sourceItem) {
      await postResult(
        requestId,
        "failed",
        "e_schrift_item_missing",
        ""
      );
      return;
    }

    const inventory =
      player.getComponent(EntityComponentTypes.Inventory);

    if (!inventory || !inventory.container) {
      await postResult(
        requestId,
        "failed",
        "inventory_unavailable",
        ""
      );
      return;
    }

    const remaining =
      inventory.container.addItem(sourceItem);

    if (remaining !== undefined) {
      await postResult(
        requestId,
        "failed",
        "inventory_full",
        ""
      );
      return;
    }

    source.setItem(itemSlot, undefined);

    await postResult(
      requestId,
      "succeeded",
      "ok",
      ""
    );
  } catch (error) {
    console.warn(
      `[NaritaBridge] E Schrift transfer failed: ${String(error)}`
    );

    await postResult(
      requestId,
      "failed",
      "e_schrift_transfer_failed",
      ""
    );
  } finally {
    if (chestPlaced) {
      try {
        player.dimension.runCommand(
          `setblock ${x} ${y} ${z} air`
        );
      } catch (error) {
        console.warn(
          `[NaritaBridge] E Schrift cleanup failed: ${String(error)}`
        );
      }
    }
  }
}

async function handleGiveItem(command, itemTypeId) {
  const requestId = String(command.request_id || "");
  const playerName = String(command.minecraft_player || "");
  if (!requestId) {
    return;
  }
  if (!isValidPlayerName(playerName)) {
    await postResult(requestId, "failed", "invalid_player_name", "");
    return;
  }
  const player = findOnlinePlayer(playerName);
  if (!player) {
    await postResult(requestId, "failed", "player_offline", "");
    return;
  }
  try {
    const inventory = player.getComponent(EntityComponentTypes.Inventory);
    if (!inventory || !inventory.container) {
      await postResult(requestId, "failed", "inventory_unavailable", "");
      return;
    }
    const remaining = inventory.container.addItem(new ItemStack(itemTypeId, 1));
    if (remaining !== undefined) {
      await postResult(requestId, "failed", "inventory_full", "");
      return;
    }
    await postResult(requestId, "succeeded", "ok", "");
  } catch (error) {
    await postResult(requestId, "failed", "item_add_failed", "");
  }
}

function blockTypeId(block) {
  return safeValue(() => block.typeId, "");
}

function permutationTypeId(permutation) {
  return safeValue(() => permutation.type.id, "");
}

function cardinalDirectionFromPermutation(permutation) {
  const direction = safeValue(() => permutation.getState(CARDINAL_DIRECTION_STATE), "north");
  if (["north", "south", "east", "west"].includes(direction)) {
    return direction;
  }
  return "north";
}

function posterVectors(direction) {
  if (direction === "south") {
    return { right: { x: 1, z: 0 }, wall: { x: 0, z: -1 } };
  }
  if (direction === "east") {
    return { right: { x: 0, z: -1 }, wall: { x: -1, z: 0 } };
  }
  if (direction === "west") {
    return { right: { x: 0, z: 1 }, wall: { x: 1, z: 0 } };
  }
  return { right: { x: -1, z: 0 }, wall: { x: 0, z: 1 } };
}

function offsetLocation(location, vector, amount, yOffset = 0) {
  return {
    x: location.x + vector.x * amount,
    y: location.y + yOffset,
    z: location.z + vector.z * amount,
  };
}

function largePosterSegmentLocation(baseLocation, direction, segment) {
  const { right } = posterVectors(direction);
  return offsetLocation(
    baseLocation,
    right,
    segment.column,
    LARGE_POSTER_ROWS - 1 - segment.row
  );
}

function largePosterBaseLocation(segmentLocation, direction, segment) {
  const { right } = posterVectors(direction);
  return {
    x: segmentLocation.x - right.x * segment.column,
    y: segmentLocation.y - (LARGE_POSTER_ROWS - 1 - segment.row),
    z: segmentLocation.z - right.z * segment.column,
  };
}

function blockAt(dimension, location) {
  return safeValue(() => dimension.getBlock(location), undefined);
}

function isReplaceablePosterTarget(block, baseLocation, poster) {
  if (!block) {
    return false;
  }
  if (
    block.location.x === baseLocation.x
    && block.location.y === baseLocation.y
    && block.location.z === baseLocation.z
  ) {
    return blockTypeId(block) === poster.baseId;
  }
  return blockTypeId(block) === AIR_BLOCK_TYPE;
}

function hasSolidPosterSupport(dimension, location, direction) {
  const { wall } = posterVectors(direction);
  const support = blockAt(dimension, offsetLocation(location, wall, 1));
  return Boolean(support && blockTypeId(support) !== AIR_BLOCK_TYPE);
}

function allLargePosterCellsReady(dimension, baseLocation, direction, poster) {
  for (const segment of LARGE_POSTER_SEGMENTS.filter((candidate) => candidate.poster === poster)) {
    const location = largePosterSegmentLocation(baseLocation, direction, segment);
    const block = blockAt(dimension, location);
    if (!isReplaceablePosterTarget(block, baseLocation, poster)) {
      return false;
    }
    if (!hasSolidPosterSupport(dimension, location, direction)) {
      return false;
    }
  }
  return true;
}

function setBlockType(dimension, location, identifier, direction) {
  const block = blockAt(dimension, location);
  if (!block) {
    return false;
  }
  const states = direction ? { [CARDINAL_DIRECTION_STATE]: direction } : undefined;
  block.setPermutation(BlockPermutation.resolve(identifier, states));
  return true;
}

function removeLargePosterCells(dimension, baseLocation, direction, poster) {
  for (const segment of LARGE_POSTER_SEGMENTS.filter((candidate) => candidate.poster === poster)) {
    const location = largePosterSegmentLocation(baseLocation, direction, segment);
    const block = blockAt(dimension, location);
    const blockSegment = LARGE_POSTER_SEGMENT_BY_ID[blockTypeId(block)];
    if (blockSegment && blockSegment.poster === poster) {
      block.setPermutation(BlockPermutation.resolve(AIR_BLOCK_TYPE));
    }
  }
}

function playerShouldReceivePosterDrop(player) {
  const gameMode = String(
    safeValue(() => player.getGameMode(), "")
  ).toLowerCase();
  return gameMode === "survival" || gameMode === "adventure";
}

function returnLargePosterItem(player, location, poster) {
  if (!playerShouldReceivePosterDrop(player)) {
    return;
  }

  const item = new ItemStack(poster.baseId, 1);
  const inventory = safeValue(
    () => player.getComponent(EntityComponentTypes.Inventory),
    undefined
  );
  const container = inventory ? inventory.container : undefined;

  if (container && safeValue(() => container.addItem(item), item) === undefined) {
    return;
  }

  safeValue(() => player.dimension.spawnItem(item, location), undefined);
}

function expandLargePoster(block, player) {
  const poster = LARGE_POSTER_BY_BASE_ID[blockTypeId(block)];
  if (!poster) {
    return;
  }
  const direction = cardinalDirectionFromPermutation(block.permutation);
  const baseLocation = block.location;
  const dimension = block.dimension;
  if (!allLargePosterCellsReady(dimension, baseLocation, direction, poster)) {
    setBlockType(dimension, baseLocation, AIR_BLOCK_TYPE);
    returnLargePosterItem(player, baseLocation, poster);
    safeValue(() => player.sendMessage(`${poster.label}には4 x 5の空いた壁面が必要です。`), undefined);
    return;
  }
  for (const segment of LARGE_POSTER_SEGMENTS.filter((candidate) => candidate.poster === poster)) {
    setBlockType(
      dimension,
      largePosterSegmentLocation(baseLocation, direction, segment),
      segment.id,
      direction
    );
  }
}

function cleanupLargePosterSegment(block, brokenPermutation, player) {
  const brokenTypeId = permutationTypeId(brokenPermutation);
  const segment = LARGE_POSTER_SEGMENT_BY_ID[brokenTypeId];
  if (!segment) {
    return;
  }
  const direction = cardinalDirectionFromPermutation(brokenPermutation);
  const baseLocation = largePosterBaseLocation(block.location, direction, segment);
  removeLargePosterCells(block.dimension, baseLocation, direction, segment.poster);
  returnLargePosterItem(player, block.location, segment.poster);
}

async function handleTaketumiSpawnNearPlayer(command) {
  const requestId = String(command.request_id || "");
  const playerName = String(command.minecraft_player || "");
  if (!requestId) {
    return;
  }
  if (!isValidPlayerName(playerName)) {
    await postResult(requestId, "failed", "invalid_player_name", "");
    return;
  }
  const player = findOnlinePlayer(playerName);
  if (!player) {
    await postResult(requestId, "failed", "player_offline", "");
    return;
  }

  let spawned;
  try {
    spawned = player.dimension.spawnEntity(TAKETUMI_ENTITY_ID, playerForwardSpawnLocation(player));
  } catch (error) {
    console.warn(`[NaritaBridge] Taketumi spawn failed: ${String(error)}`);
    await postResult(requestId, "failed", "taketumi_spawn_failed", "");
    return;
  }

  try {
    spawned.nameTag = TAKETUMI_NAME_TAG;
  } catch (error) {
    console.warn(`[NaritaBridge] Taketumi nameTag failed: ${String(error)}`);
    try {
      spawned.remove();
    } catch (cleanupError) {
      console.warn(`[NaritaBridge] Taketumi cleanup after name failure failed: ${String(cleanupError)}`);
    }
    await postResult(requestId, "failed", "taketumi_name_failed", "");
    return;
  }

  await postResult(requestId, "succeeded", "ok", "");
}

async function handleTaketumiRemoveNearPlayer(command) {
  const requestId = String(command.request_id || "");
  const playerName = String(command.minecraft_player || "");
  if (!requestId) {
    return;
  }
  if (!isValidPlayerName(playerName)) {
    await postResult(requestId, "failed", "invalid_player_name", "");
    return;
  }
  const player = findOnlinePlayer(playerName);
  if (!player) {
    await postResult(requestId, "failed", "player_offline", "");
    return;
  }

  try {
    const targets = player.dimension.getEntities({
      type: TAKETUMI_ENTITY_ID,
      location: player.location,
      maxDistance: 16,
    });
    let removed = 0;
    for (const entity of targets) {
      try {
        entity.remove();
        removed += 1;
      } catch (error) {
        console.warn(`[NaritaBridge] Taketumi remove one failed: ${String(error)}`);
      }
    }
    const message = removed === 0
      ? `${playerName} の16ブロック以内にタケツミはいません。`
      : `${playerName} の16ブロック以内のタケツミを${removed}体削除しました。`;
    await postResult(requestId, "succeeded", "ok", message);
  } catch (error) {
    console.warn(`[NaritaBridge] Taketumi remove failed: ${String(error)}`);
    await postResult(requestId, "failed", "taketumi_remove_failed", "");
  }
}

async function pollOnce() {
  const configuredGuildId = guildId();
  if (!configuredGuildId) {
    return;
  }
  const url = `${apiBase()}/commands/next?bot_id=${encodeURIComponent(botId())}&guild_id=${encodeURIComponent(configuredGuildId)}`;
  const request = new HttpRequest(url);
  request.method = HttpRequestMethod.Get;
  request.headers = [secretHeader()];
  let response;
  try {
    response = await http.request(request);
  } catch (error) {
    console.warn(`[NaritaBridge] HTTP poll failed: ${String(error)}`);
    return;
  }
  if (response.status !== 200 || !response.body) {
    return;
  }
  let payload;
  try {
    payload = JSON.parse(response.body);
  } catch (error) {
    return;
  }
  const command = payload.command;
  if (!command) {
    return;
  }
  if (command.type === "narita_carpet") {
    await handleNaritaCarpet(command);
    return;
  }
  if (command.type === "e_schrift_item") {
    await handleESchrift(command);
    return;
  }
  if (command.type === "taketumi_spawn_near_player") {
    await handleTaketumiSpawnNearPlayer(command);
    return;
  }
  if (command.type === "taketumi_remove_near_player") {
    await handleTaketumiRemoveNearPlayer(command);
    return;
  }
  if (command.type === "held_item_inspect") {
    await handleHeldItemInspect(command);
    return;
  }
  if (command.type === "server_status") {
    await handleServerStatus(command);
    return;
  }
  if (Object.prototype.hasOwnProperty.call(ITEM_TYPES_BY_COMMAND, command.type)) {
    await handleGiveItem(command, ITEM_TYPES_BY_COMMAND[command.type]);
    return;
  }
  if (command.request_id) {
    await postResult(String(command.request_id || ""), "failed", "unknown_command_type", "");
  }
}

system.runInterval(() => {
  pollOnce().catch((error) => {
    console.warn(`[NaritaBridge] pollOnce failed: ${String(error)}`);
  });
}, POLL_INTERVAL_TICKS);

world.afterEvents.playerPlaceBlock.subscribe((event) => {
  if (!LARGE_POSTER_BY_BASE_ID[blockTypeId(event.block)]) {
    return;
  }
  system.run(() => {
    expandLargePoster(event.block, event.player);
  });
});

world.afterEvents.playerBreakBlock.subscribe((event) => {
  system.run(() => {
    cleanupLargePosterSegment(event.block, event.brokenBlockPermutation, event.player);
  });
});

import "./taketumi_leash.js";
