import assert from "node:assert/strict";
import { createMokuro, attachmentPose, glideImpulse, MOKURO, OWNER, RETURN } from "../minecraft/behavior_packs/import_structures/scripts/mokuro_core.js";

let count = 0;
function test(name, fn) {
  return Promise.resolve().then(fn).then(() => { count++; console.log(`OK ${name}`); });
}
function fixture() {
  const all = [];
  const scans = [];
  const dimensions = Object.fromEntries(["overworld", "nether", "the_end"].map(id => [id, {
    id, getEntities: (q) => { scans.push(q); return all.filter(e => e.dimension.id === id && e.typeId === q.type && e.isValid); },
  }]));
  const world = { getDimension: id => dimensions[id] };
  let next = 1;
  function entity(typeId, loc = { x: 1, y: 64, z: 1 }) {
    const props = {}, dynamic = {}, events = [], impulses = [];
    const e = { typeId, id: String(next++), isValid: true, health: 20, location: { ...loc }, dimension: dimensions.overworld,
      isOnGround: true, isSneaking: false, selectedSlotIndex: 0, messages: [], events, impulses, props, dynamic,
      velocity: { x: 0, y: 0, z: 0 }, yaw: 0, leashed: false, held: undefined,
      getComponent(name) {
        if (name === "minecraft:health") return { currentValue: this.health };
        if (name === "minecraft:inventory") return { container: { getItem: () => this.held } };
        if (name === "minecraft:equippable") return { getEquipment: () => this.chest };
        if (name === "minecraft:leashable") return { isLeashed: this.leashed };
        if (name === "minecraft:riding") return this.riding;
      },
      getDynamicProperty: k => dynamic[k], setDynamicProperty: (k, v) => { if (v === undefined) delete dynamic[k]; else dynamic[k] = v; },
      getProperty: k => props[k], setProperty: (k, v) => { props[k] = v; },
      triggerEvent(name) {
        if (this.eventError) throw Error("event failure");
        events.push(name);
        props["ichiyon:carried"] = name.endsWith("_attach");
        props["ichiyon:gliding"] = false;
      },
      teleport(p, options) { if (this.teleportError) throw Error("teleport failed"); this.location = { ...p }; this.rotation = options.rotation; },
      tryTeleport(p, options) { this.location = { ...p }; this.dimension = options.dimension; return true; },
      getRotation() { return { x: 0, y: this.yaw }; },
      getHeadLocation() { return { ...this.location, y: this.location.y + 1.62 }; },
      getVelocity() { return this.velocity; },
      getViewDirection() { return { x: 0, y: -0.1, z: 1 }; },
      applyImpulse(v) { impulses.push(v); }, sendMessage(v) { this.messages.push(v); },
    };
    all.push(e);
    return e;
  }
  const p = entity("minecraft:player"), mob = entity(MOKURO);
  const system = { currentTick: 1, run: fn => fn() };
  const formState = { selection: 0 };
  class Form {
    title() { return this; } body() { return this; } button() { return this; }
    async show() { return formState; }
  }
  const deps = { world, system, ActionFormData: Form, report: () => {} };
  const core = createMokuro(deps);
  const glide = () => {
    core.attach(p, mob, "head"); p.isOnGround = false; p.velocity.y = -0.2; core.jump(p); core.tick();
  };
  return { core, p, mob, entity, system, deps, dimensions, formState, scans, glide };
}

