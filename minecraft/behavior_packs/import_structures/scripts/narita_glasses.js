import { world, system } from "@minecraft/server";
import { createNaritaGlasses, CHECK_TICKS } from "./narita_glasses_core.js";

export const naritaGlasses = createNaritaGlasses({ world, system });
world.beforeEvents.effectAdd.subscribe(event => naritaGlasses.beforeEffect(event));
world.afterEvents.playerLeave.subscribe(event => naritaGlasses.forget(event.playerId));
world.afterEvents.entityDie.subscribe(event => naritaGlasses.died(event.deadEntity));
world.afterEvents.playerSpawn.subscribe(event => system.run(() => naritaGlasses.tick()));
system.runInterval(() => naritaGlasses.tick(), CHECK_TICKS);
console.warn("[NaritaGlasses] loaded: head-slot night vision, outside effects preserved");
