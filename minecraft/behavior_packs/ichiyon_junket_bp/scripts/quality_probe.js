// Explicit, headless BDS verification. Never runs automatically or joins a
// persistent match. The operator invokes it only after a backed-up release.
import { makeQualityCase } from "./quality_cases.js";
import { permanentFixtureLabel } from "./venue_presentation.js";
import { fixtureHealthRecoveryCount, fixtureUsesGameplayHealth } from "./fixture_health.js";
import { runSpecialRuleTraces } from "./rules_special_probe.js";
import { runRemainingRuleTrace } from "./rules_remaining_probe.js";

const point = value => Array.isArray(value) ? { x: value[0], y: value[1], z: value[2] } : value;
const valid = entity => typeof entity?.isValid === "function" ? entity.isValid() : entity?.isValid !== false;

export function createQualityProbe({ world, system, live, definitions }) {
  let running = null;
  const loggedDamage = new Set();
  world.afterEvents?.entityHurt?.subscribe(event => {
    try {
      const entity = event.hurtEntity, id = entity.getDynamicProperty("junket:venue_id");
      const venue = definitions.find(value => value.id === id);
      const expected = venue?.fixtures.find(value => entity.hasTag(`junket:${id}:fixture:${value.role}`));
      if (!expected || expected.type.startsWith("minecraft:") || fixtureUsesGameplayHealth(venue, expected)) return;
      const cause = event.damageSource?.cause, key = `${id}/${expected.role}/${cause}`;
      if (loggedDamage.has(key)) return;
      loggedDamage.add(key);
      console.warn(`[JUNKET] fixed fixture damage: ${JSON.stringify({ id, role: expected.role,
        type: entity.typeId, entity: entity.id, cause, damage: event.damage,
        health: entity.getComponent("minecraft:health")?.currentValue, location: entity.location })}`);
    } catch { /* Diagnostic reads must not alter the entity damage lifecycle. */ }
  });
  const wait = ticks => new Promise(resolve => system.runTimeout(resolve, ticks));
  const guard = () => {
    if (world.getAllPlayers().length || live.manager.tables.size) {
      throw new Error("Quality check requires an empty server and no saved active matches.");
    }
  };
  function snapshot(venue, baseline = null) {
    const healthRecoveries = fixtureHealthRecoveryCount(venue.id);
    if (baseline && healthRecoveries !== baseline.healthRecoveries) {
      throw new Error(`${venue.id}: fixture health needed recovery during lifecycle (${baseline.healthRecoveries} -> ${healthRecoveries})`);
    }
    const dimension = world.getDimension("minecraft:overworld");
    const center = { x: venue.origin[0] + venue.size[0] / 2,
      y: venue.origin[1] + venue.size[1] / 2, z: venue.origin[2] + venue.size[2] / 2 };
    const entities = dimension.getEntities({ location: center, maxDistance: Math.hypot(...venue.size) / 2 + 8 });
    const gameIds = new Set(definitions.map(value => value.id));
    for (const entity of entities) {
      const at = entity.location;
      if (![at.x, at.y, at.z].every((value, axis) => value >= venue.origin[axis] - 1 &&
          value <= venue.origin[axis] + venue.size[axis] + 1)) continue;
      const owner = entity.getDynamicProperty("junket:venue_id");
      const foreignTag = entity.getTags?.().map(tag => /^junket:([a-z]+):fixture:/.exec(tag)?.[1])
        .find(id => gameIds.has(id) && id !== venue.id);
      if ((gameIds.has(owner) && owner !== venue.id) || foreignTag) {
        throw new Error(`${venue.id}: foreign fixture ${entity.id} owner=${owner ?? foreignTag} type=${entity.typeId} at=${JSON.stringify(at)}`);
      }
    }
    const ids = {}, health = {}, labels = [], fixtures = [];
    for (const expected of venue.fixtures) {
      const roleTag = `junket:${venue.id}:fixture:${expected.role}`;
      const matching = entities.filter(entity => valid(entity) && entity.hasTag(roleTag) &&
        (entity.hasTag(`junket:${venue.id}:managed`) || entity.hasTag(venue.provider?.collection ?? `junket:${venue.id}_fixture`) ||
         entity.getDynamicProperty("junket:venue_id") === venue.id));
      if (matching.length !== 1 || matching[0].typeId !== expected.type) {
        throw new Error(`${venue.id}/${expected.role}: expected one ${expected.type}, got ${matching.map(e => e.typeId).join(",")}`);
      }
      const entity = matching[0];
      ids[expected.role] = entity.id;
      if (baseline && baseline.ids[expected.role] !== entity.id) throw new Error(`${venue.id}/${expected.role}: fixture was replaced during lifecycle (${baseline.ids[expected.role]} -> ${entity.id}, ${entity.typeId})`);
      const hp = entity.getComponent("minecraft:health")?.currentValue;
      if (typeof hp === "number") {
        health[expected.role] = hp;
        if (hp <= 0 || (baseline?.health[expected.role] !== undefined && hp < baseline.health[expected.role])) {
          throw new Error(`${venue.id}/${expected.role}: fixture lost health during lifecycle (current=${hp}, baseline=${baseline?.health[expected.role] ?? "initial"}, entity=${entity.id}, type=${entity.typeId})`);
        }
      }
      const expectedLabel = permanentFixtureLabel(venue, expected);
      if (entity.nameTag !== expectedLabel) throw new Error(`${venue.id}/${expected.role}: unexpected persistent label ${entity.nameTag}`);
      if (entity.nameTag) labels.push({ role: expected.role, text: entity.nameTag, location: point(entity.location) });
      fixtures.push(entity);
    }
    if (labels.length > 2 || !labels.some(value => value.role === "reception") || !labels.some(value => value.role === "return")) {
      throw new Error(`${venue.id}: navigation labels are missing or crowded`);
    }
    return { ids, health, labels, fixtures, healthRecoveries };
  }
  async function run(requestId) {
    const checks = [], failures = [];
    try { guard(); }
    catch (error) { failures.push({ id: "precondition", message: error.message }); }
    if (!failures.length) live.manager.maintenance = requestId;
    for (const venue of failures.length ? [] : definitions) {
      const check = { id: venue.id, fixture_count: venue.fixtures.length, stages: [],
        identities_preserved: false, health_preserved: false, labels_valid: false, sentinel_preserved: false, cleaned: false };
      let sentinel = null;
      try {
        guard();
        const specialTrace = runSpecialRuleTraces({ only: venue.id });
        check.rule_progression = specialTrace[0] ?? runRemainingRuleTrace(venue.id);
        await wait(1);
        await live.venues.repair(venue.id);
        await live.venues.withLoaded(venue.id, async context => {
          guard(); context.guard();
          const sample = makeQualityCase(venue.id, requestId);
          const special = live.specialVenues[venue.id];
          const ensure = () => special ? special.ensure() : live.venues.ensure(venue.id);
          const start = table => special ? special.start(table) : live.venues.start(venue.id, table);
          const update = table => special ? special.update?.(table) : live.venues.update(venue.id, table);
          const end = table => special ? special.end(table) : live.venues.end(venue.id, table);
          const reset = async () => {
            await end(sample.advanced);
            if (!special) live.venues.update(venue.id, null);
            await ensure();
          };
          const baseline = snapshot(venue);
          sentinel = world.getDimension("minecraft:overworld").spawnEntity("minecraft:armor_stand", point(venue.destination));
          sentinel.addTag("junket:quality_probe");
          // A foreign actor carrying a role-like tag is not a managed fixture.
          sentinel.addTag(`junket:${venue.id}:fixture:quality_sentinel`);
          const sentinelId = sentinel.id;
          try {
            const stages = [
              ["entry", async () => {}],
              ["reception", ensure],
              ["start", () => start(sample.initial)],
              ["transition", () => venue.id === "sparrow" && special?.transition
                ? special.transition(sample.initial, sample.advanced) : update(sample.advanced)],
              // End uses the real manual-exit path. It does not fake a winner
              // or claim a completed match from a one-action rule sample.
              ["finished", () => end(sample.advanced)],
              ["rematch", () => start(sample.initial)],
              ["exit", reset],
              ["reentry", ensure],
              ["reinitialize", async () => { await ensure(); await wait(3); await ensure(); }],
            ];
            for (const [name, action] of stages) {
              guard(); context.guard();
              await action();
              await wait(20);
              guard(); context.guard();
              const current = snapshot(venue, baseline);
              if (!valid(sentinel) || sentinel.id !== sentinelId) throw new Error(`${venue.id}: unrelated sentinel was removed`);
              check.stages.push({ name, ok: true, fixtures: current.fixtures.length, permanent_labels: current.labels.length });
            }
            check.identities_preserved = true;
            check.health_preserved = true;
            check.labels_valid = true;
            check.sentinel_preserved = true;
            check.navigation = snapshot(venue).labels;
            check.health_recoveries_before_test = baseline.healthRecoveries;
            check.health_recoveries_during_test = snapshot(venue).healthRecoveries - baseline.healthRecoveries;
            check.rule_action = sample.action;
            check.finished_mode = "manual-end";
          } finally {
            await reset();
            await wait(3);
            const restored = snapshot(venue, baseline);
            for (const [index, entity] of restored.fixtures.entries()) {
              const expected = venue.fixtures[index], at = point(expected.location);
              if (Math.hypot(entity.location.x - at.x, entity.location.y - at.y, entity.location.z - at.z) > .75) {
                throw new Error(`${venue.id}/${expected.role}: idle location was not restored`);
              }
              const yaw = entity.getRotation?.().y;
              if (Number.isFinite(expected.rotation) && Number.isFinite(yaw) &&
                  Math.abs(((yaw - expected.rotation) % 360 + 540) % 360 - 180) > .1) {
                throw new Error(`${venue.id}/${expected.role}: idle rotation differs from manifest`);
              }
            }
            if (sentinel && valid(sentinel)) sentinel.remove();
            sentinel = null;
            check.cleaned = true;
          }
        });
      } catch (error) {
        check.message = error?.message ?? String(error);
        failures.push({ id: venue.id, message: check.message });
        if (sentinel && valid(sentinel)) { sentinel.remove(); check.cleaned = true; }
      }
      checks.push(check);
      console.log(`[JUNKET QUALITY] progress: ${JSON.stringify(check)}`);
      if (world.getAllPlayers().length || live.manager.tables.size) break;
    }
    const report = { schema: 1, rules_schema: 1, request_id: requestId, ok: checks.length === 17 && failures.length === 0,
      checks, failures, player_test: false, full_match: false, synthetic_seats: true };
    console.log(`[JUNKET QUALITY] proof: ${JSON.stringify(report)}`);
    return report;
  }
  return { run(requestId) {
    if (running) return running;
    running = run(requestId).finally(() => {
      if (live.manager.maintenance === requestId) live.manager.maintenance = null;
      running = null;
    });
    return running;
  } };
}
