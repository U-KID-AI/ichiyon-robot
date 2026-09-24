import { world, system } from "@minecraft/server";
import { createDeathPrairieDog, PRAIRIE } from "./death_prairie_dog_core.js";

const prairie = createDeathPrairieDog({ world, system });
function guard(action) {
  try { action(); } catch (error) {
    console.warn(`[DeathPrairieDog] ${String(error).slice(0, 240)}`);
  }
}
function recover(entity) {
  if (entity.typeId === PRAIRIE) system.run(() => guard(() => prairie.recover(entity)));
}
world.afterEvents.entitySpawn.subscribe(e => recover(e.entity));
world.afterEvents.entityLoad.subscribe(e => recover(e.entity));
world.afterEvents.entityRemove.subscribe(e => prairie.forget(e.removedEntityId));
world.afterEvents.entityDie.subscribe(e => prairie.forget(e.deadEntity.id));
system.runInterval(() => guard(() => prairie.tick()), 1);
system.runInterval(() => guard(() => prairie.scan()), 100);
system.run(() => guard(() => prairie.scan()));
console.warn("[DeathPrairieDog] loaded: native harmless pursuit and sleeper avoidance");
