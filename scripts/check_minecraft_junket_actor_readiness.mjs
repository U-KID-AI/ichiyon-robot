import assert from "node:assert/strict";
import { readStableFixtureSnapshot } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/resilience_probe.js";

const rows = id => [{ id, expected: { role: "reception" } }];
function missing(ids = {}) {
  const error = new Error("reception: found 0");
  error.missingFixtureActors = [{ role: "reception", global_role_count: 0,
    local_query: [{ id: "persisted-guide", type: "junket:guide" }] }];
  error.loadedFixtureIdentities = ids;
  return error;
}

{
  let tick = 0, reads = 0;
  const record = {};
  const result = await readStableFixtureSnapshot({
    read() { reads++; if (tick < 6) throw missing(); return rows("persisted-guide"); },
    async wait(ticks) { tick += ticks; }, record,
  });
  assert.equal(result[0].id, "persisted-guide");
  assert.equal(record.waited_ticks, 8);
  assert.equal(reads, 5);
  assert.equal(record.settled, true);
  assert.equal(record.first_missing[0].local_query[0].id, "persisted-guide");
}
{
  let tick = 0;
  const record = {};
  await assert.rejects(readStableFixtureSnapshot({ read() { throw missing(); },
    async wait(ticks) { tick += ticks; }, record, timeoutTicks: 6 }), /timed out/);
  assert.equal(tick, 6);
  assert.equal(record.settled, false);
}
for (const reason of ["dead fixture", "wrong type", "displaced", "wrong rotation", "residual velocity", "found 2"]) {
  let waited = 0;
  await assert.rejects(readStableFixtureSnapshot({ read() { throw new Error(reason); },
    async wait(ticks) { waited += ticks; } }), new RegExp(reason));
  assert.equal(waited, 0, `${reason} cannot be hidden by an anchor/repair during a readiness wait`);
}
{
  let reads = 0;
  await assert.rejects(readStableFixtureSnapshot({ read() { return rows(++reads === 1 ? "old" : "replacement"); },
    async wait() {} }), /identity changed/);
}
{
  let reads = 0;
  await assert.rejects(readStableFixtureSnapshot({ read() {
    if (++reads === 1) throw missing({ reception: "old" });
    return rows("replacement");
  }, async wait() {} }), /identity changed/);
}
{
  let allowed = true;
  await assert.rejects(readStableFixtureSnapshot({ read() { return rows("same"); },
    guard() { if (!allowed) throw new Error("guard cancelled"); },
    async wait() { allowed = false; } }), /guard cancelled/);
}
console.log("PASS read-only actor readiness: delayed same-ID visibility, bounded missing timeout, immediate defects, no identity replacement and cancellation (offline)");
