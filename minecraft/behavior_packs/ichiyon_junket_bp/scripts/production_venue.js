import { VENUE_CONFIG } from "./junket_venue_generated.js";

const DIMENSION = "minecraft:overworld";
const INSTALL_KEY = VENUE_CONFIG.VENUE_INSTALL_KEY;
const PER_VENUE_PREFIX = `${INSTALL_KEY}:`;
const SPECIAL_KEYS = {
  sparrow: "junket:venue_v1",
  temperance: "junket:temperance_venue_v1",
  lucy: "junket:lucy_venue_v1",
  silence: "junket:silence_venue_v1",
  jinn: "junket:jinn_venue_v1",
};

const point = value => ({ x: value.x, y: value.y, z: value.z });

export function createProductionVenues({ world, system }) {
  const entries = new Map(VENUE_CONFIG.VENUES.map(entry => [entry.id, entry]));
  const special = new Map();
  let installing = false;

  const entry = id => {
    const value = entries.get(id);
    if (!value) throw new Error(`unknown JUNKET venue: ${id}`);
    return value;
  };
  const dimension = () => world.getDimension(DIMENSION);
  const originName = id => `${PER_VENUE_PREFIX}${id}`;
  const installed = id => world.getDynamicProperty(originName(id)) === 1;
  const config = id => {
    const venue = entry(id);
    return {
      dimension: DIMENSION,
      origin: point({
        x: venue.runtime_origin[0],
        y: venue.runtime_origin[1],
        z: venue.runtime_origin[2],
      }),
      backup: null,
    };
  };
  const nearby = (id, tag, at) => dimension().getEntities({
    tags: [tag], location: point(at), maxDistance: 12,
  });
  const tell = (player, message) => {
    try { player?.sendMessage(`[JUNKET] ${message}`); } catch { /* Disconnected. */ }
  };

  function ensure(id) {
    const venue = entry(id);
    const d = dimension();
    let control = nearby(id, `junket:${id}:reception`, venue.control)[0];
    if (!control) {
      control = d.spawnEntity(`junket:${id}_prop`, point(venue.control));
      control.addTag(`junket:${id}:reception`);
      control.addTag(`junket:${id}:control`);
    }
    control.nameTag = `${venue.title}\n右クリックで参加・操作`;
    let exit = nearby(id, "junket:return", venue.return_point)[0];
    if (!exit) {
      exit = d.spawnEntity("minecraft:npc", point(venue.return_point));
      exit.addTag("junket:return");
    }
    exit.nameTag = "入口へ戻る / JUNKET";
    return { control, exit };
  }

  function destination(id) {
    const venue = entry(id);
    return {
      location: point(venue.destination),
      dimension: dimension(),
      rotation: 0,
      afterTeleport: () => ensure(id),
    };
  }

  function goto(id, player) {
    const target = destination(id);
    ensure(id);
    player.teleport(target.location, {
      dimension: target.dimension, rotation: { x: 0, y: 0 },
    });
  }

  function start(id, current) {
    const venue = entry(id);
    ensure(id);
    const online = world.getAllPlayers();
    current.players.forEach((playerId, seat) => {
      const player = online.find(candidate => candidate.id === playerId);
      const at = venue.seats[seat] ?? venue.destination;
      player?.teleport(point(at), { dimension: dimension(), rotation: { x: 0, y: 0 } });
    });
    update(id, current);
  }

  function update(id, current) {
    const venue = entry(id);
    const control = nearby(id, `junket:${id}:reception`, venue.control)[0];
    if (!control) return;
    const state = current?.state;
    if (!state) control.nameTag = `${venue.title}\n参加者待ち`;
    else if (state.phase === "finished") control.nameTag = `${venue.title}\nゲーム終了・再戦受付`;
    else control.nameTag = `${venue.title}\n進行中: ${state.phase}`;
  }

  function siteCanBeCreated(venue) {
    const d = dimension();
    const [x, y, z] = venue.origin;
    try {
      const samples = [
        d.getBlock({ x, y, z }),
        d.getBlock({ x: x + venue.size[0] - 1, y: y + venue.size[1] - 1, z: z + venue.size[2] - 1 }),
        d.getBlock({ x: x + Math.floor(venue.size[0] / 2), y: y + 1, z: z + Math.floor(venue.size[2] / 2) }),
      ];
      return samples.every(block =>
        block === undefined || block === null ||
        block.isAir || block.typeId === "minecraft:air");
    } catch {
      // A never-generated remote chunk is safe; the release snapshot also
      // verifies these fixed coordinates before the first production run.
      return true;
    }
  }

  function applySpecialConfig(id) {
    const key = SPECIAL_KEYS[id];
    if (key) world.setDynamicProperty(key, JSON.stringify(config(id)));
  }

  function installOne(id, warp, specialVenues = {}) {
    const venue = entry(id);
    if (installed(id)) {
      applySpecialConfig(id);
      return;
    }
    const d = dimension();
    let existing;
    try {
      existing = d.getBlock({
        x: venue.origin[0], y: venue.origin[1], z: venue.origin[2],
      });
    } catch {
      existing = undefined;
    }
    if (existing?.typeId === venue.origin_block) {
      applySpecialConfig(id);
      world.setDynamicProperty(originName(id), 1);
      return;
    }
    if (!siteCanBeCreated(venue)) {
      throw new Error(`JUNKET venue target is not empty: ${id} ${venue.origin.join(",")}`);
    }
    world.structureManager.place(venue.structure, dimension(), point({
      x: venue.origin[0], y: venue.origin[1], z: venue.origin[2],
    }), { includeEntities: false });
    applySpecialConfig(id);
    world.setDynamicProperty(originName(id), 1);
    void warp;
    void specialVenues;
  }

  function runFixed(blockDimension, command) {
    const result = blockDimension.runCommand(command);
    if ((result?.successCount ?? 0) < 1) {
      throw new Error(`JUNKET command failed: ${command}`);
    }
  }

  function installOneScheduled(id, specialVenues = {}) {
    return new Promise((resolve, reject) => {
      const venue = entry(id);
      if (installed(id)) {
        applySpecialConfig(id);
        resolve();
        return;
      }
      const d = dimension();
      const tickingName = `junket_venue_${id}`;
      let ticking = false;
      const cleanup = () => {
        if (ticking) {
          try { d.runCommand(`tickingarea remove ${tickingName}`); } catch { /* Best effort cleanup. */ }
          ticking = false;
        }
      };
      const finish = () => {
        applySpecialConfig(id);
        world.setDynamicProperty(originName(id), 1);
        cleanup();
        resolve();
      };
      const fail = error => {
        cleanup();
        reject(error);
      };
      try {
        try { d.runCommand(`tickingarea remove ${tickingName}`); } catch { /* No prior temporary area. */ }
        runFixed(d, `tickingarea add circle ${venue.origin[0]} ${venue.origin[1]} ${venue.origin[2]} 4 ${tickingName}`);
        ticking = true;
      } catch (error) {
        fail(error);
        return;
      }
      const verify = remaining => {
        try {
          const origin = d.getBlock({
            x: venue.origin[0], y: venue.origin[1], z: venue.origin[2],
          });
          if (!origin && remaining > 0) {
            system.runTimeout(() => verify(remaining - 1), 20);
            return;
          }
          if (origin?.typeId !== venue.origin_block && remaining > 0) {
            system.runTimeout(() => verify(remaining - 1), 20);
            return;
          }
          if (origin?.typeId !== venue.origin_block) {
            throw new Error(`JUNKET venue placement mismatch: ${id}`);
          }
          finish();
        } catch (error) {
          fail(error);
        }
      };
      const place = remaining => {
        try {
          const origin = d.getBlock({
            x: venue.origin[0], y: venue.origin[1], z: venue.origin[2],
          });
          if (!origin && remaining > 0) {
            system.runTimeout(() => place(remaining - 1), 20);
            return;
          }
          if (origin?.typeId === venue.origin_block) {
            finish();
            return;
          }
          if (!siteCanBeCreated(venue)) {
            throw new Error(`JUNKET venue target is not empty: ${id} ${venue.origin.join(",")}`);
          }
          world.structureManager.place(venue.structure, d, point({
            x: venue.origin[0], y: venue.origin[1], z: venue.origin[2],
          }), { includeEntities: false });
          system.runTimeout(() => verify(20), 20);
        } catch (error) {
          fail(error);
        }
      };
      system.runTimeout(() => place(20), 20);
      void specialVenues;
    });
  }

  function finish(warp) {
    const complete = () => world.setDynamicProperty(INSTALL_KEY, 1);
    const result = warp.installControls();
    if (result && typeof result.then === "function") {
      result.then(ok => {
        if (ok) complete();
        else console.warn("[JUNKET] warp control installation did not complete");
      }).catch(error => console.warn(`[JUNKET] warp control installation failed: ${error.message}`));
      return;
    }
    if (!result) throw new Error("JUNKET warp controls failed verification");
    complete();
  }

  function installNext(warp, specialVenues, index = 0) {
    const venues = VENUE_CONFIG.VENUES;
    if (world.getDynamicProperty(INSTALL_KEY) === 1) return;
    if (index >= venues.length) {
      finish(warp);
      installing = false;
      return;
    }
    if (installing) return;
    installing = true;
    const id = venues[index].id;
    try {
      installOne(id, warp, specialVenues);
    } catch (error) {
      installing = false;
      const players = world.getAllPlayers?.() ?? [];
      players.forEach(player => tell(player, `本番会場導入を停止しました: ${error.message}`));
      throw error;
    }
    installing = false;
    system.runTimeout(() => installNext(warp, specialVenues, index + 1), 2);
  }

  function installNextScheduled(warp, specialVenues, index = 0) {
    const venues = VENUE_CONFIG.VENUES;
    if (world.getDynamicProperty(INSTALL_KEY) === 1) return;
    if (index >= venues.length) {
      finish(warp);
      installing = false;
      return;
    }
    if (installing) return;
    installing = true;
    installOneScheduled(venues[index].id, specialVenues).then(() => {
      installing = false;
      system.runTimeout(() => installNextScheduled(warp, specialVenues, index + 1), 2);
    }).catch(error => {
      installing = false;
      const players = world.getAllPlayers?.() ?? [];
      players.forEach(player => tell(player, `本番会場導入を停止しました: ${error.message}`));
      console.warn(`[JUNKET] venue installation failed: ${error.message}`);
    });
  }

  function installAll(warp, specialVenues = {}) {
    if (world.getDynamicProperty(INSTALL_KEY) === 1 || installing) return false;
    installNextScheduled(warp, specialVenues, 0);
    return true;
  }

  return {
    config, destination, ensure, goto, start, update, installAll,
    installing: () => installing,
    complete: () => world.getDynamicProperty(INSTALL_KEY) === 1,
    entries: VENUE_CONFIG.VENUES,
  };
}
