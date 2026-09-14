import { world, system } from "@minecraft/server";

console.warn("[TaketumiLeash] debug loaded");

world.beforeEvents.playerInteractWithEntity.subscribe((ev) => {
  if (ev.target.typeId !== "ichiyon:taketumi") return;
  if (!ev.itemStack || ev.itemStack.typeId !== "minecraft:lead") return;

  ev.cancel = true;

  const player = ev.player;
  const target = ev.target;

  system.run(() => {
    try {
      const components = target.getComponents()
        .map((component) => component.typeId)
        .sort();

      player.sendMessage(
        "§e[Taketumi] components: §f" + components.join(", ")
      );

      const leashable = target.getComponent("minecraft:leashable");

      if (!leashable) {
        player.sendMessage("§c[Taketumi] minecraft:leashable は実体にありません");
        return;
      }

      player.sendMessage("§a[Taketumi] leashable FOUND");

      if (leashable.isLeashed) {
        leashable.unleash();
      } else {
        leashable.leashTo(player);
      }
    } catch (error) {
      player.sendMessage("§c[Taketumi] ERROR: " + String(error));
      console.warn("[TaketumiLeash] " + String(error));
    }
  });
});
