// Rectangle membership is independent of the query radius: native getEntities
// may return nearby actors from another room or an older managed footprint.
export function insideFixtureScope(venue, entity, padding = 4) {
  const at = entity?.location;
  if (!at || ![at.x, at.y, at.z].every(Number.isFinite)) return false;
  if (entity.dimension?.id && !["minecraft:overworld", "overworld"].includes(entity.dimension.id)) return false;
  return [venue, venue.previous_managed_bounds].filter(Boolean).some(bounds =>
    [at.x, at.y, at.z].every((value, axis) => value >= bounds.origin[axis] - padding &&
      value <= bounds.origin[axis] + bounds.size[axis] - 1 + padding));
}
export function sameFixtureActor(first, second) {
  return first === second || Boolean(first?.id && second?.id && first.id === second.id);
}

// A launched fixed prop can leave the room vertically without changing its
// ownership. Recover only this venue's horizontal footprint, never actors in
// another room or entities identified only by a coincidental role tag.
export function insideOwnedFixtureColumn(venue, entity, padding = 4) {
  const at = entity?.location;
  if (!at || ![at.x, at.y, at.z].every(Number.isFinite)) return false;
  if (entity.dimension?.id && !["minecraft:overworld", "overworld"].includes(entity.dimension.id)) return false;
  if (entity.getDynamicProperty?.("junket:venue_id") !== venue.id) return false;
  return [venue, venue.previous_managed_bounds].filter(Boolean).some(bounds => [0, 2].every(axis => {
    const value = axis === 0 ? at.x : at.z;
    return value >= bounds.origin[axis] - padding && value <= bounds.origin[axis] + bounds.size[axis] - 1 + padding;
  }));
}
