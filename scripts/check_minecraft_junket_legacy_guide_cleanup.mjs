import assert from "node:assert/strict";
import { installLegacyGuideCleanup } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/legacy_guide_cleanup.js";

let callback, subscriptions = 0;
const queued = [];
const world = { afterEvents: { entityLoad: { subscribe(handler) { callback = handler; subscriptions++; } } } };
const system = { run(handler) { queued.push(handler); } };
const definitions = ["pig", "silence"].map((id, i) => ({ id, installation: "native-preinstalled",
  provider: i ? { collection: "junket:silence_fixture" } : undefined,
  fixtures: ["reception", "return", "guard"].map(role => ({ role, type: "junket:guide", location: [100 + i * 100, 68, 100] })) }));
function actor(id = "pig", role = "reception", typeId = "minecraft:npc") {
  const venue = definitions.find(value => value.id === id);
  const tags = new Set([venue.provider?.collection ?? `junket:${id}:managed`, `junket:${id}:fixture:${role}`]);
  const props = new Map([["junket:venue_id", id], ["junket:venue_role", role]]);
  return { id: `actor-${++actor.sequence}`, typeId, dimension: { id: "minecraft:overworld" }, isValid: true,
    location: { x: venue.fixtures[0].location[0], y: 68, z: 100 }, tags, props, removed: 0,
    hasTag: tag => tags.has(tag), getDynamicProperty: key => props.get(key),
    remove() { this.removed++; }, getComponent() { throw new Error("migration must not touch health"); } };
}
actor.sequence = 0;
const installed = installLegacyGuideCleanup({ world, system, definitions });
assert.equal(installLegacyGuideCleanup({ world, system, definitions }), installed);
assert.equal(subscriptions, 1);
for (const id of ["pig", "silence"]) for (const role of ["reception", "return", "guard"]) {
  const owned = actor(id, role);
  callback({ entity: owned }); callback({ entity: owned });
  assert.equal(owned.removed, 0, "entityLoad cleanup is deferred");
  assert.equal(queued.length, 1, "duplicate load events enqueue one removal");
  queued.shift()();
  assert.equal(owned.removed, 1);
}
for (const change of [
  e => e.tags.clear(), e => e.props.delete("junket:venue_id"),
  e => e.props.set("junket:venue_id", "other"), e => e.props.set("junket:venue_role", "other"),
  e => e.tags.delete("junket:pig:managed"), e => e.tags.delete("junket:pig:fixture:reception"),
  e => { e.location.x += .2; }, e => { e.typeId = "junket:guide"; },
  e => { e.dimension.id = "minecraft:nether"; }, e => { e.isValid = false; },
]) {
  const foreign = actor(); change(foreign); callback({ entity: foreign });
  assert.equal(queued.length, 0);
  assert.equal(foreign.removed, 0);
}
for (const change of [e => { e.location.y += 5; }, e => e.props.set("junket:venue_id", "other"), e => { e.isValid = false; }]) {
  const changed = actor(); callback({ entity: changed }); change(changed); queued.shift()();
  assert.equal(changed.removed, 0, "all eligibility must still hold after deferral");
}
definitions[0].fixtures[0].type = "minecraft:npc";
const notReplaced = actor(); callback({ entity: notReplaced });
assert.equal(queued.length, 0, "manifest must explicitly replace this role with a protected guide");
console.log("PASS legacy entityLoad migration: exact owner/role/type/tags/location only, deferred recheck, no health mutation, foreign NPC/guide preserved (offline)");
