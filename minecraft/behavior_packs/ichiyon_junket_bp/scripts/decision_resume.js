// Optional menu recall without NPCs, items, permissions or a trip to reception.
// Two distinct crouch presses are recognized on keyboard/controller/touch.
// API contract: https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/inputbutton
export function installDecisionResume({ world, system, manager, runtimes }) {
  const presses = new Map(), cooldown = new Map();
  console.log(`[JUNKET DECISION] crouch menu event available: ${typeof world.afterEvents.playerButtonInput?.subscribe === "function"}`);
  world.afterEvents.playerButtonInput?.subscribe(event => {
    if (event.button !== "Sneak" || event.newButtonState !== "Pressed") return;
    const player = event.player, current = manager.forPlayer(player.id);
    if (!current?.state) return;
    const tick = system.currentTick;
    if (tick < (cooldown.get(player.id) ?? -1)) return;
    const previous = presses.get(player.id);
    presses.set(player.id, tick);
    if (previous === undefined || tick <= previous || tick - previous > 12) return;
    presses.delete(player.id); cooldown.set(player.id, tick + 30);
    const session = current.sessionToken;
    system.run(() => {
      if (manager.forPlayer(player.id)?.sessionToken !== session) return;
      void runtimes.get(current.gameId)?.open(player);
    });
  });
  world.afterEvents.playerLeave.subscribe(event => {
    presses.delete(event.playerId); cooldown.delete(event.playerId);
  });
}
