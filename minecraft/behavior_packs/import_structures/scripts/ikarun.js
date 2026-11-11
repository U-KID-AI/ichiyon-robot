import { world, system } from "@minecraft/server";
import { ActionFormData } from "@minecraft/server-ui";
import { createIkarun, HEAL, installMannequinPlacement } from "./ikarun_core.js";

const ikarun = createIkarun({ world, system });
installMannequinPlacement({ world, system, ActionFormData });
world.beforeEvents.playerInteractWithEntity.subscribe((event) => ikarun.interact(event));
world.afterEvents.entitySpawn.subscribe((event) => {
  if (event.entity?.typeId !== "ichiyon:ikarun") return;
  system.run(() => { try { ikarun.noteSpawn(event.entity); } catch { /* A dying spawn has no home to store. */ } });
});
system.runInterval(() => ikarun.scan(), HEAL.scanTicks);
console.warn("[Ikarun] hover companion and bounded friendly healing loaded");
