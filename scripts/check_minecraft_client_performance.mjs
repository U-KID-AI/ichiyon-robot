import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import { readClientPerformance, logClientPerformance } from "../minecraft/behavior_packs/import_structures/scripts/client_performance.js";
import { createPosterRuntime } from "../minecraft/behavior_packs/import_structures/scripts/poster_core.js";

const player = () => ({
  name: "TestMobile", id: "entity-id-must-not-be-in-performance-log",
  clientSystemInfo: { platformType: "Mobile", memoryTier: 0, maxRenderDistance: 12, locale: "ja_JP",
    deviceId: "private-device-id", ip: "private-ip", token: "private-token" },
  graphicsMode: "Deferred", inputInfo: { lastInputModeUsed: "Touch" },
  dimension: { id: "minecraft:overworld" }, location: { x: 1, y: 70, z: 1 },
  getComponent() {}, getGameMode: () => "Creative", selectedSlotIndex: 0,
});
const timestamp = "2026-10-01T00:00:00.000Z";
test("API whitelist retains zero memory tier and logs a single JSON line", () => {
  const p = player(); p.name = "Test\nMobile";
  const value = readClientPerformance(p, () => timestamp), logs = [];
  assert.equal(value.clientSystemInfo.memoryTier, 0);
  assert.equal(value.clientSystemInfo.locale, "ja_JP");
  assert.equal(value.graphicsMode, "Deferred");
  assert.equal(value.clientSystemInfo.platformType, "Mobile");
  assert.equal(value.clientSystemInfo.maxRenderDistance, 12);
  assert.equal(value.inputInfo.lastInputModeUsed, "Touch");
  assert.equal(value.timestamp, timestamp);
  logClientPerformance(value, line => logs.push(line));
  assert.equal(logs.length, 1);
  assert.equal(logs[0].split("\n").length, 1);
  assert.equal(JSON.parse(logs[0].slice(logs[0].indexOf("{"))).player_name, p.name);
  assert(!logs[0].includes("private"));
  assert(!logs[0].includes("entity-id"));
  assert.doesNotThrow(() => logClientPerformance(value, () => { throw Error("logger failed"); }));
});
test("each getter fails independently without serializing errors or API objects", () => {
  const fields = ["player_name", "clientSystemInfo.platformType", "clientSystemInfo.memoryTier",
    "clientSystemInfo.maxRenderDistance", "clientSystemInfo.locale", "graphicsMode", "inputInfo.lastInputModeUsed"];
  const get = (value, path) => path.split(".").reduce((v, key) => v[key], value);
  for (const path of fields) {
    const key = path === "player_name" ? "name" : path.split(".").at(-1);
    const p = player(), target = path.startsWith("clientSystemInfo.") ? p.clientSystemInfo : path.startsWith("inputInfo.") ? p.inputInfo : p;
    Object.defineProperty(target, key, { get() { throw Error("private-token"); } });
    const value = readClientPerformance(p);
    assert.equal(get(value, path), null);
    assert.equal(value.unavailable[path], "error");
    for (const other of fields) {
      if (other !== path) assert.notEqual(get(value, other), null);
    }
    assert(!JSON.stringify(value).includes("private"));
  }
  for (const key of ["clientSystemInfo", "inputInfo"]) {
    const p = player();
    Object.defineProperty(p, key, { get() { throw Error("private-token"); } });
    assert.doesNotThrow(() => readClientPerformance(p));
    assert.equal(readClientPerformance(p).graphicsMode, "Deferred");
  }
  const missing = readClientPerformance({});
  assert.equal(missing.clientSystemInfo.memoryTier, null);
  assert.equal(missing.unavailable["clientSystemInfo.memoryTier"], "unavailable");
  const p = player();
  p.graphicsMode = { toJSON() { throw Error("private-token"); } };
  p.clientSystemInfo.maxRenderDistance = NaN;
  assert.equal(readClientPerformance(p).unavailable.graphicsMode, "invalid");
  assert.equal(readClientPerformance(p).clientSystemInfo.maxRenderDistance, null);
  assert.equal(readClientPerformance(p, () => { throw Error("secret"); }).unavailable.timestamp, "error");
});

