import { EntityComponentTypes, ItemStack, system, world } from "@minecraft/server";
import { HttpHeader, HttpRequest, HttpRequestMethod, http } from "@minecraft/server-net";
import { secrets, variables } from "@minecraft/server-admin";

const DEFAULT_API_BASE = "http://10.0.0.94:8000/internal/minecraft";
const STRUCTURE_ID = "mystructure:narita_map_item";
const PLAYER_NAME_PATTERN = /^[A-Za-z0-9_]{1,16}$/;
const POLL_INTERVAL_TICKS = 40;
const ITEM_TYPES_BY_COMMAND = {
  structure_block: "minecraft:structure_block",
  command_block: "minecraft:command_block",
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
  try {
    player.dimension.runCommand(`execute at ${playerName} run structure load ${STRUCTURE_ID} ~ ~ ~`);
    await postResult(requestId, "succeeded", "ok", "");
  } catch (error) {
    await postResult(requestId, "failed", "structure_load_failed", "");
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
  if (Object.prototype.hasOwnProperty.call(ITEM_TYPES_BY_COMMAND, command.type)) {
    await handleGiveItem(command, ITEM_TYPES_BY_COMMAND[command.type]);
    return;
  }
  if (command.request_id) {
    await postResult(String(command.request_id || ""), "failed", "unknown_command_type", "");
  }
}

system.runInterval(() => {
  pollOnce().catch(() => {});
}, POLL_INTERVAL_TICKS);
