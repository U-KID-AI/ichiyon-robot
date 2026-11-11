import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";
import * as S from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/strategy_games.js";
import * as B from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/board_games.js";
import { eligibleDecisionSeats, decisionWindow } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/remaining_decisions.js";
import { runRemainingRuleTraces } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/rules_remaining_probe.js";

const sourceArg = process.argv.indexOf("--source-root");
assert.ok(sourceArg >= 0, "Pass --source-root pointing to the private original datapack (test/)");
const root = process.argv[sourceArg + 1], evidence = new Map();
function original(path, pattern) {
  const bytes = readFileSync(join(root, "data/game/function", `${path}.mcfunction`));
  const text = bytes.toString("utf8");
  evidence.set(path, createHash("sha256").update(bytes).digest("hex"));
  if (pattern) assert.match(text, pattern, `${path}: actual Java rule anchor`);
  return text;
}
const permutations = values => values.length === 0 ? [[]] : values.flatMap((value, index) =>
  permutations(values.filter((_, i) => i !== index)).map(rest => [value, ...rest]));
let cases = 0;

// Facts come from actual source files, not the current Bedrock constants.
const hp1 = original("zzz_hp/1", /schedule function game:zzz_hp\/2 1t/);
const hp2 = original("zzz_hp/2", /schedule function game:zzz_hp\/1 4t/);
assert.equal(S.FIRE_HP_TICK_STEP, Number(hp1.match(/zzz_hp\/2 (\d+)t/)[1]) +
  Number(hp2.match(/zzz_hp\/1 (\d+)t/)[1])); cases++;
original("grail/set/player-1", /if score \$potion-set grail matches 1 if score \$grail-set grail matches 1/);
original("grail/result", /if score \$player-1-cup grail matches 0 if score \$player-2-cup grail matches 0/);
original("grail/cup/player-1", /poison\/player-1\/100 5s/);
for (const amount of [1, 100, 200, 300, 400, 450, 500]) {
  const text = original(`grail/cup/poison/player-1/${amount}`);
  const expected = Number(text.match(/damage general (\d+)/)[1]);
  let state = { ...S.createGrail(), poisonCup: [amount, 0] };
  state = S.advanceGrail(state, 0, "drink");
  assert.equal(state.pendingDamage[0], expected);
  for (let tick = 0; tick < 99; tick++) state = S.grailSystem(state, "tick");
  assert.equal(state.hp[0], 60000);
  state = S.grailSystem(state, "tick");
  assert.equal(state.hp[0], Math.max(0, 60000 - expected)); cases++;
}
{
  let state = S.createGrail();
  const key = decisionWindow("grail", state, 0);
  for (let tick = 0; tick < 37; tick++) state = S.grailSystem(state, "tick");
  state = S.advanceGrail(state, 1, "arrange", { holyIndex: 2 });
  assert.equal(decisionWindow("grail", state, 0), key, "the other sealed arrangement does not stale this form");
  assert.equal(state.inputRemaining, 5963, "one arranger must not reset the shared deadline");
  assert.throws(() => S.advanceGrail(state, 1, "arrange", { holyIndex: 0 }), /確定済み/);
  assert.equal(S.grailViewForPlayer(state, 0).self.arrangement.holyIndex, undefined);
  assert.equal(S.grailViewForPlayer(state, 1).self.arrangement.holyIndex, 2);
  const cup = { ...state, poisonCup: [100, 0], holyCup: [300, 0], pendingDamage: [500, 0], poisonRemaining: [5, 0] };
  const drink = S.advanceGrail(cup, 0, "drink");
  assert.equal(drink.poison[0], -50, "source preserves negative poison credit");
  assert.equal(drink.pendingDamage[0], 0, "neutralising drink cancels earlier scheduled poison"); cases += 6;
}

