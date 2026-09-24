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
    for (const z of config.planes) for (let x = config.minX ?? config.x; x <= (config.maxX ?? config.x); x++) for (let y = config.minBottom; y <= config.maxBottom; y++) {
      const first = read(x, y, z);
      if (!first.isSolid || (config.materials && !config.materials.includes(first.typeId))) continue;
      const material = first.typeId;
      let valid = true;
      for (let dx = 0; dx < config.width && valid; dx++) for (let dy = 0; dy < config.height; dy++) {
        const block = read(x + dx, y + dy, z);
        if (!block.isSolid || block.typeId !== material || !read(x + dx, y + dy, z + 1).isAir) { valid = false; break; }
      }
      if (!valid) continue;
      // Reject subrectangles of a larger wall rather than guessing an installation.
      for (let dx = 0; dx < config.width; dx++) {
        if (read(x + dx, y - 1, z).typeId === material || read(x + dx, y + config.height, z).typeId === material) valid = false;
      }
      for (let dy = 0; dy < config.height; dy++) {
        if (read(x - 1, y + dy, z).typeId === material || read(x + config.width, y + dy, z).typeId === material) valid = false;
      }
      if (valid) matches.push({ left: x, right: x + config.width - 1, bottom: y, top: y + config.height - 1, z, material,
        origin: position(x + config.width / 2, y, z + 1.02),
        center: position(x + config.width / 2, y + config.height / 2, z + 1.02),
        button: config.floorButton ? undefined : position(x - 1, y, z + 1) });
    }
    return { status: matches.length === 1 ? "ready" : matches.length ? "ambiguous" : "not_found", screen: matches.length === 1 ? matches[0] : undefined };
  } catch (error) { return { status: "unloaded", error: String(error) }; }
}

export function detectFloorButton(dimension, config) {
  const read = reader(dimension), matches = [], { anchor, radius } = config;
  try {
    for (let x = anchor.x - radius; x <= anchor.x + radius; x++)
      for (let y = anchor.y - radius; y <= anchor.y + radius; y++)
        for (let z = anchor.z - radius; z <= anchor.z + radius; z++) {
          if (Math.hypot(x - anchor.x, y - anchor.y, z - anchor.z) > radius) continue;
          const block = read(x, y, z);
          if (!isVanillaButton(block.typeId)) continue;
          const facing = block.permutation.getState("facing_direction");
          if ((facing === 1 || facing === "up") && read(x, y - 1, z).isSolid) matches.push(position(x, y, z));
        }
    return { status: matches.length === 1 ? "ready" : matches.length ? "ambiguous" : "not_found",
      button: matches.length === 1 ? matches[0] : undefined };
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
  const area = config.video.audience;
  if (area) return p.x >= screen.left - area.marginX && p.x <= screen.right + 1 + area.marginX &&
    p.z >= c.z + area.near && p.z <= c.z + area.far &&
    p.y >= screen.bottom + area.minY && p.y <= screen.bottom + area.maxY ? 1 : 0;
  const distance = Math.hypot(p.x - c.x, p.y - c.y, p.z - c.z);
  // The positional sound definition supplies attenuation; do not apply it twice.
  return distance < config.video.audienceRadius ? 1 : 0;
}

export function createWallDisplays({ world, system, media, now = () => Date.now(), config = WALL_DISPLAYS, log = console.warn }) {
  let screen, mapWall, entity, on = false, started = 0, frame = -1, lastCycle = -1;
  let videoStatus = "cold", mapStatus = "cold", buttonStatus = "cold", lastButtonTick = -100;
  let controlButton;
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
    if (!config.map) return { status: "disabled" };
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
    if (config.video.floorButton) {
      const found = detectFloorButton(dimension(), config.video.floorButton);
      if (found.button && (buttonStatus !== found.status || !samePosition(controlButton, found.button))) {
        const { x, y, z } = found.button;
        log(`[Video screen:${config.id}] button=(${x},${y},${z}) floor=true`);
      } else if (buttonStatus !== found.status) log(`[Video screen:${config.id}] button ${found.status}`);
      buttonStatus = found.status;
      // A removed button does not stop playback. Only a uniquely detected button can operate it.
      controlButton = found.button;
      if (result.screen) result.screen.button = controlButton;
    }
    if (result.screen && (videoStatus !== result.status || !samePosition(screen?.origin, result.screen.origin))) {
      const { bottom, z, button } = result.screen;
      if (config.id) log(`[Video screen:${config.id}] ready dimension=${config.dimension} left=${result.screen.left} right=${result.screen.right} bottom=${bottom} top=${result.screen.top} wallPlaneZ=${z} audience=south(+Z)`);
      else log(`[Video screen] ready dimension=${config.dimension} bottom=${bottom} wallPlaneZ=${z} button=(${button.x},${button.y},${button.z})`);
    } else if (videoStatus !== result.status) log(`[Video screen${config.id ? ":" + config.id : ""}] ${result.status}`);
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
        if (old.typeId === config.video.entity && old.id !== entity?.id) recover(old);
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
    // Replacing a broken floor button works immediately, before the periodic scan.
    if (config.video.floorButton) {
      const { anchor, radius } = config.video.floorButton;
      if (Math.hypot(block.location.x - anchor.x, block.location.y - anchor.y, block.location.z - anchor.z) > radius) return;
      scan();
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
    status: () => ({ on, frame, videoStatus, mapStatus, buttonStatus, screen, mapWall, listeners: listeners.size }) };
}

export function createVideoDisplays({ world, system, displays, now, log = console.warn }) {
  const instances = displays.map(({ id, config, media }) => ({ id,
    core: createWallDisplays({ world, system, config, media, now, log }), lastError: -100 }));
  const call = (method, ...args) => {
    for (const entry of instances) {
      try { entry.core[method](...args); }
      catch (error) {
        try { entry.core.reset(); } catch { /* This display chunk may be unloading. */ }
        if (system.currentTick - entry.lastError >= 100) {
          log(`[Video screen:${entry.id}] ${String(error).slice(0, 250)}`);
          entry.lastError = system.currentTick;
        }
      }
    }
  };
  return Object.fromEntries(["scan", "tick", "button", "recover", "release", "leave", "reset"]
    .map((method) => [method, (...args) => call(method, ...args)]).concat([
      ["status", () => Object.fromEntries(instances.map(({ id, core }) => [id, core.status()]))],
    ]));
}
