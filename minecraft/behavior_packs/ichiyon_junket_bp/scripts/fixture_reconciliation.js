// Blocks become readable before saved actors and their tag indexes during
// BDS startup. Reconciliation must observe actors before deciding to spawn.
const created = new Set();
const persisted = new Set();
const trackedWorlds = new WeakSet();

export function markCreatedFixture(entity) {
  created.add(entity.id ?? entity);
  persisted.delete(entity.id ?? entity);
  return entity;
}

export function observeExistingFixture(entity) {
  const identity = entity.id ?? entity;
  if (!created.has(identity)) persisted.add(identity);
  return entity;
}

export function installFixtureActorTracking(world) {
  if (trackedWorlds.has(world)) return;
  trackedWorlds.add(world);
  const relevant = entity => entity?.typeId?.startsWith("junket:") ||
    entity?.typeId?.startsWith("junket_original:");
  world.afterEvents?.entityLoad?.subscribe(event => {
    if (relevant(event.entity)) observeExistingFixture(event.entity);
  });
  world.afterEvents?.entitySpawn?.subscribe(event => {
    if (!relevant(event.entity)) return;
    if (event.cause === "Loaded") observeExistingFixture(event.entity);
    else markCreatedFixture(event.entity);
  });
}

export function preferredFixture(candidates, typeId, positioned) {
  const sameType = candidates.filter(entity => entity.typeId === typeId);
  const saved = sameType.filter(entity => persisted.has(entity.id ?? entity));
  // Saved actors can appear after an empty query already caused a replacement.
  // Keep the saved actor, even when displaced, rather than query-order first.
  return saved.find(positioned) ?? saved[0] ?? sameType.find(positioned) ?? sameType[0];
}

export async function waitForFixtureActors({ read, wait, guard = () => {},
  expectedCount = 1, settleTicks = 2, emptyTicks = 40, timeoutTicks = 100 }) {
  let elapsed = 0, unchanged = 0, previous = null;
  while (elapsed <= timeoutTicks) {
    guard();
    // No spawning, removal, healing, anchoring or normalization in this wait.
    const identities = [...new Set(read())].sort();
    const fingerprint = JSON.stringify(identities);
    unchanged = previous === fingerprint ? unchanged : 0;
    if (previous === fingerprint && unchanged >= settleTicks &&
        (identities.length >= expectedCount || elapsed >= emptyTicks)) {
      return { waited_ticks: elapsed, visible_actors: identities.length, settled: true };
    }
    previous = fingerprint;
    if (elapsed === timeoutTicks) break;
    const step = Math.min(2, timeoutTicks - elapsed);
    await wait(step);
    elapsed += step;
    unchanged += step;
  }
  throw new Error("保存済み操作物の読込が安定しません。生成せず再試行してください。");
}
