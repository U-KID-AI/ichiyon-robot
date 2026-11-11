import { insideOwnedFixtureColumn } from "./fixture_scope.js";

const MOVING = {
  sparrow: new Set(["bigbox", "littlebox", "diamond"]),
  temperance: new Set(["balance0", "balance1", "stone0", "stone1"]),
};

export function fixtureAnchorMode(venue, fixture, active = false) {
  if (active && (fixture.anchor === "gameplay" || MOVING[venue.id]?.has(fixture.role))) return "gameplay";
  return "fixed";
}

// No spawning, removing, healing, or changing visible state. The runtime owns
// moving actors; this repairs only declared fixed actors carrying exact owner
// and role identity, including an actor launched above the normal query box.
export function anchorVenueFixtures(world, venue, { active = false, idle = false } = {}) {
  const dimension = world.getDimension("minecraft:overworld");
  const collection = venue.provider?.collection ?? `junket:${venue.id}:managed`;
  let candidates;
  try { candidates = dimension.getEntities({ tags: [collection] }); }
  catch (error) { throw new Error(`${venue.id}の固定操作物を読み込めません: ${error?.message ?? error}`); }
  let anchored = 0;
  for (const entity of candidates) {
    if (entity.typeId === "minecraft:player" || !insideOwnedFixtureColumn(venue, entity)) continue;
    const expected = venue.fixtures.find(fixture => entity.typeId === fixture.type &&
      entity.hasTag(`junket:${venue.id}:fixture:${fixture.role}`));
    if (!expected || fixtureAnchorMode(venue, expected, active && !idle) === "gameplay") continue;
    const [x, y, z] = expected.location;
    const at = entity.location;
    const drift = Math.hypot(at.x - x, at.y - y, at.z - z);
    const rotation = entity.getRotation?.() ?? entity.rotation;
    const yawError = rotation && expected.rotation !== undefined
      ? Math.abs(((rotation.y - expected.rotation + 540) % 360) - 180) : 0;
    if (drift > 0.03 || (!active && (yawError > 0.1 || Math.abs(rotation?.x ?? 0) > 0.1))) {
      // Preserve rotation during gameplay: Rabbit floors and other props use
      // rotations as animation, independently of their fixed position.
      const options = { dimension };
      if (!active && expected.rotation !== undefined) options.rotation = { x: 0, y: expected.rotation };
      entity.teleport({ x, y, z }, options);
      anchored++;
    }
    entity.clearVelocity?.();
  }
  return { id: venue.id, anchored };
}

export function installFixtureAnchors({ world, system, manager, definitions, interval = 10 }) {
  return system.runInterval(() => {
    for (const venue of definitions) {
      if (manager.recoveringGames?.has(venue.id)) continue;
      try {
        const active = [...manager.tables.values()].some(table => table.game.id === venue.id && table.state);
        anchorVenueFixtures(world, venue, { active });
      } catch (error) {
        // Unloaded rooms are not empty rooms. Bootstrap/explicit recovery owns
        // spawning and reports failure; the anchor loop never manufactures it.
      }
    }
  }, interval);
}
