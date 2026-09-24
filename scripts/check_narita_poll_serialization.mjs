import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import { createPosterRuntime } from "../minecraft/behavior_packs/import_structures/scripts/poster_core.js";

const source = readFileSync(new URL("../minecraft/behavior_packs/import_structures/scripts/main.js", import.meta.url), "utf8")
  .replace(/^import .*;\r?\n/gm, "");
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture() {
  const logs = [], requests = [];
  const f = { get: async () => ({ status: 200, body: '{"command":null}' }), post: async () => ({ status: 200 }), execute: async () => false };
  const context = vm.createContext({
    console: { warn: text => logs.push(text) },
    system: { currentTick: 0, runInterval() {}, runTimeout() {}, run() {} },
    world: { afterEvents: new Proxy({}, { get: () => ({ subscribe() {} }) }), getAllPlayers: () => [] },
    variables: { get: name => name === "MINECRAFT_BRIDGE_GUILD_ID" ? "guild" : undefined },
    secrets: { get: () => "test-secret-do-not-log" },
    HttpHeader: class { constructor(name, value) { Object.assign(this, { name, value }); } },
    HttpRequest: class { constructor(uri) {
      if (f.failConstruction) throw new Error("test-secret-do-not-log");
      this.uri = uri;
    } },
    HttpRequestMethod: { Get: "GET", Post: "POST" },
    http: { request: async request => {
      requests.push(request);
      assert.equal(request.timeout, 15);
      return request.method === "GET" ? f.get(request) : f.post(request);
    } },
    cosmeticsDigest: "test-digest",
    managedPosters: [], createPosterRuntime, BlockPermutation: {}, ItemStack: class {},
    cosmetics: { handleCommand: (...args) => f.execute(...args) },
    handleAvatarCommand: async () => false,
  });
  vm.runInContext(source + "\nglobalThis.bridge = { pollOnce, httpDiagnostics };", context);
  return Object.assign(f, context.bridge, { logs, requests });
}

test("one guard spans GET, parse, execution and result POST without duplicate execution", async () => {
  const f = fixture(), get = deferred(), execute = deferred(), post = deferred();
  let executions = 0;
  f.get = () => get.promise;
  f.post = () => post.promise;
  f.execute = async (command, helpers) => {
    executions++;
    await execute.promise;
    await helpers.postResult(command.request_id, "succeeded", "ok", "");
    return true;
  };
  const first = f.pollOnce();
  await f.pollOnce();
  assert.equal(f.requests.length, 1);
  assert.equal(f.httpDiagnostics.pendingHttpRequests, 1);
  get.resolve({ status: 200, body: JSON.stringify({ command: { type: "test", request_id: "one" } }) });
  await flush();
  await f.pollOnce();
  assert.equal(executions, 1);
  assert.equal(f.httpDiagnostics.pollPhase, "execute");
  assert.equal(f.httpDiagnostics.activePolls, 1);
  execute.resolve();
  await flush();
  await f.pollOnce();
  assert.equal(f.requests.length, 2);
  assert.equal(f.httpDiagnostics.pollPhase, "result_post");
  assert.equal(f.httpDiagnostics.pendingHttpRequests, 1);
  assert.equal(f.httpDiagnostics.skippedPolls, 3);
  post.resolve({ status: 200 });
  await first;
  assert.equal(f.httpDiagnostics.pollInFlight, false);
  assert.equal(f.httpDiagnostics.activePolls, 0);
  assert.equal(f.httpDiagnostics.pendingHttpRequests, 0);
  assert.equal(f.httpDiagnostics.lastPollSuccess, true);
  f.get = async () => ({ status: 200, body: '{"command":null}' });
  await f.pollOnce();
  assert.equal(f.requests.length, 3);
  assert.equal(executions, 1);
});

for (const failure of ["get", "http", "parse", "null", "execute", "post", "post_status", "constructor"]) {
  test(`${failure} failure releases the guard and never logs request secrets`, async () => {
    const f = fixture();
    const sensitive = new Error("Authorization: Bearer test-secret-do-not-log");
    f.get = async () => ({ status: 200, body: '{"command":{"type":"test","request_id":"one"}}' });
    f.execute = async (command, helpers) => {
      if (failure === "execute") throw sensitive;
      await helpers.postResult(command.request_id, "succeeded", "ok", "");
      return true;
    };
    if (failure === "get") f.get = async () => { throw sensitive; };
    if (failure === "http") f.get = async () => ({ status: 503, body: "secret" });
    if (failure === "parse") f.get = async () => ({ status: 200, body: "test-secret-do-not-log" });
    if (failure === "null") f.get = async () => ({ status: 200, body: "null" });
    if (failure === "post") f.post = async () => { throw sensitive; };
    if (failure === "post_status") f.post = async () => ({ status: 500 });
    if (failure === "constructor") f.failConstruction = true;
    await f.pollOnce();
    assert.equal(f.httpDiagnostics.pollInFlight, false);
    assert.equal(f.httpDiagnostics.pendingHttpRequests, 0);
    assert.equal(f.httpDiagnostics.lastPollSuccess, false);
    assert.ok(!JSON.stringify([f.logs, f.httpDiagnostics]).includes("test-secret-do-not-log"));
    f.failConstruction = false;
    f.get = async () => ({ status: 200, body: '{"command":null}' });
    await f.pollOnce();
    assert.equal(f.httpDiagnostics.lastPollSuccess, true);
  });
}
