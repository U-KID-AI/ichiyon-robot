import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { WARP_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_warp_generated.js";
import { VENUE_CONFIG } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/junket_venue_generated.js";
import {
  advanceAuction, advanceFire, advanceGrail, advanceLight, advanceMine, advancePig,
  auctionGame, createAuction, createFire, createGrail, createLight, createMine, createPig,
  FIRE_PROP_FRAME, fireGame, fireSystem, fireTemperatureDamage, fireVenuePresentation,
  fireView, fireWindDamage, fireWindPower,
  firePurgeWinner, grailGame, grailSystem, grailVenuePresentation,
  LIGHT_START_OXYGEN, lightGame, mineGame, PIG_DEATH_ENDING_TICKS,
  PIG_OXYGEN, PIG_PROP_FRAME, PIG_REVEAL_HIDDEN_TICKS, PIG_REVEAL_TICKS,
  PIG_TICK_STEP, pigGame, pigSystem, pigVenuePresentation, STRATEGY_GAMES,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/strategy_games.js";
import {
  advanceClock, advanceGarden, advanceHope, advanceRabbit, advanceWalker, hopeSystem, walkerSystem,
  clockGame, clockSystem, createClock, createGarden, createHope, createRabbit, createWalker,
  gardenGame, gardenSystem, hopeGame, rabbitGame, rabbitSystem, rabbitView, walkerGame, BOARD_GAMES,
  HOPE_AI_TRADERS, HOPE_DROP_TICKS, HOPE_REVEAL_TICKS, HOPE_ROUND_TEN_CHECK_AFTER_DROP_TICKS,
  HOPE_ROUND_TEN_NO_OUT_REMAINING_TICKS, hopeAiMotionActive,
  RABBIT_TRAVEL_TICKS, RABBIT_MOVE_TICKS, RABBIT_SETTLE_TICKS,
  RABBIT_REVEAL_TICKS, RABBIT_DROP_TICKS, RABBIT_RESULT_TICKS, RABBIT_RESULT_OPEN_TICKS,
  RABBIT_WIPE_RESULT_TICKS,
  rabbitChooseCpuMove,
  CLOCK_FLOOR_OPEN_TICKS, CLOCK_FLOOR_RESTORE_TICKS, CLOCK_LANDING_END_TICKS,
  CLOCK_MOVE_END_TICKS, CLOCK_NEXT_TURN_TICKS, CLOCK_TIMEOUT_END_TICKS,
  CLOCK_NEEDLE_START_TICKS, CLOCK_NEEDLE_END_TICKS,
  clockNeedleAngles, clockNeedleContacts, clockNeedleProgress,
  WALKER_WALK_TICKS, WALKER_SETUP_TICKS, WALKER_DODGE_TICKS, WALKER_FUSE_TICKS,
  WALKER_AFTERMATH_TICKS, walkerFloorDistance,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/board_games.js";
import { aceGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/ace.js";
import { GameManager } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js";
import {
  advancePhysicalControl, COMPLETE_CONTROL_SPECS, gameRules, physicalControlRequest,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/complete_runtime.js";
import { createProductionVenues } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/production_venue.js";
import { sparrowGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/sparrow.js";
import { temperanceGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/temperance.js";
import { lucyGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/lucy.js";
import { silenceGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/silence.js";
import { jinnGame } from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/jinn.js";

const catalog = JSON.parse(readFileSync(
  new URL("../scripts/minecraft/junket_games.json", import.meta.url), "utf8"));
assert.equal(catalog.games.length, 17);
assert.equal(WARP_CONFIG.WARP_CONTROLS.length, 17);
assert.ok(catalog.games.every(game => game.readiness === "in-progress"));
assert.ok(catalog.games.every(game => game.completion?.source_spec === "partial"));
assert.ok(catalog.games.every(game => game.completion?.playthrough === "not_verified"));
assert.ok(catalog.games.every(game => game.completion?.production_deployment === "not_verified"));
assert.ok(WARP_CONFIG.WARP_CONTROLS.every(game => game.enabled));
{
  const controls = id => VENUE_CONFIG.VENUES.find(venue => venue.id === id).fixtures
    .filter(fixture => fixture.action && fixture.action !== "reception" &&
      fixture.action !== "display" && fixture.action !== "return" &&
      !fixture.tags?.includes("junket:hope:apparatus"))
    .map(fixture => fixture.action);
  for (const [id, specs] of Object.entries(COMPLETE_CONTROL_SPECS)) {
    assert.deepEqual(controls(id).sort(), Object.keys(specs).sort(),
      `${id}: every authored venue control must map to an executable physical action`);
  }
  assert.deepEqual(controls("hope"), [
    ...Array.from({ length: 16 }, (_, index) => `floor_${index + 1}`),
  ]);
  assert.deepEqual(VENUE_CONFIG.VENUES.find(venue => venue.id === "hope").fixtures
    .filter(fixture => fixture.tags?.includes("junket:hope:apparatus"))
    .map(fixture => fixture.role), [
    "hope_tv_1", "hope_tv_2", "hope_tv_3", "hope_tv_4", "hope_tv_5", "hope_tv_6",
    "hope_code", "hope_code_display",
  ]);
  assert.deepEqual(controls("walker"), [
    "walker_stop_1", "bomb_prev_1", "bomb_next_1", "bomb_confirm_1",
    "walker_stop_2", "bomb_prev_2", "bomb_next_2", "bomb_confirm_2",
  ]);
  assert.deepEqual(controls("rabbit"), [
    ...Array.from({ length: 60 }, (_, index) => `floor_${index + 1}`),
    "guard_3", "guard_4", "guard_5", "guard_6",
  ]);
  assert.deepEqual(controls("jinn").filter(action => /^(card_|lever|surrender)/.test(action)), [
    ...[0, 1].flatMap(seat => ["gold", "thief", "demon"].map(card => `card_${card}_${seat}`)),
    "lever0", "lever1", "surrender0", "surrender1",
  ]);
  assert.deepEqual(controls("auction"), Array.from({ length: 4 }, (_, seat) =>
    Array.from({ length: 4 }, (_, value) => `auction_bid_${seat + 1}_${value + 1}`)).flat());
  assert.deepEqual(controls("grail"), [
    "grail_cup_1", "grail_cup_2", "grail_cup_3",
    "grail_potion_1", "grail_potion_2", "grail_potion_3",
  ]);
  assert.deepEqual(controls("light"), [
    "light_grave_1_1", "light_grave_1_2", "light_grave_1_3",
    "light_grave_2_1", "light_grave_2_2", "light_grave_2_3",
  ]);
  assert.deepEqual(controls("mine"), [
    "mine_rock_1_1", "mine_rock_1_2", "mine_rock_1_3", "mine_rock_1_4",
    "mine_rock_2_1", "mine_rock_2_2", "mine_rock_2_3", "mine_rock_2_4",
  ]);
  const lightDisplays = VENUE_CONFIG.VENUES.find(venue => venue.id === "light").fixtures
    .filter(fixture => fixture.role.startsWith("display_"))
    .sort((left, right) => Number(left.role.slice(8)) - Number(right.role.slice(8)));
  assert.equal(lightDisplays.length, 14, "Candlelight needs 14 state displays");
  assert.ok(lightDisplays.slice(0, 3).every(fixture =>
    /ハート|ラウンド|門番/.test(fixture.name)), "Light status displays must use roles 1-3");
  assert.ok(lightDisplays.slice(3, 9).every(fixture =>
    /人形/.test(fixture.name)), "Light dolls must use roles 4-9");
  assert.ok(lightDisplays.slice(9, 12).every(fixture =>
    /墓所/.test(fixture.name)), "Light graves must use roles 10-12");
  assert.ok(lightDisplays.slice(12).every(fixture =>
    /ロウソク穴/.test(fixture.name)), "Light candles must use roles 13-14");
  const grailVenue = VENUE_CONFIG.VENUES.find(venue => venue.id === "grail");
  const grailControlFixtures = grailVenue.fixtures
    .filter(fixture => fixture.action?.startsWith("grail_"));
  assert.equal(grailControlFixtures.length, 6, "Grail needs six physical rack positions");
  assert.equal(new Set(grailControlFixtures.map(fixture =>
    `${fixture.location[0]},${fixture.location[2]}`)).size, 6,
  "cup and potion racks must occupy distinct positions");
  assert.equal(grailVenue.fixtures.filter(fixture =>
    fixture.role.startsWith("display_")).length, 5,
  "Grail needs TV, instruction, result and two rack headers");
  assert.deepEqual(controls("clock"), [
    "clock_hour_plus", "clock_hour_minus", "clock_minute_plus", "clock_minute_minus",
    "clock_time_confirm", "clock_sector_cw", "clock_sector_ccw", "clock_sector_confirm",
  ]);
  const clockVenue = VENUE_CONFIG.VENUES.find(venue => venue.id === "clock");
  assert.equal(clockVenue.drop_areas.length, 12);
  assert.deepEqual(clockVenue.drop_areas.map(area => area.sector),
    Array.from({ length: 12 }, (_, index) => index));
  const clockFloorCells = new Set();
  for (const area of clockVenue.drop_areas) {
    assert.equal(area.detect, "minecraft:water");
    assert.match(area.floor, /^minecraft:[a-z_]+_wool$/);
    assert.ok(area.cells.length > 0, `clock sector ${area.sector} needs physical cells`);
    for (const cell of area.cells) {
      assert.ok(cell.x >= clockVenue.origin[0] && cell.x < clockVenue.origin[0] + clockVenue.size[0]);
      assert.ok(cell.y >= clockVenue.origin[1] && cell.y < clockVenue.origin[1] + clockVenue.size[1]);
      assert.ok(cell.z >= clockVenue.origin[2] && cell.z < clockVenue.origin[2] + clockVenue.size[2]);
      const key = `${cell.x},${cell.y},${cell.z}`;
      assert.ok(!clockFloorCells.has(key), `clock floor cell ${key} belongs to two sectors`);
      clockFloorCells.add(key);
    }
  }
  assert.deepEqual(controls("fire"), [
    "rock_hot_1", "scissors_hot_1", "paper_hot_1",
    "rock_ice_1", "scissors_ice_1", "paper_ice_1",
    "rock_hot_2", "scissors_hot_2", "paper_hot_2",
    "rock_ice_2", "scissors_ice_2", "paper_ice_2",
  ]);
  assert.deepEqual(controls("pig"), [
    ...Array.from({ length: 4 }, (_, seat) => [
      ...Array.from({ length: 4 }, (_, card) => `pig_card_${seat + 1}_${card + 1}`),
      ...Array.from({ length: 4 }, (_, target) => target + 1)
        .filter(target => target !== seat + 1)
        .map(target => `pig_target_${seat + 1}_${target}`),
    ]).flat(),
  ]);
  assert.deepEqual(controls("ace"), [
    "ace_table_100_up", "ace_table_100_down", "ace_table_10_up", "ace_table_10_down",
    "ace_table_1_up", "ace_table_1_down", "ace_table_confirm",
    "ace_raise_100_up", "ace_raise_100_down", "ace_raise_10_up", "ace_raise_10_down",
    "ace_raise_1_up", "ace_raise_1_down", "ace_raise_confirm",
    ...Array.from({ length: 4 }, (_, seat) => [
      ...Array.from({ length: 5 }, (_, die) => `ace_die_${seat + 1}_${die + 1}`),
      `ace_tray_${seat + 1}`, `ace_call_${seat + 1}`, `ace_drop_${seat + 1}`,
      `ace_raise_${seat + 1}`, `ace_reroll_confirm_${seat + 1}`,
    ]).flat(),
  ]);
}
assert.deepEqual(
  [...STRATEGY_GAMES, ...BOARD_GAMES].map(game => game.id),
  ["grail", "auction", "fire", "pig", "light", "mine",
    "hope", "clock", "walker", "garden", "rabbit"],
);
for (const game of [aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES]) {
  assert.ok(gameRules(game.id).length > 20, `${game.id} needs a visible rule summary`);
}

{
  const manager = new GameManager([aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES]);
  manager.join("hope-test", "hope", "p1");
  assert.equal(manager.view("hope-test").state, null);
  manager.join("hope-test", "hope", "p2");
  assert.equal(manager.start("hope-test").state.playerCount, 2);
  assert.throws(() => manager.join("hope-test", "hope", "late"), /参加できません/);
}

{
  const arrange = (state, trueIndex, holyIndex) => {
    const first = state.actor;
    state = advanceGrail(state, first, "arrange",
      first === state.cupOwner ? { trueIndex } : { holyIndex });
    const second = state.actor;
    return advanceGrail(state, second, "arrange",
      second === state.cupOwner ? { trueIndex } : { holyIndex });
  };
  const exchange = (state, cupIndex, potionIndex) => {
    state = advanceGrail(state, state.grailSide, "cup", cupIndex);
    return advanceGrail(state, 1 - state.grailSide, "potion", potionIndex);
  };
  const outcomes = [
    [0, 0, "poison", true, true],
    [1, 0, "holy", false, true],
    [0, 1, "holy", true, false],
    [1, 1, "poison", false, false],
  ];
  for (const [cup, potion, kind, trueCup, holyPotion] of outcomes) {
    let state = arrange(createGrail(), 0, 0);
    state = exchange(state, cup, potion);
    assert.equal(state.last.kind, kind, `source cup ${cup}, potion ${potion} outcome`);
    assert.equal(state.last.drinker, 0, "the grail-side player drinks and takes the result");
    assert.equal(state.last.grailSeat, 0);
    assert.equal(state.last.potionSeat, 1);
    assert.equal(state.last.trueCup, trueCup);
    assert.equal(state.last.holyPotion, holyPotion);
    assert.equal(state.holy[0], kind === "holy" ? 100 : 0);
    assert.equal(state.roundPoisonCup[0], kind === "poison" ? 100 : 0);
    assert.equal(state.roundHolyCup[0], kind === "holy" ? 100 : 0);
  }

  let state = createGrail();
  assert.equal(grailGame.view(state).arrangements, undefined, "secret rack positions stay hidden");
  state = arrange(state, 0, 0);
  state = exchange(state, 0, 0);
  assert.equal(state.phase, "cup");
  assert.equal(state.grailSide, 1);
  assert.equal(state.actor, 1);
  assert.throws(() => advanceGrail(state, 1, "cup", 0), /すでに選ばれています/,
    "a cup removed by the source pick cannot be selected again in the same round");
  state = exchange(state, 2, 1);
  assert.equal(state.round, 2);
  assert.equal(state.phase, "arrange");
  assert.equal(state.actor, 1, "the source alternates the first organiser each round");
  assert.equal(state.cupOwner, 1);
  assert.equal(state.potionOwner, 0);
  assert.equal(state.grailSide, 1);
  assert.deepEqual(state.poisonCup, [100, 100]);
  assert.deepEqual(state.poison, [0, 0], "settlement fills vessels without forcing a drink");
  assert.deepEqual(state.hp, [60000, 60000]);
  state = advanceGrail(state, 0, "drink");
  state = advanceGrail(state, 1, "drink");
  for (let tick = 0; tick < 99; tick++) state = grailSystem(state, "tick");
  assert.deepEqual(state.hp, [60000, 60000], "source poison is delayed by five seconds");
  state = grailSystem(state, "tick");
  assert.deepEqual(state.hp, [59000, 59000]);

  state = arrange(state, 0, 0);
  state = exchange(state, 0, 1);
  state = exchange(state, 1, 0);
  assert.equal(state.round, 3);
  assert.equal(state.actor, 0, "round three returns the first organiser to player one");
  assert.equal(state.cupOwner, 0);
  state = advanceGrail(state, 0, "drink");
  state = advanceGrail(state, 1, "drink");
  assert.deepEqual(state.poison, [50, 50], "a later holy result removes half its volume from cumulative poison");
  assert.deepEqual(state.hp, [59000, 59000], "a holy-only round does not reapply poison damage");

  const beforeArrange = grailVenuePresentation(createGrail(), ["A", "B"]);
  assert.equal(beforeArrange.cupRack.every(label => !label.includes("配置済")), true);
  assert.equal(beforeArrange.potionRack.every(label => !label.includes("配置済")), true);
  const cupArranged = advanceGrail(createGrail(), 0, "arrange", { trueIndex: 1 });
  const afterCup = grailVenuePresentation(cupArranged, ["A", "B"]);
  assert.equal(afterCup.cupRack.every(label => label.endsWith("・配置済")), true,
    "once the cup owner has arranged, the whole rack reports ready");
  assert.equal(afterCup.potionRack.some(label => label.includes("配置済")), false,
    "the potion rack must not report ready before its owner acts");
  assert.deepEqual(afterCup.cupRack,
    ["聖杯棚 1・配置済", "聖杯棚 2・配置済", "聖杯棚 3・配置済"],
    "rack labels must not reveal which slot holds the true cup");
  const ready = advanceGrail(cupArranged, 1, "arrange", { holyIndex: 2 });
  const afterBoth = grailVenuePresentation(ready, ["A", "B"]);
  assert.equal(afterBoth.potionRack.every(label => label.endsWith("・配置済")), true);
  assert.match(afterBoth.instruction, /A.*聖杯棚/);

  let full = createGrail();
  for (let round = 1; round <= 10; round++) {
    full = arrange(full, 0, 0);
    full = exchange(full, 1, 0);
    if (full.phase !== "finished") full = exchange(full, 0, 1);
  }
  assert.equal(full.phase, "final_drink", "source final result requires both vessels to be emptied");
  full = advanceGrail(full, 1, "drink");
  full = advanceGrail(full, 0, "drink");
  for (let tick = 0; tick < 400; tick++) full = grailSystem(full, "tick");
  assert.equal(full.phase, "finished");
  assert.equal(full.reason, "holy_volume");
  assert.deepEqual(full.holy, [1000, 1000]);
  assert.equal(full.winner, null, "equal holy volume is the source draw condition");

  const timedOut = grailSystem(createGrail(), "timeout");
  assert.equal(timedOut.winner, null);
  assert.equal(timedOut.reason, "timeout_both");
}

{
  let state = createAuction();
  const settle = current => {
    while (["payment", "result"].includes(current.phase)) current = auctionGame.systemAdvance(current, "tick");
    return current;
  };
  assert.deepEqual(state.bidOrder, [0, 1, 2, 3]);
  for (let index = 0; index < 4; index++) {
    const seat = state.bidOrder[index];
    state = advanceAuction(state, seat, "bid", [4, 3, 2, 1][seat]);
    assert.deepEqual(auctionGame.view(state).bids, [null, null, null, null],
      "auction bids stay hidden until every player has placed one");
    if (index < 3) {
      assert.equal(state.phase, "bid");
      assert.equal(state.actor, state.bidOrder[index + 1]);
    }
  }
  assert.equal(state.phase, "payment", "all bids start the original timed payment presentation");
  state = settle(state);
  assert.equal(state.phase, "bid");
  assert.equal(state.round, 2);
  assert.equal(state.host, 1, "the source rotates the first bidder after every round");
  assert.deepEqual(state.bidOrder, [1, 2, 3, 0]);
  assert.equal(state.actor, 1);
  assert.deepEqual(state.selected[0], [4], "a played card stays consumed within the set");

  state = advanceAuction(state, 1, "bid", 4);
  state = advanceAuction(state, 2, "bid", 1);
  state = advanceAuction(state, 3, "bid", 2);
  assert.equal(state.actor, 0);
  assert.throws(() => advanceAuction(state, 0, "bid", 4), /同じ札を2回/,
    "a player cannot reuse a card during the same set");
  state = advanceAuction(state, 0, "bid", 3);
  state = settle(state);
  assert.equal(state.round, 3);
  assert.equal(state.host, 2);
  assert.equal(state.actor, 2);
  assert.deepEqual(state.bidOrder, [2, 3, 0, 1]);
  assert.deepEqual(state.selected[0], [4, 3]);

  const resolvePaymentRound = (payment, roundWins) => {
    let next = {
      ...createAuction(), round: 3, host: 0, bidOrder: [0, 1, 2, 3], bidIndex: 0,
      actor: 0, payment: [...payment], roundWins: [...roundWins],
    };
    next = advanceAuction(next, 0, "bid", 4);
    next = advanceAuction(next, 1, "bid", 1);
    next = advanceAuction(next, 2, "bid", 1);
    return settle(advanceAuction(next, 3, "bid", 2));
  };
  const exactLimit = resolvePaymentRound([12, 0, 0, 0], [1, 0]);
  assert.equal(exactLimit.phase, "finished");
  assert.equal(exactLimit.reason, "payment");
  assert.equal(exactLimit.winnerTeam, 1, "the opposing team wins when payment reaches 16");
  assert.deepEqual(exactLimit.diamonds, [0, 0],
    "a payment-ending round does not award a set diamond");
  assert.equal(exactLimit.payment[0], 16);
  assert.equal(exactLimit.damage[0], 56000);
  assert.equal(exactLimit.hp[0], 4000);

  const belowLimit = resolvePaymentRound([11, 0, 0, 0], [1, 0]);
  assert.equal(belowLimit.phase, "set_result");
  assert.equal(belowLimit.reason, null);
  assert.deepEqual(belowLimit.diamonds, [1, 0]);
  assert.equal(belowLimit.payment[0], 15);
  assert.equal(belowLimit.damage[0], 8500);
  assert.equal(belowLimit.hp[0], 51500);
  const nextSet = auctionGame.systemAdvance(belowLimit, "next_set");
  assert.equal(nextSet.phase, "bid");
  assert.equal(nextSet.set, 2);
  assert.equal(nextSet.round, 1);
  assert.equal(nextSet.host, 1);
  assert.equal(nextSet.actor, 1);
  assert.deepEqual(nextSet.bidOrder, [1, 2, 3, 0]);
  assert.deepEqual(nextSet.selected, [[], [], [], []],
    "cards refill only when the next set starts");
  assert.deepEqual(nextSet.payment, belowLimit.payment);
  assert.deepEqual(nextSet.diamonds, [1, 0]);

  let timeoutState = createAuction();
  timeoutState = advanceAuction(timeoutState, 0, "bid", 1);
  const timedOutAuction = auctionGame.systemAdvance(timeoutState, "timeout");
  assert.equal(timedOutAuction.winnerTeam, 0,
    "the team opposite the timed-out bidder wins the timeout");
}

{
  assert.equal(fireTemperatureDamage(90), 1200,
    "the source assigns 1500 then overwrites the 90 C band with 1200");
  assert.equal(fireTemperatureDamage(91), 1500);
  assert.equal(fireTemperatureDamage(11), 0);
  assert.equal(fireTemperatureDamage(35), 0);
  assert.equal(fireTemperatureDamage(36), 30);
  assert.equal(fireWindPower(-70), 216);
  assert.equal(fireWindDamage(-70), 648, "the source wind multiplier is 3");
  assert.equal(firePurgeWinner([2, 1], [10, 10]), 0);
  assert.equal(firePurgeWinner([1, 2], [10, 10]), 1);
  assert.equal(firePurgeWinner([2, 2], [100, 200]), 0);
  assert.equal(firePurgeWinner([2, 2], [200, 100]), 1);

  let state = createFire();
  assert.throws(() => advanceFire(state, 0, "choice", { choice: "rock" }), /HOT/);
  const secondFirst = advanceFire(state, 1, "choice", { choice: "rock", heat: 1 });
  assert.equal(secondFirst.actor, 0, "both original Fire players may submit first");
  assert.throws(() => advanceFire(secondFirst, 1, "choice", { choice: "paper", heat: 1 }), /選択済み/);
  state = advanceFire(state, 0, "choice", { number: 3, heat: 1 });
  state = advanceFire(state, 1, "choice", { number: 1, heat: -1 });
  assert.equal(state.phase, "round_result");
  assert.equal(state.revealResolved, false);
  for (let tick = 0; tick < 70; tick++) state = fireSystem(state, "tick");
  assert.equal(state.revealResolved, true);
  assert.deepEqual(state.wins, [1, 0]);
  assert.deepEqual(state.stock, [5, 0]);
  for (let tick = 0; tick < 50; tick++) state = fireSystem(state, "tick");
  assert.deepEqual(state.pendingDamage, [0, 0]);
  assert.deepEqual(state.hp, [60000, 60000]);
  for (let tick = 0; tick < 20; tick++) state = fireSystem(state, "tick");
  assert.equal(state.phase, "choice");
  assert.equal(state.round, 2);

  const playFireRound = (current, first, second) => {
    let next = advanceFire(current, 0, "choice", first);
    next = advanceFire(next, 1, "choice", second);
    while (next.phase === "round_result") next = fireSystem(next, "tick");
    return next;
  };

  state = createFire();
  state = playFireRound(state, { number: 3, heat: 1 }, { number: 1, heat: 1 });
  state = playFireRound(state, { number: 2, heat: 1 }, { number: 3, heat: 1 });
  state = playFireRound(state, { number: 1, heat: 1 }, { number: 2, heat: 1 });
  state = playFireRound(state, { number: 3, heat: -1 }, { number: 3, heat: -1 });
  state = playFireRound(state, { number: 2, heat: -1 }, { number: 2, heat: -1 });
  state = playFireRound(state, { number: 1, heat: -1 }, { number: 1, heat: -1 });
  assert.equal(state.phase, "series_result");
  assert.equal(state.series, 1);
  assert.deepEqual(state.wins, [3, 0]);
  assert.deepEqual(state.stock, [7, 0]);
  for (let tick = 0; tick < 50; tick++) state = fireSystem(state, "tick");
  assert.deepEqual(state.temperature, [27, 20]);
  assert.deepEqual(state.windRemaining, [60, 200]);
  assert.deepEqual(state.windPower, [0, 5]);
  assert.equal(state.seriesPrepared, true);
  while (state.phase === "series_result") state = fireSystem(state, "tick");
  assert.equal(state.phase, "wind");
  const hpBeforeWind = [...state.hp];
  for (let tick = 0; tick < 200; tick++) state = fireSystem(state, "tick");
  assert.equal(state.phase, "choice");
  assert.equal(state.series, 2);
  assert.deepEqual(state.stock, [0, 0]);
  assert.deepEqual(state.wins, [0, 0]);
  assert.deepEqual(state.used, [[], []]);
  assert.ok(state.hp[1] < hpBeforeWind[1], "wind damage uses the source five-tick health loop");

  state = createFire();
  state = playFireRound(state, { number: 2, heat: 1 }, { number: 2, heat: 1 });
  state = playFireRound(state, { number: 3, heat: 1 }, { number: 3, heat: 1 });
  state = playFireRound(state, { number: 1, heat: 1 }, { number: 3, heat: -1 });
  state = playFireRound(state, { number: 1, heat: -1 }, { number: 2, heat: -1 });
  state = playFireRound(state, { number: 2, heat: -1 }, { number: 1, heat: 1 });
  state = playFireRound(state, { number: 3, heat: -1 }, { number: 1, heat: -1 });
  assert.equal(state.phase, "choice");
  assert.equal(state.round, 1);
  assert.deepEqual(state.wins, [0, 0]);
  assert.deepEqual(state.stock, [-5, -5], "a six-round draw keeps stock and redeals cards");
  assert.deepEqual(state.used, [[], []]);

  let wait = {
    ...createFire(), phase: "wait", actor: null, waitRemaining: 600,
    waitDamageTimer: 0, temperature: [100, 100], hp: [60000, 60000], ticks: 0, phaseTicks: 0,
  };
  for (let tick = 0; tick < 30; tick++) wait = fireSystem(wait, "tick");
  assert.deepEqual(wait.pendingDamage, [0, 0], "tick 30 is itself a five-tick damage boundary");
  for (let tick = 0; tick < 2; tick++) wait = fireSystem(wait, "tick");
  assert.deepEqual(wait.hp, [58200, 58200]);
  for (let tick = 0; tick < 568; tick++) wait = fireSystem(wait, "tick");
  assert.equal(wait.phase, "burn");
  assert.deepEqual(wait.temperature, [100, 100]);
  assert.equal(wait.burnDelayRemaining, 180);
  assert.equal(wait.burnRemaining, 6000);

  let timeout = createFire();
  timeout = advanceFire(timeout, 0, "choice", { number: 1, heat: 1 });
  timeout = fireSystem(timeout, "timeout");
  assert.equal(timeout.phase, "sanction");
  assert.equal(timeout.sanctionReason, "timeout");
  assert.deepEqual(timeout.sanctionTargets, [1],
    "only the player who did not submit a card is sanctioned");
  for (let tick = 0; tick < 100; tick++) timeout = fireSystem(timeout, "tick");
  assert.equal(timeout.phase, "ending");
  assert.equal(timeout.winner, 0);
  assert.deepEqual(timeout.hp, [60000, 0]);
  assert.equal(timeout.endingRemaining, 100);
  for (let tick = 0; tick < 100; tick++) timeout = fireSystem(timeout, "tick");
  assert.equal(timeout.phase, "finished");
  assert.equal(timeout.winner, 0);

  let burn = {
    ...createFire(), phase: "burn", actor: null, burnRemaining: 6000,
    burnDelayRemaining: 1, temperature: [100, 100], hp: [60000, 60000],
    glass: [1, 1], ticks: 0, phaseTicks: 0,
  };
  burn = fireSystem(burn, "tick");
  assert.deepEqual(burn.pendingDamage, [900, 900]);
  for (let tick = 0; tick < 4; tick++) burn = fireSystem(burn, "tick");
  assert.deepEqual(burn.hp, [59100, 59100]);
  for (let tick = 0; tick < 5; tick++) burn = fireSystem(burn, "tick");
  assert.deepEqual(burn.hp, [58200, 58200], "burn assigns 900 again instead of only once");

  let cracks = {
    ...createFire(), phase: "wind", actor: null, temperature: [-70, 30],
    windPower: [216, 18], windRemaining: [400, 400], lastWindStock: [-70, 0],
    hp: [60000, 60000], glass: [1, 1], glassStage: [0, 0], ticks: 0, phaseTicks: 0,
  };
  for (let tick = 0; tick < 60; tick++) cracks = fireSystem(cracks, "tick");
  assert.equal(cracks.glassStage[0], 1);
  for (let tick = 0; tick < 60; tick++) cracks = fireSystem(cracks, "tick");
  assert.equal(cracks.glassStage[0], 4);
  for (let tick = 0; tick < 180; tick++) cracks = fireSystem(cracks, "tick");
  assert.deepEqual(cracks.glass, [0, 1]);
  assert.equal(cracks.glassStage[0], 5);

  let purge = {
    ...createFire(), phase: "burn", actor: null, burnRemaining: 1,
    burnDelayRemaining: 0, totalWins: [1, 2], spend: [100, 200],
    temperature: [100, 100], hp: [60000, 60000], glass: [0, 0], ticks: 0, phaseTicks: 0,
  };
  purge = fireSystem(purge, "tick");
  assert.equal(purge.phase, "sanction");
  assert.deepEqual(purge.sanctionTargets, [0]);
  assert.equal(purge.sanctionReason, "purge");
  let safety = 0;
  while (purge.phase === "sanction" && safety++ < 110) purge = fireSystem(purge, "tick");
  assert.equal(purge.phase, "ending");
  assert.equal(purge.winner, 1);
  assert.deepEqual(purge.hp, [0, 60000]);
  for (let tick = 0; tick < 100; tick++) purge = fireSystem(purge, "tick");
  assert.equal(purge.phase, "finished");
  assert.equal(purge.winner, 1);
  assert.throws(() => fireGame.advance(purge, 0, "choice", { number: 1, heat: 1 }), /終了/);

  let presentationState = createFire();
  let presentation = fireVenuePresentation(fireView(presentationState), ["P1", "P2"]);
  assert.equal(presentation.cards.rock_hot_1.frame, FIRE_PROP_FRAME.active);
  assert.equal(presentation.cards.rock_hot_2.frame, FIRE_PROP_FRAME.active);
  assert.match(presentation.tvText, /S1\/4/);
  assert.match(presentation.receptionText, /両者が同時/);
  presentationState = advanceFire(presentationState, 0, "choice", { number: 1, heat: 1 });
  presentationState = advanceFire(presentationState, 1, "choice", { number: 2, heat: -1 });
  presentation = fireVenuePresentation(fireView(presentationState), ["P1", "P2"]);
  assert.equal(presentation.cards.rock_hot_1.frame, FIRE_PROP_FRAME.available,
    "both cards stay hidden until the judge phase completes");
  assert.doesNotMatch(presentation.tvText, /前R/, "unrevealed choices must not reach the TV");
  for (let tick = 0; tick < 70; tick++) presentationState = fireSystem(presentationState, "tick");
  presentation = fireVenuePresentation(fireView(presentationState), ["P1", "P2"]);
  assert.equal(presentation.cards.rock_hot_1.frame, FIRE_PROP_FRAME.hot);
  assert.equal(presentation.cards.scissors_ice_2.frame, FIRE_PROP_FRAME.ice);
  assert.match(presentation.tvText, /グー HOT/);
  const cracked = {
    ...createFire(), phase: "wind", actor: null, windRemaining: [20, 20],
    glass: [1, 0], glassStage: [3, 5], lastWindStock: [-80, -70],
  };
  presentation = fireVenuePresentation(fireView(cracked), ["P1", "P2"]);
  assert.deepEqual(presentation.glassFrames,
    [FIRE_PROP_FRAME.glassCrack3, FIRE_PROP_FRAME.glassBroken]);
}

{
  const tickUntil = (state, phase, limit = 300) => {
    let current = state;
    for (let count = 0; current.phase !== phase && count < limit; count++) {
      current = pigSystem(current, "tick");
    }
    assert.equal(current.phase, phase);
    return current;
  };
  const chooseAll = (state, cards) => {
    let current = state;
    for (let index = 0; index < cards.length; index++) {
      const seat = current.actor;
      current = advancePig(current, seat, "choose", { card: cards[seat] });
    }
    return current;
  };
  const playPeacefulRound = (state, cards) => {
    let current = chooseAll(state, cards);
    assert.equal(current.phase, "reveal");
    assert.deepEqual(pigGame.view(current).choices, [null, null, null, null],
      "pig cards stay sealed during the confirmation window");
    for (let ticks = 0; ticks < PIG_REVEAL_HIDDEN_TICKS - PIG_TICK_STEP; ticks += PIG_TICK_STEP) {
      current = pigSystem(current, "tick");
      assert.equal(current.revealed, false);
    }
    current = pigSystem(current, "tick");
    assert.equal(current.revealed, true, "cards reveal at 40 ticks");
    assert.deepEqual(pigGame.view(current).choices.map(entry => entry.card), cards);
    current = tickUntil(current, "round_result");
    assert.equal(current.last.kind, "draw");
    return tickUntil(current, "choose");
  };

  let state = playPeacefulRound(createPig(), [1, 2, 3, 4]);
  assert.equal(state.host, 1, "host rotates after every round");
  assert.equal(state.handRound, 2);
  state = playPeacefulRound(state, [2, 3, 4, 1]);
  assert.equal(state.host, 2);
  assert.equal(state.handRound, 3);
  state = playPeacefulRound(state, [3, 4, 1, 2]);
  assert.equal(state.host, 3);
  assert.equal(state.setNumber, 2);
  assert.equal(state.handRound, 1);
  assert.deepEqual(state.used, [[], [], [], []], "cards reset only after round 3");

  let skipped = {
    ...createPig(), phase: "round_result", phaseTicks: 35,
    used: [[4], [4], [4], [4]],
  };
  skipped = pigSystem(skipped, "tick");
  assert.equal(skipped.phase, "round_result");
  assert.equal(skipped.handRound, 2);
  assert.equal(skipped.host, 1);
  assert.equal(skipped.last.kind, "no_wolf");
  assert.deepEqual(skipped.choices, [null, null, null, null],
    "an all-wolf-used round skips before card selection");

  let attack = chooseAll(createPig(), [4, 1, 2, 4]);
  attack = tickUntil(attack, "attack");
  assert.deepEqual(attack.attackOrder, [0, 3], "attack order starts at the host");
  attack = advancePig(attack, 0, "target", 3);
  assert.equal(attack.wolfSpent[3], true);
  assert.deepEqual(attack.pigs, [5, 5, 5, 5]);
  assert.equal(attack.phase, "settle");
  assert.equal(attack.last.attacks.some(item => item.kind === "rage"), false,
    "a wolf hit before its turn is skipped without the rage penalty");

  let rage = chooseAll(createPig(), [4, 3, 3, 3]);
  rage = tickUntil(rage, "settle");
  assert.deepEqual(rage.pigs, [3, 5, 5, 5]);
  assert.equal(rage.o2Damage, 2);
  assert.equal(rage.last.attacks.at(-1).kind, "rage");

  const death = () => ({
    ...chooseAll({
      ...createPig(), pigs: [5, 1, 5, 5],
    }, [4, 1, 3, 3]),
  });
  let lethal = death();
  lethal = tickUntil(lethal, "attack");
  lethal = advancePig(lethal, 0, "target", 1);
  lethal = tickUntil(lethal, "ending");
  assert.equal(lethal.reason, "death");
  assert.equal(lethal.winnerTeam, 0, "seat 2 death gives 1P+4P the win");
  assert.equal(lethal.endingRemaining, PIG_DEATH_ENDING_TICKS);
  lethal = tickUntil(lethal, "finished", 100);
  assert.equal(lethal.reason, "death");

  let palace = chooseAll({
    ...createPig(), labor: [5, 0, 0, 0],
  }, [4, 1, 3, 3]);
  palace = tickUntil(palace, "attack");
  palace = advancePig(palace, 0, "target", 1);
  palace = tickUntil(palace, "construction");
  palace = tickUntil(palace, "ending");
  assert.equal(palace.reason, "palace");
  assert.equal(palace.winnerTeam, 0);
  assert.equal(palace.labor[0], 6);

  let bothPalaces = chooseAll({
    ...createPig(), labor: [5, 6, 0, 0],
  }, [4, 1, 3, 3]);
  bothPalaces = tickUntil(bothPalaces, "attack");
  bothPalaces = advancePig(bothPalaces, 0, "target", 1);
  bothPalaces = tickUntil(bothPalaces, "ending");
  assert.equal(bothPalaces.reason, "palace");
  assert.equal(bothPalaces.winnerTeam, null, "both teams completing a palace is a draw");

  const bothDead = {
    ...createPig(), phase: "oxygen", actor: null, phaseTicks: 115,
    pigs: [0, 0, 5, 5], oxygen: [6, 6, 21, 21], o2Damage: 1,
    labor: [0, 0, 0, 0], last: { cards: [4, 1, 3, 3], attacks: [] },
  };
  const draw = pigSystem(bothDead, "tick");
  assert.equal(draw.phase, "ending");
  assert.equal(draw.reason, "death");
  assert.equal(draw.winnerTeam, null, "deaths on both teams is a draw");

  const oxygenView = pigVenuePresentation({
    ...createPig(), phase: "oxygen", pigs: [0, 1, 2, 3],
    oxygen: [PIG_OXYGEN[0], PIG_OXYGEN[1], PIG_OXYGEN[2], PIG_OXYGEN[3]],
  }, ["P1", "P2", "P3", "P4"]);
  assert.match(oxygenView.seatTexts[0], /6\.0%/, "zero pigs mirrors source tv/o2 6.00%");
  assert.match(oxygenView.tvText, /第1セット 第1\/3ラウンド/);

  let hidden = chooseAll(createPig(), [4, 1, 3, 1]);
  assert.equal(hidden.phase, "reveal");
  let presentation = pigVenuePresentation(pigGame.view(hidden), ["P1", "P2", "P3", "P4"]);
  assert.equal(presentation.cards.pig_card_1_4.frame, PIG_PROP_FRAME.hidden);
  hidden = tickUntil(hidden, "attack");
  presentation = pigVenuePresentation(pigGame.view(hidden), ["P1", "P2", "P3", "P4"]);
  assert.equal(presentation.cards.pig_card_1_4.frame, PIG_PROP_FRAME.wolf);
  assert.equal(presentation.targets.pig_target_1_2.available, true);
  assert.equal(presentation.targets.pig_target_1_2.frame, PIG_PROP_FRAME.targetReady);
  assert.equal(presentation.targets.pig_target_1_3.available, false,
    "brick houses are never legal wolf targets");
  assert.equal(presentation.targets.pig_target_1_3.frame, PIG_PROP_FRAME.targetIdle);
  assert.deepEqual(physicalControlRequest("pig", createPig(), "pig_card_1_4"),
    { action: "choose", value: { card: 4 } });
  assert.deepEqual(physicalControlRequest("pig", hidden, "pig_target_1_2"),
    { action: "target", value: 1 });

  const stored = {};
  const world = {
    setDynamicProperty(key, value) { stored[key] = value; },
    getDynamicProperty(key) { return stored[key]; },
  };
  const manager = new GameManager([pigGame], { world });
  for (let seat = 1; seat <= 4; seat++) manager.join("pig-persist", "pig", `p${seat}`);
  const reveal = { ...createPig(), phase: "reveal", actor: null, phaseTicks: 25 };
  manager.tables.get("pig-persist").state = reveal;
  manager.persist();
  const restored = new GameManager([pigGame], { world });
  assert.equal(restored.restore().restored, 1);
  const recovered = restored.view("pig-persist").state;
  assert.equal(recovered.phase, "reveal");
  assert.equal(recovered.phaseTicks, 25, "deterministic phase timing survives reload");
}

{
  let state = createLight();
  const initialOxygen = [...state.oxygen];
  state = lightGame.systemAdvance(state, "tick");
  assert.deepEqual(state.oxygen, initialOxygen, "the intro candle lighting does not consume oxygen");
  while (state.phase === "intro") state = lightGame.systemAdvance(state, "tick");
  assert.equal(state.phase, "guard_arrange");
  assert.deepEqual(state.torches, [1, 1], "the source lights one torch per player before round 1");
  for (const slot of [1, 2, 3]) {
    const step = advancePhysicalControl("light", state, `light_grave_1_${slot}`);
    assert.deepEqual(step.request, { action: "place", value: { role: "guard", slot: slot - 1 } });
    state = advanceLight(state, state.actor, step.request.action, step.request.value);
  }
  assert.equal(state.phase, "tourist_arrange");
  assert.equal(state.actor, 1);
  assert.equal(lightGame.view(state).guards, null, "guard identities stay hidden before judgement");
  for (const slot of [2, 1, 3]) {
    const step = advancePhysicalControl("light", state, `light_grave_2_${slot}`);
    assert.deepEqual(step.request, { action: "place", value: { role: "tourist", slot: slot - 1 } });
    state = advanceLight(state, state.touristSetter, step.request.action, step.request.value);
  }
  assert.equal(state.phase, "result");
  assert.deepEqual(state.guards, [0, 1, 2]);
  assert.deepEqual(state.tourists, [1, 0, 2]);
  assert.equal(state.hearts[0], 2, "welcome + heart scores two hearts");
  assert.equal(state.torches[0], 2, "lazy + torch adds one torch after ignition");
  assert.equal(state.oxygen[0], 192000, "one torch halves oxygen once");
  assert.equal(lightGame.view(state).guards[1], 1, "both arrangements become visible at result");

  let burning = { ...state, phase: "result", resultRemaining: 250 };
  const oxygenBeforeResultTick = burning.oxygen[0];
  burning = lightGame.systemAdvance(burning, "tick");
  assert.equal(burning.oxygen[0], oxygenBeforeResultTick - 1,
    "a lit torch consumes oxygen during the source result loop");

  let winning = { ...createLight(), introRemaining: 0, phase: "guard_arrange", actor: 0,
    introTorchLit: true, torches: [1, 1], hearts: [6, 0] };
  for (const slot of [1, 2, 3]) {
    const step = advancePhysicalControl("light", winning, `light_grave_1_${slot}`);
    winning = advanceLight(winning, 0, step.request.action, step.request.value);
  }
  for (const slot of [2, 1, 3]) {
    const step = advancePhysicalControl("light", winning, `light_grave_2_${slot}`);
    winning = advanceLight(winning, 1, step.request.action, step.request.value);
  }
  assert.equal(winning.phase, "finished");
  assert.equal(winning.reason, "hearts");
  assert.equal(winning.winner, 0);
  assert.equal(winning.oxygen[0], LIGHT_START_OXYGEN,
    "the seventh heart ends the round before ignition halves oxygen");
  assert.deepEqual(winning.torches, [1, 1], "a hearts victory does not add the round's torch result");
  assert.equal(winning.last.ignition.attempted, false);
}

{
  let state = createMine();
  for (let guard = 0; state.phase !== "finished" && guard < 5000; guard++) {
    if (state.phase === "arrange") {
      state = advanceMine(state, state.setter, "arrange", { cards: [0, 1, 2, 3] });
    } else if (state.phase === "choose") {
      state = advanceMine(state, state.chooser, "mine", {
        indices: [2, 3, 1, 0].filter(index => !state.opened[state.chooser].includes(index)).slice(0, state.chooseCount),
      });
    } else {
      state = mineGame.systemAdvance(state, "tick", { ticks: 200 });
    }
  }
  assert.equal(state.phase, "finished", "the mine state machine reaches a result");
  assert.ok(["poison", "ingots", "explosion", "final"].includes(state.reason));
  assert.equal(mineGame.view(state).boards, undefined, "rock layouts stay hidden");
}

{
  let state = createHope(3);
  assert.equal(hopeAiMotionActive({
    ...state, phase: "reveal", phaseRemaining: HOPE_REVEAL_TICKS,
  }), true, "rounds 1-3 keep traders moving through the 99-tick reveal");
  assert.equal(hopeAiMotionActive({
    ...state, round: 4, phase: "reveal", phaseRemaining: HOPE_REVEAL_TICKS,
  }), true, "rounds 4-10 keep traders walking after the reveal begins");
  assert.equal(hopeAiMotionActive({
    ...state, round: 4, phase: "reveal", phaseRemaining: 1,
  }), true, "rounds 4-10 walk until timeup, not for only the first 20 ticks");
  assert.equal(hopeAiMotionActive({
    ...state, round: 4, phase: "drop", phaseRemaining: 85,
  }), false, "drop starts with NoAI:1b and must not move traders");
  assert.equal(hopeAiMotionActive({
    ...state, round: 1, phase: "drop", phaseRemaining: 85,
  }), false, "rounds 1-3 stop traders when timeup ends the reveal");
  state = advanceHope(state, 0, "floor", 1);
  assert.deepEqual(hopeGame.view(state).choices, [null, null, null],
    "hope floor choices stay hidden until every survivor chooses");
  {
    // game:hope/out judges every participant against the nearest marker. A
    // participant with no readable position must not survive the fall.
    let unresolved = createHope(2);
    for (let guard = 0; unresolved.phase !== "finished" && guard < 500; guard++) {
      unresolved = hopeSystem(unresolved, "tick");
    }
    assert.equal(unresolved.phase, "finished");
    assert.equal(unresolved.reason, "fallen");
    assert.deepEqual(unresolved.winners, [],
      "a participant who never reached a Hope floor must be eliminated");
  }
  const positioned = createHope(3);
  const safePad = current => current.safePad + 1;
  const positions = current => current.alive.map(alive => alive ? safePad(current) : null);
  let physical = hopeSystem(positioned, "position", positions(positioned));
  assert.deepEqual(physical.choices.map(choice => choice?.pad), [safePad(physical), safePad(physical), safePad(physical)],
    "hope must accept simultaneous physical floor positions without a form");
  const choiceTicks = physical.phaseRemaining;
  for (let tick = 1; tick < choiceTicks; tick++) physical = hopeSystem(physical, "tick");
  assert.equal(physical.phase, "choose");
  assert.equal(physical.phaseRemaining, 1);
  physical = hopeSystem(physical, "tick");
  assert.equal(physical.phase, "reveal", "the source timeup starts the 99-tick reveal");
  physical = hopeSystem(physical, "position", [2, 3, 4]);
  assert.deepEqual(physical.choices.map(choice => choice?.pad), [2, 3, 4],
    "players may keep moving while the hazard is revealed");
  let guard = 0;
  while (physical.phase !== "drop" && guard++ < 200) physical = hopeSystem(physical, "tick");
  assert.equal(physical.phase, "drop");
  assert.equal(physical.floorOpen, false,
    "the physical floor must stay closed during the 85-tick drop countdown");
  guard = 0;
  while (physical.phase !== "result" && guard++ < 200) physical = hopeSystem(physical, "tick");
  assert.equal(physical.phase, "result");
  assert.equal(physical.floorOpen, true, "hazard floors open only when the fall resolves");
  state = createHope(3);
  const advanceToPhase = (current, phase) => {
    let guard = 0;
    while (current.phase !== phase && current.phase !== "finished" && guard++ < 500) {
      current = hopeSystem(current, "tick");
    }
    assert.ok(guard < 500, `hope did not reach ${phase}`);
    return current;
  };
  for (let round = 0; round < 10; round++) {
    if (state.phase === "finished") break;
    const aliveAi = state.ai.filter(value => value.alive);
    const deadBefore = new Set(state.ai.filter(value => !value.alive).map(value => value.id));
    const expectedAiLosses = state.round === 3 || state.round === 6 || state.round === 8
      ? 2 : state.round === 4 ? 6 : 1;
    const loserAi = aliveAi.filter(value => value.loser);
    assert.equal(loserAi.length, Math.min(expectedAiLosses, aliveAi.length),
      `hope loser roles must match round ${state.round}`);
    for (const loser of loserAi) {
      assert.ok(state.hazards.includes(state.padSymbols[loser.pad - 1]),
        "every loser role must target an out judge");
    }
    const aiOnHazardPads = aliveAi.filter(value =>
      value.pad <= 16 && state.hazards.includes(state.padSymbols[value.pad - 1])).length;
    assert.equal(state.ai.length, HOPE_AI_TRADERS);
    assert.equal(aiOnHazardPads, Math.min(expectedAiLosses, aliveAi.length),
      `hope AI pads must use the round ${state.round} symbol assignment`);
    const visible = hopeGame.view(state);
    assert.equal(visible.safe, null, "hazard and safe floor are hidden before resolution");
    assert.deepEqual(visible.hazards, []);
    assert.ok(visible.ai.every(value => !("loser" in value)),
      "the internal loser role must not leak through the player view");
    // The source has simultaneous physical floor selection, so every alive
    // participant chooses the pad carrying the current safe symbol.
    state = hopeSystem(state, "position", positions(state));
    state = advanceToPhase(state, "reveal");
    assert.equal(state.floorOpen, false);
    state = advanceToPhase(state, "drop");
    assert.equal(state.floorOpen, false, "a restored physical floor stays closed until the fall");
    if (state.round === 10) {
      const checkRemaining = HOPE_DROP_TICKS - HOPE_ROUND_TEN_CHECK_AFTER_DROP_TICKS;
      const safeAtCheck = { ...state, phaseRemaining: checkRemaining };
      const hazardPad = state.padSymbols.findIndex(symbol => state.hazards.includes(symbol)) + 1;
      const boundaryState = {
        ...state,
        ai: state.ai.map((value, index) => index === 0
          ? { ...value, alive: true, pad: hazardPad, loser: true }
          : value),
      };
      const beforeWindow = hopeSystem({ ...boundaryState, phaseRemaining: checkRemaining + 2 }, "tick");
      assert.equal(beforeWindow.phase, "drop");
      assert.equal(beforeWindow.phaseRemaining, checkRemaining + 1,
        "round 10 must not check before the source's time=5790 window");
      assert.equal(beforeWindow.roundTenNoOutChecked, undefined,
        "the one-shot check must remain armed until the source window");
      const atWindow = hopeSystem(beforeWindow, "tick");
      assert.equal(atWindow.roundTenNoOut, undefined,
        "a hazard sighting must not be converted to a no-out result");
      assert.equal(atWindow.roundTenNoOutChecked, true,
        "the round-ten check must run exactly once at the source boundary");
      const tooLate = hopeSystem({ ...state, phaseRemaining: 1, roundTenNoOutChecked: true }, "tick");
      assert.equal(tooLate.phase, "result",
        "round 10 must not treat the final drop tick as the source's time=5790 check");
      const unsafeChoice = state.choices.map((choice, seat) => seat === 0
        ? { pad: hazardPad, symbol: state.padSymbols[hazardPad - 1] } : choice);
      const unsafe = advanceToPhase(
        hopeSystem({ ...safeAtCheck, choices: unsafeChoice }, "tick"), "result");
      assert.equal(unsafe.phase, "result",
        "round 10 must use normal resolution when an alive participant is on a hazard");
      assert.equal(unsafe.floorOpen, true);
      const missingChoice = state.choices.map((choice, seat) => seat === 0 ? null : choice);
      const missing = advanceToPhase(
        hopeSystem({ ...safeAtCheck, choices: missingChoice }, "tick"), "result");
      assert.equal(missing.phase, "result",
        "round 10 must not fast-forward when an alive participant lacks a valid pad");
      assert.ok(missing.last.eliminated.includes(0));

      const liveHazard = {
        ...safeAtCheck,
        ai: safeAtCheck.ai.map((value, index) => index === 0
          ? { ...value, alive: true, pad: hazardPad, loser: true }
          : value),
      };
      const blocked = advanceToPhase(hopeSystem(liveHazard, "tick"), "result");
      assert.equal(blocked.phase, "result",
        "round 10 must not fast-forward while a living AI remains on a hazard floor");
      const noOutState = {
        ...safeAtCheck,
        ai: safeAtCheck.ai.map(value => ({ ...value, alive: false })),
      };
      const aiBefore = noOutState.ai.map(value => ({ ...value }));
      state = hopeSystem(noOutState, "tick");
      assert.equal(state.phase, "drop");
      assert.equal(state.roundTenNoOut, true);
      assert.equal(state.phaseRemaining, HOPE_ROUND_TEN_NO_OUT_REMAINING_TICKS,
        "the time=5790 branch must jump to the source's time=5710 hold");
      assert.equal(state.floorOpen, false);
      for (let tick = 1; tick < HOPE_ROUND_TEN_NO_OUT_REMAINING_TICKS; tick++) {
        state = hopeSystem(state, "tick");
      }
      assert.equal(state.phase, "drop");
      assert.equal(state.phaseRemaining, 1);
      state = hopeSystem(state, "tick");
      assert.equal(state.phase, "finished",
        "round 10 with no outplayer finishes without opening the floor");
      assert.equal(state.reason, "survived");
      assert.equal(state.floorOpen, false);
      assert.deepEqual(state.ai, aiBefore, "round 10 fast-forward preserves AI match history");
      assert.deepEqual(state.last.aiLost, []);
      assert.equal(state.last.noOut, true);
      const persistedNoOut = JSON.parse(JSON.stringify(state));
      for (let tick = 0; tick < HOPE_ROUND_TEN_NO_OUT_REMAINING_TICKS; tick++) {
        state = hopeSystem(state, "tick");
      }
      assert.equal(state.phase, "finished");
      assert.equal(persistedNoOut.roundTenNoOut, true,
        "the no-out marker must survive JSON persistence");
      break;
    }
    state = advanceToPhase(state, "result");
    assert.equal(state.floorOpen, true);
    assert.equal(state.last.aiLost.length, Math.min(expectedAiLosses, aliveAi.length),
      "only AI traders standing on the current hazard symbols may fall");
    assert.equal(state.ai.filter(value => !value.alive).length,
      deadBefore.size + Math.min(expectedAiLosses, aliveAi.length),
      "fallen Hope judges accumulate instead of being revived each round");
    for (const id of deadBefore) {
      assert.equal(state.ai.find(value => value.id === id).alive, false,
        "an AI that fell in an earlier round must stay dead");
    }
    for (const id of state.last.aiLost) {
      const fallen = state.ai.find(value => value.id === id);
      assert.equal(fallen.alive, false);
      assert.equal(fallen.loser, true,
        "the hope-loser role remains on the fallen judge for persisted state");
    }
    state = advanceToPhase(state, "choose");
  }
  assert.equal(state.phase, "finished");
  assert.equal(state.winners.length, 3);
  assert.equal(state.reason, "survived");
  assert.equal(state.floorOpen, false, "the hall floor must close when the match ends");
  assert.ok(hopeGame.view(state).hazards.length > 0);
}

{
  const forceDrop = value => {
    const next = clockSystem({ ...value, moveRemaining: CLOCK_MOVE_END_TICKS + 1 }, "tick");
    assert.equal(next.phase, "drop");
    return next;
  };
  const forceNextTurn = value => {
    const next = clockSystem({ ...value, dropRemaining: CLOCK_NEXT_TURN_TICKS + 1 }, "tick");
    assert.equal(next.phase, "setup");
    return next;
  };

  let state = createClock();
  const initialView = clockGame.view(state);
  assert.deepEqual(
    [state.turn, state.actor, state.requiredSectors, state.hour, state.minute,
      state.nextHour, state.nextMinute, initialView.cursorLabel],
    [1, 0, 1, 1, 0, 1, 0, "A"]);
  state = advanceClock(state, 0, "hour_minus");
  state = advanceClock(state, 0, "minute_minus");
  assert.deepEqual([state.nextHour, state.nextMinute], [12, 55], "time wraps backward");
  state = advanceClock(state, 0, "hour_plus");
  state = advanceClock(state, 0, "minute_plus");
  assert.deepEqual([state.nextHour, state.nextMinute], [1, 0], "time wraps forward");
  state = advanceClock(state, 0, "hour_plus");
  assert.equal(clockGame.view(state).nextHour, 2,
    "the next time is visible while the current time remains separate");
  assert.equal(clockGame.view(state).hp, undefined);
  assert.equal(clockGame.view(state).attacks, undefined);
  state = advanceClock(state, 0, "time_confirm");
  assert.deepEqual([state.phase, state.hour, state.minute, state.nextHour, state.nextMinute],
    ["move", 1, 0, 2, 0], "time confirmation must keep the old time as the sweep origin");
  const needleStart = clockNeedleAngles({ ...state, moveRemaining: CLOCK_NEEDLE_START_TICKS });
  assert.deepEqual([needleStart.progress, needleStart.rotating], [0, true]);
  assert.ok(Math.abs(needleStart.hour - Math.PI / 6) < 1e-9);
  assert.ok(Math.abs(needleStart.minute) < 1e-9);
  const needleHalf = clockNeedleAngles({
    ...state, moveRemaining: (CLOCK_NEEDLE_START_TICKS + CLOCK_NEEDLE_END_TICKS) / 2,
  });
  assert.ok(Math.abs(needleHalf.hour - Math.PI / 4) < 1e-9,
    "the hour hand must sweep halfway from 1:00 to 2:00");
  const needleEnd = clockNeedleAngles({ ...state, moveRemaining: CLOCK_NEEDLE_END_TICKS });
  assert.ok(Math.abs(needleEnd.hour - Math.PI / 3) < 1e-9);
  assert.equal(clockNeedleProgress({ ...state, moveRemaining: 5999 }), null);
  const wrapState = { ...state, nextHour: 12, nextMinute: 55 };
  const wrapHalf = clockNeedleAngles({
    ...wrapState, moveRemaining: (CLOCK_NEEDLE_START_TICKS + CLOCK_NEEDLE_END_TICKS) / 2,
  });
  assert.ok(Math.abs(wrapHalf.hour - 13 * Math.PI / 12) < 1e-9,
    "1:00 to 12:00 must sweep 330 degrees clockwise, not 30 degrees backwards");
  assert.ok(Math.abs(wrapHalf.minute - 11 * Math.PI / 12) < 1e-9);
  assert.throws(() => advanceClock(state, 0, "set", 7), /区画/,
    "direct sector selection is not a clock action");
  assert.throws(() => advanceClock(state, 1, "sector_cw"), /手番/,
    "the inactive player cannot move the sector cursor");

  state = createClock();
  state = advanceClock(state, 0, "time_confirm");
  state = advanceClock(state, 0, "sector_confirm");
  assert.deepEqual(state.selected, [0]);
  assert.equal(state.selectionComplete, true);
  state = forceDrop(state);
  const open = clockSystem({ ...state, dropRemaining: CLOCK_FLOOR_OPEN_TICKS + 1 }, "tick");
  assert.equal(clockGame.view(open).floorOpen, true);
  const restored = clockSystem({ ...open, dropRemaining: CLOCK_FLOOR_RESTORE_TICKS + 1 }, "tick");
  assert.equal(clockGame.view(restored).floorOpen, false);
  state = forceNextTurn(state);
  assert.deepEqual([state.turn, state.actor, state.requiredSectors], [2, 1, 2]);
  state = advanceClock(state, 1, "time_confirm");
  state = advanceClock(state, 1, "sector_confirm");
  assert.deepEqual([state.selected, state.cursor], [[0], 1]);
  state = advanceClock(state, 1, "sector_ccw");
  assert.equal(state.cursor, 11, "counter-clockwise cursor skips a finalized sector");
  state = advanceClock(state, 1, "sector_confirm");
  assert.deepEqual(state.selected, [0, 11]);
  assert.equal(state.selectionComplete, true);

  let rounds = createClock();
  for (let turn = 1; turn <= 12; turn++) {
    assert.deepEqual([rounds.turn, rounds.requiredSectors],
      [turn, Math.min(turn, 12)]);
    rounds = advanceClock(rounds, rounds.actor, "time_confirm");
    for (let sector = 0; sector < rounds.requiredSectors; sector++) {
      rounds = advanceClock(rounds, rounds.actor, "sector_confirm");
    }
    assert.deepEqual([rounds.selected.length, rounds.selectionComplete],
      [rounds.requiredSectors, true]);
    rounds = forceDrop(rounds);
    rounds = forceNextTurn(rounds);
  }
  assert.deepEqual([rounds.turn, rounds.requiredSectors, rounds.actor], [13, 12, 0],
    "turn thirteen onward requires all twelve sectors");

  const ready = (() => {
    let value = createClock();
    value = advanceClock(value, 0, "time_confirm");
    value = advanceClock(value, 0, "sector_confirm");
    return forceDrop(value);
  })();
  const single = clockSystem(ready, "land", 0);
  assert.deepEqual([single.phase, single.winner, single.reason, single.landed],
    ["result", 1, "landed", [0]]);
  assert.equal(single.resultRemaining, CLOCK_LANDING_END_TICKS);
  const both = clockSystem(ready, "land", [1, 0]);
  assert.deepEqual([both.winner, both.reason, both.landed], [null, "landed", [1, 0]],
    "simultaneous landings are a draw");
  const duplicate = clockSystem(ready, "land", [0, 0, 3]);
  assert.deepEqual([duplicate.winner, duplicate.landed], [1, [0]]);
  const finishedLanding = clockSystem({ ...single, resultRemaining: 1 }, "tick");
  assert.deepEqual([finishedLanding.phase, finishedLanding.winner], ["finished", 1]);

  const setupTimeout = clockSystem({ ...createClock(), setupRemaining: 1 }, "tick");
  assert.deepEqual([setupTimeout.phase, setupTimeout.winner, setupTimeout.reason],
    ["result", 1, "timeout"]);
  assert.equal(setupTimeout.resultRemaining, CLOCK_TIMEOUT_END_TICKS);
  const timeoutEnd = clockSystem({ ...setupTimeout, resultRemaining: 1 }, "tick");
  assert.deepEqual([timeoutEnd.phase, timeoutEnd.winner], ["finished", 1]);
  let moveTimeout = advanceClock(createClock(), 0, "time_confirm");
  moveTimeout = clockSystem({ ...moveTimeout, moveRemaining: 1 }, "tick");
  assert.deepEqual([moveTimeout.phase, moveTimeout.winner, moveTimeout.reason],
    ["result", 1, "timeout"], "the opponent wins a selection timeout");

  const clockVenue = VENUE_CONFIG.VENUES.find(venue => venue.id === "clock");
  assert.ok(clockVenue.clock_geometry, "Clock must publish needle collision geometry");
  const clockGeometry = clockVenue.clock_geometry;
  const needleState = {
    ...advanceClock(createClock(), 0, "time_confirm"),
    moveRemaining: (CLOCK_NEEDLE_START_TICKS + CLOCK_NEEDLE_END_TICKS) / 2,
  };
  const needleAngles = clockNeedleAngles(needleState);
  const pointer = angle => ({
    x: clockGeometry.center_x + Math.sin(angle) * 4,
    z: clockGeometry.center_z - Math.cos(angle) * 4,
  });
  assert.deepEqual(clockNeedleContacts(needleState, pointer(needleAngles.minute), clockGeometry),
    ["minute"], "a participant on the sweeping minute hand must be hit");
  assert.deepEqual(clockNeedleContacts(needleState, pointer(needleAngles.hour), clockGeometry),
    ["hour"], "a participant on the sweeping hour hand must be hit");
  assert.deepEqual(clockNeedleContacts({ ...needleState, moveRemaining: 5999 },
    pointer(needleAngles.minute), clockGeometry), [],
  "the stationary hands must not damage before the source rotation window");
  assert.deepEqual(clockNeedleContacts(needleState, pointer(needleAngles.minute + Math.PI / 2),
    clockGeometry), [], "a participant beside the hand must not be hit");
  const blocks = new Map();
  const blockKey = (x, y, z) => `${x},${y},${z}`;
  const block = typeId => ({
    typeId,
    setType(next) { this.typeId = next; },
  });
  for (const area of clockVenue.drop_areas) {
    for (const cell of area.cells) {
      blocks.set(blockKey(cell.x, cell.y, cell.z), block(area.floor));
      blocks.set(blockKey(cell.x, cell.y - 1, cell.z), block("minecraft:water"));
    }
  }
  let landingPlayers = [];
  const fixtureEntities = [];
  const fakeDimension = {
    id: "minecraft:overworld",
    getBlock({ x, y, z }) { return blocks.get(blockKey(x, y, z)); },
    getEntities({ tags = [], location, maxDistance } = {}) {
      return fixtureEntities.filter(entity => !entity.removed &&
        tags.every(tag => entity.hasTag(tag)) &&
        (!location || Math.hypot(entity.location.x - location.x,
          entity.location.z - location.z) <= maxDistance));
    },
    spawnEntity(typeId, location) {
      const tags = new Set(), properties = new Map(), dynamic = new Map();
      const entity = {
        typeId, location: { ...location }, nameTag: "", removed: false,
        getComponent: name => name === "minecraft:health" ? { currentValue: 20, effectiveMax: 20 } : undefined,
        hasTag(tag) { return tags.has(tag); },
        addTag(tag) { tags.add(tag); },
        getTags() { return [...tags]; },
        setProperty(name, value) { properties.set(name, value); },
        getProperty(name) { return properties.get(name); },
        setDynamicProperty(name, value) { dynamic.set(name, value); },
        getDynamicProperty(name) { return dynamic.get(name); },
        remove() { this.removed = true; },
      };
      fixtureEntities.push(entity);
      return entity;
    },
  };
  const fakeProperties = new Map();
  const fakeSystem = {
    runTimeout() {}, runInterval() {}, clearRun() {}, currentTick: 100,
  };
  const fakeWorld = {
    getDimension() { return fakeDimension; },
    getAllPlayers() { return landingPlayers; },
    setDynamicProperty(key, value) { fakeProperties.set(key, value); },
    getDynamicProperty(key) { return fakeProperties.get(key); },
  };
  const fixtures = createProductionVenues({
    world: fakeWorld,
    system: fakeSystem,
  });
  const selectedFloor = clockVenue.drop_areas[0];
  const openState = clockGame.view({
    ...ready, dropRemaining: CLOCK_FLOOR_OPEN_TICKS, floorOpen: true,
  });
  fixtures.update("clock", { players: ["clock-a", "clock-b"], state: openState });
  for (const area of clockVenue.drop_areas) {
    const expected = area.sector === 0 ? "minecraft:air" : area.floor;
    for (const cell of area.cells) {
      assert.equal(blocks.get(blockKey(cell.x, cell.y, cell.z)).typeId, expected,
        `clock sector ${area.sector} must ${area.sector === 0 ? "open" : "stay closed"}`);
    }
  }
  const landingCell = selectedFloor.cells[0];
  landingPlayers = [{
    id: "clock-a", dimension: { id: "minecraft:overworld" },
    location: { x: landingCell.x + 0.5, y: landingCell.y - 1, z: landingCell.z + 0.5 },
  }];
  assert.deepEqual(fixtures.pollClockLanding({
    players: ["clock-a", "clock-b"], state: openState,
  }), [0]);
  landingPlayers.push({
    id: "clock-b", dimension: { id: "minecraft:overworld" },
    location: { x: landingCell.x + 0.5, y: landingCell.y - 1, z: landingCell.z + 0.5 },
  });
  assert.deepEqual(fixtures.pollClockLanding({
    players: ["clock-a", "clock-b"], state: openState,
  }), [0, 1], "simultaneous water landings must both be observed");
  landingPlayers = [];
  fixtures.update("clock", {
    players: ["clock-a", "clock-b"],
    state: { ...clockGame.view(ready), phase: "setup", floorOpen: false },
  });
  for (const area of clockVenue.drop_areas) {
    for (const cell of area.cells) {
      assert.equal(blocks.get(blockKey(cell.x, cell.y, cell.z)).typeId, area.floor,
        "the next turn or a restored setup state must close every sector floor");
    }
  }

  const makeClockPlayer = id => {
    const effects = new Map();
    return {
      id, name: id, dimension: { id: "minecraft:overworld" },
      location: { x: clockGeometry.center_x, y: clockGeometry.center_y, z: clockGeometry.center_z },
      effects, commands: [], damage: [], sounds: [],
      addEffect(identifier, duration, options) { effects.set(identifier, { duration, options }); },
      removeEffect(identifier) { effects.delete(identifier); },
      runCommand(command) { this.commands.push(command); },
      applyDamage(amount, options) { this.damage.push({ amount, options }); },
      playSound(identifier) { this.sounds.push(identifier); },
    };
  };
  const clockA = makeClockPlayer("clock-a");
  const clockB = makeClockPlayer("clock-b");
  landingPlayers = [clockA, clockB];
  const clockToken = "clock-needle-token";
  fixtures.update("clock", {
    token: clockToken, players: ["clock-a", "clock-b"], state: clockGame.view(createClock()),
  });
  for (const player of [clockA, clockB]) {
    assert.deepEqual(player.effects.get("weakness"), {
      duration: 1000000, options: { amplifier: 9, showParticles: true },
    }, "clock start.mcfunction must grant Weakness infinite level 10");
  }

  const sweepState = clockGame.view({
    ...advanceClock(createClock(), 0, "time_confirm"),
    moveRemaining: (CLOCK_NEEDLE_START_TICKS + CLOCK_NEEDLE_END_TICKS) / 2,
  });
  const sweepAngles = clockNeedleAngles(sweepState);
  clockA.location = { ...pointer(sweepAngles.minute), y: clockGeometry.center_y };
  fakeSystem.currentTick += 1;
  fixtures.update("clock", { token: clockToken, players: ["clock-a", "clock-b"], state: sweepState });
  assert.deepEqual(clockA.effects.get("slowness"), {
    duration: 10, options: { amplifier: 10, showParticles: true },
  });
  assert.deepEqual(clockA.effects.get("darkness"), {
    duration: 10, options: { amplifier: 10, showParticles: false },
  });
  assert.ok(clockA.commands.includes("attribute @s minecraft:jump_strength base set 0"));
  assert.equal(clockA.damage.at(-1).amount, 0.1);

  fakeSystem.currentTick += 1;
  fixtures.update("clock", { token: clockToken, players: ["clock-a", "clock-b"], state: sweepState });
  assert.equal(clockA.damage.length, 2,
    "a participant still under the sweeping hand must be hit again on a later source tick");

  clockA.location = { x: clockGeometry.center_x, y: clockGeometry.center_y, z: clockGeometry.center_z };
  for (let tick = 0; tick < 200; tick++) {
    fakeSystem.currentTick += 1;
    fixtures.update("clock", { token: clockToken, players: ["clock-a", "clock-b"], state: sweepState });
  }
  assert.equal(clockA.commands.filter(command =>
    command === "attribute @s minecraft:jump_strength base set 0.4").length, 1,
  "the source sting recovery must restore jump strength exactly once after 10 seconds");

  const persistedDuringMatch = JSON.parse(fakeProperties.get("junket:clock:needle_recovery"));
  assert.deepEqual(persistedDuringMatch.find(entry => entry.playerId === "clock-a"), {
    playerId: "clock-a", remaining: 0, affected: false, cleanup: false,
  }, "the source 10-second recovery state must be persisted after the hit");

  landingPlayers = [clockA];
  fixtures.update("clock", {
    token: clockToken, players: ["clock-a", "clock-b"],
    state: { ...sweepState, phase: "finished", winner: 0, reason: "landed" },
  });
  assert.equal(clockA.effects.has("weakness"), false,
    "clock end.mcfunction must clear Weakness for an online participant");
  const persistedAfterFinish = JSON.parse(fakeProperties.get("junket:clock:needle_recovery"));
  assert.deepEqual(persistedAfterFinish, [{
    playerId: "clock-b", remaining: 0, affected: false, cleanup: true,
  }], "cleanup for an offline participant must survive the finished state");

  clockB.effects.set("weakness", {
    duration: 1000000, options: { amplifier: 9, showParticles: true },
  });
  landingPlayers = [clockA, clockB];
  const restoredClockVenues = createProductionVenues({ world: fakeWorld, system: fakeSystem });
  restoredClockVenues.resume("clock-b");
  assert.equal(clockB.effects.has("weakness"), false,
    "a participant reconnecting after a BDS restart must receive the persisted end cleanup");
  assert.deepEqual(JSON.parse(fakeProperties.get("junket:clock:needle_recovery")), [],
    "the persisted cleanup must be consumed only after the player reconnects");
  landingPlayers = [];

  const walkerVenue = VENUE_CONFIG.VENUES.find(venue => venue.id === "walker");
  for (const cell of walkerVenue.walker.cells) {
    for (let dx = -2; dx <= 2; dx++) {
      for (let dz = -2; dz <= 2; dz++) {
        const key = blockKey(cell.x + dx, walkerVenue.walker.platform_y, cell.z + dz);
        if (!blocks.has(key)) blocks.set(key, block(`minecraft:${cell.color}_concrete`));
      }
    }
  }
  const walkerPlayer = (id, floor) => {
    const cell = walkerVenue.walker.cells.find(value => value.floor === floor);
    return {
      id, dimension: { id: "minecraft:overworld" },
      location: { x: cell.x + 0.5, y: walkerVenue.walker.platform_y + 1, z: cell.z + 0.5 },
      teleport(location, options) {
        this.location = { ...location };
        this.dimension = options.dimension;
        this.rotation = options.rotation;
      },
    };
  };
  const walkerA = walkerPlayer("walker-a", 21);
  const walkerB = walkerPlayer("walker-b", 17);
  assert.equal(walkerFloorDistance(21, 25), 1,
    "overlapping source footprints are one step apart");
  assert.equal(walkerFloorDistance(21, 20), 2,
    "tiles with six-block-separated centers are not adjacent");
  assert.throws(() => advanceWalker(createWalker(), 0, "position", 20), /隣接する床/,
    "the source cannot step directly from 21 to the non-touching floor 20");
  assert.equal(advanceWalker(createWalker(), 0, "position", 25).positions[0], 25,
    "the source can step from 21 to the overlapping floor 25");
  assert.equal(advanceWalker(
    advanceWalker(createWalker(), 0, "position", 25), 0, "position", 20).positions[0], 20,
  "the source can continue from floor 25 to floor 20");
  landingPlayers = [walkerA, walkerB];
  assert.deepEqual(fixtures.pollWalker({
    players: ["walker-a", "walker-b"],
    state: { ...createWalker(), positions: [25, 17], deadFloors: [21] },
  }).positions, [25, 17],
  "a position marker over a collapsed floor must not count as a move");
  walkerA.location = { ...walkerPlayer("walker-a", 21).location };
  assert.deepEqual(fixtures.pollWalker({
    players: ["walker-a", "walker-b"], state: createWalker(),
  }), { positions: [21, 17], water: [] });
  walkerA.location = { ...walkerPlayer("walker-a", 25).location };
  assert.deepEqual(fixtures.pollWalker({
    players: ["walker-a", "walker-b"], state: createWalker(),
  }).positions, [25, 17], "the active walker can step to an adjacent source floor");
  walkerA.location = { ...walkerPlayer("walker-a", 21).location };
  const walker20 = walkerVenue.walker.cells.find(value => value.floor === 20);
  walkerB.location = { x: walker20.x + 0.5, y: walkerVenue.walker.platform_y + 1, z: walker20.z + 0.5 };
  assert.deepEqual(fixtures.pollWalker({
    players: ["walker-a", "walker-b"], state: createWalker(),
  }).positions, [21, 17], "the inactive player is returned to their own floor");
  assert.equal(walkerB.rotation.y, 90, "2P keeps the source-facing rotation when bound");
  let walkerDodge = advanceWalker(createWalker(), 0, "position", 25);
  walkerDodge = advanceWalker(walkerDodge, 0, "stop");
  walkerDodge = advanceWalker(walkerDodge, 1, "bomb", 20);
  fixtures.update("walker", {
    players: ["walker-a", "walker-b"],
    state: walkerDodge,
  });
  const walker20Center = walkerVenue.walker.cells.find(value => value.floor === 20);
  const walker21 = walkerVenue.walker.cells.find(value => value.floor === 21);
  assert.equal(blocks.get(blockKey(walker20Center.x, walkerVenue.walker.platform_y, walker20Center.z)).typeId,
    `minecraft:${walker20Center.color}_concrete`,
    "a bomb must not replace the source floor with a redstone block");
  const walkerBombs = fixtureEntities.filter(entity => !entity.removed &&
    entity.typeId === "junket:walker_bomb" && entity.hasTag("junket:walker:bomb"));
  assert.equal(walkerBombs.length, 1, "a visible bomb must be a dedicated floor prop entity");
  assert.deepEqual(walkerBombs[0].location, {
    x: walker20Center.x + 0.5, y: walkerVenue.walker.platform_y + 1.04,
    z: walker20Center.z + 0.5,
  });
  const walkerLabels = fixtureEntities.filter(entity => !entity.removed &&
    entity.typeId === "junket:walker_label" && entity.hasTag("junket:walker:floor_label"));
  assert.equal(walkerLabels.length, 37, "all 37 source floor labels must exist");
  assert.equal(walkerLabels.find(entity =>
    entity.hasTag("junket:walker:floor_label:21"))?.getProperty("junket:frame"), 16,
  "floor 21 must switch to the source dead 'ほ' label");
  assert.equal(blocks.get(blockKey(walker21.x, walkerVenue.walker.platform_y, walker21.z)).typeId,
    "minecraft:air", "a collapsed source floor leaves an actual hole");
  walkerA.location = {
    x: walker21.x + 0.5, y: walkerVenue.walker.water_top + 1, z: walker21.z + 0.5,
  };
  assert.deepEqual(fixtures.pollWalker({
    players: ["walker-a", "walker-b"],
    state: { ...createWalker(), phase: "fuse", deadFloors: [21] },
  }).water, [0], "falling below the suspended floor is detected as water loss");
  landingPlayers = [];
}

{
  const walkerVenue = VENUE_CONFIG.VENUES.find(venue => venue.id === "walker");
  const walkerCell = floor => walkerVenue.walker.cells.find(cell => cell.floor === floor);
  const makeWalkerWorld = () => {
    const blocks = new Map();
    const entities = [];
    for (const cell of walkerVenue.walker.cells) {
      for (let dx = -2; dx <= 2; dx++) {
        for (let dz = -2; dz <= 2; dz++) {
          const key = `${cell.x + dx},${walkerVenue.walker.platform_y},${cell.z + dz}`;
          blocks.set(key, {
            typeId: `minecraft:${cell.color}_concrete`,
            setType(next) { this.typeId = next; },
          });
        }
      }
    }
    const dimension = {
      id: "minecraft:overworld",
      getBlock({ x, y, z }) { return blocks.get(`${x},${y},${z}`); },
      getEntities({ tags = [], location, maxDistance } = {}) {
        return entities.filter(entity => !entity.removed &&
          tags.every(tag => entity.hasTag(tag)) &&
          (!location || Math.hypot(entity.location.x - location.x,
            entity.location.z - location.z) <= maxDistance));
      },
      spawnEntity(typeId, location) {
        const tags = new Set(), properties = new Map(), dynamic = new Map();
        const entity = {
          typeId, location: { ...location }, nameTag: "", removed: false,
          getComponent: name => name === "minecraft:health" ? { currentValue: 20, effectiveMax: 20 } : undefined,
          hasTag(tag) { return tags.has(tag); },
          addTag(tag) { tags.add(tag); },
          getTags() { return [...tags]; },
          setProperty(name, value) { properties.set(name, value); },
          getProperty(name) { return properties.get(name); },
          setDynamicProperty(name, value) { dynamic.set(name, value); },
          getDynamicProperty(name) { return dynamic.get(name); },
          remove() { this.removed = true; },
        };
        entities.push(entity);
        return entity;
      },
    };
    const world = {
      value: null,
      getDynamicProperty() { return this.value; },
      setDynamicProperty(_name, value) { this.value = value; },
      getDimension() { return dimension; },
      getAllPlayers() { return []; },
    };
    return { blocks, entities, world };
  };
  const restartVenue = async tag => {
    const module = await import(
      `../minecraft/behavior_packs/ichiyon_junket_bp/scripts/production_venue.js?walker-restart=${tag}`);
    const scene = makeWalkerWorld();
    return {
      ...scene,
      fixtures: module.createProductionVenues({
        world: scene.world,
        system: { runTimeout() {}, runInterval() {}, clearRun() {} },
      }),
    };
  };
  const centerType = (blocks, floor) => {
    const cell = walkerCell(floor);
    return blocks.get(`${cell.x},${walkerVenue.walker.platform_y},${cell.z}`).typeId;
  };

  const saved = makeWalkerWorld();
  const manager = new GameManager([walkerGame], { world: saved.world });
  manager.join("walker-restart", "walker", "walker-a");
  manager.join("walker-restart", "walker", "walker-b");
  if (!manager.view("walker-restart").state) manager.start("walker-restart");
  let view = manager.view("walker-restart");
  view = manager.act("walker-a", view.token, "position", 25);
  view = manager.act("walker-a", view.token, "stop");
  view = manager.act("walker-b", view.token, "bomb", 20);
  assert.equal(view.state.phase, "dodge");
  manager.persist();

  const dodgeRestored = new GameManager([walkerGame], { world: saved.world });
  assert.deepEqual(dodgeRestored.restore(saved.world.value), { restored: 1, skipped: 0 });
  view = dodgeRestored.view("walker-restart");
  assert.deepEqual(view.state.deadFloors, [21]);
  assert.deepEqual(view.state.bombPositions, [20]);
  const dodgeScene = await restartVenue("dodge");
  dodgeScene.fixtures.update("walker", view);
  assert.equal(centerType(dodgeScene.blocks, 21), "minecraft:air",
    "a restored collapsed floor must be a physical hole after restart");
  assert.equal(centerType(dodgeScene.blocks, 20),
    `minecraft:${walkerCell(20).color}_concrete`,
    "a restored bomb must not overwrite the source floor block");
  assert.equal(dodgeScene.entities.filter(entity => !entity.removed &&
    entity.typeId === "junket:walker_bomb" && entity.hasTag("junket:walker:bomb:20")).length,
  1, "a restored visible bomb must be recreated as its dedicated entity");

  view = dodgeRestored.act("walker-a", view.token, "position", 20);
  assert.equal(view.state.phase, "dodge");
  assert.equal(view.state.dodgeMoved, true);
  assert.ok(!view.state.deadFloors.includes(25),
    "the dodge origin stays intact until the source floor/move-2 detonation step");
  for (let tick = 0; tick < WALKER_DODGE_TICKS; tick++) {
    view = dodgeRestored.systemAct("walker-restart", "tick");
  }
  assert.equal(view.state.phase, "fuse");
  assert.equal(view.state.fuseRemaining, WALKER_FUSE_TICKS,
    "the source fuse starts only after the 100-tick dodge window");
  assert.ok(!view.state.deadFloors.includes(25),
    "the dodge origin must remain walkable throughout the fuse countdown");
  for (let tick = 0; tick < WALKER_FUSE_TICKS; tick++) {
    view = dodgeRestored.systemAct("walker-restart", "tick");
  }
  assert.equal(view.state.phase, "aftermath");
  assert.ok(view.state.deadFloors.includes(25),
    "the source detonation step erases the recorded dodge origin");
  assert.deepEqual(view.state.water, [true, false]);
  dodgeRestored.persist();

  const aftermathRestored = new GameManager([walkerGame], { world: saved.world });
  assert.deepEqual(aftermathRestored.restore(saved.world.value), { restored: 1, skipped: 0 });
  const aftermathView = aftermathRestored.view("walker-restart");
  assert.deepEqual(aftermathView.state.water, [true, false],
    "the water result must survive a restart");
  const aftermathScene = await restartVenue("aftermath");
  aftermathScene.fixtures.update("walker", aftermathView);
  assert.equal(centerType(aftermathScene.blocks, 21), "minecraft:air");
  assert.equal(centerType(aftermathScene.blocks, 20), "minecraft:air");
  assert.equal(centerType(aftermathScene.blocks, 25), "minecraft:air");

  const floor1 = walkerCell(1);
  const floor3 = walkerCell(3);
  const overlap = { x: floor3.x - 2, z: floor1.z - 1 };
  const typeAt = (blocks, x, z) =>
    blocks.get(`${x},${walkerVenue.walker.platform_y},${z}`).typeId;
  const footprintScene = await restartVenue("source-footprint");
  footprintScene.fixtures.update("walker", {
    players: ["walker-a", "walker-b"],
    state: { phase: "fuse", deadFloors: [1], bombVisible: false, bombPositions: [] },
  });
  assert.equal(centerType(footprintScene.blocks, 1), "minecraft:air");
  assert.equal(typeAt(footprintScene.blocks, floor1.x - 2, floor1.z + 2), "minecraft:air",
    "the source removes the whole departed 5x5 tile");
  assert.equal(typeAt(footprintScene.blocks, overlap.x, overlap.z), "minecraft:orange_concrete",
    "a later different-colored tile keeps the cells it overwrote");
  footprintScene.fixtures.update("walker", {
    players: ["walker-a", "walker-b"],
    state: { phase: "fuse", deadFloors: [3], bombVisible: false, bombPositions: [] },
  });
  assert.equal(centerType(footprintScene.blocks, 3), "minecraft:air");
  assert.equal(centerType(footprintScene.blocks, 1), "minecraft:red_concrete",
    "restoring the source layout must also repair a previously collapsed tile");
  assert.equal(typeAt(footprintScene.blocks, overlap.x, overlap.z), "minecraft:air",
    "the later tile removes its own cells including the overlap");

  let roundBoundary = {
    ...createWalker(), positions: [21, 37], roundStart: [21, 37],
  };
  for (const floor of [25, 28, 31, 37]) {
    roundBoundary = advanceWalker(roundBoundary, 0, "position", floor);
  }
  roundBoundary = advanceWalker(roundBoundary, 0, "stop");
  assert.equal(roundBoundary.phase, "walk");
  assert.equal(roundBoundary.round, 2);
  assert.deepEqual(roundBoundary.deadFloors, [21, 25, 28, 31]);
  const roundScene = await restartVenue("round-boundary");
  roundScene.fixtures.update("walker", {
    players: ["walker-a", "walker-b"], state: roundBoundary,
  });
  for (const floor of [21, 25, 28, 31]) {
    assert.equal(centerType(roundScene.blocks, floor), "minecraft:air",
      `collapsed floor ${floor} must remain a hole after the next round starts`);
  }
  const roundRestart = await restartVenue("round-boundary-restart");
  roundRestart.fixtures.update("walker", {
    players: ["walker-a", "walker-b"], state: roundBoundary,
  });
  for (const floor of [21, 25, 28, 31]) {
    assert.equal(centerType(roundRestart.blocks, floor), "minecraft:air",
      `runtime restart must not regenerate collapsed floor ${floor} at a round boundary`);
  }
}

{
  let state = createWalker();
  assert.deepEqual(state.positions, [21, 17]);
  state = advanceWalker(state, 0, "position", 25);
  assert.equal(state.movedBomb, 1);
  assert.deepEqual(state.deadFloors, [21]);
  state = advanceWalker(state, 0, "stop");
  assert.equal(state.phase, "bomb_setup");
  assert.equal(state.actor, 1);
  assert.equal(state.bombCursor, 1);
  assert.deepEqual(walkerGame.view(state).bombPositions, [],
    "breaker bomb placements stay hidden from the walker");
  state = advanceWalker(state, 1, "bomb", 20);
  assert.deepEqual(state.bombs, [20]);
  assert.equal(state.phase, "dodge");
  assert.equal(state.dodgeRemaining, WALKER_DODGE_TICKS);
  assert.deepEqual(walkerGame.view(state).bombPositions, [20]);
  state = advanceWalker(state, 0, "position", 20);
  assert.equal(state.phase, "dodge");
  assert.equal(state.dodgeMoved, true);
  assert.ok(!state.deadFloors.includes(25),
    "the dodge origin is recorded but not erased before the source fuse");
  assert.throws(() => advanceWalker(state, 0, "position", 16), /一度だけ/,
    "the walker gets exactly one dodge move");
  for (let index = 0; index < WALKER_DODGE_TICKS; index++) state = walkerSystem(state, "tick");
  assert.equal(state.phase, "fuse");
  assert.equal(state.fuseRemaining, WALKER_FUSE_TICKS);
  for (let index = 0; index < WALKER_FUSE_TICKS; index++) state = walkerSystem(state, "tick");
  assert.equal(state.phase, "aftermath");
  assert.ok(state.deadFloors.includes(25),
    "the source fuse erases the recorded dodge origin at detonation");
  assert.deepEqual(state.water, [true, false]);
  assert.equal(state.winner, 1);
  for (let index = 0; index < WALKER_AFTERMATH_TICKS; index++) state = walkerSystem(state, "tick");
  assert.equal(state.phase, "finished");
}

{
  let state = createWalker();
  state = advanceWalker(state, 0, "position", 25);
  state = advanceWalker(state, 0, "stop");
  state = advanceWalker(state, 1, "bomb", 20);
  assert.equal(state.phase, "dodge");
  assert.equal(state.dodgeOrigin, 25);
  assert.equal(state.dodgeMoved, false);
  const dodged = advanceWalker(state, 0, "position", 20);
  assert.equal(dodged.phase, "dodge");
  assert.equal(dodged.dodgeMoved, true);
  assert.ok(!dodged.deadFloors.includes(25),
    "the source records the dodge origin but waits for detonation to erase it");
  assert.throws(() => advanceWalker(dodged, 0, "position", 16), /一度だけ/,
    "the source binds the walker after the single dodge move");
  let exploded = dodged;
  for (let index = 0; index < WALKER_DODGE_TICKS; index++) exploded = walkerSystem(exploded, "tick");
  assert.equal(exploded.phase, "fuse");
  assert.ok(!exploded.deadFloors.includes(25),
    "the source fuse delay keeps the dodge origin intact");
  for (let index = 0; index < WALKER_FUSE_TICKS; index++) exploded = walkerSystem(exploded, "tick");
  assert.equal(exploded.phase, "aftermath");
  assert.ok(exploded.deadFloors.includes(25),
    "the dodge origin becomes a physical hole at detonation");
  assert.deepEqual(exploded.water, [true, false]);

  let still = createWalker();
  still = advanceWalker(still, 0, "position", 25);
  still = advanceWalker(still, 0, "stop");
  still = advanceWalker(still, 1, "bomb", 20);
  for (let index = 0; index < WALKER_DODGE_TICKS; index++) still = walkerSystem(still, "tick");
  assert.equal(still.phase, "fuse");
  for (let index = 0; index < WALKER_FUSE_TICKS; index++) still = walkerSystem(still, "tick");
  assert.ok(!still.deadFloors.includes(25),
    "a walker who never dodged must not lose their origin at detonation");
}
{
  let state = createWalker();
  for (const floor of [25, 28, 31, 34, 37]) state = advanceWalker(state, 0, "position", floor);
  assert.equal(state.phase, "bomb_setup",
    "the fifth move grants walker-stop and automatically ends the walking phase");
  assert.equal(state.actor, 1);
  assert.equal(state.movedBomb, 5);
  assert.throws(() => advanceWalker(state, 0, "position", 27), /移動できません/);
  assert.equal(state.bombQuota, 5);
  for (let index = 0; index < 5; index++) state = advanceWalker(state, 1, "bomb_confirm");
  assert.equal(state.phase, "dodge");
  assert.equal(state.bombs.length, 5);
}

{
  let state = createWalker();
  for (let index = 0; index < WALKER_WALK_TICKS; index++) state = walkerSystem(state, "tick");
  assert.equal(state.phase, "aftermath", "a no-move walk timeout drops the walker");
  assert.deepEqual(state.water, [true, false]);
}

{
  let state = createWalker();
  state = advanceWalker(state, 0, "position", 25);
  for (let index = 0; index < WALKER_WALK_TICKS; index++) state = walkerSystem(state, "tick");
  assert.equal(state.phase, "aftermath",
    "moving without walker-stop still times out and drops the walker");
  assert.deepEqual(state.water, [true, false]);
  assert.equal(state.winner, 1);
}

{
  let state = createWalker();
  state = advanceWalker(state, 0, "position", 25);
  state = advanceWalker(state, 0, "stop");
  assert.equal(state.setupRemaining, WALKER_SETUP_TICKS,
    "the breaker gets the source's full 6000-tick bomb selection window");
  for (let index = 0; index < WALKER_SETUP_TICKS - 1; index++) {
    state = walkerSystem(state, "tick");
  }
  assert.equal(state.phase, "bomb_setup");
  state = walkerSystem(state, "tick");
  assert.equal(state.phase, "aftermath");
  assert.deepEqual(state.water, [false, true],
    "a breaker setup timeout is a loss for the breaker, not an auto-placement");
  assert.equal(state.winner, 0);
  assert.equal(state.reason, "setup_timeout");
}

{
  let state = createWalker();
  state = { ...state, positions: [21, 37], roundStart: [21, 37] };
  for (const floor of [25, 28, 31, 37]) state = advanceWalker(state, 0, "position", floor);
  state = advanceWalker(state, 0, "stop");
  assert.equal(state.phase, "walk",
    "an occupied shared floor skips the breaker turn exactly as in the source");
  assert.equal(state.round, 2);
  assert.equal(state.actor, 1);
  assert.deepEqual(state.bombs, []);
  assert.deepEqual(state.deadFloors, [21, 25, 28, 31],
    "source floor/reset only restores live tiles; collapsed tiles stay collapsed");
  assert.equal(state.positions[0], 37);
  assert.equal(state.positions[1], 37);
}

{
  let state = createWalker();
  for (const floor of [25, 28, 31, 34, 37]) state = advanceWalker(state, 0, "position", floor);
  state = advanceWalker(state, 1, "bomb_next");
  state = advanceWalker(state, 1, "bomb", 1);
  state = advanceWalker(state, 1, "bomb_next");
  state = advanceWalker(state, 1, "bomb", 2);
  const restored = JSON.parse(JSON.stringify(state));
  assert.deepEqual(restored.bombs, [1, 2]);
  assert.equal(restored.phase, "bomb_setup");
  assert.equal(restored.setupRemaining, WALKER_SETUP_TICKS,
    "multi-bomb setup and its remaining timer survive a restart round-trip");
}

{
  let state = createGarden();
  assert.equal(state.valve, 1, "the source starts with the valve on the 2P side");
  assert.equal(state.device, 5);
  assert.equal(state.deviceNow, 5);
  assert.deepEqual(state.loads, [0, 0]);
  assert.deepEqual(state.changeAvailable, [true, true]);
  state = advanceGarden(state, 0, "choose", "switch");
  assert.deepEqual(gardenGame.view(state).choices, [null, null],
    "garden actions stay hidden until both players choose");

  let simultaneous = createGarden();
  simultaneous = advanceGarden(simultaneous, 1, "choose", "double");
  assert.equal(simultaneous.phase, "choose");
  assert.equal(simultaneous.actor, 0, "the other player is still selecting");
  simultaneous = advanceGarden(simultaneous, 0, "choose", "open");
  assert.equal(simultaneous.round, 2);
  assert.equal(simultaneous.device, 5);
  assert.equal(simultaneous.deviceNow, 5);
  assert.equal(simultaneous.valve, 1);
  assert.deepEqual(simultaneous.loads, [0, 10],
    "one double makes 10 gems and releases them toward the 2P side");

  let switched = createGarden();
  switched = advanceGarden(switched, 0, "choose", "switch");
  switched = advanceGarden(switched, 1, "choose", "open");
  assert.equal(switched.valve, 0, "one switch changes the valve before release");
  assert.deepEqual(switched.loads, [5, 0]);

  let cooldown = createGarden();
  cooldown = advanceGarden(cooldown, 0, "choose", "switch");
  cooldown = advanceGarden(cooldown, 1, "choose", "switch");
  assert.equal(cooldown.valve, 1, "two switches leave the valve unchanged");
  assert.deepEqual(cooldown.changeAvailable, [false, false],
    "a used switch card is withheld for the following round");
  assert.throws(() => advanceGarden(cooldown, 0, "choose", "switch"), /翌ラウンド/);
  cooldown = advanceGarden(cooldown, 0, "choose", "double");
  cooldown = advanceGarden(cooldown, 1, "choose", "double");
  assert.deepEqual(cooldown.changeAvailable, [true, true],
    "the switch card returns after one withheld round");

  let doubled = createGarden();
  doubled = advanceGarden(doubled, 0, "choose", "double");
  doubled = advanceGarden(doubled, 1, "choose", "double");
  assert.equal(doubled.device, 20, "two doubles multiply the device by four");
  assert.equal(doubled.deviceNow, 20, "both doubles refill the physical device to the multiplied target before release");
  doubled = advanceGarden(doubled, 0, "choose", "open");
  doubled = advanceGarden(doubled, 1, "choose", "open");
  assert.deepEqual(doubled.loads, [0, 20]);
  assert.equal(doubled.device, 5);
  assert.equal(doubled.deviceNow, 5);

  let capped = { ...createGarden(), device: 1000, deviceNow: 1000 };
  capped = advanceGarden(capped, 0, "choose", "double");
  capped = advanceGarden(capped, 1, "choose", "double");
  assert.equal(capped.device, 1280, "the device display caps at 1280");
  assert.equal(capped.deviceNow, 1280, "the physical count must reach the capped target, not stop after one five-jewel tick");

  let sustained = createGarden();
  for (let round = 0; round < 20; round++) {
    sustained = advanceGarden(sustained, 0, "choose", "double");
    sustained = advanceGarden(sustained, 1, "choose", "double");
  }
  assert.equal(sustained.phase, "choose", "the source has no six-round limit");
  assert.equal(sustained.round, 21);
  assert.equal(sustained.reason, null);

  let holding = { ...createGarden(), loads: [100, 0] };
  for (let second = 0; second < 4; second++) {
    holding = gardenSystem(holding, "tick", 1);
    assert.equal(holding.phase, "choose");
  }
  holding = gardenSystem(holding, "tick", 1);
  assert.equal(holding.phase, "drop", "an exact-100 result drops the opponent before end");
  assert.equal(holding.winner, 0);
  assert.equal(holding.reason, "box_100");
  assert.equal(holding.loser, 1);
  assert.equal(holding.dropRemaining, 3,
    "the source waits one second before drop/* and forty more ticks before opening the floor");
  assert.throws(() => gardenSystem(holding, "land", 1), /まだ開いていません/);
  holding = gardenSystem(holding, "tick", 2);
  assert.equal(holding.phase, "drop");
  assert.equal(holding.dropRemaining, 1);
  assert.throws(() => gardenSystem(holding, "land", 1), /まだ開いていません/);
  holding = gardenSystem(holding, "tick", 1);
  assert.equal(holding.dropRemaining, 0);
  holding = gardenSystem(holding, "land", 1);
  assert.equal(holding.phase, "result");
  assert.equal(holding.dropRemaining, 5);
  holding = gardenSystem(holding, "tick", 4);
  assert.equal(holding.phase, "result");
  holding = gardenSystem(holding, "tick", 1);
  assert.equal(holding.phase, "finished");

  let cancel = { ...createGarden(), loads: [100, 0] };
  cancel = gardenSystem(cancel, "tick", 1);
  cancel = advanceGarden(cancel, 0, "pick");
  assert.equal(cancel.loads[0], 99);
  assert.deepEqual(cancel.pendingWin, [null, null]);
  cancel = gardenSystem(cancel, "tick", 10);
  assert.equal(cancel.phase, "choose", "picking below 100 cancels the pending win");

  let over = { ...createGarden(), loads: [101, 0] };
  over = gardenSystem(over, "tick", 10);
  assert.equal(over.phase, "choose", "only exactly 100 starts the win hold");

  let both = { ...createGarden(), loads: [100, 100] };
  both = gardenSystem(both, "tick", 5);
  assert.equal(both.phase, "drop");
  assert.equal(both.winner, 0,
    "the source schedules the 1P hundred-jewel check first on a tied tick");
  assert.equal(both.loser, 1);
  assert.equal(both.reason, "both_100");

  let timeoutBoth = gardenSystem(createGarden(), "timeout");
  assert.equal(timeoutBoth.phase, "drop");
  assert.equal(timeoutBoth.winner, null);
  assert.equal(timeoutBoth.reason, "timeout_both");
  assert.equal(timeoutBoth.dropRemaining, 3, "the physical floor stays closed for the source delay");
  assert.throws(() => advanceGarden(timeoutBoth, 0, "choose", "double"), /落下判定中/);
  assert.throws(() => gardenSystem(timeoutBoth, "land", "both"), /まだ開いていません/);
  timeoutBoth = gardenSystem(timeoutBoth, "tick", 5);
  assert.equal(timeoutBoth.phase, "drop",
    "a timeout countdown must not replace the landing verdict");
  assert.equal(timeoutBoth.dropRemaining, 0);
  timeoutBoth = gardenSystem(timeoutBoth, "land", "both");
  assert.equal(timeoutBoth.phase, "result");
  assert.equal(timeoutBoth.winner, 1,
    "player-1 is checked first, so a simultaneous landing makes player-1 lose");
  assert.equal(timeoutBoth.loser, 0);
  assert.equal(timeoutBoth.reason, "timeout_both");
  timeoutBoth = gardenSystem(timeoutBoth, "tick", 5);
  assert.equal(timeoutBoth.phase, "finished");

  let timeoutOne = advanceGarden(createGarden(), 0, "choose", "double");
  timeoutOne = gardenSystem(timeoutOne, "timeout");
  assert.equal(timeoutOne.phase, "drop");
  assert.equal(timeoutOne.loser, 1);
  assert.equal(timeoutOne.winner, 0);
  timeoutOne = gardenSystem(timeoutOne, "tick", 5);
  assert.equal(timeoutOne.phase, "drop");
  timeoutOne = gardenSystem(timeoutOne, "land", 1);
  assert.equal(timeoutOne.phase, "result");
  timeoutOne = gardenSystem(timeoutOne, "tick", 5);
  assert.equal(timeoutOne.phase, "finished");
  assert.equal(timeoutOne.winner, 0);
  assert.equal(timeoutOne.reason, "timeout");
}

{
  // The source places one trap per survivor (five here), allows a trap on an
  // occupied starting tile, and spends exactly one CPU guard in enemymode
  // order after the final trap.
  let state = { ...createRabbit(), enemyMode: 1, seed: 0x5a17 };
  assert.equal(state.trapQuota, 5);
  state = advanceRabbit(state, 1, "trap", 25);
  assert.equal(state.phase, "trap", "one trap is not enough for five survivors");
  const hidden = rabbitView(state);
  assert.deepEqual(hidden.traps, [], "traps stay hidden during placement");
  assert.equal(hidden.trapCount, null);
  assert.equal(hidden.last?.traps, undefined);
  assert.equal(hidden.last?.guardUsedBy, undefined);
  assert.ok(hidden.participants.every(participant => participant.guard === false));
  for (const tile of [1, 13, 37, 28]) state = advanceRabbit(state, 1, "trap", tile);
  assert.equal(state.phase, "move");
  assert.equal(state.traps.length, 5);
  assert.ok(state.traps.includes(25), "an occupied floor is a legal trap target");
  assert.ok(state.traps.includes(28), "the guard must protect the occupied destination floor");
  assert.equal(state.participants.find(participant => participant.id === "p1").guard, true);
  assert.equal(state.participants.find(participant => participant.id === "p6").haveGuard, false);
  assert.equal(state.participants.find(participant => participant.id === "p5").haveGuard, true);
  assert.equal(state.last.guardUsedBy, "p6", "enemymode 1 consumes 6,5,4,3");
  assert.equal(rabbitView(state).last?.guardUsedBy, undefined,
    "the guard consumer must remain hidden until the reveal");

  state = advanceRabbit(state, 0, "move", 28);
  assert.equal(state.phase, "travel");
  assert.equal(state.phaseRemaining, RABBIT_TRAVEL_TICKS);
  assert.equal(state.participants.find(participant => participant.id === "p1").alive, true,
    "moving must not eliminate anyone before physical landing");
  assert.deepEqual(rabbitView(state).traps, [],
    "traps remain hidden during the source movement interval");
  assert.equal(rabbitView(state).last?.guardUsedBy, undefined,
    "the guard result remains hidden until the source reveal interval");
  for (let tick = 0; tick < RABBIT_TRAVEL_TICKS - 1; tick++) state = rabbitSystem(state, "tick");
  assert.equal(state.phase, "travel");
  assert.equal(state.phaseRemaining, 1);
  state = rabbitSystem(state, "tick");
  assert.equal(state.phase, "advance");
  assert.equal(state.phaseRemaining, RABBIT_MOVE_TICKS);
  assert.deepEqual(rabbitView(state).traps, [],
    "traps remain hidden while the source advances the planned moves");
  for (let tick = 0; tick < RABBIT_MOVE_TICKS - 1; tick++) state = rabbitSystem(state, "tick");
  assert.equal(state.phase, "advance");
  assert.equal(state.phaseRemaining, 1);
  state = rabbitSystem(state, "tick");
  assert.equal(state.phase, "settle");
  assert.equal(state.phaseRemaining, RABBIT_SETTLE_TICKS);
  assert.deepEqual(rabbitView(state).traps, [],
    "traps remain hidden during the source's 20-tick settle wait");
  for (let tick = 0; tick < RABBIT_SETTLE_TICKS; tick++) state = rabbitSystem(state, "tick");
  assert.equal(state.phase, "reveal");
  assert.ok(rabbitView(state).traps.length === 5);
  for (let tick = 0; tick < RABBIT_REVEAL_TICKS; tick++) state = rabbitSystem(state, "tick");
  assert.equal(state.phase, "drop");
  assert.equal(state.phaseRemaining, RABBIT_DROP_TICKS);
  assert.equal(state.participants.find(participant => participant.id === "p1").alive, true);
  assert.deepEqual(state.guardedFloors, [28],
    "the human guard protects the whole floor the human moved onto");
  assert.ok(!state.dropFloors.includes(28));
  assert.ok(!state.falling.includes("p1"), "the guarded human is not a falling target");
  assert.deepEqual(state.falling, state.participants
    .filter(participant => participant.alive && state.dropFloors.includes(participant.position))
    .map(participant => participant.id),
  "only participants standing on unguarded trap floors are falling targets");

  const fallingFixture = {
    ...createRabbit(),
    phase: "reveal", phaseRemaining: 0,
    traps: [1], guardedFloors: [], dropFloors: [], falling: [],
    participants: createRabbit().participants.map(participant => ({
      ...participant,
      alive: participant.id === "p1" || participant.id === "p3",
      guard: false,
      position: participant.id === "p3" ? 1 : participant.position,
    })),
  };
  const revealed = rabbitSystem(fallingFixture, "tick");
  assert.deepEqual(revealed.falling, ["p3"],
    "an unguarded CPU standing on a trap floor remains a physical falling target");

  let landed = { ...state, phase: "drop", participants: state.participants.map(participant => ({
    ...participant, alive: true,
  })), traps: [28], dropFloors: [28], guardedFloors: [], falling: ["p1"], last: {} };
  landed = rabbitSystem(landed, "land", ["p1"]);
  assert.equal(landed.participants.find(participant => participant.id === "p1").alive, false,
    "only a reported physical landing removes the escaper");
  assert.equal(landed.round, 2);
  assert.equal(landed.phase, "trap");
  assert.equal(landed.participants.find(participant => participant.id === "p1").guard, false,
    "guards reset at the start of the next round");
  assert.equal(landed.participants.find(participant => participant.id === "p6").haveGuard, false,
    "consumed guards stay consumed across rounds");

  let mode2 = { ...createRabbit(), enemyMode: 2 };
  for (const tile of [25, 1, 13, 37, 49]) mode2 = advanceRabbit(mode2, 1, "trap", tile);
  assert.equal(mode2.last.guardUsedBy, "p3", "enemymode 2 consumes 3,4,5,6");
  assert.equal(mode2.participants.find(participant => participant.id === "p3").haveGuard, false);
}

{
  let duplicate = { ...createRabbit(), trapQuota: 5 };
  duplicate = advanceRabbit(duplicate, 1, "trap", 5);
  assert.throws(() => advanceRabbit(duplicate, 1, "trap", 5), /既に罠/,
    "the source trap cursor skips a floor that already carries a trap");
  duplicate = advanceRabbit(duplicate, 1, "trap", 13);
  duplicate = advanceRabbit(duplicate, 1, "trap", 37);
  duplicate = advanceRabbit(duplicate, 1, "trap", 49);
  duplicate = advanceRabbit(duplicate, 1, "trap", 6);
  assert.equal(duplicate.phase, "move");
  assert.deepEqual(duplicate.traps, [5, 13, 37, 49, 6]);

  const cpuState = {
    ...createRabbit(),
    participants: createRabbit().participants.map(participant =>
      participant.id === "p3" ? { ...participant, alive: true, position: 1, haveGuard: false }
        : { ...participant, alive: false }),
  };
  const choices = new Set();
  for (let seed = 0; seed < 32; seed++) {
    const choice = rabbitChooseCpuMove({ ...cpuState, seed }, cpuState.participants[1]);
    choices.add(choice.value);
  }
  assert.deepEqual([...choices].sort((a, b) => a - b), [3, 4],
    "an unguarded CPU at tile 1 moves to 3/4; increasing tile numbers run west toward the source goals");

  const edgeState = {
    ...cpuState,
    participants: cpuState.participants.map(participant =>
      participant.id === "p3" ? { ...participant, position: 10 } : participant),
  };
  for (let seed = 0; seed < 32; seed++) {
    const choice = rabbitChooseCpuMove({ ...edgeState, seed }, edgeState.participants[1]);
    assert.equal(choice.value, 12,
      "the source's ~-6/~-9 targets resolve west to goal-floor 12, not east");
  }
}

{
  let goal = { ...createRabbit(), phase: "drop", traps: [], dropFloors: [],
    guardedFloors: [], falling: [], last: {},
    participants: createRabbit().participants.map(participant =>
      participant.id === "p1" ? { ...participant, position: 24 } : participant) };
  goal = rabbitSystem(goal, "land", []);
  assert.equal(goal.phase, "result");
  assert.equal(goal.winnerTeam, 0, "the escape side wins after a survivor reaches a goal tile");
  assert.equal(goal.reason, "goal");
  assert.equal(goal.phaseRemaining, RABBIT_RESULT_TICKS);
  assert.equal(goal.oniFloorOpen, false);
  for (let tick = 0; tick < RABBIT_RESULT_TICKS - RABBIT_RESULT_OPEN_TICKS; tick++) {
    goal = rabbitSystem(goal, "tick");
  }
  assert.equal(goal.phase, "result");
  assert.equal(goal.oniFloorOpen, true,
    "goal opens floor 61 twenty ticks after the result announcement");
  assert.equal(goal.allFloorsOpen, false);
  for (let tick = 0; tick < RABBIT_RESULT_OPEN_TICKS - 1; tick++) {
    goal = rabbitSystem(goal, "tick");
  }
  assert.equal(goal.phase, "result");
  goal = rabbitSystem(goal, "tick");
  assert.equal(goal.phase, "finished", "the goal result ends five seconds after floor 61 opens");
}

{
  let dead = { ...createRabbit(), phase: "drop", traps: [1, 13, 25, 37, 49],
    dropFloors: [1, 13, 25, 37, 49], guardedFloors: [], falling: [], last: {} };
  dead = rabbitSystem(dead, "land", dead.participants.map(participant => participant.id));
  assert.equal(dead.phase, "result");
  assert.equal(dead.winnerTeam, 1, "the oni wins when every escaper lands in water");
  assert.equal(dead.reason, "全滅");
  assert.equal(dead.phaseRemaining, RABBIT_WIPE_RESULT_TICKS,
    "wincheck's wipe result lasts five seconds, not the timed result's six");
  for (let tick = 0; tick < RABBIT_WIPE_RESULT_TICKS - 1; tick++) {
    dead = rabbitSystem(dead, "tick");
  }
  assert.equal(dead.phase, "result");
  assert.equal(dead.oniFloorOpen, false);
  assert.equal(dead.allFloorsOpen, false,
    "wincheck's five-second wipe ending has no floor 61 or all-floor opening animation");
  dead = rabbitSystem(dead, "tick");
  assert.equal(dead.phase, "finished");
}

{
  const trapTimeout = rabbitSystem({ ...createRabbit(), phase: "trap" }, "timeout");
  assert.equal(trapTimeout.phase, "result");
  assert.equal(trapTimeout.winnerTeam, 0);
  assert.equal(trapTimeout.reason, "trap_timeout");
  let trapResult = trapTimeout;
  for (let tick = 0; tick < RABBIT_RESULT_TICKS - RABBIT_RESULT_OPEN_TICKS; tick++) {
    trapResult = rabbitSystem(trapResult, "tick");
  }
  assert.equal(trapResult.oniFloorOpen, true,
    "the oni's trap-placement timeout drops it through floor 61");
  assert.equal(trapResult.allFloorsOpen, false);

  let moveTimeout = rabbitSystem({ ...createRabbit(), phase: "move", actor: 0 }, "timeout");
  assert.equal(moveTimeout.phase, "result");
  assert.equal(moveTimeout.winnerTeam, 1);
  assert.equal(moveTimeout.reason, "move_timeout");
  for (let tick = 0; tick < RABBIT_RESULT_TICKS - RABBIT_RESULT_OPEN_TICKS; tick++) {
    moveTimeout = rabbitSystem(moveTimeout, "tick");
  }
  assert.equal(moveTimeout.allFloorsOpen, true,
    "the escaper-side timeout opens all sixty floors");
  assert.equal(moveTimeout.oniFloorOpen, false);
  for (let tick = 0; tick < RABBIT_RESULT_OPEN_TICKS; tick++) {
    moveTimeout = rabbitSystem(moveTimeout, "tick");
  }
  assert.equal(moveTimeout.phase, "finished");
}

{
  let state = { ...createRabbit(), enemyMode: 1 };
  state = advanceRabbit(state, 1, "trap", 5);
  const restored = JSON.parse(JSON.stringify(state));
  assert.deepEqual(restored.traps, [5]);
  assert.equal(restored.phase, "trap");
  assert.equal(restored.enemyMode, 1);
  const continued = advanceRabbit(restored, 1, "trap", 6);
  assert.deepEqual(continued.traps, [5, 6], "trap state survives a JSON restart round-trip");
}

{
  const hope = createHope(2);
  assert.deepEqual(physicalControlRequest("hope", hope, "floor_3"),
    { action: "floor", value: 3 });
  assert.deepEqual(physicalControlRequest("hope", hope, "floor_16"),
    { action: "floor", value: 16 });
  assert.equal(physicalControlRequest("hope", hope, "floor_17"), null);

  const walker = createWalker();
  assert.deepEqual(physicalControlRequest("walker", walker, "walker_stop_1"),
    { action: "stop" });
  assert.equal(physicalControlRequest("walker", walker, "bomb_confirm_1"), null);
  const bomb = { ...walker, phase: "bomb_setup" };
  assert.deepEqual(physicalControlRequest("walker", bomb, "bomb_prev_1"),
    { action: "bomb_prev" });
  assert.deepEqual(physicalControlRequest("walker", bomb, "bomb_next_2"),
    { action: "bomb_next" });
  assert.deepEqual(physicalControlRequest("walker", bomb, "bomb_confirm_2"),
    { action: "bomb_confirm" });
  assert.equal(physicalControlRequest("walker", bomb, "walker_stop_2"), null);

  const rabbitTrap = createRabbit();
  assert.deepEqual(physicalControlRequest("rabbit", rabbitTrap, "floor_26"),
    { action: "trap", value: 26 });
  const rabbitMove = { ...rabbitTrap, phase: "move" };
  assert.deepEqual(physicalControlRequest("rabbit", rabbitMove, "floor_26"),
    { action: "move", value: 26 });
  assert.equal(physicalControlRequest("rabbit", rabbitMove, "floor_61"), null);

  let clock = createClock();
  assert.deepEqual(physicalControlRequest("clock", clock, "clock_hour_plus"),
    { action: "hour_plus" });
  assert.deepEqual(physicalControlRequest("clock", clock, "clock_minute_minus"),
    { action: "minute_minus" });
  assert.equal(physicalControlRequest("clock", clock, "clock_sector_cw"), null,
    "sector controls are unavailable during time setup");
  assert.equal(physicalControlRequest("clock", clock, "clock_set_7"), null,
    "direct sector controls are not part of the source rules");
  assert.equal(physicalControlRequest("clock", clock, "hour_hand"), null);
  assert.equal(physicalControlRequest("clock", clock, "minute_hand"), null);
  clock = advanceClock(clock, 0, "time_confirm");
  assert.equal(physicalControlRequest("clock", clock, "clock_hour_plus"), null,
    "time setup controls are unavailable during sector selection");
  assert.deepEqual(physicalControlRequest("clock", clock, "clock_sector_cw"),
    { action: "sector_cw" });
  assert.deepEqual(physicalControlRequest("clock", clock, "clock_sector_ccw"),
    { action: "sector_ccw" });
  assert.deepEqual(physicalControlRequest("clock", clock, "clock_sector_confirm"),
    { action: "sector_confirm" });

  let grail = createGrail();
  grail = advanceGrail(grail, 0, "arrange", { trueIndex: 0 });
  grail = advanceGrail(grail, grail.actor, "arrange", { holyIndex: 0 });
  assert.deepEqual(physicalControlRequest("grail", grail, "grail_cup_2"),
    { action: "cup", value: 1 });
  assert.deepEqual(physicalControlRequest("auction", createAuction(), "auction_bid_1_4"),
    { action: "bid", value: 4 });
  assert.equal(physicalControlRequest("auction", createAuction(), "auction_bid_2_4"), null,
    "only the player whose seat is bidding may press that seat's cards");
  let auctionBid = advanceAuction(createAuction(), 0, "bid", 4);
  assert.deepEqual(physicalControlRequest("auction", auctionBid, "auction_bid_2_3"),
    { action: "bid", value: 3 });
  assert.equal(physicalControlRequest("auction", auctionBid, "auction_bid_1_3"), null,
    "a seat that already bid cannot act again");
  assert.equal(physicalControlRequest("auction", createAuction(), "auction_block"), null,
    "the decorative auction table must not masquerade as an executable bid control");
  assert.deepEqual(physicalControlRequest("garden", createGarden(), "double"),
    { action: "choose", value: "double" });
  assert.deepEqual(physicalControlRequest("garden", createGarden(), "pick_1"),
    { action: "pick" });
  assert.equal(physicalControlRequest("unknown", {}, "floor_1"), null);

  let ace = aceGame.create();
  let aceStep = advancePhysicalControl("ace", ace, "ace_table_10_up");
  assert.deepEqual(aceStep.progress.digits, [0, 1, 0]);
  aceStep = advancePhysicalControl("ace", ace, "ace_table_confirm", aceStep.progress);
  assert.deepEqual(aceStep.request, { action: "table_bet", value: 10 });
  ace = aceGame.advance(ace, 0, aceStep.request.action, aceStep.request.value);
  const roll = physicalControlRequest("ace", ace, "ace_tray_1");
  assert.equal(roll.action, "roll");
  assert.equal(roll.value.length, 5);
  assert.ok(roll.value.every(face => ["9", "10", "J", "Q", "K", "A"].includes(face)));
  assert.equal(physicalControlRequest("ace", ace, "ace_tray_2"), null,
    "only the active seat may use its dice tray");

  ace = aceGame.create();
  ace = aceGame.advance(ace, 0, "table_bet", 10);
  const aceDice = [
    ["A", "A", "A", "A", "A"], ["K", "K", "K", "K", "K"],
    ["Q", "Q", "Q", "Q", "Q"], ["J", "J", "J", "J", "J"],
  ];
  for (const dice of aceDice) ace = aceGame.advance(ace, ace.actor, "roll", dice);
  assert.equal(ace.phase, "betting");
  for (let turn = 0; turn < 4; turn++) {
    const seat = ace.actor;
    const call = physicalControlRequest("ace", ace, `ace_call_${seat + 1}`);
    assert.deepEqual(call, { action: "bet", value: { type: "call" } });
    ace = aceGame.advance(ace, seat, call.action, call.value);
    assert.equal(ace.phase, "reroll_choice");
    if (turn === 0) {
      const selected = advancePhysicalControl("ace", ace, `ace_die_${seat + 1}_1`);
      aceStep = advancePhysicalControl(
        "ace", ace, `ace_reroll_confirm_${seat + 1}`, selected.progress);
      assert.deepEqual(aceStep.request.value.indices, [0]);
      ace = aceGame.advance(ace, seat, aceStep.request.action, {
        ...aceStep.request.value,
        dice: [...ace.dice[seat]],
      });
    } else {
      aceStep = advancePhysicalControl("ace", ace, `ace_reroll_confirm_${seat + 1}`);
      assert.deepEqual(aceStep.request.value.indices, []);
      ace = aceGame.advance(ace, seat, aceStep.request.action, aceStep.request.value);
    }
    assert.equal(aceStep.request.action, "reroll_selected");
  }
  assert.equal(ace.phase, "between");
  assert.equal(ace.last.winner, 0);

  let raised = aceGame.create();
  raised = aceGame.advance(raised, 0, "table_bet", 10);
  for (const dice of aceDice) raised = aceGame.advance(raised, raised.actor, "roll", dice);
  for (let turn = 0; turn < 2; turn++) {
    const seat = raised.actor;
    const call = physicalControlRequest("ace", raised, `ace_call_${seat + 1}`);
    raised = aceGame.advance(raised, seat, call.action, call.value);
    aceStep = advancePhysicalControl("ace", raised, `ace_reroll_confirm_${seat + 1}`);
    raised = aceGame.advance(raised, seat, aceStep.request.action, aceStep.request.value);
  }
  aceStep = advancePhysicalControl("ace", raised, "ace_raise_10_up");
  aceStep = advancePhysicalControl("ace", raised, "ace_raise_confirm", aceStep.progress);
  assert.deepEqual(aceStep.request, { action: "bet", value: { type: "raise", amount: 10 } });
  raised = aceGame.advance(raised, raised.actor, aceStep.request.action, aceStep.request.value);
  assert.equal(raised.phase, "raise_response");
  assert.equal(raised.currentBet, 20);
  assert.deepEqual(raised.responders, [0, 1],
    "the source reopens the parent through the seat before the raiser");
  for (let turn = 0; turn < 2; turn++) {
    const seat = raised.actor;
    const response = physicalControlRequest("ace", raised, `ace_call_${seat + 1}`);
    raised = aceGame.advance(raised, seat, response.action, response.value);
  }
  assert.equal(raised.phase, "reroll_choice");
  assert.equal(raised.actor, 2);
  aceStep = advancePhysicalControl("ace", raised, "ace_reroll_confirm_3");
  raised = aceGame.advance(raised, 2, aceStep.request.action, aceStep.request.value);
  assert.equal(raised.phase, "betting");
  assert.equal(raised.actor, 3, "the seat after the raiser still takes its normal turn");
  const finalSeat = raised.actor;
  const finalCall = physicalControlRequest("ace", raised, `ace_call_${finalSeat + 1}`);
  raised = aceGame.advance(raised, finalSeat, finalCall.action, finalCall.value);
  aceStep = advancePhysicalControl("ace", raised, `ace_reroll_confirm_${finalSeat + 1}`);
  raised = aceGame.advance(raised, finalSeat, aceStep.request.action, aceStep.request.value);
  assert.equal(raised.phase, "between");
  assert.equal(raised.last.winner, 0);
  assert.equal(raised.committed.every(value => value === 20), true);

  assert.deepEqual(physicalControlRequest("fire", createFire(), "rock_hot_1"),
    { action: "choice", value: { number: 1, heat: 1 } });
  assert.deepEqual(physicalControlRequest("fire", { ...createFire(), actor: 1 }, "scissors_ice_2"),
    { action: "choice", value: { number: 2, heat: -1 } });
  assert.equal(physicalControlRequest("fire", createFire(), "rock_1"), null);
  assert.equal(physicalControlRequest("fire", createFire(), "rock_hot_2"), null,
    "seat 1 cards must not be accepted while seat 0 is choosing");
  assert.equal(physicalControlRequest("fire", { ...createFire(), actor: 1 }, "rock_hot_1"), null,
    "seat 0 cards must not be accepted while seat 1 is choosing");

  assert.deepEqual(Object.keys(COMPLETE_CONTROL_SPECS.grail).sort(), [
    "grail_cup_1", "grail_cup_2", "grail_cup_3",
    "grail_potion_1", "grail_potion_2", "grail_potion_3",
  ]);
  let grailPhysical = createGrail();
  let grailStep = advancePhysicalControl("grail", grailPhysical, "grail_potion_1");
  assert.equal(grailStep.request, null, "the cup owner cannot use the potion rack");
  assert.match(grailStep.notice, /ポーション側/);
  grailStep = advancePhysicalControl("grail", grailPhysical, "grail_cup_1");
  assert.deepEqual(grailStep.request,
    { action: "arrange", value: { trueIndex: 0 } });
  grailPhysical = advanceGrail(grailPhysical, grailPhysical.actor,
    grailStep.request.action, grailStep.request.value);
  let wrongRack = advancePhysicalControl("grail", grailPhysical, "grail_cup_2");
  assert.equal(wrongRack.request, null, "the potion owner cannot use the cup rack");
  assert.match(wrongRack.notice, /聖杯側/);
  grailStep = advancePhysicalControl("grail", grailPhysical, "grail_potion_2");
  assert.deepEqual(grailStep.request,
    { action: "arrange", value: { holyIndex: 1 } });
  grailPhysical = advanceGrail(grailPhysical, grailPhysical.actor,
    grailStep.request.action, grailStep.request.value);
  assert.deepEqual(physicalControlRequest("grail", grailPhysical, "grail_cup_2"),
    { action: "cup", value: 1 });
  assert.equal(physicalControlRequest("grail", grailPhysical, "grail_potion_2"), null);
  grailStep = advancePhysicalControl("grail", grailPhysical, "grail_cup_3");
  grailPhysical = advanceGrail(grailPhysical, grailPhysical.actor,
    grailStep.request.action, grailStep.request.value);
  assert.equal(grailPhysical.phase, "potion");
  assert.equal(physicalControlRequest("grail", grailPhysical, "grail_cup_1"), null);
  assert.deepEqual(physicalControlRequest("grail", grailPhysical, "grail_potion_1"),
    { action: "potion", value: 0 });

  let lightPhysical = createLight();
  while (lightPhysical.phase === "intro") lightPhysical = lightGame.systemAdvance(lightPhysical, "tick");
  for (const action of ["light_grave_1_1", "light_grave_1_2", "light_grave_1_3"]) {
    grailStep = advancePhysicalControl("light", lightPhysical, action);
    lightPhysical = advanceLight(lightPhysical, lightPhysical.actor,
      grailStep.request.action, grailStep.request.value);
  }
  const wrongLightSeat = advancePhysicalControl("light", lightPhysical, "light_grave_1_1");
  assert.equal(wrongLightSeat.request, null);
  assert.match(wrongLightSeat.notice, /自分のフォルダ/);
  for (const action of ["light_grave_2_2", "light_grave_2_1", "light_grave_2_3"]) {
    grailStep = advancePhysicalControl("light", lightPhysical, action);
    lightPhysical = advanceLight(lightPhysical, lightPhysical.actor,
      grailStep.request.action, grailStep.request.value);
  }
  assert.equal(lightPhysical.phase, "result");
  assert.deepEqual(lightPhysical.guards, [0, 1, 2]);
  assert.deepEqual(lightPhysical.tourists, [1, 0, 2]);

  let minePhysical = createMine();
  assert.deepEqual(COMPLETE_CONTROL_SPECS.mine.mine_rock_1_1.phases, ["arrange", "choose"],
    "mine rock controls must remain usable during both placement and pair selection");
  for (const action of [
    "mine_rock_2_4", "mine_rock_2_2", "mine_rock_2_1", "mine_rock_2_3",
  ]) {
    grailStep = advancePhysicalControl("mine", minePhysical, action, grailStep.progress);
  }
  assert.deepEqual(grailStep.request,
    { action: "arrange", value: { cards: [2, 1, 3, 0] } });
  minePhysical = advanceMine(minePhysical, 1, "arrange", grailStep.request.value);
  grailStep = advancePhysicalControl("mine", minePhysical, "mine_rock_1_3");
  assert.deepEqual(grailStep.request, { action: "mine", value: { indices: [2] } },
    "the source initially grants one pickaxe, not a mandatory pair");
  minePhysical = advanceMine(minePhysical, 0, "key", { target: 0, skill: "work" });
  grailStep = advancePhysicalControl("mine", minePhysical, "mine_rock_1_3");
  grailStep = advancePhysicalControl("mine", minePhysical, "mine_rock_1_1", grailStep.progress);
  assert.deepEqual(grailStep.request,
    { action: "mine", value: { indices: [2, 0] } });
  const staleMine = advancePhysicalControl("mine", { ...minePhysical, actor: 1 }, "mine_rock_2_4",
    { ...grailStep.progress, actor: 0 });
  assert.equal(staleMine.progress.actor, 1, "stale turn progress must not carry over");
  assert.deepEqual(staleMine.progress.indices, [3]);
}

const allGames = [
  sparrowGame, temperanceGame, lucyGame, silenceGame, jinnGame,
  aceGame, ...STRATEGY_GAMES, ...BOARD_GAMES,
];

{
  const world = {
    value: null,
    getDynamicProperty() { return this.value; },
    setDynamicProperty(_name, value) { this.value = value; },
  };
  const manager = new GameManager(allGames, { world });
  for (const game of allGames) {
    const count = game.maxPlayers ?? game.seats;
    const tableId = `persist-${game.id}`;
    for (let seat = 0; seat < count; seat++) {
      manager.join(tableId, game.id, `${game.id}-player-${seat}`);
    }
    if (!manager.view(tableId)?.state) manager.start(tableId);
    assert.ok(manager.view(tableId)?.state, `${game.id}: persistence state must exist`);
  }
  assert.ok(world.value.length < 32767, `session payload size ${world.value.length}`);
  const restored = new GameManager(allGames, { world });
  assert.deepEqual(restored.restore(world.value), { restored: 17, skipped: 0 });
  for (const game of allGames) {
    assert.ok(restored.view(`persist-${game.id}`)?.state, `${game.id}: state must survive restart`);
  }

  const migratedPayload = JSON.parse(world.value);
  const oldHope = migratedPayload.tables.find(table => table.gameId === "hope").state;
  delete oldHope.padSymbols;
  delete oldHope.safePad;
  const migrated = new GameManager(allGames, { world });
  assert.deepEqual(migrated.restore(migratedPayload), { restored: 17, skipped: 0 },
    "pre-physical Hope sessions must migrate instead of being skipped");
  const migratedHope = migrated.view("persist-hope").state;
  assert.equal(migratedHope.pads.length, 16);
  assert.ok(migrated.view("persist-hope").state.phase);

  const legacyLightPayload = JSON.parse(world.value);
  const oldLight = legacyLightPayload.tables.find(table => table.gameId === "light").state;
  oldLight.phase = "arrange";
  oldLight.hearts = [3, 1];
  oldLight.torches = [2, 4];
  oldLight.fuel = [777, 888];
  oldLight.arrangements = [[1, 2, 0], [0, 2, 1]];
  oldLight.selections = [{ guard: 1, tourist: 0 }, { guard: 0, tourist: 2 }];
  delete oldLight.oxygen;
  delete oldLight.oxygenLack;
  const legacyLight = new GameManager(allGames, { world });
  assert.deepEqual(legacyLight.restore(legacyLightPayload), { restored: 17, skipped: 0 },
    "legacy Candlelight sessions must migrate without resuming a half-converted round");
  const migratedLight = legacyLight.view("persist-light").state;
  assert.equal(migratedLight.phase, "intro");
  assert.equal(migratedLight.actor, null);
  assert.deepEqual(migratedLight.hearts, [3, 1]);
  assert.deepEqual(migratedLight.torches, [2, 4]);
  assert.deepEqual(migratedLight.oxygen, [777, 888],
    "legacy fuel is transferred to the source oxygen field");

  const expectSkipped = mutate => {
    const payload = JSON.parse(world.value);
    mutate(payload);
    const candidate = new GameManager(allGames, { world });
    const result = candidate.restore(payload);
    assert.equal(result.skipped, 1, "exactly the malformed table must be skipped");
    return candidate;
  };

  expectSkipped(payload => {
    payload.tables.find(table => table.gameId === "ace").state.actor = 99;
  });
  expectSkipped(payload => {
    payload.tables.find(table => table.gameId === "hope").players.push("hope-player-extra");
  });
  expectSkipped(payload => {
    payload.tables.find(table => table.gameId === "jinn").token = 0;
  });
  expectSkipped(payload => {
    const first = payload.tables[0];
    const second = payload.tables[1];
    second.players[0] = first.players[0];
  });
}

{
  const world = {
    value: null,
    getDynamicProperty() { return this.value; },
    setDynamicProperty(_name, value) { this.value = value; },
  };
  const manager = new GameManager([hopeGame], { world });
  manager.join("hope-ai-restart", "hope", "hope-ai-restart-a");
  manager.join("hope-ai-restart", "hope", "hope-ai-restart-b");
  manager.start("hope-ai-restart");
  manager.persist();
  const payload = JSON.parse(world.value);
  const saved = payload.tables.find(table => table.gameId === "hope").state;
  saved.ai = saved.ai.map((value, index) => index === 0
    ? { ...value, pad: 7, alive: false, loser: true }
    : index === 1 ? { ...value, pad: 12, alive: true, loser: true } : { ...value });
  saved.aiSeed = 0x51a7c0de;
  const restored = new GameManager([hopeGame], { world });
  assert.deepEqual(restored.restore(payload), { restored: 1, skipped: 0 });
  let hope = restored.view("hope-ai-restart").state;
  assert.deepEqual([hope.ai[0].pad, hope.ai[0].alive, hope.ai[1].pad, hope.ai[1].alive],
    [7, false, 12, true], "Hope AI positions and eliminations must survive restart");
  assert.equal(hope.ai.length, HOPE_AI_TRADERS);
  restored.persist();
  const firstPersistence = JSON.parse(world.value).tables[0].state;
  assert.equal(firstPersistence.aiSeed, 0x51a7c0de,
    "the hidden Hope AI PRNG state must survive the first persistence cycle");
  assert.deepEqual(firstPersistence.ai.slice(0, 2).map(value =>
    [value.pad, value.alive, value.loser]), [[7, false, true], [12, true, true]],
  "accumulated Hope loser roles must survive the first persistence cycle");
  const restoredAgain = new GameManager([hopeGame], { world });
  assert.deepEqual(restoredAgain.restore(), { restored: 1, skipped: 0 });
  hope = restoredAgain.view("hope-ai-restart").state;
  assert.deepEqual([hope.ai[0].pad, hope.ai[0].alive, hope.ai[1].pad, hope.ai[1].alive],
    [7, false, 12, true], "a second persistence cycle must not revive a fallen AI");
  restoredAgain.persist();
  assert.deepEqual(JSON.parse(world.value).tables[0].state.ai.slice(0, 2).map(value =>
    [value.pad, value.alive, value.loser]), [[7, false, true], [12, true, true]],
  "a second persistence cycle must not forget an accumulated loser role");
}

{
  const makeWorld = () => ({
    value: null,
    getDynamicProperty() { return this.value; },
    setDynamicProperty(_name, value) { this.value = value; },
  });
  const makeHopeNoOutTable = world => {
    const manager = new GameManager([hopeGame], { world });
    const tableId = "hope-round-ten-restore";
    manager.join(tableId, "hope", "hope-round-ten-a");
    manager.join(tableId, "hope", "hope-round-ten-b");
    manager.tables.get(tableId).state = {
      ...hopeGame.create(2),
      phase: "drop",
      actor: null,
      round: 10,
      phaseRemaining: HOPE_ROUND_TEN_NO_OUT_REMAINING_TICKS,
      floorOpen: false,
      roundTenNoOut: true,
      roundTenNoOutChecked: true,
    };
    manager.persist();
    return { manager, tableId };
  };

  const noOutWorld = makeWorld();
  const noOutSaved = makeHopeNoOutTable(noOutWorld);
  const noOutRestored = new GameManager([hopeGame], { world: noOutWorld });
  assert.deepEqual(noOutRestored.restore(noOutWorld.value), { restored: 1, skipped: 0 });
  let noOutView = noOutRestored.view(noOutSaved.tableId);
  let noOutState = noOutRestored.tables.get(noOutSaved.tableId).state;
  assert.equal(noOutState.roundTenNoOut, true,
    "the round-ten fast-forward marker must survive a BDS restart");
  assert.equal(noOutState.roundTenNoOutChecked, true,
    "the one-shot check marker must survive a BDS restart");
  for (let tick = 0; tick < HOPE_ROUND_TEN_NO_OUT_REMAINING_TICKS; tick++) {
    noOutView = noOutRestored.systemAct(noOutSaved.tableId, "tick", undefined, { persist: false });
  }
  assert.equal(noOutView.state.phase, "finished");
  assert.equal(noOutView.state.reason, "survived");
  assert.equal(noOutView.state.floorOpen, false,
    "a restored round-ten no-out result must finish without opening the floor");
  assert.equal(noOutView.state.last.noOut, true);

  const legacyNoOutWorld = makeWorld();
  const legacyNoOutSaved = makeHopeNoOutTable(legacyNoOutWorld);
  const legacyNoOutPayload = JSON.parse(legacyNoOutWorld.value);
  delete legacyNoOutPayload.tables.find(table => table.gameId === "hope")
    .state.roundTenNoOutChecked;
  const legacyNoOutRestored = new GameManager([hopeGame], { world: legacyNoOutWorld });
  assert.deepEqual(legacyNoOutRestored.restore(legacyNoOutPayload),
    { restored: 1, skipped: 0 });
  assert.equal(legacyNoOutRestored.tables.get(legacyNoOutSaved.tableId)
    .state.roundTenNoOutChecked, true,
  "a legacy no-out hold must restore with the check marker already consumed");

  const makeClockTable = world => {
    const manager = new GameManager([clockGame], { world });
    const tableId = "clock-restart";
    manager.join(tableId, "clock", "clock-restart-a");
    manager.join(tableId, "clock", "clock-restart-b");
    if (!manager.view(tableId)?.state) manager.start(tableId);
    return { manager, tableId };
  };

  const moveWorld = makeWorld();
  const moving = makeClockTable(moveWorld);
  let view = moving.manager.view(moving.tableId);
  view = moving.manager.act("clock-restart-a", view.token, "time_confirm");
  assert.equal(view.state.phase, "move");
  moving.manager.persist();
  const moveRestored = new GameManager([clockGame], { world: moveWorld });
  assert.deepEqual(moveRestored.restore(moveWorld.value), { restored: 1, skipped: 0 });
  view = moveRestored.view(moving.tableId);
  assert.deepEqual([view.state.phase, view.state.actor, view.state.requiredSectors],
    ["move", 0, 1], "a clock selection must survive a restart");

  const dropWorld = makeWorld();
  const dropping = makeClockTable(dropWorld);
  view = dropping.manager.view(dropping.tableId);
  assert.throws(() => dropping.manager.act("clock-restart-a", view.token, "sector_confirm"),
    /時刻設定/);
  view = dropping.manager.act("clock-restart-a", view.token, "time_confirm");
  view = dropping.manager.act("clock-restart-a", view.token, "sector_confirm");
  for (let tick = 0; tick < 6000 && view.state.phase === "move"; tick++) {
    view = dropping.manager.systemAct(dropping.tableId, "tick", undefined, { persist: false });
  }
  assert.equal(view.state.phase, "drop");
  dropping.manager.persist();
  const dropRestored = new GameManager([clockGame], { world: dropWorld });
  assert.deepEqual(dropRestored.restore(dropWorld.value), { restored: 1, skipped: 0 });
  view = dropRestored.view(dropping.tableId);
  assert.deepEqual([view.state.phase, view.state.actor, view.state.selected], ["drop", 0, [0]]);
  for (let tick = 0; tick < 20; tick++) {
    view = dropRestored.systemAct(dropping.tableId, "tick");
  }
  assert.equal(view.state.floorOpen, true, "a restored drop must reach the source floor window");
}

console.log("PASS 17 GAME LOGIC TESTS: catalog, shared manager, rule machines, hidden choices and terminal conditions (not a production playthrough)");
