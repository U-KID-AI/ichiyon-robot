import assert from "node:assert/strict";
import {
  ACE_INITIAL_COINS, ACE_RAISE_LIMIT, ACE_SEATS, aceGame, aceHand,
  aceSystem, aceView, advanceAce, compareAceDice, createAce,
} from "../minecraft/behavior_packs/ichiyon_junket_bp/scripts/ace.js";

const dice = values => values.split(' ');
assert.equal(ACE_SEATS, 4);
assert.equal(ACE_INITIAL_COINS, 100);
assert.equal(ACE_RAISE_LIMIT, 200);
assert.equal(aceGame.seats, 4);
assert.equal(aceHand(dice('A A A A A')).name, 'five');
assert.equal(aceHand(dice('K K K K 9')).name, 'four');
assert.equal(aceHand(dice('Q Q Q J J')).name, 'full_house');
assert.equal(aceHand(dice('10 10 10 A K')).name, 'three');
assert.equal(aceHand(dice('A A K K Q')).name, 'two_pair');
assert.equal(aceHand(dice('9 9 A K Q')).name, 'one_pair');
assert.equal(aceHand(dice('A K Q J 10')).name, 'high');
assert.ok(compareAceDice(dice('A A A A A'), dice('K K K K K')) > 0);
assert.ok(compareAceDice(dice('A A K K Q'), dice('K K Q Q A')) > 0);

function rollAll(state) {
  const values = [dice('A A A 9 9'), dice('K K K 10 10'), dice('Q Q J J A'), dice('9 10 J Q K')];
  for (let turn = 0; turn < 4; turn++) {
    state = advanceAce(state, state.actor, 'roll', values[state.actor]);
  }
  return state;
}

{
  let state = createAce();
  assert.throws(() => advanceAce(state, 1, 'table_bet', 10), /手番/);
  assert.throws(() => advanceAce(state, 0, 'table_bet', 9), /10枚/);
  state = advanceAce(state, 0, 'table_bet', 10);
  assert.equal(state.phase, 'roll');
  state = rollAll(state);
  assert.equal(state.phase, 'betting');
  const view = aceView(state);
  assert.deepEqual(view.hands.map(hand => hand.name), ['full_house', 'full_house', 'two_pair', 'high']);
  state = advanceAce(state, 0, 'bet', { type: 'fold' });
  state = advanceAce(state, 1, 'bet', { type: 'fold' });
  state = advanceAce(state, 2, 'bet', { type: 'fold' });
  assert.equal(state.phase, 'between');
  assert.equal(state.last.winner, 3);
  assert.deepEqual(state.coins, [90, 90, 90, 130]);
}

{
  let state = createAce();
  state = advanceAce(state, 0, 'table_bet', 10);
  state = rollAll(state);
  state = advanceAce(state, 0, 'bet', { type: 'raise', amount: 20 });
  assert.equal(state.currentBet, 30);
  state = advanceAce(state, 1, 'bet', { type: 'call' });
  state = advanceAce(state, 2, 'bet', { type: 'fold' });
  state = advanceAce(state, 3, 'bet', { type: 'call' });
  assert.equal(state.last.winner, 0);
  assert.equal(state.coins[0], 170);
  assert.equal(state.coins[1], 70);
  assert.equal(state.coins[2], 90);
  assert.equal(state.coins[3], 70);
}

{
  let state = createAce();
  state = advanceAce(state, 0, 'table_bet', 10);
  state = rollAll(state);
  assert.throws(() => advanceAce(state, 0, 'bet', { type: 'raise', amount: 999 }), /1〜200/);
}

console.log("PASS Ace four-player table bet, ordered rolls, five-card ranking, fold/raise/call settlement and payouts");
