export const RECORD_MOLCARS = new Set([
  "ichiyon:molcar", "ichiyon:molcar2", "ichiyon:molcar3", "ichiyon:garbage_molcar",
]);
export const RECORD_RADIUS = 16;

export function createMolcarRecords({ world, system, tracks, report = console.warn }) {
  const byItem = new Map(tracks.map((track) => [track.itemId, track]));
  const playback = new Map();

  function stopListener(listener) {
    try { listener.sound.stop(); } catch { /* Disconnected recipient or stopped sound. */ }
  }

  function stop(entityId) {
    const state = playback.get(entityId);
    if (!state) return;
    for (const listener of state.listeners.values()) stopListener(listener);
    playback.delete(entityId);
  }

  function rider(player, molcar) {
    return (molcar.getComponent("minecraft:rideable")?.getRiders() || [])
      .some((value) => value.id === player.id);
  }

  function use(player, molcar, itemId) {
    const track = byItem.get(itemId);
    if (!track || !RECORD_MOLCARS.has(molcar.typeId)) return false;
    if (!player.isValid || !molcar.isValid || player.dimension.id !== molcar.dimension.id) return false;
    const delta = ["x", "y", "z"].map((axis) => player.location[axis] - molcar.location[axis]);
    if (Math.hypot(...delta) > 7) return false;
    const velocity = molcar.getVelocity();
    if (!rider(player, molcar) && Math.hypot(velocity.x, velocity.z) >= 0.04) {
      player.sendMessage("停止中のモルカー、または乗車中にレコードを使ってください。");
      return false;
    }
    const previous = playback.get(molcar.id);
    stop(molcar.id);
    if (previous?.track.itemId === itemId) return true;
    playback.set(molcar.id, {
      track, started: system.currentTick, dimension: molcar.dimension.id, listeners: new Map(),
    });
    tick();
    return true;
  }

  function tick() {
    if (!playback.size) return;
    const players = world.getAllPlayers();
    for (const [id, state] of playback) {
      try {
        const molcar = world.getEntity(id);
        const elapsed = (system.currentTick - state.started) / 20;
        if (!molcar?.isValid || molcar.dimension.id !== state.dimension || elapsed >= state.track.durationSeconds) {
          stop(id);
          continue;
        }
        const audible = new Set();
        for (const player of players) {
          if (player.dimension.id !== state.dimension) continue;
          const distance = Math.hypot(...["x", "y", "z"].map((axis) => player.location[axis] - molcar.location[axis]));
          if (distance >= RECORD_RADIUS) continue;
          audible.add(player.id);
          const volume = (1 - distance / RECORD_RADIUS) ** 2;
          let listener = state.listeners.get(player.id);
          if (!listener) {
            const sound = player.playSound(state.track.soundId, { volume, pitch: 1 });
            if (!sound || typeof sound.stop !== "function" || typeof sound.seekTo !== "function") {
              player.stopSound(state.track.soundId);
              throw new Error("Molcar records require the installed Script API SoundInstance handle");
            }
            listener = { sound };
            state.listeners.set(player.id, listener);
            if (elapsed > 0) sound.seekTo(elapsed);
          }
          listener.sound.setVolume(volume);
        }
        for (const [playerId, listener] of state.listeners) {
          if (!audible.has(playerId)) {
            stopListener(listener);
            state.listeners.delete(playerId);
          }
        }
      } catch (error) {
        stop(id);
        report(`[MolcarRecords] playback stopped: ${error}`);
      }
    }
  }

  function clearPlayer(playerId) {
    for (const state of playback.values()) {
      const listener = state.listeners.get(playerId);
      if (listener) stopListener(listener);
      state.listeners.delete(playerId);
    }
  }

  function resetPlayer(player) {
    for (const track of tracks) {
      try { player.stopSound(track.soundId); } catch { /* Player may have disconnected. */ }
    }
  }

  function interact(event) {
    const itemId = event.itemStack?.typeId;
    if (!byItem.has(itemId) || !RECORD_MOLCARS.has(event.target.typeId)) return;
    event.cancel = true;
    const { player, target } = event;
    system.run(() => {
      try {
        const inventory = player.getComponent("minecraft:inventory")?.container;
        if (inventory?.getItem(player.selectedSlotIndex)?.typeId === itemId) use(player, target, itemId);
      } catch (error) { report(`[MolcarRecords] interaction failed: ${error}`); }
    });
  }
  return { interact, use, tick, stop, clearPlayer, resetPlayer, playback };
}
