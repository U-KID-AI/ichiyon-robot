import { VENUE_CONFIG } from "./junket_venue_generated.js";
import { originalVenue } from "./original_venue.js";
import { insideFixtureScope, sameFixtureActor } from "./fixture_scope.js";
import { applyFixturePresentation } from "./venue_presentation.js";
import { ensureFixedFixtureHealth } from "./fixture_health.js";
import { anchorVenueFixtures } from "./fixture_anchor.js";
import { markCreatedFixture, preferredFixture } from "./fixture_reconciliation.js";

const DIMENSION = "minecraft:overworld";
export const LOCATION_TOLERANCE = 0.75;

const DEFAULT_PROVIDERS = {
  sparrow: { collection: "junket:venue_fixture", role_property: "junket:sparrow_role" },
  temperance: { collection: "junket:temperance_fixture", role_property: "junket:temperance_role" },
  lucy: { collection: "junket:lucy_fixture", role_property: "junket:lucy_role" },
  silence: { collection: "junket:silence_fixture", role_property: "junket:silence_role" },
  jinn: { collection: "junket:jinn_fixture", role_property: "junket:jinn_role" },
};

export function specialVenue(id) {
  const venue = originalVenue(id) ?? VENUE_CONFIG.VENUES.find(entry => entry.id === id);
  if (!venue?.special_runtime) throw new Error(`専用会場定義がありません: ${id}`);
  return venue;
}

function providerOf(venue) {
  return venue.provider ?? DEFAULT_PROVIDERS[venue.id];
}

export function fixtureRoleTag(id, role) {
  return `junket:${id}:fixture:${role}`;
}

export function worldPoint(location) {
  return { x: location.x, y: location.y, z: location.z };
}

export function locationPoint(location) {
  return Array.isArray(location)
    ? { x: location[0], y: location[1], z: location[2] }
    : { x: location.x, y: location.y, z: location.z };
}

export function within(entity, location, tolerance = LOCATION_TOLERANCE) {
  try {
    return Math.hypot(entity.location.x - location.x, entity.location.y - location.y,
      entity.location.z - location.z) <= tolerance;
  } catch {
    return false;
  }
}

export function venueDimension(world) {
  return world.getDimension(DIMENSION);
}

export function venueConfig(id) {
  const venue = specialVenue(id);
  return {
    dimension: DIMENSION,
    origin: worldPoint({ x: venue.origin[0], y: venue.origin[1], z: venue.origin[2] }),
    destination: worldPoint(venue.destination),
    returnPoint: worldPoint(venue.return_point),
    size: [...venue.size],
  };
}

function centerOf(venue) {
  return {
    x: venue.origin[0] + venue.size[0] / 2,
    y: venue.origin[1] + venue.size[1] / 2,
    z: venue.origin[2] + venue.size[2] / 2,
  };
}

function tagsOf(entity) {
  try { return entity.getTags?.() ?? []; } catch { return []; }
}

function hasTag(entity, tag) {
  try { return entity.hasTag?.(tag) === true; } catch { return false; }
}

function roleProperty(entity, provider) {
  try {
    const value = entity.getDynamicProperty?.(provider.role_property);
    return typeof value === "string" && value ? value : undefined;
  } catch {
    return undefined;
  }
}

function roleFromTags(id, entity) {
  const prefix = `junket:${id}:fixture:`;
  return tagsOf(entity).find(tag => typeof tag === "string" && tag.startsWith(prefix))?.slice(prefix.length);
}

// This query deliberately does not filter by a collection tag. A persisted
// entity can lose one tag while keeping its role property or object identity,
// and that case must remain repairable after a server restart.
export function nearbyEntities(world, id) {
  const venue = specialVenue(id);
  const maxDistance = Math.hypot(...venue.size) / 2 + 8;
  try {
    const current = venueDimension(world).getEntities({
      location: centerOf(venue), maxDistance,
    });
    const previous = venue.previous_managed_bounds;
    const stale = previous ? venueDimension(world).getEntities({
      location: centerOf(previous), maxDistance: Math.hypot(...previous.size) / 2 + 8,
    }).filter(entity => isManaged(venue, entity)) : [];
    return [...new Map([...current, ...stale].map(entity => [entity.id ?? entity, entity])).values()]
      .filter(entity => entity.typeId !== "minecraft:player" && insideFixtureScope(venue, entity));
  } catch (error) {
    // An unreadable query is not evidence that the room is empty. Spawning
    // into that condition produces duplicate actors at the next chunk tick.
    throw new Error(`${id}の操作物を読み込めません: ${error?.message ?? error}`);
  }
}

function isManaged(venue, entity) {
  const provider = providerOf(venue);
  if (!provider) return false;
  try {
    const owner = entity.getDynamicProperty?.("junket:venue_id");
    if (typeof owner === "string" && owner !== venue.id) return false;
  } catch { return false; }
  if (hasTag(entity, provider.collection)) return true;
  if (roleProperty(entity, provider)) return true;
  return tagsOf(entity).some(tag => typeof tag === "string" &&
    tag.startsWith(`junket:${venue.id}:fixture:`));
}

