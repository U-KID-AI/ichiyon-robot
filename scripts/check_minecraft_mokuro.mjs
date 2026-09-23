import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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
      velocity: { x: 0, y: 0, z: 0 }, yaw: 0, leashed: false, held: undefined, mobile: true,
      getComponent(name) {
        if (name === "minecraft:health") return { currentValue: this.health };
        if (name === "minecraft:inventory") return { container: { getItem: () => this.held } };
        if (name === "minecraft:equippable") return { getEquipment: () => this.chest };
        if (name === "minecraft:leashable") return { isLeashed: this.leashed };
        if (name === "minecraft:riding") return this.riding;
        if (name === "minecraft:navigation.walk") return this.mobile ? {} : undefined;
        if (name === "minecraft:movement") return { currentValue: this.mobile ? 0.22 : 0 };
      },
      getDynamicProperty: k => dynamic[k], setDynamicProperty: (k, v) => { if (v === undefined) delete dynamic[k]; else dynamic[k] = v; },
      getProperty: k => props[k], setProperty: (k, v) => { props[k] = v; },
      triggerEvent(name) {
        if (this.eventError) throw Error("event failure");
        events.push(name);
        props["ichiyon:carried"] = name.endsWith("_attach");
        this.mobile = !props["ichiyon:carried"];
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
await test("held normal items can mount; special interaction items pass through", async () => {
  const { core, p, mob } = fixture();
  p.held = { typeId: "minecraft:stone" };
  await core.menu(p, mob);
  assert.equal(core.owners.size, 1);
  core.releasePlayer(p.id);

  for (const typeId of ["minecraft:lead", "minecraft:name_tag", "ichiyon:bartholomew_kuma_paw"]) {
    p.held = { typeId };
    const e = { player: p, target: mob };
    core.interact(e);
    assert(!e.cancel, typeId);
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
await test("missing spawn/load AI repaired without changing HP/position, once only", () => {
  const { core, mob } = fixture(); mob.mobile = false; mob.health = 9;
  const position = { ...mob.location };
  core.recover(mob); core.scan(); core.recover(mob);
  assert.equal(mob.mobile, true); assert.equal(mob.health, 9);
  assert.deepEqual(mob.location, position);
  assert.deepEqual(mob.events, ["ichiyon:mokuro_detach"]);
});
await test("healthy normal/leashed path never reset; attached never repaired into walking", () => {
  const { core, p, mob } = fixture(); mob.leashed = true;
  core.scan(); core.scan(); assert.equal(mob.events.length, 0);
  mob.leashed = false;
  for (const mode of ["head", "back"]) {
    core.attach(p, mob, mode); core.scan(); core.recover(mob);
    assert.equal(mob.mobile, false);
    core.releasePlayer(p.id); assert.equal(mob.mobile, true);
  }
});
await test("failed normal AI repair retries, unloaded/dead/unrelated are untouched", () => {
  const { core, mob, entity } = fixture(); mob.mobile = false; mob.eventError = true;
  core.scan(); assert.equal(mob.mobile, false);
  mob.eventError = false; core.scan(); assert.equal(mob.mobile, true);
  for (const attr of ["health", "isValid"]) {
    const e = entity(MOKURO); e.mobile = false; e[attr] = 0; core.recover(e);
    assert.equal(e.events.length, 0);
  }
  const other = entity("minecraft:cow"); other.mobile = false; core.recover(other);
  assert.equal(other.events.length, 0);
});

// Execute the controller's actual expressions with deterministic query inputs.
// This covers transitions/timers, not the client's rendering/Molang implementation.
const controller = JSON.parse(readFileSync(new URL("../minecraft/resource_packs/ichiyon_avatar_rp/animation_controllers/mokuro.controller.json", import.meta.url)));
function animationFixture() {
  const { initial_state, states } = controller.animation_controllers["controller.animation.mokuro.state"];
  const props = { "ichiyon:carried": false, "ichiyon:gliding": false };
  const query = { state_time: 0, ground_speed: 0, is_on_ground: true, property: k => props[k] };
  const variable = {};
  const math = { random: (min, max) => { assert.equal(min, 4); assert.equal(max, 8); return 6; } };
  let state;
  const execute = (expression, statement = false) => Function("query", "variable", "math", statement ? expression : `return (${expression});`)(query, variable, math);
  function enter(next) {
    state = next; query.state_time = 0;
    for (const expression of states[state].on_entry ?? []) execute(expression, true);
  }
  enter(initial_state);
  return { props, query, get state() { return state; },
    step(dt = 0) {
      query.state_time += dt;
      for (const transition of states[state].transitions ?? []) {
        const [next, expression] = Object.entries(transition)[0];
        if (execute(expression)) { enter(next); break; }
      }
      return state;
    },
  };
}
await test("idle waits 4-8 seconds, flap lasts two unmodified 0.5s cycles then cooldown", () => {
  const a = animationFixture(); assert.equal(a.step(5.9), "idle");
  assert.equal(a.step(.1), "flap"); assert.equal(a.step(.99), "flap");
  assert.equal(a.step(.01), "idle"); assert.equal(a.step(5.9), "idle");
  assert.equal(a.step(.1), "flap");
});
await test("actual ground movement starts walk and interrupts flap; airborne not walking", () => {
  const a = animationFixture(); a.query.ground_speed = .2; assert.equal(a.step(), "walk");
  a.query.ground_speed = 0; assert.equal(a.step(), "idle"); assert.equal(a.step(6), "flap");
  a.query.ground_speed = .2; assert.equal(a.step(), "walk");
  a.query.is_on_ground = false; assert.equal(a.step(), "idle"); assert.equal(a.step(20), "idle");
});
await test("attachment overrides idle/walk/flap, glide overrides carried, detach resumes", () => {
  for (const initial of ["idle", "walk", "flap"]) {
    const a = animationFixture();
    if (initial === "walk") { a.query.ground_speed = 1; a.step(); }
    if (initial === "flap") a.step(6);
    assert.equal(a.state, initial);
    a.props["ichiyon:carried"] = true; assert.equal(a.step(), "carried");
    assert.equal(a.step(100), "carried");
    a.props["ichiyon:gliding"] = true; assert.equal(a.step(), "glide");
    a.props["ichiyon:gliding"] = false; assert.equal(a.step(), "idle"); assert.equal(a.step(), "carried");
    a.props["ichiyon:carried"] = false; assert.equal(a.step(), "idle");
    a.query.ground_speed = 1; assert.equal(a.step(), "walk");
  }
});
await test("ground head jump boosts once, rolls up, auto glides at apex", () => {
  const {core,p,mob,system}=fixture();core.attach(p,mob,"head");core.tick();core.jump(p);
  assert.equal(p.impulses.at(-1).y,1.15);assert(mob.props["ichiyon:boosting"]);
  core.jump(p);assert.equal(p.impulses.length,1);
  p.isOnGround=false;p.velocity.y=.8;system.currentTick++;core.tick();assert(mob.props["ichiyon:boosting"]);
  p.velocity.y=-.01;core.tick();assert(!mob.props["ichiyon:boosting"]);assert(mob.props["ichiyon:gliding"]);
  p.isOnGround=true;core.tick();assert(!mob.props["ichiyon:gliding"]);
});
await test("back/Elytra/water never boost; release clears roll", () => {
  for(const condition of ['back','elytra','water']){
    const {core,p,mob}=fixture();core.attach(p,mob,condition==='back'?'back':'head');
    if(condition==='elytra')p.chest={typeId:'minecraft:elytra'};
    if(condition==='water')p.isInWater=true;
    core.jump(p);assert.equal(p.impulses.length,0);
  }
  const {core,p,mob}=fixture();core.attach(p,mob,'head');core.jump(p);core.releasePlayer(p.id);
  assert.equal(mob.props['ichiyon:boosting'],false);
});
await test("roll uses original clip then blends into open wings", () => {
  const a=animationFixture();a.props['ichiyon:carried']=true;a.step();
  a.props['ichiyon:boosting']=true;assert.equal(a.step(),'roll');
  a.props['ichiyon:boosting']=false;a.props['ichiyon:gliding']=true;assert.equal(a.step(),'glide');
});
console.log(`${count} Mokuro runtime checks passed`);
