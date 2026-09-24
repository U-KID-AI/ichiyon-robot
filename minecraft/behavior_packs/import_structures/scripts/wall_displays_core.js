import { WALL_DISPLAYS } from "./wall_displays_config.js";

export const MAP_REFRESH_LIMITATION = "Eight map frames can be rescanned; the public Bedrock API has no existing-filled-map terrain recalculation operation. Maps and frames are never replaced.";
export const isVanillaButton = (id) => /^minecraft:(?:[a-z_]+_button)$/.test(id);
export const samePosition = (a, b) => !!a && !!b && a.x === b.x && a.y === b.y && a.z === b.z;
export const isFrame = (block) => ["minecraft:frame", "minecraft:glow_frame"].includes(block?.typeId);
const position = (x, y, z) => ({ x, y, z });

function reader(dimension) {
  const cache = new Map();
  return (x, y, z) => {
    const key = `${x},${y},${z}`;
    if (!cache.has(key)) {
      // Missing chunks are unknown, never air. An incomplete scan fails closed.
      const block = dimension.getBlock(position(x, y, z));
      if (!block) throw new Error("unloaded wall block");
      cache.set(key, block);
    }
    return cache.get(key);
  };
}

export function detectVideoScreen(dimension, config = WALL_DISPLAYS.video) {
  const read = reader(dimension), matches = [];
  try {
    for (const z of config.planes) for (let y = config.minBottom; y <= config.maxBottom; y++) {
      const first = read(config.x, y, z);
      if (!first.isSolid) continue;
      const material = first.typeId;
      let valid = true;
      for (let dx = 0; dx < config.width && valid; dx++) for (let dy = 0; dy < config.height; dy++) {
        const block = read(config.x + dx, y + dy, z);
        if (!block.isSolid || block.typeId !== material || !read(config.x + dx, y + dy, z + 1).isAir) { valid = false; break; }
      }
      if (!valid) continue;
      // A larger uniform wall is not evidence of the requested 11x4 screen.
      for (let dx = 0; dx < config.width; dx++) {
        if (read(config.x + dx, y - 1, z).typeId === material || read(config.x + dx, y + config.height, z).typeId === material) valid = false;
      }
      for (let dy = 0; dy < config.height; dy++) {
        if (read(config.x - 1, y + dy, z).typeId === material || read(config.x + config.width, y + dy, z).typeId === material) valid = false;
      }
      if (valid) matches.push({ bottom: y, z, material,
        origin: position(config.x + config.width / 2, y, z + 1.02),
        center: position(config.x + config.width / 2, y + config.height / 2, z + 1.02),
        button: position(config.x - 1, y, z + 1) });
    }
    return { status: matches.length === 1 ? "ready" : matches.length ? "ambiguous" : "not_found", screen: matches.length === 1 ? matches[0] : undefined };
  } catch (error) { return { status: "unloaded", error: String(error) }; }
}

export function detectMapWall(dimension, config = WALL_DISPLAYS.map) {
  const read = reader(dimension), matches = [];
  try {
    for (const z of config.planes) for (let y = config.minBottom; y <= config.maxBottom; y++) {
      const frames = [];
      let valid = true;
      for (let row = 0; row < 3 && valid; row++) for (let col = 0; col < 3; col++) {
        const block = read(config.leftX - col, y + row, z);
        if (row === 2 && col === 2) { if (isFrame(block)) valid = false; continue; }
        if (!isFrame(block) || block.permutation.getState("facing_direction") !== config.facing ||
            !block.permutation.getState("item_frame_map_bit") || !read(config.leftX - col, y + row, z + 1).isSolid) { valid = false; break; }
        frames.push(position(config.leftX - col, y + row, z));
      }
      if (valid && frames.length === 8) matches.push({ bottom: y, z, frames, button: position(config.leftX + 1, y, z) });
    }
    return { status: matches.length === 1 ? "ready" : matches.length ? "ambiguous" : "not_found", wall: matches.length === 1 ? matches[0] : undefined,
      refreshSupported: false, limitation: MAP_REFRESH_LIMITATION };
  } catch (error) { return { status: "unloaded", refreshSupported: false, error: String(error) }; }
}

export function audienceGain(player, screen, config = WALL_DISPLAYS) {
  if (!screen || player.dimension.id !== config.dimension || player.location.z < screen.center.z) return 0;
  const p = player.location, c = screen.center;
  const distance = Math.hypot(p.x - c.x, p.y - c.y, p.z - c.z);
  // The positional sound definition supplies attenuation; do not apply it twice.
  return distance < config.video.audienceRadius ? 1 : 0;
}