function describeFixture(venue, entity) {
  try { return { id: entity.id, type: entity.typeId, location: entity.location,
    role: roleOf(venue, entity), managed: isManaged(venue, entity), tags: tagsOf(entity) }; }
  catch (error) { return { error: error?.message ?? String(error) }; }
}

export function venueObjects(world, id) {
  const venue = specialVenue(id);
  const roles = new Set(venue.fixtures.map(value => value.role));
  return nearbyEntities(world, id).filter(entity => isManaged(venue, entity) &&
    roles.has(roleOf(venue, entity) ?? recoverRole(venue, entity)));
}

export function fixture(world, id, role) {
  const venue = specialVenue(id);
  return venueObjects(world, id).find(entity =>
    hasTag(entity, fixtureRoleTag(id, role)) || roleOf(venue, entity) === role);
}

function roleOf(venue, entity) {
  const provider = providerOf(venue);
  const stored = roleProperty(entity, provider);
  const tagged = roleFromTags(venue.id, entity);
  const known = role => venue.fixtures.some(expected => expected.role === role);
  if (stored && known(stored)) return stored;
  if (tagged && known(tagged)) return tagged;
  return stored ?? tagged;
}

function recoverRole(venue, entity) {
  const provider = providerOf(venue);
  if (!isManaged(venue, entity)) return undefined;
  const nearby = venue.fixtures.filter(expected => {
    if (expected.type !== entity.typeId) return false;
    return within(entity, locationPoint(expected.location));
  });
  return nearby.length === 1 ? nearby[0].role : undefined;
}

function ensureTags(entity, tags) {
  for (const tag of tags ?? []) {
    if (!hasTag(entity, tag)) {
      try { entity.addTag(tag); } catch { /* Entity may be unloaded. */ }
    }
  }
}

function setProperty(entity, name, value) {
  try { entity.setDynamicProperty(name, value); } catch { /* Optional property. */ }
}

function applyFixture(venue, entity, expected) {
  const provider = providerOf(venue);
  entity.nameTag = expected.name ?? "";
  if (expected.rotation !== undefined || !within(entity, locationPoint(expected.location))) {
    entity.teleport(locationPoint(expected.location), {
      rotation: { x: 0, y: expected.rotation ?? 0 },
    });
    entity.clearVelocity?.();
  }
  ensureTags(entity, [provider.collection, fixtureRoleTag(venue.id, expected.role), ...(expected.tags ?? [])]);
  setProperty(entity, provider.role_property, expected.role);
  setProperty(entity, "junket:venue_action", expected.action ?? expected.role);
  setProperty(entity, "junket:venue_id", venue.id);
  if (expected.frame_property !== undefined) setProperty(entity, "junket:frame_property", expected.frame_property);
  if (expected.frame_map) setProperty(entity, "junket:frame_map", JSON.stringify(expected.frame_map));
  for (const [name, value] of Object.entries(expected.properties ?? {})) {
    setProperty(entity, name, value);
  }
  applyFixturePresentation(entity, venue, expected);
}

// Resolve the immutable model definition even when a damaged duplicate has
// only ownership/role tags. Do not infer frame support from the type namespace:
// source models include both static textures and animated property mappings.
export function visualFixtureDefinition(entity) {
  const owner = entity.getDynamicProperty?.("junket:venue_id");
  const tagged = tagsOf(entity).map(tag => /^junket:([^:]+):fixture:(.+)$/.exec(tag)).filter(Boolean);
  const ids = typeof owner === "string" ? [owner] : [...new Set(tagged.map(match => match[1]))];
  const matches = [];
  for (const id of ids) {
    const venue = originalVenue(id) ?? VENUE_CONFIG.VENUES.find(value => value.id === id);
    if (!venue) continue;
    const provider = providerOf(venue);
    const roles = new Set([
      entity.getDynamicProperty?.("junket:venue_role"),
      ...(provider?.role_property ? [entity.getDynamicProperty?.(provider.role_property)] : []),
      ...tagged.filter(match => match[1] === id).map(match => match[2]),
    ].filter(role => typeof role === "string"));
    for (const expected of venue.fixtures) {
      if (expected.type === entity.typeId && roles.has(expected.role)) matches.push(expected);
    }
  }
  if (matches.length > 1) throw new Error(`${entity.typeId}: 表示対象の会場roleが一意ではありません`);
  return matches[0] ?? null;
}

// Converted source models retain their original property names and frame order.
// Every caller uses the registered manifest before mutable actor metadata.
export function setVisualFrame(entity, value, fallback = "junket:frame", fixture = null) {
  if (!entity) return;
  // A damaged/recreated actor can have its ownership tags before reconciliation
  // restores dynamic metadata. The immutable fixture definition is authoritative:
  // static source textures must never receive substitute-model frame properties.
  const expected = fixture ?? visualFixtureDefinition(entity);
  const property = expected ? (expected.frame_property ?? fallback)
    : (entity.getDynamicProperty?.("junket:frame_property") ?? fallback);
  if (property === "none") return;
  let frame = value;
  const stored = expected ? expected.frame_map : entity.getDynamicProperty?.("junket:frame_map");
  if (stored !== undefined && stored !== null) {
    const mapping = typeof stored === "string" ? JSON.parse(stored) : stored;
    frame = mapping[value] ?? value;
  }
  entity.setProperty?.(property, frame);
}