await test("same entity, HP, owner, head yaw; AI restored on detach", () => {
  const { core, p, mob } = fixture(); mob.health = 9; p.yaw = 75;
  assert(core.attach(p, mob, "head")); assert.equal(mob.health, 9);
  assert.equal(mob.rotation.y, 75); assert.equal(mob.dynamic[OWNER], p.id);
  core.releasePlayer(p.id); assert.equal(mob.health, 9); assert.equal(mob.dynamic[OWNER], undefined);
  assert(mob.events.includes("ichiyon:mokuro_detach")); assert.equal(core.owners.size, 0);
});
await test("back offset and 180-degree reverse for four headings", () => {
  const { p } = fixture();
  for (const yaw of [0, 90, 180, -90]) {
    p.yaw = yaw; const pose = attachmentPose(p, "back");
    assert.equal(((pose.rotation.y - yaw) % 360 + 360) % 360, 180);
    assert(Math.abs(Math.hypot(pose.location.x-p.location.x, pose.location.z-p.location.z)-0.55) < 1e-9);
  }
});
await test("head only glide / wings / impulse / landing ends", () => {
  const { core, p, mob, glide } = fixture(); glide();
  assert.equal(mob.props["ichiyon:gliding"], true); assert.equal(p.impulses.length, 1);
  p.isOnGround = true; core.tick(); assert.equal(mob.props["ichiyon:gliding"], false);
});
await test("back cannot glide", () => {
  const { core, p, mob } = fixture(); core.attach(p, mob, "back"); p.isOnGround = false; p.velocity.y = -1;
  core.jump(p); core.tick(); assert.equal(p.impulses.length, 0);
});
await test("real Elytra, creative flight, water, climbing and riding excluded", () => {
  for (const flag of ["isGliding", "isFlying", "isInWater", "isSwimming", "isClimbing", "isSleeping", "riding", "chest"]) {
    const { core, p, mob } = fixture(); core.attach(p, mob, "head");
    p[flag] = flag === "chest" ? { typeId: "minecraft:elytra" } : true;
    p.isOnGround = false; p.velocity.y = -1; core.jump(p); core.tick(); assert.equal(p.impulses.length, 0, flag);
  }
});
await test("inertial bounded impulse and no upwards powered flight", () => {
  for (const y of [-1, 0, 1]) {
    const v = glideImpulse({ x: 2, y: -0.05, z: -1 }, { x: 0, y, z: 1 });
    assert(Math.abs(v.x) <= .04 && Math.abs(v.z) <= .04 && Math.abs(v.y) <= .12);
    assert(v.y <= 0);
  }
});
await test("fall protection limited to active head glide; never other damage", () => {
  const { core, p, glide } = fixture(); glide();
  const fall = { hurtEntity: p, damageSource: { cause: "fall" } }; core.fall(fall); assert(fall.cancel);
  const hit = { hurtEntity: p, damageSource: { cause: "entityAttack" } }; core.fall(hit); assert(!hit.cancel);
  core.releasePlayer(p.id); const again = { ...fall, cancel: false }; core.fall(again); assert(!again.cancel);
});
await test("sneak cancels glide immediately on next tick", () => {
  const { core, p, mob, glide } = fixture(); glide(); p.isSneaking = true; core.tick();
  assert.equal(mob.props["ichiyon:gliding"], false);
});
await test("leashed Mob and occupied slot cannot be stolen", () => {
  const { core, p, mob, entity } = fixture(); mob.leashed = true; assert(!core.attach(p, mob, "head"));
  mob.leashed = false; assert(core.attach(p, mob, "head"));
  assert(!core.attach(entity("minecraft:player"), mob, "back")); assert(!core.attach(p, entity(MOKURO), "back"));
});
await test("logout, player death, dimension and long teleport restore same Mob", () => {
  for (const cause of ["logout", "death", "dimension", "teleport"]) {
    const { core, p, mob, dimensions } = fixture(); core.attach(p, mob, "head");
    if (cause === "logout") core.releasePlayer(p.id);
    if (cause === "death") { p.health = 0; core.died(p); }
    if (cause === "dimension") p.dimension = dimensions.nether;
    if (cause === "teleport") p.location.x += 100;
    core.tick(); assert.equal(core.owners.size, 0, cause); assert.equal(mob.dynamic[OWNER], undefined, cause);
    assert.equal(mob.props["ichiyon:carried"], false, cause);
  }
});
await test("Mob death clears state without replacement or revival", () => {
  const { core, p, mob, glide } = fixture(); glide(); mob.health = 0; core.died(mob); core.tick();
  assert.equal(core.owners.size, 0); assert.equal(mob.health, 0); assert.equal(p.impulses.length, 1);
});
await test("reload/restart load recovery from persistent owner and carried flag", () => {
  for (const eraseOwner of [false, true]) {
    const { core, p, mob, deps } = fixture(); core.attach(p, mob, "head");
    if (eraseOwner) mob.setDynamicProperty(OWNER, undefined);
    const reloaded = createMokuro(deps); reloaded.recover(mob);
    assert.equal(mob.props["ichiyon:carried"], false); assert.equal(mob.dynamic[RETURN], undefined);
  }
});
await test("unloaded entity is recovered when its chunk loads again", () => {
  const { core, p, mob } = fixture(); core.attach(p, mob, "head"); mob.isValid = false; core.tick();
  assert.equal(core.owners.size, 0); assert(mob.dynamic[OWNER]);
  mob.isValid = true; core.recover(mob); assert.equal(mob.dynamic[OWNER], undefined);
});
await test("AI event failure retains recovery marker and retries", () => {
  const { core, p, mob } = fixture(); core.attach(p, mob, "head"); mob.eventError = true;
  assert.throws(() => core.releasePlayer(p.id)); assert(mob.dynamic[OWNER]);
  mob.eventError = false; core.releasePlayer(p.id); assert.equal(mob.dynamic[OWNER], undefined);
});
await test("attach teleport failure unwinds AI without modifying HP", () => {
  const { core, p, mob } = fixture(); mob.teleportError = true;
  assert.throws(() => core.attach(p, mob, "head")); assert.equal(core.owners.size, 0);
  assert.equal(mob.props["ichiyon:carried"], false); assert.equal(mob.health, 20);
});
await test("ActionForm head / back / cancel, and owner detach", async () => {
  for (const selection of [0, 1, 2]) {
    const { core, p, mob, formState } = fixture(); formState.selection = selection;
    await core.menu(p, mob); assert.equal(core.owners.size, selection === 2 ? 0 : 1);
    if (selection !== 2) { formState.selection = 0; await core.menu(p, mob); assert.equal(core.owners.size, 0); }
  }
});
await test("grounded sneak jump offers detach, held items are left alone", async () => {
  const { core, p, mob } = fixture(); core.attach(p, mob, "head"); p.isSneaking = true;
  core.jump(p); await Promise.resolve(); assert.equal(core.owners.size, 0);
  p.held = { typeId: "minecraft:lead" }; const e = { player: p, target: mob }; core.interact(e); assert(!e.cancel);
});
await test("scan is only loaded Mokuro, no mutation of unrelated Mobs", () => {
  const { core, entity, scans } = fixture(); const other = entity("minecraft:cow"); core.scan();
  assert.deepEqual(scans, [{ type: MOKURO }, { type: MOKURO }, { type: MOKURO }]); assert.equal(other.events.length, 0);
});
console.log(`${count} Mokuro runtime checks passed`);
