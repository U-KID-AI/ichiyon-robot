// Block migration and binding tests. This does not prove mobile client rendering.
import assert from "node:assert/strict";
import { createServiceControls } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/service_controls.js";
import { ensureServiceControls, inspectLegacyServiceControls } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/service_control_install.js";

const air = { name: "minecraft:air", states: {} };
const button = { name: "minecraft:stone_button", states: { facing_direction: 1, button_pressed_bit: 0 } };
const sign = { name: "minecraft:standing_sign", states: { ground_sign_direction: 4 } };
const support = { name: "minecraft:stone", states: {} };
function control(action, x) {
  return { action, button: [x, 66, 5], sign: [x, 66, 6], support: [x, 65, 5],
    button_block: button, sign_block: sign, support_block: support, text: action === "menu" ? "ゲーム操作" : action,
    source_evidence: { support_preserved: true, button_original: air, sign_original: air } };
}
function harness({ binding = true, changed, snapshotReads = false, implicitLightStates = false } = {}) {
  const legacy = ["reception", "return", "reset", "repair"].map((action, i) => control(action, 2 + i * 2));
  const venue = { id: "sparrow", title: "ウラギリスズメ", origin: [0, 60, 0], size: [30, 20, 30], fixtures: [],
    service_controls: [control("menu", 20)], legacy_service_controls: legacy };
  const cells = new Map(), writes = [];
  function put(at, definition, text = "") {
    const block = { typeId: definition.name, states: { ...definition.states }, text,
      setType(typeId) { writes.push([at.join(","), typeId]); this.typeId = typeId; this.states = {}; this.text = ""; },
      get permutation() {
        const value = states => ({ getAllStates: () => ({ ...states }), withState: (k, v) => value({ ...states, [k]: v }) });
        const light = /^minecraft:light_block_(\d+)$/.exec(this.typeId);
        return value(implicitLightStates && light ? { block_light_level: Number(light[1]), ...this.states } : this.states);
      },
      setPermutation(value) { this.states = value.getAllStates(); },
      getComponent() { return this.typeId === sign.name ? { getText: () => this.text,
        setText: value => { this.text = value; }, setWaxed() {} } : undefined; } };
    cells.set(at.join(","), block); return block;
  }
  for (const row of [venue.service_controls[0], ...legacy]) {
    put(row.support, support);
    put(row.button, row.action === "menu" ? air : button);
    put(row.sign, row.action === "menu" ? air : sign, row.text);
  }
  // Neither an original game button nor an unrelated support may be touched.
  const gameButton = put([25, 66, 5], button), unrelated = put([29, 65, 29], { name: "minecraft:diamond_block", states: {} });
  changed?.({ cells, venue });
  const signal = { subscribe() {} };
  const dynamic = new Map();
  const world = { getDynamicProperty: key => dynamic.get(key),
    setDynamicProperty: (key, value) => value === undefined ? dynamic.delete(key) : dynamic.set(key, value),
    getDimension: () => ({ getBlock: p => {
      const block = cells.get([p.x, p.y, p.z].join(","));
      if (!block || !snapshotReads) return block;
      return { typeId: block.typeId, permutation: block.permutation,
        setType: value => block.setType(value), setPermutation: value => block.setPermutation(value),
        getComponent: name => block.getComponent(name) };
    } }),
    getAllPlayers: () => [], beforeEvents: { playerInteractWithBlock: signal, playerInteractWithEntity: signal },
    afterEvents: { playerLeave: signal } };
  const system = { runTimeout: fn => setImmediate(fn) };
  if (binding) createServiceControls({ world, system, venues: [venue], runtimes: { sparrow: { openReception() {} } } }).install();
  return { venue, cells, writes, world, system, gameButton, unrelated, put, dynamic };
}
let count = 0;
async function test(name, fn) { await fn(); console.log(`OK ${++count} ${name}`); }
await test("new bound panel replaces exact four legacy pairs without touching supports or game controls", async () => {
  const h = harness();
  const logs = [], previousLog = console.log;
  let result;
  try {
    console.log = value => logs.push(value);
    result = await ensureServiceControls(h.world, h.system, h.venue);
  } finally { console.log = previousLog; }
  assert.equal(result.controls, 1); assert.equal(result.legacy.length, 4);
  assert.equal(result.handler_bound, true);
  assert.equal(logs.length, 1);
  assert.ok(logs[0].startsWith("[JUNKET PANEL] initialized: "));
  assert.deepEqual(JSON.parse(logs[0].slice("[JUNKET PANEL] initialized: ".length)), result);
  assert.ok(result.legacy.every(entry => entry.status === "restored"));
  assert.ok(h.writes.every(([cell]) => !cell.includes(",65,")));
  assert.equal(h.gameButton.typeId, button.name); assert.equal(h.unrelated.typeId, "minecraft:diamond_block");
  const before = h.writes.length;
  await ensureServiceControls(h.world, h.system, h.venue);
  assert.equal(h.writes.length, before, "reinitialization never recreates retired controls");
});
await test("unbound new panel retains every old device before failing", async () => {
  const h = harness({ binding: false });
  await assert.rejects(ensureServiceControls(h.world, h.system, h.venue), /本人向け処理/);
  assert.ok(inspectLegacyServiceControls(h.world, h.venue).every(entry => entry.status === "legacy"));
});
await test("mismatched type, states, sign text or missing source states preserve the pair", async () => {
  for (const kind of ["type", "state", "text", "evidence"]) {
    const h = harness({ changed: ({ cells, venue }) => {
      const row = venue.legacy_service_controls[0];
      if (kind === "type") cells.get(row.button.join(",")).typeId = "minecraft:gold_block";
      if (kind === "state") cells.get(row.button.join(",")).states.facing_direction = 4;
      if (kind === "text") cells.get(row.sign.join(",")).text = "利用者が変更した看板";
      if (kind === "evidence") row.source_evidence = { ...row.source_evidence, button_original: "minecraft:air" };
    } });
    const row = h.venue.legacy_service_controls[0];
    const before = [h.cells.get(row.button.join(",")).typeId, h.cells.get(row.sign.join(",")).text];
    const result = await ensureServiceControls(h.world, h.system, h.venue);
    assert.equal(result.legacy[0].status, "retained");
    assert.deepEqual([h.cells.get(row.button.join(",")).typeId, h.cells.get(row.sign.join(",")).text], before);
  }
});
await test("legally pressed buttons can migrate and new panel cells are protected", async () => {
  for (const pressed of [0, 1, false, true]) {
    const h = harness({ changed: ({ cells, venue }) => {
      cells.get(venue.legacy_service_controls[0].button.join(",")).states.button_pressed_bit = pressed;
    } });
    assert.ok((await ensureServiceControls(h.world, h.system, h.venue)).legacy.every(row => row.status === "restored"));
  }
  const h = harness();
  h.venue.legacy_service_controls.push({ ...h.venue.service_controls[0], action: "reception" });
  const result = await ensureServiceControls(h.world, h.system, h.venue);
  assert.equal(result.legacy.at(-1).status, "protected");
  assert.equal(h.cells.get(h.venue.service_controls[0].button.join(",")).typeId, button.name);
});
await test("full source restore and fresh listener installation never revive legacy controls", async () => {
  const h = harness(); await ensureServiceControls(h.world, h.system, h.venue);
  for (const row of [...h.venue.service_controls, ...h.venue.legacy_service_controls]) {
    h.put(row.button, air); h.put(row.sign, air);
  }
  assert.ok((await ensureServiceControls(h.world, h.system, h.venue)).legacy.every(row => row.status === "restored"));
  const fresh = harness();
  for (const row of fresh.venue.legacy_service_controls) { fresh.put(row.button, air); fresh.put(row.sign, air); }
  assert.ok((await ensureServiceControls(fresh.world, fresh.system, fresh.venue)).legacy.every(row => row.status === "restored"));
});
await test("native Block snapshots are reread after replacing an old button with original light", async () => {
  const h = harness({ snapshotReads: true });
  const row = h.venue.legacy_service_controls[1];
  row.source_evidence.button_original = { name: "minecraft:light_block_12", states: {} };
  const result = await ensureServiceControls(h.world, h.system, h.venue);
  assert.ok(result.legacy.every(record => record.status === "restored"));
  const actual = h.cells.get(row.button.join(","));
  assert.equal(actual.typeId, "minecraft:light_block_12");
  assert.deepEqual(actual.states, {}, "old button states cannot leak into the original light block");
});
await test("interrupted strict pair resumes only with its recorded ownership and unchanged remaining sign", async () => {
  for (const tamper of [false, true]) {
    const h = harness();
    const row = h.venue.legacy_service_controls[0], oldSign = h.cells.get(row.sign.join(","));
    const setType = oldSign.setType.bind(oldSign);
    oldSign.setType = () => { throw new Error("simulated interruption after button restore"); };
    await assert.rejects(ensureServiceControls(h.world, h.system, h.venue), /simulated interruption/);
    assert.equal(h.cells.get(row.button.join(",")).typeId, air.name);
    assert.equal(oldSign.typeId, sign.name);
    assert.ok(h.dynamic.has("junket:panel_retirement_v1:sparrow"));
    oldSign.setType = setType;
    if (tamper) oldSign.text = "a user's replacement sign";
    const result = await ensureServiceControls(h.world, h.system, h.venue);
    assert.equal(result.legacy[0].status, tamper ? "retained" : "restored");
    if (tamper) assert.equal(oldSign.text, "a user's replacement sign");
    else assert.equal(h.dynamic.has("junket:panel_retirement_v1:sparrow"), false);
  }
  const h = harness(), row = h.venue.legacy_service_controls[0];
  h.put(row.button, air);
  assert.equal((await ensureServiceControls(h.world, h.system, h.venue)).legacy[0].status, "retained",
    "an unjournaled partial pair does not authorize deletion");
});
await test("only an identical intrinsic light level is accepted from Script API", async () => {
  const h = harness({ implicitLightStates: true });
  const row = h.venue.legacy_service_controls[1];
  row.source_evidence.button_original = { name: "minecraft:light_block_12", states: {} };
  const result = await ensureServiceControls(h.world, h.system, h.venue);
  assert.ok(result.legacy.every(record => record.status === "restored"));
  const block = h.cells.get(row.button.join(","));
  assert.deepEqual(block.states, {}, "saved source states remain implicit");
  assert.deepEqual(block.permutation.getAllStates(), { block_light_level: 12 }, "BDS API adds the matching level");
  assert.equal(inspectLegacyServiceControls(h.world, h.venue)[1].status, "restored");
  for (const states of [{ block_light_level: 11 }, { block_light_level: "12" },
    { block_light_level: 12, unexpected_state: true }]) {
    block.states = states;
    assert.equal(inspectLegacyServiceControls(h.world, h.venue)[1].status, "retained",
      "wrong level, wrong type and unrelated extra states are refused");
  }
  block.typeId = "minecraft:stone_button";
  block.states = { block_light_level: 12 };
  assert.equal(inspectLegacyServiceControls(h.world, h.venue)[1].status, "retained", "only the same light block type qualifies");
});
console.log(`PASS ${count} strict panel migration checks (offline)`);
