// Focused checks for NEW venue integration only. Not a Bedrock visual test.
import assert from "node:assert/strict";
import { createRuntime } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/runtime.js";

const tasks = [], handlers = {}, forms = [], entities = [], properties = new Map(), writes = [];
let obstruction = true;
const dim = {
  id: "minecraft:overworld",
  getBlock: p => ({ isAir: p.y >= -60, typeId: obstruction && p.x === 181 && p.y === -59 ? "minecraft:stone" : p.y < -60 ? "minecraft:dirt" : "minecraft:air" }),
  getEntities: q => entities.filter(e => !e.removed && q.tags.every(t => e.hasTag(t))),
  spawnEntity(typeId, location) {
    const tags = new Set(), props = new Map();
    const e = { typeId, location, dimension: dim, removed: false, nameTag: "",
      addTag: t => tags.add(t), hasTag: t => tags.has(t),
      setProperty: (k, v) => props.set(k, v), getProperty: k => props.get(k),
      setDynamicProperty: (k, v) => props.set(k, v), getDynamicProperty: k => props.get(k),
      teleport(p) { this.location = p; }, remove() { this.removed = true; } };
    entities.push(e); return e;
  },
};
// Obstructions must not be accepted as air even in the fixture.
const getBlock = dim.getBlock;
dim.getBlock = p => { const b = getBlock(p); b.isAir = b.typeId === "minecraft:air"; return b; };
const player = id => ({ id, name: id, dimension: dim, location: { x: 180, y: -60, z: 40 }, messages: [],
  sendMessage(m) { this.messages.push(m); }, teleport(p, options) { this.location = p; this.dimension = options.dimension; },
  playSound() {}, onScreenDisplay: { setActionBar() {}, setTitle() {} } });
const a = player("A"), b = player("B");
const signal = name => ({ subscribe(fn) { handlers[name] = fn; } });
class Form {
  title() { return this; } body() { return this; } button() { return this; }
  textField(label, placeholder, options) { this.default = options?.defaultValue; return this; }
  show(p) { return new Promise(resolve => forms.push({ p, form: this, resolve })); }
}
const world = { getAllPlayers: () => [a, b], getDimension: () => dim,
  getDynamicProperty: k => properties.get(k), setDynamicProperty: (k, v) => properties.set(k, v),
  beforeEvents: { playerInteractWithEntity: signal("click") }, afterEvents: { playerLeave: signal("leave") },
  structureManager: { createFromWorld(...args) { writes.push(["backup", ...args]); }, place(...args) { writes.push(["place", ...args]); } } };
const system = { run: f => tasks.push(f), runTimeout: f => tasks.push(f), runJob: g => { for (const _ of g) {} },
  afterEvents: { scriptEventReceive: signal("script") } };
const { manager, venue, open } = createRuntime({ world, system, ActionFormData: Form, ModalFormData: Form });
const flush = () => new Promise(resolve => setImmediate(resolve));
async function drain() { while (tasks.length) { tasks.shift()(); await flush(); } }
await assert.rejects(venue.install(a), /181,-59,40/);
assert.equal(writes.length, 0, "No backups or writes before empty-region check passes");
obstruction = false;
await venue.install(a);
assert.equal(writes[0][0], "backup"); assert.equal(writes[1][0], "place");
assert.deepEqual(writes[1][3], { x: 180, y: -62, z: 40 });
await assert.rejects(venue.install(a), /設置済み/);
manager.join("sparrow", "sparrow", a.id);
venue.start(manager.join("sparrow", "sparrow", b.id));
assert.deepEqual(a.location, { x: 197, y: -60, z: 59 });
assert.deepEqual(b.location, { x: 193, y: -60, z: 59 });
const pending = open(a); forms.shift().resolve({ selection: 0 }); await flush();
const bet = forms.shift(); assert.equal(bet.form.default, "1000000");
bet.resolve({ formValues: [bet.form.default] }); await pending;
assert.equal(manager.forPlayer(a.id).state.phase, "hide");
assert.equal(forms.length, 0, "Physical box input must not auto-open a form");
const big = entities.find(e => e.typeId === "junket:bigbox");
const small = entities.find(e => e.typeId === "junket:littlebox");
async function click(p, target) { const e = { player: p, target }; handlers.click(e); assert.equal(e.cancel, true); await drain(); }
await click(b, small); assert.equal(manager.forPlayer(a.id).state.phase, "hide", "Wrong player cannot hide a gem");
await click(a, big);
assert.equal(manager.forPlayer(a.id).state.phase, "choose");
assert.equal(big.getProperty("junket:frame"), 0); assert.equal(small.getProperty("junket:frame"), 0);
assert.ok(!entities.some(e => !e.removed && e.hasTag("junket:venue_temporary")));
await click(b, small);
assert.equal(manager.forPlayer(a.id).state.round, 2);
assert.deepEqual(manager.forPlayer(a.id).state.money, [50_500_000, 49_500_000]);
assert.equal(venue.locked(), false);
handlers.leave({ playerId: a.id });
assert.equal(manager.view("sparrow"), null);
assert.ok(!entities.some(e => !e.removed && e.hasTag("junket:venue_temporary")));
assert.equal(big.getProperty("junket:frame"), 0);
console.log("PASS empty-area protection, backup-before-place, coordinates, default bet, physical boxes, secrecy, one settlement, disconnect cleanup (JS only)");
