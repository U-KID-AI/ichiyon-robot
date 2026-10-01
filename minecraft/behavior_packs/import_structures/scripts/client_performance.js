// Verified against @minecraft/server 2.11.0-beta.1.26.51-stable.
// Only these public performance fields are allowed into persistent logs.
export function readClientPerformance(player, now = () => new Date().toISOString()) {
  const unavailable = {};
  function read(key, getter, valid) {
    try {
      const value = getter();
      if (value === undefined || value === null) unavailable[key] = "unavailable";
      else if (valid(value)) return value;
      else unavailable[key] = "invalid";
    } catch { unavailable[key] = "error"; } // Never serialize exception text or API objects.
    return null;
  }
  const text = value => typeof value === "string" && value.length > 0 && value.length <= 128;
  const integer = value => Number.isInteger(value) && value >= 0;
  return {
    schema: "ichiyon.client_performance.v2",
    timestamp: read("timestamp", now, text),
    player_name: read("player_name", () => player.name, text),
    // Raw Script API observations, NOT the resource-pack memory_performance_tier.
    // Do not infer platform model, RAM capacity or the engine's selected subpack.
    clientSystemInfo: {
      platformType: read("clientSystemInfo.platformType", () => player.clientSystemInfo?.platformType, text),
      memoryTier: read("clientSystemInfo.memoryTier", () => player.clientSystemInfo?.memoryTier, integer),
      maxRenderDistance: read("clientSystemInfo.maxRenderDistance", () => player.clientSystemInfo?.maxRenderDistance, integer),
      locale: read("clientSystemInfo.locale", () => player.clientSystemInfo?.locale, text),
    },
    graphicsMode: read("graphicsMode", () => player.graphicsMode, text),
    inputInfo: {
      lastInputModeUsed: read("inputInfo.lastInputModeUsed", () => player.inputInfo?.lastInputModeUsed, text),
    },
    unavailable,
  };
}

export function logClientPerformance(performance, log = console.warn) {
  try { log("[NaritaBridge:client_performance] " + JSON.stringify(performance)); }
  catch { /* Logging must not interrupt join probes. */ }
}
