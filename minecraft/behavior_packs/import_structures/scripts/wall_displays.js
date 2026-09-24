import { world, system } from "@minecraft/server";
import { createWallDisplays } from "./wall_displays_core.js";
import { VIDEO_MEDIA } from "./video_media.generated.js";

const displays = createWallDisplays({ world, system, media: VIDEO_MEDIA });
let lastError = -100;
function guard(action) {
  try { action(); } catch (error) {
    try { displays.reset(); } catch { /* The display chunk may be unloading. */ }
    if (system.currentTick - lastError >= 100) {
      console.warn(`[Wall displays] ${String(error).slice(0, 250)}`);
      lastError = system.currentTick;
    }
  }
}
world.afterEvents.buttonPush.subscribe((event) => guard(() => displays.button(event)));
world.afterEvents.entityLoad.subscribe((event) => guard(() => displays.recover(event.entity)));
world.afterEvents.playerDimensionChange.subscribe((event) => guard(() => displays.release(event.player)));
world.afterEvents.playerLeave.subscribe((event) => displays.leave(event.playerId));
world.afterEvents.entityDie.subscribe((event) => {
  if (event.deadEntity.typeId === "minecraft:player") guard(() => displays.release(event.deadEntity));
});
system.runInterval(() => guard(() => displays.tick()), 1);
system.runInterval(() => guard(() => displays.scan()), 100);
system.run(() => guard(() => displays.scan()));
