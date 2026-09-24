import { world, system } from "@minecraft/server";
import { molcarRecords } from "./molcar_records_catalog.js";
import { createMolcarRecords } from "./molcar_records_core.js";

// No silent placeholder record is registered when the original audio is unavailable.
if (molcarRecords.length) {
  const records = createMolcarRecords({ world, system, tracks: molcarRecords });
  world.beforeEvents.playerInteractWithEntity.subscribe((event) => records.interact(event));
  world.beforeEvents.itemUse.subscribe((event) => records.itemUse(event));
  world.afterEvents.entityDie.subscribe(({ deadEntity }) => records.stop(deadEntity.id));
  world.afterEvents.entityRemove.subscribe(({ removedEntityId }) => records.stop(removedEntityId));
  world.afterEvents.playerLeave.subscribe(({ playerId }) => records.clearPlayer(playerId));
  world.afterEvents.playerSpawn.subscribe(({ player, initialSpawn }) => {
    if (initialSpawn) records.resetPlayer(player);
  });
  system.run(() => world.getAllPlayers().forEach((player) => records.resetPlayer(player)));
  system.runInterval(() => records.tick(), 1);
  console.warn(`[MolcarRecords] loaded ${molcarRecords.length} tracks`);
}
