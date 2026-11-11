// Focused two-player input rehearsal. This is NOT a Bedrock client/runtime test.
import assert from "node:assert/strict";
import { createRuntime } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/runtime.js";
import { setupReception } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/reception.js";

const forms = [], scheduled = [], handlers = {}, created = [];
const venueEntities = [], venueProperties = new Map();
const venueDimension = {
  id: "minecraft:overworld",
  getEntities({ tags = [], location, maxDistance } = {}) {
    return venueEntities.filter(entity => !entity.removed &&
      tags.every(tag => entity.hasTag(tag)) &&
      (!location || Math.hypot(entity.location.x - location.x,
        entity.location.y - location.y, entity.location.z - location.z) <= maxDistance));
  },
  spawnEntity(typeId, location) {
    const tags = new Set(), properties = new Map();
    const entity = {
      typeId, location: { ...location }, dimension: venueDimension, nameTag: "", removed: false,
      addTag(tag) { tags.add(tag); }, hasTag(tag) { return tags.has(tag); },
      getTags() { return [...tags]; }, remove() { this.removed = true; },
      teleport(at) { this.location = { ...at }; },
      setProperty(name, value) { properties.set(name, value); },
      setDynamicProperty(name, value) { properties.set(name, value); },
      getDynamicProperty(name) { return properties.get(name); },
      getComponent(name) { return name === "minecraft:health" ? { currentValue: 20, effectiveMax: 20 } : undefined; },
    };
    venueEntities.push(entity);
    return entity;
  },
};
const player = id => ({ id, name: id, typeId: "minecraft:player", dimension: venueDimension,
  location: { x: 0, y: 0, z: 0 }, messages: [], sendMessage(m) { this.messages.push(m); },
  teleport(location, options) { this.location = { ...location }; this.dimension = options?.dimension ?? this.dimension; },
  onScreenDisplay: { setActionBar() {}, setTitle() {} } });
const a = player("member-A"), b = player("member-B");
// No runCommand, isOp, inventory, teleport, tick interval or chunk-loading API.
const signal = name => ({ subscribe(fn) { handlers[name] = fn; } });
class Form {
  title(t) { this.name = t; return this; }
  body(t) { this.text = t; return this; }
  button(text) { (this.buttons ??= []).push(text); return this; }
  textField() { return this; }
  show(p) { return new Promise(resolve => forms.push({ p, form: this, resolve })); }
}
const { manager } = createRuntime({
  world: { getAllPlayers: () => [a, b], beforeEvents: { playerInteractWithEntity: signal("click") },
    afterEvents: { playerLeave: signal("leave") },
    getDimension: () => venueDimension,
    getDynamicProperty: key => venueProperties.get(key),
    setDynamicProperty: (key, value) => venueProperties.set(key, value) },
  system: { run: fn => scheduled.push(fn), runTimeout: fn => scheduled.push(fn),
    afterEvents: { scriptEventReceive: signal("script") } },
  ActionFormData: Form, ModalFormData: Form,
});
const flush = () => new Promise(resolve => setImmediate(resolve));
async function drain() { while (scheduled.length) { scheduled.shift()(); await flush(); } }
async function reply(p, response) {
  const index = forms.findIndex(f => f.p === p);
  assert.notEqual(index, -1, `Expected a form for ${p.id}`);
  forms.splice(index, 1)[0].resolve(response);
  await flush();
}
async function click(p) {
  const event = { player: p, target: { hasTag: t => t === "junket:reception" }, cancel: false };
  const count = forms.length;
  handlers.click(event);
  assert.equal(event.cancel, true, "Native NPC dialog must be canceled");
  assert.equal(forms.length, count, "Form must not be shown in read-only callback");
  await drain();
}
async function clickBox(p, choice) {
  const target = venueEntities.find(entity => !entity.removed &&
    entity.hasTag("junket:venue_box") && entity.getDynamicProperty("junket:box") === choice);
  assert.ok(target, `Expected the ${choice} box fixture`);
  const event = { player: p, target, cancel: false };
  handlers.click(event);
  assert.equal(event.cancel, true, "Physical box interaction must be canceled");
  await drain();
}
async function join() {
  await click(a); await reply(a, { selection: 0 });
  await click(b); await reply(b, { selection: 0 }); await drain();
  assert.ok(manager.forPlayer(a.id), [...a.messages, ...b.messages].join(" | "));
  assert.deepEqual(manager.forPlayer(a.id).state.money, [50_000_000, 50_000_000]);
}
await join();
for (let round = 1; round <= 6; round++) {
  const setter = round % 2 === 1 ? a : b, chooser = setter === a ? b : a;
  assert.equal(manager.forPlayer(setter.id).state.round, round);
  await reply(setter, { formValues: ["1000000"] }); await drain();
  await reply(setter, { selection: 0 }); await drain();
  await reply(chooser, { selection: 0 }); await drain();
}
assert.equal(manager.view("sparrow"), null);
assert.equal(forms.length, 0);
for (const p of [a, b]) {
  assert.ok(p.messages.some(m => m.includes("6/6") && m.includes("引き分け")));
  assert.ok(p.messages.some(m => m.includes("再戦")));
}
await join();
assert.equal(manager.forPlayer(a.id).state.round, 1);
await reply(a, { canceled: true }); await drain();
await click(a);
const leaveIndex = forms.find(f => f.p === a).form.buttons.indexOf("退出");
assert.notEqual(leaveIndex, -1);
await reply(a, { selection: leaveIndex }); await drain();
assert.equal(manager.forPlayer(b.id), null);
console.log("PASS reception -> both join -> automatic menus -> 6 rounds -> draw -> both rejoin -> round 1 -> leave (JS rehearsal only)");

const dimension = {
  getEntities: () => created,
  getBlock: pos => ({ isAir: pos.y >= 0, typeId: pos.y >= 0 ? "minecraft:air" : "minecraft:grass_block" }),
  spawnEntity(type, pos) { const npc = { type, pos, tags: [], addTag(t) { this.tags.push(t); } }; created.push(npc); return npc; },
};
const operator = { dimension, location: { x: 0, y: 0, z: 0 } };
setupReception(operator); setupReception(operator);
assert.equal(created.length, 1);
assert.equal(created[0].type, "minecraft:npc");
assert.ok(created[0].tags.includes("junket:reception"));
console.log("PASS explicit NPC setup reuses nearby reception, without terrain writes");