export function createWallDisplays({ world, system, media, now = () => Date.now(), config = WALL_DISPLAYS, log = console.warn }) {
  let screen, mapWall, entity, on = false, started = 0, frame = -1, lastCycle = -1;
  let videoStatus = "cold", mapStatus = "cold", lastButtonTick = -100;
  const listeners = new Map(), cleanedPlayers = new Set();
  const dimension = () => world.getDimension(config.dimension);
  function stopPlayer(player) {
    const previous = listeners.get(player.id);
    if (previous) {
      try { previous.handle.stop(); }
      catch { try { player.stopSound(media.sound); } catch { /* Disconnected recipient. */ } }
    }
    listeners.delete(player.id);
  }
  function reset() {
    on = false; started = 0; frame = -1; lastCycle = -1;
    for (const { player } of [...listeners.values()]) stopPlayer(player);
    if (entity?.isValid) entity.setProperty("ichiyon:frame", -1);
  }
  function recover(loaded) {
    if (loaded.typeId !== config.video.entity) return;
    // The only entities ever removed are our own transient display helpers.
    loaded.setProperty("ichiyon:frame", -1);
    if (entity?.id === loaded.id) { reset(); return; }
    loaded.remove();
  }
  function scanMap() {
    const result = detectMapWall(dimension(), config.map);
    if (result.wall && (mapStatus !== result.status || !samePosition(mapWall?.button, result.wall.button))) {
      const { bottom, z, button } = result.wall;
      log(`[Map wall] ready dimension=${config.dimension} bottom=${bottom} wallPlaneZ=${z + 1} framePlaneZ=${z} button=(${button.x},${button.y},${button.z}) frames=8; automatic map recalculation unavailable`);
    } else if (mapStatus !== result.status) log(`[Map wall] ${result.status}; automatic map recalculation unavailable`);
    mapStatus = result.status;
    // Retain the last proven button while the layout is incomplete, for rescan.
    if (result.wall) mapWall = result.wall;
    return result;
  }
  function scan() {
    const result = detectVideoScreen(dimension(), config.video);
    if (result.screen && (videoStatus !== result.status || !samePosition(screen?.button, result.screen.button))) {
      const { bottom, z, button } = result.screen;
      log(`[Video screen] ready dimension=${config.dimension} bottom=${bottom} wallPlaneZ=${z} button=(${button.x},${button.y},${button.z})`);
    } else if (videoStatus !== result.status) log(`[Video screen] ${result.status}`);
    videoStatus = result.status;
    if (!result.screen) {
      reset(); screen = undefined;
      if (entity?.isValid) entity.remove();
      entity = undefined;
    } else {
      const changed = !screen || !samePosition(screen.origin, result.screen.origin);
      if (changed) reset();
      screen = result.screen;
      for (const old of dimension().getEntities({ type: config.video.entity })) {
        if (old.id !== entity?.id) recover(old);
      }
      if (!entity?.isValid || changed) {
        reset();
        if (entity?.isValid) entity.remove();
        entity = dimension().spawnEntity(config.video.entity, screen.origin);
        entity.setRotation({ x: 0, y: 0 });
        entity.setProperty("ichiyon:frame", -1);
      }
    }
    scanMap();
  }
  function button(event) {
    const block = event.block;
    if (block.dimension.id !== config.dimension || !isVanillaButton(block.typeId)) return;
    if (samePosition(block.location, mapWall?.button)) {
      const result = scanMap();
      const message = `[Map wall] ${result.status}: ${result.wall?.frames.length ?? 0}/8. ${MAP_REFRESH_LIMITATION}`;
      if (event.source?.typeId === "minecraft:player") event.source.sendMessage(message);
      log(message);
    }
    if (!samePosition(block.location, screen?.button) || system.currentTick === lastButtonTick) return;
    lastButtonTick = system.currentTick;
    // Validate the real wall again before starting, not merely a stale coordinate.
    scan();
    if (!screen || !entity?.isValid) return;
    if (on) reset();
    else { on = true; started = now(); frame = 0; lastCycle = -1; entity.setProperty("ichiyon:frame", 0); tick(); }
  }
  function tick() {
    const players = world.getAllPlayers();
    // Reload can leave client audio alive briefly. Clear only our sound IDs.
    for (const player of players) if (!cleanedPlayers.has(player.id)) {
      if (media.sound) player.stopSound(media.sound);
      cleanedPlayers.add(player.id);
    }
    if (!on) return;
    if (!entity?.isValid || !screen) { reset(); return; }
    const elapsed = Math.max(0, (now() - started) / 1000);
    const cycle = Math.floor(elapsed / media.duration);
    const time = elapsed % media.duration;
    const nextFrame = Math.min(media.frameCount - 1, Math.floor(time * media.fps));
    if (frame !== nextFrame) { entity.setProperty("ichiyon:frame", nextFrame); frame = nextFrame; }
    if (cycle !== lastCycle) {
      for (const { player } of [...listeners.values()]) stopPlayer(player);
      lastCycle = cycle;
    }
    const ids = new Set(players.map((p) => p.id));
    for (const { player } of [...listeners.values()]) {
      if (!ids.has(player.id) || audienceGain(player, screen, config) <= 0) stopPlayer(player);
    }
    if (media.sound) for (const player of players) {
      const gain = audienceGain(player, screen, config);
      if (gain <= 0) continue;
      const previous = listeners.get(player.id);
      if (!previous) {
        // 2.11.0-beta.1.26.51-stable exposes seekTo/setVolume/stop. Start muted
        // so late arrivals do not hear the opening before the current position.
        const handle = player.playSound(media.sound, { location: screen.center, volume: 0, pitch: 1 });
        if (!handle || typeof handle.seekTo !== "function" || typeof handle.setVolume !== "function" || typeof handle.stop !== "function") {
          player.stopSound(media.sound);
          throw new Error("Video requires the 2.11-beta SoundInstance API");
        }
        listeners.set(player.id, { player, handle, gain });
        handle.seekTo(time);
        handle.setVolume(gain);
      } else if (Math.abs(previous.gain - gain) >= 0.02) {
        previous.handle.setVolume(gain);
        previous.gain = gain;
      }
    }
  }
  function release(player) { stopPlayer(player); cleanedPlayers.delete(player.id); }
  function leave(id) {
    const previous = listeners.get(id);
    if (previous) stopPlayer(previous.player);
    cleanedPlayers.delete(id);
  }
  return { scan, scanMap, tick, button, reset, recover, release, leave,
    status: () => ({ on, frame, videoStatus, mapStatus, screen, mapWall, listeners: listeners.size }) };
}
