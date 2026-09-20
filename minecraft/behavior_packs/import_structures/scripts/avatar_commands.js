// Fixed vocabulary only. No command strings, selectors, coordinates or entity IDs
// supplied by Discord are executed. Dependencies are the existing Bridge helpers.
export const AVATAR_COMMANDS = Object.freeze({
  avatar_kiana_spawn_near_player: { action: "spawn", variant: 0, event: "ichiyon:kiana" },
  avatar_mei_spawn_near_player: { action: "spawn", variant: 1, event: "ichiyon:mei" },
  avatar_bronya_spawn_near_player: { action: "spawn", variant: 2, event: "ichiyon:bronya" },
  avatar_albert_spawn_near_player: { action: "spawn", variant: 3, event: "ichiyon:albert" },
  avatar_kiana_remove_near_player: { action: "remove", variant: 0 },
  avatar_mei_remove_near_player: { action: "remove", variant: 1 },
  avatar_bronya_remove_near_player: { action: "remove", variant: 2 },
  avatar_albert_remove_near_player: { action: "remove", variant: 3 },
  avatar_all_remove_near_player: { action: "remove", variant: null },
});

export async function handleAvatarCommand(command, helpers) {
  if (!Object.prototype.hasOwnProperty.call(AVATAR_COMMANDS, command.type)) return false;
  const spec = AVATAR_COMMANDS[command.type];
  const requestId = String(command.request_id || "");
  if (!requestId) return true;
  const playerName = String(command.minecraft_player || "");
  if (!helpers.isValidPlayerName(playerName)) {
    await helpers.postResult(requestId, "failed", "invalid_player_name", "");
    return true;
  }
  const player = helpers.findOnlinePlayer(playerName);
  if (!player) {
    await helpers.postResult(requestId, "failed", "player_offline", "");
    return true;
  }

  let spawned;
  let message = "";
  try {
    if (spec.action === "spawn") {
      spawned = player.dimension.spawnEntity("ichiyon:avatar", helpers.playerForwardSpawnLocation(player));
      spawned.triggerEvent(spec.event);
      // Set once at placement; never track players or rotate an existing display.
      spawned.setRotation({ x: 0, y: player.getRotation().y });
    } else {
      const targets = player.dimension.getEntities({
        type: "ichiyon:avatar", location: player.location, maxDistance: 16,
      });
      // Resolve all variants before mutation. Missing components fail closed.
      const matching = targets.filter(entity => {
        const variant = entity.getComponent("minecraft:variant");
        if (!variant) throw new Error("missing avatar variant");
        return spec.variant === null || variant.value === spec.variant;
      });
      // Individual removal means the nearest matching mannequin, not every copy.
      matching.sort((a, b) => distanceSquared(a.location, player.location) - distanceSquared(b.location, player.location));
      const selected = spec.variant === null ? matching : matching.slice(0, 1);
      for (const entity of selected) entity.remove();
      message = `${playerName} の16ブロック以内のマネキンを${selected.length}体削除しました。`;
    }
  } catch (error) {
    let reason = spec.action === "spawn" ? "avatar_spawn_failed" : "avatar_remove_failed";
    if (spawned) {
      try { spawned.remove(); } catch (cleanupError) { reason = "avatar_cleanup_failed"; }
    }
    // A partial removal must not be reported as success.
    await helpers.postResult(requestId, "failed", reason, "");
    return true;
  }
  await helpers.postResult(requestId, "succeeded", "ok", message);
  return true;
}

function distanceSquared(a, b) {
  return (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2;
}