original("auction/set/player-1", /set\/player-2 1t/);
original("auction/open/diamond", /team-A-dia auction matches 3/);
const paymentSource = original("auction/open/win/player-1", /matches 5835\.\.5840/);
const paymentLow = Number(original("auction/open/win/electicity").match(/damage general (\d+)/)[1]);
const paymentHigh = Number(original("auction/open/win/electicity-2").match(/damage general (\d+)/)[1]);
original("auction/open/open", /matches 5945.*schedule function game:auction\/open\/win\/player-1/);
for (const [total, lower] of [[1, 5835], [5, 5830], [10, 5820], [16, 5810]]) {
  assert.ok(paymentSource.includes(`matches ${lower}..5840`));
  for (let clock = 0; clock < 5; clock++) {
    let state = { ...S.createAuction(), hpClock: clock,
      payment: [total - 1, 0, 0, 0], roundWins: [1, 0] };
    for (let seat = 0; seat < 4; seat++) state = S.advanceAuction(state, seat, "bid", seat === 0 ? 1 : 2);
    assert.equal(state.phase, "payment");
    assert.equal(state.hp[0], 60000, "payment must not instantly damage on final bid");
    assert.deepEqual(S.auctionView(state).bids, [null, null, null, null]);
    for (let tick = 0; tick < 39; tick++) state = S.auctionSystem(state, "tick");
    assert.deepEqual(S.auctionView(state).bids, [null, null, null, null]);
    state = S.auctionSystem(state, "tick");
    assert.deepEqual(S.auctionView(state).bids, [1, 2, 2, 2]);
    while (state.phase === "payment") state = S.auctionSystem(state, "tick");
    const damage = Math.ceil((5840 - lower + 1) / 5) * (total >= 16 ? paymentHigh : paymentLow);
    assert.equal(state.damage[0], damage);
    assert.equal(state.hp[0], 60000 - damage); cases++;
  }
}
{
  const state = S.advanceAuction(S.createAuction(), 0, "bid", 4);
  assert.deepEqual(S.auctionView(state).selected[0], []);
  assert.deepEqual(S.auctionViewForPlayer(state, 1).selected[0], []);
  assert.deepEqual(S.auctionViewForPlayer(state, 0).selected[0], [4]);
  assert.throws(() => S.advanceAuction(state, 2, "bid", 2), /手番/); cases += 4;
}

original("fire/set/set", /if score \$fire-folder-1 fire matches 0 run scoreboard players add \$player-1-spend fire 1/);
original("fire/set/set", /if score \$fire-folder-2 fire matches 0 run scoreboard players add \$player-2-spend fire 1/);
const cards = Object.values(S.FIRE_CONTROL_CARDS).filter(card => card.seat === 0)
  .map(card => ({ number: Number(card.id[0]), heat: card.id.endsWith("hot") ? 1 : -1 }));
for (const first of cards) for (const second of cards) {
  let a = S.createFire(), b = S.createFire();
  const firstWindow = decisionWindow("fire", a, 0);
  b = S.advanceFire(b, 1, "choice", second);
  assert.equal(decisionWindow("fire", b, 0), firstWindow);
  assert.deepEqual(S.fireView(b).used[1], []);
  assert.deepEqual(S.fireViewForPlayer(b, 0).used[1], []);
  assert.deepEqual(eligibleDecisionSeats("fire", b), [0]);
  a = S.advanceFire(S.advanceFire(a, 0, "choice", first), 1, "choice", second);
  b = S.advanceFire(b, 0, "choice", first);
  assert.deepEqual(a, b, "simultaneous Fire order must not change result");
  assert.throws(() => S.advanceFire(b, 1, "choice", second), /現在/); cases++;
}

original("pig/open/turn", /scoreboard players add \$host pig 1/);
for (const host of [0, 1, 2, 3]) {
  let state = { ...S.createPig(), host, actor: host };
  for (let step = 0; step < 4; step++) {
    assert.equal(state.actor, (host + step) % 4);
    state = S.advancePig(state, state.actor, "choose", { card: step + 1 });
  }
  assert.equal(state.phase, "reveal"); cases++;
}

