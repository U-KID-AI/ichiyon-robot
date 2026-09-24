import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createWallDisplays, createVideoDisplays, detectVideoScreen, detectFloorButton, detectMapWall, isVanillaButton, audienceGain } from "../minecraft/behavior_packs/import_structures/scripts/wall_displays_core.js";
import { WALL_DISPLAYS as config, BIG_VIDEO_DISPLAY as bigConfig } from "../minecraft/behavior_packs/import_structures/scripts/wall_displays_config.js";
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
      isAir: typeId === "minecraft:air", isSolid: ["minecraft:white_concrete", "minecraft:black_concrete", "minecraft:stone"].includes(typeId),
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
  return { dimension, blocks, entities, sounds, players, core, put, video, map, press, player, plays, logs, world, system, now: () => time,
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
function bigWall(f, left = -18, bottom = 66, z = -189) {
  for (let x = left; x < left + 24; x++) for (let y = bottom; y < bottom + 11; y++) f.put(x, y, z, "minecraft:black_concrete");
}
function floorButton(f, x = -16, y = 66, z = -185, type = "minecraft:cherry_button", facing = 1) {
  f.put(x, y - 1, z, "minecraft:stone");
  return f.put(x, y, z, type, { facing_direction: facing });
}
function multiFixture() {
  const f = fixture(); f.video(); bigWall(f); floorButton(f);
  const bigMedia = { ...media, fps: 20, frameCount: 10204, duration: 510.2, sound: "ichiyon.video_screen_big.audio" };
  const group = createVideoDisplays({ world: f.world, system: f.system, now: f.now, log: (m) => f.logs.push(m), displays: [
    { id: "small", config, media }, { id: "big", config: bigConfig, media: bigMedia },
  ] });
  group.scan();
  const press = (id) => {
    f.system.currentTick++;
    const p = group.status()[id].screen.button;
    group.button({ block: { typeId: "minecraft:stone_button", location: p, dimension: f.dimension }, source: f.players[0] });
  };
  return { ...f, group, bigMedia, pressDisplay: press };
}
test("big detector finds actual 24x11 black plane near anchor without fixed coordinates", () => {
  const f = fixture(); bigWall(f);
  const before = [...f.blocks.entries()];
  const result = detectVideoScreen(f.dimension, bigConfig.video);
  assert.equal(result.status, "ready");
  assert.deepEqual([result.screen.left, result.screen.right, result.screen.bottom, result.screen.top, result.screen.z], [-18, 5, 66, 76, -189]);
  assert.equal(result.screen.center.z, -187.98);
  assert.deepEqual([...f.blocks.entries()], before);
  bigWall(f, -18, 66, -191); assert.equal(detectVideoScreen(f.dimension, bigConfig.video).status, "ambiguous");
});
test("big detector rejects nonblack, larger wall, outside bounds, occlusion and missing chunks", () => {
  for (const mutate of [f => f.put(-19, 70, -189, "minecraft:black_concrete"),
    f => f.put(-10, 70, -188, "minecraft:stone"), f => { f.dimension.unloaded = true; }]) {
    const f = fixture(); bigWall(f); mutate(f); assert.notEqual(detectVideoScreen(f.dimension, bigConfig.video).status, "ready");
  }
  const f = fixture(); bigWall(f, 30); assert.equal(detectVideoScreen(f.dimension, bigConfig.video).status, "not_found");
  f.blocks.clear(); for (let x = -18; x < 6; x++) for (let y = 66; y < 77; y++) f.put(x, y, -189);
  assert.equal(detectVideoScreen(f.dimension, bigConfig.video).status, "not_found");
});
test("floor button detector requires one supported vanilla button and solid floor", () => {
  const f = fixture(); assert.equal(detectFloorButton(f.dimension, bigConfig.video.floorButton).status, "not_found");
  floorButton(f); assert.deepEqual(detectFloorButton(f.dimension, bigConfig.video.floorButton).button, { x: -16, y: 66, z: -185 });
  floorButton(f, -15); assert.equal(detectFloorButton(f.dimension, bigConfig.video.floorButton).status, "ambiguous");
  f.blocks.clear(); floorButton(f, -16, 66, -185, "minecraft:stone_button", 2);
  assert.equal(detectFloorButton(f.dimension, bigConfig.video.floorButton).status, "not_found");
  f.blocks.clear(); floorButton(f, -16, 66, -185, "minecraft:warped_button", "up");
  assert.equal(detectFloorButton(f.dimension, bigConfig.video.floorButton).status, "ready");
  f.blocks.delete("-16,65,-185"); assert.equal(detectFloorButton(f.dimension, bigConfig.video.floorButton).status, "not_found");
});
test("two independent helpers, buttons, frame clocks, loops and audio", () => {
  const f = multiFixture(); f.players.push(f.player("big", { x: -5, y: 67, z: -175 }));
  assert.equal(f.entities.filter(e => e.isValid).length, 2);
  assert(!f.group.status().small.on && !f.group.status().big.on);
  f.pressDisplay("big"); assert(f.group.status().big.on); assert(!f.group.status().small.on);
  assert.equal(f.plays().at(-1).sound, f.bigMedia.sound); assert.equal(f.plays().at(-1).handle.seek, 0);
  f.advance(2); f.group.tick(); assert.equal(f.group.status().big.frame, 40);
  f.pressDisplay("small"); assert.equal(f.group.status().small.frame, 0); assert.equal(f.group.status().big.frame, 40);
  f.advance(1); f.group.tick(); assert.equal(f.group.status().big.frame, 60); assert.equal(f.group.status().small.frame, 20);
  const smallHandle = f.plays().find(s => s.sound === media.sound).handle;
  f.pressDisplay("big"); assert(!f.group.status().big.on); assert(f.group.status().small.on); assert(!smallHandle.stopped);
  f.pressDisplay("big"); assert.equal(f.group.status().big.frame, 0);
  f.advance(f.bigMedia.duration); f.group.tick(); assert.equal(f.group.status().big.frame, 0);
  assert.equal(f.plays().filter(s => s.sound === f.bigMedia.sound).at(-1).handle.seek, 0);
});
test("big button destruction and replacement preserves playback without block writes", () => {
  const f = multiFixture(); f.pressDisplay("big");
  f.blocks.delete("-16,66,-185"); f.group.scan(); assert(f.group.status().big.on);
  assert.equal(f.group.status().big.buttonStatus, "not_found");
  const replacement = floorButton(f, -16, 66, -185, "minecraft:crimson_button");
  f.system.currentTick++; f.group.button({ block: replacement }); assert(!f.group.status().big.on);
  f.pressDisplay("big"); floorButton(f, -15); f.group.scan();
  f.system.currentTick++; f.group.button({ block: replacement }); assert(f.group.status().big.on);
  assert.equal(f.group.status().big.buttonStatus, "ambiguous");
});
test("big rectangular audience includes rear seats but excludes behind, sides and wrong Y/dimension", () => {
  const f = multiFixture(), screen = f.group.status().big.screen, p = f.players[0];
  for (const location of [{ x: -17, y: 66, z: -186 }, { x: 4, y: 69, z: -168 }, { x: -5, y: 67, z: -175 }]) {
    p.location = location; assert.equal(audienceGain(p, screen, bigConfig), 1);
  }
  for (const location of [{ x: -5, y: 66, z: -190 }, { x: -21, y: 66, z: -175 },
    { x: 9, y: 66, z: -175 }, { x: -5, y: 66, z: -164 }, { x: -5, y: 80, z: -175 }]) {
    p.location = location; assert.equal(audienceGain(p, screen, bigConfig), 0);
  }
  p.location = { x: -5, y: 66, z: -175 }; p.dimension = { id: "minecraft:nether" };
  assert.equal(audienceGain(p, screen, bigConfig), 0);
});
test("one display failure/unload never resets the other and reload creates no duplicate helpers", () => {
  const f = multiFixture(); f.pressDisplay("small"); f.pressDisplay("big");
  f.entities.find(e => e.typeId === bigConfig.video.entity).isValid = false;
  f.group.tick(); assert(!f.group.status().big.on); assert(f.group.status().small.on);
  f.group.scan(); assert.equal(f.entities.filter(e => e.isValid).length, 2);
  const stale = f.dimension.spawnEntity(bigConfig.video.entity, { x: -5, y: 66, z: -188 });
  f.group.recover(stale); assert(!stale.isValid); assert(f.group.status().small.on);
  f.group.scan(); assert.equal(f.entities.filter(e => e.isValid).length, 2);
  const small = f.entities.find(e => e.typeId === config.video.entity && e.isValid);
  const large = f.entities.find(e => e.typeId === bigConfig.video.entity && e.isValid);
  large.setProperty = () => { throw new Error("fixture big failure"); };
  f.group.recover(large); assert(small.isValid); assert(f.group.status().small.on);
});
console.log(`${passed} wall display runtime tests passed`);
