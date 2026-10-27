// Persistent owner identity shared by companion implementations. Never use names or nearest-player selectors.
export const isLiving = (entity) => {
  try {
    const valid = typeof entity?.isValid === "function" ? entity.isValid() : entity?.isValid;
    return !!valid && entity.getComponent("minecraft:health")?.currentValue > 0;
  } catch { return false; }
};
export const nearbyLiving = (player, entity, radius = 6) => isLiving(player) && isLiving(entity)
  && player.dimension.id === entity.dimension.id
  && Math.hypot(player.location.x - entity.location.x, player.location.y - entity.location.y,
    player.location.z - entity.location.z) <= radius;
export const ownerOf = (entity, key) => entity.getDynamicProperty(key);
export const findOwner = (players, entity, key) => players.find((player) => player.id === ownerOf(entity, key));
export function claimOwner(entity, key, player) {
  if (ownerOf(entity, key)) return false;
  entity.setDynamicProperty(key, player.id);
  return true;
}
export function releaseOwner(entity, key, playerId) {
  if (ownerOf(entity, key) !== playerId) return false;
  entity.setDynamicProperty(key, undefined);
  return true;
}