original("light/start", /light-player-1/);
original("light/set/guard/player-1", /light\/set\/tourist\/player-2/);
for (const guards of permutations([0, 1, 2])) for (const tourists of permutations([0, 1, 2])) {
  let state = S.createLight();
  while (state.phase === "intro") state = S.lightSystem(state, "tick");
  state = S.advanceLight(state, 0, "arrange", { guards });
  assert.equal(S.lightViewForPlayer(state, 1).guards, null);
  state = S.advanceLight(state, 1, "arrange", { tourists });
  assert.equal(state.phase, "result"); cases++;
}

original("mine/choose/player-1", /\$selected mine -1/);
original("mine/choose/player-2", /1stplayerskill\{2:2\}.*player-2-becomeworkerholic:2/);
original("mine/time/wait-30", /\$time mine matches 801\.\..*remove \$time mine 600/);
original("mine/key/player-1/determine", /power:2,player:1/);
original("mine/key/player-1/fate", /matches 30 run scoreboard players remove \$player-1-ingot mine 15/);
original("mine/open/result", /\$result-3 mine matches 1\.\.4 run scoreboard players add \$\$\(player\)-mine mine 3/);
original("mine/open/result", /remove \$\$\(enemy\)-mine mine 1/);
original("mine/open/remine-1", /function game:mine\/choose\/player-1/);
const combinations = (values, count) => count === 0 ? [[]] : values.flatMap((value, index) =>
  combinations(values.slice(index + 1), count - 1).map(rest => [value, ...rest]));
for (const board of permutations([0, 1, 2, 3])) for (const count of [1, 2, 3]) {
  for (const indices of combinations([0, 1, 2, 3], count)) {
    let state = S.createMine();
    state = S.advanceMine(state, 1, "arrange", { cards: board });
    for (let key = 0; key < count - 1; key++) state = S.advanceMine(state, key, "key", { target: 0, skill: "work" });
    assert.equal(state.chooseCount, count);
    assert.equal(S.mineViewForPlayer(state, 0).self.board, null);
    state = S.advanceMine(state, 0, "mine", { indices });
    assert.equal(state.phase, "reveal");
    const selected = indices.map(index => board[index]);
    assert.deepEqual(state.last.cards, selected);
    assert.equal(state.roundMine[0], count, "blank is still a mined rock");
    assert.equal(state.totalMine[0], Math.min(2, count), "source three-rock totalmine quirk");
    assert.equal(state.roundMine[1], selected.includes(3) ? -1 : 0, "potion reduces the opponent's mining");
    assert.equal(state.reminePending, selected.includes(2)); cases++;
  }
}
{
  let state = S.advanceMine(S.createMine(), 1, "arrange", { cards: [0, 1, 2, 3] });
  const key = decisionWindow("mine", state, 0);
  state = S.advanceMine(state, 1, "key", { target: 0, skill: "work" });
  assert.equal(state.chooseCount, 2);
  assert.notEqual(decisionWindow("mine", state, 0), key, "granting a new pickaxe invalidates the old count");
  assert.throws(() => S.advanceMine(state, 1, "key", { target: 0, skill: "work" }), /使用済み/);
  assert.throws(() => S.advanceMine(state, 0, "choose", { indices: [1] }), /2個/);
  state = S.advanceMine(state, 0, "choose", { indices: [1, 2] });
  while (state.phase === "reveal") state = S.mineSystem(state, "tick");
  assert.equal(state.phase, "choose");
  assert.equal(state.chooseCount, 1);
  assert.deepEqual(S.mineView(state).availableIndices, [0, 3]);
  assert.throws(() => S.advanceMine(state, 0, "choose", { indices: [2] }), /未採掘/);
  state = S.advanceMine(state, 0, "choose", { indices: [3] });
  assert.equal(state.roundMine[0], 3);
  assert.equal(state.roundMine[1], -1); cases += 8;
}
{
  let state = S.advanceMine(S.createMine(), 1, "arrange", { cards: [0, 1, 2, 3] });
  state = { ...state, phaseRemaining: 801, bombs: [1, 0], poison: [601, 0] };
  const wait = S.advanceMine(state, 0, "wait_30");
  assert.equal(wait.phaseRemaining, 201);
  assert.deepEqual(wait.poison, [1201, 0]);
  assert.deepEqual(wait.totalTime, [600, 0]);
  assert.equal(S.mineSystem(wait, "tick").hp[0], 59750);
  assert.throws(() => S.advanceMine(wait, 0, "wait_30"), /不足/);
  assert.throws(() => S.advanceMine(state, 1, "wait_30"), /手番/);
  const fate = S.advanceMine({ ...state, ingots: [29, 30] }, 0, "key", { target: 0, skill: "fate" });
  assert.equal(fate.ingots[0], 15);
  const clean = S.advanceMine(state, 0, "key", { target: 0, skill: "clean" });
  assert.throws(() => S.advanceMine(clean, 1, "key", { target: 0, skill: "clean" }), /既に/); cases += 8;
}

