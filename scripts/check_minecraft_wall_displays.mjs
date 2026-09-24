import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createWallDisplays, detectVideoScreen, detectMapWall, isVanillaButton, audienceGain } from "../minecraft/behavior_packs/import_structures/scripts/wall_displays_core.js";
import { WALL_DISPLAYS as config } from "../minecraft/behavior_packs/import_structures/scripts/wall_displays_config.js";
import { VIDEO_MEDIA as media } from "../minecraft/behavior_packs/import_structures/scripts/video_media.generated.js";

let passed = 0;
function test(name, run) { run(); passed++; console.log(`PASS ${name}`); }
const key = (p) => `${p.x},${p.y},${p.z}`;
function fixture() {
  const blocks = new Map(), entities = [], sounds = [], logs = [];
  let nextId = 0, time = 100000;
  const dimension = {
    id: config.dimension,
    getBlock(p) { if (this.unloaded) return undefined; return blocks.get(key(p)) ?? block("minecraft:air", p); },
    getEntities() { return entities.filter((e) => e.isValid); },
    spawnEntity(typeId, location) {
      const entity = { typeId, location, id: `e${++nextId}`, isValid: true, properties: {},
        setProperty(k, v) { this.properties[k] = v; }, setRotation(v) { this.rotation = v; },
        remove() { this.isValid = false; } };
      entities.push(entity); return entity;
    },
  };
  function block(typeId, location, states = {}) {
    return { typeId, location, dimension,
      isAir: typeId === "minecraft:air", isSolid: ["minecraft:white_concrete", "minecraft:stone"].includes(typeId),
      permutation: { getState: (name) => states[name] } };
  }
  function put(x, y, z, type = "minecraft:white_concrete", states) {
    const value = block(type, { x, y, z }, states); blocks.set(key(value.location), value); return value;
  }
  function video(bottom = 255, z = -86) {
    for (let x = 132; x <= 142; x++) for (let y = bottom; y < bottom + 4; y++) put(x, y, z);
  }
  function map(bottom = 94, z = 243) {
    for (let col = 0; col < 3; col++) for (let row = 0; row < 3; row++) {
      if (row === 2 && col === 2) continue;
      put(15 - col, bottom + row, z, (col + row) % 2 ? "minecraft:frame" : "minecraft:glow_frame",
        { facing_direction: 2, item_frame_map_bit: true });
      put(15 - col, bottom + row, z + 1, "minecraft:stone");
    }
  }
  function player(id, location = { x: 137, y: 257, z: -82 }) {
    return { id, typeId: "minecraft:player", dimension, location, messages: [],
      sendMessage(m) { this.messages.push(m); },
      stopSound(sound) { sounds.push({ action: "stopSound", id, sound }); },
      playSound(sound, options) {
        const handle = { stopped: false, seek: undefined, volume: options.volume,
          stop() { this.stopped = true; sounds.push({ action: "stop", id }); },
          seekTo(t) { this.seek = t; }, setVolume(v) { this.volume = v; } };
        sounds.push({ action: "play", id, sound, options, handle }); return handle;
      } };
  }
  const players = [player("front")], system = { currentTick: 0 };
  const world = { getDimension: () => dimension, getAllPlayers: () => players };
  const core = createWallDisplays({ world, system, media, now: () => time, log: (m) => logs.push(m) });
  function press(type = "minecraft:stone_button", at = core.status().screen?.button) {
    system.currentTick++; core.button({ block: block(type, at), source: players[0] });
  }
  const plays = () => sounds.filter((s) => s.action === "play");
  return { dimension, blocks, entities, sounds, players, core, put, video, map, press, player, plays, logs,
    advance(seconds) { time += seconds * 1000; system.currentTick++; core.tick(); } };
}

