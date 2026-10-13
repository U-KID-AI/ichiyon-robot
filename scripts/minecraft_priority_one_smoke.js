// ISOLATED BDS ONLY: copy into the test bridge's scripts directory and select as
// its manifest entry. Creates a test arena; never include in a release pack.
import { world, system, ItemStack, GameMode } from "@minecraft/server";
import { spawnSimulatedPlayer } from "@minecraft/server-gametest";
import { ActionFormData } from "@minecraft/server-ui";
import { createMokuro } from "./mokuro_core.js";
import { createCosmetics } from "./cosmetics_core.js";
import { createMolcarBoost } from "./molcar_boost_core.js";

const wait = ticks => new Promise(resolve => system.runTimeout(resolve, ticks));
const mokuro = createMokuro({ world, system, ActionFormData });
const cosmetics = createCosmetics({ world, system, ItemStack, GameMode, ActionFormData, catalog: { skins: [], accessories: [] } });
const boost = createMolcarBoost({ world, system });
system.runInterval(() => { mokuro.tick(); boost.tick(); }, 1);
world.afterEvents.entitySpawn.subscribe(({ entity }) => system.run(() => mokuro.recover(entity)));
world.afterEvents.entityLoad.subscribe(({ entity }) => mokuro.recover(entity));
let passed = 0, failed = 0;
function check(value, name, data = {}) {
  console.warn(`[PriorityOne] ${value ? "PASS" : "FAIL"} ${name} ${JSON.stringify(data)}`);
  if (value) passed++; else failed++;
}
async function main() {
  const d = world.getDimension("overworld");
  d.runCommand("tickingarea add circle 0 64 0 4 priority_one");
  await wait(40);
  if (world.getDynamicProperty("priority_one:restart") === true) {
    for (const type of ["molcar", "molcar2", "molcar3"]) {
      const e = d.getEntities({ type: `ichiyon:${type}`, name: "persist-ring" })[0];
      check(e?.getProperty("ichiyon:float_ring") === 1, `restart property ${type}`);
      if (e) { await wait(40); check(e.location.y > 62 && e.location.y < 65, `restart buoyancy ${type}`, { y: e.location.y }); }
    }
    // Validate land behavior with and without equipment against the same engine.
    d.runCommand("fill -35 63 -35 0 63 -1 stone");
    for (const equipped of [0, 1]) {
      const e = d.spawnEntity("ichiyon:molcar", { x: -20, y: 64, z: -27 });
      e.setProperty("ichiyon:float_ring", equipped);
      const p = spawnSimulatedPlayer({ dimension: d, x: -21, y: 64, z: -27 }, `LandTester${equipped}`, GameMode.Survival);
      await wait(10); e.getComponent("minecraft:rideable").addRider(p); await wait(3);
      p.setRotation({ x: 0, y: -90 }); const start = { ...e.location };
      p.moveRelative(0, 1, 1); await wait(15); p.stopMoving();
      const movement = Math.hypot(e.location.x - start.x, e.location.z - start.z);
      check(movement > 3 && Math.abs(e.location.y - 64) < .1, `land drive ring=${equipped}`, { movement, location: e.location });
      p.getComponent("minecraft:inventory").container.setItem(p.selectedSlotIndex, new ItemStack("minecraft:carrot", 2));
      boost.use({ source: p, itemStack: p.getComponent("minecraft:inventory").container.getItem(p.selectedSlotIndex) });
      await wait(3); check(e.getComponent("minecraft:movement").currentValue > .64, `land boost ring=${equipped}`);
      e.getComponent("minecraft:rideable").ejectRiders(); await wait(4);
      check(e.getComponent("minecraft:movement").currentValue < .41, `dismount reset ring=${equipped}`);
      p.disconnect(); e.remove();
    }
    const zombieCar = d.spawnEntity("ichiyon:molcar", { x: -20, y: 64, z: -27 });
    zombieCar.triggerEvent("ichiyon:spawn_zombie_molcar"); await wait(5);
    check(zombieCar.getComponent("minecraft:rideable").getRiders().some(r => r.typeId === "minecraft:zombie"), "zombie rider retained after format upgrade");
    zombieCar.remove();
    console.warn(`[PriorityOne] DONE restart passed=${passed} failed=${failed}`); return;
  }
  d.runCommand("gamerule doMobSpawning false");
  d.runCommand("gamerule doWeatherCycle false");
  d.runCommand("weather clear");
  d.runCommand("fill -40 57 -40 40 57 40 stone");
  for (const x of [-40, 0]) for (const z of [-40, 0]) {
    d.runCommand(`fill ${x} 58 ${z} ${x + 40} 75 ${z + 40} air`);
    d.runCommand(`fill ${x} 58 ${z} ${x + 40} 63 ${z + 40} water`);
  }
  d.runCommand("fill -15 63 -15 -5 63 -5 stone");
  await wait(10);
  let ordinal = 0;
  for (const type of ["molcar", "molcar2", "molcar3"]) {
    const e = d.spawnEntity(`ichiyon:${type}`, { x: 0, y: 64, z: 0 });
    const p = spawnSimulatedPlayer({ dimension: d, x: 1, y: 64, z: 0 }, `RingTester${ordinal}`, GameMode.Survival);
    await wait(5);
    check(Math.abs(e.getComponent("minecraft:underwater_movement").currentValue - .02) < .000001, `ordinary water speed ${type}`);
    p.getComponent("minecraft:inventory").container.setItem(p.selectedSlotIndex, new ItemStack("ichiyon:molcar_float_ring"));
    cosmetics.mutateAccessory(p, e, "float_ring", 1); await wait(60);
    check(e.getProperty("ichiyon:float_ring") === 1, `equipped ${type}`);
    const heights = [];
    for (let i = 0; i < 60; i++) { await wait(1); heights.push(e.location.y); }
    check(Math.min(...heights) > 63.4 && Math.max(...heights) < 63.7 && Math.max(...heights) - Math.min(...heights) < .03,
      `stable surface ${type}`, { min: Math.min(...heights), max: Math.max(...heights) });
    check(e.getComponent("minecraft:rideable").addRider(p), `mount ${type}`);
    await wait(5); const start = { ...e.location };
    p.moveRelative(0, 1, 1); await wait(30); p.stopMoving();
    const movement = Math.hypot(e.location.x - start.x, e.location.z - start.z);
    check(movement > 1 && e.location.y > 62 && e.location.y < 65, `steer water ${type}`, { movement, position: e.location });
    p.getComponent("minecraft:inventory").container.setItem(p.selectedSlotIndex, new ItemStack("minecraft:carrot", 2));
    boost.use({ source: p, itemStack: p.getComponent("minecraft:inventory").container.getItem(p.selectedSlotIndex) });
    await wait(3);
    check(e.getComponent("minecraft:movement").currentValue > .64, `water carrot boost ${type}`);
    const boostedStart = { ...e.location };
    p.moveRelative(0, 1, 1); await wait(30); p.stopMoving();
    const boostedMovement = Math.hypot(e.location.x - boostedStart.x, e.location.z - boostedStart.z);
    check(boostedMovement > movement * 1.3, `water boost increases speed ${type}`, { movement, boostedMovement });
    // Native turn follows the rider without Script rotation/velocity replacement.
    p.setRotation({ x: 0, y: 90 }); const turnStart = { ...e.location };
    p.moveRelative(0, 1, 1); await wait(20); p.stopMoving();
    check(Math.abs(e.location.x - turnStart.x) > 1, `water turn ${type}`, { start: turnStart, end: e.location });
    e.getComponent("minecraft:rideable").ejectRiders(); await wait(5);
    p.teleport(e.location); cosmetics.mutateAccessory(p, e, "float_ring", 0); await wait(5);
    check(e.getProperty("ichiyon:float_ring") === 0, `unequip ${type}`);
    check(Math.abs(e.getComponent("minecraft:underwater_movement").currentValue - .02) < .000001, `normal water attribute restored ${type}`);
    // Record ordinary behavior, then re-equip and retain for a cold BDS restart.
    await wait(40); console.warn(`[PriorityOne] ordinary ${type} ${JSON.stringify({ y: e.location.y, v: e.getVelocity() })}`);
    p.teleport(e.location);
    p.getComponent("minecraft:inventory").container.setItem(p.selectedSlotIndex, new ItemStack("ichiyon:molcar_float_ring"));
    cosmetics.mutateAccessory(p, e, "float_ring", 1); await wait(5);
    e.teleport({ x: 12 + ordinal * 5, y: 64, z: -20 }); e.nameTag = "persist-ring";
    p.disconnect(); ordinal++;
  }
  const bird = d.spawnEntity("ichiyon:mokuro", { x: -10, y: 84, z: -10 });
  const control = d.spawnEntity("minecraft:pig", { x: -12, y: 84, z: -12 });
  let birdLanding, pigLanding, maxDown = 0;
  for (let i = 0; i < 400; i++) {
    await wait(1);
    if (bird.isOnGround && birdLanding === undefined) birdLanding = i;
    if (control.isValid && control.isOnGround && pigLanding === undefined) pigLanding = i;
    maxDown = Math.min(maxDown, bird.getVelocity().y);
    if (birdLanding !== undefined) break;
  }
  // Native slow falling for this Mob approaches -0.4 blocks/tick on BDS 1.26.51.1.
  check(pigLanding !== undefined && birdLanding > pigLanding * 2 && maxDown > -.42,
    "slow descent", { birdLanding, pigLanding, maxDown });
  check(bird.getComponent("minecraft:health").currentValue === 20, "fall HP20");
  await wait(3); check(!bird.getProperty("ichiyon:falling"), "landing clears flap");
  bird.applyDamage(3, { cause: "fall" }); await wait(2);
  check(bird.getComponent("minecraft:health").currentValue === 20, "native fall immunity");
  bird.applyDamage(2, { cause: "entityAttack" }); await wait(2);
  check(bird.getComponent("minecraft:health").currentValue === 18, "normal damage retained");
  world.setDynamicProperty("priority_one:restart", true);
  console.warn(`[PriorityOne] DONE first passed=${passed} failed=${failed}`);
}
system.runTimeout(() => main().catch(e => console.warn(`[PriorityOne] FATAL ${e} ${e.stack}`)), 40);
