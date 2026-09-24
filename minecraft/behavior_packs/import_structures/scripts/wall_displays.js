import { world, system } from "@minecraft/server";
import { createVideoDisplays } from "./wall_displays_core.js";
import { WALL_DISPLAYS, BIG_VIDEO_DISPLAY } from "./wall_displays_config.js";
import { VIDEO_MEDIA } from "./video_media.generated.js";
import { VIDEO_BIG_MEDIA } from "./video_big_media.generated.js";

const displays = createVideoDisplays({ world, system, displays: [
  { id: "small", config: WALL_DISPLAYS, media: VIDEO_MEDIA },
  { id: "big", config: BIG_VIDEO_DISPLAY, media: VIDEO_BIG_MEDIA },
] });
world.afterEvents.buttonPush.subscribe((event) => displays.button(event));
world.afterEvents.entityLoad.subscribe((event) => displays.recover(event.entity));
world.afterEvents.playerDimensionChange.subscribe((event) => displays.release(event.player));
world.afterEvents.playerLeave.subscribe((event) => displays.leave(event.playerId));
world.afterEvents.entityDie.subscribe((event) => {
  if (event.deadEntity.typeId === "minecraft:player") displays.release(event.deadEntity);
});
system.runInterval(() => displays.tick(), 1);
system.runInterval(() => displays.scan(), 100);
system.run(() => displays.scan());
