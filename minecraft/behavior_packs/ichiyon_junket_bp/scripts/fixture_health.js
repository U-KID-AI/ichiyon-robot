// Only call after matching an owned actor to an expected fixture in its venue
// scope. This does not search for, heal, or remove arbitrary nearby entities.
const recoveries = new Map();

export function fixtureHealthRecoveryCount(id) {
  return recoveries.get(id) ?? 0;
}

function healthOf(entity) {
  const component = entity.getComponent?.("minecraft:health");
  if (!component) return null;
  const current = component.currentValue;
  const maximum = component.effectiveMax;
  if (!Number.isFinite(current)) throw new Error(`操作物の体力を取得できません: ${entity.id}`);
  return { component, current, maximum: Number.isFinite(maximum) ? maximum : null };
}

export function fixtureUsesGameplayHealth(venue, expected) {
  return venue.id === "hope" && (expected.role === "hope_code" || expected.type === "junket:hope_code");
}

export function ensureFixedFixtureHealth(venue, expected, existing, spawn) {
  // Bedrock NPCs expose an effectively unbounded maximum (FLT_MAX), not a
  // usable healing target. Applying it makes the native actor disappear.
  // Preserve existing NPCs untouched; a missing reception is recreated by
  // normal fixture installation and the lifecycle probe checks its health.
  if (expected.type === "minecraft:npc") return existing ?? spawn();
  if (fixtureUsesGameplayHealth(venue, expected)) return existing ?? spawn();
  const before = existing ? healthOf(existing) : null;
  const oldId = existing?.id ?? null;
  let entity = existing;
  const replaced = Boolean(existing && before && before.current <= 0);
  if (replaced) {
    // Restoring a number on an already dying actor does not clear its native
    // death lifecycle. Replace it during installation, before game admission
    // and lifecycle snapshots, instead of losing its ID on a later tick.
    existing.remove();
    entity = null;
  }
  if (!entity) entity = spawn();
  const health = healthOf(entity);
  if (!health) {
    // NPCs may have no health component. Every custom fixed prop in the
    // private bundle defines positive health and must actually expose it.
    if (expected.type.startsWith("minecraft:")) return entity;
    throw new Error(`${venue.id}/${expected.role}: 固定操作物に体力componentがありません`);
  }
  let healed = false;
  if (health.current <= 0 || (health.maximum !== null && health.current < health.maximum)) {
    if (typeof health.component.resetToMaxValue !== "function") {
      throw new Error(`${venue.id}/${expected.role}: 固定操作物の体力を回復できません`);
    }
    health.component.resetToMaxValue();
    healed = true;
  }
  const after = healthOf(entity);
  if (!after || after.current <= 0) {
    throw new Error(`${venue.id}/${expected.role}: 固定操作物の体力が正値になりません (${after?.current})`);
  }
  if (replaced || healed) {
    recoveries.set(venue.id, fixtureHealthRecoveryCount(venue.id) + 1);
    console.warn(`[JUNKET] fixed fixture health reconcile: ${JSON.stringify({
      id: venue.id, role: expected.role, type: expected.type,
      old_id: oldId, new_id: entity.id,
      before: before?.current ?? null, after: after.current, maximum: after.maximum,
      action: replaced ? "replace_dead" : "heal_in_place",
    })}`);
  }
  return entity;
}
