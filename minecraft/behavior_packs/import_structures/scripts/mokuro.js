import { world, system, InputButton, ButtonState } from "@minecraft/server";
import { ActionFormData } from "@minecraft/server-ui";
import { createMokuro } from "./mokuro_core.js";

const mokuro = createMokuro({ world, system, ActionFormData });
function guard(label, action) {
  try { action(); } catch (e) { console.warn(`[Mokuro] ${label}: ${String(e).slice(0, 300)}`); }
}
world.beforeEvents.playerInteractWithEntity.subscribe((e) => guard("interact", () => mokuro.interact(e)));
world.afterEvents.playerButtonInput.subscribe((e) => {
  if (e.button === InputButton.Jump && e.newButtonState === ButtonState.Pressed) guard("jump", () => mokuro.jump(e.player));
});
world.afterEvents.playerLeave.subscribe((e) => guard("logout", () => mokuro.releasePlayer(e.playerId)));
world.afterEvents.playerDimensionChange.subscribe((e) => guard("dimension", () => mokuro.releasePlayer(e.player.id)));
world.afterEvents.entityDie.subscribe((e) => guard("death", () => mokuro.died(e.deadEntity)));
world.afterEvents.entityLoad.subscribe((e) => guard("load", () => mokuro.recover(e.entity)));
world.beforeEvents.entityHurt.subscribe((e) => guard("fall", () => mokuro.fall(e)));
system.runInterval(() => mokuro.tick(), 1);
system.runInterval(() => mokuro.scan(), 100);
system.run(() => mokuro.scan());
console.warn("[Mokuro] loaded: same-entity head/back attachment and jump glide");
