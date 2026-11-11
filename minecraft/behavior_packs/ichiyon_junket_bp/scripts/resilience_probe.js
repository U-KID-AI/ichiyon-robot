// Explicit destructive server verification, never an automatic player test.
// Requires an empty server; the release runner has already made a full backup.
import { fixtureUsesGameplayHealth } from "./fixture_health.js";
import { inspectLegacyServiceControls } from "./service_control_install.js";
import { serviceControlBinding } from "./service_controls.js";

const point = v => Array.isArray(v) ? { x: v[0], y: v[1], z: v[2] } : v;
const gap = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
const valid = e => typeof e?.isValid === "function" ? e.isValid() : e?.isValid !== false;
const signature = block => JSON.stringify([block.typeId, block.permutation.getAllStates()]);
const MARKER = "junket:resilience_test_v1";

// Blocks can be readable before a reloaded chunk's actors join the dimension
// query. This performs no repair, spawn, movement, healing or tag mutation.
// Only an explicitly missing actor is retried; every observed defect fails.
export async function readStableFixtureSnapshot({ read, wait, guard = () => {}, record = {}, timeoutTicks = 100 }) {
  Object.assign(record, { waited_ticks: 0, attempts: 0, settled: false, first_missing: null });
  const seen = new Map();
  let previous = null;
  const observe = identities => {
    for (const [role, id] of Object.entries(identities)) {
      if (seen.has(role) && seen.get(role) !== id) throw new Error(`${role}: fixture identity changed while waiting for actor readiness`);
      seen.set(role, id);
    }
  };
  while (true) {
    guard(); record.attempts++;
    try {
      const rows = read();
      const current = Object.fromEntries(rows.map(row => [row.expected.role, row.id]));
      observe(current);
      if (previous !== null && JSON.stringify(current) === previous) {
        record.settled = true;
        return rows;
      }
      previous = JSON.stringify(current);
    } catch (error) {
      record.last_error = String(error);
      if (!Array.isArray(error?.missingFixtureActors)) throw error;
      observe(error.loadedFixtureIdentities ?? {});
      if (record.first_missing === null) record.first_missing = error.missingFixtureActors;
      record.last_missing = error.missingFixtureActors;
      previous = null;
    }
    if (record.waited_ticks >= timeoutTicks) throw new Error(`Fixture actor readiness timed out: ${record.last_error ?? "unstable actor queries"}`);
    const ticks = Math.min(2, timeoutTicks - record.waited_ticks);
    await wait(ticks); record.waited_ticks += ticks;
  }
}