test("unique real 11x4 wall computes lower-left control and one entity", () => {
  const f = fixture(); f.video(); f.core.scan();
  assert.equal(f.core.status().videoStatus, "ready");
  assert.deepEqual(f.core.status().screen.button, { x: 131, y: 255, z: -85 });
  assert.equal(f.entities.length, 1); assert.equal(f.core.status().on, false);
  assert.deepEqual(f.entities[0].location, f.core.status().screen.origin);
  assert.deepEqual(f.entities[0].rotation, { x: 0, y: 0 });
  assert.equal(f.entities[0].properties["ichiyon:frame"], -1);
  f.core.scan(); assert.equal(f.entities.length, 1);
});
test("missing, ambiguous, larger uniform, occluded and unloaded screens fail closed", () => {
  const f = fixture(); assert.equal(detectVideoScreen(f.dimension).status, "not_found");
  f.video(251); f.video(257); assert.equal(detectVideoScreen(f.dimension).status, "ambiguous");
  const g = fixture(); g.video(); g.put(132, 259, -86); assert.equal(detectVideoScreen(g.dimension).status, "not_found");
  const h = fixture(); h.video(); h.put(135, 256, -85); assert.equal(detectVideoScreen(h.dimension).status, "not_found");
  h.dimension.unloaded = true; assert.equal(detectVideoScreen(h.dimension).status, "unloaded");
});
test("all vanilla button names accepted; foreign namespace and nonbuttons rejected", () => {
  for (const type of ["stone", "wooden", "oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry", "bamboo", "pale_oak", "crimson", "warped", "polished_blackstone"]) assert(isVanillaButton(`minecraft:${type}_button`));
  for (const type of ["custom:stone_button", "minecraft:lever", "minecraft:button"]) assert(!isVanillaButton(type));
});
test("cold OFF, ON frame zero, time advance, loop, OFF and re-ON reset", () => {
  const f = fixture(); f.video(); f.core.scan(); f.press();
  assert.equal(f.core.status().frame, 0); assert(f.core.status().on);
  assert.equal(f.plays()[0].handle.seek, 0);
  assert.deepEqual(f.plays()[0].options.location, f.core.status().screen.center);
  f.advance(2.5); assert.equal(f.core.status().frame, 50);
  f.advance(media.duration - 2.5); assert.equal(f.core.status().frame, 0);
  assert.equal(f.plays().length, 2); assert(f.plays()[0].handle.stopped);
  assert.equal(f.plays()[1].handle.seek, 0);
  f.press(); assert.equal(f.core.status().on, false); assert.equal(f.core.status().frame, -1);
  assert(f.plays()[1].handle.stopped); f.advance(3); f.press(); assert.equal(f.core.status().frame, 0);
});
test("button destruction/replacement does not change state; alternate button operates", () => {
  const f = fixture(); f.video(); f.core.scan(); f.press();
  f.advance(1); f.core.scan(); assert(f.core.status().on);
  f.press("minecraft:pale_oak_button"); assert(!f.core.status().on);
  f.press("other:stone_button"); assert(!f.core.status().on);
  f.press("minecraft:stone_button", { x: 130, y: 255, z: -85 }); assert(!f.core.status().on);
});
test("front hemisphere only, 64 block radius, no double attenuation and correct dimension", () => {
  const f = fixture(); f.video(); f.core.scan(); const screen = f.core.status().screen;
  const p = f.players[0]; assert(audienceGain(p, screen) > 0);
  assert.equal(config.video.audienceRadius, 64);
  for (const distance of [1, 8, 16, 32, 48, 63]) {
    p.location = { ...screen.center, z: screen.center.z + distance }; assert.equal(audienceGain(p, screen), 1);
  }
  p.location.z = screen.center.z + 64; assert.equal(audienceGain(p, screen), 0);
  p.location.z = screen.center.z - 1; assert.equal(audienceGain(p, screen), 0);
  p.location.z = screen.center.z + 1; p.dimension = { id: "minecraft:nether" }; assert.equal(audienceGain(p, screen), 0);
});
test("late arrival seeks current position; exits stop only their own handle", () => {
  const f = fixture(); f.video(); f.core.scan();
  f.players.push(f.player("behind", { x: 137, y: 257, z: -90 }), f.player("far", { x: 137, y: 257, z: 0 }));
  f.press(); assert.deepEqual(f.plays().map((p) => p.id), ["front"]);
  f.advance(4); f.players.push(f.player("late")); f.advance(0.5);
  assert.equal(f.plays()[1].handle.seek, 4.5);
  f.players[0].location.z = 0; f.advance(0.05); assert(f.plays()[0].handle.stopped);
  assert(!f.plays()[1].handle.stopped);
  f.core.leave("late"); assert(f.plays()[1].handle.stopped);
});
test("moving listener keeps full gain inside audience range without restarting the track", () => {
  const f = fixture(); f.video(); f.core.scan(); f.press(); const start = f.plays()[0].handle.volume;
  f.players[0].location.z += 30; f.advance(0.1);
  assert.equal(start, 1); assert.equal(f.plays()[0].handle.volume, start); assert.equal(f.plays().length, 1);
});
test("reload clears persisted helper and sound; unloaded entity resets OFF", () => {
  const f = fixture(); f.video();
  const old = f.dimension.spawnEntity(config.video.entity, { x: 137, y: 255, z: -85 }); old.setProperty("ichiyon:frame", 123);
  f.core.scan(); assert(!old.isValid); f.press(); f.entities.at(-1).isValid = false; f.advance(0.1);
  assert(!f.core.status().on); assert(f.plays()[0].handle.stopped);
  f.core.scan(); assert.equal(f.entities.filter((e) => e.isValid).length, 1); assert(!f.core.status().on);
  assert(f.sounds.some((s) => s.action === "stopSound" && s.sound === media.sound));
});
test("missing SoundInstance API fails explicitly and stops the started sound", () => {
  const f = fixture(); f.video(); f.core.scan(); f.players[0].playSound = () => undefined;
  assert.throws(() => f.press(), /SoundInstance API/); f.core.reset(); assert(!f.core.status().on);
  assert(f.sounds.some((s) => s.action === "stopSound"));
});
test("map detection uses eight facing map BLOCKS with relative Y and missing upper-right", () => {
  const f = fixture(); f.map(92, 244); const before = [...f.blocks.entries()];
  const result = detectMapWall(f.dimension); assert.equal(result.status, "ready");
  assert.equal(result.wall.frames.length, 8); assert.equal(result.refreshSupported, false);
  assert.deepEqual(result.wall.button, { x: 16, y: 92, z: 244 }); assert.deepEqual([...f.blocks.entries()], before);
});
test("wrong-facing, empty, ninth and ambiguous map layouts are rejected", () => {
  for (const states of [{ facing_direction: 3, item_frame_map_bit: true }, { facing_direction: 2, item_frame_map_bit: false }]) {
    const f = fixture(); f.map(); f.put(15, 94, 243, "minecraft:frame", states); assert.equal(detectMapWall(f.dimension).status, "not_found");
  }
  const f = fixture(); f.map(); f.put(13, 96, 243, "minecraft:frame"); assert.equal(detectMapWall(f.dimension).status, "not_found");
  const g = fixture(); g.map(90); g.map(95); assert.equal(detectMapWall(g.dimension).status, "ambiguous");
});
test("map button immediately rescans and honestly reports no recalc without writes", () => {
  const f = fixture(); f.video(); f.map(); f.core.scan(); const button = f.core.status().mapWall.button;
  f.blocks.delete("15,94,243"); f.press("minecraft:bamboo_button", button);
  assert.equal(f.core.status().mapStatus, "not_found");
  f.map(); const before = [...f.blocks.entries()]; f.press("minecraft:polished_blackstone_button", button);
  assert.equal(f.core.status().mapStatus, "ready"); assert.deepEqual([...f.blocks.entries()], before);
  assert.match(f.players[0].messages.at(-1), /8\/8.*no existing-filled-map/);
});
test("runtime modules have no fake map APIs or world broadcast audio", () => {
  const source = readFileSync(new URL("../minecraft/behavior_packs/import_structures/scripts/wall_displays_core.js", import.meta.url), "utf8");
  for (const forbidden of ["world.playSound", "dimension().playSound", "setPermutation(", "setType(", "updateMap(", "refreshMap(", "runCommand("]) assert(!source.includes(forbidden));
});
test("ready logs prove detected coordinates once and repeated scans stay quiet", () => {
  const f = fixture(); f.video(); f.map(); f.core.scan();
  assert.deepEqual(f.logs, [
    "[Video screen] ready dimension=minecraft:overworld bottom=255 wallPlaneZ=-86 button=(131,255,-85)",
    "[Map wall] ready dimension=minecraft:overworld bottom=94 wallPlaneZ=244 framePlaneZ=243 button=(16,94,243) frames=8; automatic map recalculation unavailable",
  ]);
  for (let i = 0; i < 10; i++) { f.core.scan(); f.core.scanMap(); }
  assert.equal(f.logs.length, 2);
  f.press(); assert.equal(f.logs.length, 2);
});
test("a moved detection logs new coordinates even while status remains ready", () => {
  const f = fixture(); f.video(); f.map(); f.core.scan(); f.blocks.clear();
  f.video(256, -87); f.map(93, 245); f.core.scan();
  assert.equal(f.logs.length, 4);
  assert.match(f.logs[2], /bottom=256 wallPlaneZ=-87 button=\(131,256,-86\)/);
  assert.match(f.logs[3], /bottom=93 wallPlaneZ=246 framePlaneZ=245 button=\(16,93,245\)/);
  f.core.scan(); assert.equal(f.logs.length, 4);
});
test("lost detection logs status once and recovery proves coordinates once again", () => {
  const f = fixture(); f.video(); f.map(); f.core.scan();
  f.dimension.unloaded = true; f.core.scan();
  assert.equal(f.logs.length, 4);
  assert(f.logs.slice(2).every((message) => message.includes("unloaded") && !message.includes("button=")));
  f.core.scan(); assert.equal(f.logs.length, 4);
  f.dimension.unloaded = false; f.core.scan();
  assert.deepEqual(f.logs.slice(4), f.logs.slice(0, 2));
  f.core.scan(); assert.equal(f.logs.length, 6);
});
console.log(`${passed} wall display runtime tests passed`);
