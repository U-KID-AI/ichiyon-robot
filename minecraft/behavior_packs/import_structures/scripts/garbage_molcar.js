import { world, system } from "@minecraft/server";
import { ActionFormData } from "@minecraft/server-ui";
import { createGarbageMolcar, GARBAGE } from "./garbage_molcar_core.js";

const garbage = createGarbageMolcar({ world, system, ActionFormData });
world.beforeEvents.playerInteractWithEntity.subscribe((event) => garbage.interact(event));
world.afterEvents.entitySpawn.subscribe(({ entity }) => {
  if (entity.typeId === GARBAGE) garbage.sound(entity, "spawn");
});
world.afterEvents.entityHurt.subscribe(({ hurtEntity }) => {
  if (hurtEntity.typeId === GARBAGE) garbage.sound(hurtEntity, "hurt");
});
world.afterEvents.entityDie.subscribe(({ deadEntity }) => {
  if (deadEntity.typeId === GARBAGE) garbage.sound(deadEntity, "death");
});
system.runInterval(() => garbage.scan(), 10);
console.warn("[GarbageMolcar] collection and Kuma paw loaded");