original("hope/main/round10", /5790/);
assert.deepEqual(eligibleDecisionSeats("hope", B.createHope()), []); cases++;
original("clock/drop/player-1", /matches 5980/);
original("clock/drop/player-1", /matches 5921/);
for (let count = 0; count < 12; count++) {
  let state = B.createClock();
  for (let i = 0; i < count; i++) state = B.advanceClock(state, 0, "hour_plus");
  assert.equal(state.nextHour, (count + 1) % 12 || 12); cases++;
}
original("walker/walk/player-1", /walker-stop/);
assert.deepEqual(eligibleDecisionSeats("walker", B.createWalker()), []); cases++;

const switchSource = original("garden/open/change");
const swap = new Map([...switchSource.matchAll(/\$change garden matches ([12]) run scoreboard players set \$change garden ([34])/g)]
  .map(match => [Number(match[1]) - 1, Number(match[2]) - 3]));
original("garden/losecheck", /matches 100/);
for (const valve of [0, 1]) for (const first of ["switch", "double", "open"]) {
  for (const second of ["switch", "double", "open"]) {
    let state = { ...B.createGarden(), valve };
    state = B.advanceGarden(B.advanceGarden(state, 1, "choose", second), 0, "choose", first);
    const count = [first, second].filter(value => value === "switch").length;
    assert.equal(state.valve, count === 1 ? swap.get(valve) : valve); cases++;
  }
}
original("rabbit/move/canmove", /positioned ~9 ~ ~/);
original("rabbit/move/canmove", /positioned ~ ~ ~-9/);
original("rabbit/move/guard", /haveGuard|guard|お守り/);
for (const trap of B.rabbitLegalTrapFloors(B.createRabbit())) {
  const state = B.advanceRabbit(B.createRabbit(), 1, "trap", trap);
  assert.deepEqual(B.rabbitViewForPlayer(state, 0).self.traps, []);
  assert.deepEqual(B.rabbitViewForPlayer(state, 1).self.traps, [trap]); cases++;
}

const traces = runRemainingRuleTraces();
const report = { passed: true, cases, sourceFiles: Object.fromEntries(evidence), traces,
  notVerified: ["Java datapack executed with identical inputs", "all source branches", "PC client full playthrough", "Android/iOS full playthrough"],
  unresolved: { mine: "Source late key-grant animation timing, drowning/down physics, drilling/falling choreography and wait-60 remain unverified; direct source/Bedrock execution comparison not performed.",
    auction: "Payment windows and shared five-tick damage consumption covered; source death-room transport, all diamond animations and complete client traces remain unverified.",
    spatial: "Synthetic water/floor observations are not physical client collision proof." } };
const outputArg = process.argv.indexOf("--output");
if (outputArg >= 0) writeFileSync(process.argv[outputArg + 1], JSON.stringify(report, null, 2));
console.log(`PASS ${cases} source-anchored cases, ${evidence.size} actual Java files, ${traces.length} start-to-finish reducer traces; full parity and clients not verified`);
