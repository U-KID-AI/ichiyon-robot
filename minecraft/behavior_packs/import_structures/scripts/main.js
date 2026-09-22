import { BlockPermutation, EntityComponentTypes, ItemStack, system, world } from "@minecraft/server";
import { HttpHeader, HttpRequest, HttpRequestMethod, http } from "@minecraft/server-net";
import { secrets, variables } from "@minecraft/server-admin";
import { handleAvatarCommand } from "./avatar_commands.js";
import { cosmetics, cosmeticsDigest } from "./cosmetics.js";
import "./mokuro.js";

console.warn("[NaritaBridge] main.js loaded");

const DEFAULT_API_BASE = "http://10.0.0.94:8000/internal/minecraft";
const STRUCTURE_ID = "mystructure:narita_map_item";
const E_SCHRIFT_STRUCTURE_ID = "mystructure:e_schrift_item";
const ENTITY_COMMANDS = {
  taketumi: {
    entityId: "ichiyon:taketumi",
    nameTag: "タケツミ",
    label: "タケツミ",
    spawnFailureReason: "taketumi_spawn_failed",
    nameFailureReason: "taketumi_name_failed",
    removeFailureReason: "taketumi_remove_failed",
  },
  gonta: {
    entityId: "ichiyon:gonta",
    nameTag: "",
    label: "ゴン太",
    spawnFailureReason: "gonta_spawn_failed",
    nameFailureReason: "gonta_name_failed",
    removeFailureReason: "gonta_remove_failed",
  },
  molcar: {
    entityId: "ichiyon:molcar",
    nameTag: "",
    label: "モルカー",
    spawnFailureReason: "molcar_spawn_failed",
    nameFailureReason: "molcar_name_failed",
    removeFailureReason: "molcar_remove_failed",
  },
  molcar3: {
    entityId: "ichiyon:molcar3",
    nameTag: "",
    label: "モルカー3",
    spawnFailureReason: "molcar3_spawn_failed",
    nameFailureReason: "molcar3_name_failed",
    removeFailureReason: "molcar3_remove_failed",
  },
};
const PLAYER_NAME_PATTERN = /^[A-Za-z0-9_]{1,16}$/;
const POLL_INTERVAL_TICKS = 40;
const SCRIPT_STARTED_AT_MS = Date.now();
const JOIN_DIAGNOSTIC_LIMIT = 300;
const JOIN_PROBE_DELAYS = [1, 20, 60];
const RESOURCE_PACK_HANDSHAKE_STATUS = "RESOURCE_PACK_HANDSHAKE_NOT_OBSERVABLE_FROM_SCRIPT_API";
const TICK_DRIFT_SAMPLE_TICKS = 20;
const AIR_BLOCK_TYPE = "minecraft:air";
const CARDINAL_DIRECTION_STATE = "minecraft:cardinal_direction";
const LARGE_POSTER_COLUMNS = 4;
const LARGE_POSTER_ROWS = 6;
const LARGE_POSTERS = [
  { baseId: "ichiyon:poster_raio", segmentPrefix: "ichiyon:poster_raio", label: "ライオポスター" },
  { baseId: "ichiyon:poster_trent", segmentPrefix: "ichiyon:poster_trent", label: "トレントポスター" },
  { baseId: "ichiyon:poster_aurelia", segmentPrefix: "ichiyon:poster_aurelia", label: "オーレリアポスター" },
  { baseId: "ichiyon:poster_killzael", segmentPrefix: "ichiyon:poster_killzael", label: "キルザエルポスター" },
  { baseId: "ichiyon:poster_caravan_mammoth", segmentPrefix: "ichiyon:poster_caravan_mammoth", label: "キャラバンマンモスポスター" },
  { baseId: "ichiyon:poster_itsutake", segmentPrefix: "ichiyon:poster_itsutake", label: "イツタケポスター" },
  { baseId: "ichiyon:poster_cat_tuner", segmentPrefix: "ichiyon:poster_cat_tuner", label: "キャットチューナーポスター" },
  { baseId: "ichiyon:poster_wilbert", segmentPrefix: "ichiyon:poster_wilbert", label: "ウィルバートポスター" },
  { baseId: "ichiyon:poster_miltio", segmentPrefix: "ichiyon:poster_miltio", label: "ミルティオポスター" },
  { baseId: "ichiyon:poster_ace", segmentPrefix: "ichiyon:poster_ace", label: "エースポスター" },
  { baseId: "ichiyon:poster_eyes_eden", segmentPrefix: "ichiyon:poster_eyes_eden", label: "アイズエデンポスター" },
  { baseId: "ichiyon:poster_akuki", segmentPrefix: "ichiyon:poster_akuki", label: "悪鬼ポスター", columns: 6, rows: 4 },
];
const LARGE_POSTER_BY_BASE_ID = Object.fromEntries(
  LARGE_POSTERS.map((poster) => [poster.baseId, poster])
);
const LARGE_POSTER_SEGMENTS = [];
for (const poster of LARGE_POSTERS) {
  for (let row = 0; row < (poster.rows || LARGE_POSTER_ROWS); row += 1) {
    for (let column = 0; column < (poster.columns || LARGE_POSTER_COLUMNS); column += 1) {
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
  poster_cat_tuner: "ichiyon:poster_cat_tuner",
  poster_wilbert: "ichiyon:poster_wilbert",
  poster_miltio: "ichiyon:poster_miltio",
  poster_ace: "ichiyon:poster_ace",
  poster_eyes_eden: "ichiyon:poster_eyes_eden",
  poster_akuki: "ichiyon:poster_akuki",
  softshell_crab: "ichiyon:softshell_crab",
};
const joinDiagnostics = [];
const httpDiagnostics = {
  pendingHttpRequests: 0,
  lastPollStartMs: null,
  lastPollEndMs: null,
  lastPollLatencyMs: null,
  lastPollSuccess: null,
  lastPollFailure: null,
  lastError: "",
  lastCommandType: "",
  lastCommandReceivedMs: null,
  commandBacklogCount: "not_available_from_next_command_api",
};
const tickDriftDiagnostics = {
  currentTick: 0,
  lastSampleMs: Date.now(),
  lastIntervalMs: null,
  lastDriftMs: null,
  recentMaxDriftMs: 0,
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

function currentTick() {
  return safeValue(() => system.currentTick, 0);
}

function pushJoinDiagnostic(event) {
  joinDiagnostics.push({
    timestamp_ms: Date.now(),
    timestamp: new Date().toISOString(),
    current_tick: currentTick(),
    ...event,
  });
  while (joinDiagnostics.length > JOIN_DIAGNOSTIC_LIMIT) {
    joinDiagnostics.shift();
  }
}

function compactLocation(location) {
  if (!location) {
    return null;
  }
  return {
    x: Math.round(Number(location.x || 0) * 1000) / 1000,
    y: Math.round(Number(location.y || 0) * 1000) / 1000,
    z: Math.round(Number(location.z || 0) * 1000) / 1000,
  };
}

function itemSummary(item, slot) {
  if (!item) {
    return undefined;
  }
  return {
    slot,
    typeId: safeValue(() => item.typeId, ""),
    amount: safeValue(() => item.amount, 0),
    nameTag: safeValue(() => item.nameTag, "") || "",
  };
}

function readInventoryProbe(player) {
  const probe = {
    inventory_component_obtainable: false,
    inventory_container_obtainable: false,
    inventory_size: null,
    selectedSlotIndex: safeValue(() => selectedHotbarIndex(player), null),
    selected_slot: null,
    non_empty_slots: [],
    inventory_read_exception: null,
  };
  try {
    const inventory = player.getComponent(EntityComponentTypes.Inventory);
    probe.inventory_component_obtainable = Boolean(inventory);
    const container = inventory ? inventory.container : undefined;
    probe.inventory_container_obtainable = Boolean(container);
    if (!container) {
      return probe;
    }
    probe.inventory_size = safeValue(() => container.size, null);
    const selected = safeValue(() => container.getItem(probe.selectedSlotIndex), undefined);
    probe.selected_slot = selected ? itemSummary(selected, probe.selectedSlotIndex) : null;
    for (let slot = 0; slot < container.size; slot += 1) {
      const item = safeValue(() => container.getItem(slot), undefined);
      if (item) {
        probe.non_empty_slots.push(itemSummary(item, slot));
      }
    }
    probe.non_empty_slot_count = probe.non_empty_slots.length;
    if (probe.non_empty_slots.length > 20) {
      probe.non_empty_slots = probe.non_empty_slots.slice(0, 20);
      probe.non_empty_slots_truncated = true;
    } else {
      probe.non_empty_slots_truncated = false;
    }
  } catch (error) {
    probe.inventory_read_exception = String(error);
  }
  return probe;
}

function healthProbe(player) {
  const health = safeValue(() => player.getComponent(EntityComponentTypes.Health), undefined)
    || safeValue(() => player.getComponent("minecraft:health"), undefined);
  if (!health) {
    return { obtainable: false };
  }
  return {
    obtainable: true,
    current: safeValue(() => health.currentValue, null),
    effective_max: safeValue(() => health.effectiveMax, null),
  };
}

function playerDiagnosticPayload(playerName) {
  const player = findOnlinePlayer(playerName);
  const payload = {
    schema: "ichiyon.minecraft_sync_diagnostics.v1",
    timestamp_ms: Date.now(),
    timestamp: new Date().toISOString(),
    current_tick: currentTick(),
    resource_pack_handshake: RESOURCE_PACK_HANDSHAKE_STATUS,
    player_found: Boolean(player),
    player_name: playerName,
    entity_id: null,
    dimension: null,
    location: null,
    gameMode: null,
    health: { obtainable: false },
    inventory: null,
    tick_drift: { ...tickDriftDiagnostics, currentTick: currentTick() },
    http: { ...httpDiagnostics },
  };
  if (!player) {
    return payload;
  }
  payload.entity_id = safeValue(() => player.id, "Script APIでは取得不可");
  payload.dimension = safeValue(() => player.dimension.id, "Script APIでは取得不可");
  payload.location = compactLocation(safeValue(() => player.location, null));
  payload.gameMode = safeValue(() => player.getGameMode(), "Script APIでは取得不可");
  payload.health = healthProbe(player);
  payload.inventory = readInventoryProbe(player);
  return payload;
}

function recordPlayerProbe(player, phase) {
  pushJoinDiagnostic({
    event: "inventory_probe",
    phase,
    player_name: safeValue(() => player.name, ""),
    player_found: true,
    entity_id: safeValue(() => player.id, "Script APIでは取得不可"),
    dimension: safeValue(() => player.dimension.id, "Script APIでは取得不可"),
    location: compactLocation(safeValue(() => player.location, null)),
    gameMode: safeValue(() => player.getGameMode(), "Script APIでは取得不可"),
    health: healthProbe(player),
    inventory: readInventoryProbe(player),
  });
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

async function handleSyncDiagnostics(command) {
  const requestId = String(command.request_id || "");
  const playerName = String(command.minecraft_player || "");
  if (!requestId) {
    return;
  }
  if (!isValidPlayerName(playerName)) {
    await postResult(requestId, "failed", "invalid_player_name", "");
    return;
  }
  const payload = playerDiagnosticPayload(playerName);
  await postResult(requestId, "succeeded", "ok", truncateMessage(JSON.stringify(payload)));
}

async function handleJoinHistory(command) {
  const requestId = String(command.request_id || "");
  if (!requestId) {
    return;
  }
  const payload = {
    schema: "ichiyon.minecraft_join_history.v1",
    timestamp_ms: Date.now(),
    timestamp: new Date().toISOString(),
    current_tick: currentTick(),
    resource_pack_handshake: RESOURCE_PACK_HANDSHAKE_STATUS,
    events: joinDiagnostics.slice(-60),
    event_count: joinDiagnostics.length,
    tick_drift: { ...tickDriftDiagnostics, currentTick: currentTick() },
    http: { ...httpDiagnostics },
  };
  await postResult(requestId, "succeeded", "ok", truncateMessage(JSON.stringify(payload)));
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
  return offsetLocation(baseLocation, right, segment.column, segment.row);
}

function largePosterBaseLocation(segmentLocation, direction, segment) {
  const { right } = posterVectors(direction);
  return {
    x: segmentLocation.x - right.x * segment.column,
    y: segmentLocation.y - segment.row,
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
    safeValue(
      () => player.sendMessage(
        `${poster.label}には${poster.columns || LARGE_POSTER_COLUMNS} x ${poster.rows || LARGE_POSTER_ROWS}の空いた壁面が必要です。`
      ),
      undefined
    );
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

async function handleEntitySpawnNearPlayer(command, spec) {
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
    spawned = player.dimension.spawnEntity(spec.entityId, playerForwardSpawnLocation(player));
  } catch (error) {
    console.warn(`[NaritaBridge] ${spec.label} spawn failed: ${String(error)}`);
    await postResult(requestId, "failed", spec.spawnFailureReason, "");
    return;
  }

  if (spec.nameTag) {
    try {
      spawned.nameTag = spec.nameTag;
    } catch (error) {
      console.warn(`[NaritaBridge] ${spec.label} nameTag failed: ${String(error)}`);
      try {
        spawned.remove();
      } catch (cleanupError) {
        console.warn(`[NaritaBridge] ${spec.label} cleanup after name failure failed: ${String(cleanupError)}`);
      }
      await postResult(requestId, "failed", spec.nameFailureReason, "");
      return;
    }
  }

  await postResult(requestId, "succeeded", "ok", "");
}

async function handleEntityRemoveNearPlayer(command, spec) {
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
      type: spec.entityId,
      location: player.location,
      maxDistance: 16,
    });
    let removed = 0;
    for (const entity of targets) {
      try {
        entity.remove();
        removed += 1;
      } catch (error) {
        console.warn(`[NaritaBridge] ${spec.label} remove one failed: ${String(error)}`);
      }
    }
    const message = removed === 0
      ? `${playerName} の16ブロック以内に${spec.label}はいません。`
      : `${playerName} の16ブロック以内の${spec.label}を${removed}体削除しました。`;
    await postResult(requestId, "succeeded", "ok", message);
  } catch (error) {
    console.warn(`[NaritaBridge] ${spec.label} remove failed: ${String(error)}`);
    await postResult(requestId, "failed", spec.removeFailureReason, "");
  }
}

async function pollOnce() {
  const configuredGuildId = guildId();
  if (!configuredGuildId) {
    return;
  }
  const url = `${apiBase()}/commands/next?bot_id=${encodeURIComponent(botId())}&guild_id=${encodeURIComponent(configuredGuildId)}&cosmetics_digest=${encodeURIComponent(cosmeticsDigest)}`;
  const request = new HttpRequest(url);
  request.method = HttpRequestMethod.Get;
  request.headers = [secretHeader()];
  let response;
  const pollStartMs = Date.now();
  httpDiagnostics.pendingHttpRequests += 1;
  httpDiagnostics.lastPollStartMs = pollStartMs;
  httpDiagnostics.lastPollSuccess = null;
  httpDiagnostics.lastPollFailure = null;
  try {
    response = await http.request(request);
  } catch (error) {
    httpDiagnostics.lastPollEndMs = Date.now();
    httpDiagnostics.lastPollLatencyMs = httpDiagnostics.lastPollEndMs - pollStartMs;
    httpDiagnostics.lastPollSuccess = false;
    httpDiagnostics.lastPollFailure = "request_exception";
    httpDiagnostics.lastError = String(error);
    console.warn(`[NaritaBridge] HTTP poll failed: ${String(error)}`);
    return;
  } finally {
    httpDiagnostics.pendingHttpRequests = Math.max(0, httpDiagnostics.pendingHttpRequests - 1);
  }
  httpDiagnostics.lastPollEndMs = Date.now();
  httpDiagnostics.lastPollLatencyMs = httpDiagnostics.lastPollEndMs - pollStartMs;
  if (response.status !== 200 || !response.body) {
    httpDiagnostics.lastPollSuccess = false;
    httpDiagnostics.lastPollFailure = `http_${response.status}`;
    httpDiagnostics.lastError = "";
    return;
  }
  let payload;
  try {
    payload = JSON.parse(response.body);
  } catch (error) {
    httpDiagnostics.lastPollSuccess = false;
    httpDiagnostics.lastPollFailure = "json_parse_failed";
    httpDiagnostics.lastError = String(error);
    return;
  }
  httpDiagnostics.lastPollSuccess = true;
  httpDiagnostics.lastPollFailure = null;
  httpDiagnostics.lastError = "";
  const command = payload.command;
  if (!command) {
    return;
  }
  httpDiagnostics.lastCommandType = String(command.type || "");
  httpDiagnostics.lastCommandReceivedMs = Date.now();
  if (await cosmetics.handleCommand(command, {
    isValidPlayerName, findOnlinePlayer, playerForwardSpawnLocation, postResult,
  })) return;
  if (await handleAvatarCommand(command, {
    isValidPlayerName, findOnlinePlayer, playerForwardSpawnLocation, postResult,
  })) return;
  if (command.type === "narita_carpet") {
    await handleNaritaCarpet(command);
    return;
  }
  if (command.type === "e_schrift_item") {
    await handleESchrift(command);
    return;
  }
  if (command.type === "taketumi_spawn_near_player") {
    await handleEntitySpawnNearPlayer(command, ENTITY_COMMANDS.taketumi);
    return;
  }
  if (command.type === "taketumi_remove_near_player") {
    await handleEntityRemoveNearPlayer(command, ENTITY_COMMANDS.taketumi);
    return;
  }
  if (command.type === "gonta_spawn_near_player") {
    await handleEntitySpawnNearPlayer(command, ENTITY_COMMANDS.gonta);
    return;
  }
  if (command.type === "gonta_remove_near_player") {
    await handleEntityRemoveNearPlayer(command, ENTITY_COMMANDS.gonta);
    return;
  }
  if (command.type === "molcar_spawn_near_player") {
    await handleEntitySpawnNearPlayer(command, ENTITY_COMMANDS.molcar);
    return;
  }
  if (command.type === "molcar_remove_near_player") {
    await handleEntityRemoveNearPlayer(command, ENTITY_COMMANDS.molcar);
    return;
  }
  if (command.type === "molcar3_spawn_near_player") {
    await handleEntitySpawnNearPlayer(command, ENTITY_COMMANDS.molcar3);
    return;
  }
  if (command.type === "molcar3_remove_near_player") {
    await handleEntityRemoveNearPlayer(command, ENTITY_COMMANDS.molcar3);
    return;
  }
  if (command.type === "held_item_inspect") {
    await handleHeldItemInspect(command);
    return;
  }
  if (command.type === "sync_diagnostics") {
    await handleSyncDiagnostics(command);
    return;
  }
  if (command.type === "join_history") {
    await handleJoinHistory(command);
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

pushJoinDiagnostic({
  event: "script_startup",
  script_started_at_ms: SCRIPT_STARTED_AT_MS,
  script_started_at: new Date(SCRIPT_STARTED_AT_MS).toISOString(),
  resource_pack_handshake: RESOURCE_PACK_HANDSHAKE_STATUS,
});

system.runInterval(() => {
  const now = Date.now();
  const expectedMs = TICK_DRIFT_SAMPLE_TICKS * 50;
  const intervalMs = now - tickDriftDiagnostics.lastSampleMs;
  const driftMs = intervalMs - expectedMs;
  tickDriftDiagnostics.currentTick = currentTick();
  tickDriftDiagnostics.lastSampleMs = now;
  tickDriftDiagnostics.lastIntervalMs = intervalMs;
  tickDriftDiagnostics.lastDriftMs = driftMs;
  tickDriftDiagnostics.recentMaxDriftMs = Math.max(
    Math.abs(driftMs),
    Number(tickDriftDiagnostics.recentMaxDriftMs || 0) * 0.95
  );
}, TICK_DRIFT_SAMPLE_TICKS);

world.afterEvents.playerSpawn.subscribe((event) => {
  const player = event.player;
  pushJoinDiagnostic({
    event: "player_spawn",
    initialSpawn: Boolean(event.initialSpawn),
    player_name: safeValue(() => player.name, ""),
    entity_id: safeValue(() => player.id, "Script APIでは取得不可"),
    dimension: safeValue(() => player.dimension.id, "Script APIでは取得不可"),
    location: compactLocation(safeValue(() => player.location, null)),
    resource_pack_handshake: RESOURCE_PACK_HANDSHAKE_STATUS,
  });
  if (event.initialSpawn) {
    recordPlayerProbe(player, "initialSpawn");
    for (const delay of JOIN_PROBE_DELAYS) {
      system.runTimeout(() => {
        recordPlayerProbe(player, `initialSpawn+${delay}t`);
      }, delay);
    }
  }
});

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

world.afterEvents.itemCompleteUse.subscribe((event) => {
  const itemStack = event.itemStack;
  const player = event.source;
  if (!player || itemStack?.typeId !== "ichiyon:softshell_crab") {
    return;
  }
  system.run(() => {
    const overworld = safeValue(() => world.getDimension("overworld"), undefined);
    if (!overworld) {
      return;
    }
    safeValue(
      () => player.teleport({ x: -43, y: 75, z: 207 }, { dimension: overworld }),
      undefined
    );
  });
});

import "./taketumi_leash.js";
import "./molcar_combat.js";