export function ensureManifestFixtures(world, id) {
  const venue = specialVenue(id);
  const provider = providerOf(venue);
  if (!provider) throw new Error(`専用会場の管理タグ定義がありません: ${id}`);
  const dimension = venueDimension(world);
  if (venue.installation === "native-preinstalled") {
    const seen = new Set();
    for (const expected of venue.fixtures) {
      const cell = locationPoint(expected.location);
      for (const axis of ["x", "y", "z"]) cell[axis] = Math.floor(cell[axis]);
      const key = `${cell.x},${cell.y},${cell.z}`;
      if (seen.has(key)) continue;
      seen.add(key);
      let block;
      try { block = dimension.getBlock(cell); } catch { block = null; }
      if (!block) throw new Error(`${id}の操作物チャンク未読込: ${expected.role} (${key})`);
    }
  }
  const expectedRoles = new Set(venue.fixtures.map(value => value.role));
  anchorVenueFixtures(world, venue, { idle: true });
  const assignments = new Map();
  const managed = nearbyEntities(world, id).filter(entity => isManaged(venue, entity));
  for (const entity of managed) {
    const role = roleOf(venue, entity) ?? recoverRole(venue, entity);
    if (!role || !expectedRoles.has(role)) {
      // An obsolete role is removable only with this pack's explicit venue
      // owner property. A collection tag alone cannot authorize destruction.
      if (role && entity.getDynamicProperty?.("junket:venue_id") === id) entity.remove();
      continue;
    }
    if (!assignments.has(role)) assignments.set(role, []);
    assignments.get(role).push(entity);
  }
  for (const expected of venue.fixtures) {
    const candidates = assignments.get(expected.role) ?? [];
    let entity = preferredFixture(candidates, expected.type,
      candidate => within(candidate, locationPoint(expected.location)));
    if (candidates.length > 1 || candidates.some(candidate =>
      candidate.typeId !== expected.type || !within(candidate, locationPoint(expected.location)))) {
      console.warn(`[JUNKET] special fixture reconcile: ${JSON.stringify({ id, role: expected.role,
        expected: { type: expected.type, location: expected.location },
        retained: entity?.id, candidates: candidates.map(candidate => describeFixture(venue, candidate)) })}`);
    }
    for (const candidate of candidates) {
      if (!sameFixtureActor(candidate, entity)) candidate.remove();
    }
    entity = ensureFixedFixtureHealth(venue, expected, entity,
      () => markCreatedFixture(dimension.spawnEntity(expected.type, locationPoint(expected.location))));
    applyFixture(venue, entity, expected);
  }
  return venue;
}

export function verifyManifestFixtures(world, id) {
  const venue = specialVenue(id);
  const provider = providerOf(venue);
  if (!provider) throw new Error(`専用会場の管理タグ定義がありません: ${id}`);
  const objects = venueObjects(world, id);
  for (const expected of venue.fixtures) {
    const roleTag = fixtureRoleTag(id, expected.role);
    const matches = objects.filter(entity =>
      hasTag(entity, roleTag) && roleOf(venue, entity) === expected.role);
    if (matches.length !== 1) {
      console.warn(`[JUNKET] special fixture mismatch: ${JSON.stringify({ id, role: expected.role,
        expected: { type: expected.type, location: expected.location },
        candidates: matches.map(entity => describeFixture(venue, entity)), totalManaged: objects.length })}`);
      throw new Error(`${venue.title}の${expected.role}が1体ではありません: ${matches.length}`);
    }
    const entity = matches[0];
    if (entity.typeId !== expected.type) {
      throw new Error(`${venue.title}の${expected.role}の種類が不一致です: ${entity.typeId}`);
    }
    if (!within(entity, locationPoint(expected.location))) {
      throw new Error(`${venue.title}の${expected.role}が所定位置にありません。`);
    }
    for (const tag of [provider.collection, ...(expected.tags ?? [])]) {
      if (!hasTag(entity, tag)) throw new Error(`${venue.title}の${expected.role}にタグがありません: ${tag}`);
    }
    for (const [name, value] of Object.entries(expected.properties ?? {})) {
      let actual;
      try { actual = entity.getDynamicProperty?.(name); } catch { actual = undefined; }
      if (actual !== value) {
        throw new Error(`${venue.title}の${expected.role}の${name}が不一致です。`);
      }
    }
  }
  if (objects.length !== venue.fixtures.length) {
    throw new Error(`${venue.title}の管理対象entity数が不一致です: ${objects.length}/${venue.fixtures.length}`);
  }
  return true;
}

export function setVenueConfig(world, id, key) {
  world.setDynamicProperty(key, JSON.stringify(venueConfig(id)));
}
