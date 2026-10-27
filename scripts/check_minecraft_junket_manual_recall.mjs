// Focused review regressions: real runtime/reducers, fake client form transport.
// These tests do not establish desktop/mobile client operation.
import assert from 'node:assert/strict';
import { GameManager } from '../minecraft/behavior_packs/ichiyon_junket_bp/scripts/game_manager.js';
import { QUALITY_GAMES } from '../minecraft/behavior_packs/ichiyon_junket_bp/scripts/quality_cases.js';
import { createCompleteRuntime } from '../minecraft/behavior_packs/ichiyon_junket_bp/scripts/complete_runtime.js';
import { installDecisionResume } from '../minecraft/behavior_packs/ichiyon_junket_bp/scripts/decision_resume.js';
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function harness(id) {
  const game = QUALITY_GAMES.find(g => g.id === id), forms = [], handlers = {}, jobs = new Map(), messages = [];
  let sequence = 0, tick = 0;
  const players = Array.from({ length: game.seats }, (_, seat) => ({ id: `${id}-${seat}`, name: `P${seat + 1}`,
    sendMessage(message) { messages.push({ seat, message }); }, onScreenDisplay: { setActionBar() {} } }));
  const signal = name => ({ subscribe(fn) { (handlers[name] ??= []).push(fn); } });
  const world = { getAllPlayers: () => players,
    beforeEvents: { playerInteractWithEntity: signal('interact') },
    afterEvents: { playerLeave: signal('leave'), playerButtonInput: signal('button') } };
  const system = { get currentTick() { return tick; }, run: fn => system.runTimeout(fn, 1),
    runTimeout(fn, delay = 1) { const key = ++sequence; jobs.set(key, { fn, at: tick + delay }); return key; },
    runInterval(fn, delay = 1) { const key = ++sequence; jobs.set(key, { fn, at: tick + delay, delay }); return key; },
    clearRun: key => jobs.delete(key), afterEvents: { scriptEventReceive: signal('script') } };
  class Form {
    constructor() { this.buttons = []; this.fields = []; }
    title(v) { this.name = v; return this; } body(v) { this.text = v; return this; }
    button(v) { this.buttons.push(v); return this; }
    textField(...v) { this.fields.push(v); return this; } dropdown(...v) { this.fields.push(v); return this; }
    toggle(...v) { this.fields.push(v); return this; }
    show(player) { return new Promise(resolve => forms.push({ player, form: this, resolve })); }
  }
  const manager = new GameManager(QUALITY_GAMES);
  const runtime = createCompleteRuntime({ game, world, system, manager, ActionFormData: Form, ModalFormData: Form });
  for (const player of players) manager.join(id, id, player.id);
  installDecisionResume({ world, system, manager, runtimes: new Map([[id, runtime]]) });
  async function advance(count = 5) {
    for (let i = 0; i < count; i++) {
      tick++;
      for (const [key, job] of [...jobs]) if (job.at <= tick) {
        if (job.delay) job.at += job.delay; else jobs.delete(key);
        job.fn();
      }
      await flush();
    }
  }
  const formFor = seat => forms.find(entry => entry.player === players[seat]);
  async function reply(seat, response) {
    const index = forms.findIndex(entry => entry.player === players[seat]);
    assert.ok(index >= 0, `${id}: expected form for seat ${seat}`);
    forms.splice(index, 1)[0].resolve(response);
    await flush();
  }
  async function click(seat, label) {
    const entry = formFor(seat);
    assert.ok(entry, `${id}: expected form for seat ${seat}`);
    const selection = entry.form.buttons.findIndex(value => value.includes(label));
    assert.ok(selection >= 0, `${id}: missing button ${label}: ${entry.form.buttons.join(' / ')}`);
    await reply(seat, { selection });
  }
  async function recall(seat) {
    const event = { player: players[seat], button: 'Sneak', newButtonState: 'Pressed' };
    handlers.button[0](event); await advance(2); handlers.button[0](event); await advance(2);
  }
  function act(seat, action, value) {
    return manager.actDecision(players[seat].id, manager.forPlayer(players[seat].id), action, value);
  }
  return { game, forms, players, manager, runtime, messages, formFor, reply, click, recall, advance, act };
}

const failures = [];
async function check(name, body) {
  try { await body(); console.log(`PASS ${name}`); }
  catch (error) { failures.push({ name, error }); console.error(`FAIL ${name}: ${error.message}`); }
}

await check('Mine: recalled non-actor key survives independent opponent key response', async () => {
  const h = harness('mine'); h.runtime.resume(); await h.advance();
  assert.equal(h.manager.view('mine').state.actor, 1);
  await h.recall(0);
  await h.click(0, '鍵を使う'); await h.click(1, '鍵を使う');
  // Both choices were opened from the same match revision; the targets differ.
  await h.click(1, '2P：運命');
  await h.click(0, '1P：運命');
  assert.deepEqual(h.manager.view('mine').state.ingots, [15, 15],
    'other player key must not invalidate this still-legal key');
  assert.deepEqual(h.players.map(player => h.manager.forPlayer(player.id).state.self.keys), [0, 0]);
});

await check('Grail: recalled non-actor drink survives independent opponent drink', async () => {
  const h = harness('grail');
  h.act(0, 'arrange', { trueIndex: 0 }); h.act(1, 'arrange', { holyIndex: 0 });
  h.act(0, 'cup', 0); h.act(1, 'potion', 0);
  h.act(1, 'cup', 1); h.act(0, 'potion', 1);
  assert.deepEqual(h.manager.view('grail').state.poisonCup, [100, 100]);
  h.act(1, 'arrange', { trueIndex: 0 }); h.act(0, 'arrange', { holyIndex: 0 });
  assert.equal(h.manager.view('grail').state.actor, 1);
  h.runtime.resume(); await h.advance(); await h.recall(0);
  await h.click(1, '自分の運命の器を飲む');
  await h.click(0, '自分の運命の器を飲む');
  const state = h.manager.view('grail').state;
  assert.deepEqual(state.poisonCup, [0, 0], 'both voluntary drinks remain legal in the same phase');
  assert.deepEqual(state.poison, [100, 100]);
});

await check('Mine: 30-second wait is available after recalling a canceled choice menu', async () => {
  const h = harness('mine');
  h.act(1, 'arrange', { cards: [0, 1, 2, 3] });
  h.runtime.resume(); await h.advance();
  await h.reply(0, { canceled: true, cancelationReason: 'UserClosed' }); await h.advance(10);
  assert.equal(h.forms.length, 0, 'cancel should not reopen repeatedly');
  await h.recall(0);
  const before = h.manager.view('mine').state.phaseRemaining;
  await h.click(0, '30秒待機する');
  assert.equal(h.manager.view('mine').state.phaseRemaining, before - 600);
  assert.equal(h.manager.view('mine').state.phase, 'choose');
});

if (failures.length) {
  process.exitCode = 1;
  console.error(`${failures.length} manual-recall regression(s); no production or client claims.`);
} else console.log('PASS manual decision concurrency and recall timing (mock UI; real reducers/runtime)');
