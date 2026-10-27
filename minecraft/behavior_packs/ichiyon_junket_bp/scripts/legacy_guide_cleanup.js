// Old vanilla NPCs can become query-visible after the initial fixture ensure.
// Handle only that delayed load; regular ensure owns already loaded migration.
const installed = new WeakMap();
const GUIDE_ROLES = new Set(["reception", "return", "guard"]);

export function installLegacyGuideCleanup({ world, system, definitions }) {
  if (installed.has(world)) return installed.get(world);
  const venues = new Map(definitions.filter(venue => venue.installation === "native-preinstalled")
    .map(venue => [venue.id, venue]));
  const pending = new Set();
  function match(entity) {
    try {
      if (!entity || entity.typeId !== "minecraft:npc" || entity.dimension?.id !== "minecraft:overworld" ||
          (typeof entity.isValid === "function" ? !entity.isValid() : entity.isValid === false)) return null;
      const owner = entity.getDynamicProperty("junket:venue_id");
      const role = entity.getDynamicProperty("junket:venue_role");
      const venue = venues.get(owner);
      if (!venue || !GUIDE_ROLES.has(role)) return null;
      const expected = venue.fixtures.find(fixture => fixture.role === role && fixture.type === "junket:guide");
      const collection = venue.provider?.collection ?? `junket:${venue.id}:managed`;
      if (!expected || !entity.hasTag(collection) || !entity.hasTag(`junket:${venue.id}:fixture:${role}`)) return null;
      const [x, y, z] = expected.location;
      const at = entity.location;
      if (Math.hypot(at.x - x, at.y - y, at.z - z) > .15) return null;
      return { gameId: venue.id, role, entityId: entity.id, type: entity.typeId, location: { ...at } };
    } catch { return null; } // Unloaded/invalid or unowned actors are preserved.
  }
  const handler = event => {
    const entity = event.entity;
    const candidate = match(entity);
    if (!candidate || pending.has(candidate.entityId)) return;
    pending.add(candidate.entityId);
    system.run(() => {
      pending.delete(candidate.entityId);
      const current = match(entity);
      if (!current || current.entityId !== candidate.entityId || current.gameId !== candidate.gameId || current.role !== candidate.role) return;
      try {
        // No health mutation, spawning, or broad role-tag cleanup. Every
        // ownership/manifest/position condition is rechecked after deferral.
        entity.remove();
        console.log(`[JUNKET] legacy guide migrated on load: ${JSON.stringify(current)}`);
      } catch (error) {
        console.warn(`[JUNKET] legacy guide migration failed: ${JSON.stringify(current)} ${error?.message ?? error}`);
      }
    });
  };
  world.afterEvents.entityLoad.subscribe(handler);
  const result = { registered: true };
  installed.set(world, result);
  return result;
}
