// Offline presentation/navigation regression. Does not prove client rendering.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { VENUE_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_venue_generated.js";
import {
  NAVIGATION_LABELS, MAX_PERMANENT_FIXTURE_LABELS, applyVenuePresentation,
  permanentFixtureLabel, fixtureInteractionHint, receptionActions,
  showReceptionNavigation, requestCatalogReturn, registerCatalogReturn, installFixtureFocusHints, registrationGuide,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/venue_presentation.js";

const source = new URL("../data/junket-private/all17-reference/all17_original_generated.json", import.meta.url);
const manifestIndex = process.argv.indexOf("--manifest");
const supplied = manifestIndex >= 0 ? process.argv[manifestIndex + 1] : null;
const venues = supplied ? (supplied.endsWith(".json")
  ? JSON.parse(readFileSync(supplied, "utf8")).venues
  : (await import(pathToFileURL(resolve(supplied)).href)).ORIGINAL_VENUES)
  : existsSync(source) ? JSON.parse(readFileSync(source, "utf8")).venues : VENUE_CONFIG.VENUES;
let checks = 0;
async function test(name, fn) { await fn(); console.log(`OK ${++checks} ${name}`); }

await test("all 17 venues have precisely one reception and catalog return label", () => {
  assert.equal(venues.length, 17);
  for (const venue of venues) {
    const visible = venue.fixtures.filter(fixture => permanentFixtureLabel(venue, fixture));
    assert.equal(visible.length, MAX_PERMANENT_FIXTURE_LABELS, venue.id);
    assert.deepEqual(visible.map(value => value.role).sort(), ["reception", "return"]);
    assert.match(permanentFixtureLabel(venue, visible.find(f => f.role === "reception")), /^参加受付\n/);
    assert.equal(permanentFixtureLabel(venue, visible.find(f => f.role === "return")), "カタログへ戻る");
  }
});
await test("repeated application removes verbose labels without changing fixtures", () => {
  for (const venue of venues) {
    const actors = new Map(venue.fixtures.map(fixture => [fixture.role, {
      nameTag: "秘密：当たり / 1P金庫 5面", typeId: fixture.type,
      location: fixture.location, tags: fixture.tags, frame: 4,
    }]));
    const before = [...actors.values()].map(({ nameTag, ...rest }) => JSON.stringify(rest));
    for (let n = 0; n < 3; n++) {
      applyVenuePresentation(venue, fixture => actors.get(fixture.role));
      assert.equal([...actors.values()].filter(actor => actor.nameTag).length, 2);
    }
    assert.deepEqual([...actors.values()].map(({ nameTag, ...rest }) => JSON.stringify(rest)), before);
    for (const fixture of venue.fixtures) actors.get(fixture.role).nameTag = "a game update wrote a long label";
    applyVenuePresentation(venue, fixture => actors.get(fixture.role));
    assert.equal([...actors.values()].filter(actor => actor.nameTag).length, 2);
  }
});
await test("Lucy keyhole hints use immutable names and never selected answer state", () => {
  const lucy = venues.find(venue => venue.id === "lucy");
  const keyhole = lucy.fixtures.find(fixture => fixture.role.startsWith("keyhole_"));
  assert.ok(keyhole);
  const hint = fixtureInteractionHint(lucy, keyhole);
  assert.match(hint, /金庫/);
  assert.doesNotMatch(hint, /当たり|選択済み|設定済み/);
  assert.equal(permanentFixtureLabel(lucy, keyhole), "");
});
await test("navigation separates participation, rules, rematch, leave and catalog", () => {
  assert.deepEqual(receptionActions({ canJoin: true }), ["join", "rules", "catalog", "close"]);
  assert.deepEqual(receptionActions({ canJoin: true, rematch: true }), ["rematch", "rules", "catalog", "close"]);
  assert.deepEqual(receptionActions({ joined: true, canAct: true }), ["act", "rules", "leave", "catalog", "close"]);
  assert.deepEqual(receptionActions({ joined: true }), ["rules", "leave", "catalog", "close"]);
  assert.ok(Object.values(NAVIGATION_LABELS).every(value => !value.includes("拠点")));
});
await test("the required attendance and automatic start rule are explicit", () => {
  assert.equal(registrationGuide(), "2人の参加が揃うと自動で開始します。");
  assert.equal(registrationGuide({ seats: 4 }), "4人の参加が揃うと自動で開始します。");
  assert.equal(registrationGuide({ seats: 16, registration: true }), "1〜16人で参加できます。最初の参加から15秒後に自動で開始します。");
});

function forms(responses) {
  const shown = [];
  class Form {
    constructor() { this.buttons = []; }
    title(value) { this.heading = value; return this; }
    body(value) { this.text = value; return this; }
    button(value) { this.buttons.push(value); return this; }
    async show() { shown.push(this); assert.ok(responses.length, "unexpected extra form"); return responses.shift(); }
  }
  return { Form, shown };
}
await test("rules can be read without joining, then return to the same reception", async () => {
  const { Form, shown } = forms([{ selection: 1 }, { selection: 0 }, { selection: 2 }]);
  const action = await showReceptionNavigation({ ActionFormData: Form, player: { id: "reader" },
    gameId: "lucy", title: "気分屋ルーシー", rules: "private-free rules", canJoin: true });
  assert.equal(action, "catalog");
  assert.equal(shown[1].heading, "気分屋ルーシー｜説明");
  assert.equal(shown[1].text, "private-free rules");
  assert.equal(shown[2].buttons[0], "参加する");
});
await test("repeat registration is presented as rematch without changing its join contract", async () => {
  const { Form, shown } = forms([{ selection: 0 }, { selection: 0 }]);
  const config = { ActionFormData: Form, player: { id: "repeat" }, gameId: "sparrow", title: "ウラギリスズメ", canJoin: true };
  assert.equal(await showReceptionNavigation(config), "join");
  assert.equal(await showReceptionNavigation(config), "rematch");
  assert.equal(shown[1].buttons[0], "再戦");
});
await test("catalog navigation delegates to the existing guarded return route", () => {
  const calls = [], world = {}, player = { id: "ordinary-player" };
  registerCatalogReturn(world, value => calls.push(value.id));
  requestCatalogReturn(player, world);
  assert.deepEqual(calls, ["ordinary-player"]);
});
await test("focus hints are per-player, immutable and installed only once", () => {
  const lucy = venues.find(venue => venue.id === "lucy");
  const fixture = lucy.fixtures.find(entry => entry.role.startsWith("keyhole_"));
  const messages = [], callbacks = [];
  const actor = { nameTag: "当たり", getTags: () => [`junket:lucy:fixture:${fixture.role}`],
    getDynamicProperty: key => key === "junket:venue_id" ? "lucy" : undefined };
  const player = { getEntitiesFromViewDirection: () => [{ entity: actor }],
    onScreenDisplay: { setActionBar: value => messages.push(value) } };
  const world = { getAllPlayers: () => [player] };
  const system = { runInterval: callback => { callbacks.push(callback); return 17; } };
  assert.equal(installFixtureFocusHints({ world, system, venues }), 17);
  assert.equal(installFixtureFocusHints({ world, system, venues }), 17);
  assert.equal(callbacks.length, 1);
  callbacks[0]();
  assert.deepEqual(messages, [fixtureInteractionHint(lucy, fixture)]);
  assert.doesNotMatch(messages[0], /当たり/);
  assert.equal(actor.nameTag, "当たり", "focus helper does not mutate entities");
  actor.getDynamicProperty = () => "unrelated";
  callbacks[0]();
  assert.equal(messages.length, 1, "unrelated entity HUD is preserved");
});
console.log(`PASS ${checks} presentation checks / ${venues.length} venues / ${venues.reduce((sum, v) => sum + v.fixtures.length, 0)} fixtures (offline)`);
