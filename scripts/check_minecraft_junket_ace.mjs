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
assert.ok(compareAceDice(dice('A K Q J 10'), dice('A K Q J 9')) > 0,
  'the ver1.09 0pair table scores the absent 9 higher than the absent 10');
assert.ok(compareAceDice(dice('A K Q J 10'), dice('K Q J 10 9')) > 0,
  'the ver1.09 0pair table ranks the absent-A hand below the absent-9 hand');
assert.deepEqual(aceHand(dice('A K Q J 10')).key, [0, 60]);
assert.deepEqual(aceHand(dice('K Q J 10 9')).key, [0, 10]);
assert.equal(compareAceDice(dice('A K Q J 10'), dice('A K Q J 10')), 0);

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
  assert.equal(state.phase, 'betting', 'source still gives the last seat its decision');
  assert.equal(state.actor, 3);
  state = advanceAce(state, 3, 'bet', { type: 'call' });
  state = advanceAce(state, 3, 'reroll_selected', { indices: [], dice: [...state.dice[3]] });
  assert.equal(state.phase, 'between');
  assert.equal(state.last.winner, 3);
  assert.deepEqual(state.coins, [90, 90, 90, 130]);
  assert.deepEqual(state.committed, [10, 10, 10, 10],
    'dropped players still pay the bet they had already committed');
}

{
  let state = createAce();
  state = advanceAce(state, 0, 'table_bet', 10);
  state = rollAll(state);
  state = advanceAce(state, 0, 'bet', { type: 'raise', amount: 20 });
  assert.equal(state.currentBet, 30);
  assert.equal(state.phase, 'reroll_choice');
  assert.equal(state.actor, 0);
  state = advanceAce(state, 0, 'reroll_selected', {
    indices: [],
    dice: [...state.dice[0]],
  });
  assert.equal(state.phase, 'betting');
  assert.equal(state.actor, 1, 'raise from the parent does not skip later seats');
  for (const seat of [1, 2, 3]) {
    state = advanceAce(state, seat, 'bet', { type: 'call' });
    state = advanceAce(state, seat, 'reroll_selected', {
      indices: [],
      dice: [...state.dice[seat]],
    });
  }
  assert.equal(state.last.winner, 0);
  assert.deepEqual(state.coins, [190, 70, 70, 70]);
}

{
  let state = createAce();
  state = advanceAce(state, 0, 'table_bet', 10);
  state = rollAll(state);
  assert.throws(() => advanceAce(state, 0, 'bet', { type: 'raise', amount: 999 }), /1〜200/);
}

{
  let state = createAce();
  state = advanceAce(state, 0, 'table_bet', 10);
  state = rollAll(state);
  state = advanceAce(state, 0, 'bet', { type: 'call' });
  state = advanceAce(state, 0, 'reroll_selected', { indices: [], dice: [...state.dice[0]] });
  state = advanceAce(state, 1, 'bet', { type: 'raise', amount: 10 });
  assert.equal(state.phase, 'raise_response');
  assert.equal(state.actor, 0, 'the parent answers a raise made by the next seat');
  assert.deepEqual(state.responders, [0]);
  state = advanceAce(state, 0, 'respond', { type: 'call' });
  assert.equal(state.phase, 'reroll_choice');
  assert.equal(state.actor, 1, 'the raiser chooses their reroll last');
  state = advanceAce(state, 1, 'reroll_selected', { indices: [], dice: [...state.dice[1]] });
  assert.equal(state.phase, 'betting');
  assert.equal(state.actor, 2, 'seats after the raiser continue after the response');
}

{
  const tiedRoll = dice('A A A A 9');
  const initial = [tiedRoll, tiedRoll, dice('9 10 J Q K'), dice('9 9 10 J Q')];
  let state = createAce();
  state = advanceAce(state, 0, 'table_bet', 10);
  for (const value of initial) state = advanceAce(state, state.actor, 'roll', value);
  for (let seat = 0; seat < ACE_SEATS; seat++) {
    state = advanceAce(state, state.actor, 'bet', { type: 'call' });
    state = advanceAce(state, state.actor, 'reroll_selected', {
      indices: [], dice: [...state.dice[state.actor]],
    });
  }
  assert.equal(state.phase, 'reroll');
  assert.deepEqual(state.tied, [0, 1]);
  state = advanceAce(state, 0, 'reroll', [...tiedRoll]);
  state = advanceAce(state, 1, 'reroll', [...tiedRoll]);
  assert.equal(state.phase, 'reroll', 'a repeated tie must start another reroll round');
  assert.deepEqual(state.tied, [0, 1]);
  state = advanceAce(state, 0, 'reroll', dice('A A A A A'));
  state = advanceAce(state, 1, 'reroll', dice('K K K K K'));
  assert.equal(state.phase, 'between');
  assert.equal(state.last.winner, 0);
  assert.deepEqual(state.coins, [100, 100, 100, 100], 'source clears bets on tie rerolls');
}

{
  let state = rollAll(advanceAce(createAce(), 0, 'table_bet', 399));
  for (let seat = 0; seat < 4; seat++) state = advanceAce(state, seat, 'bet', { type: 'fold' });
  assert.equal(state.phase, 'reroll');
  assert.deepEqual(state.tied, [0, 1, 2, 3], 'source zero-score tie after all drops');
  assert.deepEqual(state.committed, [0, 0, 0, 0]);
  assert.throws(() => advanceAce(createAce(), 0, 'table_bet', 400), /399/);
}

console.log("PASS Ace four-player table bet, ordered rolls, five-card ranking, fold/raise/call settlement and payouts");