test("Script memory enum and Console are never mapped to RP tier or device model", () => {
  for (const raw of [0, 1, 2, 3, 4, 42]) {
    const p = player(); p.clientSystemInfo.memoryTier = raw; p.clientSystemInfo.platformType = "Console";
    const value = readClientPerformance(p);
    assert.equal(value.clientSystemInfo.memoryTier, raw);
    assert.equal(value.clientSystemInfo.platformType, "Console");
    assert.equal(value.schema, "ichiyon.client_performance.v2");
    assert(!/memory_performance_tier|Switch|Xbox|GPU|SoC|RAM|deviceId|token/.test(JSON.stringify(value)));
    assert.equal(value.memoryTier, undefined);
  }
});

function bridgeFixture(p = player()) {
  const source = readFileSync(new URL("../minecraft/behavior_packs/import_structures/scripts/main.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "");
  const logs = [], subscriptions = {}, delayed = [], results = [];
  const context = vm.createContext({
    console: { warn: line => logs.push(line) },
    system: { currentTick: 0, runInterval() {}, runTimeout(fn) { delayed.push(fn); }, run() {} },
    world: { afterEvents: new Proxy({}, { get: (_, event) => ({ subscribe: fn => { subscriptions[event] = fn; } }) }),
      getAllPlayers: () => [p] },
    variables: { get() {} }, secrets: { get() {} },
    cosmeticsDigest: "test", managedPosters: [], cosmetics: {}, handleAvatarCommand() {},
    EntityComponentTypes: { Inventory: "inventory", Health: "health" },
    BlockPermutation: {}, ItemStack: class {}, createPosterRuntime,
    readClientPerformance, logClientPerformance: value => logClientPerformance(value, line => logs.push(line)),
    capture: (...args) => results.push(args),
  });
  vm.runInContext(source + "\npostResult = async (...args) => capture(...args);\nglobalThis.bridge = { joinDiagnostics, playerDiagnosticPayload, handleJoinHistory };", context);
  return { ...context.bridge, p, logs, subscriptions, delayed, results };
}
test("real join hook, delayed probes, sync command payload and bounded join history", async () => {
  const f = bridgeFixture();
  f.subscriptions.playerSpawn({ player: f.p, initialSpawn: true });
  for (const fn of f.delayed) fn();
  assert.equal(f.logs.filter(line => line.startsWith("[NaritaBridge:client_performance]")).length, 1);
  assert(f.joinDiagnostics.some(e => e.event === "client_performance" && e.client_performance.clientSystemInfo.memoryTier === 0));
  assert(f.joinDiagnostics.filter(e => e.event === "inventory_probe").every(e => e.client_performance.inputInfo.lastInputModeUsed === "Touch"));
  const diagnostic = f.playerDiagnosticPayload("TestMobile");
  assert.equal(diagnostic.client_performance.clientSystemInfo.platformType, "Mobile");
  assert.equal(diagnostic.client_performance.clientSystemInfo.locale, "ja_JP");
  f.p.clientSystemInfo.locale = "en_US";
  assert.equal(f.playerDiagnosticPayload("TestMobile").client_performance.clientSystemInfo.locale, "en_US");
  assert.equal(f.playerDiagnosticPayload("Absent").client_performance, null);
  f.subscriptions.playerSpawn({ player: f.p, initialSpawn: false });
  assert.equal(f.logs.filter(line => line.startsWith("[NaritaBridge:client_performance]")).length, 1);
  for (let i = 0; i < 80; i++) {
    f.p.name = "Test" + i;
    f.subscriptions.playerSpawn({ player: f.p, initialSpawn: true });
  }
  await f.handleJoinHistory({ request_id: "history" });
  const message = f.results.at(-1)[3], history = JSON.parse(message);
  assert(message.length <= 1700);
  assert(history.events_truncated);
  assert.equal(history.events[0].client_performance.player_name, "Test79");
  assert.equal(history.events[0].client_performance.clientSystemInfo.locale, "en_US");
  assert(f.joinDiagnostics.length <= 300);
});
test("throwing client API does not break the real join hook or inventory probes", () => {
  const p = player();
  Object.defineProperty(p, "clientSystemInfo", { get() { throw Error("private-token"); } });
  const f = bridgeFixture(p);
  assert.doesNotThrow(() => f.subscriptions.playerSpawn({ player: p, initialSpawn: true }));
  for (const fn of f.delayed) assert.doesNotThrow(fn);
  assert(f.joinDiagnostics.some(e => e.event === "inventory_probe"));
  const log = f.logs.find(line => line.startsWith("[NaritaBridge:client_performance]"));
  assert(!log.includes("private-token"));
  assert(log.includes('"memoryTier":null'));
});
