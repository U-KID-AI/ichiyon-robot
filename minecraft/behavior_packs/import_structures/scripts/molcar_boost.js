import { world, system } from "@minecraft/server";
import { createMolcarBoost } from "./molcar_boost_core.js";

const boost = createMolcarBoost({ world, system });
function guard(action) {
  try { action(); } catch (e) { console.warn(`[MolcarBoost] ${String(e).slice(0, 300)}`); }
}
world.beforeEvents.itemUse.subscribe(e => guard(() => boost.use(e)));
world.beforeEvents.playerInteractWithEntity.subscribe(e => guard(() => boost.interact(e)));
world.afterEvents.entityLoad.subscribe(e => guard(() => boost.recover(e.entity)));
world.afterEvents.entitySpawn.subscribe(e => system.run(() => guard(() => boost.recover(e.entity))));
world.afterEvents.entityDie.subscribe(e => guard(() => boost.died(e.deadEntity)));
world.afterEvents.playerLeave.subscribe(e => guard(() => boost.releasePlayer(e.playerId)));
world.afterEvents.playerDimensionChange.subscribe(e => guard(() => boost.releasePlayer(e.player.id)));
system.runInterval(() => boost.tick(), 1);
system.runInterval(() => boost.scan(), 100);
system.run(() => boost.scan());
console.warn("[MolcarBoost] loaded: carrot / native movement / 100 ticks");