export function createResilienceProbe({ world, system, live, definitions }) {
  let pending;
  const dimension = () => world.getDimension("minecraft:overworld");
  const wait = ticks => new Promise(resolve => system.runTimeout(resolve, ticks));
  const guard = () => {
    if (world.getAllPlayers().length || live.manager.tables.size)
      throw new Error("Resilience check requires no players and no active/saved matches.");
  };
  function snapshot(venue) {
    const collection = venue.provider?.collection ?? `junket:${venue.id}:managed`;
    const candidates = dimension().getEntities({ tags: [collection] });
    const rows = [], missing = [];
    for (const expected of venue.fixtures) {
      const found = candidates.filter(e => e.hasTag(`junket:${venue.id}:fixture:${expected.role}`));
      if (found.length === 0) { missing.push(expected); continue; }
      if (found.length !== 1 || found[0].typeId !== expected.type)
        throw new Error(`${venue.id}/${expected.role}: expected exactly one ${expected.type}, found ${found.length}`);
      const entity = found[0], location = { ...entity.location }, velocity = entity.getVelocity();
      if (gap(location, point(expected.location)) > .15)
        throw new Error(`${venue.id}/${expected.role}: displaced ${JSON.stringify(location)}`);
      const health = entity.getComponent("minecraft:health")?.currentValue;
      if (health !== undefined && health <= 0) throw new Error(`${venue.id}/${expected.role}: dead fixture`);
      const yaw = entity.getRotation().y;
      if (expected.rotation !== undefined && Math.abs(((yaw - expected.rotation) % 360 + 540) % 360 - 180) > .1)
        throw new Error(`${venue.id}/${expected.role}: wrong rotation ${yaw}`);
      if (gap(velocity, { x: 0, y: 0, z: 0 }) > .05)
        throw new Error(`${venue.id}/${expected.role}: residual velocity`);
      rows.push({ expected, entity, id: entity.id, location, velocity, health });
    }
    if (missing.length) {
      const error = new Error(`${venue.id}/${missing[0].role}: expected exactly one ${missing[0].type}, found 0`);
      error.loadedFixtureIdentities = Object.fromEntries(rows.map(row => [row.expected.role, row.id]));
      error.missingFixtureActors = missing.map(expected => {
        const local = dimension().getEntities({ tags: [collection, `junket:${venue.id}:fixture:${expected.role}`],
          location: point(expected.location), maxDistance: .75 });
        return { role: expected.role, expected_type: expected.type, expected_location: expected.location,
          global_collection_count: candidates.length, global_role_count: 0,
          local_query: local.map(entity => ({ id: entity.id, type: entity.typeId, location: entity.location })) };
      });
      throw error;
    }
    return rows;
  }
  async function readySnapshot(venue, context, check, phase) {
    check.actor_readiness ??= {};
    const record = check.actor_readiness[phase] = {};
    try {
      return await readStableFixtureSnapshot({ read: () => snapshot(venue), wait: context.wait,
        guard: () => { guard(); context.guard(); }, record });
    } finally {
      if (record.first_missing) console.log(`[JUNKET RESILIENCE] actor readiness: ${JSON.stringify({ id: venue.id, phase, ...record })}`);
    }
  }
  function controls(venue) {
    if (venue.service_controls?.length !== 1 || venue.service_controls[0].action !== "menu")
      throw new Error(`${venue.id}: one game operation panel required`);
    const legacy = inspectLegacyServiceControls(world, venue);
    if (legacy.some(control => !["restored", "protected"].includes(control.status)))
      throw new Error(`${venue.id}: legacy service controls remain ${JSON.stringify(legacy)}`);
    return venue.service_controls.map(control => {
      const button = dimension().getBlock(point(control.button));
      const sign = dimension().getBlock(point(control.sign));
      if (!button?.typeId.endsWith("_button") || !sign?.getComponent("minecraft:sign"))
        throw new Error(`${venue.id}/${control.action}: button/sign unavailable`);
      if (!serviceControlBinding(world, venue, control)) throw new Error(`${venue.id}: panel handler is not bound`);
      if (sign.getComponent("minecraft:sign").getText() !== control.text) throw new Error(`${venue.id}: panel sign text differs`);
      const stand = point(control.stand);
      for (const cell of [stand, { ...stand, y: stand.y + 1 }]) {
        const block = dimension().getBlock(cell);
        if (!block || block.typeId === "minecraft:barrier" || block.isSolid)
          throw new Error(`${venue.id}/${control.action}: standing space obstructed by ${block?.typeId}`);
      }
      return { action: control.action, button: button.typeId, sign: sign.typeId, stand_clear: true,
        sign_text: sign.getComponent("minecraft:sign").getText(), handler_bound: true, legacy_controls: legacy };
    });
  }
  function checkAccessPaths(venue) {
    const actor = dimension().spawnEntity("minecraft:armor_stand", point(venue.destination));
    let cells = 0;
    try {
      for (const control of venue.service_controls) {
        if (!control.path?.length) throw new Error(`${venue.id}: source access path missing`);
        for (const value of control.path) {
          const at = point(value), feet = { x: Math.floor(at.x) + .5, y: at.y, z: Math.floor(at.z) + .5 };
          for (const location of [feet, { ...feet, y: feet.y + 1 }]) {
            const block = dimension().getBlock(location);
            if (!block || block.typeId === "minecraft:barrier" || block.isSolid)
              throw new Error(`${venue.id}/${control.action}: access blocked by ${block?.typeId}`);
          }
          if (!actor.tryTeleport(feet, { dimension: dimension(), checkForBlocks: true }))
            throw new Error(`${venue.id}/${control.action}: BDS actor collision check rejected ${JSON.stringify(feet)}`);
          cells++;
        }
      }
    } finally { if (valid(actor)) actor.remove(); }
    return { checked_path_cells: cells, server_actor_collision_checks: true, player_walk_test: false };
  }
  const identities = rows => Object.fromEntries(rows.map(r => [r.expected.role, r.id]));
  async function runInternal(requestId, phase = "destructive") {
    const restart = phase === "restart" || phase === "snapshot_restart";
    const snapshotOnly = phase === "snapshot" || phase === "snapshot_restart";
    const checks = [], failures = [], stored = restart ? JSON.parse(world.getDynamicProperty(MARKER) ?? "null") : null;
    guard();
    if (restart && stored?.request_id !== requestId) throw new Error("Missing pre-restart persistence checkpoint");
    if (live.manager.maintenance) throw new Error("Another maintenance operation is running");
    live.manager.maintenance = requestId;
    try {
      for (const venue of definitions) {
        const check = { id: venue.id, fixture_count: venue.fixtures.length };
        let savedBlock, sentinel;
        try {
          guard();
          // Snapshot retests observe the normal bootstrap's result. They never
          // repair or inject damage and cannot replace the destructive proof.
          if (!snapshotOnly) await live.venues.repair(venue.id);
          if (snapshotOnly) {
            await live.venues.withLoaded(venue.id, async context => {
              const rows = await readySnapshot(venue, context, check, phase);
              check.identities = identities(rows);
              check.snapshot_only = true;
              if (restart && JSON.stringify(check.identities) !== JSON.stringify(stored.identities[venue.id]))
                throw new Error(`${venue.id}: entity identities changed across BDS restart`);
              if (restart) check.restart_persistence = true;
            });
          } else if (restart) {
            await live.venues.withLoaded(venue.id, async context => {
              guard(); context.guard();
              const rows = await readySnapshot(venue, context, check, "restart");
              check.controls = controls(venue);
              check.access = checkAccessPaths(venue);
              check.identities = identities(rows);
              if (JSON.stringify(check.identities) !== JSON.stringify(stored.identities[venue.id]))
                throw new Error(`${venue.id}: entity identities changed across BDS restart`);
              check.restart_persistence = true;
            });
          } else {
            await live.venues.withLoaded(venue.id, async context => {
              guard(); context.guard();
              const before = await readySnapshot(venue, context, check, "entry");
              check.controls = controls(venue);
              check.access = checkAccessPaths(venue);
              const fixed = before.filter(row => !fixtureUsesGameplayHealth(venue, row.expected));
              for (const row of fixed) row.entity.applyDamage(7, { cause: "entityAttack" });
              await wait(5); guard(); context.guard();
              for (const row of fixed) {
                if (!valid(row.entity) || row.entity.id !== row.id || row.entity.getComponent("minecraft:health")?.currentValue !== row.health ||
                    gap(row.entity.location, row.location) > .05)
                  throw new Error(`${venue.id}/${row.expected.role}: attack changed health, identity or location`);
              }
              check.attack = { tested: fixed.length, health_identity_position_preserved: true };
              let immediateVelocity = 0;
              for (const row of fixed) {
                try {
                  row.entity.applyKnockback({ x: .1, z: .1 }, .2);
                  row.entity.applyImpulse({ x: .1, y: .3, z: .1 });
                } catch (error) {
                  throw new Error(`${venue.id}/${row.expected.role} (${row.entity.typeId}): physical force test failed: ${error}`);
                }
                immediateVelocity = Math.max(immediateVelocity, gap(row.entity.getVelocity(), { x: 0, y: 0, z: 0 }));
              }
              await wait(25); guard(); context.guard();
              const anchored = snapshot(venue);
              for (const row of anchored.filter(row => !fixtureUsesGameplayHealth(venue, row.expected))) {
                if (gap(row.velocity, { x: 0, y: 0, z: 0 }) > .05) throw new Error(`${venue.id}/${row.expected.role}: residual velocity`);
                if (row.id !== before.find(r => r.expected.role === row.expected.role).id) throw new Error("Anchor replaced a fixed actor");
              }
              check.physics = { tested: fixed.length, immediate_velocity_max: immediateVelocity,
                anchor_position_restored: true, final_velocity_zero: true };
              // Suspend the background anchor while injecting damage. The
              // explicit recovery must find the high-Y actor itself.
              live.manager.setVenueRecovery(venue.id, true);
              sentinel = dimension().spawnEntity("minecraft:armor_stand", point(venue.destination));
              sentinel.addTag("junket:resilience_probe");
              sentinel.addTag(`junket:${venue.id}:fixture:foreign_test`);
              const guide = before.find(row => row.expected.role === "reception");
              if (!guide || guide.entity.typeId === "minecraft:npc") throw new Error("Reception guide still uses creative-deletable NPC");
              guide.entity.remove();
              // Force a high-Y failure that ordinary short-radius queries miss.
              const shifted = before.find(row => row.expected.role !== "reception" && row.expected.role !== "return");
              shifted.entity.teleport({ ...shifted.location, y: shifted.location.y + 45 });
              const duplicate = dimension().spawnEntity(shifted.entity.typeId, shifted.location);
              for (const tag of shifted.entity.getTags()) duplicate.addTag(tag);
              duplicate.setDynamicProperty("junket:venue_id", venue.id);
              const support = point(venue.service_controls.find(c => c.action === "menu").support);
              const block = dimension().getBlock(support);
              savedBlock = { at: support, permutation: block.permutation, signature: signature(block) };
              block.setType("minecraft:air");
              dimension().getBlock(point(venue.service_controls[0].button)).setType("minecraft:air");
              check.damage_injected = { missing_guide: true, displaced_actor: true, duplicate_actor: true,
                original_block: support, service_button_removed: true };
            });
            // Release the shared loader before the recovery controller acquires it.
            guard();
            await live.recovery.forceRecover(venue.id, { sourceType: "Server", message: `repair ${venue.id}` });
            await live.venues.withLoaded(venue.id, async context => {
              guard(); context.guard();
              await readySnapshot(venue, context, check, "recovery"); controls(venue);
              if (signature(dimension().getBlock(savedBlock.at)) !== savedBlock.signature)
                throw new Error(`${venue.id}: original building block was not restored`);
              if (!valid(sentinel)) throw new Error(`${venue.id}: unrelated actor removed`);
              check.recovery = { deleted_restored: true, displacement_restored: true, duplicates_removed: true,
                original_block_restored: true, controls_restored: true, foreign_actor_preserved: true };
            });
            await live.recovery.forceRecover(venue.id, { sourceType: "Server", message: `repair ${venue.id}` });
            await live.venues.withLoaded(venue.id, async context => {
              guard(); context.guard();
              const rows = await readySnapshot(venue, context, check, "idempotent");
              check.identities = identities(rows);
              check.idempotent = true;
              if (!valid(sentinel)) throw new Error("Repeated recovery removed foreign actor");
              sentinel.remove(); sentinel = null;
            });
          }
          check.ok = true;
        } catch (error) {
          // QuickJS stack strings may contain only frames, without the message.
          check.ok = false; check.message = `${error}\n${error?.stack ?? ""}`;
          failures.push({ id: venue.id, message: check.message });
          // Restore the intentionally removed floor even if the new recovery
          // implementation fails. Never leave a test-induced hole behind.
          if (savedBlock) {
            try { await live.venues.withLoaded(venue.id, async () => {
              dimension().getBlock(savedBlock.at).setPermutation(savedBlock.permutation);
            }); } catch (restoreError) { check.emergency_restore_error = String(restoreError); }
          }
        } finally {
          if (sentinel && valid(sentinel)) sentinel.remove();
        }
        checks.push(check);
        console.log(`[JUNKET RESILIENCE] progress: ${JSON.stringify(check)}`);
        if (world.getAllPlayers().length || live.manager.tables.size) break;
      }
    } finally { if (live.manager.maintenance === requestId) live.manager.maintenance = null; }
    const report = { schema: 1, request_id: requestId, phase,
      scope: snapshotOnly ? "fixture_snapshot_only" : "fixture_resilience",
      ok: checks.length === 17 && failures.length === 0, checks, failures,
      player_test: false, mobile_test: false, full_match: false };
    if (report.ok && !restart) world.setDynamicProperty(MARKER, JSON.stringify({ request_id: requestId,
      identities: Object.fromEntries(checks.map(c => [c.id, c.identities])) }));
    console.log(`[JUNKET RESILIENCE] proof: ${JSON.stringify(report)}`);
    return report;
  }
  return { run(requestId, phase = "destructive") {
    if (typeof phase === "boolean") phase = phase ? "restart" : "destructive";
    if (!["destructive", "restart", "snapshot", "snapshot_restart"].includes(phase))
      return Promise.reject(new Error("Unknown resilience phase"));
    if (pending) return pending;
    pending = runInternal(requestId, phase).finally(() => { pending = null; });
    return pending;
  } };
}
